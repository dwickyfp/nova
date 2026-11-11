"""A lost native result must not admit a replacement mutation."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from asyncmy.errors import OperationalError, ProgrammingError

from app.modules.task_orchestration.credentials import StaticCredentialProvider
from app.modules.task_orchestration.dag import GraphState
from app.modules.task_orchestration.execution import DelegateExecutor, ExecutionResult
from app.modules.task_orchestration.worker import GraphRunJob, GraphRunWorker
from tests.unit.test_task_orchestration_worker import FakeRepository
from tests.unit.test_task_worker_impersonation import _Conn, _Cursor


@pytest.mark.parametrize(
    "fault",
    [
        "submit_disconnect",
        "submit_and_close_disconnect",
        "response_disconnect",
        "close_disconnect",
        "timeout",
        "heartbeat",
        "unknown_state",
    ],
)
async def test_uncertain_native_attempt_holds_graph_and_never_submits_again(monkeypatch, fault):
    repo = FakeRepository()
    root = repo.add_task("root")
    repo.tasks[root]["database_name"] = "warehouse"
    repo.add_task("child")
    repo.add_task("finalizer")
    repo.add_edge(root, "root", "child")
    repo.add_finalize(root, "root", "finalizer")
    repo.add_graph_run("first", root)
    statements = []

    class FaultCursor(_Cursor):
        async def execute(self, statement, params=None):
            await super().execute(statement, params)
            if statement.startswith("SUBMIT TASK") and fault in {
                "submit_disconnect", "submit_and_close_disconnect",
            }:
                raise OperationalError(2013, "private-transport-canary")

        async def fetchone(self):
            if self.last.startswith("SUBMIT TASK") and fault == "response_disconnect":
                raise ConnectionResetError("private-response-canary")
            return await super().fetchone()

    @asynccontextmanager
    async def owner_connection(owner, session_id=None):
        assert owner == "alice"
        try:
            yield _Conn(FaultCursor(statements))
        finally:
            if fault in {"close_disconnect", "submit_and_close_disconnect"}:
                raise ConnectionResetError("private-close-canary")

    @asynccontextmanager
    async def observer_connection():
        yield object()

    executor = DelegateExecutor(StaticCredentialProvider({}), poll_timeout=0, poll_interval=0)
    monkeypatch.setattr(executor, "_owner_conn", owner_connection)
    monkeypatch.setattr(
        "app.modules.task_orchestration.execution.db.system_conn", observer_connection
    )
    monkeypatch.setattr(executor, "_latest_create_time", AsyncMock(return_value=None))
    observed = (
        None
        if fault == "timeout"
        else ExecutionResult(
            "native-receipt-id", "UNRECOGNIZED" if fault == "unknown_state" else "RUNNING"
        )
    )
    monkeypatch.setattr(executor, "_newest_run_after", AsyncMock(return_value=observed))
    if fault == "heartbeat":
        monkeypatch.setattr(
            repo,
            "mark_task_run_heartbeat",
            AsyncMock(side_effect=RuntimeError("private-heartbeat-canary")),
        )
    audit = AsyncMock()
    monkeypatch.setattr("app.modules.task_orchestration.worker.write_audit_log", audit)
    worker = GraphRunWorker(repo, executor)
    job = GraphRunJob("first", root)
    for _ in range(3):
        assert await worker.handle(job) == GraphState.RUNNING
    row = await repo.get_node_run("first", root)
    assert row["state"] == "abandoned"
    assert "outcome is unknown" in row["error_message"]
    assert "canary" not in str(repo.task_runs) + str(audit.call_args_list)
    if fault in {"heartbeat", "unknown_state"}:
        assert row["starrocks_query_id"] == "native-receipt-id"
    assert sum(s.startswith("SUBMIT TASK") for s in statements) == 1
    assert not any(s.startswith("DROP TASK") for s in statements)
    assert {r["task_id"] for r in repo.task_runs.values() if r["state"] != "pending"} == {root}
    repo.add_graph_run("replacement", root, overlap_policy="queue")
    assert await worker.handle(GraphRunJob("replacement", root)) == GraphState.PENDING
    assert sum(s.startswith("SUBMIT TASK") for s in statements) == 1


@pytest.mark.parametrize(
    "error", [ProgrammingError(1064, "syntax refused"), OperationalError(5203, "access refused")]
)
async def test_explicit_submission_rejection_remains_a_known_failure(monkeypatch, error):
    from app.modules.task_orchestration.execution import NodeExecutionError, NodeOutcomeUnknown

    class RefusingCursor(_Cursor):
        async def execute(self, statement, params=None):
            raise error

    executor = DelegateExecutor(StaticCredentialProvider({}))
    with pytest.raises(NodeExecutionError) as caught:
        await executor._submit(
            _Conn(RefusingCursor([])), "SUBMIT TASK rejected AS INSERT INTO t SELECT 1"
        )
    assert not isinstance(caught.value, NodeOutcomeUnknown)


async def test_even_refusal_shaped_error_after_acknowledgement_is_unknown():
    from app.modules.task_orchestration.execution import NodeOutcomeUnknown

    class BrokenResponse(_Cursor):
        async def fetchone(self):
            raise ProgrammingError(1064, "private-response-canary")

    executor = DelegateExecutor(StaticCredentialProvider({}))
    with pytest.raises(NodeOutcomeUnknown):
        await executor._submit(
            _Conn(BrokenResponse([])), "SUBMIT TASK accepted AS INSERT INTO t SELECT 1"
        )
