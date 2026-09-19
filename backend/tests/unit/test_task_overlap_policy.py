"""`OVERLAP_POLICY` runtime enforcement (NOVA-54 / 9b stage 3b).

Stage 2 stored ``overlap_policy`` and validated its value, but the scheduler and
worker never read it — the policy was inert. These tests pin the three Nova
semantics at graph-run **enqueue** (scheduler) and at **claim** (worker queue
deferral), with in-memory fakes and no engine.

Semantics implemented:

* ``skip``  — do not create a new run while one is active for the graph.
* ``queue`` — create the run; the worker defers it until the active run settles.
* ``allow`` — create the run and let it execute concurrently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.modules.task_orchestration.dag import GraphState
from app.modules.task_orchestration.scheduler import (
    SchedulerTick,
    plan_tick,
    should_enqueue,
)
from app.modules.task_orchestration.worker import GraphRunJob, GraphRunWorker
from tests.unit.test_task_orchestration_worker import FakeRepository, RecordingExecutor
from tests.unit.test_task_scheduler_tick import (
    FakeRepository as SchedulerFakeRepository,
)
from tests.unit.test_task_scheduler_tick import (
    RecordingTransport,
    make_task,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

#: An anchor one 5-minute step before the last occurrence at or before ``NOW``
#: (11:55), so exactly one occurrence (12:00) is due and catch-up emits a single
#: run. A task with no ``created_at`` is anchored at ``now`` and is never due.
ONE_STEP_ANCHOR = datetime(2026, 1, 1, 11, 55, tzinfo=UTC)


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


class TestShouldEnqueue:
    def test_no_active_run_always_enqueues(self) -> None:
        for policy in ("skip", "queue", "allow"):
            assert should_enqueue(policy, 0) is True

    def test_skip_refuses_while_a_run_is_active(self) -> None:
        assert should_enqueue("skip", 1) is False

    def test_queue_and_allow_create_a_run_while_active(self) -> None:
        assert should_enqueue("queue", 1) is True
        assert should_enqueue("allow", 1) is True

    def test_unknown_policy_falls_back_to_skip(self) -> None:
        # Conservative: never invent an overlap the caller did not ask for.
        assert should_enqueue("nonsense", 1) is False
        assert should_enqueue("", 1) is False


class TestSchedulerEnqueue:
    async def test_skip_drops_a_due_occurrence_while_one_is_active(self) -> None:
        repo = SchedulerFakeRepository(
            [make_task(
                "solo", overlap_policy="skip", created_at=ONE_STEP_ANCHOR
            )]
        )
        transport = RecordingTransport()
        tick = SchedulerTick(repo, transport)

        first = await tick.tick(NOW)
        # The second tick is late, so it catches up: 12:00 and 12:05 are both due.
        second = await tick.tick(NOW + timedelta(minutes=5))

        assert len(first.due) == 1
        assert len(second.due) == 2, "both missed occurrences are due"
        # 12:00 already has a run (idempotent, not an overlap skip); 12:05 is the
        # genuinely new occurrence and `skip` refuses it while 12:00 is active.
        assert second.overlap_skipped == 1
        assert len(repo.graph_runs) == 1
        assert len(transport.published) == 1

    async def test_queue_enqueues_a_second_run(self) -> None:
        repo = SchedulerFakeRepository([make_task(
                "solo", overlap_policy="queue", created_at=ONE_STEP_ANCHOR
            )])
        transport = RecordingTransport()
        tick = SchedulerTick(repo, transport)

        await tick.tick(NOW)
        second = await tick.tick(NOW + timedelta(minutes=5))

        assert second.overlap_skipped == 0
        assert len(repo.graph_runs) == 2
        assert len(transport.published) == 2

    async def test_allow_enqueues_a_second_run(self) -> None:
        repo = SchedulerFakeRepository([make_task(
                "solo", overlap_policy="allow", created_at=ONE_STEP_ANCHOR
            )])
        transport = RecordingTransport()
        tick = SchedulerTick(repo, transport)

        await tick.tick(NOW)
        second = await tick.tick(NOW + timedelta(minutes=5))

        assert second.overlap_skipped == 0
        assert len(repo.graph_runs) == 2

    async def test_the_root_tasks_policy_is_copied_onto_the_run(self) -> None:
        repo = SchedulerFakeRepository([make_task(
                "solo", overlap_policy="allow", created_at=ONE_STEP_ANCHOR
            )])
        transport = RecordingTransport()
        await SchedulerTick(repo, transport).tick(NOW)

        run = next(iter(repo.graph_runs.values()))
        assert run["overlap_policy"] == "allow"

    async def test_cron_root_created_by_create_task_fires_a_graph_run(self) -> None:
        """End-to-end: stage-2 metadata drives a stage-3b scheduled graph run."""
        from app.modules.task_orchestration.ddl import parse_create_task

        lowered = parse_create_task(
            "CREATE TASK db1.etl.nightly SCHEDULE = '0 12 * * * UTC' "
            "AS INSERT INTO t SELECT 1",
            timezone="UTC",
        )
        # The task is schema-scoped: the graph id is the qualified name.
        assert (lowered.database_name, lowered.schema_name, lowered.name) == (
            "db1",
            "etl",
            "nightly",
        )
        root = {
            "id": "id_nightly",
            "name": lowered.name,
            "database_name": lowered.database_name,
            "schema_name": lowered.schema_name,
            "definition": lowered.body,
            "created_by": "alice",
            "schedule_kind": lowered.schedule_kind,
            "schedule_expr": lowered.schedule_expr,
            "when_expr": lowered.when_expr,
            "overlap_policy": lowered.overlap_policy,
            "timezone": lowered.timezone,
            # Created before the 12:00 fire, so the 12:00 occurrence is due.
            "created_at": datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        }
        repo = SchedulerFakeRepository([root], [])
        transport = RecordingTransport()

        plan = await SchedulerTick(repo, transport).tick(NOW)

        assert len(plan.due) == 1
        assert plan.due[0].graph_id == "db1.etl.nightly"
        assert len(transport.published) == 1

    async def test_catch_up_respects_skip(self) -> None:
        """A late `skip` task catches up to one run, not the whole backlog.

        Catch-up makes several occurrences due; `skip` refuses all but the first
        because a run is then active, so exactly one run is created.
        """
        repo = SchedulerFakeRepository(
            [
                make_task(
                    "solo",
                    overlap_policy="skip",
                    created_at=NOW - timedelta(minutes=20),
                )
            ]
        )
        transport = RecordingTransport()
        plan = await SchedulerTick(repo, transport).tick(NOW)

        assert len(plan.due) == 4
        assert plan.overlap_skipped == 3
        assert len(repo.graph_runs) == 1
        assert len(transport.published) == 1

    async def test_catch_up_enqueues_every_occurrence_for_queue(self) -> None:
        """`queue` honours the whole backlog, one run per missed occurrence."""
        repo = SchedulerFakeRepository(
            [
                make_task(
                    "solo",
                    overlap_policy="queue",
                    created_at=NOW - timedelta(minutes=20),
                )
            ]
        )
        transport = RecordingTransport()
        plan = await SchedulerTick(repo, transport).tick(NOW)

        assert len(plan.due) == 4
        assert plan.overlap_skipped == 0
        assert len(repo.graph_runs) == 4
        assert len(transport.published) == 4

    def test_plan_prefers_the_strictest_policy_default(self) -> None:
        # A task row with no policy (legacy row) plans as `skip`, the strictest
        # choice, so an unset value can never start an overlap by accident.
        task = make_task("solo", created_at=ONE_STEP_ANCHOR)
        del task["overlap_policy"]
        plan = plan_tick([task], [], NOW, "UTC")
        assert plan.due, "the interval task should be due"
        assert plan.due[0].overlap_policy == "skip"


class TestWorkerQueueDeferral:
    async def test_queue_run_defers_while_another_run_is_active(self, audit) -> None:
        repo = FakeRepository()
        repo.add_task("A")
        # An older run for the same graph is still running.
        repo.add_graph_run("gr_active", "id_A", state="running")
        repo.add_graph_run("gr_queued", "id_A", state="pending", overlap_policy="queue")

        executor = RecordingExecutor()
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr_queued", "id_A"))

        assert state == GraphState.PENDING
        assert executor.submissions == [], "a queued run must not execute yet"
        assert repo.graph_runs["gr_queued"]["state"] == "pending"

    async def test_a_deferred_queue_run_stays_pending_for_redelivery(self, audit) -> None:
        # The reconciler re-delivers every `pending` graph run
        # (worker_service: report.pending_graph_runs), so leaving the row pending
        # is the whole hand-off: no separate wake-up is invented here.
        repo = FakeRepository()
        repo.add_task("A")
        repo.add_graph_run("gr_active", "id_A", state="running")
        repo.add_graph_run("gr_queued", "id_A", state="pending", overlap_policy="queue")

        executor = RecordingExecutor()
        await GraphRunWorker(repo, executor).handle(GraphRunJob("gr_queued", "id_A"))

        assert repo.graph_runs["gr_queued"]["state"] == "pending"
        active = await repo.list_active_graph_runs("id_A")
        assert {r["id"] for r in active} == {"gr_active", "gr_queued"}

    async def test_queue_run_proceeds_when_no_other_run_is_active(self, audit) -> None:
        repo = FakeRepository()
        repo.add_task("A")
        repo.add_graph_run("gr_queued", "id_A", state="pending", overlap_policy="queue")

        executor = RecordingExecutor()
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr_queued", "id_A"))

        assert state == GraphState.SUCCESS
        assert [spec.name for spec, _ in executor.submissions] == ["A"]

    async def test_allow_run_proceeds_alongside_an_active_run(self, audit) -> None:
        repo = FakeRepository()
        repo.add_task("A")
        repo.add_graph_run("gr_active", "id_A", state="running")
        repo.add_graph_run("gr_allow", "id_A", state="pending", overlap_policy="allow")

        executor = RecordingExecutor()
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr_allow", "id_A"))

        assert state == GraphState.SUCCESS
        assert [spec.name for spec, _ in executor.submissions] == ["A"]

    async def test_skip_run_is_not_blocked_by_the_worker(self, audit) -> None:
        # `skip` is enforced at enqueue; if such a row exists it must still run
        # rather than stall forever.
        repo = FakeRepository()
        repo.add_task("A")
        repo.add_graph_run("gr_active", "id_A", state="running")
        repo.add_graph_run("gr_skip", "id_A", state="pending", overlap_policy="skip")

        executor = RecordingExecutor()
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr_skip", "id_A"))

        assert state == GraphState.SUCCESS
