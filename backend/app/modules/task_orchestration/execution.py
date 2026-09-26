"""Delegate-first node execution against StarRocks.

A graph node lowers to its own ``SUBMIT TASK``. Manual runs authenticate with
the exact triggering session; scheduled runs use restricted impersonation of
the service account bound to the owner role. The engine confirms the selected
role before executing SQL. The executor's ``owner`` argument is the effective
execution user, not necessarily the definition's creator.

The engine has **no completion hook** and cannot trigger another statement, so
after submitting, the worker can only learn the outcome by polling
``information_schema.task_runs`` (design §1). That polling is unavoidable Nova
code — see :func:`poll_task_run`.

The worker account secret is loaded from its environment. Test and legacy
callers may use an owner credential provider. No credential is logged or
persisted; only the engine's run identifier is recorded.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from app.common.identifiers import check_identifier
from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.core.security import decrypt_password
from app.modules.task_orchestration.credentials import (
    CredentialUnavailable,
    OwnerCredentialProvider,
)

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
    active_role: str | None = None
    schema: str | None = None
    native_name: str | None = None
    execution_session_id: str | None = None


def native_attempt_name(run_id: str) -> str:
    """Give each durable node attempt its own StarRocks one-shot task name."""
    return f"nova_{uuid5(NAMESPACE_URL, f'nova/task-run/{run_id}').hex}"


@dataclass(frozen=True)
class ExecutionResult:
    """The observable outcome of one delegated node execution."""

    query_id: str | None
    state: str
    error_message: str | None = None


@dataclass(frozen=True)
class RunWatermark:
    """Native run IDs observed at the newest pre-submit timestamp."""

    create_time: datetime | None
    query_ids: frozenset[str]


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
    identifier = _quote_identifier(spec.database, spec.native_name or spec.name)
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
        credentials: OwnerCredentialProvider | None,
        *,
        impersonation_user: str | None = None,
        impersonation_password: str | None = None,
        impersonation_role: str | None = None,
        poll_interval: float | None = None,
        poll_timeout: float | None = None,
    ) -> None:
        if bool(impersonation_user) != bool(impersonation_password):
            raise ValueError("worker impersonation user and password must both be configured")
        if credentials is None and not impersonation_user:
            raise ValueError("task executor requires an owner credential source")
        if impersonation_role and not impersonation_user:
            raise ValueError("worker impersonation role requires a worker account")
        if impersonation_user:
            check_identifier(impersonation_user, field="worker impersonation user")
            if impersonation_user.lower() in {"root", "nova_admin"}:
                raise ValueError("the task worker needs a dedicated unprivileged account")
        self._credentials = credentials
        self._impersonation_user = impersonation_user
        self._impersonation_password = impersonation_password
        self._impersonation_role = (
            check_identifier(impersonation_role, field="worker impersonation role")
            if impersonation_role
            else None
        )
        self._poll_interval = (
            poll_interval
            if poll_interval is not None
            else settings.WORKER_TASK_POLL_INTERVAL_SECONDS
        )
        self._poll_timeout = (
            poll_timeout if poll_timeout is not None else settings.WORKER_TASK_POLL_TIMEOUT_SECONDS
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
        async with (
            self._owner_conn(owner, spec.execution_session_id) as conn,
            _dict_cursor(conn) as cur,
        ):
            try:
                await self._prepare_task_session(cur, spec)
                await cur.execute(f"SELECT ({expression}) AS nova_when")
                row = await cur.fetchone()
            except Exception as exc:
                raise NodeExecutionError(
                    f"WHEN evaluation failed for {spec.name!r}: {_redact(str(exc))}"
                ) from exc
        if not isinstance(row, dict) or "nova_when" not in row:
            raise NodeExecutionError(f"WHEN expression for {spec.name!r} did not return a value")
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
        Manual runs use the triggering session; scheduled runs impersonate the
        role-bound service account on a fresh connection. Test and legacy callers may supply the
        owner's credential directly. The connection closes after submission.

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

        # The watermark is read before the submit so the new run can be
        # identified by being strictly newer. It is read on the system
        # connection for the reason in the docstring, and it is **best-effort**:
        # the engine's ``information_schema.task_runs`` is served by an internal
        # archive read that can fail independently of the task (observed as a
        # 1064 on `_statistics_.task_run_history`). Losing the watermark only
        # costs precision in identifying the new run — the poll falls back to
        # the newest run — so it must never stop the submit, which is where the
        # owner's RBAC is actually enforced.
        native_name = spec.native_name or spec.name
        watermark: RunWatermark | None = None
        try:
            async with db.system_conn() as observer:
                watermark = await self._latest_create_time(observer, native_name)
        except Exception as exc:
            logger.warning(
                "could not read the task-run watermark for %s; continuing: %s",
                spec.name,
                _redact(str(exc)),
            )

        async with self._owner_conn(owner, spec.execution_session_id) as conn:
            async with _dict_cursor(conn) as cur:
                await self._prepare_task_session(cur, spec)
            body = spec.body
            if "@" in body:
                from app.modules.query.dialect.parser import parse_sql

                parsed = parse_sql(body)
                if parsed.errors:
                    raise NodeExecutionError("task body contains invalid stage SQL")
                if parsed.stage_refs:
                    raise NodeExecutionError(
                        "scheduled @stage execution is unavailable because native task "
                        "definitions would persist storage credentials"
                    )
            statement = build_submit_task(replace(spec, body=body))
            await self._submit(conn, statement)

        # Never retain a system-pool connection across the full native run.
        # Long runs can exceed the pool size and their heartbeats need that pool.
        result = await self._await_completion(
            None, native_name, watermark=watermark, heartbeat=heartbeat
        )
        if spec.native_name:
            await self._drop_completed_native_task(spec, owner)

        if result.state in _FAILURE_STATES:
            raise NodeExecutionError(
                result.error_message or f"task {spec.name!r} failed",
                query_id=result.query_id,
            )
        return result

    async def _drop_completed_native_task(self, spec: TaskSpec, owner: str) -> None:
        """Release the one-shot Task template after its run has settled.

        The run history remains queryable after DROP TASK, as verified on the
        supported FE. A cleanup failure cannot change an observed run result;
        the FE's own task TTL remains the fallback.
        """
        assert spec.native_name is not None
        try:
            async with (
                self._owner_conn(owner, spec.execution_session_id) as conn,
                _dict_cursor(conn) as cur,
            ):
                await self._prepare_task_session(cur, spec)
                await cur.execute(
                    f"DROP TASK IF EXISTS {_quote_identifier(None, spec.native_name)}"
                )
        except Exception as exc:
            logger.warning(
                "could not drop completed native task %s: %s",
                spec.native_name,
                _redact(str(exc)),
            )

    @asynccontextmanager
    async def _owner_conn(
        self, owner: str, session_id: str | None = None
    ) -> AsyncIterator[NodeConnection]:
        """Open a fresh, verified owner session for one task operation."""
        if session_id:
            session = await session_store.get(session_id)
            if not session or session.get("username") != owner:
                raise CredentialUnavailable("The triggering session expired; sign in and run again")
            try:
                password = decrypt_password(session["encrypted_password"])
                connection = db.user_conn(owner, password)
                conn = await connection.__aenter__()
                del password
            except Exception:
                raise CredentialUnavailable(
                    "The triggering account could not authenticate"
                ) from None
            try:
                yield conn
            finally:
                await connection.__aexit__(None, None, None)
            return
        if self._impersonation_user:
            # Only simple StarRocks accounts are eligible. A stored task owner
            # must never be interpolated into EXECUTE AS without validation.
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", owner):
                raise CredentialUnavailable("invalid task execution account")
            target = owner
            password = self._impersonation_password
            assert password is not None
            connection = db.user_conn(self._impersonation_user, password)
            try:
                conn = await connection.__aenter__()
            except Exception:
                # Connector errors can contain connection parameters. Neither
                # the public failure nor its traceback may include the secret.
                raise CredentialUnavailable("task worker account connection failed") from None
            try:
                async with _dict_cursor(conn) as cur:
                    if self._impersonation_role:
                        try:
                            await cur.execute(f"SET ROLE {self._impersonation_role}")
                            await cur.execute("SELECT CURRENT_ROLE() AS nova_active_role")
                            active_role = await cur.fetchone()
                        except Exception as exc:
                            raise CredentialUnavailable(
                                "task worker impersonation role is unavailable "
                                f"({type(exc).__name__})"
                            ) from None
                        actual_role = str(
                            active_role.get("nova_active_role", "")
                            if isinstance(active_role, dict)
                            else ""
                        )
                        if actual_role.strip("[]`' ") != self._impersonation_role:
                            raise CredentialUnavailable(
                                "StarRocks did not confirm the task worker role"
                            )
                    try:
                        await cur.execute(f"EXECUTE AS '{target}'@'%' WITH NO REVERT")
                        await cur.execute("SELECT CURRENT_USER() AS nova_effective_user")
                        row = await cur.fetchone()
                    except Exception:
                        raise CredentialUnavailable(
                            f"Task worker is not authorized to execute as user {target!r}"
                        ) from None
                    actual = str(
                        row.get("nova_effective_user", "") if isinstance(row, dict) else ""
                    )
                    if actual.replace("'", "") != f"{target}@%":
                        raise CredentialUnavailable(
                            f"StarRocks did not confirm execution user {target!r}"
                        )
                yield conn
            finally:
                await connection.__aexit__(None, None, None)
            return

        assert self._credentials is not None
        password = await self._credentials.password_for(owner)
        async with db.user_conn(owner, password) as conn:
            del password
            yield conn

    async def _prepare_task_session(self, cur: Any, spec: TaskSpec) -> None:
        # USE itself checks database privileges, which may exist only on the stored role.
        await self._activate_task_role(cur, spec)
        if spec.database:
            name = check_identifier(spec.database, field="task database")
            await cur.execute(f"USE `{name}`")

    @staticmethod
    async def _activate_task_role(cur: Any, spec: TaskSpec) -> None:
        if not spec.active_role:
            if not settings.RANGER_ENABLED:
                return
            raise NodeExecutionError(
                f"task {spec.name!r} has no execution role; refusing service-account fallback"
            )
        role = check_identifier(spec.active_role, field="role")
        try:
            await cur.execute(f"SET ROLE {role}")
            await cur.execute("SELECT CURRENT_ROLE() AS nova_active_role")
            row = await cur.fetchone()
        except Exception as exc:
            raise NodeExecutionError(
                f"task execution role {spec.active_role!r} is no longer available"
            ) from exc
        actual = str(row.get("nova_active_role") if isinstance(row, dict) else row[0])
        active = {
            item.strip().strip("`'")
            for item in actual.replace("[", "").replace("]", "").split(",")
            if item.strip()
        }
        if active != {spec.active_role}:
            raise NodeExecutionError("StarRocks did not confirm the stored task role")

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
    ) -> RunWatermark | None:
        """Newest native timestamp and its run IDs before this submit.

        The engine's ``SUBMIT TASK`` result only echoes ``TaskName``/``Status``,
        so the submitted run is identified by being strictly newer than this
        watermark. Times are the engine's own naive ``DATETIME`` values.
        """
        sql = (
            "SELECT TASK_NAME, QUERY_ID, STATE, ERROR_MESSAGE, CREATE_TIME, FINISH_TIME "
            "FROM information_schema.task_runs WHERE TASK_NAME = %s "
            "ORDER BY CREATE_TIME DESC LIMIT 100"
        )
        async with _dict_cursor(conn) as cur:
            await cur.execute(sql, (task_name,))
            rows = _as_dicts(await cur.fetchall())
        if not rows:
            return None
        newest = _as_datetime(rows[0].get("CREATE_TIME"))
        ids = frozenset(
            str(row["QUERY_ID"])
            for row in rows
            if row.get("QUERY_ID") and _as_datetime(row.get("CREATE_TIME")) == newest
        )
        return RunWatermark(create_time=newest, query_ids=ids)

    async def _await_completion(
        self,
        conn: NodeConnection | None,
        task_name: str,
        *,
        watermark: RunWatermark | None,
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
                if conn is None:
                    async with db.system_conn() as observer:
                        result = await self._newest_run_after(observer, task_name, watermark)
                else:
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
        watermark: RunWatermark | None,
    ) -> ExecutionResult | None:
        """Find a run absent from the pre-submit snapshot, including tied times."""
        sql = (
            "SELECT TASK_NAME, QUERY_ID, STATE, ERROR_MESSAGE, CREATE_TIME, FINISH_TIME "
            "FROM information_schema.task_runs WHERE TASK_NAME = %s "
            "ORDER BY CREATE_TIME DESC LIMIT 100"
        )
        async with _dict_cursor(conn) as cur:
            await cur.execute(sql, (task_name,))
            rows = _as_dicts(await cur.fetchall())

        if not rows:
            return None
        for row in rows:
            created = _as_datetime(row.get("CREATE_TIME"))
            if watermark is not None:
                if watermark.create_time is not None and created is not None:
                    if created < watermark.create_time:
                        continue
                    if created == watermark.create_time and (
                        not row.get("QUERY_ID") or str(row["QUERY_ID"]) in watermark.query_ids
                    ):
                        continue
                elif row.get("QUERY_ID") and str(row["QUERY_ID"]) in watermark.query_ids:
                    continue
            return _row_to_result(row)
        return None


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
