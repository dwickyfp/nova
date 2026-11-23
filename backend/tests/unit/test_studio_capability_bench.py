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
