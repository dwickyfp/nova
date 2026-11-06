"""Ground a governed metric question on the published business ontology.

Before ``semantic_query`` plans, the question's business terms are resolved to
approved metric concepts. A resolved concept then decides which metric must
answer it, and in which of the agent's Semantic Views:

* two visible definitions of one term: ask which is meant;
* an approved concept with no binding the caller and agent can use: say it
  cannot be computed, instead of falling back to a metric with a similar name;
* concepts computed in different views: ask for one at a time;
* otherwise the view is chosen and the bound metrics are required in the plan.

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
    try:
        resolution = await resolver.resolve(
            question, user, release_id=pinned_release(context), kinds=METRIC_KINDS
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
        status="grounded", resolution=resolution, view_id=view_id, required=required
    )


def missing_metrics(grounding: Grounding, plan_metrics: tuple[str, ...]) -> list[str]:
    return [
        f"{item['qualified_name']} must use metric {metric}"
        for metric, item in grounding.required.items()
        if metric not in plan_metrics
    ]
