"""CREATE TASK schedule text must produce the intended scheduler occurrence."""

from datetime import UTC, datetime

import pytest

from app.modules.task_orchestration.ddl import parse_create_task
from app.modules.task_orchestration.scheduler import plan_tick


@pytest.mark.parametrize(
    ("clause", "anchor", "now", "expected"),
    [
        (
            "SCHEDULE EVERY(INTERVAL 30 SECOND)",
            datetime(2026, 9, 23, 10, 0, 0),
            datetime(2026, 9, 23, 10, 0, 30, tzinfo=UTC),
            [datetime(2026, 9, 23, 10, 0, 30, tzinfo=UTC)],
        ),
        (
            "SCHEDULE EVERY(INTERVAL 1 MINUTE)",
            datetime(2026, 9, 23, 10, 0, 0),
            datetime(2026, 9, 23, 10, 1, 0, tzinfo=UTC),
            [datetime(2026, 9, 23, 10, 1, 0, tzinfo=UTC)],
        ),
        (
            "SCHEDULE EVERY(INTERVAL 1 HOUR)",
            datetime(2026, 9, 23, 10, 0, 0),
            datetime(2026, 9, 23, 11, 0, 0, tzinfo=UTC),
            [datetime(2026, 9, 23, 11, 0, 0, tzinfo=UTC)],
        ),
        (
            "SCHEDULE EVERY(INTERVAL 1 DAY)",
            datetime(2026, 9, 23, 10, 0, 0),
            datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC),
            [datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC)],
        ),
        (
            "SCHEDULE = '* * * * * UTC'",
            datetime(2026, 9, 23, 10, 0, 0),
            datetime(2026, 9, 23, 10, 1, 0, tzinfo=UTC),
            [datetime(2026, 9, 23, 10, 1, 0, tzinfo=UTC)],
        ),
        (
            "SCHEDULE = 'USING CRON */5 * * * * UTC'",
            datetime(2026, 9, 23, 10, 0, 0),
            datetime(2026, 9, 23, 10, 5, 0, tzinfo=UTC),
            [datetime(2026, 9, 23, 10, 5, 0, tzinfo=UTC)],
        ),
        (
            "SCHEDULE = '0 11 * * * UTC'",
            datetime(2026, 9, 23, 10, 0, 0),
            datetime(2026, 9, 23, 11, 0, 0, tzinfo=UTC),
            [datetime(2026, 9, 23, 11, 0, 0, tzinfo=UTC)],
        ),
        (
            "SCHEDULE = '0 11 * * WED UTC'",
            datetime(2026, 9, 23, 10, 0, 0),
            datetime(2026, 9, 23, 11, 0, 0, tzinfo=UTC),
            [datetime(2026, 9, 23, 11, 0, 0, tzinfo=UTC)],
        ),
    ],
)
def test_create_task_schedule_reaches_expected_fire(clause, anchor, now, expected):
    parsed = parse_create_task(
        f"CREATE TASK warehouse.default.scheduled {clause} AS INSERT INTO sink SELECT 1",
        database="warehouse",
        schema="default",
        timezone="UTC",
    )
    row = {
        "id": "task-1",
        "name": parsed.name,
        "database_name": parsed.database_name,
        "schema_name": parsed.schema_name,
        "schedule_kind": parsed.schedule_kind,
        "schedule_expr": parsed.schedule_expr,
        "timezone": parsed.timezone,
        "created_at": anchor,
    }
    plan = plan_tick([row], [], now, engine_timezone="UTC")
    assert [due.due_at for due in plan.due] == expected
