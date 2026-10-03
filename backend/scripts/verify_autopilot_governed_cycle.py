"""Explicit governed fixture trial, approval, application and natural-window verification."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_ranger_e2e import _inherit_pid_one_environment  # noqa: E402


def registered_fixture(
    candidate: dict, enrollment: dict, intent: dict, trial: dict, *, sales: int, customers: int,
) -> tuple[str, str, str]:
    identifier = candidate.get("id", "")
    if not isinstance(identifier, str) or not re.fullmatch(
        r"governed-cycle-[0-9a-f]{12}", identifier,
    ):
        raise ValueError("registered_fixture_identity_invalid")
    suffix = identifier.removeprefix("governed-cycle-")
    database = "autopilot_gov_" + suffix
    snapshot = database + "_snapshot"
    group = "autopilot_gov_budget_" + suffix
    if not (
        candidate.get("state") == "INCONCLUSIVE"
        and candidate.get("kind") == "MATERIALIZED_VIEW"
        and candidate.get("scope", {}).get("database") == database
        and candidate.get("parameters", {}).get("name") == "nova_ap_" + suffix
        and candidate.get("enrollment_id") == enrollment.get("id") == identifier
        and enrollment.get("scope") == candidate.get("scope")
        and enrollment.get("sandbox_database") == snapshot
        and enrollment.get("execution_principal") == "nova_admin"
        and enrollment.get("execution_role") == "autopilot_gov_builder"
        and enrollment.get("budget", {}).get("resource_group") == group
        and intent.get("id") == identifier + "-setup"
        and intent.get("kind") == "fixture_setup"
        and intent.get("databases") == [database, snapshot]
        and intent.get("fixture_rows") == {"sales": sales, "customers": customers}
        and bool(trial.get("id"))
        and candidate.get("experiment_id") in {None, trial.get("id")}
        and trial.get("candidate_id") == identifier
        and trial.get("state") in {"NO_IMPROVEMENT", "REGRESSED"}
        and trial.get("cleanup") == "verified_object_removed"
        and trial.get("result", {}).get("correctness") == "EQUIVALENT"
    ):
        raise ValueError("registered_fixture_provenance_or_cleanup_unavailable")
    return database, snapshot, group


async def run(
    output: Path, sales: int, customers: int, *, post_apply_contention: bool = False,
    repetitions: int = 30, reuse_fixture: str | None = None,
) -> dict:
    _inherit_pid_one_environment()
    from app.core.config import settings
    from app.core.database import db
    from app.core.redis import session_store
    from app.core.security import encrypt_password
    from app.modules.access_control.security_context import SecurityContext
    from app.modules.access_control.service import access_control_service as access
    from app.modules.query_autopilot.correctness import equivalent, prove_result
    from app.modules.query_autopilot.evidence import EvidenceCollector
    from app.modules.query_autopilot.jobs import AutopilotWorker
    from app.modules.query_autopilot.models import (
        Budget,
        Candidate,
        Enrollment,
        Evidence,
        Scope,
        State,
        digest,
        utcnow,
    )
    from app.modules.query_autopilot.repository import repository
    from app.modules.query_autopilot.runtime import (
        AuthorizationUnavailable,
        AuthorizedSQL,
        current_policy_revision,
    )
    from app.modules.query_autopilot.schema import ensure_schema
    from app.modules.query_autopilot.service import AutopilotService, public_record
    from app.modules.query_autopilot.telemetry import collector
    from app.modules.task_orchestration.credentials import StaticCredentialProvider
    from app.modules.task_orchestration.execution import DelegateExecutor
    from app.sql_frontend.fingerprint import fingerprint

    if os.getenv("NOVA_AUTOPILOT_FIXTURE_STACK") != "1" or not settings.RANGER_ENABLED:
        raise ValueError("Explicit isolated patched-FE fixture required")
    if not 30 <= repetitions <= 100:
        raise ValueError("fixture_repetition_budget")
    if reuse_fixture is not None and not re.fullmatch(
        r"governed-cycle-[0-9a-f]{12}", reuse_fixture,
    ):
        raise ValueError("registered_fixture_identity_invalid")
    if not 1000 <= sales <= 1000000 or not 100 <= customers <= 1000:
        raise ValueError("fixture_row_budget")
    suffix = uuid4().hex[:12]
    prefix, database = "governed-cycle-" + suffix, "autopilot_gov_" + suffix
    snapshot = database + "_snapshot"
    group, builder = "autopilot_gov_budget_" + suffix, "autopilot_gov_builder"
    password = os.environ["NOVA_ADMIN_TEST_PASSWORD"]
    runtime = AuthorizedSQL(
        DelegateExecutor(
            StaticCredentialProvider(
                {
                    "nova_admin": password,
                    "alice": "NovaAlice2026!",
                }
            )
        )
    )
    service = AutopilotService(sql=runtime)
    await db.init_system_pool()
    await session_store.init()
    session_id = None
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "RUNNING",
        "evidence_kind": "isolated_governed_complete_cycle",
        "database": database,
        "snapshot": snapshot,
        "fixture_rows": {"sales": sales, "customers": customers},
        "fixture_phone_type": "VARCHAR(32)",
        "repetitions": repetitions,
        "reused_fixture": reuse_fixture,
        "candidate_id": prefix,
        "production_actions": 0,
        "fixture_actions": 0,
        "expected_outcome": "REGRESSED" if post_apply_contention else "SUCCESS",
    }

    def checkpoint(phase):
        report["phase"] = phase
        temporary = output / "governed-cycle-progress.json.tmp"
        temporary.write_text(json.dumps(public_record(report), indent=2) + "\n")
        temporary.replace(output / "governed-cycle-progress.json")
        print(phase, flush=True)

    def proof(result):
        return prove_result(
            result.rows,
            result.column_types,
            ordered=False,
            max_rows=200000,
            max_bytes=33554432,
            truncated=result.truncated,
        )

    try:
        await ensure_schema()
        if reuse_fixture:
            prior = await repository.get("opportunities", reuse_fixture) or {}
            enrollment_record = await repository.get("enrollments", prior.get("enrollment_id", ""))
            setup_record = await repository.get("jobs", reuse_fixture + "-setup")
            trials = await repository.page(
                "experiments", family_id=prior.get("family_id", ""),
                cohort_id=prior.get("scope") and Scope.model_validate(prior["scope"]).cohort_id,
                limit=100,
            )
            owned_trials = [item for item in trials if item.get("candidate_id") == reuse_fixture]
            trial_record = max(owned_trials, key=lambda item: item.get("finished_at", ""),
                               default={})
            database, snapshot, group = registered_fixture(
                prior, enrollment_record or {}, setup_record or {}, trial_record or {},
                sales=sales, customers=customers,
            )
            report.update(database=database, snapshot=snapshot)
        admin = Scope(
            principal="nova_admin",
            active_role="ACCOUNTADMIN",
            security_context_version=1,
            database="analytics",
            policy_revision=await current_policy_revision(),
        )
        async with runtime.connection(admin) as connection:
            await runtime.execute("SELECT 1", admin, connection=connection, category="diagnostic")
        if reuse_fixture:
            from app.modules.query_autopilot.engine_state import inspect_object

            owner_scope = admin.model_copy(update={"database": database})
            async with runtime.connection(owner_scope) as connection:
                existing = await inspect_object(
                    runtime, Candidate.model_validate(prior), owner_scope, connection,
                )
            if existing.exists:
                raise ValueError("registered_fixture_cleanup_changed")
        security = SecurityContext(principal="nova_admin", active_role="ACCOUNTADMIN")
        await repository.put(
            "jobs",
            prefix + "-setup",
            {
                "id": prefix + "-setup",
                "state": "INTENT",
                "kind": "fixture_setup",
                "databases": [database, snapshot],
                "fixture_rows": report["fixture_rows"],
            },
            state="INTENT",
        )
        checkpoint("FIXTURE_SETUP_INTENT")
        reference = await service.payloads.put(prefix + "-storage-preflight", "SELECT 1")
        try:
            stored = await service.payloads.get(
                reference, expires_at=utcnow() + timedelta(minutes=10), now=utcnow(),
            )
            if stored != "SELECT 1":
                raise ValueError("fixture_managed_storage_roundtrip_unavailable")
        finally:
            await service.payloads.delete(reference)
        report["managed_storage_preflight"] = "PASS"
        if reuse_fixture is None:
            await access.create_role(security, builder, "Isolated Autopilot fixture builder")
            await access.assign_role(security, role=builder, username="nova_admin")
            async with db.user_conn("nova_admin", password) as connection:
                from app.modules.query.service import QueryService
                from app.modules.query_autopilot.telemetry import purpose

                async def setup_sql(statement, target):
                    with purpose("maintenance"):
                        result = await QueryService().execute(
                            statement,
                            "nova_admin",
                            "",
                            role="ACCOUNTADMIN",
                            database=target,
                            connection=connection,
                            confirm_destructive=True,
                        )
                    if not result.success or result.needs_confirmation:
                        raise ValueError("fixture_setup_sql_unavailable")
                    return result

                for target in (database, snapshot):
                    await setup_sql(f"CREATE DATABASE `{target}`", "analytics")
                    for table, columns in (
                        ("sales", "id BIGINT,city VARCHAR(16),amount DECIMAL(10,2)"),
                        ("customers", "id BIGINT,city VARCHAR(16),phone VARCHAR(32)"),
                    ):
                        await setup_sql(
                            f"CREATE TABLE `{target}`.`{table}` ({columns}) DUPLICATE KEY(id) "
                            "DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1')",
                            target,
                        )
                for table, size in (("sales", sales), ("customers", customers)):
                    for start in range(0, size, 2000):
                        rows = []
                        for index in range(start, min(start + 2000, size)):
                            city = "Jakarta" if index % 2 else "Bandung"
                            tail = (str(100 + index % 100) if table == "sales"
                                    else f"'+62-{index:09d}'")
                            rows.append(f"({index + 1},'{city}',{tail})")
                        await setup_sql(f"INSERT INTO `{table}` VALUES " + ",".join(rows), database)
                    await setup_sql(
                        f"INSERT INTO `{snapshot}`.`{table}` SELECT * FROM `{database}`.`{table}`",
                        snapshot,
                    )
                await setup_sql(
                    f"CREATE RESOURCE GROUP IF NOT EXISTS {group} TO (user='nova_admin') "
                    "WITH ('cpu_weight'='1','mem_limit'='10%','concurrency_limit'='2')",
                    database,
                )
            # Fixture grants and policies are explicit setup, never an enrollment side effect.
            for target in (database, snapshot):
                await access.grant_access(
                    security,
                    role="marketing",
                    catalog="default_catalog",
                    database=target,
                    table="*",
                    accesses=["select"],
                )
                if target == snapshot:
                    for table in ("sales", "customers"):
                        await access.grant_access(
                            security, role=builder, catalog="default_catalog",
                            database=target, table=table, accesses=["select"],
                        )
                    for resource_type, accesses in (
                        ("database", ["create table", "create materialized view"]),
                        ("table", ["drop", "alter", "refresh", "insert"]),
                        ("materialized_view", ["drop", "alter", "refresh"]),
                    ):
                        await access.grant_access(
                            security, role=builder, catalog="default_catalog",
                            database=target, table="*", accesses=accesses,
                            resource_type=resource_type,
                        )
                await access.put_data_scope(
                    security,
                    principal="alice",
                    role="marketing",
                    catalog="default_catalog",
                    database=target,
                    table="sales",
                    bindings=[("city", "city", ["Jakarta"])],
                )
                await access.put_mask(
                    security,
                    role="marketing",
                    catalog="default_catalog",
                    database=target,
                    table="customers",
                    column="phone",
                    mask_type="MASK",
                )
            await asyncio.sleep(5)
        revision = await current_policy_revision()
        if not revision:
            raise ValueError("fixture_policy_revision_unavailable")
        admin = admin.model_copy(update={"policy_revision": revision})
        scope = Scope(
            principal="alice",
            active_role="marketing",
            security_context_version=1,
            database=database,
            policy_revision=revision,
        )
        collector.policy_revision = revision
        reader_ready = False
        raw_phones = {f"+62-{index:09d}" for index in range(customers)}
        for _ in range(24):
            try:
                async with runtime.connection(scope) as source_reader:
                    source_count = await runtime.execute(
                        "SELECT COUNT(*) FROM sales",
                        scope,
                        connection=source_reader,
                        category="diagnostic",
                    )
                    source_phones = await runtime.execute(
                        "SELECT DISTINCT phone FROM customers",
                        scope,
                        connection=source_reader,
                        category="diagnostic",
                    )
                bounded = scope.model_copy(
                    update={
                        "principal": "nova_admin",
                        "active_role": builder,
                        "database": snapshot,
                    }
                )
                async with runtime.connection(bounded) as bounded_reader:
                    builder_count = await runtime.execute(
                        "SELECT COUNT(*) FROM sales",
                        bounded,
                        connection=bounded_reader,
                        category="diagnostic",
                    )
                reader_ready = (
                    source_count.rows == [[sales // 2]]
                    and builder_count.rows == [[sales]]
                    and source_phones.rows
                    and not source_phones.truncated
                    and not any(row[0] in raw_phones for row in source_phones.rows)
                )
                if reader_ready:
                    break
            except ValueError:
                pass
            await asyncio.sleep(5)
        if not reader_ready:
            raise ValueError("fixture_row_filter_mask_or_builder_access_not_observed")
        builder_source = bounded.model_copy(update={"database": database})
        try:
            async with runtime.connection(builder_source) as connection:
                await runtime.execute(
                    "SELECT COUNT(*) FROM sales", builder_source,
                    connection=connection, category="diagnostic",
                )
        except AuthorizationUnavailable:
            pass
        else:
            raise ValueError("fixture_builder_can_access_source_database")
        report["fixture_security_probes"] = {
            "source_filtered_sales": sales // 2,
            "builder_snapshot_sales": sales,
            "raw_phone_values_absent": True,
            "builder_source_access": "DENIED",
        }
        session_id = await session_store.create(
            "nova_admin", encrypt_password(password), ["ACCOUNTADMIN"],
            default_role="ACCOUNTADMIN", active_role="ACCOUNTADMIN",
        )
        user = {**await session_store.get(session_id), "session_id": session_id}
        target = (
            "SELECT LENGTH(c.phone) AS phone_length,SUM(s.amount) AS total "
            "FROM sales s INNER JOIN customers c "
            "ON s.city=c.city GROUP BY s.city,c.phone"
        )
        control = "SELECT COUNT(*) AS n FROM customers"
        family, control_family = fingerprint(target).family_id, fingerprint(control).family_id
        enrollment = await service.save_enrollment(
            Enrollment(
                id=prefix,
                scope=scope,
                sandbox_database=snapshot,
                table_mapping={"sales": snapshot + ".sales", "customers": snapshot + ".customers"},
                snapshot_id=prefix,
                snapshot_created_at=utcnow(),
                snapshot_frozen=True,
                execution_principal="nova_admin",
                execution_role=builder,
                budget=Budget(resource_group=group, repetitions=repetitions),
                replay_opt_in=True,
                control_families=(control_family,),
            ),
            user,
        )
        sample_ids = []
        for identifier, statement in ((family, target), (control_family, control)):
            key = prefix + "-sample-" + str(len(sample_ids))
            evidence = Evidence(
                id=key,
                family_id=identifier,
                cohort_id=scope.cohort_id,
                kind="replay_sample",
                source="explicit_governed_fixture_opt_in",
                availability="unavailable",
                expires_at=utcnow() + timedelta(hours=24),
                payload_ref=service.payloads.reference(key),
            )
            await repository.put(
                "evidence",
                key,
                evidence.model_dump(mode="json"),
                family_id=identifier,
                cohort_id=scope.cohort_id,
            )
            await service.payloads.put(key, statement)
            await repository.put(
                "evidence",
                key,
                evidence.model_copy(
                    update={"availability": "available"},
                ).model_dump(mode="json"),
                family_id=identifier,
                cohort_id=scope.cohort_id,
            )
            sample_ids.append(key)
        candidate = await service.save_candidate(
            Candidate(
                id=prefix,
                family_id=family,
                scope=scope,
                kind="MATERIALIZED_VIEW",
                targets=("sales", "customers"),
                evidence_ids=(sample_ids[0],),
                enrollment_id=prefix,
                enrollment_version=enrollment.version,
                policy_version=(await service.policy()).version,
                parameters={"name": "nova_ap_" + suffix},
            ),
            user,
        )
        worker = AutopilotWorker(service.client, service)
        checkpoint("ENROLLED")
        await service.mutate(
            prefix,
            "experiment",
            version=candidate.version,
            idempotency_key=prefix + "-trial",
            user=user,
        )
        await worker.run_once()
        candidate = Candidate.model_validate(await repository.get("opportunities", prefix))
        experiments = await repository.page(
            "experiments", family_id=family, cohort_id=scope.cohort_id, limit=100
        )
        report["experiments"] = experiments
        report["candidate"] = candidate.model_dump(mode="json")
        report["ranger_acceptance"] = []
        for identifier in candidate.evidence_ids:
            item = await repository.get("evidence", identifier)
            if item and item.get("kind") == "ranger_acceptance":
                report["ranger_acceptance"].append(item)
        if candidate.state != State.READY_APPROVAL:
            report.update(status="FAIL", reason="measured_governed_trial_not_successful")
            checkpoint("TRIAL_NOT_ACCEPTED")
            return report
        checkpoint("MEASURED_TRIAL_ACCEPTED")
        evidence = EvidenceCollector(runtime, repository, service.payloads)
        reference = None
        execution_ids = {"before": [], "after": []}
        async with runtime.connection(scope) as reader:
            for setting in (f"SET resource_group='{group}'", "SET enable_profile=true"):
                await runtime.execute(setting, scope, connection=reader, category="diagnostic")

            async def collect(phase, blocker_connection=None, observer_connection=None):
                nonlocal reference
                for index in range(repetitions + 3):
                    category = "experiment" if index < 3 else "workload"
                    for statement in (target, control):
                        if statement == target and blocker_connection is not None:
                            blocker = asyncio.create_task(runtime.execute(
                                "SELECT sleep(2) FROM sales LIMIT 1", scope,
                                connection=blocker_connection, category="experiment",
                            ))
                            pending = None
                            queued_rows = []
                            try:
                                async with asyncio.timeout(20):
                                    while not blocker.done():
                                        running = await runtime.execute(
                                            "SHOW RUNNING QUERIES", admin,
                                            connection=observer_connection, category="diagnostic",
                                        )
                                        if any(dict(zip(running.columns, row, strict=True))["State"]
                                               == "RUNNING" for row in running.rows):
                                            break
                                        await asyncio.sleep(0.025)
                                    pending = asyncio.create_task(runtime.execute(
                                        statement, scope, connection=reader,
                                        category=category, max_rows=200000,
                                    ))
                                    while not pending.done():
                                        running = await runtime.execute(
                                            "SHOW RUNNING QUERIES", admin,
                                            connection=observer_connection, category="diagnostic",
                                        )
                                        queued_rows = [dict(zip(running.columns, row, strict=True))
                                                       for row in running.rows]
                                        if any(row["State"] == "PENDING" for row in queued_rows):
                                            break
                                        await asyncio.sleep(0.025)
                                    result = await pending
                                    blocked = await blocker
                                if not (
                                    any(row["State"] == "PENDING"
                                        and row["QueryId"] in result.engine_query_ids
                                        for row in queued_rows)
                                    and any(row["State"] == "RUNNING"
                                            and row["QueryId"] in blocked.engine_query_ids
                                            and int(row["Slots"]) >= 1 for row in queued_rows)
                                ):
                                    raise ValueError("fixture_bound_contention_not_observed")
                            finally:
                                tasks = [task for task in (blocker, pending) if task is not None]
                                for task in tasks:
                                    if not task.done():
                                        task.cancel()
                                await asyncio.gather(*tasks, return_exceptions=True)
                        else:
                            result = await runtime.execute(
                                statement, scope, connection=reader,
                                category=category, max_rows=200000,
                            )
                        if statement == target:
                            measured = proof(result)
                            reference = reference or measured
                            if equivalent(reference, measured) is not True:
                                raise ValueError("governed_workload_result_changed")
                        if index < 3:
                            continue
                        await collector.flush(repository)
                        raw = await repository.get("observations", result.nova_execution_id)
                        if not raw or raw["scope"] != scope.model_dump(mode="json"):
                            raise ValueError("collected_governed_scope_mismatch")
                        execution_ids[phase].append(result.nova_execution_id)
                        if statement == target and index in {3, repetitions + 2}:
                            observed = datetime.fromisoformat(raw["observed_at"])
                            await asyncio.sleep(0.2)
                            profile = await evidence.profile(
                                family,
                                scope,
                                result.engine_query_ids[0],
                                reader,
                                observed_at=observed,
                            )
                            plan = await evidence.capture(
                                family_id=family,
                                scope=scope,
                                kind="plan",
                                connection=reader,
                                statement="EXPLAIN COSTS " + target,
                                observed_at=observed,
                                parameter_digest=digest(target),
                                query_ids=tuple(result.engine_query_ids),
                            )
                            if (
                                profile.availability != "available"
                                or plan.availability != "available"
                            ):
                                raise ValueError("governed_verification_evidence_unavailable")
                    if index >= 3 and (index - 2) % 10 == 0:
                        print(
                            f"{phase}: {index - 2}/{repetitions} target/control pairs", flush=True,
                        )

            await collect("before")
            await service.mutate(
                prefix,
                "approve",
                version=candidate.version,
                idempotency_key=prefix + "-approve",
                user=user,
            )
            candidate = Candidate.model_validate(await repository.get("opportunities", prefix))
            application = await service.mutate(
                prefix,
                "apply",
                version=candidate.version,
                idempotency_key=prefix + "-apply",
                user=user,
            )
            await worker.run_once()
            action = await repository.get("actions", application["id"])
            if not action or action.get("state") != "APPLIED":
                raise ValueError("governed_application_not_applied")
            report["fixture_actions"] = 1
            report["action"] = action
            checkpoint("APPLIED")
            if not post_apply_contention:
                await collect("after")
            else:
                from app.modules.resource_groups.service import build_alter_resource_group_sql

                settings = {
                    "enable_group_level_query_queue": "true",
                    "enable_query_queue_select": "true",
                    "query_queue_concurrency_limit": "1",
                }
                saved = {}
                async with runtime.connection(admin) as observer:
                    for name in settings:
                        current = await runtime.execute(
                            f"SHOW GLOBAL VARIABLES LIKE '{name}'", admin,
                            connection=observer, category="diagnostic",
                        )
                        if current.truncated or len(current.rows) != 1:
                            raise ValueError("fixture_queue_inventory_unavailable")
                        value = str(current.rows[0][1]).lower()
                        if value not in {"true", "false"} and not value.isdecimal():
                            raise ValueError("fixture_queue_value_unsupported")
                        saved[name] = value
                    groups = await runtime.execute(
                        "SHOW RESOURCE GROUPS ALL", admin,
                        connection=observer, category="diagnostic",
                    )
                    original_group = [dict(zip(groups.columns, row, strict=True))
                                      for row in groups.rows if row[groups.columns.index("name")]
                                      == group]
                    if groups.truncated or len(original_group) != 1:
                        raise ValueError("fixture_group_inventory_unavailable")
                    concurrency = str(original_group[0]["concurrency_limit"])
                    if not concurrency.isdecimal():
                        raise ValueError("fixture_group_concurrency_unavailable")
                    await repository.put("jobs", prefix + "-contention", {
                        "id": prefix + "-contention", "kind": "fixture_contention",
                        "state": "INTENT", "saved_queue_settings": saved,
                        "group": group, "group_identity": original_group[0]["id"],
                        "saved_concurrency": concurrency,
                    }, state="INTENT")
                    try:
                        for name, value in settings.items():
                            await runtime.execute(
                                f"SET GLOBAL {name}={value}", admin,
                                connection=observer, category="maintenance",
                            )
                            await runtime.execute(
                                f"SET {name}={value}", scope,
                                connection=reader, category="experiment",
                            )
                        await runtime.execute(
                            build_alter_resource_group_sql(group, {"concurrency_limit": "1"}),
                            admin, connection=observer, category="maintenance",
                        )
                        async with runtime.connection(scope) as blocker_connection:
                            await runtime.execute(
                                f"SET resource_group='{group}'", scope,
                                connection=blocker_connection, category="experiment",
                            )
                            await collect("after", blocker_connection, observer)
                    finally:
                        await repository.put("jobs", prefix + "-contention", {
                            "id": prefix + "-contention", "kind": "fixture_contention",
                            "state": "RESTORING", "saved_queue_settings": saved,
                            "group": group, "saved_concurrency": concurrency,
                        }, state="RESTORING")
                        for name, value in saved.items():
                            await runtime.execute(
                                f"SET GLOBAL {name}={value}", admin,
                                connection=observer, category="maintenance",
                            )
                            current = await runtime.execute(
                                f"SHOW GLOBAL VARIABLES LIKE '{name}'", admin,
                                connection=observer, category="diagnostic",
                            )
                            if current.truncated or len(current.rows) != 1 or (
                                str(current.rows[0][1]).lower() != value
                            ):
                                raise ValueError("fixture_queue_restoration_unverified")
                        await runtime.execute(
                            build_alter_resource_group_sql(
                                group, {"concurrency_limit": concurrency},
                            ),
                            admin, connection=observer, category="maintenance",
                        )
                        groups = await runtime.execute(
                            "SHOW RESOURCE GROUPS ALL", admin,
                            connection=observer, category="diagnostic",
                        )
                        restored = [dict(zip(groups.columns, row, strict=True))
                                    for row in groups.rows if row[groups.columns.index("name")]
                                    == group]
                        if groups.truncated or len(restored) != 1 or (
                            restored[0]["id"] != original_group[0]["id"]
                            or str(restored[0]["concurrency_limit"]) != concurrency
                        ):
                            raise ValueError("fixture_group_restoration_unverified")
                        await repository.put("jobs", prefix + "-contention", {
                            "id": prefix + "-contention", "kind": "fixture_contention",
                            "state": "COMPLETE", "configuration_restored": True,
                        }, state="COMPLETE")
                        report["fixture_queue_restored"] = True
        report["execution_ids"] = execution_ids
        checkpoint("AWAITING_NATURAL_VERIFICATION_WINDOW")
        await worker.run_once()
        deadline = datetime.fromisoformat(action["applied_at"]) + timedelta(minutes=30)
        while utcnow() < deadline:
            await asyncio.sleep(min(30, (deadline - utcnow()).total_seconds()))
        await worker.run_once()
        outcome = await repository.get("outcomes", "verify:" + application["id"])
        report["outcome"] = outcome
        report["candidate"] = await repository.get("opportunities", prefix)
        expected = report["expected_outcome"]
        report["status"] = "PASS" if (
            outcome and outcome["state"] == expected and (
                expected == "SUCCESS" or (
                    outcome.get("rollback") == "verified_object_removed"
                    and report.get("fixture_queue_restored") is True
                    and report["candidate"]["state"] == "ROLLED_BACK"
                )
            )
        ) else "FAIL"
        report["reason"] = (outcome or {}).get("reason", "verification_outcome_unavailable")
        checkpoint("VERIFIED")
        return report
    except Exception as exc:
        report.update(status="FAIL", error_type=type(exc).__name__)
        checkpoint("FAILED")
        raise
    finally:
        (output / "governed-cycle.json").write_text(
            json.dumps(public_record(report), indent=2) + "\n"
        )
        (output / "governed-cycle.md").write_text(
            "# Governed fixture cycle\n\n"
            f"Status: {report['status']}; phase: {report.get('phase')}.\n\n"
            "No verdict or timestamp is seeded. Application requires the measured trial, "
            "bound approval and exact snapshot. Production actions: 0; "
            f"actions on owned fixture tables: {report['fixture_actions']}.\n"
        )
        if session_id:
            await session_store.delete(session_id)
        await session_store.close()
        await db.close_system_pool()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sales", type=int, default=50000)
    parser.add_argument("--customers", type=int, default=200)
    parser.add_argument("--post-apply-contention", action="store_true")
    parser.add_argument("--repetitions", type=int, choices=range(30, 101), default=30)
    parser.add_argument("--reuse-fixture")
    args = parser.parse_args()
    result = asyncio.run(run(
        args.output, args.sales, args.customers, post_apply_contention=args.post_apply_contention,
        repetitions=args.repetitions, reuse_fixture=args.reuse_fixture,
    ))
    print(json.dumps({"status": result["status"], "phase": result.get("phase")}))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
