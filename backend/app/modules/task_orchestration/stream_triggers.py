"""Stream-triggered tasks: which graphs are due because their streams have data.

Level-triggered and durable: every scheduler tick looks at the truth — each
stream's verified source head and committed offset — not at notifications, so
a lost wakeup, a Redis flush or a worker restart cannot lose a trigger, and a
trigger that arrives while a run is active is simply seen again next tick.

A root task is due when it is ``STARTED``, its ``schedule_kind`` is
``stream`` and its compiled ``WHEN`` is true. The run id is derived from the
graph and the observed ``[offset, head]`` of every stream it reads, so
duplicate ticks create one run, and a run that ended without consuming does
not start again until a stream changes (no hot loop). A failed run with the
same observations is retried with exponential back-off. Runs of one graph are
at least ``STREAM_TRIGGER_MIN_INTERVAL_SECONDS`` apart.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.modules.streams.schemas import StreamError
from app.modules.task_orchestration import stream_when
from app.modules.task_orchestration.repository import scope_from_graph_id
from app.modules.task_orchestration.scheduler import (
    DueGraph,
    _graph_task_ids,
    _normalise_overlap,
    _tasks_by_name,
    build_graphs,
    naive_engine_time_to_utc,
)

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 3600


def _marks(run: dict[str, Any]) -> dict[str, Any]:
    raw = run.get("wal_marks")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _utc(value: Any, zone: str) -> datetime | None:
    return naive_engine_time_to_utc(value, zone) if isinstance(value, datetime) else None


async def plan_stream_triggers(
    tasks: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    now: datetime,
    repository: Any,
    engine_timezone: str = "UTC",
) -> list[DueGraph]:
    from app.modules.streams.service import stream_service

    due: list[DueGraph] = []
    graphs = build_graphs(tasks, edges)
    for graph_id, graph in graphs.items():
        scoped = _tasks_by_name(tasks, scope_from_graph_id(graph_id))
        children = {child for kids in graph.adjacency.values() for child in kids}
        for name in sorted(n for n in graph.nodes if n not in children):
            task = scoped.get(name)
            if (
                task is None
                or (task.get("schedule_kind") or "").lower() != "stream"
                or (task.get("lifecycle_state") or "STARTED") != "STARTED"
                or not task.get("when_ast")
            ):
                continue
            try:
                tree = stream_when.load(task["when_ast"])
                bound = stream_when.dependencies(task["when_ast"])
                observed: dict[str, list[Any]] = {}

                async def has_data(text: str, bound=bound, observed=observed) -> bool:
                    if text not in bound:
                        raise StreamError("STREAM_DEPENDENCY_INVALID", f"{text} is not bound")
                    pending, state = await stream_service.trigger_state(
                        bound[text], stream_when.stream_name(text)
                    )
                    # The stream's identity is part of what was observed: a
                    # stream recreated under the same name starts at the same
                    # offsets and must not look like one already handled.
                    observed[text] = [bound[text], *state]
                    return pending

                if tree is None or not await stream_when.evaluate(tree, has_data):
                    continue
            except (StreamError, stream_when.WhenError) as exc:
                logger.warning("stream task %s cannot be triggered: %s", graph_id, exc)
                continue
            digest = hashlib.sha256(
                json.dumps(sorted(observed.items()), separators=(",", ":")).encode()
            ).hexdigest()
            attempt = await _attempt(repository, graph_id, digest, now, engine_timezone)
            if attempt is None:
                continue
            task_ids, task_names = _graph_task_ids(graph, scoped)
            due.append(
                DueGraph(
                    graph_id=graph_id,
                    due_at=now,
                    task_ids=task_ids,
                    task_names=task_names,
                    overlap_policy=_normalise_overlap(task.get("overlap_policy")),
                    root_task=name,
                    owner_role=task.get("owner_role"),
                    trigger_type="stream",
                    run_key=f"stream:{graph_id}:{digest}:{attempt}",
                    wal_marks={"stream_trigger": digest, "streams": observed},
                )
            )
    return due


async def _attempt(
    repository: Any, graph_id: str, digest: str, now: datetime, zone: str
) -> int | None:
    """The attempt number for this observation, or ``None`` when not due yet."""
    recent = await repository.latest_stream_runs(graph_id)
    if recent:
        started = _utc(recent[0].get("started_at"), zone)
        if started is not None and (now - started).total_seconds() < (
            settings.STREAM_TRIGGER_MIN_INTERVAL_SECONDS
        ):
            return None
    same = [run for run in recent if _marks(run).get("stream_trigger") == digest]
    if not same:
        return 0
    newest = same[0]
    state = str(newest.get("state") or "")
    if state in {"pending", "running"}:
        return None
    if state != "failed":
        # Already run for exactly these observations: it acknowledged them
        # (or did not consume, which DESCRIBE TASK reports). New data re-arms.
        return None
    failures = sum(1 for run in same if run.get("state") == "failed")
    finished = _utc(newest.get("finished_at"), zone)
    backoff = min(
        settings.STREAM_TRIGGER_MIN_INTERVAL_SECONDS * 2 ** max(failures - 1, 0),
        MAX_BACKOFF_SECONDS,
    )
    if finished is not None and (now - finished).total_seconds() < backoff:
        return None
    return failures
