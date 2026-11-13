import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.common.metadata_audit import AuditEvent, AuditIdentity


def event(**changes):
    return replace(AuditEvent("query", "SELECT", "TABLE", "example", "SUCCESS"), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"event_type": "metadata"},
        {"event_type": ""},
        {"action": None},
        {"object_name": "x" * 513},
        {"status": "\x00"},
        {"query_id": "\ud800"},
        {"duration_ms": True},
        {"rows_affected": -1},
        {"duration_ms": 2**63},
        {"engine_query_ids": ["id"]},
        {"engine_query_ids": ("x" * 129,)},
        {"engine_query_ids": ("id",) * 257},
    ],
)
def test_invalid_audit_event_does_not_echo_rejected_values(changes):
    with pytest.raises(ValueError) as caught:
        event(**changes)
    assert (
        "Invalid" in str(caught.value)
        or "Missing" in str(caught.value)
        or "generated" in str(caught.value)
    )


@pytest.mark.parametrize(
    "identity,timestamp",
    [
        (UUID(int=0), datetime.now(UTC)),
        ("not-a-uuid", datetime.now(UTC)),
        (uuid4(), datetime(2025, 1, 1)),
        (uuid4(), "2025-01-01"),
    ],
)
def test_replay_identity_requires_uuid_and_explicit_timezone(identity, timestamp):
    with pytest.raises(ValueError, match="identity"):
        AuditIdentity(identity, timestamp)


def test_statement_redaction_and_byte_budget_are_preserved():
    secret = "synthetic-audit-secret"
    statement = f"ALTER USER example IDENTIFIED BY '{secret}'"
    record = event(sql_text=statement, rewritten_sql=statement, error_message=statement)
    assert all(
        secret not in getattr(record, name)
        for name in (
            "sql_text",
            "rewritten_sql",
            "error_message",
        )
    )
    assert secret not in repr(record)
    long = event(sql_text="界" * 30000)
    assert len(long.sql_text.encode()) <= 60000 and "truncated" in long.sql_text


def test_audit_identity_normalizes_an_explicit_offset():
    offset = timezone(timedelta(hours=7))
    record = AuditIdentity(uuid4(), datetime(2025, 1, 2, 7, 0, tzinfo=offset))
    assert record.event_time == datetime(2025, 1, 2, tzinfo=UTC)
    assert record.event_time.tzinfo is UTC


def test_audit_identity_rejects_utc_conversion_outside_datetime_range():
    with pytest.raises(ValueError, match="identity"):
        AuditIdentity(uuid4(), datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1))))


def test_metadata_audit_import_does_not_load_legacy_database_or_configuration():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import app.common.metadata_audit; "
         "assert not {'app.common.audit', 'app.core.database', 'app.core.config', "
         "'asyncmy'}.intersection(sys.modules)"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
