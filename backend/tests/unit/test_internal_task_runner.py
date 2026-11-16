"""The task worker's internal-handler runner renews its leases and reports the
handler's own error, not the task group around it."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.task_orchestration import internal_handlers as module


def config_row(record_id="r1"):
    return {"version": 1, "scope": {"principal": "acct", "active_role": "marketing",
                                    "security_context_version": 1}, "record_id": record_id}


@pytest.fixture
def setup(monkeypatch):
    task = {"id": "t1", "name": "n", "version": 3, "handler": "viz.dashboard_export",
            "handler_config": config_row(), "schedule_kind": "cron"}
    job = SimpleNamespace(execution_user="acct", execution_role="marketing",
                          graph_run_id="g1", trigger_type="schedule", execution_session_id=None)
    repository = SimpleNamespace(
        get_task=AsyncMock(return_value=task),
        get_task_run=AsyncMock(return_value={"state": "running"}),
        get_graph_run=AsyncMock(return_value={"state": "running"}),
        get_role_execution_user=AsyncMock(return_value="acct"),
    )
    return task, job, repository


async def test_a_handler_error_is_reported_as_itself(setup, monkeypatch):
    task, job, repository = setup
    renew = AsyncMock()

    async def failing(*_args, **_kwargs):
        raise ValueError("The scheduled page no longer exists")

    executor = SimpleNamespace(owner_connection=None)
    monkeypatch.setattr(module, "_dict_cursor", None)

    class Connection:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_):
            return False

    class Cursor(Connection):
        pass

    executor.owner_connection = lambda _bound: Connection()
    executor._prepare_task_session = AsyncMock()
    monkeypatch.setattr(module, "_dict_cursor", lambda _conn: Cursor())
    monkeypatch.setattr(module, "run_once", failing)
    with pytest.raises(ValueError, match="no longer exists"):
        await module.run_internal_task(task, job, "run-1", executor, repository, renew=renew)
    renew.assert_awaited()


async def test_a_run_that_lost_its_task_stops(setup, monkeypatch):
    task, job, repository = setup
    repository.get_task_run.return_value = {"state": "failed"}
    monkeypatch.setattr(module, "run_once", AsyncMock())

    class Never:
        async def __aenter__(self):
            import asyncio
            await asyncio.sleep(5)

        async def __aexit__(self, *_):
            return False

    executor = SimpleNamespace(owner_connection=lambda _bound: Never())
    with pytest.raises(module.CredentialUnavailable):
        await module.run_internal_task(task, job, "run-1", executor, repository, renew=AsyncMock())
