from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.exceptions import register_exception_handlers
from app.modules.pipes import service as service_module
from app.modules.pipes.router import router
from app.modules.pipes.service import PipeError, PipeService
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import CapabilityUnsupportedError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner

CALLER = {
    "username": "alice",
    "encrypted_password": "encrypted",
    "session_id": "session",
    "role": "ANALYST",
    "security_context_version": 7,
}
SOURCE = "INSERT INTO db.t SELECT * FROM FILES('path'='s3://test/f.parquet','format'='parquet')"


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    async def execute(**kwargs):
        recorded.append(kwargs)
        sql = kwargs["sql"]
        if sql.startswith("SHOW PIPES"):
            columns = ["DATABASE_NAME", "PIPE_NAME", "STATE", "CREATED_TIME"]
            rows = [["db", "p", "SUSPENDED", "2026-10-13 12:00:00"]]
        elif sql.startswith("DESCRIBE PIPE"):
            columns = ["DATABASE_NAME", "PIPE_NAME", "PROPERTIES", "INSERT_SQL"]
            source = SOURCE.replace(
                "'format'='parquet'",
                "'format'='parquet','aws.s3.secret_key'='PIPE_SECRET_SENTINEL'",
            )
            rows = [["db", "p", '{"AUTO_INGEST":"true"}', source]]
        elif "information_schema.pipe_files" in sql:
            columns = ["FILE_NAME", "LOAD_STATE", "FILE_SIZE", "ERROR_MSG", "FINISH_LOAD_TIME"]
            rows = [["f.parquet", "FINISHED", 0, None, "2026-10-13 13:00:00"]]
        else:
            columns, rows = [], []
        return SimpleNamespace(columns=columns, rows=rows, error=None)

    monkeypatch.setattr(service_module.query_service, "execute", execute)
    return recorded


@pytest.mark.parametrize(
    "operation",
    [
        "list_pipes",
        "get_pipe",
        "suspend_pipe",
        "resume_pipe",
        "drop_pipe",
        "list_pipe_files",
        "create_pipe",
    ],
)
async def test_every_pipe_operation_preserves_caller(operation, calls):
    service = PipeService()
    if operation == "list_pipes":
        result = await service.list_pipes(database="db", **CALLER)
    elif operation == "create_pipe":
        result = await service.create_pipe({"name": "p", "database": "db", "sql": SOURCE}, **CALLER)
    else:
        result = await getattr(service, operation)("p", database="db", **CALLER)
    assert calls
    assert all(all(call[key] == value for key, value in CALLER.items()) for call in calls)
    assert all(call["database"] == "db" for call in calls)
    assert "PIPE_SECRET_SENTINEL" not in str(result)
    if operation == "drop_pipe":
        assert calls[0]["confirm_destructive"] is True
    if operation == "get_pipe":
        assert calls[0]["sql"] == "SHOW PIPES FROM `db` WHERE NAME = 'p'"
        assert result["name"] == "p" and result["database"] == "db"
    if operation == "list_pipe_files":
        assert result[0]["file_size"] == 0
        assert result[0]["state"] == "FINISHED"
        assert result[0]["loaded_at"] == "2026-10-13 13:00:00"
        assert "load_state" in calls[-1]["sql"]


@pytest.mark.parametrize("name,database", [("p'; DROP TABLE t", "db"), ("p", "db`; DROP TABLE t")])
async def test_invalid_identifiers_never_reach_pipeline(name, database, calls):
    with pytest.raises(Exception, match="Invalid"):
        await PipeService().get_pipe(name, database=database, **CALLER)
    assert calls == []


async def test_invisible_pipe_does_not_query_file_metadata(monkeypatch):
    queries = []

    async def execute(**kwargs):
        queries.append(kwargs["sql"])
        return SimpleNamespace(columns=[], rows=[], error=None)

    monkeypatch.setattr(service_module.query_service, "execute", execute)
    assert await PipeService().list_pipe_files("p", database="db", **CALLER) == []
    assert queries == ["SHOW PIPES FROM `db` WHERE NAME = 'p'"]


async def test_engine_failure_is_redacted_before_exposure(monkeypatch):
    async def execute(**kwargs):
        return SimpleNamespace(error="Invalid aws.s3.secret_key='PIPE_SECRET_SENTINEL'")

    monkeypatch.setattr(service_module.query_service, "execute", execute)
    with pytest.raises(PipeError) as error:
        await PipeService().list_pipes(database="db", **CALLER)
    assert "PIPE_SECRET_SENTINEL" not in str(error.value)


async def test_poll_interval_above_engine_limit_rejected_before_execution(calls):
    with pytest.raises(ValidationError, match="less than or equal to 3600"):
        await PipeService().create_pipe(
            {"name": "p", "database": "db", "sql": SOURCE, "poll_interval": 7200}, **CALLER
        )
    assert calls == []


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE PIPE db.p AS " + SOURCE,
        "CREATE PIPE db.p AS INSERT INTO db.t SELECT * FROM @incoming/*.parquet",
        "ALTER PIPE db.p RESUME",
        "ALTER PIPE db.p RETRY ALL",
        "ALTER PIPE db.p RETRY FILE 'x'",
        "ALTER PIPE db.p SET ('AUTO_INGEST'='true')",
    ],
)
async def test_background_pipe_execution_rejected_by_ranger_frontend(sql):
    statement = ast_builders.build(parse_statement(sql))
    with pytest.raises(
        CapabilityUnsupportedError, match="Pipe execution is unavailable with Ranger"
    ):
        await SQLPlanner().plan(statement, PlanningContext(ranger_enabled=True))
    native = await SQLPlanner().plan(statement, PlanningContext(ranger_enabled=False))
    assert native.effects.writes_data and native.effects.external_io


@pytest.mark.parametrize(
    "sql", ["SHOW PIPES FROM db", "DESCRIBE PIPE db.p", "ALTER PIPE db.p SUSPEND", "DROP PIPE db.p"]
)
async def test_inspection_and_shutdown_remain_available_with_ranger(sql):
    plan = await SQLPlanner().plan(
        ast_builders.build(parse_statement(sql)), PlanningContext(ranger_enabled=True)
    )
    assert plan.engine_sql == sql


@pytest.fixture
def http_client(monkeypatch):
    monkeypatch.setattr(settings, "RANGER_ENABLED", False)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/pipes")
    user = {
        "username": "alice",
        "encrypted_password": "encrypted",
        "session_id": "session",
        "assigned_roles": ["ANALYST"],
        "active_role": "ANALYST",
        "security_context_version": 7,
    }
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app), user


@pytest.mark.parametrize(
    "method,url",
    [
        ("get", "/pipes?database=db"),
        ("get", "/pipes/p?database=db"),
        ("get", "/pipes/p/files?database=db"),
        ("patch", "/pipes/p/suspend?database=db"),
        ("patch", "/pipes/p/resume?database=db"),
        ("delete", "/pipes/p?database=db"),
    ],
)
def test_http_routes_propagate_authenticated_session(method, url, http_client, calls):
    client, _ = http_client
    response = getattr(client, method)(url)
    assert response.status_code == 200, response.text
    assert "PIPE_SECRET_SENTINEL" not in response.text
    assert calls and all(call["role"] == "ANALYST" for call in calls)
    assert all(call["security_context_version"] == 7 for call in calls)


@pytest.mark.parametrize("active_role", [None, "ADMIN", "ANALYST,ADMIN", "ALL"])
def test_invalid_active_role_fails_before_pipeline(active_role, http_client, calls, monkeypatch):
    client, user = http_client
    monkeypatch.setattr(settings, "RANGER_ENABLED", True)
    user["active_role"] = active_role
    response = client.get("/pipes?database=db")
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "field,value",
    [("batch_size", '1GB"; DROP TABLE t; --'), ("poll_interval", 0), ("batch_files", -1)],
)
def test_unsafe_pipe_properties_rejected(field, value, http_client, calls):
    client, _ = http_client
    response = client.post(
        "/pipes", json={"name": "p", "database": "db", "sql": SOURCE, field: value}
    )
    assert response.status_code == 422
    assert calls == []
