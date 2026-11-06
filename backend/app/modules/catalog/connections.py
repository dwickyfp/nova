"""Nova Catalog connections and foreign catalogs (phase 2).

A *connection* registers an external source (one of :data:`.sources.SOURCES`:
StarRocks, PostgreSQL, Snowflake): its endpoint and a secret reference, in
``NOVA_SYSTEM.CATALOG_CONNECTIONS``. A *foreign catalog* is a StarRocks JDBC
catalog built from a connection, so the engine and Ranger govern every query
against it like any other catalog.

Rules this module keeps:

* **Security admin first.** Every operation requires an active security-admin
  role and is audited, refusals included. The check runs before a credential
  is resolved, so a refused caller never causes a secret lookup.
* **References, not secrets.** A connection row holds ``secret_ref``. It names a
  secret store entry (``aws://...`` or an ARN) or ``nova://<connection>``, a
  credential entered through Nova and stored Fernet-encrypted in
  ``CATALOG_CONNECTION_CREDENTIALS``. Entry is write-only: nothing here returns
  a credential, an endpoint, or a reference to a client.
* **The engine decides the rest.** ``CREATE EXTERNAL CATALOG`` runs as the
  caller through the shared query pipeline, so Ranger authorizes it and the
  audit row holds the redacted statement.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from app.common.audit import write_audit_log
from app.common.crypto import decrypt, encrypt
from app.common.sql_guard import redact_sql_credentials
from app.core.database import db
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.access_control.service import AccessControlError, AccessControlService
from app.storage.secrets import _SCHEME_ALIASES, SecretResolutionError, resolve_secret_reference

from .errors import CatalogError, safe_catalog_name
from .repository import CatalogRepository, catalog_repo
from .schemas import ConnectionSummary, SyncRun
from .service import CatalogAccessDenied
from .sources import (
    SOURCES,
    Credentials,
    Endpoint,
    parse_endpoint,
    validate_connector_type,
)
from .sync import nova_now

log = logging.getLogger(__name__)

__all__ = [
    "CatalogConnectionError",
    "ConnectionService",
    "connection_service",
    "parse_endpoint",
    "safe_name",
    "validate_connector_type",
    "validate_secret_ref",
]

MANAGED_SCHEME = "nova://"
#: The property a Nova foreign catalog stores its connection name under.
CONNECTION_PROPERTY = "nova.connection"

_ARN = re.compile(r"^arn:aws[a-z-]*:secretsmanager:[a-z0-9-]+:\d{12}:secret:[\w/+=.@-]+$")
_SCHEMED = re.compile(r"^([a-z][a-z0-9_-]{0,31})://[\w/+=.@:-]{1,1000}$")


class CatalogConnectionError(CatalogError):
    """A connection request Nova refused."""


def safe_name(name: str) -> str:
    """A connection or catalog identifier, validated like a catalog name."""
    try:
        return safe_catalog_name(name)
    except CatalogError as exc:
        raise CatalogConnectionError(str(exc)) from None


def validate_secret_ref(value: str) -> str:
    """Accept a secret *reference* and refuse anything that could be a secret.

    A reference must carry an explicit scheme (``aws://``, ``nova://``) or be a
    Secrets Manager ARN. A bare value such as ``user:password`` is refused, so a
    credential typed into ``SECRET =`` is never persisted as if it were a name.
    """
    ref = (value or "").strip()
    if ref.startswith(MANAGED_SCHEME):
        safe_name(ref[len(MANAGED_SCHEME):])
        return ref
    match = _SCHEMED.match(ref)
    if match and match.group(1) in _SCHEME_ALIASES:
        return ref
    if _ARN.match(ref):
        return ref
    raise CatalogConnectionError(
        "SECRET must be a secret reference: aws://<name>, an AWS Secrets Manager "
        "ARN, or nova://<connection> for credentials entered through Nova"
    )


def _managed_ref(name: str) -> str:
    return MANAGED_SCHEME + name


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_CONNECTION_COLUMNS = (
    "name", "connector_type", "host", "port", "database_name", "options_json",
    "secret_ref", "owner", "comment", "created_at", "updated_at",
)


def _columns(columns: tuple[str, ...]) -> str:
    return ", ".join(f"`{name}`" for name in columns)


class ConnectionRepository:
    """``CATALOG_CONNECTIONS`` and ``CATALOG_CONNECTION_CREDENTIALS``, system pool.

    The schema itself is created with the rest of the catalog store
    (:data:`app.modules.catalog.repository.CATALOG_DDLS`).
    """

    async def list(self) -> list[dict]:
        result = await db.execute_system(
            f"SELECT {_columns(_CONNECTION_COLUMNS)} FROM NOVA_SYSTEM.CATALOG_CONNECTIONS "
            "ORDER BY name"
        )
        return [self._row(row) for row in result.get("rows") or []]

    async def get(self, name: str) -> dict | None:
        result = await db.execute_system(
            f"SELECT {_columns(_CONNECTION_COLUMNS)} FROM NOVA_SYSTEM.CATALOG_CONNECTIONS "
            "WHERE name = %s",
            [name],
        )
        rows = result.get("rows") or []
        return self._row(rows[0]) if rows else None

    async def upsert(self, connection: dict) -> None:
        row = dict(connection)
        row["options_json"] = json.dumps(row.pop("options", None) or {})
        await db.execute_system(
            f"INSERT INTO NOVA_SYSTEM.CATALOG_CONNECTIONS ({_columns(_CONNECTION_COLUMNS)}) "
            f"VALUES ({', '.join(['%s'] * len(_CONNECTION_COLUMNS))})",
            [row.get(column) for column in _CONNECTION_COLUMNS],
        )

    async def delete(self, name: str) -> None:
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CATALOG_CONNECTIONS WHERE name = %s", [name]
        )
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CATALOG_CONNECTION_CREDENTIALS WHERE connection_name = %s",
            [name],
        )

    async def has_credentials(self, name: str) -> bool:
        result = await db.execute_system(
            "SELECT connection_name FROM NOVA_SYSTEM.CATALOG_CONNECTION_CREDENTIALS "
            "WHERE connection_name = %s",
            [name],
        )
        return bool(result.get("rows"))

    async def put_credentials(self, name: str, username: str, password: str, by: str) -> None:
        """Store an entered credential, encrypted. Fails closed on encryption."""
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CATALOG_CONNECTION_CREDENTIALS "
            "(connection_name, username_enc, password_enc, updated_by, updated_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            [name, encrypt(username), encrypt(password), by, nova_now()],
        )

    async def read_credentials(self, name: str) -> tuple[str, str] | None:
        """Decrypt a stored credential for engine use only. Never returned to a client."""
        result = await db.execute_system(
            "SELECT username_enc, password_enc FROM NOVA_SYSTEM.CATALOG_CONNECTION_CREDENTIALS "
            "WHERE connection_name = %s",
            [name],
        )
        rows = result.get("rows") or []
        if not rows:
            return None
        username, password = decrypt(rows[0][0]), decrypt(rows[0][1])
        if not username or not password:
            return None
        return username, password

    @staticmethod
    def _row(values: Any) -> dict:
        row = dict(zip(_CONNECTION_COLUMNS, values, strict=False))
        raw = row.pop("options_json", None)
        try:
            options = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            options = {}
        row["options"] = options if isinstance(options, dict) else {}
        return row


connection_repo = ConnectionRepository()


def endpoint_of(connection: dict) -> Endpoint:
    return Endpoint(
        host=connection["host"],
        port=int(connection["port"]),
        database=connection.get("database_name"),
        options={str(k): str(v) for k, v in (connection.get("options") or {}).items()},
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ConnectionService:
    def __init__(
        self,
        repo: ConnectionRepository | None = None,
        catalogs: CatalogRepository | None = None,
    ) -> None:
        self._repo = repo
        self._catalogs = catalogs

    @property
    def repo(self) -> ConnectionRepository:
        return self._repo or connection_repo

    @property
    def catalogs(self) -> CatalogRepository:
        return self._catalogs or catalog_repo

    # ── Authorization and audit ─────────────────────────────────

    async def _authorize(self, user: dict, action: str, target: str) -> SecurityContext:
        """Require a security-admin active role; audit and raise on refusal."""
        try:
            security = user.get("security") or SecurityContext.from_session(user)
            AccessControlService.require_security_admin(security)
        except (AccessControlError, SecurityContextError) as exc:
            await _audit(user, action, target, "DENIED", str(exc))
            raise CatalogAccessDenied(str(exc)) from exc
        return security

    # ── Read ─────────────────────────────────────────────────────

    async def list_connections(self, user: dict) -> list[ConnectionSummary]:
        await self._authorize(user, "LIST_CONNECTIONS", "*")
        uses = await self._catalog_uses()
        return [
            await self._summary(row, uses.get(row["name"], []))
            for row in await self.repo.list()
        ]

    async def describe(self, name: str, user: dict) -> ConnectionSummary | None:
        name = safe_name(name)
        await self._authorize(user, "DESCRIBE_CONNECTION", name)
        row = await self.repo.get(name)
        if row is None:
            return None
        uses = await self._catalog_uses()
        return await self._summary(row, uses.get(name, []))

    async def _summary(self, row: dict, catalogs: list[str]) -> ConnectionSummary:
        managed = row["secret_ref"] == _managed_ref(row["name"])
        return ConnectionSummary(
            name=row["name"],
            connector_type=row["connector_type"],
            credential_source="managed" if managed else "external",
            has_credentials=(not managed) or await self.repo.has_credentials(row["name"]),
            owner=row.get("owner"),
            comment=row.get("comment"),
            catalogs=sorted(catalogs),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    async def _catalog_uses(self) -> dict[str, list[str]]:
        uses: dict[str, list[str]] = {}
        for catalog in await self.catalogs.list_catalogs():
            connection = (catalog.get("properties") or {}).get(CONNECTION_PROPERTY)
            if connection:
                uses.setdefault(connection, []).append(catalog["name"])
        return uses

    # ── Connections ─────────────────────────────────────────────

    async def create(
        self,
        *,
        name: str,
        connector_type: str,
        endpoint: str,
        secret_ref: str | None,
        user: dict,
        comment: str | None = None,
        username: str | None = None,
        password: str | None = None,
        if_not_exists: bool = False,
    ) -> ConnectionSummary | None:
        name = safe_name(name)
        security = await self._authorize(user, "CREATE_CONNECTION", name)
        try:
            connector_type = validate_connector_type(connector_type)
            parsed = parse_endpoint(connector_type, endpoint)
            entered = username is not None or password is not None
            if entered and (not username or not password):
                raise CatalogConnectionError("Enter both a username and a password")
            if entered and secret_ref and secret_ref != _managed_ref(name):
                raise CatalogConnectionError(
                    "Give either a secret reference or credentials, not both"
                )
            if not entered and not secret_ref:
                raise CatalogConnectionError("A connection needs a secret reference or credentials")
            ref = _managed_ref(name) if entered else validate_secret_ref(secret_ref or "")
            if ref.startswith(MANAGED_SCHEME) and ref != _managed_ref(name):
                raise CatalogConnectionError(
                    f"A Nova-managed credential belongs to one connection: use nova://{name}"
                )
            if await self.repo.get(name) is not None:
                if if_not_exists:
                    return await self.describe(name, user)
                raise CatalogConnectionError(f"Connection '{name}' already exists")
            now = nova_now()
            await self.repo.upsert(
                {
                    "name": name,
                    "connector_type": connector_type,
                    "host": parsed.host,
                    "port": parsed.port,
                    "database_name": parsed.database,
                    "options": parsed.options,
                    "secret_ref": ref,
                    "owner": security.principal,
                    "comment": comment,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            if entered:
                await self.repo.put_credentials(
                    name, str(username), str(password), security.principal
                )
        except Exception as exc:
            await _audit(user, "CREATE_CONNECTION", name, "ERROR", str(exc))
            raise
        await _audit(user, "CREATE_CONNECTION", name, "SUCCESS", None)
        return await self.describe(name, user)

    async def set_credentials(
        self, name: str, username: str, password: str, user: dict
    ) -> ConnectionSummary | None:
        """Enter credentials for a connection; it becomes Nova-managed."""
        name = safe_name(name)
        security = await self._authorize(user, "SET_CONNECTION_CREDENTIALS", name)
        try:
            row = await self.repo.get(name)
            if row is None:
                raise CatalogConnectionError(f"Connection '{name}' does not exist")
            if not username or not password:
                raise CatalogConnectionError("Enter both a username and a password")
            await self.repo.put_credentials(name, username, password, security.principal)
            if row["secret_ref"] != _managed_ref(name):
                await self.repo.upsert(
                    {**row, "secret_ref": _managed_ref(name), "updated_at": nova_now()}
                )
        except Exception as exc:
            await _audit(user, "SET_CONNECTION_CREDENTIALS", name, "ERROR", str(exc))
            raise
        await _audit(user, "SET_CONNECTION_CREDENTIALS", name, "SUCCESS", None)
        return await self.describe(name, user)

    async def drop(self, name: str, user: dict, *, if_exists: bool = False) -> bool:
        """Remove a connection no live catalog uses. Returns whether one was dropped."""
        name = safe_name(name)
        await self._authorize(user, "DROP_CONNECTION", name)
        try:
            if await self.repo.get(name) is None:
                if if_exists:
                    return False
                raise CatalogConnectionError(f"Connection '{name}' does not exist")
            users = (await self._catalog_uses()).get(name)
            if users:
                raise CatalogConnectionError(
                    f"Connection '{name}' is used by catalog(s) {', '.join(sorted(users))}; "
                    "drop them first"
                )
            await self.repo.delete(name)
        except Exception as exc:
            await _audit(user, "DROP_CONNECTION", name, "ERROR", str(exc))
            raise
        await _audit(user, "DROP_CONNECTION", name, "SUCCESS", None)
        return True

    # ── Foreign catalogs ────────────────────────────────────────

    async def resolve_credentials(self, connection: dict) -> Credentials:
        """Resolve a connection's credential. Call only after authorization, or
        from the control-plane sync, and never return it to a client."""
        ref = connection["secret_ref"]
        if ref.startswith(MANAGED_SCHEME):
            if ref != _managed_ref(connection["name"]):
                raise CatalogConnectionError("The connection's credential reference is invalid")
            stored = await self.repo.read_credentials(connection["name"])
            if stored is None:
                raise CatalogConnectionError(
                    f"Connection '{connection['name']}' has no credentials; enter them first"
                )
            return Credentials(*stored)
        try:
            value = await asyncio.to_thread(resolve_secret_reference, ref)
        except SecretResolutionError as exc:
            raise CatalogConnectionError(str(exc)) from None
        return Credentials(value.access_key, value.secret_key)

    async def create_foreign_catalog(
        self,
        *,
        name: str,
        connection: str,
        user: dict,
        comment: str | None = None,
        properties: dict[str, str] | None = None,
        if_not_exists: bool = False,
    ) -> str:
        """Create a JDBC catalog from a connection, as the caller. Returns its name."""
        from .service import catalog_service

        name, connection = safe_name(name), safe_name(connection)
        security = await self._authorize(user, "CREATE_FOREIGN_CATALOG", name)
        try:
            existing = await self.catalogs.get_catalog(name)
            if existing and existing.get("dropped_at") is None:
                if if_not_exists:
                    return name
                raise CatalogConnectionError(f"Catalog '{name}' already exists")
            row = await self.repo.get(connection)
            if row is None:
                raise CatalogConnectionError(f"Connection '{connection}' does not exist")
            source = SOURCES.get(row["connector_type"])
            if source is None:
                raise CatalogConnectionError(
                    f"Connection type {row['connector_type']!r} can no longer back a "
                    "catalog; supported types are " + ", ".join(sorted(SOURCES))
                )
            sql = source.build_catalog_sql(
                name,
                endpoint_of(row),
                await self.resolve_credentials(row),
                comment=comment,
                properties=properties,
            )
            await catalog_service._run_as_caller(sql, user)
            now = nova_now()
            await self.catalogs.upsert_catalog(
                {
                    "name": name,
                    "kind": "foreign",
                    "catalog_type": row["connector_type"],
                    "managed": True,
                    "source": "nova",
                    "comment": comment,
                    "properties": {**(properties or {}), CONNECTION_PROPERTY: connection},
                    "created_by": security.principal,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        except Exception as exc:
            await _audit(user, "CREATE_FOREIGN_CATALOG", name, "ERROR", str(exc))
            raise
        await _audit(user, "CREATE_FOREIGN_CATALOG", name, "SUCCESS", None)
        await catalog_service._after_change(name, "create")
        return name

    async def refresh_catalog(
        self,
        name: str,
        user: dict,
        *,
        schema: str | None = None,
        relation: str | None = None,
    ) -> list[SyncRun]:
        """``REFRESH CATALOG name [SCHEMA s [TABLE t]]``.

        A named table of an external catalog first has its engine metadata
        cache refreshed (``REFRESH EXTERNAL TABLE``, as the caller); then the
        catalog is synced into the store. The Nova sync covers the whole
        catalog: a schema or table only narrows the engine refresh.
        """
        from .service import catalog_service

        name = safe_name(name)
        if relation:
            await self._authorize(user, "REFRESH_METADATA", name)
            stored = await self.catalogs.get_catalog(name)
            if stored is not None and stored.get("kind") == "native":
                raise CatalogConnectionError("REFRESH CATALOG ... TABLE names an external table")
            target = ".".join(f"`{part}`" for part in (name, schema or "", relation))
            await catalog_service._run_as_caller(f"REFRESH EXTERNAL TABLE {target}", user)
        return await catalog_service.refresh(name, user)


async def _audit(user: dict, action: str, target: str, status: str, error: str | None) -> None:
    try:
        security = user.get("security")
        await write_audit_log(
            event_type="CATALOG",
            user_name=str(user.get("username") or getattr(security, "principal", "") or ""),
            action=action,
            object_type="CATALOG_CONNECTION" if "CONNECTION" in action else "CATALOG",
            object_name=target,
            status=status,
            error_message=redact_sql_credentials(error)[:2000] if error else None,
            session_id=user.get("session_id"),
            active_role=user.get("active_role") or getattr(security, "active_role", None),
            security_context_version=user.get("security_context_version"),
        )
    except Exception:  # noqa: BLE001 - audit is best-effort, never the failure
        log.exception("Could not write catalog connection audit entry")


connection_service = ConnectionService()
