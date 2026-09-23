from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest

from app.modules.query.dialect.parser import CommandType, parse_sql
from app.modules.query.dialect.translator import StorageConfig, translate_stage_query
from app.modules.query.service import QueryService
from app.modules.query.sql_pipeline import prepare_stage_sql
from app.modules.stages.access import StageAccessDenied, _grant_rows_allow, check_stage_access
from app.modules.task_orchestration.execution import (
    DelegateExecutor,
    NodeExecutionError,
    TaskSpec,
)


def _config(prefix: str = "db/bronze/stage") -> StorageConfig:
    return StorageConfig("s3", "http://storage", "bucket", prefix, "key", "secret")


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("LIST @stage", "SELECT * FROM FILES("),
        ("LIST FILES @stage/data/", "SELECT * FROM FILES("),
        ("COPY INTO t FROM @stage.data.csv", "INSERT INTO t SELECT * FROM FILES("),
        ("COPY INTO @stage.out.parquet FROM t", "INSERT INTO FILES("),
        ("INSERT INTO @stage.out.parquet SELECT * FROM t", "INSERT INTO FILES("),
    ],
)
def test_nova_commands_lower_to_starrocks_41(sql: str, expected: str) -> None:
    parsed = parse_sql(sql)
    rewritten, _ = translate_stage_query(parsed, {"stage": _config()})
    assert rewritten.startswith(expected)
    assert "@stage" not in rewritten
    if parsed.command_type == CommandType.STAGE_BROWSE:
        assert "'list_files_only'='true'" in rewritten


def test_copy_options_and_user_variables_are_not_guessed() -> None:
    assert parse_sql("COPY INTO t VALUES (@x)").stage_refs == []
    parsed = parse_sql("COPY INTO t FROM @stage.data.csv ON_ERROR = 'SKIP'")
    with pytest.raises(ValueError, match="Unsupported COPY"):
        translate_stage_query(parsed, {"stage": _config()})


async def test_csv_properties_belong_only_to_their_reference() -> None:
    sql = "SELECT * FROM @a.first.csv JOIN @b.second.csv ON 1=1"
    parsed = parse_sql(sql)
    prepared = await prepare_stage_sql(
        sql,
        parsed=parsed,
        stage_configs_by_ref={parsed.stage_refs[0].start: _config("a"),
                              parsed.stage_refs[1].start: _config("b")},
        csv_params_by_ref={parsed.stage_refs[0].start: {"csv.column_separator": ";"},
                           parsed.stage_refs[1].start: {"csv.column_separator": "|"}},
    )
    assert prepared.engine_sql.count("'csv.column_separator'") == 2
    assert "'csv.column_separator'=';'" in prepared.engine_sql
    assert "'csv.column_separator'='|'" in prepared.engine_sql


@pytest.mark.parametrize(
    ("grant", "action", "allowed"),
    [
        ("GRANT SELECT ON TABLE db.* TO ROLE analyst", "read", True),
        ("GRANT INSERT ON TABLE db.* TO ROLE loader", "read", True),
        ("GRANT INSERT ON TABLE db.* TO ROLE loader", "write", True),
        ("GRANT SELECT ON TABLE db.* TO ROLE analyst", "write", False),
        ("GRANT ALL ON TABLE db.* TO ROLE admin", "delete", True),
        ("GRANT ALL ON DATABASE db TO ROLE admin", "read", False),
        ("GRANT SELECT ON TABLE other.* TO ROLE analyst", "read", False),
    ],
)
def test_stage_grant_scope(grant: str, action: str, allowed: bool) -> None:
    assert _grant_rows_allow([("user", "default", grant)], "db", "bronze", action) is allowed


@dataclass
class _Connection:
    type: str = "s3"
    endpoint: str = "http://storage"
    bucket: str = "bucket"
    region: str = "us-east-1"


async def test_authorization_precedes_secret_resolution(monkeypatch) -> None:
    import app.modules.query.service as module

    async def rows(sql, params=None):
        return {"rows": [("stage", "db", "bronze", "storage", "db/bronze/stage")]}

    async def deny(*args, **kwargs):
        raise StageAccessDenied("Stage access denied")

    def secret(name):
        raise AssertionError("secret was resolved before authorization")

    monkeypatch.setattr(module.db, "execute_system", rows)
    monkeypatch.setattr(module, "check_stage_access", deny)
    monkeypatch.setattr(module, "resolve_storage_credentials", secret)
    with pytest.raises(StageAccessDenied):
        await QueryService()._resolve_stage_refs(
            parse_sql("SELECT * FROM @stage.data.csv"),
            database="db", schema="bronze", username="analyst", password="pw", role=None,
        )


async def test_explicit_scope_and_same_names_do_not_collide(monkeypatch) -> None:
    import app.modules.query.service as module

    async def rows(sql, params=None):
        return {"rows": [
            ("stage", "db", "bronze", "bronze_store", "db/bronze/stage"),
            ("stage", "db", "silver", "silver_store", "db/silver/stage"),
        ]}

    checked = []

    async def allow(stage, **kwargs):
        checked.append((stage["schema_name"], kwargs["action"]))

    monkeypatch.setattr(module.db, "execute_system", rows)
    monkeypatch.setattr(module, "check_stage_access", allow)
    monkeypatch.setattr(module, "get_storage_connection", lambda name: _Connection())
    monkeypatch.setattr(module, "resolve_storage_credentials", lambda name: ("key", "secret"))
    sql = "SELECT * FROM @stage.a.csv JOIN @silver.stage.b.csv ON 1=1"
    parsed, configs = await QueryService()._resolve_stage_refs(
        parse_sql(sql), database="db", schema="bronze", username="analyst",
        password="pw", role=None,
    )
    prepared = await prepare_stage_sql(sql, parsed=parsed, stage_configs_by_ref=configs)
    assert "db/bronze/stage/a.csv" in prepared.engine_sql
    assert "db/silver/stage/b.csv" in prepared.engine_sql
    assert checked == [("bronze", "read"), ("silver", "read")]


async def test_scoped_reference_does_not_fall_back_to_foreign_stage(monkeypatch) -> None:
    import app.modules.query.service as module

    async def rows(sql, params=None):
        return {"rows": [("stage", "other", "silver", "storage", "other/silver/stage")]}

    monkeypatch.setattr(module.db, "execute_system", rows)
    with pytest.raises(ValueError, match="not found"):
        await QueryService()._resolve_stage_refs(
            parse_sql("SELECT * FROM @stage.data.csv"),
            database="db", schema="bronze", username="analyst", password="pw", role=None,
        )


async def test_only_an_active_role_grants_stage_access(monkeypatch) -> None:
    import app.modules.stages.access as module

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, sql):
            return None

        async def fetchall(self):
            return [("user", None, "GRANT 'analyst' TO 'user'@'%'")]

    class Conn:
        def cursor(self):
            return Cursor()

    calls = []

    async def role_grants(sql):
        calls.append(sql)
        return {"rows": [("analyst", "default", "GRANT SELECT ON TABLE db.* TO ROLE analyst")]}

    monkeypatch.setattr(module.settings, "RANGER_ENABLED", False)
    monkeypatch.setattr(module.db, "execute_system", role_grants)
    stage = {"database_name": "db", "schema_name": "bronze"}
    await check_stage_access(
        stage, action="read", username="user", active_role="analyst", connection=Conn()
    )
    assert calls == ["SHOW GRANTS FOR ROLE analyst"]
    with pytest.raises(StageAccessDenied):
        await check_stage_access(
            stage, action="write", username="user", active_role="analyst", connection=Conn()
        )

    class RejectedCursor(Cursor):
        async def execute(self, sql):
            if sql.startswith("SET ROLE"):
                raise RuntimeError("role is not assigned")

    class RejectedConn:
        def cursor(self):
            return RejectedCursor()

    with pytest.raises(StageAccessDenied):
        await check_stage_access(
            stage, action="read", username="user", active_role="analyst",
            connection=RejectedConn(),
        )


async def test_task_stage_body_never_submits_injected_credentials(monkeypatch) -> None:
    import app.modules.task_orchestration.execution as module

    @asynccontextmanager
    async def fake_conn(*args, **kwargs):
        yield object()

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def watermark(*args, **kwargs):
        return None

    submitted = []

    async def submit(conn, statement):
        submitted.append(statement)

    executor = DelegateExecutor(credentials=object())
    monkeypatch.setattr(module.settings, "RANGER_ENABLED", False)
    monkeypatch.setattr(executor, "_owner_conn", fake_conn)
    monkeypatch.setattr(module.db, "system_conn", fake_conn)
    monkeypatch.setattr(module, "_dict_cursor", lambda conn: Cursor())
    monkeypatch.setattr(executor, "_latest_create_time", watermark)
    monkeypatch.setattr(executor, "_submit", submit)
    with pytest.raises(NodeExecutionError, match="persist storage credentials"):
        await executor.execute(
            TaskSpec("daily", "INSERT INTO t SELECT * FROM @stage.data.csv", "db", schema="bronze"),
            "owner",
        )
    assert submitted == []
