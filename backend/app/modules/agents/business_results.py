"""Studio hooks compose governed results through Mission and Intelligence owners."""

from __future__ import annotations

import json
from decimal import Decimal

from fastapi import HTTPException

from app.core.config import settings
from app.modules.agents.child_timeline import safe_public_text
from app.modules.agents.mission import mission_service
from app.modules.agents.mission_schema import ObjectRef, SemanticAnchor
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.assistant.business_observation import (
    MAX_BUSINESS_OBSERVATION_BYTES,
    BusinessResultHookResult,
)
from app.modules.intelligence.contracts import SemanticRef
from app.modules.intelligence.engine import CanonicalizationCollector, intelligence_service
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
            context.execution_timezone = prior.timezone
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


def bind_business_hooks(context):
    if context.mission_id and settings.STUDIO_BUSINESS_WORKFLOW_ENABLED:
        context.business_result_hook = governed_result
        context.business_time_hook = pin_time
        context.business_clock_hook = execution_clock


def canonical_observation(investigation, news, comparison, *, target_metric: str) -> dict:
    """Project exact canonical revisions, without a narrative or a new model call."""
    if (investigation.news_id != news.id or investigation.news_revision != news.revision
            or investigation.semantic != news.semantic
            or comparison.semantic != news.semantic
            or comparison.investigation_id != investigation.id
            or comparison.investigation_revision != investigation.revision
            or comparison.news_id != news.id):
        raise HTTPException(409, "Canonical comparison revisions are unavailable")
    hypotheses = []
    evidence_ids = {item.id for item in investigation.evidence[:20]}
    omitted_hypothesis_refs = 0
    for item in investigation.hypotheses[:10]:
        share = None
        if item.contribution is not None and news.change and item.causal_status == "arithmetic":
            share = float(Decimal(str(item.contribution)) / Decimal(str(news.change)))
        refs = [ident for ident in item.evidence_ids if ident in evidence_ids][:20]
        omitted_hypothesis_refs += len(item.evidence_ids) - len(refs)
        hypotheses.append({
            "id": safe_public_text(item.id, 128), "label": safe_public_text(item.label, 512),
            "dimension": safe_public_text(item.dimension, 128), "contribution": item.contribution,
            "contribution_pct": share, "causal_status": item.causal_status,
            "confidence_label": item.confidence.label,
            "method": safe_public_text(item.confidence.method, 128),
            "next_test": safe_public_text(item.next_test, 512),
            "evidence_refs": refs,
        })
    limitations = []
    configuration = comparison.configuration
    if configuration and configuration.count_column is None:
        limitations.append("Sample count is unavailable; arithmetic does not establish "
                           "statistical confidence or a causal effect.")
    if investigation.status != "complete":
        limitations.append("The Investigation is incomplete.")
    if len(investigation.decompositions) > 1:
        limitations.append("Dimensions are alternative decompositions; do not add their "
                           "contributions together.")
    observation = {
        "kind": "investigation", "id": investigation.id, "revision": investigation.revision,
        "status": investigation.status, "method": safe_public_text(investigation.method, 128),
        "semantic": investigation.semantic.model_dump(mode="json"),
        "news": {"id": news.id, "revision": news.revision},
        "comparison": {"id": comparison.id, "revision": comparison.revision},
        "target_metric": target_metric,
        "current_window": comparison.current_window.model_dump(mode="json"),
        "baseline_window": comparison.baseline_window.model_dump(mode="json"),
        "timezone": comparison.calendar_timezone or (configuration.timezone if configuration
                                                    else None),
        "baseline_value": news.before, "current_value": news.after,
        "delta": news.change, "delta_pct": news.relative_change,
        "hypotheses": hypotheses, "residual": investigation.residual,
        "decompositions": [{
            "dimension": safe_public_text(item.get("dimension"), 128),
            "residual": safe_public_text(item.get("residual"), 80),
            "reconciled": item.get("reconciled") is True,
            "baseline": safe_public_text(item.get("baseline"), 128),
        } for item in investigation.decompositions[:3]],
        "limitations": limitations,
        "evidence_refs": [{
            "id": item.id, "source_type": item.source_type,
            "source_id": item.source_id, "method": safe_public_text(item.method, 128),
            "semantic": item.semantic.model_dump(mode="json") if item.semantic else None,
            "window_start": item.window_start.isoformat() if item.window_start else None,
            "window_end": item.window_end.isoformat() if item.window_end else None,
        } for item in investigation.evidence[:20]],
        "truncation": {"truncated": False, "omitted_hypotheses":
                       max(0, len(investigation.hypotheses) - 10),
                       "omitted_evidence_refs": max(0, len(investigation.evidence) - 20),
                       "omitted_hypothesis_refs": omitted_hypothesis_refs},
    }
    # Leave room for the verifier's two short evidence identifiers.
    limit = MAX_BUSINESS_OBSERVATION_BYTES - 512
    while len(json.dumps(observation, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")) > limit:
        if observation["evidence_refs"]:
            observation["evidence_refs"].pop()
            observation["truncation"]["omitted_evidence_refs"] += 1
        elif observation["hypotheses"]:
            observation["hypotheses"].pop()
            observation["truncation"]["omitted_hypotheses"] += 1
        else:
            raise HTTPException(422, "Canonical observation exceeds its bound")
    observation["truncation"]["truncated"] = bool(
        observation["truncation"]["omitted_hypotheses"]
        or observation["truncation"]["omitted_evidence_refs"]
        or observation["truncation"]["omitted_hypothesis_refs"]
    )
    retained_refs = {item["id"] for item in observation["evidence_refs"]}
    for item in observation["hypotheses"]:
        retained = [ident for ident in item["evidence_refs"] if ident in retained_refs]
        observation["truncation"]["omitted_hypothesis_refs"] += (
            len(item["evidence_refs"]) - len(retained)
        )
        item["evidence_refs"] = retained
    if observation["truncation"]["truncated"]:
        observation["limitations"].append("Observation truncated to the provider evidence bound.")
    return observation


async def governed_result(invocation, outcome, context) -> BusinessResultHookResult:
    collector = CanonicalizationCollector()
    with collector.operation():
        try:
            result = await _governed_result(invocation, outcome, context, collector)
        except HTTPException as exc:
            if exc.status_code not in {403, 404, 409, 422, 429, 503}:
                raise
            collector.complete("failed")
            result = BusinessResultHookResult(public_event={
                "status": "blocked", "reason": "canonicalization_unavailable",
                "required_inputs": [], "error_code": exc.status_code,
            })
    return BusinessResultHookResult(
        public_event=result.public_event, provider_observation=result.provider_observation,
        trace_metadata={"business_canonicalization": collector.snapshot().model_dump(mode="json")},
    )


async def _governed_result(invocation, outcome, context, collector) -> BusinessResultHookResult:
    if not context.mission_id or not settings.STUDIO_BUSINESS_WORKFLOW_ENABLED:
        collector.complete("skipped")
        return BusinessResultHookResult()
    business = outcome.business_result
    envelope, seed = business["envelope"], business["seed"]
    if (envelope.semantic is None or envelope.health.facts.execution_status != "success"
            or envelope.health.facts.semantic_grounding != "published"):
        collector.complete("skipped")
        return BusinessResultHookResult()
    mission = await mission_service.record_anchor(
        context.mission_id, envelope.semantic, envelope.metrics, context.user,
        filter_population_fingerprint=business["population_fingerprint"],
    )
    effective_intent = getattr(context, "effective_work_intent", None)
    if effective_intent != "INVESTIGATE":
        collector.complete("skipped")
        return BusinessResultHookResult()
    if not seed:
        mission = await mission_service.record_investigation_requirements(
            context.mission_id, business["required_inputs"], envelope.model_dump(mode="json"),
            context.user,
        )
        from app.modules.agents.public_projections import public_mission

        collector.complete("incomplete")
        public = {
            "status": "clarification", "reason": "investigation_inputs_required",
            "required_inputs": business["required_inputs"],
            "established": envelope.model_dump(mode="json"),
            "mission_id": mission.mission_id,
            "mission": public_mission(mission).model_dump(mode="json"),
        }
        return BusinessResultHookResult(public_event=public, provider_observation={
            "kind": "investigation_requirements", "status": "incomplete",
            "reason": "investigation_inputs_required",
            "required_inputs": business["required_inputs"],
            "established": envelope.model_dump(mode="json"),
        })
    try:
        result = await intelligence_service.automatic_investigation(
            seed, context.user, mission_id=mission.mission_id, agent_id=context.agent_id,
            canonicalization=collector,
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
        from app.modules.agents.public_projections import public_mission
        from app.modules.intelligence.public_projections import public_investigation

        observation = None
        if investigation:
            if result.get("news") is None or result.get("comparison") is None:
                raise HTTPException(409, "Canonical comparison facts are unavailable")
            observation = canonical_observation(
                investigation, result["news"], result["comparison"],
                target_metric=seed.target_metric,
            )
        return BusinessResultHookResult(public_event={
            "mission_id": mission.mission_id, "status": result["status"],
            "reason": result.get("reason"), "required_inputs": result.get("required_inputs", []),
            "investigation": public_investigation(investigation).model_dump(mode="json")
            if investigation else None,
            "comparison_id": result["comparison"].id if result.get("comparison") else None,
            "mission": public_mission(mission).model_dump(mode="json"),
        }, provider_observation=observation)
    except HTTPException as exc:
        if exc.status_code not in {403, 404, 409, 422, 429, 503}:
            raise
        collector.complete("failed")
        return BusinessResultHookResult(public_event={
            "mission_id": mission.mission_id, "status": "blocked",
            "reason": "canonicalization_unavailable", "required_inputs": [],
            "error_code": exc.status_code,
        })
