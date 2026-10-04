"""Typed Intelligence operations; all identities and evidence are server-derived."""

from typing import Annotated, Generic, Literal, TypeVar

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.core.deps import get_current_user
from app.modules.access_control.business_policy import (
    BusinessPolicy,
    read_business_policy,
    save_business_policy,
)
from app.modules.agents.sharing import ShareCreate, share_repository
from app.modules.intelligence.context_graph import (
    ConceptCreate,
    EdgeCreate,
    create_concept,
    create_edge,
    project_canonical_context,
    project_decision,
    project_semantic_view,
    resolve_metric,
    traverse_context,
)
from app.modules.intelligence.context_sources import ContextSourceRef
from app.modules.intelligence.contracts import (
    Contract,
    Monitor,
    MonitorConfiguration,
    Record,
    Scope,
    SemanticRef,
    Window,
    fingerprint,
)
from app.modules.intelligence.decision_lab import (
    AnalysisRequest,
    OptimizationRequest,
    analyze,
    optimize_options,
)
from app.modules.intelligence.decisions import (
    DecisionCreate,
    DecisionOperation,
    create_decision,
    operate_decision,
)
from app.modules.intelligence.engine import MODELS, ChatInvestigationRequest, intelligence_service
from app.modules.intelligence.responses import IntelligenceResponse, IntelligenceRoute
from app.modules.intelligence.scenarios import ScenarioRequest, run_scenario, scenario_definitions
from app.modules.ml_engine.decision_lab import SimulationInput, run_simulation

router = APIRouter(default_response_class=IntelligenceResponse, route_class=IntelligenceRoute)
CurrentUser = Annotated[dict, Depends(get_current_user)]
T = TypeVar("T", bound=Record)


class Page(Contract, Generic[T]):
    items: list[T]
    next_after: str | None


class MonitorCreate(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    configuration: MonitorConfiguration


class DecisionShare(ShareCreate):
    object_type: Literal["decision"] = "decision"


@router.post("/decisions/{decision_id}/shares", status_code=201)
async def share_decision(decision_id: str, body: DecisionShare, user: CurrentUser):
    from fastapi import HTTPException

    decision = await intelligence_service.get("decisions", decision_id, user)
    if decision.scope.principal != user["username"] or body.object_id != decision.id:
        raise HTTPException(status_code=404, detail="Decision unavailable")
    share = await share_repository.create(owner_name=user["username"], body=body)
    await intelligence_service._audit("SHARE", decision, user)
    return share


@router.get("/decisions/{decision_id}/shares")
async def decision_shares(decision_id: str, user: CurrentUser):
    from fastapi import HTTPException

    decision = await intelligence_service.get("decisions", decision_id, user)
    if decision.scope.principal != user["username"]:
        raise HTTPException(status_code=404, detail="Decision unavailable")
    return {
        "items": await share_repository.for_object(
            "decision", decision_id, owner_name=user["username"]
        )
    }


@router.delete("/decisions/{decision_id}/shares/{share_id}", status_code=204)
async def revoke_decision_share(decision_id: str, share_id: str, user: CurrentUser):
    from fastapi import HTTPException

    shares = await decision_shares(decision_id, user)
    if not any(row["share_id"] == share_id for row in shares["items"]):
        raise HTTPException(status_code=404, detail="Share unavailable")
    await share_repository.delete(share_id, owner_name=user["username"])
    decision = await intelligence_service.get("decisions", decision_id, user)
    await intelligence_service._audit("REVOKE_SHARE", decision, user)


@router.get("/decisions/shared")
async def shared_decisions(
    user: CurrentUser,
    after: Annotated[str, Query(max_length=128)] = "",
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
):
    from fastapi import HTTPException

    from app.modules.intelligence.cursors import read_cursor, write_cursor
    from app.modules.intelligence.engine import CycleBudget

    scope, budget, visible, seen = Scope.from_user(user), CycleBudget(), [], set()
    position = await read_cursor(after, "shared_decisions", scope)
    grants = await share_repository.active_grants(user, object_type="decision", after=position)
    more = len(grants) > 100
    for index, share in enumerate(grants[:100]):
        if share["object_id"] in seen:
            continue
        try:
            budget.consume("items")
            decision = await intelligence_service.get(
                "decisions", share["object_id"], user, budget=budget
            )
        except HTTPException as exc:
            if exc.status_code == 429:
                if index == 0:
                    raise
                more = True
                break
            if exc.status_code not in {403, 404, 409}:
                raise
        else:
            visible.append(decision)
        seen.add(share["object_id"])
        position = share["object_id"]
        if len(visible) >= limit:
            more = any(row["object_id"] > position for row in grants[index + 1 :])
            break
    return {
        "items": visible,
        "next_after": await write_cursor(position, "shared_decisions", scope) if more else None,
    }


class MonitorUpdate(Contract):
    expected_revision: int = Field(ge=1)
    configuration: MonitorConfiguration


class MetricResolution(Contract):
    semantic: SemanticRef
    term: str = Field(min_length=1, max_length=256)
    exact: bool = False
    include_context: bool = False


class SimulationRequest(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    parameters: SimulationInput


@router.post("/decision-lab/analyses")
async def numerical_analysis(body: AnalysisRequest, user: CurrentUser):
    return await analyze(body, user)


@router.post("/decision-lab/simulations")
async def numerical_simulation(body: SimulationRequest, user: CurrentUser):
    return await run_simulation(body.parameters, user, operation_id=body.operation_id)


@router.get("/scenarios")
async def registered_scenarios(user: CurrentUser):
    Scope.from_user(user)
    return {"items": scenario_definitions()}


@router.post("/decision-lab/scenarios")
async def registered_simulation(body: ScenarioRequest, user: CurrentUser):
    return await run_scenario(body, user)


@router.post("/investigations/from-chat")
async def chat_investigation(body: ChatInvestigationRequest, user: CurrentUser):
    return await intelligence_service.initiate_investigation(body, user)


@router.post("/decision-lab/optimize")
async def numerical_optimization(body: OptimizationRequest, user: CurrentUser):
    return await optimize_options(body, user)


@router.post("/monitors", response_model=Monitor, status_code=201)
async def create_monitor(body: MonitorCreate, user: CurrentUser):
    from app.modules.agents.router import _require_agent

    await _require_agent(body.configuration.agent_id, user)
    from app.modules.intelligence.schedules import configure_schedule, require_execution_binding

    if body.configuration.enabled:
        await require_execution_binding(user)
    scope = Scope.from_user(user)
    monitor = Monitor(
        **body.configuration.model_dump(),
        scope=scope,
        id=fingerprint([scope.model_dump(exclude={"session_id"}), body.operation_id]),
    )
    saved = await intelligence_service.register_monitor(monitor, user)
    await configure_schedule(
        saved,
        user,
        handler="intelligence.monitor",
        enabled=saved.enabled,
        cadence=saved.cadence_minutes,
    )
    return saved


@router.put("/monitors/{monitor_id}", response_model=Monitor)
async def update_monitor(monitor_id: str, body: MonitorUpdate, user: CurrentUser):
    from app.modules.agents.router import _require_agent

    await _require_agent(body.configuration.agent_id, user)
    from app.modules.intelligence.schedules import configure_schedule, require_execution_binding

    if body.configuration.enabled:
        await require_execution_binding(user)
    original = await intelligence_service.get("monitors", monitor_id, user)
    monitor = Monitor(**body.configuration.model_dump(), id=monitor_id, scope=original.scope)
    saved = await intelligence_service.register_monitor(
        monitor, user, expected_revision=body.expected_revision
    )
    await configure_schedule(
        saved,
        user,
        handler="intelligence.monitor",
        enabled=saved.enabled,
        cadence=saved.cadence_minutes,
    )
    return saved


@router.post("/monitors/{monitor_id}/run")
async def run_monitor(monitor_id: str, body: Window, user: CurrentUser):
    return await intelligence_service.run_monitor(monitor_id, body, user)


@router.post("/news/{news_id}/investigate")
async def investigate_news(news_id: str, user: CurrentUser):
    return await intelligence_service.investigate(news_id, user)


class NewsOperation(Contract):
    operation_id: str = Field(min_length=8, max_length=64)
    expected_revision: int = Field(ge=1)
    operation: Literal["resolve", "dismiss", "reopen"]
    note: str = Field(min_length=1, max_length=2000)


@router.post("/news/{news_id}/operations")
async def news_operation(news_id: str, body: NewsOperation, user: CurrentUser):
    from fastapi import HTTPException

    news = await intelligence_service.get("news", news_id, user)
    digest = fingerprint(body.model_dump(mode="json"))
    if news.status_operation_id == body.operation_id:
        if news.status_request_digest != digest:
            raise HTTPException(status_code=409, detail="Operation inputs changed")
        return news
    updated = news.model_copy(
        update={
            "status": {"resolve": "resolved", "dismiss": "dismissed", "reopen": "open"}[
                body.operation
            ],
            "status_note": body.note,
            "status_operation_id": body.operation_id,
            "status_request_digest": digest,
        }
    )
    saved = await intelligence_service.repository.save(
        "news", updated, expected_revision=body.expected_revision
    )
    await intelligence_service._audit(body.operation.upper(), saved, user)
    return saved


@router.post("/decisions", status_code=201)
async def propose_decision(body: DecisionCreate, user: CurrentUser):
    return await create_decision(body, user)


@router.post("/decisions/{decision_id}/operations")
async def decision_operation(decision_id: str, body: DecisionOperation, user: CurrentUser):
    return await operate_decision(decision_id, body, user)


@router.get("/decisions/{decision_id}/lineage")
async def decision_lineage(
    decision_id: str, user: CurrentUser,
    mission_id: str | None = Query(default=None, max_length=128),
):
    return await intelligence_service.lineage(decision_id, user, mission_id=mission_id)


@router.post("/decisions/{decision_id}/context")
async def decision_context(decision_id: str, user: CurrentUser):
    return await project_decision(decision_id, user)


@router.post("/decisions/{decision_id}/evaluate-outcome")
async def evaluate_outcome(
    decision_id: str, user: CurrentUser,
    mission_id: str | None = Query(default=None, max_length=128),
):
    return await intelligence_service.evaluate_outcome(decision_id, user, mission_id=mission_id)


@router.get("/business-policy", response_model=BusinessPolicy)
async def get_business_policy(user: CurrentUser):
    Scope.from_user(user)
    return await read_business_policy()


@router.get("/effectiveness")
async def decision_effectiveness(user: CurrentUser, after: str = Query(default="", max_length=128)):
    page = await intelligence_service.page("outcomes", user, after=after)
    comparable = (
        "forecast_relative_error",
        "interval_covered",
        "policy_compliance",
        "time_to_insight_seconds",
    )
    dimensional = (
        "forecast_absolute_error",
        "expected_change",
        "observed_change",
        "attributed_business_impact",
    )

    def aggregate(outcomes, keys):
        import math

        result = {}
        for key in keys:
            values = [
                outcome.dimensions[key]
                for outcome in outcomes
                if isinstance(outcome.dimensions.get(key), (int, float, bool))
                and math.isfinite(outcome.dimensions[key])
                and outcome.status == "complete"
            ]
            result[key] = {
                "mean": sum(values) / len(values) if values else None,
                "available_outcomes": len(values),
            }
        return result

    groups = {}
    for outcome in page["items"]:
        if not outcome.target_metric or not outcome.currency:
            continue
        key = (
            outcome.semantic.view_id,
            outcome.semantic.version,
            outcome.semantic.fingerprint,
            outcome.target_metric,
            outcome.currency,
        )
        groups.setdefault(key, []).append(outcome)
    return {
        "dimensions": aggregate(page["items"], comparable),
        "metric_groups": [
            {
                "semantic": items[0].semantic,
                "metric": items[0].target_metric,
                "currency": items[0].currency,
                "dimensions": aggregate(items, dimensional),
            }
            for items in groups.values()
        ],
        "outcomes": page["items"],
        "next_after": page["next_after"],
        "scope": "authorized_page",
        "attribution_quality": [outcome.attribution for outcome in page["items"]],
    }


@router.get("/decisions/{decision_id}/policy")
async def decision_policy_status(
    decision_id: str, user: CurrentUser,
    mission_id: Annotated[str | None, Query(max_length=128)] = None,
):
    decision = (
        await intelligence_service.get_for_mission(
            "decisions", decision_id, user, mission_id=mission_id
        ) if mission_id else await intelligence_service.get("decisions", decision_id, user)
    )
    policy = await read_business_policy()
    return {
        "current": bool(decision.policy and decision.policy.policy_revision == policy.revision),
        "policy_revision": policy.revision,
        "can_review": user["active_role"] in policy.reviewer_roles,
        "can_edit": decision.scope.principal == user["username"],
    }


class OutcomeSchedule(Contract):
    enabled: bool = False
    cadence_minutes: int = Field(default=15, ge=15, le=1440)


@router.put("/decisions/{decision_id}/outcome-schedule")
async def schedule_outcome(decision_id: str, body: OutcomeSchedule, user: CurrentUser):
    from fastapi import HTTPException

    from app.modules.intelligence.schedules import configure_schedule

    decision = await intelligence_service.get("decisions", decision_id, user)
    if decision.scope.principal != user["username"]:
        raise HTTPException(status_code=403, detail="Only the owner can schedule this outcome")
    return await configure_schedule(
        decision,
        user,
        handler="intelligence.outcome",
        enabled=body.enabled,
        cadence=body.cadence_minutes,
    )


@router.put("/business-policy", response_model=BusinessPolicy)
async def configure_business_policy(body: BusinessPolicy, user: CurrentUser):
    Scope.from_user(user)
    return await save_business_policy(body, user)


@router.post("/context/concepts", status_code=201)
async def add_concept(body: ConceptCreate, user: CurrentUser):
    return await create_concept(body, user)


@router.get("/context/search", response_model=Page[MODELS["nodes"]])
async def search_context(
    user: CurrentUser,
    term: str = Query(min_length=1, max_length=128),
    after: str = Query(default="", max_length=128),
):
    return await intelligence_service.page("nodes", user, after=after, search=term.strip())


@router.post("/context/edges", status_code=201)
async def add_edge(body: EdgeCreate, user: CurrentUser):
    return await create_edge(body, user)


@router.post("/context/project-semantic-view")
async def project_view(body: SemanticRef, user: CurrentUser):
    return await project_semantic_view(body, user)


@router.post("/context/project-canonical")
async def project_canonical(body: ContextSourceRef, user: CurrentUser):
    return await project_canonical_context(body, user)


@router.post("/context/resolve-metric")
async def canonical_metric(body: MetricResolution, user: CurrentUser):
    return await resolve_metric(
        body.semantic, body.term, user, exact=body.exact,
        include_context=body.include_context,
    )


@router.get("/context/{node_id}/graph")
async def inspect_graph(
    node_id: str,
    user: CurrentUser,
    depth: int = Query(default=2, ge=0, le=4),
    limit: int = Query(default=50, ge=1, le=100),
):
    return await traverse_context(node_id, user, depth=depth, limit=limit)


def _register_read_routes(kind, model):
    async def listing(
        user: CurrentUser,
        after: str = Query(default="", max_length=128),
        limit: int = Query(default=20, ge=1, le=20),
    ):
        return await intelligence_service.page(kind, user, after=after, limit=limit)

    async def detail(record_id: str, user: CurrentUser):
        return await intelligence_service.get(kind, record_id, user)

    router.add_api_route(
        f"/{kind}",
        listing,
        methods=["GET"],
        response_model=Page[model],
        name=f"list_intelligence_{kind}",
    )
    router.add_api_route(
        f"/{kind}/{{record_id}}",
        detail,
        methods=["GET"],
        response_model=model,
        name=f"get_intelligence_{kind}",
    )


for _kind, _model in MODELS.items():
    _register_read_routes(_kind, _model)
