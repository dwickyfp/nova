"""Interactive state and public media never bypass the authenticated source."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.modules.agents import router
from app.modules.assistant import intelligent_ui as ui
from app.modules.assistant.schemas import (
    AgentMessageRequest,
    UiActionInput,
    UiMediaInput,
    UiStateInput,
)
from tests.unit.test_intelligent_ui import CALCULATOR

USER = {"username": "alice", "active_role": "analyst", "roles": ["analyst"],
        "session_id": "device", "security_context_version": 1}


async def test_action_envelope_contains_server_derived_selection(ui_source, monkeypatch):
    import json

    from app.core.redis import session_store
    from tests.unit.test_intelligent_ui import MAP_SELECTION, MAP_TABLES

    block = await ui.compose_block(
        MAP_SELECTION, "Select a branch.", tables=MAP_TABLES, business=True,
        question="Branches", claims=None, language="en",
    )
    monkeypatch.setattr(router.assistant_repository, "ui_source", AsyncMock(return_value=block))

    @asynccontextmanager
    async def lock(_key):
        yield

    monkeypatch.setattr("app.modules.intelligence.engine_repository.metadata_lock", lock)
    monkeypatch.setattr(session_store, "_redis", SimpleNamespace(set=AsyncMock(return_value=True)))
    monkeypatch.setattr(router.assistant_repository, "ui_action_seen",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(router.assistant_repository, "learning_enabled",
                        AsyncMock(side_effect=RuntimeError("engine boundary")))
    body = AgentMessageRequest(content="Investigate", ui_action=UiActionInput(
        message_id="message", content_id="content-1", program_sha256=block["program_sha256"],
        action_id="detail", operation_id="00000000-0000-0000-0000-000000000000",
        state={"$selected": 0},
    ))
    with pytest.raises(RuntimeError, match="engine boundary"):
        await router.send_agent_message("agent", "thread", body,
            Request({"type": "http", "method": "POST", "path": "/"}), USER)
    context = json.loads(body.content.split("UI form input (user supplied):\n")[1])
    assert context["selected_rows_snapshot"][0]["row"]["branch"] == "Bandung"
    assert context["state"] == {}


def test_action_history_displays_message_without_internal_form_envelope():
    from datetime import datetime

    row = {"message_id": "message", "role": "user", "created_at": datetime.now(),
           "content": 'Explain Retail.\n\nUI form input (user supplied):\n{"state":{}}',
           "steps": [{"kind": "ui_action"}]}
    assert router._message_view(row, USER).content == "Explain Retail."
    row["steps"] = []
    assert router._message_view(row, USER).content == row["content"]


def test_disabled_ui_restores_complete_simulation_markdown_only_for_original_scope(monkeypatch):
    from datetime import datetime

    from app.core.config import settings

    monkeypatch.setattr(settings, "STUDIO_INTELLIGENT_UI_ENABLED", False)
    fallback = "Calculated assumptions:\n\n| Amount |\n| --- |\n| 100 |"
    row = {"message_id": "message", "role": "assistant", "content": "Calculated assumptions:",
           "created_at": datetime.now(),
           "security_context": {"principal": "alice", "active_role": "analyst",
                                "security_context_version": 1},
           "steps": [{"kind": "text", "text": "Calculated assumptions:"},
                     {"kind": "ui", "fallback": fallback,
                      "provenance": {"prose_rebuilt": True}}]}
    projected = router._message_view(row, USER)
    assert projected.content == fallback
    assert [step["text"] for step in projected.steps if step["kind"] == "text"] == [fallback]
    assert not any(step["kind"] == "ui" for step in projected.steps)
    row["security_context"]["active_role"] = "viewer"
    assert router._message_view(row, USER).content == "Calculated assumptions:"


@pytest.mark.parametrize("changed", [{}, {"principal": "bob"}, {"active_role": "viewer"},
                                    {"security_context_version": 2}])
def test_history_projection_keeps_ui_only_in_its_original_security_context(changed):
    from datetime import datetime

    stamp = {"principal": "alice", "active_role": "analyst", "security_context_version": 1,
             "session_id": "another-device", **changed}
    row = {"message_id": "message", "role": "assistant", "content": "Markdown fallback.",
           "created_at": datetime.now(), "security_context": stamp,
           "steps": [{"kind": "ui", "program": CALCULATOR, "evidence": {"x": []}},
                     {"kind": "answer"}]}
    projection = router._message_view(row, USER)
    assert any(step.get("kind") == "ui" for step in projection.steps) == (not changed)
    assert projection.content == "Markdown fallback."
    assert {"kind": "answer"} in projection.steps


@pytest.fixture
async def ui_source(monkeypatch):
    block = await ui.compose_block(CALCULATOR, "Adjust assumptions.", tables={}, business=False,
                                   question="Calculator", claims=None, language="en")
    monkeypatch.setattr(router, "_require_agent", AsyncMock())
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock())
    monkeypatch.setattr(router.assistant_repository, "ui_source", AsyncMock(return_value=block))
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    return block


async def test_state_uses_source_owner_and_current_role(ui_source, monkeypatch):
    get = AsyncMock(return_value={"revision": 0, "state": {"$units": 5}})
    monkeypatch.setattr(router.assistant_repository, "get_ui_state", get)
    value = await router.get_agent_ui_state("agent", "thread", "message", "content-1", USER)
    assert value["state"] == {"$units": 5}
    assert get.call_args.kwargs["user_name"] == "alice"
    assert get.call_args.kwargs["security_context"]["active_role"] == "analyst"
    monkeypatch.setattr(router.assistant_repository, "ui_source", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as refusal:
        await router.get_agent_ui_state("agent", "thread", "message", "content-1", USER)
    assert refusal.value.status_code == 404
    assert router.write_audit_log.call_args.kwargs["status"] == "DENIED"


async def test_changed_program_and_disabled_feature_fail_closed(ui_source, monkeypatch):
    with pytest.raises(HTTPException) as changed:
        await router.put_agent_ui_state("agent", "thread", "message", "content-1",
            UiStateInput(revision=0, program_sha256="b" * 64, state={"$units": 6}), USER)
    assert changed.value.status_code == 409
    ui_source["program"] += "\n$unexpected=1"
    with pytest.raises(HTTPException) as digest:
        await router.get_agent_ui_state("agent", "thread", "message", "content-1", USER)
    assert digest.value.status_code == 404
    monkeypatch.setattr(ui.settings, "STUDIO_INTELLIGENT_UI_ENABLED", False)
    with pytest.raises(HTTPException) as disabled:
        await router.get_agent_ui_state("agent", "thread", "message", "content-1", USER)
    assert disabled.value.status_code == 404


async def test_media_must_match_static_program_not_form_or_business_data(ui_source, monkeypatch):
    program = 'root = Col([SearchImage("London Eye","Landmark")])'
    block = await ui.compose_block(program, "Public landmark.", tables={}, business=False,
                                  question="Trip", claims=None, language="en")
    monkeypatch.setattr(router.assistant_repository, "ui_source", AsyncMock(return_value=block))
    media = AsyncMock(return_value={"url": "https://upload.wikimedia.org/example.jpg"})
    monkeypatch.setattr("app.modules.assistant.ui_media.public_media", media)
    await router.get_agent_ui_media("agent", "thread", "message", "content-1",
        UiMediaInput(type="SearchImage", props={"query": "London Eye", "alt": "Landmark"}), USER)
    assert media.await_count == 1
    with pytest.raises(HTTPException) as private:
        await router.get_agent_ui_media("agent", "thread", "message", "content-1",
            UiMediaInput(type="SearchImage", props={"query": "Private form value", "alt": "x"}),
            USER)
    assert private.value.status_code == 422 and media.await_count == 1
    with pytest.raises(ui.UiValidationError):
        await ui.parse_program(program, private_context=True)


@pytest.mark.parametrize("html", [
    '<a href="https://example.com">Leave</a>',
    '<meta http-equiv="refresh" content="0;url=https://example.com">',
    "<script>document['default'+'View']['loc'+'ation']='https://example.com'</script>",
    "<script>const x=document.defaultView; x.location='https://example.com'</script>",
    "<script>const x=([]).filter.constructor('return this')();</script>",
    '<button onclick="alert(1)">Run</button>',
    "<script>Object.getOwnPropertyDescriptor(document,'loc'+'ation').value.replace('https://example.com')</script>",
    "<script>const x=Object.values(document).find(v=>typeof v.replace==='function');x.replace('https://example.com')</script>",
    '<svg><a><animate attributeName="href" to="https://example.com"/></a></svg>',
])
async def test_html_cannot_navigate_or_escape_opaque_sandbox(html):
    import json

    with pytest.raises(ui.UiValidationError):
        await ui.parse_program('root = Col([HtmlView(' + json.dumps(html) + ',"Game")])')


@pytest.mark.parametrize("invalid", ["source", "state", "duplicate", "input"])
async def test_ui_action_refuses_forged_sources_state_and_replayed_operations(
    ui_source, monkeypatch, invalid,
):
    from app.core.redis import session_store

    @asynccontextmanager
    async def lock(_key):
        yield

    monkeypatch.setattr("app.modules.intelligence.engine_repository.metadata_lock", lock)
    monkeypatch.setattr(session_store, "_redis", SimpleNamespace(set=AsyncMock(return_value=True)))
    monkeypatch.setattr(router.assistant_repository, "ui_action_seen",
                        AsyncMock(return_value=invalid == "duplicate"))
    append = AsyncMock()
    monkeypatch.setattr(router.assistant_repository, "append_message", append)
    body = AgentMessageRequest(content="x" * 32_000 if invalid == "input" else "Continue",
                              ui_action=UiActionInput(
        message_id="message", content_id="content-1", program_sha256=ui_source["program_sha256"],
        action_id="forged" if invalid == "source" else "continue",
        operation_id="00000000-0000-0000-0000-000000000000",
        state={"$unknown": 8} if invalid == "state" else {"$units": 8},
    ))
    with pytest.raises(HTTPException) as refusal:
        await router.send_agent_message("agent", "thread", body,
            Request({"type": "http", "method": "POST", "path": "/"}), USER)
    assert refusal.value.status_code == (409 if invalid == "duplicate" else 422)
    append.assert_not_awaited()
    audit = router.write_audit_log.call_args.kwargs
    assert audit["status"] == "DENIED"
    assert "state" not in audit and "$units" not in str(audit)
