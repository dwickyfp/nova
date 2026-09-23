from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.access_control import router as access_router
from app.modules.access_control.schemas import DataScopeRequest
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.access_control.service import AccessControlError, AccessControlService
from app.modules.assistant.security import observation_context, secured_thread
from app.modules.assistant.state import AssistantMessage, AssistantThread
from app.modules.assistant.tools.query_execute import _active_role


def session(role="marketing", version=7):
    return {
        "username": "alice", "roles": ["marketing", "finance"],
        "active_role": role, "security_context_version": version, "session_id": "session-a",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("principal,city", [("alice", "Jakarta"), ("bob", "Bandung")])
async def test_data_scope_router_forwards_principal(monkeypatch, principal, city):
    put = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(access_router.access_control_service, "put_data_scope", put)
    body = DataScopeRequest(
        principal=principal, role="marketing", database="analytics", table="sales",
        bindings=[{"dimension": "city", "column": "city", "values": [city]}],
    )
    result = await access_router.put_data_scope(body, session())
    assert result["status"] == "PROPAGATING"
    assert put.await_args.kwargs["principal"] == principal
    assert put.await_args.kwargs["bindings"] == [("city", "city", [city])]


def test_body_role_and_assignment_order_cannot_select_tool_role():
    assert _active_role(SimpleNamespace(user=session(), role="finance")) == "marketing"
    for user in ({}, {"username": "alice", "roles": ["finance", "marketing"]}):
        with pytest.raises(SecurityContextError):
            _active_role(SimpleNamespace(user=user, role="finance"))
    user = session()
    user["active_role"] = "unassigned"
    with pytest.raises(SecurityContextError):
        _active_role(SimpleNamespace(user=user))


def test_observations_are_bound_to_principal_role_epoch_and_session():
    security = SecurityContext.from_session(session())
    stamp = observation_context(security)
    thread = AssistantThread(thread_id="t", user_name="alice", title="Conversation")
    thread.messages = [
        AssistantMessage("legacy", "assistant", "salary", steps=[{"kind": "table"}]),
        AssistantMessage("current", "assistant", "city", security_context=stamp),
    ]
    assert [m.message_id for m in secured_thread(thread, security).messages] == ["current"]
    for changed in (
        {**session(), "active_role": "finance"},
        {**session(), "security_context_version": 8},
        {**session(), "username": "bob"},
        {**session(), "session_id": "session-b"},
    ):
        assert secured_thread(thread, SecurityContext.from_session(changed)).messages == []
    assert len(thread.messages) == 2


@pytest.mark.asyncio
async def test_scope_validation_checks_metadata_and_membership(monkeypatch):
    ranger = SimpleNamespace(get_role=AsyncMock(return_value={"name": "marketing"}))
    service = AccessControlService(ranger)
    users = AsyncMock(return_value=[{"username": "alice", "roles": ["marketing"]}])
    metadata = AsyncMock(return_value={"rows": [["city"]]})
    monkeypatch.setattr("app.modules.users.service.user_service.list_users", users)
    monkeypatch.setattr("app.modules.access_control.service.db.execute_system", metadata)
    arguments = ("alice", "marketing", "default_catalog", "analytics", "sales")
    await service._validate_scope(*arguments, [("city", "city", ["Jakarta"])])
    with pytest.raises(AccessControlError, match="column"):
        await service._validate_scope(*arguments, [("city", "missing", ["Jakarta"])])
    with pytest.raises(AccessControlError, match="plain names"):
        await service._validate_scope(*arguments, [("city", "city) OR 1=1", ["Jakarta"])])
    users.return_value = [{"username": "alice", "roles": ["finance"]}]
    with pytest.raises(AccessControlError, match="assigned"):
        await service._validate_scope(*arguments, [("city", "city", ["Jakarta"])])
    users.return_value = []
    with pytest.raises(AccessControlError, match="principal"):
        await service._validate_scope(*arguments, [("city", "city", ["Jakarta"])])


@pytest.mark.parametrize("mode,roles", [
    ("none", []), ("all", []), ("explicit", []), ("explicit", ["marketing", "finance"]),
])
def test_ranger_rejects_ambiguous_default_roles(monkeypatch, mode, roles):
    from app.modules.users.schemas import UserCreate

    monkeypatch.setattr("app.core.config.settings.RANGER_ENABLED", True)
    with pytest.raises(ValueError, match="exactly one"):
        UserCreate(username="alice", password="test", default_role_mode=mode, default_roles=roles)


def test_ranger_accepts_one_explicit_default(monkeypatch):
    from app.modules.users.schemas import UserCreate

    monkeypatch.setattr("app.core.config.settings.RANGER_ENABLED", True)
    user = UserCreate(
        username="alice", password="test", default_role_mode="explicit", default_roles=["marketing"]
    )
    assert user.default_roles == ["marketing"]


def test_ranger_blocks_unqualified_flight_before_connect(monkeypatch):
    from app.modules.ml_engine.data.arrow_flight import ArrowFlightDataSource
    from app.modules.ml_engine.spec import MLSecurityContext

    monkeypatch.setattr("app.core.config.settings.RANGER_ENABLED", True)
    with pytest.raises(PermissionError, match="not qualified"):
        list(ArrowFlightDataSource()._open_reader(
            "SELECT * FROM sales", MLSecurityContext("alice", "test", role="marketing")
        ))
