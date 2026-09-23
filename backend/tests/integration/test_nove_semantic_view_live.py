"""Opt-in Nove tool to StarRocks Semantic View lifecycle smoke test."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.database import db
from app.core.security import encrypt_password
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.create_semantic_view import CreateSemanticViewTool
from app.modules.intelligence.semantic_view_schema import ensure_semantic_view_schema
from app.modules.intelligence.semantic_views import semantic_view_service

pytestmark = pytest.mark.skipif(
    os.environ.get("NOVA_LIVE_STARROCKS_TEST") != "1",
    reason="Live StarRocks test is opt-in",
)


@pytest.mark.asyncio
async def test_nove_creates_validates_publishes_and_cleans_up_view():
    name = f"nove_live_{uuid4().hex[:8]}"
    user = {
        "username": settings.STARROCKS_ROOT_USER,
        "encrypted_password": encrypt_password(settings.STARROCKS_ROOT_PASSWORD),
        "active_role": None,
        "session_id": "nove-live-test",
    }
    context = SimpleNamespace(
        user_name=user["username"],
        user=user,
        active_role=None,
        audit_session_id="nove-live-test",
        schema_name="",
    )
    designer = SimpleNamespace(
        _fetch_metadata=AsyncMock(return_value=[{
            "table": "NOVA_DEMO.orders",
            "columns": [{"name": "order_id"}, {"name": "total_amount"}],
        }]),
        _generate=AsyncMock(return_value={
            "name": name,
            "datasets": [{
                "name": "orders",
                "source": "NOVA_DEMO.orders",
                "primary_key": "order_id",
                "fields": [
                    {"name": "order_id", "datatype": "Integer"},
                    {"name": "total_amount", "datatype": "Decimal"},
                ],
            }],
            "metrics": [{
                "name": "revenue",
                "expression": "SUM(orders.total_amount)",
                "datatype": "Decimal",
            }],
        }),
    )
    await db.init_system_pool()
    view_id = None
    try:
        await ensure_semantic_view_schema()
        outcome = await CreateSemanticViewTool(designer=designer).run(
            ToolInvocation("live", "create_semantic_view", {
                "name": name,
                "database": "NOVA_DEMO",
                "tables": ["NOVA_DEMO.orders"],
                "request": "Published revenue metric",
                "publish": True,
            }),
            context,
        )
        view_id = (outcome.data or {}).get("view_id")
        assert outcome.ok, outcome.error
        assert outcome.data["status"] == "PUBLISHED"
        described = await semantic_view_service.describe(view_id, user)
        assert described["active_version"] == 1
    finally:
        if view_id:
            await semantic_view_service.drop(view_id, user)
        await db.close_system_pool()
