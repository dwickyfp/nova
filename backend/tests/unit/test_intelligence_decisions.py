"""Decision review binds current policy, exact inputs, evidence and durable events."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.access_control.business_policy import BusinessPolicy
from app.modules.intelligence import decisions, engine, scenarios
from app.modules.intelligence.contracts import Scope, Window
from app.modules.intelligence.decisions import DecisionCreate, DecisionOperation, OptionInput
from app.modules.ml_engine.analysis import Simulation, simulate
from app.modules.ml_engine.decision_lab import SimulationInput
from tests.unit.test_intelligence_engine import (
    END,
    USER,
    WINDOW,
    MemoryRepository,
    ObservationSource,
)


class Journal(MemoryRepository):
    def __init__(self):
        super().__init__()
        self.history = {}

    async def save(self, kind, record, **kwargs):
        saved = await super().save(kind, record, **kwargs)
        self.history[(kind, saved.id, saved.revision)] = deepcopy(saved)
        return saved

    async def get(self, kind, record_id, scope, model, *, revision=None):
        if revision:
            row = self.history.get((kind, record_id, revision))
            if row is None:
                current = self.rows.get((kind, record_id))
                row = current if current and current.revision == revision else None
            return deepcopy(row) if row and row.scope.principal == scope.principal else None
        return await super().get(kind, record_id, scope, model)

    async def related(self, kind, decision_id, scope, model):
        return [
            deepcopy(row)
            for (table, _), row in self.rows.items()
            if table == kind and row.decision_id == decision_id
        ]

    async def shared_decision(self, record_id, owner_name, model):
        row = self.rows.get(("decisions", record_id))
        return deepcopy(row) if row and row.scope.principal == owner_name else None


def pinned_mission(record, kind, scope, *, thread_id="thread"):
    from app.modules.agents.mission_schema import Mission, ObjectRef, WorkIntent, object_binding_key

    pin = ObjectRef(kind=kind, id=record.id, revision=record.revision)
    return Mission(
        mission_id="resumed-mission",
        thread_id=thread_id,
        scope=scope,
        objective="Continue the governed lifecycle",
        work_intent=WorkIntent.PLAN,
        status="running",
        revision=1,
        operation_id="mission-created",
        created_at=END,
        updated_at=END,
        object_refs=[pin],
        object_bindings={object_binding_key(pin): record.scope},
    )


def advance_mission_pin(mission, record):
    from app.modules.agents.mission_schema import ObjectRef, object_binding_key

    mission.historical_object_refs.append(mission.object_refs[0].model_copy(deep=True))
    pin = ObjectRef(kind="decision", id=record.id, revision=record.revision)
    mission.object_refs = [pin]
    mission.object_bindings[object_binding_key(pin)] = record.scope


@pytest.fixture
def mission_workflow(monkeypatch):
    from app.modules.agents import mission

    monkeypatch.setattr(mission.settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)


@pytest.fixture
async def program(monkeypatch):
    @asynccontextmanager
    async def lock(_key):
        yield

    monkeypatch.setattr(engine, "metadata_lock", lock)
    from app.modules.intelligence.contracts import Monitor, SemanticRef
    from tests.unit.test_intelligence_engine import REF

    repo, source = Journal(), ObservationSource()
    original = source._readable_version

    async def version(*args):
        view, row = await original(*args)
        row["definition"] = {"metrics": [{"name": "revenue", "currency": "IDR"}]}
        return view, row

    source._readable_version = version
    service = engine.IntelligenceService(repo, source)
    monkeypatch.setattr(service, "_decision_grant", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "write_audit_log", AsyncMock())
    monkeypatch.setattr(decisions, "intelligence_service", service)
    monkeypatch.setattr(decisions, "utc_now", lambda: END)
    monkeypatch.setattr(engine, "utc_now", lambda: END)
    policy = BusinessPolicy(revision=2, maximum_cost=1000, reviewer_roles=["ANALYST"])
    monkeypatch.setattr(decisions, "read_business_policy", AsyncMock(side_effect=lambda: policy))
    from app.modules.access_control import business_policy

    monkeypatch.setattr(
        business_policy, "read_business_policy", AsyncMock(side_effect=lambda: policy)
    )

    async def numerical(value, user, *, operation_id):
        return {**simulate(Simulation(**value.model_dump())), "run_id": operation_id}

    monkeypatch.setattr(scenarios, "run_simulation", numerical)
    monitor = Monitor(
        id="monitor",
        scope=Scope.from_user(USER),
        name="Revenue",
        agent_id="finance",
        semantic=SemanticRef.model_validate(REF.model_dump()),
        plan={"metrics": ["revenue", "orders"]},
        value_column="revenue",
        count_column="orders",
        time_dimension="ordered_at",
        driver_dimensions=["city"],
    )
    repo.rows[("monitors", monitor.id)] = monitor
    incident = await service.run_monitor(monitor.id, WINDOW, USER)
    investigation = await service.investigate(incident["news_id"], USER)
    body = DecisionCreate(
        operation_id="propose-1",
        title="Jakarta stock recovery",
        investigation_id=investigation.id,
        learning_enabled=False,
        outcome_window=Window(start=END + timedelta(days=1), end=END + timedelta(days=2)),
        options=[
            OptionInput(
                id="transfer",
                description="Transfer available stock",
                simulation=SimulationInput(
                    action_type="inventory_transfer",
                    baseline_units=60,
                    price=1,
                    unit_cost=0.2,
                    expected_unit_change=40,
                    unit_change_uncertainty=10,
                    action_cost=5,
                    capacity=120,
                    max_budget=10,
                ),
            )
        ],
    )
    return service, repo, source, policy, body


@pytest.mark.asyncio
async def test_decision_journal_recovers_crash_and_retry_does_not_duplicate(program):
    service, repo, _, _, body = program
    repo.fail_once = "decisions"
    with pytest.raises(RuntimeError):
        await decisions.create_decision(body, USER)
    assert len([key for key in repo.rows if key[0] == "events"]) == 1
    record = await decisions.create_decision(body, USER)
    assert (await decisions.create_decision(body, USER)).id == record.id
    lineage = await service.lineage(record.id, USER)
    assert [event.event for event in lineage["events"]] == ["created"]
    assert all(option.run_id for option in record.options)
    with pytest.raises(HTTPException) as exc:
        await decisions.create_decision(body.model_copy(update={"title": "Changed request"}), USER)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_changed_policy_invalidates_approval_and_requires_new_selection(program):
    service, repo, _, policy, body = program
    created = await decisions.create_decision(body, USER)
    selected = await decisions.operate_decision(
        created.id,
        DecisionOperation(
            operation_id="select-1", expected_revision=1, operation="select", option_id="transfer"
        ),
        USER,
    )
    assert selected.status == "awaiting_approval"
    policy.revision += 1
    with pytest.raises(HTTPException) as exc:
        await decisions.operate_decision(
            created.id,
            DecisionOperation(operation_id="approve1", expected_revision=2, operation="approve"),
            USER,
        )
    assert exc.value.status_code == 409
    assert repo.rows[("decisions", created.id)].status == "awaiting_approval"
    refreshed = await decisions.operate_decision(
        created.id,
        DecisionOperation(
            operation_id="select-2", expected_revision=2, operation="select", option_id="transfer"
        ),
        USER,
    )
    approved = await decisions.operate_decision(
        created.id,
        DecisionOperation(
            operation_id="approve2", expected_revision=refreshed.revision, operation="approve"
        ),
        USER,
    )
    assert approved.status == "approved"
    assert len((await service.lineage(created.id, USER))["events"]) == 4


@pytest.mark.asyncio
async def test_shared_same_role_viewer_still_must_reproduce_evidence(program, monkeypatch):
    service, _, source, _, body = program
    created = await decisions.create_decision(body, USER)
    bob = {**USER, "username": "bob", "session_id": "bob-session"}
    monkeypatch.setattr(service, "_decision_grant", AsyncMock(return_value={"owner_name": "alice"}))
    assert (await service.get("decisions", created.id, bob)).id == created.id
    source.masked = True
    with pytest.raises(HTTPException) as exc:
        await service.get("decisions", created.id, bob)
    assert exc.value.status_code == 409
    assert source.calls[-1][1]["username"] == "bob"


@pytest.mark.asyncio
async def test_resumed_mission_creates_under_current_binding_from_exact_investigation_pin(
    program, monkeypatch, mission_workflow
):
    from app.modules.agents import mission, router
    from app.modules.assistant.repository import assistant_repository

    service, repo, source, _, body = program
    current_user = {**USER, "session_id": "new-session", "security_context_version": 2}
    get, reads = repo.get, []

    async def scoped_get(kind, record_id, scope, model, *, revision=None):
        record = await get(kind, record_id, scope, model, revision=revision)
        if record and (
            record.scope.principal,
            record.scope.active_role,
            record.scope.security_context_version,
        ) != (scope.principal, scope.active_role, scope.security_context_version):
            record = None
        reads.append((kind, scope.security_context_version, revision, record is not None))
        return record

    monkeypatch.setattr(repo, "get", scoped_get)
    current_scope = Scope.from_user(current_user)
    pinned = repo.rows[("investigations", body.investigation_id)]
    bound = pinned_mission(pinned, "investigation", current_scope, thread_id="thread-resumed")
    current = AsyncMock(return_value=bound)
    monkeypatch.setattr(mission.mission_service, "get", current)
    monkeypatch.setattr(mission.mission_service, "_get_owner", AsyncMock(return_value=bound))
    monkeypatch.setattr(mission, "require_thread", AsyncMock())
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock())
    monkeypatch.setattr(assistant_repository, "learning_enabled", AsyncMock(return_value=False))
    request = body.model_copy(
        update={"mission_id": "resumed-mission", "investigation_revision": pinned.revision}
    )
    created = await decisions.create_decision(request, current_user)
    assert created.scope == current_scope and created.thread_id == bound.thread_id
    assert created.investigation_id == pinned.id
    assert created.investigation_revision == pinned.revision
    assert created.mission_id == "resumed-mission"
    assert ("news", 2, pinned.news_revision, False) in reads
    assert ("news", 1, pinned.news_revision, True) in reads
    assert ("monitors", 1, 1, True) in reads
    assert created.options[0].evidence_ids == [item.id for item in pinned.evidence]
    from app.modules.agents.mission_schema import ObjectRef, object_binding_key

    decision_pin = ObjectRef(kind="decision", id=created.id, revision=created.revision)
    bound.object_refs.append(decision_pin)
    bound.object_bindings[object_binding_key(decision_pin)] = created.scope
    news_current = repo.rows[("news", pinned.news_id)]
    await repo.save(
        "news",
        news_current.model_copy(update={"after": 9999}),
        expected_revision=news_current.revision,
    )
    lineage = await service.lineage(created.id, current_user, mission_id="resumed-mission")
    assert lineage["decision"].scope == current_scope
    assert lineage["investigation"].scope == pinned.scope
    assert lineage["investigation"].revision == pinned.revision
    assert lineage["news"].scope == pinned.scope
    assert lineage["news"].revision == pinned.news_revision
    assert lineage["news"].after == created.baseline
    assert repo.rows[("news", pinned.news_id)].after == 9999
    with pytest.raises(HTTPException):
        await service.lineage(created.id, current_user)
    assert all(call[1]["session_id"] == "new-session" for call in source.calls[-4:])
    current.assert_awaited_once_with("resumed-mission", current_user, project=False)
    wrong = request.model_copy(update={"investigation_revision": pinned.revision + 1})
    with pytest.raises(HTTPException) as stale:
        await decisions.create_decision(wrong, current_user)
    assert stale.value.status_code == 404
    current.side_effect = HTTPException(status_code=404, detail="Mission binding unavailable")
    with pytest.raises(HTTPException) as denied:
        await decisions.create_decision(request, USER)
    assert denied.value.status_code == 404
    source.revoked = True
    with pytest.raises(HTTPException):
        await service.lineage(created.id, current_user, mission_id="resumed-mission")


@pytest.mark.asyncio
async def test_resumed_decision_still_reauthorizes_evidence_before_numerical_execution(
    program, monkeypatch, mission_workflow
):
    from app.modules.agents import mission

    service, repo, source, _, body = program
    pinned = repo.rows[("investigations", body.investigation_id)]
    bound = pinned_mission(pinned, "investigation", Scope.from_user(USER))
    monkeypatch.setattr(mission.mission_service, "get", AsyncMock(return_value=bound))
    monkeypatch.setattr(mission.mission_service, "_get_owner", AsyncMock(return_value=bound))
    monkeypatch.setattr(mission, "require_thread", AsyncMock())
    executor = AsyncMock()
    monkeypatch.setattr(scenarios, "run_simulation", executor)
    source.revoked = True
    request = body.model_copy(update={"mission_id": "resumed-mission"})
    with pytest.raises(HTTPException):
        await decisions.create_decision(request, USER)
    executor.assert_not_awaited()


def test_absent_mission_metadata_preserves_historical_request_digest():
    # The legacy payload did not contain optional Mission pins.
    body = decisions.DecisionCreate(
        operation_id="legacy-request",
        title="Legacy",
        investigation_id="investigation",
        outcome_window=Window(start=END + timedelta(days=1), end=END + timedelta(days=2)),
        options=[
            OptionInput(
                id="legacy",
                description="Old option",
                simulation=SimulationInput.model_validate(
                    {
                        "action_type": "spend",
                        "baseline_units": 60,
                        "price": 1,
                        "unit_cost": 0.2,
                        "expected_unit_change": 40,
                        "unit_change_uncertainty": 10,
                        "action_cost": 5,
                        "capacity": 120,
                        "max_budget": 10,
                    }
                ),
            )
        ],
    )
    legacy = body.model_dump(mode="json", exclude={"mission_id", "investigation_revision"})
    for option in legacy["options"]:
        for name in ("parameters", "scenario_kind", "scenario_version"):
            option.pop(name)
    assert decisions.decision_request_digest(body) == decisions.fingerprint(legacy)


@pytest.mark.asyncio
async def test_historical_decision_digest_ignores_absent_additive_fields(program):
    from app.modules.intelligence.contracts import Decision

    _, _, _, _, body = program
    created = await decisions.create_decision(body, USER)
    historical = created.model_dump(mode="json")
    historical.pop("mission_id", None)
    historical.pop("investigation_revision", None)
    for option in historical["options"]:
        for name in ("effects", "scenario_kind", "scenario_version"):
            option.pop(name, None)
    restored = Decision.model_validate(historical)
    expected = {k: v for k, v in historical.items() if k not in {"created_at", "updated_at"}}
    for evidence in expected["evidence"]:
        if evidence.get("evidence_health") is None:
            evidence.pop("evidence_health", None)
    assert decisions.decision_digest(restored) == decisions.fingerprint(expected)


@pytest.fixture
async def resumed_decision(program, monkeypatch, mission_workflow):
    from app.modules.agents import mission

    service, repo, source, policy, body = program
    original = await decisions.create_decision(body, USER)
    current_user = {**USER, "session_id": "resumed-session", "security_context_version": 2}
    binding = pinned_mission(original, "decision", Scope.from_user(current_user))
    monkeypatch.setattr(mission.mission_service, "get", AsyncMock(return_value=binding))
    monkeypatch.setattr(mission.mission_service, "_get_owner", AsyncMock(return_value=binding))
    monkeypatch.setattr(mission, "require_thread", AsyncMock())
    return service, repo, source, policy, original, current_user, binding


async def test_mission_operation_keeps_original_scope_and_recovers_before_or_after_link(
    resumed_decision,
):
    _, repo, source, _, original, current_user, binding = resumed_decision
    request = DecisionOperation(
        operation_id="resume-select",
        expected_revision=1,
        operation="select",
        option_id="transfer",
        mission_id="mission-1",
    )
    selected = await decisions.operate_decision(original.id, request, current_user)
    assert selected.scope == original.scope and selected.revision == original.revision + 1
    assert original.selected_option_id is None
    assert repo.history[("decisions", original.id, 1)].scope == original.scope
    assert repo.history[("decisions", original.id, 1)].selected_option_id is None
    assert repo.rows[("events", decisions.fingerprint([original.id, "resume-select"]))].scope == (
        original.scope
    )
    assert source.calls[-1][1]["session_id"] == "resumed-session"
    assert await decisions.operate_decision(original.id, request, current_user) == selected
    advance_mission_pin(binding, selected)
    assert await decisions.operate_decision(original.id, request, current_user) == selected
    assert len([key for key in repo.rows if key[0] == "events"]) == 2
    changed = request.model_copy(update={"option_id": "another"})
    with pytest.raises(HTTPException) as error:
        await decisions.operate_decision(original.id, changed, current_user)
    assert error.value.status_code == 409


async def test_mission_operation_rejects_stale_binding_revocation_and_unlinked_revisions(
    resumed_decision,
):
    _, _, source, _, original, current_user, binding = resumed_decision
    request = DecisionOperation(
        operation_id="resume-select",
        expected_revision=1,
        operation="select",
        option_id="transfer",
        mission_id="mission-1",
    )
    with pytest.raises(HTTPException) as old_session:
        await decisions.operate_decision(original.id, request, USER)
    assert old_session.value.status_code == 409
    binding.cancel_requested = True
    with pytest.raises(HTTPException) as cancelled:
        await decisions.operate_decision(original.id, request, current_user)
    assert cancelled.value.status_code == 409
    binding.cancel_requested = False
    source.revoked = True
    with pytest.raises(HTTPException):
        await decisions.operate_decision(original.id, request, current_user)
    source.revoked = False
    await decisions.operate_decision(original.id, request, current_user)
    with pytest.raises(HTTPException) as stale:
        await decisions.operate_decision(
            original.id, request.model_copy(update={"operation_id": "another-select"}), current_user
        )
    assert stale.value.status_code == 409


async def test_mission_historical_approval_requires_current_policy_and_reselection(
    resumed_decision,
):
    _, _, _, policy, original, current_user, binding = resumed_decision
    selected = await decisions.operate_decision(
        original.id,
        DecisionOperation(
            operation_id="resume-select",
            expected_revision=1,
            operation="select",
            option_id="transfer",
            mission_id="mission-1",
        ),
        current_user,
    )
    advance_mission_pin(binding, selected)
    policy.revision += 1
    with pytest.raises(HTTPException) as stale:
        await decisions.operate_decision(
            original.id,
            DecisionOperation(
                operation_id="approve-stale",
                expected_revision=2,
                operation="approve",
                mission_id="mission-1",
            ),
            current_user,
        )
    assert stale.value.status_code == 409
    fresh = await decisions.operate_decision(
        original.id,
        DecisionOperation(
            operation_id="reselect-new-policy",
            expected_revision=2,
            operation="select",
            option_id="transfer",
            mission_id="mission-1",
        ),
        current_user,
    )
    advance_mission_pin(binding, fresh)
    approved = await decisions.operate_decision(
        original.id,
        DecisionOperation(
            operation_id="approve-fresh",
            expected_revision=3,
            operation="approve",
            mission_id="mission-1",
        ),
        current_user,
    )
    assert approved.status == "approved" and approved.scope == original.scope
    assert approved.policy.policy_revision == policy.revision


def test_optional_mission_operation_field_preserves_legacy_digest():
    body = DecisionOperation(operation_id="old-operation", expected_revision=1, operation="cancel")
    assert body.model_dump(mode="json") == {
        "operation_id": "old-operation",
        "expected_revision": 1,
        "operation": "cancel",
        "option_id": None,
    }


@pytest.fixture
async def resumed_outcome(resumed_decision, monkeypatch):
    from app.modules.agents.harness_repository import harness_repository
    from app.modules.agents.mission_schema import ObjectRef, object_binding_key

    service, repo, source, policy, original, user, binding = resumed_decision
    selected = await decisions.operate_decision(
        original.id,
        DecisionOperation(
            operation_id="outcome-select", expected_revision=1, operation="select",
            option_id="transfer", mission_id=binding.mission_id,
        ),
        user,
    )
    advance_mission_pin(binding, selected)
    approved = await decisions.operate_decision(
        original.id,
        DecisionOperation(
            operation_id="outcome-approve", expected_revision=selected.revision,
            operation="approve", mission_id=binding.mission_id,
        ),
        user,
    )
    advance_mission_pin(binding, approved)
    investigation = repo.rows[("investigations", approved.investigation_id)]
    ref = ObjectRef(kind="investigation", id=investigation.id, revision=investigation.revision)
    binding.object_refs.append(ref)
    binding.object_bindings[object_binding_key(ref)] = investigation.scope
    admission = SimpleNamespace(held=False, assert_owned=AsyncMock())

    @asynccontextmanager
    async def admission_lock(thread_id, owner_name):
        assert thread_id == binding.thread_id and owner_name == user["username"]
        assert not admission.held
        admission.held = True
        try:
            yield admission.assert_owned
        finally:
            admission.held = False

    monkeypatch.setattr(harness_repository, "admission_lock", admission_lock)
    monkeypatch.setattr(engine, "utc_now", lambda: approved.outcome_window.end)
    observe = service.observe

    async def complete(*args):
        assert not admission.held, "Queries must outlive the short admission fence"
        return (await observe(*args)).model_copy(update={"completeness": 1})

    monkeypatch.setattr(service, "observe", complete)
    return service, repo, source, policy, approved, user, binding, admission


async def test_resumed_outcome_uses_current_credentials_and_preserves_historical_decision(
    resumed_outcome, monkeypatch,
):
    service, repo, source, _, approved, user, binding, admission = resumed_outcome
    save = repo.save

    async def fenced_save(kind, record, **kwargs):
        if kind == "outcomes":
            assert admission.held
        return await save(kind, record, **kwargs)

    monkeypatch.setattr(repo, "save", fenced_save)
    outcome = await service.evaluate_outcome(approved.id, user, mission_id=binding.mission_id)
    assert outcome.status == "complete" and outcome.actual is not None
    assert outcome.scope == Scope.from_user(user)
    assert outcome.decision_revision == approved.revision
    assert repo.history[("decisions", approved.id, approved.revision)].scope == approved.scope
    assert approved.scope != outcome.scope
    assert source.calls[-1][1]["session_id"] == user["session_id"]
    assert not admission.held and admission.assert_owned.await_count > 0
    assert await service.evaluate_outcome(
        approved.id, user, mission_id=binding.mission_id
    ) == outcome


async def test_outcome_refuses_a_stale_approved_mission_pin_before_observation(
    resumed_outcome, monkeypatch,
):
    service, repo, _, _, approved, user, binding, _ = resumed_outcome
    await decisions.operate_decision(
        approved.id,
        DecisionOperation(
            operation_id="newer-cancellation", expected_revision=approved.revision,
            operation="cancel",
        ),
        USER,
    )
    observe = AsyncMock(wraps=service.observe)
    monkeypatch.setattr(service, "observe", observe)
    with pytest.raises(HTTPException) as stale:
        await service.evaluate_outcome(approved.id, user, mission_id=binding.mission_id)
    assert stale.value.status_code == 409 and "Decision changed" in stale.value.detail
    observe.assert_not_awaited()
    assert not [kind for kind, _ in repo.rows if kind == "outcomes"]


async def test_resumed_outcome_observations_do_not_collide_with_original_security_scope(
    resumed_outcome, monkeypatch,
):
    service, repo, _, _, approved, user, binding, _ = resumed_outcome
    read, save = repo.get, repo.save

    def storage_scope(scope):
        return scope.model_dump(exclude={"session_id"})

    async def scoped_get(kind, record_id, scope, model, **kwargs):
        record = await read(kind, record_id, scope, model, **kwargs)
        return record if record and storage_scope(record.scope) == storage_scope(scope) else None

    async def scoped_save(kind, record, **kwargs):
        prior = repo.rows.get((kind, record.id))
        if prior and storage_scope(prior.scope) != storage_scope(record.scope):
            raise HTTPException(status_code=404, detail="Record unavailable")
        return await save(kind, record, **kwargs)

    monkeypatch.setattr(repo, "get", scoped_get)
    monkeypatch.setattr(repo, "save", scoped_save)
    await service.evaluate_outcome(approved.id, USER)
    original = next(row for (kind, _), row in repo.rows.items()
                    if kind == "observations" and row.window == approved.outcome_window)
    monitor = repo.rows[("monitors", original.monitor_id)]
    assert original.id == decisions.fingerprint([
        monitor.id, monitor.revision, original.window.model_dump(), original.evidence[0].digest,
    ])
    outcome = await service.evaluate_outcome(approved.id, user, mission_id=binding.mission_id)
    current = next(row for (kind, _), row in repo.rows.items()
                   if kind == "observations" and row.window == approved.outcome_window
                   and row.scope == Scope.from_user(user))
    assert outcome.status == "complete" and current.id != original.id
    assert current.monitor_id == original.monitor_id
    assert current.monitor_revision == original.monitor_revision
    assert current.window == original.window and current.value == original.value
    assert current.evidence[0].scope == Scope.from_user(user)
    assert repo.rows[("observations", original.id)] == original
    assert monitor.scope == approved.scope
    snapshot = len([kind for kind, _ in repo.rows if kind == "observations"])
    assert await service.evaluate_outcome(
        approved.id, user, mission_id=binding.mission_id
    ) == outcome
    assert len([kind for kind, _ in repo.rows if kind == "observations"]) == snapshot


@pytest.mark.parametrize("change", ["generation", "session", "cancel", "decision"])
async def test_outcome_rechecks_binding_and_decision_after_observation_before_persistence(
    resumed_outcome, monkeypatch, change,
):
    service, repo, _, _, approved, user, binding, admission = resumed_outcome
    observe = service.observe

    async def intervening_change(*args):
        observed = await observe(*args)
        assert not admission.held
        if change == "generation":
            binding.current_binding.generation += 1
        elif change == "session":
            binding.scope = binding.scope.model_copy(update={"session_id": "another-session"})
            binding.current_binding.scope = binding.scope
        elif change == "cancel":
            binding.cancel_requested = True
        else:
            await decisions.operate_decision(
                approved.id,
                DecisionOperation(
                    operation_id="cancel-during-query", expected_revision=approved.revision,
                    operation="cancel",
                ),
                USER,
            )
        return observed

    monkeypatch.setattr(service, "observe", intervening_change)
    with pytest.raises(HTTPException) as refused:
        await service.evaluate_outcome(approved.id, user, mission_id=binding.mission_id)
    assert refused.value.status_code == 409
    assert not [kind for kind, _ in repo.rows if kind == "outcomes"]


async def test_outcome_does_not_commit_after_admission_ownership_is_lost(
    resumed_outcome, monkeypatch,
):
    from app.modules.agents.harness_repository import AutoAdmissionUnavailable

    service, repo, _, _, approved, user, binding, admission = resumed_outcome
    read = repo.get

    async def lose_admission(kind, *args, **kwargs):
        record = await read(kind, *args, **kwargs)
        if kind == "outcomes":
            admission.assert_owned.side_effect = AutoAdmissionUnavailable("Admission lease lost")
        return record

    monkeypatch.setattr(repo, "get", lose_admission)
    with pytest.raises(AutoAdmissionUnavailable):
        await service.evaluate_outcome(approved.id, user, mission_id=binding.mission_id)
    assert not [kind for kind, _ in repo.rows if kind == "outcomes"]


async def test_policy_endpoint_authorizes_resumed_decision_through_exact_mission_pin(
    resumed_decision, monkeypatch,
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_user
    from app.modules.intelligence import engine_router

    service, _, source, policy, original, user, binding = resumed_decision
    monkeypatch.setattr(engine_router, "intelligence_service", service)
    monkeypatch.setattr(engine_router, "read_business_policy", AsyncMock(return_value=policy))
    app = FastAPI()
    app.include_router(engine_router.router, prefix="/api/v1/intelligence")
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/v1/intelligence/decisions/{original.id}/policy"
        assert (await client.get(url)).status_code == 404
        response = await client.get(url, params={"mission_id": binding.mission_id})
        assert response.status_code == 200
        assert response.json() == {
            "current": False, "policy_revision": policy.revision,
            "can_review": True, "can_edit": True,
        }
        pins = list(binding.object_refs)
        binding.object_refs.clear()
        assert (await client.get(url, params={"mission_id": binding.mission_id})).status_code == 404
        binding.object_refs = pins
        source.revoked = True
        assert (await client.get(url, params={"mission_id": binding.mission_id})).status_code == 404


@pytest.mark.asyncio
async def test_missing_and_cancelled_outcome_dimensions_stay_unavailable(program):
    service, _, _, _, body = program
    created = await decisions.create_decision(body, USER)
    selected = await decisions.operate_decision(
        created.id,
        DecisionOperation(
            operation_id="select-1", expected_revision=1, operation="select", option_id="transfer"
        ),
        USER,
    )
    approved = await decisions.operate_decision(
        created.id,
        DecisionOperation(
            operation_id="approve1", expected_revision=selected.revision, operation="approve"
        ),
        USER,
    )
    pending = await service.evaluate_outcome(created.id, USER)
    assert pending.status == "pending" and pending.actual is None
    assert pending.dimensions["attributed_business_impact"] is None
    await decisions.operate_decision(
        created.id,
        DecisionOperation(
            operation_id="cancel-1", expected_revision=approved.revision, operation="cancel"
        ),
        USER,
    )
    cancelled = await service.evaluate_outcome(created.id, USER)
    assert cancelled.status == "superseded" and cancelled.attribution == "unknown"


@pytest.mark.asyncio
async def test_policy_denial_and_incomplete_lineage_fail_closed(program):
    service, repo, _, policy, body = program
    policy.maximum_cost = 0
    record = await decisions.create_decision(body, USER)
    selected = await decisions.operate_decision(
        record.id,
        DecisionOperation(
            operation_id="select-1", expected_revision=1, operation="select", option_id="transfer"
        ),
        USER,
    )
    assert selected.status == "denied"
    with pytest.raises(HTTPException):
        await decisions.operate_decision(
            record.id,
            DecisionOperation(operation_id="approve1", expected_revision=2, operation="approve"),
            USER,
        )
    events = [key for key in repo.rows if key[0] == "events"]
    del repo.rows[events[0]]
    with pytest.raises(HTTPException):
        await service.lineage(record.id, USER)


@pytest.mark.asyncio
async def test_legacy_unit_economics_accepts_published_net_booked_revenue(program):
    _, repo, source, _, body = program
    monitor = repo.rows[("monitors", "monitor")]
    monitor.value_column = "net_booked_revenue"
    monitor.plan["metrics"][0] = monitor.value_column
    original = source._readable_version

    async def published_version(*args):
        view, row = await original(*args)
        row["definition"]["metrics"][0]["name"] = monitor.value_column
        return view, row

    source._readable_version = published_version
    result = await decisions.create_decision(body, USER)
    assert result.target_metric == "net_booked_revenue"
    assert result.currency == "IDR" and result.baseline == 60
    assert result.options[0].prediction == 100
    assert result.options[0].effects["revenue_delta"] == 40
    assert result.options[0].incremental_gross_profit == 32


@pytest.mark.asyncio
async def test_lineage_and_baseline_pin_news_and_monitor_revisions(program):
    service, repo, _, _, body = program
    investigation = repo.rows[("investigations", body.investigation_id)]
    original_news = repo.history[("news", investigation.news_id, investigation.news_revision)]
    monitor = repo.rows[("monitors", original_news.monitor_id)]
    repo.history[("monitors", monitor.id, monitor.revision)] = deepcopy(monitor)
    await repo.save(
        "monitors",
        monitor.model_copy(update={"value_column": "different_metric"}),
        expected_revision=1,
    )
    current_news = repo.rows[("news", original_news.id)]
    await repo.save(
        "news",
        current_news.model_copy(update={"after": 9999, "status": "dismissed"}),
        expected_revision=current_news.revision,
    )
    decision = await decisions.create_decision(body, USER)
    assert decision.baseline == original_news.after
    assert decision.target_metric == monitor.value_column
    lineage = await service.lineage(decision.id, USER)
    assert lineage["news"].revision == investigation.news_revision
    assert lineage["news"].after == original_news.after


@pytest.mark.asyncio
async def test_owner_can_withdraw_after_revocation_without_receiving_stored_data(program):
    service, repo, source, _, body = program
    created = await decisions.create_decision(body, USER)
    source.revoked = True
    request = DecisionOperation(
        operation_id="withdraw-after-revocation", expected_revision=1, operation="cancel"
    )
    with pytest.raises(HTTPException) as denied:
        await decisions.operate_decision(created.id, request, {**USER, "username": "other"})
    assert denied.value.status_code == 404
    receipt = await decisions.operate_decision(created.id, request, USER)
    assert receipt.model_dump() == {"id": created.id, "revision": 2, "status": "cancelled"}
    assert await decisions.operate_decision(created.id, request, USER) == receipt
    assert len([key for key in repo.rows if key[0] == "events"]) == 2
    with pytest.raises(HTTPException):
        await service.get("decisions", created.id, USER)


async def test_share_management_is_bound_to_owner_and_decision(program, monkeypatch):
    from app.modules.intelligence import engine_router

    service, _, _, _, body = program
    record = await decisions.create_decision(body, USER)
    monkeypatch.setattr(engine_router, "intelligence_service", service)
    shares = AsyncMock(return_value=[{"share_id": "share-current"}])
    delete = AsyncMock()
    monkeypatch.setattr(engine_router.share_repository, "for_object", shares)
    monkeypatch.setattr(engine_router.share_repository, "delete", delete)
    assert await engine_router.decision_shares(record.id, USER) == {
        "items": [{"share_id": "share-current"}]
    }
    shares.assert_awaited_with("decision", record.id, owner_name=USER["username"])
    with pytest.raises(HTTPException) as exc:
        await engine_router.revoke_decision_share(record.id, "share-another-decision", USER)
    assert exc.value.status_code == 404
    delete.assert_not_awaited()
    await engine_router.revoke_decision_share(record.id, "share-current", USER)
    delete.assert_awaited_once_with("share-current", owner_name=USER["username"])
    monkeypatch.setattr(service, "get", AsyncMock(return_value=record))
    with pytest.raises(HTTPException) as exc:
        await engine_router.decision_shares(record.id, {**USER, "username": "another-reviewer"})
    assert exc.value.status_code == 404
    assert delete.await_count == 1


async def test_outcome_read_rechecks_prediction_evidence_even_when_actuals_still_match(
    program, monkeypatch
):
    service, repo, source, _, body = program
    created = await decisions.create_decision(body, USER)
    selected = await decisions.operate_decision(
        created.id,
        DecisionOperation(
            operation_id="select-outcome",
            expected_revision=1,
            operation="select",
            option_id="transfer",
        ),
        USER,
    )
    approved = await decisions.operate_decision(
        selected.id,
        DecisionOperation(operation_id="approve-outcome", expected_revision=2, operation="approve"),
        USER,
    )
    monkeypatch.setattr(engine, "utc_now", lambda: END + timedelta(days=3))
    outcome = await service.evaluate_outcome(approved.id, USER)
    assert (await service.get("outcomes", outcome.id, USER)).id == outcome.id
    execute = source.execute_plan

    async def changed_baseline(view_id, version, plan, user):
        result = await execute(view_id, version, plan, user)
        start = next(item.value for item in plan.filters if item.operator == ">=")
        if start < "2026-09-21":
            result["rows"][0][1 if plan.dimensions else 0] += 1
        return result

    monkeypatch.setattr(source, "execute_plan", changed_baseline)
    with pytest.raises(HTTPException) as exc:
        await service.get("outcomes", outcome.id, USER)
    assert exc.value.status_code == 409


async def test_effectiveness_does_not_average_different_definitions_or_currencies(monkeypatch):

    from app.modules.intelligence import engine_router
    from tests.unit.test_intelligence_engine import REF

    def outcome(amount, currency="IDR", version=1, metric="revenue"):
        return SimpleNamespace(
            semantic=REF.model_copy(update={"version": version}),
            target_metric=metric,
            currency=currency,
            status="complete",
            dimensions={"forecast_absolute_error": amount, "forecast_relative_error": 0.2},
            attribution="observed_after",
        )

    rows = [
        outcome(100),
        outcome(200),
        outcome(10, "USD"),
        outcome(50, version=2),
        outcome(9, metric=None),
    ]
    monkeypatch.setattr(
        engine_router.intelligence_service,
        "page",
        AsyncMock(return_value={"items": rows, "next_after": None}),
    )
    report = await engine_router.decision_effectiveness(USER, "")
    assert "forecast_absolute_error" not in report["dimensions"]
    assert report["dimensions"]["forecast_relative_error"]["mean"] == pytest.approx(0.2)
    assert [
        group["dimensions"]["forecast_absolute_error"]["mean"] for group in report["metric_groups"]
    ] == [150, 10, 50]
    assert all(
        group["dimensions"]["attributed_business_impact"]["mean"] is None
        for group in report["metric_groups"]
    )


async def test_decision_context_retries_and_rechecks_referenced_evidence(program, monkeypatch):
    from app.modules.intelligence import context_graph

    service, repo, source, _, body = program
    monkeypatch.setattr(context_graph, "intelligence_service", service)
    record = await decisions.create_decision(body, USER)
    first = await context_graph.project_decision(record.id, USER)
    retry = await context_graph.project_decision(
        record.id, {**USER, "session_id": "renewed-session"}
    )
    assert first == retry
    assert first["nodes"] == 3 and first["edges"] == 2
    node = await service.get("nodes", first["root_id"], USER)
    assert node.reference_id == record.id and node.reference_revision == 1
    assert len([kind for kind, _ in repo.rows if kind == "nodes"]) == 3
    source.masked = True
    with pytest.raises(HTTPException) as exc:
        await service.get("nodes", first["root_id"], USER)
    assert exc.value.status_code == 409
