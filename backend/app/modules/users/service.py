"""StarRocks-backed user and role administration service."""

import logging
import re
import secrets
import string
from collections import defaultdict

import asyncmy
import asyncmy.cursors

from app.common.sql_guard import normalize_role_name
from app.core.config import settings
from app.core.database import db

logger = logging.getLogger(__name__)

PROTECTED_USERS = {"root", "nova_admin"}
PROTECTED_ROLES = {"ACCOUNTADMIN"}
BUILT_IN_ROLES = {
    "db_admin",
    "cluster_admin",
    "user_admin",
    "security_admin",
    "public",
    "root",
    "ACCOUNTADMIN",
}
class UserService:
    """Manage StarRocks users, roles, memberships, and privileges."""

    @staticmethod
    async def _root_connect() -> asyncmy.Connection:
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
        return value.replace("\\", "\\\\").replace("'", "\\'")

    @classmethod
    def _quote_ident(cls, value: str) -> str:
        safe = value.replace("`", "``")
        return f"`{safe}`"

    @classmethod
    def _user_identity(cls, username: str, host: str = "%") -> str:
        return f"'{cls._escape(username)}'@'{cls._escape(host)}'"

    @staticmethod
    def _parse_identity(raw: str) -> dict[str, str]:
        identity = raw.strip()
        if "@" not in identity:
            username = identity.strip("'`\"")
            return {
                "username": username,
                "host": "%",
                "identity": f"'{username}'@'%'",
            }

        user_part, host_part = identity.split("@", 1)
        username = user_part.strip("'`\"")
        host = host_part.strip("'`\"")
        return {
            "username": username,
            "host": host or "%",
            "identity": f"'{username}'@'{host or '%'}'",
        }

    @staticmethod
    def _normalize_auth_mode(plugin: str | None, password_enabled: bool) -> str:
        normalized = (plugin or "").upper()
        if normalized == "AUTHENTICATION_LDAP_SIMPLE":
            return "ldap_simple"
        if normalized == "MYSQL_NATIVE_PASSWORD":
            return "mysql_native_password"
        if normalized == "AUTHENTICATION_JWT":
            return "jwt"
        if normalized == "AUTHENTICATION_OAUTH2":
            return "oauth2"
        if password_enabled:
            return "native_password"
        return "none"

    @staticmethod
    def _normalize_properties(rows: list[list]) -> dict[str, str]:
        properties: dict[str, str] = {}
        for row in rows:
            if len(row) >= 2:
                properties[str(row[0])] = "" if row[1] is None else str(row[1])
        return properties

    def _normalize_role_name(self, role: str) -> str:
        """Reduce a role name to its bare identifier form.

        Delegates to the guard's own unquoting rather than re-deriving the
        rules: this value is compared against ``PROTECTED_ROLES``, and a second
        spelling of ``ACCOUNTADMIN`` that only this side knows about would be a
        mismatch between SQL guards and administration metadata.
        """
        return normalize_role_name(role)

    def _protect_user(self, username: str) -> None:
        if username in PROTECTED_USERS:
            raise PermissionError(f"Cannot modify protected user '{username}'")

    def _role_flags(self, role_name: str) -> dict[str, bool]:
        normalized = role_name.lower()
        is_builtin = normalized in {role.lower() for role in BUILT_IN_ROLES}
        is_protected = self._normalize_role_name(role_name).upper() in PROTECTED_ROLES
        return {
            "is_builtin": is_builtin,
            "is_protected": is_protected,
            "is_mutable": not is_builtin and not is_protected,
        }

    async def list_users(self) -> list[dict]:
        conn = await self._root_connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute("SHOW USERS")
                user_rows = await cur.fetchall()

                await cur.execute("SHOW ALL AUTHENTICATION")
                auth_rows = await cur.fetchall()

                await cur.execute(
                    "SELECT FROM_ROLE, TO_USER FROM sys.role_edges WHERE TO_USER IS NOT NULL"
                )
                role_edges = await cur.fetchall()

                await cur.execute(
                    """
                    SELECT user_name, MAX(event_time) AS last_login
                    FROM NOVA_SYSTEM.AUDIT_LOG
                    WHERE event_type = 'login' AND status = 'SUCCESS'
                    GROUP BY user_name
                    """
                )
                login_rows = await cur.fetchall()
        finally:
            conn.close()

        auth_map: dict[str, dict] = {}
        for row in auth_rows:
            identity_raw = str(row.get("UserIdentity") or row.get("useridentity") or "")
            if not identity_raw:
                continue
            auth_map[identity_raw] = {
                "password_enabled": str(row.get("Password") or "").upper() == "YES",
                "auth_plugin": (
                    None if row.get("AuthPlugin") in (None, "NULL") else str(row.get("AuthPlugin"))
                ),
                "plugin_user": None
                if row.get("UserForAuthPlugin") in (None, "NULL")
                else str(row.get("UserForAuthPlugin")),
            }

        roles_by_identity: dict[str, list[str]] = defaultdict(list)
        for row in role_edges:
            identity_raw = str(row.get("TO_USER") or "")
            role_raw = row.get("FROM_ROLE")
            if not identity_raw or not role_raw:
                continue
            roles_by_identity[identity_raw].append(self._normalize_role_name(str(role_raw)))

        last_login_by_user: dict[str, str] = {}
        for row in login_rows:
            user_name = row.get("user_name")
            last_login = row.get("last_login")
            if user_name and last_login:
                last_login_by_user[str(user_name)] = str(last_login)

        users: list[dict] = []
        for row in user_rows:
            raw_identity = str(row.get("User") or row.get("user") or list(row.values())[0])
            parsed = self._parse_identity(raw_identity)
            auth = auth_map.get(parsed["identity"], {})
            properties = await self.get_user_properties(parsed["username"])
            default_roles = await self.get_user_default_roles(parsed["username"], parsed["host"])

            users.append(
                {
                    **parsed,
                    "is_protected": parsed["username"] in PROTECTED_USERS,
                    "roles": sorted(set(roles_by_identity.get(parsed["identity"], []))),
                    "default_roles": default_roles["roles"],
                    "default_role_mode": default_roles["mode"],
                    "auth_plugin": auth.get("auth_plugin"),
                    "auth_mode": self._normalize_auth_mode(
                        auth.get("auth_plugin"),
                        bool(auth.get("password_enabled")),
                    ),
                    "password_enabled": bool(auth.get("password_enabled")),
                    "last_login": last_login_by_user.get(parsed["username"]),
                    "properties": {
                        "max_user_connections": properties.get("max_user_connections", ""),
                        "catalog": properties.get("catalog", ""),
                        "database": properties.get("database", ""),
                    },
                }
            )

        return sorted(users, key=lambda item: (item["username"].lower(), item["host"].lower()))

    async def user_exists(self, username: str, host: str = "%") -> bool:
        users = await self.list_users()
        return any(
            entry["username"] == username and entry["host"] == host
            for entry in users
        )

    async def reset_password(self, username: str, host: str = "%") -> str:
        self._protect_user(username)
        alphabet = string.ascii_letters + string.digits
        generated_password = "".join(secrets.choice(alphabet) for _ in range(12))
        identity = self._user_identity(username, host)
        safe_password = self._escape(generated_password)
        await db.execute_system(f"ALTER USER {identity} IDENTIFIED BY '{safe_password}'")
        return generated_password

    async def get_user_grants(self, username: str, host: str = "%") -> list[str]:
        identity = self._user_identity(username, host)
        result = await db.execute_system(f"SHOW GRANTS FOR {identity}")
        grants: list[str] = []
        for row in result["rows"]:
            if len(row) >= 3:
                grants.append(str(row[2]))
            elif row:
                grants.append(str(row[-1]))
        return grants

    def _normalize_user_grant(self, grant_sql: str) -> dict | None:
        statement = " ".join(grant_sql.strip().split())
        if not statement:
            return None

        role_match = re.match(
            r"^GRANT\s+(.+?)\s+TO\s+USER\s+'[^']+'@'[^']+'$",
            statement,
            flags=re.IGNORECASE,
        )
        if role_match:
            role_name = self._normalize_role_name(role_match.group(1))
            if role_name.lower() == "root":
                return None
            return {
                "name": role_name,
                "granted_at": None,
                "granted_to": "USER",
                "granted_on": "ROLE",
                "granted_by": "SYSTEM",
                "grant_type": "ROLE",
            }

        object_match = re.match(
            r"^GRANT\s+.+?\s+ON\s+(.+?)\s+TO\s+USER\s+'[^']+'@'[^']+'(?:\s+WITH\s+GRANT\s+OPTION)?$",
            statement,
            flags=re.IGNORECASE,
        )
        if object_match:
            object_target = object_match.group(1).strip()
            parts = object_target.split(None, 1)
            granted_on = parts[0].upper() if parts else "OBJECT"
            name = parts[1].strip() if len(parts) > 1 else object_target
            return {
                "name": name,
                "granted_at": None,
                "granted_to": "USER",
                "granted_on": granted_on,
                "granted_by": "SYSTEM",
                "grant_type": "PRIVILEGE",
            }

        return {
            "name": statement,
            "granted_at": None,
            "granted_to": "USER",
            "granted_on": "UNKNOWN",
            "granted_by": "SYSTEM",
            "grant_type": "UNKNOWN",
        }

    async def get_user_detail(self, username: str, host: str = "%") -> dict:
        users = await self.list_users()
        user_entry = next(
            (
                entry
                for entry in users
                if entry["username"] == username and entry["host"] == host
            ),
            None,
        )

        if user_entry is None:
            raise ValueError(f"User '{username}@{host}' not found")

        raw_grants = await self.get_user_grants(username, host=host)
        grants = [
            normalized
            for normalized in (self._normalize_user_grant(grant) for grant in raw_grants)
            if normalized is not None
        ]

        return {
            **user_entry,
            "grants": grants,
        }

    async def get_user_properties(self, username: str) -> dict[str, str]:
        safe_user = self._escape(username)
        result = await db.execute_system(f"SHOW PROPERTY FOR '{safe_user}'")
        return self._normalize_properties(result["rows"])

    async def get_user_authentication(self, username: str, host: str = "%") -> dict:
        safe_user = self._escape(username)
        result = await db.execute_system(f"SHOW AUTHENTICATION FOR {safe_user}")

        row = result["rows"][0] if result["rows"] else []
        password_enabled = bool(row and str(row[1]).upper() == "YES")
        auth_plugin = None if not row or row[2] in (None, "NULL") else str(row[2])
        plugin_user = None if not row or row[3] in (None, "NULL") else str(row[3])
        identity = str(row[0]) if row else self._user_identity(username, host)
        parsed = self._parse_identity(identity)
        return {
            "username": parsed["username"],
            "host": parsed["host"],
            "identity": parsed["identity"],
            "password_enabled": password_enabled,
            "auth_plugin": auth_plugin,
            "auth_mode": self._normalize_auth_mode(auth_plugin, password_enabled),
            "plugin_user": plugin_user,
        }

    async def get_user_default_roles(self, username: str, host: str = "%") -> dict:
        safe_user = self._escape(username)
        safe_host = self._escape(host)

        try:
            result = await db.execute_system(
                "SELECT ROLE_NAME, IS_DEFAULT, IS_MANDATORY "
                "FROM information_schema.applicable_roles "
                f"WHERE USER = '{safe_user}' AND HOST = '{safe_host}'"
            )
        except Exception:
            logger.exception(
                "Failed to query information_schema.applicable_roles for %s@%s", username, host
            )
            return {
                "username": username,
                "host": host,
                "identity": self._user_identity(username, host),
                "mode": "none",
                "roles": [],
            }

        default_roles: list[str] = []
        assigned_roles = 0
        for row in result["rows"]:
            if len(row) < 2:
                continue
            assigned_roles += 1
            role_name = str(row[0])
            is_default = str(row[1]).upper() == "YES"
            is_mandatory = len(row) >= 3 and str(row[2]).upper() == "YES"
            if is_default or is_mandatory:
                default_roles.append(role_name)

        mode: str = "none"
        if assigned_roles and default_roles:
            mode = "all" if len(default_roles) == assigned_roles else "explicit"

        return {
            "username": username,
            "host": host,
            "identity": self._user_identity(username, host),
            "mode": mode,
            "roles": sorted(set(default_roles)),
        }

    async def create_user(
        self,
        username: str,
        password: str,
        host: str = "%",
        max_user_connections: int | None = None,
        catalog: str | None = None,
        database: str | None = None,
        session_properties: dict[str, str] | None = None,
    ) -> None:
        session_properties = session_properties or {}

        identity = self._user_identity(username, host)
        safe_pass = self._escape(password)
        await db.execute_system(f"CREATE USER {identity} IDENTIFIED BY '{safe_pass}'")

        await self._apply_user_properties(
            username=username,
            host=host,
            max_user_connections=max_user_connections,
            catalog=catalog,
            database=database,
            session_properties=session_properties,
            clear_properties=[],
        )

    async def update_user(
        self,
        username: str,
        host: str = "%",
        password: str | None = None,
        default_role_mode: str | None = None,
        default_roles: list[str] | None = None,
        max_user_connections: int | None = None,
        catalog: str | None = None,
        database: str | None = None,
        session_properties: dict[str, str] | None = None,
        clear_properties: list[str] | None = None,
    ) -> list[str]:
        self._protect_user(username)

        executed: list[str] = []
        default_roles = default_roles or []
        session_properties = session_properties or {}
        clear_properties = clear_properties or []
        identity = self._user_identity(username, host)

        if password is not None:
            safe_pass = self._escape(password)
            await db.execute_system(f"ALTER USER {identity} IDENTIFIED BY '{safe_pass}'")
            executed.append("ALTER USER IDENTIFIED BY")

        if default_role_mode is not None:
            await self._apply_default_roles(username, host, default_role_mode, default_roles)
            executed.append(f"SET DEFAULT ROLE {default_role_mode}")

        property_sql = await self._apply_user_properties(
            username=username,
            host=host,
            max_user_connections=max_user_connections,
            catalog=catalog,
            database=database,
            session_properties=session_properties,
            clear_properties=clear_properties,
        )
        executed.extend(property_sql)

        if not executed:
            raise ValueError("No update fields provided")

        return executed

    async def drop_user(self, username: str, host: str = "%") -> None:
        self._protect_user(username)
        if not await self.user_exists(username, host=host):
            logger.info("Skip DROP USER because %s@%s does not exist", username, host)
            return
        identity = self._user_identity(username, host)
        await db.execute_system(f"DROP USER {identity}")

    async def list_roles(self) -> list[dict]:
        result = await db.execute_system("SHOW ROLES")
        roles: list[dict] = []
        for row in result["rows"]:
            if not row:
                continue
            name = str(row[0])
            flags = self._role_flags(name)
            roles.append({"name": name, **flags})
        return sorted(roles, key=lambda item: item["name"].lower())

    async def get_role_members(self, name: str) -> dict:
        safe_name = self._escape(name)
        granted_to_users_result = await db.execute_system(
            "SELECT TO_USER FROM sys.role_edges "
            f"WHERE FROM_ROLE = '{safe_name}' AND TO_USER IS NOT NULL"
        )
        nested_roles_result = await db.execute_system(
            "SELECT FROM_ROLE FROM sys.role_edges "
            f"WHERE TO_ROLE = '{safe_name}'"
        )
        parent_roles_result = await db.execute_system(
            "SELECT TO_ROLE FROM sys.role_edges "
            f"WHERE FROM_ROLE = '{safe_name}' AND TO_ROLE IS NOT NULL"
        )

        users = []
        for row in granted_to_users_result["rows"]:
            if not row or not row[0]:
                continue
            parsed = self._parse_identity(str(row[0]))
            users.append(parsed)

        nested_roles = [str(row[0]) for row in nested_roles_result["rows"] if row and row[0]]
        parent_roles = [str(row[0]) for row in parent_roles_result["rows"] if row and row[0]]

        return {
            "users": users,
            "nested_roles": sorted(set(nested_roles)),
            "parent_roles": sorted(set(parent_roles)),
        }

    async def get_role_detail(self, name: str) -> dict:
        flags = self._role_flags(name)
        members = await self.get_role_members(name)

        return {
            "name": name,
            **flags,
            "members": members,
        }

    async def list_databases(self) -> list[str]:
        result = await db.execute_system("SHOW DATABASES")
        return [str(row[0]) for row in result["rows"]]

    async def list_tables(self, database: str) -> list[str]:
        result = await db.execute_system(f"SHOW TABLES FROM {self._quote_ident(database)}")
        return [str(row[0]) for row in result["rows"]]

    async def _apply_default_roles(
        self,
        username: str,
        host: str,
        mode: str,
        roles: list[str],
    ) -> None:
        from .schemas import validate_ranger_default

        validate_ranger_default(mode, roles)
        identity = self._user_identity(username, host)
        joined_roles = ", ".join(self._quote_ident(role) for role in roles)
        await db.execute_system(f"SET DEFAULT ROLE {joined_roles} TO {identity}")

    async def set_default_roles(
        self, username: str, host: str, mode: str, roles: list[str]
    ) -> None:
        """Set marker-role defaults after centralized membership provisioning."""
        await self._apply_default_roles(username, host, mode, roles)

    async def _apply_user_properties(
        self,
        username: str,
        host: str,
        max_user_connections: int | None,
        catalog: str | None,
        database: str | None,
        session_properties: dict[str, str],
        clear_properties: list[str],
    ) -> list[str]:
        identity = self._user_identity(username, host)
        executed: list[str] = []

        if max_user_connections is not None:
            await db.execute_system(
                f"SET PROPERTY FOR '{self._escape(username)}' "
                f"'max_user_connections'='{int(max_user_connections)}'"
            )
            executed.append("SET PROPERTY max_user_connections")

        property_pairs: list[tuple[str, str]] = []
        if catalog is not None:
            property_pairs.append(("catalog", catalog))
        if database is not None:
            property_pairs.append(("database", database))
        for key, value in session_properties.items():
            normalized_key = key if key.startswith("session.") else f"session.{key}"
            property_pairs.append((normalized_key, value))
        for key in clear_properties:
            property_pairs.append((key, ""))

        if property_pairs:
            rendered = ", ".join(
                f"\"{self._escape(key)}\" = \"{self._escape(value)}\""
                for key, value in property_pairs
            )
            await db.execute_system(f"ALTER USER {identity} SET PROPERTIES ({rendered})")
            executed.append("ALTER USER SET PROPERTIES")

        return executed


user_service = UserService()
