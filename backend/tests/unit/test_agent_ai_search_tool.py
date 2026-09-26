"""Agent AI Search keeps the user identity, consent, and result redaction."""

from types import SimpleNamespace

from app.modules.agents.registry import build_registry
from app.modules.agents.tools.ai_search import ai_search_tool
from app.modules.assistant.tools import ToolInvocation
from app.modules.intelligence.search import search_service


def test_ai_search_is_selected_only_when_agent_bundles_it():
    assert build_registry({"default_tools": []}).get("ai_search") is None
    selected = build_registry({"default_tools": ["ai_search"]}).get("ai_search")
    assert isinstance(selected, type(ai_search_tool))
    assert selected is not ai_search_tool
    assert selected.requires_consent is True


async def test_ai_search_uses_caller_context_and_redacts_values(monkeypatch):
    observed = {}

    async def query(name, body, user):
        observed.update(name=name, body=body, user=user)
        return {
            "version": 2,
            "hits": [{"source_key": "[1]", "content": "sk-ABCDEFGHIJKLMNOPQRSTUVWX", "rank": 1}],
        }

    monkeypatch.setattr(search_service, "query", query)
    context = SimpleNamespace(
        user={"username": "alice", "encrypted_password": "encrypted"},
        active_role="analyst",
        audit_session_id="session-1",
    )
    outcome = await ai_search_tool.run(
        ToolInvocation("call-1", "ai_search", {
            "index": "docs", "query": "shoe", "filters": {"category": "running"},
        }),
        context,
    )
    assert outcome.ok
    assert observed["user"]["active_role"] == "analyst"
    assert observed["user"]["session_id"] == "session-1"
    assert observed["body"].top_k == 5
    assert observed["body"].filters == {"category": "running"}
    assert outcome.data["hits"][0]["content"] == "***"
    assert "sk-" not in outcome.summary


async def test_ai_search_fails_closed_without_caller_credentials(monkeypatch):
    async def forbidden(*_args):
        raise AssertionError("Search should not run without a user connection")

    monkeypatch.setattr(search_service, "query", forbidden)
    outcome = await ai_search_tool.run(
        ToolInvocation("call-2", "ai_search", {"index": "docs", "query": "shoe"}),
        SimpleNamespace(user={"username": "alice"}),
    )
    assert not outcome.ok


async def test_ai_search_rejects_invalid_filter_value_before_retrieval(monkeypatch):
    async def forbidden(*_args):
        raise AssertionError("Invalid filters must not reach retrieval")

    monkeypatch.setattr(search_service, "query", forbidden)
    outcome = await ai_search_tool.run(
        ToolInvocation("call-3", "ai_search", {
            "index": "docs", "query": "shoe", "filters": {"category": ["running"]},
        }),
        SimpleNamespace(user={"username": "alice", "encrypted_password": "encrypted"}),
    )
    assert not outcome.ok
