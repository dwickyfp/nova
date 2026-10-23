"""Report calculations and navigation against the existing disposable test stack."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import asyncmy
import pytest
from fastapi import HTTPException

from app.core.redis import session_store
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticOrder, SemanticPlan, SemanticTime
from app.modules.smart_reports.repository import digest
from app.modules.smart_reports.schemas import FilterValue, ReportInteractionEvent
from app.modules.smart_reports.sessions import view_sessions
from tests.conftest import engine_host_ports, require_stack
from tests.unit.test_smart_reports import USER, report_spec, view

pytestmark = pytest.mark.engine


@pytest.fixture
async def report_engine(docker_services):
    require_stack(docker_services)
    connection = await asyncmy.connect(
        host="127.0.0.1",
        port=engine_host_ports()["starrocks-fe"],
        user="root",
        password="",
        autocommit=True,
    )
    name = "report_qa_" + uuid4().hex[:12]
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(f"CREATE DATABASE `{name}`")
            await cursor.execute(
                f"CREATE TABLE `{name}`.sales (id BIGINT, city VARCHAR(20), "
                "customer BIGINT, revenue DECIMAL(20,4), cost DECIMAL(20,4), observed_at DATE) "
                "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
                'PROPERTIES("replication_num"="1")'
            )
            values = [
                (
                    i,
                    "Jakarta" if i % 3 else "Bandung",
                    i % 37,
                    Decimal(i % 89) + Decimal("0.1234"),
                    Decimal(i % 13),
                    date(2026, 1, 1) + timedelta(days=i % 120),
                )
                for i in range(11037)
            ]
            for start in range(0, len(values), 1000):
                await cursor.executemany(
                    f"INSERT INTO `{name}`.sales VALUES (%s,%s,%s,%s,%s,%s)",
                    values[start : start + 1000],
                )
        model = SemanticModelIR.from_ossie(
            {
                "name": "sales",
                "datasets": [
                    {
                        "name": "sales",
                        "source": f"{name}.sales",
                        "fields": [
                            {"name": "city", "kind": "dimension"},
                            {"name": "customer", "kind": "dimension"},
                            {"name": "revenue", "kind": "fact"},
                            {"name": "cost", "kind": "fact"},
                            {"name": "observed_at", "dimension": {"is_time": True}},
                        ],
                    }
                ],
                "metrics": [
                    {"name": "sales_total", "expression": "SUM(revenue)", "base_dataset": "sales"},
                    {"name": "cost_total", "expression": "SUM(cost)", "base_dataset": "sales"},
                    {
                        "name": "customers",
                        "expression": "COUNT(DISTINCT customer)",
                        "base_dataset": "sales",
                    },
                ],
            }
        )
        yield connection, model, name
    finally:
        async with connection.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{name}` FORCE")
        connection.close()


async def test_calculation_ast_matches_full_data_golden_sql_before_paging(report_engine):
    connection, model, name = report_engine
    ratio = {
        "name": "ratio",
        "expression": {
            "op": "divide",
            "args": [
                {"op": "field", "field": "sales_total"},
                {"op": "field", "field": "cost_total"},
            ],
        },
    }
    band = {
        "name": "band",
        "expression": {
            "op": "case",
            "args": [
                {
                    "op": "gt",
                    "args": [{"op": "field", "field": "ratio"}, {"op": "number", "value": "7"}],
                },
                {"op": "number", "value": "1"},
                {"op": "number", "value": "0"},
            ],
        },
    }
    running = {
        "name": "running",
        "expression": {
            "op": "running_total",
            "args": [{"op": "field", "field": "sales_total"}],
            "order_by": [{"field": "city", "direction": "asc"}],
        },
    }
    plan = SemanticPlan(
        metrics=("sales_total", "cost_total", "customers"),
        dimensions=("city",),
        calculations=(ratio, band, running),
        order_by=(SemanticOrder("city", "asc"),),
        limit=1,
        offset=1,
    )
    compiled = SemanticCompiler().compile(model, plan)
    async with connection.cursor() as cursor:
        await cursor.execute(compiled.sql)
        actual = await cursor.fetchall()
        await cursor.execute(
            "SELECT city, sales_total, cost_total, customers, ratio, "
            "CASE WHEN ratio > 7 THEN 1 ELSE 0 END, "
            "SUM(sales_total) OVER (ORDER BY city ROWS UNBOUNDED PRECEDING) "
            "FROM (SELECT city, SUM(revenue) sales_total, SUM(cost) cost_total, "
            "COUNT(DISTINCT customer) customers, SUM(revenue)/NULLIF(SUM(cost),0) ratio "
            f"FROM `{name}`.sales GROUP BY city) q ORDER BY city LIMIT 1 OFFSET 1"
        )
        assert actual == await cursor.fetchall()
        assert len(actual) == 1 and actual[0][0] == "Jakarta"
        total = SemanticCompiler().compile(
            model,
            replace(
                plan,
                dimensions=(),
                calculations=(ratio,),
                limit=None,
                offset=0,
                order_by=(),
            ),
        )
        await cursor.execute(total.sql)
        total_row = (await cursor.fetchall())[0]
        await cursor.execute(
            "SELECT SUM(revenue), SUM(cost), COUNT(DISTINCT customer), "
            f"SUM(revenue)/NULLIF(SUM(cost),0) FROM `{name}`.sales"
        )
        assert total_row == (await cursor.fetchall())[0]
        assert total_row[2] == 37


async def test_fiscal_quarter_buckets_match_independent_calendar_sql(report_engine):
    connection, model, name = report_engine
    compiled = SemanticCompiler().compile(
        model,
        SemanticPlan(
            metrics=("sales_total",),
            time=SemanticTime("observed_at", grain="quarter"),
            time_output="period",
            fiscal_start_month=2,
            order_by=(SemanticOrder("period", "asc"),),
        ),
    )
    async with connection.cursor() as cursor:
        await cursor.execute(compiled.sql)
        actual = await cursor.fetchall()
        await cursor.execute(
            "SELECT CASE WHEN observed_at < '2026-02-01' THEN CAST('2025-11-01' AS DATETIME) "
            "ELSE CAST('2026-02-01' AS DATETIME) END AS period, SUM(revenue) "
            f"FROM `{name}`.sales GROUP BY period ORDER BY period"
        )
        assert actual == await cursor.fetchall()
        assert len(actual) == 2


async def test_distinct_options_page_over_unique_values_without_changing_detail_rows(report_engine):
    connection, model, name = report_engine
    plan = SemanticPlan(
        dimensions=("city",),
        distinct=True,
        order_by=(SemanticOrder("city", "asc"),),
        limit=1,
    )
    async with connection.cursor() as cursor:
        values = []
        for offset in range(3):
            compiled = SemanticCompiler().compile(model, replace(plan, offset=offset))
            await cursor.execute(compiled.sql)
            rows = await cursor.fetchall()
            await cursor.execute(
                f"SELECT city FROM `{name}`.sales GROUP BY city ORDER BY city "
                f"LIMIT 1 OFFSET {offset}"
            )
            assert rows == await cursor.fetchall()
            values.extend(row[0] for row in rows)
        assert values == ["Bandung", "Jakarta"]
        await cursor.execute(
            SemanticCompiler().compile(model, replace(plan, distinct=False, limit=2)).sql
        )
        assert [row[0] for row in await cursor.fetchall()] == ["Bandung", "Bandung"]


@pytest.mark.asyncio(loop_scope="session")
async def test_redis_handoff_replay_role_scope_and_concurrent_events(redis_client, monkeypatch):
    monkeypatch.setattr(session_store, "_redis", redis_client)
    user = {**USER, "session_id": uuid4().hex}
    state = view().model_copy(update={"session_id": uuid4().hex})
    spec = report_spec()
    key = view_sessions.key(user, state.session_id)
    event = ReportInteractionEvent(
        event_id=uuid4().hex,
        spec_revision=1,
        expected_state_revision=1,
        type="FILTER_SET",
        target="city",
        value=FilterValue(kind="single", values=["Bandung"]),
    )
    handoff_key = ""
    try:
        await view_sessions.create(user, state)
        handoff = await view_sessions.handoff(user, state)
        handoff_key = f"nova:reports:handoff:{digest(handoff['handoff_id'])}"
        with pytest.raises(HTTPException) as wrong_user:
            await view_sessions.consume({**user, "username": "bob"}, handoff["handoff_id"])
        assert wrong_user.value.status_code == 410
        full = await view_sessions.consume(user, handoff["handoff_id"])
        assert full.session_id == state.session_id
        with pytest.raises(HTTPException) as replay:
            await view_sessions.consume(user, handoff["handoff_id"])
        assert replay.value.status_code == 410
        changed = await view_sessions.event(user, state.report_id, state.session_id, spec, event)
        assert changed.filters["city"].values == ["Bandung"] and changed.state_revision == 2
        duplicate = await view_sessions.event(user, state.report_id, state.session_id, spec, event)
        assert duplicate.state_revision == 2
        stale = event.model_copy(update={"event_id": uuid4().hex})
        with pytest.raises(HTTPException) as conflict:
            await view_sessions.event(user, state.report_id, state.session_id, spec, stale)
        assert conflict.value.status_code == 409
        with pytest.raises(HTTPException):
            await view_sessions.get(
                {**user, "active_role": "publisher"}, state.report_id, state.session_id
            )
        assert (await view_sessions.get(user, state.report_id, state.session_id)).filters[
            "city"
        ].values == ["Bandung"]
        await redis_client.delete(key)
        with pytest.raises(HTTPException) as expired:
            await view_sessions.get(user, state.report_id, state.session_id)
        assert expired.value.status_code == 410
    finally:
        await redis_client.delete(key, f"{key}:event:{event.event_id}", handoff_key)


@pytest.mark.asyncio(loop_scope="session")
async def test_saved_view_preserves_shared_session_and_replays_once(redis_client, monkeypatch):
    from unittest.mock import AsyncMock

    from app.modules.smart_reports import context, exploration
    from app.modules.smart_reports.schemas import Origin

    monkeypatch.setattr(session_store, "_redis", redis_client)
    user = {**USER, "session_id": uuid4().hex}
    spec = report_spec()
    state = view().model_copy(
        update={
            "session_id": uuid4().hex,
            "origin": Origin(agent_id="agent-a", thread_id="thread-a"),
        }
    )
    saved = state.model_copy(
        update={
            "session_id": "old-session",
            "filters": {"city": FilterValue(kind="single", values=["Bandung"])},
            "origin": None,
        }
    )
    record = {
        "payload": {
            "owner": user["username"],
            "active_role": user["active_role"],
            "report_id": state.report_id,
            "state": saved.model_dump(mode="json"),
        }
    }

    async def current(report_id, session_id, caller):
        return spec, await view_sessions.get(caller, report_id, session_id)

    monkeypatch.setattr(exploration.report_service, "view", current)
    monkeypatch.setattr(exploration.report_service.repo, "read", AsyncMock(return_value=record))
    binding = AsyncMock()
    monkeypatch.setattr(context, "bind", binding)
    key = view_sessions.key(user, state.session_id)
    operation = uuid4().hex
    try:
        await view_sessions.create(user, state)
        restored = await exploration.apply_saved_view(
            state.report_id, state.session_id, "preset-a", operation, 1, user
        )
        assert restored.session_id == state.session_id
        assert restored.origin == state.origin
        assert restored.filters["city"].values == ["Bandung"]
        assert restored.state_revision == 2
        binding.assert_awaited_once_with(user, restored)
        duplicate = await exploration.apply_saved_view(
            state.report_id, state.session_id, "preset-a", operation, 1, user
        )
        assert duplicate == restored
        record["payload"]["owner"] = "another-user"
        with pytest.raises(HTTPException) as denied:
            await exploration.apply_saved_view(
                state.report_id, state.session_id, "preset-a", uuid4().hex, 2, user
            )
        assert denied.value.status_code == 404
        assert (await view_sessions.get(user, state.report_id, state.session_id)) == restored
    finally:
        await redis_client.delete(key, f"{key}:event:{operation}")


async def test_pivot_totals_match_distinct_and_ratio_golden_sql(report_engine):
    import asyncio

    from app.modules.smart_reports.pivot import pivot_result
    from app.modules.smart_reports.schemas import Widget

    connection, model, name = report_engine
    lock = asyncio.Lock()

    async def execute(source, plan):
        compiled = SemanticCompiler().compile(model, plan)
        async with lock, connection.cursor() as cursor:
            await cursor.execute(compiled.sql)
            return {"columns": [c[0] for c in cursor.description], "rows": await cursor.fetchall()}

    widget = Widget(
        id="pivot",
        title="Customer cities",
        kind="pivot",
        pivot_columns=["city"],
        query={
            "source": "sales",
            "metrics": ["customers", "sales_total", "cost_total"],
            "dimensions": ["customer", "city"],
        },
    )
    plan = SemanticPlan(
        metrics=("customers", "sales_total", "cost_total"), dimensions=("customer", "city")
    )
    result = await pivot_result(widget, plan, None, execute, view())
    async with connection.cursor() as cursor:
        await cursor.execute(
            f"SELECT COUNT(DISTINCT customer),SUM(revenue),SUM(cost) FROM `{name}`.sales"
        )
        golden = (await cursor.fetchall())[0]
    assert [result["totals"][f"total_{m}"] for m in plan.metrics] == list(golden)
    assert result["totals"]["total_customers"] == 37
    assert all(row[-3] == 1 for row in result["rows"])
    assert len(result["rows"]) == 37


async def test_window_rank_null_zero_and_decimal_precision_match_engine_sql(report_engine):
    connection, model, name = report_engine
    calculations = (
        {
            "name": "ordered_rank",
            "expression": {
                "op": "rank",
                "order_by": [{"field": "sales_total", "direction": "desc"}],
            },
        },
        {
            "name": "dense_rank",
            "expression": {
                "op": "dense_rank",
                "order_by": [{"field": "sales_total", "direction": "desc"}],
            },
        },
        {
            "name": "rolling",
            "expression": {
                "op": "moving_average",
                "args": [{"op": "field", "field": "sales_total"}],
                "order_by": [{"field": "customer"}],
                "preceding": 2,
            },
        },
        {
            "name": "undefined_ratio",
            "expression": {
                "op": "divide",
                "args": [{"op": "field", "field": "sales_total"}, {"op": "number", "value": "0"}],
            },
        },
        {
            "name": "safe_value",
            "expression": {
                "op": "coalesce",
                "args": [
                    {"op": "field", "field": "undefined_ratio"},
                    {"op": "number", "value": "9007199254740993.12"},
                ],
            },
        },
    )
    compiled = SemanticCompiler().compile(
        model,
        SemanticPlan(
            metrics=("sales_total",),
            dimensions=("customer",),
            calculations=calculations,
            order_by=(SemanticOrder("customer", "asc"),),
            limit=8,
            offset=7,
        ),
    )
    async with connection.cursor() as cursor:
        await cursor.execute(compiled.sql)
        actual = await cursor.fetchall()
        await cursor.execute(
            "SELECT customer, sales_total, RANK() OVER (ORDER BY sales_total DESC), "
            "DENSE_RANK() OVER (ORDER BY sales_total DESC), "
            "AVG(sales_total) OVER (ORDER BY customer ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), "
            "NULL, CAST('9007199254740993.12' AS DECIMAL(38,2)) "
            f"FROM (SELECT customer, SUM(revenue) sales_total FROM `{name}`.sales "
            "GROUP BY customer) q "
            "ORDER BY customer LIMIT 8 OFFSET 7"
        )
        assert actual == await cursor.fetchall()
        assert len(actual) == 8 and actual[0][-1] == Decimal("9007199254740993.12")
