"""The execution identity required by every user-data operation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Literal


class SecurityContextError(ValueError):
    """Security state is missing or ambiguous and execution must stop."""


_RESERVED_ROLE_TOKENS = frozenset({"ALL", "NONE", "DEFAULT"})


@dataclass(frozen=True, slots=True)
class SecurityContext:
    principal: str
    active_role: str
    principal_type: Literal["USER", "SERVICE"] = "USER"
    database: str | None = None
    session_id: str | None = None
    security_context_version: int = 1

    def __post_init__(self) -> None:
        principal = self.principal.strip()
        role = self.active_role.strip()
        if not principal:
            raise SecurityContextError("A principal is required for user-data execution")
        if principal.casefold() == "root" and self.principal_type == "USER":
            raise SecurityContextError("root is not a Nova user-data identity")
        if not role:
            raise SecurityContextError("Exactly one active role is required")
        if "," in role or role.upper() in _RESERVED_ROLE_TOKENS:
            raise SecurityContextError("Exactly one named active role is required")
        if self.security_context_version < 1:
            raise SecurityContextError("Security context version must be positive")
        object.__setattr__(self, "principal", principal)
        object.__setattr__(self, "active_role", role)

    @classmethod
    def from_session(cls, session: dict, *, database: str | None = None) -> SecurityContext:
        active_role = session.get("active_role")
        assigned = session.get("assigned_roles") or session.get("roles") or []
        if not active_role or active_role not in assigned:
            raise SecurityContextError("Session has no valid active role")
        return cls(
            principal=str(session.get("username") or ""),
            active_role=str(active_role),
            database=database,
            session_id=session.get("session_id"),
            security_context_version=int(session.get("security_context_version") or 1),
        )

    def switched(self, role: str) -> SecurityContext:
        return replace(
            self,
            active_role=role,
            security_context_version=self.security_context_version + 1,
        )

    def cache_partition(self, *, policy_fingerprint: str = "") -> str:
        raw = "\x1f".join(
            (
                self.principal,
                self.active_role,
                str(self.security_context_version),
                policy_fingerprint,
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_security_context(
    *,
    principal: str,
    active_role: str | None,
    database: str | None = None,
    session_id: str | None = None,
    security_context_version: int = 1,
) -> SecurityContext:
    return SecurityContext(
        principal=principal,
        active_role=active_role or "",
        database=database,
        session_id=session_id,
        security_context_version=security_context_version,
    )
