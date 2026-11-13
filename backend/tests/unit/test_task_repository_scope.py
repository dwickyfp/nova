"""A standalone CREATE TASK must be visible to its graph worker."""

from unittest.mock import AsyncMock

import pytest

from app.modules.access_control.security_context import SecurityContext
from app.modules.task_orchestration import metadata_repository as repository_module


def repository():
    store = repository_module.TaskMetadataRepository(None, SecurityContext("alice", "analyst"))
    store._bound = AsyncMock()
    return store


@pytest.mark.asyncio
async def test_scoped_standalone_graph_includes_root_without_edges(monkeypatch):
    statements = []

    async def execute(sql, params):
        statements.append((sql, params))
        return []

    repo = repository()
    repo._all = execute
    await repo.list_tasks("warehouse.default.minute_load")

    sql, params = statements[0]
    assert "name=%s OR name IN" in sql
    assert list(params) == [
        "warehouse",
        "default",
        "minute_load",
        "warehouse.default.minute_load",
        "warehouse.default.minute_load",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "graph_id,clause,params",
    [
        (
            "warehouse.legacy",
            "database_name=%s AND schema_name IS NULL",
            ["warehouse", "legacy", "warehouse.legacy", "warehouse.legacy"],
        ),
        ("legacy", "name=%s AND database_name IS NULL", ["legacy"] * 4),
    ],
)
async def test_legacy_standalone_includes_root_without_crossing_scope(
    monkeypatch, graph_id, clause, params
):
    statements = []

    async def execute(sql, values):
        statements.append((sql, values))
        return []

    repo = repository()
    repo._all = execute
    await repo.list_tasks(graph_id)
    assert clause in statements[0][0]
    assert list(statements[0][1]) == params


@pytest.mark.asyncio
async def test_existing_graph_run_ids_are_batched(monkeypatch):
    calls = []

    async def execute(sql, params):
        calls.append((sql, params))
        return [{"id": params[0][0]}]

    repo = repository()
    repo._all = execute
    ids = [f"run-{index}" for index in range(1001)]
    result = await repo.existing_graph_run_ids(ids)

    assert result == {"run-0", "run-1000"}
    assert [len(params[0]) for _, params in calls] == [1000, 1]
    assert all("WHERE id=ANY(%s)" in sql for sql, _ in calls)
