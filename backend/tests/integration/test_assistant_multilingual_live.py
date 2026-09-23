"""Opt-in live model check for Nove's multilingual tool planning."""

from __future__ import annotations

import os

import pytest

from app.modules.assistant.planning import plan_turn
from app.modules.assistant.provider import AssistantProviderClient
from app.modules.assistant.registry import build_registry

pytestmark = pytest.mark.skipif(
    os.environ.get("NOVA_LIVE_ASSISTANT_TEST") != "1",
    reason="Live Assistant provider test is opt-in",
)


@pytest.mark.asyncio
async def test_nove_model_plans_diverse_languages_without_lexical_routing():
    client = AssistantProviderClient()
    provider = await client.resolve()
    registry = build_registry()
    cases = [
        ("apakah kamu bisa bantu aku membuat semantic view atas data ku?", "workflow"),
        ("Create and publish a Semantic View from sales.orders.", "create_semantic_view"),
        ("Explícame cómo crear una vista semántica", None),
        ("Quel est le chiffre d'affaires ce mois-ci ?", "query_execute"),
        ("Zeige den Umsatz dieses Monats", "query_execute"),
        ("今月の売上を表示して", "query_execute"),
        ("أظهر مبيعات هذا الشهر", "query_execute"),
        ("Mostre as vendas deste mês", "query_execute"),
        ("이번 달 매출을 보여줘", "query_execute"),
        ("Forecast sales for 30 days", "ml_execute"),
        ("Create a chart from the previous result", "data_to_chart"),
    ]
    for prompt, required in cases:
        result = await plan_turn(
            provider_client=client,
            provider=provider,
            registry=registry,
            user_content=prompt,
            has_previous_result=required == "data_to_chart",
        )
        if required == "workflow":
            assert result.route.intent.value in {"clarification", "ui_operation"}, prompt
            if result.route.intent.value == "ui_operation":
                assert "create_semantic_view" in result.selected_tools, prompt
        elif required is None:
            assert not result.route.needs_data, prompt
            assert set(result.route.required_capabilities) <= {
                "search_knowledge", "load_skill"
            }, prompt
        else:
            assert required in result.selected_tools, prompt
            assert required in result.route.required_capabilities, prompt
