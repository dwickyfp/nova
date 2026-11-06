"""Incident reads and lifecycle, behind the same News entitlement as the newspaper."""

from __future__ import annotations

from fastapi import APIRouter

from app.common.news_entitlement import NewsUser
from app.modules.intelligence.business_events import TransitionRequest, business_event_service

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
