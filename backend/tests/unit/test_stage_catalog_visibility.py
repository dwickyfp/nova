from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.stages.access import StageAccessDenied


@pytest.mark.asyncio
async def test_object_browser_hides_denied_stage_schemas(monkeypatch):
    import app.modules.objects.service as module

    service = module.ObjectService()
    service._repo.get_database = AsyncMock(return_value={"name": "db"})
    monkeypatch.setattr(
        module.db, "execute_system",
        AsyncMock(return_value={"rows": [("bronze",), ("silver",)]}),
    )
    monkeypatch.setattr(module, "decrypt_password", lambda value: "pw")

    async def authorize(stage, **kwargs):
        if stage["schema_name"] == "silver":
            raise StageAccessDenied("Stage access denied")

    monkeypatch.setattr(module, "check_stage_access", authorize)
    rows = await service.list_schemas(
        "db", username="analyst", encrypted_password="encrypted", role="analyst"
    )
    assert rows == [{"name": "bronze"}]


@pytest.mark.asyncio
async def test_explorer_hides_denied_stage_metadata(monkeypatch):
    import app.modules.explorer.service as module

    repo = module.explorer_repo
    for name in (
        "list_tables", "list_views", "list_materialized_views", "list_functions",
        "list_pipes", "list_tasks",
    ):
        monkeypatch.setattr(repo, name, AsyncMock(return_value=[]))
    monkeypatch.setattr(
        repo, "list_stages",
        AsyncMock(return_value=[
            {"name": "bronze_stage", "schema_name": "bronze"},
            {"name": "silver_stage", "schema_name": "silver"},
        ]),
    )
    monkeypatch.setattr(module, "decrypt_password", lambda value: "pw")

    async def authorize(stage, **kwargs):
        if stage["schema_name"] == "silver":
            raise StageAccessDenied("Stage access denied")

    monkeypatch.setattr(module, "check_stage_access", authorize)
    result = await module.ExplorerService().get_database_objects(
        "db", user={"username": "analyst", "encrypted_password": "encrypted",
                    "active_role": "analyst"},
    )
    assert [stage.name for stage in result.stages] == ["bronze_stage"]


@pytest.mark.asyncio
async def test_stage_list_checks_each_schema_once(monkeypatch):
    import app.modules.stages.router as module

    rows = [
        {
            "id": str(index),
            "name": f"stage_{index}",
            "database_name": "db",
            "schema_name": schema,
            "storage_connection": "storage",
            "base_prefix": "",
        }
        for index, schema in enumerate(("bronze", "bronze", "silver"))
    ]
    monkeypatch.setattr(module.stage_service, "list_stages", AsyncMock(return_value=rows))
    checked = []

    async def authorize(stage, user, action):
        checked.append(stage["schema_name"])
        if stage["schema_name"] == "silver":
            raise HTTPException(status_code=403)

    monkeypatch.setattr(module, "_authorize", authorize)
    result = await module.list_stages(user={"username": "analyst"})

    assert [stage.name for stage in result.stages] == ["stage_0", "stage_1"]
    assert checked == ["bronze", "silver"]
