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


def test_missing_runtime_disabled_feature_and_budget_keep_markdown(monkeypatch):
    assert ui.split_program("Fallback\n```openui\nroot = Col([Text(\"Hi\")])\n```")[0] == "Fallback"
    assert ui.split_program("Fallback\n```openui\nbroken")[0] == "Fallback"
    monkeypatch.setattr(ui.settings, "STUDIO_INTELLIGENT_UI_ENABLED", False)
    assert ui.composer_prompt(enabled=True, presentation="interactive", token_room=100000) == ""
    monkeypatch.setattr(ui.settings, "STUDIO_INTELLIGENT_UI_ENABLED", True)
    assert ui.composer_prompt(enabled=True, presentation="text", token_room=100000) == ""
    assert ui.composer_prompt(enabled=True, presentation="interactive", token_room=10) == ""
    monkeypatch.setattr(ui, "runtime_available", lambda: False)
    assert ui.composer_prompt(enabled=True, presentation="interactive", token_room=100000) == ""


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
    for change in [{"principal": "bob"}, {"active_role": "other"},
                   {"security_context_version": 2}]:
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
