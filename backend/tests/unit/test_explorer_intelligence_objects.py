from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_explorer_scopes_intelligence_objects_to_database_and_catalog(monkeypatch):
    import app.modules.explorer.service as module

    for name in (
        "list_tables", "list_views", "list_materialized_views", "list_functions",
        "list_pipes", "list_stages", "list_tasks",
    ):
        monkeypatch.setattr(module.explorer_repo, name, AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "decrypt_password", lambda value: "password")

    def entity(id, catalog, database):
        return SimpleNamespace(
            id=id, name=f"entity_{id}", catalog=catalog, database=database,
            schema_name="public", relation=f"{database}.customers",
            key_columns=["customer_id"],
        )

    monkeypatch.setattr(module.entity_registry, "list", AsyncMock(return_value=[
        entity("local", "default_catalog", "sales"),
        entity("other_db", "default_catalog", "finance"),
        entity("external", "remote", "sales"),
    ]))
    monkeypatch.setattr(module.semantic_view_service, "list", AsyncMock(return_value=[
        {"id": "sv1", "name": "customer_metrics", "catalog_name": "default_catalog",
         "database_name": "sales", "schema_name": "public", "status": "ACTIVE",
         "active_version": 2},
        {"id": "sv2", "name": "finance_metrics", "catalog_name": "default_catalog",
         "database_name": "finance", "schema_name": "public", "status": "ACTIVE",
         "active_version": 1},
    ]))
    monkeypatch.setattr(module.feature_store, "list_views", AsyncMock(return_value=[
        {"name": "customer_features", "entity_id": "local", "status": "ACTIVE",
         "active_version": 3},
        {"name": "external_features", "entity_id": "external", "status": "ACTIVE",
         "active_version": 1},
    ]))

    user = {"username": "analyst", "encrypted_password": "encrypted", "active_role": "analyst"}
    result = await module.ExplorerService().get_database_objects("sales", user=user)

    assert [item.name for item in result.entities] == ["entity_local"]
    assert [item.name for item in result.semantic_views] == ["customer_metrics"]
    assert [item.name for item in result.feature_views] == ["customer_features"]
    assert result.summary["entities"] == 1
    module.entity_registry.list.assert_awaited_once_with(user)
    module.semantic_view_service.list.assert_awaited_once_with(user)
    module.feature_store.list_views.assert_awaited_once_with(user)
