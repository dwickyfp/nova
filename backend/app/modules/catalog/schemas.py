"""Nova Catalog schemas: catalog requests and the catalog store's records.

Credential handling rule (AGENTS.md §2, NOVA-62 constraint 1): a request may
carry a connection *name*, never a secret. A connection's credential is entered
once through the write-only credential flow and resolved server-side at
engine-call time; no response model has a field that can hold a credential.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.common.sql_guard import is_credential_property


#: Properties that must never be accepted from a client or echoed back. The
#: service fills these from the storage connection; a request that names one is
#: rejected so a caller cannot smuggle a secret into the metadata store or fight
#: the configured connection.
#:
#: The name-matching rule lives in ``sql_guard`` — see
#: ``is_credential_property`` — so the request-side check and the statement
#: redactor share one segmentation rule. Each spelling StarRocks accepts for a
#: credential (``azure.account.key`` / ``azure.account_key``,
#: ``gcp.gcs.service_account_private_key`` / ``...service.account.private.key``)
#: is one name to both sides; matching only the final segment left the dotted
#: spelling accepted here while the redactor already knew it was a credential.
def is_secret_property(key: str) -> bool:
    """True when ``key`` names a credential-bearing catalog property.

    Thin alias for :func:`app.common.sql_guard.is_credential_property`, kept so
    callers import the name the catalog module talks about.
    """
    return is_credential_property(key)


class ExternalCatalogAlter(BaseModel):
    """Alter a catalog. Only engine-supported property updates are allowed."""

    metastore_uri: str | None = Field(None, min_length=1, max_length=1024)
    comment: str | None = Field(None, max_length=1024)
    properties: dict[str, str] = Field(default_factory=dict)

    @field_validator("properties")
    @classmethod
    def _reject_secrets(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if is_secret_property(key):
                raise ValueError(
                    f"property '{key}' carries a credential and must not be set "
                    "on the request; configure it on the storage connection"
                )
        return value


class ExternalCatalogResponse(BaseModel):
    """Catalog detail. Deliberately has no credential-bearing field.

    ``properties`` is the *redacted* form; ``create_statement`` is the redacted
    ``SHOW CREATE CATALOG`` output. Both pass through the guard's redactor before
    reaching this model.
    """

    name: str
    type: str
    metastore_type: str | None = None
    metastore_uri: str | None = None
    storage_connection: str | None = None
    comment: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)
    create_statement: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None


class ExternalCatalogListResponse(BaseModel):
    catalogs: list[ExternalCatalogResponse]
    count: int


class CatalogTableSummary(BaseModel):
    """A table inside an external catalog."""

    name: str
    database: str
    comment: str | None = None


# ---------------------------------------------------------------------------
# Catalog store records (NOVA_SYSTEM.CATALOG_*)
# ---------------------------------------------------------------------------


class CatalogColumn(BaseModel):
    """One column of a stored object, as the last sync saw it."""

    ordinal: int
    name: str
    data_type: str | None = None
    is_nullable: bool | None = None
    comment: str | None = None


class CatalogObject(BaseModel):
    """A database, table, or view the catalog store has recorded."""

    catalog_name: str
    database_name: str
    object_name: str
    object_type: str
    engine: str | None = None
    comment: str | None = None
    row_count: int | None = None
    data_size: int | None = None
    last_seen_at: datetime | None = None


class CatalogSummary(BaseModel):
    """A catalog the caller can see, with what the store knows about it.

    ``managed`` is true for catalogs created through Nova; ``source`` says who
    first recorded it (``nova`` or ``engine``, for a catalog made in raw SQL).
    ``status`` is the last sync's outcome. No field can carry a credential.
    """

    name: str
    kind: str
    catalog_type: str
    managed: bool = False
    source: str = "engine"
    comment: str | None = None
    status: str | None = None
    last_sync_at: datetime | None = None
    last_sync_error: str | None = None
    databases: list[str] = Field(default_factory=list)


class CatalogDetail(CatalogSummary):
    """A catalog with its Nova-side definition and redacted engine DDL."""

    metastore_type: str | None = None
    metastore_uri: str | None = None
    storage_connection: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)
    create_statement: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None


class CatalogListResponse(BaseModel):
    catalogs: list[CatalogSummary]
    count: int


class SyncRun(BaseModel):
    """One metadata sync of one catalog. ``error`` is already redacted."""

    id: str
    catalog_name: str
    trigger: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    error: str | None = None


class SyncRunListResponse(BaseModel):
    runs: list[SyncRun]


# ---------------------------------------------------------------------------
# Connections and foreign catalogs (phase 2)
# ---------------------------------------------------------------------------

_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


class ConnectionCredentials(BaseModel):
    """Write-only credential entry. Stored Fernet-encrypted; never read back."""

    username: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., min_length=1, max_length=1024, repr=False)


class ConnectionCreate(BaseModel):
    """Register an external source.

    ``secret_ref`` names a credential in a secret store (``aws://...`` or an
    AWS Secrets Manager ARN). Alternatively ``credentials`` are entered here and
    stored encrypted by Nova, and the connection's reference becomes
    ``nova://<name>``. Exactly one of the two is given.
    """

    name: str = Field(..., min_length=1, max_length=256, pattern=_NAME_PATTERN)
    connector_type: str = Field(..., min_length=1, max_length=64)
    endpoint: str = Field(..., min_length=1, max_length=1024)
    secret_ref: str | None = Field(None, max_length=1024)
    credentials: ConnectionCredentials | None = None
    comment: str | None = Field(None, max_length=1024)


class ConnectionSummary(BaseModel):
    """A connection as users see it: no endpoint, no reference, no credential.

    ``credential_source`` is ``managed`` (entered through Nova) or ``external``
    (a secret store reference); ``has_credentials`` is false only for a managed
    connection whose credentials were not entered yet.
    """

    name: str
    connector_type: str
    credential_source: str
    has_credentials: bool
    owner: str | None = None
    comment: str | None = None
    catalogs: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ConnectionListResponse(BaseModel):
    connections: list[ConnectionSummary]
    count: int


class ForeignCatalogCreate(BaseModel):
    """Create a queryable catalog from a registered connection."""

    name: str = Field(..., min_length=1, max_length=256, pattern=_NAME_PATTERN)
    connection: str = Field(..., min_length=1, max_length=256, pattern=_NAME_PATTERN)
    comment: str | None = Field(None, max_length=1024)
    properties: dict[str, str] = Field(default_factory=dict)

    @field_validator("properties")
    @classmethod
    def _reject_secrets(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if is_secret_property(key):
                raise ValueError(
                    f"property '{key}' carries a credential; it comes from the connection"
                )
        return value
