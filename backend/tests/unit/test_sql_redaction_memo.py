"""Redaction is remembered within a request and never beyond it."""

from __future__ import annotations

import pytest

from app.common import sql_guard
from app.common.sql_guard import (
    CredentialsRedactionError,
    redact_sql_credentials,
    redaction_scope,
)

PADDING = "-- " + "x" * 3000 + "\n"
SECRET_SQL = PADDING + "CREATE CATALOG c PROPERTIES('aws.s3.secret_key'='hunter2-secret')"


@pytest.fixture
def passes(monkeypatch):
    """Count full redaction passes."""
    calls: list[str] = []
    original = sql_guard._redact_account_credentials

    def counting(sql):
        calls.append(sql)
        return original(sql)

    monkeypatch.setattr(sql_guard, "_redact_account_credentials", counting)
    return calls


def test_same_statement_is_redacted_once_within_a_request(passes):
    with redaction_scope():
        first = redact_sql_credentials(SECRET_SQL)
        second = redact_sql_credentials(SECRET_SQL)

    assert len(passes) == 1 and first == second
    assert "hunter2-secret" not in first and "'***'" in first


def test_nothing_is_remembered_outside_a_request(passes):
    redact_sql_credentials(SECRET_SQL)
    redact_sql_credentials(SECRET_SQL)

    assert len(passes) == 2


def test_memory_ends_with_the_request(passes):
    with redaction_scope():
        redact_sql_credentials(SECRET_SQL)
    with redaction_scope():
        redact_sql_credentials(SECRET_SQL)

    assert len(passes) == 2 and sql_guard._REQUEST_REDACTIONS.get() is None


def test_nested_scopes_share_the_outer_request(passes):
    with redaction_scope():
        redact_sql_credentials(SECRET_SQL)
        with redaction_scope():
            redact_sql_credentials(SECRET_SQL)
        redact_sql_credentials(SECRET_SQL)

    assert len(passes) == 1


def test_short_statements_are_not_remembered(passes):
    short = "CREATE CATALOG c PROPERTIES('aws.s3.secret_key'='hunter2-secret')"

    with redaction_scope():
        redact_sql_credentials(short)
        redact_sql_credentials(short)

    assert len(passes) == 2


def test_a_statement_that_cannot_be_redacted_fails_every_time():
    malformed = PADDING + '{"api_key":unquoted-secret}'

    with redaction_scope():
        for _ in range(2):
            with pytest.raises(CredentialsRedactionError):
                redact_sql_credentials(malformed)


def test_different_statements_do_not_share_a_result():
    other = SECRET_SQL.replace("hunter2-secret", "another-secret")

    with redaction_scope():
        first, second = redact_sql_credentials(SECRET_SQL), redact_sql_credentials(other)

    assert "hunter2-secret" not in first and "another-secret" not in second


def test_the_sql_request_scope_opens_a_redaction_scope(passes):
    from app.sql_frontend.parser import parsing_scope

    with parsing_scope():
        redact_sql_credentials(SECRET_SQL)
        redact_sql_credentials(SECRET_SQL)

    assert len(passes) == 1
