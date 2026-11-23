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
            _f("ui", payload=UI_PAYLOAD, content_index=1, content_id="content-1"),
            _f("error", code="run_persistence_unavailable", message="Progress could not be saved."),
            _f("done", message_id="", finish_reason="interrupted"),
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


def test_parts_open_once_and_close_once_in_production_order() -> None:
    records = encode_case(CASES[0])
    starts = [r["data"]["part_id"] for r in records if r["event"] == "part.start"]
    ends = [r["data"]["part_id"] for r in records if r["event"] == "part.end"]
    assert starts == [f"run-a:{n}" for n in range(len(starts))]
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
    consent = next(r for r in records if r["event"] == "consent.required")
    assert consent["data"]["part_id"] == tools[(None, "c2")]
    assert consent["data"]["tool_call"]["status"] == "pending"
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
    assert ends == {"text": "error", "c1": "error", "c2": "cancelled", "ui": "error"}
    names = _events(records)
    assert names.index("run.error") < names.index("part.end")
    assert records[-1]["data"]["finish_reason"] == "interrupted"


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


if __name__ == "__main__":
    CONTRACT.write_text(json.dumps(_contract(), indent=2) + "\n")
    print(f"wrote {CONTRACT}")
