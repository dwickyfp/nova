"""Conversation timestamps must carry UTC on the wire."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.modules.agents.studio_schemas import SessionView, ThreadTraceResponse, TurnView
from app.modules.assistant.schemas import MessageView, ThreadView

NAIVE_UTC = datetime(2026, 9, 23, 1, 0, 0)


@pytest.mark.parametrize(
    "view, fields",
    [
        (
            ThreadView(
                thread_id="nove",
                title="New chat",
                created_at=NAIVE_UTC,
                updated_at=NAIVE_UTC,
            ),
            ("created_at", "updated_at"),
        ),
        (
            MessageView(message_id="m1", role="user", created_at=NAIVE_UTC),
            ("created_at",),
        ),
        (
            SessionView(
                thread_id="studio",
                title="Studio chat",
                user_name="alice",
                created_at=NAIVE_UTC,
                updated_at=NAIVE_UTC,
            ),
            ("created_at", "updated_at"),
        ),
        (
            TurnView(message_id="m2", seq=1, role="assistant", created_at=NAIVE_UTC),
            ("created_at",),
        ),
        (
            ThreadTraceResponse(
                thread_id="studio",
                title="Studio chat",
                user_name="alice",
                created_at=NAIVE_UTC,
                updated_at=NAIVE_UTC,
            ),
            ("created_at", "updated_at"),
        ),
    ],
)
def test_naive_database_times_are_serialized_as_utc(view, fields):
    payload = json.loads(view.model_dump_json())
    for field in fields:
        assert datetime.fromisoformat(payload[field]) == NAIVE_UTC.replace(tzinfo=UTC)
        assert payload[field].endswith("Z")


def test_aware_time_is_converted_to_utc_without_changing_the_instant():
    jakarta = timezone(timedelta(hours=7))
    view = ThreadView(
        thread_id="nove",
        title="New chat",
        created_at=NAIVE_UTC.replace(tzinfo=UTC).astimezone(jakarta),
        updated_at=NAIVE_UTC.replace(tzinfo=UTC).astimezone(jakarta),
    )
    payload = json.loads(view.model_dump_json())
    assert payload["updated_at"] == "2026-09-23T01:00:00Z"
