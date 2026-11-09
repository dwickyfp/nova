"""Neva keeps every capability the console assistant had (parity suite, phase 0).

This pins the inventory recorded in ``docs/research/neva-assistant-harness-research.md``:
the console tools with their read/write class and approval rule, the skills and
the knowledge documents. The Neva redesign may add capabilities; it may not drop
or quietly weaken one. A deliberate change edits this file and the research note
together.
"""

from pathlib import Path

import pytest

from app.modules.assistant.registry import tool_registry
from app.modules.assistant.skill_registry import skill_library
from app.modules.assistant.tools.search_knowledge import search_references

#: name -> (classification, requires consent)
CONSOLE_TOOLS = {
    "ai_search": ("read_only", True),
    "catalog_search": ("read_only", False),
    "compute_metrics": ("read_only", True),
    "create_agent": ("destructive", True),
    "create_semantic_view": ("destructive", True),
    "data_to_chart": ("read_only", False),
    "feature_lookup": ("read_only", True),
    "grant_role_access": ("destructive", True),
    "inspect_agent_configuration": ("read_only", False),
    "inspect_query_error": ("read_only", False),
    "inspect_role_access": ("read_only", True),
    "invoke_client_capability": ("read_only", False),
    "load_skill": ("read_only", False),
    "ml_execute": ("read_only", True),
    "provision_user": ("destructive", True),
    "query_execute": ("read_only", True),
    "query_mutate": ("destructive", True),
    "search_knowledge": ("read_only", False),
    "semantic_view_query": ("read_only", True),
    "validate_sql": ("read_only", False),
    "verify_query_repair": ("read_only", False),
}

CONSOLE_SKILLS = {
    "accountadmin-guardrail",
    "ai-functions",
    "copy-into",
    "create-ml-model",
    "create-table",
    "create-task",
    "create-user",
    "debug-sql",
    "native-ml",
    "nova-streams",
    "scope-boundary",
    "sql-reference",
    "stage-query",
    "writing-style",
}

#: knowledge document -> a question that finds it today. Plainer phrasings such
#: as "how do I create a table" do not find the table guide yet; improving that
#: retrieval is phase 5 work and must not lower these.
KNOWLEDGE = {
    "dashboards-sharing": "how do I share a dashboard",
    "data-movement-storage": "how do I load data from storage into a table",
    "data-workspace-catalog": "where do I browse databases and tables in the workspace",
    "intelligence-search-entities": "what are entities and AI search",
    "intelligence-semantic-features": "what is a semantic view and the feature store",
    "ml-ai-providers": "how do I set up an AI provider for machine learning",
    "operations-monitoring": "how do I check running queries and cluster health",
    "query-troubleshooting": "my query failed, how do I troubleshoot it",
    "security-governance": "how do roles and data masking work",
    "sql-extensions": "what SQL extensions does Nova add",
    "sql-operations": "how do I see the session variables and query timeout",
    "sql-select": "how do I write a select query with a join",
    "sql-tables": "create table with distribution and partitions",
    "sql-views-catalog": "how do I create a view or use an external catalog",
    "stages-tasks-access": "how do stages and scheduled tasks work",
}
#: The assistant's own guide; renamed with the assistant (Nove -> Neva).
ASSISTANT_GUIDE = {"nove-workflows", "neva-workflows"}

KNOWLEDGE_DIR = (
    Path(__file__).resolve().parents[2] / "app" / "modules" / "assistant" / "knowledge_library"
)


def _registered() -> dict:
    return dict(tool_registry._tools)  # noqa: SLF001 - the registry is the inventory


@pytest.mark.parametrize(("name", "expected"), sorted(CONSOLE_TOOLS.items()))
def test_every_console_tool_is_still_offered_with_the_same_safety(name, expected):
    tools = _registered()
    assert name in tools, f"{name} was dropped from the console assistant"
    classification, consent = expected
    tool = tools[name]
    assert tool.classification == classification
    # A write may never lose its approval step; a read may gain one.
    if consent:
        assert tool.requires_consent is True


def test_no_write_tool_runs_without_approval():
    for name, tool in _registered().items():
        if tool.classification != "read_only":
            assert tool.requires_consent is True, f"{name} changes Nova without approval"


@pytest.mark.parametrize("name", sorted(CONSOLE_SKILLS))
def test_every_console_skill_still_loads(name):
    skill = skill_library.get(name)
    assert skill is not None, f"skill {name} was dropped"
    assert skill.body.strip()


def test_the_knowledge_library_keeps_every_document():
    present = {path.stem for path in KNOWLEDGE_DIR.glob("*.md")}
    assert set(KNOWLEDGE) <= present
    assert present & ASSISTANT_GUIDE, "the assistant's own guide is missing"


@pytest.mark.parametrize(("document", "question"), sorted(KNOWLEDGE.items()))
def test_each_document_is_found_by_a_plain_question(document, question):
    sources = [reference["source"] for reference in search_references(question)[:5]]
    assert f"knowledge:{document}" in sources, f"{question!r} no longer finds {document}"
