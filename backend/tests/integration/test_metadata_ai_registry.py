"""AI registry constraints and credential boundaries on the disposable PG17."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.common import crypto
from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.ai_ml.repository import AIRegistryRepository, ModelDefinition, ProviderDefinition
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401

pytestmark = pytest.mark.metadata_pg
ADMIN = SecurityContext("registry-admin", "ACCOUNTADMIN")
READER = SecurityContext("registry-reader", "ANALYST")


@pytest_asyncio.fixture
async def registry(environment, monkeypatch):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="a" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))

    @asynccontextmanager
    async def transaction(workload="web", actor=ADMIN, *, read_only=False):
        database = MetadataDatabase(environment.role_config("nova_meta_" + workload))
        try:
            await database.open(schema_version=len(load_migrations()), metadata_epoch=epoch)
            async with database.transaction(actor, read_only=read_only) as connection:
                yield AIRegistryRepository(connection), connection
        finally:
            await database.close()

    return transaction


def provider(key="synthetic-registry-secret"):
    return ProviderDefinition(
        "Synthetic provider", "openai_compatible", "https://example.test/v1",
        crypto.encrypt(key), '{ "temperature" : 0.25 }',
    )


def model(provider_id="provider-1"):
    return ModelDefinition(provider_id, "synthetic-model", "llm", max_tokens=256)


async def create(registry, identity="provider-1", definition=None):
    async with registry() as (repo, _):
        return await repo.create_provider(identity, definition or provider())


async def test_encrypted_key_capacity_and_public_projection(registry):
    plaintext = "🔒" * 512
    definition = provider(plaintext)
    assert len(definition.encrypted_api_key) > 512
    record = await create(registry, definition=definition)
    assert record["metadata_revision"] == 1 and record["has_api_key"]
    assert record["created_by"] == ADMIN.principal
    assert record["created_at"].utcoffset().total_seconds() == 0
    assert record["default_params"] == definition.default_params
    assert "api_key" not in record and "ciphertext" not in record
    async with registry(actor=READER, read_only=True) as (repo, _):
        assert (await repo.providers())[0] == record
        assert await repo.credential("provider-1") is None
    async with registry("agent", READER, read_only=True) as (repo, _):
        credential = await repo.credential("provider-1")
        assert credential.ciphertext == definition.encrypted_api_key
        assert crypto.decrypt(credential.ciphertext) == plaintext
        assert plaintext not in repr(credential)
        assert definition.encrypted_api_key not in repr(credential)
        assert definition.encrypted_api_key not in repr(definition)


async def test_unscoped_workload_connection_sees_no_registry_or_credentials(
    registry, environment,  # noqa: F811
):
    await create(registry)
    config = environment.role_config("nova_meta_web")
    async with await psycopg.AsyncConnection.connect(config.dsn, autocommit=True) as connection:
        for table in ("config_ai_providers", "config_ai_provider_credentials", "config_ai_models"):
            cursor = await connection.execute(
                psycopg.sql.SQL("SELECT count(*) FROM nova_meta.{}").format(
                    psycopg.sql.Identifier(table),
                )
            )
            assert (await cursor.fetchone())[0] == 0


async def test_inactive_provider_keeps_key_for_admin_edits_but_not_invocation(registry):
    definition = provider()
    await create(registry, definition=definition)
    async with registry() as (repo, _):
        await repo.replace_provider(
            "provider-1", replace(definition, is_active=False), expected_revision=1,
        )
    async with registry("agent", READER, read_only=True) as (repo, _):
        assert await repo.credential("provider-1") is None
    async with registry() as (repo, _):
        credential = await repo.credential("provider-1")
        assert credential.ciphertext == definition.encrypted_api_key
        await repo.replace_provider(
            "provider-1", replace(definition, name="Renamed while inactive", is_active=False),
            expected_revision=2,
        )
    async with registry(read_only=True) as (repo, _):
        assert (await repo.credential("provider-1")).ciphertext == definition.encrypted_api_key


async def test_import_preserves_source_identity_ciphertext_json_and_null_timestamps(
    registry, environment,  # noqa: F811
):
    ciphertext = crypto.encrypt("synthetic-import-canary")
    raw = '{ "rate" : 1.2500, "label" : "漢字" }'
    async with environment.admin.transaction():
        await environment.admin.execute(
            "INSERT INTO nova_meta.config_ai_providers "
            "(id,name,type,endpoint,default_params,is_active,created_at,created_by,"
            "metadata_revision,has_api_key,api_key_masked) "
            "VALUES ('Legacy-ID','Imported','openai_compatible','https://example.test',"
            "%s,true,NULL,NULL,17,true,%s)", (raw, crypto.mask_secret("synthetic-import-canary")),
        )
        await environment.admin.execute(
            "INSERT INTO nova_meta.config_ai_provider_credentials VALUES ('Legacy-ID',17,%s)",
            (ciphertext,),
        )
    async with registry(read_only=True) as (repo, _):
        credential = await repo.credential("Legacy-ID")
        assert credential.ciphertext == ciphertext
        assert credential.provider["default_params"] == raw
        assert credential.provider["created_at"] is credential.provider["created_by"] is None
    async with registry() as (repo, _):
        await repo.replace_provider(
            "Legacy-ID", replace(provider(), encrypted_api_key=ciphertext, default_params=raw),
            expected_revision=17,
        )
    async with registry(read_only=True) as (repo, _):
        credential = await repo.credential("Legacy-ID")
        assert credential.ciphertext == ciphertext
        assert credential.provider["metadata_revision"] == 18
        assert credential.provider["created_at"] is credential.provider["created_by"] is None


@pytest.mark.parametrize("workload", [
    "query", "proxy", "task", "ingestion", "ml", "automation", "maintenance", "readonly",
])
async def test_metadata_readers_cannot_read_credentials(registry, workload):
    await create(registry)
    async with registry(workload, READER, read_only=True) as (repo, _):
        assert len(await repo.providers()) == 1
    with pytest.raises(MetadataPermissionDenied):
        async with registry(workload, ADMIN, read_only=True) as (repo, _):
            await repo.credential("provider-1")


@pytest.mark.parametrize("workload", ["scheduler", "notebook", "outbox"])
async def test_unrelated_workloads_cannot_read_registry(registry, workload):
    with pytest.raises(MetadataPermissionDenied):
        async with registry(workload, ADMIN, read_only=True) as (repo, _):
            await repo.providers()


@pytest.mark.parametrize("workload,actor", [
    ("web", READER), ("query", ADMIN), ("proxy", ADMIN), ("agent", ADMIN),
])
async def test_registry_mutations_require_web_and_active_administrator(registry, workload, actor):
    with pytest.raises(MetadataPermissionDenied):
        async with registry(workload, actor) as (repo, _):
            await repo.create_provider("denied", provider())
    async with registry(read_only=True) as (repo, _):
        assert not await repo.providers()


async def test_provider_revision_and_credentials_have_one_concurrent_winner(registry):
    await create(registry)

    async def change(number):
        definition = replace(provider(f"synthetic-key-{number}"), name=f"writer-{number}")
        try:
            async with registry() as (repo, _):
                await repo.replace_provider("provider-1", definition, expected_revision=1)
            return number
        except MetadataConflict:
            return None

    outcomes = await asyncio.gather(*(change(i) for i in range(8)))
    winner, = [number for number in outcomes if number is not None]
    async with registry("agent", READER, read_only=True) as (repo, _):
        credential = await repo.credential("provider-1")
        assert credential.provider["name"] == f"writer-{winner}"
        assert credential.provider["metadata_revision"] == 2
        assert crypto.decrypt(credential.ciphertext) == f"synthetic-key-{winner}"


@pytest.mark.parametrize("statement,error", [
    ("UPDATE nova_meta.config_ai_providers SET name='no-revision'", MetadataIntegrityViolation),
    ("UPDATE nova_meta.config_ai_providers SET created_by='forged'", MetadataPermissionDenied),
    ("DELETE FROM nova_meta.config_ai_providers", MetadataPermissionDenied),
    ("TRUNCATE nova_meta.config_ai_providers CASCADE", MetadataPermissionDenied),
    ("UPDATE nova_meta.config_ai_provider_credentials SET ciphertext=NULL",
     MetadataIntegrityViolation),
    ("UPDATE nova_meta.config_ai_providers SET metadata_revision=2", MetadataIntegrityViolation),
])
async def test_raw_writes_cannot_break_revision_or_credential_pair(registry, statement, error):
    original = await create(registry)
    with pytest.raises(error):
        async with registry() as (_, connection):
            await connection.execute(statement)
    async with registry(read_only=True) as (repo, _):
        assert await repo.provider("provider-1") == original


async def test_credential_presence_must_match_public_projection_at_commit(registry):
    await create(registry)
    with pytest.raises(MetadataIntegrityViolation):
        async with registry() as (_, connection):
            await connection.execute(
                "UPDATE nova_meta.config_ai_providers SET metadata_revision=2"
            )
            await connection.execute(
                "UPDATE nova_meta.config_ai_provider_credentials "
                "SET metadata_revision=2,ciphertext=NULL"
            )
    async with registry(read_only=True) as (repo, _):
        assert (await repo.provider("provider-1"))["metadata_revision"] == 1


async def test_provider_delete_and_child_tombstones_rollback_together(registry):
    await create(registry)
    async with registry() as (repo, _):
        await repo.create_model("model-1", model())
    with pytest.raises(RuntimeError, match="abort service transaction"):
        async with registry() as (repo, _):
            await repo.delete_provider("provider-1", expected_revision=1)
            raise RuntimeError("abort service transaction")
    async with registry(read_only=True) as (repo, _):
        assert len(await repo.providers()) == len(await repo.models()) == 1
        assert await repo.credential("provider-1") is not None
    async with registry() as (repo, _):
        await repo.delete_provider("provider-1", expected_revision=1)
    async with registry(read_only=True) as (repo, connection):
        assert await repo.providers() == await repo.models() == []
        assert (await repo.provider("provider-1"))["metadata_revision"] == 2
        assert (await repo.model("model-1"))["is_deleted"]
        assert await repo.credential("provider-1") is None
        cursor = await connection.execute(
            "SELECT ciphertext FROM nova_meta.config_ai_provider_credentials"
        )
        assert (await cursor.fetchone())["ciphertext"] is None
    with pytest.raises(MetadataConflict):
        await create(registry)
    with pytest.raises(MetadataConflict):
        async with registry() as (repo, _):
            await repo.create_model("model-after-deletion", model())


async def test_provider_delete_serializes_with_model_creation(registry):
    await create(registry)

    async def add():
        try:
            async with registry() as (repo, _):
                await repo.create_model("model-1", model())
            return True
        except MetadataConflict:
            return False

    async def remove():
        async with registry() as (repo, _):
            await repo.delete_provider("provider-1", expected_revision=1)

    await asyncio.gather(add(), remove())
    async with registry(read_only=True) as (repo, _):
        assert (await repo.provider("provider-1"))["is_deleted"]
        assert await repo.models() == []


async def test_model_revision_is_distinct_from_embedding_descriptor_revision(registry):
    await create(registry)
    definition = ModelDefinition(
        "provider-1", "synthetic-embedding", "embedding", logical_alias="synthetic-alias",
        revision="vendor-revision-1", dimensions=32, modality="text", metric="cosine",
    )
    async with registry() as (repo, _):
        first = await repo.create_model("model-1", definition)
    assert first["revision"] == "vendor-revision-1" and first["metadata_revision"] == 1
    async with registry() as (repo, _):
        second = await repo.replace_model(
            "model-1", replace(definition, display_name="Renamed"), expected_revision=1,
        )
    assert second["revision"] == "vendor-revision-1" and second["metadata_revision"] == 2
    for change in ({"name": "changed"}, {"dimensions": 64}, {"revision": "changed"}):
        with pytest.raises(MetadataIntegrityViolation):
            async with registry() as (repo, _):
                await repo.replace_model(
                    "model-1", replace(definition, **change), expected_revision=2,
                )
    with pytest.raises(MetadataConflict):
        async with registry() as (repo, _):
            await repo.replace_model("model-1", definition, expected_revision=1)


async def test_model_alias_uniqueness_is_enforced_across_providers(registry):
    await create(registry)
    await create(registry, "provider-2")

    async def add(number):
        definition = ModelDefinition(
            f"provider-{number}", "synthetic-embedding", "embedding", logical_alias="one-alias",
            revision="v1", dimensions=32, modality="text", metric="cosine",
        )
        try:
            async with registry() as (repo, _):
                await repo.create_model(f"model-{number}", definition)
            return True
        except MetadataConflict:
            return False

    assert sorted(await asyncio.gather(add(1), add(2))) == [False, True]
    async with registry(read_only=True) as (repo, _):
        assert len(await repo.models()) == 1


async def test_deleted_model_identity_cannot_be_recreated(registry):
    await create(registry)
    async with registry() as (repo, _):
        await repo.create_model("model-1", model())
        await repo.delete_model("model-1", provider_id="provider-1", expected_revision=1)
    with pytest.raises(MetadataConflict):
        async with registry() as (repo, _):
            await repo.create_model("model-1", model())
    async with registry(read_only=True) as (repo, _):
        assert (await repo.model("model-1"))["metadata_revision"] == 2


async def test_caught_duplicate_does_not_rollback_unrelated_outer_work(registry):
    await create(registry)
    async with registry() as (repo, _):
        await repo.create_model("model-1", model())
        with pytest.raises(MetadataConflict):
            await repo.create_model("model-1", model())
        await repo.create_model("model-2", model())
    async with registry(read_only=True) as (repo, _):
        assert len(await repo.models()) == 2
    with pytest.raises(ValueError, match="active metadata transaction"):
        await repo.providers()
