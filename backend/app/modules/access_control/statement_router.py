"""Route security SQL to the same service used by the Access Control API."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings
from app.modules.query.repository import QueryResult

from .security_context import SecurityContext
from .service import AccessControlError, AccessControlService, access_control_service

_IDENT = r"(?:`([^`]+)`|'([^']+)'|([A-Za-z_][A-Za-z0-9_]*))"


def _identifier(match: re.Match[str], start: int = 1) -> str:
    for index in range(start, start + 3):
        value = match.group(index)
        if value is not None:
            return value
    raise ValueError("Missing identifier")


_CREATE_ROLE = re.compile(rf"^\s*CREATE\s+ROLE\s+{_IDENT}\s*;?\s*$", re.I)
_DROP_ROLE = re.compile(rf"^\s*DROP\s+ROLE\s+{_IDENT}\s*;?\s*$", re.I)
_ROLE_TO_USER = re.compile(rf"^\s*GRANT\s+{_IDENT}\s+TO\s+USER\s+{_IDENT}\s*;?\s*$", re.I)
_ROLE_FROM_USER = re.compile(rf"^\s*REVOKE\s+{_IDENT}\s+FROM\s+USER\s+{_IDENT}\s*;?\s*$", re.I)
_ROLE_TO_ROLE = re.compile(rf"^\s*GRANT\s+{_IDENT}\s+TO\s+ROLE\s+{_IDENT}\s*;?\s*$", re.I)
_ROLE_FROM_ROLE = re.compile(rf"^\s*REVOKE\s+{_IDENT}\s+FROM\s+ROLE\s+{_IDENT}\s*;?\s*$", re.I)
_ACCESS_TO_ROLE = re.compile(
    rf"^\s*GRANT\s+(SELECT|INSERT|UPDATE|DELETE|ALTER)\s+ON\s+(?:TABLE\s+)?"
    rf"{_IDENT}\.{_IDENT}\s+TO\s+ROLE\s+{_IDENT}\s*;?\s*$",
    re.I,
)
_ACCESS_FROM_ROLE = re.compile(
    rf"^\s*REVOKE\s+(SELECT|INSERT|UPDATE|DELETE|ALTER)\s+ON\s+(?:TABLE\s+)?"
    rf"{_IDENT}\.{_IDENT}\s+FROM\s+ROLE\s+{_IDENT}\s*;?\s*$",
    re.I,
)
_SHOW_ROLE_GRANTS = re.compile(rf"^\s*SHOW\s+GRANTS\s+FOR\s+ROLE\s+{_IDENT}\s*;?\s*$", re.I)
_SHOW_AVAILABLE = re.compile(r"^\s*SHOW\s+AVAILABLE\s+ROLES\s*;?\s*$", re.I)
_SHOW_ROLES = re.compile(r"^\s*SHOW\s+ROLES\s*;?\s*$", re.I)
_SHOW_CURRENT = re.compile(r"^\s*SHOW\s+CURRENT\s+ACCESS\s*;?\s*$", re.I)


@dataclass(frozen=True, slots=True)
class RoutedSecurityStatement:
    handled: bool
    result: QueryResult | None = None


class SecurityStatementRouter:
    def __init__(self, service: AccessControlService | None = None) -> None:
        self._service = service or access_control_service

    async def route(
        self,
        sql: str,
        *,
        security: SecurityContext,
    ) -> RoutedSecurityStatement:
        if not settings.RANGER_ENABLED:
            return RoutedSecurityStatement(False)

        if match := _CREATE_ROLE.match(sql):
            role = _identifier(match)
            await self._service.create_role(security, role)
            return self._ok(sql, "CREATE ROLE", role)

        if match := _DROP_ROLE.match(sql):
            role = _identifier(match)
            await self._service.drop_role(security, role)
            return self._ok(sql, "DROP ROLE", role)

        if match := _ROLE_TO_USER.match(sql):
            role = _identifier(match, 1)
            username = _identifier(match, 4)
            await self._service.assign_role(security, role=role, username=username)
            return self._ok(sql, "GRANT ROLE", role)

        if match := _ROLE_FROM_USER.match(sql):
            role = _identifier(match, 1)
            username = _identifier(match, 4)
            await self._service.revoke_role(security, role=role, username=username)
            return self._ok(sql, "REVOKE ROLE", role)

        if match := _ROLE_TO_ROLE.match(sql):
            member_role = _identifier(match, 1)
            parent_role = _identifier(match, 4)
            await self._service.grant_role_to_role(
                security, parent_role=parent_role, member_role=member_role
            )
            return self._ok(sql, "GRANT ROLE TO ROLE", parent_role)

        if match := _ROLE_FROM_ROLE.match(sql):
            member_role = _identifier(match, 1)
            parent_role = _identifier(match, 4)
            await self._service.revoke_role_from_role(
                security, parent_role=parent_role, member_role=member_role
            )
            return self._ok(sql, "REVOKE ROLE FROM ROLE", parent_role)

        if match := _ACCESS_TO_ROLE.match(sql):
            access = match.group(1).upper()
            database = _identifier(match, 2)
            table = _identifier(match, 5)
            role = _identifier(match, 8)
            await self._service.grant_access(
                security,
                role=role,
                catalog="default_catalog",
                database=database,
                table=table,
                accesses=[access],
            )
            return self._ok(sql, "GRANT", role)

        if match := _ACCESS_FROM_ROLE.match(sql):
            access = match.group(1).upper()
            database = _identifier(match, 2)
            table = _identifier(match, 5)
            role = _identifier(match, 8)
            await self._service.revoke_access(
                security,
                role=role,
                catalog="default_catalog",
                database=database,
                table=table,
                accesses=[access],
            )
            return self._ok(sql, "REVOKE", role)

        if match := _SHOW_ROLE_GRANTS.match(sql):
            role = _identifier(match)
            policies = await self._service.list_managed_policies()
            rows = []
            for policy in policies:
                items = policy.get("policyItems", [])
                if not any(role in item.get("roles", []) for item in items):
                    continue
                rows.append([role, policy.get("name"), "Ranger", "ACTIVE"])
            return RoutedSecurityStatement(
                True,
                QueryResult(
                    columns=["Role", "Policy", "Authorization", "PolicyHealth"],
                    rows=rows,
                    row_count=len(rows),
                    original_sql=sql,
                    executed_sql="",
                ),
            )

        if _SHOW_AVAILABLE.match(sql) or _SHOW_ROLES.match(sql):
            roles = await self._service.list_roles()
            rows = [[item.get("name"), "Ranger"] for item in roles]
            return RoutedSecurityStatement(
                True,
                QueryResult(
                    columns=["Role", "Authorization"],
                    rows=rows,
                    row_count=len(rows),
                    original_sql=sql,
                    executed_sql="",
                ),
            )

        if _SHOW_CURRENT.match(sql):
            return RoutedSecurityStatement(
                True,
                QueryResult(
                    columns=["Principal", "Active Role", "Authorization", "Policy Health"],
                    rows=[[security.principal, security.active_role, "Ranger", "Active"]],
                    row_count=1,
                    original_sql=sql,
                    executed_sql="",
                ),
            )

        if re.match(r"^\s*(GRANT|REVOKE|CREATE\s+ROLE|DROP\s+ROLE)\b", sql, re.I):
            raise AccessControlError(
                "Security statement is unsupported in strict Ranger mode; no native grant was sent"
            )
        return RoutedSecurityStatement(False)

    @staticmethod
    def _ok(sql: str, action: str, role: str) -> RoutedSecurityStatement:
        return RoutedSecurityStatement(
            True,
            QueryResult(
                columns=["Action", "Role", "Authorization"],
                rows=[[action, role, "Ranger"]],
                row_count=1,
                original_sql=sql,
                executed_sql="",
            ),
        )


security_statement_router = SecurityStatementRouter()
