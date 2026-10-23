"""Executable UI preserves the assistant's evidence and persistence boundaries."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.assistant import intelligent_ui as ui
from app.modules.assistant import repository as repository_module
from app.modules.assistant.repository import AssistantRepository
from app.modules.assistant.service import LoopContext, PendingArtifact, _ordered_output_frames

CALCULATOR = ('root = Col([Title("Assumptions calculator"), Slider($units,"Units",1,100),'
              'Text("Total: "+($units*20)),Button("Continue",'
              '@ToAssistant("Use these assumptions"),"continue")])\n$units=5')
TABLES = {"e1": {"columns": ["region", "revenue"], "rows": [["East", 42], ["West", 58]]}}
STAMP = {"principal": "alice", "active_role": "analyst", "security_context_version": 1,
         "session_id": "device-a"}
MAP_SELECTION = ('$radius=130\n$selected=-1\nroot=Col(['
                 'GeoMap(@Top(@Between(@Evidence("places"),"distance",0,$radius),'
                 '"distance",10),"lon","lat",null,"branch","Branches",$selected),'
                 'Button("Investigate",@ToAssistant("Investigate selected branch"),"detail")])')
MAP_TABLES = {"places": {"columns": ["branch", "lon", "lat", "distance"],
                        "rows": [["Jakarta", 106.8, -6.2, 0], ["Bandung", 107.6, -6.9, 120]]}}


async def test_simulation_prose_cannot_invent_changes_outside_evaluated_results():
    program = ('$total=8000000\nroot=Col([Chart(['
               '{name:"Venue",amount:$total*0.25},{name:"Konsumsi",amount:$total*0.2}'
               '],"name","amount","bar")])')
    block = await ui.compose_block(
        program, "Ini asumsi perencanaan. Konsumsi naik Rp 250.000.",
        tables={}, business=False, question="Simulasikan anggaran Rp 8.000.000.",
        claims=None, language="id",
    )
    assert block["fallback"] == "Ini asumsi perencanaan."
    assert block["provenance"]["prose_adjusted"] is True
    assert block["provenance"]["scope"] == "user_simulation"
    assert block["evidence"] == {}


async def test_calculator_text_also_checks_prose_against_evaluated_arithmetic():
    block = await ui.compose_block(
        CALCULATOR, "Total is 999.", tables={}, business=False,
        question="Build a calculator.", claims=None, language="en",
    )
    assert "999" not in block["fallback"]
    assert "| 100 |" in block["fallback"]
    assert block["provenance"]["prose_adjusted"]
    assert block["provenance"]["prose_rebuilt"]


@pytest.mark.parametrize("text", [
    "Venue is 2000000. Konsumsi is 1600000.",
    "These are planning assumptions.",
])
def test_simulation_retains_consistent_or_qualitative_prose(text):
    result = ui._simulation_prose(
        text, [{"columns": ["name", "amount"],
                "rows": [["Venue", 2000000], ["Konsumsi", 1600000]]}],
        question="Plan an event.", language="en",
    )
    assert result == (text, False)


def test_simulation_rebuilds_a_conflicting_markdown_table_from_computed_rows():
    text, adjusted = ui._simulation_prose(
        "| name | amount |\n| --- | --- |\n| Venue | 999 |",
        [{"columns": ["name", "amount"], "rows": [["Venue", 2000000], ["A|B", None]]},
         {"columns": ["Calculated value"], "rows": [[100]]}],
        question="Plan an event.", language="en",
    )
    assert adjusted
    assert "999" not in text
    assert "| Venue | 2000000 |" in text
    assert r"| A\|B |  |" in text
    assert "simulation assumptions" in text
    assert "Calculated value" not in text


async def test_conditional_actions_revalidate_source_instead_of_stale_action_metadata():
    program = ('$show=false\nroot=Col([$show ? Button("Ask",@ToAssistant("Explain"),"ask")'
               ' : Text("Fill inputs")])')
    block = await ui.compose_block(
        program, "Fill inputs.", tables={}, business=False,
        question="Form", claims=None, language="en",
    )
    assert block["actions"] == [{"id": "ask", "assistant": True}]
    block["actions"] = []
    context = await ui.action_context(block, {"$show": True}, action_id="ask")
    assert context["state"] == {"$show": True}
    with pytest.raises(ui.UiValidationError, match="Invalid UI action source"):
        await ui.action_context(block, {"$show": True}, action_id="forged")


async def test_hidden_business_visual_cannot_introduce_fabricated_rows():
    with pytest.raises(ui.UiValidationError):
        await ui.parse_program(
            '$show=false\nroot=Col([$show ? Table([{revenue:999}]) : Text("Ready")])',
            business=True,
        )


async def test_action_selection_resolves_scoped_evidence_after_filters_and_sort():
    block = await ui.compose_block(
        MAP_SELECTION, "Select a branch.", tables=MAP_TABLES, business=True,
        question="Branches", claims=None, language="en",
    )
    context = await ui.action_context(block, {"$selected": 0})
    assert context["state"] == {}
    selected = context["selected_rows_snapshot"]
    assert selected == [{"title": "Branches", "row": {
        "branch": "Bandung", "lon": 107.6, "lat": -6.9, "distance": 120,
    }, "displayed_rows": 2}]
    filtered = await ui.action_context(block, {"$selected": 0, "$radius": 0})
    assert filtered["selected_rows_snapshot"][0]["row"]["branch"] == "Jakarta"
    assert filtered["state"] == {"$radius": 0}
    stale = await ui.action_context(block, {"$selected": 1, "$radius": 0})
    assert stale["selected_rows_snapshot"][0]["row"] is None
    with pytest.raises(ui.UiValidationError):
        await ui.action_context(block, {"$selected": {"branch": "Forged"}})
    with pytest.raises(ui.UiValidationError):
        await ui.action_context(block, {"$undeclared": 1})


async def test_object_evidence_rows_bind_by_column_name_without_inventing_missing_values():
    tables = {"e1": {"columns": [{"name": "region"}, {"name": "revenue"}],
                     "rows": [{"revenue": 42, "region": "East", "unselected": 999},
                              {"region": "West"}]}}
    block = await ui.compose_block(
        'root = Col([Table(@Evidence("e1"))])', "Verified regional rows.",
        tables=tables, business=True, question="Show the rows", claims=None, language="en",
    )
    assert block["evidence"] == {"e1": [{"region": "East", "revenue": 42},
                                       {"region": "West", "revenue": None}]}


@pytest.mark.parametrize("count", [199, 200, 207])
async def test_evidence_transport_reports_omitted_rows(count):
    tables = {"e1": {"columns": ["name"], "rows": [["Fixture"] for _ in range(count)]}}
    block = await ui.compose_block(
        'root = Col([Table(@Evidence("e1"))])', "Fixture result rows.",
        tables=tables, business=True, question="Show the rows", claims=None, language="en",
    )
    assert len(block["evidence"]["e1"]) == min(count, 200)
    assert block["provenance"]["omitted_rows"] == max(0, count - 200)


async def test_smart_preserves_sealed_ui_and_assigns_distinct_resume_cursors(monkeypatch):
    from app.modules.agents import router
    from app.modules.agents.harness_worker import _root_artifacts

    context = LoopContext(user_name="alice", steps=[])
    block = await ui.compose_block(
        CALCULATOR, "100 units at 20 each totals 2000.",
        tables={}, business=False, question="Assumption calculator", claims=None, language="en",
    )
    _ordered_output_frames("Adjust the assumptions.", [PendingArtifact("ui", block)], context)
    artifacts = _root_artifacts(context.steps)
    assert [item["kind"] for item in artifacts] == ["text", "ui"]
    assert artifacts[1]["program_sha256"] == block["program_sha256"]
    assert artifacts[1]["content_id"] == "content-1"
    monkeypatch.setattr(router.harness_repository, "events_page", AsyncMock(return_value=[{
        "event_id": 1_792_512_000_000_00, "run_id": "run", "type": "agent_completed",
        "payload": {"answer": "Adjust the assumptions.", "artifacts": artifacts},
    }]))

    async def collect(after):
        return [frame async for frame in router._stream_auto_events("run", after)]

    frames = await collect(-1)
    decoded = [(frame.splitlines()[0], json.loads(frame.split("data: ", 1)[1]))
               for frame in frames]
    assert all(isinstance(value["sequence"], str) for _, value in decoded)
    cursors = [int(value["sequence"]) for _, value in decoded]
    assert cursors == sorted(set(cursors))
    assert any(kind == "event: ui" for kind, _ in decoded)
    assert any(kind == "event: content_block_done" for kind, _ in decoded)
    for index, cursor in enumerate(cursors[:-1]):
        assert await collect(cursor) == frames[index + 1:]


async def test_official_parser_validates_functional_programs_and_bound_evidence():
    result = await ui.parse_program(CALCULATOR)
    assert result["state"] == {"$units": 5}
    assert result["actions"] == [{"id": "continue", "assistant": True}]
    assert "Total: 100" in result["display_text"]
    block = await ui.compose_block(
        'root = Col([Kpi(@Evidence("e1"),"revenue","Revenue"),'
        'Chart(@Evidence("e1"),"region","revenue"),Table(@Evidence("e1"))])',
        "Verified regional revenue.", tables=TABLES, business=True,
        question="Show regional revenue", claims=None, language="en",
    )
    assert block["evidence"] == {"e1": [{"region": "East", "revenue": 42},
                                          {"region": "West", "revenue": 58}]}
    assert block["provenance"]["evidence_ids"] == ["e1"]
    filtered = await ui.parse_program(
        'root = Col([Chart(@Filter(@Evidence("e1"),"region","==",$region),'
        '"region","revenue")])\n$region="East"', tables=TABLES, business=True,
    )
    assert filtered["evidence_ids"] == ["e1"]


@pytest.mark.parametrize("business", [True, False])
async def test_public_stream_keeps_ui_provenance_but_removes_security_scope(business):
    from app.modules.agents.public_projections import sanitize_workflow_sse

    program = ('root = Col([Table(@Evidence("e1"))])' if business else CALCULATOR)
    block = await ui.compose_block(
        program, "Verified results." if business else "Adjust assumptions.",
        tables=TABLES if business else {}, business=business,
        question="Show results", claims=None, language="en",
    )
    context = LoopContext(user_name="alice", steps=[])
    frames = _ordered_output_frames("Results.", [PendingArtifact("ui", block)], context)
    frame = next(frame for frame in frames if frame.startswith("event: ui\n"))
    payload = json.loads(sanitize_workflow_sse(frame).split("data: ", 1)[1])
    assert payload["payload"]["provenance"] == block["provenance"]
    security = 'event: ui\ndata: {"scope":{"principal":"alice","active_role":"analyst"}}\n\n'
    assert "principal" not in sanitize_workflow_sse(security)


async def test_decimal_result_strings_support_local_verified_aggregation():
    from decimal import Decimal

    tables = {"e1": {"columns": ["region", "category", "revenue"],
                     "rows": [["East", "sales", Decimal("12.50")],
                              ["East", "sales", Decimal("7.25")]]}}
    result = await ui.parse_program(
        'root = Col([Text(@Pivot(@Evidence("e1"),"region","category","revenue")[0].sales)])',
        tables=tables, business=True,
    )
    assert "19.75" in result["display_text"]
    assert result["evidence_ids"] == ["e1"]


@pytest.mark.parametrize("program", [
    'root = Col([Pressable("Outer",@Set($n,1),"outer",'
    '[Button("Inner",@ToAssistant("Continue"),"inner")])])\n$n=0',
    'root = Col([Table(@Evidence("fake"))])',
    'root = Col([Table([{region:"East",revenue:42}])])',
    'root = Col([Text("Revenue: 999999")])',
    'root = Col([Text("Revenue: "+(999999))])',
    'root = Col([Text($show ? "Revenue: 42" : "Revenue: "+999999)])\n$show=true',
    'root = Col([Card($show ? "Revenue: 42" : "Revenue: 999999",[])])\n$show=true',
    'root = Col([Chart([{revenue:42}],"region","revenue")])',
    'root = Col([Table(@Concat(@Evidence("e1"),$fake))])\n$fake=[{revenue:999999}]',
    'root = Col([Table(@Append(@Evidence("e1"),{revenue:999999}))])',
    'root = Col([Button("Copy",@Set($draft,@Evidence("e1")),"copy")])\n$draft=[]',
    'root = Col([SearchImage("Business revenue","photo")])',
    'root = Col([HtmlView("<script>fetch(\\"https://example.com\\")</script>","Game")])',
    'root = Col([Unknown("x")])',
    'root = Col([Button("Refresh",@Run(q),"refresh")])\nq=Query("sql",{})',
])
async def test_unverified_or_unsafe_business_ui_is_never_published(program):
    with pytest.raises(ui.UiValidationError):
        await ui.compose_block(program, "Fallback", tables=TABLES, business=True,
                               question="Revenue", claims=None, language="en")


@pytest.mark.parametrize("state", [
    {"$token": "sk-" + "a" * 30}, {"$value": "x" * 5000},
    {"$value": [1] * 101}, {"$result": {"rows": [[42]]}}, {"__proto__": {}},
    {"$value": float("nan")},
])
def test_state_rejects_secrets_query_results_and_unbounded_values(state):
    with pytest.raises(ui.UiValidationError):
        ui.validate_state(state)


def test_missing_runtime_or_disabled_participant_keep_markdown(monkeypatch):
    assert ui.split_program("Fallback\n```openui\nroot = Col([Text(\"Hi\")])\n```")[0] == "Fallback"
    assert ui.split_program("Fallback\n```openui\nbroken")[0] == "Fallback"
    assert ui.composer_prompt(enabled=False, presentation="text") == ""
    assert ui.composer_prompt(enabled=True, presentation="interactive") == ui.catalog()["prompt"]
    monkeypatch.setattr(ui, "runtime_available", lambda: False)
    assert ui.composer_prompt(enabled=True, presentation="interactive") == ""


def test_catalog_prompt_asks_for_markdown_and_a_fenced_program():
    prompt = ui.catalog()["prompt"]
    assert "ENTIRE response" not in prompt
    assert prompt.startswith("You write Nova answers")
    assert "exactly one fenced ```openui block" in prompt


UNFENCED = (
    '$selected = null\n'
    'root = Col([Title("Rute"), GeoMap(@Evidence("evidence_2"), null, null, "geometry", '
    '"label", "Rute", $selected)])\n'
    '$viewport = null'
)


@pytest.mark.parametrize(("answer", "fallback"), [
    (f"Rute Budi tercatat lengkap.\n\n{UNFENCED}", "Rute Budi tercatat lengkap."),
    (f"Rute Budi tercatat lengkap.\n\n{UNFENCED}\n\nKlik marker untuk detail.",
     "Rute Budi tercatat lengkap.\n\n\nKlik marker untuk detail."),
    (f"Rute Budi tercatat lengkap.\n\n```\n{UNFENCED}\n```", "Rute Budi tercatat lengkap."),
    (UNFENCED, ""),
])
def test_unfenced_programs_are_taken_out_of_the_markdown(answer, fallback):
    assert ui.split_program(answer) == (fallback, UNFENCED)


@pytest.mark.parametrize("answer", [
    "Set root = the top node of the tree.",
    "```sql\nSELECT 1\n```",
    'root = Col([Text("a")])\nroot = Col([Text("b")])',
])
def test_prose_and_other_code_stay_markdown(answer):
    assert ui.split_program(answer) == (answer.strip(), None)


@pytest.mark.parametrize("presentation", ["text", "interactive"])
def test_ui_catalog_is_available_without_a_presentation_gate(monkeypatch, presentation):
    monkeypatch.setattr(ui, "runtime_available", lambda: True)
    monkeypatch.setattr(ui, "catalog", lambda: {"prompt": "Catalog instructions"})
    prompt = ui.composer_prompt(enabled=True, presentation=presentation)
    assert "Catalog instructions" in prompt
    assert ("OPTIONAL_UI_CATALOG" in prompt) == (presentation == "text")


async def test_ordered_ui_stream_is_validated_sealed_and_replayable():
    block = await ui.compose_block(CALCULATOR, "A local calculator.", tables={}, business=False,
                                   question="Calculator", claims=None, language="en")
    context = LoopContext(user_name="alice")
    frames = _ordered_output_frames("A local calculator.", [PendingArtifact("ui", block)], context)
    names = [frame.split("\n", 1)[0] for frame in frames]
    assert names == ["event: text_delta", "event: content_block_done", "event: ui",
                     "event: ui_delta", "event: content_block_done"]
    trace = next(step for step in context.steps if step["kind"] == "ui")
    assert trace["program"] == CALCULATOR
    assert trace["content_index"] == 1 and trace["content_id"] == "content-1"


async def test_source_is_owner_role_version_scoped_and_cross_device(monkeypatch):
    block = {"kind": "ui", "content_id": "content-1", "program": CALCULATOR}
    db = AsyncMock()
    db.execute_system.return_value = {"rows": [["assistant", json.dumps(STAMP), [block]]]}
    monkeypatch.setattr(repository_module, "db", db)
    repo = AssistantRepository()
    assert await repo.ui_source("thread", "message", "content-1", user_name="alice",
                                security_context={**STAMP, "session_id": "device-b"}) == block
    sql, params = db.execute_system.call_args.args
    assert "user_name=%s" in sql and params == ["thread", "message", "alice"]
    # Versions restart with each sign-in, so another session's version is not compared.
    assert await repo.ui_source(
        "thread", "message", "content-1", user_name="alice",
        security_context={**STAMP, "session_id": "device-b", "security_context_version": 2},
    ) == block
    for change in [{"principal": "bob"}, {"active_role": "other"},
                   {"security_context_version": 2},
                   {"session_id": "device-b", "active_role": "other"}]:
        assert await repo.ui_source("thread", "message", "content-1", user_name="alice",
                                    security_context={**STAMP, **change}) is None


async def test_revision_conflict_is_serialized_without_touching_message_row(monkeypatch):
    from app.modules.intelligence import engine_repository

    lease = AsyncMock()
    lease.renew.return_value = True

    @asynccontextmanager
    async def lock(_key):
        yield lease

    monkeypatch.setattr(engine_repository, "metadata_lock", lock)
    db = AsyncMock()
    db.execute_system.return_value = {"rows": [[2, {"$units": 7}, "a" * 64]]}
    monkeypatch.setattr(repository_module, "db", db)
    repo = AssistantRepository()
    block = {"state_defaults": {"$units": 5}, "program_sha256": "a" * 64}
    with pytest.raises(HTTPException) as conflict:
        await repo.put_ui_state("thread", "message", "content-1", user_name="alice",
                                security_context=STAMP, block=block, revision=1,
                                state={"$units": 8})
    assert conflict.value.status_code == 409
    assert db.execute_system.call_count == 1
    saved = await repo.put_ui_state("thread", "message", "content-1", user_name="alice",
                                    security_context=STAMP, block=block, revision=2,
                                    state={"$units": 8})
    assert saved["revision"] == 3
    assert "CONFIG_ASSISTANT_UI_STATE" in db.execute_system.call_args.args[0]
    assert "CONFIG_ASSISTANT_MESSAGES" not in db.execute_system.call_args.args[0]
