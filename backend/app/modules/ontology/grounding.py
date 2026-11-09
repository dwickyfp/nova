"""Ground a governed metric question on the published business ontology.

Before ``semantic_query`` plans, the question's business terms are resolved to
approved metric concepts. A resolved concept then decides which metric must
answer it, and in which of the agent's Semantic Views:

* two visible definitions of one term: ask which is meant;
* an approved concept with no binding the caller and agent can use: say it
  cannot be computed, instead of falling back to a metric with a similar name;
* concepts computed in different views: ask for one at a time;
* otherwise the view is chosen and the bound metrics are required in the plan,
  unless a metric the plan does use explains the mention (see ``grounding_gaps``).

A binding fits a loaded view when the metric's claim fingerprint there equals
the one the steward validated, so an agent pinned to an older view version
still uses the definition that was approved.

Without a published release, or when the question names no metric concept,
nothing changes.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.modules.ontology.contracts import ConceptKind
from app.modules.ontology.representations import public_metrics
from app.modules.ontology.resolver import ConceptResolver, Resolution, concept_resolver

logger = logging.getLogger(__name__)

METRIC_KINDS = frozenset({ConceptKind.METRIC, ConceptKind.MEASURE})


@dataclass
class Grounding:
    """What the ontology requires of one semantic query."""

    status: str
    resolution: Resolution | None = None
    view_id: str | None = None
    required: dict[str, dict[str, Any]] = field(default_factory=dict)
    error_class: str | None = None
    detail: str | None = None
    repair: dict[str, Any] = field(default_factory=dict)
    user_message: str = "The business metric cannot be answered here."
    #: The question as asked; a mention's neighbouring words can explain it.
    question: str = ""

    @property
    def blocking(self) -> bool:
        return self.error_class is not None

    def planner_hints(self) -> list[dict[str, Any]]:
        return [
            {
                "concept": item["qualified_name"],
                "label": item["label"],
                "definition": item["definition"],
                "metric": metric,
            }
            for metric, item in self.required.items()
        ]

    def provenance(self) -> dict[str, Any] | None:
        if self.resolution is None or self.status != "grounded":
            return None
        return {
            "ontology_release_id": self.resolution.release_id,
            "manifest_hash": self.resolution.manifest_hash,
            "concepts": [
                {
                    "id": item["id"],
                    "revision": item["revision"],
                    "qualified_name": item["qualified_name"],
                    "label": item["label"],
                    "definition": item["definition"],
                    "metric": metric,
                    "representation_id": item["representation_id"],
                }
                for metric, item in self.required.items()
            ],
        }


def never_created(exc: Exception) -> bool:
    """The ontology tables do not exist yet, so no release was ever published."""
    code = exc.args[0] if exc.args else None
    message = str(exc.args[1] if len(exc.args) > 1 else exc)
    return code == 5502 and "Unknown table 'NOVA_SYSTEM.CONFIG_ONTOLOGY_" in message


def pinned_release(context: Any) -> str | None:
    """The ontology release this run must use, if one is pinned."""
    manifest = getattr(context, "release_manifest", None) or {}
    pinned = (manifest.get("dependencies") or {}).get("ontology_release") or {}
    return pinned.get("release_id") or getattr(context, "ontology_release_id", None)


async def ground(
    question: str,
    context: Any,
    load_models: Callable[[], Awaitable[list[dict[str, Any]]]],
    *,
    resolver: ConceptResolver = concept_resolver,
) -> Grounding:
    user = getattr(context, "user", None) or {}
    models: list[dict[str, Any]] | None = None
    try:
        resolution = await resolver.resolve(
            question, user, release_id=pinned_release(context), kinds=METRIC_KINDS
        )
        if resolution.status in {"resolved", "ambiguous"}:
            # The agent's own Semantic Views name its business: a term means
            # what it means there, not in another business the role can see.
            models = await load_models()
            resolution = await resolver.resolve(
                question,
                user,
                release_id=resolution.release_id or pinned_release(context),
                kinds=METRIC_KINDS,
                scope_views=[str(model.get("semantic_model_id")) for model in models],
            )
    except Exception as exc:  # noqa: BLE001 - an unreadable ontology must not answer wrongly
        if never_created(exc):
            return Grounding(status="no_release")
        logger.warning("Ontology grounding failed: %s", type(exc).__name__)
        return Grounding(
            status="unavailable",
            error_class="ONTOLOGY_UNAVAILABLE",
            user_message=(
                "Official business definitions cannot be checked right now, so this metric "
                "question cannot be answered safely. Try again later."
            ),
            detail=(
                "The business ontology could not be read, so official metric definitions "
                "cannot be checked. Tell the user and do not answer with a guessed metric."
            ),
        )
    if resolution.status in {"no_release", "none"}:
        return Grounding(status=resolution.status, resolution=resolution)
    if resolution.release_id and hasattr(context, "ontology_release_id"):
        context.ontology_release_id = resolution.release_id  # one release per run
    if resolution.status == "ambiguous":
        return Grounding(
            status="ambiguous",
            resolution=resolution,
            error_class="CLARIFICATION_REQUIRED",
            detail=(
                "The question uses a business term with more than one approved definition: "
                + "; ".join(
                    f"'{item.term}' can mean "
                    + " or ".join(candidate["qualified_name"] for candidate in item.candidates)
                    for item in resolution.ambiguities
                )
                + ". Ask the user which definition they mean, naming the choices. Do not pick one."
            ),
            repair=resolution.public(),
        )

    if models is None:
        models = await load_models()
    by_view: dict[str, dict[str, dict[str, Any]]] = {}
    unbound = []
    for concept in resolution.concepts:
        options = []
        for binding in resolution.bindings.get(concept["id"], []):
            for model in models:
                if str(model.get("semantic_model_id")) != binding.semantic.view_id:
                    continue
                claim = public_metrics(model.get("definition") or {}).get(binding.metric)
                if claim is not None and claim.fingerprint == binding.claim_fingerprint:
                    options.append(binding)
        if not options:
            unbound.append(concept["qualified_name"])
            continue
        for binding in options:
            by_view.setdefault(binding.semantic.view_id, {})[binding.metric] = {
                **concept, "representation_id": binding.id,
                "matched_terms": resolution.terms.get(concept["id"], []),
            }
    if unbound:
        return Grounding(
            status="unbound",
            resolution=resolution,
            error_class="NO_AUTHORIZED_REPRESENTATION",
            detail=(
                ", ".join(unbound)
                + " has an approved definition, but no validated metric this agent and user "
                "may use computes it. Say it cannot be answered here and name the concept; "
                "do not substitute a metric with a similar name."
            ),
            repair={"concepts": unbound},
            user_message=(
                ", ".join(unbound)
                + " has an approved business definition, but no validated metric available "
                "to you computes it, so it cannot be answered here."
            ),
        )
    wanted = len(resolution.concepts)
    complete = [
        view_id for view_id, metrics in by_view.items()
        if len({item["id"] for item in metrics.values()}) == wanted
    ]
    if not complete:
        return Grounding(
            status="split",
            resolution=resolution,
            error_class="CLARIFICATION_REQUIRED",
            detail=(
                "The named business metrics are computed in different Semantic Views. "
                "Query them one at a time."
            ),
            repair=resolution.public(),
        )
    requested = getattr(context, "requested_semantic_view", None)
    view_id = next(
        (
            view_id for view_id in complete
            for model in models
            if str(model.get("semantic_model_id")) == view_id
            and requested in {view_id, str(model.get("name"))}
        ),
        sorted(complete)[0],
    )
    required: dict[str, dict[str, Any]] = {}
    for metric, item in sorted(by_view[view_id].items()):
        if item["id"] not in {chosen["id"] for chosen in required.values()}:
            required[metric] = item
    return Grounding(
        status="grounded", resolution=resolution, view_id=view_id, required=required,
        question=question
    )


def missing_metrics(
    grounding: Grounding, plan_metrics: tuple[str, ...], model: Any = None,
) -> list[str]:
    return grounding_gaps(grounding, plan_metrics, model)[0]


def grounding_gaps(
    grounding: Grounding, plan_metrics: tuple[str, ...], model: Any = None,
) -> tuple[list[str], list[str]]:
    """Required concepts the plan leaves out, and those a planned metric explains.

    A word in a question can name a concept without asking for it: "gross sales
    before *discount*" names Discount amount while asking for Gross revenue. A
    required concept is therefore satisfied when its metric is planned, or when
    a planned metric explains the mention:

    * the concept's metric is an input of a planned metric, proven on the parsed
      expressions (actual distance is part of the distance gap); or
    * the words that named the concept appear, as whole words, in how a planned
      metric describes itself (its description, synonyms, name, or the official
      definition of the concept bound to it).

    Substitution stays refused: "omzet" with gross revenue planned is not
    explained, because gross revenue's own words do not say "omzet". Without the
    view's model the rule is the strict one: every required metric must be planned.
    """
    missing: list[str] = []
    explained: list[str] = []
    for metric, item in grounding.required.items():
        if metric in plan_metrics:
            continue
        reason = _explained(metric, item, grounding, plan_metrics, model) if model else None
        if reason:
            explained.append(f"{item['qualified_name']}: {reason}")
        else:
            missing.append(f"{item['qualified_name']} must use metric {metric}")
    return missing, explained


def _explained(
    metric: str, item: dict[str, Any], grounding: Grounding,
    plan_metrics: tuple[str, ...], model: Any,
) -> str | None:
    from app.modules.ontology.derived import metric_components
    from app.modules.ontology.resolver import normalise

    required = model.metric(metric)
    if required is not None:
        own, _parts = metric_components(required.expression)
        for name in plan_metrics:
            planned = model.metric(name)
            if (
                own and planned is not None and planned.base_dataset == required.base_dataset
                and own in metric_components(planned.expression)[1]
            ):
                return f"an input of planned metric {name}"
    terms = {normalise(term) for term in item.get("matched_terms") or []} - {""}
    if not terms:
        return None
    for name in plan_metrics:
        planned = model.metric(name)
        if planned is None:
            continue
        words = [name.replace("_", " "), planned.description or "", *planned.synonyms]
        official = grounding.required.get(name)
        if official:
            words += [official.get("label") or "", official.get("definition") or "",
                      *(official.get("aliases") or [])]
        text = f" {normalise(' '.join(words))} "
        hit = next((term for term in sorted(terms) if f" {term} " in text), None)
        if hit:
            return f"'{hit}' describes planned metric {name}"
        unit = _unit_of(terms, grounding.question, planned.description or "")
        if unit:
            return f"'per {unit}' is the unit of planned metric {name}"
    return None


def _unit_of(terms: set[str], question: str, description: str) -> str | None:
    """A mention after 'per' that the planned metric's own definition also counts per.

    "rata-rata persentase diskon *per baris pesanan*" names Order lines, but the
    average discount is defined "per baris order": the words give the unit of the asked
    measure, not a second measure. It needs both the question's 'per' and the
    definition's 'per' followed by a word of the mention.
    """
    from app.modules.ontology.resolver import normalise

    asked = f" {normalise(question)} "
    defined = normalise(description).split()
    units = {defined[i + 1] for i, word in enumerate(defined[:-1]) if word == "per"}
    for term in sorted(terms):
        if f" per {term} " in asked and units & {word for word in term.split() if len(word) > 3}:
            return term
    return None
