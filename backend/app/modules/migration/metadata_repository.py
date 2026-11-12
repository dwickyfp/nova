"""Migration sources and durable migration jobs in PostgreSQL.

The prepared replacement for the ``NOVA_SYSTEM`` paths of
:class:`MigrationRepository` (source registry) and
:class:`MigrationJobRepository`, switched in at the all-domain cutover.
Compared with the source it:

* stores a source's credential only as a secret-store reference; anything that
  is not a recognisable reference is refused, so a password typed into the
  reference field is never persisted (the refusal does not echo the value);
* refuses a job request or result that carries a credential-named member;
* lets a person read and clean up only their own jobs; the worker claims a
  queued job under a new lease generation, so a worker that lost its claim can
  no longer heartbeat, report progress or record an outcome.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.pq import TransactionStatus

from app.common.responses import SanitizingJSONResponse
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

TERMINAL = ("succeeded", "partial", "failed", "interrupted")
_SOURCE_COLUMNS = "id,name,host,port,username,secret_ref,comment,created_at,created_by,provenance"


def _generation(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("A positive lease generation is required")
    return value


def _safe(result: dict[str, Any] | None) -> str | None:
    return None if result is None else json.dumps(SanitizingJSONResponse._sanitize(result))


class MigrationMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Migration metadata requires an active metadata transaction")
        cursor = await self.connection.execute(
            "SELECT current_setting('nova.principal',true)=%s "
            "AND current_setting('nova.active_role',true)=%s "
            "AND current_setting('nova.security_context_version',true)=%s "
            "AND current_setting('nova.service_identity',true)=current_user AS valid",
            (self.actor.principal, self.actor.active_role,
             str(self.actor.security_context_version)),
        )
        row = await cursor.fetchone()
        if not row or row["valid"] is not True:
            raise MetadataPermissionDenied()

    async def _one(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        cursor = await self.connection.execute(query, params)
        return await cursor.fetchone()

    # ── Sources ───────────────────────────────────────────────────────────

    async def create_source(
        self, *, name: str, host: str, port: int, username: str, secret_ref: str,
        comment: str = "",
    ) -> dict[str, Any]:
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.config_migration_sources (id,name,host,port,username,"
            f"secret_ref,comment,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            f"RETURNING {_SOURCE_COLUMNS}",
            (str(uuid4()), name, host, port, username, secret_ref or None, comment or None,
             self.actor.principal),
        )
        assert row is not None
        return row

    async def list_sources(self) -> list[dict[str, Any]]:
        await self._bound()
        cursor = await self.connection.execute(
            f"SELECT {_SOURCE_COLUMNS} FROM nova_meta.config_migration_sources "
            "ORDER BY name,id", (),
        )
        return list(await cursor.fetchall())

    async def get_source(self, name: str) -> list[dict[str, Any]]:
        """Every source with this name; more than one is an unreconciled import."""
        await self._bound()
        cursor = await self.connection.execute(
            f"SELECT {_SOURCE_COLUMNS} FROM nova_meta.config_migration_sources "
            "WHERE name=%s ORDER BY id", (name,),
        )
        return list(await cursor.fetchall())

    # ── Jobs (the person who queued them) ─────────────────────────────────

    async def create_job(
        self, *, operation: str, session_fingerprint: str, source: str,
        request: dict[str, Any], job_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Queue a job once by id; returns ``(row, created)``."""
        await self._bound()
        job_id = job_id or str(uuid4())
        body = json.dumps(request, sort_keys=True)
        row = await self._one(
            "INSERT INTO nova_meta.config_migration_jobs (id,operation,actor,session_fingerprint,"
            "active_role,security_context_version,source_name,request_json,status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'queued') ON CONFLICT (id) DO NOTHING RETURNING *",
            (job_id, operation, self.actor.principal, session_fingerprint,
             self.actor.active_role, self.actor.security_context_version, source, body),
        )
        if row is not None:
            return row, True
        existing = await self.get_job(job_id)
        if existing is None or (existing["operation"], existing["source_name"],
                                existing["request_json"]) != (operation, source, body):
            raise MetadataConflict()
        return existing, False

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        """A job the caller may see: their own, or any job for the worker."""
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_migration_jobs WHERE id=%s", (job_id,),
        )

    async def delete_finished_job(self, job_id: str) -> bool:
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.config_migration_jobs WHERE id=%s AND status=ANY(%s)",
            (job_id, list(TERMINAL)),
        )
        return cursor.rowcount == 1

    # ── Jobs (the migration worker) ───────────────────────────────────────

    async def queued(self, limit: int = 4) -> list[dict[str, Any]]:
        await self._bound()
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.config_migration_jobs WHERE status='queued' "
            "ORDER BY (operation IN ('execute','execute_batch')),created_at,id LIMIT %s",
            (max(1, min(limit, 100)),),
        )
        return list(await cursor.fetchall())

    async def claim(self, job_id: str) -> int | None:
        """Start a queued job under a new lease; returns its generation, or None."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_migration_jobs SET status='running',"
            "lease_generation=lease_generation+1 WHERE id=%s AND status='queued' "
            "RETURNING lease_generation", (job_id,),
        )
        return int(row["lease_generation"]) if row else None

    async def heartbeat(self, job_id: str, *, generation: int) -> bool:
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_migration_jobs SET heartbeat_at=clock_timestamp() "
            "WHERE id=%s AND status='running' AND lease_generation=%s RETURNING id",
            (job_id, _generation(generation)),
        )
        return row is not None

    async def progress(
        self, job_id: str, *, generation: int, current_database: str | None,
        result: dict[str, Any],
    ) -> bool:
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_migration_jobs SET current_database=%s,result_json=%s,"
            "heartbeat_at=clock_timestamp() WHERE id=%s AND status='running' "
            "AND lease_generation=%s RETURNING id",
            (current_database, _safe(result), job_id, _generation(generation)),
        )
        return row is not None

    async def finish(
        self, job_id: str, *, generation: int, status: str,
        result: dict[str, Any] | None = None, error_code: str | None = None,
    ) -> bool:
        await self._bound()
        if status not in TERMINAL:
            raise ValueError("A migration job finishes in a terminal state")
        row = await self._one(
            "UPDATE nova_meta.config_migration_jobs SET status=%s,"
            "result_json=coalesce(%s,result_json),error_code=%s,heartbeat_at=clock_timestamp() "
            "WHERE id=%s AND status='running' AND lease_generation=%s RETURNING id",
            (status, _safe(result), error_code, job_id, _generation(generation)),
        )
        return row is not None

    async def interrupt_stale(self, *, older_than_seconds: int = 120) -> list[str]:
        await self._bound()
        cursor = await self.connection.execute(
            "UPDATE nova_meta.config_migration_jobs SET status='interrupted',"
            "error_code='worker_interrupted' WHERE status='running' "
            "AND heartbeat_at<clock_timestamp()-make_interval(secs=>%s) RETURNING id",
            (float(older_than_seconds),),
        )
        return [row["id"] for row in await cursor.fetchall()]

    async def prune_completed_reads(self) -> int:
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.config_migration_jobs "
            "WHERE operation NOT IN ('execute','execute_batch') AND status=ANY(%s) "
            "AND finished_at<clock_timestamp()-interval '1 day'", (list(TERMINAL),),
        )
        return cursor.rowcount
