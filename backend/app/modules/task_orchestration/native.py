"""Observation of native StarRocks task state for reconciliation (NOVA-37).

The reconciler must learn three things from the engine, and nothing else:

* the **latest native state** of a task's run, so a node's persisted state can be
  advanced (``SUCCESS`` / ``FAILED`` / ``PENDING`` / ``RUNNING``);
* whether that run's trace has **disappeared** — a task running when the FE dies
  leaves no row at all (design §1), so an absent trace is an explicit
  ``ABANDONED``, never a silent success;
* whether the engine has **auto-paused** the task after
  ``max_task_consecutive_fail_count`` consecutive failures, which silently stops
  a DAG advancing (design §1).

Every read is best-effort. The engine's ``information_schema.task_runs`` is
served by an internal archive read that can fail independently of the task
(observed as a 1064 on ``_statistics_.task_run_history``, e.g. on a fresh FE
whose archive is not yet initialized), and ``ADMIN SHOW FRONTEND CONFIG`` is an
FE-config statement that may be refused.

A reconciliation pass that cannot observe must report ``UNKNOWN`` for that
signal rather than invent a state. This matters because the archive failure is
**engine-wide and carries no per-task information**: on a fresh FE it fires for
every ``task_runs`` read while ordinary statements still succeed, so it cannot
say whether any particular task's trace is absent. Deriving ``MISSING`` from it
would abandon healthy rows (NOVA-43, NOVA-46); the safe response is ``UNKNOWN``,
which the reconciler writes nothing for.

Lost traces are therefore settled by the **durable heartbeat path**, not by the
unreadable archive: ``Reconciler.scan`` abandons a ``RUNNING`` node whose worker
heartbeat has lapsed, independent of ``task_runs`` (design §3). ``MISSING``
remains valid only when a **readable** surface proves absence — a successful
read that returned no row.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.core.database import db
from app.modules.task_orchestration.execution import (
    _as_dicts,
    _dict_cursor,
    _redact,
)

logger = logging.getLogger(__name__)

#: Native TaskRun states that are finished without error.
_NATIVE_SUCCESS = frozenset({"FINISHED", "SUCCESS"})
#: Native TaskRun states that are finished as a failure.
_NATIVE_FAILURE = frozenset({"FAILED", "CANCELLED"})
#: Native TaskRun states that are still in flight.
_NATIVE_PENDING = frozenset({"PENDING", "RUNNING", "QUEUED"})


class NativeState(StrEnum):
    """What the engine says about one task's most recent run.

    ``UNKNOWN`` is deliberately distinct from ``MISSING``: an ``UNKNOWN`` is a
    read that failed (the engine or its archive surface was unavailable), while
    ``MISSING`` is a read that succeeded and found no row — the lost-trace case
    the FE-restart fact describes. Conflating them would let a transient read
    failure masquerade as lost work.
    """

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RUNNING = "RUNNING"
    PENDING = "PENDING"
    #: The read succeeded but the task has no run row at all.
    MISSING = "MISSING"
    #: The read itself failed; nothing may be inferred.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class NativeRun:
    """One observation of a task's latest native run."""

    task_name: str
    state: NativeState
    query_id: str | None = None
    error_message: str | None = None
    create_time: datetime | None = None


@dataclass(frozen=True)
class NativeConfig:
    """The FE config values the reconciler needs, when the engine exposes them."""

    task_runs_ttl_second: int | None = None
    max_task_consecutive_fail_count: int | None = None
    available: bool = False


def _classify(raw: str | None) -> NativeState:
    if raw is None:
        return NativeState.MISSING
    upper = raw.strip().upper()
    if not upper:
        return NativeState.MISSING
    if upper in _NATIVE_SUCCESS:
        return NativeState.SUCCESS
    if upper in _NATIVE_FAILURE:
        return NativeState.FAILED
    if upper in _NATIVE_PENDING:
        return NativeState.PENDING
    return NativeState.UNKNOWN


async def read_latest_native_run(conn: Any, task_name: str) -> NativeRun:
    """One best-effort read of a task's latest ``task_runs`` row.

    A successful read with no rows is ``MISSING`` — the lost-trace case. A
    failed read is ``UNKNOWN``; the caller must not treat either as success.
    """
    sql = (
        "SELECT TASK_NAME, QUERY_ID, STATE, ERROR_MESSAGE, CREATE_TIME "
        "FROM information_schema.task_runs WHERE TASK_NAME = %s "
        "ORDER BY CREATE_TIME DESC LIMIT 1"
    )
    try:
        async with _dict_cursor(conn) as cur:
            await cur.execute(sql, (task_name,))
            rows = _as_dicts(await cur.fetchall())
    except Exception as exc:
        logger.warning(
            "could not read native task state for %s: %s", task_name, _redact(str(exc))
        )
        return NativeRun(task_name=task_name, state=NativeState.UNKNOWN)

    if not rows:
        if not await probe_engine_liveness(conn):
            return NativeRun(task_name=task_name, state=NativeState.UNKNOWN)
        return NativeRun(task_name=task_name, state=NativeState.MISSING)
    row = rows[0]
    error = row.get("ERROR_MESSAGE")
    create_time = row.get("CREATE_TIME")
    return NativeRun(
        task_name=task_name,
        state=_classify(row.get("STATE") if row.get("STATE") else None),
        query_id=str(row.get("QUERY_ID")) if row.get("QUERY_ID") else None,
        error_message=_redact(str(error)) if error else None,
        create_time=create_time if isinstance(create_time, datetime) else None,
    )


async def probe_engine_liveness(conn: Any) -> bool:
    """Whether a statement on ``conn`` actually reaches a live engine.

    A pooled connection can keep answering from an established TCP session for
    a short window after the FE dies, so a query that returns zero rows is not
    proof the engine is up. ``SELECT 1`` forces a round trip; if it fails, any
    empty result read on this connection is untrustworthy and must be treated
    as ``UNKNOWN`` rather than "no rows exist" (NOVA-43).

    Never raises: a probe that cannot run is itself the answer.
    """
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
            await cur.fetchone()
    except Exception as exc:
        logger.warning("engine liveness probe failed: %s", _redact(str(exc)))
        return False
    return True


async def read_latest_native_runs(
    conn: Any, task_names: list[str]
) -> dict[str, NativeRun]:
    """Read the latest run for several tasks in one query per batch.

    The reconciler polls only graph runs that are actually ``RUNNING`` (design
    §2), so the name list is bounded by in-flight work, not by every task.

    A task with **no rows** is ``MISSING`` only when the engine is proven live
    on this same connection; otherwise it is ``UNKNOWN``. Without that check a
    stale pooled connection would report every in-flight task as ``MISSING``,
    and the reconciler would mark healthy work ``abandoned`` (NOVA-43).

    A **failed** read is always ``UNKNOWN``, regardless of the engine's
    liveness: the archive failure behind it is engine-wide and says nothing
    about any individual task's trace, so no verdict may be drawn from it
    (NOVA-46). The lost trace is settled by the heartbeat path instead.
    """
    if not task_names:
        return {}
    placeholders = ", ".join(["%s"] * len(task_names))
    sql = (
        "SELECT TASK_NAME, QUERY_ID, STATE, ERROR_MESSAGE, CREATE_TIME "
        "FROM information_schema.task_runs "
        f"WHERE TASK_NAME IN ({placeholders}) "
        "ORDER BY TASK_NAME, CREATE_TIME DESC"
    )
    try:
        async with _dict_cursor(conn) as cur:
            await cur.execute(sql, tuple(task_names))
            rows = _as_dicts(await cur.fetchall())
    except Exception as exc:
        logger.warning(
            "could not read native task states for %d tasks: %s",
            len(task_names),
            _redact(str(exc)),
        )
        return {name: NativeRun(task_name=name, state=NativeState.UNKNOWN) for name in task_names}

    if not rows and not await probe_engine_liveness(conn):
        return {
            name: NativeRun(task_name=name, state=NativeState.UNKNOWN)
            for name in task_names
        }

    latest: dict[str, NativeRun] = {}
    for row in rows:
        name = str(row.get("TASK_NAME") or "")
        if not name or name in latest:
            continue
        error = row.get("ERROR_MESSAGE")
        latest[name] = NativeRun(
            task_name=name,
            state=_classify(row.get("STATE") if row.get("STATE") else None),
            query_id=str(row.get("QUERY_ID")) if row.get("QUERY_ID") else None,
            error_message=_redact(str(error)) if error else None,
            create_time=(
                row.get("CREATE_TIME")
                if isinstance(row.get("CREATE_TIME"), datetime)
                else None
            ),
        )
    for name in task_names:
        latest.setdefault(name, NativeRun(task_name=name, state=NativeState.MISSING))
    return latest


#: Keys the reconciler reads from ``ADMIN SHOW FRONTEND CONFIG LIKE '%task%'``.
#: The correct surface is the FE config statement, **not** ``SHOW VARIABLES`` —
#: these are FE settings, not session variables (design §1).
_TASK_RUNS_TTL_KEY = "task_runs_ttl_second"
_MAX_CONSECUTIVE_FAIL_KEY = "max_task_consecutive_fail_count"

#: ``ALTER TASK … SUSPEND``/pause shows up in the ``SCHEDULE`` string. The
#: engine has no queryable ``STATE`` column on ``information_schema.tasks``
#: (design §1), so this string is the only native pause signal.
_PAUSE_MARKERS = ("PAUSE", "SUSPEND")


def read_frontend_config_rows(rows: Any) -> NativeConfig:
    """Parse ``ADMIN SHOW FRONTEND CONFIG`` rows into the two values needed.

    Tolerant by design: a missing key leaves the value ``None`` and the caller
    falls back to its configured default rather than failing. Keys are matched
    case-insensitively on the ``Key``/``Value`` columns StarRocks returns.
    """
    found: dict[str, str] = {}
    for row in _as_dicts(rows):
        key = str(row.get("Key") or row.get("KEY") or row.get("key") or "").strip().lower()
        if key in {_TASK_RUNS_TTL_KEY, _MAX_CONSECUTIVE_FAIL_KEY}:
            found[key] = str(row.get("Value") or row.get("VALUE") or row.get("value") or "")

    def _int(name: str) -> int | None:
        raw = found.get(name)
        if raw is None or not raw.strip():
            return None
        try:
            return int(raw.strip())
        except ValueError:
            return None

    return NativeConfig(
        task_runs_ttl_second=_int(_TASK_RUNS_TTL_KEY),
        max_task_consecutive_fail_count=_int(_MAX_CONSECUTIVE_FAIL_KEY),
        available=bool(found),
    )


async def read_native_config(conn: Any | None = None) -> NativeConfig:
    """Read task-related FE config via ``ADMIN SHOW FRONTEND CONFIG LIKE '%task%'``.

    Best-effort: if the statement is refused or the engine is unavailable, the
    result is ``NativeConfig(available=False)`` with the configured defaults
    left to the caller.
    """
    sql = "ADMIN SHOW FRONTEND CONFIG LIKE '%task%'"
    try:
        if conn is None:
            async with db.system_conn() as system_conn:
                return await _read_config_on(system_conn, sql)
        return await _read_config_on(conn, sql)
    except Exception as exc:
        logger.warning("could not read FE task config: %s", _redact(str(exc)))
        return NativeConfig(available=False)


async def fetch_native_runs(task_names: list[str]) -> dict[str, NativeRun]:
    """Read native runs on a pooled system connection, tolerating an unreachable engine.

    Connection acquisition is inside the guard: when the FE is down,
    ``db.system_conn()`` is what raises, and the reconciler must degrade to
    ``UNKNOWN`` rather than propagate (NOVA-44).
    """
    try:
        async with db.system_conn() as conn:
            return await read_latest_native_runs(conn, task_names)
    except Exception as exc:
        logger.warning(
            "could not acquire a connection to read native runs: %s", _redact(str(exc))
        )
        return {
            name: NativeRun(task_name=name, state=NativeState.UNKNOWN)
            for name in task_names
        }


async def fetch_native_schedules(task_names: list[str]) -> dict[str, str]:
    """Read native ``SCHEDULE`` strings, tolerating an unreachable engine.

    Returns an empty map on failure: an unreadable schedule is not a pause
    marker, so the caller falls back to its failure counter.
    """
    try:
        async with db.system_conn() as conn:
            return await read_native_schedules(conn, task_names)
    except Exception as exc:
        logger.warning(
            "could not acquire a connection to read native schedules: %s",
            _redact(str(exc)),
        )
        return {}


async def _read_config_on(conn: Any, sql: str) -> NativeConfig:
    async with _dict_cursor(conn) as cur:
        await cur.execute(sql)
        rows = await cur.fetchall()
    return read_frontend_config_rows(rows)


def schedule_is_paused(schedule: str | None) -> bool:
    """Whether a native ``SCHEDULE`` string signals a paused/suspended task.

    The engine exposes no task state on ``information_schema.tasks``; the
    ``SCHEDULE`` string is the only native surface. A marker match is a hint,
    not proof, so callers cross-check the consecutive-failure count.
    """
    if not schedule:
        return False
    upper = schedule.upper()
    return any(marker in upper for marker in _PAUSE_MARKERS)


async def read_native_schedules(
    conn: Any, task_names: list[str]
) -> dict[str, str]:
    """Read each task's native ``SCHEDULE`` string, best-effort.

    ``information_schema.tasks`` has no ``STATE`` column (design §1), so the
    ``SCHEDULE`` string is the only native surface that can show a task has
    been paused/suspended. A read failure yields an empty map; the caller then
    falls back to its own consecutive-failure counter rather than treating an
    unreadable schedule as a pause.
    """
    if not task_names:
        return {}
    placeholders = ", ".join(["%s"] * len(task_names))
    sql = (
        "SELECT TASK_NAME, SCHEDULE FROM information_schema.tasks "
        f"WHERE TASK_NAME IN ({placeholders})"
    )
    try:
        async with _dict_cursor(conn) as cur:
            await cur.execute(sql, tuple(task_names))
            rows = _as_dicts(await cur.fetchall())
    except Exception as exc:
        logger.warning(
            "could not read native task schedules: %s", _redact(str(exc))
        )
        return {}
    return {
        str(row.get("TASK_NAME")): str(row.get("SCHEDULE") or "")
        for row in rows
        if row.get("TASK_NAME")
    }


def parse_consecutive_failures(error_message: str | None) -> int | None:
    """Extract a consecutive-failure count the engine embeds in an error.

    StarRocks reports auto-pause reasons as text (e.g. "task has failed 10
    consecutive times"), so the count is parsed rather than assumed. Returns
    ``None`` when no number is present.
    """
    if not error_message:
        return None
    match = re.search(r"(\d+)\s*consecutive", error_message, re.IGNORECASE)
    if match is None:
        return None
    return int(match.group(1))
