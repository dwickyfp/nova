"""Incident reads and lifecycle, behind the same News entitlement as the newspaper."""

from __future__ import annotations

from fastapi import APIRouter

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
