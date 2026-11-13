"""Notebook runtime, execution and package metadata in the caller's PG transaction.

The prepared replacement for :class:`NotebookRepository`, switched in at the
all-domain cutover; until then runtime callers keep ``NOVA_SYSTEM``. Each row
belongs to one person (the bound principal), including package manifests, so
two people sharing a notebook no longer overwrite each other's manifest (R21).

Timestamps arrive as epoch seconds from the worker and are converted to aware
datetimes here, so PostgreSQL never parses a caller-supplied string.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

_EXECUTION_FIELDS = (
    "id", "notebook_id", "user_name", "runtime_id", "run_id", "cell_id", "cell_kind",
    "sql_engine", "source_revision", "generation", "execution_count", "status",
    "active_role", "error_name", "error_message", "output_key", "output_bytes",
    "truncated", "summary_json", "queued_at", "started_at", "ended_at", "duration_ms",
)
_RUNTIME_FIELDS = (
    "status", "active_role", "image", "cpu", "memory", "generation", "error_message",
    "ready_at", "ended_at", "end_reason",
)


def _when(value: Any) -> datetime | None:
    """Epoch seconds (number or numeric text) to an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromtimestamp(float(value), tz=UTC)


def _manifest(packages: Any) -> str:
    if not isinstance(packages, list) or not all(isinstance(item, dict) for item in packages):
        raise ValueError("A package manifest is a list of packages")
    return json.dumps(packages, sort_keys=True)


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    try:
        row["summary"] = json.loads(row.pop("summary_json") or "{}")
    except ValueError:
        row["summary"] = {}
    for key in ("queued_at", "started_at", "ended_at"):
        if row.get(key) is not None:
            row[key] = row[key].isoformat()
    row["truncated"] = bool(row.get("truncated"))
    return row


class NotebookMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Notebook metadata requires an active metadata transaction")
        cursor = await self.connection.execute(
            "SELECT current_setting('nova.principal',true)=%s "
            "AND current_setting('nova.active_role',true)=%s "
            "AND current_setting('nova.security_context_version',true)=%s "
            "AND current_setting('nova.session_id',true)=%s "
            "AND current_setting('nova.service_identity',true)=current_user AS valid",
            (self.actor.principal, self.actor.active_role,
             str(self.actor.security_context_version), self.actor.session_id or ""),
        )
        row = await cursor.fetchone()
        if not row or row["valid"] is not True:
            raise MetadataPermissionDenied()

    def _own(self, row: dict[str, Any]) -> None:
        if row.get("user_name") != self.actor.principal:
            raise MetadataPermissionDenied()

    # ── Runtimes ──────────────────────────────────────────────────────────

    async def record_runtime(self, row: dict[str, Any]) -> dict[str, Any]:
        """Create or advance one runtime lifecycle row, written by its worker."""
        await self._bound()
        self._own(row)
        values = {
            "status": row["status"], "active_role": row.get("active_role"),
            "image": row.get("image"), "cpu": row.get("cpu"), "memory": row.get("memory"),
            "generation": int(row.get("generation") or 1),
            "error_message": row.get("error_message"),
            "ready_at": _when(row.get("ready_at")), "ended_at": _when(row.get("ended_at")),
            "end_reason": row.get("end_reason"),
        }
        async with self.connection.transaction():
            cursor = await self.connection.execute(
                "SELECT notebook_id,user_name FROM nova_meta.config_notebook_runtimes "
                "WHERE id=%s FOR UPDATE", (row["id"],),
            )
            existing = await cursor.fetchone()
            if existing is None:
                cursor = await self.connection.execute(
                    "INSERT INTO nova_meta.config_notebook_runtimes (id,notebook_id,user_name,"
                    "created_at," + ",".join(_RUNTIME_FIELDS) + ") VALUES (%s,%s,%s,%s,"
                    + ",".join(["%s"] * len(_RUNTIME_FIELDS)) + ") RETURNING *",
                    (row["id"], row["notebook_id"], row["user_name"], _when(row["created_at"]),
                     *values.values()),
                )
            else:
                if existing["notebook_id"] != row["notebook_id"]:
                    raise MetadataConflict()
                assignments = ",".join(f"{field}=%s" for field in _RUNTIME_FIELDS)
                cursor = await self.connection.execute(
                    f"UPDATE nova_meta.config_notebook_runtimes SET {assignments},"
                    "metadata_revision=metadata_revision+1 WHERE id=%s RETURNING *",
                    (*values.values(), row["id"]),
                )
            result = await cursor.fetchone()
            assert result is not None
            return result

    # ── Executions ────────────────────────────────────────────────────────

    async def record_execution(self, row: dict[str, Any]) -> dict[str, Any]:
        """Write a finished execution once. A retry with the same content is accepted."""
        await self._bound()
        self._own(row)
        values = {
            "id": row["id"], "notebook_id": row["notebook_id"], "user_name": row["user_name"],
            "runtime_id": row["runtime_id"], "run_id": row["run_id"], "cell_id": row["cell_id"],
            "cell_kind": row["cell_kind"], "sql_engine": row.get("sql_engine"),
            "source_revision": row["source_revision"],
            "generation": int(row.get("generation") or 1),
            "execution_count": row.get("execution_count"), "status": row["status"],
            "active_role": row.get("active_role"),
            "error_name": (str(row["error_name"])[:256] if row.get("error_name") else None),
            "error_message": row.get("error_message"), "output_key": row.get("output_key"),
            "output_bytes": row.get("output_bytes"), "truncated": bool(row.get("truncated")),
            "summary_json": json.dumps(row.get("summary") or {}, sort_keys=True),
            "queued_at": _when(row["queued_at"]), "started_at": _when(row.get("started_at")),
            "ended_at": _when(row.get("ended_at")), "duration_ms": row.get("duration_ms"),
        }
        async with self.connection.transaction():
            cursor = await self.connection.execute(
                "INSERT INTO nova_meta.config_notebook_executions (" + ",".join(_EXECUTION_FIELDS)
                + ") VALUES (" + ",".join(["%s"] * len(_EXECUTION_FIELDS)) + ") "
                "ON CONFLICT (id) DO NOTHING RETURNING *",
                tuple(values[field] for field in _EXECUTION_FIELDS),
            )
            inserted = await cursor.fetchone()
            if inserted is not None:
                return inserted
            cursor = await self.connection.execute(
                "SELECT * FROM nova_meta.config_notebook_executions WHERE id=%s", (row["id"],),
            )
            existing = await cursor.fetchone()
            if existing is None or any(existing[field] != values[field] for field in values):
                raise MetadataConflict()
            return existing

    async def get_execution(self, notebook_id: str, execution_id: str) -> dict[str, Any] | None:
        await self._bound()
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.config_notebook_executions "
            "WHERE id=%s AND notebook_id=%s AND user_name=%s",
            (execution_id, notebook_id, self.actor.principal),
        )
        row = await cursor.fetchone()
        return _decode(row) if row else None

    async def latest_executions(self, notebook_id: str) -> list[dict[str, Any]]:
        """The newest finished execution of each cell of a notebook, for this person."""
        await self._bound()
        cursor = await self.connection.execute(
            "SELECT DISTINCT ON (cell_id) * FROM nova_meta.config_notebook_executions "
            "WHERE user_name=%s AND notebook_id=%s "
            "ORDER BY cell_id, ended_at DESC NULLS LAST, queued_at DESC NULLS LAST, id",
            (self.actor.principal, notebook_id),
        )
        return [_decode(row) for row in await cursor.fetchall()]

    # ── Packages ──────────────────────────────────────────────────────────

    async def packages(self, notebook_id: str) -> tuple[list[dict[str, Any]], int]:
        """This person's manifest and its revision (0 when none was saved)."""
        await self._bound()
        cursor = await self.connection.execute(
            "SELECT requirements_json,metadata_revision FROM nova_meta.config_notebook_packages "
            "WHERE notebook_id=%s AND user_name=%s", (notebook_id, self.actor.principal),
        )
        row = await cursor.fetchone()
        if row is None:
            return [], 0
        try:
            value = json.loads(row["requirements_json"] or "[]")
        except ValueError:
            value = []
        return (value if isinstance(value, list) else []), int(row["metadata_revision"])

    async def replace_packages(
        self, notebook_id: str, packages: list[dict[str, Any]], *, expected_revision: int,
    ) -> int:
        """Save this person's manifest if it is still at ``expected_revision``."""
        await self._bound()
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("A manifest revision is required")
        manifest = _manifest(packages)
        if expected_revision == 0:
            cursor = await self.connection.execute(
                "INSERT INTO nova_meta.config_notebook_packages "
                "(notebook_id,user_name,requirements_json) VALUES (%s,%s,%s) "
                "ON CONFLICT (notebook_id,user_name) DO NOTHING RETURNING metadata_revision",
                (notebook_id, self.actor.principal, manifest),
            )
        else:
            cursor = await self.connection.execute(
                "UPDATE nova_meta.config_notebook_packages SET requirements_json=%s,"
                "metadata_revision=metadata_revision+1 WHERE notebook_id=%s AND user_name=%s "
                "AND metadata_revision=%s RETURNING metadata_revision",
                (manifest, notebook_id, self.actor.principal, expected_revision),
            )
        row = await cursor.fetchone()
        if row is None:
            raise MetadataConflict()
        return int(row["metadata_revision"])

    async def modify_packages(
        self, notebook_id: str, change: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Apply ``change`` to the current manifest under its row lock.

        An install by the worker and a removal from the web serialize here, so
        neither can drop the other's change.
        """
        await self._bound()
        async with self.connection.transaction():
            cursor = await self.connection.execute(
                "SELECT metadata_revision FROM nova_meta.config_notebook_packages "
                "WHERE notebook_id=%s AND user_name=%s FOR UPDATE",
                (notebook_id, self.actor.principal),
            )
            locked = await cursor.fetchone()
            current, revision = await self.packages(notebook_id)
            if locked is None:
                revision = 0
            updated = change(list(current))
            return updated, await self.replace_packages(
                notebook_id, updated, expected_revision=revision,
            )
