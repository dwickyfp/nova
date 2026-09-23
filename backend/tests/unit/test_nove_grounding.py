"""Nove intent, skill discovery, and packaged reference contracts."""

import pytest

from app.modules.assistant.intelligence import (
    CapabilityRegistry,
    SkillRouter,
    TurnIntent,
    TurnRouter,
)
from app.modules.assistant.skill_registry import skill_library
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.search_knowledge import search_knowledge_tool, search_references


@pytest.mark.parametrize(
    ("question", "intent", "required"),
    [
        ("Apa itu revenue?", TurnIntent.DIRECT_ANSWER, ()),
        ("Jelaskan schema di Nova", TurnIntent.DIRECT_ANSWER, ()),
        ("What is forecasting?", TurnIntent.DIRECT_ANSWER, ()),
        ("How do I create a chart?", TurnIntent.DIRECT_ANSWER, ()),
        ("Buat SQL untuk revenue bulanan", TurnIntent.SQL_AUTHORING, ()),
        ("Write SQL for monthly sales", TurnIntent.SQL_AUTHORING, ()),
        ("Cari penyebab query lambat", TurnIntent.SQL_AUTHORING, ()),
        ("Tampilkan jumlah transaksi bulan ini", TurnIntent.RAW_SQL_QUERY, ("query_execute",)),
        ("Count transactions", TurnIntent.RAW_SQL_QUERY, ("query_execute",)),
        ("SHOW TABLES", TurnIntent.SCHEMA_INSPECTION, ("query_execute",)),
        (
            "Forecast revenue next 30 days",
            TurnIntent.MACHINE_LEARNING,
            ("semantic_query", "ml_execute"),
        ),
    ],
)
def test_nove_intent_contract(question, intent, required):
    route = TurnRouter().route(question)
    assert route.intent == intent
    assert route.required_capabilities == required


@pytest.mark.parametrize(
    ("question", "skill"),
    [
        ("Buat tabel untuk event", "create-table"),
        ("Cari penyebab query lambat", "debug-sql"),
        ("Bagaimana membuat task terjadwal?", "create-task"),
        ("Forecast revenue", "native-ml"),
        ("COPY INTO dari @stage", "copy-into"),
    ],
)
def test_skill_selection_handles_phrases_and_indonesian(question, skill):
    selected = SkillRouter().select(
        question,
        default_skills=(),
        discoverable_skills=skill_library.names(),
        library=skill_library,
    )
    assert skill in selected
    assert len(selected) <= 2


def test_skills_do_not_activate_from_summary_stop_words():
    assert (
        SkillRouter().select(
            "the and for",
            default_skills=(),
            discoverable_skills=skill_library.names(),
            library=skill_library,
        )
        == ()
    )


def test_reference_tools_available_without_bypassing_semantic_governance():
    registry = CapabilityRegistry.from_tool_names(
        ["query_execute", "semantic_query", "load_skill", "search_knowledge"]
    )
    selected = registry.gated_tools(TurnRouter().route("Revenue this month"))
    assert "semantic_query" in selected
    assert "load_skill" in selected and "search_knowledge" in selected
    assert "query_execute" not in selected


def test_reference_results_have_bounded_content_and_revision():
    results = search_references("query lambat penyebab")
    assert any(r["source"] == "knowledge:query-troubleshooting" for r in results)
    assert 0 < len(results) <= 3
    assert all(len(r["text"]) <= 3500 and len(r["revision"]) == 16 for r in results)
    assert results == search_references("query lambat penyebab")


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
