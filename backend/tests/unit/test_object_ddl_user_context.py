from __future__ import annotations

import pytest

from app.modules.tables import router as tables_router
from app.modules.views import router as views_router


@pytest.mark.parametrize(
    ("module", "handler", "repository_method", "object_name"),
    [
        (tables_router, "get_table_ddl", "get_table_detail", "orders"),
        (views_router, "get_view_ddl", "get_view_detail", "orders_view"),
    ],
)
@pytest.mark.asyncio
async def test_object_ddl_uses_caller_credentials(
    monkeypatch, module, handler, repository_method, object_name
):
    received = {}

    async def get_detail(database, name, **kwargs):
        received.update(database=database, name=name, **kwargs)
        return {"ddl": f"CREATE {name}"}

    monkeypatch.setattr(module.object_repo, repository_method, get_detail)
    user = {
        "username": "analyst",
        "encrypted_password": "sealed-password",
        "active_role": "analyst_role",
    }
    result = await getattr(module, handler)("analytics", object_name, user)

    assert result["ddl"] == f"CREATE {object_name}"
    assert received == {
        "database": "analytics",
        "name": object_name,
        "username": "analyst",
        "encrypted_password": "sealed-password",
        "role": "analyst_role",
    }
