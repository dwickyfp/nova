"""The read-only grant survives a restart, but never a change of role or session."""

from __future__ import annotations

import pytest

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


async def test_grants_are_per_user_and_thread(redis):
    await read_grants.grant(thread(), ANALYST, "t1", True)
    other_thread = AssistantThread("t2", "alice", "Other")
    await read_grants.apply(other_thread, ANALYST, "t2")
    bob = AssistantThread("t1", "bob", "Bob")
    await read_grants.apply(bob, {**ANALYST, "username": "bob"}, "t1")
    assert not other_thread.consent.always_allow_read_only
    assert not bob.consent.always_allow_read_only
