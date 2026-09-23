"""Optional live Redis contract for versioned online feature materialization."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.modules.intelligence.online_features import (
    OnlineFeatureError,
    OnlineFeatureRecord,
    RedisOnlineFeatureStore,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NOVA_LIVE_REDIS_TEST") != "1", reason="Live Redis test is opt-in"
)


async def test_versioned_online_feature_lifecycle():
    store = RedisOnlineFeatureStore()
    entity = {"customer_id": str(uuid4())}
    now = datetime.now(UTC)
    current = OnlineFeatureRecord("nova_acceptance", 1, entity, {"orders_7d": 7}, now, 60)
    stale = OnlineFeatureRecord(
        "nova_acceptance", 1, entity, {"orders_7d": 99}, now - timedelta(minutes=1), 60
    )
    conflict = OnlineFeatureRecord("nova_acceptance", 1, entity, {"orders_7d": 99}, now, 60)
    next_version = OnlineFeatureRecord("nova_acceptance", 2, entity, {"orders_7d": 8}, now, 60)
    assert await store.health()
    try:
        await store.publish(current)
        await store.publish(current)
        assert (await store.get("nova_acceptance", 1, entity))["values"] == {"orders_7d": 7}
        assert await store.get("nova_acceptance", 2, entity) is None
        with pytest.raises(OnlineFeatureError, match="Older"):
            await store.publish(stale)
        with pytest.raises(OnlineFeatureError, match="Conflicting"):
            await store.publish(conflict)
        await store.publish(next_version)
        records = await store.batch_get("nova_acceptance", 2, [entity, {"customer_id": "absent"}])
        assert records[0]["values"] == {"orders_7d": 8}
        assert records[1] is None
    finally:
        await store.delete("nova_acceptance", 1, entity)
        await store.delete("nova_acceptance", 2, entity)
