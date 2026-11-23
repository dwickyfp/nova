"""Studio stream protocol v2: encoding, the stored journal and the shared contract.

``protocol-v2-contract.json`` (beside the Studio client runtime) holds loop
frames and the v2 stream this encoder produces for them, numbered the way the
route numbers delivered frames. The client's test mirror is checked against the
same file, so the two encodings cannot drift. Regenerate it after an intended
protocol change with ``python -m tests.unit.test_studio_events_v2``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.modules.assistant import events_v2
from app.modules.assistant.events_v2 import (
    PROTOCOL_VERSION,
    StudioStreamEncoder,
    journal_protocol,
    journal_step,
    parse_frame,
    stored_journal,
    without_part_types,
)

CONTRACT = (
    Path(__file__).resolve().parents[3]
    / "frontend/src/features/studio/runtime/protocol-v2-contract.json"
)

ASSESSMENT = {
    "schema_version": 1,
    "rule_version": "evidence-health-v1",
    "assessed_at": "2026-10-03T10:00:00Z",
    "label": "limited",
    "facts": {
        "semantic_grounding": "published", "semantic_view_id": "sales", "semantic_version": 2,
        "semantic_fingerprint": "fp", "plan_source": "compiled", "verified_query_hit": False,
        "verified_query_id": None, "execution_status": "success", "coverage": "truncated",
        "semantic_ambiguity": "none", "source_agreement": "unknown",
        "causal_strength": "association", "unsupported_numeric_claims": False,
        "data_as_of": None, "max_age_seconds": None, "evidence_refs": ["e1"],
    },
    "data_freshness": {
        "status": "unknown", "data_as_of": None, "age_seconds": None, "max_age_seconds": None,
    },
    "reasons": ["coverage_truncated", "freshness_unknown"],
    "unknown_signals": ["source_agreement", "data_freshness"],
}

UI_PAYLOAD = {
    "catalog_version": "1",
    "program_sha256": "a" * 64,
    "fallback": "Adjust assumptions.",
    "state_defaults": {},
    "evidence": {},
    "actions": [],
    "provenance": {"evidence_ids": [], "scope": "user_simulation", "assumption": True},
}


APPROVAL = {
    "kind": "approval", "title": "Allow this action?", "classification": "destructive",
    "reason": "changes_data", "role": "ANALYST",
    "action": "Schedule the automation 'Weekly revenue' every Monday at 08:00",
}
QUESTION = {
    "kind": "question", "title": "Gross or net revenue?",
    "detail": "Net excludes refunds.", "choices": ["Gross revenue", "Net revenue"],
    "allow_free_text": True,
}
CHOICE = {
    "kind": "choice", "title": "How should I break it down?",
    "options": [
        {"id": "region", "title": "By region", "description": "Revenue per region"},
        {"id": "month", "title": "By month", "recommended": True},
    ],
}
FORM = {
    "kind": "form", "title": "Which period?",
    "fields": [{"name": "period", "title": "Period", "widget": "date_range"}],
    "required": ["period"],
}


def _f(event: str, **data: Any) -> dict[str, Any]:
    return {"event": event, "data": data}


def _call(tool_call_id: str, status: str, **extra: Any) -> dict[str, Any]:
    return _f(
        "tool_call", tool_call_id=tool_call_id, tool_name="query_execute",
        sql_preview="SELECT 1", classification="read_only", status=status, **extra,
    )


CASES: list[dict[str, Any]] = [
    {
        "name": "answer with tools, consent, content and a nested Mission call",
        "run_id": "run-a",
        "role": "ANALYST",
        "v1": [
            _f("plan", steps=[{"id": "u", "text": "Understand the request", "status": "running"}]),
            _f("thinking", phase="act", text="Reasoning about the next step", status="running"),
            _f("thinking", phase="act", text="Reading the orders table", status="running"),
            _call("c1", "running"),
            _f("tool_progress", tool_call_id="c1", stage="query_started", text="Running the query"),
            _f("tool_status", tool_call_id="c1", status="done"),
            _f("tool_detail", tool_call_id="c1", text="1 row returned"),
            _f("evidence_health", tool_call_id="c1", tool_name="query_execute",
               payload=ASSESSMENT),
            _f("thinking", phase="observe", text="Reading the result", status="done"),
            _call("c2", "pending"),
            _f("input_required", request_id="c2", kind="approval", tool_call_id="c2",
               expires_at="2026-11-23T10:05:00+00:00", request=APPROVAL),
            _f("input_resolved", request_id="c2", status="allowed",
               summary="You allowed this action"),
            _f("tool_status", tool_call_id="c2", status="running"),
            _f("tool_status", tool_call_id="c2", status="done"),
            _call("c1", "done", run_id="mission-run"),
            _f("guard", rule_id="credential-in-answer", stage="answer", action="redacted",
               tool_name="", message="A credential-shaped value was removed.",
               source="platform"),
            _f("text_delta", text="Revenue rose ", content_index=0, content_id="content-0"),
            _f("text_delta", text="12%.", content_index=0, content_id="content-0"),
            _f("content_block_done", content_index=0, content_id="content-0"),
            _f("table", title="Revenue", columns=["month", "revenue"], rows=[["2026-10", 12]],
               content_index=1, content_id="content-1"),
            _f("content_block_done", content_index=1, content_id="content-1"),
            _f("ui", payload=UI_PAYLOAD, content_index=2, content_id="content-2"),
            _f("ui_delta", text='root = Col([Text("Adjust")])', content_index=2,
               content_id="content-2"),
            _f("content_block_done", content_index=2, content_id="content-2"),
            _f("chart", tool_call_id="c1", chart_spec='{"mark":"bar"}'),
            _f("suggestions", suggestions=["Break it down by region"]),
            _f("ping"),
            _f("done", message_id="m1", finish_reason="stop",
               usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        ],
    },
    {
        "name": "role change mid-turn",
        "run_id": "run-b",
        "role": "FINANCE",
        "v1": [
            _f("thinking", phase="act", text="Reading finance data", status="running"),
            _call("c1", "running"),
            _f("text_delta", text="finance-secret", content_index=0, content_id="content-0"),
            _f("role_changed", active_role="MARKETING", security_context_version=2),
            _f("text_delta", text="Marketing answer.", content_index=0, content_id="content-0"),
            _f("done", message_id="m2", finish_reason="stop"),
        ],
    },
    {
        "name": "interrupted with a waiting and a running call",
        "run_id": "run-c",
        "role": "ANALYST",
        "v1": [
            _f("text_delta", text="Partial answer", content_index=0, content_id="content-0"),
            _call("c1", "running"),
            _call("c2", "pending"),
            _f("input_required", request_id="c2", kind="approval", tool_call_id="c2",
               expires_at="2026-11-23T10:05:00+00:00", request=APPROVAL),
            _f("ui", payload=UI_PAYLOAD, content_index=1, content_id="content-1"),
            _f("error", code="run_persistence_unavailable", message="Progress could not be saved."),
            _f("done", message_id="", finish_reason="interrupted"),
        ],
    },
    {
        "name": "human in the loop: question, choice, form, edit then run, stopped",
        "run_id": "run-d",
        "role": "MARKETING",
        "v1": [
            _f("thinking", phase="act", text="Choosing the metric", status="running"),
            _f("input_required", request_id="h1", kind="question",
               expires_at="2026-11-23T10:10:00+00:00", request=QUESTION),
            _f("input_resolved", request_id="h1", status="answered",
               summary="You answered: Net revenue", answer={"text": "Net revenue"}),
            _f("input_required", request_id="h2", kind="choice",
               expires_at="2026-11-23T10:10:00+00:00", request=CHOICE),
            _f("input_resolved", request_id="h2", status="answered",
               summary="You chose: By region",
               answer={"option_id": "region", "title": "By region"}),
            _f("input_required", request_id="h3", kind="form",
               expires_at="2026-11-23T10:10:00+00:00", request=FORM),
            _f("input_resolved", request_id="h3", status="declined",
               summary="You skipped this question"),
            _call("c3", "pending"),
            _f("input_required", request_id="c3", kind="approval", tool_call_id="c3",
               expires_at="2026-11-23T10:05:00+00:00", request=APPROVAL),
            _f("input_resolved", request_id="c3", status="denied",
               summary="You denied this action"),
            _f("tool_status", tool_call_id="c3", status="denied"),
            _f("input_required", request_id="h4", kind="question",
               expires_at="2026-11-23T10:10:00+00:00", request=QUESTION),
            _f("done", message_id="", finish_reason="cancelled"),
        ],
    },
]


def _v1_frame(entry: dict[str, Any]) -> str:
    return f"event: {entry['event']}\ndata: {json.dumps(entry['data'])}\n\n"


def encode_case(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Encode one case and number it as ``send_agent_message`` does."""
    encoder = StudioStreamEncoder(case["run_id"])
    frames = [encoder.start(role=case["role"])]
    for entry in case["v1"]:
        frames.extend(encoder.translate(_v1_frame(entry)))
    out = []
    for seq, frame in enumerate(frames):
        name, payload = parse_frame(frame)
        payload.pop("sequence", None)
        out.append({"event": name, "data": {**payload, "run_id": case["run_id"], "seq": seq}})
    return out


def _contract() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "cases": [{**case, "v2": encode_case(case)} for case in CASES],
    }


def _events(records: list[dict[str, Any]]) -> list[str]:
    return [record["event"] for record in records]


def _frames(records: list[dict[str, Any]]) -> list[str]:
    return [
        f"event: {record['event']}\ndata: {json.dumps(record['data'])}\n\n" for record in records
    ]


def test_contract_fixture_matches_the_encoder() -> None:
    stored = json.loads(CONTRACT.read_text())
    assert stored == json.loads(json.dumps(_contract())), (
        "The protocol changed: regenerate the contract with "
        "`python -m tests.unit.test_studio_events_v2` and update the client mirror."
    )


def test_every_event_carries_run_id_and_monotonic_seq() -> None:
    for case in CASES:
        records = encode_case(case)
        assert records[0]["event"] == "run.started"
        assert records[-1]["event"] == "run.finished"
        assert [record["data"]["seq"] for record in records] == list(range(len(records)))
        assert {record["data"]["run_id"] for record in records} == {case["run_id"]}


def test_a_run_keeps_one_plan_row_when_a_step_resumes_after_an_answer() -> None:
    plan = [{"id": "u", "text": "Understand the request", "status": "running"}]
    longer = [*plan, {"id": "a", "text": "Answer", "status": "pending"}]
    records = encode_case({
        "run_id": "run-p", "role": "ANALYST",
        "v1": [
            _f("plan", steps=plan),
            _f("input_required", request_id="h1", kind="question",
               expires_at="2026-11-23T10:10:00+00:00", request=QUESTION),
            _f("input_resolved", request_id="h1", status="answered", summary="You answered"),
            # The resumed step restates the same plan: nothing is added.
            _f("plan", steps=plan),
            # A changed plan replaces the one row in place.
            _f("plan", steps=longer),
            _f("done", message_id="m", finish_reason="stop"),
        ],
    })
    plans = [
        r["data"] for r in records
        if r["event"] == "part.start" and r["data"].get("phase") == "plan"
    ]
    assert len(plans) == 1
    replaced = [
        r["data"] for r in records
        if r["event"] == "part.delta" and r["data"]["part_id"] == plans[0]["part_id"]
    ]
    assert [d["text"] for d in replaced] == ["1. Understand the request\n2. Answer"]
    assert all(d["replace"] for d in replaced)


def test_parts_open_once_and_close_once_in_production_order() -> None:
    records = encode_case(CASES[0])
    starts = [r["data"]["part_id"] for r in records if r["event"] == "part.start"]
    ends = [r["data"]["part_id"] for r in records if r["event"] == "part.end"]
    assert starts == [f"run-a#{n}" for n in range(len(starts))]
    assert sorted(starts) == sorted(ends)
    assert len(set(ends)) == len(ends)


def test_restated_phase_updates_its_row_instead_of_adding_one() -> None:
    records = encode_case(CASES[0])
    reasoning = [
        r for r in records if r["event"] == "part.start" and r["data"]["type"] == "reasoning"
    ]
    assert [r["data"]["phase"] for r in reasoning] == ["plan", "act", "observe"]
    act = reasoning[1]["data"]["part_id"]
    assert {"event": "part.delta", "data": {
        "part_id": act, "text": "Reading the orders table", "replace": True,
        "run_id": "run-a", "seq": 4,
    }} in records


def test_consent_and_nested_mission_calls_reach_their_own_parts() -> None:
    records = encode_case(CASES[0])
    tools = {
        (r["data"].get("origin_run_id"), r["data"]["tool_call_id"]): r["data"]["part_id"]
        for r in records if r["event"] == "part.start" and r["data"]["type"] == "tool"
    }
    assert set(tools) == {(None, "c1"), (None, "c2"), ("mission-run", "c1")}
    approval = next(
        r for r in records if r["event"] == "part.start" and r["data"]["type"] == "hitl_request"
    )
    assert approval["data"]["tool_part_id"] == tools[(None, "c2")]
    required = next(r for r in records if r["event"] == "input.required")
    assert required["data"]["part_id"] == approval["data"]["part_id"]
    assert required["data"]["request"]["action"] == APPROVAL["action"]
    evidence = next(r for r in records if r["event"] == "tool.evidence")
    assert evidence["data"]["part_id"] == tools[(None, "c1")]
    assert evidence["data"]["kind"] == "health"


def test_content_keeps_its_authored_identity_and_seals_with_its_close_frame() -> None:
    records = encode_case(CASES[0])
    content = [
        r["data"] for r in records
        if r["event"] == "part.start" and r["data"]["type"] in {"text", "table", "ui", "chart"}
    ]
    assert [(c["type"], c.get("content_id")) for c in content] == [
        ("text", "content-0"), ("table", "content-1"), ("ui", "content-2"), ("chart", None),
    ]
    ends = {r["data"]["part_id"]: r["data"]["state"] for r in records if r["event"] == "part.end"}
    assert all(ends[c["part_id"]] == "done" for c in content)


def test_run_finished_seals_open_parts_by_outcome() -> None:
    records = encode_case(CASES[2])
    starts = {
        r["data"]["part_id"]: r["data"].get("tool_call_id") or r["data"]["type"]
        for r in records if r["event"] == "part.start"
    }
    ends = {starts[r["data"]["part_id"]]: r["data"]["state"]
            for r in records if r["event"] == "part.end"}
    # A call awaiting consent never ran; a running one broke off; so did the rest.
    assert ends == {
        "text": "error", "c1": "error", "c2": "cancelled", "ui": "error",
        "hitl_request": "cancelled",
    }
    names = _events(records)
    assert names.index("run.error") < names.index("part.end")
    assert records[-1]["data"]["finish_reason"] == "interrupted"


def test_a_chart_part_keeps_the_query_it_was_drawn_from() -> None:
    from app.modules.assistant import events

    encoder = StudioStreamEncoder("run")
    frame = events.chart(
        "chart-1", "{}", source_tool_call_id="query-1", content_index=1, content_id="run:1"
    )
    started = [
        payload
        for name, payload in map(parse_frame, encoder.translate(frame))
        if name == "part.start"
    ]
    assert started[0]["block"]["tool_call_id"] == "chart-1"
    assert started[0]["block"]["source_tool_call_id"] == "query-1"


def test_finish_force_reissues_an_undelivered_terminal() -> None:
    encoder = StudioStreamEncoder("run")
    assert encoder.finish({"finish_reason": "stop"})
    assert encoder.finish({"finish_reason": "stop"}) == []
    forced = encoder.finish({"finish_reason": "interrupted"}, force=True)
    assert [parse_frame(frame)[0] for frame in forced] == ["run.finished"]


def test_role_change_cancels_open_parts_and_the_journal_keeps_only_the_new_role() -> None:
    records = encode_case(CASES[1])
    change = _events(records).index("role.changed")
    before = records[:change]
    started = {r["data"]["part_id"] for r in before if r["event"] == "part.start"}
    ended = {r["data"]["part_id"]: r["data"]["state"] for r in before if r["event"] == "part.end"}
    # Nothing from the previous role is left streaming: the work still open
    # (the call and the answer) is cancelled.
    assert set(ended) == started
    assert list(ended.values()).count("cancelled") == 2
    journal = stored_journal(_frames(records))
    assert _events(journal)[:2] == ["run.started", "role.changed"]
    assert "finance-secret" not in json.dumps(journal)
    assert "Marketing answer." in json.dumps(journal)


def test_journal_step_drops_keepalives_and_mission_projections() -> None:
    records = encode_case(CASES[0])
    frames = _frames(records) + [
        'event: mission_updated\ndata: {"seq":99,"mission":{}}\n\n',
    ]
    step = journal_step(frames)
    assert step["kind"] == "event_journal"
    assert step["protocol_version"] == PROTOCOL_VERSION
    names = _events(step["events"])
    assert "ping" not in names and "mission_updated" not in names
    assert "suggestions" in names
    assert journal_protocol([step]) == 2
    assert journal_protocol([{"kind": "tool"}]) == 1
    assert journal_protocol([]) == 1


def test_hidden_part_types_leave_the_journal_with_all_their_events() -> None:
    step = journal_step(_frames(encode_case(CASES[0])))
    hidden = without_part_types(step, {"ui"})
    text = json.dumps(hidden)
    assert "Adjust" not in text and '"type": "ui"' not in text
    assert len(hidden["events"]) == len(step["events"]) - 3  # start, delta, end
    assert without_part_types({"kind": "tool"}, {"ui"}) == {"kind": "tool"}


def test_unknown_and_v1_only_frames_are_never_passed_on_unnumbered() -> None:
    encoder = StudioStreamEncoder("run")
    assert encoder.translate('event: something_new\ndata: {"x":1}\n\n') == []
    assert encoder.translate("not a frame") == []
    assert events_v2.frame_name(encoder.start(role="R")) == "run.started"


def test_human_input_parts_sit_where_asked_and_record_how_they_settled() -> None:
    records = encode_case(CASES[3])
    hitl = [
        r["data"] for r in records
        if r["event"] == "part.start" and r["data"]["type"] == "hitl_request"
    ]
    assert [(h["request_id"], h["kind"]) for h in hitl] == [
        ("h1", "question"), ("h2", "choice"), ("h3", "form"), ("c3", "approval"),
        ("h4", "question"),
    ]
    resolved = {
        r["data"]["part_id"]: r["data"]["status"] for r in records if r["event"] == "input.resolved"
    }
    by_request = {h["request_id"]: resolved[h["part_id"]] for h in hitl}
    # The last question was still waiting when the run stopped: it is cancelled,
    # in the journal too, so a replay never shows it as answerable.
    assert by_request == {
        "h1": "answered", "h2": "answered", "h3": "declined", "c3": "denied", "h4": "cancelled",
    }
    ends = {r["data"]["part_id"]: r["data"]["state"] for r in records if r["event"] == "part.end"}
    assert ends[hitl[-1]["part_id"]] == "cancelled"
    names = _events(records)
    denied = next(
        r for r in records if r["event"] == "tool.status" and r["data"]["status"] == "denied"
    )
    assert names.index("input.resolved") < records.index(denied)
    assert "input.required" in events_v2.FLUSH_EVENTS


if __name__ == "__main__":
    CONTRACT.write_text(json.dumps(_contract(), indent=2) + "\n")
    print(f"wrote {CONTRACT}")


def test_numbered_frames_are_accepted_by_the_run_event_store() -> None:
    # The resume journal stores the frames the route numbers. Its sequence reader
    # must find the v2 ``seq``, or every Studio run fails to save its progress.
    from app.modules.agents.metadata_repository import frame_sequence as stored_sequence

    for case in CASES:
        records = encode_case(case)
        frames = _frames(records)
        assert [stored_sequence(frame) for frame in frames] == list(range(len(records)))
        assert [events_v2.frame_sequence(frame) for frame in frames] == list(range(len(records)))
