"""Run the complete sandbox MV trial on an explicitly created retail fixture.

Native replay proves execution, typed result comparison and rewrite only. It
must remain INCONCLUSIVE without separately collected patched-FE policy proof.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core.database import db
from app.modules.query_autopilot.engine_state import compensation_sql, inspect_object
from app.modules.query_autopilot.models import (
    Availability,
    Budget,
    Candidate,
    Enrollment,
    Evidence,
    Scope,
    digest,
    utcnow,
)
from app.modules.query_autopilot.repository import repository
from app.modules.query_autopilot.runtime import AuthorizedSQL
from app.modules.query_autopilot.schema import ensure_schema
from app.modules.query_autopilot.service import AutopilotService
from app.modules.task_orchestration.credentials import StaticCredentialProvider
from app.modules.task_orchestration.execution import DelegateExecutor
from app.sql_frontend.fingerprint import fingerprint


async def run(manifest: Path, output: Path) -> dict:
    if os.getenv("NOVA_AUTOPILOT_FIXTURE_STACK") != "1" or settings.RANGER_ENABLED:
        raise ValueError("Explicit isolated native fixture stack required")
    fixture = json.loads(manifest.read_text())
    if not all(fixture[k].startswith("autopilot_") for k in ("database", "sandbox")):
        raise ValueError("Not an Autopilot fixture")
    password = os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
    runtime = AuthorizedSQL(
        DelegateExecutor(
            StaticCredentialProvider(
                {
                    "autopilot_executive": password,
                    "autopilot_replay": password,
                }
            )
        )
    )
    service = AutopilotService(sql=runtime)
    prefix = "native-trial-" + uuid4().hex
    scope = Scope(
        principal="autopilot_executive",
        active_role="autopilot_executive",
        security_context_version=1,
        database=fixture["database"],
    )
    sandbox = Scope(
        principal="autopilot_replay",
        active_role="autopilot_replay_role",
        security_context_version=1,
        database=fixture["sandbox"],
    )
    target = "SELECT store_id,COUNT(*) AS n,SUM(total) AS revenue FROM orders GROUP BY store_id"
    control = "SELECT COUNT(*) AS n FROM customers"
    family = fingerprint(target).family_id
    control_family = fingerprint(control).family_id
    enrollment = Enrollment(
        id=prefix,
        scope=scope,
        sandbox_database=fixture["sandbox"],
        table_mapping={
            "orders": fixture["sandbox"] + ".orders",
            "customers": fixture["sandbox"] + ".customers",
        },
        snapshot_id=digest(fixture),
        snapshot_created_at=utcnow(),
        snapshot_frozen=True,
        execution_principal=sandbox.principal,
        execution_role=sandbox.active_role,
        budget=Budget(resource_group="autopilot_sandbox"),
        replay_opt_in=True,
        control_families=(control_family,),
    )
    await db.init_system_pool()
    try:
        await ensure_schema()
        policy = await service.policy()
        await repository.put("enrollments", prefix, enrollment.model_dump(mode="json"))
        sample_ids = []
        for index, (identifier, statement) in enumerate(
            ((family, target), (control_family, control))
        ):
            key = prefix + f"-sample-{index}"
            ref = service.payloads.reference(key)
            record = Evidence(
                id=key,
                family_id=identifier,
                cohort_id=scope.cohort_id,
                kind="replay_sample",
                availability="unavailable",
                source="explicit_fixture_opt_in",
                expires_at=utcnow() + timedelta(hours=24),
                payload_ref=ref,
            )
            await repository.put(
                "evidence",
                key,
                record.model_dump(mode="json"),
                family_id=identifier,
                cohort_id=scope.cohort_id,
            )
            await service.payloads.put(key, statement)
            record = record.model_copy(update={"availability": Availability.AVAILABLE})
            await repository.put(
                "evidence",
                key,
                record.model_dump(mode="json"),
                family_id=identifier,
                cohort_id=scope.cohort_id,
            )
            sample_ids.append(key)
        candidate = Candidate(
            id=prefix,
            family_id=family,
            scope=scope,
            kind="MATERIALIZED_VIEW",
            targets=("orders",),
            evidence_ids=(sample_ids[0],),
            enrollment_id=prefix,
            enrollment_version=1,
            policy_version=policy.version,
            parameters={"name": "nova_ap_" + uuid4().hex[:20]},
        )
        candidate = candidate.model_copy(
            update={"evidence_digest": await service.evidence_digest(candidate)}
        )
        await repository.put(
            "opportunities",
            prefix,
            candidate.model_dump(mode="json"),
            family_id=family,
            cohort_id=scope.cohort_id,
        )
        job = {"id": prefix, "kind": "experiment", "candidate_id": prefix}
        operation = await service.run_experiment(candidate, enrollment, policy, job)
        experiment = await repository.get("experiments", prefix)
        final = await repository.get("opportunities", prefix)
        result = {
            "evidence_kind": "real_native_engine_sandbox",
            "candidate_id": prefix,
            "operation": operation,
            "candidate_state": final["state"],
            "experiment": experiment,
            "ranger_acceptance": "NOT_RUN",
            "production_actions": 0,
            "acceptance": "PASS"
            if (
                experiment
                and experiment["state"] == "INCONCLUSIVE"
                and experiment["result"]["correctness"] == "EQUIVALENT"
                and experiment["result"]["repetitions"] == 30
                and experiment["result"]["reason"]
                == "ranger_acceptance_unavailable"
                and final["state"] == "INCONCLUSIVE"
            )
            else "FAIL",
        }
        async with runtime.connection(sandbox) as connection:
            current = await inspect_object(runtime, candidate, sandbox, connection)
            result["sandbox_object_ready"] = bool(
                ((experiment or {}).get("owned_object") or {}).get("ready")
            )
            plans = (experiment or {}).get("plans", {})
            name = candidate.parameters["name"]
            result["native_rewrite_observed"] = name in json.dumps(plans.get("after", {}))
            result["fixture_object_removed"] = not current.exists
            if current.exists:
                await runtime.execute(
                    compensation_sql(candidate),
                    sandbox,
                    connection=connection,
                    category="experiment",
                    confirm=True,
                )
                result["fixture_object_removed"] = not (
                    await inspect_object(runtime, candidate, sandbox, connection)
                ).exists
        output.mkdir(parents=True, exist_ok=True)
        (output / "experiment-native.json").write_text(json.dumps(result, indent=2) + "\n")
        trial = (experiment or {}).get("result", {})
        text = (
            "# Native sandbox experiment\n\n"
            f"- Acceptance: {result['acceptance']}\n"
            f"- Measured repetitions per phase: {trial.get('repetitions', 0)}\n"
            f"- Correctness: {trial.get('correctness', 'unavailable')}\n"
            f"- Native rewrite observed: {result['native_rewrite_observed']}\n"
            f"- Candidate: {final['state']}\n"
            "- Ranger acceptance: NOT_RUN; no production application is allowed.\n"
        )
        (output / "experiment-native.md").write_text(text)
        print(text)
        return result
    finally:
        await db.close_system_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.manifest, args.output))
    if result["acceptance"] != "PASS":
        raise SystemExit(1)
