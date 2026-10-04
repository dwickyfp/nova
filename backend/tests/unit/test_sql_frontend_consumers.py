from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import ForbiddenSQLError
from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService, query_service
from app.proxy.executor import ProxyQueryExecutor
from app.proxy.session import SessionState
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import EngineSqlPlan, NovaActionPlan, SecurityPayload
from app.sql_frontend.planning.planner import SQLPlanner


async def test_native_service_parses_once_and_executes_once_without_metadata(monkeypatch):
    import app.sql_frontend.parser as parser

    original = parser.parse_tree
    parses = []

    def counted(sql):
        parses.append(sql)
        return original(sql)

    monkeypatch.setattr(parser, "parse_tree", counted)
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    repository = AsyncMock()
    repository.execute_as_user.return_value = QueryResult(rows=[[1]])
    service = QueryService()
    service._repo = repository
    result = await service.execute("SELECT 1", "alice", "enc", role="analyst")
    assert result.rows == [[1]] and parses == ["SELECT 1"]
    repository.execute_as_user.assert_awaited_once()
    assert repository.execute_as_user.await_args.kwargs["sql"] == "SELECT 1"


async def test_proxy_reuses_the_single_tree_for_unchanged_stage_sql(monkeypatch):
    import app.sql_frontend.parser as parser
    from app.modules.query.dialect.translator import StorageConfig

    original = parser.parse_tree
    parses = []

    def counted(sql):
        parses.append(sql)
        return original(sql)

    async def stages(parsed, **kwargs):
        return parsed, {
            ref.start: StorageConfig("s3", "http://storage", "bucket", "path", "key", "secret")
            for ref in parsed.stage_refs
        }

    monkeypatch.setattr(parser, "parse_tree", counted)
    monkeypatch.setattr(query_service, "_resolve_stage_refs", stages)
    monkeypatch.setattr(query_service, "_detect_csv_params", AsyncMock(return_value=({}, None)))
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    repository = AsyncMock()
    repository.execute_as_user.return_value = QueryResult(columns=["id"], rows=[[1]])
    monkeypatch.setattr(query_service, "_repo", repository)
    sql = "SELECT * FROM @s.a.csv a JOIN @s.b.csv b ON a.id=b.id"
    result = await ProxyQueryExecutor(SessionState(active_role="analyst")).execute(
        sql, username="alice", connection=object()
    )
    assert result.error is None and len(parses) == 1
    repository.execute_as_user.assert_awaited_once()


@pytest.mark.parametrize("relay", [False, True])
async def test_planned_ml_prediction_reaches_inference_with_the_caller_context(monkeypatch, relay):
    import pyarrow as pa

    from app.modules.ml_engine.service import ml_engine_service

    inference = AsyncMock(return_value=({}, pa.table({"prediction": [7.0]})))
    audit = AsyncMock()
    monkeypatch.setattr(ml_engine_service, "batch_predict_projected", inference)
    monkeypatch.setattr("app.modules.query.service.write_audit_log", audit)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "caller-pw")
    repository = AsyncMock()
    sql = "SELECT ML_PREDICT('model',CAST(n AS DOUBLE)) AS prediction FROM numbers"
    connection = object() if relay else None
    if relay:
        monkeypatch.setattr(query_service, "_repo", repository)
        state = SessionState(database="analytics", active_role="analyst")
        result = await ProxyQueryExecutor(state).execute(
            sql, username="alice", connection=connection
        )
    else:
        service = QueryService()
        service._repo = repository
        result = await service.execute(
            sql, "alice", "enc", database="analytics", role="analyst", security_context_version=9
        )
    assert result.error is None and result.rows == [[7.0]]
    inference.assert_awaited_once()
    context = inference.await_args.kwargs
    assert context["model_alias"] == "model"
    assert context["username"] == "alice" and context["role"] == "analyst"
    assert context["database_name"] == "analytics" and context["connection"] is connection
    assert context["password"] == ("" if relay else "caller-pw")
    if not relay:
        assert context["security_context_version"] == 9
    assert "ML_PREDICT" not in context["prediction_sql"]
    repository.execute_as_user.assert_not_awaited()
    assert audit.await_args.kwargs["action"] == "ml_predict_batch"


@pytest.mark.parametrize(
    "sql,operation",
    [
        ("CREATE ROLE reader", "create_role"),
        ("GRANT reader TO USER alice", "assign_role"),
        ("GRANT reader TO ROLE parent", "grant_role_to_role"),
        ("GRANT SELECT ON TABLE db.t TO ROLE reader", "grant_access"),
        ("REVOKE INSERT ON db.t FROM ROLE reader", "revoke_access"),
        ("SHOW AVAILABLE ROLES", "list_roles"),
        ("SHOW CURRENT ACCESS", "current_access"),
        ("SHOW GRANTS FOR ROLE reader", "role_grants"),
    ],
)
async def test_ranger_is_typed_and_native_mode_is_preserved(sql, operation):
    statement = ast_builders.build(parse_statement(sql))
    managed = await SQLPlanner().plan(statement, PlanningContext(ranger_enabled=True))
    assert isinstance(managed, NovaActionPlan)
    assert isinstance(managed.payload, SecurityPayload)
    assert managed.payload.operation == operation
    assert isinstance(await SQLPlanner().plan(statement, PlanningContext()), EngineSqlPlan)


@pytest.mark.parametrize(
    "sql",
    [
        "GRANT ALL ON *.* TO ROLE reader",
        "GRANT SELECT,INSERT ON db.t TO ROLE reader",
        "CREATE ROLE IF NOT EXISTS reader",
        "GRANT SELECT ON SYSTEM TO ROLE reader",
    ],
)
async def test_strict_ranger_rejects_unsupported_governance(sql):
    from app.modules.access_control.service import AccessControlError

    with pytest.raises(AccessControlError):
        await SQLPlanner().plan(
            ast_builders.build(parse_statement(sql)), PlanningContext(ranger_enabled=True)
        )


async def test_hard_guard_runs_before_parser_and_ranger_action(monkeypatch):
    service = QueryService()
    service._frontend = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("parser ran"))
    monkeypatch.setattr("app.core.config.settings.RANGER_ENABLED", True)
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
        await service.execute(
            "DROP ROLE ACCOUNTADMIN", "alice", "enc", role="ACCOUNTADMIN", confirm_destructive=True
        )


async def test_task_body_with_explicit_credentials_cannot_be_persisted():
    sql = (
        "CREATE TASK t AS INSERT INTO sink SELECT * FROM FILES("
        "'path'='s3://b/x', 'format'='csv', 'aws.s3.secret_key'='private')"
    )
    with pytest.raises(ValueError, match="persist credentials"):
        await SQLPlanner().plan(ast_builders.build(parse_statement(sql)), PlanningContext())


async def test_extension_runs_through_unmodified_query_service_with_injected_catalog(monkeypatch):
    from app.sql_frontend.analysis.effects import PlanEffects
    from app.sql_frontend.analysis.semantics import StatementSemantics, default_semantics
    from app.sql_frontend.ast.builder import AstBuilderRegistry
    from app.sql_frontend.ast.statements import Statement
    from app.sql_frontend.binding.models import BoundColumn, BoundTable, TableName
    from app.sql_frontend.planning.execution import Atomicity, CompositePlan
    from app.sql_frontend.planning.registry import PlannerRegistry

    class DummyStatement(Statement):
        pass

    name = TableName("source", "db")
    catalog = AsyncMock()
    catalog.resolve_table.return_value = BoundTable(name, "BASE TABLE")
    catalog.get_columns.return_value = (BoundColumn("id", "INT", 1, False),)

    class DummyPlanner:
        async def plan(self, logical, context):
            await context.binder.resolve_column(name, "id")
            selected = "SELECT 8" if context.capabilities.native_merge else "SELECT 7"
            return CompositePlan(
                (
                    EngineSqlPlan(selected, selected, logical.analysis.effects),
                    EngineSqlPlan("SELECT 9", "SELECT 9", PlanEffects(reads_data=True)),
                ),
                Atomicity.BEST_EFFORT,
            )

    builders = AstBuilderRegistry()
    builders.register("QueryStatementContext", DummyStatement)
    registry = PlannerRegistry()
    registry.register(DummyStatement, DummyPlanner())
    semantics = default_semantics()
    semantics.register(DummyStatement, StatementSemantics(lambda _: PlanEffects(reads_data=True)))
    service = QueryService(
        builders=builders,
        planner=SQLPlanner(registry, semantics=semantics),
        catalog_provider_factory=lambda *args: catalog,
    )
    calls = []

    async def execute(sql, **kwargs):
        calls.append(sql)
        return QueryResult(rows=[[int(sql[-1])]])

    service._repo = AsyncMock()
    service._repo.execute_as_user.side_effect = execute
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    assert (
        await service.execute("SELECT 1", "alice", "enc", database="db", role="analyst")
    ).rows == [[9]]
    assert calls == ["SELECT 7", "SELECT 9"]
    catalog.resolve_table.assert_awaited_once()
    catalog.get_columns.assert_awaited_once()
