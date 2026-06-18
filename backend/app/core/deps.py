"""Shared FastAPI dependencies — authentication, authorization, DB connections."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.database import db
from app.core.exceptions import SessionExpiredError, InsufficientRoleError
from app.core.redis import session_store
from app.core.security import decode_token

bearer_scheme = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
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
        )

    session = await session_store.get(payload["sid"])
    if not session:
        raise SessionExpiredError()

    # Refresh session TTL on activity
    await session_store.refresh(payload["sid"])

    return {
        "username": payload["sub"],
        "session_id": payload["sid"],
        "roles": session["roles"],
        "encrypted_password": session["encrypted_password"],
    }


def require_role(*allowed_roles: str):
    """Dependency factory: require the user to have one of the specified roles.

    Usage:
        @router.post("/admin-only")
        async def admin_endpoint(user=Depends(require_role("ACCOUNTADMIN"))):
            ...
    """

    async def _check(user: dict = Depends(get_current_user)) -> dict:
        if not any(r in user["roles"] for r in allowed_roles):
            raise InsufficientRoleError(
                f"Requires one of: {', '.join(allowed_roles)}"
            )
        return user

    return _check


async def get_user_connection(user: dict = Depends(get_current_user)):
    """Create a StarRocks connection as the authenticated user.

    Usage:
        @router.get("/tables")
        async def list_tables(conn=Depends(get_user_connection)):
            async with conn.cursor() as cur:
                await cur.execute("SHOW TABLES")
    """
    from app.core.security import decrypt_password

    password = decrypt_password(user["encrypted_password"])
    conn = await db.user_conn(user["username"], password)
    try:
        yield conn
    finally:
        conn.close()
