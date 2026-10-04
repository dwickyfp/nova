"""The per-user News switch: default off, administrator-set, enforced server-side."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.common import news_entitlement
from app.modules.agents.tools.intelligence import ContextGraphTool
from app.modules.intelligence import engine_router
from app.modules.users import router as users_router
from app.modules.users.schemas import UserUpdate

ADMIN = {
    "username": "nova_admin",
    "roles": ["ACCOUNTADMIN"],
    "active_role": "ACCOUNTADMIN",
    "session_id": "s-admin",
    "security_context_version": 3,
}
READER = {
    "username": "manager",
    "roles": ["news_editor"],
    "active_role": "news_editor",
    "session_id": "s-reader",
    "security_context_version": 1,
}


class Store:
    """In-memory stand-in for the preferences table and the audit log."""

    def __init__(self):
        self.rows: dict[tuple[str, str], str] = {}
        self.audit: list[dict] = []
        self.fail_reads = False

    async def execute_system(self, sql, params=None):
        if sql.startswith("SELECT"):
            if self.fail_reads:
                raise RuntimeError("metadata store unavailable")
            value = self.rows.get((params[0], params[1]))
            return {"rows": [[value]] if value is not None else []}
        self.rows[(params[0], params[1])] = params[2]
        return {"rows": []}

    async def write_audit_log(self, **fields):
        self.audit.append(fields)
        return "audit-id"


@pytest.fixture
def store(monkeypatch):
    fake = Store()
    monkeypatch.setattr(news_entitlement.db, "execute_system", fake.execute_system)
    monkeypatch.setattr(news_entitlement, "write_audit_log", fake.write_audit_log)
    return fake


async def test_news_is_off_until_an_administrator_enables_it(store):
    assert await news_entitlement.is_news_enabled("manager") is False

    await news_entitlement.set_news_enabled("manager", enabled=True, actor=ADMIN)

    assert await news_entitlement.is_news_enabled("manager") is True
    assert await news_entitlement.is_news_enabled("someone_else") is False


async def test_a_store_failure_means_disabled(store):
    await news_entitlement.set_news_enabled("manager", enabled=True, actor=ADMIN)
    store.fail_reads = True

    assert await news_entitlement.is_news_enabled("manager") is False


async def test_switching_off_takes_effect(store):
    await news_entitlement.set_news_enabled("manager", enabled=True, actor=ADMIN)
    await news_entitlement.set_news_enabled("manager", enabled=False, actor=ADMIN)

    assert await news_entitlement.is_news_enabled("manager") is False


async def test_entitlement_changes_are_audited_with_the_administrator_context(store):
    await news_entitlement.set_news_enabled("manager", enabled=True, actor=ADMIN)

    assert store.audit == [
        {
            "event_type": "USER_ADMIN",
            "user_name": "nova_admin",
            "action": "SET_NEWS_ENTITLEMENT",
            "object_type": "USER",
            "object_name": "manager",
            "status": "SUCCESS",
            "session_id": "s-admin",
            "active_role": "ACCOUNTADMIN",
            "security_context_version": 3,
            "decision": "ENABLE",
        }
    ]


async def test_scheduled_execution_accounts_cannot_be_enabled(store):
    with pytest.raises(ValueError, match="scheduled execution account"):
        await news_entitlement.set_news_enabled(
            "nova_task_service_news", enabled=True, actor=ADMIN
        )

    assert store.rows == {}


async def test_a_disabled_caller_is_refused_and_the_refusal_is_audited(store):
    with pytest.raises(HTTPException) as refused:
        await news_entitlement.require_news_entitlement(READER)

    assert refused.value.status_code == 403
    assert refused.value.detail == "News is not enabled for this account"
    assert [(row["status"], row["decision"], row["action"]) for row in store.audit] == [
        ("DENIED", "DENY", "READ_NEWS")
    ]
    assert store.audit[0]["active_role"] == "news_editor"
    assert store.audit[0]["security_context_version"] == 1


async def test_account_administrators_get_no_bypass(store):
    with pytest.raises(HTTPException) as refused:
        await news_entitlement.require_news_entitlement(ADMIN)

    assert refused.value.status_code == 403


async def test_a_failed_audit_write_still_refuses(store, monkeypatch):
    async def broken(**_fields):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(news_entitlement, "write_audit_log", broken)

    with pytest.raises(HTTPException) as refused:
        await news_entitlement.require_news_entitlement(READER)

    assert refused.value.status_code == 403


async def test_an_enabled_caller_passes_without_an_audit_row(store):
    await news_entitlement.set_news_enabled("manager", enabled=True, actor=ADMIN)
    store.audit.clear()

    await news_entitlement.require_news_entitlement(READER)

    assert store.audit == []


def _dependency_calls(route) -> set:
    found, stack = set(), [route.dependant]
    while stack:
        node = stack.pop()
        found.add(node.call)
        stack.extend(node.dependencies)
    return found


def test_every_news_route_requires_the_entitlement():
    routes = [
        route
        for route in engine_router.router.routes
        if route.path == "/news" or route.path.startswith("/news/")
    ]

    assert {(route.path, tuple(sorted(route.methods))) for route in routes} == {
        ("/news", ("GET",)),
        ("/news/{record_id}", ("GET",)),
        ("/news/{news_id}/investigate", ("POST",)),
        ("/news/{news_id}/operations", ("POST",)),
    }
    for route in routes:
        assert news_entitlement._news_user in _dependency_calls(route), route.path


def test_other_intelligence_routes_keep_the_plain_session_gate():
    for route in engine_router.router.routes:
        if route.path in {"/monitors", "/decisions", "/investigations/{record_id}"}:
            assert news_entitlement._news_user not in _dependency_calls(route), route.path


async def test_the_agent_tool_cannot_open_news_for_a_disabled_caller(store, monkeypatch):
    opened = []

    async def get(kind, record_id, user):
        opened.append((kind, record_id))
        raise AssertionError("the record must not be loaded")

    from app.modules.intelligence import engine

    monkeypatch.setattr(engine.intelligence_service, "get", get)
    context = SimpleNamespace(user=READER, agent_id=None)
    invocation = SimpleNamespace(arguments={"record_kind": "news", "record_id": "n1"})

    outcome = await ContextGraphTool().run(invocation, context)

    assert outcome.ok is False
    assert outcome.error == "News is not enabled for this account"
    assert opened == []
    assert store.audit[0]["action"] == "TOOL_READ_NEWS"


class UserService:
    def __init__(self):
        self.engine_updates = []

    async def get_user_detail(self, username, host="%"):
        if username == "ghost":
            raise ValueError("User 'ghost@%' not found")
        return {"username": username, "host": host, "identity": f"'{username}'@'{host}'",
                "is_protected": False}

    async def update_user(self, **fields):
        self.engine_updates.append(fields)
        return ["ALTER USER IDENTIFIED BY"]


@pytest.fixture
def users(store, monkeypatch):
    fake = UserService()
    monkeypatch.setattr(users_router, "user_service", fake)
    monkeypatch.setattr(users_router, "set_news_enabled", news_entitlement.set_news_enabled)
    monkeypatch.setattr(users_router, "is_news_enabled", news_entitlement.is_news_enabled)
    return fake


@pytest.mark.parametrize("ranger", [False, True])
async def test_the_switch_alone_updates_no_engine_state(store, users, monkeypatch, ranger):
    monkeypatch.setattr(users_router.settings, "RANGER_ENABLED", ranger)

    result = await users_router.update_user(
        "manager", UserUpdate(news_enabled=True), host="%", user=ADMIN
    )

    assert result["updated"] == ["SET NEWS ON"]
    assert users.engine_updates == []
    assert await news_entitlement.is_news_enabled("manager") is True


async def test_the_switch_combines_with_an_engine_update(store, users, monkeypatch):
    monkeypatch.setattr(users_router.settings, "RANGER_ENABLED", False)

    result = await users_router.update_user(
        "manager", UserUpdate(password="N3w-secret!", news_enabled=False), host="%", user=ADMIN
    )

    assert result["updated"] == ["ALTER USER IDENTIFIED BY", "SET NEWS OFF"]
    assert len(users.engine_updates) == 1


async def test_the_switch_is_refused_for_an_unknown_user(store, users, monkeypatch):
    monkeypatch.setattr(users_router.settings, "RANGER_ENABLED", False)

    with pytest.raises(HTTPException) as refused:
        await users_router.update_user(
            "ghost", UserUpdate(news_enabled=True), host="%", user=ADMIN
        )

    assert refused.value.status_code == 400
    assert store.rows == {}


async def test_an_empty_update_is_still_rejected(store, users, monkeypatch):
    monkeypatch.setattr(users_router.settings, "RANGER_ENABLED", True)

    with pytest.raises(HTTPException) as refused:
        await users_router.update_user("manager", UserUpdate(), host="%", user=ADMIN)

    assert refused.value.status_code == 400
    assert refused.value.detail == "No update fields provided"


def test_the_update_route_stays_behind_the_administrator_gate():
    route = next(
        route
        for route in users_router.router.routes
        if route.path == "/{username}" and "PUT" in route.methods
    )

    assert users_router._admin in _dependency_calls(route)
