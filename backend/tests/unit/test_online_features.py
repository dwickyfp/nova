"""Online cache identities and payloads reject invalid feature records."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.intelligence.feature_store import _online_value
from app.modules.intelligence.online_features import (
    OnlineFeatureError,
    OnlineFeatureRecord,
    RedisOnlineFeatureStore,
)


def test_default_redis_client_is_recreated_for_a_new_event_loop(monkeypatch):
    import app.modules.intelligence.online_features as module

    clients = []

    class Client:
        async def get(self, key):
            return None

    def create_client(*args, **kwargs):
        client = Client()
        clients.append(client)
        return client

    monkeypatch.setattr(module.aioredis, "from_url", create_client)
    store = RedisOnlineFeatureStore()
    asyncio.run(store.get("sales", 1, {"order_id": 1}))
    asyncio.run(store.get("sales", 1, {"order_id": 1}))
    assert len(clients) == 2


def test_identity_is_canonical_and_versioned():
    first = RedisOnlineFeatureStore._key("churn", 2, {"tenant": 1, "customer": "a"})
    reversed_order = RedisOnlineFeatureStore._key("churn", 2, {"customer": "a", "tenant": 1})
    older = RedisOnlineFeatureStore._key("churn", 1, {"tenant": 1, "customer": "a"})
    assert first == reversed_order
    assert first != older
    assert "customer" not in first


@pytest.mark.parametrize(
    "group,version,entity,values,ttl",
    [
        ("bad;drop", 1, {"id": 1}, {"x": 1}, 60),
        ("valid", 0, {"id": 1}, {"x": 1}, 60),
        ("valid", 1, {}, {"x": 1}, 60),
        ("valid", 1, {"id": True}, {"x": 1}, 60),
        ("valid", 1, {"id": 1}, {"x": float("nan")}, 60),
        ("valid", 1, {"id": 1}, {"x": 1}, 0),
    ],
)
def test_invalid_records_are_rejected(group, version, entity, values, ttl):
    record = OnlineFeatureRecord(group, version, entity, values, datetime.now(UTC), ttl)
    with pytest.raises(OnlineFeatureError):
        record.validate()


def test_decimal_feature_values_keep_precision_in_online_cache():
    value = _online_value(Decimal("123456789.123456789"))
    record = OnlineFeatureRecord("sales", 1, {"order_id": 1}, {"amount": value},
                                 datetime.now(UTC), 60)
    record.validate()
    assert value == "123456789.123456789"
