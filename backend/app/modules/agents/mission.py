"""Mission projections over existing journals and canonical Intelligence records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.database import db
from app.modules.agents.access import has_verified_access
from app.modules.agents.harness_repository import harness_repository
from app.modules.agents.identity import SMART_AGENT_IDS
from app.modules.agents.mission_schema import (
    MISSION_DDLS,
    STAGE_LABELS,
    DeliverableCreate,
    Mission,
    MissionCreate,
    MissionDeliverable,
    MissionStage,
    ObjectRef,
    StageKind,
    WorkIntent,
)
from app.modules.agents.repository import agent_repository
from app.modules.agents.run_journal import run_journal
from app.modules.assistant.repository import assistant_repository
from app.modules.intelligence.contracts import Scope, fingerprint, utc_now
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


async def canonical_payload(ref: ObjectRef, scope: Scope) -> dict:
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
            "completed" if all(stage.status == "completed" for stage in mission.stages)
            else "blocked" if any(stage.status == "blocked" for stage in mission.stages)
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

    async def create(self, thread_id: str, body: MissionCreate, user: dict) -> Mission:
        require_workflow()
        await require_thread(thread_id, user)
        scope = workflow_scope(user)
        async with harness_repository.admission_lock(thread_id, scope.principal) as owned:
            result = await db.execute_system(
                "SELECT payload FROM NOVA_SYSTEM.CONFIG_STUDIO_MISSIONS "
                f"WHERE thread_id=%s AND {SCOPE_SQL} ORDER BY created_at DESC, mission_id LIMIT 51",
                [thread_id, *scope_params(scope)],
            )
            prior = [Mission.model_validate(_decode(r[0])) for r in result["rows"]]
            same_op = next((m for m in prior if m.operation_id == body.operation_id), None)
            if same_op:
                if same_op.request_digest != fingerprint(body.model_dump(mode="json")):
                    raise HTTPException(status_code=409, detail="Mission operation id collision")
                return same_op
            if prior and not body.new_mission:
                if prior[0].cancel_requested:
                    raise HTTPException(
                        status_code=409, detail="Start a new Mission after cancellation"
                    )
                return prior[0]
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
                scope=scope,
                objective=body.objective,
                work_intent=body.work_intent,
                status="planned",
                revision=1,
                operation_id=body.operation_id,
                request_digest=fingerprint(body.model_dump(mode="json")),
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
    ) -> Mission | None:
        if not getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
            return None
        missions = await self.list(thread_id, user)
        existing_operation = next((m for m in missions if m.operation_id == operation_id), None)
        if existing_operation is not None:
            if existing_operation.objective != objective:
                raise HTTPException(status_code=409, detail="Mission operation id collision")
            return existing_operation
        linked = next((m for m in missions if operation_id in m.run_ids), None)
        if linked is not None:
            return linked
        if missions and not new_mission:
            if missions[0].cancel_requested:
                raise HTTPException(
                    status_code=409, detail="Start a new Mission after cancellation"
                )
            return missions[0]
        if not explicit and (
            work_intent in {None, WorkIntent.ANSWER}
            or (work_intent == WorkIntent.ANALYZE and len(public_work_steps) < 2)
        ):
            return None
        return await self.create(
            thread_id,
            MissionCreate(
                objective=objective,
                work_intent=work_intent or WorkIntent.INVESTIGATE,
                public_work_steps=list(public_work_steps),
                operation_id=operation_id,
                new_mission=new_mission,
            ),
            user,
        )

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
            mission.projection_cursor[run_id] = -1
            mission.status = "running"
            return await self._save(mission, mission.revision, owned)

    async def _run(self, mission: Mission, run_id: str) -> dict:
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
        if session != mission.scope.session_id or version != mission.scope.security_context_version:
            raise HTTPException(status_code=403, detail="Mission run security context changed")
        return {"status": status, "sequence": int(sequence), "smart": smart}

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
        record = await canonical_payload(ref, mission.scope)
        if ref.kind == "decision" and record.get("thread_id") not in {None, mission.thread_id}:
            raise HTTPException(status_code=422, detail="Decision belongs to another thread")
        async with harness_repository.admission_lock(
            mission.thread_id, mission.scope.principal
        ) as owned:
            mission = await self._get(mission_id, mission.scope)
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
            mission.object_refs = [
                r for r in mission.object_refs if (r.kind, r.id) != (ref.kind, ref.id)
            ]
            mission.object_refs.append(ref)
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
        mission = await self.project_run(run_id, user)
        if mission is None or mission.revision <= after_revision:
            return None
        return (
            "event: mission_updated\ndata: "
            + json.dumps({"mission": mission.model_dump(mode="json")}, separators=(",", ":"))
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
                f"WHERE deliverable_id=%s AND {SCOPE_SQL}",
                [deliverable_id, *scope_params(mission.scope)],
            )
            if prior["rows"]:
                if prior["rows"][0][1] != digest:
                    raise HTTPException(
                        status_code=409, detail="Deliverable operation id collision"
                    )
                return MissionDeliverable.model_validate(_decode(prior["rows"][0][0]))
            mission = await self._get(mission_id, mission.scope)
            if mission.revision != body.expected_revision:
                raise HTTPException(status_code=409, detail="Mission revision changed; reload")
            records = [
                (ref, await canonical_payload(ref, mission.scope)) for ref in mission.object_refs
            ]
            if not mission.evidence_refs and not any(r.get("evidence") for _, r in records):
                raise HTTPException(status_code=422, detail="A deliverable needs recorded evidence")
            title = "Decision memo" if body.kind == "decision_memo" else "Action plan"
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
                elif ref.kind == "outcome":
                    lines += [f"Attribution: {record.get('attribution', 'unknown')}."]
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
            deliverable = MissionDeliverable(
                deliverable_id=deliverable_id,
                mission_id=mission_id,
                mission_revision=mission.revision,
                kind=body.kind,
                title=title,
                markdown="\n".join(lines),
                evidence_refs=mission.evidence_refs,
                object_refs=mission.object_refs,
                created_at=utc_now(),
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

    async def deliverables(self, mission_id: str, user: dict) -> list[MissionDeliverable]:
        mission = await self.get(mission_id, user, project=False)
        result = await db.execute_system(
            "SELECT payload FROM NOVA_SYSTEM.CONFIG_STUDIO_DELIVERABLES "
            f"WHERE mission_id=%s AND {SCOPE_SQL} ORDER BY created_at DESC LIMIT 100",
            [mission_id, *scope_params(mission.scope)],
        )
        return [MissionDeliverable.model_validate(_decode(r[0])) for r in result["rows"]]

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


mission_service = MissionService()
