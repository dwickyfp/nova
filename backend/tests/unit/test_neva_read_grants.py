"""The read-only grant survives a restart, but never a change of role or session."""

from __future__ import annotations

import json

import pytest

from app.core.exceptions import InvalidCredentialsError
from app.modules.assistant import read_grants
from app.modules.assistant.state import AssistantThread

ANALYST = {"username": "alice", "active_role": "analyst", "assigned_roles": ["analyst", "sales"],
           "session_id": "s1", "security_context_version": 1}


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def set(self, key, value, ex=None):
        assert ex == read_grants.GRANT_SECONDS
        self.data[key] = value

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)

    async def eval(self, script, keys, key, value):
        assert keys == 1
        if self.data.get(key) == value:
            return int(self.data.pop(key, None) is not None)
        return 0


@pytest.fixture
def redis(monkeypatch):
    store = FakeRedis()
    monkeypatch.setattr(read_grants, "_redis", lambda: store)
    return store


def thread():
    return AssistantThread("t1", "alice", "Grant")


async def test_a_grant_comes_back_after_a_restart(redis):
    await read_grants.grant(thread(), ANALYST, "t1", True)
    fresh = thread()  # the process restarted: nothing in memory
    await read_grants.apply(fresh, ANALYST, "t1")
    assert fresh.consent.always_allow_read_only


@pytest.mark.parametrize("changed", [
    {"active_role": "sales"}, {"security_context_version": 2},
])
async def test_another_role_or_session_never_inherits_it(redis, changed):
    live = thread()
    await read_grants.grant(live, ANALYST, "t1", True)
    other = {**ANALYST, **changed}
    # Same process (role switched in the account menu between turns) ...
    await read_grants.apply(live, other, "t1")
    assert not live.consent.always_allow_read_only
    # ... and after a restart: the stored grant is gone too.
    await read_grants.grant(thread(), ANALYST, "t1", True)
    fresh = thread()
    await read_grants.apply(fresh, other, "t1")
    assert not fresh.consent.always_allow_read_only
    assert redis.data == {}


async def test_revoking_removes_it_everywhere(redis):
    live = thread()
    await read_grants.grant(live, ANALYST, "t1", True)
    await read_grants.grant(live, ANALYST, "t1", False)
    fresh = thread()
    await read_grants.apply(fresh, ANALYST, "t1")
    assert not live.consent.always_allow_read_only and not fresh.consent.always_allow_read_only


async def test_without_redis_nothing_is_restored(monkeypatch):
    monkeypatch.setattr(read_grants, "_redis", lambda: None)
    live = thread()
    await read_grants.grant(live, ANALYST, "t1", True)
    assert live.consent.always_allow_read_only  # this process still honours it
    fresh = thread()
    await read_grants.apply(fresh, ANALYST, "t1")
    assert not fresh.consent.always_allow_read_only
    await read_grants.apply(live, ANALYST, "t1")
    assert not live.consent.always_allow_read_only


async def test_grants_are_per_user_and_thread(redis):
    await read_grants.grant(thread(), ANALYST, "t1", True)
    other_thread = AssistantThread("t2", "alice", "Other")
    await read_grants.apply(other_thread, ANALYST, "t2")
    bob = AssistantThread("t1", "bob", "Bob")
    await read_grants.apply(bob, {**ANALYST, "username": "bob"}, "t1")
    assert not other_thread.consent.always_allow_read_only
    assert not bob.consent.always_allow_read_only


@pytest.mark.parametrize("same_process", [False, True])
async def test_a_new_login_does_not_inherit_or_erase_another_sessions_grant(redis, same_process):
    live = thread()
    await read_grants.grant(live, ANALYST, "t1", True)
    newcomer = {**ANALYST, "session_id": "s2"}
    other = live if same_process else thread()
    await read_grants.apply(other, newcomer, "t1")
    assert not other.consent.always_allow_read_only
    assert await read_grants.restore(ANALYST, "t1")
    await read_grants.grant(other, newcomer, "t1", True)
    await read_grants.grant(other, newcomer, "t1", False)
    assert await read_grants.restore(ANALYST, "t1")
    assert not await read_grants.restore(newcomer, "t1")


@pytest.mark.parametrize("revoked", [False, True])
async def test_expired_or_remotely_revoked_grant_is_not_retained_in_memory(redis, revoked):
    live = thread()
    await read_grants.grant(live, ANALYST, "t1", True)
    if revoked:
        await read_grants.grant(thread(), ANALYST, "t1", False)
    else:
        redis.data.clear()
    await read_grants.apply(live, ANALYST, "t1")
    assert not live.consent.always_allow_read_only
    assert live.consent.granted_under is None


async def test_legacy_grant_without_session_binding_is_not_restored(redis):
    old_key = "nova:assistant:read-grant:alice:t1"
    redis.data[old_key] = json.dumps({"role": "analyst", "version": 1})
    assert not await read_grants.restore(ANALYST, "t1")


@pytest.mark.parametrize("changed", [
    {"session_id": None}, {"session_id": ""}, {"session_id": " "}, {"session_id": 42},
    {"active_role": None}, {"security_context_version": "invalid"},
])
async def test_a_grant_requires_a_valid_session_security_context(redis, changed):
    invalid = {**ANALYST, **changed}
    live = thread()
    with pytest.raises(InvalidCredentialsError):
        await read_grants.grant(live, invalid, "t1", True)
    assert not live.consent.always_allow_read_only
    assert not await read_grants.restore(invalid, "t1")
    assert not redis.data


async def test_a_stale_reader_cannot_delete_a_concurrent_new_grant(redis, monkeypatch):
    await read_grants.grant(thread(), ANALYST, "t1", True)
    current = {**ANALYST, "security_context_version": 2}
    old_get = redis.get
    replaced = False

    async def get_and_replace(key):
        nonlocal replaced
        old = await old_get(key)
        if not replaced:
            replaced = True
            await read_grants.grant(thread(), current, "t1", True)
        return old

    monkeypatch.setattr(redis, "get", get_and_replace)
    assert not await read_grants.restore(current, "t1")
    assert await read_grants.restore(current, "t1")


async def test_unavailable_redis_drops_remembered_approval_without_leaking_details(
    redis, monkeypatch, caplog,
):
    live = thread()
    await read_grants.grant(live, ANALYST, "t1", True)

    async def fail(_key):
        raise RuntimeError("synthetic-private-cache-credential")

    monkeypatch.setattr(redis, "get", fail)
    await read_grants.apply(live, ANALYST, "t1")
    assert not live.consent.always_allow_read_only
    assert "synthetic-private-cache-credential" not in caplog.text
