"""``SHOW TASKS`` and ``DESCRIBE TASK``: Nova tasks as the caller's role sees them.

Visibility is the task API's rule: the caller's active role owns the task
(admins see every task). Bodies are shown redacted, as in the Tasks page.
"""

from __future__ import annotations

import json
from typing import Any

from app.common.sql_guard import redact_sql_credentials
from app.modules.task_orchestration.access import is_task_admin, owns_task
from app.modules.task_orchestration.repository import (
    task_graph_id,
    task_orchestration_repository,
)

_SHOW_COLUMNS = [
    "name", "database_name", "schema_name", "state", "schedule_kind", "schedule",
    "when", "language", "owner_role", "last_run_state", "created_at",
]


def _visible(task: dict[str, Any], user: dict[str, Any]) -> bool:
    return is_task_admin(user) or owns_task(task, user)


def _language(task: dict[str, Any]) -> str:
    handler = task.get("handler") or "sql"
    return "python" if handler == "python" else "sql"


async def show_tasks(
    database: str | None, schema: str | None, user: dict[str, Any]
) -> tuple[list[str], list[list[Any]]]:
    tasks = await task_orchestration_repository.list_tasks()
    latest = await task_orchestration_repository.latest_graph_runs()
    rows = []
    for task in tasks:
        if (task.get("handler") or "sql") not in {"sql", "sql_script", "python"}:
            continue
        if database and (task.get("database_name") or "").lower() != database.lower():
            continue
        if schema and (task.get("schema_name") or "").lower() != schema.lower():
            continue
        if not _visible(task, user):
            continue
        run = latest.get(task_graph_id(task)) or {}
        rows.append([
            task.get("name"), task.get("database_name"), task.get("schema_name"),
            task.get("lifecycle_state") or "STARTED", task.get("schedule_kind"),
            task.get("schedule_expr"), task.get("when_expr"), _language(task),
            task.get("owner_role"), run.get("state"), task.get("created_at"),
        ])
    return _SHOW_COLUMNS, rows


async def describe_task(
    name: str, database: str | None, schema: str, user: dict[str, Any]
) -> tuple[list[str], list[list[Any]]] | None:
    task = await task_orchestration_repository.find_task(name, database, schema)
    if task is None or not _visible(task, user):
        return None
    config = task.get("handler_config")
    try:
        config = json.loads(config) if isinstance(config, str) else (config or {})
    except ValueError:
        config = {}
    graph_id = task_graph_id(task)
    latest = await task_orchestration_repository.get_latest_graph_run(graph_id) or {}
    facts: list[tuple[str, Any]] = [
        ("name", f"{task.get('database_name')}.{task.get('schema_name')}.{task.get('name')}"),
        ("state", task.get("lifecycle_state") or "STARTED"),
        ("schedule_kind", task.get("schedule_kind")),
        ("schedule", task.get("schedule_expr")),
        ("timezone", task.get("timezone")),
        ("when", task.get("when_expr")),
        ("language", _language(task)),
        ("owner_role", task.get("owner_role")),
        ("created_by", task.get("created_by")),
        ("created_at", task.get("created_at")),
        ("overlap_policy", task.get("overlap_policy")),
        ("graph", graph_id),
        ("last_run_state", latest.get("state")),
        ("last_run_started_at", latest.get("started_at")),
    ]
    if _language(task) == "python":
        facts += [("runtime_version", config.get("runtime_version")),
                  ("packages", ", ".join(config.get("packages") or []))]
    facts += await _stream_facts(task, graph_id)
    body = task.get("definition")
    facts.append(("definition", redact_sql_credentials(str(body)) if body else None))
    return ["property", "value"], [[key, value] for key, value in facts]


async def _stream_facts(task: dict[str, Any], graph_id: str) -> list[tuple[str, Any]]:
    """The streams a stream ``WHEN`` reads, their backlog, and a no-progress warning."""
    if not task.get("when_ast"):
        return []
    from app.modules.streams.schemas import StreamError
    from app.modules.streams.service import stream_service
    from app.modules.task_orchestration import stream_when

    facts: list[tuple[str, Any]] = []
    observed: dict[str, list[Any]] = {}
    for text, stream_id in sorted(stream_when.dependencies(task["when_ast"]).items()):
        try:
            pending, state = await stream_service.trigger_state(
                stream_id, stream_when.stream_name(text)
            )
            observed[text] = [stream_id, *state]
            facts.append((f"stream {text}", f"offset {state[0]}, head {state[1]}"
                          + (" (has data)" if pending else "")))
        except StreamError as exc:
            facts.append((f"stream {text}", f"{exc.code}: {exc}"))
    runs = await task_orchestration_repository.latest_stream_runs(graph_id, 1)
    if runs and runs[0].get("state") == "success" and observed:
        marks = runs[0].get("wal_marks")
        try:
            marks = json.loads(marks) if isinstance(marks, str) else (marks or {})
        except ValueError:
            marks = {}
        if marks.get("streams") == observed and any(h > o for _id, o, h in observed.values()):
            facts.append((
                "warning",
                "STREAM_NOT_CONSUMED: the last run succeeded but did not consume its "
                "streams, so it will not run again until new data arrives. Read the stream "
                "with INSERT … SELECT … FROM <stream> to consume it.",
            ))
    return facts


async def trigger_facts(task: dict[str, Any], graph_id: str) -> dict[str, Any]:
    """The streams a root task's ``WHEN`` reads and what the trigger last saw."""
    from app.modules.streams.schemas import StreamError
    from app.modules.streams.service import stream_service
    from app.modules.task_orchestration import stream_when

    streams: list[dict[str, Any]] = []
    observed: dict[str, list[Any]] = {}
    if task.get("when_ast"):
        for text, stream_id in sorted(stream_when.dependencies(task["when_ast"]).items()):
            item: dict[str, Any] = {"stream": text}
            try:
                pending, state = await stream_service.trigger_state(
                    stream_id, stream_when.stream_name(text)
                )
                observed[text] = [stream_id, *state]
                item.update(offset=state[0], head=state[1], has_data=pending)
            except StreamError as exc:
                item.update(error=exc.code, message=str(exc))
            streams.append(item)
    runs = await task_orchestration_repository.latest_stream_runs(graph_id, 5)
    history = []
    for run in runs:
        marks = run.get("wal_marks")
        try:
            marks = json.loads(marks) if isinstance(marks, str) else (marks or {})
        except ValueError:
            marks = {}
        history.append(
            {
                "run_id": run.get("id"),
                "state": run.get("state"),
                "started_at": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "observed": {
                    name: {"offset": values[1], "head": values[2]}
                    for name, values in (marks.get("streams") or {}).items()
                    if isinstance(values, list) and len(values) == 3
                },
            }
        )
    warning = None
    if (
        runs
        and runs[0].get("state") == "success"
        and observed
        and history[0]["observed"]
        == {name: {"offset": v[1], "head": v[2]} for name, v in observed.items()}
        and any(h > o for _id, o, h in observed.values())
    ):
        warning = (
            "STREAM_NOT_CONSUMED: the last run succeeded but did not consume its streams, "
            "so it will not run again until new data arrives"
        )
    return {
        "graph_id": graph_id,
        "root_task": task.get("name"),
        "state": task.get("lifecycle_state") or "STARTED",
        "schedule_kind": task.get("schedule_kind"),
        "when": task.get("when_expr"),
        "streams": streams,
        "recent_triggered_runs": history,
        "warning": warning,
    }
