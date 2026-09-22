"""Tests for strict single-role assignment parsing."""

import pytest

from app.modules.access_control.role_activation import (
    RoleActivationError,
    RoleActivationService,
    parse_assigned_roles,
)


def test_parse_all_roles_from_one_show_grants_assignment() -> None:
    rows = [
        (
            "'alice'@'%'",
            None,
            "GRANT 'marketing', 'finance', 'regional_manager' TO 'alice'@'%'",
        )
    ]
    assert parse_assigned_roles(rows) == ["marketing", "finance", "regional_manager"]


def test_ignore_object_privileges_and_role_privilege_targets() -> None:
    rows = [
        ("'alice'@'%'", None, "GRANT SELECT ON analytics.sales TO 'alice'@'%'"),
        (None, None, "GRANT ALL ON *.* TO ROLE ACCOUNTADMIN WITH GRANT OPTION"),
    ]
    assert parse_assigned_roles(rows) == []


def test_unescape_quoted_role_name() -> None:
    rows = [("'alice'@'%'", None, "GRANT 'sales''ops' TO 'alice'@'%'")]
    assert parse_assigned_roles(rows) == ["sales'ops"]


class _Cursor:
    def __init__(self) -> None:
        self.statement = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def execute(self, statement: str) -> None:
        self.statement = statement

    async def fetchall(self) -> list:
        return []

    async def fetchone(self) -> tuple[str]:
        return ("NONE",)


class _Connection:
    def cursor(self) -> _Cursor:
        return _Cursor()


@pytest.mark.asyncio
async def test_none_current_role_is_not_treated_as_a_default() -> None:
    service = RoleActivationService()
    assignments = await service.assignments(_Connection())
    assert assignments.assigned_roles == ()
    assert assignments.default_role is None

    with pytest.raises(RoleActivationError, match="No explicit default role"):
        await service.activate(
            _Connection(), principal="legacy_user", requested_role=None
        )
