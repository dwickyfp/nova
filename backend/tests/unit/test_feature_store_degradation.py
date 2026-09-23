"""Online cache outages leave governed offline feature lookup available."""

from datetime import datetime
from types import SimpleNamespace

from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import settings
from app.core.database import db
from app.modules.intelligence.entities import entity_registry
from app.modules.intelligence.feature_store import (
    FeatureLookup,
    feature_store,
    online_store,
)


async def test_online_failure_uses_offline_projection(monkeypatch):
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
                "source_relation": "NOVA_SYSTEM.orders",
                "feature_columns": ["orders_7d"],
                "event_timestamp": "event_ts",
            },
        }

    async def offline(_sql):
        return {"rows": [[datetime(2026, 1, 1), 7]]}

    async def unavailable(*_args):
        raise RedisConnectionError("offline")

    async def audit(*_args):
        return None

    monkeypatch.setattr(settings, "RANGER_ENABLED", False)
    monkeypatch.setattr(feature_store, "_authorized_group", group)
    monkeypatch.setattr(entity_registry, "get", entity)
    monkeypatch.setattr(feature_store, "_view", view)
    monkeypatch.setattr(feature_store, "_view_version", version)
    monkeypatch.setattr(db, "execute_system", offline)
    monkeypatch.setattr(online_store, "get", unavailable)
    monkeypatch.setattr(online_store, "publish", unavailable)
    monkeypatch.setattr("app.modules.intelligence.feature_store._audit", audit)
    result = await feature_store.lookup(
        "customer",
        FeatureLookup(entity_key={"customer_id": 1}),
        {"username": "alice"},
    )
    assert result["values"] == {"orders_7d": 7}
    assert result["source"] == "offline_fallback"
