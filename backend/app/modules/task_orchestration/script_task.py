"""Run a ``sql_script`` task: its statements, in order, through the SQL frontend.

A script task (``CREATE TASK ... AS BEGIN stmt; stmt; END`` or ``AS $$ ... $$``)
cannot be one native ``SUBMIT TASK``: the engine's task body is a single
CTAS / INSERT / CACHE SELECT. Nova therefore runs the script itself, through
``QueryService`` on a connection that executes as the run's user under the
task's owner role, which the executor verified. Every statement gets the same
guard, Ranger policy, ``@stage`` and ``MERGE INTO`` support and audit as in a
worksheet, and the script stops at the first failure.

Statements that update, delete or drop were confirmed when the task was
created (``CREATE TASK`` asks for that confirmation), so each run passes the
confirmation on. Statements already run are not rolled back when a later one
fails: the pinned engine has no multi-statement transaction for DDL and DML.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import settings
from app.modules.task_orchestration.execution import (
    DelegateExecutor,
    NodeExecutionError,
    TaskSpec,
    _dict_cursor,
    _redact,
)

#: How often a long script stamps its node as alive.
HEARTBEAT_SECONDS = 10.0


def script_statements(task: dict[str, Any]) -> list[str]:
    """The stored statements of a script task, in run order."""
    raw = task.get("handler_config")
    config = json.loads(raw) if isinstance(raw, str) else (raw or {})
    statements = [str(item) for item in config.get("statements") or [] if str(item).strip()]
    if statements:
        return statements
    from app.common.sql_guard import split_sql_statements

    return split_sql_statements(str(task.get("definition") or ""))


async def run_sql_script(
    task: dict[str, Any],
    spec: TaskSpec,
    owner: str,
    executor: DelegateExecutor,
    *,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """Run the script as ``owner``; return a short summary or raise NodeExecutionError."""
    from app.modules.query.service import query_service

    statements = script_statements(task)
    if not statements:
        raise NodeExecutionError(f"task {spec.name!r} has no statements to run")
    if not spec.database:
        raise NodeExecutionError(f"task {spec.name!r} has no database")
    script = ";\n".join(statements)
    encrypted_password = ""
    if spec.execution_session_id:
        from app.core.redis import session_store

        session = await session_store.get(spec.execution_session_id) or {}
        encrypted_password = str(session.get("encrypted_password") or "")

    async with executor._owner_conn(owner, spec.execution_session_id) as conn:
        async with _dict_cursor(conn) as cur:
            await executor._prepare_task_session(cur, spec)
        run = asyncio.create_task(
            query_service.execute_statements(
                sql=script,
                username=owner,
                encrypted_password=encrypted_password,
                database=spec.database,
                role=spec.active_role,
                connection=conn,
                confirm_destructive=True,
                session_id=spec.execution_session_id,
                source="task",
            )
        )
        deadline = asyncio.get_running_loop().time() + settings.WORKER_TASK_POLL_TIMEOUT_SECONDS
        try:
            while not run.done():
                await asyncio.wait({run}, timeout=HEARTBEAT_SECONDS)
                if run.done():
                    break
                if heartbeat is not None:
                    await heartbeat()
                if asyncio.get_running_loop().time() > deadline:
                    run.cancel()
                    raise NodeExecutionError(
                        f"task {spec.name!r} did not finish within "
                        f"{settings.WORKER_TASK_POLL_TIMEOUT_SECONDS:.0f}s"
                    )
            results = run.result()
        except NodeExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001 - the node fails with a safe message
            raise NodeExecutionError(_redact(str(exc)) or type(exc).__name__) from exc
        finally:
            if not run.done():
                run.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await run

    failed = next((index for index, result in enumerate(results) if result.error), None)
    if failed is not None:
        message = _redact(str(results[failed].error))
        kept = ""
        if failed == 1:
            kept = " (statement 1 ran and is kept)"
        elif failed:
            kept = f" (statements 1-{failed} ran and are kept)"
        raise NodeExecutionError(
            f"Statement {failed + 1} of {len(statements)} failed: {message}{kept}"
        )
    if len(results) < len(statements):
        raise NodeExecutionError(f"Only {len(results)} of {len(statements)} statements ran")
    affected = sum(int(result.affected_rows or 0) for result in results)
    count = len(statements)
    return (
        f"{count} {'statement' if count == 1 else 'statements'} ran; "
        f"{affected} {'row' if affected == 1 else 'rows'} affected"
    )
