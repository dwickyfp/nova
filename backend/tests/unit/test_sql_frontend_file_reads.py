"""Native ``FILES()`` reads are refused; files are read through a stage.

``FILES()`` makes the engine connect to whatever its properties name: an
``aws.s3.endpoint`` on the internal network, an ``hdfs://`` host, the backend's
instance credentials. Nova refuses the table function in user SQL at AST
building, for every statement and consumer, and reads files through
``@stage`` instead. Pipes keep their validated ``FILES("path" = "@stage/...")``
source, and ``INSERT INTO FILES`` stays an unload to a stage.
"""

from __future__ import annotations

import pytest

from app.modules.assistant.tools import policy
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.ast.statements import PipeStatement, StageAwareStatement
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.file_reads import FILES_READ_REFUSAL, has_native_file_read
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner

REJECTED = (
    # The audit's SSRF examples: an internal S3 endpoint and an HDFS host.
    "SELECT * FROM FILES('path'='s3://b/x.parquet','format'='parquet',"
    "'aws.s3.endpoint'='http://10.0.0.5:9000')",
    "SELECT * FROM FILES('path'='hdfs://10.0.0.7:8020/data/x.parquet','format'='parquet')",
    # Instance credentials of the backend host.
    "SELECT * FROM FILES('path'='s3://b/x.csv','format'='csv',"
    "'aws.s3.use_instance_profile'='true')",
    # A public bucket with the caller's own keys is still outside stage governance.
    "SELECT * FROM FILES('path'='s3://b/x.csv','format'='csv',"
    "'aws.s3.access_key'='AK','aws.s3.secret_key'='SK')",
    # A stage path is not lowered outside pipes; read the stage directly.
    "SELECT * FROM FILES('path'='@landing/orders/a.csv','format'='csv')",
    'select * from files("path" = "s3://b/x.parquet", "format" = "parquet")',
    # Every position a relation can take.
    "SELECT a.id FROM t a JOIN FILES('path'='s3://b/x.parquet','format'='parquet') f "
    "ON a.id = f.id",
    "WITH f AS (SELECT * FROM FILES('path'='s3://b/x.parquet','format'='parquet')) "
    "SELECT * FROM f",
    "SELECT * FROM (SELECT * FROM FILES('path'='s3://b/x.parquet','format'='parquet')) s",
    "SELECT id FROM t WHERE id IN "
    "(SELECT id FROM FILES('path'='s3://b/x.parquet','format'='parquet'))",
    "INSERT INTO t SELECT * FROM FILES('path'='s3://b/x.parquet','format'='parquet')",
    "CREATE TABLE t AS SELECT * FROM FILES('path'='s3://b/x.parquet','format'='parquet')",
    "CREATE VIEW v AS SELECT * FROM FILES('path'='s3://b/x.parquet','format'='parquet')",
    "EXPLAIN SELECT * FROM FILES('path'='s3://b/x.parquet','format'='parquet')",
    "EXPLAIN SCHEMA SELECT * FROM FILES('path'='s3://b/x.parquet','format'='parquet')",
    "DESC FILES('path'='s3://b/x.parquet','format'='parquet')",
    "CREATE TASK t AS INSERT INTO sink SELECT * FROM FILES('path'='s3://b/x','format'='csv')",
    "SUBMIT TASK t AS INSERT INTO sink SELECT * FROM FILES('path'='s3://b/x','format'='csv')",
)

ACCEPTED = (
    "SELECT * FROM @landing/orders/a.csv",
    "SELECT * FROM @landing.orders.a.csv",
    "INSERT INTO t SELECT * FROM @landing/orders/a.csv",
    "COPY INTO t FROM @landing/orders/a.csv",
    # A table or column that happens to be called files.
    "SELECT * FROM `files`",
    "SELECT `files` FROM t",
    "SELECT 'FILES(' AS note",
    "SELECT * FROM TABLE(generate_series(1, 3))",
    # An unload writes to a stage folder; validated by the unload rules.
    "INSERT INTO FILES('path'='@exports/daily/','format'='parquet') SELECT * FROM t",
)

PIPE_SOURCE = (
    'CREATE PIPE p AS INSERT INTO t SELECT * FROM FILES('
    '"path" = "@landing/orders/*.parquet", "format" = "parquet")'
)


@pytest.mark.parametrize("sql", REJECTED)
def test_native_files_read_is_refused_at_ast_building(sql):
    parsed = parse_statement(sql)
    assert has_native_file_read(parsed)
    with pytest.raises(SemanticError) as refused:
        ast_builders.build(parsed)
    assert str(refused.value) == FILES_READ_REFUSAL
    # The refusal names no address, endpoint or credential from the statement.
    for fragment in ("10.0.0", "hdfs://", "s3://", "AK", "SK"):
        assert fragment not in str(refused.value)


@pytest.mark.parametrize("sql", REJECTED)
def test_assistant_policy_denies_native_files_reads(sql):
    assert policy.is_allowed_statement(sql) is False


@pytest.mark.parametrize("sql", ACCEPTED)
def test_stage_reads_and_unrelated_files_names_are_accepted(sql):
    statement = ast_builders.build(parse_statement(sql))
    assert not has_native_file_read(statement.parsed)
    SQLPlanner().preflight(statement)


def test_stage_read_is_planned_as_a_stage_reference():
    statement = ast_builders.build(parse_statement("SELECT * FROM @landing/orders/a.csv"))
    assert isinstance(statement, StageAwareStatement)
    assert policy.is_allowed_statement("SELECT * FROM @landing/orders/a.csv") is True


def test_pipe_keeps_its_validated_stage_files_source():
    statement = ast_builders.build(parse_statement(PIPE_SOURCE))
    assert isinstance(statement, PipeStatement)


def test_pipe_still_refuses_a_storage_address_in_files():
    from app.modules.pipes.definition import PipeDefinitionError
    from app.sql_frontend.context import PlanningContext

    sql = (
        'CREATE PIPE p AS INSERT INTO t SELECT * FROM FILES('
        '"path" = "hdfs://10.0.0.7:8020/x/*.parquet", "format" = "parquet")'
    )
    statement = ast_builders.build(parse_statement(sql))
    with pytest.raises(PipeDefinitionError):
        SQLPlanner().semantics.validate(statement, PlanningContext(database="db"))


async def test_query_service_refuses_before_the_engine(monkeypatch):
    from unittest.mock import AsyncMock

    from app.modules.query.service import QueryService

    audits: list[dict] = []

    async def audit(**fields):
        audits.append(fields)
        return "audit-id"

    monkeypatch.setattr("app.modules.query.service.write_audit_log", audit)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda _: "pw")
    service = QueryService()
    service._repo.execute_as_user = AsyncMock()
    result = await service.execute(
        sql=REJECTED[0], username="alice", encrypted_password="enc", role="analyst"
    )
    assert not result.success
    assert result.error == FILES_READ_REFUSAL
    service._repo.execute_as_user.assert_not_awaited()
    assert [entry["status"] for entry in audits] == ["ERROR"]
