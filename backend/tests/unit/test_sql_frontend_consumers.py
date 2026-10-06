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
        state = SessionState(
            database="analytics", active_role="analyst", security_context_version=9
        )
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


async def test_ml_prediction_rows_are_built_off_the_event_loop(monkeypatch):
    import threading

    import pyarrow as pa

    from app.modules.ml_engine.service import ml_engine_service
    from app.sql_frontend.execution import adapters

    table = pa.table({"id": [1, 2, None], "prediction": [0.5, None, 1.5]})
    converted_on: list[str] = []
    original = adapters._table_rows

    def recording(result_table):
        converted_on.append(threading.current_thread().name)
        return original(result_table)

    monkeypatch.setattr(adapters, "_table_rows", recording)
    monkeypatch.setattr(
        ml_engine_service, "batch_predict_projected", AsyncMock(return_value=({}, table))
    )
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "caller-pw")
    service = QueryService()
    service._repo = AsyncMock()

    result = await service.execute(
        "SELECT id, ML_PREDICT('model', CAST(n AS DOUBLE)) AS prediction FROM numbers",
        "alice",
        "enc",
        database="analytics",
        role="analyst",
    )

    assert result.error is None
    assert result.columns == ["id", "prediction"]
    assert result.rows == [[1, 0.5], [2, None], [None, 1.5]] and result.row_count == 3
    assert len(converted_on) == 1 and converted_on[0] != threading.current_thread().name


@pytest.mark.parametrize("rows", [0, 1, 4096, 4097, 9000])
def test_prediction_table_rows_match_the_table_across_batch_boundaries(rows):
    import pyarrow as pa

    from app.sql_frontend.execution.adapters import _table_rows

    table = pa.table({"id": list(range(rows)), "label": [f"row-{i}" for i in range(rows)]})

    assert _table_rows(table) == [[i, f"row-{i}"] for i in range(rows)]


async def test_large_statement_is_prepared_off_the_event_loop(monkeypatch):
    """Guard, build and planning of a long statement must not run on the loop."""
    import threading

    from app.core.config import settings
    from app.modules.query import service as service_module

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 1024)
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    threads: dict[str, set[str]] = {}

    def recording(name, original):
        def wrapper(*args, **kwargs):
            threads.setdefault(name, set()).add(threading.current_thread().name)
            return original(*args, **kwargs)

        return wrapper

    service = QueryService()
    monkeypatch.setattr(
        service_module,
        "guard_user_statement",
        recording("guard", service_module.guard_user_statement),
    )
    monkeypatch.setattr(service._builders, "build", recording("build", service._builders.build))
    monkeypatch.setattr(
        service._planner, "preflight", recording("preflight", service._planner.preflight)
    )
    repository = AsyncMock()
    repository.execute_as_user.return_value = QueryResult(columns=["c"], rows=[[1]])
    service._repo = repository
    values = ", ".join(str(number) for number in range(600))

    results = await service.execute_statements(
        sql=f"SELECT 1 WHERE 1 IN ({values})",
        username="alice",
        encrypted_password="",
        connection=object(),
        role="analyst",
    )

    assert results[0].error is None and results[0].rows == [[1]]
    here = threading.current_thread().name
    assert threads.keys() == {"guard", "build", "preflight"}
    for name, used in threads.items():
        assert here not in used, f"{name} ran on the event loop"


async def test_planning_falls_back_to_the_event_loop_when_a_planner_waits(monkeypatch):
    import asyncio

    from app.core.config import settings

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 1)
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    service = QueryService()
    original = service._planner.plan
    waited: list[bool] = []

    async def plan(statement, context, **kwargs):
        await asyncio.sleep(0)
        waited.append(True)
        return await original(statement, context, **kwargs)

    monkeypatch.setattr(service._planner, "plan", plan)
    repository = AsyncMock()
    repository.execute_as_user.return_value = QueryResult(columns=["c"], rows=[[1]])
    service._repo = repository

    result = await service.execute(
        "SELECT 1", "alice", "", connection=object(), role="analyst"
    )

    assert result.error is None and waited == [True]
