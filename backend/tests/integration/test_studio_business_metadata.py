"""Real-engine persistence, scoped replay, and attachment-reference acceptance."""

import json
import secrets
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.main import create_app
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
from app.modules.intelligence.contracts import Scope
from tests.benchmark.business_intelligence.client import StudioClient
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
        thread_id,
        MissionCreate(
            objective="Follow up", operation_id="followup", continue_mission_id=mission.mission_id
        ),
        user,
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


async def test_real_smart_resume_reauthorizes_original_attachment_in_new_login(studio_metadata):
    fixture_user, thread_id = studio_metadata
    principal = fixture_user["username"]
    role = "RESUME_" + uuid4().hex[:16]
    password = secrets.token_urlsafe(24)
    await db.execute_system(f"CREATE ROLE `{role}`")
    await db.execute_system(f"CREATE USER '{principal}' IDENTIFIED BY %s", [password])
    await db.execute_system(f"GRANT `{role}` TO USER '{principal}'")
    await db.execute_system(f"SET DEFAULT ROLE `{role}` TO '{principal}'")
    service, resources = MissionService(), ResourceDelegation()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://nova.test", timeout=120
    ) as http:
        client = StudioClient(http)
        tokens = []
        try:
            identity = await client.login(principal, password, role)
            tokens.append(http.headers["Authorization"])
            user = {**await session_store.get(identity["session_id"]),
                    "session_id": identity["session_id"]}
            original_scope = Scope.from_user(user)
            content = "Authorized original upload."
            message = await assistant_repository.append_message(
                thread_id, user_name=principal, role="user", content="Review the upload",
                security_context=observation_context(session_security(user)),
                attachments=[{"name": "brief.txt", "media_type": "text/plain",
                              "content": content, "size_bytes": len(content.encode())}],
            )
            root = await harness_repository.create_root(
                owner_name=principal, thread_id=thread_id, role_name=role,
                session_id=user["session_id"],
                security_version=original_scope.security_context_version,
                objective="Review the upload", user_message_id=message["message_id"],
            )
            metadata = await resources.register_root(root, user)
            mission = await service.create(thread_id, MissionCreate(
                objective="Review the original upload", operation_id=root["run_id"],
            ), user)
            await service.attach_run(mission.mission_id, root["run_id"], user)
            assert await harness_repository.claim(root["run_id"], "resume-acceptance")
            claimed = await harness_repository.get(root["run_id"])
            assert await harness_repository.transition(
                root["run_id"], from_status="running", to_status="completed",
                lease_owner=claimed["lease_owner"], generation=claimed["generation"],
            )
            prior = await service.get(mission.mission_id, user)
            next_identity = await client.login(principal, password, role)
            tokens.append(http.headers["Authorization"])
            next_user = {**await session_store.get(next_identity["session_id"]),
                         "session_id": next_identity["session_id"]}
            summaries = await client.request(
                "GET", f"agents/studio/threads/{thread_id}/missions/resumable"
            )
            assert [row["mission_id"] for row in summaries["missions"]] == [mission.mission_id]
            request = {"expected_revision": prior.revision, "operation_id": "new-login-resume"}
            path = f"agents/studio/missions/{mission.mission_id}/resume"
            resumed = await client.request("POST", path, request)
            assert not {"scope", "run_bindings", "current_binding"} & resumed.keys()
            internal = await service.get(mission.mission_id, next_user, project=False)
            assert internal.scope == Scope.from_user(next_user)
            assert internal.run_bindings[root["run_id"]] == original_scope
            assert internal.current_binding.generation == 2
            assert internal.revision == resumed["revision"]
            assert (await client.request("POST", path, request))["revision"] == resumed["revision"]
            assert await resources.reauthorize_for_mission(
                mission.mission_id, root["run_id"], next_user
            ) == [metadata[0].resource_id]
            historical = await harness_repository.get(root["run_id"])
            assert historical["session_id"] == original_scope.session_id
            with pytest.raises(ValueError, match="security context"):
                await resources.load(historical, next_user)
            old_read = await http.get(
                f"/api/v1/agents/studio/missions/{mission.mission_id}",
                headers={"Authorization": tokens[0]},
            )
            assert old_read.status_code == 404
            old_replay = await http.post(
                f"/api/v1/agents/studio/threads/{thread_id}/missions",
                json={"objective": mission.objective, "operation_id": root["run_id"]},
                headers={"Authorization": tokens[0]},
            )
            assert old_replay.status_code == 409
            unchanged = await service.get(mission.mission_id, next_user, project=False)
            assert unchanged.current_binding.generation == 2
            assert unchanged.revision == resumed["revision"]
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES "
                "WHERE message_id=%s AND thread_id=%s AND user_name=%s",
                [message["message_id"], thread_id, principal],
            )
            refused = await http.post(f"/api/v1/{path}", json=request)
            assert refused.status_code == 403
            after = await service.get(mission.mission_id, next_user, project=False)
            assert after.current_binding.generation == 2 and after.revision == resumed["revision"]
        finally:
            for token in tokens:
                http.headers["Authorization"] = token
                await client.request("POST", "auth/logout")
            await db.execute_system(f"DROP USER '{principal}'")
            await db.execute_system(f"DROP ROLE `{role}`")


async def test_real_mission_resource_reauthorization_across_sessions_and_source_changes(
    studio_metadata, monkeypatch
):
    from app.modules.intelligence.actions import action_service

    user, thread_id = studio_metadata
    text = "Uploaded planning context for the governed Mission."
    attachment = {
        "name": "planning.txt",
        "media_type": "text/plain",
        "content": text,
        "size_bytes": len(text.encode()),
    }
    message = await assistant_repository.append_message(
        thread_id,
        user_name=user["username"],
        role="user",
        content="Review the upload",
        attachments=[attachment],
        security_context=observation_context(session_security(user)),
    )
    root = await harness_repository.create_root(
        owner_name=user["username"],
        thread_id=thread_id,
        role_name=user["active_role"],
        session_id=user["session_id"],
        security_version=user["security_context_version"],
        objective="Review the upload",
        user_message_id=message["message_id"],
    )
    metadata = await ResourceDelegation().register_root(root, user)
    service = MissionService()
    mission = await service.create(
        thread_id,
        MissionCreate(objective=root["objective"], operation_id=root["run_id"]),
        user,
    )
    mission = await service.attach_run(mission.mission_id, root["run_id"], user)
    next_user = {
        **user,
        "session_id": "new-fixture-session",
        "security_context_version": 2,
    }
    live_authorization = AsyncMock()
    monkeypatch.setattr(action_service, "revalidate", live_authorization)
    assert await ResourceDelegation().reauthorize_for_mission(
        mission.mission_id, root["run_id"], next_user
    ) == [metadata[0].resource_id]
    live_authorization.assert_awaited_once_with(next_user)
    with pytest.raises(ValueError, match="security context"):
        await ResourceDelegation().load(root, next_user)
    grants = await db.execute_system(
        "SELECT session_id,security_version FROM NOVA_SYSTEM.CONFIG_STUDIO_RESOURCE_GRANTS "
        "WHERE resource_id=%s AND owner_name=%s AND role_name=%s",
        [metadata[0].resource_id, user["username"], user["active_role"]],
    )
    assert grants["rows"] == [[user["session_id"], user["security_context_version"]]]
    unchanged = await service.get(mission.mission_id, user, project=False)
    assert unchanged.run_bindings[root["run_id"]] == mission.scope
    await db.execute_system(
        "UPDATE NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES SET attachments=%s "
        "WHERE message_id=%s AND thread_id=%s AND user_name=%s",
        [
            json.dumps([{**attachment, "content": "changed uploaded context"}]),
            message["message_id"],
            thread_id,
            user["username"],
        ],
    )
    with pytest.raises(ValueError, match="changed after delegation"):
        await ResourceDelegation().reauthorize_for_mission(
            mission.mission_id, root["run_id"], next_user
        )


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
        "running",
        claimed["lease_owner"],
        claimed["generation"],
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
        root["run_id"],
        from_status="running",
        to_status="completed",
        lease_owner=claimed["lease_owner"],
        generation=claimed["generation"] - 1,
    )
    assert (await harness_repository.get(root["run_id"]))["status"] == "running"
    assert await harness_repository.transition(
        root["run_id"],
        from_status="running",
        to_status="completed",
        lease_owner=claimed["lease_owner"],
        generation=claimed["generation"],
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
