import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.query.provenance import DatasetQuery, current_dataset_query, dataset_query
from app.modules.query.repository import QueryRepository, QueryResult
from app.modules.query.service import _observe_sql_execution
from app.modules.query_autopilot.telemetry import EXECUTION, ExecutionIdentity, collector


async def test_provenance_is_task_local_and_restored_after_cancellation():
    outer, inner = DatasetQuery(uuid4(), "inspect"), DatasetQuery(uuid4(), "preview", 2)
    entered = asyncio.Event()

    async def cancelled():
        with dataset_query(inner):
            entered.set()
            await asyncio.Event().wait()

    with dataset_query(outer):
        task = asyncio.create_task(cancelled())
        await entered.wait()
        assert current_dataset_query() == outer
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert current_dataset_query() == outer
        with pytest.raises(RuntimeError), dataset_query(inner):
            raise RuntimeError("refused")
        assert current_dataset_query() == outer
    assert current_dataset_query() is None


@pytest.mark.parametrize("revision", [True, 0, -1, "1"])
def test_invalid_revision_cannot_enter_provenance(revision):
    with pytest.raises(ValueError):
        DatasetQuery(uuid4(), "preview", revision)


@pytest.mark.parametrize("failed", [False, True])
async def test_private_execution_is_correlated_without_autopilot_source_capture(
    monkeypatch, failed,
):
    request_profile, observe = Mock(), Mock()
    monkeypatch.setattr(collector, "request_profile", request_profile)
    monkeypatch.setattr(collector, "observe", observe)
    provenance = DatasetQuery(uuid4(), "preview", 2)

    @_observe_sql_execution
    async def execute(sql, username):
        assert sql == "SELECT private_formula FROM private_source"
        assert username == "alice"
        assert EXECUTION.get() is not None
        return QueryResult(
            original_sql=sql, executed_sql=sql, rows=[[1]], columns=["total"],
            error=sql if failed else None, warnings=[sql], execution_failure={"message": sql},
        )

    with dataset_query(provenance):
        result = await execute("SELECT private_formula FROM private_source", "alice")
    assert result.original_sql == provenance.label and result.executed_sql == ""
    assert result.nova_execution_id and result.total_ms is not None
    assert result.rows == [[1]] and result.columns == ["total"]
    assert bool(result.error) is failed
    assert "private_formula" not in repr(result)
    request_profile.assert_not_called()
    observe.assert_not_called()
    assert EXECUTION.get() is None


async def test_dataset_queries_refuse_a_borrowed_engine_session():
    from app.core.exceptions import StarRocksError

    with (
        dataset_query(DatasetQuery(uuid4(), "inspect")),
        pytest.raises(StarRocksError, match="managed session"),
    ):
        await QueryRepository().execute_as_user(
            "SELECT 1", "alice", "unused", role="ANALYST", connected=Mock(),
        )


async def test_private_query_disables_global_profile_before_sql():
    statements = []

    class Cursor:
        description = None
        rowcount = 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def execute(self, sql):
            statements.append(sql)

    conn = Mock()
    conn.cursor.return_value = Cursor()
    conn.select_db = AsyncMock()
    identity = ExecutionIdentity(capture_profile=True, profile_enabled=True)
    token = EXECUTION.set(identity)
    try:
        with dataset_query(DatasetQuery(uuid4(), "inspect")):
            await QueryRepository._execute_on(
                conn, "SELECT private_formula", role="ANALYST", max_rows=None, start=0,
            )
    finally:
        EXECUTION.reset(token)
    assert statements[:3] == [
        "SET ROLE ANALYST", "SET enable_profile = false", "SELECT private_formula",
    ]
    assert identity.profile_enabled is False
