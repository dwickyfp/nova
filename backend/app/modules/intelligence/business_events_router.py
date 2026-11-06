"""Incident reads and lifecycle, behind the same News entitlement as the newspaper."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.common.news_entitlement import NewsUser
from app.modules.intelligence.business_events import (
    TransitionRequest,
    UpdateReviewRequest,
    business_event_service,
)

router = APIRouter()


@router.get("/incidents")
async def list_incidents(user: NewsUser, include_closed: bool = False):
    return await business_event_service.list(user, include_closed=include_closed)


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str, user: NewsUser):
    return await business_event_service.get(incident_id, user)


@router.post("/incidents/{incident_id}/transition")
async def transition_incident(incident_id: str, body: TransitionRequest, user: NewsUser):
    return await business_event_service.transition(incident_id, body, user)


class FindingSubmission(BaseModel):
    draft: str = Field(min_length=1, max_length=64_000)


@router.post("/incidents/{incident_id}/updates", status_code=201)
async def submit_finding(incident_id: str, body: FindingSubmission, user: NewsUser):
    """Post a finding Smart prepared; the user's click is the consent."""
    from fastapi import HTTPException

    from app.modules.intelligence import update_drafts
    from app.modules.intelligence.investigation_contracts import UpdateSubmission

    try:
        draft = update_drafts.verify(body.draft, user)
        if draft.incident_id != incident_id:
            raise HTTPException(status_code=403, detail=update_drafts.UNUSABLE)
    except HTTPException:
        await business_event_service.audit_refusal(user, "POST_INVESTIGATION_UPDATE", incident_id)
        raise
    update = await business_event_service.submit_update(
        incident_id,
        UpdateSubmission(summary=draft.summary, claims=draft.claims),
        user,
        evidence=draft.evidence,
        operation_id=draft.operation_id,
        mission_id=draft.mission_id,
    )
    return {"update_id": update.id, "status": update.status}


@router.post("/incidents/{incident_id}/updates/{update_id}/review")
async def review_update(
    incident_id: str, update_id: str, body: UpdateReviewRequest, user: NewsUser
):
    return await business_event_service.review_update(incident_id, update_id, body, user)


@router.get("/stories/{story_id}/incident")
async def story_incident(story_id: str, user: NewsUser):
    return {"incident": await business_event_service.incident_for_story(story_id, user)}


@router.post("/stories/{story_id}/investigate")
async def investigate_story(story_id: str, user: NewsUser):
    from app.modules.intelligence.investigations import investigate

    return await investigate(story_id, user)
