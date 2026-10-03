"""Reviewable recommendations with approvals pinned to immutable inputs."""

from __future__ import annotations

import math
from typing import Literal

from fastapi import HTTPException
from pydantic import Field

from app.modules.access_control.business_policy import (
    evaluate_business_policy,
    read_business_policy,
)
from app.modules.intelligence.contracts import (
    Contract,
    Decision,
    DecisionEvent,
    DecisionOption,
    Scope,
    Window,
    fingerprint,
    utc_now,
)
from app.modules.intelligence.engine import CycleBudget, intelligence_service
from app.modules.ml_engine.decision_lab import SimulationInput, run_simulation


class OptionInput(Contract):
    id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    simulation: SimulationInput


class DecisionCreate(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    investigation_id: str = Field(min_length=1, max_length=128)
    outcome_window: Window
    options: list[OptionInput] = Field(min_length=1, max_length=30)
    learning_enabled: bool = True
    thread_id: str | None = Field(default=None, max_length=64)
    currency: str = Field(default="IDR", min_length=3, max_length=3)


class DecisionOperation(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    expected_revision: int = Field(ge=1)
    operation: Literal["select", "approve", "deny", "cancel", "supersede"]
    option_id: str | None = Field(default=None, max_length=128)


class DecisionReceipt(Contract):
    id: str
    revision: int
    status: Literal["cancelled", "superseded"]


async def close_owned_decision(
    decision_id: str, body: DecisionOperation, user: dict
) -> DecisionReceipt:
    service = intelligence_service
    decision = await service.repository.get(
        "decisions", decision_id, Scope.from_user(user), Decision
    )
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision unavailable")
    proof = await service.repository.get(
        "events",
        fingerprint([decision.id, decision.last_operation_id]),
        decision.scope,
        DecisionEvent,
    )
    if (
        proof is None
        or proof.decision_revision != decision.revision
        or proof.context_digest != decision_digest(decision)
    ):
        raise HTTPException(
            status_code=409, detail="Decision lineage is incomplete; retry the operation"
        )
    digest = fingerprint(body.model_dump(mode="json"))
    if decision.last_operation_id == body.operation_id:
        if decision.last_operation_digest != digest:
            raise HTTPException(status_code=409, detail="Operation inputs changed")
    else:
        if decision.revision != body.expected_revision:
            raise HTTPException(status_code=409, detail="Decision changed; review the new revision")
        if decision.status in {"cancelled", "superseded", "evaluated"}:
            raise HTTPException(status_code=409, detail="This decision is closed")
        decision.status = "cancelled" if body.operation == "cancel" else "superseded"
        decision.last_operation_id = body.operation_id
        decision.last_operation_digest = digest
        decision = await _write_decision(
            decision, user, expected_revision=body.expected_revision, event=decision.status
        )
    # Withdrawing one's recommendation remains possible after data revocation.
    # Return only a receipt; historical results still require fresh authorization.
    return DecisionReceipt(id=decision.id, revision=decision.revision, status=decision.status)


def decision_digest(decision: Decision) -> str:
    return fingerprint(decision.model_dump(mode="json", exclude={"created_at", "updated_at"}))


async def _write_decision(decision, user, *, expected_revision, event):
    service = intelligence_service
    decision.revision = expected_revision + 1
    proof = DecisionEvent(
        id=fingerprint([decision.id, decision.last_operation_id]),
        scope=decision.scope,
        decision_id=decision.id,
        decision_revision=decision.revision,
        event=event,
        actor=user["username"],
        context_digest=decision_digest(decision),
        references=[decision.investigation_id, decision.last_operation_id],
    )
    # The event is the durable intent. A retry can finish the projection after
    # a crash; an unapplied event cannot approve another decision revision.
    await service.repository.save("events", proof)
    saved = await service.repository.save(
        "decisions", decision, expected_revision=expected_revision
    )
    await service._audit(event.upper(), saved, user)
    return saved


async def create_decision(body: DecisionCreate, user: dict) -> Decision:
    service, budget = intelligence_service, CycleBudget()
    scope = Scope.from_user(user)
    record_id = fingerprint([scope.principal, scope.active_role, body.operation_id])
    prior = await service.repository.get("decisions", record_id, scope, Decision)
    if prior:
        if prior.request_digest != fingerprint(body.model_dump(mode="json")):
            raise HTTPException(status_code=409, detail="The operation identifier was already used")
        await service.authorize_record(prior, user, budget)
        return prior
    investigation = await service.get("investigations", body.investigation_id, user, budget=budget)
    news = await service.get(
        "news", investigation.news_id, user, budget=budget, revision=investigation.news_revision
    )
    monitor = await service.get(
        "monitors", news.monitor_id, user, budget=budget, revision=news.monitor_revision
    )
    learning_enabled = body.learning_enabled
    if body.thread_id:
        from app.modules.agents.router import _require_agent_thread
        from app.modules.assistant.repository import assistant_repository

        await _require_agent_thread(body.thread_id, monitor.agent_id, user["username"])
        if not await assistant_repository.learning_enabled(
            body.thread_id, user_name=user["username"]
        ):
            learning_enabled = False
    definition = await service.authorize_semantic(investigation.semantic, user, active=True)
    metric = next(
        (
            item
            for item in definition["definition"].get("metrics", [])
            if item["name"] == monitor.value_column
        ),
        {},
    )
    if metric.get("currency") != body.currency:
        raise HTTPException(
            status_code=422,
            detail="Decision unit economics require a metric with matching published currency",
        )
    if body.outcome_window.start < utc_now():
        raise HTTPException(status_code=422, detail="Choose a future outcome window")
    if body.outcome_window.end - body.outcome_window.start != news.window.end - news.window.start:
        raise HTTPException(status_code=422, detail="Prediction and baseline periods must match")
    if len({option.id for option in body.options}) != len(body.options):
        raise HTTPException(status_code=422, detail="Option identifiers must be unique")
    options = []
    for item in body.options:
        budget.consume("items")
        if not math.isclose(
            item.simulation.baseline_units * item.simulation.price,
            news.after,
            rel_tol=1e-8,
            abs_tol=1e-8,
        ):
            raise HTTPException(
                status_code=422, detail="Unit economics must reconcile to observed revenue"
            )
        estimate = await run_simulation(
            item.simulation, user, operation_id=f"{body.operation_id}:{item.id}"
        )
        options.append(
            DecisionOption(
                id=item.id,
                description=item.description,
                action_type=item.simulation.action_type,
                assumptions=item.simulation.model_dump(),
                prediction=estimate["prediction"],
                lower_bound=estimate["lower_bound"],
                upper_bound=estimate["upper_bound"],
                cost=estimate["cost"],
                incremental_gross_profit=estimate["incremental_gross_profit"],
                risk="high" if estimate["net_benefit"] < 0 else "medium",
                feasible=estimate["feasible"],
                method=estimate["method"],
                run_id=estimate["run_id"],
                evidence_ids=[item.id for item in investigation.evidence],
            )
        )
    decision = Decision(
        id=record_id,
        scope=scope,
        title=body.title,
        agent_id=monitor.agent_id,
        learning_enabled=learning_enabled,
        thread_id=body.thread_id,
        investigation_id=investigation.id,
        semantic=investigation.semantic,
        target_metric=monitor.value_column,
        baseline=news.after,
        currency=body.currency,
        outcome_window=body.outcome_window,
        options=options,
        evidence=investigation.evidence,
        last_operation_id=body.operation_id,
        request_digest=fingerprint(body.model_dump(mode="json")),
        last_operation_digest=fingerprint(body.model_dump(mode="json")),
    )
    return await _write_decision(decision, user, expected_revision=0, event="created")


async def operate_decision(
    decision_id: str, body: DecisionOperation, user: dict
) -> Decision | DecisionReceipt:
    if body.operation in {"cancel", "supersede"}:
        return await close_owned_decision(decision_id, body, user)
    service = intelligence_service
    decision = await service.get("decisions", decision_id, user)
    if body.operation not in {"approve", "deny"} and decision.scope.principal != user["username"]:
        raise HTTPException(
            status_code=403, detail="Only the decision owner can change its selected inputs"
        )
    if decision.last_operation_id == body.operation_id:
        if decision.last_operation_digest != fingerprint(body.model_dump(mode="json")):
            raise HTTPException(status_code=409, detail="Operation inputs changed")
        return decision
    if decision.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="Decision changed; review the new revision")
    if decision.status in {"cancelled", "superseded", "evaluated"}:
        raise HTTPException(status_code=409, detail="This decision is closed")
    await service.authorize_semantic(decision.semantic, user, active=True)
    selected_id = body.option_id if body.operation == "select" else decision.selected_option_id
    option = next((item for item in decision.options if item.id == selected_id), None)
    if option is None:
        raise HTTPException(status_code=422, detail="Select an available option")
    policy = await read_business_policy()
    evaluated = evaluate_business_policy(
        option,
        policy,
        {
            "decision_id": decision.id,
            "semantic": decision.semantic.model_dump(),
            "outcome_window": decision.outcome_window.model_dump(mode="json"),
            "evidence": [item.digest for item in decision.evidence],
        },
    )
    if body.operation == "select":
        decision.selected_option_id = option.id
        decision.policy = evaluated
        decision.status = {
            "ALLOW": "selected",
            "DENY": "denied",
            "REQUIRE_APPROVAL": "awaiting_approval",
        }[evaluated.decision]
        event = "selected" if evaluated.decision != "DENY" else "denied"
    else:
        if user["active_role"] not in policy.reviewer_roles:
            raise HTTPException(
                status_code=403, detail="This role cannot review business decisions"
            )
        if decision.status != "awaiting_approval" or decision.policy != evaluated:
            raise HTTPException(
                status_code=409, detail="Approval inputs changed; select the option again"
            )
        if evaluated.decision != "REQUIRE_APPROVAL":
            raise HTTPException(
                status_code=409, detail="The current policy does not allow this approval"
            )
        decision.status = "approved" if body.operation == "approve" else "denied"
        event = decision.status
    decision.last_operation_id = body.operation_id
    decision.last_operation_digest = fingerprint(body.model_dump(mode="json"))
    return await _write_decision(
        decision, user, expected_revision=body.expected_revision, event=event
    )
