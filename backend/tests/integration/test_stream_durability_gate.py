"""Release gate for the engine primitives required by Nova Streams.

These tests own a disposable database on the isolated L3 engine. They do not
enable Streams or change FE retention settings. An unavailable engine is a
skip, never evidence that the durability gate passed.
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import uuid4

import asyncmy
import pytest
import pytest_asyncio

from app.modules.streams.verification import TargetCommitVerifier, TargetIdentity, TargetOutcome
from tests.integration._stack import engine_port, require_shared_stack

pytestmark = pytest.mark.engine


async def connect():
    return await asyncmy.connect(
        host=os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1"),
        port=engine_port(os.getenv("NOVA_ORCH_SR_PORT"), "NOVA_TEST_FE_MYSQL_PORT", 29030),
        user=os.getenv("NOVA_ORCH_SR_USER", "root"),
        password=os.getenv("NOVA_ORCH_SR_PASSWORD", ""),
        autocommit=True,
        connect_timeout=10,
    )


async def execute(sql, params=None):
    conn = await connect()
    try:
        async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
            await cursor.execute(sql, params)
            return tuple(await cursor.fetchall())
    finally:
        conn.close()


@pytest_asyncio.fixture
async def source(request):
    if not os.getenv("NOVA_ORCH_SR_PORT"):
        request.getfixturevalue("docker_services")
        require_shared_stack(request)
    try:
        conn = await connect()
    except (OSError, asyncmy.errors.OperationalError):
        pytest.skip("Streams durability gate requires the isolated StarRocks test engine")
    conn.close()
    version = await execute("SELECT CURRENT_VERSION() AS version")
    assert str(version[0]["version"]).startswith("4.1.4"), version
    database = "nova_stream_gate_" + uuid4().hex[:12]
    await execute(f"CREATE DATABASE `{database}`")
    try:
        await execute(
            f"CREATE TABLE `{database}`.target (id BIGINT, payload VARCHAR(8)) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            "PROPERTIES ('replication_num'='1')"
        )
        await execute(
            f"CREATE TABLE `{database}`.claims (generation BIGINT, winner VARCHAR(32)) "
            "PRIMARY KEY(generation) DISTRIBUTED BY HASH(generation) BUCKETS 1 "
            "PROPERTIES ('replication_num'='1')"
        )
        yield database
    finally:
        await execute(f"DROP DATABASE `{database}`")


async def load_receipt(database, label):
    rows = await execute(
        "SELECT ID, LABEL, DB_NAME, TABLE_NAME, STATE, TYPE, SINK_ROWS "
        "FROM information_schema.loads WHERE DB_NAME = %s AND LABEL = %s",
        (database, label),
    )
    assert len(rows) == 1, f"Expected one durable receipt for {label}, got {rows}"
    return rows[0]


async def test_committed_label_recovers_without_target_replay(source):
    label = "nova_consume_" + uuid4().hex
    await execute(f"INSERT INTO `{source}`.target WITH LABEL {label} VALUES (1, 'first')")
    # Recovery opens a fresh connection and receives no submission result.
    receipt = await load_receipt(source, label)
    assert receipt["STATE"] == "FINISHED", receipt
    assert receipt["TABLE_NAME"] == "target", receipt
    async def metadata(sql, params):
        rows = await execute(sql, params)
        return {"columns": list(rows[0]), "rows": [list(row.values()) for row in rows]}

    verified = await TargetCommitVerifier(metadata).verify(
        TargetIdentity(source, "target", label, os.getenv("NOVA_ORCH_SR_USER", "root"))
    )
    assert verified.outcome == TargetOutcome.VISIBLE
    with pytest.raises(asyncmy.errors.Error):
        await execute(f"INSERT INTO `{source}`.target WITH LABEL {label} VALUES (2, 'replay')")
    assert await execute(f"SELECT id, payload FROM `{source}`.target") == (
        {"id": 1, "payload": "first"},
    )


async def test_successful_filtered_insert_has_recoverable_receipt(source):
    label = "nova_empty_" + uuid4().hex
    await execute(
        f"INSERT INTO `{source}`.target WITH LABEL {label} SELECT 1, 'empty' WHERE FALSE"
    )
    receipt = await load_receipt(source, label)
    assert receipt["STATE"] == "FINISHED", receipt
    assert receipt["TABLE_NAME"] == "target", receipt
    assert await execute(f"SELECT COUNT(*) AS n FROM `{source}`.target") == ({"n": 0},)


async def test_label_arbitrates_different_claimants(source):
    label = "nova_claim_" + uuid4().hex
    ready = asyncio.Event()

    async def contender(winner):
        conn = await connect()
        try:
            await ready.wait()
            async with conn.cursor() as cursor:
                try:
                    await cursor.execute(
                        f"INSERT INTO `{source}`.claims WITH LABEL {label} VALUES (1, %s)",
                        (winner,),
                    )
                    return winner
                except asyncmy.errors.Error:
                    return None
        finally:
            conn.close()

    attempts = [asyncio.create_task(contender(f"worker_{index}")) for index in range(8)]
    ready.set()
    results = await asyncio.gather(*attempts)
    winners = [result for result in results if result is not None]
    assert len(winners) == 1, results
    assert await execute(f"SELECT generation, winner FROM `{source}`.claims") == (
        {"generation": 1, "winner": winners[0]},
    )
    assert (await load_receipt(source, label))["STATE"] == "FINISHED"


async def test_claim_arbitration_across_processes(source):
    label = "nova_process_claim_" + uuid4().hex
    script = """
import asyncio, sys
from tests.integration.test_stream_durability_gate import connect
async def main():
    connection = await connect()
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO `{sys.argv[1]}`.claims WITH LABEL {sys.argv[2]} "
                "SELECT 2, %s WHERE NOT EXISTS "
                f"(SELECT 1 FROM `{sys.argv[1]}`.claims WHERE generation=2)",
                (sys.argv[3],),
            )
            print(cursor.rowcount)
    except Exception:
        print('blocked')
    finally:
        connection.close()
asyncio.run(main())
"""
    processes = await asyncio.gather(*[
        asyncio.create_subprocess_exec(
            sys.executable, "-c", script, source, label, f"process_{index}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        for index in range(4)
    ])
    outputs = await asyncio.gather(*[process.communicate() for process in processes])
    assert all(process.returncode == 0 for process in processes), outputs
    assert sum(stdout.strip() == b"1" for stdout, _ in outputs) == 1, outputs
    rows = await execute(f"SELECT generation, winner FROM `{source}`.claims")
    assert len(rows) == 1 and rows[0]["generation"] == 2, rows


async def test_claim_repository_preserves_existing_winner(source):
    from app.modules.streams.claims import CLAIMS_DDL, Claim, ClaimOutcome, ClaimRepository
    from app.modules.streams.schemas import operation_id

    await execute("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    await execute(CLAIMS_DDL)

    async def metadata(sql, params):
        connection = await connect()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                if cursor.description:
                    return {"rows": await cursor.fetchall()}
                return {"affected": cursor.rowcount}
        finally:
            connection.close()

    repository = ClaimRepository(metadata)
    resource = operation_id(source)
    first = Claim(resource, 1, 0, operation_id("first"), operation_id("insert A"))
    other = Claim(resource, 1, 0, operation_id("other"), operation_id("insert B"))
    try:
        assert (await repository.acquire(first)).outcome == ClaimOutcome.ACQUIRED
        for contender in (first, other):
            result = await repository.acquire(contender)
            assert result.outcome == ClaimOutcome.EXISTING
            assert result.winner == first
    finally:
        await execute(
            "DELETE FROM NOVA_SYSTEM.AUDIT_STREAM_CLAIMS WHERE claim_key = %s", (first.key,)
        )


async def test_durable_offset_receipt_survives_repeated_and_late_recovery(source):
    from app.modules.streams.consumption import Consumption, ConsumptionState
    from app.modules.streams.journal import CONSUMPTIONS_DDL, StarRocksConsumptionJournal
    from app.modules.streams.schemas import ChangeCursor, ChangeSnapshot, operation_id
    from app.modules.streams.verification import CommitReceipt

    await execute("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    await execute(CONSUMPTIONS_DDL)

    async def metadata(sql, params):
        connection = await connect()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                if cursor.description:
                    return {"rows": await cursor.fetchall()}
                return {"affected": cursor.rowcount}
        finally:
            connection.close()

    journal = StarRocksConsumptionJournal(metadata)
    stream_id = operation_id(source)
    first = Consumption(
        operation_id(source, 1), stream_id, 1,
        ChangeSnapshot(source, ChangeCursor(1, 0), ChangeCursor(1, 1), 1),
        TargetIdentity(source, "target", "nova_receipt_first", "root"), operation_id("first"),
    )
    second = Consumption(
        operation_id(source, 2), stream_id, 2,
        ChangeSnapshot(source, ChangeCursor(1, 1), ChangeCursor(1, 2), 1),
        TargetIdentity(source, "target", "nova_receipt_second", "root"), operation_id("second"),
    )
    # These are synthetic receipts exercising real journal persistence, not
    # evidence that target SQL ran. Target verification has separate tests.
    receipt = CommitReceipt(TargetOutcome.VISIBLE, "fixture", 123, 0)
    try:
        await journal.record(first, ConsumptionState.PREPARED)
        await journal.record(first, ConsumptionState.TARGET_COMMITTED, receipt)
        assert await journal.advance(first, receipt) == ChangeCursor(1, 1)
        assert await journal.advance(second, receipt) == ChangeCursor(1, 2)
        assert await journal.advance(first, receipt) == ChangeCursor(1, 2)
        assert await journal.committed_cursor(stream_id, ChangeCursor(2, 0)) == ChangeCursor(2, 0)
    finally:
        await execute(
            "DELETE FROM NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTIONS WHERE stream_id=%s", (stream_id,)
        )


async def test_strict_failure_does_not_write_target(source):
    label = "nova_failure_" + uuid4().hex
    conn = await connect()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SET enable_insert_strict = true")
            with pytest.raises(asyncmy.errors.Error):
                await cursor.execute(
                    f"INSERT INTO `{source}`.target WITH LABEL {label} "
                    "SELECT 1, repeat('x', 100)"
                )
    finally:
        conn.close()
    assert await execute(f"SELECT COUNT(*) AS n FROM `{source}`.target") == ({"n": 0},)
    rows = await execute(
        "SELECT STATE FROM information_schema.loads WHERE DB_NAME = %s AND LABEL = %s",
        (source, label),
    )
    # An error before job registration has no receipt. Absence cannot prove
    # failure to a recovering worker that did not receive the original error.
    assert not rows or all(row["STATE"] == "CANCELLED" for row in rows), rows


async def test_expired_label_cannot_overwrite_durable_claim(source):
    if os.getenv("NOVA_STREAM_GATE_ALLOW_RETENTION_CHANGE") != "1":
        pytest.skip("Requires disposable FE with label_clean_interval_second=1")
    config = await execute("ADMIN SHOW FRONTEND CONFIG LIKE 'label_keep_max_second'")
    original = str(config[0]["Value"])
    label = "nova_expiry_" + uuid4().hex
    try:
        await execute('ADMIN SET FRONTEND CONFIG ("label_keep_max_second" = "1")')
        await execute(
            f"INSERT INTO `{source}`.claims WITH LABEL {label} "
            "SELECT 1, 'first' WHERE NOT EXISTS "
            f"(SELECT 1 FROM `{source}`.claims WHERE generation = 1)"
        )
        # Wait for actual receipt eviction, not an assumed sleep duration.
        for _ in range(30):
            rows = await execute(
                "SHOW LOAD FROM `" + source + "` WHERE LABEL = %s", (label,)
            )
            if not rows:
                break
            await asyncio.sleep(1)
        else:
            pytest.fail("Disposable FE did not expire the label within 30 seconds")
        # The late worker must not overwrite a winner after engine dedup expires.
        await execute(
            f"INSERT INTO `{source}`.claims WITH LABEL {label} "
            "SELECT 1, 'late_worker' WHERE NOT EXISTS "
            f"(SELECT 1 FROM `{source}`.claims WHERE generation = 1)"
        )
        assert await execute(f"SELECT generation, winner FROM `{source}`.claims") == (
            {"generation": 1, "winner": "first"},
        )
    finally:
        await execute(f'ADMIN SET FRONTEND CONFIG ("label_keep_max_second" = "{original}")')
