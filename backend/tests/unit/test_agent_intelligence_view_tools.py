"""Agent tools preserve caller scope and redact Semantic View and Feature data."""

from types import SimpleNamespace

from app.modules.agents.registry import build_registry
from app.modules.agents.tools.intelligence_views import (
    feature_lookup_tool,
    semantic_view_query_tool,
)
from app.modules.assistant.tools import ToolInvocation
from app.modules.intelligence.feature_store import feature_store
from app.modules.intelligence.semantic_views import semantic_view_service


def _context():
    return SimpleNamespace(
        user={"username": "alice", "encrypted_password": "encrypted"},
        active_role="analyst",
        audit_session_id="session-1",
    )


def test_tools_are_opt_in_and_consent_gated():
    assert build_registry({"default_tools": []}).get("semantic_view_query") is None
    selected = build_registry(
        {
            "default_tools": ["semantic_view_query", "feature_lookup"],
        }
    )
    assert selected.get("semantic_view_query") is semantic_view_query_tool
    assert selected.get("feature_lookup") is feature_lookup_tool
    assert semantic_view_query_tool.requires_consent
    assert feature_lookup_tool.requires_consent


async def test_semantic_view_tool_redacts_caller_rows(monkeypatch):
    observed = {}

    async def query(view_id, body, user):
        observed.update(view_id=view_id, body=body, user=user)
        return {"version": 2, "columns": ["revenue", "api_key"], "rows": [[42, "sensitive"]]}

    monkeypatch.setattr(semantic_view_service, "query", query)
    outcome = await semantic_view_query_tool.run(
        ToolInvocation(
            "c1",
            "semantic_view_query",
            {
                "view_id": "view-1",
                "metrics": ["revenue"],
                "named_filters": ["completed"],
                "version": 2,
            },
        ),
        _context(),
    )
    assert outcome.ok
    assert observed["user"]["active_role"] == "analyst"
    assert observed["body"].named_filters == ["completed"]
    assert observed["body"].version == 2
    assert outcome.table["rows"] == [[42, "***"]]
    assert outcome.data["rows"] == [[42, "***"]]


async def test_feature_lookup_tool_redacts_values(monkeypatch):
    observed = {}

    async def lookup(group, body, user):
        observed.update(group=group, body=body, user=user)
        return {
            "version": 3,
            "as_of": "2026-01-01T00:00:00Z",
            "values": {"orders_7d": 7, "api_key": "sensitive"},
        }

    monkeypatch.setattr(feature_store, "lookup", lookup)
    outcome = await feature_lookup_tool.run(
        ToolInvocation(
            "c2",
            "feature_lookup",
            {
                "group": "customer",
                "entity_key": {"customer_id": 1},
                "as_of": "2026-01-01T00:00:00Z",
            },
        ),
        _context(),
    )
    assert outcome.ok
    assert observed["user"]["session_id"] == "session-1"
    assert observed["body"].as_of is not None
    assert outcome.data["values"] == {"orders_7d": 7, "api_key": "***"}


async def test_intelligence_view_tools_fail_closed_without_credentials():
    context = SimpleNamespace(user={"username": "alice"})
    semantic = await semantic_view_query_tool.run(
        ToolInvocation(
            "c3",
            "semantic_view_query",
            {
                "view_id": "view-1",
                "metrics": ["revenue"],
            },
        ),
        context,
    )
    feature = await feature_lookup_tool.run(
        ToolInvocation(
            "c4",
            "feature_lookup",
            {
                "group": "customer",
                "entity_key": {"customer_id": 1},
            },
        ),
        context,
    )
    assert not semantic.ok and not feature.ok
