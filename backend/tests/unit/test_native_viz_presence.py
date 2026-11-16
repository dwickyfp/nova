"""Dashboard presence: who else is here, gated by view access, expiring on its own."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import fakeredis
import httpx
import pytest
from fastapi import FastAPI

from app.core.deps import get_current_user
from app.core.exceptions import register_exception_handlers
from app.modules.viz import presence as presence_module
from app.modules.viz import router as module
from app.modules.viz.presence import PresenceService, viewer_key


def person(name: str, session: str, version: int = 1) -> dict:
    return {"username": name, "active_role": "ANALYST", "session_id": session,
            "assigned_roles": ["ANALYST"], "security_context_version": version}


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def store(redis):
    return PresenceService(lambda: redis)


async def allowed() -> bool:
    return True


async def test_lists_others_but_never_yourself_or_a_session_id(store, redis):
    dashboard = uuid4()
    page = uuid4()
    await store.beat(person("alice", "alice-login"), dashboard, page_id=page,
                     cursor={"x": 0.25, "y": 3}, editing=True, can_view=allowed)
    seen = await store.beat(person("bob", "bob-login"), dashboard, page_id=page,
                            cursor=None, editing=False, can_view=allowed)
    assert [(item["name"], item["cursor"], item["editing"]) for item in seen["others"]] == [
        ("alice", {"x": 0.25, "y": 3.0}, True)]
    assert seen["viewer"] == viewer_key("bob-login")
    stored = await redis.hgetall(f"nova:viz:presence:{dashboard}")
    assert "alice-login" not in str(stored) and "bob-login" not in str(stored)
    assert "ANALYST" not in str(stored)
    assert 0 < await redis.ttl(f"nova:viz:presence:{dashboard}") <= 30


async def test_two_tabs_of_one_person_are_two_viewers(store):
    dashboard = uuid4()
    await store.beat(person("alice", "tab-1"), dashboard, page_id=None, cursor=None,
                     editing=False, can_view=allowed)
    seen = await store.beat(person("alice", "tab-2"), dashboard, page_id=None, cursor=None,
                            editing=False, can_view=allowed)
    assert [item["name"] for item in seen["others"]] == ["alice"]


async def test_stale_and_corrupt_entries_are_dropped(store, redis, monkeypatch):
    dashboard = uuid4()
    key = f"nova:viz:presence:{dashboard}"
    clock = iter([1000.0, 1000.0 + 16])
    monkeypatch.setattr(presence_module, "_now", lambda: next(clock))
    await store.beat(person("alice", "a"), dashboard, page_id=None, cursor=None,
                     editing=False, can_view=allowed)
    await redis.hset(key, "broken", "{not json")
    seen = await store.beat(person("bob", "b"), dashboard, page_id=None, cursor=None,
                            editing=False, can_view=allowed)
    assert seen["others"] == []
    assert set(await redis.hkeys(key)) == {viewer_key("b")}


@pytest.mark.parametrize("cursor, kept", [
    ({"x": 2, "y": -4}, {"x": 1.0, "y": 0.0}),
    ({"x": float("nan"), "y": 1}, None),
    ({"x": "left", "y": 1}, None),
])
async def test_cursor_positions_are_clamped_or_dropped(store, cursor, kept):
    dashboard = uuid4()
    await store.beat(person("alice", "a"), dashboard, page_id=None, cursor=cursor,
                     editing=False, can_view=allowed)
    seen = await store.beat(person("bob", "b"), dashboard, page_id=None, cursor=None,
                            editing=False, can_view=allowed)
    assert seen["others"][0]["cursor"] == kept


async def test_access_is_checked_once_per_security_context_version(store):
    dashboard = uuid4()
    check = AsyncMock(return_value=True)
    for _ in range(3):
        await store.beat(person("alice", "a"), dashboard, page_id=None, cursor=None,
                         editing=False, can_view=check)
    assert check.await_count == 1
    # A role switch bumps the version; access is checked again.
    await store.beat(person("alice", "a", version=2), dashboard, page_id=None, cursor=None,
                     editing=False, can_view=check)
    assert check.await_count == 2


async def test_no_view_access_records_nothing(store, redis):
    dashboard = uuid4()
    with pytest.raises(PermissionError):
        await store.beat(person("mallory", "m"), dashboard, page_id=None, cursor=None,
                         editing=False, can_view=AsyncMock(return_value=False))
    assert await redis.exists(f"nova:viz:presence:{dashboard}") == 0


async def test_leave_removes_only_this_viewer(store):
    dashboard = uuid4()
    for name in ("alice", "bob"):
        await store.beat(person(name, name), dashboard, page_id=None, cursor=None,
                         editing=False, can_view=allowed)
    await store.leave(person("alice", "alice"), dashboard)
    seen = await store.beat(person("carol", "carol"), dashboard, page_id=None, cursor=None,
                            editing=False, can_view=allowed)
    assert [item["name"] for item in seen["others"]] == ["bob"]


@pytest.fixture
async def api(monkeypatch, store):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(module.router, prefix="/viz")
    app.dependency_overrides[get_current_user] = lambda: person("alice", "alice-login")
    service = SimpleNamespace(require_kind=AsyncMock(),
                              permissions=AsyncMock(return_value=["view"]))
    monkeypatch.setattr(module, "service", service)
    monkeypatch.setattr(module, "presence", store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://native.test") as client:
        yield client, service


async def test_route_checks_the_dashboard_and_view_permission(api):
    client, service = api
    dashboard = uuid4()
    response = await client.put(f"/viz/dashboards/{dashboard}/presence",
                                json={"page_id": str(uuid4()), "cursor": {"x": 0.5, "y": 2},
                                      "editing": True})
    assert response.status_code == 200
    assert response.json()["others"] == []
    service.require_kind.assert_awaited_once()
    service.permissions.assert_awaited_once()


async def test_route_hides_a_dashboard_the_viewer_cannot_see(api):
    client, service = api
    service.permissions.return_value = []
    response = await client.put(f"/viz/dashboards/{uuid4()}/presence", json={})
    assert response.status_code == 404


async def test_route_rejects_unknown_fields(api):
    client, _ = api
    response = await client.put(f"/viz/dashboards/{uuid4()}/presence",
                                json={"cursor": {"x": 0, "y": 0, "colour": "red"}})
    assert response.status_code == 422


async def test_leave_route(api):
    client, _ = api
    response = await client.request("DELETE", f"/viz/dashboards/{uuid4()}/presence", json={})
    assert response.status_code == 204


async def test_presence_rebuilds_after_redis_loses_the_key(store, redis):
    dashboard = uuid4()
    for name in ("alice", "bob"):
        await store.beat(person(name, name), dashboard, page_id=None, cursor=None,
                         editing=False, can_view=allowed)
    await redis.delete(f"nova:viz:presence:{dashboard}")
    # Each viewer's next heartbeat puts them back; nothing else depends on the key.
    await store.beat(person("alice", "alice"), dashboard, page_id=None, cursor=None,
                     editing=False, can_view=allowed)
    seen = await store.beat(person("bob", "bob"), dashboard, page_id=None, cursor=None,
                            editing=True, can_view=allowed)
    assert [item["name"] for item in seen["others"]] == ["alice"]
