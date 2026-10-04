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
    MissionResume,
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
        self.resources = []
        self.statements = []

    @staticmethod
    def scoped(mission, params):
        if len(params) == 2:
            return [mission.scope.principal, mission.scope.active_role] == params
        return module.scope_params(mission.scope) == params

    async def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if sql.lstrip().startswith("CREATE TABLE"):
            return {"affected": 0}
        if "CONFIG_STUDIO_RESOURCES" in sql:
            return {"rows": [[resource] for resource in self.resources]}
        if "CONFIG_STUDIO_MISSIONS" in sql:
            if sql.startswith("INSERT"):
                item = module.Mission.model_validate_json(params[7])
                self.missions[item.mission_id] = item
                return {"affected": 1}
            if sql.startswith("UPDATE"):
                if "session_id=%s,security_version=%s" in sql:
                    item = self.missions[params[5]]
                    if item.revision != params[6] or not self.scoped(item, params[7:]):
                        return {"affected": 0}
                    self.missions[params[5]] = module.Mission.model_validate_json(params[1])
                    return {"affected": 1}
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
                return {
                    "rows": [[row[0], row[1]]]
                    if row and row[2][: len(params[1:])] == params[1:]
                    else []
                }
            return {
                "rows": [
                    [row[0]]
                    for row in self.deliverables.values()
                    if row[2][: len(params[1:])] == params[1:]
                ]
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


async def test_mission_creation_separates_objectives_and_explicitly_continues(mission_io):
    _, service = mission_io
    mission = await create(service)
    same = await service.create(
        "thread",
        MissionCreate(
            objective="Follow up",
            operation_id="request-2",
            work_intent=WorkIntent.PLAN,
            continue_mission_id=mission.mission_id,
        ),
        USER,
    )
    assert same.mission_id == mission.mission_id
    assert (await create(service)).revision == same.revision
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


async def test_turn_continuation_sink_reports_selection_and_replays(mission_io):
    io, service = mission_io
    decisions = []
    assert (
        await service.for_turn(
            "thread",
            USER,
            operation_id="simple",
            objective="Hello",
            work_intent=WorkIntent.ANSWER,
            continuation_sink=decisions.append,
        )
        is None
    )
    assert decisions[-1] == module.ContinuationDecision(mode="none", reason="lightweight_answer")
    assert not io.missions
    mission = await service.for_turn(
        "thread",
        USER,
        operation_id="root",
        objective="Inspect revenue",
        explicit=True,
        new_mission=True,
        continuation_sink=decisions.append,
    )
    assert (decisions[-1].mode, decisions[-1].reason) == ("new", "explicit_new")
    continued = await service.for_turn(
        "thread",
        USER,
        operation_id="follow-up",
        objective="Inspect a region",
        continue_mission_id=mission.mission_id,
        continuation_sink=decisions.append,
    )
    assert continued.mission_id == mission.mission_id
    assert decisions[-1] == module.ContinuationDecision(
        mode="continue", reason="explicit_continue", mission_id=mission.mission_id
    )
    for operation, objective in (("root", "Inspect revenue"), ("follow-up", "Inspect a region")):
        replay = await service.for_turn(
            "thread",
            USER,
            operation_id=operation,
            objective=objective,
            new_mission=True,
            continue_mission_id=mission.mission_id,
            continuation_sink=decisions.append,
        )
        assert replay.mission_id == mission.mission_id
        assert decisions[-1] == module.ContinuationDecision(
            mode="continue", reason="replay", mission_id=mission.mission_id
        )
    assert len(decisions) == 5
    with pytest.raises(HTTPException):
        await service.for_turn(
            "thread",
            USER,
            operation_id="conflict",
            objective="Another objective",
            new_mission=True,
            continue_mission_id=mission.mission_id,
            continuation_sink=decisions.append,
        )
    assert len(decisions) == 5


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
        await service.for_turn(
            "thread",
            USER,
            operation_id="later",
            objective="Follow up",
            continue_mission_id=mission.mission_id,
        )
    assert error.value.status_code == 409
    with pytest.raises(HTTPException):
        await service.create(
            "thread",
            MissionCreate(
                objective="Follow up", operation_id="later", continue_mission_id=mission.mission_id
            ),
            USER,
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
    reader.assert_awaited_once()
    assert reader.await_args.args == (ref, mission.scope, USER)
    assert reader.await_args.kwargs["budget"].mission_records == {}
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
        {
            "status": "draft",
            "evidence": [{"id": "query"}],
            "options": [{"id": "observe", "method": "unit-economics-v1"}],
        },
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
        mission,
        ObjectRef(kind="decision", id="decision", revision=1),
        {"status": "draft", "evidence": [], "options": []},
    )
    assert all(stage.status == "planned" for stage in mission.stages[:2])


SEMANTIC = {"view_id": "sales", "version": 1, "fingerprint": "sales-v1"}
NEW_SESSION = {**USER, "session_id": "next-login", "security_context_version": 2}


async def test_verified_anchor_continuation_and_different_target_precedence(mission_io):
    _, service = mission_io
    mission = await create(service)
    anchored = await service.record_anchor(mission.mission_id, SEMANTIC, ["revenue"], USER)
    assert await service.record_anchor(mission.mission_id, SEMANTIC, ["revenue"], USER) == anchored
    regional = await service.for_turn(
        "thread",
        USER,
        operation_id="regional",
        objective="Regional follow-up",
        work_intent=WorkIntent.INVESTIGATE,
        semantic_target={"semantic": SEMANTIC, "metrics": ["revenue"]},
        screen_follow_up=True,
    )
    assert regional.mission_id == mission.mission_id
    assert regional.continuation.reason == "semantic_anchor"
    separate = await service.for_turn(
        "thread",
        USER,
        operation_id="separate",
        objective="Inventory in Singapore",
        work_intent=WorkIntent.INVESTIGATE,
        screen_follow_up=True,
        semantic_target={"semantic": {**SEMANTIC, "view_id": "inventory"}, "metrics": ["stock"]},
    )
    assert separate.mission_id != mission.mission_id
    assert separate.continuation.reason == "different_semantic_target"


async def test_ambiguous_followup_defaults_to_new_and_answers_stay_lightweight(mission_io):
    _, service = mission_io
    original = await create(service)
    unrelated = await service.for_turn(
        "thread",
        USER,
        operation_id="other",
        objective="Plan headcount",
        work_intent=WorkIntent.PLAN,
    )
    assert unrelated.mission_id != original.mission_id
    assert unrelated.continuation.reason == "unrelated_or_ambiguous"
    assert (
        await service.for_turn(
            "thread",
            USER,
            operation_id="greeting",
            objective="Thanks",
            work_intent=WorkIntent.ANSWER,
        )
        is None
    )
    assert (
        await service.for_turn(
            "thread",
            USER,
            operation_id="research",
            objective="Explain margins",
            work_intent=WorkIntent.RESEARCH,
        )
        is None
    )


async def test_completed_mission_requires_explicit_continue_and_replay_wins(mission_io):
    io, service = mission_io
    mission = await create(service)
    io.missions[mission.mission_id].status = "completed"
    replay = await service.for_turn(
        "thread",
        USER,
        operation_id=mission.operation_id,
        objective=mission.objective,
        new_mission=True,
        continue_mission_id=mission.mission_id,
    )
    assert replay.mission_id == mission.mission_id
    continued = await service.for_turn(
        "thread",
        USER,
        operation_id="continue",
        objective="Inspect completed work",
        continue_mission_id=mission.mission_id,
    )
    assert continued.mission_id == mission.mission_id
    assert continued.continuation.reason == "explicit_continue"
    with pytest.raises(HTTPException) as error:
        await service.for_turn(
            "thread",
            USER,
            operation_id="conflict",
            objective="New",
            new_mission=True,
            continue_mission_id=mission.mission_id,
        )
    assert error.value.status_code == 422


async def test_execution_context_is_pinned_before_dispatch_and_rejects_collision(mission_io):
    io, service = mission_io
    mission = await create(service)
    add_run(io, mission)
    mission = await service.attach_run(mission.mission_id, "direct", USER)
    context = {
        "execution_now": "2026-10-04T11:00:00Z",
        "timezone": "Asia/Jakarta",
        "semantic": SEMANTIC,
        "plan_fingerprint": "a" * 64,
        "current_window": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-04T11:00:00Z"},
        "baseline_window": {"start": "2026-09-01T00:00:00Z", "end": "2026-09-04T11:00:00Z"},
        "warnings": [],
    }
    pinned = await service.record_execution_context(
        mission.mission_id, "direct", "tool", context, USER
    )
    assert pinned == await service.record_execution_context(
        mission.mission_id, "direct", "tool", context, USER
    )
    assert len(io.missions[mission.mission_id].execution_contexts) == 1
    with pytest.raises(HTTPException) as collision:
        await service.record_execution_context(
            mission.mission_id,
            "direct",
            "tool",
            {**context, "plan_fingerprint": "b" * 64},
            USER,
        )
    assert collision.value.status_code == 409
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await service.record_execution_context(
            mission.mission_id,
            "direct",
            "other",
            {**context, "filters": {"country": "private"}},
            USER,
        )


async def test_secure_resume_retains_historical_bindings_and_fences_old_session(
    mission_io, monkeypatch
):
    io, service = mission_io
    mission = await create(service, public_work_steps=[StageKind.EVIDENCE])
    add_run(io, mission, "old", status="completed", sequence=0)
    io.events["old"] = [(0, 'event: table\ndata: {"evidence_id":"historical-query"}\n\n')]
    mission = await service.attach_run(mission.mission_id, "old", USER)
    from app.modules.intelligence.actions import action_service

    monkeypatch.setattr(action_service, "revalidate", AsyncMock())
    body = MissionResume(expected_revision=mission.revision, operation_id="resume")
    resumed = await service.resume(mission.mission_id, body, NEW_SESSION)
    assert resumed.scope == module.workflow_scope(NEW_SESSION)
    assert resumed.current_binding.generation == 2
    assert resumed.run_bindings["old"] == mission.scope
    assert resumed.historical_bindings == [mission.scope]
    assert await service.resume(mission.mission_id, body, NEW_SESSION) == resumed
    projected = await service.get(mission.mission_id, NEW_SESSION)
    assert projected.evidence_refs == ["historical-query"]
    with pytest.raises(HTTPException) as old_session:
        await service.attach_run(mission.mission_id, "old", USER)
    assert old_session.value.status_code == 404
    with pytest.raises(HTTPException):
        await service.attach_run(mission.mission_id, "old", NEW_SESSION)


@pytest.mark.parametrize("replay", ["create", "turn", "follow_up"])
async def test_resume_fences_old_operation_replay_without_overwriting_binding(
    mission_io, monkeypatch, replay
):
    io, service = mission_io
    mission = await create(service)
    mission.turn_operations["follow-up"] = module.fingerprint("Regional follow up")
    io.missions[mission.mission_id] = mission
    monkeypatch.setattr(service, "_reauthorize_resume", AsyncMock())
    resumed = await service.resume(
        mission.mission_id,
        MissionResume(expected_revision=mission.revision, operation_id="resume"),
        NEW_SESSION,
    )
    before = copy.deepcopy(io.missions)
    statements = len(io.statements)
    with pytest.raises(HTTPException, match="execution binding changed") as refused:
        if replay == "create":
            await create(service)
        else:
            await service.for_turn(
                "thread",
                USER,
                operation_id="follow-up" if replay == "follow_up" else mission.operation_id,
                objective="Regional follow up" if replay == "follow_up" else mission.objective,
                work_intent=WorkIntent.ANSWER,
            )
    assert refused.value.status_code == 409
    assert io.missions == before
    assert not any(sql.startswith(("INSERT", "UPDATE")) for sql, _ in io.statements[statements:])
    assert await service.create(
        "thread",
        MissionCreate(objective=mission.objective, operation_id=mission.operation_id),
        NEW_SESSION,
    ) == resumed


async def test_resume_blocks_active_old_execution_and_revoked_semantic_access(
    mission_io, monkeypatch
):
    io, service = mission_io
    mission = await create(service)
    add_run(io, mission)
    mission = await service.attach_run(mission.mission_id, "direct", USER)
    from app.modules.intelligence.actions import action_service
    from app.modules.intelligence.engine import intelligence_service

    monkeypatch.setattr(action_service, "revalidate", AsyncMock())
    body = MissionResume(expected_revision=mission.revision, operation_id="resume")
    with pytest.raises(HTTPException) as active:
        await service.resume(mission.mission_id, body, NEW_SESSION)
    assert active.value.status_code == 409
    assert io.missions[mission.mission_id].scope == mission.scope
    io.runs["direct"]["status"] = "completed"
    mission = await service.record_anchor(mission.mission_id, SEMANTIC, ["revenue"], USER)
    monkeypatch.setattr(
        intelligence_service,
        "authorize_semantic",
        AsyncMock(side_effect=HTTPException(404, "Revoked")),
    )
    with pytest.raises(HTTPException) as revoked:
        await service.resume(
            mission.mission_id,
            body.model_copy(update={"expected_revision": mission.revision}),
            NEW_SESSION,
        )
    assert revoked.value.status_code == 404
    assert io.missions[mission.mission_id].scope == mission.scope
    assert module.write_audit_log.await_args.kwargs["status"] == "REFUSED"


@pytest.mark.parametrize(
    "change", [{"username": "bob"}, {"active_role": "other", "assigned_roles": ["other"]}]
)
async def test_resumable_summaries_and_resume_are_owner_role_scoped(mission_io, change):
    _, service = mission_io
    mission = await create(service)
    summaries = await service.resumable("thread", NEW_SESSION)
    assert summaries[0].resume_required
    assert "scope" not in summaries[0].model_dump()
    assert await service.resumable("thread", {**NEW_SESSION, **change}) == []
    with pytest.raises(HTTPException) as denied:
        await service.resume(
            mission.mission_id,
            MissionResume(expected_revision=1, operation_id="resume"),
            {**NEW_SESSION, **change},
        )
    assert denied.value.status_code == 404


async def test_legacy_mission_synthesizes_owner_current_and_history_bindings(mission_io):
    io, service = mission_io
    mission = await create(service)
    add_run(io, mission)
    mission = await service.attach_run(mission.mission_id, "direct", USER)
    raw = mission.model_dump(mode="json")
    for field in (
        "owner_scope",
        "current_binding",
        "run_bindings",
        "object_bindings",
        "historical_bindings",
    ):
        raw.pop(field, None)
    legacy = module.Mission.model_validate(raw)
    assert legacy.owner_scope.principal == USER["username"]
    assert legacy.current_binding.scope == mission.scope
    assert legacy.run_bindings == {"direct": mission.scope}


@pytest.mark.parametrize(
    "kind,source_kind",
    [
        ("investigation_report", "investigation"),
        ("scenario_comparison", "decision"),
        ("outcome_report", "outcome"),
    ],
)
async def test_deliverables_require_pinned_factual_sources_and_are_reproducible(
    mission_io, monkeypatch, kind, source_kind
):
    io, service = mission_io
    mission = await create(service)
    io.missions[mission.mission_id].evidence_refs = ["query"]
    body = DeliverableCreate(expected_revision=1, kind=kind, operation_id="report")
    with pytest.raises(HTTPException) as absent:
        await service.deliver(mission.mission_id, body, USER)
    assert absent.value.detail["code"] == "deliverable_source_unavailable"
    ref = ObjectRef(kind=source_kind, id="canonical", revision=2)
    record = {
        "status": "complete",
        "semantic": SEMANTIC,
        "evidence": [{"id": "query"}],
        "title": "Recorded",
        "hypotheses": [{"label": "Arithmetic decline", "causal_status": "arithmetic"}],
        "options": [
            {
                "id": "option",
                "description": "Recorded scenario",
                "prediction": 12,
                "method": "test",
                "assumptions": {"sensitive": "private"},
            }
        ],
        "actual": 11,
        "predicted": 12,
        "attribution": "observed_after",
        "completeness": 1,
    }
    reader = AsyncMock(return_value=record)
    monkeypatch.setattr(module, "canonical_payload", reader)
    current_reader = AsyncMock(return_value=record)
    monkeypatch.setattr(service, "canonical_read", current_reader)
    from app.modules.intelligence.engine import intelligence_service

    semantic_authorization = AsyncMock()
    monkeypatch.setattr(intelligence_service, "authorize_semantic", semantic_authorization)
    mission = await service.link(mission.mission_id, ref, 1, USER)
    body = body.model_copy(update={"expected_revision": mission.revision})
    document = await service.deliver(mission.mission_id, body, USER)
    assert document.sources[1].revision == 2
    assert document.sources[1].semantic.view_id == "sales"
    assert "private" not in document.model_dump_json()
    calls = reader.await_count
    assert await service.deliver(mission.mission_id, body, USER) == document
    assert reader.await_count == calls
    current_reader.assert_awaited_once_with(mission.mission_id, ref, USER, budget=None)
    semantic_authorization.assert_awaited_once()
    current_reader.side_effect = HTTPException(403, "Revoked")
    with pytest.raises(HTTPException) as revoked:
        await service.deliver(mission.mission_id, body, USER)
    assert revoked.value.status_code == 403


async def test_smart_child_pins_context_under_root_mission(mission_io, monkeypatch):
    io, service = mission_io
    mission = await create(service)
    add_run(io, mission, "root", agent="__smart__")
    add_run(io, mission, "child", agent="finance")
    mission = await service.attach_run(mission.mission_id, "root", USER)
    monkeypatch.setattr(
        module.harness_repository,
        "get",
        AsyncMock(
            return_value={
                "run_id": "child",
                "root_run_id": "root",
                "thread_id": "thread",
                "owner_name": "alice",
                "role_name": "analyst",
                "session_id": "login",
                "security_version": 1,
            }
        ),
    )
    pinned = await service.record_execution_context(
        mission.mission_id,
        "child",
        "tool",
        {
            "execution_now": "2026-10-04T11:00:00Z",
            "timezone": "Asia/Jakarta",
            "semantic": SEMANTIC,
            "plan_fingerprint": "a" * 64,
        },
        USER,
    )
    assert pinned.run_id == "child"
    assert io.missions[mission.mission_id].run_ids == ["root"]


async def test_missing_inputs_are_bounded_persisted_and_cleared_by_canonical_link(
    mission_io, monkeypatch
):
    from app.modules.assistant.evidence_health import EvidenceFacts, assess_evidence

    _, service = mission_io
    mission = await create(service)
    health = assess_evidence(
        EvidenceFacts(execution_status="success", coverage="complete"),
        assessed_at=module.utc_now(),
    )
    established = {
        "health": health.model_dump(mode="json"),
        "semantic": SEMANTIC,
        "metrics": ["revenue"],
        "validated_plan_fingerprint": "a" * 64,
        "model_fingerprint": "sales-v1",
    }
    pending = await service.record_investigation_requirements(
        mission.mission_id, ["comparison_windows"], established, USER
    )
    assert pending.investigation_requirements.required_inputs == ["comparison_windows"]
    assert (
        await service.record_investigation_requirements(
            mission.mission_id, ["comparison_windows"], established, USER
        )
        == pending
    )
    monkeypatch.setattr(
        module,
        "canonical_payload",
        AsyncMock(return_value={"status": "complete", "evidence": [{"id": "query"}]}),
    )
    linked = await service.link(
        mission.mission_id,
        ObjectRef(kind="investigation", id="inv", revision=1),
        pending.revision,
        USER,
    )
    assert linked.investigation_requirements is None


async def test_canonical_reads_require_exact_mission_pin(mission_io, monkeypatch):
    from app.modules.intelligence.engine import intelligence_service

    _, service = mission_io
    mission = await create(service)
    monkeypatch.setattr(module, "canonical_payload", AsyncMock(return_value={"status": "complete"}))
    reference = ObjectRef(kind="investigation", id="inv", revision=2)
    mission = await service.link(mission.mission_id, reference, mission.revision, USER)
    reader = AsyncMock(
        return_value=SimpleNamespace(model_dump=lambda **kwargs: {"id": "inv", "revision": 2})
    )
    monkeypatch.setattr(intelligence_service, "get_for_mission", reader)
    assert await service.canonical_read(mission.mission_id, reference, USER) == {
        "id": "inv",
        "revision": 2,
    }
    reader.assert_awaited_once_with(
        "investigations", "inv", USER, mission_id=mission.mission_id, revision=2, budget=None
    )
    with pytest.raises(HTTPException):
        await service.canonical_read(
            mission.mission_id, reference.model_copy(update={"revision": 3}), USER
        )


async def test_population_target_and_standalone_same_metric_do_not_reuse_mission(mission_io):
    _, service = mission_io
    mission = await create(service)
    original = "a" * 64
    await service.record_anchor(
        mission.mission_id, SEMANTIC, ["revenue"], USER, filter_population_fingerprint=original
    )
    followup = await service.for_turn(
        "thread",
        USER,
        operation_id="regions",
        objective="Regional breakdown",
        work_intent=WorkIntent.INVESTIGATE,
        screen_follow_up=True,
        semantic_target={
            "semantic": SEMANTIC,
            "metrics": ["revenue"],
            "filter_population_fingerprint": original,
        },
    )
    assert followup.mission_id == mission.mission_id
    standalone = await service.for_turn(
        "thread",
        USER,
        operation_id="standalone",
        objective="Separate revenue objective",
        work_intent=WorkIntent.INVESTIGATE,
        semantic_target={
            "semantic": SEMANTIC,
            "metrics": ["revenue"],
            "filter_population_fingerprint": original,
        },
    )
    assert standalone.mission_id != mission.mission_id
    assert standalone.continuation.reason == "unrelated_or_ambiguous"
    singapore = await service.for_turn(
        "thread",
        USER,
        operation_id="singapore",
        objective="Singapore revenue objective",
        work_intent=WorkIntent.INVESTIGATE,
        screen_follow_up=True,
        semantic_target={
            "semantic": SEMANTIC,
            "metrics": ["revenue"],
            "filter_population_fingerprint": "b" * 64,
        },
    )
    assert singapore.mission_id != mission.mission_id
    assert singapore.continuation.reason == "different_semantic_target"


async def test_mission_agent_identity_is_sourced_from_authorized_thread(mission_io, monkeypatch):
    _, service = mission_io
    monkeypatch.setattr(module, "require_thread", AsyncMock(return_value={"agent_id": "finance"}))
    mission = await create(service)
    assert mission.agent_id == "finance"
    from app.modules.intelligence.actions import action_service

    monkeypatch.setattr(action_service, "revalidate", AsyncMock())
    monkeypatch.setattr(
        module, "require_thread", AsyncMock(return_value={"agent_id": "other-agent"})
    )
    with pytest.raises(HTTPException) as changed:
        await service.resume(
            mission.mission_id,
            MissionResume(expected_revision=1, operation_id="resume"),
            NEW_SESSION,
        )
    assert changed.value.status_code == 409


async def test_resume_checks_latest_action_uncertainty_not_only_historical_pin(
    mission_io, monkeypatch
):
    io, service = mission_io
    mission = await create(service)
    ref = ObjectRef(kind="action", id="action", revision=1)
    io.missions[mission.mission_id].object_refs = [ref]
    from app.modules.intelligence.actions import action_service
    from app.modules.intelligence.engine import intelligence_service

    monkeypatch.setattr(action_service, "revalidate", AsyncMock())
    monkeypatch.setattr(
        intelligence_service,
        "get_for_mission",
        AsyncMock(return_value=SimpleNamespace(status="awaiting_approval", scope=mission.scope)),
    )
    monkeypatch.setattr(
        intelligence_service.repository,
        "get",
        AsyncMock(return_value=SimpleNamespace(status="verification_required")),
    )
    with pytest.raises(HTTPException) as uncertain:
        await service.resume(
            mission.mission_id,
            MissionResume(expected_revision=1, operation_id="resume"),
            NEW_SESSION,
        )
    assert uncertain.value.status_code == 409
    assert io.missions[mission.mission_id].scope == mission.scope


async def test_updating_object_pin_preserves_old_deliverable_reference(mission_io, monkeypatch):
    _, service = mission_io
    mission = await create(service)
    monkeypatch.setattr(
        module,
        "canonical_payload",
        AsyncMock(return_value={"status": "complete", "evidence": [{"id": "q1"}]}),
    )
    older = ObjectRef(kind="investigation", id="inv", revision=1)
    mission = await service.link(mission.mission_id, older, mission.revision, USER)
    newer = older.model_copy(update={"revision": 2})
    mission = await service.link(mission.mission_id, newer, mission.revision, USER)
    assert mission.object_refs == [newer]
    assert mission.historical_object_refs == [older]
    assert set(module.object_binding_key(ref) for ref in mission.pinned_objects) == {
        "investigation:inv:1",
        "investigation:inv:2",
    }
    assert mission.object_bindings["investigation:inv:1"] == mission.scope


async def test_link_uses_exact_dependency_bindings_but_current_root_scope(mission_io, monkeypatch):
    io, service = mission_io
    mission = await create(service)
    historical = ObjectRef(kind="decision", id="decision", revision=3)
    old_scope = mission.scope.model_copy()
    stored = io.missions[mission.mission_id]
    stored.object_refs = [historical]
    stored.object_bindings[module.object_binding_key(historical)] = old_scope
    monkeypatch.setattr(service, "_reauthorize_resume", AsyncMock())
    resumed = await service.resume(
        mission.mission_id,
        MissionResume(expected_revision=mission.revision, operation_id="resume"),
        NEW_SESSION,
    )
    reader = AsyncMock(return_value={"status": "complete"})
    monkeypatch.setattr(module, "canonical_payload", reader)
    outcome = ObjectRef(kind="outcome", id="outcome", revision=1)
    linked = await service.link(mission.mission_id, outcome, resumed.revision, NEW_SESSION)
    assert reader.await_args.args == (outcome, resumed.scope, NEW_SESSION)
    budget = reader.await_args.kwargs["budget"]
    assert budget.mission_records == {("decisions", "decision"): 3}
    assert budget.mission_record_bindings == {("decisions", "decision"): old_scope}
    assert budget.mission_bindings == [old_scope]
    assert linked.object_bindings[module.object_binding_key(outcome)] == resumed.scope
    assert linked.object_bindings[module.object_binding_key(historical)] == old_scope


async def test_deliverable_authorization_forwards_one_budget_to_all_sources(
    mission_io, monkeypatch
):
    from app.modules.intelligence.engine import CycleBudget, intelligence_service

    _, service = mission_io
    mission = await create(service)
    ref = ObjectRef(kind="decision", id="decision", revision=2)
    document = module.MissionDeliverable(
        deliverable_id="document",
        mission_id=mission.mission_id,
        mission_revision=mission.revision,
        kind="decision_memo",
        title="Recorded decision",
        markdown="Recorded decision",
        evidence_refs=[],
        object_refs=[ref],
        created_at=mission.created_at,
        sources=[
            module.DeliverableSource(
                kind="decision",
                id=ref.id,
                revision=ref.revision,
                fingerprint="a" * 64,
                semantic=SEMANTIC,
                facts={},
            )
        ],
    )
    reader = AsyncMock()
    authorize = AsyncMock()
    monkeypatch.setattr(service, "canonical_read", reader)
    monkeypatch.setattr(intelligence_service, "authorize_semantic", authorize)
    budget = CycleBudget()
    await service._authorize_deliverable(mission.mission_id, document, USER, budget=budget)
    reader.assert_awaited_once_with(mission.mission_id, ref, USER, budget=budget)
    authorize.assert_awaited_once_with(document.sources[0].semantic, USER, budget=budget)


@pytest.mark.parametrize("failure", ["active_child", "attachment", "revoked_participant"])
async def test_smart_resume_reauthorizes_tree_and_root_attachment_without_payload_refs(
    mission_io, monkeypatch, failure
):
    io, service = mission_io
    mission = await create(service)
    add_run(io, mission, "root", agent="__smart__", status="completed")
    mission = await service.attach_run(mission.mission_id, "root", USER)
    root = {
        "run_id": "root",
        "root_run_id": None,
        "agent_id": "__smart__",
        "owner_name": "alice",
        "role_name": "analyst",
        "session_id": "login",
        "security_version": 1,
        "thread_id": "thread",
        "status": "completed",
        "payload": {},
    }
    child = {**root, "run_id": "child", "root_run_id": "root"}
    if failure == "active_child":
        child["status"] = "running"
    if failure == "revoked_participant":
        child["agent_id"] = "finance"
        monkeypatch.setattr(module.agent_repository, "get_agent", AsyncMock(return_value=None))
        monkeypatch.setattr(
            module.agent_repository, "get_shared_agent", AsyncMock(return_value=None)
        )
    monkeypatch.setattr(module.harness_repository, "tree", AsyncMock(return_value=[root, child]))
    from app.modules.agents.resource_delegation import resource_delegation
    from app.modules.intelligence.actions import action_service

    monkeypatch.setattr(action_service, "revalidate", AsyncMock())
    resource_read = AsyncMock(side_effect=ValueError("Attachment security context changed"))
    monkeypatch.setattr(resource_delegation, "reauthorize_for_mission", resource_read)
    if failure == "attachment":
        io.resources = ["registered-root-resource"]
    with pytest.raises(HTTPException) as refused:
        await service.resume(
            mission.mission_id,
            MissionResume(expected_revision=mission.revision, operation_id="resume-smart"),
            NEW_SESSION,
        )
    assert (
        refused.value.status_code
        == {"active_child": 409, "attachment": 403, "revoked_participant": 404}[failure]
    )
    assert io.missions[mission.mission_id].scope == mission.scope
    if failure == "attachment":
        resource_read.assert_awaited_once_with(mission.mission_id, "root", NEW_SESSION)
    else:
        resource_read.assert_not_awaited()


async def test_completed_turn_preserves_unfinished_business_stages(mission_io):
    io, service = mission_io
    mission = await create(
        service,
        work_intent=WorkIntent.PLAN,
        public_work_steps=[StageKind.EVIDENCE, StageKind.DECIDE, StageKind.EXECUTE],
    )
    add_run(io, mission, status="completed", sequence=1)
    io.events["direct"] = [
        (0, 'event: table\ndata: {"evidence_id":"query"}\n\n'),
        (1, 'event: done\ndata: {"status":"complete"}\n\n'),
    ]
    await service.attach_run(mission.mission_id, "direct", USER)
    projected = await service.get(mission.mission_id, USER)
    assert projected.status == "blocked"
    stages = {stage.kind: stage.status for stage in projected.stages}
    assert stages == {
        StageKind.EVIDENCE: "completed",
        StageKind.DECIDE: "planned",
        StageKind.EXECUTE: "planned",
    }


@pytest.mark.parametrize("uncertain", [False, True])
async def test_resumed_mission_cancel_keeps_historical_action_read_only(
    mission_io, monkeypatch, uncertain
):
    io, service = mission_io
    mission = await create(service)
    ref = ObjectRef(kind="action", id="historical-action", revision=1)
    stored = io.missions[mission.mission_id]
    stored.object_refs = [ref]
    stored.object_bindings[module.object_binding_key(ref)] = stored.scope.model_copy()
    stored.scope = module.workflow_scope(NEW_SESSION)
    stored.current_binding.scope = stored.scope
    monkeypatch.setattr(
        service,
        "canonical_read",
        AsyncMock(return_value={"status": "verification_required" if uncertain else "approved"}),
    )
    from app.modules.intelligence.actions import action_service

    cancel = AsyncMock()
    monkeypatch.setattr(action_service, "cancel", cancel)
    if uncertain:
        with pytest.raises(HTTPException) as refused:
            await service.cancel(mission.mission_id, stored.revision, NEW_SESSION)
        assert refused.value.status_code == 409
        assert not io.missions[mission.mission_id].cancellation_complete
    else:
        cancelled = await service.cancel(mission.mission_id, stored.revision, NEW_SESSION)
        assert cancelled.status == "cancelled"
    cancel.assert_not_awaited()


async def test_oversized_deliverable_source_returns_bounded_error(mission_io, monkeypatch):
    io, service = mission_io
    mission = await create(service)
    ref = ObjectRef(kind="investigation", id="investigation", revision=1)
    stored = io.missions[mission.mission_id]
    stored.object_refs = [ref]
    stored.object_bindings[module.object_binding_key(ref)] = stored.scope.model_copy()
    stored.evidence_refs = ["query"]
    monkeypatch.setattr(
        module,
        "canonical_payload",
        AsyncMock(
            return_value={
                "status": "complete",
                "hypotheses": [{"label": "private" * 5000, "causal_status": "arithmetic"}],
            }
        ),
    )
    with pytest.raises(HTTPException) as refused:
        await service.deliver(
            mission.mission_id,
            DeliverableCreate(
                expected_revision=1, kind="investigation_report", operation_id="bounded"
            ),
            USER,
        )
    assert refused.value.status_code == 422
    assert refused.value.detail == {"code": "deliverable_source_bound_exceeded"}
    assert not io.deliverables
