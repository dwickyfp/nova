"""Seeded real-engine contracts; these checks use the isolated CI stack."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest

from app.core.database import db
from app.core.security import encrypt_password
from app.modules.access_control.security_context import SecurityContext
from app.modules.query.service import QueryService
from app.modules.query_autopilot.evidence import EvidenceCollector
from app.modules.query_autopilot.models import Observation, Scope, utcnow
from app.modules.query_autopilot.repository import acting, repository
from app.modules.query_autopilot.runtime import AuthorizedSQL, current_policy_revision
from app.modules.query_autopilot.telemetry import collector, purpose
from app.sql_frontend.session_functions import CORRELATION_SESSION, QueryCorrelationSession

pytestmark = pytest.mark.engine


@pytest.fixture
async def autopilot_engine(app):
    name = "autopilot_" + uuid4().hex[:10]
    role = name + "_role"
    await db.execute_system(f"CREATE DATABASE {name}")
    await db.execute_system(f"CREATE ROLE {role}")
    await db.execute_system(f"GRANT ALL ON {name}.* TO ROLE {role}")
    await db.execute_system(f"GRANT {role} TO USER nova_admin")
    await db.execute_system(
        f"CREATE TABLE {name}.facts (id INT, category INT, amount DECIMAL(12,2)) "
        "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
        "PROPERTIES('replication_num'='1')"
    )
    await db.execute_system(f"INSERT INTO {name}.facts VALUES (1,1,10),(2,1,20),(3,2,30)")
    scope = Scope(
        principal="nova_admin",
        active_role=role,
        security_context_version=1,
        database=name,
        policy_revision=await current_policy_revision(),
    )
    try:
        with acting(
            SecurityContext(scope.principal, scope.active_role, security_context_version=1)
        ):
            yield scope
    finally:
        await db.execute_system(f"DROP DATABASE IF EXISTS {name}")
        await db.execute_system(f"DROP ROLE IF EXISTS {role}")


@asynccontextmanager
async def claimed_operation(engine_workload, scope, identifier):
    from app.modules.query_autopilot.jobs import leased
    from app.modules.query_autopilot.repository import WORKER_ACTOR

    job = {
        "id": identifier,
        "kind": "experiment",
        "state": "QUEUED",
        "version": 1,
        "actor": scope.principal,
        "actor_role": scope.active_role,
        "request_hash": identifier,
    }
    assert await repository.put("jobs", identifier, job, insert_only=True)
    async with engine_workload("automation"):
        with acting(WORKER_ACTOR):
            generation = await repository.claim_job(identifier, expected_version=1)
        assert generation is not None
        async with leased(repository, identifier, generation):
            yield


async def query(scope, statement, **kwargs):
    return await QueryService().execute(
        statement,
        scope.principal,
        encrypt_password(os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")),
        database=scope.database,
        role=scope.active_role,
        **kwargs,
    )


async def test_fingerprint_keeps_engine_resolved_predicate_columns_distinct(autopilot_engine):
    from app.sql_frontend.fingerprint import fingerprint

    scope = autopilot_engine
    statements = (
        "SELECT amount AS category FROM facts WHERE category=1",
        "SELECT amount AS id FROM facts WHERE id=1",
        "SELECT category.amount FROM facts category WHERE category=1",
        "SELECT id.amount FROM facts id WHERE id=1",
    )
    with purpose("experiment"):
        results = [await query(scope, sql) for sql in statements]
    assert all(result.success and not result.truncated for result in results)
    assert [result.row_count for result in results] == [2, 1, 2, 1]
    for left, right in ((0, 1), (2, 3)):
        a, b = (fingerprint(statements[index]) for index in (left, right))
        assert a.classified and b.classified and a.version == b.version == 2
        assert a.family_id != b.family_id


async def test_disposable_resource_trial_preserves_enrolled_group(app):
    from app.modules.query_autopilot.engine_state import compensation_sql, inspect_object
    from app.modules.query_autopilot.models import Candidate
    from app.modules.query_autopilot.resource_trial import (
        trial_group_statement,
        validate_resource_baseline,
    )
    from app.modules.resource_groups.schemas import ClassifierSpec
    from app.modules.resource_groups.service import (
        build_alter_resource_group_sql,
        build_create_resource_group_sql,
    )

    source = "nova_ap_source_" + uuid4().hex[:10]
    name = "nova_ap_trial_" + uuid4().hex[:10]
    scope = Scope(
        principal="nova_admin",
        active_role="ACCOUNTADMIN",
        security_context_version=1,
        database="NOVA_SYSTEM",
        policy_revision=await current_policy_revision(),
    )
    candidate = Candidate(
        id=name,
        family_id="resource-trial",
        scope=scope,
        kind="RESOURCE_GROUP",
        targets=("fixture",),
        evidence_ids=(),
        enrollment_id="fixture",
        enrollment_version=1,
        policy_version=1,
        owned_object="sandbox-resource-group:" + name,
        parameters={"resource_group": name, "properties": {"concurrency_limit": 1}},
    )
    runtime = AuthorizedSQL()
    password = os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
    async with db.user_conn(scope.principal, password) as connection:

        async def execute(statement, **kwargs):
            return await runtime.execute(
                statement, scope, connection=connection, category="experiment", **kwargs
            )

        await execute(
            build_create_resource_group_sql(
                source,
                {"mem_limit": "0.1", "concurrency_limit": 2, "cpu_weight_percent": 10},
                classifiers=(ClassifierSpec(source_ip="0.0.0.0"),),
            )
        )
        created = False
        try:
            before = await execute("SHOW RESOURCE GROUPS ALL")
            statement = trial_group_statement(before, source, name, {"concurrency_limit": 1})
            await execute(statement)
            created = True
            owned = await inspect_object(runtime, candidate, scope, connection)
            assert owned.exists and owned.ready
            await execute(f"SET resource_group='{name}'")
            assert (await execute("SELECT 42")).rows == [[42]]
            # Verify the owner's ALTER template against the pinned engine as well.
            await execute(build_alter_resource_group_sql(name, {"concurrency_limit": 1}))
            after = await execute("SHOW RESOURCE GROUPS ALL")
            bound = validate_resource_baseline(after, source, after, source)
            assert bound.exists and bound.identity
            with pytest.raises(
                ValueError,
                match="sandbox_resource_baseline_differs_from_production",
            ):
                validate_resource_baseline(after, source, after, name)
            assert [row for row in before.rows if row[0] == source] == [
                row for row in after.rows if row[0] == source
            ]
            current = await inspect_object(runtime, candidate, scope, connection)
            assert current.binding == owned.binding
            await execute("SET resource_group='default_wg'")
            await execute(compensation_sql(candidate), confirm=True)
            created = False
            assert not (await inspect_object(runtime, candidate, scope, connection)).exists
        finally:
            if created:
                await execute("SET resource_group='default_wg'")
                await execute(compensation_sql(candidate), confirm=True)
            await execute(f"DROP RESOURCE GROUP `{source}`", confirm=True)


async def test_refresh_policy_clones_owned_mv_and_requires_ranger_proof(
    autopilot_engine, engine_workload
):
    from dataclasses import asdict
    from datetime import timedelta

    from app.modules.query_autopilot.engine_state import inspect_object, wait_ready
    from app.modules.query_autopilot.models import (
        ActionKind,
        Availability,
        Budget,
        Candidate,
        Enrollment,
        Evidence,
    )
    from app.modules.query_autopilot.service import AutopilotService
    from app.modules.task_orchestration.credentials import StaticCredentialProvider
    from app.modules.task_orchestration.execution import DelegateExecutor
    from app.sql_frontend.fingerprint import fingerprint

    source = autopilot_engine.model_copy(update={"active_role": "ACCOUNTADMIN"})
    prefix = "refresh-live-" + uuid4().hex[:16]
    sandbox = source.database + "_snapshot"
    replay_user = "autopilot_replay_" + uuid4().hex[:10]
    replay_role = replay_user + "_role"
    password = os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
    name = "nova_ap_" + uuid4().hex[:20]
    statement = "SELECT category,SUM(amount) AS total FROM facts GROUP BY category"
    control = "SELECT COUNT(*) FROM facts"
    family = fingerprint(statement).family_id
    runtime = AuthorizedSQL(
        DelegateExecutor(
            StaticCredentialProvider(
                {
                    source.principal: password,
                    replay_user: password,
                }
            )
        )
    )
    service = AutopilotService(sql=runtime)
    policy = await service.policy()
    enrollment = Enrollment(
        id=prefix,
        scope=source,
        sandbox_database=sandbox,
        table_mapping={"facts": sandbox + ".facts"},
        snapshot_id=prefix,
        snapshot_created_at=utcnow(),
        snapshot_frozen=True,
        execution_principal=replay_user,
        execution_role=replay_role,
        budget=Budget(resource_group="default_wg"),
        replay_opt_in=True,
        control_families=(fingerprint(control).family_id,),
    )
    candidate = Candidate(
        id=prefix,
        family_id=family,
        scope=source,
        kind="REFRESH_POLICY",
        targets=("facts",),
        evidence_ids=(prefix + "-sample-0",),
        enrollment_id=prefix,
        enrollment_version=1,
        policy_version=policy.version,
        parameters={"name": name, "interval_minutes": 10},
    )
    await db.execute_system(f"CREATE DATABASE {sandbox}")
    await db.execute_system(f"CREATE TABLE {sandbox}.facts LIKE {source.database}.facts")
    await db.execute_system(f"INSERT INTO {sandbox}.facts SELECT * FROM {source.database}.facts")
    await db.execute_system(f"CREATE ROLE {replay_role}")
    await db.execute_system(f"CREATE USER '{replay_user}' IDENTIFIED BY %s", (password,))
    await db.execute_system(f"GRANT ALL ON {sandbox}.* TO ROLE {replay_role}")
    await db.execute_system(f"GRANT ALL ON DATABASE {sandbox} TO ROLE {replay_role}")
    await db.execute_system(
        f"GRANT ALL ON ALL MATERIALIZED VIEWS IN DATABASE {sandbox} TO ROLE {replay_role}"
    )
    await db.execute_system(f"GRANT {replay_role} TO USER {replay_user}")
    try:
        assert await repository.get("enrollments", prefix) is None
        await repository.put("enrollments", prefix, enrollment.model_dump(mode="json"))
        for index, sql in enumerate((statement, control)):
            identifier = prefix + f"-sample-{index}"
            item = Evidence(
                id=identifier,
                family_id=fingerprint(sql).family_id,
                cohort_id=source.cohort_id,
                kind="replay_sample",
                availability="unavailable",
                source="isolated_fixture_opt_in",
                expires_at=utcnow() + timedelta(hours=1),
                payload_ref=service.payloads.reference(identifier),
            )
            await repository.put(
                "evidence",
                identifier,
                item.model_dump(mode="json"),
                family_id=item.family_id,
                cohort_id=source.cohort_id,
            )
            await service.payloads.put(identifier, sql)
            await repository.put(
                "evidence",
                identifier,
                item.model_copy(
                    update={
                        "availability": Availability.AVAILABLE,
                    }
                ).model_dump(mode="json"),
                family_id=item.family_id,
                cohort_id=source.cohort_id,
            )
        async with (
            claimed_operation(engine_workload, autopilot_engine, prefix),
            runtime.connection(source) as connection,
        ):
            original = candidate.model_copy(
                update={"kind": ActionKind.MATERIALIZED_VIEW, "parameters": {"name": name}}
            )
            action_id = prefix + "-original"
            from app.modules.query_autopilot.models import digest

            intent = {
                "id": action_id,
                "kind": "MATERIALIZED_VIEW",
                "state": "APPLYING",
                "candidate_id": prefix,
                "binding": original.proposal_binding,
                "statement_digests": [digest(statement)],
                "actor": source.principal,
                "actor_role": source.active_role,
            }
            await repository.put(
                "actions",
                action_id,
                intent,
                family_id=family,
                cohort_id=source.cohort_id,
                state="APPLYING",
            )
            await runtime.execute(
                f"CREATE MATERIALIZED VIEW `{name}` DISTRIBUTED BY RANDOM "
                f"REFRESH MANUAL AS {statement}",
                source,
                connection=connection,
                category="experiment",
            )
            await runtime.execute(
                f"REFRESH MATERIALIZED VIEW `{name}` WITH SYNC MODE",
                source,
                connection=connection,
                category="experiment",
            )
            owned = await wait_ready(runtime, original, source, connection)
            await repository.put(
                "actions",
                action_id,
                {
                    **intent,
                    "state": "APPLIED",
                    "owned_object_binding": owned.binding,
                    "owned_object": asdict(owned),
                },
                family_id=family,
                cohort_id=source.cohort_id,
                state="APPLIED",
            )
            candidate = candidate.model_copy(
                update={"evidence_digest": await service.evidence_digest(candidate)}
            )
            assert await repository.get("opportunities", candidate.id) is None
            await service._candidate(candidate)
            operation = await service.run_experiment(candidate, enrollment, policy, {"id": prefix})
            assert operation["state"] == "COMPLETED", operation
            trial = await repository.get("experiments", prefix)
            assert trial["result"]["correctness"] == "EQUIVALENT"
            assert trial["result"]["repetitions"] == 30
            assert trial["state"] == "INCONCLUSIVE"
            assert trial["result"]["reason"] == "ranger_filter_or_mask_acceptance_unproven"
            assert trial["cleanup"] == "verified_object_removed"
            assert trial["refresh_source"] == asdict(owned)
            assert (
                await inspect_object(runtime, original, source, connection)
            ).binding == owned.binding
            await runtime.execute(
                f"DROP MATERIALIZED VIEW `{name}`",
                source,
                connection=connection,
                category="experiment",
                confirm=True,
            )
    finally:
        for index in range(2):
            await service.payloads.delete(service.payloads.reference(prefix + f"-sample-{index}"))
        await db.execute_system(f"DROP DATABASE IF EXISTS {sandbox}")
        await db.execute_system(f"DROP USER IF EXISTS {replay_user}")
        await db.execute_system(f"DROP ROLE IF EXISTS {replay_role}")


async def test_schema_fresh_upgrade_batch_and_compare_swap(autopilot_engine, engine_metadata):
    scope = autopilot_engine
    identifier = str(uuid4())
    from app.core.metadata_db import MetadataConflict

    job = {
        "id": identifier,
        "version": 1,
        "kind": "approve",
        "state": "RUNNING",
        "actor": scope.principal,
        "actor_role": scope.active_role,
        "request_hash": identifier,
    }
    assert await repository.put("jobs", identifier, job, insert_only=True)
    done = {**job, "version": 2, "state": "COMPLETED"}
    assert await repository.put("jobs", identifier, done, expected_version=1)
    with pytest.raises(MetadataConflict):
        await repository.put("jobs", identifier, {**done, "version": 3}, expected_version=1)
    assert (await repository.get("jobs", identifier))["version"] == 2
    observation = Observation(
        id=identifier,
        family_id="f",
        scope=scope,
        source="integration",
        status="success",
        total_ms=12,
    )
    await repository.observations([observation])
    await repository.observations([observation])
    rows = await repository.page("observations", family_id="f", cohort_id=scope.cohort_id)
    assert len(rows) == 1
    cursor = await engine_metadata.admin.execute(
        "SELECT expires_at>created_at AS retained FROM nova_meta.query_autopilot_observations "
        "WHERE id=%s",
        (identifier,),
    )
    assert (await cursor.fetchone())["retained"] is True
    with pytest.raises(ValueError, match="Only expired evidence"):
        await repository.delete("jobs", identifier)


async def test_authenticated_id_stream_limit_audit_and_proxy_session(
    autopilot_engine, monkeypatch, engine_metadata
):
    scope = autopilot_engine
    observed_scopes = []
    observe = collector.observe

    def capture(**values):
        identity = values["identity"]
        observed_scopes.append((identity.scope_checked, identity.catalog, identity.database))
        observe(**values)

    monkeypatch.setattr(collector, "observe", capture)
    password = os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
    original = collector.enabled
    collector.enabled = True
    token = CORRELATION_SESSION.set(QueryCorrelationSession())
    try:
        async with db.user_conn(scope.principal, password) as connection:
            result = await query(
                scope, "SELECT * FROM facts ORDER BY id", max_rows=1, connection=connection
            )
            assert result.success and result.truncated and result.row_count == 1
            assert len(result.engine_query_ids) == 1
            UUID(result.engine_query_ids[0])
            assert observed_scopes[-1] == (True, "default_catalog", scope.database)
            previous = await query(scope, "SELECT LAST_QUERY_ID() AS prior", connection=connection)
            assert previous.rows == [[result.engine_query_ids[0]]]
            cursor = await engine_metadata.admin.execute(
                "SELECT query_id,nova_execution_id,engine_query_ids FROM "
                "nova_meta.audit_log WHERE nova_execution_id=%s",
                (result.nova_execution_id,),
            )
            audit = await cursor.fetchall()
            assert audit
            assert audit[0]["query_id"] != result.nova_execution_id
            assert result.engine_query_ids[0] in audit[0]["engine_query_ids"]
            with purpose("diagnostic"):
                count = collector.accepted
                assert (await query(scope, "SELECT 7", connection=connection)).success
                assert collector.accepted == count
                collector.enabled = False
                diagnostic = await query(scope, "SELECT 8", connection=connection)
                assert diagnostic.success and diagnostic.engine_query_ids
                assert collector.accepted == count
    finally:
        collector.enabled = original
        CORRELATION_SESSION.reset(token)


async def test_profile_plan_and_statistics_use_authenticated_connection(autopilot_engine):
    scope = autopilot_engine
    sql = AuthorizedSQL()
    async with db.user_conn(
        scope.principal, os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
    ) as connection:
        await sql.execute(
            "SET enable_profile=true", scope, connection=connection, category="diagnostic"
        )
        result = await sql.execute(
            "SELECT category,SUM(amount) FROM facts GROUP BY category",
            scope,
            connection=connection,
            category="diagnostic",
        )
        assert result.engine_query_ids
        await asyncio.sleep(0.3)
        evidence = EvidenceCollector(sql, repository)
        records = []
        try:
            records.append(
                await evidence.profile(
                    "live-family",
                    scope,
                    result.engine_query_ids[0],
                    connection,
                    observed_at=utcnow(),
                )
            )
            assert records[-1].availability == "available", records[-1].reason
            assert records[-1].summary["facts"]["execution_ms"] >= 0
            records.append(
                await evidence.capture(
                    family_id="live-family",
                    scope=scope,
                    kind="plan",
                    statement=(
                        "EXPLAIN COSTS SELECT category,SUM(amount) FROM facts GROUP BY category"
                    ),
                    connection=connection,
                )
            )
            assert records[-1].availability == "available"
            assert records[-1].summary["operators"]
            records.extend(await evidence.statistics("live-family", scope, "facts", connection))
            assert records[-2].availability == "available"
        finally:
            for record in records:
                if record.payload_ref:
                    await evidence.payloads.delete(record.payload_ref)


async def test_api_monitoring_and_admin_mutations_require_active_roles(
    client, admin_token, analyst_token, engine_metadata
):
    client.headers["Authorization"] = f"Bearer {analyst_token}"
    read = await client.get("/api/v1/query-autopilot/overview")
    assert read.status_code == 403
    mutation = await client.post(
        "/api/v1/query-autopilot/opportunities/missing/apply",
        json={
            "candidate_version": 1,
            "idempotency_key": "not-allowed-1",
        },
    )
    assert mutation.status_code == 403
    client.headers["Authorization"] = f"Bearer {admin_token}"
    switched = await client.post("/api/v1/auth/switch-role", json={"role": "ACCOUNTADMIN"})
    assert switched.status_code == 200
    overview = await client.get("/api/v1/query-autopilot/overview")
    assert overview.status_code == 200
    assert overview.json()["policy"]["mode"] == "GOVERNED"
    refusal = await client.post(
        "/api/v1/query-autopilot/opportunities/missing/apply",
        json={
            "candidate_version": 1,
            "idempotency_key": "no-candidate-1",
        },
    )
    assert refusal.status_code in {400, 409}
    cursor = await engine_metadata.admin.execute(
        "SELECT status FROM nova_meta.audit_log "
        "WHERE event_type='QUERY_AUTOPILOT' AND action='AUTHORIZE' AND status='DENIED'"
    )
    assert await cursor.fetchall()


async def test_baseline_engine_identity_and_exact_owned_cleanup(autopilot_engine):
    from app.modules.query_autopilot.engine_state import (
        baseline_for_sample,
        compensation_sql,
        inspect_object,
    )
    from app.modules.query_autopilot.models import Candidate

    scope = autopilot_engine
    sample = "SELECT COUNT(*) FROM facts WHERE category = 1"
    sql = AuthorizedSQL()
    candidate = Candidate(
        id=str(uuid4()),
        family_id="baseline-fixture",
        scope=scope,
        kind="PLAN_BASELINE",
        targets=("facts",),
        evidence_ids=(),
        enrollment_id="fixture",
        enrollment_version=1,
        policy_version=1,
    )
    async with db.user_conn(
        scope.principal, os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
    ) as conn:
        assert not (await baseline_for_sample(sql, sample, scope, conn)).exists
        owned = None
        try:
            await sql.execute(
                "CREATE GLOBAL BASELINE USING " + sample,
                scope,
                connection=conn,
                category="experiment",
            )
            owned = await baseline_for_sample(sql, sample, scope, conn)
            assert owned.exists and owned.ready and owned.identity.isdecimal()
            candidate = candidate.model_copy(update={"owned_object": "baseline:" + owned.identity})
            assert (await inspect_object(sql, candidate, scope, conn)).binding == owned.binding
        finally:
            if owned and owned.exists:
                await sql.execute(
                    compensation_sql(candidate),
                    scope,
                    connection=conn,
                    category="experiment",
                    confirm=True,
                )
        assert not (await baseline_for_sample(sql, sample, scope, conn)).exists
