"""What the Overview shows: measured numbers only, each with its n and interval.

Values come from completed evaluation runs and from the live ontology. A metric
that has not been measured is returned as ``None`` with the reason, never as a
default, so the page can say "not measured yet" instead of showing a guess.
"""

from __future__ import annotations

from collections import Counter

from app.common.statistics import proportion
from app.modules.ontology import access
from app.modules.ontology.contracts import Concept, Domain, Proposal, Representation
from app.modules.ontology.evaluation.contracts import EvalCase, EvalRun
from app.modules.ontology.evaluation.service import EvaluationService, agreement, compare
from app.modules.ontology.evidence.discovery import same_context

#: What the Overview shows of a join run: scores, timing, calibration and decisions.
JOIN_FIELDS = (
    "databases", "profiled_columns", "profile_seconds", "discover_seconds", "candidates",
    "joins", "joins_reviewed", "undeclared_reviewed", "calibration", "decisions", "drift",
    "history",
)


class _OnceCatalog:
    """The view catalog for one Overview request: each view's visibility is checked once.

    The derived layer and the meaning checks both ask whether the caller may see the same
    views, and each check reads source privileges. Within one request, for one caller, the
    answer cannot change, so it is asked once per view.
    """

    def __init__(self, catalog) -> None:
        self._catalog = catalog
        self._visible: dict[tuple[str, str], bool] = {}

    async def visible(self, view_id: str, user: dict) -> bool:
        key = (view_id, str(user.get("session_id") or user.get("username")))
        if key not in self._visible:
            self._visible[key] = await self._catalog.visible(view_id, user)
        return self._visible[key]

    def __getattr__(self, name: str):
        return getattr(self._catalog, name)


class _OnceRepository:
    """The ontology repository for one Overview request: each kind is read once.

    The layer, the counts and the meaning checks all read the current domains,
    concepts and representations, and each read is a full revision-window scan
    of control metadata. Within one request the same snapshot serves them all.
    """

    def __init__(self, repository) -> None:
        self._repository = repository
        self._current: dict[tuple[str, type], list] = {}

    async def current(self, kind: str, model: type) -> list:
        key = (kind, model)
        if key not in self._current:
            self._current[key] = await self._repository.current(kind, model)
        return list(self._current[key])

    def __getattr__(self, name: str):
        return getattr(self._repository, name)


def _when(run: EvalRun | None) -> str | None:
    return run.updated_at.isoformat() if run else None


async def overview(user: dict, service: EvaluationService) -> dict:
    from app.modules.ontology.derived import visible_layer
    from app.modules.ontology.lint import lint

    repository = _OnceRepository(service.repository)
    runs = [run for run in await service.runs() if run.status == "COMPLETED"]
    answers = [run for run in runs if run.kind == "answers"]
    with_runs = [run for run in answers if run.mode == "with_ontology"]
    latest = with_runs[0] if with_runs else None
    baseline = next(
        (run for run in answers if run.mode == "without_ontology" and latest
         and run.set_name == latest.set_name and run.version == latest.version),
        None,
    )
    claims = next((run for run in runs if run.kind == "claims"), None)
    # A join run describes what one principal could see: only that context sees it here.
    joins = next((run for run in runs if run.kind == "joins" and same_context(run, user)), None)

    effect = None
    grounded = None
    if latest is not None:
        with_results = await service.results(latest.id)
        answerable = [item for item in with_results if item.detail.get("kind") == "answerable"]
        grounded = proportion(
            sum(1 for item in answerable if item.detail.get("grounding") == "grounded"),
            len(answerable))
        if baseline is not None:
            effect = compare(with_results, await service.results(baseline.id))

    gold = None
    if latest is not None:
        cases = await service.cases(latest.set_name, latest.version)
        gold = {
            "set_name": latest.set_name, "version": latest.version,
            "active": Counter(case.kind for case in cases if case.status == "ACTIVE"),
            "rejected": sum(1 for case in cases if case.status == "REJECTED"),
            "agreement": agreement(cases),
        }

    catalog = _OnceCatalog(service.catalog)
    layer = await visible_layer(user, catalog, repository)
    derived_metrics = [item for item in layer["items"] if item["concept_kind"] == "METRIC"]
    domains = {item.id: item for item in await repository.current("domains", Domain)}
    concepts = {
        item.id: item for item in await repository.current("concepts", Concept)
        if item.status == "APPROVED" and access.can_see(domains.get(item.domain_id), domains, user)
    }
    bindings = [item for item in await repository.current("representations", Representation)
                if item.concept_id in concepts]
    by_domain: dict[str, dict[str, int]] = {}
    for concept in concepts.values():
        domain = domains.get(concept.domain_id)
        name = access.qualified_path(domain, domains) if domain else concept.domain_id
        counts = by_domain.setdefault(name, {"concepts": 0, "metrics": 0, "bound": 0})
        counts["concepts"] += 1
        if concept.kind.value == "METRIC":
            counts["metrics"] += 1
            if any(b.concept_id == concept.id and b.status == "VALIDATED" for b in bindings):
                counts["bound"] += 1
    findings = await lint(user, catalog, repository, layer=layer)
    proposals = [item for item in await repository.current("proposals", Proposal)
                 if item.source == "ENGINE"]
    decided = Counter(item.status for item in proposals)

    history = [
        {
            "run_id": run.id, "release_id": run.release_id, "at": _when(run),
            "set": f"{run.set_name} v{run.version}",
            "answer_accuracy": run.summary.get("answer_accuracy"),
            "reliability": run.summary.get("reliability"),
        }
        for run in reversed(with_runs[:12])
    ]
    return {
        "measured": {
            "answers": {"run_id": latest.id, "at": _when(latest), "release_id": latest.release_id,
                        "set": f"{latest.set_name} v{latest.version}", **latest.summary}
            if latest else None,
            "baseline": {"run_id": baseline.id, "at": _when(baseline), **baseline.summary}
            if baseline else None,
            "effect": effect,
            "claims": {"run_id": claims.id, "at": _when(claims), **claims.summary}
            if claims else None,
            "gold": gold,
            "joins": {"run_id": joins.id, "at": _when(joins),
                      **{key: joins.summary.get(key) for key in JOIN_FIELDS}}
            if joins else None,
            "history": history,
        },
        "live": {
            "metric_coverage": proportion(
                sum(1 for item in derived_metrics if item["official_concept"]),
                len(derived_metrics)),
            "question_grounding": grounded,
            "domains": [{"domain": name, **counts} for name, counts in sorted(by_domain.items())],
            "stale_bindings": sum(1 for item in bindings if item.status == "STALE"),
            "checks": dict(Counter(item.severity for item in findings)),
            "engine_proposals": dict(decided),
            "engine_precision": proportion(
                decided.get("APPLIED", 0), decided.get("APPLIED", 0) + decided.get("REJECTED", 0)),
        },
        "not_measured": [
            reason for reason, missing in (
                ("Answer accuracy needs a completed run with the ontology", latest is None),
                ("The ontology's effect needs a run without it on the same gold set",
                 baseline is None),
                ("Claim accuracy and calibration need a completed claim audit", claims is None),
                ("Join precision and automatic decisions need a completed join discovery",
                 joins is None),
            ) if missing
        ],
    }


def cases_payload(cases: list[EvalCase]) -> list[dict]:
    return [case.model_dump(mode="json", exclude={"actor"}) for case in cases]
