from dataclasses import asdict

import pytest

from app.modules.query.repository import QueryResult
from app.sql_frontend.analysis.lineage import read_query_sources
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.ast.statements import NativeStatement, QuerySchemaStatement
from app.sql_frontend.binding.models import BoundColumn
from app.sql_frontend.binding.starrocks import query_output_schema
from app.sql_frontend.binding.types import SqlType
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parameters import ParameterSpec
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner
from app.sql_frontend.read_queries import parameter_schema_query, preview_query, schema_query


@pytest.mark.parametrize("prefix", ["EXPLAIN", "DESC", "DESCRIBE"])
async def test_schema_statement_uses_the_shared_typed_read_plan(prefix):
    sql = prefix + " SCHEMA WITH x AS (SELECT id FROM t) SELECT id + 1 AS result FROM x"
    statement = ast_builders.build(parse_statement(sql))
    assert isinstance(statement, QuerySchemaStatement)
    plan = await SQLPlanner().plan(statement, PlanningContext())
    assert plan.engine_sql == sql
    assert plan.effects.reads_data
    assert not any(value for key, value in asdict(plan.effects).items() if key != "reads_data")
    assert not plan.requires_confirmation
    assert not plan.private_bindings
    assert not plan.stage_bindings


@pytest.mark.parametrize(
    "sql",
    [
        "EXPLAIN SCHEMA INSERT INTO t VALUES (1)",
        "EXPLAIN SCHEMA UPDATE t SET x=1",
        "EXPLAIN SCHEMA DELETE FROM t",
        "EXPLAIN SCHEMA REFRESH MATERIALIZED VIEW v",
        "CREATE TABLE t AS EXPLAIN SCHEMA SELECT 1",
        "CREATE VIEW v AS EXPLAIN SCHEMA SELECT 1",
        "EXPLAIN SCHEMA SELECT 1 INTO OUTFILE 's3://untrusted/result'",
    ],
)
def test_non_read_and_nested_forms_never_fall_through_to_engine(sql):
    with pytest.raises(SemanticError, match="top-level"):
        ast_builders.build(parse_statement(sql))


@pytest.mark.parametrize(
    "inner",
    [
        "SELECT :secret AS value",
        "SELECT @value AS value",
        "SELECT @@version AS value",
        "SELECT password('canary') AS value",
        "SELECT * FROM @stage/data.csv",
        "SELECT /*+ SET_VAR(query_timeout=1) */ 1 AS value",
        "WITH RECURSIVE c AS (SELECT 1 UNION ALL SELECT * FROM c) SELECT * FROM c",
        "SELECT * FROM files('path'='s3://untrusted/data')",
        "SELECT sleep(10) AS value",
        "SELECT ML_PREDICT('model', 1) AS value",
    ],
)
async def test_schema_inspection_cannot_trigger_external_or_unbounded_work(inner):
    statement = ast_builders.build(parse_statement("EXPLAIN SCHEMA " + inner))
    with pytest.raises(SemanticError):
        await SQLPlanner().plan(statement, PlanningContext())


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "EXPLAIN SELECT 1",
        "EXPLAIN VERBOSE SELECT 1",
        "SELECT map{'x':1}",
        "SELECT [1,2,3][1:2]",
    ],
)
def test_existing_query_forms_keep_their_native_statement(sql):
    assert type(ast_builders.build(parse_statement(sql))) is NativeStatement


def test_schema_wrapper_preserves_query_and_removes_only_statement_terminator():
    query = "WITH x AS (SELECT 1 AS id) SELECT id FROM x WHERE id > 2"
    assert schema_query(parse_statement(query + "; -- tail")) == "EXPLAIN SCHEMA " + query
    for sql in ["EXPLAIN SELECT 1", "SELECT :p AS value", "DELETE FROM t"]:
        with pytest.raises(SemanticError):
            schema_query(parse_statement(sql))


def test_parameter_schema_uses_declared_types_without_values_or_text_substitution():
    sql = "SELECT :amount AS total, ':amount' AS literal FROM sales WHERE id IN (:ids) /* :other */"
    specs = (ParameterSpec("amount", "decimal", precision=38, scale=12),
             ParameterSpec("ids", "integer", multiple=True))
    described = parameter_schema_query(parse_statement(sql), specs)
    assert described == (
        "EXPLAIN SCHEMA SELECT CAST(NULL AS DECIMAL(38,12)) AS total, ':amount' AS literal "
        "FROM sales WHERE id IN (CAST(NULL AS BIGINT))"
    )
    assert specs[0].precision == 38 and not hasattr(specs[0], "values")


@pytest.mark.parametrize("query", [
    "SELECT :value AS n FROM sales", "SELECT id FROM sales WHERE id IN (:ids, 1)",
    "SELECT :ids AS chosen_values FROM sales", "SELECT id FROM sales", "DELETE FROM sales",
])
def test_parameter_schema_refuses_undeclared_unused_or_structural_list_inputs(query):
    with pytest.raises(SemanticError):
        parameter_schema_query(
            parse_statement(query), (ParameterSpec("ids", "integer", multiple=True),)
        )


def test_parameter_preview_preserves_symbolic_bindings_inside_bounded_projection():
    query = parse_statement("SELECT id FROM sales WHERE id IN (:ids)")
    columns = (BoundColumn("id", "BIGINT", 1, False),)
    specs = (ParameterSpec("ids", "integer", multiple=True),)
    sql = preview_query(query, columns, limit=101, offset=100, parameter_specs=specs)
    assert "WHERE id IN (:ids)" in sql and "LIMIT 101 OFFSET 100" in sql
    with pytest.raises(SemanticError, match="typed execution plan"):
        preview_query(query, columns, limit=101, offset=100)


def test_parameter_schema_refuses_duplicate_declarations_before_analysis():
    with pytest.raises(SemanticError, match="unique"):
        parameter_schema_query(
            parse_statement("SELECT :id AS result FROM sales"),
            (ParameterSpec("id", "integer"), ParameterSpec("id", "string")),
        )


def schema_result(rows, **kwargs):
    return QueryResult(
        columns=["ordinal", "name", "physical_type", "nullable"], rows=rows, **kwargs
    )


def test_decode_authoritative_engine_types_without_sampled_rows():
    columns = query_output_schema(
        schema_result(
            [
                ["1", "id", "BIGINT", "NO"],
                ["2", "total", "DECIMAL128(38,12)", "YES"],
                ["3", "matched", "BOOLEAN", "YES"],
                ["4", "items", "ARRAY<VARCHAR(20)>", "YES"],
            ]
        )
    )
    assert columns[0].sql_type == SqlType("INTEGER", bits=64)
    assert columns[1].sql_type == SqlType("DECIMAL", precision=38, scale=12)
    assert columns[0].nullable is False and columns[1].nullable is True
    assert columns[2].sql_type == SqlType("BOOLEAN")
    assert columns[3].sql_type.element == SqlType("VARCHAR", length=20)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [["1", "id", "BIGINT"]],
        [[1, "id", "BIGINT", "NO"]],
        [["2", "id", "BIGINT", "NO"]],
        [["01", "id", "BIGINT", "NO"]],
        [["1", "id", "BIGINT", "unknown"]],
        [["1", "", "BIGINT", "NO"]],
        [["1", "id", "untrusted_type", "NO"]],
        [["1", "\ud800", "BIGINT", "NO"]],
        [["1", "x" * 257, "BIGINT", "NO"]],
        [["1", "id\x00", "BIGINT", "NO"]],
        [["1", "Id", "BIGINT", "NO"], ["2", "id", "BIGINT", "YES"]],
        [[str(i), f"c{i}", "BIGINT", "NO"] for i in range(1, 514)],
    ],
)
def test_malformed_or_ambiguous_engine_metadata_fails_closed(rows):
    with pytest.raises(SemanticError):
        query_output_schema(schema_result(rows))


def test_legacy_explain_or_error_is_not_mistaken_for_schema_metadata():
    for result in [
        QueryResult(columns=["Explain String"], rows=[["PLAN"]]),
        schema_result([["1", "id", "BIGINT", "NO"]], error="refused"),
    ]:
        with pytest.raises(SemanticError):
            query_output_schema(result)


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("SELECT * FROM t", [("analytics", "t")]),
        (
            "WITH x AS (SELECT * FROM sales.orders), y AS (SELECT * FROM x) SELECT * FROM y",
            [("sales", "orders")],
        ),
        ("WITH x AS (SELECT * FROM x) SELECT * FROM x", [("analytics", "x")]),
        (
            "SELECT * FROM x WHERE id IN (WITH x AS (SELECT * FROM t) SELECT id FROM x)",
            [("analytics", "x"), ("analytics", "t")],
        ),
        (
            "WITH x AS (SELECT * FROM t) SELECT * FROM x JOIN sales.x q ON x.id=q.id",
            [("analytics", "t"), ("sales", "x")],
        ),
        (
            "WITH x AS (SELECT * FROM t) SELECT * FROM "
            "(WITH x AS (SELECT * FROM u) SELECT * FROM x) q",
            [("analytics", "t"), ("analytics", "u")],
        ),
        ("SELECT * FROM t UNION ALL SELECT * FROM T", [("analytics", "t")]),
        ("SELECT * FROM `sales`.`odd``name`", [("sales", "odd`name")]),
        ("SELECT 1 AS x", []),
    ],
)
def test_dependency_analysis_respects_cte_scope_and_qualified_tables(sql, expected):
    sources = read_query_sources(parse_statement(sql), database="analytics")
    assert [(name.database, name.table) for name in sources] == expected


def test_dependencies_reject_unsupported_connectors_and_excess_sources():
    for sql in [
        "SELECT * FROM foreign_catalog.db.t",
        "SELECT * FROM " + ",".join(f"table_{index}" for index in range(65)),
    ]:
        with pytest.raises(SemanticError):
            read_query_sources(parse_statement(sql), database="analytics")
