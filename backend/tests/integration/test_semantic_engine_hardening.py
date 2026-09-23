"""Execute governed comparison and logical-field SQL against the test engine."""

from uuid import uuid4

import asyncmy
import pytest
import pytest_asyncio

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan, SemanticPlanner
from tests.conftest import engine_host_ports, require_stack

pytestmark = pytest.mark.engine


@pytest_asyncio.fixture
async def semantic_engine(docker_services):
    require_stack(docker_services)
    connection = await asyncmy.connect(
        host="127.0.0.1",
        port=engine_host_ports()["starrocks-fe"],
        user="root",
        password="",
        autocommit=True,
    )
    database = "nova_semantic_qa_" + uuid4().hex[:12]
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(f"CREATE DATABASE `{database}`")
            await cursor.execute(
                f"CREATE TABLE `{database}`.orders (id BIGINT, amount DOUBLE, "
                "city VARCHAR(32), order_date DATE, region_key BIGINT) "
                "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
                'PROPERTIES("replication_num"="1")'
            )
            await cursor.execute(
                f"CREATE TABLE `{database}`.regions (region_key BIGINT, name VARCHAR(32)) "
                "DUPLICATE KEY(region_key) DISTRIBUTED BY HASH(region_key) BUCKETS 1 "
                'PROPERTIES("replication_num"="1")'
            )
            await cursor.execute(f"INSERT INTO `{database}`.regions VALUES (1, 'West')")
            await cursor.execute(
                f"INSERT INTO `{database}`.orders VALUES "
                "(1, 10, 'Surabaya', CURRENT_DATE(), 1), "
                "(2, 20, 'Surabaya', DATE_SUB(CURRENT_DATE(), INTERVAL 1 YEAR), 1), "
                "(3, 900, 'Surabaya', DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), 1), "
                "(4, 50, 'Bandung', CURRENT_DATE(), 1)"
            )
        model = SemanticModelIR.from_ossie(
            {
                "name": "sales",
                "datasets": [
                    {
                        "name": "orders",
                        "source": f"{database}.orders",
                        "fields": [
                            {"name": "gross", "expression": "amount", "kind": "fact"},
                            {
                                "name": "city",
                                "dimension": {"sample_values": ["Surabaya", "Bandung"]},
                            },
                            {"name": "order_date", "dimension": {"is_time": True}},
                            {"name": "region_id", "expression": "region_key", "kind": "fact"},
                        ],
                    },
                    {
                        "name": "regions",
                        "source": f"{database}.regions",
                        "fields": [
                            {"name": "region_id", "expression": "region_key", "kind": "fact"},
                            {"name": "region", "expression": "name", "kind": "dimension"},
                        ],
                    },
                ],
                "metrics": [
                    {
                        "name": "revenue",
                        "expression": "SUM(gross)",
                        "base_dataset": "orders",
                        "default_time_dimension": "order_date",
                    }
                ],
                "relationships": [
                    {
                        "name": "order_region",
                        "from": "orders",
                        "to": "regions",
                        "from_columns": ["region_id"],
                        "to_columns": ["region_id"],
                        "cardinality": "many_to_one",
                    }
                ],
            }
        )
        yield connection, model
    finally:
        async with connection.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
        connection.close()


async def test_compiled_yoy_preserves_literal_and_excludes_intervening_periods(semantic_engine):
    connection, model = semantic_engine
    planned = SemanticPlanner().plan(model, "Revenue Surabaya this month YoY")
    assert planned.confidence.unresolved_count == 0
    compiled = SemanticCompiler().compile(model, planned.plan)
    async with connection.cursor(asyncmy.cursors.DictCursor) as cursor:
        await cursor.execute(compiled.sql)
        rows = await cursor.fetchall()
    assert {row["comparison_period"]: row["revenue"] for row in rows} == {
        "current_period": 10,
        "previous_period": 20,
    }


async def test_compiled_relationship_uses_physical_fields_without_metric_fanout(semantic_engine):
    connection, model = semantic_engine
    compiled = SemanticCompiler().compile(
        model, SemanticPlan(metrics=("revenue",), dimensions=("region",))
    )
    async with connection.cursor(asyncmy.cursors.DictCursor) as cursor:
        await cursor.execute(compiled.sql)
        rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["region"] == "West"
    assert rows[0]["revenue"] == 980
