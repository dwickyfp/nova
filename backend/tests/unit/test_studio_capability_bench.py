"""Offline checks of the Studio capability benchmark harness (no Nova, no model)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.benchmark.studio_capability import corpus, report
from tests.benchmark.studio_capability.checks import answer_language, check, detect_outcome
from tests.benchmark.studio_capability.journal import fold
from tests.benchmark.studio_capability.judge import final_scores, packet, write_sample
from tests.benchmark.studio_capability.numbers import matches, numbers_in
from tests.benchmark.studio_capability.run import hitl_policy, select

TOTAL = 175_184_000


@pytest.mark.parametrize("text", [
    "Total revenue is Rp 175.184.000.", "Revenue: 175,184,000", "sekitar 175,2 juta",
    "about $175.18M", "Rp175,18 jt", "0,175 miliar", "175.184.000,00",
])
def test_numbers_match_written_forms(text: str) -> None:
    assert matches(TOTAL, numbers_in(text))


@pytest.mark.parametrize("text", ["180 juta", "Rp 17.518.400", "$1.75B", "176M"])
def test_numbers_reject_wrong_values(text: str) -> None:
    assert not matches(TOTAL, numbers_in(text))


def test_numbers_read_percent_as_share() -> None:
    assert matches(12.5, numbers_in("naik 12,5%"))
    assert matches(0.125, numbers_in("a 12.5% share"))
    assert matches(-80.0, numbers_in("turun -80%"))


def _events() -> list[dict]:
    seq = iter(range(100))

    def e(name: str, t: float, **data):
        return {"event": name, "t": t, "data": {"run_id": "r", "seq": next(seq), **data}}

    return [
        e("run.started", 0.4, protocol_version=2, role="marketing"),
        e("part.start", 1.2, part_id="r#0", type="reasoning", phase="plan", text="1. Query"),
        e("part.end", 1.3, part_id="r#0", state="done"),
        e("part.start", 2.0, part_id="r#1", type="tool", tool_name="semantic_query"),
        e("tool.status", 2.1, part_id="r#1", status="running", preview="SELECT 1"),
        e("tool.status", 5.0, part_id="r#1", status="done"),
        e("part.end", 5.0, part_id="r#1", state="done"),
        e("part.start", 6.0, part_id="r#2", type="text"),
        e("part.delta", 6.5, part_id="r#2", text="Total revenue is "),
        e("part.delta", 6.6, part_id="r#2", text="Rp 175.184.000 in Jakarta."),
        e("part.end", 6.7, part_id="r#2", state="done"),
        e("part.start", 7.0, part_id="r#3", type="table",
          block={"columns": ["city", "value"], "rows": [["Jakarta", "30496000.00"]]}),
        e("part.end", 7.0, part_id="r#3", state="done"),
        e("part.start", 7.1, part_id="r#4", type="ui", block={"evidence": {}}),
        e("part.delta", 7.2, part_id="r#4",
          text='root = Grid([Kpi("Revenue", 1), Chart(data=@Evidence("e"), x="d", y="v", '
               'type="line")])'),
        e("part.end", 7.3, part_id="r#4", state="done"),
        e("suggestions", 7.4, suggestions=["By city"]),
        e("run.finished", 8.0, message_id="m", finish_reason="stop", usage={"total_tokens": 9}),
    ]


def test_fold_reads_parts_timings_and_components() -> None:
    events = _events()
    events.insert(5, dict(events[4]))  # a resumed stream repeats a frame
    view = fold(events)
    assert view.duplicate_seq == 1 and view.seq_gaps == 0
    assert view.first_activity_s == 1.2 and view.first_answer_token_s == 6.5
    assert view.answer_text == "Total revenue is Rp 175.184.000 in Jakarta."
    assert view.tools == ["semantic_query"] and view.parts[1].preview == "SELECT 1"
    assert {"text", "table", "kpi", "chart", "chart:line", "suggestions"} <= view.elements()
    assert view.usage == {"total_tokens": 9} and view.finish_reason == "stop"


def test_checks_score_numbers_components_and_leaks() -> None:
    unit = {"id": "x", "lang": "en", "expect_ui": ["text", "chart:bar"],
            "expect_tools": ["semantic_query"], "expect_outcome": "answer"}
    gt = {"expected": [175184000.0, 30496000.0, 99.0], "labels": ["Jakarta"],
          "leak_values": ["iPhone 16 Pro Max 256GB"]}
    result = check(unit, gt, fold(_events()), total_s=8.0)
    assert result["numeric"]["recall"] == pytest.approx(2 / 3)
    assert result["ui_expected"] == {"text": True, "chart:bar": False}
    assert result["labels"]["recall"] == 1.0 and result["leaked_values"] == []
    assert result["deterministic"]["accuracy"] == 4 and result["outcome"] == "answer"
    leaked = check({**unit, "expect_outcome": "refuse"},
                   {"leak_values": ["175184000.00"]}, fold(_events()))
    assert leaked["leaked_values"] and leaked["deterministic"]["safety"] == 1


def test_ambiguity_either_assumes_must_asks() -> None:
    events = _events()
    either = check({"id": "a", "lang": "en", "expect_ui": ["text"], "expect_outcome": "clarify",
                    "clarify": "either"}, None, fold(events))
    assert either["outcome_ok"] and either["deterministic"].get("ui_floor") is None
    must = check({"id": "b", "lang": "en", "expect_ui": ["hitl:question"],
                  "expect_outcome": "clarify", "clarify": "must"}, None, fold(events))
    assert not must["outcome_ok"] and must["deterministic"].get("ui_floor") is None
    units = {u["id"]: u for u in corpus.units()}
    assert units["ambiguous-001"]["clarify"] == "either"
    assert "June 2025 vs July 2025" in units["ambiguous-001"]["expect_insight"]
    assert units["ambiguous-011"]["clarify"] == "must"


def test_outcome_detection() -> None:
    def text(answer: str) -> list[dict]:
        return [{"event": "part.start", "data": {"seq": 0, "part_id": "a", "type": "text"}},
                {"event": "part.delta", "data": {"seq": 1, "part_id": "a", "text": answer}},
                {"event": "run.finished", "data": {"seq": 2, "finish_reason": "stop"}}]

    assert detect_outcome(fold(text("Saya tidak memiliki akses ke data produk.")), None) == \
        "limitation"
    assert detect_outcome(fold(text("Which period do you mean?")), None) == "clarify"
    approval = [{"event": "part.start", "data": {"seq": 0, "part_id": "h",
                                                  "type": "hitl_request", "kind": "approval"}}]
    assert detect_outcome(fold(approval), None) == "approval"
    assert detect_outcome(fold([]), "ConnectError") == "error"
    assert answer_language("Pendapatan terbesar berasal dari kota Jakarta dengan total") == "id"


def test_corpus_shape_and_ground_truth() -> None:
    assert corpus.validate() == []
    questions = corpus.questions()
    sessions = corpus.sessions()
    assert len(questions) >= 300 and len(sessions) >= 30
    share = sum(q["lang"] == "id" for q in questions) / len(questions)
    assert 0.35 <= share <= 0.45
    gt = corpus.ground_truth()["items"]
    for unit in corpus.units():
        spec = unit.get("gt")
        if spec and spec.get("numeric") != "none" and unit["expect_outcome"] == "answer":
            assert gt[unit["id"]]["expected"], unit["id"]
    for unit in corpus.units():
        if unit.get("leak_sql") and "NOVA_CATALOG" in unit["leak_sql"]:
            assert "error" in gt[unit["id"]]["leak_user"], unit["id"]


def test_select_and_hitl_policy() -> None:
    class Args:
        units, ids, category, limit = "fresh", None, None, 3

    picked = select(corpus.units(), Args())
    assert len(picked) == 3 and all(u["unit"] == "fresh_twin" for u in picked)
    first = hitl_policy({"hitl": {"policy": "first"}})
    assert first("question", {"choices": ["Revenue", "Orders"]}) == \
        ("answer", {"text": "Revenue"})
    assert first("choice", {"options": [{"id": "a"}, {"id": "b", "recommended": True}]}) == \
        ("answer", {"option_id": "b"})
    form = first("form", {"fields": [{"name": "p", "widget": "date_range"},
                                     {"name": "c", "widget": "select", "values": ["Jakarta"]}]})
    assert form == ("answer", {"values": {"p": {"start": "2025-06-01", "end": "2025-07-05"},
                                          "c": "Jakarta"}})
    assert first("approval", {}) == ("deny", None)
    assert hitl_policy({"hitl": {"policy": "deny"}})("question", {}) == ("decline", None)


def test_final_scores_bind_to_facts() -> None:
    checks = {"deterministic": {"accuracy": 5, "ui_floor": 3}, "leaked_values": ["x"],
              "latency": {"first_activity_s": 2.0, "total_s": 20}, "errors": [],
              "tool_failures": [], "empty_reasoning_rows": 0}
    llm = {d: {"score": 5} for d in ("accuracy", "intelligence", "ui", "process", "safety")}
    final = final_scores(checks, {**llm, "hallucinated_numbers": ["9"]})
    assert final == {"accuracy": 1, "intelligence": 5, "ui": 3, "process": 5, "safety": 1}


def _write_run(out: Path) -> None:
    (out / "turns").mkdir(parents=True)
    (out / "judgments").mkdir()
    units = {u["id"]: u for u in corpus.units()}
    for uid, score in [("number-001", 5), ("session-001.t2", 2), ("session-001.t2.fresh", 4)]:
        view = fold(_events())
        unit = {**units[uid]}
        checks = check(unit, corpus.ground_truth()["items"].get(uid), view, total_s=8.0)
        record = {"id": uid, "unit": unit, "view": view.summary(), "checks": checks,
                  "thread_id": "t", "run_id": "r", "replay": {"ok": True},
                  "agent": {"agent_id": "a"}, "hitl_answers": []}
        (out / "turns" / f"{uid}.json").write_text(json.dumps(record))
        llm = {d: {"score": score, "why": "w"} for d in
               ("accuracy", "intelligence", "ui", "process", "safety")}
        llm.update(tags=["wrong_number"] if score < 3 else [], summary="s")
        judgment = {"id": uid, "judge_model": "judge", "vision": False, "llm": llm,
                    "final": final_scores(checks, llm), "deterministic": checks["deterministic"]}
        (out / "judgments" / f"{uid}.json").write_text(json.dumps(judgment))


def test_report_and_sample(tmp_path: Path) -> None:
    _write_run(tmp_path)
    rows = report._load(tmp_path)
    summary = report.summarize(rows)
    assert summary["judged"] == 3 and len(summary["pairs"]) == 1
    markdown = report.write_markdown(tmp_path, summary, {"agent": "Sales Analyst"}).read_text()
    assert "Matriks kapabilitas" in markdown and "Sesi vs sesi baru" in markdown
    page = report.write_html(tmp_path, summary, {}).read_text()
    assert "prefers-color-scheme:dark" in page and "var(--bg,#fbfaf8)" in page
    sample = write_sample(tmp_path, 2).read_text().splitlines()
    assert len(sample) == 2 and json.loads(sample[0])["thread_id"] == "t"
    record = json.loads((tmp_path / "turns" / "session-001.t2.json").read_text())
    assert packet(record, None, [])["what_the_user_saw"][0]["activity_row"] == "1. Query"


def _page_events() -> list[dict]:
    seq = iter(range(100))

    def e(name: str, t: float, **data):
        return {"event": name, "t": t, "data": {"run_id": "r", "seq": next(seq), **data}}

    route = ('m = PlaceMap("Trip", [{"label": "Jakarta", "lat": -6.2, "lon": 106.85, '
             '"check": "verified"}, {"label": "Bandung", "lat": -6.9, "lon": 107.6, "check": '
             '"verified"}], [{"distance_m": 151200, "duration_s": 10801, "line": "x"}, '
             '{"distance_m": 160000, "duration_s": 11000, "line": "y"}], null, false)\n')
    return [
        e("run.started", 0.2, protocol_version=2, role="marketing"),
        e("part.start", 0.9, part_id="r#0", type="ui", block={}),
        e("part.delta", 1.1, part_id="r#0", text="root = Page([h, m, k, p])\n"),
        e("part.delta", 1.2, part_id="r#0", text='h = Header("Trip")\n'),
        e("part.delta", 4.0, part_id="r#0", text=route),
        e("part.delta", 4.2, part_id="r#0",
          text='k = Controls("What if", [Slider("Change", $u, -30, 50, 5, "percent")])\n'),
        e("part.delta", 4.3, part_id="r#0",
          text='p = Picture("", "Monas", "https://commons.wikimedia.org/x")\n'),
        e("part.end", 4.4, part_id="r#0", state="done"),
        e("artifact.draft", 4.5, part_id="r#1", draft={"draft_ref": "d1", "title": "T"}),
        e("save.suggested", 4.6, part_id="r#2", draft_ref="d1", draft_refs=["d1"],
          capability="can_save", reason_code="", message="Save this chart?"),
        e("part.start", 4.7, part_id="r#3", type="citation",
          block={"citations": [{"title": "Orders"}, {"title": "Orders"}]}),
        e("part.end", 4.7, part_id="r#3", state="done"),
        e("suggestions", 4.8, suggestions=["Run semantic_query by city"]),
        e("run.finished", 5.0, message_id="m", finish_reason="stop"),
    ]


def test_fold_reads_pages_places_routes_pictures_and_artifacts() -> None:
    view = fold(_page_events())
    elements = view.elements()
    assert {"page", "place_map", "route", "route:alternatives", "slider", "interactive",
            "picture", "artifact_preview", "save_suggestion"} <= elements
    assert "picture:shown" not in elements and "artifact_saved" not in elements
    assert view.place_maps() == [{"places": 2, "routes": 2, "unavailable": None,
                                  "optimized": False}]
    assert view.first_page_s == 1.1 and view.first_section_s == 4.0


def test_checks_flag_absent_elements_tool_names_and_repeated_sources() -> None:
    unit = {"id": "s", "lang": "en", "expect_ui": ["map"], "expect_outcome": "answer",
            "expect_absent": ["page", "artifact_saved"], "expect_numbers": [151200]}
    result = check(unit, None, fold(_page_events()))
    assert result["ui_absent_violations"] == ["page"]
    assert result["deterministic"]["ui_floor"] == 3
    assert result["tool_identifiers_shown"]["suggestions"] == ["semantic_query"]
    assert result["citation_duplicates"] == 1
    assert result["numeric"]["recall"] == 1.0
    assert result["latency"]["first_section_s"] == 4.0
    saved = fold(_page_events() + [{"event": "artifact.saved", "t": 6, "data": {"seq": 99}}])
    assert check(unit, None, saved)["deterministic"]["safety"] == 2


def test_fixture_units_name_their_tools_as_the_turn_does() -> None:
    from tests.benchmark.studio_capability.run import _resolved

    units = {u["id"]: u for u in corpus.units()}
    mcp = [u for u in units.values() if u.get("agent") == "fixture"]
    assert len(mcp) >= 8 and all(u["category"] in {"mcp", "skill"} for u in mcp)
    unit = units["mcp-005"]
    resolved = _resolved(unit, {"mcp:fail_on_purpose": "mcp_abc12345"})
    assert resolved["expect_tools"] == ["mcp_abc12345"]
    assert resolved["expect_tool_failure"] == ["mcp_abc12345"]
    simple = [u for u in units.values() if u["category"] == "simple"]
    assert simple and all("page" in u["expect_absent"] for u in simple)


def test_comparison_reports_category_deltas_and_changed_outcomes(tmp_path: Path) -> None:
    base, now = tmp_path / "base", tmp_path / "now"
    _write_run(base)
    _write_run(now)
    record = json.loads((now / "judgments" / "session-001.t2.json").read_text())
    record["final"] = {**record["final"], "accuracy": 5, "intelligence": 5, "ui": 5}
    (now / "judgments" / "session-001.t2.json").write_text(json.dumps(record))
    turn = json.loads((now / "turns" / "number-001.json").read_text())
    turn["checks"]["outcome_ok"] = False
    (now / "turns" / "number-001.json").write_text(json.dumps(turn))
    result = report.compare(report._load(now), report._load(base))
    assert result["outcome_broken"] == ["number-001"]
    assert result["floor_changed_by_2"][0]["id"] == "session-001.t2"
    row = next(r for r in result["categories"] if r["category"] == "session:follow_up")
    assert row["accuracy"]["delta"] == 1.5  # the turn and its fresh twin share a category
    page = report.write_comparison(now, result, base).read_text()
    assert "Perbandingan dengan baseline" in page and "`number-001`" in page


def test_reported_outcome_reads_the_finish_reason() -> None:
    base = {"outcome": "answer", "expected_outcome": "limitation", "clarify_mode": None}
    assert report.reported_outcome_ok({**base, "finish_reason": "out_of_scope"})
    assert report.reported_outcome_ok({**base, "expected_outcome": "clarify",
                                       "finish_reason": "clarification"})
    assert not report.reported_outcome_ok({**base, "outcome": "error",
                                           "finish_reason": "out_of_scope"})
    assert report.reported_outcome_ok({**base, "expected_outcome": "answer",
                                       "finish_reason": "stop"})
