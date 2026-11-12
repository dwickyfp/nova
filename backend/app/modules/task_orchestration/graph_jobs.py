"""The durable ``task.graph`` job that delivers one graph run.

A graph run and its job commit in one metadata transaction; the job's outbox
event is the wakeup. The job is internal orchestration (``external_effect``
false): it drives node state, and each node's engine effect is fenced by the
node run's own lease generation and verified from the engine's native trace.
"""

from __future__ import annotations

import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from app.common.durable_jobs import JobDefinition

GRAPH_JOB_QUEUE = "task"
GRAPH_JOB_KIND = "task.graph"


def _digest(*parts: str) -> str:
    raw = json.dumps(list(parts), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def graph_job_reference(graph_run_id: str) -> UUID:
    """The stable job identity for one graph run: duplicate creators collide."""
    return uuid5(NAMESPACE_URL, "nova:task-graph-run:v1:" + graph_run_id)


def graph_job_definition(
    graph_run_id: str, graph_id: str, *, definition_version: int = 1,
) -> JobDefinition:
    """The queue envelope; the graph-run row is the definition it references.

    A scheduled occurrence carries the root task's definition version, so a
    changed definition reserves its own occurrence.
    """
    reference = graph_job_reference(graph_run_id)
    return JobDefinition(
        queue=GRAPH_JOB_QUEUE,
        kind=GRAPH_JOB_KIND,
        definition_ref=reference,
        definition_version=max(1, int(definition_version)),
        definition_digest=_digest(
            "task.graph", graph_id, graph_run_id, str(max(1, int(definition_version))),
        ),
        idempotency_key=reference,
        resource_key=_digest("task.graph.resource", graph_run_id),
        external_effect=False,
    )
