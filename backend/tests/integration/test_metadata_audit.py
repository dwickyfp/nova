"""Audit identity, transaction, import and role boundaries on the existing PG17."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from app.common.metadata_audit import AuditEvent, AuditIdentity, AuditRepository
from app.common.preferences import PreferenceChange, PreferenceRepository
from app.core.metadata_bootstrap import WORKLOAD_ROLES
from app.core.metadata_db import (
    MetadataConflict,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
)
from app.modules.access_control.security_context import SecurityContext
from app.modules.ai_ml.metadata_service import AIConfigurationService
from app.modules.llm_functions.repository import AliasDefinition
from tests.integration.test_metadata_ai_registry import (
    ADMIN,
    model,
    provider,
)
from tests.integration.test_metadata_ai_registry import (
    registry as registry,
)
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401

pytestmark = pytest.mark.metadata_pg
ALICE = SecurityContext("alice", "ANALYST", security_context_version=5)
BOB = SecurityContext("bob", "ANALYST", security_context_version=5)


def identity():
    return AuditIdentity(uuid4(), datetime.now(UTC))


def event(**changes):
    return replace(AuditEvent("query", "SELECT", "TABLE", "example", "SUCCESS"), **changes)


async def append(registry, key, value=None, *, actor=ALICE, workload="web"):
    async with registry(workload, actor) as (_, connection):
        return await AuditRepository(connection).append(key, value or event())


async def test_same_event_replays_but_different_payload_or_context_conflicts(registry):
    key = identity()
    first = await append(registry, key, event(engine_query_ids=("engine-1",)))
    assert await append(registry, key, event(engine_query_ids=("engine-1",))) == first
    assert first["user_name"] == ALICE.principal and first["active_role"] == ALICE.active_role
    assert first["security_context_version"] == 5 and first["writer_role"] == "nova_meta_web"
    for actor, workload, changed in (
        (ALICE, "web", event(status="FAILED")),
        (replace(ALICE, security_context_version=6), "web", event(engine_query_ids=("engine-1",))),
        (ALICE, "query", event(engine_query_ids=("engine-1",))),
        (BOB, "web", event(engine_query_ids=("engine-1",))),
    ):
        with pytest.raises((MetadataConflict, MetadataPermissionDenied)):
            await append(registry, key, changed, actor=actor, workload=workload)


async def test_concurrent_replay_records_exactly_one_event(registry):
    key = identity()
    results = await asyncio.gather(*(append(registry, key) for _ in range(4)))
    assert all(record == results[0] for record in results)


async def test_history_is_scoped_to_principal_and_active_role(registry):
    key = identity()
    await append(registry, key)
    for actor in (BOB, replace(ALICE, active_role="FINANCE")):
        async with registry("web", actor, read_only=True) as (_, connection):
            assert await AuditRepository(connection).get(key) is None
    for actor, workload in ((ADMIN, "web"), (ADMIN, "readonly"), (ALICE, "query")):
        async with registry(workload, actor, read_only=True) as (_, connection):
            assert (await AuditRepository(connection).get(key))["record_id"] == key.record_id
    async with registry("query", ADMIN, read_only=True) as (_, connection):
        assert await AuditRepository(connection).get(key) is None


async def test_password_redaction_survives_storage_and_replay(registry):
    key = identity()
    statement = "ALTER USER example IDENTIFIED BY 'synthetic-audit-secret'"
    value = event(sql_text=statement, rewritten_sql=statement, error_message=statement)
    stored = await append(registry, key, value)
    assert "synthetic-audit-secret" not in json.dumps(stored, default=str)
    assert await append(registry, key, value) == stored


async def test_metadata_audit_rolls_back_and_refusal_can_be_recorded_afterward(registry):
    with pytest.raises(RuntimeError, match="abort"):
        async with registry() as (repo, connection):
            await repo.create_provider("aborted", provider())
            cursor = await connection.execute("SELECT count(*) AS n FROM nova_meta.audit_log")
            assert (await cursor.fetchone())["n"] == 1
            raise RuntimeError("abort")
    async with registry(read_only=True) as (repo, connection):
        assert await repo.provider("aborted") is None
        cursor = await connection.execute("SELECT count(*) AS n FROM nova_meta.audit_log")
        assert (await cursor.fetchone())["n"] == 0
    key = identity()
    refusal = await append(registry, key, event(status="REFUSED", decision="DENY"), actor=ADMIN)
    assert refusal["status"] == "REFUSED"


async def test_preference_audit_contains_identity_and_revision_without_value(registry):
    async with registry("web", ALICE) as (_, connection):
        await PreferenceRepository(connection).change(
            "alice",
            {"account.profile": PreferenceChange(0, "synthetic-private-value")},
        )
        await PreferenceRepository(connection).change(
            "alice",
            {"account.profile": PreferenceChange(1, None, deleted=True)},
        )
        cursor = await connection.execute(
            "SELECT * FROM nova_meta.audit_log ORDER BY metadata_revision"
        )
        records = await cursor.fetchall()
    assert [r["action"] for r in records] == ["CREATE", "DELETE"]
    assert [r["metadata_revision"] for r in records] == [1, 2]
    assert all(
        r["metadata_key"] == {"user_name": "alice", "pref_key": "account.profile"} for r in records
    )
    assert "synthetic-private-value" not in json.dumps(records, default=str)


async def test_ai_default_move_audits_every_changed_row_with_jobs(registry):
    async with registry() as (_, connection):
        service = AIConfigurationService(connection)
        await service.create_provider("provider-1", provider())
        await service.create_model("model-1", model())
        alias = AliasDefinition("First", "complete", "provider-1", "model-1")
        await service.create_alias("alias-1", alias)
        await service.create_alias("alias-2", replace(alias, alias_name="Second"))
        cursor = await connection.execute("SELECT * FROM nova_meta.audit_log")
        records = await cursor.fetchall()
        cursor = await connection.execute("SELECT count(*) AS n FROM nova_meta.job_queue")
        assert (await cursor.fetchone())["n"] == 2
    assert len(records) == 5
    aliases = [r for r in records if r["metadata_table"] == "config_model_aliases"]
    assert {(r["object_name"], r["metadata_revision"]) for r in aliases} == {
        ("alias-1", 1),
        ("alias-1", 2),
        ("alias-2", 1),
    }
    assert all(
        r["writer_role"] == "nova_meta_web" and r["user_name"] == ADMIN.principal for r in records
    )
    assert "synthetic-registry-secret" not in json.dumps(records, default=str)
    assert "enc:" not in json.dumps(records, default=str)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE nova_meta.audit_log SET status='CHANGED'",
        "DELETE FROM nova_meta.audit_log",
        "TRUNCATE nova_meta.audit_log CASCADE",
        "SELECT * FROM nova_meta.audit_log_unpartitioned",
        "INSERT INTO nova_meta.audit_log (event_type,user_name) VALUES ('query','other')",
        "INSERT INTO nova_meta.audit_log (event_type,metadata_table) VALUES ('query','forged')",
        "INSERT INTO nova_meta.audit_log (event_type) VALUES ('metadata')",
        "ALTER TABLE nova_meta.config_ai_providers DISABLE TRIGGER provider_audit",
        "SET session_replication_role=replica",
    ],
)
async def test_runtime_cannot_rewrite_audit_or_forge_metadata_success(registry, statement):
    await append(registry, identity(), actor=ADMIN)
    with pytest.raises(MetadataPermissionDenied):
        async with registry() as (_, connection):
            await connection.execute(statement)


async def test_unscoped_connection_cannot_read_or_append(registry, environment):  # noqa: F811
    await append(registry, identity())
    async with await psycopg.AsyncConnection.connect(
        environment.role_config("nova_meta_web").dsn,
        autocommit=True,
    ) as connection:
        cursor = await connection.execute("SELECT count(*) FROM nova_meta.audit_log")
        assert await cursor.fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await connection.execute(
                "INSERT INTO nova_meta.audit_log (event_type) VALUES ('query')"
            )


async def test_duplicate_legacy_ids_and_null_actor_fields_survive_import(registry, environment):  # noqa: F811
    timestamp = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    ids = (uuid4(), uuid4())
    for record_id in ids:
        await environment.admin.execute(
            "INSERT INTO nova_meta.audit_log "
            "(event_time,record_id,log_id,event_type,user_name,active_role,"
            "security_context_version) "
            "VALUES (%s,%s,7,'legacy',NULL,NULL,NULL)",
            (timestamp, record_id),
        )
    async with registry(read_only=True) as (_, connection):
        cursor = await connection.execute(
            "SELECT record_id,log_id,event_time,user_name "
            "FROM nova_meta.audit_log ORDER BY record_id"
        )
        rows = await cursor.fetchall()
    assert {r["record_id"] for r in rows} == set(ids)
    assert all(
        r["log_id"] == 7 and r["event_time"] == timestamp and r["user_name"] is None for r in rows
    )


async def test_audit_repository_cannot_outlive_its_transaction(registry):
    async with registry(read_only=True) as (_, connection):
        repository = AuditRepository(connection)
    with pytest.raises(ValueError, match="transaction"):
        await repository.get(identity())


async def test_current_event_uses_utc_month_and_leaf_has_no_runtime_grant(registry):
    key = identity()
    await append(registry, key)
    async with registry("web", ALICE, read_only=True) as (_, connection):
        cursor = await connection.execute(
            "SELECT tableoid::regclass::text AS leaf FROM nova_meta.audit_log "
            "WHERE event_time=%s AND record_id=%s", (key.event_time, key.record_id),
        )
        expected = "audit_log_" + key.event_time.strftime("%Y%m")
        assert (await cursor.fetchone())["leaf"] == "nova_meta." + expected
    with pytest.raises(MetadataPermissionDenied):
        async with registry(read_only=True) as (_, connection):
            await connection.execute(sql.SQL("SELECT * FROM nova_meta.{}").format(
                sql.Identifier(expected),
            ))


@pytest.mark.parametrize("role", WORKLOAD_ROLES)
async def test_each_workload_can_record_its_own_service_event(registry, role):
    actor = SecurityContext("audit-worker", "ANALYST", principal_type="SERVICE")
    key = identity()
    stored = await append(registry, key, actor=actor, workload=role.removeprefix("nova_meta_"))
    assert stored["writer_role"] == role and stored["principal_type"] == "SERVICE"
    assert stored["user_name"] == actor.principal and stored["active_role"] == actor.active_role


async def test_readonly_login_cannot_append_an_event(registry):
    with pytest.raises(MetadataPermissionDenied):
        await append(registry, identity(), actor=ADMIN, workload="readonly")


@pytest.mark.parametrize("timestamp,record_id", [
    ("infinity", str(uuid4())),
    ("2025-01-01T00:00:00Z", "00000000-0000-0000-0000-000000000000"),
])
async def test_runtime_cannot_insert_nonfinite_or_empty_audit_identity(
    registry, timestamp, record_id,
):
    with pytest.raises(MetadataIntegrityViolation):
        async with registry("web", ALICE) as (_, connection):
            await connection.execute(
                "INSERT INTO nova_meta.audit_log (event_time,record_id,event_type) "
                "VALUES (%s,%s,'query')", (timestamp, record_id),
            )


async def test_privileged_alias_import_is_not_runtime_intent_or_success_audit(
    registry, environment,  # noqa: F811
):
    async with registry() as (repo, _):
        await repo.create_provider("provider-1", provider())
        await repo.create_model("model-1", model())
    async with environment.admin.transaction():
        await environment.admin.execute(
            "SELECT set_config('nova.principal','fixture-import',true), "
            "set_config('nova.active_role','ACCOUNTADMIN',true), "
            "set_config('nova.security_context_version','1',true), "
            "set_config('nova.principal_type','SERVICE',true)"
        )
        await environment.admin.execute(
            "INSERT INTO nova_meta.config_model_aliases "
            "(id,alias_name,function_type,provider_id,model_id,is_active,is_default,"
            "created_by,metadata_revision) "
            "VALUES ('imported','Imported','complete','provider-1','model-1',true,true,NULL,17)"
        )
    async with registry(read_only=True) as (_, connection):
        cursor = await connection.execute(
            "SELECT (SELECT count(*) FROM nova_meta.ai_function_heads) AS heads, "
            "(SELECT count(*) FROM nova_meta.job_queue) AS jobs, "
            "(SELECT count(*) FROM nova_meta.audit_log "
            "WHERE metadata_table='config_model_aliases') AS alias_audits"
        )
        assert await cursor.fetchone() == {"heads": 0, "jobs": 0, "alias_audits": 0}
    async with registry() as (_, connection):
        service = AIConfigurationService(connection)
        change = await service.replace_alias(
            "imported", AliasDefinition("Current", "complete", "provider-1", "model-1"),
            expected_revision=17,
        )
        assert change.record["created_by"] is None
        assert len(change.registration_jobs) == 1
        cursor = await connection.execute(
            "SELECT metadata_revision FROM nova_meta.audit_log "
            "WHERE metadata_table='config_model_aliases'"
        )
        assert await cursor.fetchall() == [{"metadata_revision": 18}]


@pytest.mark.parametrize("table,key,revision", [
    ("config_ai_models", None, 1),
    ("config_ai_models", {"id": "example"}, None),
    ("config_ai_models", None, None),
    (None, {"id": "example"}, 1),
    ("config_ai_models", [], 1),
])
async def test_import_cannot_create_partial_metadata_audit_identity(
    registry, environment, table, key, revision,  # noqa: F811
):
    with pytest.raises(psycopg.errors.CheckViolation):
        async with environment.admin.transaction():
            await environment.admin.execute(
                "INSERT INTO nova_meta.audit_log "
                "(event_type,metadata_table,metadata_key,metadata_revision) "
                "VALUES ('metadata',%s,%s,%s)",
                (table, Jsonb(key) if key is not None else None, revision),
            )
