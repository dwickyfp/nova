"""Feature lookup reads through the caller's governed query path."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.database import db
from app.modules.intelligence.entities import entity_registry
from app.modules.intelligence.feature_store import FeatureLookup, feature_store
from app.modules.query.service import query_service


async def test_lookup_queries_the_source_as_the_caller(monkeypatch):
    async def group(_name, _user, _version=None):
        return ({"entity_id": "entity-1"}, {"version": 1}, [{"view_name": "orders", "version": 1}])

    async def entity(_id, _user):
        return SimpleNamespace(key_columns=["customer_id"])

    async def view(_name):
        return {"ttl_seconds": 60, "freshness_seconds": 86400}

    async def version(_name, _version):
        return {
            "relation_name": "`_NOVA_FEATURES`.`FV_abc_V1`",
            "definition": {
                "source_relation": "sales.orders",
                "feature_columns": ["orders_7d"],
                "event_timestamp": "event_ts",
            },
        }

    execute = AsyncMock(
        return_value=SimpleNamespace(error=None, rows=[[datetime(2026, 1, 1), 7]])
    )
    system = AsyncMock(side_effect=AssertionError("system identity must not read features"))
    monkeypatch.setattr(feature_store, "_authorized_group", group)
    monkeypatch.setattr(entity_registry, "get", entity)
    monkeypatch.setattr(feature_store, "_view", view)
    monkeypatch.setattr(feature_store, "_view_version", version)
    monkeypatch.setattr(query_service, "execute", execute)
    monkeypatch.setattr(db, "execute_system", system)
    monkeypatch.setattr("app.modules.intelligence.feature_store._audit", AsyncMock())
    user = {
        "username": "alice",
        "encrypted_password": "sealed",
        "active_role": "analyst",
        "session_id": "s1",
    }
    result = await feature_store.lookup(
        "customer", FeatureLookup(entity_key={"customer_id": 1}), user
    )
    assert result["values"] == {"orders_7d": 7}
    assert result["source"] == "offline"
    call = execute.await_args.kwargs
    assert call["username"] == "alice" and call["role"] == "analyst"
    assert call["database"] == "sales"
    assert "`sales`.`orders`" in call["sql"]
