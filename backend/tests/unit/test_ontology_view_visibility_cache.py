"""Ontology reports reuse a Semantic View visibility answer briefly, per caller context."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.redis import session_store
from app.modules.intelligence import semantic_views
from app.modules.ontology.representations import SemanticViewCatalog


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    async def incr(self, key: str) -> None:
        self.data[key] = str(int(self.data.get(key, "0")) + 1)


def user(role: str = "ANALYST", version: int = 3) -> dict:
    return {"username": "alice", "active_role": role, "roles": [role],
            "session_id": "login-1", "security_context_version": version}


@pytest.fixture
def engine(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(session_store, "_redis", redis)
    monkeypatch.setattr(settings, "CATALOG_VISIBILITY_CACHE_SECONDS", 60)
    state = {"view": {"status": "ACTIVE", "active_version": 1}, "checks": 0, "answer": True,
             "error": None}

    async def get(view_id):
        return dict(state["view"]) if state["view"] else None

    async def is_visible(view_id, caller):
        state["checks"] += 1
        if state["error"]:
            raise state["error"]
        return state["answer"]

    service = semantic_views.semantic_view_service
    monkeypatch.setattr(service, "_get", get)
    monkeypatch.setattr(service, "is_visible", is_visible)
    return state, redis


async def test_the_same_caller_context_reuses_the_answer(engine):
    state, _ = engine
    catalog = SemanticViewCatalog()
    assert await catalog.visible("v1", user()) is True
    assert await catalog.visible("v1", user()) is True
    assert state["checks"] == 1


@pytest.mark.parametrize("other", [user(role="FINANCE"), user(version=4)])
async def test_a_role_switch_or_new_security_context_checks_again(engine, other):
    state, _ = engine
    catalog = SemanticViewCatalog()
    await catalog.visible("v1", user())
    state["answer"] = False
    assert await catalog.visible("v1", other) is False
    assert state["checks"] == 2


async def test_a_managed_access_change_retires_every_answer(engine):
    from app.modules.catalog import visibility

    state, _ = engine
    catalog = SemanticViewCatalog()
    await catalog.visible("v1", user())
    await visibility.bump()
    state["answer"] = False
    assert await catalog.visible("v1", user()) is False
    assert state["checks"] == 2


async def test_a_new_published_version_checks_again(engine):
    state, _ = engine
    catalog = SemanticViewCatalog()
    await catalog.visible("v1", user())
    state["view"] = {"status": "ACTIVE", "active_version": 2}
    state["answer"] = False
    assert await catalog.visible("v1", user()) is False
    assert state["checks"] == 2


async def test_an_engine_failure_is_not_cached(engine):
    state, redis = engine
    catalog = SemanticViewCatalog()
    state["error"] = ConnectionError("engine down")
    with pytest.raises(ConnectionError):
        await catalog.visible("v1", user())
    assert not [key for key in redis.data if "semantic-view" in key]
    state["error"] = None
    assert await catalog.visible("v1", user()) is True


async def test_a_missing_view_is_not_visible_without_a_check(engine):
    state, _ = engine
    state["view"] = None
    assert await SemanticViewCatalog().visible("gone", user()) is False
    assert state["checks"] == 0


async def test_without_redis_every_call_checks(engine, monkeypatch):
    state, _ = engine
    monkeypatch.setattr(session_store, "_redis", None)
    catalog = SemanticViewCatalog()
    await catalog.visible("v1", user())
    await catalog.visible("v1", user())
    assert state["checks"] == 2
