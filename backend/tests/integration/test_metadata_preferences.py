"""Production preference DDL and repositories on the existing disposable PG17."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.common import crypto
from app.common.preferences import PreferenceChange, PreferenceRepository
from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
    MetadataReadOnlyMaintenance,
    MetadataTimeout,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401

pytestmark = pytest.mark.metadata_pg


def actor(name="alice", role="ANALYST"):
    return SecurityContext(principal=name, active_role=role)


@pytest_asyncio.fixture
async def preferences(environment, monkeypatch):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="a" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s, maintenance=false",
        (epoch,),
    )
    monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))

    class ActorStore:
        def __init__(self, database):
            self.database = database

        async def read(self, identity, owner, keys):
            async with self.database.transaction(identity, read_only=True) as connection:
                return await PreferenceRepository(connection).read(owner, keys)

        async def change(self, identity, owner, changes):
            async with self.database.transaction(identity) as connection:
                return await PreferenceRepository(connection).change(owner, changes)

    @asynccontextmanager
    async def connect(role="web"):
        database = MetadataDatabase(environment.role_config("nova_meta_" + role))
        try:
            await database.open(schema_version=len(load_migrations()), metadata_epoch=epoch)
            yield ActorStore(database), database
        finally:
            await database.close()

    yield connect


async def test_personal_values_are_private_and_batch_updates_are_atomic(preferences):
    async with preferences() as (repo, _):
        changes = {
            "account.profile": PreferenceChange(0, '{"first_name":"Mawar"}'),
            "studio.theme": PreferenceChange(0, "dark"),
        }
        first = await repo.change(actor(), "alice", changes)
        assert all(record.revision == 1 for record in first.values())
        assert first["account.profile"].updated_at.utcoffset().total_seconds() == 0
        assert not await repo.read(actor("bob"), "alice", list(changes))
        assert not await repo.read(actor("admin", "ACCOUNTADMIN"), "alice", list(changes))
        with pytest.raises(MetadataPermissionDenied):
            await repo.change(actor("bob"), "alice", {"studio.theme": PreferenceChange(0, "light")})
        with pytest.raises(MetadataConflict):
            await repo.change(
                actor(),
                "alice",
                {
                    "account.profile": PreferenceChange(1, "changed"),
                    "studio.theme": PreferenceChange(9, "light"),
                },
            )
        assert await repo.read(actor(), "alice", list(changes)) == first
        second = await repo.change(
            actor(),
            "alice",
            {key: PreferenceChange(1, change.value) for key, change in changes.items()},
        )
        assert all(record.revision == 2 for record in second.values())


async def test_competing_create_and_revision_have_one_winner(preferences):
    async with preferences() as (repo, _):

        async def write(revision, value):
            try:
                return await repo.change(
                    actor(),
                    "alice",
                    {
                        "workspace.active_tab": PreferenceChange(revision, value),
                    },
                )
            except MetadataConflict:
                return None

        for revision in (0, 1):
            results = await asyncio.gather(*(write(revision, str(i)) for i in range(10)))
            assert sum(result is not None for result in results) == 1
        stored = await repo.read(actor(), "alice", ["workspace.active_tab"])
        assert stored["workspace.active_tab"].revision == 2


async def test_delete_and_recreate_keep_revision_and_reject_old_writers(preferences):
    async with preferences() as (repo, _):
        key = "workspace.last_database"
        await repo.change(actor(), "alice", {key: PreferenceChange(0, "sample")})
        deleted = await repo.change(actor(), "alice", {key: PreferenceChange(1, None, True)})
        assert deleted[key].deleted and deleted[key].value is None and deleted[key].revision == 2
        assert await repo.read(actor(), "alice", [key]) == deleted
        for stale in (0, 1):
            with pytest.raises(MetadataConflict):
                await repo.change(actor(), "alice", {key: PreferenceChange(stale, "stale")})
        fresh = await repo.change(actor(), "alice", {key: PreferenceChange(2, "restored")})
        assert fresh[key].revision == 3 and not fresh[key].deleted


@pytest.mark.parametrize("role", ["ACCOUNTADMIN", "SECURITYADMIN", "user_admin", "security_admin"])
async def test_account_flags_require_active_admin_and_preserve_self_clear(preferences, role):
    async with preferences() as (repo, _):
        changes = {
            "nova.news_enabled": PreferenceChange(0, "true"),
            "nova.must_change_password": PreferenceChange(0, "true"),
        }
        with pytest.raises(MetadataPermissionDenied):
            await repo.change(actor(), "alice", changes)
        await repo.change(actor("admin", role), "alice", changes)
        assert len(await repo.read(actor(), "alice", list(changes))) == 2
        assert not await repo.read(actor("bob"), "alice", list(changes))
        with pytest.raises(MetadataPermissionDenied):
            await repo.change(actor(), "alice", {"nova.news_enabled": PreferenceChange(1, "false")})
        with pytest.raises(MetadataPermissionDenied):
            await repo.change(
                actor(), "alice", {"nova.must_change_password": PreferenceChange(1, None, True)}
            )
        cleared = await repo.change(
            actor(),
            "alice",
            {
                "nova.must_change_password": PreferenceChange(1, "false"),
            },
        )
        assert cleared["nova.must_change_password"].revision == 2
        with pytest.raises(MetadataPermissionDenied):
            await repo.change(
                actor(),
                "alice",
                {
                    "nova.must_change_password": PreferenceChange(2, "true"),
                },
            )


@pytest.mark.parametrize(
    "role",
    [
        "query",
        "agent",
        "scheduler",
        "task",
        "ingestion",
        "ml",
        "notebook",
        "automation",
        "maintenance",
        "outbox",
        "readonly",
    ],
)
async def test_workloads_receive_only_their_required_preference_access(preferences, role):
    async with preferences() as (writer, _):
        await writer.change(actor(), "alice", {"studio.theme": PreferenceChange(0, "dark")})
    async with preferences(role) as (repo, _):
        if role in {"query", "agent", "readonly"}:
            stored = await repo.read(actor(), "alice", ["studio.theme"])
            assert stored["studio.theme"].value == "dark"
        elif role in {"task", "automation"}:
            assert not await repo.read(actor(), "alice", ["studio.theme"])
        else:
            with pytest.raises(MetadataPermissionDenied):
                await repo.read(actor(), "alice", ["studio.theme"])
        with pytest.raises(MetadataPermissionDenied):
            await repo.change(actor(), "alice", {"studio.theme": PreferenceChange(1, "light")})


async def test_system_settings_and_secret_access_follow_workload_and_active_role(preferences):
    secret = json.dumps({"api_key": crypto.encrypt("synthetic-credential-canary"), "hint": None})
    async with preferences() as (repo, _):
        settings = {
            "default_llm_model": PreferenceChange(0, '{"model_id":"fixture"}'),
            "tavily_api_key": PreferenceChange(0, secret),
        }
        with pytest.raises(MetadataPermissionDenied):
            await repo.change(actor(), "__system__", settings)
        await repo.change(actor("admin", "SECURITYADMIN"), "__system__", settings)
        with pytest.raises(MetadataPermissionDenied):
            await repo.change(
                actor("admin", "SECURITYADMIN"),
                "__system__",
                {
                    "setup_complete": PreferenceChange(0, "true"),
                },
            )
        await repo.change(
            actor("admin", "ACCOUNTADMIN"),
            "__system__",
            {
                "setup_complete": PreferenceChange(0, "true"),
            },
        )
        row = (await repo.read(actor(), "__system__", ["tavily_api_key"]))["tavily_api_key"]
        assert row.value == secret and secret not in repr(row)
    for role in ("agent", "query", "proxy", "task", "automation", "readonly"):
        async with preferences(role) as (repo, _):
            result = await repo.read(
                actor("admin", "ACCOUNTADMIN"),
                "__system__",
                [
                    "default_llm_model",
                    "tavily_api_key",
                    "setup_complete",
                ],
            )
            assert set(result) == (
                {"default_llm_model", "tavily_api_key"}
                if role == "agent"
                else {"default_llm_model"}
            )
            with pytest.raises((MetadataPermissionDenied, MetadataConflict)):
                await repo.change(
                    actor("admin", "ACCOUNTADMIN"),
                    "__system__",
                    {
                        "default_llm_model": PreferenceChange(1, '{"model_id":"forbidden"}'),
                    },
                )


async def test_secret_validation_refuses_plaintext_without_echoing_it(preferences):
    canary = "synthetic-plaintext-secret-canary"
    async with preferences() as (repo, _):
        encrypted = crypto.encrypt(canary)
        duplicate = (
            '{"api_key":'
            + json.dumps(canary)
            + ',"api_key":'
            + json.dumps(encrypted)
            + ',"hint":null}'
        )
        for value in (
            canary,
            json.dumps({"api_key": canary, "hint": None}),
            "enc:invalid",
            duplicate,
        ):
            with pytest.raises(ValueError) as caught:
                await repo.change(
                    actor("admin", "ACCOUNTADMIN"),
                    "__system__",
                    {
                        "tavily_api_key": PreferenceChange(0, value),
                    },
                )
            assert canary not in str(caught.value)
        assert not await repo.read(actor(), "__system__", ["tavily_api_key"])


async def test_imported_values_keep_owners_nulls_unicode_and_timestamp(preferences, environment):  # noqa: F811
    from datetime import UTC, datetime

    value = '{"first_name":"Mawar 🌺","note":"é"}'
    timestamp = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    await environment.admin.execute(
        "INSERT INTO nova_meta.config_user_preferences "
        "(user_name,pref_key,pref_value,updated_at) "
        "VALUES (%s,%s,%s,%s),(%s,%s,%s,%s),(%s,%s,%s,%s)",
        (
            "alice",
            "account.profile",
            value,
            timestamp,
            "alice",
            "workspace.last_schema",
            None,
            None,
            "alice",
            "legacy.unknown_key",
            "preserve for reconciliation",
            timestamp,
        ),
    )
    async with preferences() as (repo, _):
        records = await repo.read(
            actor(),
            "alice",
            [
                "account.profile",
                "workspace.last_schema",
                "legacy.unknown_key",
            ],
        )
        assert set(records) == {"account.profile", "workspace.last_schema"}
        assert records["account.profile"].value == value
        assert records["account.profile"].updated_at == timestamp
        assert records["workspace.last_schema"].value is None
        assert records["workspace.last_schema"].updated_at is None
        assert all(record.revision == 1 for record in records.values())
    cursor = await environment.admin.execute(
        "SELECT count(*) AS count FROM nova_meta.config_user_preferences"
    )
    assert (await cursor.fetchone())["count"] == 3


async def test_saved_role_does_not_change_the_active_role_or_entitlement(preferences):
    async with preferences() as (repo, _):
        await repo.change(
            actor(),
            "alice",
            {
                "workspace.last_role": PreferenceChange(0, "ACCOUNTADMIN"),
                "studio.role": PreferenceChange(0, "ACCOUNTADMIN"),
            },
        )
        with pytest.raises(MetadataPermissionDenied):
            await repo.change(
                actor(),
                "alice",
                {
                    "nova.news_enabled": PreferenceChange(0, "true"),
                },
            )


async def test_lock_failure_rolls_back_batch_and_next_write_recovers(preferences, environment):  # noqa: F811
    async with preferences() as (repo, _):
        await repo.change(
            actor(),
            "alice",
            {
                "account.profile": PreferenceChange(0, "original"),
                "studio.theme": PreferenceChange(0, "dark"),
            },
        )
        async with environment.admin.transaction():
            await environment.admin.execute(
                "SELECT * FROM nova_meta.config_user_preferences "
                "WHERE user_name='alice' AND pref_key='studio.theme' FOR UPDATE"
            )
            with pytest.raises(MetadataTimeout):
                await repo.change(
                    actor(),
                    "alice",
                    {
                        "account.profile": PreferenceChange(1, "must roll back"),
                        "studio.theme": PreferenceChange(1, "light"),
                    },
                )
        original = await repo.read(actor(), "alice", ["account.profile", "studio.theme"])
        assert original["account.profile"].value == "original"
        assert all(record.revision == 1 for record in original.values())
        assert (
            await repo.change(
                actor(),
                "alice",
                {
                    "studio.theme": PreferenceChange(1, "light"),
                },
            )
        )["studio.theme"].revision == 2


async def test_unscoped_access_unknown_keys_and_direct_revision_bypass_are_refused(preferences):
    async with preferences() as (repo, db):
        await repo.change(actor(), "alice", {"studio.theme": PreferenceChange(0, "dark")})
        async with db._pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT count(*) AS count FROM nova_meta.config_user_preferences"
            )
            assert (await cursor.fetchone())["count"] == 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(
                    "INSERT INTO nova_meta.config_user_preferences "
                    "(user_name,pref_key) VALUES ('alice','studio.theme')"
                )
        with pytest.raises(MetadataPermissionDenied):
            await repo.change(
                actor(), "alice", {"unreviewed.setting": PreferenceChange(0, "opaque")}
            )
        with pytest.raises(MetadataIntegrityViolation):
            async with db.transaction(actor()) as conn:
                await conn.execute(
                    "UPDATE nova_meta.config_user_preferences SET pref_value='bypass' "
                    "WHERE user_name='alice' AND pref_key='studio.theme'"
                )
        for statement in (
            "DELETE FROM nova_meta.config_user_preferences",
            "UPDATE nova_meta.config_user_preferences SET user_name='bob'",
            "UPDATE nova_meta.config_user_preferences SET updated_at=clock_timestamp()",
        ):
            with pytest.raises(MetadataPermissionDenied):
                async with db.transaction(actor()) as conn:
                    await conn.execute(statement)
        assert (await repo.read(actor(), "alice", ["studio.theme"]))["studio.theme"].revision == 1


async def test_maintenance_refuses_batch_and_preserves_reads(preferences, environment):  # noqa: F811
    async with preferences() as (repo, _):
        await repo.change(actor(), "alice", {"studio.theme": PreferenceChange(0, "dark")})
        await environment.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
        with pytest.raises(MetadataReadOnlyMaintenance):
            await repo.change(actor(), "alice", {"studio.theme": PreferenceChange(1, "light")})
        assert (await repo.read(actor(), "alice", ["studio.theme"]))["studio.theme"].value == "dark"


async def test_service_transaction_can_compose_preferences_with_its_other_writes(preferences):
    async with preferences() as (reader, db):
        with pytest.raises(RuntimeError, match="abort service transaction"):
            async with db.transaction(actor()) as connection:
                repo = PreferenceRepository(connection)
                await repo.change("alice", {"account.profile": PreferenceChange(0, "first")})
                await repo.change("alice", {"studio.theme": PreferenceChange(0, "dark")})
                raise RuntimeError("abort service transaction")
        assert not await reader.read(actor(), "alice", ["account.profile", "studio.theme"])
        async with db.transaction(actor()) as connection:
            repo = PreferenceRepository(connection)
            await repo.change("alice", {"account.profile": PreferenceChange(0, "first")})
            await repo.change("alice", {"studio.theme": PreferenceChange(0, "dark")})
        assert len(await reader.read(actor(), "alice", ["account.profile", "studio.theme"])) == 2
        async with db._pool.connection() as connection:
            with pytest.raises(ValueError, match="active metadata transaction"):
                PreferenceRepository(connection)


async def test_caught_batch_conflict_cannot_commit_partial_preferences(preferences):
    async with preferences() as (reader, db):
        await reader.change(
            actor(),
            "alice",
            {
                "account.profile": PreferenceChange(0, "original"),
                "studio.theme": PreferenceChange(0, "dark"),
            },
        )
        async with db.transaction(actor()) as connection:
            repo = PreferenceRepository(connection)
            with pytest.raises(MetadataConflict):
                await repo.change(
                    "alice",
                    {
                        "account.profile": PreferenceChange(1, "must roll back"),
                        "studio.theme": PreferenceChange(99, "light"),
                    },
                )
            await repo.change("alice", {"workspace.active_tab": PreferenceChange(0, "new")})
        records = await reader.read(
            actor(),
            "alice",
            [
                "account.profile",
                "studio.theme",
                "workspace.active_tab",
            ],
        )
        assert records["account.profile"].value == "original"
        assert all(record.revision == 1 for record in records.values())
        with pytest.raises(ValueError, match="active metadata transaction"):
            await repo.change("alice", {"studio.theme": PreferenceChange(1, "outside")})


@pytest.mark.parametrize("workload", ["query", "proxy"])
async def test_sql_workload_password_flags_require_active_admin(preferences, workload):
    async with preferences(workload) as (repo, _):
        administrator = actor("admin", "SECURITYADMIN")
        await repo.change(administrator, "alice", {
            "nova.must_change_password": PreferenceChange(0, "true"),
        })
        record = await repo.read(administrator, "alice", ["nova.must_change_password"])
        assert record["nova.must_change_password"].revision == 1
        with pytest.raises(MetadataPermissionDenied):
            await repo.change(actor(), "alice", {
                "nova.must_change_password": PreferenceChange(1, "false"),
            })
        for key, owner in (("nova.news_enabled", "alice"), ("studio.theme", "admin")):
            with pytest.raises(MetadataPermissionDenied):
                await repo.change(administrator, owner, {key: PreferenceChange(0, "true")})
        await repo.change(administrator, "alice", {
            "nova.must_change_password": PreferenceChange(1, "false"),
        })
        assert (await repo.read(actor(), "alice", ["nova.must_change_password"]))[
            "nova.must_change_password"
        ].revision == 2


async def test_password_policy_upgrade_preserves_existing_preference_rows(environment, tmp_path):  # noqa: F811
    await environment.bootstrap()
    for revision in load_migrations():
        if revision.version <= 2:
            (tmp_path / revision.name).write_text(revision.statement)
    result = await migrate(
        environment.role_config("nova_meta_migrator"), app_version="a" * 40, directory=tmp_path,
    )
    assert result.schema_version == 2
    await environment.admin.execute(
        "INSERT INTO nova_meta.config_user_preferences "
        "(user_name,pref_key,pref_value,revision) "
        "VALUES ('alice','nova.must_change_password','true',7)"
    )
    before = await environment.admin.execute(
        "SELECT * FROM nova_meta.config_user_preferences WHERE user_name='alice'"
    )
    original = await before.fetchone()
    result = await migrate(environment.role_config("nova_meta_migrator"), app_version="b" * 40)
    assert 3 in result.applied_versions
    after = await environment.admin.execute(
        "SELECT * FROM nova_meta.config_user_preferences WHERE user_name='alice'"
    )
    assert await after.fetchone() == original
