"""Release QA trajectories for what Neva knows about the dashboard on screen.

Each case runs the shared ``AssistantLoop`` with a scripted model and the native
tools over the faked document and dataset services.

* NDV-AGENT-005: numbers asked about the screen carry its filters and selections.
* NDV-AGENT-014: a change whose save fails is shown as failed, never as saved.
* NDV-AGENT-018: a dashboard named by a word several titles share is offered as
  a choice; nothing is changed until the person picks one.
* NDV-AGENT-019 / NDV-REVIS-014 / NDV-DATA-017: a new chat on an existing
  dashboard reads its latest version as the current role, makes no copy of the
  dashboard or its dataset, and a role that cannot open it gets nothing.

Nova has no ``@`` mention for dashboards: a new chat reaches one by having it
open beside the chat or by naming it, which is what these cases exercise.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import NovaException
from app.modules.agents.tools import native_dataviz
from app.modules.assistant.schemas import DatavizTurnInput
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.unit.test_native_dataviz_assistant_tools import (
    DASHBOARD,
    DATASET,
    PAGE,
    FakeDatasets,
    FakeDocuments,
)

USER = {"username": "alice", "active_role": "ANALYST", "roles": ["ANALYST"],
        "assigned_roles": ["ANALYST"], "security_context_version": 3, "session_id": "s-alice"}
KPI = {"title": "Total revenue", "visual": "kpi", "measures": [{"field": "revenue"}]}
ON_SCREEN = [
    {"source": "filter", "label": "City", "text": "Surabaya", "dataset_id": str(DATASET),
     "field_id": "city", "values": ["Surabaya"]},
    {"source": "selection", "label": "Order date: Mar 2026", "text": "Selected Mar 2026",
     "dataset_id": str(DATASET), "field_id": "order_date",
     "values": ["2026-03-01", "2026-03-31"]},
]


@pytest.fixture(autouse=True)
def _audit(monkeypatch):
    writer = AsyncMock()
    monkeypatch.setattr("app.common.audit.write_audit_log", writer)
    return writer


#: (tool name, outcome) for every native tool run in the test.
OUTCOMES: list[tuple[str, Any]] = []


@pytest.fixture(autouse=True)
def _outcomes(monkeypatch):
    OUTCOMES.clear()
    original = native_dataviz._Tool.run

    async def run(self, invocation, context):
        outcome = await original(self, invocation, context)
        OUTCOMES.append((self.name, outcome))
        return outcome

    monkeypatch.setattr(native_dataviz._Tool, "run", run)
    return OUTCOMES


@pytest.fixture
def docs(monkeypatch) -> FakeDocuments:
    fake = FakeDocuments()
    monkeypatch.setattr(native_dataviz, "_documents", lambda: fake)
    return fake


@pytest.fixture
def data(monkeypatch) -> FakeDatasets:
    fake = FakeDatasets()
    monkeypatch.setattr(native_dataviz, "_datasets", lambda: fake)
    return fake


def _plan(intent: str, tools: list[str], required: list[str]) -> dict:
    return {"intent": intent, "tools": tools, "required_tools": required, "skills": [],
            "ml_task": None}


async def _turn(script: list[str], question: str, *, plan: dict, opened: dict | None,
                thread: str = "t1", consent: Any = True, settled: tuple[str, ...] = ()):
    registry = ToolRegistry()
    native_dataviz.register(registry)
    asked: list[str] = []

    async def resolve(invocation, _classification):
        asked.append(invocation.tool_name)
        return consent

    context = LoopContext("alice", user=USER, thread_id=thread, steps=[], quality_facts={})
    if opened is not None:
        # As the router passes it: validated, then dumped as JSON.
        native = DatavizTurnInput.model_validate({"native_dashboard": opened}).native_dashboard
        context.dataviz = {"native_dashboard": native.model_dump(mode="json")}
    context.dashboard_tools = native_dataviz.TOOL_NAMES
    context.settled_capabilities = settled
    context.question_back = "native_dataset_query" in settled
    if plan["intent"] == "compound_analytics":
        context.capability_aliases = {"semantic_query": "native_dataset_query"}
    frames = [
        frame async for frame in AssistantLoop(
            provider=ScriptedProvider(script, turn_plan=plan), registry=registry,
            system_prompt="test",
        ).run(
            thread=AssistantThread(thread, "alice", "Dataviz"), user_content=question,
            context=context, resolve_consent=resolve,
        )
    ]
    return frames, asked


def _events(frames: list[str], name: str) -> list[dict]:
    return [json.loads(frame.split("data: ", 1)[1]) for frame in frames
            if frame.startswith(f"event: {name}\n")]


def _answer(frames: list[str]) -> str:
    return "".join(event.get("text", "") for event in _events(frames, "text_delta"))


def _finish(frames: list[str]) -> str | None:
    done = _events(frames, "done")
    return done[-1].get("finish_reason") if done else None


def _results(tool: str) -> list[Any]:
    return [outcome for name, outcome in OUTCOMES if name == tool]


def _statuses(frames: list[str]) -> list[str]:
    return [item["status"] for item in _events(frames, "tool_status")
            if item["status"] in ("done", "failed", "succeeded", "completed")]


QUESTION = _plan("compound_analytics", ["native_dataset_query"], ["semantic_query"])
CHANGE = _plan("ui_operation", ["native_dashboard_read", "native_dashboard_add_charts"],
               ["native_dashboard_add_charts"])
OPEN = {"dashboard_id": str(DASHBOARD), "revision": 1, "page_id": str(PAGE)}


# ── NDV-AGENT-005: the numbers match the filters on screen ───────────────


async def test_a_question_about_the_screen_queries_with_its_filters(docs, data):
    text, read = await native_dataviz.brief(USER, {**OPEN, "view_filters": ON_SCREEN})
    assert read and '"field_id": "city"' in text and "native_dataset_query" in text
    frames, asked = await _turn([
        tool_call_frame("q1", name="native_dataset_query", arguments={
            "dataset_id": str(DATASET), "measures": [{"field": "revenue"}],
            "filters": [
                {"field": "city", "op": "in", "values": ["Surabaya"]},
                {"field": "order_date", "op": "between",
                 "values": ["2026-03-01", "2026-03-31"]},
            ]}),
        text_frame("With the City filter and your March selection applied, revenue is "
                   "Surabaya 1,573,810,277.50 and Malang 12."),
    ], "what is the revenue I'm looking at?", plan=QUESTION,
        opened={**OPEN, "view_filters": ON_SCREEN})
    assert asked == [] and len(data.requests) == 1
    filters = json.dumps(data.requests[0].model_dump(mode="json")["filters"])
    assert '"city"' in filters and "Surabaya" in filters
    assert '"order_date"' in filters and "2026-03-31" in filters
    assert "City filter" in _answer(frames)
    assert not docs.applied and not _events(frames, "dashboard_changed")


# ── NDV-AGENT-014: a failed save is never shown as saved ─────────────────


@pytest.mark.parametrize("failure", [
    NovaException("Metadata store unavailable", status_code=503),
    RuntimeError("connection reset"),
])
async def test_a_change_whose_save_fails_is_reported_as_failed(docs, failure):
    docs.crash = failure
    docs.crash_commits = False  # nothing was committed
    frames, asked = await _turn([
        tool_call_frame("c1", name="native_dashboard_add_charts", arguments={"charts": [KPI]}),
        text_frame("I added the Total revenue KPI to the dashboard (version 2)."),
    ], "add a revenue KPI", plan=CHANGE, opened=OPEN)
    assert asked == ["native_dashboard_add_charts"]
    assert len(docs.docs[DASHBOARD]) == 1 and not docs.receipts.get(docs.applied[0].operation_id)
    assert not _events(frames, "dashboard_changed")
    result = _results("native_dashboard_add_charts")
    assert result and all(not item.ok for item in result)
    assert "failed" in _statuses(frames)
    assert "I added" not in _answer(frames)
    assert _finish(frames) != "stop"


async def test_a_save_whose_reply_was_lost_but_committed_is_confirmed_once(docs):
    docs.crash = RuntimeError("connection reset")
    docs.crash_commits = True  # the save landed; only the reply was lost
    frames, _ = await _turn([
        tool_call_frame("c1", name="native_dashboard_add_charts", arguments={"charts": [KPI]}),
        text_frame("I added the Total revenue KPI to the dashboard (version 2)."),
    ], "add a revenue KPI", plan=CHANGE, opened=OPEN)
    changed = _events(frames, "dashboard_changed")
    assert len(changed) == 1 and changed[0]["revision"] == 2
    assert len(docs.docs[DASHBOARD]) == 2 and len(docs.applied) == 1


# ── NDV-AGENT-018: a name several dashboards share ───────────────────────


async def test_an_ambiguous_name_lists_the_choices_and_changes_nothing(docs, monkeypatch):
    other = uuid4()

    async def browse(user, workspace_id, *, kind, draft, limit):
        return [{"resource_id": DASHBOARD, "title": "Sales Jakarta", "revision": 3,
                 "current_publication_id": None},
                {"resource_id": other, "title": "Sales Surabaya", "revision": 1,
                 "current_publication_id": None},
                {"resource_id": uuid4(), "title": "Inventory", "revision": 1,
                 "current_publication_id": None}]

    docs.browse = browse

    async def spaces(user):
        from types import SimpleNamespace
        return [SimpleNamespace(id="abc", name="Team")]

    monkeypatch.setattr(native_dataviz, "_spaces", spaces)
    frames, asked = await _turn([
        tool_call_frame("l1", name="native_dashboard_list", arguments={"search": "sales"}),
        text_frame("Two dashboards match “sales”: Sales Jakarta and Sales Surabaya, both in "
                   "Team. Which one should I add the KPI to?"),
    ], "add a revenue KPI to the sales dashboard", opened=None,
        plan=_plan("ui_operation", ["native_dashboard_list", "native_dashboard_add_charts"],
                   ["native_dashboard_list"]))
    listed = _results("native_dashboard_list")
    assert listed and listed[0].ok
    titles = {item["title"] for item in listed[0].data["dashboards"]}
    assert titles == {"Sales Jakarta", "Sales Surabaya"}
    assert not _results("native_dashboard_add_charts") and asked == []
    assert not docs.applied and not docs.created and not _events(frames, "dashboard_changed")
    assert "Which one" in _answer(frames)


# ── NDV-AGENT-019 / NDV-REVIS-014 / NDV-DATA-017: a new chat, same dashboard ─


async def test_a_new_chat_reads_the_latest_version_and_copies_nothing(docs, data):
    # Someone saved a newer version after the first chat.
    first, _ = await _turn([
        tool_call_frame("r1", name="native_dashboard_read",
                        arguments={"dashboard_id": str(DASHBOARD)}),
        text_frame("This is Sales."),
    ], "what is on this dashboard?", opened=OPEN, thread="chat-1",
        plan=_plan("compound_analytics", ["native_dashboard_read", "native_dataset_query"],
                   ["semantic_query"]))
    await _turn([
        tool_call_frame("c1", name="native_dashboard_add_charts", arguments={"charts": [KPI]}),
        text_frame("Added the KPI (version 2)."),
    ], "add a revenue KPI", plan=CHANGE, opened=OPEN, thread="chat-1")
    created_before = list(docs.created)
    text, read = await native_dataviz.brief(USER, {**OPEN, "revision": 1})
    assert read and "now at revision 2" in text
    second, _ = await _turn([
        tool_call_frame("r2", name="native_dashboard_read",
                        arguments={"dashboard_id": str(DASHBOARD)}),
        tool_call_frame("q2", name="native_dataset_query", arguments={
            "dataset_id": str(DATASET), "measures": [{"field": "revenue"}]}),
        text_frame("This is Sales, version 2."),
    ], "open the sales dashboard", opened={**OPEN, "revision": 1}, thread="chat-2",
        plan=_plan("compound_analytics", ["native_dashboard_read", "native_dataset_query"],
                   ["semantic_query"]))
    reads = _results("native_dashboard_read")
    assert len(reads) == 2 and all(item.ok for item in reads)
    one, two = reads[0].data, reads[1].data
    assert one["revision"] == 1 and two["revision"] == 2
    assert [item["dataset_id"] for item in one["datasets"]] == [str(DATASET)]
    assert [(item["dataset_id"], item.get("revision")) for item in two["datasets"]] == [
        (item["dataset_id"], item.get("revision")) for item in one["datasets"]]
    # Reading in the second chat created nothing: no dashboard, chart or dataset copy.
    assert docs.created == created_before
    assert set(docs.docs) == {DASHBOARD, DATASET, UUID(str(docs.created[0][1].chart_id)),
                              *{key for key in docs.docs if key not in (DASHBOARD, DATASET)}}
    assert len([key for key in docs.docs if key == DATASET]) == 1
    assert data.requests[-1].revision == 1  # the dataset revision the dashboard pins


async def test_a_new_chat_as_a_role_that_cannot_open_it_gets_nothing(docs, data):
    async def denied(user, identity, *, draft=False, revision=None):
        raise NovaException("Metadata operation is not authorized", status_code=403)

    docs.read = denied
    text, read = await native_dataviz.brief(USER, OPEN)
    assert not read and "could not be read" in text and "Sales" not in text
    frames, asked = await _turn([
        tool_call_frame("r1", name="native_dashboard_read",
                        arguments={"dashboard_id": str(DASHBOARD)}),
        tool_call_frame("q1", name="native_dataset_query", arguments={
            "dataset_id": str(DATASET), "measures": [{"field": "revenue"}]}),
        text_frame("I can't open that dashboard with your current role."),
    ], "open the sales dashboard", opened=OPEN, thread="chat-3",
        plan=_plan("compound_analytics", ["native_dashboard_read", "native_dataset_query"],
                   ["semantic_query"]))
    read_result = _results("native_dashboard_read")
    assert read_result and not read_result[0].ok and not read_result[0].data
    assert "Sales" not in (read_result[0].error or "")
    assert not docs.applied and not docs.created


# ── NDV-AGENT-013 / NDV-AGENT-014: the Neva pointer follows the real change ─


async def test_the_pointer_event_names_the_widget_the_pending_change_touches(docs):
    from tests.unit.test_native_dataviz_assistant_tools import WIDGET

    frames, asked = await _turn([
        tool_call_frame("c1", name="native_chart_update",
                        arguments={"widget_id": str(WIDGET), "visual": "line"}),
        text_frame("I changed Revenue by city to a line chart (version 2)."),
    ], "make it a line chart", opened=OPEN,
        plan=_plan("ui_operation", ["native_chart_update"], ["native_chart_update"]))
    activity = _events(frames, "dashboard_activity")
    calls = _events(frames, "tool_call")
    assert len(activity) == 1 and activity[0]["tool_call_id"] == calls[0]["tool_call_id"]
    assert activity[0] == {**activity[0], "dashboard_id": str(DASHBOARD), "action": "change",
                           "widget_ids": [str(WIDGET)], "page_id": str(PAGE)}
    # The pointer is announced before the change is saved, and the save is
    # confirmed separately for the same call.
    names = [frame.split("\n", 1)[0] for frame in frames]
    assert names.index("event: dashboard_activity") < names.index("event: dashboard_changed")
    assert _events(frames, "dashboard_changed")[0]["tool_call_id"] == calls[0]["tool_call_id"]


async def test_a_failed_save_keeps_the_pointer_event_but_sends_no_change(docs):
    docs.crash = NovaException("Metadata store unavailable", status_code=503)
    docs.crash_commits = False
    frames, _ = await _turn([
        tool_call_frame("c1", name="native_dashboard_arrange", arguments={"remove": [
            "55555555-5555-4555-8555-555555555555"]}),
        text_frame("I removed the chart (version 2)."),
    ], "remove the chart", opened=OPEN,
        plan=_plan("ui_operation", ["native_dashboard_arrange"], ["native_dashboard_arrange"]))
    activity = _events(frames, "dashboard_activity")
    assert activity and activity[0]["action"] == "remove"
    assert activity[0]["widget_ids"] == ["55555555-5555-4555-8555-555555555555"]
    failed = [item for item in _events(frames, "tool_status")
              if item["tool_call_id"] == activity[0]["tool_call_id"]]
    assert failed[-1]["status"] == "failed"
    assert not _events(frames, "dashboard_changed")


async def test_reading_sends_no_pointer_event(docs, data):
    frames, _ = await _turn([
        tool_call_frame("r1", name="native_dashboard_read",
                        arguments={"dashboard_id": str(DASHBOARD)}),
        text_frame("This is Sales."),
    ], "what is on this dashboard?", opened=OPEN,
        plan=_plan("ui_operation", ["native_dashboard_read"], ["native_dashboard_read"]))
    assert _results("native_dashboard_read") and not _events(frames, "dashboard_activity")


# ── Live attempt 1: a question back is the whole answer ─────────────────
# The planner routed "add a KPI to <name>" as a data question (the governed
# query required). When the name fits several dashboards, or the page on screen
# is gone, the answer is a question back; it must reach the person.


async def test_an_ambiguous_name_on_a_data_route_still_asks(docs, monkeypatch):
    async def browse(user, workspace_id, *, kind, draft, limit):
        return [{"resource_id": uuid4(), "title": title, "revision": 1,
                 "current_publication_id": None} for title in ("QA Sales North", "QA Sales South")]

    async def spaces(user):
        from types import SimpleNamespace
        return [SimpleNamespace(id="abc", name="Team")]

    docs.browse = browse
    monkeypatch.setattr(native_dataviz, "_spaces", spaces)
    frames, asked = await _turn([
        tool_call_frame("l1", name="native_dashboard_list", arguments={"search": "QA Sales"}),
        text_frame("Two dashboards match: QA Sales North and QA Sales South. Which one?"),
    ], "add a KPI of the total amount to my QA Sales dashboard", opened=None,
        plan=_plan("compound_analytics", ["native_dashboard_list", "native_dataset_query"],
                   ["semantic_query"]))
    assert _finish(frames) == "stop", _finish(frames)
    assert "North" in _answer(frames) and "South" in _answer(frames)
    assert asked == [] and not docs.applied


async def test_a_removed_page_on_screen_still_asks(docs):
    gone = {**OPEN, "page_id": str(uuid4())}
    _, settled = await native_dataviz.brief_settling(USER, gone)
    frames, asked = await _turn([
        text_frame("The page you had open was removed. Which page should the KPI go on: "
                   "Overview?"),
    ], "add a KPI of the total amount to this page", opened=gone, settled=settled,
        plan=_plan("compound_analytics", ["native_dashboard_read", "native_dataset_query"],
                   ["semantic_query"]))
    assert _finish(frames) == "stop", _finish(frames)
    assert "removed" in _answer(frames) and asked == [] and not docs.applied


async def test_a_question_back_turn_still_cannot_state_unverified_numbers(docs):
    gone = {**OPEN, "page_id": str(uuid4())}
    _, settled = await native_dataviz.brief_settling(USER, gone)
    frames, _ = await _turn([text_frame("Total revenue is 9,999.")],
                            "what is the total revenue on this page", opened=gone, settled=settled,
                            plan=_plan("compound_analytics",
                                       ["native_dashboard_read", "native_dataset_query"],
                                       ["semantic_query"]))
    assert "9,999" not in _answer(frames)


async def test_a_question_back_settles_a_required_change_too(docs, monkeypatch):
    # Live attempt 2: "add a KPI to <name>" was planned as a page action that
    # requires the change tool itself.
    async def browse(user, workspace_id, *, kind, draft, limit):
        return [{"resource_id": uuid4(), "title": title, "revision": 1,
                 "current_publication_id": None} for title in ("QA Sales North", "QA Sales South")]

    async def spaces(user):
        from types import SimpleNamespace
        return [SimpleNamespace(id="abc", name="Team")]

    docs.browse = browse
    monkeypatch.setattr(native_dataviz, "_spaces", spaces)
    # Live attempt 5: the plan also required reading the dashboard and its dataset.
    change = _plan("ui_operation", ["native_dashboard_list", "native_dashboard_read",
                                    "native_dataset_describe", "native_dashboard_add_charts"],
                   ["native_dashboard_read", "native_dataset_describe",
                    "native_dashboard_add_charts"])
    frames, asked = await _turn([
        tool_call_frame("l1", name="native_dashboard_list", arguments={"search": "QA Sales"}),
        text_frame("Two dashboards match: QA Sales North and QA Sales South. Which one?"),
    ], "add a KPI of the total amount to my QA Sales dashboard", opened=None, plan=change)
    assert _finish(frames) == "stop" and "Which one" in _answer(frames)
    assert asked == [] and not docs.applied
    gone = {**OPEN, "page_id": str(uuid4())}
    _, settled = await native_dataviz.brief_settling(USER, gone)
    frames, asked = await _turn([text_frame("That page was removed. Which page should I use?")],
                                "add a KPI to this page", opened=gone, settled=settled,
                                plan=_plan("ui_operation", ["native_dashboard_add_charts"],
                                           ["native_dashboard_add_charts"]))
    assert _finish(frames) == "stop" and "removed" in _answer(frames)
    assert asked == [] and not docs.applied
