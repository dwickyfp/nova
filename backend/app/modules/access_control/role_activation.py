"""Validated single-role activation for web and MySQL sessions."""

from __future__ import annotations

import re
from dataclasses import dataclass

import asyncmy

from app.common.identifiers import check_identifier
from app.core.config import settings
from app.integrations.ranger.client import RangerClient, ranger_client


class RoleActivationError(PermissionError):
    """The requested role cannot become the session's sole active role."""


@dataclass(frozen=True, slots=True)
class RoleAssignments:
    assigned_roles: tuple[str, ...]
    default_role: str | None


class RoleActivationService:
    def __init__(self, ranger: RangerClient | None = None) -> None:
        self._ranger = ranger or ranger_client

    async def assignments(
        self,
        connection: asyncmy.Connection,
        *,
        known_default_role: str | None = None,
    ) -> RoleAssignments:
        async with connection.cursor() as cur:
            await cur.execute("SHOW GRANTS")
            rows = await cur.fetchall()
        assigned_roles = tuple(sorted(set(parse_assigned_roles(rows))))
        default_role = known_default_role

        # Information Schema dispatches applicable_roles through an FE RPC
        # worker that has no ConnectContext. Strict active-role mode correctly
        # fails that ambiguous authorization check closed. SHOW GRANTS remains
        # local to the authenticated FE session and exposes marker assignments.
        # The already-active role on a fresh login is StarRocks' explicit
        # default marker; a long-lived session supplies the default captured at
        # login so CURRENT_ROLE() cannot redefine it after a role switch.
        if default_role is None:
            async with connection.cursor() as cur:
                await cur.execute("SELECT CURRENT_ROLE()")
                row = await cur.fetchone()
            actual = str(row[0] if isinstance(row, (tuple, list)) else next(iter(row.values())))
            active_names = _active_role_names(actual)
            if len(active_names) == 1:
                default_role = next(iter(active_names))

        if default_role is not None and default_role not in assigned_roles:
            raise RoleActivationError("The configured default role is not assigned")
        return RoleAssignments(assigned_roles, default_role)

    async def activate(
        self,
        connection: asyncmy.Connection,
        *,
        principal: str,
        requested_role: str | None,
        known_default_role: str | None = None,
    ) -> tuple[str, RoleAssignments]:
        assignments = await self.assignments(
            connection, known_default_role=known_default_role
        )
        target = requested_role or assignments.default_role
        if not target:
            raise RoleActivationError("No explicit default role is configured")
        if target.upper() in {"ALL", "NONE", "DEFAULT"} or "," in target:
            raise RoleActivationError("Exactly one named role must be activated")
        if target not in assignments.assigned_roles:
            raise RoleActivationError(f"Requested role '{target}' is not assigned to '{principal}'")
        if settings.RANGER_ENABLED and await self._ranger.get_role(target) is None:
            raise RoleActivationError(
                f"Role '{target}' is not healthy in the authorization provider"
            )

        safe_role = check_identifier(target, field="role")
        async with connection.cursor() as cur:
            await cur.execute(f"SET ROLE {safe_role}")
            await cur.execute("SELECT CURRENT_ROLE()")
            row = await cur.fetchone()
        actual = str(row[0] if isinstance(row, (tuple, list)) else next(iter(row.values())))
        active_names = _active_role_names(actual)
        if active_names != {target}:
            raise RoleActivationError("StarRocks did not confirm exactly the requested active role")
        return target, assignments


def _active_role_names(value: str) -> set[str]:
    return {
        part.strip().strip("`'")
        for part in value.replace("[", "").replace("]", "").split(",")
        if part.strip() and part.strip().strip("`'").upper() not in {"ALL", "NONE", "DEFAULT"}
    }


_ROLE_GRANT = re.compile(
    r"^GRANT\s+(?P<roles>'(?:[^']|'')+'(?:\s*,\s*'(?:[^']|'')+')*)\s+TO\s+'",
    re.IGNORECASE,
)
_QUOTED_ROLE = re.compile(r"'((?:[^']|'')+)'")


def parse_assigned_roles(grant_rows: object) -> list[str]:
    """Extract only StarRocks role-marker assignments from SHOW GRANTS rows."""
    roles: list[str] = []
    for row in grant_rows if isinstance(grant_rows, (list, tuple)) else ():
        grant_text = str(row[2]) if isinstance(row, (list, tuple)) and len(row) >= 3 else str(row)
        match = _ROLE_GRANT.match(grant_text.strip())
        if match:
            roles.extend(
                name.replace("''", "'")
                for name in _QUOTED_ROLE.findall(match.group("roles"))
            )
    return roles


role_activation_service = RoleActivationService()
