"""Action recovery uses durable dispatch state, live policy, and readback evidence."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.intelligence import actions, decisions
from app.modules.intelligence.action_contracts import (
    ActionOperation,
    ActionPreview,
    ActionReceipt,
    ActionReview,
    ActionVerification,
)
from app.modules.intelligence.contracts import MonitorConfiguration, utc_now
from app.modules.intelligence.decisions import DecisionOperation
from app.modules.intelligence.monitor_action import MonitorActionAdapter
from tests.unit.test_intelligence_decisions import program as _decision_program
from tests.unit.test_intelligence_engine import USER

program = _decision_program


def monitor_task(action, monitor):
    from app.modules.task_orchestration.internal_handlers import InternalTaskConfiguration

    return {
        "id": "task-effect",
        "created_by": action.scope.principal,
        "owner_role": action.scope.active_role,
        "handler": "intelligence.monitor",
        "handler_config": InternalTaskConfiguration(
            scope=action.scope.model_copy(update={"session_id": None}), record_id=monitor.id
        ).model_dump(mode="json"),
        "definition": "",
        "overlap_policy": "skip",
        "timezone": "UTC",
        "schedule_kind": "interval",
        "schedule_expr": f"{monitor.cadence_minutes} minutes",
    }


async def wait_for_action_consent(service, action):
    from app.modules.assistant.consent import consent_broker

    for _ in range(100):
        await asyncio.sleep(0)
        pending = await service.get(action.id, USER)
        if pending.consent_call_id and consent_broker.owner_of(pending.consent_call_id):
            return pending
    raise AssertionError("Action did not open its consent request")


class FakeAdapter:
    def __init__(self):
        self.effects = []
        self.compensations = []
        self.fail_after = False
        self.fail_compensation = False
        self.complete = True

    async def preview(self, configuration, user):
        assert configuration.enabled

    async def execute(self, action, user, *, guard=None):
        if guard:
            await guard()
        self.effects.append((action.id, deepcopy(user)))
        if self.fail_after:
            raise RuntimeError("provider failed after side effect")
        return ActionReceipt(
            monitor_id="monitor-effect",
            monitor_revision=1,
            task_id="task-effect",
            schedule_enabled=True,
        )

    async def verify(self, action, user, *, compensated=False):
        return ActionVerification(
            checked_at=utc_now(),
            complete=self.complete,
            reason=(
                "monitor_and_schedule_disabled" if compensated else "monitor_and_schedule_match"
            )
            if self.complete
            else "schedule_missing",
        )

    async def compensate(self, action, user, *, guard=None):
        if guard:
            await guard()
        self.compensations.append(action.id)
        if self.fail_compensation:
            raise RuntimeError("uncertain compensation")
        return ActionReceipt(
            monitor_id="monitor-effect",
            monitor_revision=2,
            task_id="task-effect",
            schedule_enabled=False,
        )


class Lease:
    def __init__(self):
        self.renews = 0
        self.fail_at = None

    async def renew(self):
        self.renews += 1
        return self.renews != self.fail_at


@pytest.fixture
async def action_program(program, monkeypatch):
    service, repo, source, policy, body = program
    created = await decisions.create_decision(body, USER)
    selected = await decisions.operate_decision(
        created.id,
        DecisionOperation(
            operation_id="select-for-action",
            expected_revision=created.revision,
            operation="select",
            option_id="transfer",
        ),
        USER,
    )
    decision = await decisions.operate_decision(
        created.id,
        DecisionOperation(
            operation_id="approve-decision",
            expected_revision=selected.revision,
            operation="approve",
        ),
        USER,
    )
    fake, lease = FakeAdapter(), Lease()

    @asynccontextmanager
    async def lock(key, **_options):
        yield lease

    monkeypatch.setattr(actions, "metadata_lock", lock)
    monkeypatch.setattr(actions, "settings", SimpleNamespace(STUDIO_ACTIONS_ENABLED=True))
    monkeypatch.setattr(actions, "read_business_policy", AsyncMock(side_effect=lambda: policy))
    monkeypatch.setattr(actions, "write_audit_log", AsyncMock())
    monkeypatch.setattr(actions.session_store, "get", AsyncMock(return_value=deepcopy(USER)))
    repo.action_for_review = AsyncMock(
        side_effect=lambda ident, model: deepcopy(repo.rows.get(("actions", ident)))
    )
    action_service = actions.ActionService(service, {"monitor-v1": fake})
    monkeypatch.setattr(actions, "action_service", action_service)
    monkeypatch.setattr(actions, "business_action_tool", actions.BusinessActionTool(action_service))
    configuration = MonitorConfiguration(
        name="Observe revenue",
        agent_id="finance",
        semantic=decision.semantic,
        plan={"metrics": ["revenue", "orders"]},
        value_column="revenue",
        count_column="orders",
        time_dimension="ordered_at",
        enabled=True,
    )
    request = ActionPreview(
        idempotency_key="monitor-action-1",
        decision_id=decision.id,
        expected_decision_revision=decision.revision,
        option_id="transfer",
        configuration=configuration,
    )
    action = await action_service.preview(request, USER)
    await action_service.review(
        action.id,
        ActionReview(
            operation_id="approve-action",
            expected_revision=action.revision,
            operation="approve",
        ),
        USER,
    )
    action = await action_service.get(action.id, USER)
    lease.renews = 0
    return action_service, action, request, fake, lease, policy, repo, source


def operation(action, ident="execute-action-1"):
    return ActionOperation(
        operation_id=ident, expected_revision=action.revision, thread_id="thread"
    )


@pytest.mark.asyncio
async def test_same_key_same_digest_executes_once(action_program):
    service, action, request, fake, _, _, _, _ = action_program
    assert (await service.preview(request, USER)).id == action.id
    verified = await service.dispatch(action.id, operation(action), USER)
    assert verified.status == "verified" and verified.verification.complete
    assert (await service.dispatch(action.id, operation(action), USER)).status == "verified"
    assert len(fake.effects) == 1
    assert fake.effects[0][1]["active_role"] == USER["active_role"]


@pytest.mark.asyncio
async def test_same_key_changed_payload_conflicts(action_program):
    service, _, request, fake, _, _, _, _ = action_program
    changed = request.model_copy(
        update={"configuration": request.configuration.model_copy(update={"name": "Other"})}
    )
    with pytest.raises(HTTPException) as error:
        await service.preview(changed, USER)
    assert error.value.status_code == 409 and not fake.effects


@pytest.mark.asyncio
async def test_failure_before_dispatch_can_resume(action_program):
    service, action, _, fake, _, _, repo, _ = action_program
    repo.fail_once = "actions"
    with pytest.raises(RuntimeError):
        await service.dispatch(action.id, operation(action), USER)
    assert not fake.effects
    assert (await service.dispatch(action.id, operation(action), USER)).status == "verified"
    assert len(fake.effects) == 1


@pytest.mark.asyncio
async def test_failure_after_side_effect_requires_verification_without_replay(action_program):
    service, action, _, fake, _, _, _, _ = action_program
    fake.fail_after = True
    uncertain = await service.dispatch(action.id, operation(action), USER)
    assert uncertain.status == "verification_required" and uncertain.receipt is None
    assert (
        await service.dispatch(action.id, operation(uncertain, "retry-action-1"), USER)
    ).status == "verification_required"
    assert len(fake.effects) == 1
    assert (await service.verify(action.id, USER)).status == "verified"


@pytest.mark.asyncio
async def test_crash_after_call_before_receipt_recovers_without_replay(action_program):
    service, action, _, fake, _, _, repo, _ = action_program
    execute = fake.execute

    async def crash_receipt(record, user, **kwargs):
        receipt = await execute(record, user, **kwargs)
        repo.fail_once = "actions"
        return receipt

    fake.execute = crash_receipt
    uncertain = await service.dispatch(action.id, operation(action), USER)
    assert uncertain.status == "verification_required"
    assert uncertain.dispatch_attempts == 1 and uncertain.receipt is None
    await service.dispatch(action.id, operation(uncertain, "recovery-1"), USER)
    assert len(fake.effects) == 1


@pytest.mark.asyncio
async def test_worker_cancellation_at_call_leaves_uncertain_dispatch(action_program):
    import asyncio

    service, action, _, fake, _, _, _, _ = action_program

    async def cancel_after(record, user, **kwargs):
        fake.effects.append((record.id, user))
        raise asyncio.CancelledError()

    fake.execute = cancel_after
    with pytest.raises(asyncio.CancelledError):
        await service.dispatch(action.id, operation(action), USER)
    recovery = await service.dispatch(action.id, operation(action), USER)
    assert recovery.status == "verification_required" and len(fake.effects) == 1


@pytest.mark.parametrize("fail_at", [1, 2, 3])
@pytest.mark.asyncio
async def test_stale_lease_cannot_execute(action_program, fail_at):
    service, action, _, fake, lease, _, _, _ = action_program
    lease.fail_at = fail_at
    try:
        await service.dispatch(action.id, operation(action), USER)
    except HTTPException as error:
        assert error.status_code == 409
    assert not fake.effects


@pytest.mark.parametrize(
    "change",
    [
        {"security_context_version": 2},
        {"active_role": "OTHER", "roles": ["OTHER"]},
        {"roles": []},
        {"username": "bob"},
        None,
    ],
)
@pytest.mark.asyncio
async def test_changed_session_and_revoked_role_cannot_execute(action_program, monkeypatch, change):
    service, action, _, fake, _, _, _, _ = action_program
    session = {**USER, **change} if change is not None else None
    monkeypatch.setattr(actions.session_store, "get", AsyncMock(return_value=session))
    with pytest.raises(HTTPException):
        await service.dispatch(action.id, operation(action), USER)
    assert not fake.effects


@pytest.mark.parametrize("change", ["policy", "denied", "decision", "data"])
@pytest.mark.asyncio
async def test_current_policy_approval_and_data_remain_live(action_program, change):
    service, action, _, fake, _, policy, repo, source = action_program
    if change == "policy":
        policy.revision += 1
    elif change == "denied":
        policy.blocked_actions.append("monitor")
    elif change == "decision":
        decision = repo.rows[("decisions", action.decision_id)]
        decision.status = "cancelled"
    else:
        source.revoked = True
    with pytest.raises(HTTPException):
        await service.dispatch(action.id, operation(action), USER)
    assert not fake.effects


@pytest.mark.asyncio
async def test_verification_failure_and_later_readback(action_program):
    service, action, _, fake, _, _, _, _ = action_program
    fake.complete = False
    result = await service.dispatch(action.id, operation(action), USER)
    assert result.status == "verification_required" and not result.verification.complete
    fake.complete = True
    assert (await service.verify(action.id, USER)).status == "verified"
    assert len(fake.effects) == 1


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.asyncio
async def test_compensation_needs_readback_and_never_replays(action_program, fails):
    service, action, _, fake, _, _, _, _ = action_program
    result = await service.dispatch(action.id, operation(action), USER)
    fake.fail_compensation = fails
    result = await service.dispatch(
        result.id, operation(result, "compensate-1"), USER, compensate=True
    )
    assert result.status == ("compensation_required" if fails else "compensated")
    if not fails:
        assert result.compensation.complete
    await service.dispatch(result.id, operation(result, "compensate-retry"), USER, compensate=True)
    assert len(fake.compensations) == 1


@pytest.mark.asyncio
async def test_cancel_before_execution_prevents_side_effect(action_program):
    service, action, _, fake, _, _, _, _ = action_program
    cancelled = await service.cancel(action.id, operation(action, "cancel-action"), USER)
    assert cancelled.status == "cancelled"
    with pytest.raises(HTTPException):
        await service.dispatch(action.id, operation(cancelled), USER)
    assert not fake.effects


def test_action_payload_does_not_accept_credentials():
    with pytest.raises(ValidationError):
        MonitorConfiguration(
            name="token=sk-abcdefghijklmnopqrstuv",
            agent_id="finance",
            semantic={"view_id": "v", "version": 1, "fingerprint": "f"},
            plan={"metrics": ["revenue", "orders"]},
            value_column="revenue",
            count_column="orders",
            time_dimension="at",
        )


@pytest.mark.asyncio
async def test_smart_tool_cannot_mutate_or_replay(action_program):
    service, action, _, fake, _, _, _, _ = action_program
    tool = actions.BusinessActionTool(service)
    context = SimpleNamespace(
        collaboration_root=False,
        collaboration_tools=("spawn_agent",),
        user=USER,
        user_name="alice",
        thread_id="thread",
    )
    invocation = actions.ToolInvocation(
        "call", tool.name, {"action_id": action.id, **operation(action).model_dump()}
    )
    result = await tool.run(invocation, context)
    assert not result.ok and result.error_class == "POLICY_DENIED" and not fake.effects


@pytest.mark.asyncio
async def test_monitor_adapter_verifies_monitor_and_schedule_independently(action_program):
    service, action, _, _, _, _, repo, _ = action_program
    adapter = MonitorActionAdapter(
        service.service,
        SimpleNamespace(
            find_task=AsyncMock(), get_role_execution_user=AsyncMock(return_value="alice")
        ),
    )
    monitor = adapter.monitor(action, USER)
    repo.rows[("monitors", monitor.id)] = monitor
    service.service.get = AsyncMock(return_value=monitor)
    adapter.tasks.find_task.return_value = None
    assert (await adapter.verify(action, USER)).reason == "schedule_missing"
    from app.modules.intelligence.contracts import Scope, fingerprint
    from app.modules.task_orchestration.internal_handlers import InternalTaskConfiguration

    task = {
        "id": "task-effect",
        "definition": "",
        "overlap_policy": "skip",
        "created_by": "alice",
        "owner_role": "ANALYST",
        "handler": "intelligence.monitor",
        "handler_config": InternalTaskConfiguration(
            scope=Scope.from_user(USER).model_copy(update={"session_id": None}),
            record_id=monitor.id,
        ).model_dump(mode="json"),
        "timezone": "UTC",
        "schedule_kind": "interval",
        "schedule_expr": "15 minutes",
    }
    adapter.tasks.find_task.return_value = task
    assert (await adapter.verify(action, USER)).complete
    adapter.tasks.find_task.assert_called_with(
        "intelligence_" + fingerprint(["intelligence.monitor", monitor.id])[:32],
        None,
        None,
    )
    task["owner_role"] = "OTHER"
    assert (await adapter.verify(action, USER)).reason == "schedule_changed"
    task["owner_role"] = "ANALYST"
    task["schedule_kind"], task["schedule_expr"] = "manual", None
    repo.rows[("monitors", monitor.id)] = monitor.model_copy(update={"enabled": False})
    service.service.get.return_value = repo.rows[("monitors", monitor.id)]
    assert (await adapter.verify(action, USER, compensated=True)).complete


@pytest.mark.parametrize(
    "allowed,expected", [(True, "verified"), (False, "denied"), (None, "cancelled")]
)
@pytest.mark.asyncio
async def test_supervised_api_uses_shared_gate_and_owned_broker(
    action_program, monkeypatch, allowed, expected
):
    service, action, _, fake, _, _, _, _ = action_program
    from app.modules.agents import router
    from app.modules.assistant import tool_gate
    from app.modules.assistant.consent import consent_broker

    monkeypatch.setattr(
        router, "_require_agent_thread", AsyncMock(return_value={"title": "Business"})
    )
    gate = AsyncMock(wraps=tool_gate.resolve_tool_consent)
    monkeypatch.setattr(tool_gate, "resolve_tool_consent", gate)
    task = asyncio.create_task(actions.supervised_action(action.id, operation(action), USER))
    pending = None
    for _ in range(100):
        await asyncio.sleep(0)
        pending = await service.get(action.id, USER)
        if pending.consent_call_id and consent_broker.owner_of(pending.consent_call_id):
            break
    assert pending.status == "awaiting_consent"
    assert not fake.effects
    assert not consent_broker.resolve(pending.consent_call_id, True, user_name="bob")
    assert consent_broker.classification_of(pending.consent_call_id) == "destructive"
    assert consent_broker.resolve(pending.consent_call_id, allowed, user_name="alice")
    result = await task
    assert result.status == expected
    assert len(fake.effects) == (1 if allowed else 0)
    gate.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_while_consent_pending_fences_mutation(action_program, monkeypatch):
    service, action, _, fake, _, _, _, _ = action_program
    from app.modules.agents import router
    from app.modules.assistant.consent import consent_broker

    monkeypatch.setattr(
        router, "_require_agent_thread", AsyncMock(return_value={"title": "Business"})
    )
    task = asyncio.create_task(actions.supervised_action(action.id, operation(action), USER))
    for _ in range(100):
        await asyncio.sleep(0)
        pending = await service.get(action.id, USER)
        if pending.consent_call_id and consent_broker.owner_of(pending.consent_call_id):
            break
    cancelled = await service.cancel(action.id, operation(pending, "cancel-pending"), USER)
    assert cancelled.status == "cancelled"
    assert (await task).status == "cancelled" and not fake.effects


@pytest.mark.asyncio
async def test_expired_scope_after_consent_cannot_dispatch(action_program, monkeypatch):
    service, action, _, fake, _, _, repo, _ = action_program
    from app.modules.agents import router
    from app.modules.assistant.consent import consent_broker

    monkeypatch.setattr(
        router, "_require_agent_thread", AsyncMock(return_value={"title": "Business"})
    )
    task = asyncio.create_task(actions.supervised_action(action.id, operation(action), USER))
    for _ in range(100):
        await asyncio.sleep(0)
        pending = await service.get(action.id, USER)
        if pending.consent_call_id and consent_broker.owner_of(pending.consent_call_id):
            break
    monkeypatch.setattr(actions.session_store, "get", AsyncMock(return_value=None))
    consent_broker.resolve(pending.consent_call_id, True, user_name="alice")
    with pytest.raises(HTTPException):
        await task
    assert not fake.effects
    assert repo.rows[("actions", action.id)].status == "cancelled"
    assert repo.rows[("actions", action.id)].consent_call_id is None


@pytest.mark.asyncio
async def test_durable_denial_cannot_be_overwritten_by_new_consent(action_program, monkeypatch):
    service, action, _, fake, _, _, repo, _ = action_program
    from app.modules.agents import router

    monkeypatch.setattr(
        router, "_require_agent_thread", AsyncMock(return_value={"title": "Business"})
    )
    repo.rows[("actions", action.id)] = action.model_copy(update={"status": "denied"})
    with pytest.raises(HTTPException) as error:
        await actions.supervised_action(action.id, operation(action), USER)
    assert error.value.status_code == 409 and not fake.effects


@pytest.mark.asyncio
async def test_abandoned_consent_reopens_prompt_without_reusing_approval(
    action_program, monkeypatch
):
    service, action, _, fake, _, _, repo, _ = action_program
    from app.modules.agents import router
    from app.modules.assistant.consent import consent_broker

    monkeypatch.setattr(
        router, "_require_agent_thread", AsyncMock(return_value={"title": "Business"})
    )
    abandoned = await service._write(
        action, USER, status="awaiting_consent", consent_call_id="orphaned-prompt"
    )
    task = asyncio.create_task(actions.supervised_action(abandoned.id, operation(abandoned), USER))
    for _ in range(100):
        await asyncio.sleep(0)
        pending = await service.get(action.id, USER)
        if pending.consent_call_id and consent_broker.owner_of(pending.consent_call_id):
            break
    assert not fake.effects
    assert pending.consent_call_id != "orphaned-prompt"
    consent_broker.resolve(pending.consent_call_id, True, user_name="alice")
    assert (await task).status == "verified"
    assert len(fake.effects) == 1


@pytest.mark.asyncio
async def test_public_action_excludes_session_material(action_program):
    *_, repo, _ = action_program
    from app.modules.intelligence.responses import IntelligenceResponse

    action = next(row for (kind, _), row in repo.rows.items() if kind == "actions")
    rendered = IntelligenceResponse(action.model_dump(mode="json")).body.decode()
    assert "session_id" not in rendered and USER["session_id"] not in rendered
    assert "encrypted_password" not in rendered


@pytest.mark.asyncio
async def test_registered_monitor_adapter_executes_and_compensates_existing_owners(
    action_program, monkeypatch
):
    service, action, _, _, lease, _, _, _ = action_program
    from app.modules.intelligence import schedules

    tasks = {}

    async def find(name, database, schema):
        return next((task for task in tasks.values() if task["name"] == name), None)

    async def create(value, user_name):
        task = {**value, "id": "scheduled-monitor", "created_by": user_name}
        tasks[task["id"]] = task
        return task

    async def update(ident, changes):
        tasks[ident].update(changes)
        return tasks[ident]

    repository = SimpleNamespace(
        find_task=AsyncMock(side_effect=find),
        create_task=AsyncMock(side_effect=create),
        update_task=AsyncMock(side_effect=update),
        get_role_execution_user=AsyncMock(return_value="alice"),
    )

    @asynccontextmanager
    async def lock(key):
        yield lease

    monkeypatch.setattr(schedules, "metadata_lock", lock)
    monkeypatch.setattr(schedules, "task_orchestration_repository", repository)
    monkeypatch.setattr(service.service, "validate_monitor", AsyncMock())
    adapter = MonitorActionAdapter(service.service, repository)
    receipt = await adapter.execute(action, USER)
    assert receipt.task_id == "scheduled-monitor" and receipt.schedule_enabled
    assert (await adapter.verify(action, USER)).complete
    undone = await adapter.compensate(action, USER)
    assert not undone.schedule_enabled
    assert (await adapter.verify(action, USER, compensated=True)).complete
    repository.create_task.assert_awaited_once()
    repository.update_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_outcomes_link_verified_actions_without_claiming_business_effect(
    action_program, monkeypatch
):
    service, action, _, _, _, _, repo, _ = action_program
    verified = await service.dispatch(action.id, operation(action), USER)
    from app.modules.intelligence import engine
    from app.modules.intelligence.contracts import MetricObservation

    decision = repo.rows[("decisions", action.decision_id)]
    monkeypatch.setattr(engine, "utc_now", lambda: decision.outcome_window.end)
    original = service.service.observe

    async def complete(monitor, window, user, budget):
        observed = await original(monitor, window, user, budget)
        return MetricObservation.model_validate({**observed.model_dump(), "completeness": 1})

    monkeypatch.setattr(service.service, "observe", complete)
    outcome = await service.service.evaluate_outcome(decision.id, USER)
    assert outcome.status == "complete"
    assert outcome.action_ids == [verified.id]
    assert outcome.attribution == "observed_after"
    assert outcome.dimensions["action_business_effect_verified"] is None
    assert outcome.learning_refs == []


@pytest.mark.asyncio
async def test_learning_references_require_complete_outcome_and_persisted_knowledge(
    action_program, monkeypatch
):
    service, action, _, _, _, _, repo, _ = action_program
    from app.modules.agents import memory
    from app.modules.intelligence import engine

    decision = repo.rows[("decisions", action.decision_id)]
    decision.learning_enabled = True
    decision.last_operation_id = "enable-learning-1"
    decision.last_operation_digest = "enable-learning"
    decision = await decisions._write_decision(
        decision,
        USER,
        expected_revision=decision.revision,
        event="selected",
    )
    remember = AsyncMock(return_value="knowledge-record")
    from app.modules.agents.knowledge import KnowledgeRevision
    from app.modules.intelligence.contracts import fingerprint

    revisions = AsyncMock(
        return_value=[
            KnowledgeRevision(
                memory_id="knowledge-record",
                revision=2,
                fact="Observed outcome",
                semantic=decision.semantic,
                definition={
                    "outcome_id": fingerprint([decision.id, decision.revision, "outcome-v1"])
                },
            )
        ]
    )

    async def remember_outcome(**kwargs):
        finalized = await kwargs["finalize_outcome"]("knowledge-record", 2)
        revisions.return_value[0].definition["outcome_revision"] = finalized["revision"]
        return "knowledge-record"

    remember.side_effect = remember_outcome
    monkeypatch.setattr(memory.memory_repository, "upsert", remember)
    monkeypatch.setattr(memory.memory_repository, "revisions", revisions)
    pending = await service.service.evaluate_outcome(decision.id, USER)
    assert pending.status == "pending" and not pending.learning_refs
    remember.assert_not_awaited()
    monkeypatch.setattr(engine, "utc_now", lambda: decision.outcome_window.end)
    original = service.service.observe

    async def complete(monitor, window, user, budget):
        observed = await original(monitor, window, user, budget)
        return observed.model_copy(update={"completeness": 1})

    monkeypatch.setattr(service.service, "observe", complete)
    outcome = await service.service.evaluate_outcome(decision.id, USER)
    assert outcome.status == "complete"
    assert revisions.return_value[0].definition["outcome_revision"] == outcome.revision
    assert [ref.model_dump() for ref in outcome.learning_refs] == [
        {"kind": "knowledge", "id": "knowledge-record", "revision": 2},
    ]
    from app.modules.intelligence.contracts import Outcome

    reloaded = Outcome.model_validate_json(outcome.model_dump_json())
    assert reloaded.learning_refs == outcome.learning_refs
    await service.service.authorize_record(reloaded, USER, engine.CycleBudget())
    remember.assert_awaited_once()
    repeated = await service.service.evaluate_outcome(decision.id, USER)
    assert repeated.learning_refs == outcome.learning_refs
    remember.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_api_passes_revision_and_rejects_stale_readback(action_program, monkeypatch):
    from app.modules.agents import router
    from app.modules.intelligence import action_router

    service, action, _, fake, _, _, _, _ = action_program
    result = await service.dispatch(action.id, operation(action), USER)
    monkeypatch.setattr(action_router, "action_service", service)
    owned_thread = AsyncMock()
    monkeypatch.setattr(router, "_require_agent_thread", owned_thread)
    verify = AsyncMock(wraps=service.verify)
    monkeypatch.setattr(service, "verify", verify)
    with pytest.raises(HTTPException) as error:
        await action_router.verify_action(result.id, operation(action, "verify-stale"), USER)
    assert error.value.status_code == 409
    verify.assert_awaited_once_with(result.id, USER, expected_revision=action.revision)
    owned_thread.assert_awaited_once_with("thread", "finance", "alice")
    assert len(fake.effects) == 1


@pytest.mark.asyncio
async def test_changed_fence_during_readback_cannot_publish_verification(action_program):
    service, action, _, fake, _, _, repo, _ = action_program
    result = await service.dispatch(action.id, operation(action), USER)
    original = fake.verify

    async def stale_readback(record, user, **kwargs):
        repo.rows[("actions", record.id)].dispatch_fence = "new-worker-fence"
        return await original(record, user, **kwargs)

    fake.verify = stale_readback
    with pytest.raises(HTTPException) as error:
        await service.verify(result.id, USER, expected_revision=result.revision)
    assert error.value.status_code == 409
    assert repo.rows[("actions", action.id)].revision == result.revision
    assert repo.rows[("actions", action.id)].dispatch_fence == "new-worker-fence"


@pytest.mark.parametrize("change", ["session", "policy"])
@pytest.mark.asyncio
async def test_revocation_during_readback_cannot_publish_verification(
    action_program, monkeypatch, change
):
    service, action, _, fake, _, policy, repo, _ = action_program
    result = await service.dispatch(action.id, operation(action), USER)
    original = fake.verify

    async def revoke(record, user, **kwargs):
        if change == "session":
            monkeypatch.setattr(actions.session_store, "get", AsyncMock(return_value=None))
        else:
            policy.revision += 1
        return await original(record, user, **kwargs)

    fake.verify = revoke
    with pytest.raises(HTTPException):
        await service.verify(result.id, USER)
    assert repo.rows[("actions", action.id)].revision == result.revision


@pytest.mark.asyncio
async def test_stale_dispatch_cannot_overwrite_new_worker_after_effect(action_program):
    service, action, _, fake, _, _, repo, _ = action_program
    original = fake.execute

    async def replace_worker(record, user, **kwargs):
        receipt = await original(record, user, **kwargs)
        latest = repo.rows[("actions", record.id)]
        repo.rows[("actions", record.id)] = latest.model_copy(
            update={"revision": latest.revision + 1, "dispatch_fence": "new-worker-fence"}
        )
        return receipt

    fake.execute = replace_worker
    with pytest.raises(HTTPException) as error:
        await service.dispatch(action.id, operation(action), USER)
    assert error.value.status_code == 409
    assert repo.rows[("actions", action.id)].dispatch_fence == "new-worker-fence"
    assert repo.rows[("actions", action.id)].receipt is None
    recovered = await service.dispatch(action.id, operation(action), USER)
    assert recovered.status == "verification_required"
    assert len(fake.effects) == 1


@pytest.mark.asyncio
async def test_readback_exception_remains_unconfirmed_and_sanitized(action_program):
    service, action, _, fake, _, _, _, _ = action_program
    fake.verify = AsyncMock(side_effect=RuntimeError("password=do-not-expose-this"))
    result = await service.dispatch(action.id, operation(action), USER)
    assert result.status == "verification_required"
    assert result.verification.reason == "readback_failed"
    assert "do-not-expose" not in result.model_dump_json()
    assert len(fake.effects) == 1


@pytest.mark.asyncio
async def test_consent_cleanup_cannot_cancel_newer_prompt(action_program):
    service, action, _, fake, _, _, repo, _ = action_program
    abandoned = await service._write(
        action, USER, status="awaiting_consent", consent_call_id="old-request"
    )
    current = await service._write(abandoned, USER, consent_call_id="new-request")
    result = await service.settle_consent(abandoned, USER)
    assert result.consent_call_id == "new-request"
    assert repo.rows[("actions", action.id)].revision == current.revision
    assert not fake.effects


@pytest.mark.parametrize("choice", [False, None, "cancel", "disconnect"])
@pytest.mark.asyncio
async def test_compensation_consent_denial_or_cancellation_preserves_original_effect(
    action_program, monkeypatch, choice
):
    from app.modules.agents import router
    from app.modules.assistant.consent import consent_broker

    service, action, _, fake, _, _, _, _ = action_program
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={}))
    verified = await service.dispatch(action.id, operation(action), USER)
    task = asyncio.create_task(
        actions.supervised_action(
            action.id, operation(verified, "compensate-consent"), USER, compensate=True
        )
    )
    pending = await wait_for_action_consent(service, action)
    assert pending.consent_previous_status == "verified" and pending.consent_compensate
    if choice == "cancel":
        result = await service.cancel(action.id, operation(pending, "cancel-undo"), USER)
        assert result.status == "verified"
    elif choice == "disconnect":
        task.cancel()
    else:
        assert consent_broker.resolve(pending.consent_call_id, choice, user_name="alice")
    if choice == "disconnect":
        with pytest.raises(asyncio.CancelledError):
            await task
        result = await service.get(action.id, USER)
    else:
        result = await task
    assert result.status == "verified" and result.verification.complete
    assert result.receipt == verified.receipt
    assert result.consent_call_id is None
    assert not fake.compensations and len(fake.effects) == 1
    assert consent_broker.owner_of(pending.consent_call_id) is None


@pytest.mark.asyncio
async def test_verified_readback_cannot_consume_pending_compensation_consent(
    action_program, monkeypatch
):
    from app.modules.agents import router
    from app.modules.assistant.consent import consent_broker

    service, action, _, fake, _, _, _, _ = action_program
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={}))
    verified = await service.dispatch(action.id, operation(action), USER)
    task = asyncio.create_task(
        actions.supervised_action(
            action.id, operation(verified, "compensate-consent"), USER, compensate=True
        )
    )
    pending = await wait_for_action_consent(service, action)
    try:
        with pytest.raises(HTTPException) as error:
            await service.verify(action.id, USER, expected_revision=pending.revision)
        assert error.value.status_code == 409
        assert not fake.compensations
    finally:
        consent_broker.resolve(pending.consent_call_id, False, user_name="alice")
        await task


@pytest.mark.parametrize(
    "change",
    [
        {"when_expr": "FALSE"},
        {"overlap_policy": "allow"},
        {"definition": "SELECT 1"},
        {"schedule_kind": "manual", "schedule_expr": None},
        {"handler_config": "invalid-json"},
        {"owner_role": "OTHER"},
        {"id": "replacement-task"},
        {"timezone": "Asia/Jakarta"},
    ],
)
@pytest.mark.asyncio
async def test_monitor_readback_rejects_scheduler_drift(action_program, change):
    service, action, _, _, _, _, repo, _ = action_program
    tasks = SimpleNamespace(
        find_task=AsyncMock(), get_role_execution_user=AsyncMock(return_value="alice")
    )
    adapter = MonitorActionAdapter(service.service, tasks)
    monitor = adapter.monitor(action, USER)
    repo.rows[("monitors", monitor.id)] = monitor
    receipt = ActionReceipt(
        monitor_id=monitor.id,
        monitor_revision=monitor.revision,
        task_id="task-effect",
        schedule_enabled=True,
    )
    action = action.model_copy(update={"receipt": receipt})
    tasks.find_task.return_value = {**monitor_task(action, monitor), **change}
    verification = await adapter.verify(action, USER)
    assert not verification.complete and verification.reason == "schedule_changed"


@pytest.mark.parametrize("change", ["revision", "session", "binding"])
@pytest.mark.asyncio
async def test_monitor_readback_rejects_changed_revision_or_execution_scope(action_program, change):
    service, action, _, _, _, _, repo, _ = action_program
    tasks = SimpleNamespace(
        find_task=AsyncMock(), get_role_execution_user=AsyncMock(return_value="alice")
    )
    adapter = MonitorActionAdapter(service.service, tasks)
    monitor = adapter.monitor(action, USER)
    tasks.find_task.return_value = monitor_task(action, monitor)
    action = action.model_copy(
        update={
            "receipt": ActionReceipt(
                monitor_id=monitor.id,
                monitor_revision=monitor.revision,
                task_id="task-effect",
                schedule_enabled=True,
            )
        }
    )
    if change == "revision":
        monitor.revision += 1
    elif change == "session":
        monitor.scope.session_id = "other-session"
    else:
        tasks.get_role_execution_user.return_value = "another-principal"
    repo.rows[("monitors", monitor.id)] = monitor
    assert not (await adapter.verify(action, USER)).complete


@pytest.mark.asyncio
async def test_uncertain_compensation_can_verify_without_reusing_execution_receipt(action_program):
    service, action, _, _, _, _, repo, _ = action_program
    adapter = MonitorActionAdapter(service.service, SimpleNamespace(find_task=AsyncMock()))
    monitor = adapter.monitor(action, USER)
    action = action.model_copy(
        update={
            "receipt": ActionReceipt(
                monitor_id=monitor.id, monitor_revision=1, task_id=None, schedule_enabled=True
            ),
            "compensation_attempts": 1,
        }
    )
    monitor.enabled = False
    monitor.revision = 2
    repo.rows[("monitors", monitor.id)] = monitor
    adapter.tasks.find_task.return_value = None
    result = await adapter.verify(action, USER, compensated=True)
    assert result.complete and result.reason == "monitor_and_schedule_disabled"


@pytest.mark.asyncio
async def test_compensation_will_not_overwrite_modified_schedule(action_program, monkeypatch):
    from app.modules.intelligence import monitor_action

    service, action, _, _, _, _, repo, _ = action_program
    adapter = MonitorActionAdapter(service.service, SimpleNamespace(find_task=AsyncMock()))
    monitor = adapter.monitor(action, USER)
    repo.rows[("monitors", monitor.id)] = monitor
    adapter.tasks.find_task.return_value = {
        **monitor_task(action, monitor),
        "schedule_expr": "30 minutes",
    }
    register = AsyncMock()
    configure = AsyncMock()
    monkeypatch.setattr(service.service, "register_monitor", register)
    monkeypatch.setattr(monitor_action, "configure_schedule", configure)
    with pytest.raises(HTTPException) as error:
        await adapter.compensate(action, USER)
    assert error.value.status_code == 409
    register.assert_not_awaited()
    configure.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_write_failure_repairs_dispatch_intent_without_replay(action_program):
    from app.modules.intelligence.contracts import fingerprint

    service, action, _, fake, _, _, repo, _ = action_program
    original = fake.execute

    async def crash_journal(record, user, **kwargs):
        result = await original(record, user, **kwargs)
        repo.fail_once = "action_events"
        return result

    fake.execute = crash_journal
    with pytest.raises(HTTPException):
        await service.dispatch(action.id, operation(action), USER)
    saved = repo.rows[("actions", action.id)]
    assert saved.status == "verification_required" and saved.receipt
    event_id = fingerprint([saved.id, saved.revision])
    assert ("action_events", event_id) not in repo.rows
    recovered = await service.get(action.id, USER)
    event = repo.rows[("action_events", event_id)]
    assert event.actor == "alice" and event.action_revision == recovered.revision
    assert event.dispatch_fence == recovered.dispatch_fence
    assert event.context_digest == fingerprint(recovered.model_dump(mode="json"))
    await service.dispatch(action.id, operation(recovered, "recover-journal"), USER)
    assert len(fake.effects) == 1


@pytest.mark.parametrize("compensate", [False, True])
@pytest.mark.asyncio
async def test_monitor_adapter_checks_guard_between_metadata_and_schedule(
    action_program, monkeypatch, compensate
):
    from app.modules.intelligence import monitor_action

    service, action, _, _, _, _, repo, _ = action_program
    adapter = MonitorActionAdapter(service.service, SimpleNamespace(find_task=AsyncMock()))
    monitor = adapter.monitor(action, USER)
    if compensate:
        repo.rows[("monitors", monitor.id)] = monitor
        adapter.tasks.find_task.return_value = monitor_task(action, monitor)
    register = AsyncMock(return_value=monitor)
    configure = AsyncMock()
    monkeypatch.setattr(service.service, "register_monitor", register)
    monkeypatch.setattr(monitor_action, "configure_schedule", configure)
    guard = AsyncMock(side_effect=[None, HTTPException(status_code=409, detail="Stale fence")])
    with pytest.raises(HTTPException):
        if compensate:
            await adapter.compensate(action, USER, guard=guard)
        else:
            await adapter.execute(action, USER, guard=guard)
    register.assert_awaited_once()
    assert guard.await_count == 2
    configure.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_policy_is_audited_without_provider_payload(action_program):
    service, action, _, fake, _, policy, _, _ = action_program
    policy.revision += 1
    with pytest.raises(HTTPException):
        await service.dispatch(action.id, operation(action), USER)
    assert not fake.effects
    audit = actions.write_audit_log.call_args.kwargs
    assert audit["status"] == "DENIED" and audit["object_name"] == action.id
    assert audit["active_role"] == USER["active_role"]
    assert audit["security_context_version"] == USER["security_context_version"]
    assert "sql_text" not in audit and "configuration" not in audit


@pytest.mark.asyncio
async def test_declined_compensation_can_request_new_consent_and_finish(
    action_program, monkeypatch
):
    from app.modules.agents import router
    from app.modules.assistant.consent import consent_broker

    service, action, _, fake, _, _, _, _ = action_program
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={}))
    verified = await service.dispatch(action.id, operation(action), USER)
    task = asyncio.create_task(
        actions.supervised_action(
            action.id, operation(verified, "decline-undo"), USER, compensate=True
        )
    )
    pending = await wait_for_action_consent(service, action)
    consent_broker.resolve(pending.consent_call_id, False, user_name="alice")
    declined = await task
    task = asyncio.create_task(
        actions.supervised_action(
            action.id, operation(declined, "approve-undo"), USER, compensate=True
        )
    )
    pending = await wait_for_action_consent(service, action)
    consent_broker.resolve(pending.consent_call_id, True, user_name="alice")
    result = await task
    assert result.status == "compensated" and result.compensation.complete
    assert result.receipt == verified.receipt and result.receipt.schedule_enabled
    assert not result.compensation_receipt.schedule_enabled
    assert len(fake.compensations) == 1 and len(fake.effects) == 1


@pytest.mark.asyncio
async def test_conflicting_action_event_fails_closed(action_program):
    from app.modules.intelligence.contracts import fingerprint

    service, action, _, fake, _, _, repo, _ = action_program
    event = repo.rows[("action_events", fingerprint([action.id, action.revision]))]
    event.context_digest = "conflicting-event"
    with pytest.raises(HTTPException) as error:
        await service.dispatch(action.id, operation(action), USER)
    assert error.value.status_code == 409 and not fake.effects


@pytest.mark.asyncio
async def test_same_operation_changed_inputs_conflict_even_after_dispatch(action_program):
    service, action, _, fake, _, _, _, _ = action_program
    await service.dispatch(action.id, operation(action), USER)
    changed = operation(action).model_copy(update={"thread_id": "different-thread"})
    with pytest.raises(HTTPException) as error:
        await service.dispatch(action.id, changed, USER)
    assert error.value.status_code == 409 and len(fake.effects) == 1


@pytest.mark.asyncio
async def test_same_key_in_another_auth_session_cannot_reuse_action(action_program, monkeypatch):
    service, _, request, fake, _, _, _, _ = action_program
    other = {**USER, "session_id": "another-session"}
    monkeypatch.setattr(actions.session_store, "get", AsyncMock(return_value=other))
    with pytest.raises(HTTPException) as error:
        await service.preview(request, other)
    assert error.value.status_code == 404 and not fake.effects
