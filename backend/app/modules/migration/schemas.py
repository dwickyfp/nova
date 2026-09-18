"""Migration Connector schemas — request/response models for assessment + dry-run.

The shapes here are the API contract. Two invariants are enforced by construction:

* No field can carry a credential **value**. A source cluster is registered by
  address plus a ``secret_ref``; the password is resolved server-side from the
  configured secret store at call time and never echoed.
* A ``DryRunItem`` verdict is one of ``migratable`` / ``lossy`` / ``skipped`` and
  always carries a ``reason``. ``lossy`` and ``skipped`` are not errors — they are
  the report the operator must see before any future cutover.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MigrationVerdict(StrEnum):
    """Per-object dry-run verdict."""

    MIGRATABLE = "migratable"
    LOSSY = "lossy"
    SKIPPED = "skipped"


class ObjectKind(StrEnum):
    """Source object families the connector enumerates."""

    TABLE = "table"
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"
    FUNCTION = "function"
    TASK = "task"
    PIPE = "pipe"
    MASKING_POLICY = "masking_policy"
    ROW_ACCESS_POLICY = "row_access_policy"


class SourceConnectionRequest(BaseModel):
    """Register a **source StarRocks cluster** to assess.

    The cluster is addressed by ``host``/``port``/``username``. Its password is
    never accepted here: ``secret_ref`` names a credential in the configured
    secret store, and the value is resolved server-side at call time. An empty
    ``secret_ref`` means "no password configured" (the same convention the local
    engine uses).
    """

    name: str = Field(..., min_length=1, max_length=256)
    host: str = Field(..., min_length=1, max_length=256)
    port: int = Field(default=9030, ge=1, le=65535)
    username: str = Field(default="root", min_length=1, max_length=128)
    secret_ref: str = Field(default="", max_length=1024)
    comment: str = Field(default="", max_length=1024)


class SourceConnectionResponse(BaseModel):
    """A registered source cluster — address only, never a secret value.

    ``secret_ref`` is echoed (an operator must be able to see *which* reference
    is configured) but it is a reference, not a credential.
    """

    id: str
    name: str
    host: str
    port: int
    username: str
    secret_ref: str = ""
    comment: str = ""
    created_at: datetime | None = None
    created_by: str | None = None


class SourceConnectionListResponse(BaseModel):
    connections: list[SourceConnectionResponse]
    count: int


class EnumerateRequest(BaseModel):
    """Enumerate the objects of one database on a registered source cluster.

    ``source`` is required: enumerate reads the **source**, never the local
    engine.
    """

    source: str = Field(..., min_length=1, max_length=256)
    database: str = Field(..., min_length=1, max_length=256)


class SourceObject(BaseModel):
    """One object discovered on the source cluster."""

    name: str
    kind: ObjectKind
    database: str
    detail: str | None = Field(
        default=None,
        description="Redacted DDL / definition when available; never a credential.",
    )
    extra: dict[str, str] = Field(default_factory=dict)


class EnumerateResponse(BaseModel):
    database: str
    objects: list[SourceObject]
    count: int


class DryRunRequest(BaseModel):
    """Dry-run assessment over an explicit object selection.

    ``objects`` empty means "assess everything enumeration finds". ``source`` is
    required for the same reason as ``EnumerateRequest``. The dry-run never
    executes anything: it only classifies.
    """

    source: str = Field(..., min_length=1, max_length=256)
    database: str = Field(..., min_length=1, max_length=256)
    objects: list[str] = Field(default_factory=list)


class DryRunItem(BaseModel):
    """A single object's verdict, with the reason a human needs to act on."""

    name: str
    kind: ObjectKind
    verdict: MigrationVerdict
    reason: str
    detail: str | None = None


class DryRunSummary(BaseModel):
    migratable: int = 0
    lossy: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.migratable + self.lossy + self.skipped


class DryRunResponse(BaseModel):
    database: str
    items: list[DryRunItem]
    summary: DryRunSummary
    engine_available: bool
    engine_path: str | None = None


class EngineStatusResponse(BaseModel):
    """Whether the optional ``starrocks-cluster-sync`` binary is usable.

    Nova never bundles or redistributes the binary (its license is undeclared);
    the operator provides it at a configured path. ``available`` is False and
    ``reason`` explains the typed error when it is missing.
    """

    available: bool
    configured_path: str | None = None
    resolved_path: str | None = None
    reason: str | None = None
