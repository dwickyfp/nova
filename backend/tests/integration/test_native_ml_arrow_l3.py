"""L3 proof that Nova reads StarRocks 4.1 through Arrow Flight SQL."""

from __future__ import annotations

from uuid import uuid4

import asyncmy
import pytest

from app.core.config import settings
from app.modules.ml_engine.data.arrow_flight import ArrowFlightDataSource
from app.modules.ml_engine.data.datasource import collect_bounded
from app.modules.ml_engine.spec import ExecutionBudget, MLSecurityContext
from tests.conftest import engine_host_ports, require_stack

pytestmark = pytest.mark.engine


@pytest.mark.asyncio
async def test_starrocks_arrow_flight_preserves_types_and_batches(docker_services, monkeypatch):
    require_stack(docker_services)
    ports = engine_host_ports()
    root = await asyncmy.connect(
        host="127.0.0.1",
        port=ports["starrocks-fe"],
        user="root",
        password="",
        autocommit=True,
    )
    database = f"nova_ml_arrow_{uuid4().hex[:10]}"
    async with root.cursor() as cursor:
        await cursor.execute(f"CREATE DATABASE `{database}`")
        await cursor.execute(
            f"CREATE TABLE `{database}`.features ("
            "id BIGINT, amount DOUBLE, category VARCHAR(32), event_time DATETIME"
            ") DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            'PROPERTIES("replication_num"="1")'
        )
        await cursor.execute(
            f"INSERT INTO `{database}`.features VALUES "
            "(1, 12.5, 'a', '2026-01-01 01:02:03'),"
            "(2, 18.0, 'b', '2026-01-02 04:05:06'),"
            "(3, 21.5, 'a', '2026-01-03 07:08:09')"
        )

    monkeypatch.setattr(settings, "STARROCKS_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "STARROCKS_ARROW_FLIGHT_PORT", ports["starrocks-fe-arrow"])
    try:
        result = await collect_bounded(
            ArrowFlightDataSource(batch_size=2),
            "SELECT id, amount, category, event_time FROM features ORDER BY id",
            MLSecurityContext("root", "", database=database),
            ExecutionBudget(20, 100, 1_000_000),
        )
        assert result.table.num_rows == 3
        assert result.metrics.batches_read == 2
        assert str(result.table.schema.field("id").type) == "int64"
        assert str(result.table.schema.field("amount").type) == "double"
        assert result.table.column("category").to_pylist() == ["a", "b", "a"]
    finally:
        async with root.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
        root.close()
