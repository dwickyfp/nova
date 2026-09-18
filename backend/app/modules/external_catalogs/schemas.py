"""External catalog schemas — Pydantic models for catalog CRUD.

Credential handling rule (AGENTS.md §2, NOVA-62 constraint 1): a request may
carry a storage *connection name* or an explicit storage type, never a secret.
The service resolves the secret from ``nova.yaml`` / env at engine-call time and
the secret never appears on a response model. ``ExternalCatalogResponse``
therefore has no field that can hold a credential.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class CatalogType(StrEnum):
    """GA catalog types Nova exposes (NOVA-62 constraint 5: GA-only)."""

    HIVE = "hive"
    ICEBERG = "iceberg"


class MetastoreType(StrEnum):
    """Metastore selector Nova supports for the GA types."""

    HMS = "hms"
    REST = "rest"


#: Properties that must never be accepted from a client or echoed back. The
#: service fills these from the storage connection; a request that names one is
#: rejected so a caller cannot smuggle a secret into the metadata store or fight
#: the configured connection.
SECRET_PROPERTY_SUFFIXES: tuple[str, ...] = (
    "access_key",
    "secret_key",
    "session_token",
    "account_key",
    "sas_token",
    "service_account_key",
    "shared_key",
    "private_key",
    "password",
    "credential",
)


def is_secret_property(key: str) -> bool:
    """True when ``key`` names a credential-bearing catalog property.

    Matches on the final ``_``/``.``-separated segment so the dotted and
    underscore spellings StarRocks accepts are treated the same.
    """
    return key.lower().rsplit(".", 1)[-1].endswith(SECRET_PROPERTY_SUFFIXES)


class ExternalCatalogCreate(BaseModel):
    """Create an external catalog.

    ``metastore_uri`` / ``metastore_type`` describe the *metadata* endpoint;
    ``storage_connection`` names an entry in ``nova.yaml`` whose credentials are
    resolved server-side. No secret field exists here on purpose.
    """

    name: str = Field(..., min_length=1, max_length=256, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: CatalogType
    metastore_type: MetastoreType = MetastoreType.HMS
    metastore_uri: str = Field(..., min_length=1, max_length=1024)
    storage_connection: str | None = Field(None, max_length=256)
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
