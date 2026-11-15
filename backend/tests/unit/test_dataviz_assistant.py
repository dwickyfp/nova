"""The agent-free Dataviz assistant: admission, instructions and scope."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

ALICE = {
    "username": "alice",
    "session_id": "s1",
    "active_role": "finance",
    "roles": ["finance"],
    "security_context_version": 3,
}


async def test_the_assistant_is_admitted_only_on_conversation_routes():
    from app.modules.agents import router
    from app.modules.agents.dataviz_assistant import DATAVIZ_ASSISTANT_ID

    agent = await router._require_agent(DATAVIZ_ASSISTANT_ID, ALICE, platform=True)
    assert agent["agent_id"] == DATAVIZ_ASSISTANT_ID and agent["policy"] == "auto_read_only"
    for user, platform in ((ALICE, False), ("alice", True)):
        with pytest.raises(HTTPException) as caught:
            await router._require_agent(DATAVIZ_ASSISTANT_ID, user, platform=platform)
        assert caught.value.status_code == 404


def test_the_assistant_asks_for_five_w_one_h_answers_from_native_dashboards():
    from app.modules.agents.dataviz_assistant import (
        DASHBOARD_ALIASES,
        TOOLS,
        dataviz_assistant_config,
    )

    config = dataviz_assistant_config("alice", semantic_view_ids=["v1"])
    text = " ".join(config["instructions_response"].split())
    for word in ("What:", "Who:", "When:", "Where:", "Why:", "How:"):
        assert word in text
    assert "does not prove" in text and "native_dataset_query" in text
    assert "dataviz_query" not in text and "BI dashboards" not in config["description"]
    assert config["semantic_view_ids"] == ["v1"]
    assert not {"ml_execute", "dashboard_build", "query_execute"} & set(TOOLS)
    assert set(DASHBOARD_ALIASES.values()) == {"native_dataset_query"}


async def test_scope_puts_the_questions_views_first_and_drops_unreadable(monkeypatch):
    from app.modules.agents import dataviz_assistant as assistant
    from app.modules.intelligence.semantic_views import semantic_view_service

    async def question(user, text):
        return ["onto-view", "hidden"]

    async def readable(view_id, user, agent_id=None):
        return None if view_id == "hidden" else {"id": view_id}

    async def everything(user, agent_id=None):
        return [{"id": "other"}, {"id": "onto-view"}]

    monkeypatch.setattr(assistant, "_views_for_question", question)
    monkeypatch.setattr(semantic_view_service, "get_active_for_agent", readable)
    monkeypatch.setattr(semantic_view_service, "list_active_for_agent", everything)
    assert await assistant.scope_views(ALICE, question="kenapa omzet turun") == [
        "onto-view", "other",
    ]


async def test_the_assistants_threads_need_no_agent_grant(monkeypatch):
    from app.modules.agents import mission
    from app.modules.agents.dataviz_assistant import DATAVIZ_ASSISTANT_ID

    async def thread(thread_id, user_name):
        return {"thread_id": thread_id, "agent_id": DATAVIZ_ASSISTANT_ID}

    monkeypatch.setattr(mission.assistant_repository, "get_thread", thread)
    found = await mission.require_thread("t1", ALICE)
    assert found["agent_id"] == DATAVIZ_ASSISTANT_ID


def test_retired_dashboard_hints_are_accepted_and_dropped():
    from app.modules.assistant.schemas import DatavizTurnInput

    hints = DatavizTurnInput.model_validate({
        "dashboard_id": 7, "native_filters_key": "k", "edit_state": "clean", "path": "/x",
    })
    assert hints.turn_hints() == {"native_dashboard": None}
    assert DatavizTurnInput.model_validate({"dashboard_id": "a" * 36}).dashboard_id


def test_a_grounded_semantic_query_settles_ontology_resolution_only():
    from app.modules.agents.tools.semantic_query import semantic_query_tool
    from app.modules.assistant.service import _settled_capabilities
    from app.modules.assistant.tools import ToolOutcome

    grounded = ToolOutcome(
        ok=True,
        summary="",
        metadata={"settles_capabilities": ["ontology_resolve", "diagnose_change"]},
    )
    assert _settled_capabilities(semantic_query_tool, grounded) == {"ontology_resolve"}
