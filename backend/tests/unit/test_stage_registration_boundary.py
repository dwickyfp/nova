from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import config
from app.core.deps import get_current_user
from app.core.exceptions import InsufficientRoleError, register_exception_handlers
from app.modules.query.dialect.parser import parse_sql
from app.modules.stages import router, service
from app.modules.stages.schemas import StageCreate
from app.sql_frontend.execution.stages import StageRuntime

DATA = {
    "name": "files",
    "database_name": "db",
    "schema_name": "bronze",
    "storage_connection": "production",
    "base_prefix": "",
}


@pytest.fixture
def registration(monkeypatch):
    statements = []

    class Cursor:
        async def execute(self, sql, params):
            statements.append((sql, params))

    @asynccontextmanager
    async def cursor(*args):
        yield Cursor()

    @asynccontextmanager
    async def connection():
        yield SimpleNamespace(cursor=cursor)

    async def get_stage(self, stage_id):
        params = statements[-1][1]
        return dict(
            zip(
                (
                    "id",
                    "name",
                    "database_name",
                    "schema_name",
                    "storage_connection",
                    "base_prefix",
                    "created_by",
                ),
                params,
                strict=True,
            )
        )

    monkeypatch.setattr(service.db, "system_conn", connection)
    monkeypatch.setattr(service.StageService, "get_stage", get_stage)
    monkeypatch.setattr(
        config,
        "load_nova_app_config",
        lambda: SimpleNamespace(
            storage_connections={"production": SimpleNamespace(name="production")}
        ),
    )
    return statements


async def test_new_stage_names_cannot_select_an_existing_namespace(registration):
    rows = [
        await service.stage_service.create_stage(
            {**DATA, "name": "../../other", "database_name": "workspaces"},
            "analyst",
            active_role="loader",
        )
        for _ in range(2)
    ]
    assert len(registration) == 2
    for row in rows:
        assert row["base_prefix"] == f"nova/stages/{row['id']}"
        assert UUID(row["id"]).version == 4
        assert row["created_by"] == "analyst"
    assert rows[0]["base_prefix"] != rows[1]["base_prefix"]


@pytest.mark.parametrize("prefix", ["other/private", "/", "../outside", "nova/stages/victim"])
async def test_non_admin_cannot_register_an_existing_prefix(registration, prefix):
    with pytest.raises(InsufficientRoleError):
        await service.stage_service.create_stage(
            {**DATA, "base_prefix": prefix},
            "analyst",
            active_role="loader",
        )
    assert registration == []


@pytest.mark.parametrize("role", [None, ""])
async def test_roleless_registration_is_denied(registration, role):
    with pytest.raises(InsufficientRoleError):
        await service.stage_service.create_stage(DATA, "admin", active_role=role)
    assert registration == []


async def test_active_admin_can_register_an_explicit_existing_prefix(registration):
    row = await service.stage_service.create_stage(
        {**DATA, "base_prefix": "/existing/files/"},
        "admin",
        active_role="ACCOUNTADMIN",
    )
    assert row["base_prefix"] == "existing/files"


@pytest.mark.parametrize("prefix", ["/", "../outside", "safe/../../outside", "safe\\outside"])
async def test_admin_prefix_must_be_a_relative_folder(registration, prefix):
    with pytest.raises(service.StagePathError):
        await service.stage_service.create_stage(
            {**DATA, "base_prefix": prefix},
            "admin",
            active_role="ACCOUNTADMIN",
        )
    assert registration == []


async def test_unknown_connection_never_writes_metadata_or_resolves_secrets(
    registration, monkeypatch
):
    secrets = Mock(side_effect=AssertionError("secret resolution must not run"))
    monkeypatch.setattr(service, "resolve_storage_credentials", secrets)
    with pytest.raises(service.StageRegistrationError, match="not configured"):
        await service.stage_service.create_stage(
            {**DATA, "storage_connection": "missing"},
            "analyst",
            active_role="loader",
        )
    assert registration == []
    secrets.assert_not_called()


def test_legacy_stage_with_missing_connection_cannot_borrow_default(registration, monkeypatch):
    secrets = Mock(side_effect=AssertionError("secret resolution must not run"))
    client = Mock(side_effect=AssertionError("storage client must not be created"))
    monkeypatch.setattr(service, "resolve_storage_credentials", secrets)
    monkeypatch.setattr(service.boto3, "client", client)
    with pytest.raises(ValueError, match="not configured"):
        service.StageService._s3_client_for_stage({**DATA, "storage_connection": "missing"})
    secrets.assert_not_called()
    client.assert_not_called()


async def test_sql_stage_rejects_missing_connection_after_authorizing_before_secrets(
    registration, monkeypatch
):
    monkeypatch.setattr(
        service.db,
        "execute_system",
        AsyncMock(
            return_value={
                "rows": [("files", "db", "bronze", "missing", "other/private")],
            }
        ),
    )
    authorize = AsyncMock()
    secrets = Mock(side_effect=AssertionError("secret resolution must not run"))
    runtime = StageRuntime(access_check=authorize, credentials=secrets)
    with pytest.raises(ValueError, match="not configured"):
        await runtime._resolve_stage_refs(
            parse_sql("SELECT * FROM @files.data.csv"),
            database="db",
            schema="bronze",
            username="analyst",
            password="",
            role="loader",
        )
    assert authorize.await_args.kwargs["active_role"] == "loader"
    secrets.assert_not_called()


def test_existing_stage_locations_remain_unchanged():
    assert service.StageService._resolve_prefix({**DATA, "base_prefix": "old/files"}) == "old/files"
    assert service.StageService._resolve_prefix(DATA) == "db/bronze/files"


@pytest.mark.parametrize(
    "field", ["name", "database_name", "schema_name", "storage_connection", "base_prefix"]
)
def test_schema_accepts_exact_utf8_width_and_rejects_overflow(field):
    limit = 512 if field == "base_prefix" else 128
    accepted = "あ" * (limit // 3) + "a" * (limit % 3)
    assert StageCreate(**{**DATA, field: accepted}).model_dump()[field] == accepted
    for bad in ("x" * (limit + 1), accepted + "a", "\x00"):
        with pytest.raises(ValidationError):
            StageCreate(**{**DATA, field: bad})


@pytest.mark.parametrize("field", ["name", "database_name", "schema_name", "storage_connection"])
def test_blank_registration_fields_are_rejected(field):
    with pytest.raises(ValidationError):
        StageCreate(**{**DATA, field: "  "})


@pytest.mark.parametrize(
    "role,prefix,connection,expected",
    [
        ("loader", "private/files", "production", 403),
        ("loader", "", "production", 201),
        ("ACCOUNTADMIN", "private/files", "production", 201),
        ("loader", "", "missing", 400),
        ("ACCOUNTADMIN", "../outside", "production", 400),
    ],
)
def test_http_registration_uses_active_role_and_audits_outcome(
    registration,
    monkeypatch,
    role,
    prefix,
    connection,
    expected,
):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router.router, prefix="/stages")
    app.dependency_overrides[get_current_user] = lambda: {
        "username": "switcher",
        "roles": ["ACCOUNTADMIN", "loader"],
        "active_role": role,
        "security_context_version": 2,
    }
    authorize = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(router, "_authorize", authorize)
    monkeypatch.setattr(router, "write_audit_log", audit)
    with TestClient(app) as client:
        response = client.post(
            "/stages", json={**DATA, "base_prefix": prefix, "storage_connection": connection}
        )
    assert response.status_code == expected
    assert authorize.await_args.args[2] == "delete"
    assert audit.await_count == 2
    assert audit.await_args_list[0].kwargs["status"] == "ATTEMPTED"
    assert audit.await_args.kwargs["status"] == ("SUCCESS" if expected == 201 else "ERROR")
    assert audit.await_args.kwargs["active_role"] == role
    assert len(registration) == (1 if expected == 201 else 0)
    assert prefix not in str(audit.await_args) if prefix else True


async def test_scope_refusal_is_audited_before_any_registration(registration, monkeypatch):
    from fastapi import HTTPException

    audit = AsyncMock()
    monkeypatch.setattr(router, "write_audit_log", audit)
    monkeypatch.setattr(
        router, "_authorize", AsyncMock(side_effect=HTTPException(403, "Stage access denied"))
    )
    with pytest.raises(HTTPException):
        await router.create_stage(
            StageCreate(**DATA), {"username": "analyst", "active_role": "loader"}
        )
    assert registration == []
    assert audit.await_args.kwargs["status"] == "ERROR"
    assert audit.await_args.kwargs["error_message"] == "HTTPException"


@pytest.mark.parametrize(
    "folder,filename,expected",
    [
        ("", "data.csv", "data.csv"),
        ("incoming", "data.csv", "incoming/data.csv"),
        ("incoming/./daily/", "nested/data.csv", "incoming/daily/nested/data.csv"),
    ],
)
def test_multipart_upload_honors_the_selected_folder(monkeypatch, folder, filename, expected):
    app = FastAPI()
    app.include_router(router.router, prefix="/stages")
    app.dependency_overrides[get_current_user] = lambda: {
        "username": "analyst",
        "active_role": "loader",
    }
    authorized = AsyncMock(return_value=DATA)
    audit = AsyncMock()
    uploaded = []

    async def upload(stage_id, name, stream):
        uploaded.append((stage_id, name, stream.read()))
        return {"filename": name, "size": 5}

    monkeypatch.setattr(router, "_authorized_stage", authorized)
    monkeypatch.setattr(router, "write_audit_log", audit)
    monkeypatch.setattr(service.stage_service, "upload_stream", upload)
    with TestClient(app) as client:
        response = client.post(
            "/stages/fixture/files",
            data={"path": folder},
            files={"file": (filename, b"id\n1\n", "text/csv")},
        )
    assert response.status_code == 200
    assert uploaded == [("fixture", expected, b"id\n1\n")]
    assert authorized.await_args.args[2] == "write"
    assert audit.await_args.kwargs["object_name"] == f"fixture/{expected}"


@pytest.mark.parametrize("folder", ["../outside", "/outside", "a\\outside", "a/../../b", "/"])
def test_multipart_upload_refuses_escaping_folders_before_storage(monkeypatch, folder):
    app = FastAPI()
    app.include_router(router.router, prefix="/stages")
    app.dependency_overrides[get_current_user] = lambda: {"username": "analyst"}
    authorized = AsyncMock()
    upload = AsyncMock()
    monkeypatch.setattr(router, "_authorized_stage", authorized)
    monkeypatch.setattr(service.stage_service, "upload_stream", upload)
    with TestClient(app) as client:
        response = client.post(
            "/stages/fixture/files",
            data={"path": folder},
            files={"file": ("data.csv", b"id\n1\n", "text/csv")},
        )
    assert response.status_code == 400
    authorized.assert_not_called()
    upload.assert_not_called()
