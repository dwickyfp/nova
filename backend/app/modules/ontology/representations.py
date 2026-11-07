"""Bindings from approved concepts to published semantic metrics.

A representation pins one Semantic View version, one public metric and the
metric's claim fingerprint (dataset, expression, unit, time dimension, filter
and type). When the view is republished, an unchanged claim is re-pinned to the
new version; a changed or missing claim makes the binding STALE, and a stale
binding is never used to answer with that concept.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.intelligence.context_graph import _claim_fingerprint
from app.modules.intelligence.contracts import SemanticRef, fingerprint, utc_now
from app.modules.ontology.contracts import (
    Actor,
    Concept,
    ConceptKind,
    Representation,
    RepresentationChange,
)

logger = logging.getLogger(__name__)

BINDABLE = frozenset({ConceptKind.METRIC, ConceptKind.MEASURE})


@dataclass(frozen=True)
class PublishedView:
    view_id: str
    name: str
    version: int
    fingerprint: str
    definition: dict


class SemanticCatalog(Protocol):
    async def published(self, view_id: str) -> PublishedView | None: ...

    async def visible(self, view_id: str, user: dict) -> bool: ...

    async def published_ids(self, user: dict) -> list[str]: ...

    async def version(self, view_id: str, version: int) -> PublishedView | None: ...


class SemanticViewCatalog:
    """Published Semantic Views, read through their owning service."""

    async def published(self, view_id: str) -> PublishedView | None:
        from app.modules.intelligence.semantic_views import semantic_view_service

        view = await semantic_view_service._get(view_id)
        if not view or view.get("status") != "ACTIVE" or not view.get("active_version"):
            return None
        row = await semantic_view_service._version(view_id, view["active_version"])
        if not row or row["status"] != "ACTIVE":
            return None
        return PublishedView(
            view_id=view_id,
            name=view["name"],
            version=row["version"],
            fingerprint=row["fingerprint"],
            definition=row["definition"],
        )

    async def visible(self, view_id: str, user: dict) -> bool:
        from app.modules.intelligence.semantic_views import semantic_view_service

        return await semantic_view_service.is_visible(view_id, user)

    async def version(self, view_id: str, version: int) -> PublishedView | None:
        """One version of a view, published now or before."""
        from app.modules.intelligence.semantic_views import semantic_view_service

        view = await semantic_view_service._get(view_id)
        row = await semantic_view_service._version(view_id, version) if view else None
        if not view or not row:
            return None
        return PublishedView(
            view_id=view_id, name=view["name"], version=row["version"],
            fingerprint=row["fingerprint"], definition=row["definition"],
        )

    async def published_ids(self, user: dict) -> list[str]:
        """Ids of the published views ``user`` can see."""
        from app.modules.intelligence.semantic_views import semantic_view_service

        return [
            str(view["id"]) for view in await semantic_view_service.list(user)
            if view.get("status") == "ACTIVE" and view.get("active_version")
        ]


@dataclass(frozen=True)
class Claim:
    fingerprint: str
    unit: str | None
    description: str
    synonyms: tuple[str, ...]
    expression: str


def public_metrics(definition: dict) -> dict[str, Claim]:
    model = SemanticModelIR.from_ossie(definition)
    raw = {item.get("name"): item for item in definition.get("metrics", [])}
    return {
        metric.name: Claim(
            fingerprint=_claim_fingerprint(raw.get(metric.name, {})),
            unit=metric.unit,
            description=metric.description,
            synonyms=metric.synonyms,
            expression=metric.expression,
        )
        for metric in model.metrics
        if metric.visibility == "public"
    }


def meaning_fingerprint(definition: dict, metric_name: str) -> str | None:
    """What a metric computes, with every name resolved to physical tables and columns.

    A claim fingerprint changes when a dataset, field or time dimension is only
    renamed (``route_date`` exposing ``plan_date``). This one does not: it reads
    the base table, the expression, the time column, the filter, the unit and
    the type after resolving field aliases. ``None`` when it cannot be resolved,
    which callers treat as a real change.
    """
    from app.modules.agents.semantic.expressions import qualify_expression

    try:
        model = SemanticModelIR.from_ossie(definition)
        metric = model.metric(metric_name)
        raw = next(
            (item for item in definition.get("metrics") or []
             if isinstance(item, dict) and item.get("name") == metric_name),
            None,
        )
        base = model.dataset(metric.base_dataset) if metric else None
        if metric is None or raw is None or base is None:
            return None
        sources = {item.name: (item.source or item.name).casefold() for item in model.datasets}

        def physical(expression: str, dataset: str) -> str:
            text = qualify_expression(expression, dataset, model)
            for name, source in sorted(sources.items(), key=lambda pair: -len(pair[0])):
                text = text.replace(f"`{name}`.", f"{source}.")
            return text.replace("`", "").replace(" ", "").casefold()

        time = None
        if metric.default_time_dimension:
            dataset_name, _, field_name = metric.default_time_dimension.rpartition(".")
            dataset = model.dataset(dataset_name or base.name)
            field = dataset.field(field_name) if dataset else None
            if dataset is None or field is None:
                return None
            time = physical(field.expression, dataset.name)
        raw_filter = raw.get("filter")
        return fingerprint({
            "source": sources[base.name],
            "expression": physical(metric.expression, base.name),
            "time": time,
            "filter": physical(raw_filter, base.name) if isinstance(raw_filter, str)
            and raw_filter.strip() else raw_filter,
            "unit": metric.unit,
            "type": raw.get("type"),
        })
    except Exception:  # noqa: BLE001 - unresolvable meaning is never treated as unchanged
        return None


def _units_compatible(concept_unit: str | None, metric_unit: str | None) -> bool:
    return not concept_unit or not metric_unit or concept_unit.casefold() == metric_unit.casefold()


async def build(
    change: RepresentationChange,
    concept: Concept | None,
    catalog: SemanticCatalog,
    actor: Actor,
    proposal_id: str,
) -> tuple[Representation | None, list[str]]:
    """Validate a new binding against the currently published view."""
    if concept is None or concept.status != "APPROVED":
        return None, [f"Representation concept {change.concept_id} is not an approved concept"]
    if concept.kind not in BINDABLE:
        return None, [f"{concept.kind.value} concepts cannot bind to a metric"]
    view = await catalog.published(change.view_id)
    if view is None:
        return None, [f"Semantic View {change.view_id} has no published version"]
    claim = public_metrics(view.definition).get(change.metric)
    if claim is None:
        return None, [f"{view.name} has no public metric {change.metric}"]
    if not _units_compatible(concept.unit, claim.unit):
        return None, [
            f"{view.name}.{change.metric} is in {claim.unit}, "
            f"but {concept.slug} is in {concept.unit}"
        ]
    return (
        Representation(
            id=change.representation_id,
            actor=actor,
            concept_id=concept.id,
            semantic=SemanticRef(
                view_id=view.view_id, version=view.version, fingerprint=view.fingerprint
            ),
            metric=change.metric,
            claim_fingerprint=claim.fingerprint,
            purpose=change.purpose,
            unit=claim.unit,
            proposal_id=proposal_id,
        ),
        [],
    )


Freshness = Literal["current", "repin", "stale"]


async def freshness(
    representation: Representation, catalog: SemanticCatalog
) -> tuple[Freshness, PublishedView | None, str | None]:
    """Compare a binding with what its view publishes now."""
    view = await catalog.published(representation.semantic.view_id)
    if view is None:
        return "stale", None, "The semantic view is no longer published"
    if (
        view.version == representation.semantic.version
        and view.fingerprint == representation.semantic.fingerprint
    ):
        return "current", view, None
    claim = public_metrics(view.definition).get(representation.metric)
    if claim is None:
        return "stale", view, f"Version {view.version} no longer publishes {representation.metric}"
    if claim.fingerprint != representation.claim_fingerprint:
        if await _same_meaning(representation, view, catalog):
            return (
                "repin", view,
                f"Version {view.version} renamed or redescribed {representation.metric}; "
                "it computes the same thing",
            )
        return (
            "stale", view,
            f"Version {view.version} changed the definition of {representation.metric}",
        )
    # The claim names fields; a field kept under its name can still read another column.
    if await _same_meaning(representation, view, catalog, unknown=True) is False:
        return (
            "stale", view,
            f"Version {view.version} changed what {representation.metric} computes",
        )
    return "repin", view, None


async def _same_meaning(
    representation: Representation,
    view: PublishedView,
    catalog: SemanticCatalog,
    *,
    unknown: bool = False,
) -> bool:
    """Whether the validated version and ``view`` compute the metric identically.

    ``unknown`` is returned when either side cannot be resolved.
    """
    reader = getattr(catalog, "version", None)
    validated = (
        await reader(representation.semantic.view_id, representation.semantic.version)
        if reader is not None else None
    )
    if validated is None:
        return unknown
    before = meaning_fingerprint(validated.definition, representation.metric)
    after = meaning_fingerprint(view.definition, representation.metric)
    if before is None or after is None:
        return unknown
    return before == after


def repinned(
    representation: Representation,
    view: PublishedView,
    actor: Actor,
    reason: str | None = None,
) -> Representation:
    """Pin the binding to ``view``; a cosmetic change also takes the new claim."""
    claim = public_metrics(view.definition).get(representation.metric)
    return representation.model_copy(
        update={
            "actor": actor,
            "semantic": SemanticRef(
                view_id=view.view_id, version=view.version, fingerprint=view.fingerprint
            ),
            "claim_fingerprint": claim.fingerprint if claim else representation.claim_fingerprint,
            "status_reason": reason,
            "validated_at": utc_now(),
        }
    )


def staled(representation: Representation, reason: str, actor: Actor) -> Representation:
    return representation.model_copy(
        update={"actor": actor, "status": "STALE", "status_reason": reason}
    )


async def refresh_view(
    view_id: str,
    actor: Actor,
    catalog: SemanticCatalog | None = None,
    repository=None,
) -> dict[str, int]:
    """Re-pin or stale every validated binding to ``view_id``."""
    from app.modules.ontology.repository import ontology_repository

    catalog = catalog or SemanticViewCatalog()
    repository = repository or ontology_repository
    counts = {"repinned": 0, "stale": 0, "cosmetic": 0}
    for representation in await repository.current("representations", Representation):
        if representation.semantic.view_id != view_id or representation.status != "VALIDATED":
            continue
        state, view, reason = await freshness(representation, catalog)
        if state == "repin" and view is not None:
            await repository.save(
                "representations",
                repinned(representation, view, actor, reason),
                expected_revision=representation.revision,
            )
            counts["repinned"] += 1
            counts["cosmetic"] += reason is not None
        elif state == "stale":
            await repository.save(
                "representations",
                staled(representation, reason or "Semantic definition changed", actor),
                expected_revision=representation.revision,
            )
            counts["stale"] += 1
    return counts


async def refresh_after_publish(view_id: str, user: dict) -> None:
    """Called once a Semantic View version is published.

    Publication already succeeded; a failure here is logged, and readers still
    re-check freshness before they use a binding.
    """
    try:
        counts = await refresh_view(view_id, Actor.from_user(user))
    except Exception as exc:  # noqa: BLE001 - publication must not be undone by drift bookkeeping
        logger.warning("Ontology drift check failed for %s: %s", view_id, type(exc).__name__)
        return
    if counts["repinned"] or counts["stale"]:
        logger.info("Ontology bindings after publishing %s: %s", view_id, counts)
    if counts["cosmetic"]:
        from app.modules.ontology import access

        await access.audit(
            "REPIN", "ONTOLOGY_REPRESENTATION", view_id, user,
            error=f"{counts['cosmetic']} binding(s) kept: the metric computes the same thing",
        )
    from app.modules.ontology.derived import compile_after_publish

    await compile_after_publish(view_id, user)
