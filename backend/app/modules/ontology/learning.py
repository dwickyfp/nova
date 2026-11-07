"""Learn from measured failures: steward proposals from audits and failed questions.

Three detectors turn evidence the evaluation already holds into ``ENGINE``
proposals. Nothing changes until a domain steward approves and a release is
published.

* **Misleading names.** An official alias the latest claim audit rejected by
  consensus (one judge's "no" is enough) is proposed for removal, with the
  judges' reasons. A generic word like 'retur' attached to returned units while
  returned value exists is the typical case.
* **Words people used.** A gold question that failed while asking, in business
  words, for a metric bound to an official concept, proposes those words as
  that concept's aliases. The enrichment validator keeps a word only when no
  other concept uses it and it is not generic.
* **Metrics a refusal asked for.** A question refused because no measure
  computes it is given, with the view's measures, to a language model that
  proposes a formula over them or says it cannot be derived. A formula becomes
  a proposal only after it is added to a copy of the view, validated, compiled
  and executed once as the caller with a non-empty result. The proposal is a
  new metric concept carrying the formula for the view owner.
"""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from typing import Any

from pydantic import Field

from app.modules.intelligence.contracts import Contract, fingerprint
from app.modules.ontology import access
from app.modules.ontology.contracts import (
    Concept,
    ConceptBody,
    ConceptChange,
    ConceptKind,
    Proposal,
    Representation,
)
from app.modules.ontology.evaluation.contracts import EvalCase, EvalResult, EvalRun
from app.modules.ontology.resolver import names_of, normalise

#: Refused questions sent to the model in one call.
METRIC_BATCH = 12
_SNAKE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


class LearnRequest(Contract):
    operation_id: str = Field(min_length=8, max_length=64)
    claims_run_id: str | None = Field(default=None, max_length=64)
    answer_run_ids: list[str] = Field(default_factory=list, max_length=8)
    #: Detectors to run; all by default.
    detectors: list[str] = Field(
        default_factory=lambda: ["aliases", "vocabulary", "metrics"], max_length=3)


async def learn(body: LearnRequest, user: dict, evaluation, stewardship, client=None) -> dict:
    await access.require_admin("LEARN", "ONTOLOGY_PROPOSAL", body.operation_id, user)
    runs = [run for run in await evaluation.runs() if run.status == "COMPLETED"]
    concepts = {item.id: item for item in await evaluation.repository.current(
        "concepts", Concept) if item.status == "APPROVED"}
    busy = await _busy(evaluation.repository)
    report: dict[str, Any] = {"proposals": [], "skipped": []}
    if "aliases" in body.detectors:
        claims = next((run for run in runs if run.kind == "claims"
                       and (body.claims_run_id in (None, run.id))), None)
        if claims is not None:
            await _misleading_aliases(claims, concepts, busy, body, user, evaluation,
                                      stewardship, report)
    answer_runs = [run for run in runs if run.kind == "answers"
                   and (run.id in body.answer_run_ids if body.answer_run_ids
                        else run.mode == "with_ontology")][:8]
    if "vocabulary" in body.detectors and answer_runs:
        await _vocabulary(answer_runs, concepts, busy, body, user, evaluation, stewardship,
                          report)
    if "metrics" in body.detectors and answer_runs:
        await _missing_metrics(answer_runs, concepts, body, user, evaluation, stewardship,
                               report, client)
    await access.audit("LEARN", "ONTOLOGY_PROPOSAL",
                       ",".join(item["id"] for item in report["proposals"])[:128] or "none", user)
    return report


async def _busy(repository) -> set[str]:
    """Concepts an open proposal already changes; a second one would conflict."""
    busy = set()
    for proposal in await repository.current("proposals", Proposal):
        if proposal.status in {"PENDING", "APPROVED"}:
            busy |= {change.concept_id for change in proposal.changes
                     if isinstance(change, ConceptChange) and change.op != "create"}
    return busy


async def _submit(stewardship, user, title: str, rationale: str, changes: list, key: str):
    from app.modules.ontology.stewardship import ProposalCreate

    proposal = await stewardship.create(
        ProposalCreate(title=title[:200], rationale=rationale[:4000], changes=changes,
                       operation_id="lrn-" + fingerprint([key, [
                           change.model_dump(mode="json") for change in changes]])[:40]),
        user, source="ENGINE")
    return {"id": proposal.id, "title": proposal.title, "changes": len(changes)}


# --- misleading aliases -------------------------------------------------------------------

async def _misleading_aliases(claims: EvalRun, concepts, busy, body, user, evaluation,
                              stewardship, report) -> None:
    by_concept: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for result in await evaluation.results(claims.id):
        # Removing an official name is consequential: every judge who reviewed it, and at
        # least two, must have rejected it. One dissent leaves the name to the stewards.
        verdicts = [review.verdict for review in result.reviews]
        if (not result.item.startswith("alias:") or len(verdicts) < 2
                or any(verdict != "no" for verdict in verdicts)):
            continue
        _, concept_id, alias = result.item.split(":", 2)
        reasons = [f"{review.reviewer}: {review.reason}" for review in result.reviews
                   if review.verdict == "no"]
        by_concept[concept_id].append((alias, reasons))
    by_domain: dict[str, list[tuple[Concept, list[tuple[str, list[str]]]]]] = defaultdict(list)
    for concept_id, items in by_concept.items():
        concept = concepts.get(concept_id)
        if concept is None:
            report["skipped"].append({"concept": concept_id, "reason": "no longer approved"})
            continue
        if concept_id in busy:
            report["skipped"].append({"concept": concept.label,
                                      "reason": "an open proposal already changes it"})
            continue
        present = [(alias, why) for alias, why in items if alias in concept.aliases]
        if present:
            by_domain[concept.domain_id].append((concept, present))
    for domain_id, entries in sorted(by_domain.items()):
        changes, lines = [], []
        for concept, items in entries:
            body_now = concept.model_dump(include=set(ConceptBody.model_fields))
            removed = {alias for alias, _ in items}
            body_now["aliases"] = [alias for alias in concept.aliases if alias not in removed]
            changes.append(ConceptChange(op="update", concept_id=concept.id,
                                         base_revision=concept.revision,
                                         body=ConceptBody(**body_now)))
            for alias, why in items:
                lines.append(f"- {concept.label}: remove '{alias}' ({'; '.join(why)[:300]})")
        report["proposals"].append(await _submit(
            stewardship, user, "Remove names the claim audit found misleading",
            f"The claim audit {claims.id} rejected these official names by consensus: Studio "
            "answers anyone who says them with this concept, so a misleading name gives a "
            "confident wrong answer. Removing it lets Studio ask instead.\n" + "\n".join(lines),
            changes, f"aliases:{domain_id}:{claims.id}:{body.operation_id}"))


# --- words people used -------------------------------------------------------------------

async def _vocabulary(answer_runs, concepts, busy, body, user, evaluation, stewardship,
                      report) -> None:
    from app.modules.ontology.contracts import Domain
    from app.modules.ontology.enrichment import validate

    bindings = [item for item in await evaluation.repository.current(
        "representations", Representation) if item.status == "VALIDATED"]
    bound = {(item.semantic.view_id, item.metric): item.concept_id for item in bindings}
    domains = {item.id: item for item in await evaluation.repository.current("domains", Domain)}
    names = {cid: access.qualified_path(domains[c.domain_id], domains) + "." + c.slug
             for cid, c in concepts.items() if c.domain_id in domains}
    taken: dict[str, set[str]] = {}
    for cid, concept in concepts.items():
        if cid in names:
            for name in names_of(concept, names[cid]):
                taken.setdefault(name, set()).add(cid)
    wanted: dict[str, set[str]] = defaultdict(set)
    for run in answer_runs:
        cases = {case.id: case for case in await evaluation.cases(run.set_name, run.version)}
        for result in await evaluation.results(run.id):
            case = cases.get(result.item)
            if case is None or case.kind != "answerable" or result.outcome == "correct":
                continue
            for metric in (case.plan or {}).get("metrics") or []:
                concept_id = bound.get((case.view_id, metric))
                if concept_id and concept_id not in busy:
                    for term in _metric_words(case, result):
                        wanted[concept_id].add(term)
    if not wanted:
        return
    raw = {"concepts": [{"name": names[cid], "aliases": sorted(terms)}
                        for cid, terms in wanted.items() if cid in names]}
    sent = {names[cid]: concepts[cid] for cid in wanted if cid in names}
    accepted, rejected = validate(raw, sent, names, taken)
    report["skipped"] += [{"concept": item.concept, "value": item.value, "reason": item.reason}
                          for item in rejected]
    by_domain: dict[str, list[ConceptChange]] = defaultdict(list)
    lines: dict[str, list[str]] = defaultdict(list)
    for concept_id, suggestion in accepted.items():
        concept = concepts[concept_id]
        fresh = [alias for alias, _ in suggestion.aliases
                 if normalise(alias) not in {normalise(a) for a in concept.aliases}]
        if not fresh:
            continue
        body_now = concept.model_dump(include=set(ConceptBody.model_fields))
        body_now["aliases"] = [*concept.aliases, *fresh][:32]
        by_domain[concept.domain_id].append(ConceptChange(
            op="update", concept_id=concept.id, base_revision=concept.revision,
            body=ConceptBody(**body_now)))
        lines[concept.domain_id].append(
            f"- {concept.label}: + {', '.join(repr(alias) for alias in fresh)}")
    for domain_id, changes in sorted(by_domain.items()):
        report["proposals"].append(await _submit(
            stewardship, user, "Words people used for official metrics",
            "Gold questions asked for these metrics in these words and were not answered "
            "correctly. Each word is used by no other concept and is not generic.\n"
            + "\n".join(lines[domain_id]),
            changes, f"vocabulary:{domain_id}:{body.operation_id}"))


_MEASURE_TERM = re.compile(r"measure term: '([^']+)'")


def _metric_words(case: EvalCase, result: EvalResult) -> list[str]:
    """The business words a failed question used for its measure.

    Only the gold case's recorded measure term counts. A planner's unresolved text is
    often a grouping or a filter ('per route', 'status outlet'), never a measure's name.
    """
    words = _MEASURE_TERM.findall(case.meaning)
    return [word for word in dict.fromkeys(words)
            if normalise(word) and normalise(word) in normalise(case.question)][:4]


# --- metrics a refusal asked for --------------------------------------------------------

async def _missing_metrics(answer_runs, concepts, body, user, evaluation, stewardship, report,
                           client) -> None:
    from app.modules.agents.semantic.ir import SemanticModelIR
    from app.modules.ontology.evaluation.judging import TEXT, ask_items, items_schema

    models = await evaluation._models(user)
    refused: dict[str, EvalCase] = {}
    for run in answer_runs:
        for case in await evaluation.cases(run.set_name, run.version):
            if (case.kind == "unanswerable" and case.intent == "missing_measure"
                    and case.status == "ACTIVE" and case.view_id in models
                    and "window" not in case.meaning and "jendela" not in case.meaning):
                refused.setdefault(normalise(case.question), case)
    by_view: dict[str, list[EvalCase]] = defaultdict(list)
    for case in refused.values():
        by_view[case.view_id].append(case)
    client = client or evaluation.client()
    existing = {normalise(c.slug) for c in concepts.values()}
    for view_id, cases in sorted(by_view.items()):
        model = models[view_id]
        ir = SemanticModelIR.from_ossie(model["definition"])
        catalog = {
            "metrics": [{"name": m.name, "description": m.description[:160],
                         "expression": m.expression[:200], "dataset": m.base_dataset}
                        for m in ir.metrics if m.visibility == "public"],
            "fields": [f"{d.name}.{f.name} ({f.datatype or f.kind.value})"
                       for d in ir.datasets for f in d.fields][:120],
            "relationships": [f"{r.from_dataset} -> {r.to_dataset}" for r in ir.relationships],
        }
        for start in range(0, len(cases), METRIC_BATCH):
            batch = cases[start:start + METRIC_BATCH]
            found, _model = await ask_items(
                client,
                "A business question was refused because no measure computes it. Decide "
                "whether a correct measure for it can be built from this Semantic View's "
                "existing columns and measures, exactly and without assumptions (no "
                "invented costs, targets, factors or status values). The formula computes "
                "the measure only: a breakdown the question asks for (per city, per outlet) "
                "is applied by the query through the listed relationships, so it is not a "
                "reason to refuse. Existing measures' expressions are building blocks (a "
                "ratio of two of them is a measure). If it can, give a snake_case name, a "
                "label, one-sentence definition, the base dataset and one aggregate SQL "
                "expression over dataset.column references (SUM, COUNT, AVG, MIN, MAX, "
                "NULLIF and arithmetic). If it cannot, set derivable false and say what is "
                "missing. Write the reason first.",
                {"catalog": catalog},
                [{"id": case.id, "question": case.question} for case in batch],
                items_schema({"id": TEXT, "reason": TEXT, "derivable": {"type": "boolean"},
                              "name": TEXT, "label": TEXT, "definition": TEXT,
                              "dataset": TEXT, "expression": TEXT}),
                "missing_metric", answer_key="derivable",
            )
            for case in batch:
                item = found.get(case.id) or {}
                outcome = await _metric_proposal(case, item, model, ir, existing, user,
                                                 evaluation)
                if isinstance(outcome, str):
                    report["skipped"].append({"question": case.question, "reason": outcome})
                    continue
                domain_id = _domain_for(view_id, concepts, await evaluation.repository.current(
                    "representations", Representation))
                if domain_id is None:
                    report["skipped"].append({"question": case.question,
                                              "reason": "no domain owns this view's metrics"})
                    continue
                name, label, definition, expression, value = outcome
                change = ConceptChange(op="create", body=ConceptBody(
                    domain_id=domain_id, kind=ConceptKind.METRIC,
                    slug="".join(part.title() for part in name.split("_"))[:64],
                    label=label[:200], definition=definition[:8000]))
                existing.add(normalise(change.body.slug))  # type: ignore[union-attr]
                report["proposals"].append({**await _submit(
                    stewardship, user, f"New metric from a refused question: {label}",
                    f"Studio refused \"{case.question}\" because no measure computes it. "
                    f"This metric can be built from {model.get('name')} as {expression} "
                    f"(validated in a copy of the view, compiled and executed: {value}). "
                    "Approve the concept, then ask the view owner to add the metric with "
                    "this formula and bind it.", [change],
                    f"metric:{case.id}:{body.operation_id}"),
                    "question": case.question, "expression": expression})


async def _metric_proposal(case, item, model, ir, existing, user, evaluation):
    """``(name, label, definition, expression, value)`` once proven, or why not."""
    from app.modules.agents.semantic.compiler import SemanticCompiler
    from app.modules.agents.semantic.ir import SemanticModelIR
    from app.modules.agents.semantic.ossie import SUPPORTED_VERSIONS
    from app.modules.agents.semantic.planning import SemanticPlan
    from app.modules.agents.semantic.serialize import to_ossie_document
    from app.modules.intelligence.autopilot import SemanticChange, apply_changes

    if not item or not item.get("derivable"):
        return "not derivable: " + str(item.get("reason") or "no answer")[:200]
    name, expression = str(item.get("name") or ""), str(item.get("expression") or "")
    dataset = str(item.get("dataset") or "")
    if not _SNAKE.match(name) or ir.metric(name) is not None or normalise(
            "".join(part.title() for part in name.split("_"))) in existing:
        return f"name '{name}' is invalid or already used"
    if ir.dataset(dataset) is None or not expression or ";" in expression:
        return "the formula names no dataset of the view"
    time = next((m.default_time_dimension for m in ir.metrics
                 if m.base_dataset == dataset and m.default_time_dimension), None)
    # A ratio is not additive, so the compiler groups it only by listed dimensions: take
    # those of the dataset's widest non-additive metric, which share its grain.
    allowed = max(
        ([list(m.allowed_dimensions) for m in ir.metrics
          if m.base_dataset == dataset and m.additivity == "non_additive"]),
        key=len, default=[])
    change = SemanticChange(kind="metric", name=name, definition={
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": expression}]},
        "description": str(item.get("definition") or "")[:300], "datatype": "Decimal",
        "base_dataset": dataset, "additivity": "non_additive", "visibility": "public",
        "allowed_dimensions": allowed,
        **({"default_time_dimension": time} if time else {}),
    })
    try:
        document = to_ossie_document(copy.deepcopy(model["definition"]))
        document["version"] = SUPPORTED_VERSIONS[0]
        candidate = apply_changes(document, [change])
        candidate_ir = SemanticModelIR.from_ossie(candidate)
        sql = SemanticCompiler().compile(candidate_ir, SemanticPlan(metrics=(name,))).sql
        # A measure people ask for per something must also compile per something.
        grouped = next((field for field in allowed if candidate_ir.field(field)), None)
        if grouped:
            SemanticCompiler().compile(candidate_ir, SemanticPlan(metrics=(name,),
                                                                  dimensions=(grouped,)))
    except Exception as exc:  # noqa: BLE001 - any refusal means the formula is not proven
        return f"the formula does not validate: {str(getattr(exc, 'detail', exc))[:200]}"
    _columns, rows, error = await evaluation._execute(sql, model, user)
    if error or not rows or rows[0][0] is None:
        return f"the formula does not compute a value ({error or 'empty'})"
    return (name, str(item.get("label") or name.replace("_", " ")),
            str(item.get("definition") or "").strip() or f"{name}.", expression,
            str(rows[0][0])[:40])


def _domain_for(view_id: str, concepts: dict[str, Concept], bindings) -> str | None:
    """The domain owning most of this view's bound metrics."""
    counts: dict[str, int] = defaultdict(int)
    for item in bindings:
        if item.semantic.view_id == view_id and item.concept_id in concepts:
            counts[concepts[item.concept_id].domain_id] += 1
    return max(counts, key=lambda key: counts[key]) if counts else None
