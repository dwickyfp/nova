"""Release QA: a scheduled export never reports success when its file was not saved.

NDV-STORE-010: the workspace store is unavailable during a scheduled run. The
run fails, its failure reaches the task (no "saved" result) and no file is
recorded. Native Dataviz has no object store or signed URLs; the scheduled
workbook is saved as one workspace file.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import StorageError
from app.modules.viz import scheduled_export
from app.modules.viz.contracts.queries import DatasetQueryResult
from app.modules.viz.scheduled_export import FOLDER, record_for
from tests.unit.test_native_viz_scheduled_export import (
    CHART,
    DASHBOARD,
    DATASET,
    PAGE,
    board,
    chart,
)

ACCOUNT = {"username": "nova_task_service_mkt", "active_role": "marketing",
           "session_id": "task-run", "security_context_version": 1,
           "assigned_roles": ["marketing"]}


def _result() -> DatasetQueryResult:
    return DatasetQueryResult.model_validate({
        "dataset_id": str(DATASET), "revision": 1, "truncated": False,
        "columns": [
            {"key": "d0", "name": "region", "logical_type": "string", "role": "dimension",
             "field_id": "region"},
            {"key": "m0", "name": "revenue", "logical_type": "decimal", "role": "measure",
             "field_id": "revenue", "aggregation": "sum"}],
        "rows": [[{"type": "string", "value": "West"}, {"type": "decimal", "value": "12.5"}]],
    })


@pytest.fixture
def wired(monkeypatch):
    from app.modules.viz import dataset_service, document_service
    from app.modules.workspaces import service as workspace_module
    from app.modules.workspaces import spaces

    bar = chart({"kind": "cartesian", "dimensions": [{"field_id": "region"}],
                 "measures": [{"field_id": "revenue", "aggregation": "sum"}]})

    async def read(user, identity, *, draft=False, revision=None):
        return {"definition": board() if identity == DASHBOARD else bar}

    documents = SimpleNamespace(
        permissions=AsyncMock(return_value=["view", "export"]),
        read=AsyncMock(side_effect=read),
        workspace_of=AsyncMock(return_value="0123456789abcdef0123456789abcdef"),
    )
    datasets = SimpleNamespace(query=AsyncMock(return_value=_result()))
    files = SimpleNamespace(create_folder=AsyncMock(return_value={"id": "folder"}),
                            create_file_bytes=AsyncMock(return_value={"id": "entry-1"}))
    space = SimpleNamespace(namespace="space:sales")
    monkeypatch.setattr(document_service, "native_document_service", documents)
    monkeypatch.setattr(dataset_service, "native_dataset_service", datasets)
    monkeypatch.setattr(workspace_module, "workspace_service", files)
    monkeypatch.setattr(spaces.space_service, "resolve", AsyncMock(return_value=space))
    return SimpleNamespace(documents=documents, datasets=datasets, files=files)


async def _run():
    return await scheduled_export.run(record_for(DASHBOARD, PAGE, "UTC"), ACCOUNT)


async def test_a_healthy_run_reports_the_entry_it_saved(wired):
    outcome = await _run()
    assert outcome["status"] == "saved" and outcome["entry_id"] == "entry-1"
    wired.files.create_file_bytes.assert_awaited_once()
    namespace, folder, name, data = wired.files.create_file_bytes.await_args.args
    assert (namespace, folder) == ("space:sales", FOLDER) and name.endswith(".xlsx")
    assert outcome["bytes"] == len(data) > 0
    assert wired.documents.read.await_args_list[1].args[1] == CHART


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_a_store_failure_while_saving_fails_the_run(wired, status):
    wired.files.create_file_bytes.side_effect = StorageError("store unreachable", status)
    with pytest.raises(StorageError) as error:
        await _run()
    assert error.value.status_code == status
    # One attempt, no silent retry, no "saved" result for the task to record.
    wired.files.create_file_bytes.assert_awaited_once()


@pytest.mark.parametrize("status", [500, 503])
async def test_a_store_failure_creating_the_folder_saves_nothing(wired, status):
    wired.files.create_folder.side_effect = StorageError("store unreachable", status)
    with pytest.raises(StorageError):
        await _run()
    wired.files.create_file_bytes.assert_not_awaited()


async def test_an_existing_folder_is_not_a_failure(wired):
    wired.files.create_folder.side_effect = StorageError("exists", 409)
    outcome = await _run()
    assert outcome["status"] == "saved"
    wired.files.create_file_bytes.assert_awaited_once()


async def test_a_query_failure_never_reaches_the_store(wired):
    wired.datasets.query.side_effect = RuntimeError("engine unavailable")
    with pytest.raises(RuntimeError):
        await _run()
    wired.files.create_folder.assert_not_awaited()
    wired.files.create_file_bytes.assert_not_awaited()


async def test_the_task_handler_propagates_the_store_failure(wired):
    from app.modules.task_orchestration.internal_handlers import (
        InternalTaskConfiguration,
        Scope,
        run_once,
    )

    wired.files.create_file_bytes.side_effect = StorageError("store unreachable", 503)
    config = InternalTaskConfiguration(scope=Scope.from_user(ACCOUNT),
                                       record_id=record_for(DASHBOARD, PAGE, "UTC"))
    with pytest.raises(StorageError):
        await run_once("viz.dashboard_export", config, ACCOUNT)
    wired.files.create_file_bytes.assert_awaited_once()
