"""A standalone CREATE TASK must be visible to its graph worker."""

import pytest

from app.modules.task_orchestration import repository as repository_module


@pytest.mark.asyncio
async def test_scoped_standalone_graph_includes_root_without_edges(monkeypatch):
    statements = []

    async def execute_system(sql, params):
        statements.append((sql, params))
        return {"rows": []}

    monkeypatch.setattr(repository_module.db, "execute_system", execute_system)
    repo = repository_module.TaskOrchestrationRepository()
    await repo.list_tasks("warehouse.default.minute_load")

    sql, params = statements[0]
    assert "name = %s OR name IN" in sql
    assert params == [
        "warehouse", "default", "minute_load",
        "warehouse.default.minute_load", "warehouse.default.minute_load",
    ]


@pytest.mark.asyncio
async def test_existing_graph_run_ids_are_batched(monkeypatch):
    calls = []

    async def execute_system(sql, params):
        calls.append((sql, params))
        return {"rows": [(params[0],)]}

    monkeypatch.setattr(repository_module.db, "execute_system", execute_system)
    ids = [f"run-{index}" for index in range(501)]
    result = await repository_module.TaskOrchestrationRepository().existing_graph_run_ids(ids)

    assert result == {"run-0", "run-500"}
    assert [len(params) for _, params in calls] == [500, 1]
    assert all("WHERE id IN" in sql for sql, _ in calls)
