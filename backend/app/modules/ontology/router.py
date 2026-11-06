"""HTTP API of the Supernova Business Ontology, mounted at ``/ontology``."""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.modules.ontology import access
from app.modules.ontology.bootstrap import BootstrapRequest, bootstrap
from app.modules.ontology.contracts import Proposal, Release
from app.modules.ontology.releases import AliasMove, ReleasePrepare, release_service
from app.modules.ontology.service import DomainCreate, DomainUpdate, Scope, ontology_service
from app.modules.ontology.stewardship import ProposalCreate, ReviewRequest, stewardship_service

router = APIRouter()
CurrentUser = Annotated[dict, Depends(get_current_user)]
ScopeQuery = Annotated[Scope, Query()]


def _proposal(proposal: Proposal) -> dict:
    return proposal.model_dump(mode="json", exclude={"request_digest"})


def _release(release: Release, user: dict) -> dict:
    """Member ids are visible to administrators only; others see counts."""
    summary = release.model_dump(mode="json", exclude={"members"})
    summary["member_counts"] = dict(Counter(member.kind for member in release.members))
    if access.is_admin(user):
        summary["members"] = [member.model_dump() for member in release.members]
    else:
        summary.pop("issues", None)
        summary.pop("warnings", None)
    return summary


@router.get("/domains")
async def list_domains(user: CurrentUser):
    return await ontology_service.list_domains(user)


@router.post("/domains", status_code=201)
async def create_domain(body: DomainCreate, user: CurrentUser):
    return await ontology_service.create_domain(body, user)


@router.put("/domains/{domain_id}")
async def update_domain(domain_id: str, body: DomainUpdate, user: CurrentUser):
    return await ontology_service.update_domain(domain_id, body, user)


@router.get("/concepts")
async def list_concepts(
    user: CurrentUser,
    scope: ScopeQuery = "current",
    domain_id: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    include_deprecated: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
):
    return await ontology_service.list_concepts(
        user, scope=scope, domain_id=domain_id, query=q,
        include_deprecated=include_deprecated, limit=limit,
    )


@router.get("/concepts/{concept_id}")
async def get_concept(concept_id: str, user: CurrentUser, scope: ScopeQuery = "current"):
    return await ontology_service.get_concept(concept_id, user, scope=scope)


@router.get("/concepts/{concept_id}/related")
async def related_concepts(
    concept_id: str,
    user: CurrentUser,
    scope: ScopeQuery = "current",
    depth: Annotated[int, Query(ge=1, le=2)] = 1,
):
    return await ontology_service.related(concept_id, user, scope=scope, depth=depth)


@router.get("/concepts/{concept_id}/representations")
async def concept_representations(
    concept_id: str, user: CurrentUser, scope: ScopeQuery = "current"
):
    return await ontology_service.representations(concept_id, user, scope=scope)


@router.get("/proposals")
async def list_proposals(
    user: CurrentUser,
    status: Literal["PENDING", "APPROVED", "APPLIED", "REJECTED", "WITHDRAWN"] | None = None,
):
    return [_proposal(item) for item in await stewardship_service.list(user, status)]


@router.post("/proposals", status_code=201)
async def create_proposal(body: ProposalCreate, user: CurrentUser):
    return _proposal(await stewardship_service.create(body, user))


@router.post("/bootstrap", status_code=201)
async def bootstrap_proposal(body: BootstrapRequest, user: CurrentUser):
    return _proposal(await bootstrap(body, user, stewardship_service))


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str, user: CurrentUser):
    return _proposal(await stewardship_service.get(proposal_id, user))


@router.post("/proposals/{proposal_id}/review")
async def review_proposal(proposal_id: str, body: ReviewRequest, user: CurrentUser):
    return _proposal(await stewardship_service.review(proposal_id, body, user))


@router.post("/proposals/{proposal_id}/apply")
async def apply_proposal(proposal_id: str, user: CurrentUser):
    return _proposal(await stewardship_service.apply(proposal_id, user))


@router.post("/proposals/{proposal_id}/withdraw")
async def withdraw_proposal(proposal_id: str, user: CurrentUser):
    return _proposal(await stewardship_service.withdraw(proposal_id, user))


@router.get("/releases")
async def list_releases(user: CurrentUser):
    return [_release(item, user) for item in await release_service.list()]


@router.get("/releases/current")
async def current_release(user: CurrentUser):
    alias = await release_service.alias()
    if alias is None:
        return {"release": None, "previous_release_id": None}
    release = await release_service.get(alias.release_id)
    return {
        "release": _release(release, user),
        "previous_release_id": alias.previous_release_id,
        "reason": alias.reason,
        "moved_at": alias.updated_at,
    }


@router.post("/releases", status_code=201)
async def prepare_release(body: ReleasePrepare, user: CurrentUser):
    return _release(await release_service.prepare(body, user), user)


@router.get("/releases/{release_id}")
async def get_release(release_id: str, user: CurrentUser):
    return _release(await release_service.get(release_id), user)


@router.post("/releases/{release_id}/publish")
async def publish_release(release_id: str, user: CurrentUser):
    return _release(await release_service.publish(release_id, user), user)


@router.post("/releases/{release_id}/rollback")
async def rollback_release(release_id: str, body: AliasMove, user: CurrentUser):
    alias = await release_service.rollback(release_id, body, user)
    return {"release_id": alias.release_id, "previous_release_id": alias.previous_release_id}
