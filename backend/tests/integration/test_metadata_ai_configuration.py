"""AI metadata, immutable definitions and registration intents commit together."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
import pytest_asyncio

from app.common.durable_jobs import JobDefinition, JobRepository
from app.core.metadata_db import (
    MetadataConflict,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
)
from app.modules.access_control.security_context import SecurityContext
from app.modules.ai_ml.metadata_service import AIConfigurationService
from app.modules.ai_ml.repository import AIRegistryRepository, ModelDefinition
from app.modules.llm_functions.registrations import FunctionRegistrationRepository
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
async def configuration(registry):  # noqa: F811
    @asynccontextmanager
    async def transaction(*args, **kwargs):
        async with registry(*args, **kwargs, publish_functions=False) as (_, connection):
            yield (
                AIConfigurationService(connection),
                FunctionRegistrationRepository(connection),
                connection,
            )

    async with transaction() as (service, _, _):
        await service.create_provider("provider-1", provider())
        await service.create_model("model-1", model())
    return transaction


def alias(**changes):
    return replace(AliasDefinition("Example", "complete", "provider-1", "model-1"), **changes)


async def create(configuration, identity="alias-1", **changes):
    async with configuration() as (service, _, _):
        return await service.create_alias(identity, alias(**changes))


async def counts(configuration):
    async with configuration(read_only=True) as (_, _, connection):
        cursor = await connection.execute(
            "SELECT (SELECT count(*) FROM nova_meta.ai_function_definitions) AS definitions, "
            "(SELECT count(*) FROM nova_meta.ai_function_heads) AS heads, "
            "(SELECT count(*) FROM nova_meta.job_queue) AS jobs, "
            "(SELECT count(*) FROM nova_meta.outbox_events) AS outbox"
        )
        return await cursor.fetchone()


async def test_definition_job_outbox_and_alias_share_the_original_actor(configuration):
    change = await create(configuration, system_prompt="synthetic-instruction")
    assert len(change.registration_jobs) == 1
    job = change.registration_jobs[0]
    assert job["kind"] == "ai.function.reconcile" and job["queue"] == "maintenance"
    assert job["state"] == "PENDING" and job["external_effect"] is True
    async with configuration(read_only=True) as (_, registrations, _):
        head = await registrations.head("complete")
        frozen = await registrations.definition(head["definition_id"])
    assert head["version"] == frozen["version"] == job["definition_version"] == 1
    assert head["digest"] == frozen["digest"] == job["definition_digest"]
    assert hashlib.sha256(frozen["payload"].encode()).hexdigest() == frozen["digest"]
    assert all(
        job[key] == frozen[key]
        for key in ("owner_principal", "owner_role", "principal_type", "security_context_version")
    )
    assert frozen["owner_principal"] == ADMIN.principal
    snapshot = json.loads(frozen["payload"])
    assert snapshot["contract_version"] == 1 and snapshot["alias"]["revision"] == 1
    assert snapshot["alias"]["system_prompt"] == "synthetic-instruction"
    assert await counts(configuration) == {"definitions": 1, "heads": 1, "jobs": 1, "outbox": 1}


async def test_secret_and_endpoint_overrides_do_not_enter_definitions_or_queue(configuration):
    change = await create(
        configuration,
        default_params=json.dumps(
            {
                "temperature": 0.25,
                "max_tokens": 128,
                "top_p": 0.8,
                "timeout_ms": 5000,
                "api_key": "synthetic-parameter-key",
                "endpoint": "https://private.example.test",
                "model": "unapproved-override",
                "headers": {"Authorization": "synthetic-header"},
            }
        ),
    )
    async with configuration(read_only=True) as (_, registrations, connection):
        frozen = await registrations.definition(change.registration_jobs[0]["definition_ref"])
        cursor = await connection.execute("SELECT * FROM nova_meta.outbox_events")
        serialized = json.dumps(
            [frozen, change.registration_jobs, await cursor.fetchall()], default=str
        )
    for value in (
        "synthetic-parameter-key",
        "private.example.test",
        "synthetic-header",
        "unapproved-override",
        "synthetic-registry-secret",
        "enc:",
    ):
        assert value not in serialized
    assert json.loads(frozen["payload"])["parameters"] == {
        "temperature": 0.25,
        "max_tokens": 128,
        "top_p": 0.8,
        "timeout_ms": 5000,
    }


async def test_configured_snapshot_remains_immutable_after_provider_and_model_changes(
    configuration,
):
    first = (await create(configuration)).registration_jobs[0]
    async with configuration(read_only=True) as (_, registrations, _):
        original = await registrations.definition(first["definition_ref"])
    async with configuration() as (service, _, _):
        changed = await service.replace_provider(
            "provider-1", replace(provider(), name="Renamed"), expected_revision=1
        )
        assert len(changed.registration_jobs) == 1
    async with configuration() as (service, _, _):
        changed = await service.replace_model(
            "model-1", replace(model(), name="new-model"), expected_revision=1
        )
        assert len(changed.registration_jobs) == 1
    async with configuration(read_only=True) as (_, registrations, _):
        assert await registrations.definition(first["definition_ref"]) == original
        head = await registrations.head("complete")
        payload = json.loads((await registrations.definition(head["definition_id"]))["payload"])
    assert head["version"] == 3
    assert payload["provider"]["revision"] == payload["model"]["revision"] == 2
    assert payload["model"]["name"] == "new-model"


async def test_default_move_reconciles_both_old_and_new_functions(configuration):
    await create(configuration)
    async with configuration() as (service, _, _):
        change = await service.replace_alias(
            "alias-1", alias(function_type="sentiment"), expected_revision=1
        )
    assert len(change.registration_jobs) == 2
    async with configuration(read_only=True) as (_, registrations, _):
        old = await registrations.head("complete")
        new = await registrations.head("sentiment")
        old_payload = json.loads((await registrations.definition(old["definition_id"]))["payload"])
    assert old["version"] == 2 and new["version"] == 1
    assert old_payload["mode"] == "unconfigured" and old_payload["alias"] is None


async def test_nonselected_alias_does_not_install_a_different_function(configuration):
    await create(configuration)
    change = await create(configuration, "alias-2", is_default=False)
    assert change.registration_jobs == ()
    async with configuration() as (service, _, _):
        change = await service.replace_alias("alias-2", alias(is_default=True), expected_revision=1)
    assert len(change.registration_jobs) == 1
    assert await counts(configuration) == {"definitions": 2, "heads": 1, "jobs": 2, "outbox": 2}


async def test_disabled_default_and_last_deletion_have_explicit_frozen_states(configuration):
    await create(configuration)
    await create(configuration, "alias-2", is_default=False)
    async with configuration() as (service, registrations, _):
        change = await service.replace_alias("alias-1", alias(is_active=False), expected_revision=1)
        payload = json.loads(
            (await registrations.definition(change.registration_jobs[0]["definition_ref"]))[
                "payload"
            ]
        )
        assert payload["mode"] == "disabled" and payload["alias"]["id"] == "alias-1"
        await service.delete_alias("alias-2", expected_revision=1)
        change = await service.delete_alias("alias-1", expected_revision=2)
        payload = json.loads(
            (await registrations.definition(change.registration_jobs[0]["definition_ref"]))[
                "payload"
            ]
        )
        assert payload["mode"] == "unconfigured"


async def test_reconciliation_without_changes_does_not_duplicate_jobs(configuration):
    await create(configuration)
    async with configuration() as (_, registrations, _):
        assert await registrations.reconcile_changed() == []
        assert await registrations.reconcile_changed() == []
    assert await counts(configuration) == {"definitions": 1, "heads": 1, "jobs": 1, "outbox": 1}


async def test_concurrent_default_commands_keep_monotonic_immutable_versions(configuration):
    await asyncio.gather(create(configuration, "alias-1"), create(configuration, "alias-2"))
    async with configuration(read_only=True) as (_, registrations, connection):
        head = await registrations.head("complete")
        aliases = await ModelAliasRepository(connection).aliases()
        payload = json.loads((await registrations.definition(head["definition_id"]))["payload"])
    assert head["version"] == 2
    assert payload["alias"]["id"] == next(row["id"] for row in aliases if row["is_default"])
    assert await counts(configuration) == {"definitions": 2, "heads": 1, "jobs": 2, "outbox": 2}


async def test_service_rollback_and_caught_stale_revision_leave_no_registration_intent(
    configuration,
):
    await create(configuration)
    with pytest.raises(RuntimeError, match="abort service transaction"):
        async with configuration() as (service, _, _):
            await service.replace_alias("alias-1", alias(alias_name="Aborted"), expected_revision=1)
            raise RuntimeError("abort service transaction")
    async with configuration() as (service, _, _):
        with pytest.raises(MetadataConflict):
            await service.replace_alias("alias-1", alias(), expected_revision=2)
        await service.create_alias("alias-2", alias(is_default=False))
    assert await counts(configuration) == {"definitions": 1, "heads": 1, "jobs": 1, "outbox": 1}


@pytest.mark.parametrize("target", ["alias", "provider", "model"])
async def test_configuration_without_registration_intent_cannot_commit(configuration, target):
    await create(configuration)
    with pytest.raises(MetadataIntegrityViolation):
        async with configuration() as (_, _, connection):
            if target == "alias":
                await ModelAliasRepository(connection).replace(
                    "alias-1",
                    alias(alias_name="Missing intent"),
                    expected_revision=1,
                )
            elif target == "provider":
                await AIRegistryRepository(connection).replace_provider(
                    "provider-1",
                    replace(provider(), name="Missing intent"),
                    expected_revision=1,
                )
            else:
                await AIRegistryRepository(connection).replace_model(
                    "model-1",
                    replace(model(), name="Missing intent"),
                    expected_revision=1,
                )
    assert await counts(configuration) == {"definitions": 1, "heads": 1, "jobs": 1, "outbox": 1}


@pytest.mark.parametrize("missing", ["head", "job", "outbox"])
async def test_incomplete_definition_job_boundary_cannot_commit(configuration, missing):
    with pytest.raises(MetadataIntegrityViolation):
        async with configuration() as (_, _, connection):
            await ModelAliasRepository(connection).create("alias-1", alias())
            cursor = await connection.execute(
                "SELECT nova_meta.ai_function_snapshot('complete')::text AS payload"
            )
            payload = (await cursor.fetchone())["payload"]
            identity, digest = uuid4(), hashlib.sha256(payload.encode()).hexdigest()
            await connection.execute(
                "INSERT INTO nova_meta.ai_function_definitions "
                "(id,function_type,version,payload,digest) "
                "VALUES (%s,'complete',1,%s,%s)",
                (identity, payload, digest),
            )
            if missing != "head":
                await connection.execute(
                    "INSERT INTO nova_meta.ai_function_heads "
                    "(function_type,definition_id,version,digest) VALUES ('complete',%s,1,%s)",
                    (identity, digest),
                )
            if missing == "job":
                return
            definition = JobDefinition(
                "maintenance",
                "ai.function.reconcile",
                identity,
                1,
                digest,
                uuid4(),
                hashlib.sha256(b"nova.ai.function.complete").hexdigest(),
                True,
            )
            if missing == "outbox":
                await connection.execute(
                    "INSERT INTO nova_meta.job_queue "
                    "(id,queue,kind,definition_ref,definition_version,"
                    "definition_digest,idempotency_key,resource_key,external_effect) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        uuid4(),
                        definition.queue,
                        definition.kind,
                        identity,
                        1,
                        digest,
                        definition.idempotency_key,
                        definition.resource_key,
                        True,
                    ),
                )
            else:
                await JobRepository(connection).enqueue(definition)


@pytest.mark.parametrize(
    "changes",
    [
        {"queue": "task"},
        {"definition_version": 2},
        {"definition_digest": "0" * 64},
        {"resource_key": "0" * 64},
        {"external_effect": False},
        {"definition_ref": uuid4()},
    ],
)
async def test_function_job_cannot_lie_about_its_definition(configuration, changes):
    original = (await create(configuration)).registration_jobs[0]
    definition = JobDefinition(
        **{
            key: original[key]
            for key in (
                "queue",
                "kind",
                "definition_ref",
                "definition_version",
                "definition_digest",
                "idempotency_key",
                "resource_key",
                "external_effect",
            )
        }
    )
    with pytest.raises(MetadataIntegrityViolation):
        async with configuration() as (_, _, connection):
            await JobRepository(connection).enqueue(
                replace(definition, idempotency_key=uuid4(), **changes)
            )


async def test_one_definition_cannot_enqueue_a_second_independent_registration(configuration):
    original = (await create(configuration)).registration_jobs[0]
    definition = JobDefinition(
        **{
            key: original[key]
            for key in (
                "queue",
                "kind",
                "definition_ref",
                "definition_version",
                "definition_digest",
                "idempotency_key",
                "resource_key",
                "external_effect",
            )
        }
    )
    async with configuration() as (_, _, connection):
        assert (await JobRepository(connection).enqueue(definition))["id"] == original["id"]
    with pytest.raises(MetadataConflict):
        async with configuration() as (_, _, connection):
            await JobRepository(connection).enqueue(replace(definition, idempotency_key=uuid4()))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE nova_meta.ai_function_definitions SET version=2",
        "DELETE FROM nova_meta.ai_function_definitions",
        "TRUNCATE nova_meta.ai_function_definitions CASCADE",
        "DELETE FROM nova_meta.ai_function_heads",
    ],
)
async def test_runtime_cannot_rewrite_history_or_remove_its_head(configuration, statement):
    await create(configuration)
    with pytest.raises(MetadataPermissionDenied):
        async with configuration() as (_, _, connection):
            await connection.execute(statement)


async def test_definition_read_is_restricted_to_admin_and_maintenance_service(configuration):
    identity = (await create(configuration)).registration_jobs[0]["definition_ref"]
    async with configuration(actor=READER, read_only=True) as (_, registrations, _):
        assert await registrations.definition(identity) is None
    async with configuration("maintenance", READER, read_only=True) as (_, registrations, _):
        assert await registrations.definition(identity) is None
    worker = SecurityContext("registration-worker", "ACCOUNTADMIN", principal_type="SERVICE")
    async with configuration("maintenance", worker, read_only=True) as (_, registrations, _):
        assert (await registrations.definition(identity))["id"] == identity
    with pytest.raises(MetadataPermissionDenied):
        async with configuration("query", ADMIN, read_only=True) as (_, registrations, _):
            await registrations.definition(identity)


@pytest.mark.parametrize(
    "parameters",
    [
        {"temperature": "secret-canary"},
        {"temperature": 3},
        {"top_p": -1},
        {"max_tokens": 0},
        {"max_tokens": 1.5},
        {"timeout_ms": True},
    ],
)
async def test_invalid_engine_parameters_rollback_the_alias_and_intent(configuration, parameters):
    with pytest.raises(MetadataIntegrityViolation) as error:
        await create(configuration, default_params=json.dumps(parameters))
    assert "secret-canary" not in str(error.value)
    assert await counts(configuration) == {"definitions": 0, "heads": 0, "jobs": 0, "outbox": 0}


async def test_registration_repository_rejects_use_after_transaction(configuration):
    async with configuration(read_only=True) as (_, registrations, connection):
        assert await registrations.head("complete") is None
    with pytest.raises(ValueError, match="active metadata transaction"):
        await registrations.reconcile_changed()
    with pytest.raises(ValueError, match="active metadata transaction"):
        FunctionRegistrationRepository(connection)


async def test_snapshot_preserves_parameter_precision_without_python_float_rounding(configuration):
    value = "0.123456789012345678901234567890"
    change = await create(configuration, default_params='{"temperature":' + value + "}")
    async with configuration(read_only=True) as (_, registrations, _):
        frozen = await registrations.definition(change.registration_jobs[0]["definition_ref"])
    assert value in frozen["payload"]


async def test_embedding_snapshot_retains_descriptor_without_claiming_live_registration(
    configuration,
):
    async with configuration() as (service, registrations, connection):
        await service.create_model(
            "embed-1",
            ModelDefinition(
                "provider-1",
                "synthetic-embedding",
                "embedding",
                logical_alias="synthetic-embedding",
                revision="vendor-v1",
                dimensions=32,
                modality="text",
                metric="cosine",
            ),
        )
        change = await service.create_alias(
            "alias-1", alias(function_type="embed", model_id="embed-1")
        )
        job = change.registration_jobs[0]
        payload = json.loads((await registrations.definition(job["definition_ref"]))["payload"])
        assert payload["model"]["descriptor_revision"] == "vendor-v1"
        assert payload["model"]["dimensions"] == 32 and payload["model"]["revision"] == 1
        assert job["state"] == "PENDING"
        cursor = await connection.execute(
            "SELECT count(*) AS n FROM nova_meta.external_effect_intents"
        )
        assert (await cursor.fetchone())["n"] == 0


@pytest.mark.parametrize("corruption", ["payload", "digest", "version"])
async def test_forged_definition_cannot_commit(configuration, corruption):
    with pytest.raises(MetadataIntegrityViolation):
        async with configuration() as (_, _, connection):
            await ModelAliasRepository(connection).create("alias-1", alias())
            cursor = await connection.execute(
                "SELECT nova_meta.ai_function_snapshot('complete')::text AS payload"
            )
            payload = (await cursor.fetchone())["payload"]
            if corruption == "payload":
                data = json.loads(payload)
                data["provider"]["revision"] = 99
                payload = json.dumps(data)
            digest = (
                "0" * 64 if corruption == "digest" else hashlib.sha256(payload.encode()).hexdigest()
            )
            await connection.execute(
                "INSERT INTO nova_meta.ai_function_definitions "
                "(id,function_type,version,payload,digest) VALUES (%s,'complete',%s,%s,%s)",
                (uuid4(), 2 if corruption == "version" else 1, payload, digest),
            )


async def test_foreign_administrator_cannot_enqueue_under_another_definition_actor(configuration):
    original = (await create(configuration)).registration_jobs[0]
    definition = JobDefinition(
        **{
            key: original[key]
            for key in (
                "queue",
                "kind",
                "definition_ref",
                "definition_version",
                "definition_digest",
                "idempotency_key",
                "resource_key",
                "external_effect",
            )
        }
    )
    actor = SecurityContext("other-admin", "ACCOUNTADMIN")
    with pytest.raises(MetadataIntegrityViolation):
        async with configuration(actor=actor) as (_, _, connection):
            await JobRepository(connection).enqueue(replace(definition, idempotency_key=uuid4()))


async def test_shared_provider_change_creates_one_intent_for_each_selected_function(configuration):
    await create(configuration)
    await create(configuration, "alias-2", function_type="sentiment")
    async with configuration() as (service, registrations, _):
        change = await service.replace_provider(
            "provider-1",
            replace(provider(), is_active=False),
            expected_revision=1,
        )
        assert len(change.registration_jobs) == 2
        payloads = [
            json.loads((await registrations.definition(job["definition_ref"]))["payload"])
            for job in change.registration_jobs
        ]
    assert {item["function_type"] for item in payloads} == {"complete", "sentiment"}
    assert all(
        item["mode"] == "disabled" and item["provider"]["revision"] == 2 for item in payloads
    )
    assert await counts(configuration) == {"definitions": 4, "heads": 2, "jobs": 4, "outbox": 4}


async def test_alias_and_provider_deletion_preserve_old_definitions_and_clear_credentials(
    configuration,
):
    original = (await create(configuration)).registration_jobs[0]
    async with configuration() as (service, _, _):
        await service.delete_alias("alias-1", expected_revision=1)
        await service.delete_provider("provider-1", expected_revision=1)
    async with configuration(read_only=True) as (_, registrations, connection):
        history = json.loads(
            (await registrations.definition(original["definition_ref"]))["payload"]
        )
        assert history["mode"] == "configured" and history["provider"]["id"] == "provider-1"
        registry_repo = AIRegistryRepository(connection)
        assert (await registry_repo.provider("provider-1"))["is_deleted"]
        assert (await registry_repo.model("model-1"))["is_deleted"]
        assert await registry_repo.credential("provider-1") is None
    assert await counts(configuration) == {"definitions": 2, "heads": 1, "jobs": 2, "outbox": 2}


async def test_provider_change_serializes_with_alias_creation(configuration):
    async def update_provider():
        async with configuration() as (service, _, _):
            await service.replace_provider(
                "provider-1", replace(provider(), name="New"), expected_revision=1
            )

    await asyncio.gather(create(configuration), update_provider())
    async with configuration(read_only=True) as (_, registrations, _):
        head = await registrations.head("complete")
        payload = json.loads((await registrations.definition(head["definition_id"]))["payload"])
    assert payload["provider"]["revision"] == 2 and payload["alias"]["id"] == "alias-1"
