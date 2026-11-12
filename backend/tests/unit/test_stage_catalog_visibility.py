from unittest.mock import AsyncMock

import pytest

from app.modules.stages.access import StageAccessDenied
from tests.unit.stage_metadata_fakes import install_stages

pytestmark = pytest.mark.usefixtures("fake_metadata_db")


@pytest.mark.asyncio
async def test_object_browser_hides_denied_stage_schemas(monkeypatch):
    import app.modules.objects.service as module

    service = module.ObjectService()
    service._repo.get_database = AsyncMock(return_value={"name": "db"})
    install_stages(
        monkeypatch,
        [
            {"id": schema, "name": schema, "database_name": "db", "schema_name": schema}
            for schema in ("bronze", "silver")
        ],
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
        "list_tables",
        "list_views",
        "list_materialized_views",
        "list_functions",
        "list_pipes",
        "list_tasks",
    ):
        monkeypatch.setattr(repo, name, AsyncMock(return_value=[]))
    install_stages(
        monkeypatch,
        [
            {"id": schema, "name": schema + "_stage", "database_name": "db", "schema_name": schema}
            for schema in ("bronze", "silver")
        ],
    )
    monkeypatch.setattr(module.entity_registry, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.semantic_view_service, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.feature_store, "list_views", AsyncMock(return_value=[]))

    async def authorize(stage, **kwargs):
        if stage["schema_name"] == "silver":
            raise StageAccessDenied("Stage access denied")

    monkeypatch.setattr("app.modules.stages.metadata_service.check_stage_access", authorize)
    _allow_all_caller(module, monkeypatch)
    result = await module.ExplorerService().get_database_objects(
        "db",
        user={
            "username": "analyst",
            "encrypted_password": "encrypted",
            "active_role": "analyst",
            "assigned_roles": ["analyst"],
        },
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
    install_stages(monkeypatch, rows)
    checked = []

    async def authorize(stage, **kwargs):
        checked.append(stage["schema_name"])
        if stage["schema_name"] == "silver":
            raise StageAccessDenied("Stage access denied")

    monkeypatch.setattr("app.modules.stages.metadata_service.check_stage_access", authorize)
    result = await module.list_stages(
        user={"username": "analyst", "active_role": "analyst", "assigned_roles": ["analyst"]}
    )

    assert [stage.name for stage in result.stages] == ["stage_0", "stage_1"]
    assert checked == ["bronze", "silver"]


class _AllowAllCaller:
    async def databases(self, catalog):
        return ["sales", "db"]

    async def relations(self, catalog, database):
        return {}

    async def function_names(self, database):
        return set()


def _allow_all_caller(module, monkeypatch):
    import contextlib

    @contextlib.asynccontextmanager
    async def fake(user):
        yield _AllowAllCaller()

    monkeypatch.setattr(module, "caller_metadata", fake)
    monkeypatch.setattr(module.catalog_repo, "list_objects", AsyncMock(return_value=[]))
