"""Nova's `ALTER USER … REQUIRE PASSWORD CHANGE` statement and its flag store.

The statement is Nova metadata (StarRocks has no such attribute): it records a
flag in `nova_meta.config_user_preferences`, and login reads it to require a
password change at first use. These tests pin the parser, the store helper, and
the login/change-password wiring.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.common import user_flags
from app.core.metadata_db import MetadataUnavailable
from app.modules.access_control.security_context import SecurityContext
from app.modules.query.dialect.force_password_change import (
    is_force_password_change,
    parse_force_password_change,
)

# ── Parser ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER USER 'dwicky.f.putra' REQUIRE PASSWORD CHANGE;",
        "ALTER USER dwicky REQUIRE PASSWORD CHANGE",
        "alter user `analyst` require password change",
        "ALTER USER 'x'@'%' REQUIRE PASSWORD CHANGE;",
    ],
)
def test_recognises_the_require_password_change_statement(sql):
    assert is_force_password_change(sql)
    parsed = parse_force_password_change(sql)
    assert parsed.required is True


def test_parses_a_quoted_identity_with_a_dot():
    parsed = parse_force_password_change(
        "ALTER USER 'dwicky.f.putra' REQUIRE PASSWORD CHANGE;"
    )
    assert parsed.username == "dwicky.f.putra"
    assert parsed.required is True


def test_strips_the_host_from_a_quoted_pair():
    parsed = parse_force_password_change("ALTER USER 'alice'@'%' REQUIRE PASSWORD CHANGE")
    assert parsed.username == "alice"


def test_off_and_none_clear_the_requirement():
    assert parse_force_password_change(
        "ALTER USER 'alice' REQUIRE PASSWORD CHANGE OFF"
    ).required is False
    assert parse_force_password_change(
        "ALTER USER 'alice' REQUIRE PASSWORD CHANGE NONE"
    ).required is False


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "ALTER USER 'alice' IDENTIFIED BY 'x'",
        "ALTER USER REQUIRE PASSWORD CHANGE",
    ],
)
def test_rejects_non_matching_statements(sql):
    assert not is_force_password_change(sql)


# ── Flag store ──────────────────────────────────────────────────────────────


ADMIN = SecurityContext("admin", "ACCOUNTADMIN", session_id="admin-login")
ALICE = SecurityContext("alice", "PUBLIC", session_id="alice-login")


async def test_set_writes_true_as_the_administrator_and_scopes_to_the_user(monkeypatch):
    write = AsyncMock()
    monkeypatch.setattr(user_flags, "write_values", write)
    await user_flags.set_must_change_password("alice", required=True, actor=ADMIN)
    write.assert_awaited_once_with(ADMIN, "alice", {user_flags.MUST_CHANGE_PASSWORD: "true"})


async def test_clear_writes_false_as_the_affected_user(monkeypatch):
    write = AsyncMock()
    monkeypatch.setattr(user_flags, "read_value", AsyncMock(return_value="true"))
    monkeypatch.setattr(user_flags, "write_values", write)
    await user_flags.clear_must_change_password(ALICE)
    write.assert_awaited_once_with(ALICE, "alice", {user_flags.MUST_CHANGE_PASSWORD: "false"})


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False), (None, False)])
async def test_read_is_bound_to_the_affected_user(monkeypatch, value, expected):
    read = AsyncMock(return_value=value)
    monkeypatch.setattr(user_flags, "read_value", read)
    assert await user_flags.is_must_change_password(ALICE) is expected
    read.assert_awaited_once_with(ALICE, "alice", user_flags.MUST_CHANGE_PASSWORD)


@pytest.mark.parametrize("value", [None, "false"])
async def test_clear_does_not_create_an_absent_or_already_cleared_flag(monkeypatch, value):
    write = AsyncMock()
    monkeypatch.setattr(user_flags, "read_value", AsyncMock(return_value=value))
    monkeypatch.setattr(user_flags, "write_values", write)
    await user_flags.clear_must_change_password(ALICE)
    write.assert_not_awaited()


async def test_store_outage_cannot_bypass_password_change(monkeypatch):
    monkeypatch.setattr(user_flags, "read_value", AsyncMock(side_effect=MetadataUnavailable()))
    with pytest.raises(MetadataUnavailable):
        await user_flags.is_must_change_password(ALICE)
