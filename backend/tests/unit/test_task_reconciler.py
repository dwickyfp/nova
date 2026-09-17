"""Unit tests for native-state reconciliation (NOVA-37).

These prove the acceptance criteria an engine cannot easily show, with fakes:

* a **lost trace** — a running node whose worker heartbeat lapsed — is marked
  ``abandoned``, an explicit state, never success;
* a **fresh node with no native row** is never settled by ``reconcile_native``
  (no abandon, no audit) — only the heartbeat path may settle it;
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
    fetch_native_runs,
    parse_consecutive_failures,
    read_frontend_config_rows,
    read_latest_native_runs,
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
        self.stale_task_runs: list[dict[str, Any]] = []

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
        return self.stale_task_runs[:limit]

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
    async def test_missing_native_run_is_not_settled(self, audit):
        """NOVA-52: no native row proves nothing per-task, so nothing settles.

        A ``MISSING`` observation must not abandon the node via
        ``reconcile_native``: the engine's archive is engine-wide and carries no
        per-task information. The lost trace is the heartbeat path's job
        (``abandon_stale_nodes``), which is the only writer of
        ``NODE_ABANDONED``.
        """
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {"A": NativeRun(task_name="A", state=NativeState.MISSING)}
        )

        report = await _reconciler(repo, observer).reconcile_native()

        assert report.lost_traces == []
        assert report.advanced == []
        assert repo.task_runs["n1"]["state"] == "running"
        assert repo.task_runs["n1"]["state"] != "success"
        assert audit == []

    async def test_missing_native_run_is_idempotent(self, audit):
        """Criterion 5: repeated passes on a MISSING read change nothing."""
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
        assert repo.task_runs["n1"]["state"] == "running"
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


class _Cursor:
    """A cursor that yields scripted rows, or raises on a chosen statement."""

    def __init__(self, conn):
        self._conn = conn
        self._rows: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self._conn.executed.append(sql)
        for marker, error in self._conn.failures:
            if marker in sql:
                raise RuntimeError(error)
        self._rows = list(self._conn.rows)

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _Connection:
    """A connection whose empties may come from a dead-but-stale session.

    ``rows=[]`` mimics a successful-but-empty read; ``raise_on="SELECT 1"``
    mimics the liveness probe finding the engine gone, which is exactly the
    stale-pool state NOVA-43 describes.

    ``failures`` maps a SQL substring to the error that reading it must raise,
    so NOVA-46's run-trace-surface failure (the *task_runs* read raising 1064
    while ``SELECT 1`` still succeeds) can be replayed, as well as a generic
    transport blip (which must stay ``UNKNOWN``).
    """

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        raise_on: str | None = None,
        raise_on_error: str = "engine gone",
        failures: list[tuple[str, str]] | None = None,
    ) -> None:
        self.rows = rows if rows is not None else []
        self.raise_on = raise_on
        self.raise_on_error = raise_on_error
        self.failures = list(failures or [])
        if raise_on is not None:
            self.failures.append((raise_on, raise_on_error))
        self.executed: list[str] = []

    def cursor(self, *args, **kwargs):
        return _Cursor(self)


class TestStaleReadStillLivenessChecked:
    """NOVA-43: an empty read is MISSING only if the engine is proven live."""

    async def test_empty_read_with_dead_probe_is_unknown(self):
        conn = _Connection(rows=[], raise_on="SELECT 1", raise_on_error="transport gone")
        result = await read_latest_native_runs(conn, ["A"])
        assert result["A"].state is NativeState.UNKNOWN
        assert any("SELECT 1" in sql for sql in conn.executed)

    async def test_empty_read_with_live_probe_is_missing(self):
        conn = _Connection(rows=[])
        result = await read_latest_native_runs(conn, ["A"])
        assert result["A"].state is NativeState.MISSING

    async def test_nonempty_read_skips_the_probe(self):
        conn = _Connection(rows=[{"TASK_NAME": "A", "STATE": "FINISHED"}])
        result = await read_latest_native_runs(conn, ["A"])
        assert result["A"].state is NativeState.SUCCESS
        assert not any("SELECT 1" in sql for sql in conn.executed)

    async def test_dead_probe_never_marks_a_node_abandoned(self, audit, monkeypatch):
        """The end-to-end consequence: stale empty + dead probe writes nothing."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)

        async def stale_fetch(task_names):
            return {n: NativeRun(task_name=n, state=NativeState.UNKNOWN) for n in task_names}

        reconciler = _reconciler(repo, FakeObserver())
        monkeypatch.setattr(reconciler._observer, "read_runs", stale_fetch)

        report = await reconciler.reconcile_native()

        assert report.lost_traces == []
        assert repo.task_runs["n1"]["state"] == "running"
        assert audit == []


_ARCHIVE_1064 = (
    "Getting analyzing error. Detail message: RepoExecutor execute sql failed: "
    "SELECT history_content_json FROM _statistics_.task_run_history"
)


class TestArchiveFailureIsUnknownNotMissing:
    """NOVA-46 / NOVA-47: a failed read is ``UNKNOWN``, never a settled trace.

    On a fresh FE the ``information_schema.task_runs`` read raises 1064 because
    ``_statistics_.task_run_history`` is not initialized, and it does so for
    *every* ``task_runs`` read while ordinary statements still succeed. The
    signal is engine-wide and carries no per-task information, so no verdict
    about an individual task's trace may be drawn from it. Any failed read —
    archive 1064, an FE RPC ``getTaskRuns`` failure, or a transport blip; on a
    live engine or a dead one — is ``UNKNOWN`` and the reconciler writes
    nothing. Lost traces are settled by the heartbeat path instead.
    """

    async def test_batch_read_1064_on_a_live_engine_is_unknown(self):
        conn = _Connection(failures=[("ORDER BY TASK_NAME", _ARCHIVE_1064)])

        result = await read_latest_native_runs(conn, ["lost_1"])

        assert result["lost_1"].state is NativeState.UNKNOWN

    async def test_fe_rpc_getTaskRuns_failure_is_unknown(self):
        conn = _Connection(
            failures=[
                (
                    "ORDER BY TASK_NAME",
                    "1064 FE RPC failure, reason=Internal error processing "
                    "getTaskRuns: BE:10001, host: unknown",
                )
            ]
        )

        result = await read_latest_native_runs(conn, ["lost_1"])

        assert result["lost_1"].state is NativeState.UNKNOWN

    async def test_batch_read_1064_with_dead_engine_is_unknown(self):
        conn = _Connection(
            failures=[("ORDER BY TASK_NAME", _ARCHIVE_1064), ("SELECT 1", "engine gone")]
        )

        result = await read_latest_native_runs(conn, ["A", "B"])

        assert result["A"].state is NativeState.UNKNOWN
        assert result["B"].state is NativeState.UNKNOWN

    async def test_transport_blip_on_a_live_engine_is_unknown(self):
        """A transport blip must NOT abandon healthy work (NOVA-43)."""
        conn = _Connection(
            failures=[("ORDER BY TASK_NAME", "Lost connection to MySQL server during query")]
        )

        result = await read_latest_native_runs(conn, ["A"])

        assert result["A"].state is NativeState.UNKNOWN

    async def test_single_task_1064_is_unknown(self):
        from app.modules.task_orchestration.native import read_latest_native_run

        conn = _Connection(failures=[("ORDER BY CREATE_TIME", _ARCHIVE_1064)])

        result = await read_latest_native_run(conn, "lost_1")

        assert result.state is NativeState.UNKNOWN

    async def test_single_task_transport_blip_is_unknown(self):
        from app.modules.task_orchestration.native import read_latest_native_run

        conn = _Connection(
            failures=[("ORDER BY CREATE_TIME", "Lost connection to MySQL server during query")]
        )

        result = await read_latest_native_run(conn, "A")

        assert result.state is NativeState.UNKNOWN

    async def test_archive_failure_does_not_settle_a_running_node(
        self, audit, monkeypatch
    ):
        """The end-to-end consequence: a failed read writes nothing.

        The node stays ``running`` — it is not abandoned from an uninformative
        read, and it is not silently succeeded. The heartbeat path settles it
        once the worker's heartbeat lapses (proven separately).
        """
        repo = FakeRepository()
        task_id = repo.add_task("lost_1")
        repo.add_node_run("n1", task_id)

        async def archive_failure(task_names):
            return {
                name: NativeRun(task_name=name, state=NativeState.UNKNOWN)
                for name in task_names
            }

        reconciler = _reconciler(repo, FakeObserver())
        monkeypatch.setattr(reconciler._observer, "read_runs", archive_failure)

        report = await reconciler.reconcile_native()

        assert report.lost_traces == []
        assert report.unknown == ["lost_1"]
        assert repo.task_runs["n1"]["state"] == "running"
        assert audit == []


class TestLostTraceSettledByHeartbeat:
    """AC #2 via the durable signal: a lapsed heartbeat settles the node.

    The native archive cannot say whether a trace is absent (NOVA-46), so the
    lost trace is settled here instead: ``scan`` reports a ``RUNNING`` node
    whose worker heartbeat stopped and ``abandon_stale_nodes`` performs the
    conditional write with ``NODE_ABANDONED`` audit, independent of any engine
    read. This is the "task was running when the FE/worker died" scenario, and
    it cannot fire on a healthy in-flight node because the worker keeps stamping
    its heartbeat.
    """

    async def test_stale_heartbeat_is_reported_for_abandonment(self):
        repo = FakeRepository()
        task_id = repo.add_task("A")
        node = repo.add_node_run("n1", task_id)
        repo.stale_task_runs.append(node)

        report = await Reconciler(
            repo, observer=FakeObserver(), heartbeat_timeout_seconds=120
        ).scan()

        assert report.abandoned_task_runs == ["n1"]
        assert report.abandoned_graph_runs == ["gr1"]

    async def test_abandoned_node_ids_filters_by_graph_run(self):
        repo = FakeRepository()
        task_id = repo.add_task("A")
        node = repo.add_node_run("n1", task_id, graph_run_id="gr1")
        other = repo.add_node_run("n2", task_id, graph_run_id="gr2")
        repo.stale_task_runs.extend([node, other])

        ids = await Reconciler(
            repo, observer=FakeObserver(), heartbeat_timeout_seconds=120
        ).abandoned_node_ids("gr1")

        assert ids == ["n1"]

    async def test_a_healthy_node_is_not_reported(self):
        """A node whose heartbeat is fresh is never a lost trace."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)

        report = await Reconciler(
            repo, observer=FakeObserver(), heartbeat_timeout_seconds=120
        ).scan()

        assert report.abandoned_task_runs == []

    async def test_stale_heartbeat_settles_the_node_with_audit(self, audit):
        """AC #3: the heartbeat path abandons the row, not just reports it."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        node = repo.add_node_run("n1", task_id)
        repo.stale_task_runs.append(node)

        abandoned = await Reconciler(
            repo, observer=FakeObserver(), heartbeat_timeout_seconds=120
        ).abandon_stale_nodes()

        assert [row["id"] for row in abandoned] == ["n1"]
        assert repo.task_runs["n1"]["state"] == "abandoned"
        assert [entry["action"] for entry in audit] == ["NODE_ABANDONED"]

    async def test_settling_a_stale_node_is_idempotent(self, audit):
        """AC #5: a second pass on the same stale row writes nothing."""
        repo = FakeRepository()
        task_id = repo.add_task("A")
        node = repo.add_node_run("n1", task_id)
        repo.stale_task_runs.append(node)
        reconciler = Reconciler(
            repo, observer=FakeObserver(), heartbeat_timeout_seconds=120
        )

        first = await reconciler.abandon_stale_nodes()
        audit_count = len(audit)
        second = await reconciler.abandon_stale_nodes()

        assert [row["id"] for row in first] == ["n1"]
        assert second == []
        assert len(audit) == audit_count
        assert repo.task_runs["n1"]["state"] == "abandoned"

    async def test_a_fresh_node_is_never_abandoned(self, audit):
        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)

        abandoned = await Reconciler(
            repo, observer=FakeObserver(), heartbeat_timeout_seconds=120
        ).abandon_stale_nodes()

        assert abandoned == []
        assert repo.task_runs["n1"]["state"] == "running"
        assert audit == []

    async def test_reconcile_native_never_abandons_a_fresh_inflight_node(self, audit):
        """NOVA-52 fail-before: a fresh node with no native row stays running.

        A worker that just sent ``SUBMIT TASK`` has a running row and a fresh
        heartbeat but no ``task_runs`` row yet. ``reconcile_native`` reads that
        as ``MISSING``; before the fix it settled the node as ``abandoned`` and
        wrote ``NODE_ABANDONED``. No observation from the engine's archive may
        settle a node — only the lapsed heartbeat may.
        """
        repo = FakeRepository()
        task_id = repo.add_task("qa_healthy")
        repo.add_node_run("n1", task_id)
        observer = FakeObserver(
            {"qa_healthy": NativeRun(task_name="qa_healthy", state=NativeState.MISSING)}
        )

        reconciler = _reconciler(repo, observer)
        scan_report = await reconciler.scan()
        native_report = await reconciler.reconcile_native()

        assert scan_report.abandoned_task_runs == []
        assert native_report.advanced == []
        assert native_report.lost_traces == []
        assert repo.task_runs["n1"]["state"] == "running"
        assert audit == []


class TestConnectionAcquireTolerance:
    """NOVA-44: acquiring a connection must not raise out of the reconciler."""

    async def test_fetch_native_runs_returns_unknown_when_acquire_fails(self, monkeypatch):
        import app.modules.task_orchestration.native as native_module

        def _boom():
            raise RuntimeError("can't connect to MySQL server")

        monkeypatch.setattr(native_module.db, "system_conn", _boom)

        result = await fetch_native_runs(["A", "B"])

        assert {n: r.state for n, r in result.items()} == {
            "A": NativeState.UNKNOWN,
            "B": NativeState.UNKNOWN,
        }

    async def test_reconcile_native_degrades_without_raising(self, audit, monkeypatch):
        import app.modules.task_orchestration.native as native_module

        monkeypatch.setattr(
            native_module.db,
            "system_conn",
            lambda: (_ for _ in ()).throw(RuntimeError("can't connect")),
        )

        repo = FakeRepository()
        task_id = repo.add_task("A")
        repo.add_node_run("n1", task_id)

        report = await Reconciler(repo).reconcile_native()

        assert report.advanced == []
        assert report.lost_traces == []
        assert repo.task_runs["n1"]["state"] == "running"
        assert audit == []
