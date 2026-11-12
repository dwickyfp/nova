"""Shared entities keep identity stable and never bypass caller source grants."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.metadata_db import MetadataConflict
from app.modules.intelligence import entities
from app.modules.intelligence.entities import EntityCreate, EntityRegistry

USER = {
    "username": "alice",
    "encrypted_password": "encrypted",
    "active_role": "analyst",
    "session_id": "session-1",
}
BODY = {
    "name": "customer",
    "database": "commerce",
    "relation": "commerce.customers",
    "key_columns": ["tenant_id", "customer_id"],
}




def entity_row(entity_id="one", relation="commerce.customers", owner="alice", **changes):
    return {
        "id": entity_id, "name": "customer", "description": None,
        "catalog_name": "default_catalog", "database_name": relation.split(".")[0],
        "schema_name": "", "relation_name": relation, "key_columns": ["customer_id"],
        "owner_name": owner, "tags": None, "status": "ACTIVE", "created_at": None,
        "updated_at": None, "metadata_revision": 3, **changes,
    }


def use_metadata(monkeypatch, repo):
    """Yield ``repo`` from every metadata transaction and record read-only flags."""
    transactions = []

    @asynccontextmanager
    async def transaction(actor, *, read_only=False):
        transactions.append((actor.principal, read_only))
        yield repo

    monkeypatch.setattr(entities, "intelligence_metadata", transaction)
    monkeypatch.setattr(
        entities, "user_actor", lambda user: SimpleNamespace(principal=user["username"])
    )
    return transactions


def test_entity_contract_supports_compound_keys():
    entity = EntityCreate.model_validate(BODY)
    assert entity.key_columns == ["tenant_id", "customer_id"]


@pytest.mark.parametrize(
    "change",
    [
        {"key_columns": []},
        {"key_columns": ["id", "id"]},
        {"relation": "customers"},
        {"relation": "other.customers"},
        {"description": "api_key=secret-value"},
    ],
)
def test_entity_contract_rejects_unsafe_metadata(change):
    with pytest.raises(ValidationError):
        EntityCreate.model_validate({**BODY, **change})


async def test_source_probe_uses_active_caller_and_compound_key(monkeypatch):
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(error=None)

    monkeypatch.setattr(entities.query_service, "execute", execute)
    assert await EntityRegistry._authorized(
        USER, "commerce.customers", ["tenant_id", "customer_id"], "commerce"
    )
    assert calls[0]["username"] == "alice"
    assert calls[0]["role"] == "analyst"
    assert calls[0]["sql"] == (
        "SELECT `tenant_id`, `customer_id` FROM `commerce`.`customers` WHERE 1 = 0"
    )


async def test_source_probe_fails_closed(monkeypatch):
    async def execute(**_kwargs):
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(entities.query_service, "execute", execute)
    assert not await EntityRegistry._authorized(
        USER, "commerce.customers", ["customer_id"], "commerce"
    )


async def test_create_requires_source_access_and_does_not_write(monkeypatch):
    async def unauthorized(*_args):
        return False

    repo = AsyncMock()
    transactions = use_metadata(monkeypatch, repo)
    monkeypatch.setattr(EntityRegistry, "_authorized", unauthorized)
    with pytest.raises(HTTPException) as exc:
        await EntityRegistry().create(EntityCreate.model_validate(BODY), USER)
    assert exc.value.status_code == 403
    assert transactions == [] and repo.create_entity.await_count == 0


async def test_create_registers_the_scoped_entity_as_the_caller(monkeypatch):
    repo = AsyncMock()
    repo.get_entity.return_value = entity_row()
    transactions = use_metadata(monkeypatch, repo)
    monkeypatch.setattr(EntityRegistry, "_authorized", AsyncMock(return_value=True))
    monkeypatch.setattr(entities, "write_audit_log", AsyncMock())
    monkeypatch.setattr(entities, "_relink", AsyncMock())
    created = await EntityRegistry().create(EntityCreate.model_validate(BODY), USER)
    data = repo.create_entity.await_args.args[0]
    assert (data["catalog"], data["database"], data["schema"], data["name"]) == (
        "default_catalog", "commerce", "", "customer")
    assert data["key_columns"] == ["tenant_id", "customer_id"]
    assert transactions[0] == ("alice", False)
    assert created.description == "" and created.tags == []


async def test_a_taken_entity_name_keeps_its_409(monkeypatch):
    repo = AsyncMock()
    repo.create_entity.side_effect = MetadataConflict()
    use_metadata(monkeypatch, repo)
    monkeypatch.setattr(EntityRegistry, "_authorized", AsyncMock(return_value=True))
    with pytest.raises(HTTPException) as exc:
        await EntityRegistry().create(EntityCreate.model_validate(BODY), USER)
    assert (exc.value.status_code, exc.value.detail) == (409, "Entity already exists")


async def test_list_hides_entities_without_source_grants(monkeypatch):
    repo = AsyncMock()
    repo.list_entities.return_value = [
        entity_row(), entity_row("two", "finance.accounts", "bob"),
    ]
    transactions = use_metadata(monkeypatch, repo)

    async def authorized(_user, relation, _keys, _db):
        return relation == "commerce.customers"

    monkeypatch.setattr(EntityRegistry, "_authorized", staticmethod(authorized))
    visible = await EntityRegistry().list(USER)
    assert [entity.id for entity in visible] == ["one"]
    assert transactions == [("alice", True)]


async def test_entity_deprecation_is_owner_scoped_and_audited(monkeypatch):
    repo = AsyncMock()
    repo.get_entity.return_value = entity_row()
    use_metadata(monkeypatch, repo)
    audit = AsyncMock()
    monkeypatch.setattr(EntityRegistry, "_authorized", AsyncMock(return_value=True))
    monkeypatch.setattr(entities, "write_audit_log", audit)
    monkeypatch.setattr(entities, "_relink", AsyncMock())
    assert await EntityRegistry().deprecate("one", USER)
    repo.deprecate_entity.assert_awaited_once_with("one", expected_revision=3)
    assert audit.await_args.kwargs["action"] == "DEPRECATE"


async def test_entity_deprecation_refuses_another_owner(monkeypatch):
    repo = AsyncMock()
    repo.get_entity.return_value = entity_row(owner="bob")
    use_metadata(monkeypatch, repo)
    monkeypatch.setattr(EntityRegistry, "_authorized", AsyncMock(return_value=True))
    assert not await EntityRegistry().deprecate("one", USER)
    assert repo.deprecate_entity.await_count == 0
