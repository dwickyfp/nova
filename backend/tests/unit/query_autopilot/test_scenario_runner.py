from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query_autopilot.experiments import ExperimentResult
from tests.benchmark.query_autopilot import scenarios_live


async def test_unregistered_contention_database_is_rejected_before_engine_access(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("NOVA_AUTOPILOT_FIXTURE_STACK", "1")
    initialize = AsyncMock()
    monkeypatch.setattr(scenarios_live.db, "init_system_pool", initialize)
    with pytest.raises(ValueError, match="isolated contention snapshot"):
        await scenarios_live.run("autopilot_retail", tmp_path, contention_database="production")
    initialize.assert_not_awaited()


async def test_unavailable_contention_keeps_completed_cases_without_granting_access(
    monkeypatch, tmp_path,
):
    import json

    from tests.benchmark.query_autopilot import contention_live, statistics_live

    monkeypatch.setenv("NOVA_AUTOPILOT_FIXTURE_STACK", "1")
    monkeypatch.setattr(scenarios_live.db, "init_system_pool", AsyncMock())
    monkeypatch.setattr(scenarios_live.db, "close_system_pool", AsyncMock())

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield object()

    statements = []

    async def execute(self, statement, *args, **kwargs):
        statements.append(statement)
        return QueryResult(
            rows=[[1]], row_count=1, column_types=("INT",),
            engine_roundtrip_ms=6000 if "sleep(6)" in statement else 10,
            engine_query_ids=["unit-fixture-id"],
        )

    monkeypatch.setattr(scenarios_live.db, "user_conn", connection)
    monkeypatch.setattr(scenarios_live.QueryService, "execute", execute)
    monkeypatch.setattr(scenarios_live.Experiment, "run", AsyncMock(return_value=ExperimentResult(
        "FAILED", "result_changed", {}, {}, {}, "DIFFERENT", None, "fixture", 30,
    )))
    monkeypatch.setattr(statistics_live, "statistics_case", AsyncMock(return_value={
        "case": "E", "status": "PASS", "evidence_kind": "deterministic_unit_fixture",
    }))
    contention = AsyncMock(side_effect=RuntimeError("private-connection-state"))
    monkeypatch.setattr(contention_live, "contention_case", contention)
    result = await scenarios_live.run(
        "autopilot_retail", tmp_path, contention_database="autopilot_registered_snapshot",
    )
    assert contention.call_args.args[0] == "autopilot_registered_snapshot"
    cases = {case["case"]: case for case in result["cases"]}
    assert cases["A"]["status"] == cases["H"]["status"] == "PASS"
    assert cases["G"]["status"] == cases["D"]["status"] == "UNAVAILABLE"
    assert cases["G"]["error_type"] == "RuntimeError"
    assert result["complete_acceptance"] is False and result["counts"]["UNAVAILABLE"] == 2
    assert not any("GRANT" in statement.upper() for statement in statements)
    assert "private-connection-state" not in (tmp_path / "scenarios-native.json").read_text()
    persisted = json.loads((tmp_path / "scenarios-native.json").read_text())
    assert persisted == json.loads(json.dumps(result))
