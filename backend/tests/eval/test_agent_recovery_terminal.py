"""A stale lease cannot replace a specialist's durable terminal result."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import harness_repository as repository_module
from app.modules.agents.harness_repository import HarnessRepository


@pytest.mark.asyncio
async def test_recovery_restores_completed_child_after_stale_running_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, Any] = {"status": "running", "summary": None, "error": None}
    updates: list[str] = []

    async def execute(sql: str, params: list[Any] | None = None) -> dict[str, Any]:
        if sql.startswith("SELECT run_id, root_run_id"):
            return {"rows": [["child", "root"]] if state["status"] == "running" else []}
        if sql.startswith("SELECT root_run_id, run_id, event_type, payload"):
            return {"rows": [[
                "root", "child", "agent_completed", '{"summary":"Specialist answer"}',
            ]]}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS"):
            updates.append(sql)
            if "AND status = 'interrupted' AND error_class = 'worker_lost'" in sql:
                assert params is not None
                state.update(status=params[0], summary=params[2], error=None)
            elif "error_class = 'worker_lost'" in sql:
                state.update(status="interrupted", error="worker_lost")
            else:
                raise AssertionError(sql)
            return {"affected": 1}
        raise AssertionError(sql)

    monkeypatch.setattr(repository_module.db, "execute_system", execute)
    repo = HarnessRepository()
    repo.get = AsyncMock(return_value={"payload": {}, "checkpoint": {}})
    repo.event = AsyncMock(side_effect=ValueError("Conflicting Auto terminal event"))
    repo.wake_parent = AsyncMock(return_value=True)

    recovered = await repo.recover_stale()

    assert recovered == ["child"]
    assert state == {
        "status": "completed", "summary": "Specialist answer", "error": None
    }
    assert len(updates) == 2
    repo.event.assert_awaited_once_with("root", "child", "agent_interrupted", {})
    repo.wake_parent.assert_awaited_once_with("root")


@pytest.mark.asyncio
async def test_unreadable_terminal_rolls_back_for_a_later_recovery_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"status": "running", "error": None}
    terminal_reads = 0

    async def execute(sql: str, params: list[Any] | None = None) -> dict[str, Any]:
        nonlocal terminal_reads
        if sql.startswith("SELECT run_id, root_run_id"):
            return {"rows": [["child", "root"]] if state["status"] == "running" else []}
        if sql.startswith("SELECT root_run_id, run_id, event_type, payload"):
            terminal_reads += 1
            return {"rows": [["malformed"]]}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS"):
            if "SET status = 'running'" in sql:
                assert params is not None and params[1:] == ["child", "root"]
                state.update(status="running", error=None)
            elif "error_class = 'worker_lost'" in sql:
                state.update(status="interrupted", error="worker_lost")
            else:
                raise AssertionError(sql)
            return {"affected": 1}
        raise AssertionError(sql)

    monkeypatch.setattr(repository_module.db, "execute_system", execute)
    monkeypatch.setattr(repository_module.asyncio, "sleep", AsyncMock())
    repo = HarnessRepository()
    repo.get = AsyncMock(return_value={"payload": {}, "checkpoint": {}})
    repo.event = AsyncMock(side_effect=ValueError("Conflicting Auto terminal event"))
    repo.wake_parent = AsyncMock(return_value=True)

    assert await repo.recover_stale() == []
    assert state == {"status": "running", "error": None}
    assert terminal_reads == 12
    repo.wake_parent.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_terminal_clears_stale_worker_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock(return_value={"affected": 1})
    monkeypatch.setattr(repository_module.db, "execute_system", execute)
    repo = HarnessRepository()
    monkeypatch.setattr(repo, "_prior_terminal", AsyncMock(
        return_value=("agent_cancelled", {})
    ))

    await repo._restore_prior_terminal("root", "child")

    sql, params = execute.await_args.args
    assert "status = %s" in sql and "error_class = NULL" in sql
    assert "AND status = 'interrupted' AND error_class = 'worker_lost'" in sql
    assert params[0] == "cancelled"
    assert params[-2:] == ["child", "root"]
