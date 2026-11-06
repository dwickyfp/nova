"""Draft concepts and bindings from a published Semantic View.

Bootstrapping only proposes. Every public metric not already named in the
target domain becomes a draft concept with a binding to that metric, and a
steward still has to review the meaning before anything is approved.
"""

from __future__ import annotations

import re

from fastapi import HTTPException
from pydantic import Field

from app.modules.intelligence.contracts import Contract
from app.modules.ontology import access
from app.modules.ontology.contracts import (
    Concept,
    ConceptBody,
    ConceptChange,
    ConceptKind,
    Domain,
    Proposal,
    RepresentationChange,
)
from app.modules.ontology.representations import public_metrics
from app.modules.ontology.stewardship import ProposalCreate, StewardshipService

MAX_METRICS = 100


class BootstrapRequest(Contract):
    view_id: str = Field(min_length=1, max_length=128)
    domain_id: str = Field(min_length=1, max_length=64)
    operation_id: str = Field(min_length=8, max_length=64)


def slug_for(metric: str) -> str:
    words = [word for word in re.split(r"[^A-Za-z0-9]+", metric) if word]
    slug = "".join(word[:1].upper() + word[1:] for word in words) or "Metric"
    return (slug if slug[0].isalpha() else f"M{slug}")[:64]


async def bootstrap(
    body: BootstrapRequest, user: dict, stewardship: StewardshipService
) -> Proposal:
    repository = stewardship.repository
    catalog = stewardship.catalog
    domains = {item.id: item for item in await repository.current("domains", Domain)}
    domain = domains.get(body.domain_id)
    if not access.can_see(domain, domains, user) or domain is None or domain.status != "ACTIVE":
        raise HTTPException(status_code=404, detail=access.NOT_FOUND)
    if not await catalog.visible(body.view_id, user):
        raise HTTPException(status_code=404, detail="Semantic View not found")
    view = await catalog.published(body.view_id)
    if view is None:
        raise HTTPException(
            status_code=409, detail="Publish the Semantic View before bootstrapping"
        )
    taken: set[str] = set()
    for concept in await repository.current("concepts", Concept):
        if concept.domain_id == domain.id and concept.status == "APPROVED":
            names = (concept.slug, concept.label, *concept.aliases)
            taken.update(name.casefold() for name in names)
    changes: list = []
    for name, claim in sorted(public_metrics(view.definition).items())[:MAX_METRICS]:
        slug = slug_for(name)
        if slug.casefold() in taken or name.casefold() in taken:
            continue
        taken.add(slug.casefold())
        aliases = [
            alias for alias in {name, *claim.synonyms}
            if alias.casefold() not in taken and alias.casefold() != slug.casefold()
        ]
        taken.update(alias.casefold() for alias in aliases)
        label = name.replace("_", " ").strip().capitalize() or slug
        description = claim.description.strip()
        index = len(changes)
        changes.append(
            ConceptChange(
                op="create",
                body=ConceptBody(
                    domain_id=domain.id,
                    kind=ConceptKind.METRIC,
                    slug=slug,
                    label=label[:200],
                    definition=(
                        description
                        or f"Draft from {view.name}.{name}; a steward must describe its meaning."
                    )[:8000],
                    aliases=aliases[:32],
                    unit=claim.unit,
                ),
            )
        )
        changes.append(
            RepresentationChange(
                op="create", concept_id=f"@{index}", view_id=view.view_id, metric=name,
            )
        )
    if not changes:
        raise HTTPException(
            status_code=409, detail="Every public metric of this view is already a concept here"
        )
    proposal = await stewardship.create(
        ProposalCreate(
            title=f"Bootstrap {domain.name} from {view.name} v{view.version}",
            rationale=(
                f"Draft concepts for the public metrics of the published Semantic View "
                f"{view.name} version {view.version}. Review each definition before approving."
            ),
            changes=changes,
            operation_id=body.operation_id,
        ),
        user,
        source="BOOTSTRAP",
    )
    await access.audit("BOOTSTRAP", "ONTOLOGY_PROPOSAL", proposal.id, user)
    return proposal
