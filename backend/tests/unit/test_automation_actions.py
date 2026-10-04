"""Automation effects retain the existing Action consent and recovery boundaries."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.agents import automations, router
from app.modules.intelligence import automation_action
from app.modules.intelligence.action_contracts import (
    Action,
    ActionPreview,
    ActionReview,
    AutomationActionConfiguration,
    AutomationActionReceipt,
)
from app.modules.intelligence.automation_action import AutomationActionAdapter
from app.modules.intelligence.contracts import Scope, fingerprint
from app.modules.intelligence.decisions import decision_digest
from tests.unit.test_agent_automations import run_env as run_env
from tests.unit.test_business_actions import action_program as action_program
from tests.unit.test_business_actions import operation
from tests.unit.test_business_actions import program as program
from tests.unit.test_intelligence_engine import USER


class AutomationStore:
    def __init__(self):
        self.rows = {}
        self.creations = []
        self.disables = []
        self.fail_after_create = False
        self.fail_after_disable = False

    async def get(self, identifier, *, owner_name):
        item = self.rows.get(identifier)
        return deepcopy(item) if item and item["owner_name"] == owner_name else None

    async def create(
        self, *, agent_id, owner_name, role_name, body, creation_identity, execution_binding
    ):
        identifier = automations.automation_creation_id(
            owner_name, role_name, agent_id, creation_identity
        )
        saved = {
            **body.model_dump(mode="json"),
            "automation_id": identifier,
            "agent_id": agent_id,
            "owner_name": owner_name,
            "role_name": role_name,
            "next_run_at": "2026-10-05 01:00:00",
        }
        saved["delivery"]["execution_binding"] = execution_binding.model_dump(mode="json")
        if identifier in self.rows:
            assert self.rows[identifier] == saved
        else:
            self.rows[identifier] = saved
            self.creations.append(identifier)
        if self.fail_after_create:
            raise RuntimeError("lost creation response")
        return deepcopy(saved)

    async def update(self, item, body, *, expected_configuration_digest):
        saved = self.rows[item["automation_id"]]
        if (
            fingerprint(automations.automation_configuration(saved))
            != expected_configuration_digest
        ):
            raise automations.AutomationError("Automation configuration changed")
        saved.update(body.model_dump(exclude_unset=True))
        self.disables.append(item["automation_id"])
        if self.fail_after_disable:
            raise RuntimeError("lost disable response")
        return deepcopy(saved)


@pytest.fixture
async def automation_program(action_program, monkeypatch):
    service, monitor_action, _, _, lease, policy, repo, source = action_program
    store = AutomationStore()
    adapter = AutomationActionAdapter(service.service, store)
    service.adapters[adapter.id] = adapter
    owned = AsyncMock(return_value={"owner_name": USER["username"]})
    binding = AsyncMock()
    monkeypatch.setattr(router, "_require_owned_agent", owned)
    monkeypatch.setattr(automation_action, "require_execution_binding", binding)
    request = ActionPreview(
        idempotency_key="automation-action-1",
        decision_id=monitor_action.decision_id,
        expected_decision_revision=monitor_action.decision_revision,
        option_id=monitor_action.option_id,
        adapter_id="automation-v1",
        configuration=AutomationActionConfiguration(
            agent_id="finance",
            semantic=monitor_action.semantic,
            title="Weekly revenue report",
            prompt="Summarize governed revenue for last week.",
            schedule_kind="cron",
            schedule_expr="0 8 * * 1",
            timezone="Asia/Jakarta",
        ),
    )
    action = await service.preview(request, USER)
    assert action.action_type == "automation"
    await service.review(
        action.id,
        ActionReview(
            operation_id="approve-automation",
            expected_revision=action.revision,
            operation="approve",
        ),
        USER,
    )
    action = await service.get(action.id, USER)
    return SimpleNamespace(
        service=service,
        action=action,
        request=request,
        adapter=adapter,
        store=store,
        lease=lease,
        policy=policy,
        repo=repo,
        source=source,
        owned=owned,
        binding=binding,
    )


async def test_automation_creation_is_stable_and_readback_confirms_configuration(
    automation_program,
):
    env = automation_program
    assert (await env.service.preview(env.request, USER)).id == env.action.id
    saved = await env.service.dispatch(env.action.id, operation(env.action), USER)
    assert saved.status == "verified" and saved.verification.reason == "automation_matches"
    assert isinstance(saved.receipt, AutomationActionReceipt)
    assert saved.receipt.automation_id == env.adapter.identity(saved)
    assert (await env.service.dispatch(saved.id, operation(env.action), USER)).status == "verified"
    assert len(env.store.creations) == 1
    persisted = env.store.rows[saved.receipt.automation_id]
    assert persisted["delivery"]["mcp_tool_id"] is None
    assert persisted["delivery"]["execution_binding"]["scope"] == saved.scope.model_dump(
        mode="json", exclude={"session_id"}
    ) | {"session_id": None}
    assert env.owned.await_count > 0 and env.binding.await_count > 0


@pytest.mark.parametrize("phase", ["creation", "compensation"])
async def test_uncertain_automation_mutation_requires_readback_not_redispatch(
    automation_program, phase
):
    env = automation_program
    env.store.fail_after_create = phase == "creation"
    saved = await env.service.dispatch(env.action.id, operation(env.action), USER)
    if phase == "creation":
        assert saved.status == "verification_required" and saved.receipt is None
        assert (
            await env.service.dispatch(saved.id, operation(env.action), USER)
        ).status == saved.status
        reconciled = await env.service.verify(saved.id, USER, expected_revision=saved.revision)
        assert reconciled.status == "verified"
        assert len(env.store.creations) == 1
    else:
        env.store.fail_after_disable = True
        body = operation(saved, "disable-automation-1")
        uncertain = await env.service.dispatch(saved.id, body, USER, compensate=True)
        assert uncertain.status == "compensation_required"
        assert uncertain.compensation_receipt is None
        assert (
            await env.service.dispatch(saved.id, body, USER, compensate=True)
        ).status == uncertain.status
        reconciled = await env.service.verify(saved.id, USER, expected_revision=uncertain.revision)
        assert reconciled.status == "compensated"
        assert reconciled.compensation.reason == "automation_disabled"
        assert len(env.store.disables) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("role_name", "OTHER"),
        ("agent_id", "other-agent"),
        ("prompt", "Another governed request"),
        ("schedule_expr", "0 9 * * 1"),
        ("timezone", "UTC"),
        ("enabled", False),
        ("delivery", {"mcp_tool_id": "external-tool"}),
    ],
)
async def test_automation_drift_refuses_verification_and_compensation(
    automation_program, field, value
):
    env = automation_program
    saved = await env.service.dispatch(env.action.id, operation(env.action), USER)
    env.store.rows[saved.receipt.automation_id][field] = value
    checked = await env.service.verify(saved.id, USER, expected_revision=saved.revision)
    assert checked.status == "verification_required"
    assert checked.verification.reason == "automation_changed"
    with pytest.raises(HTTPException) as error:
        await env.adapter.compensate(checked, USER)
    assert error.value.status_code == 409 and not env.store.disables


@pytest.mark.parametrize("boundary", ["owner", "execution_binding", "semantic"])
async def test_revoked_authority_blocks_automation_creation(automation_program, boundary):
    env = automation_program
    refusal = HTTPException(status_code=403, detail="Current access refused")
    if boundary == "owner":
        env.owned.side_effect = refusal
    elif boundary == "execution_binding":
        env.binding.side_effect = refusal
    else:
        env.service.service.authorize_semantic = AsyncMock(side_effect=refusal)
    with pytest.raises(HTTPException):
        await env.service.dispatch(env.action.id, operation(env.action), USER)
    assert not env.store.creations


async def test_compensation_retains_original_receipt_and_requires_current_authority(
    automation_program,
):
    env = automation_program
    saved = await env.service.dispatch(env.action.id, operation(env.action), USER)
    receipt = saved.receipt.model_dump()
    env.binding.side_effect = HTTPException(status_code=403, detail="Execution binding revoked")
    with pytest.raises(HTTPException):
        await env.service.dispatch(
            saved.id, operation(saved, "disable-refused-1"), USER, compensate=True
        )
    assert not env.store.disables
    env.binding.side_effect = None
    compensated = await env.service.dispatch(
        saved.id, operation(saved, "disable-accepted-1"), USER, compensate=True
    )
    assert compensated.status == "compensated"
    assert compensated.receipt.model_dump() == receipt
    assert compensated.compensation_receipt.schedule_enabled is False


async def test_current_policy_blocks_automation_without_dispatch(automation_program):
    env = automation_program
    env.policy.blocked_actions.append("automation")
    with pytest.raises(HTTPException):
        await env.service.dispatch(env.action.id, operation(env.action), USER)
    assert not env.store.creations


@pytest.mark.parametrize("boundary", ["preview", "execute", "verify", "compensate"])
async def test_replaced_semantic_version_keeps_reads_but_refuses_scheduled_effects(
    automation_program, boundary
):
    env = automation_program
    action = env.action
    if boundary in {"verify", "compensate"}:
        action = await env.service.dispatch(action.id, operation(action), USER)
    before_creations = len(env.store.creations)
    env.source.active_version = action.semantic.version + 1
    assert (await env.service.get(action.id, USER)).semantic == action.semantic
    with pytest.raises(HTTPException) as error:
        if boundary == "preview":
            await env.adapter.preview(action.configuration, USER)
        elif boundary == "verify":
            await env.service.verify(action.id, USER, expected_revision=action.revision)
        else:
            await env.service.dispatch(
                action.id,
                operation(action, "stale-semantic-operation"),
                USER,
                compensate=boundary == "compensate",
            )
    assert error.value.status_code == 409
    assert error.value.detail == "Semantic version is stale; revalidate first"
    assert len(env.store.creations) == before_creations
    assert not env.store.disables


async def test_monitor_adapter_explicitly_refuses_unknown_count(action_program):
    from app.modules.intelligence.monitor_action import MonitorActionAdapter

    service, action, *_ = action_program
    adapter = MonitorActionAdapter(service.service)
    configuration = action.configuration.model_copy(update={"count_column": None})
    with pytest.raises(HTTPException) as error:
        await adapter.preview(configuration, USER)
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        await adapter.execute(action.model_copy(update={"configuration": configuration}), USER)
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"delivery": "email"},
        {"enabled": False},
        {"unknown_field": "ignored"},
        {"prompt": "Use https://user:password@example.com/"},
        {"condition": {"metric": "revenue", "operator": ">", "value": 1, "private": "ignored"}},
        {"condition": {"metric": "revenue", "operator": ">", "value": float("nan")}},
    ],
)
def test_automation_configuration_rejects_external_delivery_and_secrets(change):
    with pytest.raises(ValidationError):
        AutomationActionConfiguration(
            agent_id="finance",
            semantic={"view_id": "sales", "version": 1, "fingerprint": "abc"},
            **(
                {
                    "title": "Revenue report",
                    "prompt": "Show weekly revenue",
                    "schedule_kind": "cron",
                    "schedule_expr": "0 8 * * 1",
                }
                | change
            ),
        )


async def test_registry_refuses_configuration_and_receipt_from_another_adapter(automation_program):
    env = automation_program
    with pytest.raises(ValidationError):
        ActionPreview.model_validate(env.request.model_dump() | {"adapter_id": "monitor-v1"})
    with pytest.raises(ValidationError):
        Action.model_validate(
            env.action.model_dump()
            | {
                "receipt": {
                    "monitor_id": "wrong-owner",
                    "monitor_revision": 1,
                    "schedule_enabled": True,
                }
            }
        )


async def test_resumed_mission_action_retains_historical_decision_proof(
    automation_program, monkeypatch
):
    env = automation_program
    decision = deepcopy(env.repo.rows[("decisions", env.action.decision_id)])
    decision.scope.session_id = "historical-session"
    historical_digest = decision_digest(decision)
    mission = SimpleNamespace(scope=Scope.from_user(USER), cancel_requested=False)
    owned = AsyncMock(return_value=mission)
    narrow = AsyncMock(return_value=decision)
    monkeypatch.setattr(env.service, "_owned_mission", owned)
    monkeypatch.setattr(env.service.service, "get_for_mission", narrow)
    request = env.request.model_copy(
        update={"idempotency_key": "resumed-action-1", "mission_id": "mission-1"}
    )
    created = await env.service.preview(request, USER)
    assert created.scope == Scope.from_user(USER)
    assert created.decision_digest == historical_digest
    assert created.approval is None and created.dispatch_attempts == 0
    assert created.mission_id == "mission-1"
    assert (await env.service.get(created.id, USER)).id == created.id
    assert decision_digest(decision) == historical_digest
    assert all(
        call.kwargs
        == {
            "mission_id": "mission-1",
            "revision": decision.revision,
        }
        for call in narrow.await_args_list
    )


async def test_unresumed_mission_refuses_action_preview(automation_program, monkeypatch):
    from app.modules.agents.mission import mission_service

    env = automation_program
    monkeypatch.setattr(
        mission_service,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                scope=Scope.from_user(USER).model_copy(update={"session_id": "old-session"}),
            )
        ),
    )
    narrow = AsyncMock()
    monkeypatch.setattr(env.service.service, "get_for_mission", narrow)
    with pytest.raises(HTTPException) as error:
        await env.service._read_decision(
            env.action.decision_id, USER, mission_id="mission-1", revision=1
        )
    assert error.value.status_code == 409
    narrow.assert_not_awaited()


async def test_mission_historical_action_read_keeps_binding_immutable(
    automation_program, monkeypatch
):
    from app.modules.agents import mission

    env = automation_program
    historical = env.action.model_copy(deep=True)
    historical.scope.session_id = "historical-session"
    pin = SimpleNamespace(kind="action", id=historical.id, revision=historical.revision)
    old_scope = historical.scope.model_dump()
    owner_mission = SimpleNamespace(scope=historical.scope, object_refs=[pin], thread_id="thread")
    monkeypatch.setattr(
        mission.mission_service, "_get_owner", AsyncMock(return_value=owner_mission)
    )
    monkeypatch.setattr(mission, "require_thread", AsyncMock())
    narrow = AsyncMock(return_value=historical)
    monkeypatch.setattr(env.service.service, "get_for_mission", narrow)
    read = await env.service.read(
        historical.id, USER, mission_id="mission-1", revision=historical.revision
    )
    assert read.execution_current is False
    assert read.scope.model_dump() == old_scope
    narrow.assert_awaited_once_with(
        "actions", historical.id, USER, mission_id="mission-1", revision=historical.revision
    )
    with pytest.raises(HTTPException) as error:
        await env.service.read(
            historical.id, USER, mission_id="mission-1", revision=historical.revision + 1
        )
    assert error.value.status_code == 404


async def test_mission_current_action_read_uses_current_verified_revision(
    automation_program, monkeypatch
):
    from app.modules.agents import mission

    env = automation_program
    pin = SimpleNamespace(kind="action", id=env.action.id, revision=env.action.revision)
    owner_mission = SimpleNamespace(
        scope=Scope.from_user(USER), object_refs=[pin], thread_id="thread"
    )
    monkeypatch.setattr(
        mission.mission_service, "_get_owner", AsyncMock(return_value=owner_mission)
    )
    monkeypatch.setattr(mission, "require_thread", AsyncMock())
    read = await env.service.read(
        env.action.id, USER, mission_id="mission-1", revision=env.action.revision
    )
    assert read.execution_current is True
    assert read.scope == Scope.from_user(USER)
    assert read.revision == env.action.revision


class AutomationDatabase:
    def __init__(self):
        self.rows = {}
        self.inserts = 0

    async def execute_system(self, sql, params=None):
        params = params or []
        if sql.startswith("INSERT"):
            self.rows[params[0]] = list(params)
            self.inserts += 1
        if sql.startswith("SELECT"):
            return {
                "rows": [
                    row
                    for row in self.rows.values()
                    if (
                        (row[0] == params[0] and row[2] == params[1])
                        if "automation_id =" in sql
                        else (row[1] == params[0] and row[2] == params[1])
                    )
                ]
            }
        return {"rows": []}


@pytest.fixture
def automation_metadata(monkeypatch):
    database = AutomationDatabase()
    lease = SimpleNamespace(renew=AsyncMock(return_value=True))

    @asynccontextmanager
    async def lock(_key):
        yield lease

    monkeypatch.setattr(automations, "db", database)
    monkeypatch.setattr(automations, "metadata_lock", lock)
    return automations.AutomationRepository(), database, lease


async def test_owner_repository_recovers_same_creation_and_rejects_collision(automation_metadata):
    repository, database, _lease = automation_metadata
    body = automations.AutomationCreate(
        title="Revenue", prompt="Report revenue.", schedule_kind="cron", schedule_expr="0 8 * * 1"
    )
    args = {
        "agent_id": "finance",
        "owner_name": "alice",
        "role_name": "ANALYST",
        "creation_identity": "stable-action-1",
        "body": body,
    }
    saved = await repository.create(**args)
    assert (await repository.create(**args))["automation_id"] == saved["automation_id"]
    assert database.inserts == 1
    with pytest.raises(automations.AutomationError, match="inputs changed"):
        await repository.create(**(args | {"body": body.model_copy(update={"title": "Changed"})}))


async def test_owner_repository_refuses_stale_disable_snapshot(automation_metadata):
    repository, database, lease = automation_metadata
    body = automations.AutomationCreate(
        title="Revenue", prompt="Report revenue.", schedule_kind="cron", schedule_expr="0 8 * * 1"
    )
    saved = await repository.create(
        agent_id="finance", owner_name="alice", role_name="ANALYST", body=body
    )
    digest = fingerprint(automations.automation_configuration(saved))
    database.rows[saved["automation_id"]][5] = "Updated prompt by owner."
    with pytest.raises(automations.AutomationError, match="configuration changed"):
        await repository.update(
            saved, automations.AutomationUpdate(enabled=False), expected_configuration_digest=digest
        )
    assert database.rows[saved["automation_id"]][11] is True
    lease.renew.return_value = False
    with pytest.raises(automations.AutomationError, match="lease expired"):
        await repository.create(
            agent_id="finance", owner_name="alice", role_name="ANALYST", body=body
        )


def test_legacy_monitor_contract_dumps_do_not_gain_automation_defaults():
    configuration = {
        "name": "Revenue",
        "agent_id": "finance",
        "semantic": {"view_id": "sales", "version": 1, "fingerprint": "abc"},
        "plan": {"metrics": ["revenue", "orders"]},
        "value_column": "revenue",
        "count_column": "orders",
        "time_dimension": "ordered_at",
    }
    preview = ActionPreview(
        idempotency_key="legacy-key",
        decision_id="decision",
        expected_decision_revision=1,
        option_id="option",
        configuration=configuration,
    )
    dumped = preview.model_dump(mode="json")
    assert dumped.keys() == {
        "idempotency_key",
        "decision_id",
        "expected_decision_revision",
        "option_id",
        "adapter_id",
        "configuration",
    }
    assert "delivery" not in dumped["configuration"]
    assert Scope.from_user(USER).principal == USER["username"]


async def test_legacy_action_event_and_request_digests_survive_absent_mission_default(
    action_program,
):
    _service, action, request, *_ = action_program
    payload = action.model_dump(mode="json")
    assert "mission_id" not in payload
    restored = Action.model_validate(payload | {"mission_id": None})
    assert fingerprint(restored.model_dump(mode="json")) == fingerprint(payload)
    body = request.model_dump(mode="json")
    restored_request = ActionPreview.model_validate(body | {"mission_id": None})
    assert fingerprint(restored_request.model_dump(mode="json")) == fingerprint(body)


@pytest.mark.parametrize("revoked", [False, True])
async def test_bound_automation_worker_reauthorizes_current_role_and_semantic_pin(
    run_env, monkeypatch, revoked
):
    from app.modules.intelligence.engine import intelligence_service
    from app.modules.task_orchestration import execution
    from app.modules.task_orchestration.repository import task_orchestration_repository
    from tests.unit.test_agent_automations import FakeExecutor, automation

    _seen, append, repository = run_env
    item = automation()
    binding = automations.AutomationExecutionBinding(
        action_id="action-1",
        scope=Scope(principal="alice", active_role="analyst", security_context_version=3),
        semantic={"view_id": "sales", "version": 2, "fingerprint": "abc"},
    )
    item["delivery"] = {"execution_binding": binding.model_dump(mode="json")}
    repository.get = AsyncMock(return_value=deepcopy(item))
    monkeypatch.setattr(
        task_orchestration_repository,
        "get_role_execution_user",
        AsyncMock(return_value="another-principal" if revoked else "alice"),
    )
    authorize = AsyncMock()
    monkeypatch.setattr(intelligence_service, "authorize_semantic", authorize)
    executor = FakeExecutor()
    executor._prepare_task_session = AsyncMock()

    @asynccontextmanager
    async def cursor(_connection):
        yield SimpleNamespace()

    monkeypatch.setattr(execution, "_dict_cursor", cursor)
    result = await automations.AutomationRunner(executor, repository=repository).run(item)
    if revoked:
        assert result.status == "failed:execution_binding_changed"
        assert not executor.owners
        authorize.assert_not_awaited()
        append.assert_not_awaited()
    else:
        assert result.status == "delivered:inbox"
        assert executor.owners == ["alice"]
        assert executor._prepare_task_session.await_args.args[1].active_role == "analyst"
        authorize.assert_awaited_once()
        assert authorize.await_args.args[0] == binding.semantic
        assert authorize.await_args.args[1]["security_context_version"] == 3
        assert authorize.await_args.args[1]["intelligence_allowed_views"] == ["sales"]
