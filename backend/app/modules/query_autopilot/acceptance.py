"""Validate the exact governed snapshot proof before accepting an MV trial."""

from datetime import datetime

from app.modules.query_autopilot.models import Availability, Evidence, Scope, digest


def ranger_acceptance_reason(
    record: dict | None,
    scope: Scope,
    snapshot_id: str,
    replay: str,
    now: datetime,
    *,
    family_id: str,
    execution_scope: Scope,
    snapshot_state: str | None,
) -> str | None:
    if record is None:
        return "ranger_acceptance_unavailable"
    proof = Evidence.model_validate(record)
    availability = proof.effective_availability(now)
    if availability != Availability.AVAILABLE:
        return "ranger_acceptance_" + availability.value
    if proof.kind != "ranger_acceptance" or proof.source != "patched_fe_acceptance":
        return "ranger_acceptance_provenance_unverified"
    required = {
        "scope": scope.model_dump(mode="json"),
        "snapshot_id": snapshot_id,
        "candidate_shape": digest(replay),
        "execution_scope": execution_scope.model_dump(mode="json"),
        "snapshot_state": snapshot_state,
    }
    if (
        not snapshot_state
        or proof.family_id != family_id
        or proof.cohort_id != scope.cohort_id
        or any(proof.summary.get(key) != value for key, value in required.items())
    ):
        return "ranger_acceptance_binding_changed"
    if any(proof.summary.get(key) != "PASS" for key in ("row_filter", "masking")):
        return "ranger_filter_or_mask_acceptance_unproven"
    return None


async def collect_ranger_acceptance(
    sql,
    repo,
    candidate,
    enrollment,
    replay: str,
    sandbox: Scope,
    connection,
    owned,
) -> Evidence:
    """Compare governed results on the registered snapshot under the workload identity."""
    import asyncio
    from datetime import timedelta
    from uuid import uuid4

    from app.common.identifiers import check_identifier
    from app.core.config import settings
    from app.modules.access_control.service import access_control_service
    from app.modules.query_autopilot.correctness import (
        equivalent,
        prove_result,
    )
    from app.modules.query_autopilot.engine_state import (
        inspect_object,
        object_name,
        registered_snapshot_state,
    )
    from app.modules.query_autopilot.evidence import plan_uses_object
    from app.modules.query_autopilot.models import utcnow
    from app.sql_frontend.autopilot import deterministic_order

    if not settings.RANGER_ENABLED or not candidate.scope.policy_revision:
        raise ValueError("ranger_acceptance_unsupported")
    group = check_identifier(enrollment.budget.resource_group, field="resource group")
    now = utcnow()
    record = Evidence(
        id="ranger-acceptance-" + uuid4().hex,
        family_id=candidate.family_id,
        cohort_id=candidate.scope.cohort_id,
        kind="ranger_acceptance",
        source="patched_fe_acceptance",
        availability=Availability.UNAVAILABLE,
        expires_at=now + timedelta(hours=24),
        reason="verification_pending",
    )

    async def persist():
        await repo.put(
            "evidence",
            record.id,
            record.model_dump(mode="json"),
            family_id=record.family_id,
            cohort_id=record.cohort_id,
            expires_at=record.expires_at,
        )

    await persist()
    try:
        async with asyncio.timeout(enrollment.budget.timeout_seconds):
            effects = {"row_filter": False, "masking": False}
            for table, target in enrollment.table_mapping.items():
                source = table if "." in table else candidate.scope.database + "." + table
                before, after = [
                    await access_control_service.effective_access(
                        principal=candidate.scope.principal,
                        active_role=candidate.scope.active_role,
                        resource=path,
                    )
                    for path in (source, target)
                ]
                if any(
                    value.get("security_effects_comparison_supported") is not True
                    for value in (before, after)
                ):
                    raise ValueError("ranger_security_effects_comparison_unsupported")
                for key in (
                    "row_restrictions",
                    "column_restrictions",
                    "column_restriction_fingerprints",
                    "security_effects_fingerprint",
                ):
                    a, b = before.get(key), after.get(key)
                    if (sorted(a) if isinstance(a, list) else a) != (
                        sorted(b) if isinstance(b, list) else b
                    ):
                        raise ValueError("ranger_snapshot_security_effects_differ")
                effects["row_filter"] |= bool(before.get("row_restrictions"))
                effects["masking"] |= bool(before.get("column_restrictions"))
            if not all(effects.values()):
                raise ValueError("ranger_filter_or_mask_acceptance_unproven")
            state = await registered_snapshot_state(sql, enrollment, sandbox, connection)
            if not state:
                raise ValueError("snapshot_state_unavailable")
            governed = candidate.scope.model_copy(update={"database": enrollment.sandbox_database})
            ids, proofs = [], []
            async with sql.connection(governed) as reader:
                for setting in (
                    f"SET resource_group='{group}'",
                    f"SET query_mem_limit={enrollment.budget.max_memory_bytes}",
                    f"SET query_timeout={enrollment.budget.timeout_seconds}",
                ):
                    await sql.execute(setting, governed, connection=reader, category="experiment")
                for rewrite in (False, True):
                    await sql.execute(
                        f"SET enable_materialized_view_rewrite={'true' if rewrite else 'false'}",
                        governed,
                        connection=reader,
                        category="experiment",
                    )
                    plan = await sql.execute(
                        "EXPLAIN " + replay,
                        governed,
                        connection=reader,
                        category="experiment",
                    )
                    if rewrite and not plan_uses_object(plan, object_name(candidate)):
                        raise ValueError("ranger_governed_rewrite_unproven")
                    for index in range(enrollment.budget.warmups + enrollment.budget.repetitions):
                        result = await sql.execute(
                            replay,
                            governed,
                            connection=reader,
                            category="experiment",
                            max_rows=enrollment.budget.max_result_rows,
                        )
                        proof = prove_result(
                            result.rows,
                            result.column_types,
                            ordered=deterministic_order(replay, result.columns, result.rows),
                            max_rows=enrollment.budget.max_result_rows,
                            max_bytes=enrollment.budget.max_result_bytes,
                            truncated=result.truncated,
                        )
                        if proof.reason or not result.engine_query_ids:
                            raise ValueError("ranger_complete_result_or_correlation_unavailable")
                        if index >= enrollment.budget.warmups:
                            proofs.append(proof)
                            ids.extend(result.engine_query_ids)
            if any(equivalent(proofs[0], value) is not True for value in proofs[1:]):
                raise ValueError("ranger_governed_result_changed")
            if await registered_snapshot_state(sql, enrollment, sandbox, connection) != state:
                raise ValueError("snapshot_changed")
            fresh = await inspect_object(sql, candidate, sandbox, connection)
            if not fresh.exists or not fresh.ready or fresh.binding != owned.binding:
                raise ValueError("materialized_view_rewrite_or_freshness_unproven")
            record = record.model_copy(
                update={
                    "availability": Availability.AVAILABLE,
                    "reason": None,
                    "query_ids": tuple(ids),
                    "summary": {
                        "scope": candidate.scope.model_dump(mode="json"),
                        "execution_scope": sandbox.model_dump(mode="json"),
                        "snapshot_id": enrollment.snapshot_id,
                        "snapshot_state": state,
                        "candidate_shape": digest(replay),
                        "row_filter": "PASS",
                        "masking": "PASS",
                        "semantics": "governed_result_preservation",
                        "policy_effects_matched": True,
                        "complete_result_comparisons": len(proofs),
                        "warmups_per_phase": enrollment.budget.warmups,
                        "repetitions_per_phase": enrollment.budget.repetitions,
                        "trial_object_binding": owned.binding,
                    },
                }
            )
            await persist()
            return record
    except BaseException as exc:
        from app.modules.query_autopilot.runtime import AuthorizationUnavailable

        reason = (
            str(exc) if isinstance(exc, ValueError) else "ranger_acceptance_operation_unavailable"
        )
        record = record.model_copy(
            update={
                "availability": Availability.UNAUTHORIZED
                if isinstance(exc, AuthorizationUnavailable)
                else Availability.UNAVAILABLE,
                "reason": reason
                if reason
                in {
                    "ranger_snapshot_security_effects_differ",
                    "ranger_security_effects_comparison_unsupported",
                    "snapshot_state_unavailable",
                    "ranger_filter_or_mask_acceptance_unproven",
                    "ranger_governed_rewrite_unproven",
                    "ranger_complete_result_or_correlation_unavailable",
                    "ranger_governed_result_changed",
                    "snapshot_changed",
                    "materialized_view_rewrite_or_freshness_unproven",
                }
                else "ranger_acceptance_operation_unavailable",
            }
        )
        await persist()
        raise
