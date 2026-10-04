"""Authenticated Studio Mission, resource, and deliverable endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from app.core.deps import get_current_user
from app.modules.agents.agent_control import AgentControl
from app.modules.agents.harness_repository import harness_repository
from app.modules.agents.identity import participant_path
from app.modules.agents.mission import mission_service, require_thread, require_workflow
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
from app.modules.agents.resource_delegation import ResourceGrantRequest, resource_delegation
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
        ObjectRef(kind=kind, id=object_id, revision=revision),
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


async def _resource_control(thread_id: str, root_run_id: str, user: dict) -> AgentControl:
    require_workflow()
    await require_thread(thread_id, user)
    root = await harness_repository.get(root_run_id)
    if root is None or root["thread_id"] != thread_id or root["depth"] != 0:
        raise HTTPException(status_code=404, detail="Resource collaboration not found")
    control = AgentControl(harness_repository, root, user)
    try:
        await control._tree()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Resource collaboration not found") from exc
    return control


@router.get("/threads/{thread_id}/resources")
async def list_resources(
    thread_id: str,
    root_run_id: str,
    user: Annotated[dict, Depends(get_current_user)],
    participant: str = "/root",
):
    control = await _resource_control(thread_id, root_run_id, user)
    try:
        run, _ = await control._target(participant)
        return {"resources": await resource_delegation.available(run, user)}
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Resource access denied") from exc


@router.post("/threads/{thread_id}/resources/grants")
async def grant_resources(
    thread_id: str, body: ResourceGrantRequest, user: Annotated[dict, Depends(get_current_user)]
):
    control = await _resource_control(thread_id, body.root_run_id, user)
    async with harness_repository.admission_lock(control.root_id, user["username"]) as owned:
        try:
            grantor, _ = await control._target(body.grantor)
            recipient, _ = await control._target(body.target)
            await owned()
            refs = await resource_delegation.grant(grantor, recipient, body.resource_refs, user)
            return {"resource_refs": refs, "participant": participant_path(recipient).value}
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Resource delegation rejected") from exc
