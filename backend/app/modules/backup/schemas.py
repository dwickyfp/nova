"""Backup & recovery schemas — snapshots, repositories, recycle bin.

These are *privileged* operations (``docs/21-backup-recovery.md``): backup and
restore require the engine's ``REPOSITORY ON SYSTEM`` privilege, and recovery
from the recycle bin touches dropped objects. The schemas shape the request;
authorization is enforced in the router and again by the engine, never by the
UI.

No field here holds a storage credential. A ``CREATE REPOSITORY`` request names
a storage *connection* (``docs/14-storage-connections.md``) whose credentials
are resolved server-side, exactly as ``@stage`` and external catalogs do.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

#: Engine identifiers Nova accepts. Snapshot/database/table/repository names are
#: interpolated into DDL, so a path segment is validated too.
NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
#: A snapshot's qualified name is ``<db>.<snapshot>``.
QUALIFIED_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$"

BACKUP_TYPES = ("full", "incremental")


class SnapshotCreate(BaseModel):
    """``BACKUP SNAPSHOT <db>.<label> TO <repo> ON (...) PROPERTIES(...)``.

    ``tables`` empty means the whole database (``ON (DATABASE <db>)``), which is
    how the doc spells a full-database backup.
    """

    database: str = Field(..., min_length=1, max_length=256, pattern=NAME_PATTERN)
    label: str = Field(..., min_length=1, max_length=256)
    repository: str = Field(..., min_length=1, max_length=256, pattern=NAME_PATTERN)
    tables: list[str] = Field(default_factory=list, max_length=512)
    backup_type: str = Field("full", pattern="^(full|incremental)$")
    timeout: int = Field(3600, ge=1, le=86400)


class SnapshotRestore(BaseModel):
    """``RESTORE SNAPSHOT <db>.<label> FROM <repo> ON (...) PROPERTIES(...)``.

    ``tables`` empty restores the database; ``backup_timestamp`` selects a
    point-in-time when the snapshot has several. ``allow_overwrite`` refuses to
    silently clobber an existing partition by default.
    """

    database: str = Field(..., min_length=1, max_length=256, pattern=NAME_PATTERN)
    label: str = Field(..., min_length=1, max_length=256)
    repository: str = Field(..., min_length=1, max_length=256, pattern=NAME_PATTERN)
    tables: list[str] = Field(default_factory=list, max_length=512)
    backup_timestamp: str | None = Field(None, max_length=64)
    allow_overwrite: bool = False
    replication_num: int = Field(1, ge=1, le=10)


class RepositoryCreate(BaseModel):
    """``CREATE REPOSITORY <name> WITH BROKER ON LOCATION <path>``.

    ``location`` is the storage path from the named connection; credentials are
    resolved server-side from ``storage_connection`` and never accepted here.
    """

    name: str = Field(..., min_length=1, max_length=256, pattern=NAME_PATTERN)
    storage_connection: str = Field(..., min_length=1, max_length=256)
    location: str = Field(..., min_length=1, max_length=2048)


class RecoverRequest(BaseModel):
    """``RECOVER TABLE|DATABASE <name> [AS <new_name>]``."""

    object_type: str = Field(..., pattern="^(table|database)$")
    database: str | None = Field(None, max_length=256, pattern=NAME_PATTERN)
    name: str = Field(..., min_length=1, max_length=256, pattern=NAME_PATTERN)
    new_name: str | None = Field(None, max_length=256, pattern=NAME_PATTERN)


class SnapshotResponse(BaseModel):
    name: str
    database: str | None = None
    snapshot_type: str | None = None
    status: str | None = None
    state: str | None = None
    created_at: datetime | None = None
    raw: dict[str, str] = Field(default_factory=dict)


class SnapshotListResponse(BaseModel):
    snapshots: list[SnapshotResponse]
    count: int


class RepositoryResponse(BaseModel):
    name: str
    location: str | None = None
    raw: dict[str, str] = Field(default_factory=dict)


class RecycleBinItem(BaseModel):
    name: str
    object_type: str | None = None
    database: str | None = None
    drop_time: datetime | None = None
    raw: dict[str, str] = Field(default_factory=dict)


class RecycleBinResponse(BaseModel):
    items: list[RecycleBinItem]
    count: int
