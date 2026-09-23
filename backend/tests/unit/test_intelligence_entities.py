"""Shared entities keep identity stable and never bypass caller source grants."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.common.nova_system import ENTITIES_DDL
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


def test_entity_contract_supports_compound_keys():
    entity = EntityCreate.model_validate(BODY)
    assert entity.key_columns == ["tenant_id", "customer_id"]
    assert "PRIMARY KEY(catalog_name, database_name, schema_name, name)" in ENTITIES_DDL


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

    async def forbidden_sql(*_args, **_kwargs):
        raise AssertionError("metadata write must not run")

    monkeypatch.setattr(EntityRegistry, "_authorized", unauthorized)
    monkeypatch.setattr(entities.db, "execute_system", forbidden_sql)
    with pytest.raises(HTTPException) as exc:
        await EntityRegistry().create(EntityCreate.model_validate(BODY), USER)
    assert exc.value.status_code == 403


async def test_list_hides_entities_without_source_grants(monkeypatch):
    async def execute_system(_sql, _params=None):
        return {
            "rows": [
                [
                    "one",
                    "customer",
                    "",
                    "default_catalog",
                    "commerce",
                    "",
                    "commerce.customers",
                    '["customer_id"]',
                    "alice",
                    "[]",
                    "ACTIVE",
                    None,
                    None,
                ],
                [
                    "two",
                    "account",
                    "",
                    "default_catalog",
                    "finance",
                    "",
                    "finance.accounts",
                    '["account_id"]',
                    "bob",
                    "[]",
                    "ACTIVE",
                    None,
                    None,
                ],
            ]
        }

    async def authorized(_user, relation, _keys, _db):
        return relation == "commerce.customers"

    monkeypatch.setattr(entities.db, "execute_system", execute_system)
    monkeypatch.setattr(EntityRegistry, "_authorized", staticmethod(authorized))
    visible = await EntityRegistry().list(USER)
    assert [entity.id for entity in visible] == ["one"]


async def test_entity_deprecation_is_owner_scoped_and_audited(monkeypatch):
    registry = EntityRegistry()
    row = [
        "one",
        "customer",
        "",
        "default_catalog",
        "commerce",
        "",
        "commerce.customers",
        '["customer_id"]',
        "alice",
        "[]",
        "ACTIVE",
        None,
        None,
    ]
    statements = []
    audit = []

    async def execute_system(sql, _params=None):
        statements.append(sql)
        return {"rows": [row]} if sql.startswith("SELECT") else {"rows": []}

    async def authorized(*_args):
        return True

    async def write_log(**kwargs):
        audit.append(kwargs)

    monkeypatch.setattr(entities.db, "execute_system", execute_system)
    monkeypatch.setattr(EntityRegistry, "_authorized", authorized)
    monkeypatch.setattr(entities, "write_audit_log", write_log)
    assert await registry.deprecate("one", USER)
    assert any("status='DEPRECATED'" in sql for sql in statements)
    assert audit[0]["action"] == "DEPRECATE"
