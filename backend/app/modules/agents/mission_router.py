"""Authenticated Studio Mission and deliverable endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from app.core.deps import get_current_user
from app.modules.agents.mission import mission_service
from app.modules.agents.mission_schema import (
    DeliverableCreate,
    MissionCreate,
    MissionLink,
    MissionOperation,
    MissionResume,
    ObjectRef,
)
from app.modules.agents.public_projections import (
    PublicDeliverableList,
    PublicMission,
    PublicMissionDeliverable,
    PublicMissionList,
    PublicResumableMission,
    PublicResumableMissionList,
    public_mission,
)
from app.modules.intelligence.contracts import Contract
from app.modules.intelligence.public_projections import (
    PublicAction,
    PublicDecision,
    PublicInvestigation,
    PublicInvestigationContext,
    PublicOutcome,
    projection_input,
    public_canonical_record,
    public_investigation_context,
)

router = APIRouter(prefix="/studio", tags=["Studio business workflow"])


class RunLink(Contract):
    run_id: str = Field(min_length=1, max_length=64)


@router.get("/threads/{thread_id}/missions", response_model=PublicMissionList)
async def list_missions(thread_id: str, user: Annotated[dict, Depends(get_current_user)]):
    missions = await mission_service.list(thread_id, user)
    return PublicMissionList(missions=[public_mission(mission) for mission in missions])


@router.get("/threads/{thread_id}/missions/resumable", response_model=PublicResumableMissionList)
async def resumable_missions(thread_id: str, user: Annotated[dict, Depends(get_current_user)]):
    missions = await mission_service.resumable(thread_id, user)
    return PublicResumableMissionList(
        missions=[
            PublicResumableMission.model_validate(projection_input(mission)) for mission in missions
        ]
    )


@router.post("/missions/{mission_id}/resume", response_model=PublicMission)
async def resume_mission(
    mission_id: str,
    body: MissionResume,
    user: Annotated[dict, Depends(get_current_user)],
):
    return public_mission(await mission_service.resume(mission_id, body, user))


@router.get(
    "/missions/{mission_id}/objects/{kind}/{object_id}",
    response_model=PublicInvestigation | PublicDecision | PublicAction | PublicOutcome,
)
async def read_mission_object(
    mission_id: str,
    kind: str,
    object_id: str,
    revision: int,
    user: Annotated[dict, Depends(get_current_user)],
):
    if kind not in {"investigation", "decision", "action", "outcome"}:
        raise HTTPException(status_code=404, detail="Mission canonical reference not found")
    record = await mission_service.canonical_read(
        mission_id,
        ObjectRef.model_validate({"kind": kind, "id": object_id, "revision": revision}),
        user,
    )
    return public_canonical_record(kind, record)


@router.post("/threads/{thread_id}/missions", response_model=PublicMission)
async def create_mission(
    thread_id: str, body: MissionCreate, user: Annotated[dict, Depends(get_current_user)]
):
    return public_mission(await mission_service.create(thread_id, body, user))


@router.get(
    "/missions/{mission_id}/objects/investigation/{investigation_id}/context",
    response_model=PublicInvestigationContext,
)
async def mission_investigation_context(
    mission_id: str,
    investigation_id: str,
    revision: int,
    user: Annotated[dict, Depends(get_current_user)],
):
    records = await mission_service.investigation_context(
        mission_id,
        investigation_id,
        revision,
        user,
    )
    return public_investigation_context(records)


@router.get("/missions/{mission_id}", response_model=PublicMission)
async def get_mission(mission_id: str, user: Annotated[dict, Depends(get_current_user)]):
    return public_mission(await mission_service.get(mission_id, user))


@router.post("/missions/{mission_id}/runs", response_model=PublicMission)
async def attach_run(
    mission_id: str, body: RunLink, user: Annotated[dict, Depends(get_current_user)]
):
    return public_mission(await mission_service.attach_run(mission_id, body.run_id, user))


@router.post("/missions/{mission_id}/objects", response_model=PublicMission)
async def link_object(
    mission_id: str, body: MissionLink, user: Annotated[dict, Depends(get_current_user)]
):
    return public_mission(
        await mission_service.link(mission_id, body.object_ref, body.expected_revision, user)
    )


@router.post("/missions/{mission_id}/cancel", response_model=PublicMission)
async def cancel_mission(
    mission_id: str, body: MissionOperation, user: Annotated[dict, Depends(get_current_user)]
):
    return public_mission(await mission_service.cancel(mission_id, body.expected_revision, user))


@router.post("/missions/{mission_id}/deliverables", response_model=PublicMissionDeliverable)
async def create_deliverable(
    mission_id: str, body: DeliverableCreate, user: Annotated[dict, Depends(get_current_user)]
):
    result = await mission_service.deliver(mission_id, body, user)
    return PublicMissionDeliverable.model_validate(projection_input(result))


@router.get("/missions/{mission_id}/deliverables", response_model=PublicDeliverableList)
async def list_deliverables(mission_id: str, user: Annotated[dict, Depends(get_current_user)]):
    results = await mission_service.deliverables(mission_id, user)
    return PublicDeliverableList(
        deliverables=[
            PublicMissionDeliverable.model_validate(projection_input(result)) for result in results
        ]
    )
