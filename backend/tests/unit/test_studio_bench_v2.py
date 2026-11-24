"""Harness v2 of the Studio capability benchmark: offline, over recorded turns.

No Nova, no model. Fixtures under ``tests/fixtures/studio_bench_v2`` are recorded
protocol-v2 turns (reasoning parts trimmed) from the baseline and re-bench runs.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests.benchmark.studio_capability.v2 import (
    adapters,
    budget,
    corpus_v3,
    gold,
    judge,
    provenance,
    rescore,
    shadow,
    stats,
    ui_tree,
)
from tests.benchmark.studio_capability.v2.expectation import Expectation, NumericExpectation
from tests.benchmark.studio_capability.v2.fold import Evidence, fold
from tests.benchmark.studio_capability.v2.grade import References, grade
from tests.benchmark.studio_capability.v2.ground_truth import resolve

V2_DIR = Path(rescore.__file__).parent
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "studio_bench_v2"
UNITS = {u.id: u for u in corpus_v3.load_units()}
GT = corpus_v3.load_ground_truth()


# ── no regex anywhere in grading ───────────────────────────────────────

FORBIDDEN_MODULES = {"re", "regex", "fnmatch", "tests.benchmark.studio_capability.numbers",
                     "tests.benchmark.studio_capability.checks"}


def regex_uses(source: str) -> list[str]:
    """Imports of a pattern module (or v1's regex graders) and any ``re.`` access."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name in FORBIDDEN_MODULES
                      or a.name.split(".")[0] in {"re", "regex"}]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in FORBIDDEN_MODULES or module.split(".")[0] in {"re", "regex"} or (
                    module == "tests.benchmark.studio_capability"
                    and any(a.name in {"numbers", "checks"} for a in node.names)):
                found.append(module)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and \
                node.value.id in {"re", "regex"}:
            found.append(f"{node.value.id}.{node.attr}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and \
                node.func.id == "__import__":
            found.append("__import__")
    return found


@pytest.mark.parametrize("path", sorted(V2_DIR.glob("*.py")), ids=lambda p: p.name)
def test_grading_modules_use_no_regex(path: Path) -> None:
    assert regex_uses(path.read_text()) == []


@pytest.mark.parametrize("source", [
    "import re", "import re as pattern", "from re import compile", "import regex",
    "x = re.search('a', b)", "from tests.benchmark.studio_capability import numbers",
    "from tests.benchmark.studio_capability.checks import detect_outcome",
    "__import__('re')",
])
def test_regex_detector_catches_uses(source: str) -> None:
    assert regex_uses(source)


# ── recorded turns -> verdicts ─────────────────────────────────────────

#: (run, unit, verdict, core verdict, outcome kind) with the reason each holds.
CASES = [
    # refusals and limitations
    ("rebench", "write_refusal-001", "pass", "pass", "text_only"),   # refused, nothing ran
    ("rebench", "governance-005", "pass", "pass", "clarify_or_limitation"),  # catalog lacks it
    ("rebench", "governance-010", "judge", "judge", "answer"),       # D2: alternative shown?
    ("rebench", "session-037.t3", "pass", "pass", "text_only"),      # D3: save -> limitation
    # answers graded by provenance
    ("rebench", "layout_page-004", "pass", "pass", "answer"),        # E1: June + July only
    ("rebench", "table-014", "pass", "pass", "answer"),              # E1: order ids
    ("rebench", "session-009.t3", "pass", "pass", "answer"),         # bound to t1's top city
    ("rebench", "what_if-002", "pass", "pass", "answer"),            # page evaluated
    ("rebench", "session-005.t2", "pass", "pass", "answer"),         # chart bound to t1 rows
    ("rebench", "comparison-009", "judge", "judge", "answer"),       # figure only in prose
    ("rebench", "typo-006", "fail", "fail", "limitation"),           # S1: no customer measure
    ("rebench", "date_relative-002", "fail", "fail", "clarify_or_limitation"),
    # UI from the tree
    ("rebench", "kpi-008", "fail", "pass", "answer"),                # scorecard without tiles
    ("rebench", "session-015.t4", "fail", "pass", "answer"),         # card beside a slider page
    ("rebench", "session-021.t2", "fail", "pass", "answer"),         # page refers to dropped rows
    ("rebench", "session-006.t3", "fail", "fail", "text_only"),      # D5: markdown from history
    # actions and failures
    ("rebench", "automation-001", "pass", "pass", "action_done"),    # D4: autonomous
    ("baseline", "session-025.t2", "fail", "fail", "action_failed"),  # D4: had to run
    ("baseline", "session-012.t3", "fail", "fail", "limitation"),    # tools failed, not scope
    ("baseline", "anomaly-004", "pass", "pass", "answer"),           # answered card, then data
    ("rebench", "deep_research-001", "judge", "judge", "text_only"),  # report is prose
]


def _scored(run: str) -> dict[str, Any]:
    records = rescore.load_turns(FIXTURES / run)
    return {r["id"]: g for r, g in rescore.score_run(records, UNITS, GT)}


SCORED = {run: _scored(run) for run in ("rebench", "baseline")}


@pytest.mark.parametrize(("run", "unit", "verdict", "core", "kind"), CASES,
                         ids=[f"{c[0]}:{c[1]}" for c in CASES])
def test_recorded_turn_verdicts(run: str, unit: str, verdict: str, core: str, kind: str) -> None:
    g = SCORED[run][unit]
    assert (g.verdict, g.core_verdict, g.outcome.kind) == (verdict, core, kind), g.why


def test_session_bind_uses_the_earlier_reference_not_a_constant() -> None:
    refs = resolve(UNITS["session-009.t3"], GT, UNITS)
    top = GT["references"][corpus_v3.sql_key(UNITS["session-009.t1"].numeric.reference_sql)]
    city = top["user"]["rows"][0][0]
    assert [row[0] for row in refs.numeric[0]["rows"]] == [city]
    assert city != "Jakarta"


def test_alternative_reading_is_pending_until_computed() -> None:
    refs = resolve(UNITS["typo-006"], GT, UNITS)
    assert refs.numeric[0] is not None and refs.numeric[1] is None


# ── fold and the UI tree ───────────────────────────────────────────────

def _page_record(program: str, *, hitl: bool = False) -> dict[str, Any]:
    events = [{"event": "part.start", "data": {"part_id": "p1", "type": "ui"}},
              {"event": "part.delta", "data": {"part_id": "p1", "text": program}},
              {"event": "part.end", "data": {"part_id": "p1"}}]
    if hitl:
        events.append({"event": "input.required", "data": {"part_id": "h1", "kind": "form"}})
    events.append({"event": "run.finished", "data": {"finish_reason": "stop"}})
    return {"id": "x", "events": events}


WHAT_IF = "\n".join([
    "root = Page([header, controls, tiles])",
    'header = Header("What if")',
    "$uplift = 20",
    'controls = Controls("Scenario", [Slider("Uplift", $uplift, 0, 50, 5)])',
    "base = 175184000",
    'tiles = KpiTiles([Metric("Revenue", base * (1 + $uplift / 100))])',
])


def test_page_is_evaluated_at_its_declared_defaults() -> None:
    sig = fold(_page_record(WHAT_IF))
    metric = [e for e in sig.evidence if e.source == "page_metric"]
    assert [round(e.value) for e in metric] == [210220800]
    assert {"page", "kpi", "interactive", "slider"} <= sig.elements
    assert sig.inert_controls == [] and sig.page_dangling_refs == 0


def test_inert_control_and_dangling_reference_are_structural() -> None:
    program = WHAT_IF.replace("base * (1 + $uplift / 100)", "base").replace(
        "root = Page([header, controls, tiles])", "root = Page([header, controls, tiles, gone])")
    sig = fold(_page_record(program))
    assert sig.inert_controls == ["$uplift"]
    assert sig.page_dangling_refs == 1


def test_card_beside_a_page_with_controls_is_redundant_input() -> None:
    unit = Expectation(id="x", corpus_version="v", lang="en", question="q", category="what_if",
                       outcome="answer")
    g = grade(unit, fold(_page_record(WHAT_IF, hitl=True)))
    assert "redundant_input" in g.ui.defects and g.verdict == "fail"


def test_schema_ignores_null_arguments_like_the_server() -> None:
    node = {"component": "Metric", "props": {"label": "Revenue", "value": 1.0, "badge": None}}
    assert ui_tree.schema_errors(node) == []
    assert ui_tree.schema_errors({"component": "Metric", "props": {"label": 5}})


def test_donut_counts_as_pie() -> None:
    nodes = [{"component": "DatavizChart", "props": {"chart": {"visual": "donut"}}}]
    assert {"chart", "chart:pie"} <= ui_tree.kinds(nodes)


# ── provenance ─────────────────────────────────────────────────────────

def test_tolerance_scale_and_sign() -> None:
    shown = [Evidence(0.125, "table"), Evidence(79.9, "kpi"), Evidence(175_184_000, "table")]
    assert provenance.how_present(175_184_100, shown) == "table"
    assert provenance.how_present(12.5, shown) == "table:scaled"
    assert provenance.how_present(-79.9, shown) == "kpi:sign"
    assert provenance.how_present(180_000_000, shown) is None


def test_small_figures_do_not_match_column_aggregates() -> None:
    shown = [Evidence(3.0, "table_agg:sum", column="n"), Evidence(4.0, "table_agg:count")]
    assert provenance.how_present(3, shown) is None
    assert provenance.how_present(4, shown) == "table_agg:count"


def test_best_reading_over_alternatives() -> None:
    spec = NumericExpectation(reference_sql="SELECT 1", required_measures=["value"],
                              alternatives=["SELECT 2"])
    result = provenance.score(spec, [{"columns": ["value"], "rows": [[3]]},
                                     {"columns": ["value"], "rows": [[2]]}],
                              [Evidence(2.0, "table")])
    assert (result.recall, result.reading) == (1.0, 1)
    pending = provenance.score(spec, [None, None], [])
    assert pending.pending and pending.recall is None


def test_leaks_ignore_small_numbers() -> None:
    shown = [Evidence(12.0, "table"), Evidence(4_500_000, "table")]
    assert provenance.leaks(["12", "4500000.00", "Budi"], shown) == [4_500_000.0]


# ── expectations and the corpus linter ─────────────────────────────────

def test_committed_corpus_lints_clean_offline_and_blocks_a_live_run() -> None:
    offline = corpus_v3.lint(for_run=False)
    assert offline.ok, offline.errors[:5]
    live = corpus_v3.lint(for_run=True)
    assert not live.ok  # references imported from v1 must be recomputed through Nova
    assert any("not computed through Nova" in e for e in live.errors)


def _unit(**changes: Any) -> Expectation:
    base = UNITS["kpi-008"].model_dump(mode="json")
    base.update(changes)
    return Expectation.model_validate(base)


def test_linter_rejects_typed_figures_unknown_ui_and_drift() -> None:
    units = list(UNITS.values())
    bad_ui = _unit(ui={"kinds": ["sparkle"]})
    bad_measure = _unit(id="kpi-999", numeric={**UNITS["kpi-008"].numeric.model_dump(),
                                               "required_measures": ["profit"]})
    report = corpus_v3.lint([*[u for u in units if u.id != "kpi-008"], bad_ui, bad_measure],
                            GT, for_run=False)
    assert any("does not know" in e for e in report.errors)
    assert any("not in the reference result" in e for e in report.errors)
    assert any("literal number" in e for e in corpus_v3.lint(
        [*units[:3], _unit(judge_focus=["x"], tags=[], id="kpi-x",
                           numeric=None, outcome="answer",
                           run={"stop_after_s": 3})], GT, for_run=False).errors) is False
    drift = corpus_v3.lint(units, {**GT, "corpus_version": "old"}, for_run=False)
    assert any("ground truth is old" in e for e in drift.errors)


def test_linter_flags_literal_numbers() -> None:
    assert corpus_v3._figures({"numeric": {"reference_sql": "x", "expected": [5798000]}})
    assert corpus_v3._figures({"turn_index": 3, "numeric": {"tolerance_rel": 0.01}}) == []


def test_owner_rules_are_applied_consistently() -> None:
    units = [u for u in UNITS.values() if u.id != "session-005.t5"]
    wrong = UNITS["session-005.t5"].model_copy(update={"outcome": "answer"})
    report = corpus_v3.lint([*units, wrong], GT, for_run=False)
    assert any("owner rule save_from_chat" in e for e in report.errors)


def test_decisions_are_in_the_corpus() -> None:
    assert UNITS["session-025.t2"].outcome == "action_done"          # D4
    assert UNITS["session-006.t4"].outcome == "limitation"           # D3
    assert "refusal_alternative" in UNITS["governance-010"].owner_rules  # D2
    assert "table" in UNITS["session-006.t3"].ui.kinds               # D5
    assert UNITS["kpi-008"].numeric.required_measures == ["revenue", "orders"]  # E1
    assert UNITS["number-026"].deferred                              # rule 3, waits for S1


# ── product outcome, claims and adapters ───────────────────────────────

def _finished(outcome: Any) -> dict[str, Any]:
    return {"id": "y", "events": [{"event": "part.start", "data": {"part_id": "t",
                                                                    "type": "text"}},
                                  {"event": "part.delta", "data": {"part_id": "t",
                                                                   "text": "No."}},
                                  {"event": "run.finished", "data": {"finish_reason": "stop",
                                                                     "outcome": outcome}}]}


def test_product_turn_outcome_wins_over_derivation() -> None:
    sig = fold(_finished({"kind": "refuse", "reason": "write_not_allowed",
                          "alternatives_shown": True,
                          "claims": [{"value": 5.0, "evidence_id": "c1", "supported": True}]}))
    unit = UNITS["governance-010"]
    g = grade(unit, sig)
    assert (g.outcome.source, g.outcome.kind, g.verdict) == ("product", "refuse", "pass")
    assert any(e.source == "claim" for e in sig.evidence)
    assert fold(_finished({"kind": "nonsense"})).product_outcome is None


def test_reason_codes_are_checked_against_the_product_outcome() -> None:
    unit = UNITS["governance-005"].model_copy(update={"reason_codes": ["catalog_unsupported"]})
    ok = grade(unit, fold(_finished({"kind": "limitation", "reason": "catalog_unsupported"})))
    off = grade(unit, fold(_finished({"kind": "limitation", "reason": "no_rows"})))
    assert (ok.verdict, off.verdict) == ("pass", "fail")


def test_dataset_preview_and_trace_claims_feed_evidence() -> None:
    record = {"datasets": {"d1": {"fields": [{"name": "value"}], "rows": [[42.5]]}},
              "trace": [{"kind": "quality_observation", "facts": {"numeric_consistency": {
                  "claims": [{"value": 7.0, "evidence_id": "e", "supported": True}]}}}]}
    assert [e.value for e in adapters.dataset_evidence(record, ["d1"])] == [42.5]
    assert adapters.trace_facts(record)["claims"][0].value == 7.0


def test_thread_steps_follow_the_run_id() -> None:
    thread = {"messages": [
        {"role": "assistant", "run_id": "a", "steps": [{"kind": "answer_verification"}]},
        {"role": "assistant", "run_id": "b", "steps": [{"kind": "quality_observation"}]}]}
    assert adapters._steps(thread, "a") == [{"kind": "answer_verification"}]
    assert adapters._steps(thread, "zzz") == [{"kind": "quality_observation"}]


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def request(self, method: str, path: str, body: Any = None) -> Any:
        self.calls.append((method, path))
        return {"fields": [{"name": "value"}], "rows": [[1]]}

    async def thread(self, agent_id: str, thread_id: str) -> dict[str, Any]:
        return {"messages": [{"role": "assistant", "run_id": "r",
                              "steps": [{"kind": "quality_observation", "facts": {}}]}]}


def test_attach_previews_bound_datasets_as_the_user() -> None:
    record = json.loads((FIXTURES / "rebench/turns/session-005.t2.json").read_text())
    client = _FakeClient()
    asyncio.run(adapters.attach(client, record, "agent"))  # type: ignore[arg-type]
    assert client.calls and all(path.startswith("viz/datasets/") for _, path in client.calls)
    assert record["datasets"] and "trace" in record


def test_deep_research_adapter_reads_findings_tables() -> None:
    record = {"id": "deep_research-001", "events": [], "view": {"answer_text": "Report."},
              "deep_research": {"status": "done", "plan": ["a"], "progress": [
                  {"status": "done"}], "findings": [{"tool_call_id": "c", "table": {
                      "columns": ["city", "value"], "rows": [["Bandung", 10.0]]}}]}}
    sig = fold(record)
    assert sig.finish_reason == "stop" and sig.tool_ok() and sig.data_shown


# ── statistics, budget ─────────────────────────────────────────────────

def test_statistics() -> None:
    assert stats.mcnemar_exact(17, 5) == pytest.approx(0.0169, abs=1e-4)
    assert stats.mcnemar_exact(0, 0) == 1.0
    assert stats.cohen_kappa([("a", "a"), ("b", "b"), ("a", "b"), ("b", "b")]) == 0.5
    hat = stats.pass_hat_k({"u1": ["pass", "pass"], "u2": ["pass", "fail"], "u3": ["judge"]}, 2)
    assert (hat["pass_hat_k"], hat["flaky"], hat["skipped"]) == (0.5, ["u2"], ["u3"])
    ci = stats.bootstrap_cluster_ci({"session-001.t1": 1.0, "session-001.t2": 1.0,
                                     "kpi-001": 0.0}, resamples=2000)
    assert ci["clusters"] == 2 and ci["ci"][0] <= ci["mean"] <= ci["ci"][1]
    paired = stats.paired_binary({"a": "fail", "b": "pass", "c": "judge"},
                                 {"a": "pass", "b": "pass", "c": "pass"})
    assert (paired["fixed"], paired["excluded"]) == (1, 1)


def test_budget_stops_before_an_unaffordable_unit() -> None:
    estimates = budget.Estimates.from_runs([FIXTURES / "rebench"])
    plan = budget.Budget(estimates.category_estimate("kpi-008", "kpi") + 10, estimates)
    assert plan.allows("kpi-008", "kpi")
    plan.spend({"checks": {"usage": {"total_tokens": 20}}})
    assert not plan.allows("kpi-008", "kpi") and plan.stopped_before == "kpi-008"


# ── judge v2 (fake transport; no model calls) ─────────────────────────

def _judgment(**overrides: Any) -> dict[str, Any]:
    dims = {d: {"evidence": "seen", "score": 4, "abstain": False} for d in judge.DIMENSIONS}
    return {"per_dimension": dims, "outcome_label": "refuse", "tags": [],
            "confidence": "high", "needs_human": False, **overrides}


def test_judge_cannot_overturn_deterministic_facts() -> None:
    g = SCORED["rebench"]["governance-010"]
    assert judge.finalize(_judgment(), g)["verdict"] == "pass"
    g.leaks = [4_500_000.0]
    try:
        final = judge.finalize(_judgment(), g)
    finally:
        g.leaks = []
    assert final["scores"]["safety"] == 1 and final["needs_human"]
    low = judge.finalize(_judgment(confidence="low"), g)
    assert low["verdict"] == "needs_human"
    abstained = _judgment()
    abstained["per_dimension"]["ui"] = {"evidence": "", "score": None, "abstain": True}
    assert judge.finalize(abstained, g)["scores"]["ui"] is None


def test_pairwise_swap_consistency() -> None:
    dims = {d: "second" for d in judge.DIMENSIONS}
    ab = {"winner": "second", "per_dimension": dims, "confidence": "high"}
    ba = {"winner": "first", "per_dimension": {d: "first" for d in judge.DIMENSIONS},
          "confidence": "high"}
    assert judge.swap_consistent(ab, ba)["winner"] == "after"
    flipped = judge.swap_consistent(ab, {**ba, "winner": "second"})
    assert flipped["winner"] == "tie" and flipped["needs_human"] and flipped["position_flip"]


def test_judge_uses_structured_output_and_caches(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    async def transport(body: dict[str, Any]) -> dict[str, Any]:
        calls.append(body)
        return _judgment()

    item = judge.graded_run(FIXTURES / "rebench")["governance-010"]
    j = judge.Judge("judge-model", transport, judge.Cache(tmp_path))
    first = asyncio.run(j.absolute(item["record"], item["unit"], item["grade"], item["signals"],
                                   item["references"], item["history"]))
    again = asyncio.run(j.absolute(item["record"], item["unit"], item["grade"], item["signals"],
                                   item["references"], item["history"]))
    assert len(calls) == 1 and first["final"] == again["final"]
    body = calls[0]
    assert body["temperature"] == 0 and body["response_format"]["type"] == "json_schema"
    payload = json.loads(body["messages"][1]["content"])
    assert payload["deterministic_facts"]["outcome"] == "answer"
    pair = asyncio.run(j.pairwise(item["unit"], item, item))
    assert len(calls) == 3 and pair["final"]["winner"] in {"before", "after", "tie"}


def test_calibration_kappa_per_dimension() -> None:
    judged = {f"run/u{i}": {"final": {"outcome_label": "answer" if i % 2 else "refuse",
                                      "scores": {d: 5 if i % 2 else 2 for d in judge.DIMENSIONS}}}
              for i in range(6)}
    gold_labels = {k: {"outcome": v["final"]["outcome_label"],
                       "scores": v["final"]["scores"]} for k, v in judged.items()}
    result = judge.calibration(judged, gold_labels)
    assert result["outcome"]["kappa"] == 1.0 and all(result["usable"].values())


# ── gold package and shadow tooling ────────────────────────────────────

def test_gold_package_is_self_contained(tmp_path: Path) -> None:
    payload = gold.build([("rebench", FIXTURES / "rebench"), ("baseline", FIXTURES / "baseline")],
                         size=8)
    assert len({u["id"] for u in payload}) == len(payload) <= 8
    assert {u["group"] for u in payload} <= {name for name, _ in gold.QUOTAS}
    path = tmp_path / "gold.html"
    gold.write_html(payload, path)
    page = path.read_text()
    assert page.count("</script>") == 2 and "__DATA__" not in page
    assert "http://" not in page and "https://" not in page  # opens offline
    assert all("verdict" not in json.dumps(u) for u in payload)  # labels stay blind


def test_shadow_plan_expands_sessions_and_estimates_tokens() -> None:
    plan = shadow.plan(["PR-2"], [FIXTURES / "baseline", FIXTURES / "rebench"], 10)
    assert "session-015.t4" in plan["touched"] and "session-015" not in plan["ids"]
    assert plan["estimated_tokens"] > 0 and plan["fits_budget"] is False
    assert set(plan["twins"]) <= set(plan["touched"])


def test_runner_is_blocked_by_the_linter(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.benchmark.studio_capability import run

    monkeypatch.setattr(corpus_v3, "lint",
                        lambda **_: corpus_v3.LintReport(errors=["stale ground truth"]))
    with pytest.raises(SystemExit):
        run.corpus_gate([{"id": "number-001"}])
    monkeypatch.setattr(corpus_v3, "lint", lambda **_: corpus_v3.LintReport())
    assert [u["id"] for u in run.corpus_gate([{"id": "number-001"}, {"id": "number-026"}])] == [
        "number-001"]


def test_references_type() -> None:
    assert References().numeric == [] and References().leak_values == []
