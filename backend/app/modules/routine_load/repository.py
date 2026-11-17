"""Routine-load jobs and Kafka connection grants in the caller's PostgreSQL transaction.

Two kinds of callers use this repository:

* a person's statement (``CREATE``/``ALTER``/``PAUSE``/``RESUME``/``STOP ROUTINE
  LOAD``) through the web, query or proxy login, as their own actor. Every
  signed-in actor reads which jobs Nova manages; forced RLS lets only the owner
  role and administrators change one;
* the reconciler in the maintenance worker, as the ``nova-routine-load``
  SERVICE actor, which reads every job and records what it found.

Every change names the revision it read (compare-and-swap). The engine job is
never touched here: the service records intent before it calls the engine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.core.metadata_scope import metadata_transaction
from app.modules.access_control.security_context import SecurityContext

#: The SERVICE actor of the reconciler (``routine_load_reconciler()`` in PG).
RECONCILER = SecurityContext("nova-routine-load", "SECURITYADMIN", principal_type="SERVICE")

#: Nova states of a job that still has (or may have) an engine job.
LIVE_STATES = ("CREATING", "UNKNOWN", "ACTIVE", "PAUSED_BY_NOVA")

_CREATE = (
    "id", "database_name", "job_name", "table_name", "connection_name", "topic", "data_format",
    "owner_principal", "owner_role", "security_context_version",
)
_CHANGES = frozenset({
    "engine_job_id", "state", "pause_reason", "security_context_version",
    "last_checked_at", "last_check_error",
})


class RoutineLoadRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Routine-load metadata requires an active metadata transaction")
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

    # ── Jobs ──────────────────────────────────────────────────────────

    async def create_job(self, data: dict[str, Any]) -> dict[str, Any]:
        """Record the intent to create an engine job (state ``CREATING``)."""
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.routine_load_jobs (" + ",".join(_CREATE) + ") VALUES ("
            + ",".join(["%s"] * len(_CREATE)) + ") RETURNING *",
            tuple(data[field] for field in _CREATE),
        )
        assert row is not None
        return row

    async def live_job(self, database: str, job_name: str) -> dict[str, Any] | None:
        """The job Nova manages under this engine name, if any."""
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.routine_load_jobs WHERE database_name=%s AND job_name=%s "
            "AND state = ANY(%s)",
            (database, job_name, list(LIVE_STATES)),
        )

    async def latest_job(self, database: str, job_name: str) -> dict[str, Any] | None:
        """The newest row for this engine name, live or not."""
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.routine_load_jobs WHERE database_name=%s AND job_name=%s "
            "ORDER BY created_at DESC LIMIT 1",
            (database, job_name),
        )

    async def get_job(self, job_id: UUID | str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one("SELECT * FROM nova_meta.routine_load_jobs WHERE id=%s", (job_id,))

    async def live_jobs(self) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.routine_load_jobs WHERE state = ANY(%s) "
            "ORDER BY database_name,job_name",
            (list(LIVE_STATES),),
        )

    async def live_jobs_using(self, connection_name: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.routine_load_jobs WHERE connection_name=%s "
            "AND state = ANY(%s) ORDER BY database_name,job_name",
            (connection_name, list(LIVE_STATES)),
        )

    async def update_job(
        self, job_id: UUID | str, changes: dict[str, Any], *, expected_revision: int,
    ) -> dict[str, Any]:
        """Change a job only at the revision the caller read.

        ``last_checked_at`` takes any value: the database records its own clock.
        """
        await self._bound()
        if not changes or set(changes) - _CHANGES:
            raise ValueError("Unsupported routine-load change")
        assignments: list[str] = []
        values: list[Any] = []
        for field, value in changes.items():
            if field == "last_checked_at":
                assignments.append("last_checked_at=clock_timestamp()")
                continue
            assignments.append(f"{field}=%s")
            values.append(value)
        row = await self._one(
            f"UPDATE nova_meta.routine_load_jobs SET {','.join(assignments)},revision=revision+1 "
            "WHERE id=%s AND revision=%s RETURNING *",
            (*values, job_id, expected_revision),
        )
        if row is None:
            raise MetadataConflict()
        return row

    # ── Connection grants ─────────────────────────────────────────────

    async def connection_roles(self, connection_name: str) -> list[str]:
        await self._bound()
        rows = await self._all(
            "SELECT role_name FROM nova_meta.catalog_connection_roles "
            "WHERE connection_name=%s ORDER BY role_name",
            (connection_name,),
        )
        return [str(row["role_name"]) for row in rows]

    async def set_connection_roles(self, connection_name: str, roles: list[str]) -> list[str]:
        """Replace the roles allowed to load from a connection (security admins only)."""
        await self._bound()
        current = set(await self.connection_roles(connection_name))
        wanted = set(roles)
        for role in sorted(current - wanted):
            await self.connection.execute(
                "DELETE FROM nova_meta.catalog_connection_roles "
                "WHERE connection_name=%s AND role_name=%s",
                (connection_name, role),
            )
        for role in sorted(wanted - current):
            await self.connection.execute(
                "INSERT INTO nova_meta.catalog_connection_roles (connection_name,role_name) "
                "VALUES (%s,%s)",
                (connection_name, role),
            )
        return sorted(wanted)


@asynccontextmanager
async def routine_load_transaction(
    actor: SecurityContext, *, read_only: bool = False,
) -> AsyncIterator[RoutineLoadRepository]:
    """One actor-bound metadata transaction with the routine-load repository."""
    async with metadata_transaction(actor, read_only=read_only) as scope:
        yield RoutineLoadRepository(scope.connection, scope.actor)
