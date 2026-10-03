"""Decision review binds current policy, exact inputs, evidence and durable events."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.access_control.business_policy import BusinessPolicy
from app.modules.intelligence import decisions, engine
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

    monkeypatch.setattr(decisions, "run_simulation", numerical)
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
    from types import SimpleNamespace

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
