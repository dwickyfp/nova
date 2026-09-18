"""FINALIZE runtime semantics (NOVA-54 / 9b stage 3b).

The surface promises that ``FINALIZE x`` runs a task *after* ``x``'s dependency
graph completes — not as an ordinary child, and never concurrently with a
predecessor. PR 3a stored the edge with ``edge_kind='finalize'`` and kept it out
of the dependency adjacency; this file pins the execution semantics added here.

These run with in-memory fakes, no engine and no Redis.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.task_orchestration.dag import (
    GraphState,
    NodeState,
    evaluate,
    finalizers_ready,
    graph_from_task_rows,
)
from app.modules.task_orchestration.worker import GraphRunJob, GraphRunWorker
from tests.unit.test_task_orchestration_worker import FakeRepository, RecordingExecutor


@pytest.fixture
def audit(monkeypatch):
    """Silence audit writes; the fake repository is what the tests assert on."""
    captured: list[dict[str, Any]] = []

    async def fake_audit(**kwargs):
        captured.append(kwargs)
        return "audit-id"

    monkeypatch.setattr(
        "app.modules.task_orchestration.worker.write_audit_log", fake_audit
    )
    return captured


def _graph_with_finalizer():
    edges = [
        {"parent_task": "A", "child_task": "B", "edge_kind": "after"},
        {"parent_task": "A", "child_task": "F", "edge_kind": "finalize"},
    ]
    return graph_from_task_rows(["A", "B", "F"], edges)


class TestFinalizerIsNotADependency:
    def test_finalizer_is_excluded_from_the_dependency_graph(self) -> None:
        graph = _graph_with_finalizer()
        assert graph.nodes == ["A", "B"]
        assert graph.finalizer_nodes == {"F"}

    def test_finalizer_is_not_offered_as_a_zero_dependency_root(self) -> None:
        graph = _graph_with_finalizer()
        # The bug this guards: with the finalizer left in `nodes`, `evaluate`
        # sees no incoming edge and runs it immediately, alongside A.
        decision = evaluate(graph, {})
        assert decision.ready == ("A",)
        assert "F" not in decision.ready

    def test_finalizer_waits_until_the_whole_dependency_graph_succeeds(self) -> None:
        graph = _graph_with_finalizer()
        assert finalizers_ready(graph, {"A": NodeState.SUCCESS}) == ((), ())
        assert finalizers_ready(
            graph, {"A": NodeState.SUCCESS, "B": NodeState.SUCCESS}
        ) == (("F",), ())

    def test_finalizer_is_skipped_when_a_dependency_fails(self) -> None:
        graph = _graph_with_finalizer()
        ready, skipped = finalizers_ready(
            graph, {"A": NodeState.FAILED, "B": NodeState.SKIPPED}
        )
        assert ready == ()
        assert skipped == ("F",)

    def test_finalizer_is_skipped_when_a_dependency_is_skipped(self) -> None:
        graph = _graph_with_finalizer()
        ready, skipped = finalizers_ready(
            graph, {"A": NodeState.SUCCESS, "B": NodeState.SKIPPED}
        )
        assert ready == ()
        assert skipped == ("F",)


class TestFinalizerExecution:
    async def test_finalizer_runs_last_not_as_a_child(self, audit) -> None:
        repo = FakeRepository()
        for name in ("A", "B", "F"):
            repo.add_task(name)
        repo.add_edge("g1", "A", "B")
        repo.add_finalize("g1", "A", "F")
        repo.add_graph_run("gr1", "g1")

        executor = RecordingExecutor()
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "g1"))

        assert state == GraphState.SUCCESS
        order = [spec.name for spec, _ in executor.submissions]
        assert order[0] == "A"
        assert order[-1] == "F"
        assert order.index("B") < order.index("F")

    async def test_finalizer_does_not_run_concurrently_with_a_dependency(
        self, audit
    ) -> None:
        repo = FakeRepository()
        for name in ("A", "B", "F"):
            repo.add_task(name)
        repo.add_edge("g1", "A", "B")
        repo.add_finalize("g1", "A", "F")
        repo.add_graph_run("gr1", "g1")

        executor = RecordingExecutor(delay=0.05)
        await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "g1"))
        # A and B are serialised by the edge, so the peak in-flight is 1; the
        # finalizer must not overlap with either.
        assert executor.max_in_flight == 1

    async def test_a_failing_dependency_skips_the_finalizer(self, audit) -> None:
        repo = FakeRepository()
        for name in ("A", "B", "F"):
            repo.add_task(name)
        repo.add_edge("g1", "A", "B")
        repo.add_finalize("g1", "A", "F")
        repo.add_graph_run("gr1", "g1")

        executor = RecordingExecutor(fail_for={"B"})
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "g1"))

        assert state == GraphState.FAILED
        ran = {spec.name for spec, _ in executor.submissions}
        assert "F" not in ran, "a finalizer must not run over a failed graph"
        states = {r["task_id"]: r["state"] for r in repo.task_runs.values()}
        assert states["id_F"] == NodeState.SKIPPED.value

    async def test_finalizer_failure_fails_the_graph(self, audit) -> None:
        repo = FakeRepository()
        for name in ("A", "F"):
            repo.add_task(name)
        repo.add_finalize("g1", "A", "F")
        repo.add_graph_run("gr1", "g1")

        executor = RecordingExecutor(fail_for={"F"})
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "g1"))

        assert state == GraphState.FAILED
        states = {r["task_id"]: r["state"] for r in repo.task_runs.values()}
        assert states["id_A"] == NodeState.SUCCESS.value
        assert states["id_F"] == NodeState.FAILED.value

    async def test_finalizer_honours_its_own_when_expression(self, audit) -> None:
        repo = FakeRepository()
        for name in ("A", "F"):
            repo.add_task(name)
        repo.tasks["id_F"]["when_expr"] = "should_not_run = TRUE"
        repo.add_finalize("g1", "A", "F")
        repo.add_graph_run("gr1", "g1")

        executor = RecordingExecutor(when_false_for={"F"})
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "g1"))

        # A false WHEN skips only the finalizer; the graph is still successful.
        assert state == GraphState.SUCCESS
        ran = {spec.name for spec, _ in executor.submissions}
        assert ran == {"A"}
        states = {r["task_id"]: r["state"] for r in repo.task_runs.values()}
        assert states["id_F"] == NodeState.SKIPPED.value

    async def test_finalizer_only_graph_runs_after_its_target(self, audit) -> None:
        repo = FakeRepository()
        repo.add_task("A")
        repo.add_task("F")
        repo.add_finalize("g1", "A", "F")
        repo.add_graph_run("gr1", "g1")

        executor = RecordingExecutor()
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "g1"))

        assert state == GraphState.SUCCESS
        assert [spec.name for spec, _ in executor.submissions] == ["A", "F"]
