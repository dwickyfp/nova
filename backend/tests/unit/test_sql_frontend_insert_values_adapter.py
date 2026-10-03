from unittest.mock import AsyncMock

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.capabilities.starrocks import StarRocksCapabilityProvider, normalize_identity
from app.sql_frontend.context import ExecutionContext, PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.execution.transactions import validate_transaction
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import Atomicity, CompositePlan, EngineSqlPlan
from app.sql_frontend.planning.planner import SQLPlanner


@pytest.mark.parametrize(
    "sql,columns,reads",
    [
        ("INSERT INTO records (id, value) VALUES (1, 'one')", ("id", "value"), ()),
        (
            "INSERT INTO records (`id`, `value`) VALUES (1, DEFAULT), (2, 'two')",
            ("id", "value"),
            (),
        ),
        (
            "INSERT INTO records WITH LABEL batch1 (id, value) VALUES (1, 'one')",
            ("id", "value"),
            (),
        ),
        (
            "INSERT INTO records (id, value) WITH LABEL batch1 VALUES (1, 'one')",
            ("id", "value"),
            (),
        ),
        ("INSERT INTO records WITH LABEL batch1 VALUES (1, 'one')", None, ()),
        ("INSERT INTO records VALUES (1, 'one')", None, ()),
        (
            "INSERT INTO records (id, value) SELECT id, value FROM source_records",
            ("id", "value"),
            (("default_catalog", "db", "source_records"),),
        ),
    ],
)
async def test_insert_preserves_sql_and_extracts_explicit_columns(sql, columns, reads):
    plan = await SQLPlanner().plan(
        ast_builders.build(parse_statement(sql)), PlanningContext(database="db")
    )
    assert isinstance(plan, EngineSqlPlan)
    assert plan.engine_sql == sql
    assert plan.effects.writes_data
    assert not plan.requires_confirmation
    intent = plan.transaction_intent
    assert intent is not None
    assert intent.kind == "insert"
    assert intent.target == ("default_catalog", "db", "records")
    assert intent.columns == columns
    assert intent.reads == reads
    assert intent.proven


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO records BY NAME SELECT 1 AS id",
        "INSERT INTO records WITH LABEL batch1 BY NAME SELECT 1 AS id",
        "INSERT INTO records (id) (value) VALUES (1, 'one')",
    ],
)
async def test_unresolved_column_mapping_is_not_transaction_eligible(sql):
    plan = await SQLPlanner().plan(
        ast_builders.build(parse_statement(sql)), PlanningContext(database="db")
    )
    assert isinstance(plan, EngineSqlPlan)
    assert plan.engine_sql == sql
    assert plan.transaction_intent is not None
    assert not plan.transaction_intent.proven
    capabilities = StarRocksCapabilityProvider().resolve(
        normalize_identity("4.1.4", deployment_mode="shared_data")
    )
    with pytest.raises(SemanticError, match="dependencies are not eligible"):
        await validate_transaction(
            CompositePlan((plan,), Atomicity.SINGLE_ENGINE_TRANSACTION),
            ExecutionContext("alice", database="db", capabilities=capabilities),
        )


async def test_insert_values_query_service_keeps_caller_and_dispatches_once(monkeypatch):
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda _: "pw")
    repository = AsyncMock()
    repository.execute_as_user.return_value = QueryResult(affected_rows=1)
    service = QueryService()
    service._repo = repository
    sql = "INSERT INTO records (id, value) VALUES (1, 'one')"
    result = await service.execute(sql, "alice", "enc", database="db", role="analyst")
    assert result.error is None
    assert result.affected_rows == 1
    repository.execute_as_user.assert_awaited_once()
    dispatched = repository.execute_as_user.await_args.kwargs
    assert dispatched["sql"] == sql
    assert dispatched["username"] == "alice"
    assert dispatched["role"] == "analyst"
    assert dispatched["database"] == "db"
