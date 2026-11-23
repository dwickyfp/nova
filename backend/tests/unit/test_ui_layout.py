"""Page answers: the layout language, checked statement by statement as it streams."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.modules.assistant import ui_layout as ul
from app.modules.assistant.ui_legacy import legacy_program, migrate_steps

TABLES = {"evidence_1": {
    "columns": ["month", "revenue", "orders"],
    "rows": [["2025-06-01", Decimal("145886000.00"), 10], ["2025-07-01", 29298000, 5]],
    "sql": "SELECT month, revenue, orders FROM sales",
}}

ANSWER = """Here is the page.
```openui
root = Page([header, scenario, kpis, trend, bad, summary])
header = Header("Sales overview", "Revenue for June and July 2025.")
$uplift = 10
scenario = Controls("What-if scenario", [Slider("Revenue change", $uplift, -30, 50, 5, "percent")], "Order volume stays constant.")
base = @Value("evidence_1", "revenue", "sum")
jun = @Value("evidence_1", "revenue", "value", {month: "2025-06"})
jul = @Value("evidence_1", "revenue", "value", {month: "2025-07"})
kpis = KpiTiles([Metric("Baseline revenue", base), Metric("Scenario revenue", base * (1 + $uplift / 100), "number", $uplift / 100, "vs. baseline"), Metric("July vs June", jul, "number", (jul - jun) / jun, "July 2025")])
trend = Section("Trend", [Chart("evidence_1", "line", "month", "revenue", "Revenue by month")])
bad = Metric("Typed", 999)
summary = Text("Revenue was 175,184,000 in total. It grew 999% in July.")
```
That is all."""  # noqa: E501 - one statement per line is the language


async def _chart(evidence_id, table, plan, title):
    return {"props": {"title": title, "chart": {"visual": plan["visual"]}},
            "source": {"evidence_id": evidence_id, "statement_fingerprint": "f"}, "plan": plan}


def _ctx(**changes):
    return ul.LayoutContext(**{"tables": TABLES, "question": "Sales overview with a what-if",
                               "chart": _chart, "sign": lambda position: f"dr1.ref{position}.mac",
                               **changes})


async def _stream(text: str, size: int) -> tuple[ul.LayoutStream, list[str]]:
    stream = ul.LayoutStream(_ctx())
    out: list[str] = []
    for start in range(0, len(text), size):
        out += await stream.feed(text[start:start + size])
    out += await stream.finish()
    return stream, out


def test_statements_parse_and_serialize_back_to_the_same_tree():
    for line in [
        'kpis = KpiTiles([Metric("A", base * (1 + $u / 100), "number", null, "x")])',
        "t = $on ? 1 : -2.5",
        'w = Section(null, [Row([a, b])], "Note")',
        'v = @Value("e", "c", "sum", {"month": "2025-06"})',
    ]:
        name, node = ul.parse_statement(line)
        # Operators are written back fully parenthesised; the tree is the same.
        assert ul.parse_statement(f"{name} = {ul.serialize(node)}") == (name, node)
    with pytest.raises(ul.LayoutError):
        ul.parse_statement('x = Header("unclosed')


@pytest.mark.parametrize("size", [1, 7, 64, 10_000])
async def test_chunked_streams_publish_the_same_checked_page(size):
    stream, out = await _stream(ANSWER, size)
    whole, _ = await _stream(ANSWER, 10_000)
    assert out == whole.published == stream.published
    # Reading order is kept: the root first, then each block as it was written.
    assert out[0].startswith("root = Page(")
    assert [line.split(" =")[0] for line in out] == [
        "root", "header", "$uplift", "scenario", "base", "jun", "jul", "kpis", "trend", "summary",
    ]


async def test_figures_come_only_from_verified_rows():
    stream, out = await _stream(ANSWER, 32)
    lines = dict(line.split(" = ", 1) for line in out)
    assert lines["base"] == "175184000"
    assert lines["jun"] == "145886000" and lines["jul"] == "29298000"
    # A typed number is refused, per statement; the rest of the page stays.
    errors = {error["statement"]: error["error"] for error in stream.result().errors}
    assert "bad" in errors and "@Value" in errors["bad"]
    # An unsupported figure in narrative loses its clause, never the page.
    assert lines["summary"] == 'Text("Revenue was 175,184,000 in total.")'
    result = stream.result()
    assert result.adjusted and result.evidence_ids == ["evidence_1"]
    assert result.state_defaults == {"$uplift": 10}


async def test_a_model_chart_becomes_a_native_draft_chart_with_its_governed_source():
    stream, out = await _stream(ANSWER, 50)
    trend = next(line for line in out if line.startswith("trend ="))
    assert 'DatavizChart("Revenue by month", {"visual": "line"}, "dr1.ref0.mac")' in trend
    [chart] = stream.result().charts
    assert chart["source"]["evidence_id"] == "evidence_1" and chart["plan"]["visual"] == "line"
    # The browser never receives the governed source.
    assert "statement_fingerprint" not in "\n".join(out)


@pytest.mark.parametrize(("line", "error"), [
    ('c = Chart("evidence_1", "line", "month", "profit", "Profit")', "no column"),
    ('c = Chart("evidence_9", "line", "month", "revenue", "Revenue")', "evidence_9"),
    ('m = Metric("Revenue", @Value("evidence_1", "revenue", "value", {month: "2025"}))', "rows"),
    ('d = DatavizChart("x", {}, "dr1.forged.mac")', "Nova"),
    ("root = Header(\"Not a page\")", "Page"),
    ('$x = @Value("evidence_1", "revenue")', "literal"),
])
async def test_invalid_statements_are_refused_with_a_reason(line, error):
    with pytest.raises(ul.LayoutError, match=error):
        await ul.check_statement(line, _ctx(), state_names=set(), defined=set())


async def test_a_table_shows_bounded_rows_of_its_result():
    statement = await ul.check_statement('t = Table("evidence_1", ["month", "revenue"], "Rows")',
                                         _ctx(), state_names=set(), defined=set())
    assert statement.text == (
        't = ResultTable("Rows", ["month", "revenue"], '
        '[["2025-06-01", 145886000], ["2025-07-01", 29298000]])'
    )


async def test_a_page_without_a_root_gets_one_and_prose_stays_outside():
    text = 'Intro.\n```openui\nh = Header("Overview")\nt = Text("Short.")\n```\nAfter.'
    stream, out = await _stream(text, 5)
    assert out[-1] == "root = Page([h, t])"
    assert stream.prose() == "Intro.\n\nAfter."
    plain = ul.LayoutStream(_ctx())
    assert await plain.feed("Revenue was 175,184,000.") == []
    assert not plain.found and plain.prose() == "Revenue was 175,184,000."


async def _live(text: str):
    recorded: list[dict] = []
    live = ul.LiveLayout(ctx=_ctx(), content_index=0, content_id="c-0", record=recorded.append)
    frames: list[str] = []
    for start in range(0, len(text), 9):
        frames += await live.feed(text[start:start + 9])
    return live, frames, recorded


def _events(frames: list[str]) -> list[tuple[str, dict]]:
    out = []
    for frame in frames:
        name = frame.split("\n", 1)[0].removeprefix("event: ").strip()
        data = json.loads(frame.split("data: ", 1)[1])
        out.append((name, data))
    return out


async def test_live_page_streams_one_delta_per_statement_and_replays_identically():
    live, frames, recorded = await _live(ANSWER)
    frames += await live.finish(prose="That is all.")
    events = _events(frames)
    assert events[0][0] == "ui" and events[0][1]["payload"]["catalog_version"] == ul.CATALOG
    deltas = [data["text"] for name, data in events if name == "ui_delta"]
    assert all(text.endswith("\n") and text.count("\n") == 1 for text in deltas)
    assert events[-1][0] == "content_block_done"
    [step] = recorded
    # Replay reads the stored program; it is exactly what streamed.
    assert "".join(deltas) == step["program"] + "\n"
    assert step["charts"][0]["draft_ref"] == "dr1.ref0.mac"
    assert step["fallback"].startswith("**Sales overview**")
    assert {error["statement"] for error in step["provenance"]["errors"]} == {"bad"}


async def test_a_page_that_was_not_the_answer_is_emptied():
    live, frames, recorded = await _live(ANSWER[:400])
    assert live.shown
    events = _events(live.retract())
    assert events[0] == ("ui_delta", {"content_index": 0, "content_id": "c-0", "text": "",
                                      "replace": True})
    assert events[1][0] == "content_block_done"
    assert await live.finish(prose="") == [] and recorded == []


def test_a_layout_is_asked_for_only_when_enabled_and_pages_only_for_pages():
    assert ul.composer_prompt(enabled=False, presentation="interactive") == ""
    text = ul.composer_prompt(enabled=True, presentation="text")
    assert "<PAGE_LAYOUT>" in text and "one number never becomes a page" in text
    assert "This request asks for a page" not in text
    assert "This request asks for a page" in ul.composer_prompt(
        enabled=True, presentation="interactive")


def test_the_final_composer_does_not_invite_currency_remarks():
    from app.modules.assistant.intelligence import EvidenceTracker
    from app.modules.assistant.service import LoopContext, _final_composer_prompt

    prompt = _final_composer_prompt(EvidenceTracker(), LoopContext(user_name="alice"))
    assert "A currency or unit the evidence does not name is not uncertainty" in prompt
    assert "do not remark on it unless the person asked about currency" in prompt
    assert "Do not remark on a missing currency." in ul.prompt()


LEGACY = {
    "kind": "ui", "content_id": "c1", "catalog_version": "nova-openui-3",
    "fallback": "Revenue rose in July.", "program_sha256": "a" * 64, "actions": [],
    "program": ('root = Col([Kpi(@Evidence("e1"), "revenue", "Revenue", 0), '
                'Chart(@Evidence("e2"), "month", "revenue", "line", "Trend"), '
                'Table(@Evidence("e2"), null, "Rows")])'),
    "evidence": {"e1": [{"revenue": 1200.5}],
                 "e2": [{"month": "2025-06", "revenue": 10}, {"month": "2025-07", "revenue": 12}]},
    "charts": [{"title": "Trend", "chart": {"visual": "line"}, "draft_ref": "dr1.abc.def",
                "source": {"statement": "SELECT 1"}}],
}


def test_an_answer_from_a_retired_catalog_replays_as_a_read_only_page():
    assert legacy_program(LEGACY).splitlines() == [
        "root = Page([b0, b1, b2])",
        'b0 = KpiTiles([Metric("Revenue", 1200.5, "number", null, null, null, 0)])',
        'b1 = DatavizChart("Trend", {"visual": "line"}, "dr1.abc.def")',
        'b2 = ResultTable("Rows", ["month", "revenue"], [["2025-06", 10], ["2025-07", 12]])',
    ]
    journal = {"kind": "event_journal", "events": [
        {"event": "part.start", "data": {"part_id": "p1", "type": "ui", "content_id": "c1",
                                         "block": {**{k: v for k, v in LEGACY.items()
                                                      if k not in {"kind", "program"}}}}},
        {"event": "part.delta", "data": {"part_id": "p1", "text": "root = Col("}},
        {"event": "part.delta", "data": {"part_id": "p1", "text": "[...])"}},
        {"event": "part.end", "data": {"part_id": "p1", "state": "done"}},
    ]}
    step, migrated = migrate_steps([LEGACY, journal])
    assert step["catalog_version"] == ul.CATALOG and step["provenance"]["legacy"] is True
    assert "evidence" not in step and "actions" not in step
    # The draft chart keeps its recorded source on the server side only.
    assert step["charts"][0]["source"] == {"statement": "SELECT 1"}
    start, delta, end = migrated["events"]
    assert start["data"]["block"]["catalog_version"] == ul.CATALOG
    assert "source" not in start["data"]["block"]["charts"][0]
    assert "evidence" not in start["data"]["block"]
    assert delta["data"] == {"part_id": "p1", "text": step["program"]}
    assert end["event"] == "part.end"
    # A page in the current catalog is left as it is.
    current = {"kind": "ui", "catalog_version": ul.CATALOG, "program": "root = Page([])"}
    assert migrate_steps([current]) == [current]
