"""Neva lists only the Semantic Views the active role can open."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.semantic_views import ListSemanticViewsTool

USER = {"username": "alice", "encrypted_password": "x", "active_role": "SALES"}


async def test_lists_what_the_role_can_open(monkeypatch):
    import app.modules.agents.semantic.access as access
    import app.modules.agents.tools.semantic_query as semantic

    loader = AsyncMock(return_value=[{"name": "retail_sales"}])
    monkeypatch.setattr(access, "load_authorized_models", loader)
    monkeypatch.setattr(semantic, "readable_view_summaries", lambda models: [
        {"name": model["name"], "about": "Sales", "metrics": ["net_revenue"], "dimensions": []}
        for model in models
    ])
    context = LoopContext(user_name="alice", user=USER, semantic_view_ids=["v-sales"])
    outcome = await ListSemanticViewsTool().run(
        ToolInvocation("l1", "list_semantic_views", {}), context
    )
    assert outcome.ok
    assert [view["name"] for view in outcome.data["semantic_views"]] == ["retail_sales"]
    loader.assert_awaited_once_with(context)


async def test_a_role_with_no_views_gets_an_empty_list_not_others(monkeypatch):
    import app.modules.agents.semantic.access as access

    loader = AsyncMock(return_value=[{"name": "secret"}])
    monkeypatch.setattr(access, "load_authorized_models", loader)
    context = LoopContext(user_name="alice", user=USER, semantic_view_ids=[])
    outcome = await ListSemanticViewsTool().run(
        ToolInvocation("l1", "list_semantic_views", {}), context
    )
    assert outcome.ok and outcome.data["semantic_views"] == []
    loader.assert_not_awaited()


async def test_no_session_no_list():
    outcome = await ListSemanticViewsTool().run(
        ToolInvocation("l1", "list_semantic_views", {}), LoopContext(user_name="alice")
    )
    assert not outcome.ok and outcome.error_class == "AUTHORIZATION"
