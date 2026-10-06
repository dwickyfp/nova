"""Audit rows that arrive together are written by one INSERT.

A single-row INSERT takes the engine about 100 ms and concurrent ones do not
overlap, which capped every deployment near 60 audited statements a second.
Grouping must not weaken the audit: every caller still waits for its own row,
and a failure never silently drops or duplicates one.
"""

from __future__ import annotations

import asyncio

import pytest
from asyncmy.errors import OperationalError, ProgrammingError

from app.common import audit
from app.common.audit import write_audit_log
from app.core.config import settings

ROW = dict(
    event_type="query",
    user_name="alice",
    action="execute",
    object_type="query",
    object_name="",
    status="SUCCESS",
)
WIDTH = 23  # parameters per row: every column except event_time, plus three execution columns


class Engine:
    """Records INSERTs; the first one blocks until released so others queue up."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, list]] = []
        self.release = asyncio.Event()
        self.release.set()
        self.fail: list[Exception] = []

    async def execute_system(self, sql, parameters=None):
        self.statements.append((sql, list(parameters or [])))
        await self.release.wait()
        if self.fail:
            failure = self.fail.pop(0)
            if failure is not None:
                raise failure
        return {"affected": 1}

    def rows(self, index: int) -> int:
        return self.statements[index][0].count("NOW()")

    def users(self, index: int) -> list[str]:
        parameters = self.statements[index][1]
        return [parameters[offset + 2] for offset in range(0, len(parameters), WIDTH)]


@pytest.fixture
def engine(monkeypatch):
    engine = Engine()
    monkeypatch.setattr(audit.db, "execute_system", engine.execute_system)
    monkeypatch.setattr(audit, "_audit_writer", audit._AuditWriter())
    return engine


async def _burst(engine, count: int) -> list:
    """One write in flight, then ``count`` more arriving behind it."""
    engine.release.clear()
    first = asyncio.create_task(write_audit_log(**{**ROW, "user_name": "first"}))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    rest = [
        asyncio.create_task(write_audit_log(**{**ROW, "user_name": f"user-{index}"}))
        for index in range(count)
    ]
    await asyncio.sleep(0)
    return [first, *rest]


async def test_a_lone_row_is_written_at_once_with_the_single_row_statement(engine):
    query_id = await write_audit_log(**ROW)

    ((sql, parameters),) = engine.statements
    assert engine.rows(0) == 1 and len(parameters) == WIDTH
    assert parameters[0] == query_id and parameters[2] == "alice"


async def test_rows_arriving_during_a_write_share_the_next_insert(engine):
    tasks = await _burst(engine, 5)
    assert len(engine.statements) == 1  # the others are waiting, not writing

    engine.release.set()
    ids = await asyncio.gather(*tasks)

    assert [engine.rows(0), engine.rows(1)] == [1, 5]
    assert engine.users(1) == [f"user-{index}" for index in range(5)]
    assert len(set(ids)) == 6


async def test_no_caller_returns_before_its_row_is_written(engine):
    tasks = await _burst(engine, 3)
    await asyncio.sleep(0.01)

    assert not any(task.done() for task in tasks)
    engine.release.set()
    await asyncio.gather(*tasks)


async def test_group_size_is_bounded(monkeypatch, engine):
    monkeypatch.setattr(settings, "AUDIT_GROUP_MAX_ROWS", 4)
    tasks = await _burst(engine, 10)

    engine.release.set()
    await asyncio.gather(*tasks)

    assert [engine.rows(index) for index in range(len(engine.statements))] == [1, 4, 4, 2]


async def test_long_statements_are_not_packed_past_the_packet_budget(monkeypatch, engine):
    monkeypatch.setattr(audit, "_GROUP_MAX_CHARS", 250)
    engine.release.clear()
    first = asyncio.create_task(write_audit_log(**ROW))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    rest = [
        asyncio.create_task(write_audit_log(**ROW, sql_text="SELECT " + "x" * 100))
        for _ in range(4)
    ]
    await asyncio.sleep(0)

    engine.release.set()
    await asyncio.gather(first, *rest)

    assert all(engine.rows(index) <= 2 for index in range(len(engine.statements)))
    assert sum(engine.rows(index) for index in range(len(engine.statements))) == 5


async def test_a_refused_group_is_rewritten_row_by_row_so_one_bad_row_fails_alone(engine):
    tasks = await _burst(engine, 3)
    # first row ok, the group is refused, then row 2 of 3 is refused on its own.
    engine.fail = [
        None,
        ProgrammingError(1064, "bad value"),
        None,
        ProgrammingError(1064, "bad value"),
        None,
    ]

    engine.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert [type(result).__name__ for result in results] == [
        "str",
        "str",
        "ProgrammingError",
        "str",
    ]
    assert [engine.rows(index) for index in range(5)] == [1, 3, 1, 1, 1]


async def test_an_uncertain_group_failure_is_not_retried(engine):
    tasks = await _burst(engine, 3)
    engine.fail = [None, OperationalError(2013, "Lost connection during query")]

    engine.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # The INSERT may have been applied; writing the rows again could duplicate them.
    assert [type(result).__name__ for result in results] == ["str"] + ["OperationalError"] * 3
    assert len(engine.statements) == 2


async def test_group_falls_back_to_the_legacy_columns_on_an_older_schema(engine):
    tasks = await _burst(engine, 2)
    unknown = ProgrammingError(1054, "Unknown column 'nova_execution_id' in 'field list'")
    engine.fail = [None, unknown, unknown, None, unknown, None]

    engine.release.set()
    await asyncio.gather(*tasks)

    legacy = [sql for sql, _ in engine.statements if "nova_execution_id" not in sql]
    assert len(legacy) == 2 and all(
        len(p) == WIDTH - 3 for s, p in engine.statements if s in legacy
    )


async def test_a_caller_that_goes_away_does_not_lose_anyone_elses_row(engine):
    tasks = await _burst(engine, 3)
    tasks[2].cancel()

    engine.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert isinstance(results[2], asyncio.CancelledError)
    # The cancelled caller's row is still recorded, with the others.
    assert engine.rows(1) == 3


async def test_grouping_can_be_turned_off(monkeypatch, engine):
    monkeypatch.setattr(settings, "AUDIT_GROUP_MAX_ROWS", 1)
    engine.release.clear()
    tasks = [asyncio.create_task(write_audit_log(**ROW)) for _ in range(4)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(engine.statements) == 4  # every row started its own write
    engine.release.set()
    await asyncio.gather(*tasks)
    assert all(engine.rows(index) == 1 for index in range(4))


async def test_grouped_rows_are_still_redacted(engine):
    tasks = await _burst(engine, 0)
    secret = asyncio.create_task(
        write_audit_log(
            **ROW, sql_text="CREATE CATALOG c PROPERTIES('aws.s3.secret_key'='hunter2')"
        )
    )
    other = asyncio.create_task(write_audit_log(**ROW))
    await asyncio.sleep(0)

    engine.release.set()
    await asyncio.gather(*tasks, secret, other)

    assert "hunter2" not in str(engine.statements)


async def test_the_write_does_not_inherit_one_callers_request_state(engine):
    from contextvars import ContextVar

    marker: ContextVar[str] = ContextVar("audit_test_marker", default="unset")
    seen: list[str] = []
    original = engine.execute_system

    async def recording(sql, parameters=None):
        seen.append(marker.get())
        return await original(sql, parameters)

    audit.db.execute_system = recording
    marker.set("request-of-alice")

    await write_audit_log(**ROW)

    assert seen == ["unset"]
