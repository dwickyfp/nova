from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from app.common.audit import write_audit_log
from app.common.identifiers import check_identifier
from app.core.redis import session_store
from app.modules.access_control.security_context import SecurityContext
from app.modules.query_autopilot.actions import action_sql
from app.modules.query_autopilot.aggregation import aggregate
from app.modules.query_autopilot.correctness import prove_result
from app.modules.query_autopilot.engine_state import (
    ObjectState,
    baseline_for_sample,
    compensation_sql,
    inspect_object,
    object_name,
    resource_group_state,
    wait_ready,
)
from app.modules.query_autopilot.evidence import EvidenceCollector, plan_diff, plan_uses_object
from app.modules.query_autopilot.experiments import Experiment, Measurement
from app.modules.query_autopilot.jobs import exclusive
from app.modules.query_autopilot.models import (
    ActionKind,
    Availability,
    Candidate,
    Enrollment,
    Evidence,
    Mode,
    Policy,
    Risk,
    Scope,
    State,
    digest,
    utcnow,
)
from app.modules.query_autopilot.payloads import PayloadStore
from app.modules.query_autopilot.policy import (
    application_block,
    can_compensate,
    classify,
    transition,
)
from app.modules.query_autopilot.repository import repository
from app.modules.query_autopilot.runtime import (
    AuthorizationUnavailable,
    AuthorizedSQL,
    EvidenceUnavailable,
    EvidenceUnsupported,
    current_policy_revision,
    validate_policy_revision,
    worker_sql,
)
from app.sql_frontend.autopilot import (
    clone_materialized_view,
    deterministic_order,
    map_snapshot,
    quote_path,
)


class Conflict(ValueError):
    pass


def public_record(record: dict) -> dict:
    # Session handles and opaque payload locations belong to the worker, not UI state.
    if (
        "availability" in record
        and record.get("expires_at")
        and datetime.fromisoformat(record["expires_at"]) <= utcnow()
    ):
        record = {**record, "availability": "expired", "reason": "retention_expired"}

    def redact(value):
        if isinstance(value, dict):
            return {
                key: redact(item)
                for key, item in value.items()
                if key
                not in {
                    "session_id",
                    "payload_ref",
                    "actor_session_id",
                    "encrypted_password",
                    "api_key",
                    "access_token",
                    "credential",
                    "credentials",
                }
            }
        if isinstance(value, (list, tuple)):
            return [redact(item) for item in value]
        return value

    return redact(record)


class AutopilotService:
    def __init__(
        self,
        repo=repository,
        *,
        client=None,
        sql: AuthorizedSQL | None = None,
        payloads: PayloadStore | None = None,
    ):
        self.repo = repo
        self._client = client
        self.sql = sql or worker_sql()
        self.payloads = payloads or PayloadStore()

    @property
    def client(self):
        value = self._client or session_store._redis
        if value is None:
            raise ValueError("coordination_unavailable")
        return value

    async def policy(self) -> Policy:
        return Policy.model_validate(await self.repo.get("policies", "default") or {})

    async def audit(
        self,
        user: dict,
        action: str,
        identifier: str,
        *,
        status: str = "SUCCESS",
        reason: str | None = None,
    ) -> None:
        await write_audit_log(
            event_type="QUERY_AUTOPILOT",
            user_name=user["username"],
            action=action,
            object_type="QUERY_AUTOPILOT",
            object_name=identifier,
            status=status,
            error_message=reason,
            active_role=user.get("active_role"),
            security_context_version=user.get("security_context_version"),
            session_id=user.get("session_id"),
        )

    async def save_policy(self, policy: Policy, user: dict) -> Policy:
        async with exclusive(self.client, ("policy:default",)):
            current = await self.policy()
            if policy.version != current.version:
                raise Conflict("policy_version_changed")
            updated = policy.model_copy(update={"id": "default", "version": current.version + 1})
            await self.repo.put(
                "policies", "default", updated.model_dump(mode="json"), version=updated.version
            )
            await self.audit(user, "UPDATE_POLICY", "default")
            return updated

    async def save_enrollment(self, value: Enrollment, user: dict) -> Enrollment:
        async with exclusive(self.client, ("enrollment:" + value.id,)):
            current = await self.repo.get("enrollments", value.id)
            if current and value.version != current["version"]:
                raise Conflict("enrollment_version_changed")
            context = SecurityContext.from_session(user, database=value.scope.database)
            revision = await current_policy_revision()
            value = value.model_copy(
                update={"scope": value.scope.model_copy(update={"policy_revision": revision})}
            )
            scope = Scope(
                principal=context.principal,
                active_role=context.active_role,
                security_context_version=context.security_context_version,
                database=value.scope.database,
                policy_revision=revision,
            )
            async with self.sql.connection(scope, session_id=user["session_id"]) as connection:
                for source, target in value.table_mapping.items():
                    path, mapped = quote_path(source), quote_path(target)
                    if not mapped.startswith(
                        f"`{check_identifier(value.sandbox_database, field='sandbox')}`."
                    ):
                        raise ValueError("mapping_outside_sandbox")
                    for table in (path, mapped):
                        await self.sql.execute(
                            f"SELECT * FROM {table} WHERE FALSE",
                            scope,
                            connection=connection,
                            category="diagnostic",
                            max_rows=1,
                        )
            updated = value.model_copy(update={"version": value.version + 1 if current else 1})
            await self.repo.put(
                "enrollments",
                value.id,
                updated.model_dump(mode="json"),
                cohort_id=value.scope.cohort_id,
                version=updated.version,
            )
            await self.audit(user, "ENROLL", value.id)
            return updated

    async def save_candidate(self, candidate: Candidate, user: dict) -> Candidate:
        enrollment_data = await self.repo.get("enrollments", candidate.enrollment_id)
        if not enrollment_data:
            raise ValueError("enrollment_not_found")
        enrollment = Enrollment.model_validate(enrollment_data)
        policy = await self.policy()
        if (
            candidate.scope != enrollment.scope
            or candidate.enrollment_version != enrollment.version
            or candidate.policy_version != policy.version
        ):
            raise Conflict("candidate_context_changed")
        if (
            candidate.kind == "RESOURCE_GROUP"
            and candidate.parameters.get("resource_group") != enrollment.production_resource_group
        ):
            raise ValueError("resource_group_not_enrolled")
        if not set(candidate.targets).issubset(enrollment.table_mapping):
            raise ValueError("target_not_enrolled")
        if (
            candidate.state != State.PROPOSED
            or candidate.version != 1
            or candidate.approved_by
            or candidate.approval_digest
            or candidate.experiment_id
            or candidate.owned_object
        ):
            raise ValueError("new_candidate_must_be_unapproved")
        if candidate.kind not in {
            "MATERIALIZED_VIEW",
            "PLAN_BASELINE",
            "NATIVE_FEEDBACK",
            "PARTITION",
            "SORT_KEY",
            "BUCKETING",
            "APPLICATION_SQL",
            "DESTRUCTIVE",
            "SECURITY_POLICY",
        }:
            action_sql(candidate)
        elif candidate.kind in {"PLAN_BASELINE", "NATIVE_FEEDBACK"} and candidate.parameters:
            raise ValueError("unexpected_candidate_parameters")
        elif candidate.kind == "MATERIALIZED_VIEW":
            if set(candidate.parameters) - {"name"}:
                raise ValueError("unexpected_candidate_parameters")
            if "name" in candidate.parameters:
                name = check_identifier(candidate.parameters["name"], field="materialized view")
                if not name.startswith("nova_ap_"):
                    raise ValueError("autopilot_owned_object_required")
        candidate = candidate.model_copy(
            update={"evidence_digest": await self.evidence_digest(candidate)}
        )
        if not await self.evidence_current(candidate):
            raise ValueError("evidence_unavailable_or_expired")
        async with exclusive(self.client, ("candidate:" + candidate.id,)):
            if await self.repo.get("opportunities", candidate.id):
                raise Conflict("candidate_exists")
            await self.repo.put(
                "opportunities",
                candidate.id,
                candidate.model_dump(mode="json"),
                family_id=candidate.family_id,
                cohort_id=candidate.scope.cohort_id,
                state=candidate.state,
            )
            await self.audit(user, "PROPOSE", candidate.id)
        return candidate

    async def mutate(
        self, candidate_id: str, operation: str, *, version: int, idempotency_key: str, user: dict
    ) -> dict:
        identifier = digest(
            [
                user["username"],
                user["active_role"],
                user["security_context_version"],
                operation,
                idempotency_key,
            ]
        )
        request_hash = digest([candidate_id, version, operation])
        async with exclusive(
            self.client, ("candidate:" + candidate_id, "idempotency:" + identifier)
        ):
            existing = await self.repo.get("jobs", identifier)
            if existing:
                if existing["request_hash"] != request_hash:
                    raise Conflict("idempotency_key_reused_with_different_request")
                return public_record(existing)
            raw = await self.repo.get("opportunities", candidate_id)
            if not raw:
                raise ValueError("candidate_not_found")
            candidate = Candidate.model_validate(raw)
            if candidate.version != version:
                raise Conflict("candidate_version_changed")
            expired_approval = (
                operation == "experiment"
                and candidate.state == State.APPROVED
                and (
                    candidate.approval_expires_at is None
                    or candidate.approval_expires_at <= utcnow()
                )
            )
            if expired_approval:
                candidate = transition(
                    candidate, State.BLOCKED, reason="approval_expired"
                ).model_copy(
                    update={
                        "approved_by": None,
                        "approval_digest": None,
                        "approval_expires_at": None,
                    }
                )
            if operation == "experiment" and candidate.state not in {
                State.PROPOSED,
                State.INCONCLUSIVE,
                State.BLOCKED,
            }:
                raise Conflict("candidate_not_ready_for_experiment")
            if operation == "approve":
                if candidate.state != State.READY_APPROVAL:
                    raise Conflict("candidate_not_ready_for_approval")
                enrollment = Enrollment.model_validate(
                    await self.repo.get("enrollments", candidate.enrollment_id)
                )
                policy = await self.policy()
                if (
                    candidate.policy_version != policy.version
                    or candidate.enrollment_version != enrollment.version
                    or not await self.evidence_current(candidate)
                ):
                    raise Conflict("approval_evidence_or_policy_changed")
                candidate = transition(candidate, State.APPROVED).model_copy(
                    update={
                        "approved_by": user["username"],
                        "approval_digest": candidate.binding,
                        "approval_expires_at": utcnow() + timedelta(hours=24),
                    }
                )
            elif operation == "reject":
                candidate = transition(candidate, State.REJECTED)
            elif operation == "apply" and candidate.state not in {State.APPROVED, State.READY_AUTO}:
                raise Conflict("candidate_not_ready_to_apply")
            if operation not in {"experiment", "approve", "reject", "apply"}:
                raise ValueError("unsupported_operation")
            immediate = operation in {"approve", "reject"}
            job = {
                "id": identifier,
                "request_hash": request_hash,
                "candidate_id": candidate_id,
                "candidate_version": version,
                "candidate_binding": candidate.binding,
                "kind": operation,
                "version": 1,
                "state": "RUNNING" if immediate else "QUEUED",
                "started_at": utcnow().isoformat(),
                "actor": user["username"],
                "actor_role": user["active_role"],
                "actor_version": user["security_context_version"],
                "actor_session_id": user["session_id"],
                "created_at": utcnow().isoformat(),
            }
            await self.repo.put("jobs", identifier, job, state=job["state"], insert_only=True)
            if expired_approval:
                await self._candidate(candidate)
            if immediate:
                await self._candidate(candidate)
                job = {**job, "state": "COMPLETED", "version": 2}
                await self.repo.put(
                    "jobs", identifier, job, state="COMPLETED", version=2, expected_version=1
                )
            await self.audit(user, operation.upper(), candidate_id)
            return public_record(job)

    async def _candidate(self, candidate: Candidate) -> None:
        await self.repo.put(
            "opportunities",
            candidate.id,
            candidate.model_dump(mode="json"),
            family_id=candidate.family_id,
            cohort_id=candidate.scope.cohort_id,
            version=candidate.version,
            state=candidate.state,
        )

    async def bound_evidence_ids(self, candidate: Candidate) -> tuple[str, ...]:
        identifiers = candidate.evidence_ids
        if candidate.kind in {"MATERIALIZED_VIEW", "REFRESH_POLICY"}:
            enrollment = await self.repo.get("enrollments", candidate.enrollment_id)
            reference = (enrollment or {}).get("ranger_acceptance_ref")
            if reference and reference not in identifiers:
                identifiers = (*identifiers, reference)
        return identifiers

    async def evidence_digest(self, candidate: Candidate) -> str:
        records = [
            await self.repo.get("evidence", identifier)
            for identifier in await self.bound_evidence_ids(candidate)
        ]
        return digest(records)

    async def evidence_current(self, candidate: Candidate) -> bool:
        try:
            await validate_policy_revision(candidate.scope)
        except AuthorizationUnavailable:
            return False
        if not candidate.evidence_ids:
            return False
        for identifier in await self.bound_evidence_ids(candidate):
            value = await self.repo.get("evidence", identifier)
            if not value:
                return False
            evidence = Evidence.model_validate(value)
            if (
                evidence.family_id != candidate.family_id
                or evidence.cohort_id != candidate.scope.cohort_id
                or evidence.effective_availability(utcnow()) != Availability.AVAILABLE
            ):
                return False
        return candidate.evidence_digest == await self.evidence_digest(candidate)

    async def sample_evidence(
        self, candidate: Candidate, *, prefer_profile: bool = False
    ) -> Evidence:
        after = ""
        selected = None
        while True:
            values = await self.repo.page(
                "evidence",
                family_id=candidate.family_id,
                cohort_id=candidate.scope.cohort_id,
                after=after,
                limit=100,
            )
            if not values:
                if selected is None:
                    raise ValueError("opt_in_replay_sample_unavailable_or_expired")
                return selected
            for value in values:
                evidence = Evidence.model_validate(value)
                if (
                    evidence.kind == "replay_sample"
                    and evidence.effective_availability(utcnow()) == Availability.AVAILABLE
                    and evidence.payload_ref
                    and evidence.expires_at
                ):
                    rank = (
                        prefer_profile and bool(evidence.summary.get("profile_requested")),
                        evidence.collected_at,
                    )
                    previous = (
                        (
                            prefer_profile and bool(selected.summary.get("profile_requested")),
                            selected.collected_at,
                        )
                        if selected
                        else None
                    )
                    if previous is None or rank > previous:
                        selected = evidence
            after = values[-1]["id"]

    async def read_sample(self, candidate: Candidate, evidence: Evidence) -> str:
        if (
            evidence.family_id != candidate.family_id
            or evidence.cohort_id != candidate.scope.cohort_id
            or evidence.kind != "replay_sample"
            or evidence.effective_availability(utcnow()) != Availability.AVAILABLE
            or not evidence.payload_ref
            or not evidence.expires_at
        ):
            raise ValueError("bound_replay_sample_unavailable")
        return await self.payloads.get(
            evidence.payload_ref, expires_at=evidence.expires_at, now=utcnow()
        )

    async def sample(self, candidate: Candidate) -> str:
        return await self.read_sample(candidate, await self.sample_evidence(candidate))

    async def run_operation(self, job: dict) -> dict:
        kind = job["kind"]
        if kind == "aggregate":
            await self.enrich()
            result = await aggregate(self.repo)
            await self.propose_detected()
            return result
        if kind == "cleanup":
            return await self.cleanup()
        raw = await self.repo.get("opportunities", job["candidate_id"])
        candidate = Candidate.model_validate(raw)
        enrollment = Enrollment.model_validate(
            await self.repo.get("enrollments", candidate.enrollment_id)
        )
        policy = await self.policy()
        async with exclusive(
            self.client,
            (
                "candidate:" + candidate.id,
                *("object:" + t for t in candidate.targets),
                "sandbox:" + enrollment.sandbox_database,
                "resource-group:" + enrollment.budget.resource_group,
                *(
                    ("mv:" + candidate.scope.database + ":" + object_name(candidate),)
                    if candidate.kind == "REFRESH_POLICY"
                    else ()
                ),
                *(
                    ("resource-group:" + enrollment.production_resource_group,)
                    if candidate.kind == "RESOURCE_GROUP" and enrollment.production_resource_group
                    else ()
                ),
            ),
        ):
            candidate = Candidate.model_validate(await self.repo.get("opportunities", candidate.id))
            if (
                candidate.version != job["candidate_version"]
                or candidate.binding != job["candidate_binding"]
            ):
                return {"state": "BLOCKED", "reason": "candidate_changed"}
            if kind == "experiment":
                return await self.run_experiment(candidate, enrollment, policy, job)
            if kind == "apply":
                prior = await self.repo.get("actions", job["id"])
                if prior and prior.get("state") == "APPLIED":
                    return {"state": "COMPLETED", "reason": "application_already_recorded"}
                return await self.apply(candidate, enrollment, policy, job)
            if kind == "verify":
                return await self.verify(candidate, enrollment, policy, job)
        return {"state": "BLOCKED", "reason": "unsupported_operation"}

    async def discovery_batch(self, kind: str, *, limit: int) -> list[tuple[Enrollment, dict]]:
        """Resume bounded discovery across both enrollment and family boundaries."""
        identifier = "discovery:" + kind
        cursor = await self.repo.get("jobs", identifier) or {}
        enrollment_id = cursor.get("enrollment_id", "")
        enrollment_after = cursor.get("enrollment_after", "")
        family_after = cursor.get("family_after", "")
        result: list[tuple[Enrollment, dict]] = []
        for _ in range(20):
            raw = await self.repo.get("enrollments", enrollment_id) if enrollment_id else None
            if raw is None:
                rows = await self.repo.page("enrollments", after=enrollment_after, limit=1)
                if not rows:
                    enrollment_id = enrollment_after = family_after = ""
                    break
                raw = rows[0]
                enrollment_id, family_after = raw["id"], ""
            enrollment = Enrollment.model_validate(raw)
            if enrollment.enabled and (kind != "enrich" or enrollment.replay_opt_in):
                families = await self.repo.page(
                    "families",
                    cohort_id=enrollment.scope.cohort_id,
                    after=family_after,
                    limit=limit - len(result),
                )
                result.extend((enrollment, family) for family in families)
                if families:
                    family_after = families[-1]["id"]
                if len(result) >= limit:
                    break
            enrollment_after, enrollment_id, family_after = enrollment_id, "", ""
        await self.repo.put(
            "jobs",
            identifier,
            {
                "id": identifier,
                "state": "CHECKPOINT",
                "enrollment_id": enrollment_id,
                "enrollment_after": enrollment_after,
                "family_after": family_after,
            },
            state="CHECKPOINT",
        )
        return result

    async def enrich(self) -> None:
        # A bounded sweep lets the next scheduler tick make progress without
        # turning every user statement into a diagnostic replay.
        for enrollment, family in await self.discovery_batch("enrich", limit=4):
            checkpoint_id = "enrichment:" + family["id"]
            checkpoint = await self.repo.get("jobs", checkpoint_id)
            if checkpoint and datetime.fromisoformat(checkpoint["through"]) > utcnow() - timedelta(
                minutes=5
            ):
                continue
            placeholder = Candidate(
                id="enrichment",
                family_id=family["family_id"],
                scope=enrollment.scope,
                kind=ActionKind.STATISTICS,
                targets=tuple(enrollment.table_mapping),
                evidence_ids=(),
                enrollment_id=enrollment.id,
                enrollment_version=enrollment.version,
                policy_version=(await self.policy()).version,
            )
            try:
                selected = await self.sample_evidence(placeholder, prefer_profile=True)
                sample = await self.read_sample(placeholder, selected)
                async with self.sql.connection(enrollment.scope) as connection:
                    await self.sql.execute(
                        "EXPLAIN " + sample,
                        enrollment.scope,
                        connection=connection,
                        category="diagnostic",
                    )
                    capture = EvidenceCollector(self.sql, self.repo, self.payloads)
                    await capture.capture(
                        family_id=placeholder.family_id,
                        scope=enrollment.scope,
                        kind="plan",
                        statement="EXPLAIN COSTS " + sample,
                        connection=connection,
                        parameter_digest=selected.summary.get("parameter_digest"),
                    )
                    if selected.summary.get("profile_requested"):
                        for query_id in selected.query_ids[-1:]:
                            await capture.profile(
                                placeholder.family_id,
                                enrollment.scope,
                                query_id,
                                connection,
                                observed_at=selected.collected_at,
                            )
                            await capture.analyze_profile(
                                placeholder.family_id,
                                enrollment.scope,
                                query_id,
                                connection,
                                observed_at=selected.collected_at,
                            )
                            await capture.logs(
                                placeholder.family_id, enrollment.scope, query_id, connection
                            )
                    for table in enrollment.table_mapping:
                        await capture.statistics(
                            placeholder.family_id, enrollment.scope, table, connection
                        )
                    if enrollment.production_resource_group:
                        await capture.resource(
                            placeholder.family_id,
                            enrollment.scope,
                            enrollment.production_resource_group,
                            connection,
                        )
                    from app.sql_frontend.autopilot import materialized_view_shape

                    mv = materialized_view_shape(sample)
                    # Exact canonical shapes have compatible grouping and
                    # predicates. Different shapes require separate proof.
                    replay_records = await self.repo.page(
                        "evidence",
                        family_id=placeholder.family_id,
                        cohort_id=enrollment.scope.cohort_id,
                        limit=1000,
                    )
                    compatible = len(
                        {
                            item["summary"]["parameter_digest"]
                            for item in replay_records
                            if item.get("kind") == "replay_sample"
                            and item.get("summary", {}).get("parameter_digest")
                            and Evidence.model_validate(item).effective_availability(utcnow())
                            == Availability.AVAILABLE
                        }
                    )
                    record = Evidence(
                        id=str(uuid4()),
                        family_id=placeholder.family_id,
                        cohort_id=enrollment.scope.cohort_id,
                        kind="aggregate_shape",
                        availability=Availability.AVAILABLE,
                        source="central_sql_frontend",
                        expires_at=utcnow() + timedelta(hours=24),
                        summary={
                            "facts": {
                                "mv_eligible": mv.eligible,
                                "compatible_aggregates": compatible,
                            },
                            "reason": mv.reason,
                        },
                    )
                    await self.repo.put(
                        "evidence",
                        record.id,
                        record.model_dump(mode="json"),
                        family_id=record.family_id,
                        cohort_id=record.cohort_id,
                    )
            except Exception as exc:
                record = Evidence(
                    id=str(uuid4()),
                    family_id=placeholder.family_id,
                    cohort_id=enrollment.scope.cohort_id,
                    kind="enrichment",
                    availability=Availability.UNAUTHORIZED
                    if isinstance(exc, AuthorizationUnavailable)
                    else Availability.UNAVAILABLE,
                    source="autopilot",
                    reason="identity_sample_or_engine_unavailable",
                    expires_at=utcnow() + timedelta(hours=24),
                )
                await self.repo.put(
                    "evidence",
                    record.id,
                    record.model_dump(mode="json"),
                    family_id=record.family_id,
                    cohort_id=record.cohort_id,
                )
            await self.repo.put(
                "jobs",
                checkpoint_id,
                {"id": checkpoint_id, "state": "CHECKPOINT", "through": utcnow().isoformat()},
                state="CHECKPOINT",
            )

    async def propose_detected(self) -> None:
        policy = await self.policy()
        for enrollment, family in await self.discovery_batch("propose", limit=100):
            for finding in family.get("findings", []):
                kind = {
                    "statistics": "STATISTICS",
                    "materialized_view": "MATERIALIZED_VIEW",
                }.get(finding["detector"])
                if kind is None or not finding["evidence_ids"]:
                    continue
                identifier = digest([family["id"], kind, enrollment.version, policy.version])
                if await self.repo.get("opportunities", identifier):
                    continue
                candidate = Candidate(
                    id=identifier,
                    family_id=family["family_id"],
                    scope=enrollment.scope,
                    kind=ActionKind(kind),
                    targets=tuple(
                        table
                        for table in enrollment.table_mapping
                        if table in family.get("tables", ())
                        or table.split(".")[-1] in family.get("tables", ())
                    ),
                    evidence_ids=tuple(finding["evidence_ids"]),
                    enrollment_id=enrollment.id,
                    enrollment_version=enrollment.version,
                    policy_version=policy.version,
                )
                candidate = candidate.model_copy(
                    update={"evidence_digest": await self.evidence_digest(candidate)}
                )
                await self._candidate(candidate)
                if policy.mode != Mode.OBSERVE and enrollment.replay_opt_in:
                    operation_id = "experiment:" + candidate.id
                    await self.repo.put(
                        "jobs",
                        operation_id,
                        {
                            "id": operation_id,
                            "kind": "experiment",
                            "state": "QUEUED",
                            "version": 1,
                            "candidate_id": candidate.id,
                            "candidate_version": candidate.version,
                            "candidate_binding": candidate.binding,
                            "automatic": True,
                            "actor": candidate.scope.principal,
                            "actor_role": candidate.scope.active_role,
                            "actor_version": candidate.scope.security_context_version,
                            "actor_session_id": None,
                        },
                        state="QUEUED",
                        insert_only=True,
                    )

    async def run_experiment(
        self, candidate: Candidate, enrollment: Enrollment, policy: Policy, job: dict
    ) -> dict:
        if (
            policy.mode == Mode.OBSERVE
            or candidate.policy_version != policy.version
            or candidate.enrollment_version != enrollment.version
        ):
            return {"state": "BLOCKED", "reason": "mode_policy_or_enrollment_changed"}
        if not await self.evidence_current(candidate):
            return {"state": "BLOCKED", "reason": "evidence_unavailable_or_expired"}
        optional = {"PLAN_BASELINE": "sql_plan_manager", "NATIVE_FEEDBACK": "plan_advisor"}.get(
            candidate.kind
        )
        if optional and not getattr(await self.sql.capabilities(), optional):
            return {"state": "BLOCKED", "reason": "optional_engine_capability_unsupported"}
        if classify(candidate, enrollment, policy) in {Risk.RECOMMEND_ONLY, Risk.PROHIBITED}:
            return {"state": "BLOCKED", "reason": "action_classification_does_not_allow_execution"}
        if not enrollment.snapshot_frozen or not enrollment.enabled or not enrollment.replay_opt_in:
            return {"state": "BLOCKED", "reason": "immutable_opt_in_snapshot_required"}
        if candidate.state in {State.BLOCKED, State.INCONCLUSIVE}:
            candidate = transition(candidate, State.PROPOSED)
        candidate = transition(candidate, State.VALIDATING).model_copy(
            update={
                "version": candidate.version + 1,
                "experiment_id": None,
                "experiment_digest": None,
                "approved_by": None,
                "approval_digest": None,
                "approval_expires_at": None,
            }
        )
        await self._candidate(candidate)
        try:
            sample_evidence = await self.sample_evidence(candidate)
            original = await self.read_sample(candidate, sample_evidence)
            application_definition_digest = digest(action_sql(candidate, sample_sql=original))
            replay = map_snapshot(
                original,
                enrollment.table_mapping,
                candidate.scope.database,
                enrollment.sandbox_database,
            )
            if not enrollment.control_families:
                raise ValueError("untargeted_control_workload_required")
            control_candidate = candidate.model_copy(
                update={"family_id": enrollment.control_families[0]}
            )
            control = map_snapshot(
                await self.sample(control_candidate),
                enrollment.table_mapping,
                candidate.scope.database,
                enrollment.sandbox_database,
            )
            sandbox = Scope(
                principal=enrollment.execution_principal,
                active_role=enrollment.execution_role,
                security_context_version=enrollment.version,
                database=enrollment.sandbox_database,
                policy_revision=candidate.scope.policy_revision,
            )
            targets = tuple(enrollment.table_mapping[t] for t in candidate.targets)
            trial_candidate = candidate
            refresh_source = None
            resource_source = None
            resource_budget = None
            if candidate.kind == "RESOURCE_GROUP":
                trial_group = "nova_ap_" + digest([candidate.id, job["id"]])[:24]
                trial_candidate = candidate.model_copy(
                    update={
                        "owned_object": "sandbox-resource-group:" + trial_group,
                        "parameters": {
                            **candidate.parameters,
                            "resource_group": trial_group,
                        },
                    }
                )
            if candidate.kind == "REFRESH_POLICY":
                trial_candidate = candidate.model_copy(
                    update={
                        "parameters": {
                            **candidate.parameters,
                            "name": "nova_ap_" + digest([candidate.id, job["id"]])[:24],
                        },
                    }
                )
            statements = action_sql(trial_candidate, targets=targets, sample_sql=replay)
            async with (
                self.sql.connection(candidate.scope) as source_conn,
                self.sql.connection(sandbox) as connection,
            ):
                group = check_identifier(enrollment.budget.resource_group, field="resource group")
                if candidate.kind == "RESOURCE_GROUP":
                    from app.modules.query_autopilot.resource_trial import (
                        trial_group_statement,
                        validate_resource_baseline,
                    )

                    inventory = await self.sql.execute(
                        "SHOW RESOURCE GROUPS ALL",
                        sandbox,
                        connection=connection,
                        category="diagnostic",
                    )
                    production_inventory = await self.sql.execute(
                        "SHOW RESOURCE GROUPS ALL", candidate.scope,
                        connection=source_conn, category="diagnostic",
                    )
                    resource_source = validate_resource_baseline(
                        production_inventory, cast(str, enrollment.production_resource_group),
                        inventory, group,
                    )
                    resource_budget = resource_group_state(inventory, group)
                    statements = (
                        trial_group_statement(
                            inventory, group, trial_group, candidate.parameters["properties"]
                        ),
                    )
                for setting in (
                    f"SET resource_group = '{group}'",
                    f"SET query_mem_limit = {enrollment.budget.max_memory_bytes}",
                    f"SET query_timeout = {enrollment.budget.timeout_seconds}",
                    "SET enable_profile = true",
                ):
                    await self.sql.execute(
                        setting, sandbox, connection=connection, category="experiment"
                    )
                evidence = EvidenceCollector(self.sql, self.repo, self.payloads)
                plans = {}
                trial_ownership: dict[str, Any] = {}
                if candidate.kind == "REFRESH_POLICY":
                    await self.sql.execute(
                        "EXPLAIN " + original,
                        candidate.scope,
                        connection=source_conn,
                        category="diagnostic",
                    )
                    refresh_source = await self.owned_refresh_object(
                        candidate, candidate.scope, source_conn
                    )
                    definition = await self.sql.execute(
                        f"SHOW CREATE MATERIALIZED VIEW {quote_path(candidate.scope.database)}."
                        f"`{object_name(candidate)}`",
                        candidate.scope,
                        connection=source_conn,
                        category="diagnostic",
                    )
                    if (
                        definition.truncated
                        or len(definition.rows) != 1
                        or len(definition.rows[0]) < 2
                    ):
                        raise ValueError("materialized_view_definition_unavailable")
                    create = clone_materialized_view(
                        definition.rows[0][1],
                        object_name(trial_candidate),
                        enrollment.table_mapping,
                        candidate.scope.database,
                        enrollment.sandbox_database,
                    )
                    inspection = trial_candidate.model_copy(
                        update={
                            "kind": ActionKind.MATERIALIZED_VIEW,
                            "targets": targets,
                            "parameters": {"name": object_name(trial_candidate)},
                        }
                    )
                    if (await inspect_object(self.sql, inspection, sandbox, connection)).exists:
                        raise ValueError("sandbox_object_already_exists_requires_reconciliation")
                    setup_intent = {
                        "id": job["id"],
                        "candidate_id": candidate.id,
                        "state": "APPLYING",
                        "target": "sandbox",
                        "kind": candidate.kind,
                        "enrollment_binding": digest(enrollment.model_dump(mode="json")),
                        "refresh_source": asdict(refresh_source),
                        "statement_digests": [digest(create)],
                    }
                    await self.repo.put(
                        "experiments",
                        job["id"],
                        setup_intent,
                        family_id=candidate.family_id,
                        cohort_id=candidate.scope.cohort_id,
                        state="APPLYING",
                    )
                    await self.sql.execute(
                        create, sandbox, connection=connection, category="experiment"
                    )
                    await self.sql.execute(
                        f"REFRESH MATERIALIZED VIEW `{object_name(inspection)}` WITH SYNC MODE",
                        sandbox,
                        connection=connection,
                        category="experiment",
                    )
                    owned = await wait_ready(self.sql, inspection, sandbox, connection)
                    trial_ownership.update(candidate=inspection, state=owned)
                    await self.repo.put(
                        "experiments",
                        job["id"],
                        {
                            **setup_intent,
                            "state": "APPLIED",
                            "owned_object": asdict(owned),
                            "trial_candidate": inspection.model_dump(mode="json"),
                        },
                        family_id=candidate.family_id,
                        cohort_id=candidate.scope.cohort_id,
                        state="APPLIED",
                    )

                async def revalidate():
                    current = Enrollment.model_validate(
                        await self.repo.get("enrollments", enrollment.id)
                    )
                    if (
                        not current.enabled
                        or current.version != enrollment.version
                        or (await self.policy()).version != policy.version
                    ):
                        return False
                    await self.sql.execute(
                        "EXPLAIN " + original,
                        candidate.scope,
                        connection=source_conn,
                        category="diagnostic",
                        max_rows=2000,
                    )
                    if refresh_source is not None:
                        current_object = await self.owned_refresh_object(
                            candidate, candidate.scope, source_conn
                        )
                        if current_object.binding != refresh_source.binding:
                            return False
                    if resource_source is not None:
                        source_inventory = await self.sql.execute(
                            "SHOW RESOURCE GROUPS ALL", candidate.scope,
                            connection=source_conn, category="diagnostic",
                        )
                        budget_inventory = await self.sql.execute(
                            "SHOW RESOURCE GROUPS ALL", sandbox,
                            connection=connection, category="diagnostic",
                        )
                        if (
                            resource_group_state(
                                source_inventory, enrollment.production_resource_group,
                            ).binding != resource_source.binding
                            or resource_group_state(budget_inventory, group).binding
                            != resource_budget.binding
                        ):
                            return False
                    return True

                async def snapshot():
                    from app.modules.query_autopilot.engine_state import registered_snapshot_state

                    return await registered_snapshot_state(
                        self.sql, enrollment, sandbox, connection,
                    )

                measurement_counts: dict[str, int] = {}

                async def measure(phase, is_control):
                    statement = control if is_control else replay
                    result = await self.sql.execute(
                        statement,
                        sandbox,
                        connection=connection,
                        category="experiment",
                        max_rows=enrollment.budget.max_result_rows,
                    )
                    proof = prove_result(
                        result.rows,
                        result.column_types,
                        ordered=deterministic_order(statement, result.columns, result.rows),
                        max_rows=enrollment.budget.max_result_rows,
                        max_bytes=enrollment.budget.max_result_bytes,
                        truncated=result.truncated,
                    )
                    key = (phase, is_control)
                    measurement_counts[key] = measurement_counts.get(key, 0) + 1
                    resource = None
                    if result.engine_query_ids and measurement_counts[key] in {
                        enrollment.budget.warmups + 1,
                        enrollment.budget.warmups + enrollment.budget.repetitions,
                    }:
                        import asyncio

                        await asyncio.sleep(0.2)
                        profile = await evidence.profile(
                            candidate.family_id, sandbox, result.engine_query_ids[0], connection
                        )
                        if profile.availability == Availability.AVAILABLE:
                            resource = profile.summary.get("facts")
                    return Measurement(
                        result.engine_roundtrip_ms or result.elapsed_ms,
                        proof,
                        resource=resource,
                        query_id=result.engine_query_ids[0] if result.engine_query_ids else None,
                    )

                async def apply_trial():
                    before_plan = await evidence.capture(
                        family_id=candidate.family_id,
                        scope=sandbox,
                        kind="plan",
                        statement="EXPLAIN COSTS " + replay,
                        connection=connection,
                    )
                    plans["before"] = before_plan.summary
                    if (
                        candidate.kind == "PLAN_BASELINE"
                        and (
                            await baseline_for_sample(self.sql, replay, sandbox, connection)
                        ).exists
                    ):
                        raise ValueError("sandbox_baseline_already_exists")
                    intent = {
                        "id": job["id"],
                        "candidate_id": candidate.id,
                        "state": "APPLYING",
                        "target": "sandbox",
                        "statement_digests": [digest(statement) for statement in statements],
                        "kind": candidate.kind,
                        "targets": candidate.targets,
                        "snapshot_id": enrollment.snapshot_id,
                        "enrollment_binding": digest(enrollment.model_dump(mode="json")),
                    }
                    if trial_ownership:
                        intent.update(
                            owned_object=asdict(trial_ownership["state"]),
                            trial_candidate=trial_ownership["candidate"].model_dump(mode="json"),
                        )
                    if refresh_source is not None:
                        intent["refresh_source"] = asdict(refresh_source)
                    if resource_source is not None:
                        intent["resource_source"] = asdict(resource_source)
                        intent["resource_budget"] = asdict(resource_budget)
                    await self.repo.put(
                        "experiments",
                        job["id"],
                        intent,
                        family_id=candidate.family_id,
                        cohort_id=candidate.scope.cohort_id,
                        state="APPLYING",
                    )
                    if candidate.kind in {"MATERIALIZED_VIEW", "ADD_INDEX", "RESOURCE_GROUP"}:
                        inspection = trial_candidate.model_copy(update={"targets": targets})
                        if (await inspect_object(self.sql, inspection, sandbox, connection)).exists:
                            raise ValueError(
                                "sandbox_object_already_exists_requires_reconciliation"
                            )
                    for statement in statements:
                        await self.sql.execute(
                            statement, sandbox, connection=connection, category="experiment"
                        )
                    if candidate.kind == "REFRESH_POLICY":
                        owned = await wait_ready(
                            self.sql, trial_ownership["candidate"], sandbox, connection
                        )
                        trial_ownership["state"] = owned
                        intent["owned_object"] = asdict(owned)
                        await self.repo.put(
                            "experiments",
                            job["id"],
                            {**intent, "state": "APPLIED"},
                            family_id=candidate.family_id,
                            cohort_id=candidate.scope.cohort_id,
                            state="APPLIED",
                        )
                    if candidate.kind == "PLAN_BASELINE":
                        owned = await baseline_for_sample(self.sql, replay, sandbox, connection)
                        if not owned.exists or not owned.ready:
                            raise ValueError("sandbox_baseline_outcome_uncertain")
                        intent["owned_object"] = asdict(owned)
                        trial_ownership.update(
                            candidate=candidate.model_copy(
                                update={"owned_object": "baseline:" + owned.identity}
                            ),
                            state=owned,
                        )
                        intent["trial_candidate"] = trial_ownership["candidate"].model_dump(
                            mode="json"
                        )
                        await self.repo.put(
                            "experiments",
                            job["id"],
                            intent,
                            family_id=candidate.family_id,
                            cohort_id=candidate.scope.cohort_id,
                            state="APPLIED",
                        )
                    if candidate.kind in {"MATERIALIZED_VIEW", "ADD_INDEX", "RESOURCE_GROUP"}:
                        owned = await wait_ready(self.sql, inspection, sandbox, connection)
                        trial_ownership.update(candidate=inspection, state=owned)
                        intent["owned_object"] = asdict(owned)
                        intent["trial_candidate"] = inspection.model_dump(mode="json")
                        await self.repo.put(
                            "experiments",
                            job["id"],
                            intent,
                            family_id=candidate.family_id,
                            cohort_id=candidate.scope.cohort_id,
                            state="APPLIED",
                        )
                    if candidate.kind == "RESOURCE_GROUP":
                        await self.sql.execute(
                            f"SET resource_group = '{trial_group}'",
                            sandbox,
                            connection=connection,
                            category="experiment",
                        )
                    after_plan = await evidence.capture(
                        family_id=candidate.family_id,
                        scope=sandbox,
                        kind="plan",
                        statement="EXPLAIN COSTS " + replay,
                        connection=connection,
                    )
                    plans["after"] = after_plan.summary

                async def rewrite():
                    nonlocal candidate
                    from app.core.config import settings
                    from app.modules.query_autopilot.acceptance import (
                        collect_ranger_acceptance,
                        ranger_acceptance_reason,
                    )

                    acceptance = (
                        await self.repo.get("evidence", enrollment.ranger_acceptance_ref)
                        if enrollment.ranger_acceptance_ref else None
                    )
                    if acceptance is None and not enrollment.ranger_acceptance_ref:
                        for identifier in reversed(candidate.evidence_ids):
                            value = await self.repo.get("evidence", identifier)
                            if value and value.get("kind") == "ranger_acceptance":
                                acceptance = value
                                break
                    if (acceptance is None and not enrollment.ranger_acceptance_ref
                            and settings.RANGER_ENABLED):
                        proof = await collect_ranger_acceptance(
                            self.sql, self.repo, trial_ownership["candidate"], enrollment,
                            replay, sandbox, connection, trial_ownership["state"],
                        )
                        acceptance = proof.model_dump(mode="json")
                        candidate = candidate.model_copy(update={
                            "evidence_ids": (*candidate.evidence_ids, proof.id),
                        })
                        candidate = candidate.model_copy(update={
                            "evidence_digest": await self.evidence_digest(candidate),
                        })
                        await self._candidate(candidate)
                    reason = ranger_acceptance_reason(
                        acceptance, candidate.scope, enrollment.snapshot_id, replay, utcnow(),
                        family_id=candidate.family_id, execution_scope=sandbox,
                        snapshot_state=await snapshot(),
                    )
                    if reason:
                        raise ValueError(reason)
                    name = object_name(trial_candidate)
                    result = await self.sql.execute(
                        "EXPLAIN " + replay, sandbox, connection=connection, category="experiment"
                    )
                    fresh = await inspect_object(self.sql, trial_candidate, sandbox, connection)
                    return plan_uses_object(result, name) and fresh.exists and fresh.ready

                runner = Experiment(enrollment.budget, minimum_gain=policy.minimum_gain)
                result = await runner.run(
                    measure=measure,
                    snapshot=snapshot,
                    apply=apply_trial,
                    revalidate=revalidate,
                    require_rewrite=rewrite
                    if candidate.kind in {"MATERIALIZED_VIEW", "REFRESH_POLICY"}
                    else None,
                )
                cleanup = "not_required"
                if trial_ownership:
                    if candidate.kind == "RESOURCE_GROUP":
                        await self.sql.execute(
                            f"SET resource_group = '{group}'",
                            sandbox,
                            connection=connection,
                            category="experiment",
                        )
                    cleanup = await self.cleanup_trial(
                        trial_ownership["candidate"],
                        trial_ownership["state"],
                        sandbox,
                        connection,
                        job["id"],
                    )
                    if cleanup != "verified_object_removed":
                        result = replace(result, status="INCONCLUSIVE", reason=cleanup)
                if result.status == "SUCCESS" and any(
                    not values.get("resource") for values in (result.before, result.after)
                ):
                    result = replace(
                        result, status="INCONCLUSIVE", reason="resource_measurements_unavailable"
                    )
                result_digest = digest(
                    {
                        "measurement": result.binding,
                        "sample": digest(original),
                        "control": digest(control),
                        "enrollment": enrollment.model_dump(mode="json"),
                        "candidate": candidate.binding,
                        "refresh_source": asdict(refresh_source) if refresh_source else None,
                        "resource_source": asdict(resource_source) if resource_source else None,
                        "resource_budget": asdict(resource_budget) if resource_budget else None,
                        "application_definition_digest": application_definition_digest,
                    }
                )
                record = {
                    "id": job["id"],
                    "sample_id": sample_evidence.id,
                    "application_definition_digest": application_definition_digest,
                    "candidate_id": candidate.id,
                    "candidate_binding": candidate.proposal_binding,
                    "state": result.status,
                    "result": asdict(result),
                    "result_digest": result_digest,
                    "plans": plans,
                    "plan_diff": plan_diff(plans.get("before", {}), plans.get("after", {})),
                    "snapshot_id": enrollment.snapshot_id,
                    "cleanup": cleanup,
                    "enrollment_binding": digest(enrollment.model_dump(mode="json")),
                    "refresh_source": asdict(refresh_source) if refresh_source else None,
                    "resource_source": asdict(resource_source) if resource_source else None,
                    "resource_budget": asdict(resource_budget) if resource_budget else None,
                    "trial_candidate": trial_ownership["candidate"].model_dump(mode="json")
                    if trial_ownership
                    else None,
                    "owned_object": asdict(trial_ownership["state"]) if trial_ownership else None,
                }
                await self.repo.put(
                    "experiments",
                    job["id"],
                    record,
                    family_id=candidate.family_id,
                    cohort_id=candidate.scope.cohort_id,
                    state=result.status,
                )
                if result.status == "SUCCESS":
                    ready = (
                        State.READY_AUTO
                        if classify(candidate, enrollment, policy) == Risk.AUTO
                        else State.READY_APPROVAL
                    )
                    candidate = transition(candidate, ready).model_copy(
                        update={"experiment_id": job["id"], "experiment_digest": result_digest}
                    )
                else:
                    candidate = transition(
                        candidate, State.INCONCLUSIVE, reason=result.reason or result.status
                    )
                await self._candidate(candidate)
                if candidate.state == State.READY_AUTO and policy.mode != Mode.OBSERVE:
                    auto_id = "auto:" + job["id"]
                    auto = {
                        **job,
                        "id": auto_id,
                        "kind": "apply",
                        "state": "QUEUED",
                        "version": 1,
                        "candidate_binding": candidate.binding,
                        "candidate_version": candidate.version,
                        "automatic": True,
                        "actor": candidate.scope.principal,
                        "actor_role": candidate.scope.active_role,
                        "actor_version": candidate.scope.security_context_version,
                        "actor_session_id": None,
                    }
                    await self.repo.put("jobs", auto_id, auto, state="QUEUED", insert_only=True)
                return {"state": "COMPLETED", "reason": result.reason}
        except Exception as exc:
            reason = str(exc) if str(exc) in {
                "sandbox_resource_baseline_differs_from_production",
                "production_resource_group_unavailable",
                "ranger_policy_revision_unavailable", "ranger_policy_revision_changed",
            } else "experiment_identity_evidence_or_engine_unavailable"
            await self._candidate(
                transition(
                    candidate,
                    State.BLOCKED,
                    reason=reason,
                )
            )
            return {
                "state": "BLOCKED",
                "reason": reason,
            }

    async def owned_refresh_object(self, candidate, scope, connection) -> ObjectState:
        current = await inspect_object(self.sql, candidate, scope, connection)
        if not current.exists or not current.ready:
            raise ValueError("refresh_object_unavailable_or_not_fresh")
        cursor = ""
        while True:
            page = await self.repo.page(
                "actions",
                family_id=candidate.family_id,
                cohort_id=candidate.scope.cohort_id,
                after=cursor,
                limit=1000,
            )
            if not page:
                break
            if any(
                action.get("state") == "APPLIED"
                and action.get("kind") in {"MATERIALIZED_VIEW", "REFRESH_POLICY"}
                and action.get("owned_object_binding") == current.binding
                for action in page
            ):
                return current
            cursor = page[-1]["id"]
        raise ValueError("refresh_object_ownership_unproven")

    async def cleanup_trial(self, candidate, owned, sandbox, connection, operation_id) -> str:
        identifier = "trial-cleanup:" + operation_id
        current = await inspect_object(self.sql, candidate, sandbox, connection)
        prior = await self.repo.get("actions", identifier)
        if prior:
            if prior.get("owned_object_binding") != owned.binding:
                return "sandbox_cleanup_binding_changed"
            if current.exists:
                return "sandbox_cleanup_outcome_uncertain_no_retry"
        else:
            if not current.exists or current.binding != owned.binding:
                return "sandbox_object_changed_cleanup_blocked"
            intent = {
                "id": identifier,
                "kind": "EXPERIMENT_CLEANUP",
                "state": "APPLYING",
                "candidate_id": candidate.id,
                "owned_object_binding": owned.binding,
                "target": "sandbox",
            }
            if not await self.repo.put(
                "actions", identifier, intent, state="APPLYING", insert_only=True
            ):
                return "sandbox_cleanup_outcome_uncertain_no_retry"
            await self.sql.execute(
                compensation_sql(candidate),
                sandbox,
                connection=connection,
                category="experiment",
                confirm=True,
            )
            if (await inspect_object(self.sql, candidate, sandbox, connection)).exists:
                return "sandbox_cleanup_outcome_uncertain"
            prior = intent
        await self.repo.put("actions", identifier, {**prior, "state": "APPLIED"}, state="APPLIED")
        return "verified_object_removed"

    async def reconcile_trial(self, candidate: Candidate, job: dict) -> str:
        record = await self.repo.get("experiments", job["id"])
        if not record or not record.get("owned_object") or not record.get("trial_candidate"):
            return "sandbox_creation_outcome_uncertain_ownership_unproven"
        enrollment = Enrollment.model_validate(
            await self.repo.get("enrollments", candidate.enrollment_id)
        )
        if (
            not enrollment.enabled
            or not enrollment.replay_opt_in
            or record.get("enrollment_binding") != digest(enrollment.model_dump(mode="json"))
            or record.get("candidate_id") != candidate.id
        ):
            return "sandbox_cleanup_enrollment_changed"
        trial = Candidate.model_validate(record["trial_candidate"])
        owned = ObjectState(**record["owned_object"])
        sandbox = Scope(
            principal=enrollment.execution_principal,
            active_role=enrollment.execution_role,
            security_context_version=enrollment.version,
            database=enrollment.sandbox_database,
            policy_revision=candidate.scope.policy_revision,
        )
        async with (
            exclusive(self.client, tuple("object:" + t for t in candidate.targets)),
            self.sql.connection(candidate.scope) as source,
            self.sql.connection(sandbox) as connection,
        ):
            await self.sql.execute(
                "EXPLAIN " + await self.sample(candidate),
                candidate.scope,
                connection=source,
                category="diagnostic",
            )
            cleanup = await self.cleanup_trial(trial, owned, sandbox, connection, job["id"])
        await self.repo.put(
            "experiments",
            job["id"],
            {
                **record,
                "state": "INCONCLUSIVE",
                "cleanup": cleanup,
                "reason": "interrupted_experiment_measurements_incomplete",
            },
            state="INCONCLUSIVE",
        )
        return cleanup

    async def apply(
        self, candidate: Candidate, enrollment: Enrollment, policy: Policy, job: dict
    ) -> dict:
        session = await session_store.get(job.get("actor_session_id", ""))
        authorized = bool(
            session
            and session.get("username") == job["actor"]
            and session.get("active_role") == "ACCOUNTADMIN"
            and session.get("security_context_version") == job["actor_version"]
        )
        automatic = job.get("automatic") is True
        if automatic:
            authorized = classify(candidate, enrollment, policy) == Risk.AUTO and (
                job["actor"],
                job["actor_role"],
                job["actor_version"],
            ) == (
                candidate.scope.principal,
                candidate.scope.active_role,
                candidate.scope.security_context_version,
            )
        experiment = await self.repo.get("experiments", candidate.experiment_id or "")
        passed = bool(
            experiment
            and experiment.get("state") == "SUCCESS"
            and experiment.get("result_digest") == candidate.experiment_digest
            and experiment.get("candidate_binding") == candidate.proposal_binding
        )
        reason = application_block(
            candidate,
            enrollment,
            policy,
            utcnow(),
            experiment_passed=passed,
            evidence_current=await self.evidence_current(candidate),
            authorized=authorized,
        )
        if reason:
            await self._candidate(transition(candidate, State.BLOCKED, reason=reason))
            return {"state": "BLOCKED", "reason": reason}
        from app.modules.query_autopilot.engine_state import registered_snapshot_state

        tested_snapshot = experiment.get("result", {}).get("snapshot")
        if not tested_snapshot:
            reason = "experiment_snapshot_evidence_unavailable"
            await self._candidate(transition(candidate, State.BLOCKED, reason=reason))
            return {"state": "BLOCKED", "reason": reason}
        sandbox = Scope(
            principal=enrollment.execution_principal, active_role=enrollment.execution_role,
            security_context_version=enrollment.version, database=enrollment.sandbox_database,
            policy_revision=candidate.scope.policy_revision,
        )
        async with self.sql.connection(sandbox) as snapshot_connection:
            current_snapshot = await registered_snapshot_state(
                self.sql, enrollment, sandbox, snapshot_connection,
            )
        if current_snapshot != tested_snapshot:
            reason = "snapshot_changed_since_experiment"
            await self._candidate(transition(candidate, State.BLOCKED, reason=reason))
            return {"state": "BLOCKED", "reason": reason}
        sample_record = await self.repo.get("evidence", experiment["sample_id"])
        if sample_record is None:
            return {"state": "BLOCKED", "reason": "bound_replay_sample_unavailable"}
        sample = await self.read_sample(candidate, Evidence.model_validate(sample_record))
        try:
            statements = action_sql(candidate, sample_sql=sample)
            reason = (
                "application_definition_changed_since_experiment"
                if experiment.get("application_definition_digest") != digest(statements)
                else None
            )
        except ValueError:
            reason = "application_definition_unavailable"
        if reason:
            await self._candidate(
                transition(candidate, State.BLOCKED, reason=reason).model_copy(
                    update={
                        "approval_digest": None,
                        "approval_expires_at": None,
                        "approved_by": None,
                    }
                )
            )
            return {"state": "BLOCKED", "reason": reason}
        actor = Scope(
            principal=job["actor"],
            active_role=candidate.scope.active_role if automatic else "ACCOUNTADMIN",
            security_context_version=job["actor_version"],
            database=candidate.scope.database,
            policy_revision=candidate.scope.policy_revision,
        )
        # Source access is revalidated independently of the administrator who
        # approves the maintenance operation.
        async with (
            self.sql.connection(candidate.scope) as caller,
            self.sql.connection(actor, session_id=job["actor_session_id"]) as connection,
        ):
            await self.sql.execute(
                "EXPLAIN " + sample, candidate.scope, connection=caller, category="diagnostic"
            )
            before_object = None
            if candidate.kind == "RESOURCE_GROUP":
                inventory = await self.sql.execute(
                    "SHOW RESOURCE GROUPS ALL", actor,
                    connection=connection, category="diagnostic",
                )
                for key, name in (
                    ("resource_source", enrollment.production_resource_group),
                    ("resource_budget", enrollment.budget.resource_group),
                ):
                    tested = experiment.get(key)
                    if not tested or ObjectState(**tested).binding != resource_group_state(
                        inventory, cast(str, name),
                    ).binding:
                        reason = "resource_group_changed_since_experiment"
                        await self._candidate(transition(candidate, State.BLOCKED, reason=reason))
                        return {"state": "BLOCKED", "reason": reason}
            if candidate.kind == "PLAN_BASELINE":
                before_object = await baseline_for_sample(self.sql, sample, actor, connection)
                if before_object.exists:
                    raise ValueError("production_baseline_already_exists")
            if candidate.kind in {"MATERIALIZED_VIEW", "ADD_INDEX", "REFRESH_POLICY"}:
                before_object = await inspect_object(self.sql, candidate, actor, connection)
                if candidate.kind != "REFRESH_POLICY" and before_object.exists:
                    raise ValueError("production_object_already_exists")
                if candidate.kind == "REFRESH_POLICY":
                    before_object = await self.owned_refresh_object(candidate, actor, connection)
                    tested_source = experiment.get("refresh_source")
                    if (
                        not tested_source
                        or ObjectState(**tested_source).binding != before_object.binding
                    ):
                        raise ValueError("refresh_object_changed_since_experiment")
            candidate = transition(candidate, State.APPLYING)
            await self._candidate(candidate)
            intent = {
                "id": job["id"],
                "candidate_id": candidate.id,
                "binding": candidate.binding,
                "state": "APPLYING",
                "statement_digests": [digest(statement) for statement in statements],
                "kind": candidate.kind,
                "targets": candidate.targets,
                "actor": actor.principal,
                "actor_role": actor.active_role,
                "actor_version": actor.security_context_version,
                "started_at": utcnow().isoformat(),
                "before_object": asdict(before_object) if before_object else None,
            }
            await self.repo.put(
                "actions",
                job["id"],
                intent,
                family_id=candidate.family_id,
                cohort_id=candidate.scope.cohort_id,
                state="APPLYING",
            )
            for statement in statements:
                await self.sql.execute(
                    statement, actor, connection=connection, category="maintenance"
                )
            if candidate.kind == "PLAN_BASELINE":
                after_object = await baseline_for_sample(self.sql, sample, actor, connection)
                if not after_object.exists or not after_object.ready:
                    raise ValueError("baseline_application_outcome_uncertain")
                intent["owned_object_binding"] = after_object.binding
                intent["owned_object"] = asdict(after_object)
                candidate = candidate.model_copy(
                    update={"owned_object": "baseline:" + cast(str, after_object.identity)}
                )
            if candidate.kind in {"MATERIALIZED_VIEW", "ADD_INDEX", "REFRESH_POLICY"}:
                after_object = await wait_ready(self.sql, candidate, actor, connection)
                intent["owned_object_binding"] = after_object.binding
                intent["owned_object"] = asdict(after_object)
                if candidate.kind != "REFRESH_POLICY":
                    candidate = candidate.model_copy(
                        update={"owned_object": object_name(candidate)}
                    )
            candidate = transition(candidate, State.APPLIED)
            await self._candidate(candidate)
            await self.repo.put(
                "actions",
                job["id"],
                {**intent, "state": "APPLIED", "applied_at": utcnow().isoformat()},
                family_id=candidate.family_id,
                cohort_id=candidate.scope.cohort_id,
                state="APPLIED",
            )
            candidate = transition(candidate, State.VERIFYING)
            await self._candidate(candidate)
            verify_id = "verify:" + job["id"]
            await self.repo.put(
                "jobs",
                verify_id,
                {
                    **job,
                    "id": verify_id,
                    "kind": "verify",
                    "candidate_binding": candidate.binding,
                    "state": "QUEUED",
                    "version": 1,
                },
                state="QUEUED",
                insert_only=True,
            )
        return {"state": "COMPLETED"}

    async def verify(
        self, candidate: Candidate, enrollment: Enrollment, policy: Policy, job: dict
    ) -> dict:
        from app.modules.query_autopilot.statistics import Distribution, mean_gain_evidence

        cached = await self.repo.get("outcomes", job["id"])
        if cached:
            if cached.get("candidate_binding") != candidate.binding:
                return {"state": "BLOCKED", "reason": "outcome_candidate_binding_changed"}
            if candidate.state == State.VERIFYING:
                candidate = transition(
                    candidate, State(cached["state"]), reason=cached.get("reason")
                )
                await self._candidate(candidate)
            if cached.get("rollback") == "pending":
                action = await self.repo.get("actions", job["id"].removeprefix("verify:"))
                cached["rollback"] = (
                    "verified_object_removed"
                    if candidate.state == State.ROLLED_BACK
                    else await self.compensate(candidate, action, job)
                )
                await self.repo.put(
                    "outcomes",
                    job["id"],
                    cached,
                    family_id=candidate.family_id,
                    cohort_id=candidate.scope.cohort_id,
                    state=cached["state"],
                )
            return {"state": "COMPLETED", "reason": cached.get("reason")}
        if candidate.state != State.VERIFYING:
            return {"state": "BLOCKED", "reason": "candidate_not_awaiting_verification"}
        original = await self.sample(candidate)
        object_evidence: dict[str, Any] | None = None
        action = await self.repo.get("actions", job["id"].removeprefix("verify:"))
        async with self.sql.connection(candidate.scope) as connection:
            actual_plan = await self.sql.execute(
                "EXPLAIN " + original, candidate.scope, connection=connection, category="diagnostic"
            )
        if candidate.kind in {ActionKind.MATERIALIZED_VIEW, ActionKind.REFRESH_POLICY}:
            try:
                if not action or not job.get("actor_session_id") or (
                    action.get("actor") != job.get("actor")
                    or action.get("actor_role") != job.get("actor_role")
                    or job.get("actor_role") != "ACCOUNTADMIN"
                    or action.get("actor_version") != job.get("actor_version")
                ):
                    raise AuthorizationUnavailable("verification_owner_identity_unavailable")
                owner = Scope(
                    principal=job["actor"], active_role=job["actor_role"],
                    security_context_version=job["actor_version"],
                    database=candidate.scope.database,
                    policy_revision=candidate.scope.policy_revision,
                )
                async with self.sql.connection(
                    owner, session_id=job["actor_session_id"],
                ) as owner_connection:
                    current_object = await inspect_object(
                        self.sql, candidate, owner, owner_connection,
                    )
                object_evidence = {
                    "availability": Availability.AVAILABLE,
                    "state": asdict(current_object),
                    "binding": current_object.binding,
                    "rewrite_observed": plan_uses_object(actual_plan, object_name(candidate)),
                }
            except (EvidenceUnavailable, EvidenceUnsupported) as exc:
                object_evidence = {
                    "availability": Availability.UNSUPPORTED
                    if isinstance(exc, EvidenceUnsupported) else Availability.UNAVAILABLE,
                    "reason": "verification_owner_metadata_unavailable",
                }
            except ValueError:
                object_evidence = {
                    "availability": Availability.UNAUTHORIZED,
                    "reason": "verification_owner_identity_unavailable",
                }
        if not action or action.get("state") != "APPLIED" or not action.get("applied_at"):
            return {"state": "BLOCKED", "reason": "application_completion_time_unavailable"}
        try:
            started = datetime.fromisoformat(action["started_at"])
            applied = datetime.fromisoformat(action["applied_at"])
            if not started.tzinfo or not applied.tzinfo or applied < started:
                raise ValueError("invalid_application_timestamps")
        except (KeyError, TypeError, ValueError):
            return {"state": "BLOCKED", "reason": "application_timestamps_invalid"}
        if utcnow() < applied + timedelta(minutes=30):
            return {
                "state": "QUEUED",
                "reason": "awaiting_complete_verification_window",
                "not_before": (applied + timedelta(minutes=30)).isoformat(),
            }

        async def observations(family_id):
            before, after = Distribution(), Distribution()
            cursor = ""
            while True:
                values = await self.repo.page(
                    "observations",
                    family_id=family_id,
                    cohort_id=candidate.scope.cohort_id,
                    after=cursor,
                    limit=1000,
                    created_since=started - timedelta(minutes=30),
                    before=applied + timedelta(minutes=30),
                )
                if not values:
                    break
                for value in values:
                    if value["status"] != "success":
                        continue
                    observed = datetime.fromisoformat(value["observed_at"])
                    if started - timedelta(minutes=30) <= observed < started:
                        before.add(value["total_ms"])
                    elif applied <= observed < applied + timedelta(minutes=30):
                        after.add(value["total_ms"])
                cursor = values[-1]["id"]
            return before, after

        before, after = await observations(candidate.family_id)
        from app.modules.query_autopilot.verification import verification_signals

        signals = await verification_signals(
            self.repo, candidate.family_id, candidate.scope.cohort_id, applied, started=started,
        )
        controls = {}
        control_unavailable = not enrollment.control_families
        control_regression = False
        for family in enrollment.control_families:
            control_before, control_after = await observations(family)
            a, b = control_before.quantile(0.95), control_after.quantile(0.95)
            controls[family] = {
                "before": control_before.as_dict(),
                "after": control_after.as_dict(),
            }
            if control_before.count < 20 or control_after.count < 20 or not a or b is None:
                control_unavailable = True
            elif b > a * 1.1 and b - a > 100:
                control_regression = True
        old, new = before.quantile(0.95), after.quantile(0.95)
        gain_evidence = mean_gain_evidence(before, after)
        gain = None
        if control_unavailable:
            state, reason = State.INCONCLUSIVE, "insufficient_control_workload_samples"
        elif control_regression:
            state, reason = State.REGRESSED, "untargeted_workload_regressed"
        elif before.count < 20 or after.count < 20 or not old or new is None:
            state, reason = State.INCONCLUSIVE, "insufficient_post_application_samples"
        else:
            gain = 1 - new / old
            if gain >= policy.minimum_gain:
                state, reason = State.SUCCESS, None
            elif gain <= -policy.minimum_gain:
                state, reason = State.REGRESSED, "measured_post_application_regression"
            else:
                state, reason = State.NO_IMPROVEMENT, "measured_gain_below_threshold"
        if state == State.SUCCESS and object_evidence is not None and (
            object_evidence["availability"] != Availability.AVAILABLE
        ):
            state = State.INCONCLUSIVE
            reason = "production_mv_metadata_identity_unavailable"
        if state == State.SUCCESS and object_evidence is not None and (
            not object_evidence["state"]["ready"]
            or not object_evidence["rewrite_observed"]
            or object_evidence["binding"] != action.get("owned_object_binding")
        ):
            state = State.INCONCLUSIVE
            reason = "production_mv_rewrite_freshness_or_binding_unproven"
        if state == State.SUCCESS and signals["availability"] != "available":
            state, reason = State.INCONCLUSIVE, "production_plan_or_resource_evidence_unavailable"
        if state == State.SUCCESS and not gain_evidence["repeatable_gain"]:
            state, reason = State.INCONCLUSIVE, "post_application_gain_not_repeatable"
        candidate = transition(candidate, state, reason=reason)
        outcome = {
            "id": job["id"],
            "candidate_id": candidate.id,
            "candidate_binding": candidate.binding,
            "kind": candidate.kind,
            "state": state,
            "reason": reason,
            "before": before.as_dict(),
            "after": after.as_dict(),
            "measured_gain": gain,
            "gain_evidence": gain_evidence,
            "windows": {
                "before_start": (started - timedelta(minutes=30)).isoformat(),
                "before_end": started.isoformat(),
                "after_start": applied.isoformat(),
                "after_end": (applied + timedelta(minutes=30)).isoformat(),
                "application_interval_excluded": True,
            },
            "experiment_id": candidate.experiment_id,
            "correctness": "sandbox_equivalence_only",
            "controls": controls,
            "verification_evidence": signals,
            "object_evidence": object_evidence,
            "causality": "observational_post_application_window",
            "rollback": "pending" if state == State.REGRESSED else "not_needed",
        }
        await self.repo.put(
            "outcomes",
            job["id"],
            outcome,
            family_id=candidate.family_id,
            cohort_id=candidate.scope.cohort_id,
            state=state,
        )
        await self._candidate(candidate)
        if state == State.REGRESSED:
            outcome["rollback"] = await self.compensate(candidate, action, job)
            await self.repo.put(
                "outcomes",
                job["id"],
                outcome,
                family_id=candidate.family_id,
                cohort_id=candidate.scope.cohort_id,
                state=state,
            )
        return {"state": "COMPLETED", "reason": reason}

    async def compensate(self, candidate: Candidate, action: dict, job: dict) -> str:
        if candidate.kind not in {"MATERIALIZED_VIEW", "ADD_INDEX", "PLAN_BASELINE"}:
            return "not_reversible"
        if not action.get("owned_object_binding"):
            return "ownership_unproven"
        enrollment = Enrollment.model_validate(
            await self.repo.get("enrollments", candidate.enrollment_id)
        )
        if (
            not enrollment.enabled
            or enrollment.version != candidate.enrollment_version
            or (await self.policy()).version != candidate.policy_version
        ):
            return "compensation_policy_or_enrollment_changed"
        actor = Scope(
            principal=job["actor"],
            active_role=job["actor_role"],
            security_context_version=job["actor_version"],
            database=candidate.scope.database,
            policy_revision=candidate.scope.policy_revision,
        )
        try:
            async with self.sql.connection(
                actor, session_id=job.get("actor_session_id")
            ) as connection:
                sample = await self.sample(candidate)
                async with self.sql.connection(candidate.scope) as caller:
                    await self.sql.execute(
                        "EXPLAIN " + sample,
                        candidate.scope,
                        connection=caller,
                        category="diagnostic",
                    )
                current = await inspect_object(self.sql, candidate, actor, connection)
                identifier = "rollback:" + action["id"]
                prior = await self.repo.get("actions", identifier)
                if prior:
                    if (
                        prior.get("owned_object_binding") != action["owned_object_binding"]
                        or prior.get("candidate_id") != candidate.id
                    ):
                        return "compensation_intent_binding_changed"
                    if current.exists:
                        return "compensation_outcome_uncertain_no_retry"
                    await self.repo.put(
                        "actions", identifier, {**prior, "state": "APPLIED"}, state="APPLIED"
                    )
                    await self._candidate(transition(candidate, State.ROLLED_BACK))
                    return "verified_object_removed"
                if not can_compensate(
                    candidate,
                    object_identity_matches=current.binding == action["owned_object_binding"],
                    authorized=True,
                ):
                    return "object_changed_compensation_blocked"
                intent = {
                    "id": identifier,
                    "candidate_id": candidate.id,
                    "state": "APPLYING",
                    "owned_object_binding": current.binding,
                    "kind": "COMPENSATION",
                }
                claimed = await self.repo.put(
                    "actions", identifier, intent, state="APPLYING", insert_only=True
                )
                if not claimed:
                    return "compensation_outcome_uncertain_no_retry"
                await self.sql.execute(
                    compensation_sql(candidate),
                    actor,
                    connection=connection,
                    category="maintenance",
                    confirm=True,
                )
                if (await inspect_object(self.sql, candidate, actor, connection)).exists:
                    return "compensation_outcome_uncertain"
                await self.repo.put(
                    "actions", identifier, {**intent, "state": "APPLIED"}, state="APPLIED"
                )
                await self._candidate(transition(candidate, State.ROLLED_BACK))
                return "verified_object_removed"
        except Exception:
            return "compensation_authorization_or_outcome_unavailable"

    async def reconcile(self, job: dict) -> None:
        state, reason = "BLOCKED", "interrupted_operation_outcome_unknown_no_retry"
        if job["kind"] in {"aggregate", "cleanup", "verify"}:
            state, reason = "QUEUED", "retry_read_only_metadata_work"
        elif job.get("candidate_id"):
            async with exclusive(self.client, ("candidate:" + job["candidate_id"],)):
                candidate = Candidate.model_validate(
                    await self.repo.get("opportunities", job["candidate_id"])
                )
                if job["kind"] in {"approve", "reject"}:
                    target = State.APPROVED if job["kind"] == "approve" else State.REJECTED
                    if candidate.state == target and candidate.binding == job["candidate_binding"]:
                        state, reason = "COMPLETED", "durable_decision_reconciled"
                elif job["kind"] == "experiment":
                    try:
                        reason = await self.reconcile_trial(candidate, job)
                    except Exception:
                        reason = "sandbox_cleanup_authorization_or_evidence_unavailable"
                elif job["kind"] == "apply":
                    action = await self.repo.get("actions", job["id"])
                    if action and action["state"] == "APPLIED":
                        if candidate.state == State.APPLYING:
                            candidate = transition(candidate, State.APPLIED)
                        if candidate.state == State.APPLIED:
                            candidate = transition(candidate, State.VERIFYING)
                        if candidate.state == State.VERIFYING:
                            await self._candidate(candidate)
                            verify_id = "verify:" + job["id"]
                            await self.repo.put(
                                "jobs",
                                verify_id,
                                {
                                    **job,
                                    "id": verify_id,
                                    "kind": "verify",
                                    "state": "QUEUED",
                                    "version": 1,
                                    "candidate_binding": candidate.binding,
                                },
                                state="QUEUED",
                                insert_only=True,
                            )
                            state, reason = (
                                "COMPLETED",
                                "durable_application_acknowledgement_reconciled",
                            )
                    elif candidate.kind in {"MATERIALIZED_VIEW", "ADD_INDEX", "PLAN_BASELINE"}:
                        try:
                            actor = Scope(
                                principal=job["actor"],
                                active_role=job["actor_role"],
                                security_context_version=job["actor_version"],
                                database=candidate.scope.database,
                                policy_revision=candidate.scope.policy_revision,
                            )
                            async with self.sql.connection(
                                actor, session_id=job.get("actor_session_id")
                            ) as connection:
                                current = (
                                    await baseline_for_sample(
                                        self.sql, await self.sample(candidate), actor, connection
                                    )
                                    if candidate.kind == "PLAN_BASELINE"
                                    and not candidate.owned_object
                                    else await inspect_object(
                                        self.sql, candidate, actor, connection
                                    )
                                )
                                reason = (
                                    "engine_object_present_ownership_unproven"
                                    if current.exists
                                    else "engine_object_absent_submission_outcome_uncertain"
                                )
                        except Exception:
                            reason = "engine_reconciliation_authorization_unavailable"
                if state == "BLOCKED" and candidate.state in {State.VALIDATING, State.APPLYING}:
                    await self._candidate(transition(candidate, State.BLOCKED, reason=reason))
        await self.repo.put(
            "jobs",
            job["id"],
            {**job, "state": state, "reason": reason, "version": job["version"] + 1},
            state=state,
            version=job["version"] + 1,
            expected_version=job["version"],
        )

    async def cleanup(self) -> dict:
        policy = await self.policy()
        now, after = utcnow(), ""
        protected = set()

        def references(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "evidence_ids" and isinstance(item, (list, tuple)):
                        protected.update(x for x in item if isinstance(x, str))
                    elif key in {"sample_id", "ranger_acceptance_ref"} and isinstance(item, str):
                        protected.add(item)
                    else:
                        references(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    references(item)

        for kind in (
            "incidents",
            "opportunities",
            "experiments",
            "actions",
            "outcomes",
            "enrollments",
        ):
            cursor = ""
            while True:
                records = await self.repo.page(kind, after=cursor, limit=1000)
                if not records:
                    break
                for record in records:
                    references(record)
                cursor = records[-1]["id"]
        while True:
            values = await self.repo.page("evidence", after=after, limit=100)
            if not values:
                break
            for value in values:
                item = Evidence.model_validate(value)
                expires = min(
                    item.expires_at or now,
                    item.collected_at + timedelta(hours=policy.payload_hours),
                )
                if item.expires_at and expires <= now:
                    if item.payload_ref:
                        await self.payloads.delete(item.payload_ref)
                    expired = item.model_copy(
                        update={
                            "availability": Availability.EXPIRED,
                            "payload_ref": None,
                            "reason": "retention_expired",
                            "expires_at": expires,
                        }
                    )
                    await self.repo.put(
                        "evidence",
                        item.id,
                        expired.model_dump(mode="json"),
                        family_id=item.family_id,
                        cohort_id=item.cohort_id,
                    )
                    if item.id not in protected and item.collected_at < now - timedelta(
                        days=policy.history_days
                    ):
                        await self.repo.delete("evidence", item.id)
            after = values[-1]["id"]
        await self.repo.cleanup_before(
            "observations", now - timedelta(days=policy.observations_days)
        )
        for kind in ("rollups", "baselines"):
            await self.repo.cleanup_before(kind, now - timedelta(days=policy.history_days))
        cursor = ""
        while True:
            records = await self.repo.page("jobs", after=cursor, state="COMPLETED", limit=1000)
            if not records:
                break
            for record in records:
                if (
                    record.get("kind") in {"aggregate", "cleanup"}
                    and record.get("finished_at")
                    and datetime.fromisoformat(record["finished_at"])
                    < now - timedelta(days=policy.observations_days)
                ):
                    await self.repo.delete("jobs", record["id"])
            cursor = records[-1]["id"]
        return {"state": "COMPLETED"}
