"""Nove intent, skill discovery, and packaged reference contracts."""

import pytest

from app.modules.assistant.intelligence import (
    TurnIntent,
    TurnRoute,
)
from app.modules.assistant.planning import validate_turn_plan
from app.modules.assistant.provider import AssistantProviderClient
from app.modules.assistant.registry import build_registry
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.search_knowledge import search_knowledge_tool, search_references


def test_nove_registers_intelligence_tools_and_routes_explanations_to_references():
    registry = build_registry()
    for name in ("ai_search", "semantic_view_query", "feature_lookup"):
        tool = registry.get(name)
        assert tool is not None and tool.requires_consent
    plan = validate_turn_plan(
        {"intent": "capability_help", "tools": ["search_knowledge"],
         "required_tools": [], "ml_task": None}, set(registry.names())
    )
    assert plan.selected_tools == ("search_knowledge",)


def test_nove_feature_help_receives_packaged_reference_without_tool_call():
    loop = AssistantLoop(
        provider=AssistantProviderClient(), registry=build_registry(), system_prompt="test"
    )
    context = LoopContext(user_name="alice", selected_tools=["search_knowledge"])
    messages = loop._build_messages(
        AssistantThread(thread_id="t", user_name="alice", title="t"),
        "Apa itu Feature Store?",
        context,
        route=TurnRoute(TurnIntent.CAPABILITY_HELP),
    )
    joined = "\n".join(str(message["content"]) for message in messages)
    assert "point-in-time training set" in joined
    assert "proof of live deployment state" in joined
    assert "knowledge:intelligence-semantic-features" not in joined
    assert "docs/28-intelligence-foundation.md" not in joined
    assert "Implementation references:" not in joined
    assert "feature_lookup" not in context.selected_tools


def test_semantic_view_creation_help_excludes_agent_studio_creation_tool():
    loop = AssistantLoop(
        provider=AssistantProviderClient(), registry=build_registry(), system_prompt="test"
    )
    context = LoopContext(user_name="alice", selected_tools=["search_knowledge"])
    messages = loop._build_messages(
        AssistantThread(thread_id="t", user_name="alice", title="t"),
        "apakah kamu bisa bantu aku membuat semantic view atas data ku ?",
        context,
        route=TurnRoute(TurnIntent.CAPABILITY_HELP),
    )
    joined = "\n".join(str(message["content"]) for message in messages)
    assert "Nove can create one from named tables" in joined
    assert "create_semantic_model" not in context.selected_tools
    assert "semantic_view_query" not in context.selected_tools


def test_reference_results_have_bounded_content_and_revision():
    results = search_references("query lambat penyebab")
    assert any(r["source"] == "knowledge:query-troubleshooting" for r in results)
    assert 0 < len(results) <= 3
    assert all(len(r["text"]) <= 3500 and len(r["revision"]) == 16 for r in results)
    assert results == search_references("query lambat penyebab")


@pytest.mark.parametrize(
    ("question", "source"),
    [
        ("AI Search fitur untuk apa", "intelligence-search-entities"),
        ("Feature Store point-in-time", "intelligence-semantic-features"),
        ("Semantic View metric", "intelligence-semantic-features"),
        ("ML Models training", "ml-ai-providers"),
        ("AI Providers embedding model", "ml-ai-providers"),
        ("Database Explorer catalog", "data-workspace-catalog"),
        ("Stage upload storage", "data-movement-storage"),
        ("Migration backup", "data-movement-storage"),
        ("Tasks monitoring query history", "operations-monitoring"),
        ("Ranger row access", "security-governance"),
        ("Dashboard sharing", "dashboards-sharing"),
    ],
)
def test_nove_has_grounded_reference_for_each_product_domain(question, source):
    assert any(
        result["source"] == f"knowledge:{source}"
        for result in search_references(question)
    )


@pytest.mark.parametrize(
    ("menu", "reference"),
    [
        ("Home", "data-workspace-catalog"),
        ("Workspaces", "data-workspace-catalog"),
        ("Database Explorer", "data-workspace-catalog"),
        ("Tasks", "operations-monitoring"),
        ("Agent Studio", "ml-ai-providers"),
        ("Semantic Views", "intelligence-semantic-features"),
        ("AI Search", "intelligence-search-entities"),
        ("Skills", "ml-ai-providers"),
        ("Tools", "ml-ai-providers"),
        ("Nova Studio", "ml-ai-providers"),
        ("Feature Store", "intelligence-semantic-features"),
        ("ML Models", "ml-ai-providers"),
        ("Users", "security-governance"),
        ("Roles", "security-governance"),
        ("Data Access", "security-governance"),
        ("Audit Trail", "security-governance"),
        ("AI Providers", "ml-ai-providers"),
        ("Admin Settings", "security-governance"),
        ("Migration", "data-movement-storage"),
        ("Query History", "operations-monitoring"),
        ("Active Queries", "operations-monitoring"),
        ("Query Cost", "operations-monitoring"),
        ("Data Loads", "operations-monitoring"),
        ("Cluster Monitor", "operations-monitoring"),
        ("Production Health", "operations-monitoring"),
    ],
)
def test_nove_ranks_each_sidebar_feature_reference_first(menu, reference):
    assert search_references(menu)[0]["source"] == f"knowledge:{reference}"


def test_reference_lookup_has_no_arbitrary_file_access(monkeypatch):
    from pathlib import Path

    reads = []
    original = Path.read_text

    def recording_read(path, *args, **kwargs):
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", recording_read)
    search_references("/etc/passwd")
    assert reads
    assert all(path.parent.name == "knowledge_library" for path in reads)


def test_unsafe_packaged_reference_is_excluded(monkeypatch, tmp_path):
    (tmp_path / "unsafe.md").write_text("uniqueunsafe password='do-not-expose'")
    monkeypatch.setattr("app.modules.assistant.tools.search_knowledge._KNOWLEDGE_DIR", tmp_path)
    assert search_references("uniqueunsafe") == []


@pytest.mark.asyncio
async def test_knowledge_tool_rejects_unbounded_input():
    result = await search_knowledge_tool.run(
        ToolInvocation("x", "search_knowledge", {"query": "x" * 1001}), None
    )
    assert not result.ok


@pytest.mark.asyncio
async def test_knowledge_tool_keeps_provenance_out_of_model_response():
    result = await search_knowledge_tool.run(
        ToolInvocation("x", "search_knowledge", {"query": "AI Search"}), None
    )
    assert result.ok
    assert "AI Search finds source records" in result.summary
    assert "knowledge:" not in result.summary
    assert "docs/28-intelligence-foundation.md" not in result.summary
    assert "Implementation references:" not in result.summary
    assert "knowledge:intelligence-search-entities" in result.metadata["sources"]
