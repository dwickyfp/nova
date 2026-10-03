"""Metadata budgets use complete result-set reads or disclose unavailable coverage."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from app.core.database import StarRocksConnectionFactory
from app.modules.agents.quality_scoring import Assertion, score_assertion
from app.modules.assistant.measurements import (
    AttemptMeasurements,
    measurement_scope,
    metadata_execution_scope,
)


class Cursor:
    description = None
    rowcount = 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_error):
        pass

    async def execute(self, sql, _params=None):
        await asyncio.sleep(0)
        if sql == "failed":
            raise RuntimeError("Metadata read failed")
        self.description = [("value",)] if sql == "read" else None

    async def fetchall(self):
        return [{"value": 7}]


class Connection:
    def cursor(self, _kind=None):
        return Cursor()


class Pool:
    @asynccontextmanager
    async def acquire(self):
        yield Connection()


def factory():
    value = StarRocksConnectionFactory()
    value._system_pool = Pool()
    return value


def observation(measured):
    counts, measurement = measured.observation(None)
    return {"counts": counts, "measurement": measurement}


def budget(measured, limit):
    return score_assertion(
        Assertion(scorer="efficiency", expected={"metadata_reads": limit}),
        observation(measured),
    ).status


async def test_successful_result_reads_count_without_mutation_or_argument_content():
    db = factory()
    measured = AttemptMeasurements()
    with measurement_scope(measured):
        assert (await db.execute_system("read", ["private-test-value"]))["rows"] == [[7]]
        assert (await db.execute_system("mutation"))["affected"] == 1
        await db.execute_system("read")
    assert observation(measured)["counts"]["metadata_reads"] == 2
    assert budget(measured, 1) == "fail"
    assert budget(measured, 2) == "pass"
    assert "private-test-value" not in json.dumps(observation(measured))


async def test_failed_query_cannot_pass_a_metadata_read_budget():
    measured = AttemptMeasurements()
    with measurement_scope(measured), pytest.raises(RuntimeError):
        await factory().execute_system("failed")
    assert budget(measured, 100) == "unavailable"
    assert "metadata_reads" not in observation(measured)["counts"]


async def test_direct_cursor_borrow_discloses_partial_read_coverage():
    db = factory()
    measured = AttemptMeasurements()
    with measurement_scope(measured):
        await db.execute_system("read")
        async with db.system_conn() as connection, connection.cursor() as cursor:
            await cursor.execute("read")
            assert await cursor.fetchall() == [{"value": 7}]
    assert measured.metadata_reads == 1
    assert budget(measured, 10) == "unavailable"
    assert observation(measured)["measurement"]["unavailable"]["metadata_reads"] == [
        "direct_metadata_borrow_unmeasured"
    ]


async def test_child_task_cannot_inherit_its_parents_metadata_coverage_marker():
    measured = AttemptMeasurements()

    async def direct_borrow():
        async with factory().system_conn() as connection, connection.cursor() as cursor:
            await cursor.execute("read")

    with measurement_scope(measured), metadata_execution_scope():
        await asyncio.create_task(direct_borrow())
    assert budget(measured, 10) == "unavailable"


async def test_concurrent_attempts_keep_independent_metadata_budgets():
    async def attempt(reads):
        measured = AttemptMeasurements()
        with measurement_scope(measured):
            for _ in range(reads):
                await factory().execute_system("read")
        return measured

    first, second = await asyncio.gather(attempt(2), attempt(3))
    assert (first.metadata_reads, second.metadata_reads) == (2, 3)
    assert budget(first, 2) == "pass"
    assert budget(second, 2) == "fail"


async def test_unobserved_owner_cannot_turn_missing_reads_into_a_zero_budget(monkeypatch):
    from app.core.database import db

    monkeypatch.setattr(db, "execute_system", lambda *_args: None)
    assert budget(AttemptMeasurements(), 0) == "unavailable"
