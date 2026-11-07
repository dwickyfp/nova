"""Measured accuracy: gold sets, answer runs, claim audits and the Overview numbers."""

from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan, UnresolvedConcept
from app.modules.ontology import access
from app.modules.ontology import grounding as grounding_module
from app.modules.ontology.contracts import Actor
from app.modules.ontology.derived import compile_view
from app.modules.ontology.evaluation import gold
from app.modules.ontology.evaluation.judging import cell, same_result
from app.modules.ontology.evaluation.metrics import overview
from app.modules.ontology.evaluation.service import (
    AnswerRunRequest,
    ClaimRunRequest,
    EvaluationService,
    GoldRequest,
    ReviewBatch,
    ReviewItem,
    compare,
)
from app.modules.ontology.grounding import Grounding
from tests.benchmark.retail_field.views import VIEWS
from tests.unit._ontology import ADMIN, FakeRepository, user
from tests.unit.test_ontology_derived import lab_catalog

OTHER_ADMIN = user("ada", "ACCOUNTADMIN")
ANALYST = user("ana", "retail_analyst")


# --- matching ---------------------------------------------------------------------------

def test_cells_compare_days_and_four_significant_digits():
    assert cell(datetime(2026, 10, 1)) == "2026-10-01"
    assert cell("2026-10-01 00:00:00") == "2026-10-01"
    assert cell(date(2026, 10, 1)) == "2026-10-01"
    assert cell(1234.5678) == "1235" and cell("1234.0") == "1234"
    assert cell(0.123449) == "0.1234"
    assert cell(None) == "null" and cell(0) == "0" and cell("Jakarta") == "Jakarta"


def test_results_match_like_genie_benchmarks():
    gold_columns, gold_rows = ["city", "revenue"], [["A", 10.0], ["B", 20.0]]
    assert same_result(["revenue", "city", "share"], [[20.00001, "B", 0.6], [10, "A", 0.4]],
                       gold_columns, gold_rows) == (True, "")
    ok, reason = same_result(["city", "revenue"], [["A", 10], ["B", 21]], gold_columns, gold_rows)
    assert not ok and "revenue" in reason
    assert same_result(["c", "r"], [["A", 10]], gold_columns, gold_rows)[1] == "1 rows instead of 2"
    assert same_result(["c", "r"], [], gold_columns, gold_rows)[1] == "the answer is empty"
    assert not same_result(["c"], [["A"]], ["c"], [])[0]
    # Ordered plans must keep their order.
    assert not same_result(["c", "r"], [["B", 20], ["A", 10]], gold_columns, gold_rows,
                           ordered=True)[0]
    # Ties on the sort key may come in any order.
    tied_columns, tied = ["name", "rate"], [["A", 0.5], ["B", 0.5], ["C", 0.1]]
    assert same_result(["name", "rate"], [["B", 0.5], ["A", 0.5], ["C", 0.1]], tied_columns, tied,
                       ordered=True, order_key="rate") == (True, "")
    assert not same_result(["name", "rate"], [["C", 0.1], ["A", 0.5], ["B", 0.5]], tied_columns,
                           tied, ordered=True, order_key="rate")[0]
    # Same values, paired differently, are a different answer.
    assert not same_result(["c", "r"], [["A", 20], ["B", 10]], gold_columns, gold_rows)[0]


# --- gold -------------------------------------------------------------------------------

def _model(name: str):
    from app.modules.agents.semantic.ir import SemanticModelIR

    definition = parse_ossie(json.dumps(VIEWS[name])).model
    return definition, SemanticModelIR.from_ossie(definition)


def test_periods_are_whole_and_absolute():
    whole, trend = gold.periods("2026-05-01", "2026-11-04")
    assert [p.range for p in whole] == ["2026-10", "2026-09", "2026-08", "2026-Q3"]
    assert trend.range == "2026-05-01..2026-10-31"
    whole, _ = gold.periods("2026-01-01", "2026-03-31")
    assert whole[0].range == "2026-03" and whole[-1].range == "2026-Q1"


def test_breakdowns_skip_keys_coordinates_and_amounts():
    _, ir = _model("retail_sales")
    names = {name for name, _ in gold.breakdowns(ir, "net_revenue")}
    assert {"outlets.city", "products.category", "sales_orders.payment_method"} <= names
    assert not {"outlets.latitude", "outlets.outlet_id", "sales_orders.salesman_id"} & names
    # Active outlets counts outlets: never broken down or filtered by an outlet field.
    counted = {name for name, _ in gold.breakdowns(ir, "active_outlets")}
    assert counted and not any(name.startswith("outlets.") for name in counted)
    # Labels keep two fields with the same words apart.
    assert all("[" in label for _, label in gold.breakdowns(ir, "net_revenue"))


def test_intents_are_deterministic_and_valid_for_every_lab_view():
    from app.modules.agents.semantic.compiler import SemanticCompiler
    from app.modules.agents.semantic.planning import validate_plan
    from app.modules.ontology.representations import public_metrics

    for name in VIEWS:
        definition, ir = _model(name)
        metrics = list(public_metrics(definition))
        first = gold.intents(ir, name, name, metrics, ("2026-05-01", "2026-11-04"),
                             per_metric=4, seed=7)
        again = gold.intents(ir, name, name, metrics, ("2026-05-01", "2026-11-04"),
                             per_metric=4, seed=7)
        assert [i.plan for i in first] == [i.plan for i in again]
        assert len(first) >= 3 * len(metrics)
        for intent in first:
            plan = dict(intent.plan)
            if intent.needs_value:
                plan = gold.with_value(intent, "X").plan
            parsed = SemanticPlan.from_dict(plan)
            assert validate_plan(ir, parsed) == []
            SemanticCompiler().compile(ir, parsed)


# --- a deterministic stand-in for the engine and the model -----------------------------

def models_for(catalog):
    async def load(_user):
        return {
            view_id: {"semantic_model_id": view_id, "name": view.name,
                      "definition": view.definition, "database_name": "nova_retail_field"}
            for view_id, view in catalog.views.items()
        }
    return load


async def engine(sql, model, _user):
    """Rows derived from the SQL text: the same plan gives the same answer."""
    from app.modules.intelligence.contracts import fingerprint

    digest = int(fingerprint(sql)[:6], 16)
    if "GROUP BY" in sql.upper() and "day" in sql.lower():
        return ["day", "value"], [["2026-05-01", 1], ["2026-11-04", 2]], None
    return ["label", "value"], [["v1", digest % 997], ["v2", digest % 991]], None


class FakeModel:
    """Answers each structured call by its schema name."""

    def __init__(self, verdicts=None) -> None:
        self.calls = []
        self.verdicts = verdicts or {}

    async def resolve(self, **_):
        return SimpleNamespace(model="fake-judge",
                               capabilities=SimpleNamespace(supports_json_schema=True))

    async def complete(self, *, messages, provider, response_format=None, **_):
        name = (response_format or {}).get("json_schema", {}).get("name")
        payload = json.loads(messages[1]["content"])
        self.calls.append(name)
        if name == "evaluation_questions":
            items = [{"id": item["id"], "question": f"Pertanyaan tentang {item['meaning']}"}
                     for item in payload["items"]]
            return {"content": json.dumps({"items": items})}
        if name in {"evaluation_review", "ontology_claims"}:
            items = [{"id": item["id"], "verdict": self.verdicts.get(item["id"], "yes"),
                      "reason": "checked"} for item in payload["items"]]
            return {"content": json.dumps({"items": items})}
        if name == "view_route":
            return {"content": json.dumps({"reason": "stock words", "view_id": "outlet_stock"})}
        if name == "evaluation_negatives":
            view = payload["catalog"][0]["view_id"]
            return {"content": json.dumps({
                "unanswerable": [{"question": f"Berapa kepuasan pelanggan {i}?", "language": "id",
                                  "view_id": view, "reason": "no satisfaction measure"}
                                 for i in range(30)],
                "seeded": [{"seed": item["seed"], "question": f"Ambigu {item['seed']}",
                            "language": "id", "view_id": view}
                           for item in payload.get("ambiguous_seeds", [])],
                "vague": [{"question": f"Berapa penjualan {i}?", "language": "id",
                           "view_id": view, "measures": ["gross_revenue", "net_revenue"]}
                          for i in range(3)],
            })}
        return {"content": "{}"}


class Lab:
    def __init__(self, monkeypatch, verdicts=None) -> None:
        self.repository = FakeRepository()
        self.catalog = lab_catalog()
        self.model = FakeModel(verdicts)
        self.service = EvaluationService(
            self.repository, self.catalog, client_factory=lambda: self.model,
            executor=engine, models=models_for(self.catalog),
        )

        async def record(**_):
            return "audit"

        monkeypatch.setattr(access, "write_audit_log", record)

    async def compile(self):
        actor = Actor.from_user(ADMIN)
        for view_id in sorted(self.catalog.views):
            await compile_view(await self.catalog.published(view_id), actor, self.repository)

    async def finish(self, run, caller=ADMIN, limit=200):
        for _ in range(limit):
            run = await self.service.step(run.id, caller)
            if run.status != "RUNNING":
                return run
        raise AssertionError("run did not finish")


@pytest.fixture
def lab(monkeypatch):
    return Lab(monkeypatch)


async def test_a_gold_set_has_answerable_unanswerable_and_ambiguous_cases(lab):
    await lab.compile()
    run = await lab.service.start_gold(
        GoldRequest(set_name="serbadata", view_ids=["retail_sales"], per_metric=3), ADMIN)
    run = await lab.finish(run)
    assert run.status == "COMPLETED", run.error
    cases = await lab.service.cases("serbadata")
    active = [case for case in cases if case.status == "ACTIVE"]
    kinds = {case.kind for case in active}
    assert kinds == {"answerable", "unanswerable", "ambiguous"}
    answerable = [case for case in active if case.kind == "answerable"]
    assert len(answerable) >= 30
    share = sum(1 for case in active if case.kind == "unanswerable") / len(active)
    assert 0.1 <= share <= 0.2
    assert all(case.plan and case.question and case.reviews for case in answerable)
    assert all("2026" in json.dumps(case.plan) for case in answerable)  # absolute periods
    # A role the sales view does not have is asked about as a clarification.
    assert any(case.intent == "role_mismatch" and case.kind == "unanswerable"
               for case in active)
    assert {"evaluation_questions", "evaluation_review", "evaluation_negatives"} <= set(
        lab.model.calls)
    second = await lab.finish(await lab.service.start_gold(
        GoldRequest(set_name="serbadata", view_ids=["retail_sales"], per_metric=1), ADMIN))
    assert second.version == 2


async def test_rejected_cases_never_enter_the_gold_set(monkeypatch):
    lab = Lab(monkeypatch)
    lab.model.verdicts = {}
    run = await lab.service.start_gold(
        GoldRequest(set_name="strict", view_ids=["outlet_stock"], per_metric=2), ADMIN)
    # The judge says no to every second case it sees.
    original = lab.model.complete

    async def picky(**kwargs):
        response = await original(**kwargs)
        name = (kwargs.get("response_format") or {}).get("json_schema", {}).get("name")
        if name == "evaluation_review":
            body = json.loads(response["content"])
            for index, item in enumerate(body["items"]):
                item["verdict"] = "no" if index % 2 else "yes"
            response = {"content": json.dumps(body)}
        return response

    lab.model.complete = picky
    run = await lab.finish(run)
    cases = await lab.service.cases("strict")
    assert any(case.status == "REJECTED" for case in cases)
    assert all(review.verdict == "yes" for case in cases if case.status == "ACTIVE"
               for review in case.reviews)


def fake_planner(monkeypatch, wrong: set[str]):
    """Plans the gold plan, except for the questions listed, which get another metric."""
    import app.modules.agents.semantic.model_planner as planner

    async def catalog(_ir, _question):
        return {}

    async def plan(_provider, ir, _catalog, question, context, business_concepts=None, **_):
        context.usage = {"total_tokens": 100}
        case = PLANS.get(question)
        if case is None:
            return SemanticPlan(unresolved_concepts=(UnresolvedConcept(
                text="kepuasan", material=True),))
        value = dict(case)
        if question in wrong and business_concepts is None:
            other = "gross_revenue" if value["metrics"] != ["gross_revenue"] else "net_revenue"
            value["metrics"] = [other]
            value["order_by"] = [dict(item, field=other) for item in value.get("order_by", [])]
        return SemanticPlan.from_dict(value)

    monkeypatch.setattr(planner, "planning_catalog", catalog)
    monkeypatch.setattr(planner, "generate_plan", plan)


PLANS: dict[str, dict] = {}


def fake_grounding(monkeypatch):
    async def ground(question, context, load_models, **_):
        if question.startswith("Ambigu") or question.startswith("Berapa penjualan"):
            return Grounding(status="ambiguous", error_class="CLARIFICATION_REQUIRED")
        case = PLANS.get(question)
        if case is None:
            return Grounding(status="none")
        return Grounding(status="grounded", view_id=context.requested_semantic_view, required={
            case["metrics"][0]: {"qualified_name": "Penjualan.X", "label": "X",
                                 "definition": "d", "id": "c1"}})

    monkeypatch.setattr(grounding_module, "ground", ground)


async def test_answer_runs_measure_the_ontology_against_the_same_cases(lab, monkeypatch):
    await lab.compile()
    gold_run = await lab.finish(await lab.service.start_gold(
        GoldRequest(set_name="serbadata", view_ids=["retail_sales"], per_metric=2), ADMIN))
    assert gold_run.status == "COMPLETED"
    cases = [case for case in await lab.service.cases("serbadata") if case.status == "ACTIVE"]
    PLANS.clear()
    PLANS.update({case.question: case.plan for case in cases if case.kind == "answerable"})
    wrong = set(list(PLANS)[: len(PLANS) // 3])
    fake_planner(monkeypatch, wrong)
    fake_grounding(monkeypatch)
    with pytest.raises(HTTPException) as unpublished:
        await lab.service.start_answers(
            AnswerRunRequest(set_name="serbadata", mode="with_ontology"), ADMIN)
    assert unpublished.value.status_code == 409

    without = await lab.finish(await lab.service.start_answers(
        AnswerRunRequest(set_name="serbadata", mode="without_ontology"), ADMIN))
    assert without.status == "COMPLETED", without.error
    run = await lab.service.start_answers(
        AnswerRunRequest(set_name="serbadata", mode="without_ontology"), ADMIN)
    run = run.model_copy(update={"mode": "with_ontology", "release_id": "ont_rel_test",
                                 "revision": run.revision + 1})
    await lab.repository.save("eval_runs", run, expected_revision=run.revision - 1)
    with_run = await lab.finish(run)
    assert with_run.status == "COMPLETED", with_run.error

    base, onto = without.summary, with_run.summary
    answerable = base["answer_accuracy"]["n"]
    assert base["answer_accuracy"]["successes"] == answerable - len(wrong)
    assert onto["answer_accuracy"]["successes"] == answerable
    assert onto["answer_accuracy"]["interval_95"][0] > 0.85
    # Ambiguous cases clarify with the ontology; unanswerable ones clarify in both.
    assert onto["clarification"]["value"] == 1.0
    assert onto["abstention"]["value"] == 1.0
    assert onto["reliability"]["value"] > base["reliability"]["value"]

    effect = compare(await lab.service.results(with_run.id),
                     await lab.service.results(without.id))
    assert effect["mcnemar"]["gained"] == len(wrong) and effect["mcnemar"]["lost"] == 0
    assert effect["mcnemar"]["p_value"] < 0.05
    # Result rows are never stored: only their count. A filtered question keeps the
    # category value it asks about (it is part of the question), never a measure.
    for item in await lab.service.results(with_run.id):
        assert isinstance(item.detail.get("rows", 0), int)
        assert isinstance(item.detail.get("gold_rows", 0), int)
    assert all(isinstance(item.detail.get("latency_ms"), int)
               for item in await lab.service.results(with_run.id))

    # The Overview pairs the latest run with and without the ontology on one gold set.
    shown = await overview(ADMIN, lab.service)
    assert shown["measured"]["effect"]["mcnemar"]["gained"] == len(wrong)
    assert shown["measured"]["answers"]["run_id"] == with_run.id
    assert shown["measured"]["baseline"]["run_id"] == without.id
    assert shown["measured"]["history"][-1]["run_id"] == with_run.id
    assert shown["live"]["question_grounding"]["value"] == 1.0
    assert "Answer accuracy needs a completed run with the ontology" not in shown["not_measured"]


async def test_claims_are_audited_by_stratum_with_calibration_and_kappa(monkeypatch):
    lab = Lab(monkeypatch)
    await lab.compile()
    run = await lab.service.start_claims(ClaimRunRequest(per_stratum=6), ADMIN)
    strata = run.summary["population"]
    assert {"entity", "dimension", "metric", "measures", "derives"} <= set(strata)
    no = {key for key in run.items if key.startswith("derived:relation:DERIVES")}
    lab.model.verdicts = dict.fromkeys(no, "no")
    run = await lab.finish(run)
    summary = run.summary
    assert summary["by_stratum"]["derives"]["value"] == 0.0
    assert summary["by_stratum"]["entity"]["value"] == 1.0
    estimate = summary["claim_accuracy"]
    assert 0 < estimate["value"] < 1 and estimate["interval_95"][0] <= estimate["value"]
    # Constant confidences against a mixed outcome are miscalibrated, and it shows.
    assert summary["calibration"]["n"] > 0 and summary["calibration"]["ece"] > 0

    results = await lab.service.results(run.id)
    review = ReviewBatch(reviewer="claude-opus-5.5", kind="external", items=[
        ReviewItem(item=item.item, verdict=item.outcome) for item in results[:10]
    ])
    agreement = (await lab.service.review_results(run.id, review, ADMIN))["agreement"]
    assert agreement["claude-opus-5.5"]["kappa"] == 1.0
    assert agreement["claude-opus-5.5"]["n"] == 10

    # One reviewer's 'no' makes the claim wrong, and the stored summary follows.
    entity = next(item for item in results if item.detail["stratum"] == "entity")
    await lab.service.review_results(run.id, ReviewBatch(
        reviewer="steward", kind="person",
        items=[ReviewItem(item=entity.item, verdict="no", reason="wrong grain")]), ADMIN)
    run = await lab.repository.latest("eval_runs", run.id, type(run))
    assert run.summary["by_stratum"]["entity"]["value"] < 1.0
    assert run.summary["model_only"]["value"] > run.summary["claim_accuracy"]["value"]
    assert run.summary["reviewers"] == ["claude-opus-5.5", "fake-judge", "steward"]


async def test_only_administrators_start_runs_and_only_the_starter_advances(lab):
    with pytest.raises(HTTPException) as denied:
        await lab.service.start_gold(
            GoldRequest(set_name="x", view_ids=["retail_sales"]), ANALYST)
    assert denied.value.status_code in {403, 404}
    run = await lab.service.start_gold(GoldRequest(set_name="x", view_ids=["retail_sales"]), ADMIN)
    with pytest.raises(HTTPException) as other:
        await lab.service.step(run.id, OTHER_ADMIN)
    assert other.value.status_code == 403
    with pytest.raises(HTTPException) as missing:
        await lab.service.start_gold(GoldRequest(set_name="x", view_ids=["nope"]), ADMIN)
    assert missing.value.status_code == 404


async def test_an_external_reviewer_can_reject_a_gold_case(lab):
    await lab.compile()
    await lab.finish(await lab.service.start_gold(
        GoldRequest(set_name="serbadata", view_ids=["outlet_stock"], per_metric=1), ADMIN))
    case = next(c for c in await lab.service.cases("serbadata") if c.status == "ACTIVE")
    outcome = await lab.service.review_cases("serbadata", ReviewBatch(
        reviewer="claude-opus-5.5", kind="external",
        items=[ReviewItem(item=case.id, verdict="no", reason="asks for a broader period")]),
        ADMIN)
    assert outcome == {"reviewed": 1, "rejected": 1}
    again = next(c for c in await lab.service.cases("serbadata") if c.id == case.id)
    assert again.status == "REJECTED" and again.reviews[-1].reviewer == "claude-opus-5.5"


async def test_the_overview_shows_only_what_was_measured(lab):
    await lab.compile()
    empty = await overview(ADMIN, lab.service)
    assert empty["measured"]["answers"] is None and empty["measured"]["claims"] is None
    assert len(empty["not_measured"]) == 4 and empty["measured"]["joins"] is None
    assert empty["live"]["metric_coverage"]["n"] > 0
    await lab.finish(await lab.service.start_claims(ClaimRunRequest(per_stratum=5), ADMIN))
    measured = await overview(ADMIN, lab.service)
    assert measured["measured"]["claims"]["claim_accuracy"]["value"] == pytest.approx(1.0)
    assert len(measured["not_measured"]) == 3


async def test_a_case_that_breaks_is_recorded_not_fatal(lab, monkeypatch):
    await lab.compile()
    await lab.finish(await lab.service.start_gold(
        GoldRequest(set_name="serbadata", view_ids=["outlet_stock"], per_metric=1), ADMIN))
    import app.modules.agents.semantic.model_planner as planner

    async def catalog(_ir, _question):
        return {}

    async def broken(*_args, **_kwargs):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(planner, "planning_catalog", catalog)
    monkeypatch.setattr(planner, "generate_plan", broken)
    run = await lab.finish(await lab.service.start_answers(
        AnswerRunRequest(set_name="serbadata", mode="without_ontology"), ADMIN))
    assert run.status == "COMPLETED"
    assert run.summary["failed"] == run.summary["reliability"]["n"]
    assert run.summary["answer_accuracy"]["successes"] == 0


async def test_a_provider_timeout_is_retried_and_a_failed_run_resumes(lab):
    from app.modules.assistant.provider import AssistantProviderError

    run = await lab.service.start_gold(
        GoldRequest(set_name="flaky", view_ids=["outlet_stock"], per_metric=1), ADMIN)
    run = await lab.service.step(run.id, ADMIN)  # intents for the only view
    original, calls = lab.model.complete, {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AssistantProviderError("AI provider request failed: ReadTimeout")
        return await original(**kwargs)

    lab.model.complete = flaky
    run = await lab.service.step(run.id, ADMIN)  # moves to phrasing
    run = await lab.service.step(run.id, ADMIN)  # the phrasing call times out
    assert run.status == "RUNNING" and "ReadTimeout" in (run.error or "")
    run = await lab.finish(run)
    assert run.status == "COMPLETED"

    failed = run.model_copy(update={"status": "FAILED", "revision": run.revision + 1})
    await lab.repository.save("eval_runs", failed, expected_revision=run.revision)
    resumed = await lab.service.resume(run.id, ADMIN)
    assert resumed.status == "RUNNING" and resumed.error is None
    with pytest.raises(HTTPException):
        await lab.service.resume(run.id, OTHER_ADMIN)


async def test_negatives_wait_when_views_cannot_be_read(lab):
    run = await lab.service.start_gold(
        GoldRequest(set_name="outage", view_ids=["outlet_stock"], per_metric=1), ADMIN)
    for _ in range(20):
        run = await lab.service.step(run.id, ADMIN)
        if run.stage == "negatives":
            break
    loader = lab.service._models_loader

    async def nothing(_user):
        return {}

    lab.service._models_loader = nothing
    with pytest.raises(HTTPException) as unavailable:
        await lab.service.step(run.id, ADMIN)
    assert unavailable.value.status_code == 503
    lab.service._models_loader = loader
    run = await lab.finish(run)
    assert run.status == "COMPLETED"
    assert all(case.view_id for case in await lab.service.cases("outage"))


async def test_a_plan_the_compiler_refuses_is_dropped_not_fatal(lab, monkeypatch):
    from app.modules.agents.semantic import compiler

    original = compiler.SemanticCompiler.compile

    def refuse_breakdowns(self, ir, plan):
        if plan.dimensions:
            raise compiler.SemanticPlanError("Metric is non-additive for the requested dimensions.")
        return original(self, ir, plan)

    monkeypatch.setattr(compiler.SemanticCompiler, "compile", refuse_breakdowns)
    run = await lab.finish(await lab.service.start_gold(
        GoldRequest(set_name="additive", view_ids=["outlet_stock"], per_metric=4), ADMIN))
    assert run.status == "COMPLETED"
    kept = run.summary["intents"]["outlet_stock"]
    assert 0 < kept["kept"] < kept["planned"]
    assert not any(case.plan and case.plan.get("dimensions")
                   for case in await lab.service.cases("additive"))


# --- business vocabulary --------------------------------------------------------------

def _release(*rows):
    """Concepts and validated bindings, shaped like a release snapshot."""
    from app.modules.ontology.contracts import ConceptKind

    concepts, bindings = {}, []
    for index, (kind, label, aliases, metric) in enumerate(rows):
        concepts[f"c{index}"] = SimpleNamespace(
            id=f"c{index}", kind=ConceptKind(kind), label=label, aliases=aliases)
        if metric:
            bindings.append(SimpleNamespace(
                concept_id=f"c{index}", status="VALIDATED", metric=metric,
                semantic=SimpleNamespace(view_id="retail_sales")))
    return concepts, bindings


def test_business_terms_are_only_words_the_view_never_uses():
    _, ir = _model("retail_sales")
    concepts, bindings = _release(
        ("METRIC", "Omzet", ["penjualan bersih", "net_sales_idr", "nilai order"], "net_revenue"),
        ("METRIC", "Gross margin", ["untung kotor"], "gross_margin"),
        ("METRIC", "Shared", ["penjualan neto"], "units_sold"),
        ("METRIC", "Other", ["penjualan neto"], None),
        ("BUSINESS_ENTITY", "Employee", ["sopir", "employee"], None),
        ("BUSINESS_ENTITY", "Outlet", ["toko"], None),
    )
    metrics, entities = gold.business_terms(ir, "retail_sales", concepts, bindings)
    # 'Omzet' and 'nilai order' are view synonyms; identifiers are not business words.
    assert metrics["net_revenue"] == ["penjualan bersih"]
    assert metrics["gross_margin"] == ["untung kotor"]
    # A word two concepts share is not a fair test; the unique label still is.
    assert metrics["units_sold"] == ["Shared"]
    # 'toko' is in the view's own field description.
    assert entities == {"employees": ("employees.employee_name", ["sopir"])}


def test_business_intents_carry_their_words_and_ask_per_entity():
    _, ir = _model("retail_sales")
    concepts, bindings = _release(
        ("METRIC", "Omzet", ["penjualan bersih"], "net_revenue"),
        ("BUSINESS_ENTITY", "Employee", ["sopir"], None),
    )
    terms = gold.business_terms(ir, "retail_sales", concepts, bindings)
    found = gold.business_intents(ir, "retail_sales", "retail_sales", ["net_revenue", "units_sold"],
                                  ("2026-05-01", "2026-11-04"), terms, per_metric=2, seed=3)
    assert {intent.metric for intent in found} == {"net_revenue"}
    assert all("penjualan bersih" in intent.terms for intent in found)
    entity = next(intent for intent in found if intent.template == "by_entity")
    assert entity.plan["dimensions"] == ["employees.employee_name"]
    assert entity.terms == ["penjualan bersih", "sopir"]


async def test_a_question_without_its_business_words_never_enters_the_set(lab, monkeypatch):
    original = lab.model.complete

    async def forgetful(**kwargs):
        response = await original(**kwargs)
        name = (kwargs.get("response_format") or {}).get("json_schema", {}).get("name")
        if name == "evaluation_questions":
            body = json.loads(response["content"])
            for item in body["items"]:
                item["question"] = "Berapa nilainya?"
            response = {"content": json.dumps(body)}
        return response

    lab.model.complete = forgetful

    def terms(*_args):
        return ({"closing_stock": ["sisa barang"]}, {})

    monkeypatch.setattr(gold, "business_terms", terms)
    run = await lab.finish(await lab.service.start_gold(GoldRequest(
        set_name="vocab", view_ids=["outlet_stock"], per_metric=1, vocabulary="business"), ADMIN))
    assert run.status == "COMPLETED"
    answerable = [c for c in await lab.service.cases("vocab") if c.kind == "answerable"]
    assert answerable and all(c.status == "REJECTED" for c in answerable)
    assert all(any(r.reviewer == "nova:vocabulary" for r in c.reviews) for c in answerable)


async def test_without_a_view_hint_the_run_routes_and_reports_view_accuracy(lab, monkeypatch):
    await lab.compile()
    await lab.finish(await lab.service.start_gold(GoldRequest(
        set_name="routing", view_ids=["retail_sales", "outlet_stock"], per_metric=1), ADMIN))
    cases = [c for c in await lab.service.cases("routing") if c.status == "ACTIVE"]
    PLANS.clear()
    PLANS.update({c.question: c.plan for c in cases if c.kind == "answerable"})
    fake_planner(monkeypatch, set())
    run = await lab.finish(await lab.service.start_answers(AnswerRunRequest(
        set_name="routing", mode="without_ontology", view_hint=False), ADMIN))
    assert run.status == "COMPLETED" and run.summary["view_hint"] is False
    results = await lab.service.results(run.id)
    answerable = [r for r in results if r.detail["kind"] == "answerable"]
    assert {r.detail["routed_by"] for r in answerable} <= {"metric name", "model"}
    stock = [r for r in answerable if r.detail["view"] == "outlet_stock"]
    assert stock, "the scripted router sends unnamed questions to outlet_stock"
    view = run.summary["view_accuracy"]
    assert view["n"] == len(answerable)
    assert view["successes"] == sum(r.detail["view_correct"] for r in answerable)
    # A question routed to the wrong view is not answered from the right one.
    assert all(r.outcome != "correct" for r in answerable if not r.detail["view_correct"])
    # When no view computes the question, the router's 'none' is an abstention.
    # When the router finds no view that computes the question, that is an abstention.

    async def nowhere(_question, _models):
        return None, "model"

    monkeypatch.setattr(lab.service, "_route", nowhere)
    run = await lab.finish(await lab.service.start_answers(AnswerRunRequest(
        set_name="routing", mode="without_ontology", view_hint=False), ADMIN))
    unrouted = [r for r in await lab.service.results(run.id) if r.detail["routed_by"] == "model"]
    assert unrouted and all(r.outcome == "abstained" for r in unrouted)
    assert all(r.detail["error"] == "no view answers the question" for r in unrouted)


async def test_a_period_outside_the_data_is_scored_as_an_abstention(lab, monkeypatch):
    from datetime import date

    from app.modules.agents.semantic.coverage import CoverageGap, DataWindow
    from app.modules.ontology.evaluation import service as evaluation

    await lab.compile()
    await lab.finish(await lab.service.start_gold(GoldRequest(
        set_name="window", view_ids=["retail_sales"], per_metric=1), ADMIN))
    cases = [c for c in await lab.service.cases("window") if c.status == "ACTIVE"]
    PLANS.clear()
    PLANS.update({c.question: c.plan for c in cases if c.kind == "answerable"})
    fake_planner(monkeypatch, set())
    window = DataWindow(first=date(2026, 5, 1), last=date(2026, 11, 4))

    async def outside(*_args):
        return CoverageGap(window=window, period="requested",
                           start=date(2026, 12, 1), end=date(2026, 12, 31))

    monkeypatch.setattr(evaluation, "period_without_data", outside)
    run = await lab.finish(await lab.service.start_answers(AnswerRunRequest(
        set_name="window", mode="without_ontology"), ADMIN))
    answered = [r for r in await lab.service.results(run.id)
                if r.detail.get("data_window")]
    assert answered and all(r.outcome == "abstained" for r in answered)
    assert answered[0].detail["data_window"] == ["2026-05-01", "2026-11-04"]
