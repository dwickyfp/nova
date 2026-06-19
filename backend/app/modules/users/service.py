"""StarRocks-backed user and role administration service."""

import logging
import secrets
import string
from collections import defaultdict
from typing import Literal

import asyncmy
import asyncmy.cursors

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
PRIVILEGE_OPTIONS: dict[str, list[str]] = {
    "SYSTEM": [
        "GRANT",
        "NODE",
        "CREATE RESOURCE",
        "PLUGIN",
        "FILE",
        "BLACKLIST",
        "OPERATE",
        "CREATE EXTERNAL CATALOG",
        "REPOSITORY",
        "CREATE RESOURCE GROUP",
        "CREATE GLOBAL FUNCTION",
        "CREATE STORAGE VOLUME",
        "SECURITY",
        "ALL",
    ],
    "CATALOG": ["USAGE", "CREATE DATABASE", "DROP", "ALTER", "ALL"],
    "DATABASE": [
        "ALTER",
        "DROP",
        "CREATE TABLE",
        "CREATE VIEW",
        "CREATE FUNCTION",
        "CREATE MATERIALIZED VIEW",
        "ALL",
    ],
    "TABLE": ["ALTER", "DROP", "SELECT", "INSERT", "UPDATE", "EXPORT", "DELETE", "ALL"],
    "VIEW": ["SELECT", "ALTER", "DROP", "ALL"],
    "MATERIALIZED VIEW": ["SELECT", "ALTER", "REFRESH", "DROP", "ALL"],
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
        return role.strip().strip("'`\"")

    def _protect_user(self, username: str) -> None:
        if username in PROTECTED_USERS:
            raise PermissionError(f"Cannot modify protected user '{username}'")

    def _protect_role(self, role_name: str) -> None:
        if role_name.upper() in PROTECTED_ROLES:
            raise PermissionError(f"Cannot modify protected role '{role_name}'")

    def _role_flags(self, role_name: str) -> dict[str, bool]:
        normalized = role_name.lower()
        is_builtin = normalized in {role.lower() for role in BUILT_IN_ROLES}
        is_protected = role_name.upper() in PROTECTED_ROLES
        return {
            "is_builtin": is_builtin,
            "is_protected": is_protected,
            "is_mutable": not is_builtin and not is_protected,
        }

    def _validate_privilege(self, scope: str, privilege: str) -> str:
        normalized_scope = scope.upper()
        normalized_privilege = " ".join(privilege.upper().split())
        allowed = PRIVILEGE_OPTIONS.get(normalized_scope, [])
        if normalized_privilege not in allowed:
            raise ValueError(
                f"Privilege '{normalized_privilege}' is not supported for scope '{normalized_scope}'"
            )
        return normalized_privilege

    def _build_privilege_sql(
        self,
        action: Literal["GRANT", "REVOKE"],
        role_name: str,
        privilege: str,
        scope: str,
        selector_mode: str,
        catalog: str | None = None,
        database: str | None = None,
        object_name: str | None = None,
        with_grant_option: bool = False,
    ) -> str:
        normalized_scope = scope.upper()
        normalized_privilege = self._validate_privilege(normalized_scope, privilege)
        role_sql = self._quote_ident(role_name)

        if normalized_scope == "SYSTEM":
            sql = f"{action} {normalized_privilege} ON SYSTEM TO ROLE {role_sql}"
        elif normalized_scope == "CATALOG":
            sql = (
                f"{action} {normalized_privilege} ON CATALOG {self._quote_ident(catalog or '')} "
                f"TO ROLE {role_sql}"
            )
        elif normalized_scope == "DATABASE":
            if selector_mode == "all_databases":
                sql = f"{action} {normalized_privilege} ON ALL DATABASES TO ROLE {role_sql}"
            else:
                sql = (
                    f"{action} {normalized_privilege} ON DATABASE {self._quote_ident(database or '')} "
                    f"TO ROLE {role_sql}"
                )
        else:
            scope_keyword = normalized_scope
            if selector_mode == "all_databases":
                plural = {
                    "TABLE": "TABLES",
                    "VIEW": "VIEWS",
                    "MATERIALIZED VIEW": "MATERIALIZED VIEWS",
                }[normalized_scope]
                sql = (
                    f"{action} {normalized_privilege} ON ALL {plural} IN ALL DATABASES "
                    f"TO ROLE {role_sql}"
                )
            elif selector_mode == "all_in_database":
                plural = {
                    "TABLE": "TABLES",
                    "VIEW": "VIEWS",
                    "MATERIALIZED VIEW": "MATERIALIZED VIEWS",
                }[normalized_scope]
                sql = (
                    f"{action} {normalized_privilege} ON ALL {plural} IN DATABASE "
                    f"{self._quote_ident(database or '')} TO ROLE {role_sql}"
                )
            else:
                sql = (
                    f"{action} {normalized_privilege} ON {scope_keyword} "
                    f"{self._quote_ident(database or '')}.{self._quote_ident(object_name or '')} "
                    f"TO ROLE {role_sql}"
                )

        if action == "GRANT" and with_grant_option:
            sql += " WITH GRANT OPTION"

        return sql

    async def list_users(self) -> list[dict]:
        conn = await self._root_connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute("SHOW USERS")
                user_rows = await cur.fetchall()

                await cur.execute("SHOW ALL AUTHENTICATION")
                auth_rows = await cur.fetchall()

                await cur.execute("SELECT FROM_ROLE, TO_USER FROM sys.role_edges WHERE TO_USER IS NOT NULL")
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
                "auth_plugin": None if row.get("AuthPlugin") in (None, "NULL") else str(row.get("AuthPlugin")),
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
            logger.exception("Failed to query information_schema.applicable_roles for %s@%s", username, host)
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
        granted_roles: list[str] | None = None,
        default_role_mode: str = "none",
        default_roles: list[str] | None = None,
        max_user_connections: int | None = None,
        catalog: str | None = None,
        database: str | None = None,
        session_properties: dict[str, str] | None = None,
    ) -> None:
        granted_roles = granted_roles or []
        default_roles = default_roles or []
        session_properties = session_properties or {}

        identity = self._user_identity(username, host)
        safe_pass = self._escape(password)
        await db.execute_system(f"CREATE USER {identity} IDENTIFIED BY '{safe_pass}'")

        for role in granted_roles:
            await self.assign_role(username, role, host=host)

        await self._apply_default_roles(username, host, default_role_mode, default_roles)

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
        granted_roles_add: list[str] | None = None,
        granted_roles_remove: list[str] | None = None,
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
        granted_roles_add = granted_roles_add or []
        granted_roles_remove = granted_roles_remove or []
        default_roles = default_roles or []
        session_properties = session_properties or {}
        clear_properties = clear_properties or []
        identity = self._user_identity(username, host)

        if password is not None:
            safe_pass = self._escape(password)
            await db.execute_system(f"ALTER USER {identity} IDENTIFIED BY '{safe_pass}'")
            executed.append("ALTER USER IDENTIFIED BY")

        for role in granted_roles_add:
            await self.assign_role(username, role, host=host)
            executed.append(f"GRANT ROLE {role}")

        for role in granted_roles_remove:
            await self.revoke_role(username, role, host=host)
            executed.append(f"REVOKE ROLE {role}")

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
        identity = self._user_identity(username, host)
        await db.execute_system(f"DROP USER {identity}")

    async def assign_role(self, username: str, role: str, host: str = "%") -> None:
        role_sql = self._quote_ident(role)
        identity = self._user_identity(username, host)
        await db.execute_system(f"GRANT {role_sql} TO USER {identity}")

    async def revoke_role(self, username: str, role: str, host: str = "%") -> None:
        role_sql = self._quote_ident(role)
        identity = self._user_identity(username, host)
        await db.execute_system(f"REVOKE {role_sql} FROM USER {identity}")

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

    async def create_role(self, name: str) -> None:
        await db.execute_system(f"CREATE ROLE {self._quote_ident(name)}")

    async def drop_role(self, name: str) -> None:
        self._protect_role(name)
        await db.execute_system(f"DROP ROLE {self._quote_ident(name)}")

    async def get_role_privileges(self, name: str) -> list[dict]:
        safe_name = self._escape(name)
        result = await db.execute_system(
            "SELECT GRANTEE, OBJECT_CATALOG, OBJECT_DATABASE, OBJECT_NAME, "
            "OBJECT_TYPE, PRIVILEGE_TYPE, IS_GRANTABLE "
            f"FROM sys.grants_to_roles WHERE GRANTEE = '{safe_name}'"
        )
        return [dict(zip(result["columns"], row)) for row in result["rows"]]

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

    async def get_role_grants(self, name: str) -> list[str]:
        result = await db.execute_system(f"SHOW GRANTS FOR ROLE {self._quote_ident(name)}")
        grants: list[str] = []
        for row in result["rows"]:
            if len(row) >= 3:
                grants.append(str(row[2]))
            elif row:
                grants.append(str(row[-1]))
        return grants

    async def get_role_detail(self, name: str) -> dict:
        flags = self._role_flags(name)
        privileges = await self.get_role_privileges(name)
        members = await self.get_role_members(name)
        grants = await self.get_role_grants(name)

        return {
            "name": name,
            **flags,
            "privileges": privileges,
            "members": members,
            "grants": grants,
        }

    async def grant_role_to_role(self, role_name: str, member_role: str) -> None:
        self._protect_role(role_name)
        await db.execute_system(
            f"GRANT {self._quote_ident(member_role)} TO ROLE {self._quote_ident(role_name)}"
        )

    async def revoke_role_from_role(self, role_name: str, member_role: str) -> None:
        self._protect_role(role_name)
        await db.execute_system(
            f"REVOKE {self._quote_ident(member_role)} FROM ROLE {self._quote_ident(role_name)}"
        )

    async def grant_role_to_member_user(self, role_name: str, username: str, host: str = "%") -> None:
        self._protect_role(role_name)
        await self.assign_role(username, role_name, host=host)

    async def revoke_role_from_member_user(self, role_name: str, username: str, host: str = "%") -> None:
        self._protect_role(role_name)
        await self.revoke_role(username, role_name, host=host)

    async def grant_privilege(
        self,
        name: str,
        privilege: str,
        scope: str,
        selector_mode: str,
        catalog: str | None = None,
        database: str | None = None,
        object_name: str | None = None,
        with_grant_option: bool = False,
    ) -> str:
        self._protect_role(name)
        sql = self._build_privilege_sql(
            "GRANT",
            name,
            privilege,
            scope,
            selector_mode,
            catalog=catalog,
            database=database,
            object_name=object_name,
            with_grant_option=with_grant_option,
        )
        await db.execute_system(sql)
        return sql

    async def revoke_privilege(
        self,
        name: str,
        privilege: str,
        scope: str,
        selector_mode: str,
        catalog: str | None = None,
        database: str | None = None,
        object_name: str | None = None,
    ) -> str:
        self._protect_role(name)
        sql = self._build_privilege_sql(
            "REVOKE",
            name,
            privilege,
            scope,
            selector_mode,
            catalog=catalog,
            database=database,
            object_name=object_name,
        )
        sql = sql.replace(" TO ROLE ", " FROM ROLE ")
        await db.execute_system(sql)
        return sql

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
        identity = self._user_identity(username, host)
        if mode == "all":
            await db.execute_system(f"SET DEFAULT ROLE ALL TO {identity}")
            return
        if mode == "none":
            await db.execute_system(f"SET DEFAULT ROLE NONE TO {identity}")
            return
        joined_roles = ", ".join(self._quote_ident(role) for role in roles)
        await db.execute_system(f"SET DEFAULT ROLE {joined_roles} TO {identity}")

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
