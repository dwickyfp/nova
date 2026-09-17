"""Delegate-first node execution against StarRocks.

A graph node lowers to its own ``SUBMIT TASK`` and is submitted **on the task
owner's connection** (design D9.4), so the engine checks privileges against the
submitter and records them in ``CREATOR``. Nova adds no authorization logic; if
the owner's grants do not cover the body, the engine rejects the submit and the
node fails.

The engine has **no completion hook** and cannot trigger another statement, so
after submitting, the worker can only learn the outcome by polling
``information_schema.task_runs`` (design §1). That polling is unavoidable Nova
code — see :func:`poll_task_run`.

The connection is opened with the owner's credential, which comes from an
:class:`~app.modules.task_orchestration.credentials.OwnerCredentialProvider` at
execution time and is discarded when the connection closes. No credential is
ever logged or persisted; only the engine's own run identifier is recorded.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.core.config import settings
from app.core.database import db
from app.modules.task_orchestration.credentials import OwnerCredentialProvider

logger = logging.getLogger(__name__)

#: Native TaskRun states. ``FINISHED`` is success; ``CANCELLED`` is failure.
_SUCCESS_STATES = frozenset({"FINISHED", "SUCCESS"})
_FAILURE_STATES = frozenset({"CANCELLED", "FAILED"})
_PENDING_STATES = frozenset({"PENDING", "RUNNING", "QUEUED"})


class NodeExecutionError(RuntimeError):
    """A node's delegated run did not succeed.

    ``message`` is engine-supplied and has already been stripped of anything
    credential-shaped; ``query_id`` is the native run identifier when known.
    """

    def __init__(self, message: str, *, query_id: str | None = None) -> None:
        super().__init__(message)
        self.query_id = query_id


class NodeConnection(Protocol):
    """The slice of a StarRocks connection the executor uses.

    Injected rather than constructed so the delegate-first path can be tested
    without a real engine, and so the executor can never accidentally open a
    root connection itself.
    """

    def cursor(self, cursor_class: Any = None) -> Any: ...


@dataclass(frozen=True)
class TaskSpec:
    """The engine-facing shape of one node's execution."""

    name: str
    body: str
    database: str | None = None


@dataclass(frozen=True)
class ExecutionResult:
    """The observable outcome of one delegated node execution."""

    query_id: str | None
    state: str
    error_message: str | None = None


def build_submit_task(spec: TaskSpec) -> str:
    """Lower a node definition to the engine statement the worker submits.

    Only the four forms ``SUBMIT TASK`` actually accepts are produced — a bare
    ``AS body``. ``SCHEDULE``/``PROPERTIES`` are deliberately absent: the DAG is
    Nova-owned, so every node is submitted as a **one-shot** and Nova fires the
    next node itself after polling. Sending a ``SCHEDULE`` here would make the
    engine re-run the body on its own clock, outside the graph.

    The body is passed through verbatim; the engine's grammar validates it
    (CTAS / INSERT / CACHE SELECT) before anything runs, so "the body must be
    delegatable" is enforced by the parser, not by Nova (design §1).
    """
    identifier = _quote_identifier(spec.database, spec.name)
    return f"SUBMIT TASK {identifier} AS {spec.body}"


def _quote_identifier(database: str | None, name: str) -> str:
    escaped = name.replace("`", "``")
    if database:
        return f"`{database.replace('`', '``')}`.`{escaped}`"
    return f"`{escaped}`"


def _dict_cursor(conn: NodeConnection) -> Any:
    dict_cursor = importlib.import_module("asyncmy.cursors").DictCursor
    return conn.cursor(dict_cursor)


def _redact(message: str) -> str:
    """Strip anything credential-shaped from an engine error.

    The engine echoes the rejected statement, which can contain storage
    credentials after ``@stage`` rewriting. The worker must never surface those.
    """
    from app.common.sql_guard import redact_sql_credentials

    return redact_sql_credentials(message)


class DelegateExecutor:
    """Runs one node as its owner, then observes completion by polling."""

    def __init__(
        self,
        credentials: OwnerCredentialProvider,
        *,
        poll_interval: float | None = None,
        poll_timeout: float | None = None,
    ) -> None:
        self._credentials = credentials
        self._poll_interval = (
            poll_interval
            if poll_interval is not None
            else settings.WORKER_TASK_POLL_INTERVAL_SECONDS
        )
        self._poll_timeout = (
            poll_timeout
            if poll_timeout is not None
            else settings.WORKER_TASK_POLL_TIMEOUT_SECONDS
        )

    async def evaluate_when(self, expression: str, *, spec: TaskSpec, owner: str) -> bool:
        """Evaluate a node's ``WHEN`` expression on the owner's connection.

        Snowflake semantics: ``WHEN`` decides whether the node runs, not
        whether the graph succeeds. A false result skips the node and every
        node below it (design: "``WHEN`` FALSE → node dan turunannya tidak
        jalan").

        Evaluated on the owner's connection so the expression only sees data
        the owner may read. A non-boolean or failing expression is **not**
        silently treated as "no data": an error is raised so the node is
        recorded as failed rather than skipped (design §6).
        """
        password = await self._credentials.password_for(owner)
        async with db.user_conn(owner, password, database=spec.database) as conn:
            del password
            async with _dict_cursor(conn) as cur:
                try:
                    await cur.execute(f"SELECT ({expression}) AS nova_when")
                    row = await cur.fetchone()
                except Exception as exc:
                    raise NodeExecutionError(
                        f"WHEN evaluation failed for {spec.name!r}: {_redact(str(exc))}"
                    ) from exc
        if not isinstance(row, dict) or "nova_when" not in row:
            raise NodeExecutionError(
                f"WHEN expression for {spec.name!r} did not return a value"
            )
        return _truthy(row["nova_when"])

    async def execute(
        self,
        spec: TaskSpec,
        owner: str,
        *,
        heartbeat: Callable[[], Awaitable[None]] | None = None,
    ) -> ExecutionResult:
        """Submit ``spec`` on ``owner``'s connection and wait for the TaskRun.

        Raises :class:`NodeExecutionError` on submit failure or a failed run.
        The plaintext password is resolved inside this call and dropped when the
        ``async with`` closes the connection.

        **The owner's connection exists to submit, nothing else.** Its entire
        purpose is that StarRocks checks the body's privileges against the
        owner, so a forbidden table is refused by the engine (5203) at
        ``SUBMIT TASK``. Perception of the run's outcome is Nova's own
        bookkeeping and must not be gated by the owner's grants: on some FEs the
        ``information_schema.task_runs`` read is backed by an internal archive
        table the owner cannot see, and polling as the owner turned the
        engine's real refusal into an unrelated internal 1064. So the submit
        runs on the owner connection and the watermark/poll runs on the system
        connection.

        ``heartbeat``, when given, is awaited on each poll so a long native run
        keeps its node row visibly alive; a worker that dies stops stamping and
        the reconciler can then tell the row is abandoned.
        """
        # ``SUBMIT TASK`` needs a session database even when the body is fully
        # qualified (verified on 4.1.1: "No database selected"). The task's
        # ``database_name`` supplies it; without one there is no safe default,
        # so the node fails rather than guessing a catalog.
        if not spec.database:
            raise NodeExecutionError(
                f"task {spec.name!r} has no database; SUBMIT TASK requires one"
            )

        statement = build_submit_task(spec)

        # The watermark is read before the submit so the new run can be
        # identified by being strictly newer. It is read on the system
        # connection for the reason in the docstring, and it is **best-effort**:
        # the engine's ``information_schema.task_runs`` is served by an internal
        # archive read that can fail independently of the task (observed as a
        # 1064 on `_statistics_.task_run_history`). Losing the watermark only
        # costs precision in identifying the new run — the poll falls back to
        # the newest run — so it must never stop the submit, which is where the
        # owner's RBAC is actually enforced.
        watermark: datetime | None = None
        try:
            async with db.system_conn() as observer:
                watermark = await self._latest_create_time(observer, spec.name)
        except Exception as exc:
            logger.warning(
                "could not read the task-run watermark for %s; continuing: %s",
                spec.name,
                _redact(str(exc)),
            )

        password = await self._credentials.password_for(owner)
        async with db.user_conn(owner, password, database=spec.database) as conn:
            # Drop the local reference as early as possible; the connection
            # owns whatever it needs from here on.
            del password
            await self._submit(conn, statement)

        async with db.system_conn() as observer:
            result = await self._await_completion(
                observer, spec.name, watermark=watermark, heartbeat=heartbeat
            )

        if result.state in _FAILURE_STATES:
            raise NodeExecutionError(
                result.error_message or f"task {spec.name!r} failed",
                query_id=result.query_id,
            )
        return result

    async def _submit(self, conn: NodeConnection, statement: str) -> None:
        """Execute ``SUBMIT TASK``.

        Nothing from the statement is logged: the body can embed storage
        credentials after ``@stage`` rewriting.
        """
        try:
            async with conn.cursor() as cur:
                await cur.execute(statement)
                await cur.fetchone()
        except Exception as exc:
            raise NodeExecutionError(_redact(str(exc))) from exc

    async def _latest_create_time(
        self, conn: NodeConnection, task_name: str
    ) -> datetime | None:
        """Newest native run time for this task, before this submit.

        The engine's ``SUBMIT TASK`` result only echoes ``TaskName``/``Status``,
        so the submitted run is identified by being strictly newer than this
        watermark. Times are the engine's own naive ``DATETIME`` values.
        """
        sql = (
            "SELECT CREATE_TIME FROM information_schema.task_runs "
            "WHERE TASK_NAME = %s ORDER BY CREATE_TIME DESC LIMIT 1"
        )
        async with _dict_cursor(conn) as cur:
            await cur.execute(sql, (task_name,))
            rows = _as_dicts(await cur.fetchall())
        return _as_datetime(rows[0].get("CREATE_TIME")) if rows else None

    async def _await_completion(
        self,
        conn: NodeConnection,
        task_name: str,
        *,
        watermark: datetime | None,
        heartbeat: Callable[[], Awaitable[None]] | None = None,
    ) -> ExecutionResult:
        """Poll ``information_schema.task_runs`` until the new run settles.

        Losing the run row (an FE restart drops in-flight runs with no trace,
        design §1) is not success: after the timeout the node is reported
        failed so the graph does not advance on an unknown outcome.

        A transient read failure is neither: the engine's task-run surface can
        fail while the run itself is fine, so a failed poll is retried until the
        deadline rather than failing the node on the first error.
        """
        deadline = asyncio.get_running_loop().time() + self._poll_timeout
        while True:
            try:
                result = await self._newest_run_after(conn, task_name, watermark)
            except Exception as exc:
                logger.warning(
                    "task-run poll for %s failed; retrying: %s",
                    task_name,
                    _redact(str(exc)),
                )
                result = None
            if result is not None and result.state not in _PENDING_STATES:
                return result

            if heartbeat is not None:
                await heartbeat()

            if asyncio.get_running_loop().time() >= deadline:
                raise NodeExecutionError(
                    f"task {task_name!r} did not finish within "
                    f"{self._poll_timeout:.0f}s; its trace may have been lost",
                    query_id=result.query_id if result else None,
                )
            await asyncio.sleep(self._poll_interval)

    async def _newest_run_after(
        self,
        conn: NodeConnection,
        task_name: str,
        watermark: datetime | None,
    ) -> ExecutionResult | None:
        """The newest run strictly newer than ``watermark``, or ``None``.

        ``CREATE_TIME`` has second granularity, so a run submitted in the same
        second as the watermark ties. A tie is treated as *this* submit only
        when no older run is being waited on; the worker polls until the state
        is terminal anyway, so a stale tie resolves on the next tick.
        """
        sql = (
            "SELECT TASK_NAME, QUERY_ID, STATE, ERROR_MESSAGE, CREATE_TIME, FINISH_TIME "
            "FROM information_schema.task_runs WHERE TASK_NAME = %s "
            "ORDER BY CREATE_TIME DESC LIMIT 20"
        )
        async with _dict_cursor(conn) as cur:
            await cur.execute(sql, (task_name,))
            rows = _as_dicts(await cur.fetchall())

        if not rows:
            return None
        newest_created = _as_datetime(rows[0].get("CREATE_TIME"))
        if (
            watermark is not None
            and newest_created is not None
            and newest_created <= watermark
        ):
            return None
        return _row_to_result(rows[0])


_RUN_COLUMNS = (
    "TASK_NAME",
    "QUERY_ID",
    "STATE",
    "ERROR_MESSAGE",
    "CREATE_TIME",
    "FINISH_TIME",
)


def _as_dicts(rows: Any) -> list[dict[str, Any]]:
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return list(rows)
    return [dict(zip(_RUN_COLUMNS, row, strict=False)) for row in rows]


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    return None


def _truthy(value: Any) -> bool:
    """Interpret a StarRocks boolean/numeric expression result."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "t", "yes"}


def _row_to_result(row: dict[str, Any]) -> ExecutionResult:
    state = str(row.get("STATE") or "").upper()
    error = row.get("ERROR_MESSAGE")
    return ExecutionResult(
        query_id=str(row.get("QUERY_ID")) if row.get("QUERY_ID") else None,
        state=state,
        error_message=_redact(str(error)) if error else None,
    )


async def poll_task_run(
    conn: NodeConnection, task_name: str, *, query_id: str | None = None
) -> str | None:
    """One-shot read of a task's latest native state, for reconciliation.

    Returns the uppercased ``STATE`` or ``None`` when the run row is gone.
    """
    sql = (
        "SELECT STATE FROM information_schema.task_runs WHERE TASK_NAME = %s "
        "ORDER BY CREATE_TIME DESC LIMIT 1"
    )
    async with _dict_cursor(conn) as cur:
        await cur.execute(sql, (task_name,))
        rows = await cur.fetchall()
    rows = _as_dicts(rows)
    if not rows:
        return None
    with contextlib.suppress(Exception):
        return str(rows[0].get("STATE") or "").upper() or None
    return None
