"""Unit tests for native-state reconciliation (NOVA-37).

These prove the acceptance criteria an engine cannot easily show, with fakes:

* a **lost trace** — a running node whose native row vanished — is marked
  ``abandoned``, an explicit state, never success;
* a settled native run advances the node (``SUCCESS``/``FAILED``);
* **auto-pause** (the engine's ``max_task_consecutive_fail_count``) is surfaced
  to the audit log instead of letting a DAG hang silently;
* two reconcile passes on unchanged engine state change nothing (idempotency);
* config is read through the FE-config surface, and an unavailable engine is
  tolerated (``UNKNOWN``, no write).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.task_orchestration.native import (
    NativeConfig,
    NativeRun,
    NativeState,
    parse_consecutive_failures,
    read_frontend_config_rows,
    schedule_is_paused,
)
from app.modules.task_orchestration.reconciler import Reconciler


class FakeRepository:
    """The reconciler-facing repository surface, in memory.

    Conditional transitions mirror the SQL ``WHERE state IN (...)`` guard so a
    repeated pass is observable as a no-op.
    """

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.task_runs: dict[str, dict[str, Any]] = {}
        self.graph_runs: dict[str, dict[str, Any]] = {}

    def add_task(self, name: str, *, owner: str = "alice") -> str:
        task_id = f"id_{name}"
        self.tasks[task_id] = {
            "id": task_id,
            "name": name,
            "created_by": owner,
        }
        return task_id

    def add_node_run(
        self,
        run_id: str,
        task_id: str,
        state: str = "running",
        *,
        graph_run_id: str = "gr1",
    ) -> dict[str, Any]:
        row = {
            "id": run_id,
            "graph_run_id": graph_run_id,
            "task_id": task_id,
            "attempt": 1,
            "state": state,
            "starrocks_query_id": None,
            "error_message": None,
        }
        self.task_runs[run_id] = row
        return row

    async def list_tasks(self, graph_id: str | None = None):
        return list(self.tasks.values())

    async def list_running_task_runs(self, *, limit: int = 500):
        return [r for r in self.task_runs.values() if r["state"] == "running"][:limit]

    async def transition_task_run(
        self, run_id, from_states, to_state, *, query_id=None, error_message=None
    ):
        row = self.task_runs[run_id]
        if row["state"] not in from_states:
            return False
        row["state"] = to_state
        if query_id is not None:
            row["starrocks_query_id"] = query_id
        if error_message is not None:
            row["error_message"] = error_message
        return True

    async def list_graph_runs_by_state(self, states, *, limit=200):
        return []

    async def list_stale_task_runs(self, older_than_seconds, *, limit=200):
        return []

    async def list_stale_graph_runs(self, older_than_seconds, *, limit=200):
        return []


class FakeObserver:
    def __init__(
        self,
        runs: dict[str, NativeRun] | None = None,
        config: NativeConfig | None = None,
    ) -> None:
        self.runs = runs or {}
        self.config = config or NativeConfig(
            task_runs_ttl_second=604800,
            max_task_consecutive_fail_count=10,
            available=True,
        )
        self.config_reads = 0

    async def read_native_config(self) -> NativeConfig:
        self.config_reads += 1
        return self.config

    async def read_runs(self, task_names: list[str]) -> dict[str, NativeRun]:
        return {
            name: self.runs.get(name, NativeRun(task_name=name, state=NativeState.MISSING))
            for name in task_names
        }


@pytest.fixture
def audit(monkeypatch):
    captured: list[dict[str, Any]] = []

    async def fake_audit(**kwargs):
        captured.append(kwargs)
        return "audit-id"

    monkeypatch.setattr(
        "app.modules.task_orchestration.reconciler.write_audit_log", fake_audit
    )
    return captured


def _reconciler(repo: FakeRepository, observer: FakeObserver) -> Reconciler:
    return Reconciler(repo, observer=observer, heartbeat_timeout_seconds=120)


class TestLostTrace:
    async def test_missing_native_run_is_abandoned_not_success(self, audit):
        """Criterion 2: a run that vanished is an explicit non-success state."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {"A": NativeRun(task_name="A", state=NativeState.MISSING)}
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.lost_traces == ["A"]
        assert repo.task_runs["n1"]["state"] == "abandoned"
        actions = [entry["action"] for entry in audit]
        assert "NODE_ABANDONED" in actions
        assert repo.task_runs["n1"]["state"] != "success"

    async def test_lost_trace_is_idempotent(self, audit):
        """Criterion 5: a second pass on unchanged state writes nothing."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {"A": NativeRun(task_name="A", state=NativeState.MISSING)}
        )
        reconciler = _reconciler(repo, observer)

        await reconciler.reconcile_native()
        audit_count = len(audit)
        second = await reconciler.reconcile_native()

        assert second.advanced == []
        assert second.lost_traces == []
        assert len(audit) == audit_count

    async def test_unknown_read_does_not_write(self, audit):
        """A failed native read must not be treated as lost work."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {"A": NativeRun(task_name="A", state=NativeState.UNKNOWN)}
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.unknown == ["A"]
        assert repo.task_runs["n1"]["state"] == "running"
        assert audit == []


class TestAdvanceFromNative:
    async def test_native_success_advances_the_node(self, audit):
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {
                "A": NativeRun(
                    task_name="A", state=NativeState.SUCCESS, query_id="q1"
                )
            }
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.advanced == ["n1"]
        row = repo.task_runs["n1"]
        assert row["state"] == "success"
        assert row["starrocks_query_id"] == "q1"

    async def test_native_failure_advances_the_node(self, audit):
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {
                "A": NativeRun(
                    task_name="A",
                    state=NativeState.FAILED,
                    error_message="engine exploded",
                )
            }
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.advanced == ["n1"]
        assert repo.task_runs["n1"]["state"] == "failed"
        assert repo.task_runs["n1"]["error_message"] == "engine exploded"

    async def test_still_running_native_leaves_the_node_running(self, audit):
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {"A": NativeRun(task_name="A", state=NativeState.RUNNING)}
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.advanced == []
        assert repo.task_runs["n1"]["state"] == "running"
        assert audit == []

    async def test_settled_node_is_not_reconciled(self, audit):
        """Only running rows are polled — never every task."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id, state="success")
        observer = FakeObserver()

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.observed == 0
        assert observer.config_reads == 0


class TestAutoPause:
    async def test_failure_surfaces_auto_pause(self, audit):
        """Criterion 3: a failure is surfaced, never a silent hang."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {"A": NativeRun(task_name="A", state=NativeState.FAILED)}
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.auto_paused == ["A"]
        entry = next(e for e in audit if e["action"] == "TASK_AUTO_PAUSE_SUSPECTED")
        assert "10" in (entry["error_message"] or "")
        assert entry["object_name"] == "A"

    async def test_auto_pause_ceiling_comes_from_the_engine(self, audit):
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {"A": NativeRun(task_name="A", state=NativeState.FAILED)},
            config=NativeConfig(max_task_consecutive_fail_count=3, available=True),
        )

        await _reconciler(repo, observer).reconcile_native()

        entry = next(e for e in audit if e["action"] == "TASK_AUTO_PAUSE_SUSPECTED")
        assert "3 consecutive" in (entry["error_message"] or "")


class TestFrontendConfigParsing:
    def test_parses_both_keys(self):
        rows = [
            {"Key": "task_runs_ttl_second", "Value": "604800"},
            {"Key": "max_task_consecutive_fail_count", "Value": "10"},
            {"Key": "task_check_interval_second", "Value": "60"},
        ]
        config = read_frontend_config_rows(rows)
        assert config.task_runs_ttl_second == 604800
        assert config.max_task_consecutive_fail_count == 10
        assert config.available is True

    def test_missing_keys_are_tolerated(self):
        config = read_frontend_config_rows([])
        assert config.available is False
        assert config.task_runs_ttl_second is None

    def test_schedule_pause_markers(self):
        assert schedule_is_paused("PAUSED") is True
        assert schedule_is_paused("SUSPENDED") is True
        assert schedule_is_paused("EVERY(INTERVAL 5 MINUTE)") is False
        assert schedule_is_paused(None) is False

    def test_parse_consecutive_failures(self):
        assert parse_consecutive_failures("failed 10 consecutive times") == 10
        assert parse_consecutive_failures("boom") is None
        assert parse_consecutive_failures(None) is None


class TestEngineObserverToleratesUnavailableEngine:
    async def test_read_runs_returns_unknown_when_conn_acquire_fails(self, monkeypatch):
        """The engine going away is tolerated, not raised, at the observer.

        The failure mode is connection *acquisition* (``system_conn``), which
        sits above ``read_latest_native_runs``'s own try. A direct caller of
        ``Reconciler.reconcile_native`` must still get ``UNKNOWN`` and no write,
        matching criterion 7.
        """
        import app.modules.task_orchestration.reconciler as reconciler_module
        from app.modules.task_orchestration.reconciler import EngineNativeObserver

        class _Boom:
            async def __aenter__(self):
                raise RuntimeError("FE unavailable")

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(reconciler_module.db, "system_conn", lambda: _Boom())
        runs = await EngineNativeObserver().read_runs(["A", "B"])

        assert {name: run.state for name, run in runs.items()} == {
            "A": NativeState.UNKNOWN,
            "B": NativeState.UNKNOWN,
        }

    async def test_reconcile_native_returns_unknown_when_engine_is_gone(self, monkeypatch):
        """End to end: a dead engine yields an UNKNOWN report, never a raise."""
        import app.modules.task_orchestration.reconciler as reconciler_module

        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)

        class _Boom:
            async def __aenter__(self):
                raise RuntimeError("FE unavailable")

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(reconciler_module.db, "system_conn", lambda: _Boom())

        report = await Reconciler(repo, heartbeat_timeout_seconds=120).reconcile_native()

        assert report.unknown == ["A"]
        assert report.advanced == []
        assert repo.task_runs["n1"]["state"] == "running"


class TestNoRunningNodes:
    async def test_no_running_nodes_reads_no_engine(self):
        """No in-flight work means no engine round trip, no config read."""
        repo = FakeRepository()
        observer = FakeObserver()
        report = await _reconciler(repo, observer).reconcile_native()
        assert report.observed == 0
        assert observer.config_reads == 0
