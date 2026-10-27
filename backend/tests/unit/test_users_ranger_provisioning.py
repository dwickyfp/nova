from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.users import router
from app.modules.users.schemas import UserCreate
from app.modules.users.service import UserService

ADMIN = {
    "username": "nova_admin",
    "assigned_roles": ["ACCOUNTADMIN"],
    "active_role": "ACCOUNTADMIN",
    "session_id": "admin-session",
    "security_context_version": 1,
}


@pytest.fixture
def provisioning(monkeypatch):
    service = UserService()
    execute = AsyncMock(return_value={"rows": [], "columns": []})
    assign = AsyncMock()
    revoke = AsyncMock()
    drop = AsyncMock()
    monkeypatch.setattr(router, "user_service", service)
    monkeypatch.setattr("app.modules.users.service.db.execute_system", execute)
    monkeypatch.setattr(service, "list_users", AsyncMock(return_value=[{
        "username": "alice", "host": "%", "identity": "'alice'@'%'",
        "is_protected": False, "roles": ["analyst"],
        "default_roles": ["analyst"], "default_role_mode": "explicit",
    }]))
    monkeypatch.setattr(service, "drop_user", drop)
    monkeypatch.setattr(router.access_control_service, "assign_role", assign)
    monkeypatch.setattr(router.access_control_service, "revoke_role", revoke)
    body = UserCreate(
        username="alice", password="temporary-test-password",
        granted_roles=["analyst"], default_role_mode="explicit", default_roles=["analyst"],
    )
    return body, execute, assign, revoke, drop


async def test_create_user_installs_ranger_membership_before_default(provisioning):
    body, execute, assign, revoke, drop = provisioning

    async def assigned(*args, **kwargs):
        assert not any("SET DEFAULT ROLE" in call.args[0] for call in execute.await_args_list)

    assign.side_effect = assigned
    result = await router.create_user(body, ADMIN)
    assert result.username == "alice"
    assert assign.await_args.args[0].principal == "nova_admin"
    assert assign.await_args.args[0].active_role == "ACCOUNTADMIN"
    assert assign.await_args.kwargs == {"role": "analyst", "username": "alice", "host": "%"}
    sql = [call.args[0] for call in execute.await_args_list]
    assert sql[0].startswith("CREATE USER 'alice'@'%'")
    assert "SET DEFAULT ROLE `analyst` TO 'alice'@'%'" in sql
    assert not any(statement.startswith(("GRANT", "REVOKE")) for statement in sql)
    revoke.assert_not_awaited()
    drop.assert_not_awaited()


async def test_failed_ranger_membership_removes_partial_account(provisioning):
    body, execute, assign, revoke, drop = provisioning
    assign.side_effect = RuntimeError("provider unavailable")
    with pytest.raises(HTTPException) as caught:
        await router.create_user(body, ADMIN)
    assert caught.value.status_code == 400
    assert caught.value.detail == "Unable to create user"
    assert not any("SET DEFAULT ROLE" in call.args[0] for call in execute.await_args_list)
    revoke.assert_not_awaited()
    drop.assert_awaited_once_with("alice", host="%")


async def test_failed_default_compensates_ranger_membership(provisioning):
    body, execute, assign, revoke, drop = provisioning

    async def fail_default(sql):
        if sql.startswith("SET DEFAULT ROLE"):
            raise RuntimeError("default update failed")
        return {"rows": [], "columns": []}

    execute.side_effect = fail_default
    with pytest.raises(HTTPException):
        await router.create_user(body, ADMIN)
    assign.assert_awaited_once()
    assert revoke.await_args.kwargs == {"role": "analyst", "username": "alice", "host": "%"}
    drop.assert_awaited_once_with("alice", host="%")


async def test_user_grants_report_role_markers_not_native_privileges(monkeypatch):
    service = UserService()
    rows = [
        ("'alice'@'%'", "default_catalog", "GRANT 'analyst', 'root' TO 'alice'@'%'"),
        (
            "'alice'@'%'",
            "default_catalog",
            "GRANT SELECT ON TABLE sales.orders TO USER 'alice'@'%'",
        ),
    ]
    monkeypatch.setattr(
        "app.modules.users.service.db.execute_system",
        AsyncMock(return_value={"rows": rows, "columns": []}),
    )
    monkeypatch.setattr(service, "list_users", AsyncMock(return_value=[{
        "username": "alice", "host": "%", "identity": "'alice'@'%'",
    }]))

    grants = await service.get_user_grants("alice")
    detail = await service.get_user_detail("alice")

    assert grants == ["GRANT 'analyst', 'root' TO 'alice'@'%'"]
    assert [(item["name"], item["grant_type"]) for item in detail["grants"]] == [
        ("analyst", "ROLE")
    ]
