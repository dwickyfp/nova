"""Human-in-the-loop requests: the request and answer contract and its endpoint.

The browser validates answers too, but only the checks here are a boundary:
every answer is validated again against the stored request, and only the login,
active role and security-context version that opened a request may answer it,
for its own run and conversation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.assistant import human_input
from app.modules.assistant.consent import ConsentBroker
from app.modules.assistant.human_input import (
    HumanAnswer,
    HumanInputError,
    normalize_request,
    validate_answer,
)
from app.modules.assistant.schemas import HumanInputAnswerRequest
from app.modules.assistant.state import thread_store
from app.modules.assistant.tools import ToolInvocation
from tests.unit.test_assistant_consent_ownership import _signed_in

FORM = {
    "kind": "form",
    "title": "Which period and region?",
    "fields": {
        "type": "object",
        "properties": {
            "period": {"type": "object", "format": "date-range", "title": "Period"},
            "region": {"type": "string", "enum": ["West", "East"], "title": "Region"},
            "top": {"type": "integer", "minimum": 1, "maximum": 50, "title": "Top N"},
            "refunds": {"type": "boolean", "title": "Include refunds"},
            "note": {"type": "string", "title": "Note"},
            "day": {"type": "string", "format": "date", "title": "Day"},
        },
        "required": ["period", "region"],
    },
}


# ── requests ───────────────────────────────────────────────────────────────


def test_a_question_keeps_bounded_distinct_suggestions_and_free_text_by_default() -> None:
    request = normalize_request({
        "kind": "question", "title": "  Gross   or net? ",
        "choices": ["Net", "Net", "Gross", *[f"c{n}" for n in range(10)]],
    })
    assert request["title"] == "Gross or net?"
    assert request["choices"][:2] == ["Net", "Gross"] and len(request["choices"]) <= 6
    assert request["allow_free_text"] is True


def test_a_form_becomes_typed_widgets() -> None:
    request = normalize_request(FORM)
    widgets = {field["name"]: field["widget"] for field in request["fields"]}
    assert widgets == {
        "period": "date_range", "region": "select", "top": "integer", "refunds": "toggle",
        "note": "text", "day": "date",
    }
    assert request["required"] == ["period", "region"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"kind": "approval", "title": "Run it?"},
        {"kind": "question", "title": ""},
        {"kind": "question", "title": "x", "choices": [], "allow_free_text": False},
        {"kind": "choice", "title": "x", "options": [{"title": "only"}]},
        {"kind": "choice", "title": "x", "options": [{"id": "a", "title": "A"},
                                                     {"id": "a", "title": "B"}]},
        {"kind": "form", "title": "x", "fields": {"type": "object", "properties": {}}},
        {"kind": "form", "title": "x", "fields": {"properties": {"f": {"type": "array"}}}},
        {"kind": "form", "title": "x", "fields": {"properties": {"f": {"type": "string"}},
                                                  "required": ["g"]}},
        {"kind": "form", "title": "x", "fields": {"properties": {"bad name": {"type": "string"}}}},
    ],
)
def test_invalid_requests_go_back_to_the_model(arguments: dict) -> None:
    with pytest.raises(HumanInputError):
        normalize_request(arguments)


# ── answers ────────────────────────────────────────────────────────────────


def test_question_answers_respect_free_text() -> None:
    closed = normalize_request({
        "kind": "question", "title": "Which?", "choices": ["A", "B"], "allow_free_text": False,
    })
    assert validate_answer(closed, {"text": "B"}).summary == "You answered: B"
    with pytest.raises(HumanInputError):
        validate_answer(closed, {"text": "C"})
    with pytest.raises(HumanInputError):
        validate_answer(closed, {"text": "   "})


def test_choice_answers_name_a_real_option() -> None:
    request = normalize_request({
        "kind": "choice", "title": "Plan?",
        "options": [{"id": "a", "title": "By region"}, {"id": "b", "title": "By month"}],
    })
    answer = validate_answer(request, {"option_id": "b"})
    assert answer.value == {"option_id": "b", "title": "By month"}
    assert answer.summary == "You chose: By month"
    with pytest.raises(HumanInputError):
        validate_answer(request, {"option_id": "z"})


def test_form_answers_are_validated_field_by_field() -> None:
    request = normalize_request(FORM)
    answer = validate_answer(request, {"values": {
        "period": {"start": "2026-10-01", "end": "2026-10-31"}, "region": "West",
        "top": 10, "refunds": False, "note": "",
    }})
    assert answer.value["values"] == {
        "period": {"start": "2026-10-01", "end": "2026-10-31"}, "region": "West",
        "top": 10, "refunds": False,
    }
    assert answer.summary.startswith("You set: Period 2026-10-01 to 2026-10-31, Region West")
    for values in (
        {"region": "West"},  # required period missing
        {"period": {"start": "2026-10-31", "end": "2026-10-01"}, "region": "West"},
        {"period": {"start": "2026-02-30", "end": "2026-03-01"}, "region": "West"},
        {"period": {"start": "2026-10-01", "end": "2026-10-31"}, "region": "North"},
        {"period": {"start": "2026-10-01", "end": "2026-10-31"}, "region": "West", "top": 51},
        {"period": {"start": "2026-10-01", "end": "2026-10-31"}, "region": "West", "top": 2.5},
        {"period": {"start": "2026-10-01", "end": "2026-10-31"}, "region": "West",
         "refunds": "yes"},
        {"period": {"start": "2026-10-01", "end": "2026-10-31"}, "region": "West", "extra": 1},
    ):
        with pytest.raises(HumanInputError):
            validate_answer(request, {"values": values})


def test_an_action_approval_says_what_the_action_does() -> None:
    from app.modules.agents.tools.schedule_automation import ScheduleAutomationTool

    tool = ScheduleAutomationTool()
    call = ToolInvocation("c1", "schedule_automation", {
        "title": "Weekly revenue", "schedule_kind": "cron", "schedule_expr": "0 8 * * 1",
    })
    request = human_input.approval_request(
        tool, call, classification="destructive", preview=tool.preview(call),
        role="MARKETING", reason=human_input.approval_reason("destructive"),
    )
    assert request == {
        "kind": "approval", "title": "Allow this action?", "classification": "destructive",
        "reason": "changes_data", "role": "MARKETING",
        "action": "Schedule 'Weekly revenue' (cron: 0 8 * * 1)",
    }
    assert human_input.approval_reason("session_change") == "changes_role"


# ── the answer endpoint ────────────────────────────────────────────────────


@pytest.fixture
def endpoint(monkeypatch):
    from app.modules.agents import router as agents

    broker = ConsentBroker()
    monkeypatch.setattr(agents, "consent_broker", broker)
    monkeypatch.setattr(agents, "_require_agent", AsyncMock(return_value={"agent_id": "a1"}))
    monkeypatch.setattr(agents, "_require_agent_thread", AsyncMock(return_value={}))
    monkeypatch.setattr(agents, "write_audit_log", AsyncMock())
    user = _signed_in("alice")
    thread = thread_store.create(user_name="alice", title="Synthetic HITL fixture")
    yield agents, broker, user, thread
    broker.clear()
    thread_store.clear()


QUESTION = normalize_request({"kind": "question", "title": "Gross or net?",
                              "choices": ["Gross", "Net"]})


@pytest.mark.asyncio
async def test_the_owner_answers_a_question_for_its_run(endpoint) -> None:
    agents, broker, user, thread = endpoint
    future = broker.open("h1", thread_id=thread.thread_id, user=user, run_id="r1",
                         request=QUESTION)
    response = await agents.answer_agent_input(
        "a1", thread.thread_id, "r1", "h1",
        HumanInputAnswerRequest(action="answer", answer={"text": "Net"}), user=user,
    )
    assert response == {"request_id": "h1", "status": "answered"}
    answer = await future
    assert isinstance(answer, HumanAnswer) and answer.value == {"text": "Net"}


@pytest.mark.asyncio
async def test_skip_declines_without_an_answer(endpoint) -> None:
    agents, broker, user, thread = endpoint
    future = broker.open("h1", thread_id=thread.thread_id, user=user, run_id="r1",
                         request=QUESTION)
    await agents.answer_agent_input(
        "a1", thread.thread_id, "r1", "h1", HumanInputAnswerRequest(action="decline"), user=user,
    )
    assert (await future).status == human_input.DECLINED


@pytest.mark.asyncio
@pytest.mark.parametrize("forgery", ["other_user", "other_role", "new_version", "other_run",
                                     "other_thread"])
async def test_stale_or_forged_answers_are_not_found_and_leave_the_request(
    endpoint, forgery,
) -> None:
    agents, broker, user, thread = endpoint
    future = broker.open("h1", thread_id=thread.thread_id, user=user, run_id="r1",
                         request=QUESTION)
    caller, run, thread_id = dict(user), "r1", thread.thread_id
    if forgery == "other_user":
        caller = _signed_in("bob")
    elif forgery == "other_role":
        caller["active_role"] = "finance"
    elif forgery == "new_version":
        caller["security_context_version"] = int(user.get("security_context_version", 1)) + 1
    elif forgery == "other_run":
        run = "r2"
    else:
        thread_id = "another-thread"
    with pytest.raises(HTTPException) as raised:
        await agents.answer_agent_input(
            "a1", thread_id, run, "h1",
            HumanInputAnswerRequest(action="answer", answer={"text": "Net"}), user=caller,
        )
    assert raised.value.status_code == 404
    assert not future.done()


@pytest.mark.asyncio
async def test_invalid_answers_are_rejected_and_the_request_keeps_waiting(endpoint) -> None:
    agents, broker, user, thread = endpoint
    closed = normalize_request({"kind": "choice", "title": "Plan?", "options": [
        {"id": "a", "title": "A"}, {"id": "b", "title": "B"}]})
    future = broker.open("h1", thread_id=thread.thread_id, user=user, run_id="r1",
                         request=closed)
    for body in (
        HumanInputAnswerRequest(action="answer", answer={"option_id": "z"}),
        HumanInputAnswerRequest(action="allow_once"),
    ):
        with pytest.raises(HTTPException) as raised:
            await agents.answer_agent_input("a1", thread.thread_id, "r1", "h1", body, user=user)
        assert raised.value.status_code == 422
    assert not future.done()


@pytest.mark.asyncio
async def test_approvals_take_allow_once_or_deny_only(endpoint) -> None:
    agents, broker, user, thread = endpoint
    allowed = broker.open("c1", thread_id=thread.thread_id, user=user, run_id="r1",
                          classification="destructive")
    result = await agents.answer_agent_input(
        "a1", thread.thread_id, "r1", "c1", HumanInputAnswerRequest(action="allow_once"),
        user=user,
    )
    assert result == {"request_id": "c1", "status": "allowed"}
    assert await allowed is True

    broker.open("c2", thread_id=thread.thread_id, user=user, run_id="r1",
                classification="destructive")
    with pytest.raises(HTTPException) as raised:
        await agents.answer_agent_input(
            "a1", thread.thread_id, "r1", "c2",
            HumanInputAnswerRequest(action="answer", answer={"text": "yes"}), user=user,
        )
    assert raised.value.status_code == 422
    with pytest.raises(ValidationError):
        HumanInputAnswerRequest(action="allow_session")
    with pytest.raises(ValidationError):
        HumanInputAnswerRequest(action="allow_once", edited_arguments={"sql": "SELECT 2"})
    denied = broker.open("c3", thread_id=thread.thread_id, user=user, run_id="r1")
    await agents.answer_agent_input(
        "a1", thread.thread_id, "r1", "c3", HumanInputAnswerRequest(action="deny"), user=user,
    )
    assert await denied is False


@pytest.mark.asyncio
async def test_an_answered_request_cannot_be_answered_again(endpoint) -> None:
    agents, broker, user, thread = endpoint
    broker.open("h1", thread_id=thread.thread_id, user=user, run_id="r1", request=QUESTION)
    body = HumanInputAnswerRequest(action="answer", answer={"text": "Net"})
    await agents.answer_agent_input("a1", thread.thread_id, "r1", "h1", body, user=user)
    with pytest.raises(HTTPException) as raised:
        await agents.answer_agent_input("a1", thread.thread_id, "r1", "h1", body, user=user)
    assert raised.value.status_code == 404


def test_planning_spells_the_tool_name_the_loop_offers() -> None:
    from app.modules.assistant import planning

    assert planning.HUMAN_INPUT_TOOL == human_input.TOOL_NAME


def test_a_badge_that_repeats_the_recommended_mark_is_dropped():
    request = normalize_request({
        "kind": "choice", "title": "Which plan?",
        "options": [
            {"id": "a", "title": "By region", "badges": ["Recommended", "Fast"]},
            {"id": "b", "title": "By month", "badges": ["Suggested"]},
        ],
    })
    first, second = request["options"]
    assert first["badges"] == ["Fast"] and first["recommended"] is True
    assert "badges" not in second and second["recommended"] is True
