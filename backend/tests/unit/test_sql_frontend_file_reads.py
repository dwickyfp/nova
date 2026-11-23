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


# --- The migration import: Nova's one generated FILES() read --------------------

_ROOT = "s3://stages/migration-staging"
_FOLDER = f"{_ROOT}/run1/sales/orders/"
_CREDS = {
    "aws.s3.access_key": "AK",
    "aws.s3.secret_key": "SK",
    "aws.s3.endpoint": "http://minio:9000",
    "aws.s3.enable_path_style_access": "true",
}
_GRANT_PROPERTIES = {"format": "parquet", **_CREDS}


def _creds_sql(creds: dict[str, str]) -> str:
    return ", ".join(f"'{key}'='{value}'" for key, value in creds.items())


def _migration_import_sql(folder: str = _FOLDER, creds: dict[str, str] = _CREDS) -> str:
    from app.modules.migration.data_mover import CopyColumn, build_table_copy

    plan = build_table_copy(
        source_database="sales",
        target_database="sales_copy",
        table="orders",
        columns=(CopyColumn("id", "BIGINT"), CopyColumn("amount", "DOUBLE")),
        stage_path=folder,
        files_credential_sql=_creds_sql(creds),
    )
    assert plan.stage_path == folder
    return plan.import_sql


def _build(sql: str):
    return ast_builders.build(parse_statement(sql))


def _grant(folder: str = _FOLDER, root: str = _ROOT, properties=None):
    from app.sql_frontend.file_reads import internal_file_read

    return internal_file_read(
        folder, root=root, properties=_GRANT_PROPERTIES if properties is None else properties
    )


def test_migration_import_is_refused_without_the_grant():
    # The exact statement the migration generates, written as user SQL.
    with pytest.raises(SemanticError, match="Reading with FILES"):
        _build(_migration_import_sql())


def test_migration_import_is_accepted_inside_its_grant_and_only_there():
    with _grant():
        SQLPlanner().preflight(_build(_migration_import_sql()))
    with pytest.raises(SemanticError):
        _build(_migration_import_sql())


def test_the_grant_is_cleared_when_the_import_fails():
    with pytest.raises(RuntimeError), _grant():
        raise RuntimeError("import failed")
    with pytest.raises(SemanticError):
        _build(_migration_import_sql())


_PARQUET = f"'{_FOLDER}*.parquet','format'='parquet', {_creds_sql(_CREDS)}"


@pytest.mark.parametrize(
    "sql",
    [
        # Outside the folder: another folder, a sibling sharing the prefix text,
        # a sub-folder, a climb out, the folder itself.
        f"SELECT * FROM FILES('path'='{_ROOT}/other/x.parquet','format'='parquet', "
        f"{_creds_sql(_CREDS)})",
        f"SELECT * FROM FILES('path'='{_ROOT}/run1/sales/orders2/x.parquet','format'="
        f"'parquet', {_creds_sql(_CREDS)})",
        f"SELECT * FROM FILES('path'='{_FOLDER}sub/x.parquet','format'='parquet', "
        f"{_creds_sql(_CREDS)})",
        f"SELECT * FROM FILES('path'='{_FOLDER}../../secret/x.parquet','format'='parquet', "
        f"{_creds_sql(_CREDS)})",
        f"SELECT * FROM FILES('path'='{_FOLDER}','format'='parquet', {_creds_sql(_CREDS)})",
        # The right path with a user-supplied endpoint, region or extra property.
        _migration_import_sql(creds={**_CREDS, "aws.s3.endpoint": "http://10.0.0.5:9000"}),
        _migration_import_sql(creds={**_CREDS, "aws.s3.region": "us-east-1"}),
        _migration_import_sql(creds={**_CREDS, "aws.s3.use_instance_profile": "true"}),
        _migration_import_sql(creds={k: v for k, v in _CREDS.items() if k != "aws.s3.endpoint"}),
        # A second FILES() read next to the granted one, two paths, a doubled key.
        f"SELECT * FROM FILES('path'={_PARQUET}) a "
        "JOIN FILES('path'='hdfs://10.0.0.7:8020/x','format'='parquet') b ON 1=1",
        f"SELECT * FROM FILES('path'='{_FOLDER}a.parquet','path'='s3://x/y','format'='parquet', "
        f"{_creds_sql(_CREDS)})",
        f"SELECT * FROM FILES('path'={_PARQUET}, 'format'='csv')",
        # DESC FILES is never granted.
        f"DESC FILES('path'={_PARQUET})",
    ],
)
def test_the_grant_covers_only_its_folder_and_properties(sql):
    with _grant(), pytest.raises(SemanticError, match="Reading with FILES"):
        _build(sql)


def test_the_granted_read_also_works_as_a_plain_select():
    with _grant():
        _build(f"SELECT * FROM FILES('path'={_PARQUET})")


@pytest.mark.parametrize(
    ("folder", "root"),
    [
        ("s3://stages/migration-staging/run1/x/", "s3://other-bucket/migration-staging"),
        ("s3://stages/migration-staging/", _ROOT),
        ("s3://stages/elsewhere/run1/", _ROOT),
        ("s3://stages/migration-staging/../x/", _ROOT),
        ("s3://stages/migration-staging/run1/*/", _ROOT),
        ("s3://stages/migration-staging//run1/", _ROOT),
        ("relative/folder/", "relative"),
        ("", _ROOT),
    ],
)
def test_the_grant_needs_a_concrete_folder_inside_the_transfer_location(folder, root):
    with pytest.raises(ValueError), _grant(folder, root):
        pass


def test_the_grant_properties_may_not_name_the_path():
    with pytest.raises(ValueError), _grant(properties={**_GRANT_PROPERTIES, "path": "x"}):
        pass


async def test_concurrent_migrations_and_a_user_query_do_not_share_grants(monkeypatch):
    """Two migrations hold different grants while a user query parses on the same worker."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from app.core.config import settings
    from app.sql_frontend import parser as sql_parser

    one_worker = ThreadPoolExecutor(max_workers=1, initializer=sql_parser._mark_worker_thread)
    monkeypatch.setattr(sql_parser, "_pool", lambda: one_worker)
    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 1)
    other = f"{_ROOT}/run2/sales/orders/"
    started = asyncio.Event()

    async def parse(sql):
        return await sql_parser.run_sql_cpu(len(sql), _build, sql)

    async def migration(folder, foreign):
        with _grant(folder):
            started.set()
            await asyncio.sleep(0)
            await parse(_migration_import_sql(folder))
            with pytest.raises(SemanticError):
                await parse(_migration_import_sql(foreign))
            await asyncio.sleep(0)
            await parse(_migration_import_sql(folder))

    async def user_query():
        await started.wait()
        for sql in (_migration_import_sql(_FOLDER), _migration_import_sql(other)):
            with pytest.raises(SemanticError):
                await parse(sql)

    try:
        await asyncio.gather(
            migration(_FOLDER, other), migration(other, _FOLDER), user_query()
        )
    finally:
        one_worker.shutdown(wait=True)
    # Nothing is left behind on the worker or here.
    with pytest.raises(SemanticError):
        _build(_migration_import_sql())


async def test_migration_runs_its_import_inside_the_grant(monkeypatch):
    """The service grants exactly the folder and properties of the table it imports."""
    from unittest.mock import AsyncMock

    from app.modules.migration import service as migration_service
    from app.modules.migration.service import MigrationService

    seen: list[str] = []

    async def run_on_target(self, statement, **kwargs):
        _build(statement)  # refused unless the grant is active and matches
        seen.append(statement)
        return None, type("R", (), {"affected_rows": 3})()

    async def scalar_on_target(self, statement, **kwargs):
        return None, 3.0

    repo = migration_service.migration_repo
    monkeypatch.setattr(
        repo, "list_columns", AsyncMock(return_value=[("id", "BIGINT"), ("amount", "DOUBLE")])
    )
    monkeypatch.setattr(repo, "count_rows", AsyncMock(return_value=3))
    monkeypatch.setattr(repo, "scalar", AsyncMock(return_value=3.0))
    monkeypatch.setattr(MigrationService, "_run_on_source", AsyncMock(return_value=None))
    monkeypatch.setattr(MigrationService, "_run_on_target", run_on_target)
    monkeypatch.setattr(MigrationService, "_scalar_on_target", scalar_on_target)

    result = await MigrationService()._copy_one_table(
        conn=object(),
        source_database="sales",
        target_database="sales_copy",
        table="orders",
        migration_id="run1",
        stage_root=_ROOT,
        credential_params=dict(_CREDS),
        actor="alice",
        encrypted_password="enc",
        session_id=None,
        role="analyst",
    )
    assert result.errors == [] and result.verified and result.rows_imported == 3
    assert len(seen) == 1 and _FOLDER + "*.parquet" in seen[0]
    # The grant ended with the import.
    with pytest.raises(SemanticError):
        _build(seen[0])
