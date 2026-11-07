"""Evaluation runs: build a gold set, answer it, audit claims, summarise.

Every run belongs to the administrator who started it and advances only in
that person's live session, one small step at a time. Each step stores what
it finished, so a run survives restarts and never needs one long request.
Queries run as the caller through the normal governed path; only plans,
outcomes and result signatures are stored, never data rows.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import Counter
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import Field

from app.common.statistics import calibration, cohen_kappa, mcnemar, proportion, stratified
from app.modules.agents.semantic.coverage import period_without_data
from app.modules.assistant.provider import AssistantProviderError
from app.modules.intelligence.contracts import Contract, fingerprint
from app.modules.ontology import access
from app.modules.ontology.contracts import Actor, Alias, Concept, Domain, Release
from app.modules.ontology.evaluation import gold as gold_cases
from app.modules.ontology.evaluation.contracts import (
    EvalCase,
    EvalResult,
    EvalRun,
    Mode,
    Review,
    Verdict,
    case_id,
    result_id,
)
from app.modules.ontology.evaluation.judging import (
    TEXT,
    VERDICT,
    ask,
    ask_items,
    items_schema,
    same_result,
)

STEP_BATCH = 6
JUDGE_BATCH = 15
CLAIMS_PER_STRATUM = 25
PARALLEL = 2
MAX_ROWS = 2000
ANSWERABLE_PER_METRIC = 4
UNANSWERABLE_SHARE = 0.15
AMBIGUOUS_SHARE = 0.10


class GoldRequest(Contract):
    set_name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    view_ids: list[str] = Field(min_length=1, max_length=20)
    per_metric: int = Field(default=ANSWERABLE_PER_METRIC, ge=1, le=5)
    seed: int = 20261108
    #: ``view`` phrases questions with the view's own words; ``business`` with the
    #: released ontology's words the view never uses, which only grounding can map.
    vocabulary: Literal["view", "business"] = "view"


class AnswerRunRequest(Contract):
    set_name: str = Field(min_length=1, max_length=64)
    version: int | None = Field(default=None, ge=1)
    mode: Mode
    release_id: str | None = Field(default=None, max_length=64)
    #: False hides the case's view: the run must choose among every published view,
    #: as Studio does for an agent with several views.
    view_hint: bool = True


class JudgeRunRequest(Contract):
    """Judge an existing gold set again with the current rubric; case status is unchanged."""

    set_name: str = Field(min_length=1, max_length=64)
    version: int | None = Field(default=None, ge=1)


class ClaimRunRequest(Contract):
    set_name: str = Field(default="claims", min_length=1, max_length=64)
    per_stratum: int = Field(default=CLAIMS_PER_STRATUM, ge=5, le=100)
    seed: int = 20261108


class ReviewItem(Contract):
    item: str = Field(min_length=1, max_length=300)
    verdict: str = Field(pattern=r"^(yes|no|unsure)$")
    reason: str = Field(default="", max_length=1000)


class ReviewBatch(Contract):
    reviewer: str = Field(min_length=1, max_length=128)
    kind: str = Field(pattern=r"^(external|person)$")
    items: list[ReviewItem] = Field(min_length=1, max_length=500)


def _verdict(value: Any) -> Verdict:
    if value == "yes":
        return "yes"
    if value == "no":
        return "no"
    return "unsure"


#: Upper bound of results one run stores (a join run keeps one per column pair).
RESULTS_PER_RUN = 20000

#: Version of the judge instructions; every model verdict names the rubric it used.
RUBRIC = "rubric-4"

GOLD_RUBRIC = (
    "You are a strict reviewer of an evaluation set for a business analytics assistant. "
    "For each item answer 'yes' only when the item is valid, by these rules. "
    "'answerable': a correct answer to the question is exactly the stated meaning (same "
    "measure, breakdown, period, filter value and top-N; nothing broader, narrower or extra). "
    "Its meaning has already been executed on the data, so it is computable even when a "
    "breakdown is not listed: judge only whether the question asks exactly that meaning. "
    "Before answering, check the catalog for a measure with the same name in another view: "
    "when two views both have it and the question names neither view nor a breakdown only "
    "one has, an answerable item is 'no', even when the question quotes the measure's "
    "description. "
    "A word that is a measure's name or specific synonym names that measure ('jumlah order' "
    "is the order count, not order lines); every measure can be grouped by the listed time "
    "grains of its time field. "
    "'unanswerable': the question truly cannot be answered exactly from the catalog: a "
    "measure, breakdown or period outside its data window that it does not have. A measure "
    "asked per a role (per salesman) when the catalog links that measure to the entity only "
    "through another role (driver) has no governed path, so it is unanswerable; filtering "
    "by the role would answer a different question. "
    "'ambiguous': valid when two or more listed measures or fields fit the question about "
    "equally and nothing in the question picks one. A view synonym resolves the word unless "
    "another listed measure is an equally natural reading of it: 'retur' can be counted in "
    "units or valued in money, so it stays ambiguous; 'transaksi' as the order count is "
    "resolved, because order lines are not what people call transactions. "
    "Write the reason first, then a verdict that agrees with it: 'no' when the rule is not "
    "met, 'unsure' only when the catalog cannot decide it."
)

CLAIMS_RUBRIC = (
    "You audit an automatically built business ontology. For each claim decide whether it "
    "is correct given its evidence (the published Semantic View definition excerpt) and "
    "ordinary business meaning. Judge only the claim stated: a problem with another name "
    "or synonym of the same concept belongs to that name's own claim. A claim that a metric "
    "or field exists, shown by the excerpt, is 'yes' even without a description. Answer "
    "'no' when any of these holds: wrong entity, wrong "
    "direction or wrong role; a definition that contradicts the formula; a label that names "
    "something other than its definition ('Plan date' for a delivery date); a label or "
    "definition that describes only one of the views in the evidence when the same field "
    "means more in the others (a name defined as 'the driver' that the sales view uses for "
    "the salesman); an alias (official name) that people would at least as naturally use "
    "for one of the competing concepts listed with it, or that ordinary usage gives another "
    "meaning, even when a qualifier follows ('net sales before returns' names a value "
    "before returns, but 'net sales' means after returns). A generic word attached to one "
    "of several concepts that share it ('retur' for returned units when returned value "
    "also exists) is 'no'. Do not reject an everyday phrase for the concept ('nilai order', "
    "'baris pesanan') or the name of one of its roles ('sopir' for an employee entity that "
    "includes drivers) unless one listed competing concept of the same kind (a metric for a "
    "metric's name) is what the word ordinarily means. Answer "
    "'yes' when the claim is true, stated the right way round and not taken by a competing "
    "concept; 'unsure' only when the evidence cannot decide it. Write the reason first, then "
    "a verdict that agrees with it."
)


def competitors(claim: dict, population: dict[str, dict], limit: int = 6) -> list[str]:
    """Other approved concepts sharing a content word with an alias claim's alias or label."""
    from app.modules.ontology.resolver import normalise

    if claim.get("stratum") != "alias":
        return []
    item = claim["item"]
    words = {word for word in normalise(f"{item['alias']} {item['label']}").split()
             if len(word) >= 3}
    found: dict[str, str] = {}
    for other in population.values():
        if other.get("stratum") != "alias":
            continue
        concept = other["item"]
        if concept["qualified_name"] == item["qualified_name"]:
            continue
        names = normalise(f"{concept['alias']} {concept['label']}").split()
        if words & set(names):
            found.setdefault(concept["qualified_name"],
                             f"{concept['qualified_name']} ({concept['label']}): "
                             f"{(concept.get('definition') or '')[:160]}")
    return sorted(found.values())[:limit]


def _new_id(prefix: str, *parts: Any) -> str:
    return prefix + fingerprint([*parts, time.time_ns()])[:32]


class EvaluationService:
    def __init__(
        self, repository=None, catalog=None, client_factory=None, executor=None, models=None,
        autopilot=None,
    ) -> None:
        from app.modules.ontology.repository import ontology_repository
        from app.modules.ontology.representations import SemanticViewCatalog

        self.repository = repository or ontology_repository
        self.catalog = catalog or SemanticViewCatalog()
        self._client_factory = client_factory
        self._executor = executor
        self._models_loader = models
        #: Query Autopilot families, read for join evidence; the shared repository by default.
        self.autopilot = autopilot

    # --- plumbing -------------------------------------------------------------------

    def client(self):
        if self._client_factory is not None:
            return self._client_factory()
        from app.modules.assistant.provider import AssistantProviderClient

        return AssistantProviderClient(timeout_seconds=120, max_attempts=1)

    async def _models(self, user: dict) -> dict[str, dict]:
        if self._models_loader is not None:
            return await self._models_loader(user)
        from app.modules.intelligence.semantic_views import semantic_view_service

        return {
            str(model["semantic_model_id"]): model
            for model in await semantic_view_service.list_active_for_agent(user)
        }

    async def _execute(self, sql: str, model: dict, user: dict) -> tuple[list, list, str | None]:
        if self._executor is not None:
            return await self._executor(sql, model, user)
        from app.modules.query.service import query_service

        try:
            result = await asyncio.wait_for(query_service.execute(
                sql=sql, username=user["username"], encrypted_password=user["encrypted_password"],
                database=model.get("database_name"), role=user.get("active_role"),
                session_id=user.get("session_id"), max_rows=MAX_ROWS,
                security_context_version=int(user.get("security_context_version") or 1),
            ), timeout=90)
        except Exception as exc:  # noqa: BLE001 - a failed query is an outcome
            return [], [], type(exc).__name__
        if result.error:
            return [], [], "query error"
        return list(result.columns or []), [list(row) for row in result.rows or []], None

    async def _run_plan(self, plan: dict, model: dict, user: dict) -> tuple[list, list, str | None]:
        from app.modules.agents.semantic.compiler import SemanticCompiler
        from app.modules.agents.semantic.ir import SemanticModelIR
        from app.modules.agents.semantic.planning import SemanticPlan, validate_plan

        ir = SemanticModelIR.from_ossie(model["definition"])
        try:
            semantic_plan = SemanticPlan.from_dict(plan)
            errors = validate_plan(ir, semantic_plan)
            if errors:
                return [], [], "; ".join(errors)[:300]
            sql = SemanticCompiler().compile(ir, semantic_plan).sql
        except Exception as exc:  # noqa: BLE001 - a plan the compiler refuses is not gold
            # e.g. a non-additive metric over a breakdown it cannot be summed across.
            return [], [], f"{type(exc).__name__}: {str(exc)[:240]}"
        return await self._execute(sql, model, user)

    async def _owned_run(self, run_id: str, user: dict) -> EvalRun:
        await access.require_admin("EVALUATE", "ONTOLOGY_EVALUATION", run_id, user)
        run = await self.repository.latest("eval_runs", run_id, EvalRun)
        if run is None:
            raise HTTPException(status_code=404, detail=access.NOT_FOUND)
        if run.actor.principal != Actor.from_user(user).principal:
            raise HTTPException(status_code=403, detail="Only the person who started a run "
                                                        "can advance it")
        return run

    async def _save_run(self, run: EvalRun, **changes) -> EvalRun:
        updated = run.model_copy(update={**changes, "revision": run.revision + 1})
        return await self.repository.save("eval_runs", updated, expected_revision=run.revision)

    async def cases(self, set_name: str, version: int | None = None) -> list[EvalCase]:
        found = [case for case in await self.repository.current("eval_cases", EvalCase)
                 if case.set_name == set_name]
        if version is None and found:
            version = max(case.version for case in found)
        return sorted((case for case in found if case.version == version), key=lambda c: c.id)

    async def runs(self) -> list[EvalRun]:
        return sorted(await self.repository.current("eval_runs", EvalRun),
                      key=lambda run: run.created_at, reverse=True)

    async def visible_results(self, run_id: str, user: dict) -> list[EvalResult]:
        """A run's results; a join run's profiles only for the context that profiled."""
        from app.modules.ontology.evidence.discovery import same_context

        run = await self.repository.latest("eval_runs", run_id, EvalRun)
        if run is not None and run.kind == "joins" and not same_context(run, user):
            raise HTTPException(status_code=404, detail=access.NOT_FOUND)
        return await self.results(run_id)

    async def start_joins(self, body: Any, user: dict) -> EvalRun:
        from app.modules.ontology.evidence import discovery

        return await discovery.start(self, body, user)

    async def results(self, run_id: str) -> list[EvalResult]:
        # Filtered in the engine: reading every run's results to keep one made the
        # Overview take seconds once a few thousand results existed.
        return await self.repository.current_matching(
            "eval_results", EvalResult, "run_id", run_id, limit=RESULTS_PER_RUN)

    # --- starting runs ----------------------------------------------------------------

    async def start_gold(self, body: GoldRequest, user: dict) -> EvalRun:
        await access.require_admin("EVALUATE", "ONTOLOGY_EVALUATION", body.set_name, user)
        models = await self._models(user)
        missing = [view_id for view_id in body.view_ids if view_id not in models]
        if missing:
            raise HTTPException(status_code=404, detail=access.NOT_FOUND)
        existing = [case.version for case in await self.repository.current("eval_cases", EvalCase)
                    if case.set_name == body.set_name]
        version = max(existing, default=0) + 1
        run = EvalRun(
            id=_new_id("evn_", body.set_name, version), actor=Actor.from_user(user),
            kind="gold", set_name=body.set_name, version=version, stage="intents",
            items=list(body.view_ids),
            summary={"per_metric": body.per_metric, "seed": body.seed,
                     "vocabulary": body.vocabulary},
        )
        await access.audit("EVALUATE", "ONTOLOGY_EVALUATION", run.id, user)
        return await self.repository.save("eval_runs", run)

    async def start_answers(self, body: AnswerRunRequest, user: dict) -> EvalRun:
        await access.require_admin("EVALUATE", "ONTOLOGY_EVALUATION", body.set_name, user)
        cases = [case for case in await self.cases(body.set_name, body.version)
                 if case.status == "ACTIVE"]
        if not cases:
            raise HTTPException(status_code=409, detail="This gold set has no active cases")
        release_id, manifest = body.release_id, None
        if release_id is None:
            alias = await self.repository.latest("aliases", "current", Alias)
            release_id = alias.release_id if alias else None
        if release_id:
            release = await self.repository.latest("releases", release_id, Release)
            if release is None or release.status != "PUBLISHED":
                raise HTTPException(status_code=409, detail="Evaluate a published release")
            manifest = release.manifest_hash
        if body.mode == "with_ontology" and not release_id:
            raise HTTPException(status_code=409, detail="No ontology release is published")
        run = EvalRun(
            id=_new_id("evn_", body.set_name, body.mode), actor=Actor.from_user(user),
            kind="answers", set_name=body.set_name, version=cases[0].version, mode=body.mode,
            release_id=release_id, manifest_hash=manifest, stage="answers",
            items=[case.id for case in cases], summary={"view_hint": body.view_hint},
        )
        await access.audit("EVALUATE", "ONTOLOGY_EVALUATION", run.id, user)
        return await self.repository.save("eval_runs", run)

    async def start_judge(self, body: JudgeRunRequest, user: dict) -> EvalRun:
        await access.require_admin("EVALUATE", "ONTOLOGY_EVALUATION", body.set_name, user)
        cases = [case for case in await self.cases(body.set_name, body.version)
                 if case.status != "DRAFT" and case.question]
        if not cases:
            raise HTTPException(status_code=404, detail=access.NOT_FOUND)
        version = cases[0].version
        gold = next((run for run in await self.runs() if run.kind == "gold"
                     and run.set_name == body.set_name and run.version == version), None)
        run = EvalRun(
            id=_new_id("evn_", "judge"), actor=Actor.from_user(user), kind="judge",
            set_name=body.set_name, version=version, stage="judge",
            items=sorted(case.id for case in cases),
            summary={"rubric": RUBRIC, "windows": (gold.summary.get("windows") if gold else {})},
        )
        await access.audit("EVALUATE", "ONTOLOGY_EVALUATION", run.id, user)
        return await self.repository.save("eval_runs", run)

    async def _judge_step(self, run: EvalRun, user: dict) -> EvalRun:
        batch = run.items[run.done:run.done + JUDGE_BATCH]
        cases = {case.id: case for case in await self.cases(run.set_name, run.version)}
        if not batch:
            judged = [cases[key] for key in run.items if key in cases]
            return await self._save_run(run, status="COMPLETED", stage="done", summary={
                **run.summary, "judged": len(judged), "agreement": agreement(judged)})
        await self._review_cases([cases[key] for key in batch if key in cases], user,
                                 run.summary.get("windows") or {})
        return await self._save_run(run, done=run.done + len(batch))

    async def start_claims(self, body: ClaimRunRequest, user: dict) -> EvalRun:
        await access.require_admin("EVALUATE", "ONTOLOGY_EVALUATION", body.set_name, user)
        population = await self._claims(user)
        strata: dict[str, list[str]] = {}
        for key, claim in population.items():
            strata.setdefault(claim["stratum"], []).append(key)
        rng = random.Random(body.seed)
        items: list[str] = []
        for stratum in sorted(strata):
            keys = sorted(strata[stratum])
            items += rng.sample(keys, min(body.per_stratum, len(keys)))
        alias = await self.repository.latest("aliases", "current", Alias)
        run = EvalRun(
            id=_new_id("evn_", "claims"), actor=Actor.from_user(user), kind="claims",
            set_name=body.set_name, version=1, stage="claims", items=items,
            release_id=alias.release_id if alias else None,
            summary={"population": {name: len(keys) for name, keys in strata.items()}},
        )
        await access.audit("EVALUATE", "ONTOLOGY_EVALUATION", run.id, user)
        return await self.repository.save("eval_runs", run)

    # --- advancing --------------------------------------------------------------------

    async def step(self, run_id: str, user: dict) -> EvalRun:
        run = await self._owned_run(run_id, user)
        if run.status != "RUNNING":
            return run
        try:
            if run.kind == "gold":
                return await self._gold_step(run, user)
            if run.kind == "answers":
                return await self._answers_step(run, user)
            if run.kind == "judge":
                return await self._judge_step(run, user)
            if run.kind == "joins":
                from app.modules.ontology.evidence import discovery

                return await discovery.step(self, run, user)
            return await self._claims_step(run, user)
        except HTTPException:
            raise
        except AssistantProviderError as exc:
            # A provider timeout is transient: the run stays open and the next step retries.
            latest = await self.repository.latest("eval_runs", run.id, EvalRun) or run
            return await self._save_run(latest, error=f"Retrying after: {exc.message}"[:1000])
        except Exception as exc:  # noqa: BLE001 - a run records why it stopped
            latest = await self.repository.latest("eval_runs", run.id, EvalRun) or run
            return await self._save_run(latest, status="FAILED",
                                        error=f"{type(exc).__name__}: {str(exc)[:300]}")

    async def resume(self, run_id: str, user: dict) -> EvalRun:
        """Reopen a failed run where it stopped; finished work is kept."""
        run = await self._owned_run(run_id, user)
        if run.status != "FAILED":
            return run
        await access.audit("EVALUATE", "ONTOLOGY_EVALUATION", run.id, user)
        return await self._save_run(run, status="RUNNING", error=None)

    # --- gold ---------------------------------------------------------------------------

    async def _gold_step(self, run: EvalRun, user: dict) -> EvalRun:
        if run.stage == "intents":
            return await self._gold_intents(run, user)
        drafts = [case for case in await self.cases(run.set_name, run.version)
                  if case.status == "DRAFT"]
        if run.stage == "phrase":
            pending = [case for case in drafts if not case.question][:JUDGE_BATCH]
            if not pending:
                return await self._save_run(run, stage="negatives", done=0)
            await self._phrase(pending, user)
            return await self._save_run(run, done=run.done + len(pending))
        if run.stage == "negatives":
            await self._negatives(run, user)
            return await self._save_run(run, stage="review", done=0)
        if run.stage == "review":
            pending = [case for case in drafts
                       if not any(review.kind == "model" for review in case.reviews)
                       and case.question][:JUDGE_BATCH]
            if not pending:
                return await self._finish_gold(run)
            await self._review_cases(pending, user, run.summary.get("windows") or {})
            return await self._save_run(run, done=run.done + len(pending))
        return run

    async def _gold_intents(self, run: EvalRun, user: dict) -> EvalRun:
        from app.modules.agents.semantic.ir import SemanticModelIR
        from app.modules.ontology.representations import public_metrics

        if run.done >= len(run.items):
            return await self._save_run(run, stage="phrase", done=0)
        view_id = run.items[run.done]
        models = await self._models(user)
        model = models.get(view_id)
        if model is None:
            raise HTTPException(status_code=404, detail=access.NOT_FOUND)
        definition = model["definition"]
        ir = SemanticModelIR.from_ossie(definition)
        metrics = [name for name in public_metrics(definition)]
        window = await self._window(ir, metrics, model, user)
        per_metric = int(run.summary.get("per_metric") or ANSWERABLE_PER_METRIC)
        seed = int(run.summary.get("seed") or 0)
        if run.summary.get("vocabulary") == "business":
            from app.modules.ontology.service import OntologyService

            reads = OntologyService(self.repository, self.catalog)
            snapshot = await reads.snapshot("current")
            terms = gold_cases.business_terms(
                ir, view_id, reads.visible_concepts(snapshot, user), snapshot.representations)
            found = gold_cases.business_intents(
                ir, view_id, model["name"], metrics, window, terms,
                per_metric=per_metric, seed=seed)
        else:
            found = gold_cases.intents(
                ir, view_id, model["name"], metrics, window, per_metric=per_metric, seed=seed)
        rng = random.Random(f"{run.summary.get('seed')}:{view_id}:language")
        semaphore = asyncio.Semaphore(PARALLEL)

        async def check(intent: gold_cases.Intent) -> EvalCase | None:
            async with semaphore:
                if intent.needs_value:
                    columns, rows, error = await self._run_plan(
                        gold_cases.value_probe(intent), model, user)
                    field = intent.needs_value.rpartition(".")[2]
                    index = next((i for i, name in enumerate(columns)
                                  if str(name).casefold() == field.casefold()), 0)
                    values = [row[index] for row in rows if row and row[index] not in (None, "")]
                    if error or not values:
                        return None
                    intent = gold_cases.with_value(intent, values[0])
                columns, rows, error = await self._run_plan(intent.plan, model, user)
                if error or not rows:
                    return None
            return EvalCase(
                id=case_id(run.set_name, run.version, intent.key), actor=run.actor,
                set_name=run.set_name, version=run.version, kind="answerable",
                intent=intent.template, language="id" if rng.random() < 0.7 else "en",
                view_id=view_id, view_name=model["name"], plan=intent.plan,
                meaning=intent.meaning, expected="answer", terms=intent.terms,
            )

        cases = [case for case in await asyncio.gather(*(check(item) for item in found)) if case]
        if cases:
            await self.repository.save_many("eval_cases", [(case, 0) for case in cases])
        windows = dict(run.summary.get("windows") or {})
        windows[view_id] = list(window)
        counts = dict(run.summary.get("intents") or {})
        counts[view_id] = {"planned": len(found), "kept": len(cases)}
        return await self._save_run(run, done=run.done + 1,
                                    summary={**run.summary, "windows": windows, "intents": counts})

    async def _window(self, ir, metrics: list[str], model: dict, user: dict) -> tuple[str, str]:
        """The months that have data, read through a governed plan."""
        for name in metrics:
            metric = ir.metric(name)
            if metric is None or not metric.default_time_dimension:
                continue
            columns, rows, error = await self._run_plan({
                "metrics": [name],
                "time": {"dimension": metric.default_time_dimension, "grain": "day",
                         "range": "since_2000"},
            }, model, user)
            dates = sorted(str(row[0])[:10] for row in rows if row and row[0] is not None)
            if not error and dates:
                return dates[0], dates[-1]
        raise HTTPException(status_code=422, detail="The view has no dated data to ask about")

    async def _phrase(self, cases: list[EvalCase], user: dict) -> None:
        answer = await ask(
            self.client(),
            "You write evaluation questions for a business analytics assistant, as a manager "
            "would type them. For each item write one question in its language ('id' is natural "
            "Bahasa Indonesia used in Indonesian companies; 'en' is English) that asks for "
            "exactly the given meaning: the same measure, the same breakdown, the same period, "
            "the same filter value and the same top-N. Use everyday business words, never "
            "snake_case or technical names, write the period in words and keep filter values "
            "exactly as given. Square-bracketed names are technical identifiers: never copy "
            "them. When the meaning gives a measure term or a breakdown term, use that exact "
            "phrase for it and never the measure's own name or description. Do not add "
            "anything the meaning does not ask for.",
            {"items": [{"id": case.id, "language": case.language, "meaning": case.meaning}
                       for case in cases]},
            items_schema({"id": TEXT, "question": TEXT}), "evaluation_questions",
        )
        by_id = {str(item.get("id")): str(item.get("question") or "").strip()
                 for item in answer.get("items") or [] if isinstance(item, dict)}
        from app.modules.ontology.resolver import normalise

        updated = []
        for case in cases:
            question = by_id.get(case.id, "")[:1000]
            reviews = list(case.reviews)
            missing = [term for term in case.terms
                       if f" {normalise(term)} " not in f" {normalise(question)} "]
            if question and missing:
                # A vocabulary case that does not use its words tests nothing.
                reviews.append(Review(
                    reviewer="nova:vocabulary", kind="model", verdict="no",
                    reason="the question does not use " + ", ".join(repr(t) for t in missing),
                ))
            updated.append((case.model_copy(update={
                "question": question or "-", "revision": case.revision + 1, "reviews": reviews,
                "status": "DRAFT" if question else "REJECTED",
            }), case.revision))
        await self.repository.save_many("eval_cases", updated)

    def _catalog_text(
        self, models: dict[str, dict], view_ids: list[str], windows: dict | None = None,
    ) -> list[dict]:
        """What a judge needs to know about each view: measures, breakdowns, time, window."""
        from app.modules.agents.semantic.ir import SemanticModelIR
        from app.modules.ontology.representations import public_metrics

        catalog = []
        for view_id in view_ids:
            model = models.get(view_id)
            if model is None:
                continue
            ir = SemanticModelIR.from_ossie(model["definition"])
            measures = []
            for name in public_metrics(model["definition"]):
                metric = ir.metric(name)
                measures.append({
                    "name": name,
                    "description": metric.description if metric else "",
                    "unit": metric.unit if metric else None,
                    "time": metric.default_time_dimension if metric else None,
                    "breakdowns": [label for _, label in gold_cases.breakdowns(ir, name)],
                })
            catalog.append({
                "view": model["name"], "view_id": view_id, "measures": measures,
                "time_grains": ["day", "week", "month", "quarter", "year"],
                "data_window": (windows or {}).get(view_id),
            })
        return catalog

    async def _negatives(self, run: EvalRun, user: dict) -> None:
        from app.modules.ontology.lint import lint

        answerable = [case for case in await self.cases(run.set_name, run.version)
                      if case.kind == "answerable" and case.status != "REJECTED"]
        total = len(answerable) / (1 - UNANSWERABLE_SHARE - AMBIGUOUS_SHARE)
        unanswerable, ambiguous = round(total * UNANSWERABLE_SHARE), round(total * AMBIGUOUS_SHARE)
        models = await self._models(user)
        view_ids = [view_id for view_id in run.items if view_id in models]
        if not view_ids:
            # An empty list means the views could not be read right now, not that they
            # vanished: stop here so the next step retries instead of writing empty cases.
            raise HTTPException(status_code=503, detail="Semantic Views are unavailable; retry")
        windows = run.summary.get("windows") or {}
        catalog = self._catalog_text(models, view_ids, windows)
        drafts: list[EvalCase] = []
        rng = random.Random(f"{run.summary.get('seed')}:negatives")

        def draft(kind: str, intent: str, question: str, language: str, view_id: str,
                  meaning: str, expected: str, key: str) -> EvalCase:
            return EvalCase(
                id=case_id(run.set_name, run.version, f"{kind}:{key}"), actor=run.actor,
                set_name=run.set_name, version=run.version, kind=kind, intent=intent,  # type: ignore[arg-type]
                language=language if language in {"id", "en"} else "id",  # type: ignore[arg-type]
                question=question[:1000], view_id=view_id,
                view_name=models.get(view_id, {}).get("name", ""), meaning=meaning[:2000],
                expected=expected,  # type: ignore[arg-type]
            )

        # Names two domains share and roles a measure does not have: found, not invented.
        shared = []
        for finding in await lint(user, self.catalog, self.repository):
            if finding.rule == "ambiguous_name":
                name = finding.message.split("'")[1] if "'" in finding.message else ""
                if name:
                    shared.append((name, finding.message))
        role_conflicts = await self._role_conflicts(user, models, view_ids)
        # Found cases count toward their share; the model writes only the rest.
        unanswerable = max(0, unanswerable - len(role_conflicts))
        vague = max(0, ambiguous - len(shared))
        schema: dict[str, Any] = {
            "type": "object", "additionalProperties": False,
            "required": ["unanswerable", "seeded", "vague"],
            "properties": {
                "unanswerable": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["question", "language", "view_id", "reason"],
                    "properties": {"question": TEXT, "language": TEXT, "view_id": TEXT,
                                   "reason": TEXT}}},
                "seeded": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["seed", "question", "language", "view_id"],
                    "properties": {"seed": TEXT, "question": TEXT, "language": TEXT,
                                   "view_id": TEXT}}},
                "vague": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["question", "language", "view_id", "measures"],
                    "properties": {"question": TEXT, "language": TEXT, "view_id": TEXT,
                                   "measures": {"type": "array", "items": TEXT}}}},
            },
        }
        seeds_payload = [
            *({"seed": f"shared:{name}", "about": message} for name, message in shared),
            *({"seed": f"role:{item['key']}", "about": item["about"]} for item in role_conflicts),
        ]
        rules = (
            " Mix Bahasa Indonesia (about 70%) and English. Never use snake_case or bracketed "
            "technical names. Leave the other lists empty."
        )
        # Two smaller calls, side by side: one long call outlived the provider's time limit.
        missing, hard = await asyncio.gather(
            ask(
                self.client(),
                "You build the hard part of an evaluation set for a business analytics assistant. "
                f"Given the catalog and the data window, write {unanswerable} questions a "
                "manager could plausibly ask that CANNOT be answered exactly from this catalog (a "
                "measure, breakdown or role it does not have, or a period outside the data "
                "window), each with the closest view and the reason." + rules,
                {"catalog": catalog, "data_window": windows}, schema, "evaluation_negatives",
            ),
            ask(
                self.client(),
                "You build the hard part of an evaluation set for a business analytics assistant. "
                "Write one natural question for each listed ambiguous seed, using the shared name "
                f"or role as people would; and {vague} questions that are vague because two or "
                "more catalog measures fit equally (for example a general word for sales), each "
                "with the measures that fit, only when a careful analyst could not tell from the "
                "definitions which is meant." + rules,
                {"catalog": catalog, "data_window": windows, "ambiguous_seeds": seeds_payload},
                schema, "evaluation_negatives",
            ),
        )
        answer = {
            "unanswerable": missing.get("unanswerable") or [],
            "seeded": hard.get("seeded") or [],
            "vague": hard.get("vague") or [],
        }
        fallback = view_ids[0] if view_ids else ""

        def view(value: Any) -> str:
            return str(value) if str(value) in models else fallback

        for index, item in enumerate((answer.get("unanswerable") or [])[:unanswerable]):
            if isinstance(item, dict) and item.get("question"):
                drafts.append(draft(
                    "unanswerable", "missing_measure", str(item["question"]),
                    str(item.get("language")), view(item.get("view_id")),
                    "Not answerable from the catalog: " + str(item.get("reason") or ""),
                    "abstain", f"u{index}:{item['question']}",
                ))
        seeds = {f"shared:{name}": message for name, message in shared}
        seeds.update({f"role:{item['key']}": item["about"] for item in role_conflicts})
        for item in answer.get("seeded") or []:
            if not isinstance(item, dict) or str(item.get("seed")) not in seeds:
                continue
            seed = str(item["seed"])
            role = seed.startswith("role:")
            # A role the measure has no path to cannot be answered; a shared name must be asked.
            drafts.append(draft(
                "unanswerable" if role else "ambiguous", "role_mismatch" if role else "shared_name",
                str(item.get("question") or ""), str(item.get("language")),
                view(item.get("view_id")),
                ("Not answerable: " if role else "Needs clarification: ") + seeds[seed],
                "abstain" if role else "clarify", seed,
            ))
        for index, item in enumerate((answer.get("vague") or [])[:vague]):
            if isinstance(item, dict) and item.get("question"):
                drafts.append(draft(
                    "ambiguous", "vague_measure", str(item["question"]),
                    str(item.get("language")), view(item.get("view_id")),
                    "Needs clarification: these measures fit equally: "
                    + ", ".join(str(value) for value in item.get("measures") or []),
                    "clarify", f"v{index}:{item['question']}",
                ))
        rng.shuffle(drafts)
        if drafts:
            await self.repository.save_many("eval_cases", [(case, 0) for case in drafts])

    async def _role_conflicts(self, user, models, view_ids) -> list[dict]:
        """A measure asked per a role its entity is not linked through (sales per driver)."""
        from app.modules.ontology.derived import visible_layer

        layer = await visible_layer(user, self.catalog, self.repository)
        items = {item["key"]: item for item in layer["items"]}
        roles_of: dict[str, set[str]] = {}
        links: dict[str, dict[str, set[str]]] = {}
        for item in items.values():
            if item["relation_type"] == "RELATED_TO" and item["role"]:
                roles_of.setdefault(item["target_key"], set()).add(item["role"])
                links.setdefault(item["source_key"], {}).setdefault(
                    item["target_key"], set()).add(item["role"])
        found = []
        for item in items.values():
            if item["relation_type"] != "MEASURES":
                continue
            metric, entity = items.get(item["source_key"]), item["target_key"]
            if metric is None:
                continue
            view_id = (metric.get("metric") or "").rpartition(".")[0]
            if view_id not in view_ids:
                continue
            for target, roles in links.get(entity, {}).items():
                others = sorted(roles_of.get(target, set()) - roles)
                if others:
                    found.append({
                        "key": f"{metric['key']}:{others[0]}",
                        "view_id": view_id,
                        "about": (
                            f"{metric['label']} is linked to {items[target]['label']} only as "
                            f"{', '.join(sorted(roles))}; a question per {others[0]} has no "
                            "governed path"
                        ),
                    })
        unique = {item["about"]: item for item in found}
        return sorted(unique.values(), key=lambda item: item["key"])[:6]

    async def _review_cases(
        self, cases: list[EvalCase], user: dict, windows: dict | None = None,
    ) -> None:
        models = await self._models(user)
        catalog = self._catalog_text(models, sorted({case.view_id for case in cases}), windows)
        by_id, model_name = await ask_items(
            self.client(),
            GOLD_RUBRIC,
            {"catalog": catalog},
            [{"id": case.id, "kind": case.kind, "question": case.question,
              "meaning": case.meaning} for case in cases],
            items_schema({"id": TEXT, "reason": TEXT, "verdict": VERDICT}), "evaluation_review",
        )
        model_name = f"{model_name}@{RUBRIC}"
        updated = []
        for case in cases:
            item = by_id.get(case.id) or {}
            verdict = _verdict(item.get("verdict"))
            review = Review(reviewer=model_name, kind="model", verdict=verdict,
                            reason=str(item.get("reason") or "no verdict returned")[:1000])
            updated.append((case.model_copy(update={
                "reviews": [*case.reviews, review], "revision": case.revision + 1,
            }), case.revision))
        await self.repository.save_many("eval_cases", updated)

    async def _finish_gold(self, run: EvalRun) -> EvalRun:
        cases = await self.cases(run.set_name, run.version)
        updated = []
        for case in cases:
            if case.status != "DRAFT":
                continue
            verdicts = [review.verdict for review in case.reviews]
            status = "ACTIVE" if verdicts and all(v == "yes" for v in verdicts) else "REJECTED"
            updated.append((case.model_copy(update={
                "status": status, "revision": case.revision + 1}), case.revision))
        if updated:
            await self.repository.save_many("eval_cases", updated)
        cases = await self.cases(run.set_name, run.version)
        counts = Counter((case.kind, case.status) for case in cases)
        summary = {
            **run.summary,
            "cases": {
                f"{kind}:{status}": count for (kind, status), count in sorted(counts.items())
            },
            "active": sum(1 for case in cases if case.status == "ACTIVE"),
        }
        return await self._save_run(run, status="COMPLETED", stage="done", summary=summary)

    # --- answers ------------------------------------------------------------------------

    async def _answers_step(self, run: EvalRun, user: dict) -> EvalRun:
        batch = run.items[run.done:run.done + STEP_BATCH]
        if not batch:
            return await self._finish_answers(run)
        cases = {case.id: case for case in await self.cases(run.set_name, run.version)}
        models = await self._models(user)
        semaphore = asyncio.Semaphore(PARALLEL)

        async def answer(case_ref: str) -> EvalResult:
            async with semaphore:
                case = cases[case_ref]
                started = time.perf_counter()
                try:
                    detail = await self._answer(case, run, models, user)
                except Exception as exc:  # noqa: BLE001 - one broken case never stops a run
                    detail = self._score(case, {
                        "kind": case.kind, "expected": case.expected, "outcome": "failed",
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    })
                detail["latency_ms"] = round((time.perf_counter() - started) * 1000)
                return EvalResult(
                    id=result_id(run.id, case.id), actor=run.actor, run_id=run.id, item=case.id,
                    outcome=detail.pop("outcome"), detail=detail,
                )

        results = await asyncio.gather(*(answer(item) for item in batch if item in cases))
        await self.repository.save_many("eval_results", [(item, 0) for item in results])
        return await self._save_run(run, done=run.done + len(batch))

    async def _answer(self, case: EvalCase, run: EvalRun, models: dict, user: dict) -> dict:
        from app.modules.agents.semantic.access import load_authorized_models
        from app.modules.agents.semantic.compiler import SemanticCompiler
        from app.modules.agents.semantic.ir import SemanticModelIR
        from app.modules.agents.semantic.model_planner import generate_plan, planning_catalog
        from app.modules.agents.semantic.planning import SemanticPlanError, validate_plan
        from app.modules.ontology.grounding import ground, grounding_gaps

        hinted = run.summary.get("view_hint", True) is not False
        context = SimpleNamespace(
            user=user, ontology_release_id=run.release_id,
            requested_semantic_view=case.view_id if hinted else None,
            authorized_semantic_models=list(models.values()), quality_facts={}, usage=None,
            model_provider_id=None, model_name=None, agent_id=None,
        )
        detail: dict[str, Any] = {"kind": case.kind, "expected": case.expected}
        hints = None
        grounding = None
        view_id: str | None = case.view_id if hinted else None
        routed_by = "hint" if hinted else ""
        if run.mode == "with_ontology":
            grounding = await ground(case.question, context,
                                     lambda: load_authorized_models(context))
            detail["grounding"] = grounding.status
            if grounding.resolution is not None:
                detail["concepts"] = sorted(
                    item["qualified_name"] for item in grounding.resolution.concepts)
            if grounding.blocking:
                detail["outcome"] = (
                    "clarified" if grounding.error_class == "CLARIFICATION_REQUIRED"
                    else "abstained")
                return self._score(case, detail)
            hints = grounding.planner_hints()
            if grounding.view_id and not hinted:
                view_id, routed_by = grounding.view_id, "ontology"
            elif grounding.view_id:
                view_id = grounding.view_id
        if view_id is None:
            view_id, routed_by = await self._route(case.question, models)
        detail["view"] = view_id
        detail["routed_by"] = routed_by
        if case.kind == "answerable":
            detail["view_correct"] = view_id == case.view_id
        model = models.get(view_id or "")
        if model is None and view_id is None and routed_by == "model":
            # The router found no view whose measures compute the question: Studio says so.
            detail["outcome"] = "abstained"
            detail["error"] = "no view answers the question"
            return self._score(case, detail)
        if model is None:
            detail["outcome"] = "failed"
            detail["error"] = "view unavailable"
            return self._score(case, detail)
        ir = SemanticModelIR.from_ossie(model["definition"])
        try:
            plan = await generate_plan(
                self.client(), ir, await planning_catalog(ir, case.question), case.question,
                context, business_concepts=hints,
            )
        except (SemanticPlanError, ValueError) as exc:
            detail["outcome"] = "failed"
            detail["error"] = str(exc)[:300]
            return self._score(case, detail)
        except Exception as exc:  # noqa: BLE001 - a provider failure is recorded, not hidden
            detail["outcome"] = "failed"
            detail["error"] = type(exc).__name__
            return self._score(case, detail)
        detail["plan"] = plan.as_dict()
        if [item for item in plan.unresolved_concepts if item.material]:
            detail["outcome"] = "clarified"
            return self._score(case, detail)
        errors = validate_plan(ir, plan)
        if grounding is not None:
            missing, explained = grounding_gaps(grounding, plan.metrics, ir)
            if explained:
                detail["explained"] = explained
            errors = [*missing, *errors]
        if errors:
            detail["outcome"] = "failed"
            detail["error"] = "; ".join(errors)[:300]
            return self._score(case, detail)
        detail["tokens"] = (getattr(context, "usage", None) or {}).get("total_tokens")
        time_context, not_yet = _time_context(plan)
        if not_yet:
            detail["outcome"] = "abstained"
            detail["error"] = not_yet[:300]
            return self._score(case, detail)
        sql = SemanticCompiler().compile(ir, plan).sql
        detail["sql_fingerprint"] = fingerprint(sql)[:16]
        columns, rows, error = await self._execute(sql, model, user)
        if error:
            detail["outcome"] = "failed"
            detail["error"] = error
            return self._score(case, detail)
        gap = await period_without_data(
            plan, columns, rows, time_context,
            lambda window_plan: self._run_plan(window_plan, model, user),
        )
        if gap is not None:
            detail["outcome"] = "abstained"
            detail["data_window"] = [gap.window.first.isoformat(), gap.window.last.isoformat()]
            return self._score(case, detail)
        if case.kind != "answerable" or case.plan is None:
            detail["outcome"] = "answered"
            return self._score(case, detail)
        gold_model = models.get(case.view_id)
        if gold_model is None:
            detail["outcome"] = "failed"
            detail["error"] = "gold view unavailable"
            return self._score(case, detail)
        gold_columns, gold_rows, gold_error = await self._run_plan(case.plan, gold_model, user)
        if gold_error:
            detail["outcome"] = "failed"
            detail["error"] = "gold failed: " + gold_error
            return self._score(case, detail)
        order_by = case.plan.get("order_by") or []
        match, reason = same_result(
            columns, rows, gold_columns, gold_rows, ordered=gold_cases.ordered(case.plan),
            order_key=str(order_by[0].get("field")) if order_by else None,
        )
        detail["rows"] = len(rows)
        detail["gold_rows"] = len(gold_rows)
        detail["mismatch"] = reason
        detail["outcome"] = "correct" if match else "wrong"
        return self._score(case, detail)

    @staticmethod
    def _score(case: EvalCase, detail: dict) -> dict:
        """TrustSQL reliability: reward the right behaviour, penalise a confident wrong one."""
        outcome = detail["outcome"]
        if case.expected == "answer":
            detail["reliability"] = 1 if outcome == "correct" else (
                -1 if outcome in {"wrong", "answered"} else 0)
        else:
            detail["reliability"] = 1 if outcome in {"abstained", "clarified"} else (
                -1 if outcome in {"answered", "correct", "wrong"} else 0)
        return detail

    async def _route(self, question: str, models: dict[str, dict]) -> tuple[str | None, str]:
        """Choose a view the way Studio does without the ontology.

        Studio first takes the one view whose declared metric names or synonyms the
        question uses, then the view the agent model names. The agent model is
        stood in for by one structured call over the same catalog.
        """
        from app.modules.agents.semantic.ir import SemanticModelIR
        from app.modules.agents.tools.semantic_query import _view_naming_metric

        named = _view_naming_metric(list(models.values()), question)
        if named:
            return named, "metric name"
        catalog = []
        for view_id, model in sorted(models.items()):
            ir = SemanticModelIR.from_ossie(model["definition"])
            catalog.append({
                "view_id": view_id, "name": model.get("name"),
                "description": ir.description[:300],
                "measures": [f"{m.name}: {m.description}"[:120] for m in ir.metrics][:40],
                # What a measure can be broken down or filtered by (role, payment method):
                # without it a question per role looked unanswerable to the router.
                "breakdowns": sorted({
                    field.name for dataset in ir.datasets for field in dataset.fields
                    if field.kind.value == "dimension"
                })[:60],
            })
        answer = await ask(
            self.client(),
            "You route a business question to the one Semantic View that can answer it, as a "
            "data assistant does before querying. Choose the view whose measures compute what "
            "the question asks; answer 'none' only when no view can.",
            {"question": question, "views": catalog},
            {"type": "object", "additionalProperties": False, "required": ["reason", "view_id"],
             "properties": {"reason": TEXT, "view_id": TEXT}},
            "view_route",
        )
        chosen = str(answer.get("view_id") or "")
        return (chosen, "model") if chosen in models else (None, "model")

    async def _finish_answers(self, run: EvalRun) -> EvalRun:
        results = await self.results(run.id)
        return await self._save_run(run, status="COMPLETED", stage="done",
                                    summary={**run.summary, **summarise_answers(results)})

    # --- claims -------------------------------------------------------------------------

    async def _claims(self, user: dict) -> dict[str, dict]:
        """Every claim the ontology makes that the caller may see, by stratum."""
        from app.modules.ontology.derived import visible_layer

        layer = await visible_layer(user, self.catalog, self.repository)
        claims: dict[str, dict] = {}
        for item in layer["items"]:
            kind = item["concept_kind"] or item["relation_type"] or ""
            stratum = {
                "BUSINESS_ENTITY": "entity", "DIMENSION": "dimension", "METRIC": "metric",
                "MEASURES": "measures", "DERIVES": "derives", "EQUIVALENT_TO": "equivalent",
            }.get(kind)
            if kind == "RELATED_TO":
                stratum = "related_role" if item["role"] else "related"
            if stratum:
                claims[f"derived:{item['key']}"] = {"stratum": stratum, "item": item,
                                                    "confidence": item["confidence"]}
        domains = {item.id: item for item in await self.repository.current("domains", Domain)}
        for concept in await self.repository.current("concepts", Concept):
            domain = domains.get(concept.domain_id)
            if concept.status != "APPROVED" or not access.can_see(domain, domains, user):
                continue
            for alias in concept.aliases:
                claims[f"alias:{concept.id}:{alias}"] = {
                    "stratum": "alias", "confidence": None,
                    "item": {"label": concept.label, "alias": alias,
                             "qualified_name": access.qualified_path(domain, domains)  # type: ignore[arg-type]
                             + "." + concept.slug, "definition": concept.definition,
                             "kind": concept.kind.value},
                }
        return claims

    def _describe(self, key: str, claim: dict, items: dict[str, dict]) -> str:
        item = claim["item"]
        if claim["stratum"] == "alias":
            return (f"People who say '{item['alias']}' mean {item['qualified_name']} "
                    f"({item['kind']}: {item['label']} — {item['definition'][:300]})")

        def name(ref: str | None) -> str:
            other = items.get(ref or "") or {}
            text = other.get("label") or ref or ""
            if other.get("definition"):
                text += f" ({other['definition'][:160]})"
            return text

        evidence = "; ".join(
            f"{e['view_name']} v{e['semantic']['version']} {e['method']} {e['source']}"
            for e in item["evidence"][:4])
        if item["item_type"] == "concept":
            text = f"{item['concept_kind']} '{item['label']}'"
            if item.get("definition"):
                text += f": {item['definition'][:300]}"
            if item.get("roles"):
                text += f"; plays the roles {', '.join(item['roles'])}"
        else:
            relation = {
                "MEASURES": "is measured over each", "DERIVES": "is an input used to compute",
                "RELATED_TO": "refers to", "EQUIVALENT_TO": "computes the same as",
            }.get(item["relation_type"], item["relation_type"])
            text = f"{name(item['source_key'])} {relation} {name(item['target_key'])}"
            if item.get("role"):
                text += f" as its {item['role']}"
        return f"{text}. Evidence: {evidence}"

    async def _claims_step(self, run: EvalRun, user: dict) -> EvalRun:
        batch = run.items[run.done:run.done + JUDGE_BATCH]
        if not batch:
            results = await self.results(run.id)
            return await self._save_run(run, status="COMPLETED", stage="done", summary={
                **run.summary, **summarise_claims(results, run.summary.get("population") or {})})
        population = await self._claims(user)
        items = {claim["item"].get("key"): claim["item"] for claim in population.values()
                 if claim["item"].get("key")}
        present = [key for key in batch if key in population]
        evidence = await self._claim_evidence(present, population, user)
        by_id, model_name = await ask_items(
            self.client(), CLAIMS_RUBRIC, {},
            [{"id": key, "claim": self._describe(key, population[key], items),
              "definition_excerpt": evidence.get(key, ""),
              "competing_concepts": competitors(population[key], population)}
             for key in present],
            items_schema({"id": TEXT, "reason": TEXT, "verdict": VERDICT}), "ontology_claims",
        )
        model_name = f"{model_name}@{RUBRIC}"
        results = []
        for key in batch:
            claim = population.get(key)
            if claim is None:
                results.append(EvalResult(
                    id=result_id(run.id, key), actor=run.actor, run_id=run.id, item=key[:300],
                    outcome="gone", detail={"stratum": "gone"}))
                continue
            item = by_id.get(key) or {}
            verdict = _verdict(item.get("verdict"))
            results.append(EvalResult(
                id=result_id(run.id, key), actor=run.actor, run_id=run.id, item=key[:300],
                outcome=verdict,
                detail={"stratum": claim["stratum"], "confidence": claim["confidence"],
                        "claim": self._describe(key, claim, items)[:1500]},
                reviews=[Review(reviewer=model_name, kind="model", verdict=verdict,
                                reason=str(item.get("reason") or "no verdict returned")[:1000])],
            ))
        await self.repository.save_many("eval_results", [(item, 0) for item in results])
        return await self._save_run(run, done=run.done + len(batch))

    async def _claim_evidence(self, keys, population, user) -> dict[str, str]:
        """The part of each view definition a claim rests on."""
        import json

        models = await self._models(user)
        found: dict[str, str] = {}
        for key in keys:
            item = population[key]["item"]
            parts = []
            for evidence in (item.get("evidence") or [])[:2]:
                model = models.get(evidence["semantic"]["view_id"])
                if model is None:
                    continue
                kind, _, name = evidence["source"].partition(":")
                definition = model["definition"]
                excerpt: Any = None
                if kind == "dataset":
                    excerpt = next((d for d in definition.get("datasets", [])
                                    if d.get("name") == name), None)
                    if excerpt:
                        excerpt = {k: excerpt.get(k) for k in
                                   ("name", "source", "description", "primary_key")}
                elif kind == "field":
                    dataset, _, field_name = name.partition(".")
                    for d in definition.get("datasets", []):
                        if d.get("name") == dataset:
                            excerpt = next((f for f in d.get("fields", [])
                                            if f.get("name") == field_name), None)
                    if excerpt:
                        excerpt = {k: excerpt.get(k) for k in (
                            "name", "description", "expression", "datatype", "synonyms")}
                elif kind in {"metric", "claim"}:
                    excerpt = next((m for m in definition.get("metrics", [])
                                    if m.get("name") == name), None)
                    if excerpt:
                        # The meaning first: a long dimension list must not push it out.
                        excerpt = {k: excerpt.get(k) for k in (
                            "name", "description", "expression", "base_dataset", "synonyms",
                            "unit", "additivity", "default_time_dimension")}
                elif kind == "relationship":
                    excerpt = next((r for r in definition.get("relationships", [])
                                    if r.get("name") == name), None)
                if excerpt is not None:
                    parts.append(f"{model['name']}: " + json.dumps(excerpt, default=str)[:900])
            if population[key]["stratum"] == "alias":
                parts.append("Concept definition: " + item["definition"][:600])
            found[key] = " | ".join(parts)[:2000]
        return found

    # --- external reviews ---------------------------------------------------------------

    async def review_results(self, run_id: str, body: ReviewBatch, user: dict) -> dict:
        await access.require_admin("REVIEW", "ONTOLOGY_EVALUATION", run_id, user)
        results = {item.item: item for item in await self.results(run_id)}
        updated = []
        for entry in body.items:
            result = results.get(entry.item)
            if result is None:
                continue
            reviews = [r for r in result.reviews if r.reviewer != body.reviewer]
            reviews.append(Review(reviewer=body.reviewer, kind=body.kind,  # type: ignore[arg-type]
                                  verdict=entry.verdict, reason=entry.reason))  # type: ignore[arg-type]
            updated.append((result.model_copy(update={
                "reviews": reviews, "revision": result.revision + 1}), result.revision))
        if updated:
            await self.repository.save_many("eval_results", updated)
        await access.audit("REVIEW", "ONTOLOGY_EVALUATION", run_id, user)
        reviewed = await self.results(run_id)
        run = await self.repository.latest("eval_runs", run_id, EvalRun)
        if run is not None and run.kind == "joins" and run.status == "COMPLETED":
            from app.modules.ontology.evidence.discovery import assess

            gold = {tuple(pair) for pair in run.summary.get("gold") or []}
            joins, assessed = assess(reviewed, gold)
            changed = [(item, item.revision) for item, old in zip(joins, reviewed, strict=True)
                       if item.detail != old.detail]
            if changed:
                await self.repository.save_many("eval_results", [
                    (item.model_copy(update={"revision": revision + 1}), revision)
                    for item, revision in changed])
            await self._save_run(run, summary={**run.summary, **assessed})
        if run is not None and run.kind == "claims" and run.status == "COMPLETED":
            # A new reviewer changes the consensus; the stored summary follows it.
            await self._save_run(run, summary={
                **run.summary,
                **summarise_claims(reviewed, run.summary.get("population") or {}),
            })
        return {"reviewed": len(updated), "agreement": agreement(reviewed)}

    async def review_cases(self, set_name: str, body: ReviewBatch, user: dict) -> dict:
        await access.require_admin("REVIEW", "ONTOLOGY_EVALUATION", set_name, user)
        cases = {case.id: case for case in await self.cases(set_name)}
        updated = []
        for entry in body.items:
            case = cases.get(entry.item)
            if case is None:
                continue
            reviews = [r for r in case.reviews if r.reviewer != body.reviewer]
            reviews.append(Review(reviewer=body.reviewer, kind=body.kind,  # type: ignore[arg-type]
                                  verdict=entry.verdict, reason=entry.reason))  # type: ignore[arg-type]
            # A case stays in the gold set only while every reviewer confirms it.
            status = case.status
            if case.status == "ACTIVE" and entry.verdict != "yes":
                status = "REJECTED"
            updated.append((case.model_copy(update={
                "reviews": reviews, "status": status, "revision": case.revision + 1,
            }), case.revision))
        if updated:
            await self.repository.save_many("eval_cases", updated)
        await access.audit("REVIEW", "ONTOLOGY_EVALUATION", set_name, user)
        return {"reviewed": len(updated),
                "rejected": sum(1 for case, _ in updated if case.status == "REJECTED")}


# --- summaries --------------------------------------------------------------------------

def _time_context(plan: Any) -> tuple[Any, str | None]:
    """The plan's period pinned to now as Studio pins it, or why it has no data yet."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from app.core.database import configured_timezone
    from app.modules.agents.semantic.coverage import future_period
    from app.modules.agents.semantic.time_ranges import resolve_execution_time

    if plan.time is None or not plan.time.range:
        return None, None
    now = datetime.now(UTC)
    zone = configured_timezone()
    not_yet = future_period(plan, now.astimezone(ZoneInfo(zone)).date())
    if not_yet:
        return None, not_yet
    try:
        return resolve_execution_time(plan.time.range, plan.time.compare,
                                      now=now, timezone=zone), None
    except Exception:  # noqa: BLE001 - an unpinnable period makes no window claim
        return None, None


def summarise_answers(results: list[EvalResult]) -> dict:
    by_kind: dict[str, list[EvalResult]] = {}
    for item in results:
        by_kind.setdefault(str(item.detail.get("kind")), []).append(item)
    answerable = by_kind.get("answerable", [])
    negatives = by_kind.get("unanswerable", []) + by_kind.get("ambiguous", [])
    ambiguous = by_kind.get("ambiguous", [])
    correct = sum(1 for item in answerable if item.outcome == "correct")
    reliability = [int(item.detail.get("reliability") or 0) for item in results]
    wrong = sum(1 for item in results if int(item.detail.get("reliability") or 0) < 0)
    routed = [item for item in answerable if "view_correct" in item.detail]
    return {
        "answer_accuracy": proportion(correct, len(answerable)),
        "view_accuracy": proportion(
            sum(1 for item in routed if item.detail["view_correct"]), len(routed)),
        "reliability": {"value": sum(reliability) / len(reliability) if reliability else None,
                        "n": len(reliability)},
        "wrong_answers": proportion(wrong, len(results)),
        "abstention": proportion(
            sum(1 for item in negatives if item.outcome in {"abstained", "clarified"}),
            len(negatives)),
        "clarification": proportion(
            sum(1 for item in ambiguous if item.outcome == "clarified"), len(ambiguous)),
        "failed": sum(1 for item in results if item.outcome == "failed"),
        "outcomes": dict(Counter(f"{item.detail.get('kind')}:{item.outcome}" for item in results)),
        "latency_ms_p50": sorted(int(item.detail.get("latency_ms") or 0)
                                 for item in results)[len(results) // 2] if results else None,
    }


def consensus(item: EvalResult) -> str:
    """A claim is correct only if no reviewer rejects it: one 'no' outweighs any 'yes'."""
    verdicts: set[str] = {str(review.verdict) for review in item.reviews} or {item.outcome}
    if "no" in verdicts:
        return "no"
    return "yes" if "yes" in verdicts else "unsure"


def summarise_claims(
    results: list[EvalResult], population: dict[str, int], *, model_only: bool = False,
) -> dict:
    strata: dict[str, list[int]] = {}
    pairs = []
    for item in results:
        stratum = str(item.detail.get("stratum"))
        verdict = item.outcome if model_only else consensus(item)
        if verdict not in {"yes", "no"} or stratum == "gone":
            continue
        counts = strata.setdefault(stratum, [0, 0])
        counts[0] += 1
        counts[1] += verdict == "yes"
        confidence = item.detail.get("confidence")
        if isinstance(confidence, int | float):
            pairs.append((float(confidence), verdict == "yes"))
    estimate = stratified({
        name: (int(population.get(name, sampled)), sampled, correct)
        for name, (sampled, correct) in strata.items()
    })
    summary = {
        "claim_accuracy": estimate,
        "by_stratum": {name: proportion(correct, sampled)
                       for name, (sampled, correct) in sorted(strata.items())},
        "unsure": sum(
            1 for item in results
            if (item.outcome if model_only else consensus(item)) == "unsure"),
        "calibration": calibration(pairs),
        "agreement": agreement(results),
        "reviewers": sorted({review.reviewer for item in results for review in item.reviews}),
    }
    if not model_only:
        summary["model_only"] = summarise_claims(results, population, model_only=True)[
            "claim_accuracy"]
    return summary


def agreement(results: list) -> dict:
    """Cohen's kappa between the model judge and every other reviewer, per reviewer."""
    pairs: dict[str, tuple[list[str], list[str]]] = {}
    for item in results:
        reviews = list(getattr(item, "reviews", []) or [])
        # Deterministic checks (``nova:*``) are not a judge; agreement is between judges.
        # The newest model verdict: a re-judged item is compared under the current rubric.
        model = next((r for r in reversed(reviews)
                      if r.kind == "model" and not r.reviewer.startswith("nova:")), None)
        if model is None:
            continue
        for review in reviews:
            if review.kind == "model":
                continue
            first, second = pairs.setdefault(review.reviewer, ([], []))
            first.append(model.verdict)
            second.append(review.verdict)
    return {reviewer: cohen_kappa(first, second) for reviewer, (first, second) in pairs.items()}


def compare(with_ontology: list[EvalResult], without: list[EvalResult]) -> dict:
    """The ontology's effect on the same answerable cases: difference and McNemar."""
    left = {item.item: item.outcome == "correct" for item in without
            if item.detail.get("kind") == "answerable"}
    right = {item.item: item.outcome == "correct" for item in with_ontology
             if item.detail.get("kind") == "answerable"}
    shared = sorted(set(left) & set(right))
    before = [left[key] for key in shared]
    after = [right[key] for key in shared]
    test = mcnemar(before, after)
    return {
        "n": len(shared),
        "without": proportion(sum(before), len(shared)),
        "with": proportion(sum(after), len(shared)),
        "difference": (sum(after) - sum(before)) / len(shared) if shared else None,
        "mcnemar": test,
    }


evaluation_service = EvaluationService()
