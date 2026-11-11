"""Shared FastAPI dependencies — authentication, authorization, DB connections."""

from typing import Annotated

from cryptography.fernet import InvalidToken
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.database import db
from app.core.exceptions import InsufficientRoleError, SessionExpiredError
from app.core.redis import session_store
from app.core.security import decode_token, decrypt_password

bearer_scheme = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> dict:
    """Extract user from JWT → verify session in Redis.

    Returns:
        {"username": str, "session_id": str, "roles": list[str], "encrypted_password": str}
    """
    try:
        payload = decode_token(credentials.credentials)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from None

    subject, session_id = payload.get("sub"), payload.get("sid")
    if (
        not isinstance(subject, str)
        or not subject
        or not isinstance(session_id, str)
        or not session_id
    ):
        raise SessionExpiredError() from None
    session = await session_store.get(session_id)
    if not session or session.get("username") != subject:
        raise SessionExpiredError()

    # Sessions store an encrypted StarRocks password. If the backend restarted
    # with a different Fernet key, old sessions become unreadable and should be
    # treated as expired instead of surfacing 500s from downstream handlers.
    try:
        decrypt_password(session["encrypted_password"])
    except (InvalidToken, KeyError, TypeError, ValueError):
        await session_store.delete(payload["sid"])
        raise SessionExpiredError() from None

    # Refresh session TTL on activity
    await session_store.refresh(payload["sid"])

    return {
        "username": payload["sub"],
        "session_id": payload["sid"],
        "roles": session["roles"],
        "assigned_roles": session["assigned_roles"],
        "default_role": session.get("default_role"),
        "active_role": session.get("active_role"),
        "security_context_version": session.get("security_context_version", 1),
        "encrypted_password": session["encrypted_password"],
    }


def require_role(*allowed_roles: str):
    """Dependency factory: require the user to have one of the specified roles.

    Usage:
        @router.post("/admin-only")
        async def admin_endpoint(user=Depends(require_role("ACCOUNTADMIN"))):
            ...
    """

    async def _check(user: Annotated[dict, Depends(get_current_user)]) -> dict:
        if not any(r in user["roles"] for r in allowed_roles):
            raise InsufficientRoleError(f"Requires one of: {', '.join(allowed_roles)}")
        return user

    return _check


async def get_user_connection(user: Annotated[dict, Depends(get_current_user)]):
    """Create a StarRocks connection as the authenticated user.

    Usage:
        @router.get("/tables")
        async def list_tables(conn=Depends(get_user_connection)):
            async with conn.cursor() as cur:
                await cur.execute("SHOW TABLES")
    """
    password = decrypt_password(user["encrypted_password"])
    async with db.user_conn(user["username"], password) as conn:
        yield conn
