"""Supervised business action API; consent is resolved by the existing broker."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.modules.intelligence.action_contracts import (
    Action,
    ActionOperation,
    ActionPreview,
    ActionRead,
    ActionReview,
)
from app.modules.intelligence.actions import action_service, supervised_action
from app.modules.intelligence.responses import IntelligenceResponse, IntelligenceRoute

router = APIRouter(default_response_class=IntelligenceResponse, route_class=IntelligenceRoute)
CurrentUser = Annotated[dict, Depends(get_current_user)]


@router.post("/actions/preview", response_model=Action, status_code=201)
async def preview_action(body: ActionPreview, user: CurrentUser):
    return await action_service.preview(body, user)


@router.get("/actions/{action_id}", response_model=ActionRead)
async def read_action(
    action_id: str,
    user: CurrentUser,
    mission_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    revision: Annotated[int | None, Query(ge=1)] = None,
):
    return await action_service.read(action_id, user, mission_id=mission_id, revision=revision)


@router.post("/actions/{action_id}/review")
async def review_action(action_id: str, body: ActionReview, user: CurrentUser):
    return await action_service.review(action_id, body, user)


@router.post("/actions/{action_id}/execute", response_model=Action)
async def execute_action(action_id: str, body: ActionOperation, user: CurrentUser):
    return await supervised_action(action_id, body, user)


@router.post("/actions/{action_id}/verify", response_model=Action)
async def verify_action(action_id: str, body: ActionOperation, user: CurrentUser):
    from app.modules.agents.router import _require_agent_thread

    action = await action_service.get(action_id, user)
    await _require_agent_thread(body.thread_id, action.configuration.agent_id, user["username"])
    return await action_service.verify(action_id, user, expected_revision=body.expected_revision)


@router.post("/actions/{action_id}/compensate", response_model=Action)
async def compensate_action(action_id: str, body: ActionOperation, user: CurrentUser):
    return await supervised_action(action_id, body, user, compensate=True)


@router.post("/actions/{action_id}/cancel", response_model=Action)
async def cancel_action(action_id: str, body: ActionOperation, user: CurrentUser):
    return await action_service.cancel(action_id, body, user)
