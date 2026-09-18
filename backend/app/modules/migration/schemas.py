"""Migration Connector schemas — v1 Assessment + Dry-run (NOVA-85).

The verdict vocabulary is deliberately small and closed: every enumerated
object is ``migratable``, ``lossy`` or ``skipped``. ``lossy`` and ``skipped``
must carry a ``reason`` so an omission is always declared rather than silent —
a silent omission is a defect, a declared one is the product (NOVA-84 Ruling 2).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Verdict(StrEnum):
    """Per-object dry-run verdict."""

    MIGRATABLE = "migratable"
    LOSSY = "lossy"
    SKIPPED = "skipped"


class ObjectKind(StrEnum):
    """Source object categories the assessment enumerates."""

    DATABASE = "database"
    TABLE = "table"
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"
    TASK = "task"
    PIPE = "pipe"
    FUNCTION = "function"
    MASKING_POLICY = "masking_policy"
    ROW_ACCESS_POLICY = "row_access_policy"
    RBAC = "rbac"
    RESOURCE_GROUP = "resource_group"
    STORAGE_VOLUME = "storage_volume"


class MigrationConnectRequest(BaseModel):
    """Connect to a source StarRocks deployment.

    The source password is a connection secret: it is accepted, used to verify
    reachability, and never persisted in a plaintext form or echoed back.
    """

    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(default=9030, ge=1, le=65535)
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(default="", max_length=4096)
    database: str | None = Field(default=None, max_length=255)


class SourceObject(BaseModel):
    """A single enumerated source object with its dry-run verdict."""

    kind: ObjectKind
    database: str | None = None
    name: str
    verdict: Verdict
    reason: str | None = None


class MigrationConnectResponse(BaseModel):
    """Result of a source connection probe.

    ``connection_id`` is an opaque handle to the server-side credential, not the
    credential itself. The source password is never in this payload.
    """

    connection_id: str
    connected: bool
    server_version: str | None = None
    database_count: int = 0
    message: str = ""


class EnumerateResponse(BaseModel):
    """Enumerated source objects, grouped by kind."""

    database: str | None = None
    objects: list[SourceObject]
    count: int
    #: Materialized views are enumerated from ``materialized_views`` — never
    #: from ``information_schema.tables``. Surfaced so the caller can assert it.
    materialized_view_source: str = "information_schema.materialized_views"


class DryRunRequest(BaseModel):
    """Run an assessment over a source database.

    ``database`` narrows enumeration; omit it to assess every user database.
    """

    database: str | None = Field(default=None, max_length=255)


class DryRunSummary(BaseModel):
    """Verdict counts for a dry-run."""

    migratable: int = 0
    lossy: int = 0
    skipped: int = 0
    total: int = 0


class DryRunResponse(BaseModel):
    """Dry-run report — one row per object plus the aggregate counts.

    ``has_skipped`` and ``has_lossy`` exist so a caller (and the UI) can gate
    the cutover acknowledgement on "there is something to acknowledge" without
    re-deriving it from the rows.
    """

    database: str | None = None
    objects: list[SourceObject]
    summary: DryRunSummary
    has_lossy: bool = False
    has_skipped: bool = False
    generated_at: datetime


class MigrationEngineStatus(BaseModel):
    """Status of the operator-provided ``starrocks-cluster-sync`` binary.

    Nova never bundles or redistributes the engine (NOVA-84 Ruling A). The
    wizard invokes an operator-provided binary and must fail loudly — naming the
    configured path — when it is absent or unreadable, never silently fall back
    or auto-download.
    """

    configured: bool
    available: bool
    path: str
    execute_supported: bool = False
    message: str = ""
