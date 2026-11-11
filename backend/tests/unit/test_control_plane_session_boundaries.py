import asyncio
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.core import deps
from app.core.exceptions import InsufficientRoleError, SessionExpiredError
from app.core.redis import SESSION_PREFIX, SessionStore
from app.modules.ai_ml.router import require_admin


@pytest.fixture
async def store():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    instance = SessionStore()
    instance._redis = redis
    try:
        yield instance
    finally:
        await instance.close()


async def make_session(store):
    return await store.create(
        "alice",
        "sealed",
        ["analyst", "finance"],
        default_role="analyst",
        active_role="analyst",
    )


@pytest.mark.asyncio
async def test_one_hundred_role_switches_have_unique_revisions(store):
    sid = await make_session(store)
    barrier = asyncio.Event()

    async def switch(index):
        await barrier.wait()
        role = "analyst" if index % 2 else "finance"
        return await store.set_active_role(sid, role), role

    tasks = [asyncio.create_task(switch(i)) for i in range(100)]
    barrier.set()
    results = await asyncio.gather(*tasks)
    assert sorted(version for version, _ in results) == list(range(2, 102))
    final = await store.get(sid)
    assert final["security_context_version"] == 101
    assert final["active_role"] == max(results)[1]
    assert await store._redis.ttl(SESSION_PREFIX + sid) > 0


@pytest.mark.asyncio
async def test_logout_and_role_switch_never_resurrect_session(store):
    for _ in range(20):
        sid = await make_session(store)
        await asyncio.gather(
            store.delete(sid), store.set_active_role(sid, "finance"), return_exceptions=True
        )
        assert await store.get(sid) is None
        with pytest.raises(ValueError, match="missing or expired"):
            await store.set_active_role(sid, "finance")
        assert not await store._redis.exists(SESSION_PREFIX + sid)


@pytest.mark.asyncio
async def test_unassigned_role_does_not_change_session(store):
    sid = await make_session(store)
    before = await store.get(sid)
    with pytest.raises(ValueError, match="not assigned"):
        await store.set_active_role(sid, "ACCOUNTADMIN")
    assert await store.get(sid) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", ["", "0", "-1", "bad", "1.5", "9007199254740991"])
async def test_corrupt_revision_fails_before_role_mutation(store, revision):
    sid = await make_session(store)
    key = SESSION_PREFIX + sid
    await store._redis.hset(key, "security_context_version", revision)
    before = await store._redis.hgetall(key)
    with pytest.raises(ValueError, match="revision is invalid"):
        await store.set_active_role(sid, "finance")
    assert await store._redis.hgetall(key) == before


@pytest.mark.asyncio
async def test_legacy_session_revision_starts_at_two(store):
    sid = await make_session(store)
    await store._redis.hdel(SESSION_PREFIX + sid, "security_context_version")
    assert await store.set_active_role(sid, "finance") == 2


@pytest.mark.asyncio
async def test_session_cannot_supply_credentials_for_a_different_jwt_principal(monkeypatch):
    monkeypatch.setattr(deps, "decode_token", lambda _: {"sub": "alice", "sid": "bob-session"})
    get = AsyncMock(return_value={"username": "bob", "encrypted_password": "sealed"})
    refresh = AsyncMock()
    monkeypatch.setattr(deps.session_store, "get", get)
    monkeypatch.setattr(deps.session_store, "refresh", refresh)
    with pytest.raises(SessionExpiredError):
        await deps.get_current_user(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="test")
        )
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_admin_assignment_does_not_authorize_inactive_role():
    user = {"username": "alice", "roles": ["ACCOUNTADMIN", "analyst"], "active_role": "analyst"}
    with pytest.raises(InsufficientRoleError):
        await require_admin.dependency(user)
    user["active_role"] = "ACCOUNTADMIN"
    assert await require_admin.dependency(user) is user
