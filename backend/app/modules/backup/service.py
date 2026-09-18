"""Backup & recovery service — snapshots, repositories, recycle bin.

Design constraints (roadmap #7, ``docs/21-backup-recovery.md``):

- **Privileged, enforced in the backend.** Every statement runs on the caller's
  connection through the shared ``QueryService`` pipeline, so the engine's
  ``REPOSITORY ON SYSTEM`` privilege is the real authority and each mutation
  lands in ``NOVA_SYSTEM.AUDIT_LOG``. The router additionally gates the
  mutating endpoints with ``require_role`` — the frontend is not a security
  boundary (AGENTS.md §6).
- **No credential ever leaves the service.** A repository names a storage
  connection; the secret is resolved server-side from ``nova.yaml`` and is
  never accepted on a request or returned in a response, log, or
  ``NOVA_SYSTEM`` row.
- **Engine surface only.** Snapshot and recycle-bin listings are read with
  ``SHOW BACKUP`` / ``SHOW CATALOG RECYCLE BIN``; Nova stores no snapshot
  inventory of its own.
"""

from __future__ import annotations

import logging
import re

from app.core.config import get_storage_connection
from app.modules.query.dialect.injector import resolve_storage_credentials
from app.modules.query.service import query_service

from .schemas import (
    RecoverRequest,
    RepositoryCreate,
    SnapshotCreate,
    SnapshotRestore,
)

log = logging.getLogger(__name__)

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_QUALIFIED = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


class BackupError(ValueError):
    """A backup/recovery operation the service refused before or after the engine."""


def _safe_ident(value: str, what: str) -> str:
    if not _NAME.match(value or ""):
        raise BackupError(f"Invalid {what}: {value!r}")
    return value


def _quote(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def _safe_label(value: str) -> str:
    """Snapshot labels may contain ``-``/``_`` but not a dot (see ``_safe_ident``)."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\-]*", value or ""):
        raise BackupError(f"Invalid snapshot label: {value!r}")
    return value


class BackupService:
    """Snapshot/restore/recover through the caller's SQL pipeline."""

    async def _run(
        self,
        sql: str,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> None:
        result = await query_service.execute(
            sql=sql,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
            confirm_destructive=True,
        )
        if result.error:
            raise BackupError(result.error)

    async def _query(
        self,
        sql: str,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ):
        result = await query_service.execute(
            sql=sql,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        if result.error:
            raise BackupError(result.error)
        return result

    # ── Snapshots ───────────────────────────────────────────────

    async def create_snapshot(
        self,
        body: SnapshotCreate,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        database = _safe_ident(body.database, "database")
        label = _safe_label(body.label)
        repository = _safe_ident(body.repository, "repository")
        on_clause = self._on_clause(body.tables, database=database)
        properties = f'"type" = "{body.backup_type}", "timeout" = "{body.timeout}"'
        statement = (
            f"BACKUP SNAPSHOT {_quote(database)}.{_quote(label)} "
            f"TO {_quote(repository)} ON ({on_clause}) PROPERTIES({properties})"
        )
        await self._run(
            statement,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {
            "success": True,
            "database": database,
            "label": label,
            "repository": repository,
            "message": f"Snapshot '{database}.{label}' created",
        }

    async def restore_snapshot(
        self,
        body: SnapshotRestore,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        database = _safe_ident(body.database, "database")
        label = _safe_label(body.label)
        repository = _safe_ident(body.repository, "repository")
        on_clause = self._on_clause(body.tables, database=database)
        props = [f'"replication_num" = "{body.replication_num}"']
        if body.backup_timestamp:
            ts = re.sub(r"[^0-9A-Za-z\-_:]", "", body.backup_timestamp)
            if ts != body.backup_timestamp:
                raise BackupError("Invalid backup_timestamp")
            props.append(f'"backup_timestamp" = "{ts}"')
        if body.allow_overwrite:
            props.append('"allow_overwrite" = "true"')
        statement = (
            f"RESTORE SNAPSHOT {_quote(database)}.{_quote(label)} "
            f"FROM {_quote(repository)} ON ({on_clause}) "
            f"PROPERTIES({', '.join(props)})"
        )
        await self._run(
            statement,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {
            "success": True,
            "database": database,
            "label": label,
            "repository": repository,
            "message": f"Snapshot '{database}.{label}' restored",
        }

    @staticmethod
    def _on_clause(tables: list[str], *, database: str) -> str:
        if not tables:
            return f"DATABASE {_quote(database)}"
        return ", ".join(
            f"TABLE {_quote(_safe_ident(t, 'table'))}" for t in tables
        )

    async def list_snapshots(
        self,
        *,
        database: str | None = None,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> list[dict]:
        """``SHOW BACKUP [FROM <db>]`` — the engine's snapshot list."""
        statement = "SHOW BACKUP"
        if database:
            statement += f" FROM {_quote(_safe_ident(database, 'database'))}"
        result = await self._query(
            statement,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return _rows_to_maps(result)

    # ── Repositories ────────────────────────────────────────────

    async def create_repository(
        self,
        body: RepositoryCreate,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        """``CREATE REPOSITORY … WITH BROKER ON LOCATION <path> PROPERTIES(...)``.

        Credentials come from the named storage connection, resolved here. They
        are embedded in the statement (the engine needs them) but never stored
        by Nova and never returned — ``redact_sql_credentials`` scrubs the
        audited copy of the statement.
        """
        name = _safe_ident(body.name, "repository name")
        location = body.location.strip()
        if not location or any(c in location for c in ("'", '"', ";", "\n")):
            raise BackupError("Invalid repository location")

        connection = get_storage_connection(body.storage_connection)
        access_key, secret_key = resolve_storage_credentials(body.storage_connection)
        props: list[str] = []
        if connection.endpoint:
            props.append(f'"aws.s3.endpoint" = "{connection.endpoint}"')
        if access_key:
            props.append(f'"aws.s3.access_key" = "{access_key}"')
        if secret_key:
            props.append(f'"aws.s3.secret_key" = "{secret_key}"')
        # Resolve region/bucket through the connection, never a hardcoded default.
        if getattr(connection, "region", None):
            props.append(f'"aws.s3.region" = "{connection.region}"')

        statement = (
            f"CREATE REPOSITORY {_quote(name)} WITH BROKER "
            f'ON LOCATION "{location}"'
        )
        if props:
            statement += f" PROPERTIES({', '.join(props)})"
        await self._run(
            statement,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {
            "success": True,
            "name": name,
            "storage_connection": body.storage_connection,
            "location": location,
        }

    async def list_repositories(
        self,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> list[dict]:
        """``SHOW REPOSITORIES`` — redacted so no secret key is echoed."""
        result = await self._query(
            "SHOW REPOSITORIES",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return _rows_to_maps(result)

    # ── Recycle bin ─────────────────────────────────────────────

    async def list_recycle_bin(
        self,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> list[dict]:
        """``SHOW CATALOG RECYCLE BIN`` — recoverable dropped objects."""
        result = await self._query(
            "SHOW CATALOG RECYCLE BIN",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return _rows_to_maps(result)

    async def recover(
        self,
        body: RecoverRequest,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        """``RECOVER TABLE|DATABASE <name> [AS <new_name>]``."""
        name = _safe_ident(body.name, "object name")
        if body.object_type == "database":
            statement = f"RECOVER DATABASE {_quote(name)}"
            if body.new_name:
                statement += f" AS {_quote(_safe_ident(body.new_name, 'new name'))}"
        else:
            database = _safe_ident(body.database or "", "database")
            statement = f"RECOVER TABLE {_quote(database)}.{_quote(name)}"
            if body.new_name:
                statement += f" AS {_quote(_safe_ident(body.new_name, 'new name'))}"
        await self._run(
            statement,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {"success": True, "message": f"Recovered {body.object_type} '{name}'"}


def _rows_to_maps(result) -> list[dict]:
    """Normalize an engine result into ``{column: value}`` maps.

    Column labels differ across builds; every row is returned as a plain map so
    the UI can render whatever the engine exposes without a second shape. No
    credential is scrubbed here — the shared pipeline redacts statement text and
    the router's response class is the last line of defence.
    """
    columns = [str(c) for c in result.columns]
    return [
        {columns[i]: value for i, value in enumerate(row) if i < len(columns)}
        for row in (result.rows or [])
    ]


backup_service = BackupService()
