"""Real-engine persistence, scoped replay, and attachment-reference acceptance."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.database import db
from app.modules.agents.harness_repository import harness_repository
from app.modules.agents.mission import MissionService
from app.modules.agents.mission_schema import DeliverableCreate, MissionCreate, StageKind
from app.modules.agents.run_journal import run_journal
from app.modules.assistant.repository import assistant_repository
from tests.integration.test_intelligence_metadata import intelligence_db as intelligence_db

pytestmark = pytest.mark.engine


@pytest.fixture
async def studio_metadata(intelligence_db, monkeypatch):
    scope = intelligence_db
    await assistant_repository.ensure_schema()
    await harness_repository.ensure_schema()
    await MissionService().ensure_schema()
    user = {
        "username": scope.principal,
        "active_role": scope.active_role,
        "assigned_roles": [scope.active_role],
        "session_id": scope.session_id,
        "security_context_version": scope.security_context_version,
    }
    thread = await assistant_repository.create_thread(user_name=scope.principal)
    try:
        yield user, thread["thread_id"]
    finally:
        for table in (
            "CONFIG_STUDIO_DELIVERABLES",
            "CONFIG_STUDIO_MISSIONS",
        ):
            await db.execute_system(
                f"DELETE FROM NOVA_SYSTEM.{table} WHERE owner_name=%s", [scope.principal]
            )
        await run_journal.delete_thread(thread["thread_id"], owner_name=scope.principal)
        await assistant_repository.delete_thread(thread["thread_id"], user_name=scope.principal)


async def test_real_mission_projection_reconnect_and_deliverable(studio_metadata):
    user, thread_id = studio_metadata
    service = MissionService()
    mission = await service.create(
        thread_id,
        MissionCreate(
            objective="Inspect sales evidence",
            operation_id="initial",
            public_work_steps=[StageKind.EVIDENCE],
        ),
        user,
    )
    run_id = str(uuid4())
    await run_journal.start(
        run_id=run_id,
        owner_name=user["username"],
        agent_id="finance",
        thread_id=thread_id,
        role_name=user["active_role"],
        session_id=user["session_id"],
        security_version=user["security_context_version"],
    )
    await service.attach_run(mission.mission_id, run_id, user)
    await run_journal.append(
        run_id,
        'event: table\ndata: {"sequence":0,"columns":["sales"],'
        '"rows":[[100]],"evidence_id":"query-accepted"}\n\n',
    )
    await run_journal.finish(run_id, "completed")
    projected = await MissionService().get(mission.mission_id, user)
    assert projected.status == "completed"
    assert projected.evidence_refs == ["query-accepted"]
    assert (await MissionService().get(mission.mission_id, user)) == projected
    memo = await service.deliver(
        mission.mission_id,
        DeliverableCreate(
            expected_revision=projected.revision, kind="decision_memo", operation_id="memo"
        ),
        user,
    )
    assert "query-accepted" in memo.markdown
    assert (await MissionService().deliverables(mission.mission_id, user)) == [memo]
    with pytest.raises(HTTPException) as error:
        await service.get(mission.mission_id, {**user, "security_context_version": 2})
    assert error.value.status_code == 404
    again = await service.create(
        thread_id,
        MissionCreate(
            objective="Follow up", operation_id="followup", continue_mission_id=mission.mission_id
        ),
        user,
    )
    assert again.mission_id == mission.mission_id
