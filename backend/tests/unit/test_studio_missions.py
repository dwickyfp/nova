from __future__ import annotations

import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_current_user
from app.modules.agents import mission as module
from app.modules.agents.mission import MissionService, project_event, project_object
from app.modules.agents.mission_router import router
from app.modules.agents.mission_schema import (
    MISSION_DDLS,
    DeliverableCreate,
    MissionCreate,
    ObjectRef,
    StageKind,
    WorkIntent,
)
from app.modules.assistant.planning import TurnPlanningError, validate_turn_plan

USER = {
    "username": "alice",
    "active_role": "analyst",
    "assigned_roles": ["analyst"],
    "session_id": "login",
    "security_context_version": 1,
}


class MissionIO:
    def __init__(self):
        self.missions = {}
        self.deliverables = {}
        self.runs = {}
        self.events = {}
        self.statements = []

    @staticmethod
    def scoped(mission, params):
        return module.scope_params(mission.scope) == params

    async def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if sql.lstrip().startswith("CREATE TABLE"):
            return {"affected": 0}
        if "CONFIG_STUDIO_MISSIONS" in sql:
            if sql.startswith("INSERT"):
                item = module.Mission.model_validate_json(params[7])
                self.missions[item.mission_id] = item
                return {"affected": 1}
            if sql.startswith("UPDATE"):
                item = self.missions[params[3]]
                if item.revision != params[4] or not self.scoped(item, params[5:]):
                    return {"affected": 0}
                self.missions[params[3]] = module.Mission.model_validate_json(params[1])
                return {"affected": 1}
            items = list(self.missions.values())
            if "mission_id=%s" in sql:
                items = [m for m in items if m.mission_id == params[0]]
            else:
                items = [m for m in items if m.thread_id == params[0]]
                items.sort(key=lambda m: m.created_at, reverse=True)
            items = [m for m in items if self.scoped(m, params[1:])]
            return {"rows": [[m.model_dump_json()] for m in items]}
        if "CONFIG_STUDIO_DELIVERABLES" in sql:
            if sql.startswith("INSERT"):
                self.deliverables[params[0]] = (params[7], params[6], params[2:6])
                return {"affected": 1}
            if "deliverable_id=%s" in sql:
                row = self.deliverables.get(params[0])
                return {"rows": [[row[0], row[1]]] if row and row[2] == params[1:] else []}
            return {
                "rows": [[row[0]] for row in self.deliverables.values() if row[2] == params[1:]]
            }
        if "CONFIG_AGENT_RUNS" in sql:
            row = self.runs.get(params[0])
            if sql.startswith("SELECT thread_id"):
                return {"rows": [[row["thread_id"]]] if row and row["scope"] == params[1:] else []}
            if (
                not row
                or row["scope"][:2] != [params[1], params[3]]
                or row["thread_id"] != params[2]
            ):
                return {"rows": []}
            return {"rows": [[row["status"], row["sequence"], *row["scope"][2:], row["agent_id"]]]}
        if "CONFIG_AGENT_RUN_EVENTS" in sql:
            return {
                "rows": [
                    [seq, frame] for seq, frame in self.events.get(params[0], []) if seq > params[1]
                ][:100]
            }
        if "CONFIG_AGENT_SESSION_EVENTS" in sql:
            return {"rows": [r for r in self.events.get(params[0], []) if r[0] > params[1]][:100]}
        raise AssertionError(sql)


@pytest.fixture
def mission_io(monkeypatch):
    io = MissionIO()
    monkeypatch.setattr(module, "settings", SimpleNamespace(STUDIO_BUSINESS_WORKFLOW_ENABLED=True))
    monkeypatch.setattr(module.db, "execute_system", io.execute)
    monkeypatch.setattr(module, "require_thread", AsyncMock(return_value={"thread_id": "thread"}))
    monkeypatch.setattr(module, "write_audit_log", AsyncMock())

    @asynccontextmanager
    async def lock(*args):
        yield AsyncMock()

    monkeypatch.setattr(module.harness_repository, "admission_lock", lock)
    return io, MissionService()


async def create(service, **kwargs):
    return await service.create(
        "thread",
        MissionCreate(objective="Why did revenue decline?", operation_id="request-1", **kwargs),
        USER,
    )


def test_work_intent_is_additive_and_cannot_authorize_tools():
    legacy = {"intent": "direct_answer", "tools": [], "required_tools": [], "skills": []}
    assert validate_turn_plan(legacy, set()).work_intent is None
    plan = validate_turn_plan(
        {**legacy, "work_intent": "INVESTIGATE", "public_work_steps": ["investigate", "evidence"]},
        set(),
    )
    assert plan.work_intent == WorkIntent.INVESTIGATE
    assert plan.selected_tools == ()
    assert plan.public_work_steps == (StageKind.INVESTIGATE, StageKind.EVIDENCE)
    for invalid in (
        {"work_intent": "ADMIN"},
        {"public_work_steps": ["private_reasoning"]},
        {"public_work_steps": ["evidence", "evidence"]},
    ):
        with pytest.raises(TurnPlanningError):
            validate_turn_plan({**legacy, **invalid}, set())


def test_strict_planner_schema_declares_every_property_required():
    from app.modules.assistant.planning import _plan_schema

    schema = _plan_schema(None)
    assert set(schema["required"]) == set(schema["properties"])


async def test_mission_reuses_thread_until_explicit_new_and_detects_collision(mission_io):
    _, service = mission_io
    mission = await create(service)
    same = await service.create(
        "thread",
        MissionCreate(objective="Follow up", operation_id="request-2", work_intent=WorkIntent.PLAN),
        USER,
    )
    assert same.mission_id == mission.mission_id
    assert (await create(service)).revision == 1
    with pytest.raises(HTTPException) as error:
        await create(service, public_work_steps=[StageKind.SCENARIOS])
    assert error.value.status_code == 409
    fresh = await service.create(
        "thread",
        MissionCreate(objective="Another analysis", operation_id="request-3", new_mission=True),
        USER,
    )
    assert fresh.mission_id != mission.mission_id


async def test_simple_answer_has_no_mission(mission_io):
    io, service = mission_io
    assert (
        await service.for_turn(
            "thread", USER, operation_id="simple", objective="Hello", work_intent=WorkIntent.ANSWER
        )
        is None
    )
    assert not io.missions


@pytest.mark.parametrize(
    "change",
    [
        {"username": "bob"},
        {"active_role": "other", "assigned_roles": ["other"]},
        {"session_id": "another"},
        {"security_context_version": 2},
    ],
)
async def test_missions_fail_closed_across_scope_changes(mission_io, change):
    _, service = mission_io
    mission = await create(service)
    with pytest.raises(HTTPException) as error:
        await service.get(mission.mission_id, {**USER, **change}, project=False)
    assert error.value.status_code == 404


async def test_durable_direct_projection_is_replay_safe_and_emits_no_private_text(mission_io):
    io, service = mission_io
    mission = await create(service, public_work_steps=[StageKind.EVIDENCE])
    io.runs["run"] = {
        "thread_id": "thread",
        "scope": module.scope_params(mission.scope),
        "status": "completed",
        "sequence": 0,
        "agent_id": "finance",
    }
    io.events["run"] = [
        (
            0,
            'event: table\ndata: {"evidence_id":"q1",'
            '"rows":[[42]],"private_reasoning":"hidden"}\n\n',
        )
    ]
    mission = await service.attach_run(mission.mission_id, "run", USER)
    projection = await service.get(mission.mission_id, USER)
    assert projection.status == "completed"
    assert projection.stages[0].status == "completed"
    assert projection.evidence_refs == ["q1"]
    assert projection.projection_cursor == {"run": 0}
    assert "hidden" not in projection.model_dump_json()
    assert "rows" not in projection.model_dump_json()
    again = await MissionService().get(mission.mission_id, USER)
    assert again == projection
    update = await service.stream_update("run", USER)
    assert update.startswith("event: mission_updated\n")
    assert json.loads(update.split("data: ", 1)[1])["mission"]["mission_id"] == mission.mission_id
    assert await service.stream_update("run", USER, after_revision=projection.revision) is None


async def test_unstamped_direct_run_is_rejected(mission_io):
    io, service = mission_io
    mission = await create(service)
    io.runs["old"] = {
        "thread_id": "thread",
        "scope": ["alice", "analyst", None, None],
        "status": "completed",
        "sequence": -1,
        "agent_id": "finance",
    }
    with pytest.raises(HTTPException) as error:
        await service.attach_run(mission.mission_id, "old", USER)
    assert error.value.status_code == 403


async def test_projection_cursor_does_not_turn_its_own_frame_into_another_revision(mission_io):
    io, service = mission_io
    mission = await create(service, public_work_steps=[StageKind.EVIDENCE])
    io.runs["run"] = {
        "thread_id": "thread",
        "scope": module.scope_params(mission.scope),
        "status": "running",
        "sequence": -1,
        "agent_id": "finance",
    }
    mission = await service.attach_run(mission.mission_id, "run", USER)
    io.runs["run"]["sequence"] = 0
    io.events["run"] = [(0, "event: mission_updated\ndata: {}\n\n")]
    projected = await service.get(mission.mission_id, USER)
    assert projected.revision == mission.revision
    assert projected.updated_at == mission.updated_at
    assert projected.projection_cursor["run"] == 0
    assert await service.stream_update("run", USER, after_revision=mission.revision) is None


async def test_explicit_new_mission_operation_is_reused_by_root_planning_hook(mission_io):
    _, service = mission_io
    original = await service.for_turn(
        "thread",
        USER,
        operation_id="run",
        objective="Revenue",
        work_intent=WorkIntent.INVESTIGATE,
        explicit=True,
        new_mission=True,
    )
    planned = await service.for_turn(
        "thread",
        USER,
        operation_id="run",
        objective="Revenue",
        work_intent=WorkIntent.INVESTIGATE,
        public_work_steps=(StageKind.INVESTIGATE, StageKind.EVIDENCE),
        explicit=True,
        new_mission=True,
    )
    assert planned.mission_id == original.mission_id


async def test_child_table_references_preserve_source_participant(mission_io):
    _, service = mission_io
    mission = await create(service)
    project_event(
        mission,
        "child",
        "child_activity",
        {
            "event_type": "table",
            "table": {"columns": ["sales"], "rows": [[1]], "tool_call_id": "semantic-call"},
        },
    )
    assert mission.evidence_refs == ["child:semantic-call"]
    assert mission.stages[-1].source_refs == ["child"]


async def test_plan_and_completed_run_do_not_fabricate_business_stage_completion(mission_io):
    io, service = mission_io
    mission = await create(service)
    io.runs["smart"] = {
        "thread_id": "thread",
        "scope": module.scope_params(mission.scope),
        "status": "completed",
        "sequence": 0,
        "agent_id": "__smart__",
    }
    io.events["smart"] = [[0, "smart", "agent_completed", {"text": "All actions succeeded"}]]
    await service.attach_run(mission.mission_id, "smart", USER)
    mission = await service.get(mission.mission_id, USER)
    assert mission.status == "blocked"
    assert all(stage.status == "planned" for stage in mission.stages)


async def test_cancellation_is_durable_and_uses_existing_smart_owner(mission_io, monkeypatch):
    io, service = mission_io
    mission = await create(service)
    io.runs["smart"] = {
        "thread_id": "thread",
        "scope": module.scope_params(mission.scope),
        "status": "running",
        "sequence": -1,
        "agent_id": "__smart__",
    }
    mission = await service.attach_run(mission.mission_id, "smart", USER)
    cancel = AsyncMock(return_value=True)
    monkeypatch.setattr(module.harness_repository, "cancel_tree", cancel)
    monkeypatch.setattr(module.harness_repository, "reconcile_cancelled_children", AsyncMock())
    cancelled = await service.cancel(mission.mission_id, mission.revision, USER)
    assert cancelled.cancel_requested
    cancel.assert_awaited_once_with("smart")
    assert await service.cancellation_requested(mission.mission_id, USER)
    assert (await service.cancel(mission.mission_id, 1, USER)).cancel_requested
    with pytest.raises(HTTPException):
        await service.attach_run(mission.mission_id, "smart", USER)


def add_run(io, mission, run_id="direct", *, agent="finance", status="running", sequence=-1):
    io.runs[run_id] = {
        "thread_id": mission.thread_id,
        "scope": module.scope_params(mission.scope),
        "status": status,
        "sequence": sequence,
        "agent_id": agent,
    }


async def test_cancel_stops_direct_owner_and_closes_stages(mission_io, monkeypatch):
    io, service = mission_io
    mission = await create(service)
    add_run(io, mission)
    mission = await service.attach_run(mission.mission_id, "direct", USER)

    async def finish(run_id, status):
        assert io.missions[mission.mission_id].cancel_requested
        io.runs[run_id]["status"] = status

    stop = AsyncMock(side_effect=finish)
    monkeypatch.setattr(module.run_journal, "finish", stop)
    cancelled = await service.cancel(mission.mission_id, mission.revision, USER)
    stop.assert_awaited_once_with("direct", "interrupted")
    assert cancelled.status == "cancelled"
    assert all(s.status == "cancelled" for s in cancelled.stages)
    assert await service.cancel(mission.mission_id, 1, USER) == cancelled


async def test_cancellation_retry_repairs_owner_failure_and_stops_other_runs(
    mission_io, monkeypatch
):
    io, service = mission_io
    mission = await create(service)
    for run_id in ("one", "two"):
        add_run(io, mission, run_id)
        mission = await service.attach_run(mission.mission_id, run_id, USER)
    failed = True

    async def finish(run_id, status):
        nonlocal failed
        if run_id == "one" and failed:
            failed = False
            raise RuntimeError("owner temporarily unavailable")
        io.runs[run_id]["status"] = status

    stop = AsyncMock(side_effect=finish)
    monkeypatch.setattr(module.run_journal, "finish", stop)
    with pytest.raises(RuntimeError):
        await service.cancel(mission.mission_id, mission.revision, USER)
    assert io.runs["two"]["status"] == "interrupted"
    assert (await service.get(mission.mission_id, USER)).status == "cancelling"
    cancelled = await service.cancel(mission.mission_id, 1, USER)
    assert cancelled.status == "cancelled"
    assert io.runs["one"]["status"] == "interrupted"


async def test_cancel_stops_current_action_revision_through_action_owner(mission_io, monkeypatch):
    from app.modules.intelligence.actions import action_service

    io, service = mission_io
    mission = await create(service)
    io.missions[mission.mission_id].object_refs = [
        ObjectRef(kind="action", id="action", revision=1)
    ]
    action = SimpleNamespace(scope=mission.scope, revision=4)
    monkeypatch.setattr(action_service, "get", AsyncMock(return_value=action))

    async def stop(action_id, body, user):
        assert io.missions[mission.mission_id].cancel_requested
        assert body.expected_revision == 4 and body.thread_id == "thread"
        assert user == USER and action_id == "action"

    cancel = AsyncMock(side_effect=stop)
    monkeypatch.setattr(action_service, "cancel", cancel)
    cancelled = await service.cancel(mission.mission_id, 1, USER)
    assert cancelled.cancellation_complete
    cancel.assert_awaited_once()


async def test_dispatched_action_is_left_to_review_without_false_cancel_completion(
    mission_io, monkeypatch
):
    from app.modules.intelligence.actions import action_service

    io, service = mission_io
    mission = await create(service)
    io.missions[mission.mission_id].object_refs = [
        ObjectRef(kind="action", id="action", revision=1)
    ]
    monkeypatch.setattr(
        action_service,
        "get",
        AsyncMock(return_value=SimpleNamespace(scope=mission.scope, revision=2)),
    )
    cancel = AsyncMock(side_effect=HTTPException(409, "Dispatched action requires review"))
    monkeypatch.setattr(action_service, "cancel", cancel)
    for revision in (1, 1):
        with pytest.raises(HTTPException) as error:
            await service.cancel(mission.mission_id, revision, USER)
        assert error.value.status_code == 409
    assert cancel.await_count == 2
    assert (await service.get(mission.mission_id, USER)).status == "cancelling"


async def test_late_run_attachment_is_stopped_before_rejection(mission_io, monkeypatch):
    io, service = mission_io
    mission = await create(service)
    await service.cancel(mission.mission_id, 1, USER)
    add_run(io, mission)
    stop = AsyncMock()
    monkeypatch.setattr(module.run_journal, "finish", stop)
    with pytest.raises(HTTPException) as error:
        await service.attach_run(mission.mission_id, "direct", USER)
    assert error.value.status_code == 409
    stop.assert_awaited_once_with("direct", "interrupted")
    assert io.missions[mission.mission_id].run_ids == []


async def test_run_bound_mission_reuse_survives_newer_mission(mission_io):
    io, service = mission_io
    older = await create(service)
    add_run(io, older, "followup")
    await service.attach_run(older.mission_id, "followup", USER)
    await service.create(
        "thread",
        MissionCreate(objective="Another task", operation_id="new", new_mission=True),
        USER,
    )
    reused = await service.for_turn("thread", USER, operation_id="followup", objective="Follow up")
    assert reused.mission_id == older.mission_id


async def test_successful_followup_can_complete_mission_after_failed_earlier_turn(mission_io):
    io, service = mission_io
    mission = await create(service, public_work_steps=[StageKind.EVIDENCE])
    add_run(io, mission, "first", status="failed")
    await service.attach_run(mission.mission_id, "first", USER)
    assert (await service.get(mission.mission_id, USER)).status == "blocked"
    add_run(io, mission, "followup", status="completed", sequence=0)
    io.events["followup"] = [(0, 'event: table\ndata: {"evidence_id":"query"}\n\n')]
    await service.attach_run(mission.mission_id, "followup", USER)
    final = await service.get(mission.mission_id, USER)
    assert final.status == "completed" and final.evidence_refs == ["query"]


async def test_cancelled_mission_requires_explicit_new_work(mission_io):
    _, service = mission_io
    mission = await create(service)
    cancelled = await service.cancel(mission.mission_id, 1, USER)
    assert cancelled.status == "cancelled"
    with pytest.raises(HTTPException) as error:
        await service.for_turn("thread", USER, operation_id="later", objective="Follow up")
    assert error.value.status_code == 409
    with pytest.raises(HTTPException):
        await service.create(
            "thread", MissionCreate(objective="Follow up", operation_id="later"), USER
        )
    fresh = await service.for_turn(
        "thread", USER, operation_id="later", objective="Follow up", new_mission=True, explicit=True
    )
    assert fresh.mission_id != cancelled.mission_id


async def test_object_revision_cannot_regress_and_cancel_rejects_new_links(mission_io, monkeypatch):
    _, service = mission_io
    mission = await create(service)
    reader = AsyncMock(return_value={"status": "complete"})
    monkeypatch.setattr(module, "canonical_payload", reader)
    linked = await service.link(
        mission.mission_id, ObjectRef(kind="investigation", id="inv", revision=3), 1, USER
    )
    with pytest.raises(HTTPException) as error:
        await service.link(
            mission.mission_id,
            ObjectRef(kind="investigation", id="inv", revision=2),
            linked.revision,
            USER,
        )
    assert error.value.status_code == 409
    cancelled = await service.cancel(mission.mission_id, linked.revision, USER)
    with pytest.raises(HTTPException):
        await service.link(
            mission.mission_id,
            ObjectRef(kind="investigation", id="inv", revision=4),
            cancelled.revision,
            USER,
        )


@pytest.mark.parametrize("smart", [False, True])
async def test_projection_pages_are_durable_across_service_restarts(mission_io, smart):
    io, service = mission_io
    mission = await create(service, public_work_steps=[StageKind.EVIDENCE])
    add_run(
        io,
        mission,
        "run",
        agent="__smart__" if smart else "finance",
        status="completed",
        sequence=100,
    )
    if smart:
        io.events["run"] = [[i, "run", "thinking", {}] for i in range(100)]
        io.events["run"].append([100, "child", "table", {"evidence_id": "final-evidence"}])
    else:
        io.events["run"] = [(i, "event: thinking\ndata: {}\n\n") for i in range(100)]
        io.events["run"].append((100, 'event: table\ndata: {"evidence_id":"final-evidence"}\n\n'))
    await service.attach_run(mission.mission_id, "run", USER)
    page = await service.get(mission.mission_id, USER)
    assert page.status == "running" and page.projection_cursor["run"] == 99
    final = await MissionService().get(mission.mission_id, USER)
    assert final.status == "completed" and final.evidence_refs == ["final-evidence"]
    assert final.projection_cursor["run"] == 100
    assert await MissionService().get(mission.mission_id, USER) == final


async def test_canonical_links_validate_scope_and_revision_before_projection(
    mission_io, monkeypatch
):
    _, service = mission_io
    mission = await create(service)
    ref = ObjectRef(kind="investigation", id="inv", revision=1)
    record = {"status": "complete", "hypotheses": [], "evidence": [{"id": "semantic-1"}]}
    reader = AsyncMock(return_value=record)
    monkeypatch.setattr(module, "canonical_payload", reader)
    linked = await service.link(mission.mission_id, ref, mission.revision, USER)
    assert linked.object_refs == [ref]
    assert linked.evidence_refs == ["semantic-1"]
    assert linked.stages[0].status == "completed"
    reader.assert_awaited_once_with(ref, mission.scope)
    with pytest.raises(HTTPException) as error:
        await service.link(
            mission.mission_id,
            ObjectRef(kind="decision", id="d", revision=1),
            mission.revision,
            USER,
        )
    assert error.value.status_code == 409


async def test_deliverable_uses_recorded_evidence_and_rejects_key_reuse(mission_io):
    io, service = mission_io
    mission = await create(service)
    body = DeliverableCreate(expected_revision=1, kind="decision_memo", operation_id="memo")
    with pytest.raises(HTTPException) as error:
        await service.deliver(mission.mission_id, body, USER)
    assert error.value.status_code == 422
    io.missions[mission.mission_id].evidence_refs = ["query-1"]
    memo = await service.deliver(mission.mission_id, body, USER)
    assert "query-1" in memo.markdown
    assert "planned" in memo.markdown
    assert (await service.deliver(mission.mission_id, body, USER)) == memo
    assert (await service.deliverables(mission.mission_id, USER)) == [memo]
    with pytest.raises(HTTPException) as error:
        await service.deliver(
            mission.mission_id, body.model_copy(update={"kind": "action_plan"}), USER
        )
    assert error.value.status_code == 409


async def test_schema_and_routes_are_mountable(mission_io, monkeypatch):
    io, service = mission_io
    await service.ensure_schema()
    assert [sql for sql, _ in io.statements] == list(MISSION_DDLS)
    monkeypatch.setattr("app.modules.agents.mission_router.mission_service", service)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: USER
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/studio/threads/thread/missions",
            json={
                "objective": "Inspect change",
                "operation_id": "http",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["work_intent"] == "INVESTIGATE"
        response = await client.get("/api/v1/studio/threads/thread/missions")
        assert len(response.json()["missions"]) == 1
        assert app.openapi()["paths"]["/api/v1/studio/missions/{mission_id}/objects"]


async def test_disabled_workflow_does_not_touch_metadata(mission_io, monkeypatch):
    io, service = mission_io
    monkeypatch.setattr(module, "settings", SimpleNamespace(STUDIO_BUSINESS_WORKFLOW_ENABLED=False))
    assert (
        await service.for_turn(
            "thread",
            USER,
            operation_id="op",
            objective="Investigate",
            work_intent=WorkIntent.INVESTIGATE,
        )
        is None
    )
    with pytest.raises(HTTPException) as error:
        await create(service)
    assert error.value.status_code == 404
    assert not io.statements


async def test_causal_labels_are_not_upgraded_by_projection(mission_io):
    _, service = mission_io
    mission = await create(service)
    before = copy.deepcopy(mission)
    project_event(mission, "run", "thinking", {"private_reasoning": "I know the cause"})
    assert mission == before
    project_object(
        mission,
        ObjectRef(kind="outcome", id="o", revision=1),
        {"status": "missing_data", "attribution": "unknown"},
    )
    assert mission.stages[-1].status == "running"


async def test_canonical_evidence_and_simulations_complete_their_public_stages(mission_io):
    _, service = mission_io
    mission = await create(service, public_work_steps=[StageKind.EVIDENCE, StageKind.SCENARIOS])
    project_object(
        mission,
        ObjectRef(kind="decision", id="decision", revision=2),
        {"status": "draft", "evidence": [{"id": "query"}],
         "options": [{"id": "observe", "method": "unit-economics-v1"}]},
    )
    states = {stage.kind: stage.status for stage in mission.stages}
    assert states[StageKind.EVIDENCE] == "completed"
    assert states[StageKind.SCENARIOS] == "completed"
    assert states[StageKind.DECIDE] == "running"
    assert mission.evidence_refs == ["query"]
    assert mission.status == "running"


async def test_empty_canonical_inputs_do_not_complete_evidence_or_scenarios(mission_io):
    _, service = mission_io
    mission = await create(service, public_work_steps=[StageKind.EVIDENCE, StageKind.SCENARIOS])
    project_object(
        mission, ObjectRef(kind="decision", id="decision", revision=1),
        {"status": "draft", "evidence": [], "options": []},
    )
    assert all(stage.status == "planned" for stage in mission.stages[:2])
