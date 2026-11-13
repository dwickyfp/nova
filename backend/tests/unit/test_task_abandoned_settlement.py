"""An abandoned node is verified or settled, never left holding its run forever.

A node is abandoned when its worker's heartbeat lapses or the engine outcome of
its submission is unknown. The run used to stay ``running`` with no exit, so the
task could never be dropped. These tests pin the settlement rules:

* a native attempt with a verified ``SUCCESS``/``FAILED`` trace takes that
  outcome and the graph continues without re-submitting anything;
* otherwise the run is held until no effect of the attempt can still be in
  flight, then settled ``failed``; the node stays ``abandoned`` with an
  explanation and is never re-run;
* ``DROP TASK`` refuses only while a run is live.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.config import settings
from app.modules.task_orchestration.dag import GraphState
from app.modules.task_orchestration.native import NativeRun, NativeState
from app.modules.task_orchestration.reconciler import (
    STOPPED_REPORTING_MESSAGE,
    UNKNOWN_OUTCOME_MESSAGE,
    Reconciler,
    active_run_hold,
    effect_deadline,
)
from app.modules.task_orchestration.worker import GraphRunJob, GraphRunWorker
from tests.unit.test_task_orchestration_worker import FakeRepository as WorkerRepository

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=1)
JUST_NOW = NOW - timedelta(minutes=5)


class FakeRepository(WorkerRepository):
    """Worker fake plus the abandoned-node surface, with the SQL guards."""

    def __init__(self) -> None:
        super().__init__()
        self.failure_resets: list[str] = []

    def abandoned_node(
        self,
        task_id: str,
        graph_run_id: str = "gr1",
        *,
        heartbeat_at: datetime = LONG_AGO,
        delegated: bool = False,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": f"tr_{graph_run_id}_{task_id}",
            "graph_run_id": graph_run_id,
            "task_id": task_id,
            "attempt": 1,
            "state": "abandoned",
            "delegated": delegated,
            "starrocks_query_id": None,
            "error_message": error_message,
            "started_at": heartbeat_at - timedelta(minutes=1),
            "heartbeat_at": heartbeat_at,
            "lease_generation": 1,
        }
        self.task_runs[row["id"]] = row
        return row

    async def list_unsettled_abandoned_task_runs(self, *, limit: int = 200):
        return [
            row
            for row in self.task_runs.values()
            if row["state"] == "abandoned"
            and self.graph_runs.get(row["graph_run_id"], {}).get("state") in ("pending", "running")
        ][:limit]

    async def settle_abandoned_task_run(
        self, run_id, to_state, *, generation, query_id=None, error_message=None
    ):
        row = self.task_runs[run_id]
        if row["state"] != "abandoned" or row.get("lease_generation", 0) != generation:
            return False
        row["state"] = to_state
        if query_id is not None:
            row["starrocks_query_id"] = query_id
        if error_message is not None:
            row["error_message"] = error_message
        return True

    async def reset_consecutive_failures(self, task_id: str) -> None:
        self.failure_resets.append(task_id)

    async def get_engine_timezone(self):
        return None


class FakeObserver:
    def __init__(self, runs: dict[str, NativeRun] | None = None, *, fail: bool = False) -> None:
        self.runs = runs or {}
        self.fail = fail
        self.reads: list[list[str]] = []

    async def read_runs(self, task_names: list[str]) -> dict[str, NativeRun]:
        self.reads.append(task_names)
        if self.fail:
            raise ConnectionError("engine unavailable")
        return {
            name: self.runs.get(name, NativeRun(task_name=name, state=NativeState.MISSING))
            for name in task_names
        }


class NeverExecute:
    """An executor that fails the test if anything is (re-)submitted."""

    async def execute(self, *args, **kwargs):
        raise AssertionError("an abandoned node must never be re-executed")


@pytest.fixture
def audit(monkeypatch):
    captured: list[dict[str, Any]] = []

    async def fake_audit(**kwargs):
        captured.append(kwargs)
        return "audit-id"

    monkeypatch.setattr("app.modules.task_orchestration.reconciler.write_audit_log", fake_audit)
    monkeypatch.setattr("app.modules.task_orchestration.worker.write_audit_log", fake_audit)
    return captured


def _repo(handler: str = "python") -> tuple[FakeRepository, str]:
    repo = FakeRepository()
    task_id = repo.add_task("py_summary")
    repo.tasks[task_id]["handler"] = handler
    repo.add_graph_run("gr1", task_id, state="running")
    repo.graph_runs["gr1"]["lease_generation"] = 3
    return repo, task_id


def _reconciler(repo: FakeRepository, observer: FakeObserver | None = None) -> Reconciler:
    return Reconciler(repo, observer=observer or FakeObserver(), heartbeat_timeout_seconds=120)


class TestEffectDeadline:
    def test_python_waits_for_the_sandbox_and_token_lifetime(self):
        row = {"heartbeat_at": NOW, "started_at": NOW - timedelta(minutes=1)}
        deadline = effect_deadline(row, {"handler": "python"})
        expected = (
            settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS + settings.TASK_PYTHON_TIMEOUT_SECONDS + 600
        )
        assert deadline == NOW + timedelta(seconds=expected)

    @pytest.mark.parametrize("handler", ["sql", "sql_script", None, "intelligence.monitor"])
    def test_other_handlers_wait_for_the_poll_window(self, handler):
        deadline = effect_deadline({"heartbeat_at": NOW}, {"handler": handler})
        expected = (
            settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS + settings.WORKER_TASK_POLL_TIMEOUT_SECONDS
        )
        assert deadline == NOW + timedelta(seconds=expected)

    def test_a_naive_start_is_read_as_utc_and_no_time_holds(self):
        naive = datetime(2026, 11, 13, 9, 0)
        assert effect_deadline({"started_at": naive}, None) == NOW + timedelta(
            seconds=settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS
        )
        assert effect_deadline({}, {"handler": "python"}) is None


class TestSettleUnknownOutcome:
    async def test_expired_python_node_fails_the_run_without_rerunning(self, audit):
        """The observed shape: py_summary abandoned, no error, run held for hours."""
        repo, task_id = _repo()
        node = repo.abandoned_node(task_id)

        report = await _reconciler(repo).settle_abandoned_runs(now=NOW)

        assert report.settled == ["gr1"] and report.requeue == [] and report.held == []
        assert repo.graph_runs["gr1"]["state"] == "failed"
        assert node["state"] == "abandoned"
        assert node["error_message"] == UNKNOWN_OUTCOME_MESSAGE
        failed = [entry for entry in audit if entry["action"] == "GRAPH_RUN_FAILED"]
        assert len(failed) == 1 and "py_summary" in failed[0]["error_message"]
        # The worker now sees a settled run and does nothing, ever.
        worker = GraphRunWorker(repo, NeverExecute())  # type: ignore[arg-type]
        assert await worker.handle(GraphRunJob("gr1", task_id)) == GraphState.FAILED

    async def test_provisional_message_is_replaced_but_a_specific_one_is_kept(self, audit):
        repo, task_id = _repo()
        first = repo.abandoned_node(task_id, error_message=STOPPED_REPORTING_MESSAGE)
        other = repo.add_task("other")
        repo.tasks[other]["handler"] = "python"
        repo.add_edge(task_id, "py_summary", "other")
        kept = repo.abandoned_node(other, error_message="Task outcome is unknown; verify it")

        await _reconciler(repo).settle_abandoned_runs(now=NOW)

        assert first["error_message"] == UNKNOWN_OUTCOME_MESSAGE
        assert kept["error_message"] == "Task outcome is unknown; verify it"

    async def test_node_inside_its_window_keeps_the_run_held(self, audit):
        repo, task_id = _repo()
        node = repo.abandoned_node(task_id, heartbeat_at=JUST_NOW)

        report = await _reconciler(repo).settle_abandoned_runs(now=NOW)

        assert report.held == ["gr1"] and report.settled == []
        assert repo.graph_runs["gr1"]["state"] == "running"
        assert node["error_message"] is None
        assert audit == []

    async def test_a_running_sibling_keeps_the_run_held(self, audit):
        repo, task_id = _repo()
        repo.abandoned_node(task_id)
        sibling = repo.add_task("sibling")
        repo.task_runs["live"] = {
            "id": "live", "graph_run_id": "gr1", "task_id": sibling, "attempt": 1,
            "state": "running", "lease_generation": 1,
        }

        report = await _reconciler(repo).settle_abandoned_runs(now=NOW)

        assert report.settled == []
        assert repo.graph_runs["gr1"]["state"] == "running"

    async def test_settlement_is_idempotent(self, audit):
        repo, task_id = _repo()
        repo.abandoned_node(task_id)
        reconciler = _reconciler(repo)

        assert (await reconciler.settle_abandoned_runs(now=NOW)).settled == ["gr1"]
        count = len(audit)
        second = await reconciler.settle_abandoned_runs(now=NOW)

        assert second.settled == [] and len(audit) == count

    async def test_a_lost_graph_lease_settles_nothing(self, audit, monkeypatch):
        repo, task_id = _repo()
        repo.abandoned_node(task_id)

        async def lost(*args, **kwargs):
            return None

        monkeypatch.setattr(repo, "claim_graph_run", lost)
        report = await _reconciler(repo).settle_abandoned_runs(now=NOW)

        assert report.settled == []
        assert repo.graph_runs["gr1"]["state"] == "running"

    async def test_a_dropped_task_needs_only_the_heartbeat_window(self, audit):
        repo, task_id = _repo()
        repo.abandoned_node(task_id, heartbeat_at=NOW - timedelta(minutes=10))
        del repo.tasks[task_id]

        report = await _reconciler(repo).settle_abandoned_runs(now=NOW)

        assert report.settled == ["gr1"]
        assert "of a dropped task" in audit[-1]["error_message"]

    async def test_nothing_abandoned_reads_nothing(self, audit):
        repo, _ = _repo()
        observer = FakeObserver()
        report = await _reconciler(repo, observer).settle_abandoned_runs(now=NOW)
        assert report.settled == [] and observer.reads == []


class TestVerifyNativeAttempt:
    @pytest.mark.parametrize("native, node", [(NativeState.SUCCESS, "success"),
                                              (NativeState.FAILED, "failed")])
    async def test_verified_trace_settles_the_node_and_requeues(self, audit, native, node):
        repo, task_id = _repo("sql")
        row = repo.abandoned_node(task_id, heartbeat_at=JUST_NOW, delegated=True)
        observer = FakeObserver({"py_summary": NativeRun(
            task_name="py_summary", state=native, query_id="q-1", error_message=None,
        )})

        report = await _reconciler(repo, observer).settle_abandoned_runs(now=NOW)

        assert report.verified == [row["id"]] and report.requeue == ["gr1"]
        assert row["state"] == node and row["starrocks_query_id"] == "q-1"
        assert repo.failure_resets == ([task_id] if node == "success" else [])
        # Driving the run again continues from the verified state; nothing is re-submitted.
        worker = GraphRunWorker(repo, NeverExecute())  # type: ignore[arg-type]
        expected = GraphState.SUCCESS if node == "success" else GraphState.FAILED
        assert await worker.handle(GraphRunJob("gr1", task_id)) == expected

    async def test_a_native_run_still_in_flight_holds_past_the_window(self, audit):
        repo, task_id = _repo("sql")
        row = repo.abandoned_node(task_id, delegated=True)
        observer = FakeObserver({"py_summary": NativeRun(
            task_name="py_summary", state=NativeState.RUNNING,
        )})

        report = await _reconciler(repo, observer).settle_abandoned_runs(now=NOW)

        assert report.held == ["gr1"] and row["state"] == "abandoned"
        assert repo.graph_runs["gr1"]["state"] == "running"

    async def test_an_older_attempts_trace_is_not_this_outcome(self, audit):
        repo, task_id = _repo("sql")
        row = repo.abandoned_node(task_id, heartbeat_at=JUST_NOW, delegated=True)
        observer = FakeObserver({"py_summary": NativeRun(
            task_name="py_summary", state=NativeState.SUCCESS,
            create_time=(row["started_at"] - timedelta(hours=1)).replace(tzinfo=None),
        )})

        report = await _reconciler(repo, observer).settle_abandoned_runs(now=NOW)

        assert report.verified == [] and row["state"] == "abandoned"
        assert report.held == ["gr1"]

    @pytest.mark.parametrize("fail", [False, True])
    async def test_missing_or_unreadable_trace_settles_after_the_window(self, audit, fail):
        repo, task_id = _repo("sql")
        row = repo.abandoned_node(task_id, delegated=True)

        report = await _reconciler(repo, FakeObserver(fail=fail)).settle_abandoned_runs(now=NOW)

        assert report.settled == ["gr1"]
        assert row["state"] == "abandoned" and row["error_message"] == UNKNOWN_OUTCOME_MESSAGE


class TestStaleHeartbeatMessage:
    async def test_abandoning_a_stale_node_records_why(self, audit):
        repo, task_id = _repo()
        repo.task_runs["n1"] = {
            "id": "n1", "graph_run_id": "gr1", "task_id": task_id, "attempt": 1,
            "state": "running", "lease_generation": 2, "running_stale": True,
            "error_message": None,
        }

        await _reconciler(repo).abandon_stale_nodes()

        assert repo.task_runs["n1"]["state"] == "abandoned"
        assert repo.task_runs["n1"]["error_message"] == STOPPED_REPORTING_MESSAGE


class TestActiveRunHold:
    async def test_pending_and_running_steps_are_live(self):
        repo, task_id = _repo()
        repo.add_graph_run("queued", task_id)
        assert (await active_run_hold(repo, repo.graph_runs["queued"], now=NOW)).live
        repo.task_runs["live"] = {
            "id": "live", "graph_run_id": "gr1", "task_id": task_id, "attempt": 1,
            "state": "running",
        }
        assert (await active_run_hold(repo, repo.graph_runs["gr1"], now=NOW)).live

    async def test_a_run_without_abandoned_steps_is_live(self):
        repo, _ = _repo()
        hold = await active_run_hold(repo, repo.graph_runs["gr1"], now=NOW)
        assert hold.live and hold.settles_at is None

    async def test_abandoned_inside_the_window_is_live_with_a_settle_time(self):
        repo, task_id = _repo()
        repo.abandoned_node(task_id, heartbeat_at=JUST_NOW)
        hold = await active_run_hold(repo, repo.graph_runs["gr1"], now=NOW)
        assert hold.live
        assert hold.settles_at == effect_deadline(
            {"heartbeat_at": JUST_NOW}, repo.tasks[task_id]
        )

    async def test_abandoned_past_the_window_is_not_live(self):
        repo, task_id = _repo()
        repo.abandoned_node(task_id)
        hold = await active_run_hold(repo, repo.graph_runs["gr1"], now=NOW)
        assert not hold.live


class TestDropTaskWaitsOnlyForLiveRuns:
    @pytest.fixture
    def repo(self, monkeypatch):
        from app.modules.task_orchestration import lowering

        repo, task_id = _repo()
        task = repo.tasks[task_id]
        task.update({"database_name": "QA", "schema_name": "default", "owner_role": "ACCOUNTADMIN"})
        repo.deleted: list[str] = []  # type: ignore[misc]

        async def find_task(name, database_name, schema_name):
            return task if name == "py_summary" else None

        async def list_all_edges():
            return []

        async def list_active_graph_runs(graph_id):
            assert graph_id == "QA.default.py_summary"
            return [r for r in repo.graph_runs.values() if r["state"] in ("pending", "running")]

        async def delete_task(task_id):
            repo.deleted.append(task_id)

        repo.find_task = find_task  # type: ignore[method-assign]
        repo.list_all_edges = list_all_edges  # type: ignore[attr-defined]
        repo.list_active_graph_runs = list_active_graph_runs  # type: ignore[method-assign]
        repo.delete_task = delete_task  # type: ignore[attr-defined]
        monkeypatch.setattr(lowering, "task_orchestration_repository", repo)
        return repo

    OWNER = {"username": "nova_admin", "active_role": "ACCOUNTADMIN"}

    async def test_a_run_held_by_an_expired_abandoned_step_does_not_block(self, repo):
        from app.modules.task_orchestration.lowering import drop_nova_task

        task_id = next(iter(repo.tasks))
        repo.abandoned_node(task_id)
        dropped = await drop_nova_task("py_summary", "QA", "default", self.OWNER)
        assert dropped is not None and repo.deleted == [task_id]

    async def test_an_unconfirmed_step_refuses_with_the_settle_time(self, repo):
        from app.modules.task_orchestration.lowering import TaskLoweringError, drop_nova_task

        task_id = next(iter(repo.tasks))
        repo.abandoned_node(task_id, heartbeat_at=datetime.now(UTC))
        refusal = r"not confirmed yet; Nova ends that run by .* UTC"
        with pytest.raises(TaskLoweringError, match=refusal):
            await drop_nova_task("py_summary", "QA", "default", self.OWNER)
        assert repo.deleted == []

    async def test_a_genuinely_running_run_refuses(self, repo):
        from app.modules.task_orchestration.lowering import TaskLoweringError, drop_nova_task

        task_id = next(iter(repo.tasks))
        repo.task_runs["live"] = {
            "id": "live", "graph_run_id": "gr1", "task_id": task_id, "attempt": 1,
            "state": "running",
        }
        with pytest.raises(TaskLoweringError, match="has a run in progress"):
            await drop_nova_task("py_summary", "QA", "default", self.OWNER)
        assert repo.deleted == []


class TestWorkerServiceSettles:
    async def test_reconcile_requeues_verified_runs_and_reports_settlement(self):
        from app.modules.task_orchestration.reconciler import AbandonedSettleReport
        from app.modules.task_orchestration.worker_service import WorkerService

        driven: list[str] = []

        class Reconcile:
            async def reconcile_native(self):
                from app.modules.task_orchestration.reconciler import NativeReconcileReport

                return NativeReconcileReport()

            async def abandon_stale_nodes(self):
                return []

            async def settle_abandoned_runs(self):
                return AbandonedSettleReport(verified=["n1"], requeue=["gr1"], settled=["gr2"])

            async def scan(self):
                from app.modules.task_orchestration.reconciler import ReconcileReport

                return ReconcileReport(abandoned_graph_runs=["gr1"])

        class Repo:
            async def get_graph_run(self, run_id):
                return {"id": run_id, "graph_id": "g", "state": "running"}

        service = WorkerService(Repo(), executor=None, consumer=None, reconciler=Reconcile())  # type: ignore[arg-type]

        async def requeue(run):
            driven.append(run["id"])

        service._requeue = requeue  # type: ignore[method-assign]

        async def no_learning(**kwargs):
            return None

        import app.modules.agents.learning_work as learning

        original = learning.enqueue_pending_learning
        learning.enqueue_pending_learning = no_learning  # type: ignore[assignment]
        try:
            assert await service.reconcile_once() is True
        finally:
            learning.enqueue_pending_learning = original  # type: ignore[assignment]
        assert driven == ["gr1"]

    async def test_a_settlement_failure_is_partial_not_fatal(self):
        from app.modules.task_orchestration.worker_service import WorkerService

        class Broken:
            async def settle_abandoned_runs(self):
                raise RuntimeError("metadata unavailable")

        service = WorkerService(None, executor=None, consumer=None, reconciler=Broken())  # type: ignore[arg-type]
        assert await service._settle_abandoned_runs() == (False, [])
