"""Measured failures become steward proposals: misleading names, missing words, missing metrics."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.modules.ontology.contracts import Actor, ConceptChange
from app.modules.ontology.evaluation.contracts import EvalCase, EvalResult, EvalRun, Review
from app.modules.ontology.learning import LearnRequest, _metric_proposal, _metric_words, learn
from tests.unit._ontology import ADMIN
from tests.unit.test_ontology_grounding import bind


@pytest.fixture
async def world(monkeypatch):
    """The grounding test world: domains, a published sales view and the resolver."""
    from app.modules.ontology import grounding as grounding_module
    from app.modules.ontology.resolver import ConceptResolver
    from tests.unit.test_business_ontology import World
    from tests.unit.test_ontology_grounding import sales_definition

    built = World(monkeypatch)
    await built.domains()
    built.catalog.publish("sales", sales_definition())
    built.resolver = ConceptResolver(built.reads)
    monkeypatch.setattr(grounding_module.concept_resolver, "resolve", built.resolver.resolve)
    return built


class Evidence:
    """The evaluation records a learning pass reads."""

    def __init__(self, repository, runs, results, cases, models=None, value=12.5):
        self.repository = repository
        self._runs, self._results, self._cases = runs, results, cases
        self._model_map, self.value = models or {}, value
        self.executed: list[str] = []

    async def runs(self):
        return self._runs

    async def results(self, run_id):
        return self._results.get(run_id, [])

    async def cases(self, set_name, version=None):
        return self._cases

    async def _models(self, _user):
        return self._model_map

    async def _execute(self, sql, model, user):
        self.executed.append(sql)
        if "no_such_column" in sql:
            return [], [], "query error"  # the engine refuses an unknown column
        return ["value"], [[self.value]], None

    def client(self):
        raise AssertionError("no model call expected")


def run(kind, run_id, **extra):
    return EvalRun(id=run_id, actor=Actor.from_user(ADMIN), kind=kind, set_name="s", version=1,
                   status="COMPLETED", **extra)


def case(case_id, question, *, kind="answerable", terms=(), plan=None, intent="total",
         meaning="m"):
    return EvalCase(id=case_id, actor=Actor.from_user(ADMIN), set_name="s", version=1,
                    kind=kind, intent=intent, language="id", question=question, view_id="sales",
                    plan=plan, meaning=meaning, expected="answer", terms=list(terms),
                    status="ACTIVE")


async def test_an_alias_the_audit_rejected_is_proposed_for_removal(world):
    concept = await bind(world, world.finance, "ReturnedUnits", aliases=["retur", "unit retur"])
    rejected = EvalResult(
        id="r1", actor=Actor.from_user(ADMIN), run_id="claims", item=f"alias:{concept}:retur",
        outcome="yes", detail={"stratum": "alias"},
        reviews=[Review(reviewer="judge", kind="model", verdict="no", reason="too generic"),
                 Review(reviewer="claude", kind="external", verdict="no",
                        reason="'retur' also names returned value")])
    evidence = Evidence(world.repository, [run("claims", "claims")], {"claims": [rejected]}, [])
    report = await learn(LearnRequest(operation_id="op-learn-1", detectors=["aliases"]), ADMIN,
                         evidence, world.stewardship)
    assert len(report["proposals"]) == 1
    proposal = (await world.stewardship.list(ADMIN, None))[0]
    assert proposal.source == "ENGINE" and "also names returned value" in proposal.rationale
    change = proposal.changes[0]
    assert isinstance(change, ConceptChange) and change.body.aliases == ["unit retur"]


async def test_a_name_one_judge_defends_is_left_to_the_stewards(world):
    concept = await bind(world, world.finance, "OrderLines", aliases=["baris pesanan"])
    disputed = EvalResult(
        id="r1", actor=Actor.from_user(ADMIN), run_id="claims",
        item=f"alias:{concept}:baris pesanan", outcome="no", detail={"stratum": "alias"},
        reviews=[Review(reviewer="judge", kind="model", verdict="no", reason="generic"),
                 Review(reviewer="claude", kind="external", verdict="yes", reason="fine")])
    evidence = Evidence(world.repository, [run("claims", "claims")], {"claims": [disputed]}, [])
    report = await learn(LearnRequest(operation_id="op-learn-3", detectors=["aliases"]), ADMIN,
                         evidence, world.stewardship)
    assert report["proposals"] == []


async def test_words_a_failed_question_used_are_proposed_for_its_metric(world):
    concept = await bind(world, world.finance, "TotalRevenue", label="Total revenue")
    failed = case("c1", "Berapa sisa duit masuk bulan lalu?", terms=["sisa duit masuk"],
                  plan={"metrics": ["total_revenue"]},
                  meaning="Total revenue || measure term: 'sisa duit masuk'")
    evidence = Evidence(
        world.repository, [run("answers", "a1", mode="with_ontology")],
        {"a1": [EvalResult(id="x", actor=Actor.from_user(ADMIN), run_id="a1", item="c1",
                           outcome="wrong", detail={"plan": {}})]}, [failed])
    report = await learn(LearnRequest(operation_id="op-learn-2", detectors=["vocabulary"]),
                         ADMIN, evidence, world.stewardship)
    assert len(report["proposals"]) == 1
    proposal = (await world.stewardship.list(ADMIN, None))[0]
    change = proposal.changes[0]
    assert change.concept_id == concept and "sisa duit masuk" in change.body.aliases


def test_only_the_recorded_measure_term_is_learned():
    item = case("c", "What was the item order count per staff?", terms=["item order", "staff"],
                meaning="Order lines || measure term: 'item order' || breakdown term: 'staff'")
    result = SimpleNamespace(detail={"plan": {"unresolved_concepts": [
        {"text": "per staff"}]}})
    # A grouping the planner could not resolve is not the name of a measure.
    assert _metric_words(item, result) == ["item order"]


async def test_a_metric_formula_is_proposed_only_once_it_compiles_and_computes():
    from app.modules.agents.semantic.runtime import semantic_ir_to_definition
    from tests.unit.test_semantic_intelligence import sales_model

    definition = semantic_ir_to_definition(sales_model())
    model = {"name": "sales", "definition": json.loads(json.dumps(definition))}
    ir = sales_model()
    evidence = Evidence(None, [], {}, [])
    refused = case("c", "Berapa rata-rata nilai per order?", kind="unanswerable")
    proven = await _metric_proposal(refused, {
        "derivable": True, "name": "average_order_value", "label": "Average order value",
        "definition": "Revenue per order.", "dataset": "orders",
        "expression": "SUM(orders.amount) / NULLIF(COUNT(orders.order_id), 0)",
    }, model, ir, set(), ADMIN, evidence)
    assert proven[0] == "average_order_value" and proven[-1] == "12.5"
    assert evidence.executed and "average_order_value" in evidence.executed[0]
    # Not derivable, an unknown dataset or a broken formula never becomes a proposal.
    assert (await _metric_proposal(refused, {"derivable": False, "reason": "no cost data"},
                                   model, ir, set(), ADMIN, evidence)).startswith("not derivable")
    assert "dataset" in await _metric_proposal(refused, {
        "derivable": True, "name": "x_value", "dataset": "nowhere", "expression": "SUM(1)"},
        model, ir, set(), ADMIN, evidence)
    assert "does not compute" in await _metric_proposal(refused, {
        "derivable": True, "name": "broken_value", "dataset": "orders",
        "expression": "SUM(orders.no_such_column)"}, model, ir, set(), ADMIN, evidence)
    empty = Evidence(None, [], {}, [], value=None)
    assert "does not compute" in await _metric_proposal(refused, {
        "derivable": True, "name": "average_order_value", "dataset": "orders",
        "expression": "SUM(orders.amount) / NULLIF(COUNT(orders.order_id), 0)"},
        model, ir, set(), ADMIN, empty)


async def test_an_answer_of_false_counts_and_a_skipped_item_is_asked_again():
    from app.modules.ontology.evaluation.judging import TEXT, ask_items, items_schema

    calls = []

    class Model:
        async def resolve(self, **_):
            return SimpleNamespace(model="m", capabilities=SimpleNamespace(
                supports_json_schema=True))

        async def complete(self, *, messages, **_):
            items = json.loads(messages[1]["content"])["items"]
            calls.append([item["id"] for item in items])
            # The first reply forgets item b; the answer for a is a plain False.
            answers = [{"id": item["id"], "reason": "r", "derivable": False}
                       for item in items if len(calls) > 1 or item["id"] == "a"]
            return {"content": json.dumps({"items": answers})}

    found, _model = await ask_items(
        Model(), "system", {}, [{"id": "a"}, {"id": "b"}],
        items_schema({"id": TEXT, "reason": TEXT, "derivable": {"type": "boolean"}}), "x",
        answer_key="derivable")
    assert set(found) == {"a", "b"} and found["a"]["derivable"] is False
    assert calls == [["a", "b"], ["b"]]


async def test_a_refused_question_becomes_a_proven_metric_proposal(world):
    from app.modules.agents.semantic.runtime import semantic_ir_to_definition
    from tests.unit.test_semantic_intelligence import sales_model

    await bind(world, world.finance, "TotalRevenue", label="Total revenue")
    refused = case("c9", "Berapa nilai rata-rata per order?", kind="unanswerable",
                   intent="missing_measure", meaning="Not answerable: no average order value")
    definition = json.loads(json.dumps(semantic_ir_to_definition(sales_model())))

    class Model:
        async def resolve(self, **_):
            return SimpleNamespace(model="m", capabilities=SimpleNamespace(
                supports_json_schema=True))

        async def complete(self, *, messages, **_):
            items = json.loads(messages[1]["content"])["items"]
            return {"content": json.dumps({"items": [{
                "id": item["id"], "reason": "revenue over orders", "derivable": True,
                "name": "average_order_value", "label": "Average order value",
                "definition": "Revenue per order.", "dataset": "orders",
                "expression": "SUM(orders.amount) / NULLIF(COUNT(orders.order_id), 0)",
            } for item in items]})}

    evidence = Evidence(world.repository, [run("answers", "a1", mode="with_ontology")], {},
                        [refused], models={"sales": {"name": "sales",
                                                     "definition": definition}})
    report = await learn(LearnRequest(operation_id="op-learn-9", detectors=["metrics"]), ADMIN,
                         evidence, world.stewardship, client=Model())
    assert len(report["proposals"]) == 1
    proposal = report["proposals"][0]
    assert proposal["question"] == "Berapa nilai rata-rata per order?"
    assert "NULLIF" in proposal["expression"] and evidence.executed
    created = next(item for item in await world.stewardship.list(ADMIN, None)
                   if item.id == proposal["id"])
    assert created.changes[0].op == "create" and created.changes[0].body.kind == "METRIC"


# --- words people used in Studio ---------------------------------------------------------

def liked(user_name, question, metrics=("total_revenue",), view="sales"):
    return {"message_id": f"m-{user_name}-{len(question)}", "user_name": user_name, "steps": [{
        "kind": "tool", "name": "semantic_query", "status": "done",
        "trace_detail": {"question": question, "semantic_view": {"id": view, "version": 1},
                         "semantic_plan": {"metrics": list(metrics)}}}]}


class PhraseModel:
    """Names, for each question, the words the test says name its measure."""

    def __init__(self, phrases):
        self.phrases, self.questions = phrases, []

    async def resolve(self, **_):
        return SimpleNamespace(model="m", capabilities=SimpleNamespace(supports_json_schema=True))

    async def complete(self, *, messages, **_):
        items = json.loads(messages[1]["content"])["items"]
        self.questions += [item["question"] for item in items]
        return {"content": json.dumps({"items": [
            {"id": item["id"], "phrase": self.phrases.get(item["question"], "")}
            for item in items]})}


async def conversations(world, monkeypatch, answers, phrases):
    from app.modules.assistant.repository import assistant_repository

    async def liked_answers(**_):
        return answers

    monkeypatch.setattr(assistant_repository, "liked_answers", liked_answers)
    model = PhraseModel(phrases)
    evidence = Evidence(world.repository, [], {}, [])
    report = await learn(LearnRequest(operation_id="op-conv-1", detectors=["conversations"]),
                         ADMIN, evidence, world.stewardship, client=model)
    return report, model


async def test_a_phrase_two_people_used_in_liked_answers_is_proposed(world, monkeypatch):
    concept = await bind(world, world.finance, "TotalRevenue", label="Total revenue")
    answers = [liked("ani", "Berapa omzet kotor per kota bulan lalu?"),
               liked("budi", "omzet kotor Oktober 2026"),
               liked("budi", "Tolong omzet kotor per brand")]
    report, _model = await conversations(world, monkeypatch, answers, {
        "Berapa omzet kotor per kota bulan lalu?": "omzet kotor",
        "omzet kotor Oktober 2026": "omzet kotor",
        "Tolong omzet kotor per brand": "omzet kotor"})
    assert len(report["proposals"]) == 1
    proposal = (await world.stewardship.list(ADMIN, None))[0]
    change = proposal.changes[0]
    assert proposal.source == "ENGINE" and change.concept_id == concept
    assert "omzet kotor" in change.body.aliases
    assert "2 people, 3 liked answers" in proposal.rationale
    # The proposal carries the phrase and counts, never a question or a person.
    assert "kota bulan lalu" not in proposal.rationale and "ani" not in proposal.rationale


async def test_one_persons_phrase_or_an_invented_one_is_not_proposed(world, monkeypatch):
    await bind(world, world.finance, "TotalRevenue", label="Total revenue")
    answers = [liked("ani", "Berapa duit masuk per kota?"),
               liked("ani", "duit masuk Oktober"),
               liked("budi", "Berapa pendapatan kotor Oktober?")]
    report, _model = await conversations(world, monkeypatch, answers, {
        "Berapa duit masuk per kota?": "duit masuk", "duit masuk Oktober": "duit masuk",
        # Words the question does not contain are never learned.
        "Berapa pendapatan kotor Oktober?": "omzet kotor"})
    assert report["proposals"] == []
    assert {"concept": "Total revenue", "value": "duit masuk",
            "reason": "Used by 1 person; needs 2"} in report["skipped"]


async def test_known_names_and_several_measures_teach_nothing_and_cost_no_model_call(
        world, monkeypatch):
    await bind(world, world.finance, "TotalRevenue", label="Total revenue",
               aliases=["omzet"])
    answers = [liked("ani", "Berapa omzet per kota?"),  # already an official name
               liked("budi", "pendapatan dan diskon per kota",
                     metrics=("total_revenue", "discount_amount")),  # two measures
               liked("budi", "duit masuk per kota", view="other")]  # an unbound view
    report, model = await conversations(world, monkeypatch, answers, {})
    assert report["proposals"] == [] and model.questions == []
