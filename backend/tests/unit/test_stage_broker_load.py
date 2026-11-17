"""Broker Load from stages, backup repositories on stages, and SUBMIT TASK.

Broker Load reads only stage paths: Nova authorizes the active role's read
access to every stage before any storage secret is resolved, renders the
physical paths and ``WITH BROKER (<credentials>)`` only at execution, and
returns, audits and shows (``SHOW LOAD``, ``information_schema.loads``) only
``@stage`` paths. A repository lives only in a stage folder and every
repository, backup and restore statement needs ``ACCOUNTADMIN``. ``SUBMIT TASK``
becomes a Nova task owned by the caller's active role.
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
from app.sql_frontend.ast.statements import CreateTaskStatement, StageAwareStatement
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner
from app.sql_frontend.stages import planned_stages

ACCESS_KEY = "AKIALOADTESTKEY00001"
SECRET_KEY = "load-test-secret-value-0001"

LOAD_SQL = (
    'LOAD LABEL db.label1 (DATA INFILE("@raw/input/file1.csv") INTO TABLE table1 '
    'COLUMNS TERMINATED BY "," (id, name, score), '
    'DATA INFILE("@raw/input/file2.csv") INTO TABLE table2 COLUMNS TERMINATED BY "," '
    '(id, city)) WITH BROKER PROPERTIES ("timeout" = "3600")'
)
REPOSITORY_SQL = 'CREATE REPOSITORY repo1 WITH BROKER ON LOCATION "@backups/nightly/"'


def _config(access_key: str = ACCESS_KEY) -> StorageConfig:
    return StorageConfig(
        storage_type="s3",
        endpoint="http://minio:9000",
        bucket="lake",
        base_prefix="nova/stages/abc",
        access_key=access_key,
        secret_key=SECRET_KEY,
        region="us-east-1",
        storage_connection="production",
    )


def _build(sql: str):
    return ast_builders.build(parse_statement(sql))


async def _plan(sql: str):
    statement = _build(sql)
    return statement, await SQLPlanner().plan(statement, PlanningContext(database="db"))


async def _translate(sql: str, configs=None) -> tuple[str, str]:
    _, plan = await _plan(sql)
    parsed = planned_stages(plan)
    configs = configs or {ref.start: _config() for ref in parsed.stage_refs}
    prepared = await prepare_stage_sql(plan.engine_sql, parsed=parsed, stage_configs_by_ref=configs)
    assert prepared.redacted_sql == redact_sql_credentials(prepared.engine_sql)
    assert ACCESS_KEY not in prepared.redacted_sql and SECRET_KEY not in prepared.redacted_sql
    return prepared.engine_sql, prepared.redacted_sql


# ── Broker Load: every documented clause and property is accepted ───────────

DOCUMENTED_LOADS = [
    LOAD_SQL,
    # Every data_desc clause in one description.
    'LOAD LABEL l2 (DATA INFILE("@raw/a.csv", "@raw/dir/*") NEGATIVE INTO TABLE t '
    "PARTITION (p1, p2) COLUMNS TERMINATED BY \"\\\\t\" ROWS TERMINATED BY \"\\\\n\" "
    'FORMAT AS "CSV" (skip_header = 1 trim_space = true enclose = "\\"" escape = "\\\\") '
    "(c1, c2, tmp) COLUMNS FROM PATH AS (city) SET (c3 = tmp + 1) WHERE c1 > 0)",
    'LOAD LABEL l3 (DATA INFILE("@raw/x.csv") INTO TABLE t TEMPORARY PARTITION (tp1))',
    'LOAD LABEL l4 (DATA INFILE("@raw/p/file.parquet") INTO TABLE t FORMAT AS "parquet")',
    'LOAD LABEL l5 (DATA INFILE("@raw/o/file.orc") INTO TABLE t FORMAT AS "orc")',
    'LOAD LABEL l6 (DATA INFILE("@raw/j/file.json") INTO TABLE t FORMAT AS "json" '
    '(category, price, author)) PROPERTIES ("jsonpaths" = "[\\"$.category\\",\\"$.price\\"]", '
    '"strip_outer_array" = "true", "json_root" = "$.RECORDS")',
    'LOAD LABEL l7 (DATA INFILE("@raw/dt=2020-02-17 00%3A00%3A00/example12.csv") '
    "INTO TABLE t COLUMNS FROM PATH AS (data_time))",
    # Every documented job property.
    'LOAD LABEL l8 (DATA INFILE("@raw/x.csv") INTO TABLE t) PROPERTIES ('
    '"timeout" = "60", "max_filter_ratio" = "0.1", "log_rejected_record_num" = "-1", '
    '"load_mem_limit" = "1073741824", "strict_mode" = "true", "timezone" = "UTC", '
    '"priority" = "HIGH", "partial_update" = "true", "partial_update_mode" = "row", '
    '"merge_condition" = "ts", "warehouse" = "default_warehouse")',
    'LOAD LABEL l9 (DATA INFILE("@db1.raw/x.csv") INTO TABLE t)',
    'LOAD LABEL l10 (DATA INFILE("@db1.sch.raw/{a,b}/[0-9]?.csv") INTO TABLE t)',
]


@pytest.mark.parametrize("sql", DOCUMENTED_LOADS)
async def test_documented_load_forms_plan_read_slots_without_location_or_secret(sql):
    statement, plan = await _plan(sql)
    assert isinstance(statement, StageAwareStatement)
    assert plan.stage_command == "stage_broker_load"
    assert plan.stage_bindings and all(b.access == "read" for b in plan.stage_bindings)
    kinds = [binding.unload.kind for binding in plan.stage_bindings]
    assert kinds.count("load_broker") == 1 and kinds[-1] == "load_broker"
    for binding in plan.stage_bindings:
        assert plan.engine_sql.count(binding.slot) == 1
    for text in (plan.engine_sql, plan.redacted_sql, repr(plan)):
        assert "s3://" not in text and ACCESS_KEY not in text and SECRET_KEY not in text
    assert plan.effects.writes_data and plan.effects.external_io


async def test_a_load_is_a_write_without_confirmation_and_negative_is_confirmed():
    _, plan = await _plan(LOAD_SQL)
    assert plan.requires_confirmation is False
    _, negative = await _plan(
        'LOAD LABEL l (DATA INFILE("@raw/a.csv") NEGATIVE INTO TABLE agg)'
    )
    assert negative.requires_confirmation is True and negative.effects.updates_rows


async def test_paths_and_broker_are_rendered_only_at_translation():
    engine_sql, redacted = await _translate(LOAD_SQL)
    assert engine_sql.startswith(
        "LOAD LABEL db.label1 (DATA INFILE('s3://lake/nova/stages/abc/input/file1.csv') "
        'INTO TABLE table1 COLUMNS TERMINATED BY "," (id, name, score), '
        "DATA INFILE('s3://lake/nova/stages/abc/input/file2.csv')"
    )
    assert f"WITH BROKER ('aws.s3.access_key'='{ACCESS_KEY}', " in engine_sql
    assert "'aws.s3.endpoint'='http://minio:9000'" in engine_sql
    assert engine_sql.endswith(') PROPERTIES ("timeout" = "3600")')
    assert "'aws.s3.secret_key'='***'" in redacted


async def test_a_load_without_with_broker_gains_the_broker_clause():
    engine_sql, _ = await _translate('LOAD LABEL l (DATA INFILE("@raw/d/*") INTO TABLE t)')
    assert engine_sql.startswith(
        "LOAD LABEL l (DATA INFILE('s3://lake/nova/stages/abc/d/*') INTO TABLE t) "
        f"WITH BROKER ('aws.s3.access_key'='{ACCESS_KEY}'"
    )


async def test_files_on_two_storage_credentials_are_refused():
    sql = (
        'LOAD LABEL l (DATA INFILE("@a/x.csv") INTO TABLE t, '
        'DATA INFILE("@b/y.csv") INTO TABLE u)'
    )
    _, plan = await _plan(sql)
    parsed = planned_stages(plan)
    configs = {
        ref.start: _config(ACCESS_KEY if ref.stage_name == "a" else "AKIAOTHERKEY0000002")
        for ref in parsed.stage_refs
    }
    with pytest.raises(ValueError, match="same storage connection"):
        await prepare_stage_sql(plan.engine_sql, parsed=parsed, stage_configs_by_ref=configs)


# ── Broker Load refusals ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "s3://bucket/input/file1.csv",
        "s3a://bucket_s3/input/*",
        "hdfs://10.0.0.1:9000/user/starrocks/file1.csv",
        "gs://bucket_gcs/input/file1.csv",
        "wasbs://c@a.blob.core.windows.net/p/f.csv",
        "file:///home/data/file.csv",
        "input/file1.csv",
    ],
)
def test_storage_urls_and_local_paths_are_refused(path):
    with pytest.raises(SemanticError, match="reads files only from a stage") as raised:
        _build(f'LOAD LABEL l (DATA INFILE("{path}") INTO TABLE t)')
    assert path not in str(raised.value)


@pytest.mark.parametrize(
    "path",
    [
        "@raw", "@raw/", "@raw/../x.csv", "@raw/a//b.csv", "@raw/./x.csv",
        "@raw/a\\\\b.csv", "@raw/a'b.csv",
    ],
)
def test_paths_must_name_files_inside_a_stage(path):
    with pytest.raises(SemanticError):
        _build(f'LOAD LABEL l (DATA INFILE("{path}") INTO TABLE t)')


@pytest.mark.parametrize(
    "tail",
    [
        f'WITH BROKER ("aws.s3.access_key" = "{ACCESS_KEY}", "aws.s3.secret_key" = "{SECRET_KEY}")',
        'WITH BROKER "hdfs_broker"',
        f'WITH BROKER hdfs_broker ("password" = "{SECRET_KEY}")',
    ],
)
def test_broker_names_and_credentials_are_refused_without_echoing_them(tail):
    with pytest.raises(SemanticError, match="no broker name or broker properties") as raised:
        _build(f'LOAD LABEL l (DATA INFILE("@raw/x.csv") INTO TABLE t) {tail}')
    assert SECRET_KEY not in str(raised.value) and ACCESS_KEY not in str(raised.value)


@pytest.mark.parametrize(
    "key",
    ["aws.s3.secret_key", "aws.s3.endpoint", "fs.s3a.access.key", "username",
     "bos_secret_accesskey"],
)
def test_credential_properties_are_refused_without_echoing_them(key):
    with pytest.raises(SemanticError, match="no storage location, credential") as raised:
        _build(
            'LOAD LABEL l (DATA INFILE("@raw/x.csv") INTO TABLE t) '
            f'PROPERTIES ("{key}" = "{SECRET_KEY}")'
        )
    assert SECRET_KEY not in str(raised.value) and key not in str(raised.value)


def test_unknown_and_duplicate_properties_are_refused():
    with pytest.raises(SemanticError, match="does not support the property 'colour'"):
        _build('LOAD LABEL l (DATA INFILE("@raw/x.csv") INTO TABLE t) PROPERTIES ("colour" = "1")')
    with pytest.raises(SemanticError, match="more than once"):
        _build(
            'LOAD LABEL l (DATA INFILE("@raw/x.csv") INTO TABLE t) '
            'PROPERTIES ("timeout" = "1", "TIMEOUT" = "2")'
        )


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        (
            'LOAD LABEL l (DATA INFILE("@raw/x.csv") INTO TABLE t) WITH RESOURCE "spark0"',
            "Spark Load",
        ),
        ("LOAD LABEL l (DATA FROM TABLE hive_t INTO TABLE t) WITH BROKER", "DATA FROM TABLE"),
    ],
)
def test_spark_and_hive_table_forms_are_refused(sql, message):
    with pytest.raises(SemanticError, match=message):
        _build(sql)


def test_the_guard_passes_loads_and_load_management():
    for sql in (LOAD_SQL, "SHOW LOAD FROM db", 'CANCEL LOAD FROM db WHERE LABEL = "l"',
                'ALTER LOAD FOR db.l PROPERTIES ("priority" = "HIGH")'):
        guard_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SHOW LOAD FROM db WHERE LABEL = 'l'",
        "CANCEL LOAD FROM db WHERE LABEL = 'l'",
        "ALTER LOAD FOR db.l PROPERTIES ('priority' = 'HIGH')",
    ],
)
def test_load_management_statements_stay_native(sql):
    assert not isinstance(_build(sql), StageAwareStatement)


# ── Execution: read authorization before secrets, redacted output ───────────


class _Engine:
    def __init__(self, result: QueryResult | None = None):
        self.calls: list[str] = []
        self.result = result

    async def execute_as_user(self, sql, **kwargs):
        self.calls.append(sql)
        if self.result is not None:
            self.result.executed_sql = sql
            return self.result
        return QueryResult(affected_rows=0, executed_sql=sql)


def _service(monkeypatch, *, deny: set[str] = frozenset(), result: QueryResult | None = None):
    from app.modules.query import service as service_module
    from app.modules.query.service import QueryService
    from app.modules.stages.access import StageAccessDenied
    from tests.unit.stage_metadata_fakes import install_stages

    checks: list[tuple[str, str]] = []
    resolved: list[str] = []
    audits: list[dict] = []

    async def audit(**kwargs):
        audits.append(kwargs)
        return "qid"

    async def access(stage, *, action, **kwargs):
        checks.append((stage["name"], action))
        if action in deny or stage["name"] in deny:
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
    rows = [
        {"id": f"s{i}", "name": name, "database_name": "db", "schema_name": "db",
         "storage_connection": "production", "base_prefix": f"nova/stages/s{i}"}
        for i, name in enumerate(("raw", "other", "backups"))
    ]
    install_stages(monkeypatch, rows)
    service = QueryService()
    engine = _Engine(result)
    service._repo = engine
    return service, engine, SimpleNamespace(checks=checks, resolved=resolved, audits=audits)


async def _run(service, sql, role="analyst", **kwargs):
    [result] = await service.execute_statements(
        sql=sql, username="alice", encrypted_password="enc", role=role,
        database="db", schema="db", **kwargs,
    )
    return result


@pytest.mark.usefixtures("fake_metadata_db")
async def test_load_runs_after_read_authorization_and_returns_only_stage_paths(monkeypatch):
    service, engine, seen = _service(monkeypatch)
    result = await _run(service, LOAD_SQL, allow_stage_export=True)
    assert result.error is None, result.error
    assert seen.checks == [("raw", "read")] * 3
    assert seen.resolved == ["production"] * 3
    (engine_sql,) = engine.calls
    assert ACCESS_KEY in engine_sql and "s3://lake/nova/stages/s0/input/file1.csv" in engine_sql
    shown = " ".join(str(v) for v in (result.executed_sql, result.original_sql, result.warnings))
    for text in (shown, repr(seen.audits)):
        assert ACCESS_KEY not in text and SECRET_KEY not in text
        assert "s3://lake" not in text and "minio:9000" not in text
    assert "@raw/input/file1.csv" in result.executed_sql


@pytest.mark.usefixtures("fake_metadata_db")
async def test_load_without_read_access_is_refused_before_any_secret(monkeypatch):
    service, engine, seen = _service(monkeypatch, deny={"other"})
    sql = (
        'LOAD LABEL l (DATA INFILE("@raw/x.csv") INTO TABLE t, '
        'DATA INFILE("@other/y.csv") INTO TABLE u)'
    )
    result = await _run(service, sql, allow_stage_export=True)
    assert result.error == "Stage access denied"
    assert ("other", "read") in seen.checks
    assert seen.resolved == [] and engine.calls == []


@pytest.mark.usefixtures("fake_metadata_db")
async def test_a_load_reads_and_needs_no_stage_write_access(monkeypatch):
    service, engine, seen = _service(monkeypatch, deny={"write"})
    result = await _run(service, 'LOAD LABEL l (DATA INFILE("@raw/x.csv") INTO TABLE t)')
    assert result.error is None, result.error
    assert {action for _, action in seen.checks} == {"read"}


@pytest.mark.usefixtures("fake_metadata_db")
async def test_load_errors_name_the_stage(monkeypatch):
    service, engine, _ = _service(
        monkeypatch,
        result=QueryResult(error="No files were found matching s3://lake/nova/stages/s0/x.csv"),
    )
    result = await _run(service, 'LOAD LABEL l (DATA INFILE("@raw/x.csv") INTO TABLE t)')
    assert result.error == "No files were found matching @raw/x.csv"


# ── SHOW LOAD, information_schema.loads: stage paths only ───────────────────


@pytest.mark.parametrize(
    ("sql", "database"),
    [
        ("SHOW LOAD FROM db", "db"),
        ("SHOW LOAD WARNINGS FROM db", "db"),
        ("SELECT * FROM information_schema.loads", "db"),
        ("SELECT error_msg FROM `information_schema`.`loads` WHERE label = 'x'", "db"),
        ("SELECT * FROM default_catalog.information_schema.load_tracking_logs", "db"),
        ("SELECT * FROM loads", "information_schema"),
        ("SHOW REPOSITORIES", None),
        ("SHOW BACKUP FROM db", None),
        ("SHOW RESTORE FROM db", None),
        ("SHOW SNAPSHOT ON repo1", None),
        ("SHOW EXPORT FROM db", None),
    ],
)
def test_statements_that_show_storage_paths_are_recognised(sql, database):
    from app.sql_frontend.execution.adapters import _shows_exports

    assert _shows_exports(_build(sql), database)


@pytest.mark.parametrize(
    ("sql", "database"),
    [("SELECT * FROM loads", "db"), ("SELECT 'information_schema.loads'", "db"),
     ("SELECT * FROM t", "db")],
)
def test_other_queries_keep_their_values(sql, database):
    from app.sql_frontend.execution.adapters import _shows_exports

    assert not _shows_exports(_build(sql), database)


@pytest.mark.usefixtures("fake_metadata_db")
async def test_show_load_rows_name_stages_the_caller_may_read(monkeypatch):
    from app.core import config as config_module

    service, engine, _ = _service(
        monkeypatch,
        result=QueryResult(
            columns=["Label", "ErrorMsg"],
            rows=[["l1", "No files at 's3://lake/nova/stages/s0/x.csv'"],
                  ["l2", "No files at 's3://elsewhere/data/y.csv'"]],
            row_count=2,
        ),
    )
    monkeypatch.setattr(
        config_module, "require_storage_connection", lambda name: SimpleNamespace(bucket="lake")
    )
    from app.modules.stages import metadata_service

    async def allowed(*args, **kwargs):
        return None

    monkeypatch.setattr(metadata_service, "check_stage_access", allowed, raising=False)
    result = await _run(service, "SHOW LOAD FROM db")
    assert result.rows == [
        ["l1", "No files at '@raw/x.csv'"],
        ["l2", "No files at '<external location>'"],
    ]


async def test_streamed_show_load_rows_are_mapped_too():
    from app.sql_frontend.execution.adapters import _StoragePathSink

    received = []

    class Sink:
        async def begin(self, columns, types):
            received.append(columns)

        async def rows(self, rows):
            received.extend(rows)

    sink = _StoragePathSink(Sink(), [("s3://lake/nova/stages/s0", "@raw")])
    await sink.begin(["ErrorMsg"], ("varchar",))
    await sink.rows([["at s3://lake/nova/stages/s0/a.csv"], [1]])
    assert received == [["ErrorMsg"], ["at @raw/a.csv"], [1]]


# ── Backup repositories on stages ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("sql", "access"),
    [
        (REPOSITORY_SQL, "write"),
        ('CREATE REPOSITORY r WITH BROKER ON LOCATION "@backups/nightly"', "write"),
        ('CREATE READ ONLY REPOSITORY r WITH BROKER ON LOCATION "@backups/a/b/"', "read"),
        ('CREATE REPOSITORY r WITH BROKER ON LOCATION "@db.sch.backups/x/"', "write"),
    ],
)
async def test_repository_location_is_one_stage_slot(sql, access):
    statement, plan = await _plan(sql)
    assert isinstance(statement, StageAwareStatement)
    (binding,) = plan.stage_bindings
    assert binding.access == access and binding.unload.kind == "repository"
    assert plan.stage_command == "stage_repository"
    for text in (plan.engine_sql, repr(plan)):
        assert "s3a://" not in text and SECRET_KEY not in text


async def test_repository_is_rendered_with_location_and_credentials_at_translation():
    engine_sql, redacted = await _translate(REPOSITORY_SQL)
    assert engine_sql.startswith(
        "CREATE REPOSITORY repo1 WITH BROKER ON LOCATION "
        "'s3a://lake/nova/stages/abc/nightly' PROPERTIES("
        f"'aws.s3.access_key'='{ACCESS_KEY}', 'aws.s3.secret_key'='{SECRET_KEY}'"
    )
    assert "'aws.s3.secret_key'='***'" in redacted


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ('CREATE REPOSITORY r WITH BROKER ON LOCATION "s3a://bucket_s3/backup"',
         "only in a stage"),
        ('CREATE REPOSITORY r WITH BROKER ON LOCATION "hdfs://x:9000/repo"', "only in a stage"),
        ('CREATE REPOSITORY r WITH BROKER ON LOCATION "@backups/"', "needs a folder"),
        ('CREATE REPOSITORY r WITH BROKER ON LOCATION "@backups/../x/"', "no empty"),
        (
            'CREATE REPOSITORY r WITH BROKER ON LOCATION "@backups/x/" '
            f'PROPERTIES ("aws.s3.secret_key" = "{SECRET_KEY}")',
            "no broker name or properties",
        ),
        ('CREATE REPOSITORY r WITH BROKER b1 ON LOCATION "@backups/x/"',
         "no broker name or properties"),
    ],
)
def test_repository_refusals(sql, message):
    with pytest.raises(SemanticError, match=message) as raised:
        _build(sql)
    assert SECRET_KEY not in str(raised.value)


BACKUP_STATEMENTS = [
    REPOSITORY_SQL,
    "DROP REPOSITORY repo1",
    "SHOW REPOSITORIES",
    "BACKUP DATABASE db SNAPSHOT s1 TO repo1 ON (TABLE t)",
    "CANCEL BACKUP FROM db",
    "SHOW BACKUP FROM db",
    "RESTORE SNAPSHOT db.s1 FROM repo1 ON (TABLE t) PROPERTIES ('backup_timestamp' = 'x')",
    "CANCEL RESTORE FROM db",
    "SHOW RESTORE FROM db",
    "SHOW SNAPSHOT ON repo1",
]


@pytest.mark.usefixtures("fake_metadata_db")
@pytest.mark.parametrize("sql", BACKUP_STATEMENTS)
async def test_backup_statements_need_accountadmin_before_any_stage_or_secret(monkeypatch, sql):
    service, engine, seen = _service(monkeypatch)
    for role in ("analyst", "user_admin", "SECURITYADMIN"):
        result = await _run(service, sql, role=role, allow_stage_export=True,
                            confirm_destructive=True)
        assert result.error and "ACCOUNTADMIN" in result.error
    assert engine.calls == [] and seen.checks == [] and seen.resolved == []
    result = await _run(service, sql, role="ACCOUNTADMIN", allow_stage_export=True,
                        confirm_destructive=True)
    assert result.error is None, result.error
    assert len(engine.calls) == 1


@pytest.mark.usefixtures("fake_metadata_db")
async def test_create_repository_checks_stage_write_before_secrets(monkeypatch):
    service, engine, seen = _service(monkeypatch, deny={"write"})
    result = await _run(service, REPOSITORY_SQL, role="ACCOUNTADMIN", allow_stage_export=True)
    assert result.error == "Stage access denied"
    assert seen.checks == [("backups", "write")] and seen.resolved == []
    assert engine.calls == []


@pytest.mark.usefixtures("fake_metadata_db")
async def test_create_repository_output_and_audit_hide_location_and_credentials(monkeypatch):
    service, engine, seen = _service(monkeypatch)
    result = await _run(service, REPOSITORY_SQL, role="ACCOUNTADMIN", allow_stage_export=True)
    assert result.error is None, result.error
    (engine_sql,) = engine.calls
    assert SECRET_KEY in engine_sql and "s3a://lake/nova/stages/s2/nightly" in engine_sql
    for text in (str(result.executed_sql), repr(seen.audits)):
        assert SECRET_KEY not in text and ACCESS_KEY not in text and "s3a://" not in text
    assert "@backups/nightly" in result.executed_sql


@pytest.mark.usefixtures("fake_metadata_db")
async def test_repository_needs_a_context_that_may_write_to_stages(monkeypatch):
    service, engine, seen = _service(monkeypatch)
    result = await _run(service, REPOSITORY_SQL, role="ACCOUNTADMIN")
    assert result.error and engine.calls == [] and seen.resolved == []


@pytest.mark.parametrize(
    ("sql", "confirm"),
    [
        ("BACKUP DATABASE db SNAPSHOT s1 TO repo1", True),
        ("DROP REPOSITORY repo1", True),
        (REPOSITORY_SQL, False),
        ("RESTORE SNAPSHOT db.s1 FROM repo1 PROPERTIES ('backup_timestamp' = 'x')", False),
    ],
)
def test_backup_confirmation(sql, confirm):
    analysis = SQLPlanner().preflight(_build(sql))
    assert analysis.requires_confirmation is confirm


# ── SUBMIT TASK: a Nova task of the caller's active role ────────────────────


def _lower(sql: str):
    from app.modules.task_orchestration.ddl import parse_create_task

    return parse_create_task(sql, database="db", schema=None, timezone="UTC")


@pytest.mark.parametrize(
    ("sql", "name", "database", "body"),
    [
        ("SUBMIT TASK etl0 AS CREATE TABLE tbl1 AS SELECT * FROM src_tbl", "etl0", "db",
         "CREATE TABLE tbl1 AS SELECT * FROM src_tbl"),
        ("SUBMIT TASK mydb.etl1 AS INSERT INTO tbl2 SELECT * FROM src_tbl", "etl1", "mydb",
         "INSERT INTO tbl2 SELECT * FROM src_tbl"),
        (
            "SUBMIT TASK t PROPERTIES ('session.enable_profile' = 'true', "
            "'session.insert_timeout' = '10000') AS insert into t2 select * from t1",
            "t", "db",
            "insert /*+ SET_VAR(enable_profile = true, insert_timeout = 10000) */ "
            "into t2 select * from t1",
        ),
        (
            "SUBMIT /*+set_var(insert_timeout=100000)*/ TASK t AS INSERT OVERWRITE w SELECT 1",
            "t", "db", "INSERT /*+ SET_VAR(insert_timeout = 100000) */ OVERWRITE w SELECT 1",
        ),
        (
            "SUBMIT TASK t PROPERTIES ('session.query_timeout' = '600') "
            "AS CREATE TABLE c AS SELECT * FROM s",
            "t", "db", "CREATE TABLE c AS SELECT /*+ SET_VAR(query_timeout = 600) */ * FROM s",
        ),
    ],
)
def test_submit_task_lowers_to_a_one_run_nova_task(sql, name, database, body):
    task = _lower(sql)
    assert (task.name, task.database_name, task.schema_name) == (name, database, "default")
    assert task.body == body
    assert task.run_once and task.submitted and task.schedule_kind == "manual"


def test_submit_task_without_a_name_is_named_by_nova():
    task = _lower("SUBMIT TASK AS INSERT OVERWRITE t SELECT 1")
    assert task.name.startswith("submit_task_") and task.run_once


def test_scheduled_submit_task_is_a_scheduled_nova_task():
    task = _lower("SUBMIT TASK t SCHEDULE EVERY(INTERVAL 1 MINUTE) AS INSERT INTO t SELECT 1")
    assert task.schedule_kind == "interval" and not task.run_once and task.submitted


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        (f"SUBMIT TASK t PROPERTIES ('aws.s3.secret_key' = '{SECRET_KEY}') "
         "AS INSERT INTO t SELECT 1",
         "session properties only"),
        ("SUBMIT TASK t PROPERTIES ('warehouse' = 'w') AS INSERT INTO t SELECT 1",
         "session properties only"),
        ("SUBMIT TASK t AS INSERT INTO t SELECT * FROM @raw/a.csv", "cannot read @stage"),
        (
            "SUBMIT TASK t AS INSERT INTO t SELECT * FROM FILES('path'='s3://b/x', "
            f"'aws.s3.secret_key'='{SECRET_KEY}')",
            "must not persist credentials",
        ),
        ("SUBMIT TASK t SCHEDULE START('2026-01-01 00:00:00') EVERY(INTERVAL 1 MINUTE) "
         "AS INSERT INTO t SELECT 1", "START is not supported"),
        ("SUBMIT TASK t PROPERTIES ('session.x' = '1') AS INSERT /*+ SET_VAR(y=1) */ INTO t "
         "SELECT 1", "not both"),
        ("CREATE TASK t PROPERTIES ('session.x' = '1') AS INSERT INTO t SELECT 1",
         "takes no PROPERTIES"),
    ],
)
def test_submit_task_refusals(sql, message):
    from app.modules.task_orchestration.ddl import TaskDDLError

    with pytest.raises(TaskDDLError, match=message) as raised:
        _lower(sql)
    assert SECRET_KEY not in str(raised.value)


async def test_submit_task_plans_a_task_action_with_its_statement_effects():
    statement, plan = await _plan("SUBMIT TASK t AS INSERT OVERWRITE t SELECT * FROM s")
    assert isinstance(statement, CreateTaskStatement)
    assert plan.payload.definition.run_once
    assert plan.effects.writes_data and plan.effects.replaces_data
    _, scheduled = await _plan(
        "SUBMIT TASK t SCHEDULE EVERY(INTERVAL 1 HOUR) AS INSERT INTO t SELECT 1"
    )
    assert not scheduled.effects.writes_data


def _task_adapters(monkeypatch, *, session, persisted, enqueue):
    from app.core import redis as redis_module
    from app.modules.task_orchestration import router as task_router
    from app.sql_frontend.execution import adapters as adapters_module
    from app.sql_frontend.execution.adapters import FeatureAdapters

    calls: list[str] = []

    class Sessions:
        async def get(self, session_id):
            return session

    async def persist(task, *, created_by, owner_role):
        calls.append(f"persist {task.qualified_name} {owner_role}")
        return SimpleNamespace(task={"id": "task-1", **persisted}, edges=[])

    async def resolve(name, database, schema, user):
        calls.append(f"resolve {database}.{schema}.{name} {user['active_role']}")
        return f"{database}.{schema}.{name}"

    async def audit(**kwargs):
        calls.append(f"audit {kwargs['status']}")

    monkeypatch.setattr(redis_module, "session_store", Sessions())
    monkeypatch.setattr(adapters_module, "persist_lowered_task", persist)
    monkeypatch.setattr(task_router, "resolve_execute_target", resolve)
    monkeypatch.setattr(task_router, "enqueue_manual_run", enqueue)
    adapters = object.__new__(FeatureAdapters)
    adapters._audit_sink = audit

    class Repo:
        async def get_engine_timezone(self):
            return "UTC"

    adapters._task_repository = Repo()
    return adapters, calls


async def test_submit_task_saves_the_task_then_queues_one_run_as_the_caller(monkeypatch):
    async def enqueue(graph_id, user):
        calls.append(f"enqueue {graph_id} {user['active_role']} {user['session_id']}")
        return {"id": "run-1", "state": "pending"}

    adapters, calls = _task_adapters(
        monkeypatch, session={"username": "alice"}, persisted={"name": "t"}, enqueue=enqueue
    )
    sql = "SUBMIT TASK t AS INSERT INTO t2 SELECT 1"
    result = await adapters._execute_create_task(
        sql=sql, normalized_sql=sql, username="alice", role="analyst", database="db",
        session_id="sess", file_id=None, schema=None, encrypted_password="enc",
        validated_frontend=_lower(sql),
    )
    assert result.error is None, result.error
    assert calls == [
        "persist db.default.t analyst",
        "resolve db.default.t analyst",
        "enqueue db.default.t analyst sess",
        "audit SUCCESS",
    ]
    assert result.columns[:4] == ["TaskName", "Status", "task_id", "run_id"]
    assert result.rows[0][:4] == ["db.default.t", "SUBMITTED", "task-1", "run-1"]
    assert "DESCRIBE TASK db.default.t" in result.warnings[0]


async def test_submit_task_without_a_nova_session_saves_nothing(monkeypatch):
    async def enqueue(graph_id, user):  # pragma: no cover - must not run
        raise AssertionError("no run without a session")

    adapters, calls = _task_adapters(monkeypatch, session=None, persisted={}, enqueue=enqueue)
    sql = "SUBMIT TASK t AS INSERT INTO t2 SELECT 1"
    result = await adapters._execute_create_task(
        sql=sql, normalized_sql=sql, username="alice", role="analyst", database="db",
        session_id=None, file_id=None, schema=None, validated_frontend=_lower(sql),
    )
    assert "Nova sign-in" in result.error
    assert not any(call.startswith("persist") for call in calls)


async def test_submit_task_whose_run_cannot_start_says_how_to_finish(monkeypatch):
    from fastapi import HTTPException

    async def enqueue(graph_id, user):
        raise HTTPException(403, "Select an assigned role before running a task")

    adapters, calls = _task_adapters(
        monkeypatch, session={"username": "alice"}, persisted={"name": "t"}, enqueue=enqueue
    )
    sql = "SUBMIT TASK t AS INSERT INTO t2 SELECT 1"
    result = await adapters._execute_create_task(
        sql=sql, normalized_sql=sql, username="alice", role="analyst", database="db",
        session_id="sess", file_id=None, schema=None, validated_frontend=_lower(sql),
    )
    assert "EXECUTE TASK db.default.t" in result.error
    assert "DROP TASK db.default.t" in result.error
    assert calls[-1] == "audit ERROR"


def test_executor_guard_refuses_backup_statements_for_other_roles():
    from app.sql_frontend.backup_restore import require_backup_admin

    for sql in BACKUP_STATEMENTS:
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            require_backup_admin(_build(sql), "analyst")
        require_backup_admin(_build(sql), "ACCOUNTADMIN")
    require_backup_admin(_build("SELECT 1"), "analyst")
