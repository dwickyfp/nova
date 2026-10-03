from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query_autopilot.experiments import ExperimentResult
from tests.benchmark.query_autopilot import statistics_live


@pytest.mark.parametrize("items", [500, 999, 1000, 1500])
async def test_planted_cardinality_positive_requires_the_real_support_floor(
    monkeypatch, items,
):
    monkeypatch.setenv("NOVA_AUTOPILOT_FIXTURE_STACK", "1")

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield object()

    statements = []

    async def execute(self, sql, *args, **kwargs):
        statements.append(sql)
        return QueryResult(rows=[[items]], columns=["count"], row_count=1)

    trial = AsyncMock(return_value=ExperimentResult(
        "INCONCLUSIVE", "unit_fixture_not_measured", {}, {}, {}, "INCONCLUSIVE", None, None, 0,
    ))
    monkeypatch.setattr(statistics_live.db, "user_conn", connection)
    monkeypatch.setattr(statistics_live.QueryService, "execute", execute)
    monkeypatch.setattr(statistics_live.Experiment, "run", trial)
    if items < 1000:
        with pytest.raises(ValueError, match="cardinality_fixture_requires_at_least_1000_items"):
            await statistics_live.statistics_case("autopilot_retail")
        trial.assert_not_awaited()
        assert not any("SELECT item_id,1" in sql for sql in statements)
    else:
        result = await statistics_live.statistics_case("autopilot_retail")
        assert result["planting"] == {
            "growth_source": "order_items", "inserted_rows": items,
            "cardinality_support_floor": 1000,
        }
        assert any("SELECT item_id,1 FROM order_items" in sql for sql in statements)
        assert result["status"] == "FAIL"
    assert statements[-1].startswith("DROP TABLE autopilot_stats_")
    assert not any("GRANT" in sql for sql in statements)
