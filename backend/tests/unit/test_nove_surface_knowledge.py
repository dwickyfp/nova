"""Packaged reference retrieval uses current surface hints without losing lexical search."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.assistant.app_context import NoveAppContext
from app.modules.assistant.intelligence import TurnIntent, TurnRoute
from app.modules.assistant.provider import AssistantProviderClient
from app.modules.assistant.registry import build_registry
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.search_knowledge import (
    search_knowledge_tool,
    search_references,
)


def _app(surface: str, entity_type: str | None = None, *, failed_sql: bool = False):
    value = {"version": 1, "surface": {"id": surface, "route": "/"}}
    if entity_type:
        value["entity"] = {"type": entity_type}
    if failed_sql:
        value["execution"] = {"type": "sql", "status": "error"}
    return NoveAppContext.model_validate(value)


@pytest.mark.parametrize(
    ("surface", "entity", "failed_sql", "expected"),
    [
        ("role.detail", "role", False, "security-governance"),
        ("studio.agent", "studio_agent", False, "nove-workflows"),
        ("workspace.sql", "workspace_file", True, "query-troubleshooting"),
        ("monitoring.query-history", "query", False, "operations-monitoring"),
        ("intelligence.semantic-view", "semantic_view", False, "intelligence-semantic-features"),
    ],
)
def test_generic_reference_request_uses_current_surface(
    surface, entity, failed_sql, expected
):
    results = search_references(
        "Explain this" if not failed_sql else "Why did this fail?",
        app_context=_app(surface, entity, failed_sql=failed_sql),
    )
    assert results[0]["source"] == f"knowledge:{expected}"
    assert len(results) <= 3
    assert all(len(result["text"]) <= 3500 for result in results)


def test_explicit_topic_overrides_unrelated_surface_and_legacy_search_is_unchanged():
    studio = _app("studio.agent", "studio_agent")
    role = _app("role.detail", "role")
    assert search_references("Stage upload storage", app_context=studio)[0]["source"] == (
        "knowledge:data-movement-storage"
    )
    assert search_references("Query History", app_context=role)[0]["source"] == (
        "knowledge:operations-monitoring"
    )
    assert search_references("Explain this", app_context=_app("unknown.page")) == (
        search_references("Explain this")
    )
    assert search_references("how") == []
    assert search_references("how", app_context=role)[0]["source"] == (
        "knowledge:security-governance"
    )


@pytest.mark.asyncio
async def test_knowledge_tool_uses_surface_without_needing_new_arguments():
    outcome = await search_knowledge_tool.run(
        ToolInvocation("lookup", "search_knowledge", {"query": "Explain this"}),
        SimpleNamespace(app_context=_app("role.detail", "role")),
    )
    assert outcome.ok is True
    assert outcome.metadata["sources"][0] == "knowledge:security-governance"
    assert "# Authentication, access, governance" in outcome.summary
    assert "knowledge:security-governance" not in outcome.summary


def test_capability_help_context_compiler_uses_same_surface_ranking():
    loop = AssistantLoop(
        provider=AssistantProviderClient(), registry=build_registry(), system_prompt="test"
    )
    context = LoopContext(
        user_name="alice",
        selected_tools=["search_knowledge"],
        app_context=_app("role.detail", "role"),
    )
    messages = loop._build_messages(
        AssistantThread(thread_id="t", user_name="alice", title="t"),
        "Explain this",
        context,
        route=TurnRoute(TurnIntent.CAPABILITY_HELP),
    )
    reference_message = next(
        str(item["content"]) for item in messages if "<NOVA_REFERENCE_DATA>" in item["content"]
    )
    assert "# Authentication, access, governance" in reference_message
    assert "knowledge:security-governance" not in reference_message


def test_surface_boost_does_not_admit_secret_bearing_reference(monkeypatch, tmp_path):
    (tmp_path / "security-governance.md").write_text(
        "# Roles\n\npassword='private-value'\n",
        encoding="utf-8",
    )
    (tmp_path / "query-troubleshooting.md").write_text(
        "# Safe query guidance\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.modules.assistant.tools.search_knowledge._KNOWLEDGE_DIR", tmp_path)
    results = search_references("Explain this", app_context=_app("role.detail", "role"))
    assert all(result["source"] != "knowledge:security-governance" for result in results)
    assert "private-value" not in str(results)
