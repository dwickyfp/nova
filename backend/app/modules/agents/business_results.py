"""Studio hooks compose governed results through Mission and Intelligence owners."""

from __future__ import annotations

from fastapi import HTTPException

from app.core.config import settings
from app.modules.agents.mission import mission_service
from app.modules.agents.mission_schema import ObjectRef, SemanticAnchor
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.intelligence.contracts import SemanticRef
from app.modules.intelligence.engine import intelligence_service
from app.modules.intelligence.evidence import semantic_population_fingerprint


def planner_target(plan, context) -> SemanticAnchor | None:
    """Untrusted target hints select work; successful execution establishes anchors."""
    primary = plan.primary_plan
    if not primary or not primary.get("metrics"):
        return None
    wanted = plan.primary_view
    matches = [row for row in context.authorized_semantic_models or [] if wanted in {
        str(row.get("id")), str(row.get("semantic_model_id")), row.get("name"),
    }]
    if len(matches) != 1:
        return None
    row = matches[0]
    if not row.get("version") or not row.get("fingerprint"):
        return None
    try:
        population = semantic_population_fingerprint(SemanticPlan.from_dict(primary))
    except (ValueError, TypeError, KeyError):
        return None
    return SemanticAnchor(semantic=SemanticRef(
        view_id=str(row.get("id") or row.get("semantic_model_id")),
        version=row["version"], fingerprint=row["fingerprint"],
    ), metrics=list(primary["metrics"]), filter_population_fingerprint=population)


async def execution_clock(invocation, context):
    if context.mission_id:
        mission = await mission_service.get(context.mission_id, context.user, project=False)
        prior = next((item for item in mission.execution_contexts
                      if (item.run_id, item.tool_call_id)
                      == (context.run_id, invocation.tool_call_id)), None)
        if prior:
            return prior.execution_now
    return context.execution_now


async def pin_time(invocation, payload, context):
    if not context.mission_id or not payload.get("semantic_version"):
        return
    fixed = payload["execution_time"]
    await mission_service.record_execution_context(
        context.mission_id, context.run_id, invocation.tool_call_id, {
            "execution_now": fixed["now"], "timezone": fixed["timezone"],
            "semantic": {
                "view_id": payload["semantic_view_id"], "version": payload["semantic_version"],
                "fingerprint": payload["semantic_fingerprint"],
            },
            "plan_fingerprint": payload["validated_plan_fingerprint"],
            "current_window": fixed["current"], "baseline_window": fixed["baseline"],
            "warnings": fixed["warnings"],
        }, context.user,
    )


async def governed_result(invocation, outcome, context):
    if not context.mission_id or not settings.STUDIO_BUSINESS_WORKFLOW_ENABLED:
        return None
    business = outcome.business_result
    envelope, seed = business["envelope"], business["seed"]
    if (envelope.semantic is None or envelope.health.facts.execution_status != "success"
            or envelope.health.facts.semantic_grounding != "published"):
        return None
    mission = await mission_service.record_anchor(
        context.mission_id, envelope.semantic, envelope.metrics, context.user,
        filter_population_fingerprint=business["population_fingerprint"],
    )
    if not seed:
        if mission.work_intent.value != "INVESTIGATE":
            return None
        mission = await mission_service.record_investigation_requirements(
            context.mission_id, business["required_inputs"], envelope.model_dump(mode="json"),
            context.user,
        )
        return {
            "status": "clarification", "reason": "investigation_inputs_required",
            "required_inputs": business["required_inputs"],
            "established": envelope.model_dump(mode="json"),
            "mission_id": mission.mission_id,
            "mission": mission.model_dump(mode="json"),
        }
    try:
        result = await intelligence_service.automatic_investigation(
            seed, context.user, mission_id=mission.mission_id, agent_id=context.agent_id,
        )
        investigation = result.get("investigation")
        if investigation:
            # A retry repairs comparison→Mission linkage after an interrupted write.
            mission = await mission_service.get(mission.mission_id, context.user, project=False)
            mission = await mission_service._link_automatic_investigation(
                mission.mission_id, result["comparison"].id,
                ObjectRef(kind="investigation", id=investigation.id,
                          revision=investigation.revision),
                mission.revision, context.user,
            )
        return {
            "mission_id": mission.mission_id, "status": result["status"],
            "reason": result.get("reason"), "required_inputs": result.get("required_inputs", []),
            "investigation": investigation.model_dump(mode="json") if investigation else None,
            "comparison_id": result["comparison"].id if result.get("comparison") else None,
            "mission": mission.model_dump(mode="json"),
        }
    except HTTPException as exc:
        if exc.status_code not in {403, 404, 409, 422, 429, 503}:
            raise
        return {
            "mission_id": mission.mission_id, "status": "blocked",
            "reason": "canonicalization_unavailable", "required_inputs": [],
            "error_code": exc.status_code,
        }
