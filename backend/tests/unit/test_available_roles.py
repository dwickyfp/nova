"""SHOW AVAILABLE ROLES lists the roles this session can switch to.

It used to list every Ranger role, so ``USE ROLE marketing`` after seeing
``marketing`` in the list was refused. The list now follows the role switch:
the session's assigned roles (or a held connection's grants), limited to roles
Ranger knows.
"""

from unittest.mock import AsyncMock

import pytest

from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.service import AccessControlService, access_control_service
from app.modules.access_control.statement_router import SecurityStatementRouter
from app.sql_frontend.security import SecurityOperation, execute_security_operation

RANGER_ROLES = [{"name": name} for name in ("ACCOUNTADMIN", "finance", "marketing", "analyst")]


class Ranger:
    async def list_roles(self):
        return RANGER_ROLES


def _security(session_id="sid", role="analyst"):
    return SecurityContext(principal="alice", active_role=role, session_id=session_id)


@pytest.fixture
def sessions(monkeypatch):
    store = {"sid": {"assigned_roles": ["analyst", "finance", "retired_role"]}}

    async def get(session_id):
        return store.get(session_id)

    monkeypatch.setattr("app.core.redis.session_store.get", get)
    return store


async def test_session_roles_that_ranger_knows(sessions):
    roles = await AccessControlService(Ranger()).available_roles(_security())
    # marketing exists in Ranger but is not assigned; retired_role is assigned
    # but missing from Ranger, so activation would refuse both.
    assert roles == ["analyst", "finance"]


async def test_held_connection_uses_its_grants(sessions, monkeypatch):
    from app.modules.access_control.role_activation import (
        RoleAssignments,
        role_activation_service,
    )

    assignments = AsyncMock(return_value=RoleAssignments(("analyst", "marketing"), "analyst"))
    monkeypatch.setattr(role_activation_service, "assignments", assignments)
    connection = object()
    roles = await AccessControlService(Ranger()).available_roles(
        _security(session_id=None), connection=connection,
    )
    assert roles == ["analyst", "marketing"]
    assignments.assert_awaited_once_with(connection)


async def test_without_a_session_only_the_active_role(sessions):
    roles = await AccessControlService(Ranger()).available_roles(_security(session_id="gone"))
    assert roles == ["analyst"]


async def test_show_available_roles_executes_the_session_list(sessions, monkeypatch):
    monkeypatch.setattr(access_control_service, "_ranger", Ranger())
    result = await execute_security_operation(
        SecurityOperation("available_roles"), _security(), "SHOW AVAILABLE ROLES"
    )
    assert result.columns == ["Role", "Authorization"]
    assert result.rows == [["analyst", "Ranger"], ["finance", "Ranger"]]
    every = await execute_security_operation(
        SecurityOperation("list_roles"), _security(), "SHOW ROLES"
    )
    assert [row[0] for row in every.rows] == [role["name"] for role in RANGER_ROLES]


async def test_statement_router_matches(sessions):
    router = SecurityStatementRouter(AccessControlService(Ranger()))
    routed = await router.route("SHOW AVAILABLE ROLES", security=_security())
    assert routed.result.rows == [["analyst", "Ranger"], ["finance", "Ranger"]]
