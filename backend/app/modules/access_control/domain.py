"""Provider-neutral access-control domain objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ProjectionState(StrEnum):
    PENDING = "PENDING"
    PROVISIONING = "PROVISIONING"
    PROPAGATING = "PROPAGATING"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"
    DRIFTED = "DRIFTED"
    DELETING = "DELETING"


class ResourceCapability(StrEnum):
    TABLE = "TABLE"
    COLUMN = "COLUMN"
    VIEW = "VIEW"
    MATERIALIZED_VIEW = "MATERIALIZED_VIEW"
    ROW_FILTER = "ROW_FILTER"
    MASKING = "MASKING"
    CATALOG = "CATALOG"
    DATABASE = "DATABASE"
    SYSTEM = "SYSTEM"


@dataclass(frozen=True, slots=True)
class RoleProjection:
    name: str
    state: ProjectionState
    marker_exists: bool
    ranger_exists: bool
    message: str | None = None


@dataclass(frozen=True, slots=True)
class DataScopeBinding:
    dimension_key: str
    column: str
    values: tuple[str, ...] = field(default_factory=tuple)


class AuthorizationCapabilityRegistry:
    """Explicitly gate resources supported by the pinned service definition."""

    _SUPPORTED = frozenset(ResourceCapability)

    @classmethod
    def require(cls, capability: ResourceCapability | str) -> ResourceCapability:
        try:
            normalized = ResourceCapability(str(capability).upper())
        except ValueError as exc:
            raise ValueError(
                f"Resource type '{capability}' is not supported by the Ranger provider"
            ) from exc
        if normalized not in cls._SUPPORTED:
            raise ValueError(
                f"Resource type '{capability}' is not supported by the Ranger provider"
            )
        return normalized
