"""Reconciliation for worker restarts and lost stream deliveries.

Redis is ephemeral transport; ``NOVA_SYSTEM`` is the source of truth (design
§2). Two failure modes need a re-derivation path that does **not** depend on the
stream:

* **Redis flushed / job never delivered.** A graph run exists in
  ``pending`` but no worker ever saw it.
* **Worker died mid-node.** A node row is left ``running`` with a heartbeat
  that has stopped advancing. It is **abandoned**, not trusted: the graph is
  re-evaluated from the last confirmed node states, and the node is re-run.

The reconciler runs on a cadence in the worker process. It polls only graph runs
that are actually active — never every task — so its cost stays proportional to
in-flight work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.config import settings
from app.modules.task_orchestration.repository import TaskOrchestrationRepository

logger = logging.getLogger(__name__)


@dataclass
class ReconcileReport:
    """What one reconciliation pass found and re-enqueued."""

    pending_graph_runs: list[str] = field(default_factory=list)
    abandoned_task_runs: list[str] = field(default_factory=list)
    abandoned_graph_runs: list[str] = field(default_factory=list)


class Reconciler:
    """Re-derives unfinished work from durable state alone."""

    def __init__(
        self,
        repository: TaskOrchestrationRepository,
        *,
        heartbeat_timeout_seconds: int | None = None,
    ) -> None:
        self._repository = repository
        self._heartbeat_timeout = (
            heartbeat_timeout_seconds
            if heartbeat_timeout_seconds is not None
            else settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS
        )

    async def scan(self) -> ReconcileReport:
        """Find work the stream may have lost. Pure reads, no writes."""
        report = ReconcileReport()

        pending = await self._repository.list_graph_runs_by_state(["pending", "running"])
        for run in pending:
            report.pending_graph_runs.append(str(run["id"]))

        stale_nodes = await self._repository.list_stale_task_runs(self._heartbeat_timeout)
        for node in stale_nodes:
            graph_run_id = node.get("graph_run_id")
            if graph_run_id:
                report.abandoned_task_runs.append(str(node["id"]))
                if str(graph_run_id) not in report.abandoned_graph_runs:
                    report.abandoned_graph_runs.append(str(graph_run_id))

        stale_graphs = await self._repository.list_stale_graph_runs(self._heartbeat_timeout)
        for run in stale_graphs:
            if str(run["id"]) not in report.abandoned_graph_runs:
                report.abandoned_graph_runs.append(str(run["id"]))

        return report

    async def abandoned_node_ids(self, graph_run_id: str) -> list[str]:
        """Node ids in a graph run that are ``running`` with a stale heartbeat.

        The worker moves these to ``abandoned`` before re-evaluating, so a
        redelivery cannot mistake a dead worker's row for live progress.
        """
        stale = await self._repository.list_stale_task_runs(self._heartbeat_timeout)
        return [
            str(node["id"])
            for node in stale
            if str(node.get("graph_run_id")) == graph_run_id
        ]
