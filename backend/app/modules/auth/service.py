"""Auth service — StarRocks native authentication + session management."""

import asyncmy
import asyncmy.errors

from app.common.audit import write_audit_log
from app.common.nova_system import is_setup_complete, mark_setup_complete
from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.core.security import create_access_token, decrypt_password, encrypt_password
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
            roles = await self.get_user_roles(username, password)
            enc_password = encrypt_password(password)
            session_id = await session_store.create(
                username,
                enc_password,
                roles,
                active_role=roles[0] if roles else None,
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
                "roles": roles,
                "active_role": roles[0] if roles else None,
                "message": "First login — set a new admin password",
            }

        # 4. Block default password after setup
        if setup_done and password == "nova" and username == "nova_admin":
            raise DefaultPasswordError()

        # 5. Normal authenticated session
        roles = await self.get_user_roles(username, password)
        enc_password = encrypt_password(password)
        session_id = await session_store.create(
            username,
            enc_password,
            roles,
            active_role=roles[0] if roles else None,
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
            "roles": roles,
            "active_role": roles[0] if roles else None,
        }

    async def switch_role(self, session_id: str, requested_role: str) -> dict:
        session = await session_store.get(session_id)
        if not session:
            raise InvalidCredentialsError("Session expired")

        roles = session.get("roles", [])
        if requested_role not in roles:
            raise InvalidCredentialsError("Role is not granted to this user")

        reordered_roles = [requested_role, *[role for role in roles if role != requested_role]]
        await session_store.set_active_role(session_id, requested_role, reordered_roles)

        return {
            "roles": reordered_roles,
            "active_role": requested_role,
        }

    async def setup(self, username: str, session_id: str, new_password: str, confirm_password: str) -> dict:
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
            new_session_id = await session_store.create("nova_admin", enc_password, roles)
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

        return {"status": "PASSWORD_CHANGED", "message": "Password updated successfully"}

    async def logout(self, session_id: str) -> None:
        """Delete session from Redis."""
        await session_store.delete(session_id)

    @staticmethod
    def _parse_roles(grants_rows: list) -> list[str]:
        """Parse role names from SHOW GRANTS output.

        StarRocks SHOW GRANTS returns tuples:
          ('user'@'%', None, "GRANT 'ACCOUNTADMIN' TO 'user'@'%'")
          ('user'@'%', None, "GRANT ALL ON *.* TO ROLE analyst")
        """
        roles = []
        for row in grants_rows:
            # Extract the grant text (3rd element of tuple)
            if isinstance(row, (tuple, list)) and len(row) >= 3:
                grant_text = str(row[2])
            else:
                grant_text = str(row)

            upper = grant_text.upper()

            # Pattern 1: GRANT 'ROLENAME' TO 'user' — role assignment
            if grant_text.startswith("GRANT '") and " TO '" in grant_text:
                parts = grant_text.split("'")
                if len(parts) >= 2:
                    role = parts[1].strip()
                    if role and role.upper() not in ("ALL", "SELECT", "INSERT", "UPDATE", "DELETE"):
                        roles.append(role)

            # Pattern 2: GRANT ... TO ROLE rolename — privilege grant
            elif "TO ROLE" in upper:
                parts = upper.split("TO ROLE")
                if len(parts) > 1:
                    role = parts[-1].strip().strip("'\"").strip(")").strip()
                    if role:
                        roles.append(role)

        return roles

    @staticmethod
    def _escape(value: str) -> str:
        """Basic SQL string escaping for identifiers/passwords."""
        return value.replace("'", "\\'").replace("\\", "\\\\")


# Singleton
auth_service = AuthService()
