"""User Management service — StarRocks user/role CRUD via direct asyncmy connections."""

import logging

import asyncmy
import asyncmy.cursors

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Guard rails ──────────────────────────────────────────────────

PROTECTED_USERS = {"root", "nova_admin"}
PROTECTED_ROLES = {"ACCOUNTADMIN"}


class UserService:
    """Manage StarRocks users and roles.

    Every method opens a fresh root-level asyncmy connection, executes,
    and closes. This keeps the service stateless and avoids pool contention
    with the system pool used elsewhere.
    """

    # ── helpers ──────────────────────────────────────────────────

    @staticmethod
    async def _root_connect() -> asyncmy.Connection:
        """Open a root-level connection to StarRocks."""
        return await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=settings.STARROCKS_ROOT_USER,
            password=settings.STARROCKS_ROOT_PASSWORD,
            autocommit=True,
            connect_timeout=10,
        )

    @staticmethod
    def _escape(value: str) -> str:
        """Basic SQL string escaping for identifiers/passwords."""
        return value.replace("'", "\\'").replace("\\", "\\\\")

    # ── Users ────────────────────────────────────────────────────

    async def list_users(self) -> list[dict]:
        """Return all StarRocks users with their granted roles.

        Executes SHOW USERS, then for each user runs SHOW GRANTS FOR
        to collect role memberships.
        """
        conn = await self._root_connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute("SHOW USERS")
                rows = await cur.fetchall()

                users = []
                for row in rows:
                    raw = row.get("User") or row.get("user") or list(row.values())[0]
                    # StarRocks SHOW USERS returns "'root'@'%'" — extract just username
                    username = self._parse_username(str(raw))
                    try:
                        grants = await self._get_grants_for(cur, username)
                        roles = self._parse_roles(grants)
                    except Exception:
                        roles = []
                    users.append({"username": username, "roles": roles})
                return users
        finally:
            conn.close()

    async def get_user_grants(self, username: str) -> list[str]:
        """Return raw SHOW GRANTS output for a user."""
        conn = await self._root_connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                grants = await self._get_grants_for(cur, username)
                return [str(g) for g in grants]
        finally:
            conn.close()

    async def create_user(self, username: str, password: str) -> None:
        """CREATE USER in StarRocks."""
        safe_user = self._escape(username)
        safe_pass = self._escape(password)
        conn = await self._root_connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"CREATE USER '{safe_user}' IDENTIFIED BY '{safe_pass}'"
                )
        finally:
            conn.close()

    async def drop_user(self, username: str) -> None:
        """DROP USER in StarRocks. Guard: cannot drop protected users."""
        if username in PROTECTED_USERS:
            raise PermissionError(
                f"Cannot drop protected user '{username}'"
            )
        safe_user = self._escape(username)
        conn = await self._root_connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP USER '{safe_user}'")
        finally:
            conn.close()

    # ── Roles ────────────────────────────────────────────────────

    async def assign_role(self, username: str, role: str) -> None:
        """GRANT role TO user."""
        safe_user = self._escape(username)
        safe_role = self._escape(role)
        conn = await self._root_connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"GRANT '{safe_role}' TO '{safe_user}'")
        finally:
            conn.close()

    async def revoke_role(self, username: str, role: str) -> None:
        """REVOKE role FROM user."""
        safe_user = self._escape(username)
        safe_role = self._escape(role)
        conn = await self._root_connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"REVOKE '{safe_role}' FROM '{safe_user}'")
        finally:
            conn.close()

    async def list_roles(self) -> list[dict]:
        """Return all StarRocks roles."""
        conn = await self._root_connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute("SHOW ROLES")
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
        finally:
            conn.close()

    async def create_role(self, name: str) -> None:
        """CREATE ROLE IF NOT EXISTS."""
        safe_name = self._escape(name)
        conn = await self._root_connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE ROLE IF NOT EXISTS '{safe_name}'")
        finally:
            conn.close()

    async def drop_role(self, name: str) -> None:
        """DROP ROLE IF EXISTS. Guard: cannot drop ACCOUNTADMIN."""
        if name.upper() in PROTECTED_ROLES:
            raise PermissionError(
                f"Cannot drop protected role '{name}'"
            )
        safe_name = self._escape(name)
        conn = await self._root_connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP ROLE IF EXISTS '{safe_name}'")
        finally:
            conn.close()

    # ── Internal helpers ─────────────────────────────────────────

    @staticmethod
    def _parse_username(raw: str) -> str:
        """Extract username from StarRocks SHOW USERS format.

        Input:  "'root'@'%'" or "'nova_admin'@'%'"
        Output: "root" or "nova_admin"
        """
        # Strip surrounding quotes and @'%' host part
        if "@" in raw:
            user_part = raw.split("@")[0]
        else:
            user_part = raw
        return user_part.strip("'\"")

    @staticmethod
    async def _get_grants_for(
        cur: asyncmy.cursors.DictCursor, username: str
    ) -> list:
        """Execute SHOW GRANTS FOR and return rows."""
        safe = username.replace("'", "\\'")
        await cur.execute(f"SHOW GRANTS FOR '{safe}'")
        return await cur.fetchall()

    @staticmethod
    def _parse_roles(grants_rows: list) -> list[str]:
        """Extract role names from SHOW GRANTS output."""
        roles = []
        for row in grants_rows:
            grant_text = str(row)
            # Look for "TO ROLE <name>" pattern
            upper = grant_text.upper()
            if "TO ROLE" in upper:
                parts = upper.split("TO ROLE")
                if len(parts) > 1:
                    role = parts[-1].strip().strip("'").strip('"').strip(")").strip()
                    if role:
                        roles.append(role)
        return roles


# Singleton
user_service = UserService()
