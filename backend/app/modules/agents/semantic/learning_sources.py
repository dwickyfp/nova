"""Bounded, reauthorized evidence feeds reviewed proposals without assigning business truth."""

from __future__ import annotations

from typing import Literal, Protocol

from fastapi import HTTPException
from pydantic import Field

from app.modules.agents.semantic.usage_learning import aggregate_usage, load_scoped_usage
from app.modules.intelligence.contracts import (
    ContextNode,
    Contract,
    KnowledgeState,
    Scope,
    SemanticRef,
    fingerprint,
)
from app.modules.intelligence.engine import CycleBudget, intelligence_service


class LearningSourceRef(Contract):
    kind: Literal[
        "semantic_usage",
        "verified_query",
        "mission_deliverable",
        "decision",
        "outcome",
        "context_query_pattern",
    ]
    id: str = Field(min_length=1, max_length=128)
    revision: int | None = Field(default=None, ge=1)
    mission_id: str | None = Field(default=None, max_length=128)


class LearningRequest(Contract):
    agent_id: str = Field(min_length=1, max_length=64)
    thread_id: str = Field(min_length=1, max_length=64)
    semantic: SemanticRef
    sources: list[LearningSourceRef] = Field(min_length=1, max_length=20)


class ContextObservation(Contract):
    source_kind: str = Field(max_length=64)
    source_identity: str = Field(min_length=64, max_length=64)
    source_revision: int | None = None
    authority: Literal["usage_observation", "published_verified_query", "lifecycle_evidence"]
    semantic: SemanticRef
    state: Literal["INFERRED"] = "INFERRED"
    validity: Literal["current", "historical"]
    freshness: Literal["unknown"] = "unknown"
    shape: dict = Field(max_length=8)
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)
    observations: int | None = Field(default=None, ge=0)
    attribution: Literal["observed_after", "association", "supported_effect", "unknown"] = "unknown"


class LearningContext:
    def __init__(self, request: LearningRequest, user: dict, definition: dict):
        self.request, self.user, self.definition = request, user, definition
        self.scope = Scope.from_user(user)
        self.budget = CycleBudget()
        self.authorized_missions: set[str] = set()


class LearningSourceAdapter(Protocol):
    kind: str

    async def observe(
        self, ref: LearningSourceRef, context: LearningContext
    ) -> list[ContextObservation]: ...


def safe_shape(plan: dict, context: LearningContext) -> dict | None:
    if not isinstance(plan, dict):
        return None
    filters = plan.get("filters", plan.get("filter_shape", []))
    if not isinstance(filters, list) or any(not isinstance(item, dict) for item in filters):
        return None
    time = plan.get("time") or {}
    if not isinstance(time, dict):
        return None
    semantic = context.request.semantic
    result = aggregate_usage(
        [
            {
                "owner_name": context.scope.principal,
                "active_role": context.scope.active_role,
                "security_context_version": context.scope.security_context_version,
                "semantic_model_id": semantic.view_id,
                "semantic_version": semantic.version,
                "model_fingerprint": semantic.fingerprint,
                "succeeded": True,
                "metrics": plan.get("metrics"),
                "dimensions": plan.get("dimensions", []),
                "filter_shape": filters,
                "time_grain": plan.get("time_grain", time.get("grain")),
            }
        ],
        context.scope,
        semantic,
        context.definition,
    )
    if not result["patterns"]:
        return None
    pattern = result["patterns"][0]
    return {key: pattern[key] for key in ("metrics", "dimensions", "filter_shape", "time_grain")}


def observation(
    ref: LearningSourceRef,
    context: LearningContext,
    shape: dict,
    *,
    authority: str,
    evidence: list | None = None,
    observations: int | None = None,
    attribution="unknown",
    identity: str | None = None,
) -> ContextObservation:
    return ContextObservation(
        source_kind=ref.kind,
        source_identity=fingerprint(
            [
                context.request.semantic.model_dump(mode="json"),
                ref.model_dump(mode="json"),
                identity,
            ]
        ),
        source_revision=ref.revision,
        semantic=context.request.semantic,
        shape=shape,
        authority=authority,
        validity="current" if ref.kind == "verified_query" else "historical",
        evidence_refs=sorted(
            {
                fingerprint([item.get("id"), item.get("digest")])
                for item in (evidence or [])[:32]
                if isinstance(item, dict) and item.get("id") and item.get("digest")
            }
        ),
        observations=observations,
        attribution=attribution,
    )


class SemanticUsageSource:
    kind = "semantic_usage"

    async def observe(self, ref, context):
        if ref.id != context.request.semantic.view_id:
            raise HTTPException(422, "Usage must reference the selected Semantic View")
        summary = await load_scoped_usage(
            context.scope, context.request.semantic, context.definition
        )
        return [
            observation(
                ref,
                context,
                {
                    key: pattern[key]
                    for key in (
                        "metrics",
                        "dimensions",
                        "filter_shape",
                        "time_grain",
                    )
                },
                authority="usage_observation",
                observations=pattern["observations"],
                identity=pattern["pattern_id"],
            )
            for pattern in summary["patterns"][:100]
        ]


class VerifiedQuerySource:
    kind = "verified_query"

    async def observe(self, ref, context):
        from app.modules.agents.semantic.ir import SemanticModelIR
        from app.modules.agents.semantic.planning import (
            SemanticPlan,
            SemanticPlanError,
            validate_plan,
        )

        selected = next(
            (
                item
                for index, item in enumerate(
                    context.definition.get("verified_queries", []),
                    1,
                )
                if isinstance(item, dict)
                and str(
                    item.get("verified_query_id")
                    or (
                        f"{context.request.semantic.view_id}"
                        f":v{context.request.semantic.version}:{index}"
                    )
                )
                == ref.id
            ),
            None,
        )
        if selected is None:
            raise HTTPException(404, "Published verified query unavailable")
        plan = selected.get("semantic_plan")
        try:
            errors = validate_plan(
                SemanticModelIR.from_ossie(context.definition),
                SemanticPlan.from_dict(plan),
            )
            if errors:
                raise ValueError("Verified query plan failed validation")
        except (ValueError, TypeError, KeyError, SemanticPlanError) as exc:
            raise HTTPException(422, "Verified query plan needs review") from exc
        shape = safe_shape(plan, context)
        if shape is None:
            raise HTTPException(422, "Verified query shape needs review")
        return [observation(ref, context, shape, authority="published_verified_query")]


async def read_lifecycle(ref, context):
    if ref.revision is None:
        raise HTTPException(422, "Pin the learning source revision")
    if ref.mission_id:
        key = (f"{ref.kind}s", ref.id)
        if (
            ref.mission_id in context.authorized_missions
            and context.budget.mission_records.get(key) == ref.revision
        ):
            record = await intelligence_service.get(
                key[0],
                ref.id,
                context.user,
                revision=ref.revision,
                budget=context.budget,
            )
        else:
            record = await intelligence_service.get_for_mission(
                key[0],
                ref.id,
                context.user,
                revision=ref.revision,
                mission_id=ref.mission_id,
                budget=context.budget,
            )
            context.authorized_missions.add(ref.mission_id)
        return record.model_dump(mode="json")
    record = await intelligence_service.get(
        f"{ref.kind}s",
        ref.id,
        context.user,
        revision=ref.revision,
        budget=context.budget,
    )
    return record.model_dump(mode="json")


async def learning_allowed(thread_id, context):
    from app.modules.agents.mission import require_thread
    from app.modules.assistant.repository import assistant_repository

    await require_thread(thread_id, context.user)
    return await assistant_repository.learning_enabled(
        thread_id,
        user_name=context.scope.principal,
    )


class LifecycleEvidenceSource:
    def __init__(self, kind):
        self.kind = kind

    async def observe(self, ref, context):
        record = await read_lifecycle(ref, context)
        if record.get("semantic") != context.request.semantic.model_dump():
            raise HTTPException(409, "Learning source uses another semantic identity")
        decision = (
            record
            if ref.kind == "decision"
            else await read_lifecycle(
                LearningSourceRef(
                    kind="decision",
                    id=record["decision_id"],
                    revision=record["decision_revision"],
                    mission_id=ref.mission_id,
                ),
                context,
            )
        )
        if not decision.get("learning_enabled") or (
            not decision.get("thread_id")
            or not await learning_allowed(decision["thread_id"], context)
        ):
            return []
        if ref.kind == "outcome" and record.get("status") != "complete":
            return []
        shape = safe_shape({"metrics": [decision["target_metric"]]}, context)
        if shape is None:
            raise HTTPException(422, "Lifecycle metric needs review")
        return [
            observation(
                ref,
                context,
                shape,
                authority="lifecycle_evidence",
                evidence=record.get("evidence"),
                attribution=record.get("attribution", "unknown"),
            )
        ]


class MissionDeliverableSource:
    kind = "mission_deliverable"

    async def observe(self, ref, context):
        from app.modules.agents.mission import mission_service

        if not ref.mission_id:
            raise HTTPException(422, "A deliverable requires its Mission")
        mission = await mission_service.get(ref.mission_id, context.user, project=False)
        if not await learning_allowed(mission.thread_id, context):
            return []
        deliverable = next(
            (
                item
                for item in await mission_service.deliverables(
                    ref.mission_id,
                    context.user,
                    budget=context.budget,
                )
                if item.deliverable_id == ref.id
            ),
            None,
        )
        if deliverable is None:
            raise HTTPException(404, "Deliverable unavailable")
        if ref.revision != deliverable.mission_revision:
            raise HTTPException(409, "Deliverable source revision changed")
        result = []
        for obj in deliverable.object_refs[:20]:
            context.budget.consume("items")
            source = await intelligence_service.get_for_mission(
                {
                    "investigation": "investigations",
                    "decision": "decisions",
                    "action": "actions",
                    "outcome": "outcomes",
                }[obj.kind],
                obj.id,
                context.user,
                mission_id=ref.mission_id,
                revision=obj.revision,
                budget=context.budget,
            )
            context.authorized_missions.add(ref.mission_id)
            record = source.model_dump(mode="json")
            if record.get("semantic") != context.request.semantic.model_dump():
                continue
            decision = record if obj.kind == "decision" else None
            if obj.kind in {"outcome", "action"}:
                decision = await read_lifecycle(
                    LearningSourceRef(
                        kind="decision",
                        id=record["decision_id"],
                        revision=record["decision_revision"],
                        mission_id=ref.mission_id,
                    ),
                    context,
                )
            if decision and (
                not decision.get("learning_enabled")
                or not decision.get("thread_id")
                or not await learning_allowed(decision["thread_id"], context)
            ):
                continue
            for item in record.get("evidence", [])[:20]:
                if item.get("semantic") != context.request.semantic.model_dump():
                    continue
                shape = safe_shape(item.get("semantic_plan"), context)
                if shape:
                    result.append(
                        observation(
                            ref,
                            context,
                            shape,
                            authority="lifecycle_evidence",
                            evidence=[item],
                            identity=fingerprint([obj.model_dump(), shape]),
                        )
                    )
        return result[:100]


class ContextQueryPatternSource:
    kind = "context_query_pattern"

    async def observe(self, ref, context):
        from app.modules.intelligence.context_graph import derive_authority

        if ref.revision is None:
            raise HTTPException(422, "Pin the Context query-pattern revision")
        node = await intelligence_service.get(
            "nodes",
            ref.id,
            context.user,
            revision=ref.revision,
            budget=context.budget,
        )
        if (
            not isinstance(node, ContextNode)
            or node.kind != "query_pattern"
            or (node.semantic != context.request.semantic)
        ):
            raise HTTPException(404, "Context query pattern unavailable")
        current = await derive_authority(node, context.user, context.budget)
        if current.source_kind != "usage" or current.state != KnowledgeState.INFERRED:
            raise HTTPException(404, "Context query pattern no longer supported")
        summary = await load_scoped_usage(
            context.scope, context.request.semantic, context.definition
        )
        pattern = next(
            (item for item in summary["patterns"] if item["pattern_id"] == current.reference_id),
            None,
        )
        if not pattern:
            raise HTTPException(404, "Context pattern observation unavailable")
        return [
            observation(
                ref,
                context,
                {
                    key: pattern[key]
                    for key in (
                        "metrics",
                        "dimensions",
                        "filter_shape",
                        "time_grain",
                    )
                },
                authority="usage_observation",
                observations=pattern["observations"],
            )
        ]


SOURCE_ADAPTERS: dict[str, LearningSourceAdapter] = {
    adapter.kind: adapter
    for adapter in (
        SemanticUsageSource(),
        VerifiedQuerySource(),
        MissionDeliverableSource(),
        LifecycleEvidenceSource("decision"),
        LifecycleEvidenceSource("outcome"),
        ContextQueryPatternSource(),
    )
}


async def collect_observations(request: LearningRequest, user: dict) -> dict:
    from app.modules.agents.mission import require_thread
    from app.modules.agents.router import _require_agent
    from app.modules.agents.semantic.access import bound_view_ids
    from app.modules.assistant.repository import assistant_repository

    scope = Scope.from_user(user)
    agent = await _require_agent(request.agent_id, user)
    if request.semantic.view_id not in bound_view_ids(agent):
        raise HTTPException(404, "Learning Semantic View is not bound to this agent")
    thread = await require_thread(request.thread_id, user)
    if thread.get("agent_id") != request.agent_id:
        raise HTTPException(404, "Learning thread is not bound to this agent")
    payload = {
        "method": "governed-learning-sources-v1",
        "scope": scope.model_dump(exclude={"session_id"}),
        "semantic": request.semantic.model_dump(mode="json"),
        "observations": [],
        "review_required": True,
        "bounded": True,
    }
    if not await assistant_repository.learning_enabled(
        request.thread_id, user_name=scope.principal
    ):
        payload["reason"] = "learning_disabled"
        return {**payload, "digest": fingerprint(payload)}
    row = await intelligence_service.authorize_semantic(request.semantic, user, active=True)
    context = LearningContext(request, user, row["definition"])
    observations = {}
    for ref in request.sources:
        context.budget.consume("items")
        for item in await SOURCE_ADAPTERS[ref.kind].observe(ref, context):
            observations[fingerprint(item.model_dump(mode="json"))] = item.model_dump(mode="json")
            if len(observations) > 100:
                raise HTTPException(422, "Narrow learning to at most 100 observations")
    payload["observations"] = [observations[key] for key in sorted(observations)]
    return {**payload, "digest": fingerprint(payload)}
