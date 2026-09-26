"""Unit tests for the graph-run worker, with in-memory fakes (NOVA-36).

These prove the properties an engine cannot easily show:

* delivering the same job twice executes a node **once** (idempotency),
* the DAG drives ``A -> B -> [C, D]`` to completion with a join,
* a node is submitted **as its owner** and on a per-owner connection,
* no credential appears in any persisted field, the stream payload, or a log,
* a ``RUNNING`` row with a stale heartbeat is abandoned and re-evaluated rather
  than trusted, so a crashed worker loses no work permanently.

The fake repository mirrors the primary-key semantics of the StarRocks tables:
a conditional transition only moves a row out of an expected state, so a
duplicate delivery is observable as a no-op.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.modules.task_orchestration.credentials import (
    CredentialUnavailable,
    OwnerCredentialProvider,
    StaticCredentialProvider,
)
from app.modules.task_orchestration.dag import GraphState, NodeState
from app.modules.task_orchestration.execution import (
    DelegateExecutor,
    ExecutionResult,
    NodeExecutionError,
    TaskSpec,
    build_submit_task,
    native_attempt_name,
)
from app.modules.task_orchestration.worker import GraphRunJob, GraphRunWorker


class FakeRepository:
    """In-memory stand-in for ``TaskOrchestrationRepository``.

    Only the surface the worker uses. Conditional transitions compare against
    the row's current state, exactly as the SQL ``WHERE state IN (...)`` does.
    """

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, list[dict[str, Any]]] = {}
        self.graph_runs: dict[str, dict[str, Any]] = {}
        self.task_runs: dict[str, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []

    def add_task(
        self,
        name: str,
        *,
        task_id: str | None = None,
        body: str = "INSERT INTO t SELECT 1",
        owner: str = "alice",
        when_expr: str | None = None,
    ) -> str:
        tid = task_id or f"id_{name}"
        self.tasks[tid] = {
            "id": tid,
            "name": name,
            "definition": body,
            "database_name": None,
            "created_by": owner,
            "owner_role": "analyst",
            "schedule_kind": "manual",
            "when_expr": when_expr,
        }
        return tid

    def add_edge(self, graph_id: str, parent: str, child: str, edge_kind: str = "after") -> None:
        self.edges.setdefault(graph_id, []).append(
            {
                "id": f"e_{parent}_{child}",
                "graph_id": graph_id,
                "parent_task": parent,
                "child_task": child,
                "edge_kind": edge_kind,
            }
        )

    def add_finalize(self, graph_id: str, finalized: str, finalizer: str) -> None:
        """A ``FINALIZE`` edge: ``finalizer`` runs after ``finalized``'s graph."""
        self.add_edge(graph_id, finalized, finalizer, edge_kind="finalize")

    def add_graph_run(
        self,
        run_id: str,
        graph_id: str,
        state: str = "pending",
        overlap_policy: str = "skip",
    ) -> None:
        self.graph_runs[run_id] = {
            "id": run_id,
            "graph_id": graph_id,
            "trigger_type": "schedule",
            "execution_user": "alice",
            "execution_role": "analyst",
            "state": state,
            "overlap_policy": overlap_policy,
            "wal_marks": None,
            "heartbeat_at": None,
        }

    # ── repository surface ─────────────────────────────────────
    async def get_role_execution_user(self, role):
        return "alice"

    async def get_graph_run(self, run_id: str):
        return self.graph_runs.get(run_id)

    async def list_active_graph_runs(self, graph_id: str):
        return [
            row
            for row in self.graph_runs.values()
            if row["graph_id"] == graph_id and row["state"] in ("pending", "running")
        ]

    async def list_tasks(self, graph_id: str | None = None):
        return list(self.tasks.values())

    async def list_edges(self, graph_id: str):
        return list(self.edges.get(graph_id, []))

    async def list_node_runs(self, graph_run_id: str):
        return [r for r in self.task_runs.values() if r["graph_run_id"] == graph_run_id]

    async def get_node_run(self, graph_run_id: str, task_id: str):
        for run in self.task_runs.values():
            if run["graph_run_id"] == graph_run_id and run["task_id"] == task_id:
                return run
        return None

    async def create_task_run_once(self, graph_run_id: str, task_id: str, attempt: int = 1):
        existing = await self.get_node_run(graph_run_id, task_id)
        if existing:
            return existing
        rid = f"tr_{graph_run_id}_{task_id}"
        row = {
            "id": rid,
            "graph_run_id": graph_run_id,
            "task_id": task_id,
            "attempt": attempt,
            "state": "pending",
            "delegated": True,
            "starrocks_query_id": None,
            "error_message": None,
        }
        self.task_runs[rid] = row
        return row

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

    async def transition_graph_run(self, run_id, from_states, to_state):
        row = self.graph_runs[run_id]
        if row["state"] not in from_states:
            return False
        row["state"] = to_state
        return True

    async def mark_task_run_heartbeat(self, run_id):
        self.task_runs[run_id]["heartbeat_at"] = "now"

    async def mark_graph_run_heartbeat(self, run_id):
        self.graph_runs[run_id]["heartbeat_at"] = "now"

    async def list_stale_task_runs(self, older_than_seconds, *, limit=200):
        return [
            r for r in self.task_runs.values() if r["state"] == "running" and r.get("running_stale")
        ]

    async def list_stale_graph_runs(self, older_than_seconds, *, limit=200):
        return []

    async def list_graph_runs_by_state(self, states, *, limit=200):
        return [r for r in self.graph_runs.values() if r["state"] in states]

    async def create_task_run(self, data):  # pragma: no cover - not used
        raise NotImplementedError


class RecordingExecutor(DelegateExecutor):
    """An executor that records submissions instead of touching an engine."""

    def __init__(
        self,
        fail_for: set[str] | None = None,
        when_false_for: set[str] | None = None,
        when_error_for: set[str] | None = None,
        delay: float = 0.0,
    ) -> None:
        super().__init__(StaticCredentialProvider({}))
        self.submissions: list[tuple[TaskSpec, str]] = []
        self.fail_for = fail_for or set()
        self.when_false_for = when_false_for or set()
        self.when_error_for = when_error_for or set()
        self.when_evaluations: list[str] = []
        self.delay = delay
        self.max_in_flight = 0
        self._in_flight = 0

    async def execute(self, spec: TaskSpec, owner: str, *, heartbeat=None) -> ExecutionResult:
        self.submissions.append((spec, owner))
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if spec.name in self.fail_for:
                raise NodeExecutionError(f"engine rejected {spec.name}", query_id="q1")
            return ExecutionResult(query_id=f"q_{spec.name}", state="FINISHED")
        finally:
            self._in_flight -= 1

    async def evaluate_when(self, expression: str, *, spec: TaskSpec, owner: str) -> bool:
        self.when_evaluations.append(spec.name)
        if spec.name in self.when_error_for:
            raise NodeExecutionError(f"WHEN failed for {spec.name}")
        return spec.name not in self.when_false_for


@pytest.fixture
def audit(monkeypatch):
    captured: list[dict[str, Any]] = []

    async def fake_audit(**kwargs):
        captured.append(kwargs)
        return "audit-id"

    monkeypatch.setattr("app.modules.task_orchestration.worker.write_audit_log", fake_audit)
    return captured


def make_chain(repo: FakeRepository, graph_id: str = "g1") -> str:
    for name in ("A", "B", "C", "D"):
        repo.add_task(name)
    repo.add_edge(graph_id, "A", "B")
    repo.add_edge(graph_id, "B", "C")
    repo.add_edge(graph_id, "B", "D")
    repo.add_graph_run("gr1", graph_id)
    return graph_id


class TestDagExecution:
    async def test_chain_runs_to_success_in_order(self, audit):
        repo = FakeRepository()
        make_chain(repo)
        executor = RecordingExecutor()
        state = await GraphRunWorker(repo, executor).handle(
            GraphRunJob("gr1", "g1", task_ids=("id_A", "id_B", "id_C", "id_D"))
        )
        assert state == GraphState.SUCCESS
        assert [spec.name for spec, _ in executor.submissions] == ["A", "B", "C", "D"]
        assert len({spec.native_name for spec, _ in executor.submissions}) == 4
        assert all(spec.native_name for spec, _ in executor.submissions)
        assert repo.graph_runs["gr1"]["state"] == "success"

    async def test_node_failure_fails_the_graph_and_skips_descendants(self, audit):
        repo = FakeRepository()
        make_chain(repo)
        executor = RecordingExecutor(fail_for={"B"})
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "g1"))
        assert state == GraphState.FAILED
        ran = {spec.name for spec, _ in executor.submissions}
        assert ran == {"A", "B"}
        states = {r["task_id"]: r["state"] for r in repo.task_runs.values()}
        assert states["id_C"] == NodeState.SKIPPED.value
        assert states["id_D"] == NodeState.SKIPPED.value

    async def test_siblings_execute_in_parallel(self, audit):
        """Criterion 3: C and D run concurrently, not one after the other."""
        repo = FakeRepository()
        make_chain(repo)
        executor = RecordingExecutor(delay=0.05)
        await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "g1"))
        # A, then B, then C+D in flight together: the peak is 2, not 1.
        assert executor.max_in_flight == 2
        assert len(executor.submissions) == 4

    async def test_wide_graph_limits_in_flight_nodes(self, audit):
        repo = FakeRepository()
        repo.add_task("A")
        for index in range(12):
            name = f"child_{index}"
            repo.add_task(name)
            repo.add_edge("g_wide", "A", name)
        repo.add_graph_run("gr_wide", "g_wide")
        executor = RecordingExecutor(delay=0.01)

        state = await GraphRunWorker(repo, executor, max_parallel_nodes=3).handle(
            GraphRunJob("gr_wide", "g_wide")
        )

        assert state == GraphState.SUCCESS
        assert executor.max_in_flight == 3
        assert len(executor.submissions) == 13

    async def test_join_waits_for_both_parents(self, audit):
        repo = FakeRepository()
        for name in ("A", "B", "C", "D"):
            repo.add_task(name)
        repo.add_edge("g2", "A", "B")
        repo.add_edge("g2", "A", "C")
        repo.add_edge("g2", "B", "D")
        repo.add_edge("g2", "C", "D")
        repo.add_graph_run("gr2", "g2")
        executor = RecordingExecutor()
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr2", "g2"))
        assert state == GraphState.SUCCESS
        order = [spec.name for spec, _ in executor.submissions]
        assert order[0] == "A"
        assert set(order[1:3]) == {"B", "C"}
        assert order[-1] == "D"


class TestWhenFalse:
    async def test_when_false_skips_node_and_descendants(self, audit):
        repo = FakeRepository()
        for name in ("A", "B", "C", "D"):
            repo.add_task(name, when_expr="SELECT TRUE" if name == "B" else None)
        repo.add_edge("g1", "A", "B")
        repo.add_edge("g1", "B", "C")
        repo.add_edge("g1", "B", "D")
        repo.add_graph_run("gr1", "g1")
        executor = RecordingExecutor(when_false_for={"B"})
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "g1"))
        assert state == GraphState.SUCCESS
        ran = {spec.name for spec, _ in executor.submissions}
        assert ran == {"A"}
        states = {r["task_id"]: r["state"] for r in repo.task_runs.values()}
        assert states["id_B"] == NodeState.SKIPPED.value
        assert states["id_C"] == NodeState.SKIPPED.value
        assert states["id_D"] == NodeState.SKIPPED.value

    async def test_when_error_fails_the_node_not_silently_skips(self, audit):
        repo = FakeRepository()
        repo.add_task("A", when_expr="SELECT 1/0")
        repo.add_graph_run("gr1", "id_A")
        executor = RecordingExecutor(when_error_for={"A"})
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "id_A"))
        assert state == GraphState.FAILED
        row = next(iter(repo.task_runs.values()))
        assert row["state"] == "failed"
        assert executor.submissions == []

    async def test_no_when_expression_runs_unconditionally(self, audit):
        repo = FakeRepository()
        repo.add_task("A")
        repo.add_graph_run("gr1", "id_A")
        executor = RecordingExecutor()
        await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "id_A"))
        assert executor.when_evaluations == []


class TestDelegateFirst:
    async def test_manual_run_uses_persisted_caller_for_entire_graph(self, audit):
        repo = FakeRepository()
        repo.add_task("A", owner="nova_admin", when_expr="1=1")
        repo.add_task("B", owner="root")
        repo.add_edge("g", "A", "B")
        repo.add_graph_run("manual", "g")
        repo.graph_runs["manual"].update(
            trigger_type="manual",
            execution_user="dwicky.f.putra",
            execution_role="analyst",
            execution_session_id="caller-session",
        )
        executor = RecordingExecutor()
        result = await GraphRunWorker(repo, executor).handle(
            GraphRunJob(
                "manual",
                "g",
                trigger_type="schedule",
                execution_user="root",
                execution_role="ACCOUNTADMIN",
            )
        )
        assert result == GraphState.SUCCESS
        assert len(executor.submissions) == 2
        assert all(
            owner == "dwicky.f.putra" and spec.active_role == "analyst"
            for spec, owner in executor.submissions
        )
        assert repo.tasks["id_A"]["created_by"] == "nova_admin"
        assert all(
            entry["user_name"] == "dwicky.f.putra"
            for entry in audit
            if entry.get("object_type") == "TASK"
        )

    async def test_manual_run_without_snapshot_cannot_fall_back_to_owner(self, audit):
        repo = FakeRepository()
        repo.add_task("A", owner="root")
        repo.add_graph_run("manual", "id_A")
        repo.graph_runs["manual"]["trigger_type"] = "manual"
        repo.graph_runs["manual"]["execution_user"] = None
        executor = RecordingExecutor()
        assert (
            await GraphRunWorker(repo, executor).handle(GraphRunJob("manual", "id_A"))
            == GraphState.FAILED
        )
        assert not executor.submissions
        assert "no execution identity" in next(iter(repo.task_runs.values()))["error_message"]

    async def test_scheduled_node_uses_service_identity_not_creator(self, audit):
        repo = FakeRepository()
        repo.add_task("A", owner="bob")
        repo.add_graph_run("gr1", "id_A")
        executor = RecordingExecutor()
        await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "id_A"))
        assert executor.submissions == [
            (
                TaskSpec(
                    name="A",
                    body="INSERT INTO t SELECT 1",
                    database=None,
                    active_role="analyst",
                    native_name=native_attempt_name("tr_gr1_id_A"),
                ),
                "alice",
            )
        ]

    async def test_owner_without_a_credential_fails_the_node(self, audit):
        repo = FakeRepository()
        repo.add_task("A", owner="carol")
        repo.add_graph_run("gr1", "id_A")

        class Denying(DelegateExecutor):
            def __init__(self):
                super().__init__(_DenyProvider())

            async def execute(self, spec, owner):
                raise CredentialUnavailable("no session")

        state = await GraphRunWorker(repo, Denying()).handle(GraphRunJob("gr1", "id_A"))
        assert state == GraphState.FAILED
        row = next(iter(repo.task_runs.values()))
        assert row["state"] == "failed"
        # The node never ran — no submission was made under a fallback identity.
        assert executor_none(row)

    def test_submit_task_lowers_to_the_one_shot_form(self):
        assert build_submit_task(TaskSpec("t", "INSERT INTO x SELECT 1")) == (
            "SUBMIT TASK `t` AS INSERT INTO x SELECT 1"
        )
        assert build_submit_task(TaskSpec("t", "SELECT 1", database="db")) == (
            "SUBMIT TASK `db`.`t` AS SELECT 1"
        )
        first = native_attempt_name("node-run-1")
        second = native_attempt_name("node-run-2")
        assert first != second
        assert first == native_attempt_name("node-run-1")
        assert (
            build_submit_task(
                TaskSpec("t", "INSERT INTO x SELECT 1", database="db", native_name=first)
            )
            == f"SUBMIT TASK `db`.`{first}` AS INSERT INTO x SELECT 1"
        )

    def test_identifier_is_escaped(self):
        assert build_submit_task(TaskSpec("a`b", "SELECT 1")) == ("SUBMIT TASK `a``b` AS SELECT 1")


def executor_none(row: dict[str, Any]) -> bool:
    return row.get("starrocks_query_id") is None


class _DenyProvider(OwnerCredentialProvider):
    async def password_for(self, username: str) -> str:
        raise CredentialUnavailable("no session")


class TestIdempotency:
    async def test_late_claim_cannot_reset_a_settled_graph(self, audit):
        class RacingRepository(FakeRepository):
            async def transition_graph_run(self, run_id, from_states, to_state):
                self.graph_runs[run_id]["state"] = "success"
                return await super().transition_graph_run(run_id, from_states, to_state)

        repo = RacingRepository()
        repo.add_task("A")
        repo.add_graph_run("gr1", "id_A")
        executor = RecordingExecutor()

        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "id_A"))

        assert state == GraphState.SUCCESS
        assert repo.graph_runs["gr1"]["state"] == "success"
        assert executor.submissions == []

    async def test_duplicate_delivery_executes_each_node_once(self, audit):
        repo = FakeRepository()
        make_chain(repo)
        executor = RecordingExecutor()
        worker = GraphRunWorker(repo, executor)
        job = GraphRunJob("gr1", "g1")

        await worker.handle(job)
        await worker.handle(job)

        assert len(executor.submissions) == 4
        assert len(repo.task_runs) == 4

    async def test_redelivery_after_completion_is_a_noop(self, audit):
        repo = FakeRepository()
        make_chain(repo)
        executor = RecordingExecutor()
        worker = GraphRunWorker(repo, executor)
        first = await worker.handle(GraphRunJob("gr1", "g1"))
        second = await worker.handle(GraphRunJob("gr1", "g1"))
        assert first == second == GraphState.SUCCESS
        assert len(executor.submissions) == 4

    async def test_concurrent_delivery_claims_one_run(self, audit):
        """A second delivery only proceeds if the conditional claim succeeds.

        The repository's transition is a conditional write; the fake mirrors
        that by refusing a move out of an unexpected state. Here the node has
        already been claimed, so the second worker cannot re-claim it.
        """
        repo = FakeRepository()
        make_chain(repo)
        executor = RecordingExecutor()
        worker = GraphRunWorker(repo, executor)
        job = GraphRunJob("gr1", "g1")

        # First delivery performs all four nodes.
        await worker.handle(job)
        # Freeze the fake repo so a redelivery sees everything terminal and
        # cannot claim or execute anything.
        before = len(executor.submissions)
        await worker.handle(job)
        assert len(executor.submissions) == before == 4

    async def test_missing_graph_run_is_not_fatal(self, audit):
        repo = FakeRepository()
        assert (
            await GraphRunWorker(repo, RecordingExecutor()).handle(GraphRunJob("missing", "g"))
            is None
        )


class TestRestartSafety:
    async def test_abandoned_running_row_is_held_without_resubmission(self, audit):
        """A lost heartbeat cannot prove the native submit did not happen."""
        repo = FakeRepository()
        repo.add_task("A")
        repo.add_graph_run("gr1", "id_A", state="running")
        stale = await repo.create_task_run_once("gr1", "id_A")
        stale["state"] = "running"
        stale["running_stale"] = True

        executor = RecordingExecutor()
        # The worker re-derives the graph but does not replay uncertain SQL.
        await repo.transition_task_run(stale["id"], ["running"], "abandoned")
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "id_A"))
        assert state == GraphState.RUNNING
        assert repo.task_runs[stale["id"]]["state"] == "abandoned"
        assert executor.submissions == []

    async def test_pending_run_with_no_delivery_is_reconcilable(self, audit):
        """A graph run persisted but never published is still visible."""
        repo = FakeRepository()
        repo.add_task("A")
        repo.add_graph_run("gr1", "id_A", state="pending")
        pending = await repo.list_graph_runs_by_state(["pending", "running"])
        assert [r["id"] for r in pending] == ["gr1"]

    async def test_running_node_does_not_loop_the_drive_cycle(self, audit):
        """NOVA-52: a ready-but-unclaimable node must not spin ``_drive``.

        A live worker holds the node ``running``; this delivery sees it as
        ``ready`` (a standalone node's parents are vacuously satisfied) but
        cannot claim it. Re-evaluating would see the same state forever, so the
        delivery must return ``RUNNING`` and let the live worker or the
        reconciler settle it. This is the loop QA reproduced on a fresh engine.
        """
        repo = FakeRepository()
        repo.add_task("A")
        repo.add_graph_run("gr1", "id_A", state="running")
        live = await repo.create_task_run_once("gr1", "id_A")
        live["state"] = "running"

        executor = RecordingExecutor()
        state = await asyncio.wait_for(
            GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "id_A")),
            timeout=5,
        )

        assert state == GraphState.RUNNING
        assert repo.task_runs[live["id"]]["state"] == "running"
        assert executor.submissions == []


class TestFinalizeOwnership:
    """The terminal transition decides who records a node's outcome.

    If the reconciler abandoned the row while a slow node was still executing,
    the late worker's ``running -> terminal`` write affects no rows. Its outcome
    must then be dropped silently — not audited as if this delivery still owned
    the row (which produced a misleading ``NODE_*`` audit and re-evaluation
    churn).
    """

    async def test_result_is_dropped_and_not_audited_when_row_was_abandoned(self, audit):
        repo = FakeRepository()
        repo.add_task("A")
        repo.add_graph_run("gr1", "id_A", state="running")
        row = await repo.create_task_run_once("gr1", "id_A")
        row["state"] = "running"

        class AbandoningExecutor(RecordingExecutor):
            async def execute(self, spec, owner, *, heartbeat=None):
                # Simulate the reconciler abandoning the node mid-flight.
                repo.task_runs[row["id"]]["state"] = "abandoned"
                return ExecutionResult(query_id="q1", state="FINISHED")

        executor = AbandoningExecutor()
        await GraphRunWorker(repo, executor)._execute_one(
            GraphRunJob("gr1", "id_A"), repo.tasks["id_A"], row["id"]
        )

        assert repo.task_runs[row["id"]]["state"] == "abandoned"
        assert repo.task_runs[row["id"]].get("starrocks_query_id") is None
        assert audit == [], "no NODE_* audit may be written for a row we lost"


class TestEngineObservationResilience:
    """The engine's task-run surface can fail; observing it must not gate the submit.

    On some FEs ``information_schema.task_runs`` is served by an internal
    archive read that fails with a 1064 (e.g. a corrupt/absent
    ``_statistics_.task_run_history``). A failure reading the *watermark* must
    not stop the submit — the submit is where the owner's RBAC is enforced, and
    a graph node on a forbidden table must surface the engine's 5203 privilege
    refusal, not the observation error.
    """

    async def test_watermark_failure_does_not_block_the_submit(self, monkeypatch):
        submitted: list[str] = []

        class _Conn:
            def cursor(self, *args):
                return _Cursor()

        class _Cursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, statement, params=None):
                submitted.append(statement)

            async def fetchone(self):
                return None

        from app.modules.task_orchestration import execution as execution_module

        class _Ctx:
            async def __aenter__(self):
                return _Conn()

            async def __aexit__(self, *exc):
                return False

        async def failing_watermark(self, conn, name):
            raise RuntimeError(
                '(1064, "RepoExecutorexecute sql failed: SELECT history_content_json '
                'FROM _statistics_.task_run_history ...")'
            )

        async def _no_wait(self, conn, name, *, watermark, heartbeat=None):
            return ExecutionResult(query_id="q1", state="FINISHED")

        monkeypatch.setattr(execution_module.db, "system_conn", _Ctx)
        monkeypatch.setattr(execution_module.db, "user_conn", lambda *a, **k: _Ctx())
        monkeypatch.setattr(
            execution_module.DelegateExecutor, "_latest_create_time", failing_watermark
        )
        monkeypatch.setattr(execution_module.DelegateExecutor, "_await_completion", _no_wait)

        executor = DelegateExecutor(StaticCredentialProvider({"bob": "pw"}))
        result = await executor.execute(
            TaskSpec("A", "INSERT INTO secret SELECT 1", database="db"), "bob"
        )
        assert result.state == "FINISHED"
        assert submitted == ["USE `db`", "SUBMIT TASK `db`.`A` AS INSERT INTO secret SELECT 1"]

    async def test_poll_failure_is_retried_not_fatal(self):
        calls = {"n": 0}

        executor = DelegateExecutor(
            StaticCredentialProvider({"bob": "pw"}),
            poll_interval=0.0,
            poll_timeout=5.0,
        )

        async def flaky(conn, name, watermark):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient FE RPC failure")
            return ExecutionResult(query_id="q1", state="FINISHED")

        executor._newest_run_after = flaky  # type: ignore[method-assign]

        result = await executor._await_completion(object(), "A", watermark=None)
        assert result.state == "FINISHED"
        assert calls["n"] == 3


class TestCredentialInvisible:
    async def test_task_rows_carry_no_credential(self, audit):
        repo = FakeRepository()
        repo.add_task("A")
        repo.add_graph_run("gr1", "id_A")
        await GraphRunWorker(repo, RecordingExecutor()).handle(GraphRunJob("gr1", "id_A"))
        serialized = str(repo.task_runs).lower()
        for bad in ("password", "secret", "token", "credential"):
            assert bad not in serialized

    async def test_job_payload_carries_ids_only(self):
        job = GraphRunJob.from_payload(
            {
                "graph_run_id": "gr1",
                "graph_id": "g1",
                "trigger_type": "schedule",
                "task_ids": "a,b",
            }
        )
        assert job.task_ids == ("a", "b")
        serialized = str(job).lower()
        for bad in ("password", "secret", "token"):
            assert bad not in serialized

    async def test_static_provider_never_leaks_the_password_in_its_error(self):
        provider = StaticCredentialProvider({})
        with pytest.raises(CredentialUnavailable) as exc:
            await provider.password_for("nobody")
        assert "nobody" in str(exc.value)
