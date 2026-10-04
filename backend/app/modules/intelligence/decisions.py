"""Reviewable recommendations with approvals pinned to immutable inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException
from pydantic import Field, model_validator

from app.modules.access_control.business_policy import (
    evaluate_business_policy,
    read_business_policy,
)
from app.modules.intelligence.contracts import (
    Contract,
    Decision,
    DecisionEvent,
    DecisionOption,
    Investigation,
    NewsItem,
    Scope,
    Window,
    fingerprint,
    utc_now,
)
from app.modules.intelligence.engine import CycleBudget, intelligence_service
from app.modules.intelligence.scenarios import (
    ScenarioContext,
    ScenarioScalar,
    execute_scenario,
    normalize_scenario,
    scenario_compatibility,
    scenario_definition,
)
from app.modules.ml_engine.decision_lab import SimulationInput

if TYPE_CHECKING:
    from app.modules.agents.mission_schema import Mission


class OptionInput(Contract):
    id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    simulation: SimulationInput | None = None
    scenario_kind: str = Field(default="unit-economics", max_length=64)
    scenario_version: int = Field(default=1, ge=1)
    parameters: dict[str, ScenarioScalar] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def registered_inputs(self):
        if (self.simulation is None) == (self.parameters is None):
            raise ValueError("Provide simulation or scenario parameters")
        scenario_definition(self.scenario_kind, self.scenario_version)
        legacy = self.scenario_kind == "unit-economics" and self.scenario_version == 1
        if self.simulation is not None and not legacy:
            raise ValueError("Legacy simulation inputs require unit-economics version 1")
        if self.parameters is not None:
            normalized = normalize_scenario(
                self.scenario_kind,
                self.scenario_version,
                self.parameters,
            )
            if legacy:
                self.simulation = normalized
                self.parameters = None
            else:
                self.parameters = normalized.model_dump(mode="json")
        return self


class DecisionCreate(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    investigation_id: str = Field(min_length=1, max_length=128)
    outcome_window: Window
    options: list[OptionInput] = Field(min_length=1, max_length=30)
    learning_enabled: bool = True
    thread_id: str | None = Field(default=None, max_length=64)
    mission_id: str | None = Field(default=None, min_length=1, max_length=128)
    investigation_revision: int | None = Field(default=None, ge=1)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class DecisionOperation(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    expected_revision: int = Field(ge=1)
    operation: Literal["select", "approve", "deny", "cancel", "supersede"]
    option_id: str | None = Field(default=None, max_length=128)
    mission_id: str | None = Field(
        default=None, min_length=1, max_length=128, exclude_if=lambda value: value is None
    )


class DecisionReceipt(Contract):
    id: str
    revision: int
    status: Literal["cancelled", "superseded"]


def decision_request_digest(body: DecisionCreate) -> str:
    payload = body.model_dump(mode="json")
    for name in ("mission_id", "investigation_revision"):
        if payload.get(name) is None:
            payload.pop(name, None)
    for option in payload["options"]:
        if option["scenario_kind"] == "unit-economics" and option["scenario_version"] == 1:
            option.pop("parameters", None)
            option.pop("scenario_kind")
            option.pop("scenario_version")
        elif option.get("simulation") is None:
            option.pop("simulation", None)
    return fingerprint(payload)


async def _mission_operation_decision(
    decision_id: str, body: DecisionOperation, user: dict
) -> Decision:
    from app.modules.agents.mission import mission_service

    mission = await mission_service.get(body.mission_id, user, project=False)
    if mission.scope != Scope.from_user(user):
        raise HTTPException(status_code=409, detail="Resume the Mission under the current binding")
    if mission.cancel_requested or mission.status == "cancelled":
        raise HTTPException(status_code=409, detail="Cancelled Mission cannot continue a Decision")
    pin = next(
        (ref for ref in mission.object_refs if ref.kind == "decision" and ref.id == decision_id),
        None,
    )
    if pin is None or pin.revision not in {body.expected_revision, body.expected_revision + 1}:
        raise HTTPException(status_code=409, detail="Mission Decision revision changed; refresh")
    budget = CycleBudget()
    decision = await intelligence_service.get_for_mission(
        "decisions",
        decision_id,
        user,
        mission_id=body.mission_id,
        revision=pin.revision,
        budget=budget,
    )
    if pin.revision != body.expected_revision:
        if decision.last_operation_id != body.operation_id:
            raise HTTPException(
                status_code=409, detail="Mission Decision revision changed; refresh"
            )
        return decision
    current = await intelligence_service.repository.get(
        "decisions", decision_id, decision.scope, Decision
    )
    if current is not None and current.scope != decision.scope:
        raise HTTPException(status_code=409, detail="Decision ownership changed")
    if (
        current is not None
        and current.revision == body.expected_revision + 1
        and current.last_operation_id == body.operation_id
    ):
        await intelligence_service.authorize_record(current, user, budget)
        return current
    if current is None or current.revision != decision.revision:
        raise HTTPException(status_code=409, detail="Decision changed; review the new revision")
    return decision


async def close_owned_decision(
    decision_id: str, body: DecisionOperation, user: dict
) -> DecisionReceipt:
    service = intelligence_service
    decision = (
        await _mission_operation_decision(decision_id, body, user)
        if body.mission_id
        else await service.repository.get("decisions", decision_id, Scope.from_user(user), Decision)
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
    payload = decision.model_dump(mode="json", exclude={"created_at", "updated_at"})
    # Legacy event and approval proofs omitted the implicit built-in scenario.
    for option in payload["options"]:
        if option.get("scenario_kind") == "unit-economics" and option.get("scenario_version") == 1:
            option.pop("scenario_kind")
            option.pop("scenario_version")
    for evidence in payload["evidence"]:
        if evidence.get("evidence_health") is None:
            evidence.pop("evidence_health", None)
    return fingerprint(payload)


async def current_decision_policy(decision: Decision):
    option = next(
        (item for item in decision.options if item.id == decision.selected_option_id), None
    )
    if option is None:
        raise HTTPException(status_code=422, detail="Select an available option")
    return evaluate_business_policy(
        option,
        await read_business_policy(),
        {
            "decision_id": decision.id,
            "semantic": decision.semantic.model_dump(),
            "outcome_window": decision.outcome_window.model_dump(mode="json"),
            "evidence": [item.digest for item in decision.evidence],
        },
    )


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


async def _scenario_mission(
    investigation_id: str,
    user: dict,
    *,
    mission_id: str | None,
    investigation_revision: int | None,
    thread_id: str | None,
) -> tuple[Mission | None, int | None]:
    mission = None
    revision = investigation_revision
    if mission_id:
        from app.modules.agents.mission import mission_service

        mission = await mission_service.get(mission_id, user, project=False)
        if mission.cancel_requested or mission.status == "cancelled":
            raise HTTPException(
                status_code=409, detail="Cancelled Missions cannot create Decisions"
            )
        if thread_id and mission.thread_id != thread_id:
            raise HTTPException(status_code=422, detail="Decision thread differs from its Mission")
        pin = next(
            (
                ref
                for ref in mission.object_refs
                if ref.kind == "investigation" and ref.id == investigation_id
            ),
            None,
        )
        if pin is None or (
            investigation_revision is not None and pin.revision != investigation_revision
        ):
            raise HTTPException(
                status_code=404, detail="Mission Investigation revision unavailable"
            )
        revision = pin.revision
    return mission, revision


async def resolve_scenario_context(
    investigation_id: str,
    user: dict,
    *,
    investigation_revision: int | None = None,
    mission_id: str | None = None,
    thread_id: str | None = None,
    currency: str | None = None,
    outcome_window: Window | None = None,
    budget: CycleBudget | None = None,
    mission_context: tuple[Mission | None, int | None] | None = None,
) -> tuple[ScenarioContext, Investigation, NewsItem]:
    """Resolve exact canonical dependencies using the caller's current authorization."""
    service, budget = intelligence_service, budget or CycleBudget()
    mission, revision = mission_context or await _scenario_mission(
        investigation_id,
        user,
        mission_id=mission_id,
        investigation_revision=investigation_revision,
        thread_id=thread_id,
    )
    if mission:
        investigation = await service.get_for_mission(
            "investigations",
            investigation_id,
            user,
            mission_id=mission_id,
            revision=revision,
            budget=budget,
        )
        budget.mission_records[("news", investigation.news_id)] = investigation.news_revision
        budget.mission_record_bindings[("news", investigation.news_id)] = investigation.scope
    else:
        investigation = await service.get(
            "investigations",
            investigation_id,
            user,
            budget=budget,
            revision=revision,
        )
    news = await service.get(
        "news", investigation.news_id, user, budget=budget, revision=investigation.news_revision
    )
    if mission:
        budget.mission_records[("monitors", news.monitor_id)] = news.monitor_revision
        budget.mission_record_bindings[("monitors", news.monitor_id)] = news.scope
    monitor = await service.get(
        "monitors", news.monitor_id, user, budget=budget, revision=news.monitor_revision
    )
    thread_id = mission.thread_id if mission else thread_id
    if thread_id:
        from app.modules.agents.router import _require_agent_thread

        await _require_agent_thread(thread_id, monitor.agent_id, user["username"])
    if news.semantic != investigation.semantic or monitor.semantic != investigation.semantic:
        raise HTTPException(status_code=409, detail="Investigation semantic lineage changed")
    definition = await service.authorize_semantic(
        investigation.semantic, user, active=mission is None, budget=budget
    )
    metric = next(
        (
            item
            for item in definition["definition"].get("metrics", [])
            if item["name"] == monitor.value_column
        ),
        {},
    )
    if not metric:
        raise HTTPException(
            status_code=422,
            detail="Decision target metric is unavailable in the published definition",
        )
    context = ScenarioContext(
        purpose="decision" if outcome_window else "discovery",
        agent_id=monitor.agent_id,
        thread_id=thread_id,
        mission_id=mission_id,
        investigation_id=investigation.id,
        semantic=investigation.semantic,
        target_metric=monitor.value_column,
        baseline=news.after,
        currency=currency if currency is not None else metric.get("currency"),
        metric_currency=metric.get("currency"),
        metric_unit=metric.get("unit"),
        metric_additivity=metric.get("additivity"),
        outcome_window=outcome_window,
        evidence_ids=[item.id for item in investigation.evidence],
        evidence_types=list(dict.fromkeys(item.source_type for item in investigation.evidence)),
    )
    return context, investigation, news


async def discover_scenarios(
    investigation_id: str,
    investigation_revision: int,
    user: dict,
    *,
    mission_id: str | None = None,
) -> dict:
    from app.modules.intelligence.scenarios import scenario_registry

    context, _, _ = await resolve_scenario_context(
        investigation_id,
        user,
        investigation_revision=investigation_revision,
        mission_id=mission_id,
    )
    return scenario_registry.discover(context)


def _matches_request(prior: Decision, body: DecisionCreate) -> bool:
    if prior.request_digest == decision_request_digest(body):
        return True
    # Old requests hashed the implicit IDR field. This admits only an existing
    # operation's historical hash; new context and persistence never use that default.
    return "currency" not in body.model_fields_set and prior.request_digest == (
        decision_request_digest(body.model_copy(update={"currency": "IDR"}))
    )


async def create_decision(body: DecisionCreate, user: dict) -> Decision:
    service, budget = intelligence_service, CycleBudget()
    scope = Scope.from_user(user)
    record_id = fingerprint([scope.principal, scope.active_role, body.operation_id])
    mission_context = await _scenario_mission(
        body.investigation_id,
        user,
        mission_id=body.mission_id,
        investigation_revision=body.investigation_revision,
        thread_id=body.thread_id,
    )
    prior = await service.repository.get("decisions", record_id, scope, Decision)
    if prior:
        if not _matches_request(prior, body):
            raise HTTPException(status_code=409, detail="The operation identifier was already used")
        await service.authorize_record(prior, user, budget)
        return prior
    context, investigation, news = await resolve_scenario_context(
        body.investigation_id,
        user,
        investigation_revision=body.investigation_revision,
        mission_id=body.mission_id,
        thread_id=body.thread_id,
        currency=body.currency,
        outcome_window=body.outcome_window,
        budget=budget,
        mission_context=mission_context,
    )
    learning_enabled = body.learning_enabled
    if context.thread_id:
        from app.modules.assistant.repository import assistant_repository

        if not await assistant_repository.learning_enabled(
            context.thread_id, user_name=user["username"]
        ):
            learning_enabled = False
    if body.outcome_window.start < utc_now():
        raise HTTPException(status_code=422, detail="Choose a future outcome window")
    if body.outcome_window.end - body.outcome_window.start != news.window.end - news.window.start:
        raise HTTPException(status_code=422, detail="Prediction and baseline periods must match")
    if len({option.id for option in body.options}) != len(body.options):
        raise HTTPException(status_code=422, detail="Option identifiers must be unique")
    currencies = {
        resolved.currency
        for option in body.options
        if (
            resolved := scenario_compatibility(
                option.scenario_kind, option.scenario_version, context
            ).resolved
        ).currency
        is not None
    }
    if len(currencies) > 1:
        raise HTTPException(
            status_code=422, detail="Scenario currencies must agree; conversion is unavailable"
        )
    options = []
    for item in body.options:
        budget.consume("items")
        estimate = await execute_scenario(
            item.scenario_kind,
            item.scenario_version,
            item.simulation.model_dump(mode="json") if item.simulation else item.parameters,
            context,
            user,
            operation_id=f"{body.operation_id}:{item.id}",
        )
        options.append(
            DecisionOption(
                id=item.id,
                scenario_kind=item.scenario_kind,
                scenario_version=item.scenario_version,
                description=item.description,
                action_type=estimate.action_type,
                assumptions=estimate.assumptions,
                prediction=estimate.prediction,
                lower_bound=estimate.lower_bound,
                upper_bound=estimate.upper_bound,
                cost=estimate.cost,
                effects=estimate.effects,
                incremental_gross_profit=estimate.incremental_gross_profit,
                risk=estimate.risk,
                feasible=estimate.feasible,
                method=estimate.method,
                run_id=estimate.run_id,
                evidence_ids=estimate.evidence_ids,
            )
        )
    decision = Decision(
        id=record_id,
        scope=scope,
        title=body.title,
        agent_id=context.agent_id,
        learning_enabled=learning_enabled,
        thread_id=context.thread_id,
        investigation_id=investigation.id,
        investigation_revision=(
            investigation.revision if body.mission_id else body.investigation_revision
        ),
        mission_id=body.mission_id,
        semantic=investigation.semantic,
        target_metric=context.target_metric,
        baseline=news.after,
        currency=next(iter(currencies), None),
        outcome_window=body.outcome_window,
        options=options,
        evidence=investigation.evidence,
        last_operation_id=body.operation_id,
        request_digest=decision_request_digest(body),
        last_operation_digest=decision_request_digest(body),
    )
    return await _write_decision(decision, user, expected_revision=0, event="created")


async def operate_decision(
    decision_id: str, body: DecisionOperation, user: dict
) -> Decision | DecisionReceipt:
    if body.operation in {"cancel", "supersede"}:
        return await close_owned_decision(decision_id, body, user)
    service = intelligence_service
    decision = (
        await _mission_operation_decision(decision_id, body, user)
        if body.mission_id
        else await service.get("decisions", decision_id, user)
    )
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
    await service.authorize_semantic(decision.semantic, user, active=body.mission_id is None)
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
