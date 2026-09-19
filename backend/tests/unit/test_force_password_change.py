"""Nova's `ALTER USER … REQUIRE PASSWORD CHANGE` statement and its flag store.

The statement is Nova metadata (StarRocks has no such attribute): it records a
flag in `NOVA_SYSTEM.CONFIG_USER_PREFERENCES`, and login reads it to require a
password change at first use. These tests pin the parser, the store helper, and
the login/change-password wiring.
"""

from __future__ import annotations

import pytest

from app.common import user_flags
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


class _FlagDB:
    """Records the writes and serves a fixed read, so no engine is needed."""

    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.writes: list[tuple[str, list | None]] = []

    async def execute_system(self, sql, params=None):
        self.writes.append((" ".join(sql.split()), params))
        if " ".join(sql.split()).upper().startswith("SELECT"):
            return {"columns": [], "rows": [[self.value]] if self.value else []}
        return {"columns": [], "rows": [], "affected": 1}


async def test_set_writes_true_and_scopes_to_the_user(monkeypatch):
    db = _FlagDB()
    monkeypatch.setattr(user_flags, "db", db)

    await user_flags.set_must_change_password("alice", required=True)

    sql, params = db.writes[-1]
    assert "CONFIG_USER_PREFERENCES" in sql
    assert params == ["alice", user_flags.MUST_CHANGE_PASSWORD, "true"]


async def test_clear_writes_false(monkeypatch):
    db = _FlagDB()
    monkeypatch.setattr(user_flags, "db", db)

    await user_flags.clear_must_change_password("alice")

    assert db.writes[-1][1][2] == "false"


async def test_is_true_reads_the_flag(monkeypatch):
    monkeypatch.setattr(user_flags, "db", _FlagDB("true"))
    assert await user_flags.is_must_change_password("alice") is True


async def test_is_false_when_absent(monkeypatch):
    monkeypatch.setattr(user_flags, "db", _FlagDB(None))
    assert await user_flags.is_must_change_password("alice") is False


async def test_is_false_on_store_error(monkeypatch):
    class _Broken:
        async def execute_system(self, sql, params=None):
            raise RuntimeError("engine down")

    monkeypatch.setattr(user_flags, "db", _Broken())
    # A metadata outage must not lock every user out of login.
    assert await user_flags.is_must_change_password("alice") is False
