"""Query-bound queue evidence on the explicitly selected isolated fixture stack."""

from __future__ import annotations

import asyncio
import json
import math
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from app.common.identifiers import check_identifier
from app.core.database import db
from app.modules.query.service import QueryService
from app.modules.query_autopilot.correctness import ResultProof, equivalent, prove_result
from app.modules.query_autopilot.detection import Facts, detect, diagnose, serialize_findings
from app.modules.query_autopilot.models import Policy, digest, utcnow
from app.modules.query_autopilot.profile import profile_summary
from app.modules.query_autopilot.statistics import Distribution, Window, compare_baseline
from app.modules.query_autopilot.telemetry import purpose


def blocker_statement(seconds: int) -> str:
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 10:
        raise ValueError("fixture_blocker_budget")
    return f"SELECT sleep({seconds}) FROM orders LIMIT 1"


def retained_history(value: dict, database: str, now: datetime) -> dict:
    before = value.get("phases", {}).get("uncontended", {})
    anchor = datetime.fromisoformat(value["history_anchor"])
    ids = value.get("durable_execution_ids", [])[:120]
    observations = before.get("observations", [])
    latencies = before.get("measured_latencies_ms", [])
    valid = (
        value.get("database") == database
        and value.get("statement") == "SELECT SUM(total) FROM orders"
        and value.get("wall_clock_history") is True
        and value.get("state") in {
            "COLLECTION_FAILED_CONFIGURATION_RESTORED", "CONFIGURATION_RESTORED",
            "DETECTION_COMPLETED_CONFIGURATION_RESTORED",
        }
        and before.get("results_equivalent") is True
        and value.get("snapshot_state")
        and len(ids) == len(set(ids)) == len(observations) == len(latencies) == 120
        and now - timedelta(days=7) < anchor < now - timedelta(minutes=90)
    )
    if not valid:
        raise ValueError("retained_history_unavailable")
    for index, observation in enumerate(observations):
        start = anchor + timedelta(minutes=30 * (index // 40))
        observed = datetime.fromisoformat(observation["observed_at"])
        latency = latencies[index]
        if (not start <= observed < start + timedelta(minutes=30)
                or observation.get("nova_execution_id") != ids[index]
                or not isinstance(latency, (int, float)) or isinstance(latency, bool)
                or not math.isfinite(latency) or latency < 0):
            raise ValueError("retained_history_binding_invalid")
    return before


def recent_window_anchor(now: datetime) -> datetime:
    anchor = now.replace(minute=30 * (now.minute // 30), second=0, microsecond=0)
    return anchor if now < anchor + timedelta(minutes=20) else anchor + timedelta(minutes=30)


def rolling_comparison_ready_at(observations: list[dict]) -> datetime:
    if len(observations) != 60:
        raise ValueError("two_complete_recent_batches_required")
    times = [datetime.fromisoformat(value["observed_at"]) for value in observations]
    if any(a > b for a, b in zip(times, times[1:], strict=False)):
        raise ValueError("recent_completion_order_unavailable")
    deadline = max(
        max(times[:30]) + timedelta(minutes=30, seconds=1),
        max(times[30:]) + timedelta(seconds=1),
    )
    if not all(deadline - timedelta(minutes=60) <= value < deadline - timedelta(minutes=30)
               for value in times[:30]) or not all(
        deadline - timedelta(minutes=30) <= value < deadline for value in times[30:]
    ):
        raise ValueError("recent_batches_cannot_form_adjacent_rolling_windows")
    return deadline


def current_regression_incidents(family: dict | None, incidents: list[dict]) -> list[dict]:
    from app.modules.query_autopilot.aggregation import window_start

    if not family or not family.get("baseline", {}).get("eligible") or not any(
        finding.get("detector") == "latency_regression"
        for finding in family.get("findings", [])
    ):
        return []
    try:
        current = window_start(datetime.fromisoformat(family["window"]))
    except (KeyError, TypeError, ValueError):
        return []
    matched = []
    for incident in incidents:
        if (incident.get("detector") != "latency_regression"
                or incident.get("family_id") != family.get("family_id")
                or incident.get("cohort_id") != family.get("cohort_id")):
            continue
        try:
            start = window_start(datetime.fromisoformat(incident["window"]))
        except (KeyError, TypeError, ValueError):
            continue
        if start == current:
            matched.append(incident)
    return matched


async def contention_case(
    database: str, *, wall_clock_history: bool = False, checkpoint: Path | None = None,
    persist_workload: bool = False, resume_history: Path | None = None,
    blocker_seconds: int = 1,
) -> dict:
    if os.getenv("NOVA_AUTOPILOT_FIXTURE_STACK") != "1" or not database.startswith("autopilot_"):
        raise ValueError("Explicit isolated retail fixture required")
    check_identifier(database, field="fixture database")
    blocker_sql = blocker_statement(blocker_seconds)
    if persist_workload and not wall_clock_history:
        raise ValueError("durable_workload_requires_natural_windows")
    from app.modules.query_autopilot.repository import repository
    from app.modules.query_autopilot.telemetry import collector

    if persist_workload and (not collector.enabled or not collector.queue.empty()):
        raise ValueError("dedicated_enabled_collector_required")
    durable_ids = []
    durable_scope = None
    durable_family = None
    password = os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
    saved, phases = {}, {}
    reference = None
    failure = None
    snapshot_state = None
    statement = "SELECT SUM(total) FROM orders"
    now = utcnow()
    anchor = recent_window_anchor(now)
    recent_anchor = anchor + timedelta(minutes=90)
    resumed = None
    if resume_history is not None:
        if not persist_workload or not wall_clock_history:
            raise ValueError("resume_requires_durable_natural_windows")
        resumed = json.loads(resume_history.read_text())
        phases["uncontended"] = retained_history(resumed, database, now)
        recent_anchor = recent_window_anchor(now)
        anchor = datetime.fromisoformat(resumed["history_anchor"])
        durable_ids = resumed["durable_execution_ids"][:120]
        reference = ResultProof(**{
            **resumed["reference_result"], "types": tuple(resumed["reference_result"]["types"]),
        })
        if reference.reason or not reference.digest:
            raise ValueError("retained_result_proof_unavailable")
        from app.sql_frontend.fingerprint import fingerprint

        expected_family = fingerprint(statement).family_id
        for identifier, observation in zip(
            durable_ids, phases["uncontended"]["observations"], strict=True,
        ):
            raw = await repository.get("observations", identifier)
            if (not raw or raw["observed_at"] != observation["observed_at"]
                    or tuple(raw["engine_query_ids"]) != (observation["query_id"],)
                    or raw["family_id"] != expected_family
                    or raw["scope"]["database"] != database
                    or raw["scope"]["principal"] != "autopilot_replay"
                    or raw["scope"]["active_role"] != "autopilot_replay_role"
                    or raw["status"] != "success" or raw["truncated"]):
                raise ValueError("retained_observation_unavailable")
            if durable_scope is not None and (
                raw["scope"] != durable_scope or raw["family_id"] != durable_family
            ):
                raise ValueError("retained_cohort_changed")
            durable_scope, durable_family = raw["scope"], raw["family_id"]
    new_executions = 60 if resumed else 180
    persisted_before = collector.persisted

    def save_checkpoint(state):
        if checkpoint is None:
            return
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        temporary = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "state": state,
                    "database": database,
                    "statement": statement,
                    "reference_result": asdict(reference) if reference else None,
                    "snapshot_state": snapshot_state,
                    "failure_type": failure,
                    "history_anchor": anchor.isoformat(),
                    "recent_anchor": recent_anchor.isoformat(),
                    "resumed_history": resume_history is not None,
                    "saved_queue_settings": saved,
                    "blocker_seconds": blocker_seconds,
                    "phases": phases,
                    "wall_clock_history": wall_clock_history,
                    "durable_execution_ids": durable_ids,
                    "collector_counters": {
                        "newly_persisted": collector.persisted - persisted_before,
                        "accepted": collector.accepted,
                        "dropped": collector.dropped,
                        "failed_batches": collector.failed_batches,
                        "queued": collector.queue.qsize(),
                    },
                },
                indent=2,
            ) + "\n"
        )
        temporary.replace(checkpoint)

    async def execute(connection, principal, role, sql, *, workload=False):
        with purpose("workload" if workload else "experiment"):
            result = await QueryService().execute(
                sql, principal, "", database=database, role=role, connection=connection
            )
        if not result.success or result.truncated:
            raise ValueError("fixture_execution_failed_or_truncated")
        return result

    def proof(result):
        return prove_result(
            result.rows, result.column_types, ordered=False, max_rows=100, max_bytes=10000
        )

    async with (
        db.user_conn("nova_admin", password) as admin,
        db.user_conn("autopilot_replay", password) as first,
        db.user_conn("autopilot_replay", password) as second,
    ):

        async def a(sql):
            return await execute(admin, "nova_admin", "ACCOUNTADMIN", sql)

        async def q(connection, sql, *, workload=False):
            return await execute(
                connection, "autopilot_replay", "autopilot_replay_role", sql, workload=workload,
            )

        async def snapshot():
            partitions = await q(second, "SHOW PARTITIONS FROM orders")
            indexes = [
                partitions.columns.index(key) for key in ("PartitionId", "VisibleVersion")
            ]
            if not partitions.rows:
                raise ValueError("snapshot_state_unavailable")
            return digest([[row[i] for i in indexes] for row in partitions.rows])

        try:
            settings = {
                "enable_group_level_query_queue": "true",
                "enable_query_queue_select": "true",
                "query_queue_concurrency_limit": "1",
            }
            for name in settings:
                result = await a(f"SHOW GLOBAL VARIABLES LIKE '{name}'")
                saved[name] = result.rows[0][1]
            save_checkpoint("CONFIGURATION_SAVED")
            for name, value in settings.items():
                await a(f"SET GLOBAL {name}={value}")
            save_checkpoint("COLLECTING")
            for connection in (first, second):
                await q(connection, "SET resource_group='autopilot_sandbox'")
                await q(connection, "SET enable_profile=true")
                await q(connection, "SET query_timeout=15")
                await q(connection, "SET wait_timeout=10800")
                await q(connection, "SET query_mem_limit=536870912")
            snapshot_state = await snapshot()
            if resumed and snapshot_state != resumed["snapshot_state"]:
                raise ValueError("retained_snapshot_changed")
            save_checkpoint("COLLECTING")
            for phase in (("contended",) if resumed else ("uncontended", "contended")):
                samples, observations, matches = [], [], []
                for iteration in range(123 if phase == "uncontended" else 63):
                    measured_index = iteration - 3
                    batch = (
                        measured_index // 40 if phase == "uncontended" else 3 + measured_index // 30
                    )
                    if wall_clock_history and measured_index >= 0:
                        boundary = (
                            anchor + timedelta(minutes=30 * batch) if phase == "uncontended"
                            else recent_anchor + timedelta(minutes=30 * (batch - 3))
                        )
                        while utcnow() < boundary:
                            await asyncio.sleep(min(30, (boundary - utcnow()).total_seconds()))
                        if utcnow() >= boundary + timedelta(minutes=30):
                            raise ValueError("history_window_expired")
                        if measured_index % (40 if phase == "uncontended" else 30) == 0:
                            if await snapshot() != snapshot_state:
                                raise ValueError("snapshot_changed")
                            print(f"Wall-clock regression batch {batch + 1}/5 started", flush=True)
                    observed_at = utcnow()
                    rows = []
                    if phase == "contended":
                        blocker = asyncio.create_task(
                            q(first, blocker_sql)
                        )
                        target = None
                        try:
                            async with asyncio.timeout(15):
                                while not blocker.done():
                                    running = await a("SHOW RUNNING QUERIES")
                                    if any(
                                        dict(zip(running.columns, row, strict=True))["State"]
                                        == "RUNNING"
                                        for row in running.rows
                                    ):
                                        break
                                    await asyncio.sleep(0.025)
                                target = asyncio.create_task(q(
                                    second, statement, workload=persist_workload and iteration >= 3,
                                ))
                                while not target.done():
                                    running = await a("SHOW RUNNING QUERIES")
                                    rows = [
                                        dict(zip(running.columns, row, strict=True))
                                        for row in running.rows
                                    ]
                                    if any(row["State"] == "PENDING" for row in rows):
                                        break
                                    await asyncio.sleep(0.025)
                                result = await target
                                blocker_result = await blocker
                        finally:
                            # Finish or cancel only the two fixture operations before
                            # restoring queue settings or releasing their connections.
                            tasks = [task for task in (blocker, target) if task is not None]
                            for task in tasks:
                                if not task.done():
                                    task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                    else:
                        result = await q(
                            second, statement, workload=persist_workload and iteration >= 3,
                        )
                    if reference is None:
                        reference = proof(result)
                    matches.append(equivalent(reference, proof(result)) is True)
                    if iteration < 3:
                        continue
                    samples.append(result.engine_roundtrip_ms)
                    if persist_workload:
                        await collector.flush(repository)
                        raw = await repository.get("observations", result.nova_execution_id)
                        if (not raw or tuple(raw["engine_query_ids"])
                                != tuple(result.engine_query_ids)):
                            raise ValueError("workload_observation_correlation_unavailable")
                        durable_ids.append(raw["id"])
                        durable_scope, durable_family = raw["scope"], raw["family_id"]
                    identifier = result.engine_query_ids[0]
                    await asyncio.sleep(0.2)
                    profile = await q(second, f"SELECT get_query_profile('{identifier}')")
                    facts = profile_summary("\n".join(str(v) for row in profile.rows for v in row))[
                        "facts"
                    ]
                    pending = any(
                        row["QueryId"] == identifier and row["State"] == "PENDING" for row in rows
                    )
                    occupied = phase == "contended" and any(
                        row["QueryId"] in blocker_result.engine_query_ids
                        and row["State"] == "RUNNING"
                        and int(row["Slots"]) >= 1
                        for row in rows
                    )
                    if persist_workload:
                        from app.modules.query_autopilot.models import Evidence, Scope

                        scope = Scope.model_validate(durable_scope)
                        record = Evidence(
                            id="pipeline-profile-" + result.nova_execution_id,
                            family_id=durable_family, cohort_id=scope.cohort_id,
                            kind="profile", availability="available",
                            expires_at=utcnow() + timedelta(hours=24),
                            source="isolated_live_workload_profile",
                            query_ids=tuple(result.engine_query_ids),
                            summary={"observed_at": raw["observed_at"], "facts": facts},
                        )
                        await repository.put(
                            "evidence", record.id, record.model_dump(mode="json"),
                            family_id=record.family_id, cohort_id=record.cohort_id,
                            expires_at=record.expires_at,
                        )
                        record = record.model_copy(update={
                            "id": "pipeline-queue-" + result.nova_execution_id,
                            "kind": "resource_group", "source": "show_running_queries",
                            "summary": {"observed_at": raw["observed_at"],
                                        "facts": {"saturated": pending and occupied}},
                        })
                        await repository.put(
                            "evidence", record.id, record.model_dump(mode="json"),
                            family_id=record.family_id, cohort_id=record.cohort_id,
                            expires_at=record.expires_at,
                        )
                    observations.append(
                        {
                            "query_id": identifier,
                            "observed_at": raw["observed_at"] if persist_workload
                            else observed_at.isoformat(),
                            **({"nova_execution_id": result.nova_execution_id}
                               if persist_workload else {}),
                            "facts": facts,
                            "pending_observed": pending,
                            "limit_occupied": occupied,
                        }
                    )
                    phases[phase] = {
                        "count": len(samples),
                        "warmups": 3,
                        "measured_latencies_ms": samples,
                        "observations": observations,
                        "results_equivalent": all(matches),
                    }
                    save_checkpoint("COLLECTING")
                if await snapshot() != snapshot_state:
                    raise ValueError("snapshot_changed")
                latency = Distribution()
                for duration in samples:
                    latency.add(duration)
                bound = [o for o in observations if o["pending_observed"] and o["limit_occupied"]]
                supported = bound[0] if bound else observations[0]
                measured = supported["facts"]
                facts = Facts(
                    latency,
                    compare_baseline(Window(utcnow(), latency), []),
                    evidence_ids=(supported["query_id"],),
                    queue_ms=measured.get("queue_ms"),
                    execution_ms=measured.get("execution_ms"),
                    saturated=bool(bound),
                )
                findings = detect(facts, Policy())
                phases[phase] = {
                    "count": len(samples),
                    "warmups": 3,
                    "latency": latency.as_dict(),
                    "measured_latencies_ms": samples,
                    "observations": observations,
                    "results_equivalent": all(matches),
                    "findings": serialize_findings(findings),
                    "diagnosis": [asdict(d) for d in diagnose(findings, facts)],
                }
                save_checkpoint("COLLECTING")
        except BaseException as exc:
            failure = type(exc).__name__
            save_checkpoint("COLLECTION_FAILED")
            raise
        finally:
            # Reconnect: an engine restart can invalidate all measurement sessions.
            try:
                async with db.user_conn("nova_admin", password) as restore_connection:
                    for name, value in saved.items():
                        await execute(
                            restore_connection, "nova_admin", "ACCOUNTADMIN",
                            f"SET GLOBAL {name}={value}",
                        )
                        restored = await execute(
                            restore_connection, "nova_admin", "ACCOUNTADMIN",
                            f"SHOW GLOBAL VARIABLES LIKE '{name}'",
                        )
                        if str(restored.rows[0][1]).lower() != str(value).lower():
                            raise ValueError("fixture_configuration_restoration_unverified")
                save_checkpoint("COLLECTION_FAILED_CONFIGURATION_RESTORED" if failure
                                else "CONFIGURATION_RESTORED")
            except Exception:
                save_checkpoint("RESTORATION_FAILED")
                raise
    durable_pipeline = None
    if persist_workload:
        from app.modules.query_autopilot.aggregation import aggregate
        from app.modules.query_autopilot.models import Scope

        deadline = rolling_comparison_ready_at(phases["contended"]["observations"])
        save_checkpoint("AWAITING_ROLLING_COMPARISON_CONFIGURATION_RESTORED")
        print("Awaiting two populated rolling comparison windows", flush=True)
        while utcnow() < deadline:
            await asyncio.sleep(min(30, (deadline - utcnow()).total_seconds()))
        scope = Scope.model_validate(durable_scope)
        await aggregate(repository)
        family = await repository.get("families", digest([durable_family, scope.cohort_id]))
        incidents = await repository.page(
            "incidents", family_id=durable_family, cohort_id=scope.cohort_id, limit=1000,
        )
        baseline = family.get("baseline", {}) if family else {}
        regression_incidents = current_regression_incidents(family, incidents)
        durable_pipeline = {
            "status": "PASS" if (len(durable_ids) == 180
                and collector.persisted - persisted_before == new_executions
                and collector.dropped == 0
                and collector.failed_batches == 0 and baseline.get("eligible")
                and regression_incidents) else "FAIL",
            "history_source": "query_service_collector_real_completion_timestamps",
            "executions": len(durable_ids), "persisted": len(durable_ids),
            "newly_persisted": collector.persisted - persisted_before,
            "retained_history_revalidated": 120 if resumed else 0,
            "dropped": collector.dropped, "failed_batches": collector.failed_batches,
            "family_id": durable_family, "scope": durable_scope,
            "current_regression_incident_ids": [i["id"] for i in regression_incidents],
            "family": family, "incidents": incidents,
        }
        save_checkpoint("DETECTION_COMPLETED_CONFIGURATION_RESTORED")
    positive = phases["contended"]
    negative = phases["uncontended"]
    return {
        "case": "G",
        "ground_truth": "controlled_global_admission_contention",
        "status": "PASS"
        if (
            positive["diagnosis"][0]["category"] == "RESOURCE_CONTENTION"
            and not any(f["detector"] == "contention" for f in negative["findings"])
            and positive["results_equivalent"]
            and negative["results_equivalent"]
        )
        else "FAIL",
        "evidence_kind": "real_native_engine_controlled_fixture",
        "phases": phases,
        "queue_limit": 1,
        "blocker_seconds": blocker_seconds,
        "queue_settings_restored": True,
        "wall_clock_history": wall_clock_history,
        "history_anchor": anchor.isoformat(),
        "recent_anchor": recent_anchor.isoformat(),
        "snapshot_state": snapshot_state,
        "production_actions": 0,
        "durable_pipeline": durable_pipeline,
    }
