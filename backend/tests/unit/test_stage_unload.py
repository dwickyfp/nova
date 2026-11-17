"""Unload to a stage: INSERT INTO FILES, SELECT ... INTO OUTFILE, EXPORT TABLE.

The target is always a stage path. Nova authorizes the active role's write
access to the stage before any storage secret is resolved, renders the
physical path and credentials only at execution, asks for confirmation, and
returns, audits and reports only the ``@stage`` form. Storage URLs, inline
credentials and broker settings are refused, and read-only contexts refuse
every unload form as before.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.common.sql_guard import guard_sql, redact_sql_credentials
from app.core.exceptions import ForbiddenSQLError
from app.modules.query.dialect.translator import StorageConfig
from app.modules.query.repository import QueryResult
from app.modules.query.sql_pipeline import prepare_stage_sql
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.ast.statements import StageAwareStatement
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner
from app.sql_frontend.stages import planned_stages
from app.sql_frontend.unload import export_location_map, logical_export_text

ACCESS_KEY = "AKIAUNLOADTESTKEY0001"
SECRET_KEY = "unload-test-secret-value-0001"

FILES_SQL = (
    "INSERT INTO FILES('path'='@exports/daily/', 'format'='parquet', "
    "'compression'='zstd', 'single'='true') SELECT * FROM sales"
)
OUTFILE_SQL = (
    "SELECT * FROM sales INTO OUTFILE '@exports/daily/sales_' FORMAT AS CSV "
    "PROPERTIES('column_separator'=',', 'max_file_size'='104857600')"
)
EXPORT_SQL = (
    "EXPORT TABLE sales PARTITION (p1) (id, amount) TO '@exports/daily/' "
    "PROPERTIES('column_separator'=',', 'timeout'='600') WITH BROKER"
)
COPY_SQL = "COPY INTO @exports/daily/ FROM (SELECT * FROM sales)"
INSERT_STAGE_SQL = "INSERT INTO @exports/daily/ SELECT * FROM sales"

ALL_FORMS = [FILES_SQL, OUTFILE_SQL, EXPORT_SQL, COPY_SQL, INSERT_STAGE_SQL]
UNLOAD_FORMS = [FILES_SQL, OUTFILE_SQL, EXPORT_SQL]


def _config() -> StorageConfig:
    return StorageConfig(
        storage_type="s3",
        endpoint="http://minio:9000",
        bucket="lake",
        base_prefix="nova/stages/abc",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        region="us-east-1",
        storage_connection="production",
    )


async def _plan(sql: str):
    statement = ast_builders.build(parse_statement(sql))
    return statement, await SQLPlanner().plan(statement, PlanningContext(database="db"))


# ── Planning: one write slot per target, no location or secret ──────────────


@pytest.mark.parametrize("sql", UNLOAD_FORMS)
async def test_each_form_plans_one_write_slot_without_location_or_secret(sql):
    statement, plan = await _plan(sql)
    assert isinstance(statement, StageAwareStatement)
    (binding,) = plan.stage_bindings
    assert binding.access == "write"
    assert binding.unload is not None and binding.unload.path.startswith("@exports/daily/")
    assert plan.stage_command == "stage_unload"
    assert plan.engine_sql.count(binding.slot) == 1
    for text in (plan.engine_sql, plan.redacted_sql, repr(plan)):
        assert "s3://" not in text and ACCESS_KEY not in text and SECRET_KEY not in text
    assert plan.requires_confirmation is True
    assert plan.effects.exports_data and plan.effects.external_io


async def test_unload_with_a_stage_source_keeps_the_source_a_read():
    _, plan = await _plan(
        "INSERT INTO FILES('path'='@exports/out/', 'format'='csv') "
        "SELECT * FROM @raw/in.csv"
    )
    accesses = sorted((binding.name, binding.access) for binding in plan.stage_bindings)
    assert accesses == [("exports", "write"), ("raw", "read")]


# ── Translation: credentials and physical path only at execution ────────────


async def _translate(sql: str) -> str:
    _, plan = await _plan(sql)
    parsed = planned_stages(plan)
    configs = {ref.start: _config() for ref in parsed.stage_refs}
    prepared = await prepare_stage_sql(
        plan.engine_sql, parsed=parsed, stage_configs_by_ref=configs
    )
    assert ACCESS_KEY not in prepared.redacted_sql and SECRET_KEY not in prepared.redacted_sql
    assert prepared.redacted_sql == redact_sql_credentials(prepared.engine_sql)
    return prepared.engine_sql


async def test_insert_into_files_is_rendered_with_path_options_and_credentials():
    sql = await _translate(FILES_SQL)
    assert sql.startswith(
        "INSERT INTO FILES('path'='s3://lake/nova/stages/abc/daily/', 'format'='parquet', "
        "'compression'='zstd', 'single'='true', "
        f"'aws.s3.access_key'='{ACCESS_KEY}', 'aws.s3.secret_key'='{SECRET_KEY}'"
    )
    assert "'aws.s3.endpoint'='http://minio:9000'" in sql
    assert sql.endswith(") SELECT * FROM sales")


async def test_outfile_gets_properties_with_credentials_and_keeps_its_format():
    sql = await _translate(OUTFILE_SQL)
    assert sql.startswith(
        "SELECT * FROM sales INTO OUTFILE 's3://lake/nova/stages/abc/daily/sales_' "
        "FORMAT AS CSV PROPERTIES('column_separator'=',', 'max_file_size'='104857600', "
        f"'aws.s3.access_key'='{ACCESS_KEY}'"
    )


async def test_outfile_without_properties_gains_a_properties_clause():
    sql = await _translate("SELECT 1 INTO OUTFILE '@exports/x/part_' FORMAT AS PARQUET")
    assert sql.startswith(
        "SELECT 1 INTO OUTFILE 's3://lake/nova/stages/abc/x/part_' FORMAT AS PARQUET "
        f"PROPERTIES('aws.s3.access_key'='{ACCESS_KEY}'"
    )


async def test_export_uses_s3a_and_puts_credentials_in_the_broker_clause():
    sql = await _translate(EXPORT_SQL)
    assert sql.startswith(
        "EXPORT TABLE sales PARTITION (p1) (id, amount) TO "
        "'s3a://lake/nova/stages/abc/daily/' "
        "PROPERTIES('column_separator'=',', 'timeout'='600') WITH BROKER ("
        f"'aws.s3.access_key'='{ACCESS_KEY}'"
    )
    assert sql.endswith(")")


async def test_export_without_broker_keyword_and_with_sync_mode():
    sql = await _translate("EXPORT TABLE sales TO '@exports/a/b/sales_' WITH SYNC MODE")
    assert sql.startswith(
        "EXPORT TABLE sales TO 's3a://lake/nova/stages/abc/a/b/sales_' WITH SYNC MODE "
        "WITH BROKER ('aws.s3.access_key'"
    )


async def test_stage_root_folder_is_written_inside_the_stage():
    sql = await _translate("INSERT INTO FILES('path'='@exports/', 'format'='csv') SELECT 1")
    assert "'path'='s3://lake/nova/stages/abc/'" in sql


# ── Refusals: storage URLs, credentials, broker, unknown options, paths ─────


@pytest.mark.parametrize(
    "target",
    [
        "s3://bucket/unload/",
        "s3a://bucket/unload/",
        "hdfs://10.0.0.1:9000/unload/",
        "file:///home/ubuntu/csvfile/",
        "oss://bucket/x/",
        "unload/data1",
        "/tmp/out/",
    ],
)
@pytest.mark.parametrize(
    "template",
    [
        "INSERT INTO FILES('path'='{t}', 'format'='csv') SELECT 1",
        "SELECT 1 INTO OUTFILE '{t}' FORMAT AS CSV",
        "EXPORT TABLE sales TO '{t}' WITH BROKER",
    ],
)
def test_storage_urls_and_local_paths_are_refused(template, target):
    with pytest.raises(SemanticError, match="writes only to a stage") as raised:
        ast_builders.build(parse_statement(template.format(t=target)))
    assert target not in str(raised.value)


@pytest.mark.parametrize(
    "target",
    ["@exports", "@exports/../other/", "@exports/a//b/", "@exports/*.csv", "@exports/a b/"],
)
def test_stage_paths_must_stay_inside_a_stage_folder(target):
    with pytest.raises(SemanticError):
        ast_builders.build(
            parse_statement(f"INSERT INTO FILES('path'='{target}', 'format'='csv') SELECT 1")
        )


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO FILES('path'='@exports/a/', 'format'='csv', "
        f"'aws.s3.access_key'='{ACCESS_KEY}', 'aws.s3.secret_key'='{SECRET_KEY}') SELECT 1",
        "INSERT INTO FILES('path'='@exports/a/', 'format'='csv', "
        "'aws.s3.endpoint'='http://evil:9000') SELECT 1",
        "INSERT INTO FILES('path'='@exports/a/', 'format'='parquet', "
        f"'username'='u', 'password'='{SECRET_KEY}') SELECT 1",
        "SELECT 1 INTO OUTFILE '@exports/a/x_' FORMAT AS CSV "
        f"PROPERTIES('aws.s3.secret_key'='{SECRET_KEY}')",
        "SELECT 1 INTO OUTFILE '@exports/a/x_' PROPERTIES('broker.name'='b')",
    ],
)
def test_inline_credentials_and_storage_options_are_refused_without_echoing_them(sql):
    with pytest.raises(SemanticError, match="takes no storage location, credential") as raised:
        ast_builders.build(parse_statement(sql))
    assert SECRET_KEY not in str(raised.value) and ACCESS_KEY not in str(raised.value)
    assert "access_key" not in str(raised.value)


@pytest.mark.parametrize(
    "sql",
    [
        f"EXPORT TABLE sales TO '@exports/a/' WITH BROKER ('aws.s3.secret_key'='{SECRET_KEY}')",
        "EXPORT TABLE sales TO '@exports/a/' WITH BROKER 'hdfs_broker'",
    ],
)
def test_export_refuses_broker_name_and_properties(sql):
    with pytest.raises(SemanticError, match="no broker name or broker properties") as raised:
        ast_builders.build(parse_statement(sql))
    assert SECRET_KEY not in str(raised.value)


@pytest.mark.parametrize(
    ("sql", "option"),
    [
        (
            "INSERT INTO FILES('path'='@exports/a/', 'format'='csv', "
            "'csv.row_delimitor'='\\n') SELECT 1",
            "csv.row_delimitor",
        ),
        ("SELECT 1 INTO OUTFILE '@exports/a/x_' PROPERTIES('schema'='x')", "schema"),
        ("EXPORT TABLE t TO '@exports/a/' PROPERTIES('format'='parquet')", "format"),
    ],
)
def test_unknown_options_are_refused_with_the_supported_list(sql, option):
    with pytest.raises(SemanticError, match=f"does not support the option '{option}'"):
        ast_builders.build(parse_statement(sql))


def test_duplicate_options_and_missing_path_are_refused():
    with pytest.raises(SemanticError, match="more than once"):
        ast_builders.build(parse_statement(
            "INSERT INTO FILES('path'='@e/a/', 'format'='csv', 'FORMAT'='csv') SELECT 1"
        ))
    with pytest.raises(SemanticError, match="more than once"):
        ast_builders.build(parse_statement(
            "INSERT INTO FILES('path'='@e/a/', 'path'='@e/b/', 'format'='csv') SELECT 1"
        ))
    with pytest.raises(SemanticError, match="needs a 'path'"):
        ast_builders.build(parse_statement("INSERT INTO FILES('format'='csv') SELECT 1"))


def test_reading_native_files_is_unchanged():
    statement = ast_builders.build(parse_statement(
        "SELECT * FROM FILES('path'='s3://bucket/x.parquet', 'format'='parquet')"
    ))
    assert not isinstance(statement, StageAwareStatement)


# ── Confirmation ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("sql", ALL_FORMS)
def test_every_unload_form_requires_confirmation(sql):
    analysis = SQLPlanner().preflight(ast_builders.build(parse_statement(sql)))
    assert analysis.requires_confirmation is True
    assert analysis.effects.exports_data is True
    assert analysis.effects.mutates is True


def test_reading_a_stage_needs_no_confirmation():
    analysis = SQLPlanner().preflight(
        ast_builders.build(parse_statement("SELECT * FROM @exports/a.csv"))
    )
    assert analysis.requires_confirmation is False


# ── Read-only contexts keep refusing every form ─────────────────────────────


@pytest.mark.parametrize("sql", ALL_FORMS)
def test_read_only_contexts_refuse_every_unload_form(sql):
    with pytest.raises(ForbiddenSQLError, match="not read-only"):
        guard_sql(sql)


@pytest.mark.parametrize("sql", ALL_FORMS)
def test_the_worksheet_guard_passes_unload_to_the_frontend(sql):
    guard_sql(sql, allow_stage_export=True)


def test_raw_export_is_refused_even_by_the_guard():
    with pytest.raises(ForbiddenSQLError, match="EXPORT TABLE"):
        guard_sql("EXPORT TABLE t TO 's3a://bucket/x/' WITH BROKER")
    with pytest.raises(ForbiddenSQLError, match="EXPORT TABLE"):
        guard_sql("export /* x */ table t TO 's3a://bucket/x/'")
    guard_sql("SELECT 'EXPORT TABLE t' AS note")


@pytest.mark.parametrize("sql", ALL_FORMS)
def test_assistant_read_only_policy_refuses_unload(sql):
    from app.modules.assistant.tools.policy import is_allowed_statement

    assert is_allowed_statement(sql) is False


# ── Execution: write authorization before secrets, redacted output ───────────


class _Engine:
    def __init__(self):
        self.calls: list[str] = []

    async def execute_as_user(self, sql, **kwargs):
        self.calls.append(sql)
        return QueryResult(affected_rows=6, executed_sql=sql)


def _service(monkeypatch, *, deny: set[str] = frozenset()):
    from app.modules.query import service as service_module
    from app.modules.query.service import QueryService
    from app.modules.stages.access import StageAccessDenied
    from tests.unit.stage_metadata_fakes import install_stages

    checks: list[str] = []
    resolved: list[str] = []
    audits: list[dict] = []

    async def audit(**kwargs):
        audits.append(kwargs)
        return "qid"

    async def access(stage, *, action, **kwargs):
        checks.append(action)
        if action in deny:
            raise StageAccessDenied("Stage access denied")

    def credentials(name):
        resolved.append(name)
        return ACCESS_KEY, SECRET_KEY

    monkeypatch.setattr(service_module, "write_audit_log", audit)
    monkeypatch.setattr(service_module, "decrypt_password", lambda value: "pw")
    monkeypatch.setattr(service_module, "check_stage_access", access)
    monkeypatch.setattr(
        service_module,
        "require_storage_connection",
        lambda name: SimpleNamespace(
            type="s3", endpoint="http://minio:9000", bucket="lake", region="us-east-1"
        ),
    )
    monkeypatch.setattr(service_module, "resolve_storage_credentials", credentials)
    install_stages(monkeypatch, [{
        "id": "s1", "name": "exports", "database_name": "db", "schema_name": "db",
        "storage_connection": "production", "base_prefix": "nova/stages/s1",
    }])
    service = QueryService()
    engine = _Engine()
    service._repo = engine
    return service, engine, SimpleNamespace(
        checks=checks, resolved=resolved, audits=audits
    )


async def _run(service, sql, **kwargs):
    [result] = await service.execute_statements(
        sql=sql, username="alice", encrypted_password="enc", role="analyst",
        database="db", schema="db", **kwargs,
    )
    return result


@pytest.mark.usefixtures("fake_metadata_db")
@pytest.mark.parametrize("sql", UNLOAD_FORMS)
async def test_unload_runs_after_write_authorization_and_returns_only_stage_paths(
    monkeypatch, sql
):
    service, engine, seen = _service(monkeypatch)
    result = await _run(service, sql, allow_stage_export=True, confirm_destructive=True)
    assert result.error is None, result.error
    assert seen.checks == ["write"]
    assert seen.resolved == ["production"]
    (engine_sql,) = engine.calls
    assert ACCESS_KEY in engine_sql and "lake/nova/stages/s1/daily/" in engine_sql
    shown = " ".join(str(value) for value in (
        result.executed_sql, result.original_sql, result.warnings, result.error,
    ))
    audit_text = repr(seen.audits)
    for text in (shown, audit_text):
        assert ACCESS_KEY not in text and SECRET_KEY not in text
        assert "s3://lake" not in text and "s3a://lake" not in text
    assert "@exports/daily/" in result.executed_sql


@pytest.mark.usefixtures("fake_metadata_db")
@pytest.mark.parametrize("sql", UNLOAD_FORMS)
async def test_unload_without_stage_write_access_is_refused_before_secrets(monkeypatch, sql):
    service, engine, seen = _service(monkeypatch, deny={"write"})
    result = await _run(service, sql, allow_stage_export=True, confirm_destructive=True)
    assert result.error == "Stage access denied"
    assert seen.checks == ["write"]
    assert seen.resolved == [] and engine.calls == []
    assert "__nova_stage_" not in str(result.executed_sql)


@pytest.mark.usefixtures("fake_metadata_db")
async def test_read_access_alone_does_not_allow_an_unload(monkeypatch):
    service, engine, seen = _service(monkeypatch, deny={"write"})
    ok = await _run(service, "SELECT * FROM @exports/daily/a.parquet", allow_stage_export=True)
    assert ok.error is None and seen.checks == ["read"]
    refused = await _run(service, FILES_SQL, allow_stage_export=True, confirm_destructive=True)
    assert refused.error == "Stage access denied"


@pytest.mark.usefixtures("fake_metadata_db")
@pytest.mark.parametrize("sql", UNLOAD_FORMS)
async def test_unload_without_confirmation_asks_for_it(monkeypatch, sql):
    service, engine, seen = _service(monkeypatch)
    result = await _run(service, sql, allow_stage_export=True)
    assert result.needs_confirmation is True
    assert engine.calls == [] and seen.checks == [] and seen.resolved == []


@pytest.mark.usefixtures("fake_metadata_db")
@pytest.mark.parametrize("sql", ALL_FORMS)
async def test_contexts_without_stage_export_refuse(monkeypatch, sql):
    service, engine, seen = _service(monkeypatch)
    result = await _run(service, sql, confirm_destructive=True)
    assert result.error and "not read-only" in result.error
    assert engine.calls == [] and seen.resolved == []


async def test_executor_refuses_a_write_slot_outside_an_export_context():
    from app.sql_frontend.context import ExecutionContext
    from app.sql_frontend.execution.adapters import FeatureAdapters

    statement, plan = await _plan(FILES_SQL)
    adapters = object.__new__(FeatureAdapters)
    context = ExecutionContext("alice", role="analyst", statements={0: statement})
    with pytest.raises(ForbiddenSQLError, match="Writing to a stage is not allowed"):
        await adapters.execute_engine_sql(plan, context)


# ── SHOW EXPORT and error text name stages, not storage ─────────────────────


def test_show_export_paths_map_to_stages_and_others_are_hidden():
    location_map = [
        ("s3a://lake/nova/stages/s1", "@exports"),
        ("s3://lake/nova/stages/s1", "@exports"),
    ]
    assert logical_export_text("s3a://lake/nova/stages/s1/daily/", location_map) == (
        "@exports/daily/"
    )
    assert logical_export_text("s3a://other/bucket/x/", location_map) == "<external location>"
    assert logical_export_text(
        "msg:errors while check if path s3a://lake/nova/stages/s1x/a exist", location_map
    ) == "msg:errors while check if path <external location> exist"


@pytest.mark.usefixtures("fake_metadata_db")
async def test_export_location_map_covers_readable_stages_in_both_schemes(monkeypatch):
    from app.core import config as config_module
    from tests.unit.stage_metadata_fakes import install_stages

    install_stages(monkeypatch, [{
        "id": "s1", "name": "exports", "database_name": "db", "schema_name": "db",
        "storage_connection": "production", "base_prefix": "nova/stages/s1",
    }])

    async def allowed(*args, **kwargs):
        return None

    from app.modules.stages import metadata_service

    monkeypatch.setattr(metadata_service, "check_stage_access", allowed, raising=False)
    monkeypatch.setattr(
        config_module, "require_storage_connection", lambda name: SimpleNamespace(bucket="lake")
    )
    mapping = dict(await export_location_map("alice", "analyst"))
    assert mapping == {
        "s3://lake/nova/stages/s1": "@exports",
        "s3a://lake/nova/stages/s1": "@exports",
    }


def test_storage_errors_name_the_stage_for_export_paths():
    from app.sql_frontend.execution.adapters import _logical_location, _stage_location_map

    ref = SimpleNamespace(start=0, stage_name="exports")
    location_map = _stage_location_map(SimpleNamespace(stage_refs=[ref]), {0: _config()})
    message = "path s3a://lake/nova/stages/abc/daily/x.csv exist"
    assert _logical_location(message, location_map) == "path @exports/daily/x.csv exist"


@pytest.mark.parametrize(
    ("sql", "suffix"),
    [
        (COPY_SQL, " (SELECT * FROM sales)"),
        ("COPY INTO @exports/daily/ FROM sales", " SELECT * FROM sales"),
        (INSERT_STAGE_SQL, " SELECT * FROM sales"),
    ],
)
async def test_copy_and_insert_into_a_stage_write_csv_with_credentials_at_execution(sql, suffix):
    engine_sql = await _translate(sql)
    assert engine_sql.startswith(
        "INSERT INTO FILES('path'='s3://lake/nova/stages/abc/daily', 'format'='csv', "
        f"'aws.s3.access_key'='{ACCESS_KEY}'"
    )
    assert engine_sql.endswith(")" + suffix)


def test_an_unload_result_row_names_the_stage_not_the_bucket():
    import asyncio

    from app.modules.query.dialect.parser import CommandType
    from app.sql_frontend.context import ExecutionContext
    from app.sql_frontend.execution.adapters import FeatureAdapters

    class Repo:
        async def execute_as_user(self, **kwargs):
            return QueryResult(
                columns=["JobId", "Path"],
                rows=[[1, "s3a://lake/nova/stages/abc/sync/"]],
                row_count=1,
                executed_sql=kwargs["sql"],
            )

    async def audit(**kwargs):
        return None

    adapters = object.__new__(FeatureAdapters)
    adapters._repo = Repo()
    adapters._audit_sink = audit
    adapters._decrypt = lambda _value: "pw"
    context = ExecutionContext(username="u", encrypted_password="e", role="r", statements={})
    result = asyncio.run(
        adapters._run_on_engine(
            SimpleNamespace(source_key=0), context,
            sql="EXPORT TABLE t TO '@exports/sync/' WITH SYNC MODE",
            executed_sql="EXPORT ...", audit_sql="EXPORT ...", warnings=[],
            location_map=[("s3a://lake/nova/stages/abc", "@exports")],
            parsed=SimpleNamespace(command_type=CommandType.STAGE_UNLOAD),
        )
    )
    assert result.rows == [[1, "@exports/sync/"]]
