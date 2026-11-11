"""Function-alias concurrency, revision and access contracts on real PG17."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import psycopg
import pytest
import pytest_asyncio

from app.core.metadata_db import (
    MetadataConflict,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
)
from app.modules.access_control.security_context import SecurityContext
from app.modules.ai_ml.repository import AIRegistryRepository, ModelDefinition
from app.modules.llm_functions.repository import AliasDefinition, ModelAliasRepository
from tests.integration.test_metadata_ai_registry import (
    ADMIN,
    READER,
    model,
    provider,
    registry,  # noqa: F401
)
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401

pytestmark = pytest.mark.metadata_pg


@pytest_asyncio.fixture
async def aliases(registry):  # noqa: F811
    async with registry() as (repo, _):
        await repo.create_provider("provider-1", provider())
        await repo.create_model("model-1", model())

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        async with registry(*args, **kwargs) as (_, connection):
            yield ModelAliasRepository(connection), connection

    return transaction


def definition(**changes):
    return replace(AliasDefinition("Example", "complete", "provider-1", "model-1"), **changes)


async def create(aliases, identity="alias-1", **changes):
    async with aliases() as (repo, _):
        return await repo.create(identity, definition(**changes))


async def test_identity_prompt_parameters_and_actor_survive_updates(aliases):
    raw = '{ "temperature" : 0.2500, "nested" : {"label":"漢字"} }'
    first = await create(
        aliases, "Original-ID", system_prompt="synthetic-prompt", default_params=raw
    )
    assert first.affected_function_types == ("complete",)
    assert first.record["id"] == "Original-ID" and first.record["created_by"] == ADMIN.principal
    assert first.record["metadata_revision"] == 1 and first.record["default_params"] == raw
    assert "synthetic-prompt" not in repr(first)
    async with aliases() as (repo, _):
        second = await repo.replace(
            "Original-ID", definition(alias_name="Changed"), expected_revision=1
        )
    assert second.record["created_at"] == first.record["created_at"]
    assert second.record["created_by"] == first.record["created_by"]
    assert second.record["updated_at"] > first.record["updated_at"]
    assert second.record["metadata_revision"] == 2
    async with aliases(read_only=True) as (repo, _):
        assert await repo.aliases() == [second.record]
        assert await repo.aliases("sentiment") == []


async def test_import_retains_null_flags_timestamps_and_author(aliases, environment):  # noqa: F811
    raw = '{ "temperature" : 0.2500 }'
    async with environment.admin.transaction():
        await environment.admin.execute(
            "SELECT set_config('nova.principal','fixture-import',true), "
            "set_config('nova.active_role','ACCOUNTADMIN',true), "
            "set_config('nova.security_context_version','1',true), "
            "set_config('nova.principal_type','SERVICE',true)"
        )
        await environment.admin.execute(
            "INSERT INTO nova_meta.config_model_aliases "
            "(id,alias_name,function_type,provider_id,default_params,is_active,is_default,"
            "created_at,updated_at,created_by,metadata_revision) "
            "VALUES ('Imported-ID','Imported','complete','provider-1',%s,"
            "NULL,NULL,NULL,NULL,NULL,17)",
            (raw,),
        )
    async with aliases(read_only=True) as (repo, _):
        record = await repo.alias("Imported-ID")
        assert record["default_params"] == raw and record["metadata_revision"] == 17
        assert all(
            record[key] is None
            for key in ("is_active", "is_default", "created_at", "updated_at", "created_by")
        )
        assert await repo.selected_alias("complete") is None
    async with aliases() as (repo, _):
        change = await repo.replace("Imported-ID", definition(), expected_revision=17)
    assert change.record["metadata_revision"] == 18
    assert change.record["created_at"] is change.record["created_by"] is None


@pytest.mark.parametrize(
    "workload",
    [
        "web",
        "query",
        "proxy",
        "agent",
        "task",
        "ingestion",
        "ml",
        "automation",
        "maintenance",
    ],
)
async def test_reader_workloads_can_list_but_cannot_mutate(aliases, workload):
    await create(aliases)
    async with aliases(workload, READER, read_only=True) as (repo, _):
        assert len(await repo.aliases()) == 1
    with pytest.raises(MetadataPermissionDenied):
        async with aliases(workload, READER) as (repo, _):
            await repo.create("unauthorized", definition())
    with pytest.raises(MetadataPermissionDenied):
        async with aliases(workload, READER) as (_, connection):
            await connection.execute(
                "INSERT INTO nova_meta.config_model_aliases "
                "(id,alias_name,function_type,provider_id) "
                "VALUES ('direct','Direct','complete','provider-1')"
            )


@pytest.mark.parametrize("workload", ["scheduler", "notebook", "outbox"])
async def test_unrelated_workloads_have_no_alias_table_access(aliases, workload):
    with pytest.raises(MetadataPermissionDenied):
        async with aliases(workload, READER, read_only=True) as (repo, _):
            await repo.aliases()


async def test_unscoped_connection_cannot_read_aliases(aliases, environment):  # noqa: F811
    await create(aliases)
    async with await psycopg.AsyncConnection.connect(
        environment.role_config("nova_meta_web").dsn,
        autocommit=True,
    ) as connection:
        cursor = await connection.execute("SELECT count(*) FROM nova_meta.config_model_aliases")
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.parametrize("role", ["ACCOUNTADMIN", "SECURITYADMIN", "user_admin", "security_admin"])
async def test_active_administrator_roles_can_create(aliases, role):
    async with aliases(actor=SecurityContext("synthetic-admin", role)) as (repo, _):
        assert (await repo.create("alias-1", definition())).record[
            "created_by"
        ] == "synthetic-admin"


async def test_moving_default_clears_destination_without_losing_old_type(aliases):
    await create(aliases)
    await create(aliases, "alias-2", alias_name="Second", function_type="sentiment")
    async with aliases() as (repo, _):
        change = await repo.replace(
            "alias-1", definition(function_type="sentiment"), expected_revision=1
        )
    assert change.affected_function_types == ("complete", "sentiment")
    async with aliases(read_only=True) as (repo, _):
        assert await repo.selected_alias("complete") is None
        assert (await repo.selected_alias("sentiment"))["id"] == "alias-1"
        previous_default = await repo.alias("alias-2")
        assert (
            previous_default["is_default"] is False and previous_default["metadata_revision"] == 2
        )


async def test_two_default_creates_commit_with_exactly_one_default(aliases):
    await asyncio.gather(create(aliases, "alias-1"), create(aliases, "alias-2"))
    async with aliases(read_only=True) as (repo, _):
        rows = await repo.aliases()
        assert len(rows) == 2 and sum(row["is_default"] for row in rows) == 1
        assert sorted(row["metadata_revision"] for row in rows) == [1, 2]


async def test_concurrent_revision_writers_have_one_winner(aliases):
    await create(aliases)

    async def change(name):
        try:
            async with aliases() as (repo, _):
                await repo.replace("alias-1", definition(alias_name=name), expected_revision=1)
            return True
        except MetadataConflict:
            return False

    assert sorted(await asyncio.gather(change("First"), change("Second"))) == [False, True]
    async with aliases(read_only=True) as (repo, _):
        assert (await repo.alias("alias-1"))["metadata_revision"] == 2


async def test_duplicate_create_does_not_clear_default_when_conflict_is_caught(aliases):
    await create(aliases, "alias-1", is_default=False)
    await create(aliases, "alias-2")
    async with aliases() as (repo, _):
        with pytest.raises(MetadataConflict):
            await repo.create("alias-1", definition())
        await repo.create("alias-3", definition(is_default=False))
    async with aliases(read_only=True) as (repo, _):
        assert (await repo.selected_alias("complete"))["id"] == "alias-2"
        assert (await repo.alias("alias-2"))["metadata_revision"] == 1
        assert len(await repo.aliases()) == 3


async def test_inactive_default_is_selected_without_silent_provider_fallback(aliases):
    await create(aliases, is_active=False)
    await create(aliases, "alias-2", is_default=False)
    async with aliases(read_only=True) as (repo, _):
        selected = await repo.selected_alias("complete")
        assert selected["id"] == "alias-1" and selected["is_active"] is False


async def test_delete_promotes_first_active_and_reserves_deleted_identity(aliases):
    await create(aliases)
    await create(aliases, "inactive", alias_name="A inactive", is_default=False, is_active=False)
    await create(aliases, "active", alias_name="B active", is_default=False)
    await create(aliases, "last", alias_name="C active", is_default=False)
    async with aliases() as (repo, _):
        change = await repo.delete("alias-1", expected_revision=1)
        assert change.record["is_deleted"] and change.record["metadata_revision"] == 2
        assert not change.record["is_active"] and not change.record["is_default"]
    async with aliases(read_only=True) as (repo, _):
        selected = await repo.selected_alias("complete")
        assert selected["id"] == "active" and selected["metadata_revision"] == 2
        assert len(await repo.aliases()) == 3
    with pytest.raises(MetadataConflict):
        await create(aliases)
    with pytest.raises(MetadataConflict):
        async with aliases() as (repo, _):
            await repo.replace("alias-1", definition(), expected_revision=2)


async def test_outer_rollback_restores_alias_and_promoted_default(aliases):
    await create(aliases)
    await create(aliases, "alias-2", is_default=False)
    with pytest.raises(RuntimeError, match="abort service transaction"):
        async with aliases() as (repo, _):
            await repo.delete("alias-1", expected_revision=1)
            raise RuntimeError("abort service transaction")
    async with aliases(read_only=True) as (repo, _):
        assert (await repo.selected_alias("complete"))["id"] == "alias-1"
        assert all(row["metadata_revision"] == 1 for row in await repo.aliases())


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_id": "missing"},
        {"model_id": "missing"},
        {"function_type": "embed"},
    ],
)
async def test_invalid_registry_reference_conflicts_without_clearing_default(aliases, changes):
    await create(aliases)
    with pytest.raises(MetadataConflict):
        await create(aliases, "invalid", **changes)
    async with aliases(read_only=True) as (repo, _):
        assert (await repo.selected_alias("complete"))["metadata_revision"] == 1


async def test_cross_provider_model_is_refused(aliases):
    async with aliases() as (_, connection):
        await AIRegistryRepository(connection).create_provider("provider-2", provider())
    with pytest.raises(MetadataConflict):
        await create(aliases, provider_id="provider-2")


async def test_embedding_descriptor_reference_without_live_provider_call(aliases):
    async with aliases() as (_, connection):
        await AIRegistryRepository(connection).create_model(
            "embed-1",
            ModelDefinition(
                "provider-1",
                "synthetic-embedding",
                "embedding",
                logical_alias="synthetic-embedding",
                revision="v1",
                dimensions=32,
                modality="text",
                metric="cosine",
            ),
        )
    assert (await create(aliases, function_type="embed", model_id="embed-1")).record[
        "model_id"
    ] == "embed-1"
    with pytest.raises(MetadataConflict):
        await create(aliases, "invalid", model_id="embed-1")


@pytest.mark.parametrize("target", ["provider", "model"])
async def test_referenced_registry_deletion_rolls_back_all_rows(aliases, target):
    await create(aliases)
    with pytest.raises(MetadataIntegrityViolation):
        async with aliases() as (_, connection):
            registry_repo = AIRegistryRepository(connection)
            if target == "provider":
                await registry_repo.delete_provider("provider-1", expected_revision=1)
            else:
                await registry_repo.delete_model(
                    "model-1", provider_id="provider-1", expected_revision=1
                )
    async with aliases(read_only=True) as (_, connection):
        registry_repo = AIRegistryRepository(connection)
        assert (await registry_repo.provider("provider-1"))["metadata_revision"] == 1
        assert (await registry_repo.model("model-1"))["metadata_revision"] == 1
        assert await registry_repo.credential("provider-1") is not None
    async with aliases() as (repo, connection):
        await repo.delete("alias-1", expected_revision=1)
        await AIRegistryRepository(connection).delete_provider("provider-1", expected_revision=1)


async def test_registry_deletion_serializes_with_alias_creation(aliases):
    async def add():
        try:
            await create(aliases)
            return True
        except MetadataConflict:
            return False

    async def remove():
        try:
            async with aliases() as (_, connection):
                await AIRegistryRepository(connection).delete_provider(
                    "provider-1", expected_revision=1
                )
            return True
        except MetadataIntegrityViolation:
            return False

    assert sorted(await asyncio.gather(add(), remove())) == [False, True]
    async with aliases(read_only=True) as (repo, connection):
        parent = await AIRegistryRepository(connection).provider("provider-1")
        assert bool(await repo.aliases()) is not parent["is_deleted"]


@pytest.mark.parametrize(
    "statement,exception",
    [
        (
            "UPDATE nova_meta.config_model_aliases SET metadata_revision=5",
            MetadataIntegrityViolation,
        ),
        ("UPDATE nova_meta.config_model_aliases SET created_by='other'", MetadataPermissionDenied),
        ("DELETE FROM nova_meta.config_model_aliases", MetadataPermissionDenied),
        ("TRUNCATE nova_meta.config_model_aliases", MetadataPermissionDenied),
        (
            "UPDATE nova_meta.config_model_aliases SET function_type='embed',metadata_revision=2",
            MetadataIntegrityViolation,
        ),
    ],
)
async def test_direct_writes_cannot_bypass_revision_identity_or_reference_guards(
    aliases, statement, exception
):
    await create(aliases)
    with pytest.raises(exception):
        async with aliases() as (_, connection):
            await connection.execute(statement)


async def test_alias_repository_cannot_escape_service_transaction(aliases):
    async with aliases() as (repo, connection):
        await repo.create("alias-1", definition())
    with pytest.raises(ValueError, match="active metadata transaction"):
        await repo.aliases()
    with pytest.raises(ValueError, match="active metadata transaction"):
        ModelAliasRepository(connection)
