"""Mission projections over existing journals and canonical Intelligence records."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import ValidationError

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.database import db
from app.modules.agents.access import has_verified_access
from app.modules.agents.harness_repository import harness_repository
from app.modules.agents.identity import SMART_AGENT_IDS
from app.modules.agents.mission_schema import (
    MISSION_DDLS,
    STAGE_LABELS,
    ContinuationDecision,
    DeliverableCreate,
    DeliverableSource,
    ExecutionTimeContext,
    InvestigationRequirements,
    Mission,
    MissionBinding,
    MissionCreate,
    MissionDeliverable,
    MissionExecutionContext,
    MissionReleasePin,
    MissionResume,
    MissionStage,
    ObjectRef,
    ResumableMission,
    ResumeReceipt,
    SemanticAnchor,
    StageKind,
    WorkIntent,
    object_binding_key,
)
from app.modules.agents.repository import agent_repository
from app.modules.agents.run_journal import run_journal
from app.modules.assistant.repository import assistant_repository
from app.modules.intelligence.contracts import Scope, SemanticRef, fingerprint, utc_now
from app.observability.metrics import studio_operation


def require_workflow() -> None:
    if not getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
        raise HTTPException(status_code=404, detail="Studio business workflow is unavailable")


def workflow_scope(user: dict) -> Scope:
    scope = Scope.from_user(user)
    if not scope.session_id:
        raise HTTPException(status_code=403, detail="A live session is required")
    return scope


def scope_params(scope: Scope) -> list:
    return [scope.principal, scope.active_role, scope.session_id, scope.security_context_version]


SCOPE_SQL = "owner_name=%s AND role_name=%s AND session_id=%s AND security_version=%s"
OWNER_SQL = "owner_name=%s AND role_name=%s"


def mission_request_digest(body: MissionCreate) -> str:
    value = body.model_dump(mode="json")
    if value.get("continue_mission_id") is None:
        value.pop("continue_mission_id", None)
    return fingerprint(value)


def continuation_policy(
    missions: list[Mission],
    *,
    operation_id: str,
    new_mission: bool = False,
    continue_mission_id: str | None = None,
    target: SemanticAnchor | None = None,
    canonical_refs: tuple[ObjectRef, ...] = (),
    screen_follow_up: bool = False,
    lightweight: bool = False,
) -> ContinuationDecision:
    replay = next(
        (
            m
            for m in missions
            if m.operation_id == operation_id
            or operation_id in m.run_ids
            or operation_id in m.turn_operations
        ),
        None,
    )
    if replay:
        return ContinuationDecision(mode="continue", reason="replay", mission_id=replay.mission_id)
    if new_mission and continue_mission_id:
        raise HTTPException(status_code=422, detail="New and continue Mission controls conflict")
    if new_mission:
        return ContinuationDecision(mode="new", reason="explicit_new")
    if continue_mission_id:
        chosen = next((m for m in missions if m.mission_id == continue_mission_id), None)
        if chosen is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        if chosen.cancel_requested or chosen.status in {"cancelled", "cancelling"}:
            raise HTTPException(status_code=409, detail="Cancelled Missions cannot continue")
        return ContinuationDecision(
            mode="continue", reason="explicit_continue", mission_id=chosen.mission_id
        )
    active = [
        m
        for m in missions
        if not m.cancel_requested and m.status in {"planned", "running", "blocked"}
    ]
    if target:
        matching = [
            m
            for m in active
            if any(
                a.semantic == target.semantic and set(target.metrics) <= set(a.metrics)
                for a in m.semantic_anchors
            )
        ]
        population_anchors = [
            a
            for m in matching
            for a in m.semantic_anchors
            if a.semantic == target.semantic
            and set(target.metrics) <= set(a.metrics)
            and a.filter_population_fingerprint is not None
        ]
        if (
            target.filter_population_fingerprint
            and population_anchors
            and not any(
                a.filter_population_fingerprint == target.filter_population_fingerprint
                for a in population_anchors
            )
        ):
            return ContinuationDecision(
                mode="none" if lightweight else "new",
                reason="different_semantic_target",
            )
        if target.filter_population_fingerprint:
            matching = [
                m
                for m in matching
                if any(
                    a.semantic == target.semantic
                    and set(target.metrics) <= set(a.metrics)
                    and a.filter_population_fingerprint == target.filter_population_fingerprint
                    for a in m.semantic_anchors
                )
            ]
        if len(matching) == 1 and screen_follow_up:
            return ContinuationDecision(
                mode="continue", reason="semantic_anchor", mission_id=matching[0].mission_id
            )
        if not matching and any(m.semantic_anchors for m in active):
            return ContinuationDecision(
                mode="none" if lightweight else "new", reason="different_semantic_target"
            )
    referenced = [m for m in active if any(ref in m.object_refs for ref in canonical_refs)]
    if len(referenced) == 1:
        return ContinuationDecision(
            mode="continue", reason="canonical_reference", mission_id=referenced[0].mission_id
        )
    if screen_follow_up and len(active) == 1 and not target:
        return ContinuationDecision(
            mode="continue", reason="screen_follow_up", mission_id=active[0].mission_id
        )
    return ContinuationDecision(
        mode="none" if lightweight else "new",
        reason="lightweight_answer" if lightweight else "unrelated_or_ambiguous",
    )


async def require_thread(thread_id: str, user: dict) -> dict:
    scope = workflow_scope(user)
    thread = await assistant_repository.get_thread(thread_id, user_name=scope.principal)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    agent_id = thread.get("agent_id")
    if agent_id and agent_id not in SMART_AGENT_IDS:
        agent = await agent_repository.get_agent(agent_id, owner_name=scope.principal)
        if agent is None:
            agent = await agent_repository.get_shared_agent(agent_id, role_name=scope.active_role)
        if agent is None or not await has_verified_access(
            agent, role_name=scope.active_role, user=user
        ):
            raise HTTPException(status_code=404, detail="Thread not found")
    return thread


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def default_steps(intent: WorkIntent) -> list[StageKind]:
    return {
        WorkIntent.ANSWER: [],
        WorkIntent.ANALYZE: [StageKind.EVIDENCE],
        WorkIntent.INVESTIGATE: [StageKind.INVESTIGATE, StageKind.EVIDENCE],
        WorkIntent.PLAN: [StageKind.EVIDENCE, StageKind.SCENARIOS, StageKind.DECIDE],
        WorkIntent.RESEARCH: [StageKind.INVESTIGATE, StageKind.EVIDENCE],
        WorkIntent.ACT: [StageKind.APPROVE, StageKind.EXECUTE, StageKind.VERIFY],
    }[intent]


def project_event(mission: Mission, run_id: str, kind: str, payload: dict) -> None:
    """Copy only code-owned stage transitions and identifiers, never journal prose."""
    if kind == "child_activity":
        kind = str(payload.get("event_type") or "")
        if kind == "table" and isinstance(payload.get("table"), dict):
            payload = payload["table"]
    stage_kind = None
    stage_status = "running"
    if kind in {"table", "chart"} or (
        kind == "tool_result" and payload.get("ok") is True and payload.get("evidence")
    ):
        stage_kind, stage_status = StageKind.EVIDENCE, "completed"
        evidence = payload.get("evidence") or {}
        ref = evidence.get("evidence_id") if isinstance(evidence, dict) else None
        ref = ref or payload.get("artifact_id") or payload.get("evidence_id")
        if not ref and (payload.get("tool_call_id") or payload.get("content_id")):
            ref = run_id + ":" + str(payload.get("tool_call_id") or payload["content_id"])
        if (
            isinstance(ref, str)
            and 0 < len(ref) <= 128
            and len(mission.evidence_refs) < 100
            and ref not in mission.evidence_refs
        ):
            mission.evidence_refs.append(ref)
    elif kind == "delegation_plan":
        stage_kind = StageKind.INVESTIGATE
    elif kind in {"approval_request", "consent_request"}:
        stage_kind = StageKind.APPROVE
    elif kind in {"error", "agent_failed"}:
        mission.status = "blocked"
    if stage_kind is not None:
        stage = next((s for s in mission.stages if s.kind == stage_kind), None)
        if stage is None:
            stage = MissionStage(kind=stage_kind, label=STAGE_LABELS[stage_kind])
            mission.stages.append(stage)
        if stage.status != "completed":
            stage.status = stage_status
        if run_id not in stage.source_refs:
            stage.source_refs.append(run_id)


async def canonical_payload(
    ref: ObjectRef, scope: Scope, user: dict | None = None, *, budget=None
) -> dict:
    from app.modules.intelligence.engine_schema import ENGINE_TABLES

    key = {
        "investigation": "investigations",
        "decision": "decisions",
        "action": "actions",
        "outcome": "outcomes",
    }[ref.kind]
    table = ENGINE_TABLES.get(key)
    if table is None:
        raise HTTPException(status_code=404, detail="Canonical object not found")
    result = await db.execute_system(
        f"SELECT payload FROM NOVA_SYSTEM.{table} WHERE id=%s AND principal=%s "
        "AND active_role=%s AND security_context_version=%s AND revision=%s LIMIT 2",
        [ref.id, scope.principal, scope.active_role, scope.security_context_version, ref.revision],
    )
    if len(result["rows"]) != 1:
        raise HTTPException(status_code=404, detail="Canonical object not found")
    payload = _decode(result["rows"][0][0])
    recorded_scope = payload.get("scope") or {}
    if any(
        recorded_scope.get(k) != scope.model_dump().get(k)
        for k in ("principal", "active_role", "security_context_version", "session_id")
    ):
        raise HTTPException(status_code=404, detail="Canonical object not found")
    if user is not None:
        from app.modules.intelligence.engine import MODELS, CycleBudget, intelligence_service

        await intelligence_service.authorize_record(
            MODELS[key].model_validate(payload),
            user,
            budget if budget is not None else CycleBudget(),
        )
    return payload


def project_object(mission: Mission, ref: ObjectRef, record: dict) -> None:
    status = record.get("status")
    mapping = {
        "investigation": (StageKind.INVESTIGATE, status == "complete"),
        "decision": (
            StageKind.DECIDE,
            status in {"selected", "approved", "observing", "evaluated"},
        ),
        "action": (StageKind.EXECUTE, status in {"executed", "verified", "completed"}),
        "outcome": (StageKind.OBSERVE, status == "complete"),
    }
    kind, complete = mapping[ref.kind]
    kinds = [(kind, "completed" if complete else "running")]
    if record.get("evidence"):
        kinds.append((StageKind.EVIDENCE, "completed"))
    if ref.kind == "decision" and record.get("options"):
        kinds.append((StageKind.SCENARIOS, "completed"))
    if ref.kind == "decision" and status in {"awaiting_approval", "approved", "denied"}:
        kinds.append((StageKind.APPROVE, "completed" if status == "approved" else "blocked"))
    if ref.kind == "action" and status == "verified":
        kinds.append((StageKind.VERIFY, "completed"))
    for stage_kind, stage_status in kinds:
        stage = next((s for s in mission.stages if s.kind == stage_kind), None)
        if stage is None:
            stage = MissionStage(kind=stage_kind, label=STAGE_LABELS[stage_kind])
            mission.stages.append(stage)
        stage.status = stage_status
        stage.source_refs = [
            r for r in stage.source_refs if not r.startswith(f"{ref.kind}:{ref.id}:")
        ]
        stage.source_refs.append(f"{ref.kind}:{ref.id}:{ref.revision}")
    for evidence in record.get("evidence") or []:
        evidence_id = evidence.get("id")
        if evidence_id and evidence_id not in mission.evidence_refs:
            if len(mission.evidence_refs) >= 100:
                raise HTTPException(status_code=422, detail="Mission evidence bound reached")
            mission.evidence_refs.append(evidence_id)
    if not mission.run_ids and not mission.cancel_requested:
        mission.status = (
            "completed"
            if all(stage.status == "completed" for stage in mission.stages)
            else "blocked"
            if any(stage.status == "blocked" for stage in mission.stages)
            else "running"
        )


class MissionService:
    async def ensure_schema(self) -> None:
        for ddl in MISSION_DDLS:
            await db.execute_system(ddl)

    async def _get(self, mission_id: str, scope: Scope) -> Mission:
        result = await db.execute_system(
            "SELECT payload FROM NOVA_SYSTEM.CONFIG_STUDIO_MISSIONS "
            f"WHERE mission_id=%s AND {SCOPE_SQL}",
            [mission_id, *scope_params(scope)],
        )
        if not result["rows"]:
            raise HTTPException(status_code=404, detail="Mission not found")
        mission = Mission.model_validate(_decode(result["rows"][0][0]))
        if mission.scope != scope:
            raise HTTPException(status_code=404, detail="Mission not found")
        return mission

    async def get(self, mission_id: str, user: dict, *, project: bool = True) -> Mission:
        require_workflow()
        scope = workflow_scope(user)
        mission = await self._get(mission_id, scope)
        await require_thread(mission.thread_id, user)
        return await self.refresh(mission, user) if project else mission

    async def _get_owner(self, mission_id: str, scope: Scope) -> Mission:
        result = await db.execute_system(
            "SELECT payload FROM NOVA_SYSTEM.CONFIG_STUDIO_MISSIONS "
            f"WHERE mission_id=%s AND {OWNER_SQL}",
            [mission_id, scope.principal, scope.active_role],
        )
        if len(result["rows"]) != 1:
            raise HTTPException(status_code=404, detail="Mission not found")
        mission = Mission.model_validate(_decode(result["rows"][0][0]))
        if (mission.owner_scope.principal, mission.owner_scope.active_role) != (
            scope.principal,
            scope.active_role,
        ):
            raise HTTPException(status_code=404, detail="Mission not found")
        return mission

    async def resumable(self, thread_id: str, user: dict) -> list[ResumableMission]:
        require_workflow()
        await require_thread(thread_id, user)
        scope = workflow_scope(user)
        result = await db.execute_system(
            "SELECT payload FROM NOVA_SYSTEM.CONFIG_STUDIO_MISSIONS "
            f"WHERE thread_id=%s AND {OWNER_SQL} ORDER BY created_at DESC, mission_id LIMIT 51",
            [thread_id, scope.principal, scope.active_role],
        )
        if len(result["rows"]) > 50:
            raise HTTPException(status_code=422, detail="Thread Mission bound reached")
        summaries = []
        for row in result["rows"]:
            mission = Mission.model_validate(_decode(row[0]))
            if mission.cancel_requested:
                continue
            if (mission.owner_scope.principal, mission.owner_scope.active_role) != (
                scope.principal,
                scope.active_role,
            ):
                raise HTTPException(
                    status_code=409, detail="Mission ownership requires reconciliation"
                )
            summaries.append(
                ResumableMission(
                    mission_id=mission.mission_id,
                    thread_id=thread_id,
                    objective=mission.objective,
                    status=mission.status,
                    revision=mission.revision,
                    resume_required=mission.scope != scope,
                )
            )
        return summaries

    async def canonical_read(
        self, mission_id: str, ref: ObjectRef, user: dict, *, budget=None
    ) -> dict:
        mission = await self.get(mission_id, user, project=False)
        if ref not in mission.pinned_objects:
            raise HTTPException(status_code=404, detail="Mission canonical reference not found")
        from app.modules.intelligence.engine import intelligence_service

        record = await intelligence_service.get_for_mission(
            {
                "investigation": "investigations",
                "decision": "decisions",
                "action": "actions",
                "outcome": "outcomes",
            }[ref.kind],
            ref.id,
            user,
            mission_id=mission_id,
            revision=ref.revision,
            budget=budget,
        )
        return record.model_dump(mode="json")

    async def investigation_context(
        self,
        mission_id: str,
        investigation_id: str,
        revision: int,
        user: dict,
    ) -> dict:
        mission = await self.get(mission_id, user, project=False)
        ref = ObjectRef(kind="investigation", id=investigation_id, revision=revision)
        if ref not in mission.pinned_objects:
            raise HTTPException(status_code=404, detail="Mission related reference not found")
        from app.modules.intelligence.engine import intelligence_service

        records = await intelligence_service.mission_investigation_context(
            mission_id,
            investigation_id,
            revision,
            user,
        )
        return {key: record.model_dump(mode="json") for key, record in records.items()}

    async def _reauthorize_resume(
        self, mission: Mission, user: dict, *, check_execution: bool = True
    ) -> None:
        from app.modules.intelligence.actions import action_service
        from app.modules.intelligence.engine import intelligence_service

        await action_service.revalidate(user)
        thread = await require_thread(mission.thread_id, user)
        agent_id = thread.get("agent_id")
        if mission.agent_id and mission.agent_id != agent_id:
            raise HTTPException(status_code=409, detail="Mission thread agent changed")
        if agent_id and agent_id not in SMART_AGENT_IDS:
            from app.modules.agents.releases import load_runtime_manifest, resource_contracts

            agent = await agent_repository.get_agent(agent_id, owner_name=mission.scope.principal)
            if agent is None:
                agent = await agent_repository.get_shared_agent(
                    agent_id, role_name=mission.scope.active_role
                )
            if agent is None:
                raise HTTPException(status_code=404, detail="Mission agent unavailable")
            manifest = await load_runtime_manifest(agent)
            if manifest and manifest["dependencies"].get("resources") != await resource_contracts(
                dict(agent), user
            ):
                raise HTTPException(status_code=409, detail="Pinned agent resources changed")
        references = {a.semantic.model_dump_json(): a.semantic for a in mission.semantic_anchors}
        references.update(
            {c.semantic.model_dump_json(): c.semantic for c in mission.execution_contexts}
        )
        for semantic in references.values():
            await intelligence_service.authorize_semantic(semantic, user)
        for pin in mission.release_pins:
            await self.authorize_release_pin(pin, user)
        for ref in mission.pinned_objects:
            record = await intelligence_service.get_for_mission(
                {
                    "investigation": "investigations",
                    "decision": "decisions",
                    "action": "actions",
                    "outcome": "outcomes",
                }[ref.kind],
                ref.id,
                user,
                mission_id=mission.mission_id,
                revision=ref.revision,
            )
            if ref.kind == "action" and record.status in {
                "executing",
                "verification_required",
                "compensating",
                "compensation_required",
            }:
                raise HTTPException(
                    status_code=409, detail="Uncertain action requires reconciliation before resume"
                )
            if ref.kind == "action":
                from app.modules.intelligence.engine import MODELS

                latest = await intelligence_service.repository.get(
                    "actions",
                    ref.id,
                    record.scope,
                    MODELS["actions"],
                )
                if latest is None or latest.status in {
                    "executing",
                    "verification_required",
                    "compensating",
                    "compensation_required",
                }:
                    raise HTTPException(
                        status_code=409,
                        detail="Uncertain action requires reconciliation before resume",
                    )
        for run_id in mission.run_ids:
            run = await self._run(mission, run_id)
            if (check_execution or mission.run_bindings[run_id] != workflow_scope(user)) and run[
                "status"
            ] not in {"completed", "failed", "cancelled", "interrupted"}:
                raise HTTPException(
                    status_code=409, detail="Old-session execution must stop before resume"
                )
            if run["smart"]:
                await self._reauthorize_smart_resume(mission, run_id, user, check_execution)

    async def _reauthorize_smart_resume(
        self, mission: Mission, run_id: str, user: dict, check_execution: bool
    ) -> None:
        from app.modules.agents.resource_delegation import resource_delegation
        from app.modules.assistant.attachments import MAX_FILES

        binding = mission.run_bindings[run_id]
        tree = await harness_repository.tree(
            run_id,
            owner_name=mission.scope.principal,
            role_name=mission.scope.active_role,
        )
        if not tree or len(tree) > 100:
            raise HTTPException(status_code=409, detail="Mission execution tree unavailable")
        for participant in tree:
            if [
                participant.get("owner_name"),
                participant.get("role_name"),
                participant.get("session_id"),
                participant.get("security_version"),
            ] != scope_params(binding) or participant.get("thread_id") != mission.thread_id:
                raise HTTPException(status_code=403, detail="Mission execution tree scope changed")
            if (check_execution or binding != workflow_scope(user)) and participant[
                "status"
            ] not in {"completed", "failed", "cancelled", "interrupted"}:
                raise HTTPException(
                    status_code=409, detail="Old-session execution must stop before resume"
                )
        for agent_id in {row["agent_id"] for row in tree} - set(SMART_AGENT_IDS):
            from app.modules.agents.releases import load_runtime_manifest, resource_contracts

            agent = await agent_repository.get_agent(agent_id, owner_name=binding.principal)
            if agent is None:
                agent = await agent_repository.get_shared_agent(
                    agent_id, role_name=binding.active_role
                )
            if agent is None or not await has_verified_access(
                agent, role_name=binding.active_role, user=user
            ):
                raise HTTPException(status_code=404, detail="Mission participant unavailable")
            manifest = await load_runtime_manifest(agent)
            if manifest and manifest["dependencies"].get("resources") != await resource_contracts(
                dict(agent), user
            ):
                raise HTTPException(status_code=409, detail="Pinned participant resources changed")
        resources = await db.execute_system(
            "SELECT resource_id FROM NOVA_SYSTEM.CONFIG_STUDIO_RESOURCES "
            f"WHERE {SCOPE_SQL} AND thread_id=%s AND root_run_id=%s LIMIT 4",
            [*scope_params(binding), mission.thread_id, run_id],
        )
        expected = {row[0] for row in resources["rows"]}
        expected.update(
            ref for row in tree for ref in (row.get("payload") or {}).get("resource_refs", [])
        )
        if len(expected) > MAX_FILES:
            raise HTTPException(status_code=409, detail="Mission resource bound exceeded")
        if expected:
            try:
                available = await resource_delegation.reauthorize_for_mission(
                    mission.mission_id, run_id, user
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=403, detail="Pinned resources require current authorization"
                ) from exc
            if expected - set(available):
                raise HTTPException(status_code=403, detail="Pinned resources are unavailable")

    async def resume(self, mission_id: str, body: MissionResume, user: dict) -> Mission:
        require_workflow()
        scope = workflow_scope(user)
        mission = None
        try:
            mission = await self._get_owner(mission_id, scope)
            async with harness_repository.admission_lock(
                mission.thread_id, scope.principal
            ) as owned:
                mission = await self._get_owner(mission_id, scope)
                digest = fingerprint([body.model_dump(mode="json"), scope.model_dump(mode="json")])
                prior = mission.resume_operations.get(body.operation_id)
                if prior:
                    if prior.request_digest != digest or prior.binding != mission.current_binding:
                        raise HTTPException(
                            status_code=409, detail="Mission resume operation collision"
                        )
                    await self._reauthorize_resume(mission, user, check_execution=False)
                    return mission
                if mission.revision != body.expected_revision:
                    raise HTTPException(status_code=409, detail="Mission revision changed; reload")
                if mission.cancel_requested:
                    raise HTTPException(status_code=409, detail="Cancelled Missions cannot resume")
                await self._reauthorize_resume(mission, user)
                if len(mission.resume_operations) >= 32:
                    raise HTTPException(
                        status_code=422, detail="Mission resume operation bound reached"
                    )
                old_scope = mission.scope
                if old_scope != scope and old_scope not in mission.historical_bindings:
                    if len(mission.historical_bindings) >= 32:
                        raise HTTPException(
                            status_code=422, detail="Mission historical binding bound reached"
                        )
                    mission.historical_bindings.append(old_scope.model_copy())
                mission.scope = scope
                mission.current_binding = MissionBinding(
                    scope=scope,
                    generation=mission.current_binding.generation + 1,
                    bound_at=utc_now(),
                )
                mission.resume_operations[body.operation_id] = ResumeReceipt(
                    request_digest=digest,
                    binding=mission.current_binding,
                    mission_revision=mission.revision + 1,
                )
                mission.updated_at = utc_now()
                mission.revision += 1
                validated = Mission.model_validate(mission.model_dump())
                await owned()
                result = await db.execute_system(
                    "UPDATE NOVA_SYSTEM.CONFIG_STUDIO_MISSIONS "
                    "SET revision=%s,payload=%s,updated_at=%s,"
                    "session_id=%s,security_version=%s "
                    f"WHERE mission_id=%s AND revision=%s AND {SCOPE_SQL}",
                    [
                        validated.revision,
                        validated.model_dump_json(),
                        _sql_time(validated.updated_at),
                        scope.session_id,
                        scope.security_context_version,
                        mission_id,
                        body.expected_revision,
                        *scope_params(old_scope),
                    ],
                )
                if result.get("affected") != 1:
                    raise HTTPException(status_code=409, detail="Mission revision changed; reload")
                await self.audit(validated, "RESUME")
                return validated
        except Exception:
            if mission:
                await self.audit(
                    mission.model_copy(update={"scope": scope}), "RESUME", status="REFUSED"
                )
            else:
                await write_audit_log(
                    event_type="STUDIO_MISSION",
                    user_name=scope.principal,
                    action="RESUME",
                    object_type="MISSION",
                    object_name=mission_id,
                    status="REFUSED",
                    session_id=scope.session_id,
                    active_role=scope.active_role,
                    security_context_version=scope.security_context_version,
                )
            raise

    async def list(self, thread_id: str, user: dict) -> list[Mission]:
        require_workflow()
        await require_thread(thread_id, user)
        scope = workflow_scope(user)
        result = await db.execute_system(
            "SELECT payload FROM NOVA_SYSTEM.CONFIG_STUDIO_MISSIONS "
            f"WHERE thread_id=%s AND {SCOPE_SQL} ORDER BY created_at DESC, mission_id LIMIT 51",
            [thread_id, *scope_params(scope)],
        )
        if len(result["rows"]) > 50:
            raise HTTPException(status_code=422, detail="Thread Mission bound reached")
        missions = [Mission.model_validate(_decode(row[0])) for row in result["rows"]]
        if any(m.scope != scope for m in missions):
            raise HTTPException(status_code=409, detail="Mission scope requires reconciliation")
        return [await self.refresh(m, user) for m in missions]

    async def _turn_candidates(
        self, thread_id: str, scope: Scope, operation_id: str
    ) -> list[Mission]:
        result = await db.execute_system(
            "SELECT payload FROM NOVA_SYSTEM.CONFIG_STUDIO_MISSIONS "
            f"WHERE thread_id=%s AND {OWNER_SQL} ORDER BY created_at DESC, mission_id LIMIT 51",
            [thread_id, scope.principal, scope.active_role],
        )
        if len(result["rows"]) > 50:
            raise HTTPException(status_code=422, detail="Thread Mission bound reached")
        missions = [Mission.model_validate(_decode(row[0])) for row in result["rows"]]
        for mission in missions:
            if (mission.owner_scope.principal, mission.owner_scope.active_role) != (
                scope.principal,
                scope.active_role,
            ):
                raise HTTPException(
                    status_code=409, detail="Mission ownership requires reconciliation"
                )
            if mission.scope != scope and (
                mission.operation_id == operation_id or operation_id in mission.turn_operations
            ):
                raise HTTPException(
                    status_code=409, detail="Mission execution binding changed; resume required"
                )
        return [mission for mission in missions if mission.scope == scope]

    async def create(
        self,
        thread_id: str,
        body: MissionCreate,
        user: dict,
        *,
        selection: ContinuationDecision | None = None,
    ) -> Mission:
        require_workflow()
        thread = await require_thread(thread_id, user)
        scope = workflow_scope(user)
        async with harness_repository.admission_lock(thread_id, scope.principal) as owned:
            prior = await self._turn_candidates(thread_id, scope, body.operation_id)
            same_op = next((m for m in prior if m.operation_id == body.operation_id), None)
            if same_op:
                if same_op.request_digest != mission_request_digest(body):
                    raise HTTPException(status_code=409, detail="Mission operation id collision")
                return same_op
            selected_operation = next(
                (m for m in prior if body.operation_id in m.turn_operations), None
            )
            if selected_operation:
                if selected_operation.turn_operations[body.operation_id] != fingerprint(
                    body.objective
                ):
                    raise HTTPException(status_code=409, detail="Mission operation id collision")
                return selected_operation
            if body.continue_mission_id:
                decision = continuation_policy(
                    prior,
                    operation_id=body.operation_id,
                    continue_mission_id=body.continue_mission_id,
                    new_mission=body.new_mission,
                )
                mission = next(m for m in prior if m.mission_id == decision.mission_id)
                if len(mission.turn_operations) >= 100:
                    raise HTTPException(
                        status_code=422, detail="Mission turn operation bound reached"
                    )
                mission.turn_operations[body.operation_id] = fingerprint(body.objective)
                mission.continuation = decision
                return await self._save(mission, mission.revision, owned)
            if len(prior) >= 50:
                raise HTTPException(status_code=422, detail="Thread Mission bound reached")
            mission_id = str(
                uuid5(
                    NAMESPACE_URL,
                    "nova:mission:"
                    + fingerprint([thread_id, scope.model_dump(), body.operation_id]),
                )
            )
            now = utc_now()
            mission = Mission(
                mission_id=mission_id,
                thread_id=thread_id,
                agent_id=thread.get("agent_id"),
                scope=scope,
                objective=body.objective,
                work_intent=body.work_intent,
                status="planned",
                revision=1,
                operation_id=body.operation_id,
                request_digest=mission_request_digest(body),
                continuation=selection
                or ContinuationDecision(
                    mode="new",
                    reason="explicit_new" if body.new_mission else "unrelated_or_ambiguous",
                ),
                created_at=now,
                updated_at=now,
                stages=[
                    MissionStage(kind=k, label=STAGE_LABELS[k])
                    for k in dict.fromkeys(
                        body.public_work_steps or default_steps(body.work_intent)
                    )
                ],
            )
            await owned()
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CONFIG_STUDIO_MISSIONS "
                "(mission_id,owner_name,thread_id,role_name,session_id,security_version,"
                "revision,payload,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    mission_id,
                    scope.principal,
                    thread_id,
                    scope.active_role,
                    scope.session_id,
                    scope.security_context_version,
                    1,
                    mission.model_dump_json(),
                    _sql_time(now),
                    _sql_time(now),
                ],
            )
            await self.audit(mission, "CREATE")
            return mission

    async def for_turn(
        self,
        thread_id: str,
        user: dict,
        *,
        operation_id: str,
        objective: str,
        work_intent: WorkIntent | None = None,
        public_work_steps: tuple[StageKind, ...] = (),
        explicit: bool = False,
        new_mission: bool = False,
        continue_mission_id: str | None = None,
        semantic_target: SemanticAnchor | dict | None = None,
        canonical_refs: tuple[ObjectRef, ...] = (),
        screen_follow_up: bool = False,
        continuation_sink: Callable[[ContinuationDecision], None] | None = None,
    ) -> Mission | None:
        if not getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
            return None
        await require_thread(thread_id, user)
        candidates = await self._turn_candidates(thread_id, workflow_scope(user), operation_id)
        missions = [await self.refresh(mission, user) for mission in candidates]
        existing_operation = next((m for m in missions if m.operation_id == operation_id), None)
        if existing_operation is not None:
            if existing_operation.objective != objective:
                raise HTTPException(status_code=409, detail="Mission operation id collision")
            if continuation_sink:
                continuation_sink(
                    ContinuationDecision(
                        mode="continue", reason="replay", mission_id=existing_operation.mission_id
                    )
                )
            return existing_operation
        selected_operation = next((m for m in missions if operation_id in m.turn_operations), None)
        if selected_operation:
            if selected_operation.turn_operations[operation_id] != fingerprint(objective):
                raise HTTPException(status_code=409, detail="Mission operation id collision")
            if continuation_sink:
                continuation_sink(
                    ContinuationDecision(
                        mode="continue", reason="replay", mission_id=selected_operation.mission_id
                    )
                )
            return selected_operation
        lightweight = not explicit and (
            work_intent in {None, WorkIntent.ANSWER}
            or (
                work_intent in {WorkIntent.ANALYZE, WorkIntent.RESEARCH}
                and len(public_work_steps) < 2
            )
        )
        selection = continuation_policy(
            missions,
            operation_id=operation_id,
            new_mission=new_mission,
            continue_mission_id=continue_mission_id,
            target=SemanticAnchor.model_validate(semantic_target) if semantic_target else None,
            canonical_refs=canonical_refs,
            screen_follow_up=screen_follow_up,
            lightweight=lightweight,
        )
        if selection.mode == "none":
            if continuation_sink:
                continuation_sink(selection)
            return None
        if selection.mode == "continue":
            selected = next(m for m in missions if m.mission_id == selection.mission_id)
            if selection.reason == "replay":
                if continuation_sink:
                    continuation_sink(selection)
                return selected
            async with harness_repository.admission_lock(
                thread_id, selected.scope.principal
            ) as owned:
                selected = await self._get(selected.mission_id, workflow_scope(user))
                if selected.cancel_requested:
                    raise HTTPException(
                        status_code=409, detail="Cancelled Missions cannot continue"
                    )
                if len(selected.turn_operations) >= 100:
                    raise HTTPException(
                        status_code=422, detail="Mission turn operation bound reached"
                    )
                selected.turn_operations[operation_id] = fingerprint(objective)
                selected.continuation = selection
                result = await self._save(selected, selected.revision, owned)
            if continuation_sink:
                continuation_sink(selection)
            return result
        result = await self.create(
            thread_id,
            MissionCreate(
                objective=objective,
                work_intent=work_intent or WorkIntent.INVESTIGATE,
                public_work_steps=list(public_work_steps),
                operation_id=operation_id,
                new_mission=True,
            ),
            user,
            selection=selection,
        )
        if continuation_sink:
            continuation_sink(selection)
        return result

    async def _save(
        self, mission: Mission, previous_revision: int, owned, *, advance_revision: bool = True
    ) -> Mission:
        await owned()
        mission.revision = previous_revision + int(advance_revision)
        if advance_revision:
            mission.updated_at = utc_now()
        validated = Mission.model_validate(mission.model_dump())
        result = await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_STUDIO_MISSIONS SET revision=%s,payload=%s,updated_at=%s "
            f"WHERE mission_id=%s AND revision=%s AND {SCOPE_SQL}",
            [
                validated.revision,
                validated.model_dump_json(),
                _sql_time(validated.updated_at),
                validated.mission_id,
                previous_revision,
                *scope_params(validated.scope),
            ],
        )
        if result.get("affected") != 1:
            raise HTTPException(status_code=409, detail="Mission revision changed; reload")
        return validated

    async def attach_run(self, mission_id: str, run_id: str, user: dict) -> Mission:
        mission = await self.get(mission_id, user, project=False)
        async with harness_repository.admission_lock(
            mission.thread_id, mission.scope.principal
        ) as owned:
            mission = await self._get(mission_id, mission.scope)
            if run_id in mission.run_bindings and mission.run_bindings[run_id] != mission.scope:
                raise HTTPException(
                    status_code=403, detail="Historical run cannot start new execution"
                )
            if mission.cancel_requested:
                await self._stop_run(mission, run_id)
                raise HTTPException(
                    status_code=409, detail="Start a new Mission after cancellation"
                )
            await self._run(mission, run_id)
            if run_id in mission.run_ids:
                return mission
            if len(mission.run_ids) >= 100:
                raise HTTPException(status_code=422, detail="Mission run bound reached")
            mission.run_ids.append(run_id)
            mission.run_bindings[run_id] = mission.scope.model_copy()
            mission.projection_cursor[run_id] = -1
            mission.status = "running"
            return await self._save(mission, mission.revision, owned)

    async def _run(self, mission: Mission, run_id: str) -> dict:
        binding = mission.run_bindings.get(run_id, mission.scope)
        result = await db.execute_system(
            "SELECT status,last_sequence,session_id,security_version,agent_id FROM "
            "NOVA_SYSTEM.CONFIG_AGENT_RUNS WHERE run_id=%s AND owner_name=%s "
            "AND thread_id=%s AND role_name=%s",
            [run_id, mission.scope.principal, mission.thread_id, mission.scope.active_role],
        )
        if len(result["rows"]) != 1:
            raise HTTPException(status_code=404, detail="Mission run not found")
        status, sequence, session, version, agent_id = result["rows"][0]
        smart = agent_id in SMART_AGENT_IDS
        if session != binding.session_id or version != binding.security_context_version:
            raise HTTPException(status_code=403, detail="Mission run security context changed")
        return {
            "status": status,
            "sequence": int(sequence),
            "smart": smart,
            "agent_id": agent_id,
        }

    async def authorize_release_pin(
        self, pin: MissionReleasePin, user: dict, *, budget=None
    ) -> dict:
        from app.modules.agents.releases import get_manifest, resource_contracts
        from app.modules.agents.router import _require_agent
        from app.modules.intelligence.engine import intelligence_service

        agent = await _require_agent(pin.agent_id, user)
        manifest = await get_manifest(
            pin.agent_id,
            agent["owner_name"],
            manifest_id=pin.manifest_id,
        )
        if not manifest or (
            manifest.get("id"),
            manifest.get("agent_id"),
            manifest.get("version_id"),
            manifest.get("fingerprint"),
        ) != (pin.manifest_id, pin.agent_id, pin.version_id, pin.fingerprint):
            raise HTTPException(status_code=404, detail="Mission release unavailable")
        dependencies = manifest.get("dependencies")
        if not isinstance(dependencies, dict) or fingerprint(dependencies) != pin.fingerprint:
            raise HTTPException(status_code=409, detail="Mission release requires reconciliation")
        semantic_pins = dependencies.get("semantic_views", [])
        if not isinstance(semantic_pins, list) or len(semantic_pins) > 16:
            raise HTTPException(status_code=409, detail="Mission release semantic pins invalid")
        for semantic in semantic_pins:
            await intelligence_service.authorize_semantic(
                SemanticRef.model_validate(semantic),
                user,
                budget=budget,
            )
        from app.modules.agents.resources import validate_resources

        configuration = dict(dependencies.get("configuration") or {})
        await validate_resources(configuration, user)
        if "resources" in dependencies and dependencies["resources"] != await resource_contracts(
            configuration, user
        ):
            raise HTTPException(status_code=409, detail="Mission release resources changed")
        return manifest

    async def record_release(
        self,
        mission_id: str,
        run_id: str,
        manifest: dict | None,
        user: dict,
    ) -> Mission:
        mission = await self.get(mission_id, user, project=False)
        if manifest is None:
            return mission
        pin = MissionReleasePin(
            agent_id=manifest["agent_id"],
            manifest_id=manifest["id"],
            version_id=manifest["version_id"],
            fingerprint=manifest["fingerprint"],
            run_id=run_id,
        )
        async with harness_repository.admission_lock(
            mission.thread_id, mission.scope.principal
        ) as owned:
            mission = await self._get(mission_id, workflow_scope(user))
            try:
                if mission.cancel_requested:
                    raise HTTPException(status_code=409, detail="Mission cancellation requested")
                if mission.run_bindings.get(run_id) != mission.scope:
                    raise HTTPException(status_code=403, detail="Mission execution binding changed")
                run = await self._run(mission, run_id)
                if run["agent_id"] != pin.agent_id:
                    raise HTTPException(status_code=403, detail="Mission release agent changed")
                prior = next((item for item in mission.release_pins if item.run_id == run_id), None)
                if prior and prior != pin:
                    raise HTTPException(
                        status_code=409, detail="Mission release identity collision"
                    )
                if fingerprint(manifest.get("dependencies")) != pin.fingerprint:
                    raise HTTPException(
                        status_code=409, detail="Bound release requires reconciliation"
                    )
                await self.authorize_release_pin(pin, user)
                if prior:
                    return mission
                if len(mission.release_pins) >= 100:
                    raise HTTPException(status_code=422, detail="Mission release pin bound reached")
                mission.release_pins.append(pin)
                result = await self._save(mission, mission.revision, owned)
                await self.audit(result, "PIN_RELEASE")
                return result
            except HTTPException:
                await self.audit(mission, "PIN_RELEASE", status="REFUSED")
                raise

    async def record_execution_context(
        self,
        mission_id: str,
        run_id: str,
        tool_call_id: str,
        bounded_context: dict,
        user: dict,
    ) -> MissionExecutionContext:
        context = ExecutionTimeContext.model_validate(bounded_context)
        digest = fingerprint(context.model_dump(mode="json"))
        pinned = MissionExecutionContext(
            **context.model_dump(),
            run_id=run_id,
            tool_call_id=tool_call_id,
            context_digest=digest,
        )
        mission = await self.get(mission_id, user, project=False)
        async with harness_repository.admission_lock(
            mission.thread_id, mission.scope.principal
        ) as owned:
            mission = await self._get(mission_id, workflow_scope(user))
            if mission.cancel_requested:
                raise HTTPException(status_code=409, detail="Mission cancellation requested")
            root_run_id = run_id
            if run_id not in mission.run_ids:
                participant = await harness_repository.get(run_id)
                root_run_id = participant.get("root_run_id") if participant else None
                if (
                    not participant
                    or participant.get("thread_id") != mission.thread_id
                    or tuple(
                        participant.get(key)
                        for key in ("owner_name", "role_name", "session_id", "security_version")
                    )
                    != tuple(scope_params(mission.scope))
                ):
                    raise HTTPException(status_code=403, detail="Mission execution binding changed")
            if (
                root_run_id not in mission.run_ids
                or mission.run_bindings.get(root_run_id) != mission.scope
            ):
                raise HTTPException(status_code=403, detail="Mission execution binding changed")
            await self._run(mission, run_id)
            prior = next(
                (
                    item
                    for item in mission.execution_contexts
                    if (item.run_id, item.tool_call_id) == (run_id, tool_call_id)
                ),
                None,
            )
            if prior:
                if prior.context_digest != digest:
                    raise HTTPException(status_code=409, detail="Execution time context collision")
                return prior
            if len(mission.execution_contexts) >= 100:
                raise HTTPException(
                    status_code=422, detail="Mission execution context bound reached"
                )
            mission.execution_contexts.append(pinned)
            await self._save(mission, mission.revision, owned)
            return pinned

    async def record_anchor(
        self,
        mission_id: str,
        semantic: SemanticRef | dict,
        metrics: list[str],
        user: dict,
        *,
        filter_population_fingerprint: str | None = None,
    ) -> Mission:
        anchor = SemanticAnchor(
            semantic=SemanticRef.model_validate(semantic),
            metrics=metrics,
            filter_population_fingerprint=filter_population_fingerprint,
        )
        mission = await self.get(mission_id, user, project=False)
        async with harness_repository.admission_lock(
            mission.thread_id, mission.scope.principal
        ) as owned:
            mission = await self._get(mission_id, workflow_scope(user))
            if mission.cancel_requested:
                raise HTTPException(status_code=409, detail="Mission cancellation requested")
            if anchor in mission.semantic_anchors:
                return mission
            if len(mission.semantic_anchors) >= 32:
                raise HTTPException(status_code=422, detail="Mission semantic anchor bound reached")
            mission.semantic_anchors.append(anchor)
            return await self._save(mission, mission.revision, owned)

    async def record_investigation_requirements(
        self,
        mission_id: str,
        requirements: list[str],
        established: dict,
        user: dict,
    ) -> Mission:
        pending = InvestigationRequirements(required_inputs=requirements, established=established)
        mission = await self.get(mission_id, user, project=False)
        async with harness_repository.admission_lock(
            mission.thread_id,
            mission.scope.principal,
        ) as owned:
            mission = await self._get(mission_id, workflow_scope(user))
            if mission.cancel_requested:
                raise HTTPException(status_code=409, detail="Mission cancellation requested")
            if mission.investigation_requirements == pending:
                return mission
            mission.investigation_requirements = pending
            return await self._save(mission, mission.revision, owned)

    async def refresh(self, initial: Mission, user: dict) -> Mission:
        with studio_operation("mission", "project"):
            return await self._refresh(initial, user)

    async def _refresh(self, initial: Mission, user: dict) -> Mission:
        async with harness_repository.admission_lock(
            initial.thread_id, initial.scope.principal
        ) as owned:
            mission = await self._get(initial.mission_id, workflow_scope(user))
            before = mission.model_dump_json()
            public_before = mission.model_dump(exclude={"projection_cursor"})
            runs = []
            for run_id in mission.run_ids:
                run = await self._run(mission, run_id)
                runs.append(run)
                cursor = mission.projection_cursor.get(run_id, -1)
                if run["smart"]:
                    rows = await db.execute_system(
                        "SELECT session_sequence,run_id,event_type,payload FROM "
                        "NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS WHERE root_run_id=%s "
                        "AND session_sequence>%s ORDER BY session_sequence LIMIT 100",
                        [run_id, cursor],
                    )
                    events = [(r[0], r[1], r[2], _decode(r[3]) or {}) for r in rows["rows"]]
                else:
                    rows = await db.execute_system(
                        "SELECT `sequence`,frame FROM NOVA_SYSTEM.CONFIG_AGENT_RUN_EVENTS "
                        "WHERE run_id=%s AND `sequence`>%s ORDER BY `sequence` LIMIT 100",
                        [run_id, cursor],
                    )
                    events = []
                    for sequence, frame in rows["rows"]:
                        try:
                            kind = frame.split("event: ", 1)[1].split("\n", 1)[0]
                            payload = json.loads(frame.split("data: ", 1)[1])
                        except (IndexError, ValueError, TypeError) as exc:
                            raise HTTPException(
                                status_code=409, detail="Run journal needs reconciliation"
                            ) from exc
                        events.append((sequence, run_id, kind, payload))
                for sequence, source_run_id, kind, payload in events:
                    if int(sequence) <= cursor:
                        raise HTTPException(status_code=409, detail="Run journal order is invalid")
                    project_event(mission, source_run_id, kind, payload)
                    cursor = int(sequence)
                mission.projection_cursor[run_id] = cursor
                run["caught_up"] = cursor >= run["sequence"]
            if runs or mission.cancel_requested:
                active = any(
                    r["status"] not in {"completed", "failed", "cancelled", "interrupted"}
                    for r in runs
                )
                caught_up = all(r["caught_up"] for r in runs)
                if mission.cancel_requested:
                    mission.status = (
                        "cancelled"
                        if mission.cancellation_complete and not active
                        else "cancelling"
                    )
                    if mission.status == "cancelled":
                        for stage in mission.stages:
                            if stage.status != "completed":
                                stage.status = "cancelled"
                elif active or not caught_up:
                    mission.status = "running"
                elif runs[-1]["status"] != "completed":
                    mission.status = "blocked"
                else:
                    mission.status = (
                        "completed"
                        if all(s.status == "completed" for s in mission.stages)
                        else "blocked"
                    )
            if mission.model_dump_json() == before:
                return mission
            return await self._save(
                mission,
                mission.revision,
                owned,
                advance_revision=mission.model_dump(exclude={"projection_cursor"}) != public_before,
            )

    async def link(
        self, mission_id: str, ref: ObjectRef, expected_revision: int, user: dict
    ) -> Mission:
        mission = await self.get(mission_id, user, project=False)
        from app.modules.intelligence.engine import CycleBudget

        budget = CycleBudget()
        tables = {
            "investigation": "investigations",
            "decision": "decisions",
            "action": "actions",
            "outcome": "outcomes",
        }
        for pin in mission.object_refs:
            binding = mission.object_bindings.get(object_binding_key(pin), mission.scope)
            if (binding.principal, binding.active_role) != (
                mission.scope.principal,
                mission.scope.active_role,
            ):
                raise HTTPException(status_code=404, detail="Mission owner scope changed")
            key = (tables[pin.kind], pin.id)
            budget.mission_records[key] = pin.revision
            budget.mission_record_bindings[key] = binding
            if binding not in budget.mission_bindings:
                budget.mission_bindings.append(binding)
        record = await canonical_payload(ref, mission.scope, user, budget=budget)
        if ref.kind == "decision" and record.get("thread_id") not in {None, mission.thread_id}:
            raise HTTPException(status_code=422, detail="Decision belongs to another thread")
        return await self._link_authorized_record(
            mission, ref, record, mission.scope, expected_revision, user,
        )

    async def _link_automatic_investigation(
        self, mission_id: str, comparison_id: str, ref: ObjectRef,
        expected_revision: int, user: dict,
    ) -> Mission:
        from app.modules.intelligence.engine import ChatComparison, intelligence_service

        mission = await self.get(mission_id, user, project=False)
        matches = {}
        bindings = {}
        for binding in [mission.scope, *mission.historical_bindings,
                        *mission.run_bindings.values(), *mission.object_bindings.values()]:
            bindings.setdefault(fingerprint(binding.model_dump(exclude={"session_id"})), binding)
        if len(bindings) > 100:
            raise HTTPException(status_code=409, detail="Mission comparison recovery bound reached")
        for binding in bindings.values():
            row = await intelligence_service.repository.get(
                "comparisons", comparison_id, binding, ChatComparison,
            )
            if row:
                matches[row.id] = row
        if len(matches) != 1:
            raise HTTPException(status_code=409, detail="Automatic comparison unavailable")
        comparison = next(iter(matches.values()))
        if (ref.kind != "investigation" or comparison.investigation_id != ref.id
                or comparison.investigation_revision != ref.revision
                or comparison.status == "pending" or not comparison.automatic_operation_id):
            raise HTTPException(status_code=409, detail="Automatic Investigation linkage changed")
        budget, _ = await intelligence_service._automatic_comparison_budget(
            comparison, mission, comparison.automatic_operation_id, user,
        )
        record = await intelligence_service.get(
            "investigations", ref.id, user, budget=budget, revision=ref.revision,
        )
        return await self._link_authorized_record(
            mission, ref, record.model_dump(mode="json"), record.scope, expected_revision, user,
        )

    async def _link_authorized_record(
        self, mission: Mission, ref: ObjectRef, record: dict, source_scope: Scope,
        expected_revision: int, user: dict,
    ) -> Mission:
        original_binding = mission.current_binding.model_copy(deep=True)
        async with harness_repository.admission_lock(
            mission.thread_id, mission.scope.principal
        ) as owned:
            mission = await self._get(mission.mission_id, Scope.from_user(user))
            if mission.current_binding != original_binding:
                raise HTTPException(status_code=409, detail="Mission execution binding changed")
            if mission.revision != expected_revision:
                raise HTTPException(status_code=409, detail="Mission revision changed; reload")
            if mission.cancel_requested:
                raise HTTPException(status_code=409, detail="Mission cancellation requested")
            if ref in mission.object_refs:
                return mission
            previous = next(
                (r for r in mission.object_refs if (r.kind, r.id) == (ref.kind, ref.id)), None
            )
            if previous is not None and ref.revision < previous.revision:
                raise HTTPException(
                    status_code=409, detail="Canonical revision cannot go backwards"
                )
            key = object_binding_key(ref)
            if key not in mission.object_bindings and len(mission.object_bindings) >= 100:
                raise HTTPException(status_code=422, detail="Mission canonical pin bound reached")
            if previous is not None and previous not in mission.historical_object_refs:
                mission.historical_object_refs.append(previous)
            mission.object_refs = [
                r for r in mission.object_refs if (r.kind, r.id) != (ref.kind, ref.id)
            ]
            mission.object_refs.append(ref)
            if ref.kind == "investigation":
                mission.investigation_requirements = None
            mission.object_bindings[object_binding_key(ref)] = source_scope.model_copy()
            project_object(mission, ref, record)
            result = await self._save(mission, expected_revision, owned)
            await self.audit(result, "LINK")
            return result

    async def cancel(self, mission_id: str, expected_revision: int, user: dict) -> Mission:
        mission = await self.get(mission_id, user, project=False)
        async with harness_repository.admission_lock(
            mission.thread_id, mission.scope.principal
        ) as owned:
            mission = await self._get(mission_id, mission.scope)
            if not mission.cancel_requested and mission.revision != expected_revision:
                raise HTTPException(status_code=409, detail="Mission revision changed; reload")
            if not mission.cancel_requested:
                mission.cancel_requested = True
                mission.status = "cancelling"
                mission = await self._save(mission, expected_revision, owned)
        failures = []
        for run_id in mission.run_ids:
            try:
                await self._stop_run(mission, run_id)
            except Exception as exc:
                failures.append(exc)
        from app.modules.intelligence.action_contracts import ActionOperation
        from app.modules.intelligence.actions import action_service

        for ref in mission.object_refs:
            if ref.kind != "action":
                continue
            try:
                if (
                    mission.object_bindings.get(object_binding_key(ref), mission.scope)
                    != mission.scope
                ):
                    historical = await self.canonical_read(mission_id, ref, user)
                    if historical["status"] in {
                        "executing",
                        "verification_required",
                        "compensating",
                        "compensation_required",
                    }:
                        raise HTTPException(
                            status_code=409,
                            detail="Historical action requires reconciliation before cancellation",
                        )
                    continue
                action = await action_service.get(ref.id, user)
                if action.scope != mission.scope:
                    raise HTTPException(status_code=404, detail="Mission action not found")
                await action_service.cancel(
                    ref.id,
                    ActionOperation(
                        operation_id=f"mission-cancel:{mission.mission_id}",
                        expected_revision=action.revision,
                        thread_id=mission.thread_id,
                    ),
                    user,
                )
            except Exception as exc:
                failures.append(exc)
        await self.audit(mission, "CANCEL", status="FAILED" if failures else "SUCCESS")
        if failures:
            raise failures[0]
        async with harness_repository.admission_lock(
            mission.thread_id, mission.scope.principal
        ) as owned:
            mission = await self._get(mission_id, mission.scope)
            if not mission.cancellation_complete:
                mission.cancellation_complete = True
                mission = await self._save(mission, mission.revision, owned)
        return await self.refresh(mission, user)

    async def _stop_run(self, mission: Mission, run_id: str) -> None:
        run = await self._run(mission, run_id)
        if run["smart"]:
            if run["status"] not in {"completed", "failed", "interrupted"}:
                await harness_repository.cancel_tree(run_id)
                await harness_repository.reconcile_cancelled_children(run_id)
        elif run["status"] == "running":
            await run_journal.finish(run_id, "interrupted")

    async def cancellation_requested(self, mission_id: str, user: dict) -> bool:
        return (await self.get(mission_id, user, project=False)).cancel_requested

    async def project_run(self, run_id: str, user: dict) -> Mission | None:
        """Refresh a linked Mission for the existing direct/Smart streaming producer."""
        if not getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
            return None
        scope = workflow_scope(user)
        rows = await db.execute_system(
            "SELECT thread_id FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS WHERE run_id=%s "
            "AND owner_name=%s AND role_name=%s AND session_id=%s AND security_version=%s",
            [run_id, *scope_params(scope)],
        )
        if len(rows["rows"]) != 1:
            raise HTTPException(status_code=404, detail="Mission run not found")
        thread_id = rows["rows"][0][0]
        await require_thread(thread_id, user)
        result = await db.execute_system(
            "SELECT payload FROM NOVA_SYSTEM.CONFIG_STUDIO_MISSIONS "
            f"WHERE thread_id=%s AND {SCOPE_SQL} ORDER BY created_at DESC LIMIT 50",
            [thread_id, *scope_params(scope)],
        )
        for row in result["rows"]:
            mission = Mission.model_validate(_decode(row[0]))
            if run_id in mission.run_ids:
                return await self.refresh(mission, user)
        return None

    async def stream_update(
        self, run_id: str, user: dict, *, after_revision: int = 0
    ) -> str | None:
        from app.modules.agents.public_projections import public_mission

        mission = await self.project_run(run_id, user)
        if mission is None or mission.revision <= after_revision:
            return None
        return (
            "event: mission_updated\ndata: "
            + json.dumps({"mission": public_mission(mission).model_dump(mode="json")},
                         separators=(",", ":"))
            + "\n\n"
        )

    async def deliver(
        self, mission_id: str, body: DeliverableCreate, user: dict
    ) -> MissionDeliverable:
        mission = await self.get(mission_id, user, project=False)
        deliverable_id = str(
            uuid5(NAMESPACE_URL, f"nova:deliverable:{mission_id}:{body.operation_id}")
        )
        digest = fingerprint(body.model_dump())
        async with harness_repository.admission_lock(
            mission.thread_id, mission.scope.principal
        ) as owned:
            prior = await db.execute_system(
                "SELECT payload,request_digest FROM NOVA_SYSTEM.CONFIG_STUDIO_DELIVERABLES "
                f"WHERE deliverable_id=%s AND {OWNER_SQL}",
                [deliverable_id, mission.scope.principal, mission.scope.active_role],
            )
            if prior["rows"]:
                if prior["rows"][0][1] != digest:
                    raise HTTPException(
                        status_code=409, detail="Deliverable operation id collision"
                    )
                document = MissionDeliverable.model_validate(_decode(prior["rows"][0][0]))
                await self._authorize_deliverable(mission_id, document, user)
                return document
            mission = await self._get(mission_id, mission.scope)
            if mission.revision != body.expected_revision:
                raise HTTPException(status_code=409, detail="Mission revision changed; reload")
            records = []
            for ref in mission.object_refs:
                binding = mission.object_bindings.get(object_binding_key(ref), mission.scope)
                record = (
                    await canonical_payload(ref, mission.scope, user)
                    if binding == mission.scope
                    else await self.canonical_read(mission_id, ref, user)
                )
                records.append((ref, record))
            if not mission.evidence_refs and not any(r.get("evidence") for _, r in records):
                raise HTTPException(status_code=422, detail="A deliverable needs recorded evidence")
            required_kind = {
                "investigation_report": "investigation",
                "scenario_comparison": "decision",
                "outcome_report": "outcome",
            }.get(body.kind)
            compatible = [record for ref, record in records if ref.kind == required_kind]
            if required_kind and (
                not compatible
                or (
                    body.kind == "scenario_comparison"
                    and not any(r.get("options") for r in compatible)
                )
            ):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "deliverable_source_unavailable",
                        "required_kind": required_kind,
                    },
                )
            title = {
                "decision_memo": "Decision memo",
                "action_plan": "Action plan",
                "analysis_summary": "Analysis summary",
                "investigation_report": "Investigation report",
                "scenario_comparison": "Scenario comparison",
                "outcome_report": "Outcome report",
            }[body.kind]
            sources = [_mission_source(mission)]
            sources += [_object_source(ref, record) for ref, record in records]
            sources += [
                _deliverable_source(
                    kind="execution",
                    id=f"{context.run_id}:{context.tool_call_id}",
                    revision=1,
                    fingerprint=context.context_digest,
                    semantic=context.semantic,
                    facts=context.model_dump(
                        mode="json", exclude={"run_id", "tool_call_id", "context_digest"}
                    ),
                )
                for context in mission.execution_contexts
            ]
            lines = [f"# {title}", "", mission.objective, "", "## Recorded work", ""]
            lines += [f"- {s.label}: {s.status}." for s in mission.stages]
            for ref, record in records:
                lines += [
                    "",
                    f"## {ref.kind.title()} {ref.id}",
                    "",
                    f"Revision {ref.revision}; status: {record.get('status', 'unknown')}.",
                ]
                if ref.kind == "investigation":
                    lines += [
                        f"- {h['label']} (causal basis: {h['causal_status']})."
                        for h in record.get("hypotheses", [])
                    ]
                elif ref.kind == "decision":
                    lines += [str(record.get("title", ""))]
                    selected = next(
                        (
                            o
                            for o in record.get("options", [])
                            if o["id"] == record.get("selected_option_id")
                        ),
                        None,
                    )
                    if selected:
                        lines += [f"Selected option: {selected['description']}."]
                    if body.kind == "scenario_comparison":
                        for option in record.get("options", []):
                            lines += [
                                f"- {option['description']}: {option.get('prediction', 'unknown')} "
                                f"{record.get('currency', '')}; "
                                f"method {option.get('method', 'unknown')}; "
                                f"scenario {option.get('scenario_kind', 'unit-economics')} "
                                f"v{option.get('scenario_version', 1)}.",
                            ]
                elif ref.kind == "outcome":
                    lines += [
                        f"Attribution: {record.get('attribution', 'unknown')}.",
                        f"Actual: {record.get('actual', 'unknown')}; "
                        f"predicted: {record.get('predicted', 'unknown')}; "
                        f"completeness: {record.get('completeness', 'unknown')}.",
                    ]
            for context in mission.execution_contexts:
                lines += [
                    "",
                    f"Semantic View {context.semantic.view_id} v{context.semantic.version}; "
                    f"fingerprint {context.semantic.fingerprint}.",
                    f"Plan fingerprint: {context.plan_fingerprint}; timezone: {context.timezone}.",
                ]
                for label, window in (
                    ("Current", context.current_window),
                    ("Baseline", context.baseline_window),
                ):
                    if window:
                        lines += [
                            f"{label}: {window.start.isoformat()} to {window.end.isoformat()}."
                        ]
            lines += ["", "## Evidence references", ""]
            lines += [f"- {r}" for r in mission.evidence_refs]
            if body.kind == "action_plan":
                lines += ["", "## Next steps", ""]
                pending = [s for s in mission.stages if s.status != "completed"]
                lines += [
                    f"- {s.label}: {s.status}; follow the governed workflow." for s in pending
                ]
                if not pending:
                    lines += [
                        "Recorded stages are complete; inspect the outcome "
                        "before proposing changes."
                    ]
            markdown = "\n".join(lines)
            if len(markdown) > 32000:
                raise HTTPException(
                    status_code=422, detail={"code": "deliverable_report_bound_exceeded"}
                )
            deliverable = MissionDeliverable(
                deliverable_id=deliverable_id,
                mission_id=mission_id,
                mission_revision=mission.revision,
                kind=body.kind,
                title=title,
                markdown=markdown,
                evidence_refs=mission.evidence_refs,
                object_refs=mission.object_refs,
                created_at=utc_now(),
                sources=sources,
            )
            await owned()
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CONFIG_STUDIO_DELIVERABLES (deliverable_id,mission_id,"
                "owner_name,role_name,session_id,security_version,"
                "request_digest,payload,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    deliverable_id,
                    mission_id,
                    *scope_params(mission.scope),
                    digest,
                    deliverable.model_dump_json(),
                    _sql_time(deliverable.created_at),
                ],
            )
            await self.audit(mission, "DELIVER")
            return deliverable

    async def deliverables(
        self, mission_id: str, user: dict, *, budget=None
    ) -> list[MissionDeliverable]:
        mission = await self.get(mission_id, user, project=False)
        result = await db.execute_system(
            "SELECT payload FROM NOVA_SYSTEM.CONFIG_STUDIO_DELIVERABLES "
            f"WHERE mission_id=%s AND {OWNER_SQL} ORDER BY created_at DESC LIMIT 100",
            [mission_id, mission.scope.principal, mission.scope.active_role],
        )
        documents = [MissionDeliverable.model_validate(_decode(r[0])) for r in result["rows"]]
        for document in documents:
            await self._authorize_deliverable(mission_id, document, user, budget=budget)
        return documents

    async def _authorize_deliverable(
        self, mission_id: str, document: MissionDeliverable, user: dict, *, budget=None
    ) -> None:
        from app.modules.intelligence.engine import intelligence_service

        for ref in document.object_refs:
            await self.canonical_read(mission_id, ref, user, budget=budget)
        semantics = {
            source.semantic.model_dump_json(): source.semantic
            for source in document.sources
            if source.semantic is not None
        }
        for semantic in semantics.values():
            await intelligence_service.authorize_semantic(semantic, user, budget=budget)

    @staticmethod
    async def audit(mission: Mission, action: str, *, status: str = "SUCCESS") -> None:
        await write_audit_log(
            event_type="STUDIO_MISSION",
            user_name=mission.scope.principal,
            action=action,
            object_type="MISSION",
            object_name=mission.mission_id,
            status=status,
            session_id=mission.scope.session_id,
            active_role=mission.scope.active_role,
            security_context_version=mission.scope.security_context_version,
        )


def _sql_time(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _deliverable_source(**values) -> DeliverableSource:
    try:
        return DeliverableSource(**values)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "deliverable_source_bound_exceeded"}
        ) from exc


def _mission_source(mission: Mission) -> DeliverableSource:
    facts = {
        "objective": mission.objective,
        "status": mission.status,
        "work_intent": mission.work_intent.value,
        "stages": [stage.model_dump(mode="json") for stage in mission.stages],
        "evidence_refs": mission.evidence_refs,
        "object_refs": [ref.model_dump() for ref in mission.object_refs],
        "release_pins": [pin.model_dump() for pin in mission.release_pins],
    }
    return _deliverable_source(
        kind="mission",
        id=mission.mission_id,
        revision=mission.revision,
        fingerprint=fingerprint(facts),
        facts=facts,
    )


def _object_source(ref: ObjectRef, record: dict) -> DeliverableSource:
    fields = {
        "investigation": ("status", "news_id", "news_revision", "hypotheses", "residual", "method"),
        "decision": (
            "status",
            "title",
            "target_metric",
            "baseline",
            "currency",
            "outcome_window",
            "selected_option_id",
            "options",
            "investigation_id",
        ),
        "action": (
            "status",
            "adapter_id",
            "action_type",
            "decision_id",
            "decision_revision",
            "expected_effect",
            "receipt",
            "verification",
        ),
        "outcome": (
            "status",
            "target_metric",
            "currency",
            "window",
            "predicted",
            "actual",
            "completeness",
            "attribution",
            "decision_id",
            "decision_revision",
            "action_ids",
        ),
    }[ref.kind]
    facts = {key: record[key] for key in fields if key in record}
    if ref.kind == "decision":
        facts["options"] = [
            {
                key: value
                for key, value in option.items()
                if key
                in {
                    "id",
                    "description",
                    "scenario_kind",
                    "scenario_version",
                    "prediction",
                    "lower_bound",
                    "upper_bound",
                    "cost",
                    "incremental_gross_profit",
                    "effects",
                    "risk",
                    "feasible",
                    "method",
                    "run_id",
                    "evidence_ids",
                }
            }
            for option in record.get("options", [])
        ]
    facts["evidence_refs"] = [evidence["id"] for evidence in record.get("evidence", [])]
    return _deliverable_source(
        kind=ref.kind,
        id=ref.id,
        revision=ref.revision,
        fingerprint=fingerprint(facts),
        facts=facts,
        semantic=SemanticRef.model_validate(record["semantic"]) if record.get("semantic") else None,
    )


mission_service = MissionService()
