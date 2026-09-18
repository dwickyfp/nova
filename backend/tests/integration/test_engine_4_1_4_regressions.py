"""L3 regressions for the StarRocks 4.1.4 engine bump (NOVA-51).

The engine bump pins the images to the ``4.1.4`` commit
``4a9848edf03f5c936dac664b2d52527f48e72eb0``. Two documented behaviour changes
in that jump can only be observed against a real engine, so they are asserted
here rather than in the unit suite:

1. **CTAS preserves an explicit ``VARCHAR(N)`` length** (StarRocks #73498). On
   4.1.1 a ``CREATE TABLE ... AS SELECT CAST(x AS VARCHAR(n))`` widened the
   column to ``VARCHAR(MAX)``; on 4.1.4 the declared length survives, so the
   column is created as ``varchar(n)``. A Nova CTAS path that relied on the
   widening now enforces the length on later writes.

2. **``isAdjustedToUTC=false`` INT64 Parquet timestamps are wall-clock**
   (StarRocks #73674). ``FILES()`` no longer applies a session timezone shift
   to such columns, so the loaded value equals the stored wall-clock value even
   when the session ``time_zone`` is not UTC.

StarRocks is optional: when unreachable the module skips rather than fails.
Point at an already-running engine via ``NOVA_ORCH_SR_PORT`` (default 29030,
the test compose port). The Parquet suite uploads its own committed fixture to
the stage object store, so no out-of-band seed step is required beyond the
engine reachability the fixture checks.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncmy
import pytest
import pytest_asyncio

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = int(_EXPLICIT_PORT or "29030")
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
PARQUET_FIXTURE = FIXTURE_DIR / "utc_flag_false_int64_ts.parquet"

# The fixture stores these wall-clock values (see the regeneration note in
# ``HOW_TO_RUN.md``). If the fixture is regenerated with different values these
# assertions must move with it.
FIXTURE_TS_ROWS = ("2024-01-02 03:04:05", "2024-01-02 04:05:06")

pytestmark = pytest.mark.engine


async def _sr_reachable() -> bool:
    try:
        conn = await asyncio.wait_for(
            asyncmy.connect(
                host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD
            ),
            timeout=5,
        )
    except Exception:
        return False
    conn.close()
    return True


async def _connect():
    return await asyncmy.connect(
        host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD
    )


@pytest_asyncio.fixture
async def engine(request):
    if "docker_services" in request.fixturenames:
        request.getfixturevalue("docker_services")
    if not await _sr_reachable():
        pytest.skip("StarRocks not reachable")
    yield


async def _execute(sql: str):
    conn = await _connect()
    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
            try:
                return await cur.fetchall()
            except Exception:
                return None
    finally:
        conn.close()


async def _fetch_all(sql: str):
    conn = await _connect()
    try:
        async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
            await cur.execute(sql)
            return await cur.fetchall()
    finally:
        conn.close()


def _upload_fixture(connection) -> None:
    """Put the committed Parquet fixture at the stage key the test reads.

    The fixture lives in the repo, not in the test object store, so the suite
    provisions it itself rather than depending on an out-of-band upload step
    that CI does not run. The key mirrors the stage layout
    (``<bucket>/NOVA_ANALYTICS/public/fixtures/<file>``) so the ``s3://`` path
    handed to StarRocks matches what ``build_s3_path`` would produce.
    """
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=connection.endpoint,
        aws_access_key_id=connection.access_key,
        aws_secret_access_key=connection.secret_key,
        region_name=connection.region or "us-east-1",
    )
    key = f"NOVA_ANALYTICS/public/fixtures/{PARQUET_FIXTURE.name}"
    with PARQUET_FIXTURE.open("rb") as handle:
        client.put_object(Bucket=connection.bucket, Key=key, Body=handle)


class TestCtasPreservesVarcharLength:
    """StarRocks #73498: an explicit ``VARCHAR(N)`` in CTAS survives 4.1.4."""

    async def test_ctas_keeps_declared_varchar_length(self, engine):
        table = "nova_ctas_varchar_len_probe"
        await _execute(f"DROP TABLE IF EXISTS NOVA_SYSTEM.{table}")
        try:
            # ``replication_num=1``: the test stack has a single BE and CTAS
            # defaults to replication 3, which fails before the assertion runs.
            await _execute(
                f"CREATE TABLE NOVA_SYSTEM.{table} "
                "PROPERTIES('replication_num'='1') AS "
                "SELECT CAST('abcd' AS VARCHAR(4)) AS label"
            )

            rows = await _fetch_all(
                "SELECT COLUMN_TYPE FROM information_schema.columns "
                "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' "
                f"AND TABLE_NAME = '{table}' AND COLUMN_NAME = 'label'"
            )
            assert rows, "the CTAS column is missing from information_schema.columns"

            # 4.1.1 widened this to varchar(MAX). On 4.1.4 the declared length is
            # preserved, so the assertion is the exact declared type.
            assert str(rows[0]["COLUMN_TYPE"]).lower() == "varchar(4)"

            # The behaviour Nova must absorb: a longer write is now rejected,
            # where the widened 4.1.1 column accepted it.
            with pytest.raises(asyncmy.errors.ProgrammingError):
                await _execute(
                    f"INSERT INTO NOVA_SYSTEM.{table} VALUES ('too long')"
                )
        finally:
            await _execute(f"DROP TABLE IF EXISTS NOVA_SYSTEM.{table}")


class TestParquetUtcFlagFalseIsWallClock:
    """StarRocks #73674: ``isAdjustedToUTC=false`` INT64 timestamps stay as-is."""

    async def test_wall_clock_round_trip_under_non_utc_session(self, engine):
        assert PARQUET_FIXTURE.is_file(), f"missing fixture {PARQUET_FIXTURE}"

        from app.core.config import get_storage_connection, to_docker_endpoint

        connection = get_storage_connection("production")
        _upload_fixture(connection)
        key = f"NOVA_ANALYTICS/public/fixtures/{PARQUET_FIXTURE.name}"
        # boto3 (on the host) uses the host-side endpoint; the FILES() call
        # (executed by StarRocks inside the compose network) needs the
        # docker-internal one, hence the rewrite.
        docker_endpoint = to_docker_endpoint(connection.endpoint)

        # Same parameter set the dialect translator emits for an @stage query
        # (app/modules/query/dialect/translator.py:61-84): an `s3://` path plus
        # `aws.s3.endpoint`, not a bare URL. MinIO rejects the latter with a 403
        # and StarRocks reports the file as size -1.
        files = (
            "FILES("
            f"'path'='s3://{connection.bucket}/{key}', "
            "'format'='parquet', "
            f"'aws.s3.access_key'='{connection.access_key}', "
            f"'aws.s3.secret_key'='{connection.secret_key}', "
            f"'aws.s3.endpoint'='{docker_endpoint}', "
            "'aws.s3.enable_ssl'='false', "
            "'aws.s3.enable_path_style_access'='true', "
            "'aws.s3.use_aws_sdk_default_behavior'='false', "
            "'aws.s3.use_instance_profile'='false')"
        )

        table = "nova_parquet_ts_probe"
        await _execute(f"DROP TABLE IF EXISTS NOVA_SYSTEM.{table}")
        try:
            # A non-UTC session is the point: before #73674 the loader shifted
            # these values into the session zone.
            await _execute("SET time_zone = 'Asia/Jakarta'")
            # ``replication_num=1`` for the single-BE test stack.
            await _execute(
                f"CREATE TABLE NOVA_SYSTEM.{table} "
                "PROPERTIES('replication_num'='1') AS "
                f"SELECT * FROM {files}"
            )

            rows = await _fetch_all(
                f"SELECT ts FROM NOVA_SYSTEM.{table} ORDER BY id"
            )
            assert len(rows) == len(FIXTURE_TS_ROWS)

            loaded = [str(row["ts"]) for row in rows]
            assert loaded == list(FIXTURE_TS_ROWS), (
                "isAdjustedToUTC=false INT64 timestamps must load as the stored "
                f"wall-clock values, not a session-zone shift: {loaded!r}"
            )
        finally:
            await _execute(f"DROP TABLE IF EXISTS NOVA_SYSTEM.{table}")
            await _execute("SET time_zone = 'UTC'")
