"""Scheduler tick tests with in-memory fakes (NOVA-35).

These pin the properties a database cannot easily show: the **order** of
"persist then publish" (criterion 5), idempotency of a repeated tick for one
due-time (criterion 6), singleton behaviour under a leader lock (criterion 7),
and exactly one graph run for an ``A -> B -> [C, D]`` graph (criterion 8).

The fake repository is a real in-memory store with the same primary-key
semantics as the StarRocks table: an insert of an existing id is observable as a
single row, so idempotency is proven, not assumed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.config import settings
from app.modules.task_orchestration.schedule import ScheduleError
from app.modules.task_orchestration.scheduler import (
    SchedulerTick,
    deterministic_run_id,
    naive_engine_time_to_utc,
    plan_tick,
    resolve_engine_timezone,
)


class FakeRepository:
    """In-memory stand-in for ``TaskOrchestrationRepository``."""

    def __init__(
        self,
        tasks: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
        engine_timezone: str | None = "UTC",
    ) -> None:
        self.tasks = tasks or []
        self.edges = edges or []
        self.graph_runs: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.engine_timezone = engine_timezone

    async def list_tasks(self) -> list[dict[str, Any]]:
        self.calls.append("list_tasks")
        return list(self.tasks)

    async def list_all_edges(self) -> list[dict[str, Any]]:
        self.calls.append("list_all_edges")
        return list(self.edges)

    async def get_engine_timezone(self) -> str | None:
        self.calls.append("get_engine_timezone")
        return self.engine_timezone

    async def get_graph_run(self, run_id: str) -> dict[str, Any] | None:
        self.calls.append("get_graph_run")
        return self.graph_runs.get(run_id)

    async def create_graph_run(self, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("create_graph_run")
        row = {
            "id": data["id"],
            "graph_id": data["graph_id"],
            "trigger_type": data.get("trigger_type", "manual"),
            "state": data.get("state", "pending"),
            "overlap_policy": data.get("overlap_policy", "skip"),
        }
        self.graph_runs[row["id"]] = row
        return row

    async def create_graph_run_once(
        self, data: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Non-destructive create-if-absent, mirroring the Primary-Key guard.

        The real repository decides ownership from the guarded insert's affected
        count, and refuses to clobber an existing row (a plain INSERT on a
        Primary-Key table is a destructive upsert), so the fake returns the
        existing row with ``created=False``.
        """
        self.calls.append("create_graph_run_once")
        run_id = data["id"]
        existing = self.graph_runs.get(run_id)
        if existing is not None:
            return existing, False
        return await self.create_graph_run(data), True

    async def delete_graph_run(self, run_id: str) -> bool:
        self.calls.append("delete_graph_run")
        return self.graph_runs.pop(run_id, None) is not None

    async def list_active_graph_runs(self, graph_id: str) -> list[dict[str, Any]]:
        self.calls.append("list_active_graph_runs")
        return [
            row
            for row in self.graph_runs.values()
            if row["graph_id"] == graph_id and row["state"] in ("pending", "running")
        ]


class RecordingTransport:
    """Records every publish, and can be told to fail."""

    def __init__(self, fail: bool = False) -> None:
        self.published: list[tuple[dict[str, Any], list[str]]] = []
        self.fail = fail

    async def publish_graph_run(self, graph_run: dict[str, Any], task_ids: list[str]) -> str:
        if self.fail:
            raise RuntimeError("redis down")
        self.published.append((graph_run, task_ids))
        return "1-0"


def make_task(
    name: str,
    *,
    task_id: str | None = None,
    kind: str = "interval",
    expr: str = "EVERY(INTERVAL 5 MINUTE)",
    tz: str = "UTC",
    created_at: datetime | None = None,
    overlap_policy: str = "skip",
) -> dict[str, Any]:
    """A task row for planning.

    ``created_at`` defaults to ``None`` — the planner then treats ``now`` as the
    anchor, so the task has no missed occurrence and is not due. A test that
    wants a due occurrence passes ``created_at`` explicitly, which also makes
    the number of due occurrences (catch-up) deterministic.
    """
    return {
        "id": task_id or f"id_{name}",
        "name": name,
        "schedule_kind": kind,
        "schedule_expr": expr,
        "timezone": tz,
        "created_at": created_at,
        "overlap_policy": overlap_policy,
    }


def make_edge(graph_id: str, parent: str, child: str) -> dict[str, Any]:
    return {
        "id": f"e_{graph_id}_{parent}_{child}",
        "graph_id": graph_id,
        "parent_task": parent,
        "child_task": child,
    }


NOW = datetime(2026, 1, 1, 0, 17, tzinfo=UTC)


class TestEngineTimeConversion:
    """A naive ``NOW()`` from the engine is in the session zone, not UTC."""

    def test_naive_value_is_read_in_the_engine_timezone(self):
        naive = datetime(2026, 1, 1, 9, 0)
        result = naive_engine_time_to_utc(naive, "Asia/Jakarta")
        assert result == datetime(2026, 1, 1, 2, 0, tzinfo=UTC)

    def test_utc_engine_timezone_is_identity(self):
        naive = datetime(2026, 1, 1, 9, 0)
        assert naive_engine_time_to_utc(naive, "UTC") == datetime(
            2026, 1, 1, 9, 0, tzinfo=UTC
        )

    def test_already_aware_value_is_converted_not_reinterpreted(self):
        aware = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        assert naive_engine_time_to_utc(aware, "Asia/Jakarta") == aware

    def test_unknown_engine_timezone_is_rejected(self):
        with pytest.raises(ScheduleError):
            naive_engine_time_to_utc(datetime(2026, 1, 1, 9, 0), "Not/AZone")

    def test_offset_engine_timezone_is_read_correctly(self):
        """NOVA-41: ``@@time_zone`` may be ``+07:00``, not an IANA key."""
        naive = datetime(2026, 1, 1, 9, 0)
        assert naive_engine_time_to_utc(naive, "+07:00") == datetime(
            2026, 1, 1, 2, 0, tzinfo=UTC
        )

    def test_negative_offset_engine_timezone_is_read_correctly(self):
        naive = datetime(2026, 1, 1, 9, 0)
        assert naive_engine_time_to_utc(naive, "-07:00") == datetime(
            2026, 1, 1, 16, 0, tzinfo=UTC
        )


class TestResolveEngineTimezone:
    """NOVA-39: the zone comes from the engine, never a static default."""

    async def test_reads_the_zone_from_the_engine_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "")
        repo = FakeRepository(engine_timezone="Asia/Jakarta")
        assert await resolve_engine_timezone(repo) == "Asia/Jakarta"
        assert "get_engine_timezone" in repo.calls

    async def test_explicit_override_wins_and_skips_the_engine(self, monkeypatch):
        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "Europe/Berlin")
        repo = FakeRepository(engine_timezone="Asia/Jakarta")
        assert await resolve_engine_timezone(repo) == "Europe/Berlin"
        assert "get_engine_timezone" not in repo.calls

    async def test_blank_override_is_treated_as_unconfigured(self, monkeypatch):
        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "   ")
        repo = FakeRepository(engine_timezone="Asia/Jakarta")
        assert await resolve_engine_timezone(repo) == "Asia/Jakarta"

    async def test_falls_back_to_utc_only_when_the_engine_is_silent(self, monkeypatch):
        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "")
        repo = FakeRepository(engine_timezone=None)
        assert await resolve_engine_timezone(repo) == "UTC"

    async def test_offset_engine_zone_is_accepted(self, monkeypatch):
        """NOVA-41: an offset session zone must not raise per-tick."""
        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "")
        repo = FakeRepository(engine_timezone="+07:00")
        assert await resolve_engine_timezone(repo) == "+07:00"

    async def test_unusable_engine_zone_raises_once_at_resolution(self, monkeypatch):
        """A bad engine zone fails where it is read, not silently per task."""
        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "")
        repo = FakeRepository(engine_timezone="Not/AZone")
        with pytest.raises(ScheduleError):
            await resolve_engine_timezone(repo)


class TestSchedulerStartupTimezoneGuard:
    """NOVA-39: an explicit override that contradicts the engine must stop startup.

    Auto-detect cannot be wrong, so the guard only exists to catch a pinned
    ``SCHEDULER_ENGINE_TIMEZONE`` that disagrees with ``SELECT @@time_zone`` — the
    case where an operator forces the 7-hour anchor shift back in.
    """

    def _probe(self, zone: str | None):
        async def _get_engine_timezone() -> str | None:
            return zone

        return _get_engine_timezone

    async def test_auto_detect_returns_the_engine_zone(self, monkeypatch):
        from app.modules.task_orchestration.repository import (
            task_orchestration_repository as repo,
        )
        from app.scheduler.__main__ import _assert_engine_timezone

        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "")
        monkeypatch.setattr(repo, "get_engine_timezone", self._probe("Asia/Jakarta"))
        assert await _assert_engine_timezone() == "Asia/Jakarta"

    async def test_blank_override_is_treated_as_auto_detect(self, monkeypatch):
        from app.modules.task_orchestration.repository import (
            task_orchestration_repository as repo,
        )
        from app.scheduler.__main__ import _assert_engine_timezone

        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "   ")
        monkeypatch.setattr(repo, "get_engine_timezone", self._probe("Asia/Jakarta"))
        assert await _assert_engine_timezone() == "Asia/Jakarta"

    async def test_matching_override_starts(self, monkeypatch):
        from app.modules.task_orchestration.repository import (
            task_orchestration_repository as repo,
        )
        from app.scheduler.__main__ import _assert_engine_timezone

        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "+07:00")
        monkeypatch.setattr(repo, "get_engine_timezone", self._probe("Asia/Jakarta"))
        assert await _assert_engine_timezone() == "Asia/Jakarta"

    async def test_mismatched_override_fails_fast(self, monkeypatch):
        from app.modules.task_orchestration.repository import (
            task_orchestration_repository as repo,
        )
        from app.scheduler.__main__ import (
            EngineTimezoneMismatchError,
            _assert_engine_timezone,
        )

        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "UTC")
        monkeypatch.setattr(repo, "get_engine_timezone", self._probe("Asia/Jakarta"))
        with pytest.raises(EngineTimezoneMismatchError, match="does not match"):
            await _assert_engine_timezone()

    async def test_silent_engine_does_not_block_start(self, monkeypatch):
        from app.modules.task_orchestration.repository import (
            task_orchestration_repository as repo,
        )
        from app.scheduler.__main__ import _assert_engine_timezone

        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "UTC")
        monkeypatch.setattr(repo, "get_engine_timezone", self._probe(None))
        assert await _assert_engine_timezone() is None


class TestEngineTimezoneAnchorRegression:
    """NOVA-39: an Asia/Jakarta engine must not push the anchor +7h ahead."""

    async def test_jakarta_created_at_still_fires(self, monkeypatch):
        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "")
        # A naive created_at of 09:00 means 02:00 UTC in Asia/Jakarta.
        created = datetime(2026, 1, 1, 9, 0)
        repo = FakeRepository(
            [make_task("solo", created_at=created)], engine_timezone="Asia/Jakarta"
        )
        transport = RecordingTransport()
        plan = await SchedulerTick(repo, transport).tick(
            datetime(2026, 1, 1, 2, 6, tzinfo=UTC)
        )
        assert len(plan.due) == 1
        assert len(transport.published) == 1

    async def test_ignoring_the_engine_zone_would_miss_the_fire(self):
        """The old behaviour, pinned as the regression it is.

        Reading the same naive 09:00 as UTC puts the anchor at 09:00Z while the
        tick is 02:06Z, so no interval has elapsed and nothing is due.
        """
        created = datetime(2026, 1, 1, 9, 0)
        repo = FakeRepository([make_task("solo", created_at=created)])
        plan = await SchedulerTick(repo, RecordingTransport()).tick(
            datetime(2026, 1, 1, 2, 6, tzinfo=UTC)
        )
        assert plan.due == []

    async def test_offset_engine_zone_still_fires(self):
        """NOVA-41: an engine reporting ``+07:00`` must fire like Asia/Jakarta."""
        created = datetime(2026, 1, 1, 9, 0)
        repo = FakeRepository([make_task("solo", created_at=created)], engine_timezone="+07:00")
        transport = RecordingTransport()
        plan = await SchedulerTick(repo, transport).tick(
            datetime(2026, 1, 1, 2, 6, tzinfo=UTC)
        )
        assert len(plan.due) == 1
        assert len(transport.published) == 1


class TestDeterministicRunId:
    def test_same_graph_and_due_time_yield_the_same_id(self):
        due = datetime(2026, 1, 1, 0, 13, tzinfo=UTC)
        assert deterministic_run_id("g1", due) == deterministic_run_id("g1", due)

    def test_different_due_time_yields_a_different_id(self):
        a = deterministic_run_id("g1", datetime(2026, 1, 1, 0, 13, tzinfo=UTC))
        b = deterministic_run_id("g1", datetime(2026, 1, 1, 0, 18, tzinfo=UTC))
        assert a != b

    def test_different_graph_yields_a_different_id(self):
        due = datetime(2026, 1, 1, 0, 13, tzinfo=UTC)
        assert deterministic_run_id("g1", due) != deterministic_run_id("g2", due)

    def test_id_fits_the_primary_key_column(self):
        assert len(deterministic_run_id("g" * 64, NOW)) <= 64


class TestPlanTick:
    #: An anchor one 5-minute step before the last occurrence at or before
    #: ``NOW`` (00:17). With anchor 00:10 the next fire is 00:15, so exactly one
    #: occurrence is due and catch-up emits a single run. Tests that want more or
    #: fewer occurrences pass their own ``created_at``.
    ONE_STEP_ANCHOR = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)

    def test_single_interval_task_is_due(self):
        tasks = [make_task("solo", created_at=self.ONE_STEP_ANCHOR)]
        plan = plan_tick(tasks, [], NOW)
        assert len(plan.due) == 1
        assert plan.due[0].task_names == ["solo"]
        assert plan.due[0].due_at == datetime(2026, 1, 1, 0, 15, tzinfo=UTC)

    def test_manual_task_is_never_due(self):
        tasks = [make_task("manual_only", kind="manual", expr="")]
        assert plan_tick(tasks, [], NOW).due == []

    def test_task_created_after_the_fire_is_not_due(self):
        tasks = [make_task("new", created_at=datetime(2026, 1, 1, 0, 16, tzinfo=UTC))]
        # First interval fire is one step after creation (00:21), after NOW.
        assert plan_tick(tasks, [], NOW).due == []

    def test_catch_up_emits_each_missed_occurrence(self):
        """A late tick fires the occurrences it missed, not only the last one."""
        # Anchor at 00:00, NOW at 00:17, 5-minute cadence: 00:05/10/15 are missed.
        tasks = [make_task("solo", created_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC))]
        plan = plan_tick(tasks, [], NOW)
        assert [d.due_at for d in plan.due] == [
            datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
        ]

    def test_catch_up_is_bounded_keeping_the_newest(self):
        """A long outage converges to the present instead of replaying history."""
        from app.modules.task_orchestration.schedule import MAX_CATCH_UP_OCCURRENCES

        # Anchor far in the past -> far more occurrences than the cap.
        tasks = [make_task("solo", created_at=datetime(2025, 1, 1, tzinfo=UTC))]
        plan = plan_tick(tasks, [], NOW)
        assert len(plan.due) == MAX_CATCH_UP_OCCURRENCES
        # The newest missed occurrence (00:15) is present; the oldest are dropped.
        assert plan.due[-1].due_at == datetime(2026, 1, 1, 0, 15, tzinfo=UTC)

    def test_root_only_triggers_a_graph_not_each_node(self):
        """Criterion 8: A -> B -> [C, D] is one graph run, not four."""
        tasks = [
            make_task("A", created_at=self.ONE_STEP_ANCHOR),
            make_task("B", kind="manual", expr=""),
            make_task("C", kind="manual", expr=""),
            make_task("D", kind="manual", expr=""),
        ]
        edges = [
            make_edge("g_dag", "A", "B"),
            make_edge("g_dag", "B", "C"),
            make_edge("g_dag", "B", "D"),
        ]
        plan = plan_tick(tasks, edges, NOW)
        assert len(plan.due) == 1
        run = plan.due[0]
        assert run.graph_id == "g_dag"
        assert run.task_names == ["A", "B", "C", "D"]
        assert set(run.task_ids) == {"id_A", "id_B", "id_C", "id_D"}

    def test_child_schedule_does_not_trigger_a_second_run(self):
        """Only roots anchor; a scheduled child must not double-fire the graph."""
        tasks = [
            make_task("A", created_at=self.ONE_STEP_ANCHOR),
            make_task("B", created_at=self.ONE_STEP_ANCHOR),
        ]
        edges = [make_edge("g_dag", "A", "B")]
        plan = plan_tick(tasks, edges, NOW)
        assert len(plan.due) == 1
        assert plan.due[0].task_names == ["A", "B"]

    def test_two_disjoint_graphs_are_two_runs(self):
        tasks = [
            make_task("A", created_at=self.ONE_STEP_ANCHOR),
            make_task("X", created_at=self.ONE_STEP_ANCHOR),
        ]
        plan = plan_tick(tasks, [], NOW)
        assert len(plan.due) == 2
        # An unscoped standalone task's graph id is its bare name.
        assert {d.graph_id for d in plan.due} == {"A", "X"}

    def test_two_scheduled_roots_in_one_graph_are_two_occurrences(self):
        """A graph with several scheduled roots fires once per due root.

        Previously only the earliest occurrence across a graph's roots was
        enqueued, so a second root was silently dropped forever once the
        earliest root's run existed. Each due root is its own occurrence and its
        own (deterministically keyed) run.

        ``A -> B`` and ``C -> B``: A and C are both roots (no incoming edge)
        feeding the join B, and both are scheduled with different cadences.
        """
        tasks = [
            # Each anchor yields exactly one due occurrence for its cadence
            # (A: 00:16 from a 4-min step at 00:12; C: 00:17 from a 3-min step at
            # 00:14), so the test observes two distinct roots, not a catch-up.
            make_task(
                "A",
                expr="EVERY(INTERVAL 4 MINUTE)",
                created_at=datetime(2026, 1, 1, 0, 12, tzinfo=UTC),
            ),
            make_task(
                "C",
                expr="EVERY(INTERVAL 3 MINUTE)",
                created_at=datetime(2026, 1, 1, 0, 14, tzinfo=UTC),
            ),
            make_task("B", kind="manual", expr=""),
        ]
        edges = [
            make_edge("g_dag", "A", "B"),
            make_edge("g_dag", "C", "B"),
        ]
        plan = plan_tick(tasks, edges, NOW)

        assert len(plan.due) == 2, "both roots are due and must fire"
        assert {d.root_task for d in plan.due} == {"A", "C"}
        # Distinct occurrences -> distinct idempotency keys.
        assert len({d.run_id for d in plan.due}) == 2

    def test_two_roots_due_at_the_same_instant_do_not_collide(self):
        """Same instant, different roots: two runs, not one.

        The run id includes the root, so two roots whose occurrences coincide
        cannot collapse onto one primary key (which would drop one root's work).
        """
        tasks = [
            # Same cadence, so both roots land on the same 00:16 occurrence —
            # the identity collision this test guards against. Anchor at 00:12
            # gives exactly that one step.
            make_task(
                "A",
                expr="EVERY(INTERVAL 4 MINUTE)",
                created_at=datetime(2026, 1, 1, 0, 12, tzinfo=UTC),
            ),
            make_task(
                "C",
                expr="EVERY(INTERVAL 4 MINUTE)",
                created_at=datetime(2026, 1, 1, 0, 12, tzinfo=UTC),
            ),
            make_task("B", kind="manual", expr=""),
        ]
        edges = [
            make_edge("g", "A", "B"),
            make_edge("g", "C", "B"),
        ]
        plan = plan_tick(tasks, edges, NOW)

        assert len(plan.due) == 2
        assert plan.due[0].due_at == plan.due[1].due_at
        assert len({d.run_id for d in plan.due}) == 2

    def test_invalid_schedule_is_skipped_not_fatal(self):
        tasks = [
            make_task("broken", kind="cron", expr="not a cron"),
            make_task("ok", created_at=self.ONE_STEP_ANCHOR),
        ]
        plan = plan_tick(tasks, [], NOW)
        assert [d.task_names for d in plan.due] == [["ok"]]
        assert plan.skipped == 1

    def test_offset_engine_zone_anchors_a_naive_created_at(self):
        """NOVA-41: a naive anchor is read in the engine's offset zone."""
        # 07:00 at UTC+7 is 00:00Z; 12:10 at UTC+7 is 00:10Z, one step before NOW.
        tasks = [make_task("solo", created_at=datetime(2026, 1, 1, 7, 10))]
        plan = plan_tick(tasks, [], NOW, "+07:00")
        assert [d.task_names for d in plan.due] == [["solo"]]

    def test_unusable_engine_zone_skips_every_task_not_crashes(self):
        """NOVA-41: a bad anchor zone counts as skipped, not a raised tick.

        ``naive_engine_time_to_utc`` runs before the occurrence walk; it must be
        inside the same ``ScheduleError`` guard so one bad zone cannot abort the
        whole tick. The anchor must be naive for the engine zone to be consulted.
        """
        tasks = [make_task("solo", created_at=datetime(2026, 1, 1, 9, 0))]
        bad = plan_tick(tasks, [], NOW, "Not/AZone")
        assert bad.due == []
        assert bad.skipped == 1

    def test_one_broken_task_does_not_suppress_a_healthy_one(self):
        tasks = [
            make_task("broken", tz="Not/AZone", created_at=self.ONE_STEP_ANCHOR),
            make_task("ok", created_at=self.ONE_STEP_ANCHOR),
        ]
        plan = plan_tick(tasks, [], NOW)
        assert [d.task_names for d in plan.due] == [["ok"]]
        assert plan.skipped == 1


class TestSchemaScoping:
    """Tasks are scoped to a database.schema, like a stage."""

    def _scoped(self, name: str, database: str, schema: str, **kwargs):
        task = make_task(name, created_at=TestPlanTick.ONE_STEP_ANCHOR, **kwargs)
        task["database_name"] = database
        task["schema_name"] = schema
        return task

    def test_same_name_in_two_schemas_are_two_standalone_graphs(self):
        tasks = [
            self._scoped("etl_daily", "db1", "silver"),
            self._scoped("etl_daily", "db1", "gold"),
        ]
        plan = plan_tick(tasks, [], NOW)
        assert {d.graph_id for d in plan.due} == {
            "db1.silver.etl_daily",
            "db1.gold.etl_daily",
        }
        assert len(plan.due) == 2

    def test_a_scheduled_root_uses_its_own_schema_task_row(self):
        """Two same-named roots must not share one task row.

        The graph id is qualified, so each graph resolves its root within its own
        scope: a schedule set on one schema's task must not fire the other's.
        """
        scheduled = self._scoped("etl", "db1", "silver", expr="EVERY(INTERVAL 5 MINUTE)")
        manual = self._scoped("etl", "db1", "gold", kind="manual", expr="")
        plan = plan_tick([scheduled, manual], [], NOW)
        assert [d.graph_id for d in plan.due] == ["db1.silver.etl"]


class TestTickOrderingAndIdempotency:
    async def test_persist_happens_before_publish(self):
        """Criterion 5: NOVA_SYSTEM first, Redis second."""
        repo = FakeRepository([make_task("solo", created_at=TestPlanTick.ONE_STEP_ANCHOR)])
        transport = RecordingTransport()
        tick = SchedulerTick(repo, transport)

        await tick.tick(NOW)

        create_index = repo.calls.index("create_graph_run_once")
        assert create_index >= 0
        assert len(transport.published) == 1
        persisted_id = next(iter(repo.graph_runs))
        published_row, published_tasks = transport.published[0]
        assert published_row["id"] == persisted_id
        assert published_tasks == ["id_solo"]

    async def test_graph_run_survives_a_failed_publish(self):
        """The row exists even when the push fails — Redis is not the source.

        A transport failure is isolated per graph (``_enqueue`` is called inside
        a try/except), so it must be logged rather than escape the tick: a
        single Redis blip must not abandon every later due graph in the batch.
        The persisted row is what the reconciler re-derives from.
        """
        repo = FakeRepository([make_task("solo", created_at=TestPlanTick.ONE_STEP_ANCHOR)])
        tick = SchedulerTick(repo, RecordingTransport(fail=True))

        plan = await tick.tick(NOW)

        assert len(repo.graph_runs) == 1, "the row must survive a failed publish"
        assert len(plan.due) == 1

    async def test_one_failed_publish_does_not_drop_the_rest_of_the_batch(self):
        """Per-graph isolation: a bad graph must not starve its siblings."""

        class FlakyTransport(RecordingTransport):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            async def publish_graph_run(self, graph_run, task_ids):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("redis down")
                return await super().publish_graph_run(graph_run, task_ids)

        repo = FakeRepository([
            make_task("A", created_at=TestPlanTick.ONE_STEP_ANCHOR),
            make_task("X", created_at=TestPlanTick.ONE_STEP_ANCHOR),
        ])
        transport = FlakyTransport()

        plan = await SchedulerTick(repo, transport).tick(NOW)

        assert len(plan.due) == 2
        assert len(repo.graph_runs) == 2, "both rows persisted despite one failure"
        assert len(transport.published) == 1, "the healthy graph was still pushed"

    async def test_two_ticks_for_one_due_time_create_one_run(self):
        """Criterion 6: the deterministic id makes a repeated tick a no-op."""
        repo = FakeRepository([make_task("solo", created_at=TestPlanTick.ONE_STEP_ANCHOR)])
        transport = RecordingTransport()
        tick = SchedulerTick(repo, transport)

        await tick.tick(NOW)
        await tick.tick(NOW)

        assert len(repo.graph_runs) == 1
        assert len(transport.published) == 1

    async def test_batch_existing_lookup_avoids_per_occurrence_reads(self):
        class BatchRepository(FakeRepository):
            async def existing_graph_run_ids(self, run_ids: list[str]) -> set[str]:
                self.calls.append("existing_graph_run_ids")
                return set(run_ids) & self.graph_runs.keys()

        repo = BatchRepository([make_task("solo", created_at=TestPlanTick.ONE_STEP_ANCHOR)])
        transport = RecordingTransport()
        tick = SchedulerTick(repo, transport)

        await tick.tick(NOW)
        await tick.tick(NOW)

        assert repo.calls.count("existing_graph_run_ids") == 2
        assert "get_graph_run" not in repo.calls
        assert len(transport.published) == 1

    async def test_a_later_due_time_creates_a_second_run(self):
        # `queue` is required for a second run to be enqueued while the first is
        # still active; with the default `skip` the occurrence is deliberately
        # dropped (see the overlap tests).
        repo = FakeRepository([
            make_task("solo", overlap_policy="queue", created_at=TestPlanTick.ONE_STEP_ANCHOR)
        ])
        transport = RecordingTransport()
        tick = SchedulerTick(repo, transport)

        await tick.tick(NOW)
        await tick.tick(NOW + timedelta(minutes=5))

        assert len(repo.graph_runs) == 2
        assert len(transport.published) == 2

    async def test_retick_does_not_reset_an_existing_run(self):
        """Regression: a re-tick must not clobber a run the worker already moved.

        ``CONFIG_TASK_GRAPH_RUNS`` is a StarRocks Primary-Key table where a
        plain INSERT is a destructive upsert, so a second tick for the same due
        instant used to reset a ``running``/``success`` run back to ``pending``
        and re-publish it — resurrecting completed work. Create-if-absent must
        leave the existing row (and its state) untouched.
        """
        repo = FakeRepository([make_task("solo", created_at=TestPlanTick.ONE_STEP_ANCHOR)])
        transport = RecordingTransport()
        tick = SchedulerTick(repo, transport)

        await tick.tick(NOW)
        run_id = next(iter(repo.graph_runs))
        repo.graph_runs[run_id]["state"] = "success"

        await tick.tick(NOW)

        assert repo.graph_runs[run_id]["state"] == "success"
        assert len(transport.published) == 1, "a re-tick must not re-publish"

    async def test_payload_carries_ids_and_metadata_only(self):
        """Credential-invisible: the stream payload is ids plus trigger type."""
        repo = FakeRepository([make_task("solo", created_at=TestPlanTick.ONE_STEP_ANCHOR)])
        transport = RecordingTransport()
        await SchedulerTick(repo, transport).tick(NOW)

        payload = transport.published[0][0]
        # `overlap_policy` is a policy string (`skip`/`queue`/`allow`), not a
        # credential; it is carried so the worker can honour QUEUE. The
        # credential check below is what this test exists for.
        assert set(payload) <= {
            "id",
            "graph_id",
            "trigger_type",
            "state",
            "overlap_policy",
        }
        serialized = str(payload).lower()
        for bad in ("password", "secret", "token", "credential"):
            assert bad not in serialized

    async def test_tick_with_nothing_due_publishes_nothing(self):
        repo = FakeRepository([make_task("manual_only", kind="manual", expr="")])
        transport = RecordingTransport()
        plan = await SchedulerTick(repo, transport).tick(NOW)
        assert plan.due == []
        assert transport.published == []
        assert repo.graph_runs == {}


class TestEngineTimezoneSettingIsNotLeaked:
    """NOVA-131: the integration suite must not mutate global settings.

    ``tests/integration/test_task_scheduler.py`` used to assign
    ``settings.SCHEDULER_ENGINE_TIMEZONE`` directly and never restore it. In a
    full run that leaked into this unit module: ``test_offset_engine_zone_still
    _fires`` sets the setting to ``""`` (auto-detect) via ``monkeypatch``, but
    the leaked value from the already-executed integration fixture took
    precedence, so the test failed only when the two suites ran together.
    """

    async def test_offset_engine_zone_still_fires_after_other_suites(
        self, monkeypatch
    ):
        """The NOVA-41 case, re-asserted at its own boundary.

        Sets the setting to ``""`` itself, so it can only pass if the setting is
        free of foreign writes at this point in the session.
        """
        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "")
        created = datetime(2026, 1, 1, 9, 0)
        repo = FakeRepository(
            [make_task("solo", created_at=created)], engine_timezone="+07:00"
        )
        transport = RecordingTransport()
        plan = await SchedulerTick(repo, transport).tick(
            datetime(2026, 1, 1, 2, 6, tzinfo=UTC)
        )
        assert len(plan.due) == 1
        assert len(transport.published) == 1


def test_integration_scheduler_fixture_restores_scheduler_timezone() -> None:
    """The integration fixture must write ``SCHEDULER_ENGINE_TIMEZONE`` safely.

    A behavioural test cannot observe the leak: ``monkeypatch`` restores on
    teardown, so by the time any unit test runs the setting looks untouched
    whether the integration file used ``monkeypatch`` or a bare assignment.
    Assert the source contract directly — the fixture may only reach the global
    through a restoring mechanism (``monkeypatch``), never ``settings.X = ...``
    or ``setattr(settings, ...)``.
    """
    import ast
    from pathlib import Path

    source_path = (
        Path(__file__).resolve().parent.parent
        / "integration"
        / "test_task_scheduler.py"
    )
    tree = ast.parse(source_path.read_text())

    offender: ast.Assign | ast.Call | None = None
    for node in ast.walk(tree):
        # `settings.SCHEDULER_ENGINE_TIMEZONE = ...` (bare assignment)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "SCHEDULER_ENGINE_TIMEZONE"
                ):
                    offender = node
        # `setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", ...)` — also a leak
        # unless it goes through monkeypatch, which is a method call on a
        # `monkeypatch` object, not the builtin `setattr`.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "SCHEDULER_ENGINE_TIMEZONE"
        ):
            offender = node

    assert offender is None, (
        "tests/integration/test_task_scheduler.py mutates the global "
        "settings.SCHEDULER_ENGINE_TIMEZONE without restoration at line "
        f"{offender.lineno}; use monkeypatch.setattr so the value is restored."
    )
