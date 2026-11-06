"""Which approved concept a governed metric observation is about.

News detects changes in Semantic View metrics. A detection is about an
official concept only when the published release has a validated binding to
that view and metric, and the definition the detection used has the same claim
fingerprint as the binding. Anything else is reported as unbound rather than
guessed from names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.modules.intelligence.contracts import SemanticRef
from app.modules.ontology.representations import public_metrics
from app.modules.ontology.service import OntologyService, ontology_service

BindingStatus = Literal["BOUND", "ONTOLOGY_UNBOUND", "NO_RELEASE"]


@dataclass(frozen=True)
class MetricBinding:
    status: BindingStatus
    release_id: str | None = None
    concept_id: str | None = None
    concept_revision: int | None = None
    representation_id: str | None = None


async def bind_metric(
    semantic: SemanticRef,
    metric: str,
    definition: dict,
    *,
    service: OntologyService = ontology_service,
) -> MetricBinding:
    """Bind ``metric`` as defined in ``definition`` to the published release."""
    snapshot = await service.snapshot("current")
    if snapshot.release is None:
        return MetricBinding(status="NO_RELEASE")
    claim = public_metrics(definition).get(metric)
    if claim is not None:
        for item in sorted(snapshot.representations, key=lambda rep: rep.id):
            concept = snapshot.concepts.get(item.concept_id)
            if (
                item.status == "VALIDATED"
                and item.semantic.view_id == semantic.view_id
                and item.metric == metric
                and item.claim_fingerprint == claim.fingerprint
                and concept is not None
                and concept.status == "APPROVED"
            ):
                return MetricBinding(
                    status="BOUND",
                    release_id=snapshot.release.id,
                    concept_id=concept.id,
                    concept_revision=concept.revision,
                    representation_id=item.id,
                )
    return MetricBinding(status="ONTOLOGY_UNBOUND", release_id=snapshot.release.id)


async def linked(
    concept_a: str, concept_b: str, *, service: OntologyService = ontology_service
) -> str | None:
    """The id of an active relation between two concepts in the published release."""
    snapshot = await service.snapshot("current")
    for relation in snapshot.relations:
        if relation.status == "ACTIVE" and {relation.source_id, relation.target_id} == {
            concept_a,
            concept_b,
        }:
            return relation.id
    return None
