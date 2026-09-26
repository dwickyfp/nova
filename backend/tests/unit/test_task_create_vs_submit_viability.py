"""A `CREATE TASK`-created row and a `SUBMIT TASK`-created row run identically.

The grammar accepts `(SUBMIT | CREATE) TASK`, and stage 2 (PR 3a) intercepts
`CREATE TASK` so it never reaches the engine. At runtime the distinction must be
invisible: both produce a `CONFIG_TASKS` row with the same shape, and the worker
executes whichever row it finds through the single lowering point
(`execution.build_submit_task`).

These tests drive the worker with rows built by the real `CREATE TASK` lowering
and with rows built the way the legacy `SUBMIT TASK` path builds them, and assert
the observable execution is the same. No engine.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.task_orchestration.dag import GraphState
from app.modules.task_orchestration.ddl import parse_create_task
from app.modules.task_orchestration.worker import GraphRunJob, GraphRunWorker
from tests.unit.test_task_orchestration_worker import FakeRepository, RecordingExecutor


@pytest.fixture
def audit(monkeypatch):
    """Silence audit writes; the fake repository is what the tests assert on."""
    captured: list[dict[str, Any]] = []

    async def fake_audit(**kwargs):
        captured.append(kwargs)
        return "audit-id"

    monkeypatch.setattr("app.modules.task_orchestration.worker.write_audit_log", fake_audit)
    return captured


def _row_from_create_task(sql: str, *, task_id: str) -> dict:
    """The metadata shape stage 2 writes for a `CREATE TASK` statement."""
    lowered = parse_create_task(sql, database="db1", timezone="UTC")
    return {
        "id": task_id,
        "name": lowered.name,
        "definition": lowered.body,
        "database_name": lowered.database_name,
        "created_by": "alice",
        "owner_role": "analyst",
        "schedule_kind": lowered.schedule_kind,
        "schedule_expr": lowered.schedule_expr,
        "when_expr": lowered.when_expr,
        "overlap_policy": lowered.overlap_policy,
    }


def _row_from_submit_task(name: str, body: str, *, task_id: str) -> dict:
    """The legacy `SUBMIT TASK` task row: same columns, no Nova clauses."""
    return {
        "id": task_id,
        "name": name,
        "definition": body,
        "database_name": "db1",
        "created_by": "alice",
        "owner_role": "analyst",
        "schedule_kind": "manual",
        "schedule_expr": None,
        "when_expr": None,
        "overlap_policy": "skip",
    }


class TestCreateAndSubmitRowsExecuteIdentically:
    async def test_same_body_produces_the_same_submission(self, audit) -> None:
        body = "INSERT INTO t SELECT 1"

        create_repo = FakeRepository()
        create_repo.tasks["id_x"] = _row_from_create_task(
            f"CREATE TASK x AS {body}", task_id="id_x"
        )
        create_repo.add_graph_run("gr_c", "id_x")

        submit_repo = FakeRepository()
        submit_repo.tasks["id_x"] = _row_from_submit_task("x", body, task_id="id_x")
        submit_repo.add_graph_run("gr_s", "id_x")

        create_exec = RecordingExecutor()
        submit_exec = RecordingExecutor()
        create_state = await GraphRunWorker(create_repo, create_exec).handle(
            GraphRunJob("gr_c", "id_x")
        )
        submit_state = await GraphRunWorker(submit_repo, submit_exec).handle(
            GraphRunJob("gr_s", "id_x")
        )

        assert create_state == submit_state == GraphState.SUCCESS
        create_spec = create_exec.submissions[0][0]
        submit_spec = submit_exec.submissions[0][0]
        assert create_spec.body == submit_spec.body
        assert create_spec.name == submit_spec.name
        assert create_spec.database == submit_spec.database

    async def test_create_task_clauses_are_honoured_at_runtime(self, audit) -> None:
        """A `CREATE TASK` row with an `AFTER` edge runs in dependency order."""
        repo = FakeRepository()
        repo.tasks["id_b"] = _row_from_create_task(
            "CREATE TASK b AFTER a WHEN flag = TRUE AS INSERT INTO t SELECT 1",
            task_id="id_b",
        )
        repo.tasks["id_a"] = _row_from_create_task(
            "CREATE TASK a AS INSERT INTO t SELECT 1", task_id="id_a"
        )
        repo.add_edge("g1", "a", "b")
        repo.add_graph_run("gr1", "g1")

        executor = RecordingExecutor()
        state = await GraphRunWorker(repo, executor).handle(GraphRunJob("gr1", "g1"))

        assert state == GraphState.SUCCESS
        # `a` before `b`; `b`'s WHEN was evaluated (its metadata survived).
        assert [spec.name for spec, _ in executor.submissions] == ["a", "b"]
        assert executor.when_evaluations == ["b"]
        assert repo.tasks["id_b"]["when_expr"] == "flag = TRUE"
