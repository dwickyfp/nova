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
            "consecutive_fail_count": 0,
        }
        return task_id

    def add_node_run(
        self,
        run_id: str,
        task_id: str,
        state: str = "running",
        *,
        graph_run_id: str = "gr1",
        started_at: Any = None,
    ) -> dict[str, Any]:
        row = {
            "id": run_id,
            "graph_run_id": graph_run_id,
            "task_id": task_id,
            "attempt": 1,
            "state": state,
            "starrocks_query_id": None,
            "error_message": None,
            "started_at": started_at,
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

    async def increment_consecutive_failures(self, task_id: str) -> int:
        task = self.tasks[task_id]
        task["consecutive_fail_count"] = task.get("consecutive_fail_count", 0) + 1
        return task["consecutive_fail_count"]

    async def reset_consecutive_failures(self, task_id: str) -> None:
        self.tasks[task_id]["consecutive_fail_count"] = 0

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
        schedules: dict[str, str] | None = None,
    ) -> None:
        self.runs = runs or {}
        self.config = config or NativeConfig(
            task_runs_ttl_second=604800,
            max_task_consecutive_fail_count=10,
            available=True,
        )
        self.schedules = schedules or {}
        self.config_reads = 0

    async def read_native_config(self) -> NativeConfig:
        self.config_reads += 1
        return self.config

    async def read_runs(self, task_names: list[str]) -> dict[str, NativeRun]:
        return {
            name: self.runs.get(name, NativeRun(task_name=name, state=NativeState.MISSING))
            for name in task_names
        }

    async def read_schedules(self, task_names: list[str]) -> dict[str, str]:
        return {name: self.schedules.get(name, "") for name in task_names}


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
    """Criterion 3, gated on the real threshold (NOVA-42).

    The defect this replaces alarmed on the *first* failure. These tests pin the
    threshold: failures 1..9 are quiet, the tenth consecutive failure raises the
    alarm, a success resets the run, and a native pause marker raises it
    immediately. They also prove the helper signals are wired, not dead.
    """

    async def test_first_failure_does_not_surface_auto_pause(self, audit):
        """A single failure is just a failure — no false auto-pause alarm."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {"A": NativeRun(task_name="A", state=NativeState.FAILED)}
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.auto_paused == []
        actions = [e["action"] for e in audit]
        assert "NODE_FAILED" in actions
        assert "TASK_AUTO_PAUSE_SUSPECTED" not in actions
        assert repo.tasks[task_id]["consecutive_fail_count"] == 1

    async def test_tenth_consecutive_failure_surfaces_auto_pause(self, audit):
        """Failures 1..9 are quiet; the tenth raises the alarm."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        reconciler = _reconciler(
            repo, FakeObserver({"A": NativeRun(task_name="A", state=NativeState.FAILED)})
        )

        for attempt in range(1, 10):
            repo.add_node_run(f"n{attempt}", task_id)
            report = await reconciler.reconcile_native()
            assert report.auto_paused == [], f"alarmed early at attempt {attempt}"

        repo.add_node_run("n10", task_id)
        report = await reconciler.reconcile_native()

        assert report.auto_paused == ["A"]
        entry = next(e for e in audit if e["action"] == "TASK_AUTO_PAUSE_SUSPECTED")
        assert "10 consecutive failures" in (entry["error_message"] or "")

    async def test_success_resets_the_consecutive_run(self, audit):
        """A success breaks the run, so a later failure starts from one again."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        observer = FakeObserver(
            {"A": NativeRun(task_name="A", state=NativeState.FAILED)}
        )
        reconciler = _reconciler(repo, observer)
        for attempt in range(5):
            repo.add_node_run(f"n{attempt}", task_id)
            await reconciler.reconcile_native()
        assert repo.tasks[task_id]["consecutive_fail_count"] == 5

        repo.add_node_run("ok", task_id)
        observer.runs["A"] = NativeRun(task_name="A", state=NativeState.SUCCESS)
        await reconciler.reconcile_native()

        assert repo.tasks[task_id]["consecutive_fail_count"] == 0
        audit.clear()

        # The next four failures must not alarm: 9 is still below the ceiling.
        observer.runs["A"] = NativeRun(task_name="A", state=NativeState.FAILED)
        for attempt in range(4):
            repo.add_node_run(f"again{attempt}", task_id)
            report = await reconciler.reconcile_native()
            assert report.auto_paused == []

    async def test_engine_reported_count_is_honoured(self, audit):
        """When the engine embeds the count, it is preferred over Nova's own."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {
                "A": NativeRun(
                    task_name="A",
                    state=NativeState.FAILED,
                    error_message="task has failed 10 consecutive times",
                )
            }
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.auto_paused == ["A"]
        # The engine's count is authoritative, so Nova's counter is not bumped.
        assert repo.tasks[task_id]["consecutive_fail_count"] == 0

    async def test_schedule_pause_marker_surfaces_immediately(self, audit):
        """Criterion 3: a native pause marker is surfaced on the first failure."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {"A": NativeRun(task_name="A", state=NativeState.FAILED)},
            schedules={"A": "PAUSED"},
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.auto_paused == ["A"]
        entry = next(e for e in audit if e["action"] == "TASK_AUTO_PAUSE_SUSPECTED")
        assert "SCHEDULE" in (entry["error_message"] or "")

    async def test_auto_pause_ceiling_comes_from_the_engine(self, audit):
        """The threshold read from the engine, not a hardcoded 10."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        reconciler = _reconciler(
            repo,
            FakeObserver(
                {"A": NativeRun(task_name="A", state=NativeState.FAILED)},
                config=NativeConfig(max_task_consecutive_fail_count=3, available=True),
            ),
        )

        for attempt in range(1, 3):
            repo.add_node_run(f"n{attempt}", task_id)
            report = await reconciler.reconcile_native()
            assert report.auto_paused == [], f"alarmed early at attempt {attempt}"

        repo.add_node_run("n3", task_id)
        report = await reconciler.reconcile_native()

        assert report.auto_paused == ["A"]
        entry = next(e for e in audit if e["action"] == "TASK_AUTO_PAUSE_SUSPECTED")
        assert "3 consecutive" in (entry["error_message"] or "")

    async def test_auto_pause_alarm_is_idempotent_across_passes(self, audit):
        """Once the node is no longer running, another pass adds no alarm."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        reconciler = _reconciler(
            repo,
            FakeObserver(
                {"A": NativeRun(task_name="A", state=NativeState.FAILED)},
                config=NativeConfig(max_task_consecutive_fail_count=1, available=True),
            ),
        )
        repo.add_node_run("n1", task_id)
        await reconciler.reconcile_native()
        alarms = [e for e in audit if e["action"] == "TASK_AUTO_PAUSE_SUSPECTED"]
        assert len(alarms) == 1

        second = await reconciler.reconcile_native()

        assert second.auto_paused == []
        alarms = [e for e in audit if e["action"] == "TASK_AUTO_PAUSE_SUSPECTED"]
        assert len(alarms) == 1


class TestStaleNativeRowGuard:
    """NOVA-42 second finding: a stale native row must not fail a live node.

    A prior attempt's FAILED row is the newest row only until the in-flight
    attempt's own run appears. Settling on it would fail a node another worker
    is still waiting on (and could raise a false auto-pause).
    """

    async def test_row_older_than_the_node_is_ignored(self, audit):
        from datetime import datetime

        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run(
            "n1", task_id, started_at=datetime(2026, 9, 18, 12, 0, 30)
        )
        observer = FakeObserver(
            {
                "A": NativeRun(
                    task_name="A",
                    state=NativeState.FAILED,
                    create_time=datetime(2026, 9, 18, 12, 0, 0),
                )
            }
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.advanced == []
        assert report.auto_paused == []
        assert repo.task_runs["n1"]["state"] == "running"
        assert audit == []

    async def test_row_at_or_after_the_node_is_settled(self, audit):
        from datetime import datetime

        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run(
            "n1", task_id, started_at=datetime(2026, 9, 18, 12, 0, 0)
        )
        observer = FakeObserver(
            {
                "A": NativeRun(
                    task_name="A",
                    state=NativeState.FAILED,
                    create_time=datetime(2026, 9, 18, 12, 0, 0),
                )
            }
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.advanced == ["n1"]
        assert repo.task_runs["n1"]["state"] == "failed"

    async def test_guard_skips_when_timestamps_are_unknown(self, audit):
        """No timestamps means no comparison — the row still settles."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {"A": NativeRun(task_name="A", state=NativeState.FAILED)}
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.advanced == ["n1"]


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


class TestNoRunningNodes:
    async def test_no_running_nodes_reads_no_engine(self):
        """No in-flight work means no engine round trip, no config read."""
        repo = FakeRepository()
        observer = FakeObserver()
        report = await _reconciler(repo, observer).reconcile_native()
        assert report.observed == 0
        assert observer.config_reads == 0
