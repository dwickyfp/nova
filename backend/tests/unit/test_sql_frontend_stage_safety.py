"""Stage references must name what the client wrote, and answers must name stages.

``@stage/*.csv`` makes ``/*`` open a comment. With a later ``*/`` the glob is
swallowed and the reference silently becomes the whole stage, so the frontend
refuses a block comment glued to a stage path. In the other direction, engine
listings and storage errors name physical bucket paths; clients see ``@stage``.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.execution.adapters import (
    _logical_location,
    _LogicalLocationSink,
    _stage_location_map,
)
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner


def _validate(sql: str) -> None:
    planner = SQLPlanner()
    planner.semantics.validate(ast_builders.build(parse_statement(sql)), PlanningContext())


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM @raw/*.csv /* note */ LIMIT 1",
        "SELECT * FROM @raw/2024/x.csv/* note */",
        "SELECT * FROM @raw.sales_*.csv/* note */",
    ],
)
def test_a_comment_glued_to_a_stage_path_is_refused(sql):
    with pytest.raises(SemanticError, match="comment starts directly after a stage path"):
        _validate(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM @raw.*.csv /* note */ LIMIT 1",
        "SELECT * FROM @raw.sales_*.csv",
        "SELECT * FROM @raw/2024/x.csv /* note */",
        "SELECT * FROM @raw /* note */",
        "SELECT 1 /* @raw/*.csv */",
    ],
)
def test_separated_comments_and_dotted_globs_are_accepted(sql):
    _validate(sql)


def _config(bucket: str, prefix: str, endpoint: str | None = None):
    return SimpleNamespace(bucket=bucket, base_prefix=prefix, endpoint=endpoint)


def test_physical_roots_map_to_stage_names_longest_first():
    parsed = SimpleNamespace(
        stage_refs=[
            SimpleNamespace(start=0, stage_name="raw"),
            SimpleNamespace(start=40, stage_name="raw_2024"),
        ]
    )
    location_map = _stage_location_map(
        parsed,
        {
            0: _config("lake", "/tenants/a/"),
            40: _config("lake", "tenants/a/2024", "http://minio.internal:9000/"),
        },
    )

    text = (
        "No files found: 's3://lake/tenants/a/2024/x.csv' or "
        "'s3://lake/tenants/a/y.csv' at http://minio.internal:9000"
    )
    assert _logical_location(text, location_map) == (
        "No files found: '@raw_2024/x.csv' or '@raw/y.csv' at <storage endpoint>"
    )


def test_unresolved_references_contribute_no_mapping():
    parsed = SimpleNamespace(stage_refs=[SimpleNamespace(start=3, stage_name="raw")])
    assert _stage_location_map(parsed, {}) == []


def test_streamed_listing_rows_are_rewritten():
    received = {}

    class Sink:
        async def begin(self, columns, column_types):
            received["columns"] = columns

        async def rows(self, rows):
            received.setdefault("rows", []).extend(rows)

    sink = _LogicalLocationSink(Sink(), [("s3://lake/tenants/a", "@raw")])

    async def run():
        await sink.begin(["PATH", "SIZE"], ("STRING", "BIGINT"))
        await sink.rows([["s3://lake/tenants/a/x.csv", 54], [None, 0]])

    asyncio.run(run())
    assert received == {"columns": ["PATH", "SIZE"], "rows": [["@raw/x.csv", 54], [None, 0]]}


def test_engine_response_names_stages_not_buckets_or_endpoints():
    from app.modules.query.dialect.parser import CommandType
    from app.modules.query.repository import QueryResult
    from app.sql_frontend.execution.adapters import FeatureAdapters

    physical = (
        "SELECT * FROM FILES('path'='s3://lake/tenants/a/x.csv', 'format'='csv', "
        "'aws.s3.endpoint'='http://minio.internal:9000')"
    )

    class Repo:
        async def execute_as_user(self, **kwargs):
            return QueryResult(columns=["c"], rows=[[1]], row_count=1, executed_sql=kwargs["sql"])

    audited = []

    async def audit(**kwargs):
        audited.append(kwargs)

    adapters = object.__new__(FeatureAdapters)
    adapters._repo = Repo()
    adapters._audit_sink = audit
    adapters._decrypt = lambda _value: "pw"
    context = SimpleNamespace(
        username="u", encrypted_password="e", database="db", schema=None, role="r",
        max_rows=10, connection=None, session_id=None, file_id=None, row_sink=None,
        engine_session_prepared=False, transaction_active=False, statements={},
    )
    result = asyncio.run(
        adapters._run_on_engine(
            SimpleNamespace(source_key=0), context, sql="SELECT * FROM @raw/x.csv",
            executed_sql=physical, audit_sql=physical, warnings=[],
            location_map=[("s3://lake/tenants/a", "@raw"),
                          ("http://minio.internal:9000", "<storage endpoint>")],
            parsed=SimpleNamespace(command_type=CommandType.STAGE_QUERY),
        )
    )
    assert "s3://" not in result.executed_sql
    assert "minio" not in result.executed_sql
    assert "'path'='@raw/x.csv'" in result.executed_sql


@pytest.mark.parametrize(
    "sql",
    ["COPY INTO t FROM other_table", "COPY INTO t FROM (SELECT * FROM other_table)"],
)
def test_copy_without_a_stage_is_refused_with_its_alternative(sql):
    with pytest.raises(SemanticError, match="to copy between tables use INSERT INTO"):
        _validate(sql)


@pytest.mark.parametrize("sql", ["COPY INTO t FROM @raw/x.csv", "COPY INTO t FROM @raw.*.csv"])
def test_copy_from_a_stage_still_validates(sql):
    _validate(sql)


class _NoEngine:
    async def execute_as_user(self, sql, **kwargs):  # pragma: no cover - not reached
        raise AssertionError("an unresolved stage must not reach the engine")


def _stage_service(monkeypatch, rows):
    from app.modules.query import service as service_module
    from app.modules.query.service import QueryService
    from tests.unit.stage_metadata_fakes import install_stages

    async def audit(**kwargs):
        return "qid"

    async def allowed(*args, **kwargs):
        return None

    monkeypatch.setattr(service_module, "write_audit_log", audit)
    monkeypatch.setattr(service_module, "decrypt_password", lambda value: "pw")
    monkeypatch.setattr(service_module, "check_stage_access", allowed)
    install_stages(monkeypatch, rows)
    service = QueryService()
    service._repo = _NoEngine()
    return service


def _row(identity, schema):
    return {
        "id": identity, "name": "raw", "database_name": "db", "schema_name": schema,
        "storage_connection": "production", "base_prefix": "db/raw",
    }


@pytest.mark.usefixtures("fake_metadata_db")
async def test_a_missing_stage_is_named_as_written(monkeypatch):
    service = _stage_service(monkeypatch, [_row("s1", "default")])
    [result] = await service.execute_statements(
        sql="SELECT * FROM @nostage_here/x.csv", username="alice", encrypted_password="enc",
        role="analyst", database="db",
    )
    assert result.error == "Stage 'nostage_here' not found in db"
    assert result.error_code == "stage_not_found"
    assert "__nova_stage_" not in str(result.warnings)


@pytest.mark.usefixtures("fake_metadata_db")
async def test_a_missing_stage_without_a_database_says_so(monkeypatch):
    service = _stage_service(monkeypatch, [_row("s1", "default")])
    [result] = await service.execute_statements(
        sql="SELECT * FROM @nostage_here/x.csv", username="alice", encrypted_password="enc",
        role="analyst",
    )
    assert result.error == "Stage 'nostage_here' not found (no database is selected)"
    assert result.error_code == "stage_not_found"


@pytest.mark.usefixtures("fake_metadata_db")
async def test_an_ambiguous_stage_is_named_as_written(monkeypatch):
    service = _stage_service(monkeypatch, [_row("s1", "default"), _row("s2", "other")])
    [result] = await service.execute_statements(
        sql="SELECT * FROM @raw/x.csv", username="alice", encrypted_password="enc",
        role="analyst", database="db",
    )
    assert result.error.startswith("Stage 'raw' matches more than one stage in db")
    assert result.error_code == "stage_ambiguous"
    assert "__nova_stage_" not in result.error
