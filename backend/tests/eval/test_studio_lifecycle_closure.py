"""One governed journey composes the live owners across turns, replay, and resume.

Model responses, database IO, and governed observations are deterministic seams;
Mission, semantic compilation, Investigation, Decision, Action readback, Outcome,
and reviewed proposal behavior run through their production owners.
"""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.modules.agents import business_results, mission, releases, router
from app.modules.agents.memory import memory_repository
from app.modules.agents.mission_schema import MissionResume, ObjectRef, StageKind
from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic import learning_sources
from app.modules.agents.semantic.access import load_authorized_models
from app.modules.agents.semantic.ir import (
    Additivity,
    FieldKind,
    SemanticFieldIR,
    SemanticMetricIR,
    SemanticModelIR,
)
from app.modules.agents.semantic.runtime import semantic_ir_to_definition
from app.modules.agents.tools.semantic_query import SemanticQueryTool
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread, ConsentPolicy
from app.modules.assistant.tools import ToolRegistry
from app.modules.intelligence import actions, autopilot, decisions, engine, scenarios, schedules
from app.modules.intelligence.action_contracts import ActionOperation, ActionPreview, ActionReview
from app.modules.intelligence.contracts import (
    Monitor,
    MonitorConfiguration,
    Scope,
    SemanticRef,
    Window,
)
from app.modules.intelligence.engine_schema import ENGINE_TABLES
from app.modules.intelligence.monitor_action import MonitorActionAdapter
from app.modules.ml_engine.service import ml_engine_service
from app.modules.query.service import query_service
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import TurnResult
from tests.unit.test_business_actions import Lease
from tests.unit.test_intelligence_decisions import program as program
from tests.unit.test_intelligence_engine import END, USER
from tests.unit.test_scenario_registry import CapacityScenarioAdapter
from tests.unit.test_semantic_intelligence import sales_model
from tests.unit.test_studio_missions import mission_io as mission_io

NOW = END + timedelta(days=1)
PRIVATE = "journey-private-credential"
STEPS = [stage.value for stage in StageKind]


class RevenueCapacityAdapter(CapacityScenarioAdapter):
    def definition(self):
        return super().definition().model_copy(update={"target_metric": "revenue"})


def semantic_plan(dimension="city"):
    return {
        "metrics": ["revenue"],
        "dimensions": [dimension],
        "filters": [],
        "named_filters": [],
        "order_by": [],
        "limit": None,
        "unresolved_concepts": [],
        "time": {
            "dimension": "ordered_at",
            "grain": None,
            "range": "2026-09-20..2026-09-20",
            "compare": "previous_period",
        },
    }


def event_payloads(result, kind):
    import json

    return [
        json.loads(frame.split("data: ", 1)[1])
        for frame in result.frames
        if frame.startswith(f"event: {kind}\n")
    ]


class Journey:
    def __init__(self, service, repo, source, io, missions, task_repo, policy, users):
        self.service, self.repo, self.source = service, repo, source
        self.io, self.missions, self.task_repo = io, missions, task_repo
        self.policy, self.users = policy, users
        self.turns, self.consent, self.sql = [], [], []

    async def turn(
        self,
        run_id,
        prompt,
        user,
        *,
        view=None,
        dimension="city",
        tool_call=None,
        continue_mission_id=None,
        consent=True,
        previous_answer=False,
        finish_reason="stop",
        save_state=None,
    ):
        selected = (
            ["semantic_query"] if view else (["execute_business_action"] if tool_call else [])
        )
        plan = {
            "intent": "semantic_analytics"
            if view
            else ("ui_operation" if tool_call else "direct_answer"),
            "tools": selected,
            "required_tools": selected,
            "skills": [],
            "ml_task": None,
            "work_intent": "INVESTIGATE" if view else ("ACT" if tool_call else "ANSWER"),
            "public_work_steps": STEPS if view else [],
            "primary_plan": semantic_plan(dimension) if view else None,
            "primary_view": view,
        }
        provider = ScriptedProvider(
            [*([tool_call] if tool_call else []), text_frame("The governed work is available.")],
            turn_plan=plan,
            intent_frame={"refers_to_previous_answer": True} if previous_answer else None,
        )
        registry = ToolRegistry()
        registry.register(SemanticQueryTool(provider=provider))
        registry.register(actions.BusinessActionTool(actions.action_service))
        context = LoopContext(
            user_name=user["username"],
            user=user,
            role=user["active_role"],
            session_id=user["session_id"],
            thread_id="thread",
            run_id=run_id,
            agent_id="finance",
            agent_owner_name=user["username"],
            semantic_view_ids=["sales", "singapore"],
            execution_now=NOW,
            business_result_hook=business_results.governed_result,
            business_time_hook=business_results.pin_time,
            business_clock_hook=business_results.execution_clock,
        )
        await load_authorized_models(context)
        self.io.runs[run_id] = {
            "thread_id": "thread",
            "scope": mission.scope_params(Scope.from_user(user)),
            "agent_id": "finance",
            "status": "running",
            "sequence": -1,
            "primary_plan": plan["primary_plan"],
        }

        async def start(plan, current):
            chosen = await self.missions.for_turn(
                "thread",
                user,
                operation_id=run_id,
                objective=prompt,
                work_intent=plan.work_intent,
                public_work_steps=plan.public_work_steps,
                continue_mission_id=continue_mission_id,
                semantic_target=business_results.planner_target(plan, current),
                continuation_sink=lambda choice: setattr(
                    current, "mission_continuation", choice.model_dump(mode="json")
                ),
                screen_follow_up=bool(
                    plan.intent_frame
                    and (
                        plan.intent_frame.refers_to_screen
                        or plan.intent_frame.refers_to_previous_answer
                    )
                ),
            )
            if chosen:
                chosen = await self.missions.attach_run(chosen.mission_id, run_id, user)
                return chosen.model_dump(mode="json")
            return None

        async def authorize(call, classification):
            self.consent.append((run_id, call.tool_name, classification, user["session_id"]))
            return consent

        context.business_turn_hook = start
        thread = AssistantThread("thread", user["username"], "Revenue lifecycle")
        result = TurnResult(
            frames=[
                frame
                async for frame in AssistantLoop(
                    provider=provider, registry=registry, system_prompt="Use governed evidence."
                ).run(
                    thread=thread, user_content=prompt, context=context, resolve_consent=authorize,
                    save_state=save_state,
                )
            ]
        )
        self.io.events[run_id] = list(enumerate(result.frames))
        self.io.runs[run_id].update(status="completed", sequence=len(result.frames) - 1)
        self.turns.append((provider, result, context, thread))
        assert result.finish_reason == finish_reason, event_payloads(result, "error")
        assert not result.error_codes
        assert PRIVATE not in "".join(result.frames)
        return result, context

    async def link(self, mission_id, kind, record, user):
        current = await self.missions.get(mission_id, user, project=False)
        return await self.missions.link(
            mission_id,
            ObjectRef(kind=kind, id=record.id, revision=record.revision),
            current.revision,
            user,
        )


@pytest.fixture
async def journey(program, mission_io, monkeypatch):
    service, repo, source, policy, _ = program
    io, missions = mission_io
    repo.rows.clear()
    repo.history.clear()
    source.calls.clear()
    read_record, save_record, related_records = repo.get, repo.save, repo.related

    def storage_scope(scope):
        return scope.model_dump(exclude={"session_id"})

    async def scoped_get(kind, identifier, scope, model, *, revision=None):
        row = await read_record(kind, identifier, scope, model, revision=revision)
        return row if row and storage_scope(row.scope) == storage_scope(scope) else None

    async def scoped_save(kind, record, *, expected_revision=0):
        prior = repo.rows.get((kind, record.id))
        if prior and storage_scope(prior.scope) != storage_scope(record.scope):
            raise HTTPException(status_code=404, detail="Record unavailable")
        return await save_record(kind, record, expected_revision=expected_revision)

    async def scoped_related(kind, identifier, scope, model):
        return [
            row
            for row in await related_records(kind, identifier, scope, model)
            if storage_scope(row.scope) == storage_scope(scope)
        ]

    monkeypatch.setattr(repo, "get", scoped_get)
    monkeypatch.setattr(repo, "save", scoped_save)
    monkeypatch.setattr(repo, "related", scoped_related)
    user = {**USER, "encrypted_password": PRIVATE}
    users = {user["session_id"]: user}
    model = replace(sales_model(), version="0.1.1")
    ordered = replace(
        model.datasets[0],
        fields=(
            *tuple(
                replace(field, name="ordered_at", expression="ordered_at")
                if field.is_time
                else field
                for field in model.datasets[0].fields
            ),
            SemanticFieldIR(
                name="complete", dataset="orders", expression="complete", kind=FieldKind.FACT
            ),
        ),
    )
    model = replace(
        model,
        datasets=(ordered, *model.datasets[1:]),
        metrics=(
            replace(model.metrics[0], name="revenue", default_time_dimension="ordered_at"),
            SemanticMetricIR(name="orders", expression="COUNT(*)", base_dataset="orders"),
            SemanticMetricIR(
                name="coverage",
                expression="MIN(orders.complete)",
                base_dataset="orders",
                additivity=Additivity.NON_ADDITIVE,
            ),
        ),
        examples=(),
    )
    definition = semantic_ir_to_definition(model)
    definition["metrics"][0]["currency"] = "IDR"
    fingerprint = SemanticModelIR.from_ossie(definition).fingerprint
    models = {
        view: {
            "id": view,
            "semantic_model_id": view,
            "name": view,
            "version": 1,
            "fingerprint": fingerprint,
            "status": "ACTIVE",
            "definition": definition,
        }
        for view in ("sales", "singapore")
    }

    async def readable(view_id, version, current_user):
        assert Scope.from_user(current_user).principal == user["username"]
        assert version == 1 and view_id in models and not source.revoked
        return {"active_version": 1}, {**models[view_id]}

    original_execute = source.execute_plan

    async def observe(view_id, version, plan, current_user):
        value = await original_execute(view_id, version, plan, current_user)
        value["model_fingerprint"] = fingerprint
        current = any(
            item.operator == ">=" and str(item.value).startswith("2026-09-20")
            for item in plan.filters
        )
        if plan.dimensions:
            value["columns"][0] = plan.dimensions[0]
            value["rows"][0][1] = 10 if current else 50
            if plan.dimensions[0] == "region":
                value["rows"][0][0], value["rows"][1][0] = "West", "East"
        else:
            value["rows"][0][0] = 60 if current else 100
        # This source has a reviewed completeness metric; extra aggregate columns
        # are bounded IO, and the Monitor chooses which ones have authority.
        if not plan.dimensions:
            value["columns"].append("coverage")
            for row in value["rows"]:
                row.append(1)
        return value

    source._readable_version, source.execute_plan = readable, observe
    monkeypatch.setattr(engine, "intelligence_service", service)
    monkeypatch.setattr(learning_sources, "intelligence_service", service)
    monkeypatch.setattr(business_results, "intelligence_service", service)
    monkeypatch.setattr(mission, "mission_service", missions)
    monkeypatch.setattr(business_results, "mission_service", missions)
    monkeypatch.setattr(engine, "utc_now", lambda: NOW)
    monkeypatch.setattr(decisions, "utc_now", lambda: NOW)
    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    monkeypatch.setattr(settings, "STUDIO_ACTIONS_ENABLED", True)
    monkeypatch.setattr(
        scenarios,
        "scenario_registry",
        scenarios.ScenarioRegistry(
            (
                scenarios.UnitEconomicsScenarioAdapter(),
                RevenueCapacityAdapter(),
            )
        ),
    )
    monkeypatch.setattr(ml_engine_service.repository, "record_run", AsyncMock())
    agent = {
        "agent_id": "finance",
        "owner_name": user["username"],
        "semantic_view_ids": list(models),
    }
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value=agent))
    monkeypatch.setattr(
        mission,
        "require_thread",
        AsyncMock(
            return_value={
                "thread_id": "thread",
                "agent_id": "finance",
            }
        ),
    )
    monkeypatch.setattr(agent_repository, "get_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(releases, "load_runtime_manifest", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_repository, "list_verified_queries", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_repository, "record_semantic_usage", AsyncMock())
    monkeypatch.setattr(assistant_repository, "learning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        engine.semantic_view_service,
        "get_active_for_agent",
        AsyncMock(
            side_effect=lambda view_id, *args, **kwargs: deepcopy(models[view_id]),
        ),
    )
    monkeypatch.setattr(engine.semantic_view_service, "_readable_version", readable)
    canonical_tables = {table: kind for kind, table in ENGINE_TABLES.items()}

    async def execute(sql, params=None):
        if "SELECT payload FROM" in sql and "CONFIG_STUDIO_" not in sql:
            table = sql.split("NOVA_SYSTEM.", 1)[1].split()[0]
            if table in canonical_tables:
                kind = canonical_tables[table]
                row = repo.history.get((kind, params[0], params[4]))
                if (
                    row
                    and [
                        row.scope.principal,
                        row.scope.active_role,
                        row.scope.security_context_version,
                    ]
                    == params[1:4]
                ):
                    return {"rows": [[row.model_dump_json()]]}
                return {"rows": []}
        return await io.execute(sql, params)

    monkeypatch.setattr(mission.db, "execute_system", execute)
    lease, tasks = Lease(), {}

    @asynccontextmanager
    async def lock(*args, **kwargs):
        yield lease

    async def find(name, database, schema):
        return next((deepcopy(task) for task in tasks.values() if task["name"] == name), None)

    async def create_task(value, user_name):
        task = {
            "database_name": None,
            "schema_name": None,
            "when_expr": None,
            **deepcopy(value),
            "id": "scheduled-monitor",
            "created_by": user_name,
        }
        tasks[task["id"]] = task
        return deepcopy(task)

    task_repo = SimpleNamespace(
        find_task=AsyncMock(side_effect=find),
        create_task=AsyncMock(side_effect=create_task),
        get_role_execution_user=AsyncMock(return_value=user["username"]),
    )
    monkeypatch.setattr(schedules, "metadata_lock", lock)
    monkeypatch.setattr(schedules, "task_orchestration_repository", task_repo)
    monkeypatch.setattr(actions, "metadata_lock", lock)
    monkeypatch.setattr(actions, "write_audit_log", AsyncMock())
    monkeypatch.setattr(actions, "read_business_policy", AsyncMock(side_effect=lambda: policy))
    monkeypatch.setattr(
        actions.session_store,
        "get",
        AsyncMock(
            side_effect=lambda session_id: deepcopy(users.get(session_id)),
        ),
    )
    repo.action_for_review = AsyncMock(
        side_effect=lambda ident, model: deepcopy(repo.rows.get(("actions", ident))),
    )
    action_service = actions.ActionService(
        service,
        {
            "monitor-v1": MonitorActionAdapter(service, task_repo),
        },
    )
    monkeypatch.setattr(actions, "action_service", action_service)
    result = Journey(service, repo, source, io, missions, task_repo, policy, users)

    async def query(**kwargs):
        current = next(
            item
            for item in io.missions.values()
            if any(io.runs[run]["status"] == "running" for run in item.run_ids)
        )
        assert current.execution_contexts, "Concrete time must be durable before SQL dispatch"
        pinned = current.execution_contexts[-1]
        sql = kwargs["sql"]
        result.sql.append((sql, pinned.model_copy(deep=True), kwargs))
        assert "CURRENT_DATE" not in sql
        for window in (pinned.current_window, pinned.baseline_window):
            assert window is not None
            for bound in (window.start, window.end):
                assert (
                    bound.astimezone(ZoneInfo(pinned.timezone)).strftime("%Y-%m-%d %H:%M:%S.%f")
                    in sql
                )
        assert kwargs["username"] == user["username"]
        assert kwargs["role"] == user["active_role"]
        dimension = io.runs[pinned.run_id]["primary_plan"]["dimensions"][0]
        labels = ("West", "East") if dimension == "region" else ("Jakarta", "Bandung")
        return [
            SimpleNamespace(
                columns=[dimension, "revenue", "comparison_period"],
                rows=[
                    [labels[0], 10, "current"],
                    [labels[1], 50, "current"],
                    [labels[0], 50, "baseline"],
                    [labels[1], 50, "baseline"],
                ],
                row_count=4,
                error=None,
                truncated=False,
            )
        ]

    monkeypatch.setattr(query_service, "execute_statements", AsyncMock(side_effect=query))
    await service.register_monitor(
        Monitor(
            id="reviewed-revenue",
            scope=Scope.from_user(user),
            name="Reviewed revenue",
            agent_id="finance",
            semantic=SemanticRef(view_id="sales", version=1, fingerprint=fingerprint),
            plan={"metrics": ["revenue", "orders", "coverage"]},
            value_column="revenue",
            count_column="orders",
            completeness_column="coverage",
            time_dimension="ordered_at",
            driver_dimensions=["city"],
            timezone="Asia/Jakarta",
            enabled=False,
        ),
        user,
    )
    result.user, result.models, result.tasks = user, models, tasks
    return result


@pytest.mark.asyncio
async def test_governed_lifecycle_survives_objective_separation_replay_and_next_session(
    journey,
    monkeypatch,
):
    j, user = journey, journey.user
    simple, context = await j.turn("answer-1", "What is an Investigation?", user)
    assert context.mission_id is None and not j.io.missions
    assert not simple.tool_calls_proposed and not j.source.calls
    assert event_payloads(simple, "mission_continuation")[0]["continuation"] == {
        "mode": "none", "reason": "lightweight_answer", "mission_id": None,
    }

    revenue, context = await j.turn(
        "revenue-1",
        "Investigate revenue for September 20 against September 19",
        user,
        view="sales",
    )
    mission_id = context.mission_id
    root = await j.missions.get(mission_id, user)
    assert root.semantic_anchors[0].semantic.view_id == "sales"
    assert root.semantic_anchors[0].metrics == ["revenue"]
    assert root.semantic_anchors[0].filter_population_fingerprint is not None
    pin = next(ref for ref in root.object_refs if ref.kind == "investigation")
    investigation = await j.service.get("investigations", pin.id, user, revision=pin.revision)
    assert investigation.status == "complete" and investigation.hypotheses
    assert all(item.causal_status == "arithmetic" for item in investigation.hypotheses)
    news = await j.service.get(
        "news", investigation.news_id, user, revision=investigation.news_revision
    )
    assert news.monitor_id == "reviewed-revenue" and (news.before, news.after) == (100, 60)
    envelope = event_payloads(revenue, "evidence_envelope")[0]["payload"]
    fixed = root.execution_contexts[0]
    assert envelope["current_window"] == fixed.current_window.model_dump(mode="json")
    assert envelope["baseline_window"] == fixed.baseline_window.model_dump(mode="json")
    assert envelope["semantic"] == investigation.semantic.model_dump(mode="json")

    _, regional = await j.turn(
        "regions-1",
        "Break down that revenue by region",
        user,
        view="sales",
        dimension="region",
        previous_answer=True,
    )
    assert regional.mission_id == mission_id
    continued = await j.missions.get(mission_id, user)
    assert continued.continuation.reason == "semantic_anchor"
    _, singapore = await j.turn(
        "singapore-1",
        "Investigate the Singapore revenue objective",
        user,
        view="singapore",
        previous_answer=True,
    )
    assert singapore.mission_id != mission_id and len(j.io.missions) == 2
    separate = await j.missions.get(singapore.mission_id, user)
    assert separate.continuation.reason == "different_semantic_target"
    singapore_pin = next(ref for ref in separate.object_refs if ref.kind == "investigation")
    singapore_investigation = await j.service.get("investigations", singapore_pin.id, user)
    singapore_news = await j.service.get("news", singapore_investigation.news_id, user)
    singapore_monitor = await j.service.get("monitors", singapore_news.monitor_id, user)
    assert singapore_monitor.count_column is None and not singapore_monitor.enabled
    assert all(
        item.confidence.label == "insufficient" for item in singapore_investigation.hypotheses
    )
    parameters = {
        "action_type": "capacity_upgrade",
        "baseline": news.after,
        "additional_capacity": 40,
        "action_cost": 5,
    }
    preview = await scenarios.run_scenario(
        scenarios.ScenarioRequest(
            operation_id="scenario-preview",
            scenario_kind="capacity",
            parameters=parameters,
        ),
        user,
    )
    assert preview["prediction"] == 100 and preview["effects"] == {"capacity_delta": 40}
    assert preview["causal_status"] == "unknown" and not preview["evidence_ids"]

    request = decisions.DecisionCreate(
        operation_id="decision-journey",
        title="Restore regional capacity",
        investigation_id=investigation.id,
        investigation_revision=investigation.revision,
        mission_id=mission_id,
        thread_id="thread",
        learning_enabled=True,
        outcome_window=Window(start=NOW + timedelta(days=1), end=NOW + timedelta(days=2)),
        options=[
            decisions.OptionInput(
                id="transfer",
                description="Transfer inventory",
                scenario_kind="capacity",
                parameters=parameters,
            )
        ],
    )
    decision = await decisions.create_decision(request, user)
    assert decision.baseline == news.after
    assert decision.options[0].evidence_ids == [item.id for item in investigation.evidence]
    assert decision.options[0].scenario_kind == "capacity"
    assert decision.options[0].effects == {"capacity_delta": 40}
    assert decision.options[0].prediction == 100
    assert decision.options[0].incremental_gross_profit is None and decision.currency == "IDR"
    assert (await decisions.create_decision(request, user)).id == decision.id
    await j.link(mission_id, "decision", decision, user)
    for operation, identifier in (("select", "decision-select"), ("approve", "decision-approve")):
        decision = await decisions.operate_decision(
            decision.id,
            decisions.DecisionOperation(
                operation_id=identifier,
                expected_revision=decision.revision,
                operation=operation,
                option_id="transfer" if operation == "select" else None,
                mission_id=mission_id,
            ),
            user,
        )
        await j.link(mission_id, "decision", decision, user)
    assert decision.status == "approved" and decision.policy.decision == "REQUIRE_APPROVAL"
    configuration = MonitorConfiguration(
        name="Observe reviewed revenue recovery",
        agent_id="finance",
        semantic=decision.semantic,
        plan={"metrics": ["revenue", "orders"]},
        value_column="revenue",
        count_column="orders",
        time_dimension="ordered_at",
        driver_dimensions=["city"],
        enabled=True,
    )
    action = await actions.action_service.preview(
        ActionPreview(
            idempotency_key="action-journey",
            decision_id=decision.id,
            expected_decision_revision=decision.revision,
            option_id="transfer",
            configuration=configuration,
            mission_id=mission_id,
        ),
        user,
    )
    await actions.action_service.review(
        action.id,
        ActionReview(
            operation_id="action-review",
            expected_revision=action.revision,
            operation="approve",
        ),
        user,
    )
    action = await actions.action_service.get(action.id, user)
    operation = ActionOperation(
        operation_id="action-execute", expected_revision=action.revision, thread_id="thread"
    )
    action_turn, _ = await j.turn(
        "execute-1",
        "Execute the reviewed monitor",
        user,
        continue_mission_id=mission_id,
        tool_call=tool_call_frame(
            "action-call",
            name="execute_business_action",
            arguments={
                "action_id": action.id,
                **operation.model_dump(),
            },
        ),
    )
    assert action_turn.tool_calls_proposed == ["execute_business_action"]
    assert j.consent[-1] == (
        "execute-1",
        "execute_business_action",
        "destructive",
        user["session_id"],
    )
    verified = await actions.action_service.get(action.id, user)
    assert verified.status == "verified" and verified.verification.complete
    assert verified.verification.reason == "monitor_and_schedule_match"
    assert verified.receipt.task_id in j.tasks
    assert j.task_repo.create_task.await_count == 1
    assert (await actions.action_service.dispatch(action.id, operation, user)).id == action.id
    assert j.task_repo.create_task.await_count == 1
    await j.link(mission_id, "action", verified, user)

    knowledge = {}

    async def save_learning(**fields):
        memory_id = "journey-memory-" + fields["outcome"]["id"]
        linkage = await fields["finalize_outcome"](memory_id, 1)
        knowledge[memory_id] = [
            SimpleNamespace(
                revision=1,
                semantic=decision.semantic,
                definition={
                    "outcome_id": linkage["id"],
                    "outcome_revision": linkage["revision"],
                },
            )
        ]

    monkeypatch.setattr(memory_repository, "upsert", AsyncMock(side_effect=save_learning))
    monkeypatch.setattr(
        memory_repository, "revisions", AsyncMock(side_effect=lambda ident, **kw: knowledge[ident])
    )
    monkeypatch.setattr(engine, "utc_now", lambda: decision.outcome_window.end + timedelta(hours=1))
    outcome = await j.service.evaluate_outcome(decision.id, user, mission_id=mission_id)
    assert outcome.status == "complete" and outcome.completeness == 1
    assert outcome.action_ids == [verified.id]
    assert outcome.dimensions["verified_action_count"] == 1
    assert outcome.dimensions["action_business_effect_verified"] is None
    assert outcome.attribution in {"observed_after", "association"}
    assert outcome.learning_refs[0].id == "journey-memory-" + outcome.id
    await j.link(mission_id, "outcome", outcome, user)
    learning = learning_sources.LearningRequest(
        agent_id="finance",
        thread_id="thread",
        semantic=decision.semantic,
        sources=[
            learning_sources.LearningSourceRef(
                kind="outcome", id=outcome.id, revision=outcome.revision, mission_id=mission_id
            )
        ],
    )
    observations = await learning_sources.collect_observations(learning, user)
    assert observations["observations"][0]["attribution"] == outcome.attribution
    assert observations["observations"][0]["state"] == "INFERRED"
    saved = {}

    async def propose(fields):
        saved[fields["proposal_id"]] = {
            **deepcopy(fields),
            "status": "pending",
            "previewed_at": None,
        }
        return deepcopy(saved[fields["proposal_id"]])

    @asynccontextmanager
    async def proposal_lock(*args):
        yield

    monkeypatch.setattr(autopilot, "metadata_lock", proposal_lock)
    monkeypatch.setattr(
        autopilot.semantic_view_service, "_owned", AsyncMock(return_value={"active_version": 1})
    )
    monkeypatch.setattr(
        autopilot.semantic_view_service, "_source_access", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(autopilot.semantic_view_service, "_audit", AsyncMock())
    monkeypatch.setattr(
        autopilot.rule_proposal_repository,
        "get",
        AsyncMock(
            side_effect=lambda ident, **kw: deepcopy(saved.get(ident)),
        ),
    )
    monkeypatch.setattr(
        autopilot.rule_proposal_repository, "create", AsyncMock(side_effect=propose)
    )
    publish = AsyncMock()
    monkeypatch.setattr(autopilot.semantic_view_service, "publish", publish)
    proposal_body = autopilot.AutopilotProposal(
        operation_id="outcome-proposal",
        agent_id="finance",
        base=decision.semantic,
        learning=learning,
        learning_digest=observations["digest"],
        changes=[
            autopilot.SemanticChange(
                kind="synonyms", name="revenue", synonyms=["regional booked revenue"]
            )
        ],
    )
    proposal = await autopilot.propose("sales", proposal_body, user)
    assert proposal["status"] == "pending" and proposal["previewed_at"] is None
    assert proposal["details"]["learning_evidence"]["digest"] == observations["digest"]
    assert (await autopilot.propose("sales", proposal_body, user))["proposal_id"] == proposal[
        "proposal_id"
    ]
    publish.assert_not_awaited()

    before = (len(j.source.calls), len(j.sql), sum(provider.calls for provider, *_ in j.turns))
    replay = await j.missions.get(mission_id, user)
    assert await j.missions.get(mission_id, user) == replay
    assert len(replay.object_refs) == len(set(ref.model_dump_json() for ref in replay.object_refs))
    assert all(j.io.runs[run_id]["status"] == "completed" for run_id in replay.run_ids)
    persisted = TurnResult(frames=[frame for _, frame in j.io.events["revenue-1"]])
    assert event_payloads(persisted, "evidence_envelope")[0]["payload"] == envelope
    assert before == (
        len(j.source.calls),
        len(j.sql),
        sum(provider.calls for provider, *_ in j.turns),
    )

    next_user = {**user, "session_id": "next-session", "security_context_version": 2}
    j.users[next_user["session_id"]] = next_user
    summaries = await j.missions.resumable("thread", next_user)
    summary = next(item for item in summaries if item.mission_id == mission_id)
    assert summary.resume_required
    resume = MissionResume(operation_id="resume-journey", expected_revision=summary.revision)
    resumed = await j.missions.resume(mission_id, resume, next_user)
    assert resumed.current_binding.generation == 2 and resumed.scope == Scope.from_user(next_user)
    assert resumed.run_bindings["revenue-1"] == Scope.from_user(user)
    assert (await j.missions.resume(mission_id, resume, next_user)).revision == resumed.revision
    historical = await j.missions.canonical_read(
        mission_id,
        ObjectRef(kind="outcome", id=outcome.id, revision=outcome.revision),
        next_user,
    )
    assert historical["scope"]["session_id"] == user["session_id"]
    assert historical["attribution"] == outcome.attribution
    with pytest.raises(HTTPException) as general_historical:
        await j.service.get("outcomes", outcome.id, next_user, revision=outcome.revision)
    assert general_historical.value.status_code == 404
    with pytest.raises(HTTPException) as other_owner:
        await j.service.get_for_mission(
            "outcomes",
            outcome.id,
            {**next_user, "username": "another-owner"},
            mission_id=mission_id,
            revision=outcome.revision,
        )
    assert other_owner.value.status_code == 404
    historical_lineage = await j.service.lineage(decision.id, next_user, mission_id=mission_id)
    assert historical_lineage["decision"].scope == Scope.from_user(user)
    assert historical_lineage["investigation"].id == investigation.id
    assert historical_lineage["outcomes"][0].id == outcome.id
    resumed_request = request.model_copy(
        update={
            "operation_id": "decision-next-session",
            "title": "Continue regional recovery",
            "learning_enabled": False,
            "outcome_window": Window(
                start=decision.outcome_window.end + timedelta(days=1),
                end=decision.outcome_window.end + timedelta(days=2),
            ),
        }
    )
    next_decision = await decisions.create_decision(resumed_request, next_user)
    assert next_decision.scope == Scope.from_user(next_user)
    assert next_decision.investigation_revision == pin.revision
    assert next_decision.baseline == decision.baseline
    assert (await decisions.create_decision(resumed_request, next_user)).id == next_decision.id
    await j.link(mission_id, "decision", next_decision, next_user)
    for operation, identifier in (
        ("select", "resumed-select"),
        ("approve", "resumed-approve"),
    ):
        next_decision = await decisions.operate_decision(
            next_decision.id,
            decisions.DecisionOperation(
                operation_id=identifier,
                expected_revision=next_decision.revision,
                operation=operation,
                option_id="transfer" if operation == "select" else None,
                mission_id=mission_id,
            ),
            next_user,
        )
        await j.link(mission_id, "decision", next_decision, next_user)
    next_action = await actions.action_service.preview(
        ActionPreview(
            idempotency_key="resumed-action",
            decision_id=next_decision.id,
            expected_decision_revision=next_decision.revision,
            option_id="transfer",
            mission_id=mission_id,
            configuration=configuration,
        ),
        next_user,
    )
    await actions.action_service.review(
        next_action.id,
        ActionReview(
            operation_id="resumed-action-review",
            expected_revision=next_action.revision,
            operation="approve",
        ),
        next_user,
    )
    next_action = await actions.action_service.get(next_action.id, next_user)
    denied, _ = await j.turn(
        "resumed-action-denied",
        "Execute the new reviewed action",
        next_user,
        continue_mission_id=mission_id,
        consent=False,
        finish_reason="denied",
        tool_call=tool_call_frame(
            "resumed-action-call",
            name="execute_business_action",
            arguments={
                "action_id": next_action.id,
                "operation_id": "resumed-action-execute",
                "expected_revision": next_action.revision,
                "thread_id": "thread",
            },
        ),
    )
    assert denied.finish_reason == "denied"
    assert j.consent[-1] == (
        "resumed-action-denied",
        "execute_business_action",
        "destructive",
        "next-session",
    )
    assert (await actions.action_service.get(next_action.id, next_user)).status == "approved"
    assert j.task_repo.create_task.await_count == 1
    with pytest.raises(HTTPException) as stale:
        await j.missions.attach_run(mission_id, "revenue-1", user)
    assert stale.value.status_code == 404
    comparison_ids_before_resume = {ident for kind, ident in j.repo.rows if kind == "comparisons"}
    resumed_turn, resumed_context = await j.turn(
        "resume-turn",
        "Continue the revenue investigation",
        next_user,
        view="sales",
        continue_mission_id=mission_id,
    )
    assert resumed_context.mission_id == mission_id
    assert {ident for kind, ident in j.repo.rows if kind == "comparisons"} == (
        comparison_ids_before_resume
    )
    assert j.consent[-1] == ("resume-turn", "semantic_query", "read_only", "next-session")
    assert (
        event_payloads(resumed_turn, "evidence_envelope")[0]["payload"]["semantic"]
        == envelope["semantic"]
    )
    assert j.sql[-1][2]["security_context_version"] == 2
    assert all(current_user["username"] == user["username"] for _, current_user in j.source.calls)
    assert all(provider.calls <= 2 for provider, *_ in j.turns)
    assert len(j.io.missions) == 2
    assert not j.turns[-1][3].consent.covers("destructive")
    assert isinstance(j.turns[-1][3].consent, ConsentPolicy)
    next_outcome = await j.service.evaluate_outcome(
        decision.id,
        next_user,
        mission_id=mission_id,
    )
    assert next_outcome.attribution == outcome.attribution
    assert next_outcome.scope == Scope.from_user(next_user)
    assert next_outcome.id != outcome.id
    await j.link(mission_id, "outcome", next_outcome, next_user)


@pytest.mark.parametrize("wrong_contribution,wrong_causal_status", [
    (False, False), (True, False), (False, True),
])
async def test_same_turn_provider_uses_canonical_hypotheses_after_final_verification(
    journey, monkeypatch, wrong_contribution, wrong_causal_status,
):
    import sys

    observations = []

    class CanonicalProvider(ScriptedProvider):
        async def stream(self, *, messages, **kwargs):
            payloads = [json.loads(message["content"])
                        for message in messages if message["role"] == "tool"]
            payload = next(item for item in payloads if item.get("canonical_business_result"))
            canonical = payload["canonical_business_result"]
            observations.append(deepcopy(canonical))
            assert canonical["revision"] >= 1 and canonical["news"]["revision"] >= 1
            assert canonical["baseline_value"] == 100 and canonical["current_value"] == 60
            assert canonical["delta"] == -40
            hypothesis = canonical["hypotheses"][0]
            assert hypothesis["causal_status"] == "arithmetic"
            assert hypothesis["contribution"] == -40
            assert hypothesis["contribution_pct"] == 1
            encoded = json.dumps(canonical)
            for forbidden in ("scope", "session_id", "security_context_version", "lease",
                              "request_digest", "dispatch_fence", PRIVATE):
                assert forbidden not in encoded
            assert "confidence" not in payload["data"]
            refs = canonical["numeric_evidence_refs"]
            contribution = 999 if wrong_contribution else -40
            text = (f"Revenue changed -40. The arithmetic hypothesis {hypothesis['label']} "
                    f"contributes {contribution} and 100% of the movement. "
                    "This decomposition does not establish a causal effect.")
            causal_statement = ("The hypothesis establishes a supported causal effect."
                                if wrong_causal_status else "The hypothesis is arithmetic.")
            text += " " + causal_statement
            claims = [
                {"text": "-40", "value": -40, "kind": "cell",
                 "evidence_id": refs["comparison"], "column": "delta"},
                {"text": str(contribution), "value": contribution, "kind": "cell",
                 "evidence_id": refs["hypotheses"], "column": "contribution",
                 "row_label": hypothesis["label"]},
                {"text": "100%", "value": 100, "kind": "cell",
                 "evidence_id": refs["hypotheses"], "column": "contribution_pct",
                 "row_label": hypothesis["label"]},
                {"text": causal_statement, "kind": "hypothesis",
                 "hypothesis_id": hypothesis["id"],
                 "causal_status": "supported_effect" if wrong_causal_status else "arithmetic"},
            ]
            self.script = [text_frame(text + "<claims>" + json.dumps(claims) + "</claims>")]
            async for frame in super().stream(messages=messages, **kwargs):
                yield frame

    monkeypatch.setattr(sys.modules[__name__], "ScriptedProvider", CanonicalProvider)
    checkpoints = []

    async def checkpoint(state):
        checkpoints.append(deepcopy(state))

    result, context = await journey.turn(
        "canonical-answer", "Why did revenue fall against the previous period?",
        journey.user, view="sales", save_state=checkpoint,
    )
    final_text = "".join(item["text"] for item in event_payloads(result, "text_delta"))
    verification = next(step for step in context.steps if step["kind"] == "answer_verification")
    assert observations and event_payloads(result, "business_result")[0]["investigation"]
    assert "999" not in final_text
    assert "establishes a supported causal effect" not in final_text
    if not wrong_contribution and not wrong_causal_status:
        assert verification["status"] == "accepted"
        assert observations[0]["hypotheses"][0]["label"] in final_text
        assert "-40" in final_text and "100%" in final_text and "arithmetic" in final_text
    elif wrong_contribution:
        assert verification["status"] == "unsupported_number"
    else:
        assert verification["status"] == "unsupported_hypothesis"
        assert context.quality_facts["causal_consistency"]["accepted"] is False
    assert journey.turns[-1][0].calls == 1
    metric = context.quality_facts["business_canonicalization"][0]
    assert metric["business_canonicalization_status"] == "created"
    assert metric["comparison_query_count"] == 2 and metric["driver_query_count"] == 2
    assert context.quality_facts["semantic_tools"][0]["duration_ms"] >= 0
    if not wrong_contribution and not wrong_causal_status:
        completed = next(state for state in reversed(checkpoints) if any(
            message["role"] == "tool" for message in state["messages"]
        ))
        calls_before = len(journey.source.calls)
        rows_before = set(journey.repo.rows)
        registry = ToolRegistry()
        registry.register(SemanticQueryTool())
        resumed_context = replace(context, steps=None, quality_facts=None,
                                  business_turn_hook=None)
        provider = CanonicalProvider([text_frame("unused")], turn_plan={
            "intent": "semantic_analytics", "tools": ["semantic_query"],
            "required_tools": ["semantic_query"], "ml_task": None,
            "work_intent": "INVESTIGATE",
        })
        resumed_frames = [frame async for frame in AssistantLoop(
            provider=provider, registry=registry,
        ).run(thread=journey.turns[-1][3],
              user_content="Why did revenue fall against the previous period?",
              context=resumed_context, resume_state=completed,
              resolve_consent=AsyncMock(return_value=True))]
        assert TurnResult(frames=resumed_frames).finish_reason == "stop"
        assert observations[-1] == observations[0]
        assert len(journey.source.calls) == calls_before and set(journey.repo.rows) == rows_before
        assert resumed_context.quality_facts["business_canonicalization"] == [metric]
