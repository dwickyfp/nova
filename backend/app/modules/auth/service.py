"""Auth service — StarRocks native authentication + session management."""

import asyncmy
import asyncmy.errors

from app.common.audit import write_audit_log
from app.common.nova_system import is_setup_complete, mark_setup_complete
from app.common.user_flags import (
    clear_must_change_password,
    is_must_change_password,
)
from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.core.security import create_access_token, decrypt_password, encrypt_password
from app.modules.access_control.role_activation import (
    RoleActivationError,
    parse_assigned_roles,
    role_activation_service,
)
from app.modules.auth.exceptions import (
    DefaultPasswordError,
    InvalidCredentialsError,
    PasswordMismatchError,
    SetupAlreadyCompleteError,
    WeakPasswordError,
)


class AuthService:
    """Authenticate against StarRocks directly. No separate user table."""

    async def verify_credentials(self, username: str, password: str) -> bool:
        """Try a real MySQL connection to verify credentials."""
        try:
            conn = await asyncmy.connect(
                host=settings.STARROCKS_HOST,
                port=settings.STARROCKS_FE_MYSQL_PORT,
                user=username,
                password=password,
                connect_timeout=5,
            )
            conn.close()
            return True
        except asyncmy.errors.OperationalError:
            return False

    async def get_user_roles(self, username: str, password: str) -> list[str]:
        """Fetch all granted/applicable roles for this user."""
        conn = await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=username,
            password=password,
            connect_timeout=5,
        )
        try:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT ROLE_NAME FROM information_schema.applicable_roles")
                    rows = await cur.fetchall()
                    roles = sorted({str(row[0]) for row in rows if row and row[0]})
                    if roles:
                        return roles
                except Exception:
                    pass

                await cur.execute("SHOW GRANTS")
                rows = await cur.fetchall()
                return sorted(set(self._parse_roles(rows)))
        finally:
            conn.close()

    async def _resolve_security_state(self, username: str, password: str) -> dict:
        """Resolve and verify the explicit default role on a real user session."""
        if not settings.RANGER_ENABLED:
            # Legacy/native-RBAC deployments may grant privileges directly to
            # users and legitimately have CURRENT_ROLE() = NONE. Do not invent
            # a role from list ordering. Strict role activation begins when the
            # deployment enables Ranger, which is Nova's production target.
            roles = await self.get_user_roles(username, password)
            return {
                "roles": roles,
                "assigned_roles": roles,
                "default_role": None,
                "active_role": None,
                "security_context_version": 1,
            }
        try:
            async with db.user_conn(username=username, password=password) as conn:
                active_role, assignments = await role_activation_service.activate(
                    conn, principal=username, requested_role=None
                )
        except RoleActivationError as exc:
            raise InvalidCredentialsError(str(exc)) from exc
        roles = list(assignments.assigned_roles)
        return {
            "roles": roles,
            "assigned_roles": roles,
            "default_role": assignments.default_role,
            "active_role": active_role,
            "security_context_version": 1,
        }

    async def login(self, username: str, password: str) -> dict:
        """Full login flow.

        1. Verify credentials against StarRocks
        2. Check if setup is required
        3. Block default password after setup
        4. Create session + JWT

        Returns:
            Login response dict with status, token, user info.
        """
        # 1. Verify credentials
        if not await self.verify_credentials(username, password):
            raise InvalidCredentialsError()

        # 2. Check setup status
        setup_done = await is_setup_complete()

        # 3. Force setup on first login (nova_admin only)
        if not setup_done and username == "nova_admin":
            security = await self._resolve_security_state(username, password)
            enc_password = encrypt_password(password)
            session_id = await session_store.create(
                username,
                enc_password,
                security["roles"],
                default_role=security["default_role"],
                active_role=security["active_role"],
            )
            token = create_access_token(username, session_id)
            await write_audit_log(
                event_type="login",
                user_name=username,
                action="LOGIN",
                object_type="USER",
                object_name=username,
                status="SUCCESS",
                session_id=session_id,
            )
            return {
                "status": "SETUP_REQUIRED",
                "access_token": token,
                "token_type": "bearer",
                "user": username,
                **security,
                "message": "First login — set a new admin password",
            }

        # 4. Block default password after setup
        if setup_done and password == "nova" and username == "nova_admin":
            raise DefaultPasswordError()

        # 5. A user created with a generated password must change it at first
        # login. They are authenticated (the credential is valid), but the
        # session is marked so the UI routes them to the change-password screen
        # before anything else. This is the same shape as SETUP_REQUIRED above.
        if await is_must_change_password(username):
            security = await self._resolve_security_state(username, password)
            enc_password = encrypt_password(password)
            session_id = await session_store.create(
                username,
                enc_password,
                security["roles"],
                default_role=security["default_role"],
                active_role=security["active_role"],
            )
            token = create_access_token(username, session_id)
            await write_audit_log(
                event_type="login",
                user_name=username,
                action="LOGIN",
                object_type="USER",
                object_name=username,
                status="SUCCESS",
                session_id=session_id,
            )
            return {
                "status": "PASSWORD_CHANGE_REQUIRED",
                "access_token": token,
                "token_type": "bearer",
                "user": username,
                **security,
                "message": "Your administrator requires a password change before first use",
            }

        # 6. Normal authenticated session
        security = await self._resolve_security_state(username, password)
        enc_password = encrypt_password(password)
        session_id = await session_store.create(
            username,
            enc_password,
            security["roles"],
            default_role=security["default_role"],
            active_role=security["active_role"],
        )
        token = create_access_token(username, session_id)
        await write_audit_log(
            event_type="login",
            user_name=username,
            action="LOGIN",
            object_type="USER",
            object_name=username,
            status="SUCCESS",
            session_id=session_id,
        )

        return {
            "status": "AUTHENTICATED",
            "access_token": token,
            "token_type": "bearer",
            "user": username,
            **security,
        }

    async def switch_role(self, session_id: str, requested_role: str) -> dict:
        session = await session_store.get(session_id)
        if not session:
            raise InvalidCredentialsError("Session expired")

        roles = session.get("assigned_roles") or session.get("roles", [])
        if requested_role not in roles:
            raise InvalidCredentialsError("Role is not granted to this user")

        password = decrypt_password(session["encrypted_password"])
        try:
            async with db.user_conn(session["username"], password) as conn:
                active_role, assignments = await role_activation_service.activate(
                    conn,
                    principal=session["username"],
                    requested_role=requested_role,
                )
        except RoleActivationError as exc:
            raise InvalidCredentialsError(str(exc)) from exc
        version = await session_store.set_active_role(session_id, active_role)

        return {
            "roles": list(assignments.assigned_roles),
            "assigned_roles": list(assignments.assigned_roles),
            "default_role": assignments.default_role,
            "active_role": active_role,
            "security_context_version": version,
        }

    async def setup(
        self, username: str, session_id: str, new_password: str, confirm_password: str
    ) -> dict:
        """First-login setup: change admin password.

        Only nova_admin can run setup. Only works before setup is marked complete.
        """
        if username != "nova_admin":
            raise SetupAlreadyCompleteError("Only nova_admin can run setup")

        if await is_setup_complete():
            raise SetupAlreadyCompleteError()

        if new_password != confirm_password:
            raise PasswordMismatchError()

        if len(new_password) < 8:
            raise WeakPasswordError()

        # Change password in StarRocks via root connection
        await db.execute_system(
            f"ALTER USER 'nova_admin' IDENTIFIED BY '{self._escape(new_password)}'"
        )

        # Update session with new encrypted password
        enc_password = encrypt_password(new_password)
        session = await session_store.get(session_id)
        if session:
            # Delete old session, create new with updated password
            roles = session["roles"]
            await session_store.delete(session_id)
            await session_store.create(
                "nova_admin",
                enc_password,
                roles,
                default_role=session.get("default_role"),
                active_role=session.get("active_role"),
            )
            # Note: caller should issue new JWT with new session_id

        # Mark setup complete
        await mark_setup_complete()

        return {"status": "SETUP_COMPLETE", "message": "Password changed. Welcome to Nova!"}

    async def change_password(
        self, username: str, current_password: str, new_password: str, confirm_password: str
    ) -> dict:
        """Change password for any authenticated user."""
        if new_password != confirm_password:
            raise PasswordMismatchError()

        if len(new_password) < 8:
            raise WeakPasswordError()

        # Verify current password
        if not await self.verify_credentials(username, current_password):
            raise InvalidCredentialsError("Current password is incorrect")

        # Change in StarRocks
        await db.execute_system(
            f"ALTER USER '{self._escape(username)}' IDENTIFIED BY '{self._escape(new_password)}'"
        )

        # The requirement is satisfied: clear it so the next login is normal.
        await clear_must_change_password(username)

        return {"status": "PASSWORD_CHANGED", "message": "Password updated successfully"}

    async def logout(self, session_id: str) -> None:
        """Delete session from Redis."""
        await session_store.delete(session_id)

    @staticmethod
    def _parse_roles(grants_rows: list) -> list[str]:
        """Parse StarRocks role-marker assignments from SHOW GRANTS output."""
        return parse_assigned_roles(grants_rows)

    @staticmethod
    def _escape(value: str) -> str:
        """Basic SQL string escaping for identifiers/passwords."""
        return value.replace("'", "\\'").replace("\\", "\\\\")


# Singleton
auth_service = AuthService()
