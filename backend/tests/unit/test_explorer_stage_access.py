from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.explorer import router as explorer
from app.modules.stages import router as stages
from app.modules.stages.service import stage_service


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["read", "write", "delete"])
async def test_explorer_stage_file_routes_deny_before_storage(monkeypatch, action):
    user = {"username": "analyst"}
    monkeypatch.setattr(
        explorer.explorer_service, "get_stage_id", AsyncMock(return_value="stage-id")
    )
    authorize = AsyncMock(side_effect=HTTPException(status_code=403, detail="Stage access denied"))
    monkeypatch.setattr(stages, "_authorized_stage", authorize)
    list_files = AsyncMock()
    upload_stream = AsyncMock()
    delete_file = AsyncMock()
    monkeypatch.setattr(stage_service, "list_files", list_files)
    monkeypatch.setattr(stage_service, "upload_stream", upload_stream)
    monkeypatch.setattr(stage_service, "delete_file", delete_file)

    with pytest.raises(HTTPException) as denied:
        if action == "read":
            await explorer.get_stage_files("db", "stage", user)
        elif action == "write":
            await explorer.upload_stage_file(
                "db",
                "stage",
                SimpleNamespace(filename="file.csv", file=object()),
                user,
                filename=None,
            )
        else:
            await explorer.delete_stage_file("db", "stage", "file.csv", user)

    assert denied.value.status_code == 403
    authorize.assert_awaited_once_with("stage-id", user, action)
    list_files.assert_not_awaited()
    upload_stream.assert_not_awaited()
    delete_file.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["read", "write", "delete"])
async def test_explorer_rejects_escaped_stage_path_before_authorization(monkeypatch, action):
    monkeypatch.setattr(
        explorer.explorer_service, "get_stage_id", AsyncMock(return_value="stage-id")
    )
    authorize = AsyncMock()
    monkeypatch.setattr(stages, "_authorized_stage", authorize)

    with pytest.raises(HTTPException) as rejected:
        if action == "read":
            await explorer.get_stage_files("db", "stage", {}, prefix="../private")
        elif action == "write":
            await explorer.upload_stage_file(
                "db",
                "stage",
                SimpleNamespace(filename="../private", file=object()),
                {},
                filename=None,
            )
        else:
            await explorer.delete_stage_file("db", "stage", "../private", {})

    assert rejected.value.status_code == 400
    authorize.assert_not_awaited()
