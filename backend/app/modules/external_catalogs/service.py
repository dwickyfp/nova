"""External catalog service — create/alter/drop/list + external table reads.

Design constraints (NOVA-62, AGENTS.md §2/§5):

- **RBAC is StarRocks-native.** ``CREATE/ALTER/DROP EXTERNAL CATALOG`` is issued
  on the *caller's* connection through the shared ``QueryService`` pipeline, so
  the engine's own SYSTEM-level privilege decides who may manage catalogs. Nova
  never re-implements catalog permissions.
- **One credential mechanism.** Secret properties are resolved from the existing
  storage connection (``resolve_storage_credentials`` / ``get_storage_connection``)
  and never accepted from or returned to a client.
- **Never echo a secret.** ``SHOW CREATE CATALOG`` is redacted before it leaves
  the service. On StarRocks 4.1.4 the engine masks most secret keys itself but
  returns ``aws.s3.session_token`` and ``hive.metastore.password`` in full, so the
  redactor — not the engine — is the guarantee.
"""

from __future__ import annotations

import logging
import re

from app.common.sql_guard import redact_sql_credentials
from app.core.config import get_storage_connection, settings, to_docker_endpoint
from app.modules.query.dialect.injector import resolve_storage_credentials
from app.modules.query.service import query_service

from .repository import external_catalog_repo
from .schemas import (
    CatalogType,
    ExternalCatalogAlter,
    ExternalCatalogCreate,
    ExternalCatalogResponse,
    MetastoreType,
)

log = logging.getLogger(__name__)

#: Catalog identifiers are interpolated into engine statements on both the
#: caller's connection and the system pool, so they are validated at every entry
#: point — a URL path segment is not trusted just because ``ExternalCatalogCreate``
#: validated the create body. Same shape the Pydantic field enforces.
_CATALOG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ExternalCatalogError(ValueError):
    """A catalog operation the service refused before or after the engine."""


def _safe_catalog_name(name: str) -> str:
    """Return ``name`` if it is a bare catalog identifier, else raise.

    Guards the ``SHOW CREATE CATALOG`` system-pool call and the DROP/ALTER
    statements against an identifier smuggled in through a path parameter.
    """
    if not _CATALOG_NAME.match(name or ""):
        raise ExternalCatalogError(f"Invalid catalog name: {name!r}")
    return name


def build_storage_credential_params(
    storage_connection: str | None = None,
) -> dict[str, str]:
    """Build the ``StorageCredentialParams`` for a catalog from config.

    Reuses ``resolve_storage_credentials`` — the same resolver ``@stage`` uses —
    so a catalog and a stage cannot drift onto two credential mechanisms. The
    ``aws.s3.*`` key names are identical to the FILES() injection.

    Returns an empty dict when the connection has no credentials, so catalog
    creation fails loudly at the engine instead of silently authenticating with
    a well-known default.
    """
    access_key, secret_key = resolve_storage_credentials(storage_connection)
    if not access_key or not secret_key:
        return {}

    connection = get_storage_connection(storage_connection or "")
    params = {
        "aws.s3.access_key": access_key,
        "aws.s3.secret_key": secret_key,
    }
    # StarRocks runs inside Docker and must reach MinIO by its internal name.
    endpoint = to_docker_endpoint(connection.endpoint or settings.S3_ENDPOINT)
    if endpoint:
        params["aws.s3.endpoint"] = endpoint
        params["aws.s3.enable_path_style_access"] = "true"
        if not endpoint.startswith("https"):
            params["aws.s3.enable_ssl"] = "false"
    if connection.region:
        params["aws.s3.region"] = connection.region
    return params


def _quote_literal(value: str) -> str:
    """Single-quote a SQL literal, escaping an embedded quote."""
    return "'" + value.replace("'", "''") + "'"


class ExternalCatalogService:
    """Orchestrate catalog CRUD over the engine + Nova metadata."""

    async def ensure_schema(self) -> None:
        await external_catalog_repo.ensure_schema()

    # ── DDL builders (pure) ────────────────────────────────────

    def build_create_sql(self, body: ExternalCatalogCreate) -> str:
        """Build ``CREATE EXTERNAL CATALOG`` including credentials.

        The returned statement carries real credentials and must only be handed
        to the engine, never returned, logged, or persisted.
        """
        params: dict[str, str] = {"type": body.type.value}

        if body.type is CatalogType.ICEBERG:
            # 4.1.4 requires the metastore selector and accepts ``hive``/``rest``
            # (not the doc's ``hms`` alias); REST additionally needs a URI.
            catalog_type = "rest" if body.metastore_type is MetastoreType.REST else "hive"
            params["iceberg.catalog.type"] = catalog_type
            if catalog_type == "rest":
                params["iceberg.catalog.uri"] = body.metastore_uri
            else:
                params["hive.metastore.uris"] = body.metastore_uri
        else:
            params["hive.metastore.uris"] = body.metastore_uri

        params.update(body.properties)
        params.update(build_storage_credential_params(body.storage_connection))

        properties = ", ".join(
            f"{_quote_literal(key)} = {_quote_literal(value)}"
            for key, value in params.items()
        )
        sql = f"CREATE EXTERNAL CATALOG {body.name} PROPERTIES ({properties})"
        if body.comment:
            sql = (
                f"CREATE EXTERNAL CATALOG {body.name} "
                f"COMMENT {_quote_literal(body.comment)} PROPERTIES ({properties})"
            )
        return sql

    def build_alter_sql(self, name: str, body: ExternalCatalogAlter) -> str:
        """Build ``ALTER CATALOG ... SET`` including resolved credentials."""
        params: dict[str, str] = {}
        if body.metastore_uri:
            params["hive.metastore.uris"] = body.metastore_uri
        params.update(body.properties)
        if not params:
            raise ExternalCatalogError("Nothing to alter: no properties supplied")
        properties = ", ".join(
            f"{_quote_literal(key)} = {_quote_literal(value)}"
            for key, value in params.items()
        )
        return f"ALTER CATALOG {name} SET ({properties})"

    @staticmethod
    def build_drop_sql(name: str) -> str:
        return f"DROP CATALOG {name}"

    # ── Engine execution (caller's connection → RBAC) ──────────

    async def _run(
        self,
        sql: str,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> None:
        """Execute one catalog statement as the caller.

        Reuses the full query pipeline so the guard, audit row, and credential
        redaction all apply exactly as they do to a hand-typed statement.
        ``role`` activates the caller's selected role: ``CREATE EXTERNAL
        CATALOG`` is a SYSTEM privilege that only an activated role carries.

        ``confirm_destructive=True`` because ``DROP CATALOG`` matches the
        destructive pattern and the confirmation is the API call itself: the
        catalog manager's drop button is an explicit, deliberate action, not a
        free-form SQL statement that needs a second prompt. The hard guard
        (``guard_sql``, e.g. ``DROP ROLE ACCOUNTADMIN``) still runs regardless
        of this flag, so the safety boundary is unchanged.
        """
        result = await query_service.execute(
            sql=sql,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
            confirm_destructive=True,
        )
        if result.error:
            raise ExternalCatalogError(result.error)

    async def create(
        self,
        body: ExternalCatalogCreate,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None = None,
        role: str | None = None,
    ) -> ExternalCatalogResponse:
        existing = await external_catalog_repo.get_by_name(body.name)
        if existing:
            raise ExternalCatalogError(f"Catalog '{body.name}' already exists")

        await self._run(
            self.build_create_sql(body),
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        # Persist metadata by reference only — never the credential params.
        await external_catalog_repo.upsert(
            name=body.name,
            catalog_type=body.type.value,
            metastore_type=body.metastore_type.value,
            metastore_uri=body.metastore_uri,
            storage_connection=body.storage_connection,
            comment=body.comment,
            properties=dict(body.properties),
            username=username,
        )
        return await self.get(body.name)

    async def alter(
        self,
        name: str,
        body: ExternalCatalogAlter,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None = None,
        role: str | None = None,
    ) -> ExternalCatalogResponse:
        name = _safe_catalog_name(name)
        await self._run(
            self.build_alter_sql(name, body),
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        existing = await external_catalog_repo.get_by_name(name)
        if existing:
            await external_catalog_repo.upsert(
                name=name,
                catalog_type=existing["catalog_type"],
                metastore_type=existing.get("metastore_type"),
                metastore_uri=body.metastore_uri or existing.get("metastore_uri"),
                storage_connection=existing.get("storage_connection"),
                comment=body.comment if body.comment is not None else existing.get("comment"),
                properties={**existing.get("properties", {}), **body.properties},
                username=existing.get("created_by") or username,
            )
        return await self.get(name)

    async def drop(
        self,
        name: str,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None = None,
        role: str | None = None,
    ) -> bool:
        name = _safe_catalog_name(name)
        await self._run(
            self.build_drop_sql(name),
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return await external_catalog_repo.delete(name)

    # ── Read path ──────────────────────────────────────────────

    async def get(self, name: str) -> ExternalCatalogResponse:
        """Catalog detail with the redacted ``SHOW CREATE CATALOG`` output."""
        name = _safe_catalog_name(name)
        meta = await external_catalog_repo.get_by_name(name) or {}
        create_statement = await self._fetch_redacted_create_statement(name)
        return ExternalCatalogResponse(
            name=name,
            type=meta.get("catalog_type") or "",
            metastore_type=meta.get("metastore_type"),
            metastore_uri=meta.get("metastore_uri"),
            storage_connection=meta.get("storage_connection"),
            comment=meta.get("comment"),
            properties=meta.get("properties", {}),
            create_statement=create_statement,
            created_at=meta.get("created_at"),
            created_by=meta.get("created_by"),
        )

    async def _fetch_redacted_create_statement(self, name: str) -> str | None:
        """``SHOW CREATE CATALOG`` output with every credential value masked.

        Runs on the admin system pool because it is metadata, not table data;
        the caller's privilege still gates every create/alter/drop. Redaction is
        mandatory: the engine returns ``aws.s3.session_token`` and
        ``hive.metastore.password`` verbatim on 4.1.4.
        """
        from app.core.database import db

        try:
            result = await db.execute_system(f"SHOW CREATE CATALOG {name}")
        except Exception as exc:  # catalog may be engine-only, not Nova-created
            log.debug("SHOW CREATE CATALOG %s failed: %s", name, exc)
            return None
        rows = result.get("rows") or []
        if not rows or len(rows[0]) < 2:
            return None
        raw = rows[0][1]
        if not raw:
            return None
        return redact_sql_credentials(str(raw))

    async def list_catalogs(self) -> list[ExternalCatalogResponse]:
        """List Nova-managed catalogs with redacted detail."""
        rows = await external_catalog_repo.list_all()
        return [await self.get(row["name"]) for row in rows]


external_catalog_service = ExternalCatalogService()
