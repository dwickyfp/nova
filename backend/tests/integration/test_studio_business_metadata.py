"""Real-engine persistence, scoped replay, and attachment-reference acceptance."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.database import db
from app.modules.agents.harness_repository import harness_repository
from app.modules.agents.mission import MissionService
from app.modules.agents.mission_schema import DeliverableCreate, MissionCreate, StageKind
from app.modules.agents.resource_delegation import ResourceDelegation
from app.modules.agents.run_journal import run_journal
from app.modules.assistant.analysis_workspace import (
    AnalysisRequest,
    AnalysisResult,
    AnalyticalWorkspace,
)
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.security import observation_context, session_security
from tests.integration.test_intelligence_metadata import intelligence_db as intelligence_db

pytestmark = pytest.mark.engine


@pytest.fixture
async def studio_metadata(intelligence_db, monkeypatch):
    scope = intelligence_db
    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    await assistant_repository.ensure_schema()
    await harness_repository.ensure_schema()
    await MissionService().ensure_schema()
    await ResourceDelegation().ensure_schema()
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
            "CONFIG_STUDIO_RESOURCE_GRANTS",
            "CONFIG_STUDIO_RESOURCES",
            "CONFIG_STUDIO_DELIVERABLES",
            "CONFIG_STUDIO_MISSIONS",
        ):
            await db.execute_system(
                f"DELETE FROM NOVA_SYSTEM.{table} WHERE owner_name=%s", [scope.principal]
            )
        await harness_repository.delete_thread(thread["thread_id"], owner_name=scope.principal)
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
        thread_id, MissionCreate(objective="Follow up", operation_id="followup"), user
    )
    assert again.mission_id == mission.mission_id


async def test_real_resource_grants_are_durable_selective_and_scope_bound(studio_metadata):
    user, thread_id = studio_metadata
    body = "Sales declined in the western region."
    attachments = [
        {
            "name": "brief.txt",
            "media_type": "text/plain",
            "content": body,
            "size_bytes": len(body.encode()),
        }
    ]
    message = await assistant_repository.append_message(
        thread_id,
        user_name=user["username"],
        role="user",
        content="Review the brief",
        attachments=attachments,
        security_context=observation_context(session_security(user)),
    )
    root = await harness_repository.create_root(
        owner_name=user["username"],
        thread_id=thread_id,
        role_name=user["active_role"],
        session_id=user["session_id"],
        security_version=1,
        objective="Review the brief",
        user_message_id=message["message_id"],
    )
    resources = ResourceDelegation()
    metadata = await resources.register_root(root, user)
    assert body not in metadata[0].model_dump_json()
    child = await harness_repository.spawn(
        parent=root,
        agent_id="finance",
        objective="Review",
        operation_id="finance",
        agent_path="/root/finance",
    )
    sibling = await harness_repository.spawn(
        parent=root,
        agent_id="sales",
        objective="Review",
        operation_id="sales",
        agent_path="/root/sales",
    )
    await resources.grant(root, child, [metadata[0].resource_id], user)
    loaded, refs = await ResourceDelegation().load(child, user)
    assert refs == [metadata[0].resource_id] and loaded == attachments
    assert await resources.available(sibling, user) == []
    with pytest.raises(ValueError, match="security context"):
        await resources.load(child, {**user, "session_id": "another"})
    await ResourceDelegation().ensure_schema()
    assert await ResourceDelegation().load(child, user) == (loaded, refs)


async def test_real_mission_attachment_fake_workspace_and_deliverable_journey(
    studio_metadata,
    monkeypatch,
):
    user, thread_id = studio_metadata
    monkeypatch.setattr(settings, "STUDIO_ANALYSIS_WORKSPACE_ENABLED", True)
    body = "region,sales\nwest,100\neast,150\n"
    files = [
        {
            "name": "sales.csv",
            "media_type": "text/plain",
            "content": body,
            "size_bytes": len(body.encode()),
        }
    ]
    message = await assistant_repository.append_message(
        thread_id,
        user_name=user["username"],
        role="user",
        content="Analyze attached sales",
        security_context=observation_context(session_security(user)),
        attachments=files,
    )
    root = await harness_repository.create_root(
        owner_name=user["username"],
        thread_id=thread_id,
        role_name=user["active_role"],
        session_id=user["session_id"],
        security_version=1,
        objective="Analyze attached sales",
        user_message_id=message["message_id"],
        work_intent="ANALYZE",
    )
    metadata = await ResourceDelegation().register_root(root, user)
    assert await harness_repository.claim(root["run_id"], "workspace-acceptance")
    claimed = await harness_repository.get(root["run_id"])
    assert claimed["status"] == "running"
    assert claimed["lease_owner"] == "workspace-acceptance"
    assert claimed["generation"] == root["generation"] + 1
    mission = await MissionService().create(
        thread_id,
        MissionCreate(
            objective=root["objective"],
            operation_id=root["run_id"],
            public_work_steps=[StageKind.EVIDENCE],
        ),
        user,
    )
    await MissionService().attach_run(mission.mission_id, root["run_id"], user)

    class FixtureExecutor:
        isolated = True

        async def execute(self, execution_id, request, scope, inputs, cancelled):
            assert request.bounds.network is False
            assert scope.principal == user["username"]
            assert [item.content for item in inputs] == [body]
            return AnalysisResult(
                execution_id=execution_id,
                status="completed",
                summary="Fixture summary",
                rows=[{"regions": 2}],
            )

        async def cancel(self, execution_id, scope):
            return None

    workspace = AnalyticalWorkspace(FixtureExecutor())
    result = await workspace.execute(
        AnalysisRequest(
            thread_id=thread_id,
            run_id=root["run_id"],
            resource_refs=[metadata[0].resource_id],
            code="# ignored by fixture executor",
        ),
        user,
    )
    assert result.status == "completed" and result.rows == [{"regions": 2}]
    current = await harness_repository.get(root["run_id"])
    assert (current["status"], current["lease_owner"], current["generation"]) == (
        "running", claimed["lease_owner"], claimed["generation"]
    )
    await harness_repository.event(
        root["run_id"],
        root["run_id"],
        "child_activity",
        {
            "event_type": "table",
            "table": {"columns": ["regions"], "rows": [[2]], "tool_call_id": result.execution_id},
        },
    )
    assert not await harness_repository.transition(
        root["run_id"], from_status="running", to_status="completed",
        lease_owner=claimed["lease_owner"], generation=claimed["generation"] - 1,
    )
    assert (await harness_repository.get(root["run_id"]))["status"] == "running"
    assert await harness_repository.transition(
        root["run_id"], from_status="running", to_status="completed",
        lease_owner=claimed["lease_owner"], generation=claimed["generation"],
    )
    assert (await harness_repository.get(root["run_id"]))["status"] == "completed"
    projected = await MissionService().get(mission.mission_id, user)
    assert projected.status == "completed"
    assert projected.evidence_refs == [root["run_id"] + ":" + result.execution_id]
    memo = await MissionService().deliver(
        mission.mission_id,
        DeliverableCreate(
            expected_revision=projected.revision,
            kind="decision_memo",
            operation_id="journey-memo",
        ),
        user,
    )
    assert projected.evidence_refs[0] in memo.markdown
    assert body not in memo.markdown
    assert (await MissionService().get(mission.mission_id, user)) == projected
