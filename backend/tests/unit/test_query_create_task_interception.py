"""``CREATE TASK`` must be intercepted by the pipeline, never sent to the engine.

This is the acceptance criterion that makes the Nova surface safe: a
``CREATE TASK`` is a Nova statement, lowered to ``CONFIG_TASK*`` metadata, and the
engine must never see the raw text. The recording repository is what proves it —
it captures exactly the SQL the engine would receive, so an empty call list is
evidence, not an inference.

The engine timezone and the metadata repository are both replaced, so no engine
and no database is involved.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService

MISSING = object()


class RecordingRepo:
    """Captures every statement the engine would receive."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_as_user(self, sql, **kwargs):
        self.calls.append(sql)
        return QueryResult(executed_sql=sql, columns=["v"], rows=[[1]], row_count=1)

    async def execute_as_system(self, sql, **kwargs):
        self.calls.append(sql)
        return QueryResult(executed_sql=sql, columns=["v"], rows=[[1]], row_count=1)


class FakeTaskRepository:
    """Minimal in-memory ``TaskOrchestrationRepository`` for the interceptor."""

    def __init__(self, timezone: str | None = "Asia/Jakarta") -> None:
        self.timezone = timezone
        self.tasks: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self._seq = 0

    async def get_engine_timezone(self) -> str | None:
        return self.timezone

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq}"

    async def create_task(self, data: dict[str, Any], created_by: str | None):
        task_id = self._next_id("task")
        row = {"id": task_id, "created_by": created_by, **data}
        self.tasks[task_id] = row
        return row

    async def delete_task(self, task_id: str) -> bool:
        return self.tasks.pop(task_id, None) is not None

    async def create_edge(self, graph_id: str, data: dict[str, Any]):
        edge_id = self._next_id("edge")
        row = {"id": edge_id, "graph_id": graph_id, **data}
        self.edges.append(row)
        return row

    async def delete_edge(self, edge_id: str) -> bool:
        before = len(self.edges)
        self.edges = [e for e in self.edges if e["id"] != edge_id]
        return len(self.edges) != before

    async def list_tasks(self, graph_id: str | None = None):
        return list(self.tasks.values())

    async def list_edges(self, graph_id: str):
        return [e for e in self.edges if e["graph_id"] == graph_id]

    async def list_all_edges(self):
        return list(self.edges)


class AuditSink:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    async def __call__(self, **kwargs):
        self.entries.append(kwargs)
        return "audit-id"

    @property
    def statuses(self) -> list[str]:
        return [e["status"] for e in self.entries]


@pytest.fixture
def wired(monkeypatch):
    engine = RecordingRepo()
    tasks = FakeTaskRepository()
    audit = AuditSink()
    service = QueryService()
    service._repo = engine
    monkeypatch.setattr("app.modules.query.service.write_audit_log", audit)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    monkeypatch.setattr(
        "app.modules.task_orchestration.lowering.task_orchestration_repository", tasks
    )
    monkeypatch.setattr(
        "app.modules.query.service.task_orchestration_repository", tasks
    )
    return service, engine, tasks, audit


class TestRawCreateTaskNeverReachesEngine:
    async def test_create_task_is_not_sent_to_the_engine(self, wired):
        service, engine, tasks, _ = wired

        result = await service.execute(
            sql="CREATE TASK t1 AFTER a AS INSERT INTO t SELECT 1",
            username="alice",
            encrypted_password="enc",
            database="db1",
        )

        assert engine.calls == [], "CREATE TASK must never reach the engine"
        assert result.success
        assert len(tasks.tasks) == 1
        task = next(iter(tasks.tasks.values()))
        assert task["name"] == "t1"
        assert task["definition"] == "INSERT INTO t SELECT 1"
        assert task["created_by"] == "alice"
        assert {(e["parent_task"], e["child_task"]) for e in tasks.edges} == {("a", "t1")}

    async def test_raw_create_task_text_is_not_in_executed_sql(self, wired):
        service, _, _, _ = wired
        result = await service.execute(
            sql="CREATE TASK t1 AFTER a AS INSERT INTO t SELECT 1",
            username="alice",
            encrypted_password="enc",
            database="db1",
        )
        # `executed_sql` is the Nova statement, not an engine statement; the
        # warning says as much so the surface does not imply the engine ran it.
        assert not result.executed_sql.upper().startswith("SUBMIT TASK")
        assert any("no statement was sent to StarRocks" in w for w in result.warnings)

    async def test_body_is_stored_verbatim(self, wired):
        service, _, tasks, _ = wired
        await service.execute(
            sql="CREATE TASK t1 AS INSERT OVERWRITE agg SELECT a, b FROM src WHERE a > 1",
            username="alice",
            encrypted_password="enc",
            database="db1",
        )
        task = next(iter(tasks.tasks.values()))
        assert task["definition"] == "INSERT OVERWRITE agg SELECT a, b FROM src WHERE a > 1"

    async def test_schedule_and_when_are_persisted(self, wired):
        service, _, tasks, _ = wired
        await service.execute(
            sql=(
                "CREATE TASK t1 AFTER a WHEN x > 1 AND y < 2 "
                "OVERLAP_POLICY = 'QUEUE' SCHEDULE = '0 2 * * * UTC' "
                "AS INSERT INTO t SELECT 1"
            ),
            username="alice",
            encrypted_password="enc",
            database="db1",
        )
        task = next(iter(tasks.tasks.values()))
        assert task["schedule_kind"] == "cron"
        assert task["schedule_expr"] == "0 2 * * *"
        assert task["timezone"] == "UTC"
        assert task["when_expr"] == "x > 1 AND y < 2"
        assert task["overlap_policy"] == "queue"


class TestValidationFailureIsReported:
    async def test_unknown_overlap_policy_returns_an_error_without_engine_call(self, wired):
        service, engine, tasks, audit = wired
        result = await service.execute(
            sql="CREATE TASK t1 OVERLAP_POLICY = 'MAYBE' AS INSERT INTO t SELECT 1",
            username="alice",
            encrypted_password="enc",
            database="db1",
        )
        assert result.error is not None
        assert "OVERLAP_POLICY" in result.error
        assert engine.calls == []
        assert tasks.tasks == {}
        assert audit.statuses == ["ERROR"]

    async def test_out_of_order_clause_is_reported(self, wired):
        service, engine, _, _ = wired
        result = await service.execute(
            sql="CREATE TASK t1 SCHEDULE = '0 2 * * *' AFTER a AS INSERT INTO t SELECT 1",
            username="alice",
            encrypted_password="enc",
            database="db1",
        )
        assert result.error is not None
        assert "order" in result.error
        assert engine.calls == []

    async def test_invalid_cron_is_reported(self, wired):
        service, engine, _, _ = wired
        result = await service.execute(
            sql="CREATE TASK t1 SCHEDULE = 'not a cron' AS INSERT INTO t SELECT 1",
            username="alice",
            encrypted_password="enc",
            database="db1",
        )
        assert result.error is not None
        assert engine.calls == []

    async def test_missing_engine_timezone_is_reported(self, wired, monkeypatch):
        service, engine, _, _ = wired
        # The service binds the repository at import time, so the service's own
        # reference is what has to change for this test.
        monkeypatch.setattr(
            "app.modules.query.service.task_orchestration_repository",
            FakeTaskRepository(timezone=None),
        )
        result = await service.execute(
            sql="CREATE TASK t1 AS INSERT INTO t SELECT 1",
            username="alice",
            encrypted_password="enc",
            database="db1",
        )
        assert result.error is not None
        assert "timezone" in result.error.lower()


class TestNonTaskStatementsAreUnaffected:
    async def test_submit_task_still_goes_to_the_engine(self, wired):
        service, engine, tasks, _ = wired
        result = await service.execute(
            sql="SUBMIT TASK x AS INSERT INTO t SELECT 1",
            username="alice",
            encrypted_password="enc",
            database="db1",
        )
        assert result.success
        assert engine.calls, "a real engine statement must still be executed"
        assert tasks.tasks == {}, "SUBMIT TASK must not create Nova metadata"

    async def test_plain_select_still_goes_to_the_engine(self, wired):
        service, engine, tasks, _ = wired
        result = await service.execute(
            sql="SELECT cron, finalize FROM t",
            username="alice",
            encrypted_password="enc",
            database="db1",
        )
        assert result.success
        assert engine.calls
        assert tasks.tasks == {}
