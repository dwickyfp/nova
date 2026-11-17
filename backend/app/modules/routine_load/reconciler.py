"""Keeps engine Routine Load jobs within their owner's access.

Runs in the maintenance worker beside the catalog refresher; a Redis leader
lock lets one instance do each pass. A pass reads every job Nova manages and,
for each one:

* a job whose create outcome was not observed (``UNKNOWN``, or ``CREATING``
  for longer than a statement can take) becomes ``ACTIVE`` when the engine
  has it and ``FAILED`` when it does not; nothing is created again;
* a job the engine no longer has, or has stopped or cancelled, is ``STOPPED``;
* a running job whose owner lost the role, the write access to the table or
  the use of the connection is paused on the engine and recorded as
  ``PAUSED_BY_NOVA`` with the reason. Only ``RESUME`` by the owner role or an
  administrator, after the same checks pass, starts it again.

The engine is read and paused through the control plane's system session:
pausing is enforcement, not data access, and the job keeps its owner. A check
that cannot complete (Ranger or the engine unavailable) changes nothing and is
recorded on the job, so an outage never pauses or resumes jobs by itself.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.redis import session_store
from app.modules.task_orchestration.transport import LeaderLock

from .repository import RECONCILER, routine_load_transaction
from .service import (
    ENGINE_LIVE,
    ENGINE_RUNNING,
    EngineReply,
    access_problem,
    engine_job,
    grant_state,
    quote_name,
)

logger = logging.getLogger(__name__)

_LOCK = "nova:routine-load:reconciler"
#: Seconds between passes; a revoked owner's job is paused within about this long.
INTERVAL_SECONDS = 60
#: A CREATING row older than this lost its statement: verify it on the engine.
STALE_CREATE = timedelta(minutes=2)


async def system_run(sql: str) -> EngineReply:
    from app.core.database import db

    result = await db.execute_system(sql)
    return EngineReply(list(result.get("columns") or []), list(result.get("rows") or []))


async def _record(job: dict[str, Any], **changes: Any) -> dict[str, Any]:
    async with routine_load_transaction(RECONCILER) as repo:
        return await repo.update_job(
            job["id"], {**changes, "last_checked_at": True}, expected_revision=job["revision"]
        )


async def reconcile_job(job: dict[str, Any], run=system_run) -> str:
    """Check one job; returns what was done (for logs and tests)."""
    database, name = job["database_name"], job["job_name"]
    try:
        found = await engine_job(run, database, name)
    except Exception as exc:  # noqa: BLE001 - try again next pass
        await _record(job, last_check_error=f"Engine unavailable: {type(exc).__name__}"[:1024])
        return "engine_unavailable"
    ours = found is not None and (
        job.get("engine_job_id") is None or str(found.get("Id")) == str(job["engine_job_id"])
    )
    engine_state = str(found.get("State") or "").upper() if ours and found else ""

    if job["state"] in {"CREATING", "UNKNOWN"}:
        created = job.get("created_at")
        if job["state"] == "CREATING" and isinstance(created, datetime) and (
            datetime.now(UTC) - created < STALE_CREATE
        ):
            return "creating"
        if ours and engine_state in ENGINE_LIVE and found is not None:
            await _record(job, state="ACTIVE", engine_job_id=str(found["Id"]),
                          last_check_error=None)
            return "verified"
        await _record(job, state="FAILED",
                      last_check_error="The engine has no job of this name")
        return "failed"

    if not ours or engine_state not in ENGINE_LIVE:
        await _record(job, state="STOPPED", last_check_error=(
            "The engine job was stopped" if ours else "The engine no longer has this job"
        ))
        return "stopped"

    try:
        async with routine_load_transaction(RECONCILER, read_only=True) as repo:
            connection, roles = await grant_state(repo, job["connection_name"])
        problem = await access_problem(job, connection, roles)
    except Exception as exc:  # noqa: BLE001 - never pause on an unanswered check
        await _record(job, last_check_error=f"Access check unavailable: {type(exc).__name__}")
        return "check_unavailable"

    if problem is None:
        if job.get("last_check_error"):
            await _record(job, last_check_error=None)
        else:
            await _record(job)
        return "ok"
    if engine_state in ENGINE_RUNNING:
        try:
            await run(f"PAUSE ROUTINE LOAD FOR {quote_name(database, name)}")
        except Exception as exc:  # noqa: BLE001 - retried next pass
            await _record(job, last_check_error=f"Pause failed: {type(exc).__name__}")
            return "pause_failed"
    if job["state"] != "PAUSED_BY_NOVA" or job.get("pause_reason") != problem:
        await _record(job, state="PAUSED_BY_NOVA", pause_reason=problem[:1024],
                      last_check_error=None)
        logger.info("Paused routine load %s.%s: %s", database, name, problem)
        return "paused"
    await _record(job)
    return "kept_paused"


async def reconcile_once() -> dict[str, int]:
    """One pass over every job Nova manages."""
    async with routine_load_transaction(RECONCILER, read_only=True) as repo:
        jobs = await repo.live_jobs()
    outcomes: dict[str, int] = {}
    for job in jobs:
        try:
            outcome = await reconcile_job(job)
        except Exception:  # noqa: BLE001 - one job never stops the pass
            logger.warning("Routine load check of %s.%s failed", job["database_name"],
                           job["job_name"], exc_info=True)
            outcome = "error"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return outcomes


async def run_once_if_leader() -> bool:
    client = session_store._redis
    if client is None:
        return False
    lock = LeaderLock(client, key=_LOCK, ttl_seconds=INTERVAL_SECONDS * 4)
    if not await lock.acquire():
        return False
    try:
        await reconcile_once()
    finally:
        await lock.release()
    return True


async def run(stop: asyncio.Event) -> None:
    """Check on an interval until ``stop`` is set; the first pass runs at once."""
    while not stop.is_set():
        try:
            await run_once_if_leader()
        except Exception:
            logger.warning("Routine load reconciler pass failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL_SECONDS)
        except TimeoutError:
            continue
