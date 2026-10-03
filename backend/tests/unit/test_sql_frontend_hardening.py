import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.binding.catalog import Binder
from app.sql_frontend.binding.models import (
    BoundColumn,
    BoundOutputColumn,
    BoundRelation,
    BoundTable,
    TableName,
)
from app.sql_frontend.binding.relations import RelationBinder
from app.sql_frontend.binding.schema import alter_eligibility, compare_schema, map_by_name
from app.sql_frontend.binding.types import (
    TypeCompatibility,
    TypeCompatibilityChecker,
    parse_sql_type,
)
from app.sql_frontend.capabilities.starrocks import StarRocksCapabilityProvider, normalize_identity
from app.sql_frontend.context import ExecutionContext, PlanningContext
from app.sql_frontend.errors import BindingError, SemanticError
from app.sql_frontend.execution.executor import SQLExecutor
from app.sql_frontend.execution.transactions import TransactionRunner, validate_transaction
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import Atomicity, CompositePlan
from app.sql_frontend.planning.planner import SQLPlanner
from app.sql_frontend.stages import planned_stages


async def test_capabilities_singleflight_ttl_failure_retry_and_target_isolation():
    clock = [0]
    provider = StarRocksCapabilityProvider(clock=lambda: clock[0])
    reader = AsyncMock(return_value="4.1.4 abc123")
    results = await asyncio.gather(*(provider.detect("one", reader) for _ in range(20)))
    assert all(result is results[0] for result in results)
    assert results[0].sql_transactions and not results[0].transaction_update_delete
    reader.assert_awaited_once()
    assert (await provider.detect("two", reader)).identity.build == "abc123"
    assert reader.await_count == 2
    clock[0] = 901
    await provider.detect("one", reader)
    assert reader.await_count == 3
    provider.invalidate("one")
    await provider.detect("one", reader)
    assert reader.await_count == 4
    failing = AsyncMock(side_effect=RuntimeError("no engine"))
    assert not (await provider.detect("broken", failing)).sql_transactions
    await provider.detect("broken", failing)
    failing.assert_awaited_once()
    clock[0] += 31
    await provider.detect("broken", failing)
    assert failing.await_count == 2


async def test_capability_overrides_identity_modes_and_malformed_values():
    provider = StarRocksCapabilityProvider()
    reader = AsyncMock(return_value="StarRocks version 4.1.4+abc123")
    caps = await provider.detect(
        "one", reader, deployment_mode="shared_data", overrides={"native_merge": True}
    )
    assert caps.native_merge and caps.transaction_update_delete and caps.transaction_repeated_insert
    identity = normalize_identity("4.2.0-rc1+build9")
    assert identity.version.prerelease == "rc1" and identity.build == "build9"
    assert not provider.resolve(identity).sql_transactions
    assert normalize_identity("5.1.0 mysql garbage").version is None
    with pytest.raises(ValueError):
        await provider.detect("one", reader, overrides={"typo": True})
    with pytest.raises(ValueError):
        await provider.detect("one", reader, overrides={"native_merge": 1})


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; UPDATE t SET x=2; SELECT 3",
        "CREATE TABLE t (x INT); DELETE FROM t",
        "SELECT 1; /* change */ DROP TABLE t",
    ],
)
async def test_script_preflight_never_executes_before_confirmation(sql, monkeypatch):
    service = QueryService(capability_resolver=AsyncMock())
    engine = AsyncMock(return_value=QueryResult())
    service._repo.execute_as_user = engine
    audit = AsyncMock()
    monkeypatch.setattr("app.modules.query.service.write_audit_log", audit)
    result = await service.execute_statements(sql, "alice", "enc")
    assert len(result) == 1 and result[0].needs_confirmation
    assert result[0].error_code == "confirmation_required" and result[0].effects
    engine.assert_not_awaited()
    audit.assert_awaited_once()


async def test_confirmed_create_then_insert_and_overwrite_policy(monkeypatch):
    provider = StarRocksCapabilityProvider()
    service = QueryService(capability_resolver=AsyncMock(return_value=provider.for_version()))
    service._repo.execute_as_user = AsyncMock(return_value=QueryResult())
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda _: "pw")
    results = await service.execute_statements(
        "CREATE TABLE t (x INT); INSERT OVERWRITE t SELECT 1", "alice", "enc"
    )
    assert len(results) == 2 and all(result.success for result in results)
    assert results[-1].effects["replaces_data"] and not results[-1].destructive
    assert service._repo.execute_as_user.await_count == 2


async def test_stage_template_keeps_rewrites_without_ast_rescan():
    sql = "SELECT * FROM @s/a.csv a JOIN @db.public.s.b.csv b ON a.id=b.id"
    plan = await SQLPlanner().plan(ast_builders.build(parse_statement(sql)), PlanningContext())
    rewritten = replace(plan, engine_sql="/* rule prefix */ " + plan.engine_sql + " LIMIT 3")
    stages = planned_stages(rewritten)
    assert stages.original_sql.startswith("/* rule prefix */") and stages.original_sql.endswith(
        "LIMIT 3"
    )
    assert len(stages.stage_refs) == 2 and stages.stage_refs[0].file_name == "a.csv"
    for ref in stages.stage_refs:
        assert stages.original_sql[ref.start : ref.end] == ref.full_match
    with pytest.raises(ValueError):
        planned_stages(replace(plan, engine_sql="SELECT 1"))


async def test_capability_cache_configuration_identity_and_inflight_invalidation():
    provider = StarRocksCapabilityProvider()
    started, release = asyncio.Event(), asyncio.Event()

    async def reader():
        started.set()
        await release.wait()
        return "4.1.4"

    pending = asyncio.create_task(provider.detect("one", reader, config_identity="config-a"))
    await started.wait()
    provider.invalidate("one")
    release.set()
    await pending
    probe = AsyncMock(return_value="4.1.4")
    await provider.detect("one", probe, config_identity="config-a")
    await provider.detect("one", probe, config_identity="config-b")
    assert probe.await_count == 2
    await provider.detect("one", probe, config_identity="config-a")
    assert probe.await_count == 2


@pytest.mark.parametrize(
    "join,expected", [("LEFT", [False, True]), ("RIGHT", [True, False]), ("FULL", [True, True])]
)
async def test_outer_join_output_nullability(join, expected):
    binder = RelationBinder(Binder(Catalog()), database="db")
    bound = await binder.bind_relation(f"SELECT a.id,b.id FROM t a {join} JOIN t b ON a.id=b.id")
    assert [column.nullable for column in bound.columns] == expected


@pytest.mark.parametrize("stage", [False, True])
async def test_sensitive_runtime_slots_preserve_rewrites_and_private_material(stage, monkeypatch):
    from app.modules.query.dialect.translator import StorageConfig
    from app.sql_frontend.rules.registry import RuleRegistry
    from app.sql_frontend.rules.stage import StageReferenceRule

    class Rewrite:
        name = "private_sql_rewrite"

        def matches(self, plan, context):
            return True

        async def apply(self, plan, context):
            return replace(plan, engine_sql="/* longer prefix */ " + plan.engine_sql + " LIMIT 1")

    rules = RuleRegistry()
    rules.register(StageReferenceRule())
    rules.register(Rewrite())
    service = QueryService(
        planner=SQLPlanner(rules=rules),
        capability_resolver=AsyncMock(return_value=StarRocksCapabilityProvider().for_version()),
    )
    repository = AsyncMock(return_value=QueryResult(rows=[[1]]))
    service._repo.execute_as_user = repository
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda _: "pw")

    async def stages(parsed, **kwargs):
        return parsed, {
            ref.start: StorageConfig("s3", "http://storage", "bucket", "path", "key", "stage-key")
            for ref in parsed.stage_refs
        }

    monkeypatch.setattr(service, "_resolve_stage_refs", stages)
    monkeypatch.setattr(service, "_detect_csv_params", AsyncMock(return_value=({}, None)))
    sql = (
        "SELECT * FROM FILES('path'='s3://bucket/a.csv','aws.s3.secret_key'='private-native-key') p"
    )
    if stage:
        sql += " JOIN @s.a.csv s ON p.id=s.id"
    plan = await service._planner.plan(ast_builders.build(parse_statement(sql)), PlanningContext())
    assert "private-native-key" not in str(plan)
    assert plan.private_bindings
    result = await service.execute(sql, "alice", "enc", role="analyst")
    assert result.success, result.error
    engine_sql = repository.await_args.kwargs["sql"]
    assert "private-native-key" in engine_sql and engine_sql.endswith("LIMIT 1")
    assert engine_sql.startswith("/* longer prefix */")
    assert "private-native-key" not in result.executed_sql
    assert "__nova_private_" not in engine_sql and "__nova_stage_" not in engine_sql


async def test_stream_preparation_restores_private_material_and_redacts_public_sql():
    from app.modules.ml_engine.spec import MLSecurityContext
    from app.sql_frontend.preparation import prepare_stream_sql

    sql = "SELECT * FROM FILES('path'='s3://bucket/a.csv','aws.s3.secret_key'='private-key')"
    prepared = await prepare_stream_sql(sql, MLSecurityContext("alice", "pw", role="analyst"))
    assert prepared.engine_sql == sql
    assert "private-key" not in prepared.redacted_sql
    assert "__nova_private_" not in prepared.redacted_sql


async def test_http_security_refusal_keeps_result_metadata_and_forbidden_status(monkeypatch):
    from app.modules.query.router import QueryRequest, execute_query

    service = QueryService(capability_resolver=AsyncMock())
    monkeypatch.setattr("app.modules.query.router.query_service", service)
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    response = await execute_query(
        QueryRequest(sql="SELECT 1; DROP ROLE ACCOUNTADMIN", confirm_destructive=True),
        {
            "username": "alice",
            "encrypted_password": "enc",
            "active_role": "analyst",
            "assigned_roles": ["analyst"],
            "session_id": "s",
        },
    )
    assert response.status_code == 403
    import json

    result = json.loads(response.body)[0]
    assert not result["success"] and result["error_code"] == "security_rejection"
    assert not result["needs_confirmation"]


class Catalog:
    def __init__(self):
        self.calls = 0

    async def resolve_table(self, name):
        return BoundTable(name, "VIEW")

    async def get_columns(self, name):
        self.calls += 1
        return (BoundColumn("id", "INT", 1, False), BoundColumn("label", "VARCHAR(32)", 2, True))


@pytest.mark.parametrize(
    "sql,names,kinds",
    [
        ("SELECT * FROM t", ["id", "label"], ["INTEGER", "VARCHAR"]),
        ("SELECT t.* FROM t", ["id", "label"], ["INTEGER", "VARCHAR"]),
        (
            "SELECT label AS Name,id,id AS id FROM t",
            ["Name", "id", "id"],
            ["VARCHAR", "INTEGER", "INTEGER"],
        ),
        ("SELECT id+1 AS computed FROM t", ["computed"], ["UNKNOWN"]),
        ("WITH c(a,b) AS (SELECT * FROM t) SELECT b,a FROM c", ["b", "a"], ["VARCHAR", "INTEGER"]),
        ("SELECT a.* FROM (SELECT label,id FROM t) a", ["label", "id"], ["VARCHAR", "INTEGER"]),
        (
            "SELECT a.id,b.label FROM t a JOIN t b ON a.id=b.id",
            ["id", "label"],
            ["INTEGER", "VARCHAR"],
        ),
    ],
)
async def test_relation_outputs_are_ordered_and_cached(sql, names, kinds):
    catalog = Catalog()
    binder = RelationBinder(Binder(catalog), database="db")
    bound = await binder.bind_relation(sql)
    assert [column.name for column in bound.columns] == names
    assert [column.sql_type.kind for column in bound.columns] == kinds
    assert await binder.bind_relation(sql) is bound
    assert catalog.calls == 1


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id+1 FROM t",
        "SELECT id FROM t a JOIN t b ON a.id=b.id",
        "WITH c(a) AS (SELECT * FROM t) SELECT * FROM c",
        "SELECT * FROM t UNION SELECT * FROM t",
    ],
)
async def test_undetermined_relation_outputs_fail_structurally(sql):
    with pytest.raises(BindingError):
        await RelationBinder(Binder(Catalog())).bind_relation(sql)


async def test_stage_relation_provider_is_lazy():
    provider = AsyncMock(
        return_value=BoundRelation((BoundOutputColumn("id", parse_sql_type("INT")),))
    )
    binder = RelationBinder(Binder(Catalog()), stage_schema=provider)
    assert (await binder.bind_relation("SELECT x.* FROM @s/file.parquet x")).columns[0].name == "id"
    provider.assert_awaited_once()


@pytest.mark.parametrize(
    "raw,kind",
    [
        ("decimal128(38,9)", "DECIMAL"),
        ("varchar(64)", "VARCHAR"),
        ("ARRAY<MAP<VARCHAR(8),STRUCT<a:INT,b:ARRAY<DECIMAL(12,3)>>>>", "ARRAY"),
        ("futurevector(8)", "UNKNOWN"),
        ("decimal(2,4)", "UNKNOWN"),
    ],
)
def test_nested_types_keep_raw_engine_type(raw, kind):
    typ = parse_sql_type(raw)
    assert typ.kind == kind and typ.raw == raw


@pytest.mark.parametrize(
    "source,target,result",
    [
        ("INT", "BIGINT", "SAFE_WIDEN"),
        ("BIGINT", "INT", "LOSSY"),
        ("DECIMAL(12,3)", "DECIMAL(14,4)", "SAFE_WIDEN"),
        ("DECIMAL(12,3)", "DECIMAL(12,1)", "LOSSY"),
        ("ARRAY<INT>", "ARRAY<BIGINT>", "SAFE_WIDEN"),
        ("ARRAY<future>", "ARRAY<future>", "UNKNOWN"),
    ],
)
def test_schema_widening_is_distinct_from_casting(source, target, result):
    assert TypeCompatibilityChecker.compare(
        parse_sql_type(source), parse_sql_type(target)
    ) == getattr(TypeCompatibility, result)
    assert (
        TypeCompatibilityChecker.query_assignment(parse_sql_type("INT"), parse_sql_type("DOUBLE"))
        == TypeCompatibility.IMPLICIT_CAST
    )
    assert not TypeCompatibilityChecker.safe_schema_widen(
        parse_sql_type("INT"), parse_sql_type("DOUBLE")
    )


def test_name_mapping_and_schema_delta_readiness():
    integer = parse_sql_type("INT")
    source = BoundRelation((BoundOutputColumn("B", integer), BoundOutputColumn("a", integer)))
    target = BoundRelation((BoundOutputColumn("A", integer), BoundOutputColumn("b", integer)))
    assert [item.source.name for item in map_by_name(source, target)] == ["a", "B"]
    with pytest.raises(BindingError, match="Duplicate"):
        map_by_name(BoundRelation((source.columns[0], source.columns[0])), target)
    with pytest.raises(BindingError, match="Missing"):
        map_by_name(BoundRelation(source.columns[:1]), target)
    table = BoundTable(
        TableName("t", "db"),
        "BASE TABLE",
        (BoundColumn("a", "INT", 1, False),),
        key_type="DUPLICATE",
        partition_columns=(),
    )
    delta = compare_schema(source, table)
    assert delta.safe_candidate and delta.additions[0].column.name == "B"
    assert alter_eligibility(table.columns[0]) == (
        "auto_increment_unknown",
        "key_column_unknown",
        "hidden_unknown",
        "partition_column_unknown",
    )
    assert not compare_schema(source, replace(table, key_type=None)).safe_candidate


class Connection:
    def __init__(self, fail=None):
        self.commands = []
        self.fail = fail
        self.closed = False

    @asynccontextmanager
    async def cursor(self):
        yield self

    async def execute(self, sql):
        self.commands.append(sql)
        if sql == self.fail:
            raise RuntimeError("lost response")

    async def select_db(self, db):
        self.commands.append("USE " + db)

    def close(self):
        self.closed = True


async def transaction_plans(sqls, mode="shared_data"):
    planner = SQLPlanner()
    steps = tuple(
        [
            await planner.plan(
                ast_builders.build(parse_statement(sql)), PlanningContext(database="db")
            )
            for sql in sqls
        ]
    )
    caps = StarRocksCapabilityProvider().resolve(normalize_identity("4.1.4", deployment_mode=mode))
    catalog = AsyncMock()
    catalog.resolve_table.side_effect = lambda name: BoundTable(name, "BASE TABLE")
    catalog.get_details.side_effect = lambda name: BoundTable(
        name, "BASE TABLE", key_type="PRIMARY"
    )
    catalog.get_columns.return_value = ()
    return CompositePlan(steps, Atomicity.SINGLE_ENGINE_TRANSACTION), ExecutionContext(
        "alice",
        database="db",
        role="analyst",
        capabilities=caps,
        confirm_destructive=True,
        binder=Binder(catalog),
    )


@pytest.mark.parametrize("failure", [None, "step", "exception", "cancel", "COMMIT", "ROLLBACK"])
async def test_transaction_outcomes_and_one_session(failure):
    plan, context = await transaction_plans(["INSERT INTO a VALUES(1)", "INSERT INTO b VALUES(2)"])
    connection = Connection(failure)
    audit = AsyncMock()

    @asynccontextmanager
    async def open_connection(ctx):
        assert ctx is context
        yield connection

    calls = []

    async def engine(step, ctx):
        assert ctx.connection is connection and ctx.engine_session_prepared
        calls.append(step)
        if len(calls) == 2:
            if failure == "cancel":
                raise asyncio.CancelledError()
            if failure == "exception":
                raise RuntimeError("failed")
            if failure in {"step", "ROLLBACK"}:
                return QueryResult(error="failed")
        return QueryResult(affected_rows=1)

    executor = SQLExecutor(engine, transactions=TransactionRunner(open_connection, audit))
    if failure in {"cancel", "exception", "COMMIT"}:
        with pytest.raises(BaseException) as caught:
            await executor.execute(plan, context)
        outcome = caught.value.execution_failure["outcome"]
    else:
        result = await executor.execute(plan, context)
        outcome = result.execution_failure["outcome"] if result.error else "committed"
    expected = (
        "committed"
        if failure is None
        else "unknown"
        if failure in {"COMMIT", "ROLLBACK"}
        else "rolled_back"
    )
    assert outcome == expected
    if failure:
        metadata = (
            caught.value.execution_failure
            if failure in {"cancel", "exception", "COMMIT"}
            else result.execution_failure
        )
        assert metadata["rollback_attempted"] == (failure != "COMMIT")
        assert metadata["rollback_succeeded"] == (expected == "rolled_back")
    assert connection.commands[:3] == ["SET ROLE analyst", "USE db", "BEGIN"]
    assert connection.commands[-1] == ("COMMIT" if failure in {None, "COMMIT"} else "ROLLBACK")
    assert connection.closed == (outcome == "unknown")
    audit.assert_awaited_once()


@pytest.mark.parametrize(
    "sqls,mode",
    [
        (["INSERT INTO a VALUES(1)", "INSERT INTO a VALUES(2)"], "shared_nothing"),
        (["UPDATE a SET id=2"], "shared_nothing"),
        (["INSERT INTO a VALUES(1)", "UPDATE a SET id=2"], "shared_data"),
        (["DELETE FROM a", "UPDATE a SET id=2"], "shared_data"),
        (["INSERT INTO a VALUES(1)", "INSERT INTO b SELECT * FROM a"], "shared_data"),
        (["INSERT OVERWRITE a SELECT 1"], "shared_data"),
        (["CREATE TABLE a(id INT)"], "shared_data"),
        (["INSERT INTO other.a VALUES(1)", "INSERT INTO b VALUES(1)"], "shared_data"),
    ],
)
async def test_incompatible_transactions_are_refused_before_begin(sqls, mode):
    plan, context = await transaction_plans(sqls, mode)
    with pytest.raises(SemanticError):
        await validate_transaction(plan, context)


async def test_update_before_insert_eligible_but_nested_and_unknown_mode_refused():
    plan, context = await transaction_plans(["UPDATE a SET id=2", "INSERT INTO a VALUES(3)"])
    assert await validate_transaction(plan, context) == "db"
    with pytest.raises(SemanticError):
        await validate_transaction(plan, replace(context, transaction_active=True))
    identity = replace(context.capabilities.identity, deployment_mode=None)
    caps = replace(context.capabilities, identity=identity, transaction_update_delete=True)
    with pytest.raises(SemanticError):
        await validate_transaction(plan, replace(context, capabilities=caps))
