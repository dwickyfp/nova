"""Pipe definitions, file ledger and batch intent in the caller's PostgreSQL transaction.

Two kinds of callers use this repository:

* a person's statement (``CREATE``/``ALTER``/``DROP``/``SHOW PIPE``) through the
  web or query login, as their own actor. Forced RLS shows and changes only the
  pipes of their active role (administrators see every pipe);
* the task worker, as the ``nova-pipes`` SERVICE actor, which claims one pipe
  with a fenced lease and records what it stages and loads.

A property or state change names the revision it read (compare-and-swap). A
worker's ledger write first locks the pipe row under the claim it holds, so a
run that lost its lease cannot record anything after a newer run took over.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

#: The SERVICE actor of the worker that loads pipes (``pipe_worker()`` in PG).
WORKER = "pipes"

_CREATE = (
    "id", "database_name", "name", "target_table", "definition", "source", "auto_ingest",
    "poll_interval", "batch_size", "batch_files", "state", "owner_role", "created_by", "task_id",
)
_PROPERTIES = frozenset({"auto_ingest", "poll_interval", "batch_size", "batch_files"})


class LeaseLost(RuntimeError):
    """The pipe's lease moved to another run; this run records nothing further."""


class PipeRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Pipe metadata requires an active metadata transaction")
        cursor = await self.connection.execute(
            "SELECT current_setting('nova.principal',true)=%s "
            "AND current_setting('nova.active_role',true)=%s "
            "AND current_setting('nova.service_identity',true)=current_user AS valid",
            (self.actor.principal, self.actor.active_role),
        )
        row = await cursor.fetchone()
        if not row or row["valid"] is not True:
            raise MetadataPermissionDenied()

    async def _one(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        cursor = await self.connection.execute(query, params)
        return await cursor.fetchone()

    async def _all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = await self.connection.execute(query, params)
        return list(await cursor.fetchall())

    # ── Definitions (a person's statements) ─────────────────────────────

    async def create_pipe(self, data: dict[str, Any]) -> dict[str, Any]:
        await self._bound()
        values = [Jsonb(data[field]) if field == "source" else data[field] for field in _CREATE]
        row = await self._one(
            "INSERT INTO nova_meta.config_pipes (" + ",".join(_CREATE) + ") VALUES ("
            + ",".join(["%s"] * len(_CREATE)) + ") RETURNING *",
            tuple(values),
        )
        assert row is not None
        return row

    async def get_pipe(self, database: str, name: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_pipes WHERE database_name=%s AND name=%s",
            (database, name),
        )

    async def get_pipe_by_id(self, pipe_id: UUID | str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one("SELECT * FROM nova_meta.config_pipes WHERE id=%s", (pipe_id,))

    async def list_pipes(self, database: str | None = None) -> list[dict[str, Any]]:
        await self._bound()
        if database is None:
            return await self._all(
                "SELECT * FROM nova_meta.config_pipes ORDER BY database_name,name"
            )
        return await self._all(
            "SELECT * FROM nova_meta.config_pipes WHERE database_name=%s ORDER BY name",
            (database,),
        )

    async def update_pipe(
        self, pipe_id: UUID | str, changes: dict[str, Any], *, expected_revision: int,
    ) -> dict[str, Any]:
        """Change properties, state or last error only at the revision the caller read."""
        await self._bound()
        allowed = _PROPERTIES | {"state", "last_error"}
        if not changes or set(changes) - allowed:
            raise ValueError("Unsupported pipe change")
        assignments = ",".join(f"{field}=%s" for field in changes)
        row = await self._one(
            f"UPDATE nova_meta.config_pipes SET {assignments},revision=revision+1 "
            "WHERE id=%s AND revision=%s RETURNING *",
            (*changes.values(), pipe_id, expected_revision),
        )
        if row is None:
            raise MetadataConflict()
        return row

    async def wake(self, pipe_id: UUID | str) -> None:
        """Ask the next run (or the run in progress) for another pass."""
        await self._bound()
        await self.connection.execute(
            "UPDATE nova_meta.config_pipes SET wake_count=wake_count+1 WHERE id=%s", (pipe_id,),
        )

    async def delete_pipe(self, pipe_id: UUID | str) -> bool:
        """Remove the pipe with its file records; loaded rows stay in the table."""
        await self._bound()
        await self.connection.execute(
            "DELETE FROM nova_meta.pipe_files WHERE pipe_id=%s", (pipe_id,),
        )
        await self.connection.execute(
            "DELETE FROM nova_meta.pipe_batches WHERE pipe_id=%s", (pipe_id,),
        )
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.config_pipes WHERE id=%s", (pipe_id,),
        )
        return cursor.rowcount == 1

    async def retry_files(self, pipe_id: UUID | str, file_name: str | None = None) -> int:
        """Move failed files back to the queue: every one, or every version of one file."""
        await self._bound()
        query = (
            "UPDATE nova_meta.pipe_files SET load_state='UNLOADED',retry_count=retry_count+1 "
            "WHERE pipe_id=%s AND load_state='ERROR'"
        )
        params: tuple[Any, ...] = (pipe_id,)
        if file_name is not None:
            query += " AND file_name=%s"
            params += (file_name,)
        cursor = await self.connection.execute(query, params)
        return cursor.rowcount

    async def file_states(
        self, pipe_id: UUID | str, file_name: str,
    ) -> list[str]:
        await self._bound()
        rows = await self._all(
            "SELECT load_state FROM nova_meta.pipe_files WHERE pipe_id=%s AND file_name=%s",
            (pipe_id, file_name),
        )
        return [str(row["load_state"]) for row in rows]

    async def list_files(
        self, pipe_ids: Iterable[UUID | str], *, limit: int = 10000,
    ) -> list[dict[str, Any]]:
        await self._bound()
        ids = [str(item) for item in pipe_ids]
        if not ids:
            return []
        return await self._all(
            "SELECT * FROM nova_meta.pipe_files WHERE pipe_id::text = ANY(%s) "
            "ORDER BY pipe_id,staged_time,file_name,file_version LIMIT %s",
            (ids, limit),
        )

    async def load_status(self, pipe_ids: Iterable[UUID | str]) -> dict[str, dict[str, Any]]:
        """Per pipe: files by state, loaded bytes and the last finish time."""
        await self._bound()
        ids = [str(item) for item in pipe_ids]
        if not ids:
            return {}
        rows = await self._all(
            "SELECT pipe_id::text AS pipe_id, load_state, count(*) AS files, "
            "coalesce(sum(file_size),0) AS bytes, max(finish_load_time) AS last_finished "
            "FROM nova_meta.pipe_files WHERE pipe_id::text = ANY(%s) "
            "GROUP BY pipe_id, load_state",
            (ids,),
        )
        status: dict[str, dict[str, Any]] = {
            pipe_id: {
                "UNLOADED": 0, "LOADING": 0, "FINISHED": 0, "ERROR": 0,
                "loaded_bytes": 0, "loaded_rows": 0, "last_loaded": None,
            }
            for pipe_id in ids
        }
        for row in rows:
            entry = status[row["pipe_id"]]
            entry[row["load_state"]] = int(row["files"])
            if row["load_state"] == "FINISHED":
                entry["loaded_bytes"] = int(row["bytes"])
                entry["last_loaded"] = row["last_finished"]
        # Rows are counted per batch: a batch of several files reports one total.
        for row in await self._all(
            "SELECT pipe_id::text AS pipe_id, coalesce(sum(rows_loaded),0) AS rows "
            "FROM nova_meta.pipe_batches WHERE pipe_id::text = ANY(%s) AND state='FINISHED' "
            "GROUP BY pipe_id",
            (ids,),
        ):
            status[row["pipe_id"]]["loaded_rows"] = int(row["rows"])
        return status

    # ── The worker's lease ──────────────────────────────────────────────

    async def claim(
        self, pipe_id: UUID | str, token: UUID, *, seconds: int,
    ) -> dict[str, Any] | None:
        """Take the pipe when no live run holds it; ``None`` when another run does."""
        await self._bound()
        return await self._one(
            "UPDATE nova_meta.config_pipes SET claim_token=%s,"
            "claim_generation=claim_generation+1,"
            "claim_expires_at=clock_timestamp()+make_interval(secs=>%s) "
            "WHERE id=%s AND (claim_token IS NULL OR claim_expires_at<clock_timestamp()) "
            "RETURNING *",
            (token, seconds, pipe_id),
        )

    async def fence(self, pipe_id: UUID | str, token: UUID, generation: int) -> dict[str, Any]:
        """Lock the pipe row under this run's claim for the rest of the transaction."""
        await self._bound()
        row = await self._one(
            "SELECT * FROM nova_meta.config_pipes WHERE id=%s AND claim_token=%s "
            "AND claim_generation=%s FOR NO KEY UPDATE",
            (pipe_id, token, generation),
        )
        if row is None:
            raise LeaseLost("The pipe was claimed by another run")
        return row

    async def renew(
        self, pipe_id: UUID | str, token: UUID, generation: int, *, seconds: int,
    ) -> None:
        await self.fence(pipe_id, token, generation)
        await self.connection.execute(
            "UPDATE nova_meta.config_pipes SET "
            "claim_expires_at=clock_timestamp()+make_interval(secs=>%s) WHERE id=%s",
            (seconds, pipe_id),
        )

    async def release(self, pipe_id: UUID | str, token: UUID, generation: int) -> None:
        await self._bound()
        await self.connection.execute(
            "UPDATE nova_meta.config_pipes SET claim_token=NULL,claim_expires_at=NULL "
            "WHERE id=%s AND claim_token=%s AND claim_generation=%s",
            (pipe_id, token, generation),
        )

    async def set_state(
        self, pipe_id: UUID | str, token: UUID, generation: int, *,
        state: str, last_error: str | None,
    ) -> dict[str, Any]:
        """The worker's own state change, at the revision it holds under its lease."""
        row = await self.fence(pipe_id, token, generation)
        if row["state"] == state and row["last_error"] == last_error:
            return row
        updated = await self._one(
            "UPDATE nova_meta.config_pipes SET state=%s,last_error=%s,revision=revision+1 "
            "WHERE id=%s AND revision=%s RETURNING *",
            (state, last_error, pipe_id, row["revision"]),
        )
        if updated is None:
            raise MetadataConflict()
        return updated

    async def suspend_schedule(
        self, pipe_id: UUID | str, token: UUID, generation: int, task_id: str,
    ) -> None:
        """Stop the pipe's schedule (the task login may only suspend a pipe's task)."""
        await self.fence(pipe_id, token, generation)
        await self.connection.execute(
            "UPDATE nova_meta.config_tasks SET lifecycle_state='SUSPENDED' "
            "WHERE id=%s AND handler='pipe.load'",
            (task_id,),
        )

    async def mark_polled(self, pipe_id: UUID | str, token: UUID, generation: int) -> None:
        await self.fence(pipe_id, token, generation)
        await self.connection.execute(
            "UPDATE nova_meta.config_pipes SET last_polled_at=clock_timestamp() WHERE id=%s",
            (pipe_id,),
        )

    # ── The ledger (worker) ─────────────────────────────────────────────

    async def stage_files(
        self, pipe_id: UUID | str, token: UUID, generation: int,
        files: Iterable[tuple[str, str, int, datetime | None]],
    ) -> int:
        """Record file versions not seen before; returns how many are new."""
        await self.fence(pipe_id, token, generation)
        added = 0
        async with self.connection.cursor() as cursor:
            for name, version, size, modified in files:
                await cursor.execute(
                    "INSERT INTO nova_meta.pipe_files "
                    "(pipe_id,file_name,file_version,file_size,last_modified) "
                    "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (pipe_id, name, version, size, modified),
                )
                added += cursor.rowcount
        return added

    async def files_in(self, pipe_id: UUID | str, state: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.pipe_files WHERE pipe_id=%s AND load_state=%s "
            "ORDER BY staged_time,file_name,file_version",
            (pipe_id, state),
        )

    async def batch(self, pipe_id: UUID | str, label: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.pipe_batches WHERE pipe_id=%s AND label=%s",
            (pipe_id, label),
        )

    async def begin_batch(
        self, pipe_id: UUID | str, token: UUID, generation: int, label: str,
        files: list[tuple[str, str]], total_bytes: int,
    ) -> None:
        """Record the intent: the batch and its files are LOADING under ``label``."""
        await self.fence(pipe_id, token, generation)
        existing = await self.batch(pipe_id, label)
        if existing is None:
            await self.connection.execute(
                "INSERT INTO nova_meta.pipe_batches "
                "(pipe_id,label,file_count,total_bytes,claim_generation) VALUES (%s,%s,%s,%s,%s)",
                (pipe_id, label, len(files), total_bytes, generation),
            )
        elif existing["state"] == "FINISHED":
            raise MetadataConflict()
        else:
            await self.connection.execute(
                "UPDATE nova_meta.pipe_batches SET state='LOADING',claim_generation=%s "
                "WHERE pipe_id=%s AND label=%s",
                (generation, pipe_id, label),
            )
        async with self.connection.cursor() as cursor:
            for name, version in files:
                await cursor.execute(
                    "UPDATE nova_meta.pipe_files SET load_state='LOADING',batch_label=%s "
                    "WHERE pipe_id=%s AND file_name=%s AND file_version=%s "
                    "AND load_state='UNLOADED'",
                    (label, pipe_id, name, version),
                )
                if cursor.rowcount != 1:
                    raise MetadataConflict()

    async def finish_batch(
        self, pipe_id: UUID | str, token: UUID, generation: int, label: str,
        rows: dict[tuple[str, str], int | None],
    ) -> None:
        """The engine committed the batch: its files are FINISHED."""
        await self.fence(pipe_id, token, generation)
        total = sum(value or 0 for value in rows.values())
        await self.connection.execute(
            "UPDATE nova_meta.pipe_batches SET state='FINISHED',rows_loaded=%s,claim_generation=%s "
            "WHERE pipe_id=%s AND label=%s AND state='LOADING'",
            (total, generation, pipe_id, label),
        )
        async with self.connection.cursor() as cursor:
            for (name, version), count in rows.items():
                await cursor.execute(
                    "UPDATE nova_meta.pipe_files SET load_state='FINISHED',rows_loaded=%s "
                    "WHERE pipe_id=%s AND file_name=%s AND file_version=%s "
                    "AND load_state='LOADING' AND batch_label=%s",
                    (count, pipe_id, name, version, label),
                )

    async def fail_batch(
        self, pipe_id: UUID | str, token: UUID, generation: int, label: str, *,
        error: str, file_state: str,
    ) -> None:
        """The batch did not load: its files go to ERROR or back to the queue."""
        if file_state not in {"ERROR", "UNLOADED"}:
            raise ValueError("A failed batch's files are ERROR or UNLOADED")
        await self.fence(pipe_id, token, generation)
        await self.connection.execute(
            "UPDATE nova_meta.pipe_batches SET state='FAILED',error_msg=%s,claim_generation=%s "
            "WHERE pipe_id=%s AND label=%s AND state='LOADING'",
            (error[:4096], generation, pipe_id, label),
        )
        await self.connection.execute(
            "UPDATE nova_meta.pipe_files SET load_state=%s,error_msg=%s "
            "WHERE pipe_id=%s AND batch_label=%s AND load_state='LOADING'",
            (file_state, error[:4096] if file_state == "ERROR" else None, pipe_id, label),
        )
