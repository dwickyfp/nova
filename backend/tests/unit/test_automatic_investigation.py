"""Automatic canonicalization uses validated fixed-window evidence and honest counts."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.modules.agents.semantic.planning import SemanticPlan, SemanticTime
from app.modules.agents.semantic.time_ranges import resolve_execution_time
from app.modules.intelligence.contracts import MonitorConfiguration, Scope, fingerprint
from app.modules.intelligence.evidence import InvestigationSeed
from tests.unit.test_chat_investigation import comparison as _comparison
from tests.unit.test_intelligence_engine import END, REF, USER
from tests.unit.test_intelligence_engine import lifecycle as _lifecycle

comparison, lifecycle = _comparison, _lifecycle


@pytest.fixture
def automatic_mission(comparison, monkeypatch):
    from fastapi import HTTPException

    from app.modules.agents import mission as module
    from app.modules.agents.mission_schema import Mission, WorkIntent
    from app.modules.intelligence import engine

    service, repo, _, _ = comparison
    state = SimpleNamespace(value=Mission(
        mission_id="mission", thread_id="thread", agent_id="finance", scope=Scope.from_user(USER),
        objective="Investigate revenue", work_intent=WorkIntent.INVESTIGATE, status="running",
        revision=1, operation_id="mission-operation", created_at=END, updated_at=END,
    ))
    history = {(kind, record.id, record.revision): deepcopy(record)
               for (kind, _), record in repo.rows.items()}
    original_save = repo.save

    async def read(kind, record_id, scope, model, *, revision=None):
        row = (history.get((kind, record_id, revision)) if revision is not None
               else repo.rows.get((kind, record_id)))
        if row and row.scope.model_dump(exclude={"session_id"}) == scope.model_dump(
            exclude={"session_id"},
        ):
            return deepcopy(row)
        return None

    async def save(kind, record, **kwargs):
        old = repo.rows.get((kind, record.id))
        if old and old.scope.model_dump(exclude={"session_id"}) != record.scope.model_dump(
            exclude={"session_id"},
        ):
            raise HTTPException(status_code=404, detail="Record unavailable")
        saved = await original_save(kind, record, **kwargs)
        history[(kind, saved.id, saved.revision)] = deepcopy(saved)
        return saved

    async def get(mission_id, user, **kwargs):
        if mission_id != state.value.mission_id or Scope.from_user(user) != state.value.scope:
            raise HTTPException(status_code=404, detail="Mission not found")
        return deepcopy(state.value)

    async def get_owner(mission_id, scope):
        if mission_id != state.value.mission_id or (
            scope.principal, scope.active_role,
        ) != (state.value.scope.principal, state.value.scope.active_role):
            raise HTTPException(status_code=404, detail="Mission not found")
        return deepcopy(state.value)

    async def get_scoped(mission_id, scope):
        return await get(mission_id, {**USER, **scope.model_dump(), "username": scope.principal})

    async def save_mission(record, expected_revision, owned):
        await owned()
        assert expected_revision == state.value.revision
        state.value = record.model_copy(update={"revision": expected_revision + 1}, deep=True)
        return deepcopy(state.value)

    @asynccontextmanager
    async def admission(*args):
        yield AsyncMock()

    monkeypatch.setattr(repo, "get", read)
    monkeypatch.setattr(repo, "save", save)
    monkeypatch.setattr(module.mission_service, "get", get)
    monkeypatch.setattr(module.mission_service, "_get", get_scoped)
    monkeypatch.setattr(module.mission_service, "_get_owner", get_owner)
    monkeypatch.setattr(module.mission_service, "_save", save_mission)
    monkeypatch.setattr(module.mission_service, "audit", AsyncMock())
    monkeypatch.setattr(module, "require_thread", AsyncMock())
    monkeypatch.setattr(module, "require_workflow", lambda: None)
    monkeypatch.setattr(module.harness_repository, "admission_lock", admission)
    monkeypatch.setattr(engine, "intelligence_service", service)
    state.history = history
    return state


async def test_unknown_count_preserves_arithmetic_without_statistical_claim(comparison):
    service, repo, _, body = comparison
    configuration = body.configuration.model_copy(update={"count_column": None})
    result = await service.initiate_investigation(body.model_copy(update={
        "configuration": configuration,
    }), USER)
    assert result["investigation"].status == "complete"
    assert result["investigation"].hypotheses
    assert all(h.causal_status == "arithmetic" for h in result["investigation"].hypotheses)
    assert all(h.confidence.label == "insufficient" for h in result["investigation"].hypotheses)
    assert all(row.sample_count is None for (kind, _), row in repo.rows.items()
               if kind == "observations")
    news = next(row for (kind, _), row in repo.rows.items() if kind == "news")
    assert news.confidence.label == "insufficient"
    assert not any(row.enabled for (kind, _), row in repo.rows.items() if kind == "monitors")


def test_scheduled_monitor_cannot_acquire_unknown_count(comparison):
    configuration = comparison[3].configuration.model_dump()
    configuration.update(count_column=None, enabled=True)
    with pytest.raises(ValidationError, match="reviewed count"):
        MonitorConfiguration.model_validate(configuration)


def seed():
    fixed = resolve_execution_time(
        "2026-09-19..2026-09-19", "previous_period", now=END, timezone="Asia/Jakarta",
    )
    plan = SemanticPlan(metrics=("revenue",), dimensions=("city",),
                        time=SemanticTime("ordered_at", range=fixed.range,
                                          compare=fixed.comparison))
    return InvestigationSeed(REF, plan, "revenue", ("city",), fixed, fingerprint(plan.as_dict()))


async def test_exact_authorized_monitor_is_preferred_and_duplicates_recover(
    comparison, automatic_mission,
):
    service, repo, source, _ = comparison
    result = await service.automatic_investigation(seed(), USER, mission_id="mission",
                                                 agent_id="finance")
    assert result["comparison"].monitor_id == "monitor"
    assert len([kind for kind, _ in repo.rows if kind == "monitors"]) == 1
    repeated = await service.automatic_investigation(seed(), USER, mission_id="mission",
                                                   agent_id="finance")
    assert repeated["investigation"].id == result["investigation"].id
    assert repeated["comparison"].id == result["comparison"].id
    assert all(user["username"] == USER["username"] for _, user in source.calls)


@pytest.mark.parametrize("existing_monitor", [False, True])
async def test_automatic_comparison_survives_json_persistence_and_resume(
    comparison, automatic_mission, monkeypatch, existing_monitor,
):
    service, repo, _, _ = comparison
    if not existing_monitor:
        repo.rows[("monitors", "monitor")].plan["named_filters"] = ["different"]
    original_get = repo.get

    async def read(*args, **kwargs):
        record = await original_get(*args, **kwargs)
        return type(record).model_validate_json(record.model_dump_json()) if record else None

    monkeypatch.setattr(repo, "get", read)
    original = await service.automatic_investigation(
        seed(), USER, mission_id="mission", agent_id="finance",
    )
    assert original["status"] == "complete"
    next_user, _ = resume_binding(automatic_mission)
    recovered = await service.automatic_investigation(
        seed(), next_user, mission_id="mission", agent_id="finance",
    )
    assert recovered["comparison"].id == original["comparison"].id
    assert recovered["comparison"].request_digest == original["comparison"].request_digest
    assert recovered["investigation"].id == original["investigation"].id
    assert recovered["investigation"].revision == original["investigation"].revision
    assert len([key for key in repo.rows if key[0] == "comparisons"]) == 1


async def test_different_filters_cannot_reuse_monitor(comparison, automatic_mission):
    service, repo, _, _ = comparison
    repo.rows[("monitors", "monitor")].plan["named_filters"] = ["different"]
    result = await service.automatic_investigation(seed(), USER, mission_id="mission",
                                                 agent_id="finance")
    assert result["comparison"].monitor_id != "monitor"
    monitor = repo.rows[("monitors", result["comparison"].monitor_id)]
    assert monitor.count_column is None and not monitor.enabled


@pytest.mark.parametrize("interrupted", [False, True])
async def test_automatic_recovery_uses_original_monitor_after_configuration_change(
    comparison, automatic_mission, monkeypatch, interrupted,
):
    from copy import deepcopy

    from fastapi import HTTPException

    service, repo, source, _ = comparison
    pinned = deepcopy(repo.rows[("monitors", "monitor")])
    original_get = repo.get

    async def historical_get(kind, record_id, scope, model, **kwargs):
        if (kind == "monitors" and record_id == pinned.id
                and kwargs.get("revision") == pinned.revision):
            return deepcopy(pinned)
        return await original_get(kind, record_id, scope, model, **kwargs)

    monkeypatch.setattr(repo, "get", historical_get)
    if interrupted:
        repo.fail_once = "investigations"
        with pytest.raises(RuntimeError):
            await service.automatic_investigation(
                seed(), USER, mission_id="mission", agent_id="finance",
            )
    else:
        await service.automatic_investigation(
            seed(), USER, mission_id="mission", agent_id="finance",
        )
    original = next(row for (kind, _), row in repo.rows.items() if kind == "comparisons")
    await repo.save("monitors", pinned.model_copy(update={"name": "Renamed later"}),
                    expected_revision=pinned.revision)
    recovered = await service.automatic_investigation(
        seed(), USER, mission_id="mission", agent_id="finance",
    )
    assert recovered["comparison"].id == original.id
    assert recovered["comparison"].monitor_revision == pinned.revision
    assert recovered["comparison"].configuration.name == pinned.name
    assert recovered["investigation"].status == "complete"
    assert len([key for key in repo.rows if key[0] == "comparisons"]) == 1
    source.revoked = True
    with pytest.raises(HTTPException):
        await service.automatic_investigation(
            seed(), USER, mission_id="mission", agent_id="finance",
        )


def resume_binding(state):
    from app.modules.agents.mission_schema import MissionBinding

    next_user = {**USER, "session_id": "next-session", "security_context_version": 2}
    old = state.value.scope.model_copy()
    state.value = state.value.model_copy(update={
        "scope": Scope.from_user(next_user), "historical_bindings": [old],
        "current_binding": MissionBinding(scope=Scope.from_user(next_user), generation=2,
                                          bound_at=END),
    }, deep=True)
    return next_user, old


@pytest.mark.parametrize("checkpoint", ["complete", "monitor", "investigation", "news_link",
                                        "comparison_link"])
async def test_automatic_resume_recovers_original_operation_and_interrupted_link(
    comparison, automatic_mission, monkeypatch, checkpoint,
):
    from fastapi import HTTPException

    from app.modules.agents.mission import mission_service
    from app.modules.agents.mission_schema import ObjectRef, object_binding_key

    service, repo, source, _ = comparison
    original_save = repo.save
    if checkpoint == "monitor":
        repo.rows[("monitors", "monitor")].plan["named_filters"] = ["other_population"]
        repo.fail_once = "monitors"
    elif checkpoint == "investigation":
        repo.fail_once = "investigations"
    elif checkpoint in {"news_link", "comparison_link"}:
        failed = False

        async def interrupted_save(kind, record, **kwargs):
            nonlocal failed
            matches = ((checkpoint == "news_link" and kind == "news"
                        and record.investigation_id)
                       or (checkpoint == "comparison_link" and kind == "comparisons"
                           and record.status != "pending"))
            if matches and not failed:
                failed = True
                raise RuntimeError("interrupted canonical linkage")
            return await original_save(kind, record, **kwargs)

        monkeypatch.setattr(repo, "save", interrupted_save)
    if checkpoint == "complete":
        initial = await service.automatic_investigation(
            seed(), USER, mission_id="mission", agent_id="finance",
        )
        original_investigation_id = initial["investigation"].id
    else:
        with pytest.raises(RuntimeError):
            await service.automatic_investigation(
                seed(), USER, mission_id="mission", agent_id="finance",
            )
        original_investigation_id = next(
            (record.id for (kind, _), record in repo.rows.items() if kind == "investigations"),
            None,
        )
    original = next(record for (kind, _), record in repo.rows.items() if kind == "comparisons")
    next_user, original_scope = resume_binding(automatic_mission)
    source.calls.clear()
    recovered = await service.automatic_investigation(
        seed(), next_user, mission_id="mission", agent_id="finance",
    )
    assert recovered["comparison"].id == original.id
    assert recovered["comparison"].scope == original_scope
    investigation = recovered["investigation"]
    assert investigation.scope == original_scope
    if original_investigation_id:
        assert investigation.id == original_investigation_id
    assert len([key for key in repo.rows if key[0] == "comparisons"]) == 1
    assert len([key for key in repo.rows if key[0] == "investigations"]) == 1
    assert source.calls and all(user == next_user for _, user in source.calls)
    ref = ObjectRef(kind="investigation", id=investigation.id, revision=investigation.revision)
    linked = await mission_service._link_automatic_investigation(
        "mission", original.id, ref, automatic_mission.value.revision, next_user,
    )
    assert ref in linked.object_refs
    assert linked.object_bindings[object_binding_key(ref)] == original_scope
    retry = await service.automatic_investigation(
        seed(), next_user, mission_id="mission", agent_id="finance",
    )
    assert retry["investigation"].id == investigation.id
    linked_retry = await mission_service._link_automatic_investigation(
        "mission", original.id, ref, linked.revision, next_user,
    )
    assert linked_retry.revision == linked.revision
    with pytest.raises(HTTPException) as stale:
        await service.automatic_investigation(
            seed(), USER, mission_id="mission", agent_id="finance",
        )
    assert stale.value.status_code == 404
    source.revoked = True
    with pytest.raises(HTTPException) as revoked:
        await service.automatic_investigation(
            seed(), next_user, mission_id="mission", agent_id="finance",
        )
    assert revoked.value.status_code == 404
    assert len([key for key in repo.rows if key[0] == "comparisons"]) == 1


@pytest.mark.parametrize("tamper", ["mission", "operation", "monitor_revision", "news_revision"])
async def test_historical_scope_does_not_replace_automatic_dependency_proof(
    comparison, automatic_mission, tamper,
):
    from fastapi import HTTPException

    service, repo, _, _ = comparison
    initial = await service.automatic_investigation(
        seed(), USER, mission_id="mission", agent_id="finance",
    )
    row = repo.rows[("comparisons", initial["comparison"].id)]
    if tamper == "mission":
        row.automatic_mission_id = "another-mission"
    elif tamper == "operation":
        row.automatic_operation_id = "another-operation"
    elif tamper == "monitor_revision":
        row.monitor_revision = 999
    else:
        investigation = initial["investigation"]
        repo.rows[("investigations", investigation.id)].news_revision = 999
        automatic_mission.history[("investigations", investigation.id,
                                   investigation.revision)].news_revision = 999
    next_user, _ = resume_binding(automatic_mission)
    with pytest.raises(HTTPException) as refused:
        await service.automatic_investigation(
            seed(), next_user, mission_id="mission", agent_id="finance",
        )
    assert refused.value.status_code == 409
    assert len([key for key in repo.rows if key[0] == "comparisons"]) == 1


async def test_another_mission_does_not_reuse_historical_comparison(comparison, automatic_mission):
    service, repo, _, _ = comparison
    original = await service.automatic_investigation(
        seed(), USER, mission_id="mission", agent_id="finance",
    )
    next_user, _ = resume_binding(automatic_mission)
    automatic_mission.value.mission_id = "separate-mission"
    separate = await service.automatic_investigation(
        seed(), next_user, mission_id="separate-mission", agent_id="finance",
    )
    assert separate["comparison"].id != original["comparison"].id
    assert separate["investigation"].scope == Scope.from_user(next_user)
    assert len([key for key in repo.rows if key[0] == "comparisons"]) == 2


async def test_resumed_automatic_comparison_rechecks_current_masks(comparison, automatic_mission):
    from fastapi import HTTPException

    service, repo, source, _ = comparison
    await service.automatic_investigation(seed(), USER, mission_id="mission", agent_id="finance")
    next_user, _ = resume_binding(automatic_mission)
    source.masked = True
    with pytest.raises(HTTPException) as refused:
        await service.automatic_investigation(
            seed(), next_user, mission_id="mission", agent_id="finance",
        )
    assert refused.value.status_code == 409
    assert len([key for key in repo.rows if key[0] == "comparisons"]) == 1


async def test_interrupted_recovery_fences_binding_change_during_query(
    comparison, automatic_mission, monkeypatch,
):
    from fastapi import HTTPException

    service, repo, source, _ = comparison
    repo.fail_once = "monitors"
    repo.rows[("monitors", "monitor")].plan["named_filters"] = ["other_population"]
    with pytest.raises(RuntimeError):
        await service.automatic_investigation(
            seed(), USER, mission_id="mission", agent_id="finance",
        )
    next_user, _ = resume_binding(automatic_mission)
    original_query = source.execute_plan

    async def rebound(*args):
        result = await original_query(*args)
        automatic_mission.value.current_binding.generation += 1
        return result

    monkeypatch.setattr(source, "execute_plan", rebound)
    with pytest.raises(HTTPException) as refused:
        await service.automatic_investigation(
            seed(), next_user, mission_id="mission", agent_id="finance",
        )
    assert refused.value.status_code == 409
    assert len(source.calls) == 1
    assert not any(kind in {"observations", "news", "investigations"} for kind, _ in repo.rows)
    assert next(row for (kind, _), row in repo.rows.items() if kind == "comparisons").status == (
        "pending"
    )


async def test_no_automatic_seed_is_derived_from_failed_or_unbounded_execution(monkeypatch):
    from app.modules.agents.tools.semantic_query import SemanticQueryTool
    from app.modules.assistant.service import LoopContext
    from app.modules.assistant.tools import ToolInvocation

    tool = SemanticQueryTool()
    outcome = await tool.run(ToolInvocation("call", "semantic_query", {"question": "Revenue"}),
                             LoopContext(user_name="alice"))
    assert not outcome.ok and outcome.business_result is None


async def test_time_context_is_durable_before_query(monkeypatch):
    from app.core.config import settings
    from app.modules.assistant.service import LoopContext
    from app.modules.assistant.tools import ToolInvocation
    from tests.unit.test_semantic_guidance_fallback import USER as TOOL_USER
    from tests.unit.test_semantic_llm_planner import _plan, setup_tool

    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    tool, _, execute = setup_tool(monkeypatch, _plan(time={
        "dimension": "order_date", "grain": None,
        "range": "current_month", "compare": "previous_period",
    }))
    tool._resolve_model.return_value.update(status="ACTIVE", version=1, fingerprint="pinned")
    saved = []
    async def pin(invocation, payload, context):
        assert execute.await_count == 0
        saved.append(payload)
    context = LoopContext(user_name="alice", user=TOOL_USER, business_time_hook=pin,
                          execution_now=datetime(2026, 10, 4, 12, tzinfo=UTC))
    outcome = await tool.run(ToolInvocation("call", "semantic_query", {"question": "Revenue"}),
                             context)
    assert outcome.ok and len(saved) == 1
    sql = execute.await_args.kwargs["sql"]
    assert "CURRENT_DATE" not in sql
    assert outcome.trace_detail["execution_time_context"] == saved[0]["execution_time"]


def test_group_limit_boundary_and_population_identity_are_conservative():
    from types import SimpleNamespace

    from app.modules.agents.semantic.planning import SemanticFilter
    from app.modules.agents.tools.semantic_query import _execution_facts
    from app.modules.intelligence.evidence import semantic_population_fingerprint

    plan = SemanticPlan(metrics=("revenue",), dimensions=("city",), limit=2)
    below = SimpleNamespace(rows=[["Jakarta", 1]], truncated=False)
    at_limit = SimpleNamespace(rows=[["Jakarta", 1], ["Singapore", 2]], truncated=False)
    assert _execution_facts({}, [below], "compiled", None, plan).coverage == "complete"
    assert _execution_facts({}, [at_limit], "compiled", None, plan).coverage == "truncated"
    scalar = SemanticPlan(metrics=("revenue",), limit=1)
    assert _execution_facts({}, [below], "compiled", None, scalar).coverage == "complete"
    comparison_plan = SemanticPlan(metrics=("revenue",), limit=1, time=SemanticTime(
        "ordered_at", range="current_month", compare="previous_period",
    ))
    assert _execution_facts({}, [below], "compiled", None, comparison_plan).coverage == "truncated"
    jakarta = SemanticPlan(metrics=("revenue",), filters=(
        SemanticFilter("city", "=", "Jakarta"),
    ))
    singapore = SemanticPlan(metrics=("revenue",), filters=(
        SemanticFilter("city", "=", "Singapore"),
    ))
    assert semantic_population_fingerprint(jakarta) != semantic_population_fingerprint(singapore)
    assert semantic_population_fingerprint(plan) == semantic_population_fingerprint(scalar)
    assert len(semantic_population_fingerprint(jakarta)) == 64
