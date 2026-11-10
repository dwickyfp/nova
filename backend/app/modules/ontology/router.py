"""HTTP API of the Supernova Business Ontology, mounted at ``/ontology``."""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import get_current_user
from app.modules.intelligence.contracts import Contract
from app.modules.ontology import access
from app.modules.ontology.bootstrap import BootstrapRequest, bootstrap
from app.modules.ontology.contracts import Actor, Proposal, Release
from app.modules.ontology.derived import compile_view, visible_layer
from app.modules.ontology.enrichment import EnrichRequest, enrich
from app.modules.ontology.evaluation.service import (
    AnswerRunRequest,
    ClaimRunRequest,
    GoldRequest,
    JudgeRunRequest,
    ReviewBatch,
)
from app.modules.ontology.evidence.discovery import JoinRunRequest
from app.modules.ontology.learning import LearnRequest
from app.modules.ontology.osi import OsiImport
from app.modules.ontology.promotion import PromotionRequest, propose_from_derived
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


@router.get("/resolve")
async def resolve_terms(
    user: CurrentUser,
    q: Annotated[str, Query(min_length=1, max_length=2000)],
    release_id: Annotated[str | None, Query(max_length=64)] = None,
):
    from app.modules.ontology.resolver import ConceptResolver

    resolver = ConceptResolver(ontology_service)
    # A person reads this page, so every visible concept is listed.
    return (
        await resolver.resolve(q, user, release_id=release_id, for_model=False)
    ).public()


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


class DerivedCompile(Contract):
    #: One published view; omitted, every published view the caller can see.
    view_id: str | None = None


@router.get("/derived")
async def derived_layer(user: CurrentUser):
    """What Nova derived from the published views the caller can see."""
    return await visible_layer(
        user, stewardship_service.catalog, stewardship_service.repository
    )


@router.post("/derived/compile")
async def compile_derived(body: DerivedCompile, user: CurrentUser):
    """Recompile the derived layer now; publishing a view already does this."""
    catalog = stewardship_service.catalog
    if body.view_id is not None:
        if not await catalog.visible(body.view_id, user):
            raise HTTPException(status_code=404, detail="Semantic View not found")
        view_ids = [body.view_id]
    else:
        view_ids = await catalog.published_ids(user)
    compilations = []
    for view_id in view_ids:
        view = await catalog.published(view_id)
        if view is not None:
            compilations.append(await compile_view(
                view, Actor.from_user(user), stewardship_service.repository
            ))
    await access.audit("DERIVE", "ONTOLOGY_DERIVED", (body.view_id or "all")[:128], user)
    return {
        "compilations": [item.model_dump(mode="json", exclude={"actor"}) for item in compilations]
    }


@router.get("/metrics")
async def ontology_metrics(user: CurrentUser):
    from app.modules.ontology.evaluation.metrics import overview
    from app.modules.ontology.evaluation.service import evaluation_service

    return await overview(user, evaluation_service)


@router.get("/evaluation/runs")
async def evaluation_runs(user: CurrentUser):
    from app.modules.ontology.evaluation.service import evaluation_service

    await access.require_admin("READ", "ONTOLOGY_EVALUATION", "runs", user)
    from app.modules.ontology.evidence.discovery import same_context

    # A join run describes the tables one principal could see; others do not list it.
    return [run.model_dump(mode="json", exclude={"items"}) | {"total": len(run.items)}
            for run in await evaluation_service.runs()
            if run.kind != "joins" or same_context(run, user)]


@router.post("/evaluation/gold", status_code=201)
async def evaluation_gold(body: GoldRequest, user: CurrentUser):
    from app.modules.ontology.evaluation.service import evaluation_service

    return _run_view(await evaluation_service.start_gold(body, user))


@router.post("/evaluation/answers", status_code=201)
async def evaluation_answers(body: AnswerRunRequest, user: CurrentUser):
    from app.modules.ontology.evaluation.service import evaluation_service

    return _run_view(await evaluation_service.start_answers(body, user))


@router.post("/evaluation/claims", status_code=201)
async def evaluation_claims(body: ClaimRunRequest, user: CurrentUser):
    from app.modules.ontology.evaluation.service import evaluation_service

    return _run_view(await evaluation_service.start_claims(body, user))


@router.post("/learn", status_code=201)
async def learn_from_evidence(body: LearnRequest, user: CurrentUser):
    """Steward proposals from the claim audit and from failed and refused gold questions."""
    from app.modules.ontology.evaluation.service import evaluation_service
    from app.modules.ontology.learning import learn

    return await learn(body, user, evaluation_service, stewardship_service)


@router.get("/osi/export")
async def osi_export(user: CurrentUser, release_id: str | None = None):
    """The release as Ossie 0.1.1 documents, one per bound Semantic View the caller may see."""
    from app.modules.ontology.osi import export_release

    return await export_release(user, release_id)


@router.post("/osi/import")
async def osi_import(body: OsiImport, user: CurrentUser):
    """Read exported documents back and prove whether the release survived intact."""
    from app.modules.ontology.osi import read_documents

    await access.require_admin("READ", "ONTOLOGY_RELEASE", "osi", user)
    return read_documents(body.documents)


@router.post("/evaluation/judge", status_code=201)
async def evaluation_judge(body: JudgeRunRequest, user: CurrentUser):
    from app.modules.ontology.evaluation.service import evaluation_service

    return _run_view(await evaluation_service.start_judge(body, user))


@router.post("/evaluation/joins", status_code=201)
async def evaluation_joins(body: JoinRunRequest, user: CurrentUser):
    from app.modules.ontology.evaluation.service import evaluation_service

    return _run_view(await evaluation_service.start_joins(body, user))


@router.post("/evaluation/runs/{run_id}/step")
async def evaluation_step(run_id: str, user: CurrentUser):
    from app.modules.ontology.evaluation.service import evaluation_service

    return _run_view(await evaluation_service.step(run_id, user))


@router.post("/evaluation/runs/{run_id}/resume")
async def evaluation_resume(run_id: str, user: CurrentUser):
    from app.modules.ontology.evaluation.service import evaluation_service

    return _run_view(await evaluation_service.resume(run_id, user))


@router.get("/evaluation/runs/{run_id}/results")
async def evaluation_results(run_id: str, user: CurrentUser):
    from app.modules.ontology.evaluation.service import evaluation_service

    await access.require_admin("READ", "ONTOLOGY_EVALUATION", run_id, user)
    return [item.model_dump(mode="json", exclude={"actor"})
            for item in await evaluation_service.visible_results(run_id, user)]


@router.post("/evaluation/runs/{run_id}/reviews")
async def evaluation_review_results(run_id: str, body: ReviewBatch, user: CurrentUser):
    from app.modules.ontology.evaluation.service import evaluation_service

    return await evaluation_service.review_results(run_id, body, user)


@router.get("/evaluation/cases")
async def evaluation_cases(user: CurrentUser, set_name: str, version: int | None = None):
    from app.modules.ontology.evaluation.metrics import cases_payload
    from app.modules.ontology.evaluation.service import evaluation_service

    await access.require_admin("READ", "ONTOLOGY_EVALUATION", set_name, user)
    return cases_payload(await evaluation_service.cases(set_name, version))


@router.post("/evaluation/cases/{set_name}/reviews")
async def evaluation_review_cases(set_name: str, body: ReviewBatch, user: CurrentUser):
    from app.modules.ontology.evaluation.service import evaluation_service

    return await evaluation_service.review_cases(set_name, body, user)


def _run_view(run) -> dict:
    return run.model_dump(mode="json", exclude={"items"}) | {"total": len(run.items)}


@router.post("/enrich", status_code=201)
async def suggest_names(body: EnrichRequest, user: CurrentUser):
    return await enrich(body, user, stewardship_service)


@router.get("/lint")
async def meaning_checks(user: CurrentUser):
    from app.modules.ontology.lint import lint_report

    return await lint_report(user, ontology_service.catalog, ontology_service.repository)


@router.post("/derived/propose", status_code=201)
async def propose_derived(body: PromotionRequest, user: CurrentUser):
    """One proposal per domain for what Nova derived but no steward approved yet."""
    return await propose_from_derived(body, user, stewardship_service)


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
