"""Unit tests for the explorer's Create Database endpoint.

``POST /explorer/catalogs/{catalog}/databases`` runs DDL on the caller's
StarRocks connection. Here that connection is a fake whose cursor records the
statement instead of executing it, and the authenticated user is supplied
through a dependency override, so no engine or Redis is involved. The assertions
are on the *assembled SQL*, on the identifier allow-list, and on the refusal
paths — the boundary this endpoint owns.

The engine-side behaviour (does StarRocks accept the statement?) is an L3
concern and belongs with the engine stack, not here.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.explorer import router as explorer_router

USER = {
    "username": "alice",
    "roles": [],
    "session_id": "s-1",
    "encrypted_password": "enc",
}


class FakeCursor:
    def __init__(self, error: Exception | None = None):
        self.executed: list[str] = []
        self._error = error

    async def execute(self, sql: str, *args: Any) -> None:
        if self._error is not None:
            raise self._error
        self.executed.append(sql)

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    def cursor(self, *_args: Any, **_kwargs: Any) -> FakeCursor:
        return self._cursor


class AuditSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "qid"


def make_client(
    *,
    cursor: FakeCursor | None = None,
    audit: AuditSink | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[TestClient, FakeCursor, AuditSink]:
    cursor = cursor or FakeCursor()
    audit = audit or AuditSink()
    if monkeypatch is not None:
        monkeypatch.setattr(explorer_router, "write_audit_log", audit)

    app = FastAPI()
    from app.core.exceptions import register_exception_handlers

    register_exception_handlers(app)
    app.include_router(explorer_router.router, prefix="/api/v1/explorer")
    app.dependency_overrides[explorer_router.get_current_user] = lambda: USER
    app.dependency_overrides[explorer_router.get_user_connection] = lambda: FakeConnection(cursor)
    return TestClient(app, raise_server_exceptions=False), cursor, audit


class TestCreateDatabase:
    def test_creates_on_internal_catalog(self, monkeypatch) -> None:
        client, cursor, audit = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/explorer/catalogs/default_catalog/databases",
            json={"name": "analytics"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["name"] == "analytics"
        assert cursor.executed == ["CREATE DATABASE `analytics`"]

        status = [c["status"] for c in audit.calls]
        assert status == ["SUCCESS"]
        assert audit.calls[0]["object_name"] == "analytics"

    def test_comment_is_ignored(self, monkeypatch) -> None:
        # StarRocks' CREATE DATABASE takes no COMMENT clause, so an extra field
        # in the request must not leak into the statement.
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/explorer/catalogs/default_catalog/databases",
            json={"name": "analytics", "comment": "it's data"},
        )

        assert response.status_code == 200
        assert cursor.executed == ["CREATE DATABASE `analytics`"]

    @pytest.mark.parametrize(
        "bad_name",
        ["1abc", "has space", "semi;colon", "back`tick", "drop table x"],
    )
    def test_rejects_invalid_identifier(self, monkeypatch, bad_name) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/explorer/catalogs/default_catalog/databases",
            json={"name": bad_name},
        )

        assert response.status_code == 400
        assert cursor.executed == []

    def test_rejects_empty_name_at_schema_boundary(self, monkeypatch) -> None:
        # ``min_length=1`` means an empty name never reaches the handler, so the
        # contract is FastAPI's 422 rather than the allow-list's 400.
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/explorer/catalogs/default_catalog/databases",
            json={"name": ""},
        )

        assert response.status_code == 422
        assert cursor.executed == []

    def test_rejects_external_catalog(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/explorer/catalogs/iceberg_lake/databases",
            json={"name": "analytics"},
        )

        assert response.status_code == 400
        assert "external catalog" in response.json()["detail"]
        assert cursor.executed == []

    def test_engine_error_is_audited_as_error(self, monkeypatch) -> None:
        cursor = FakeCursor(error=RuntimeError("table exists"))
        client, _, audit = make_client(cursor=cursor, monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/explorer/catalogs/default_catalog/databases",
            json={"name": "analytics"},
        )

        assert response.status_code == 400
        assert [c["status"] for c in audit.calls] == ["ERROR"]
        assert audit.calls[0]["error_message"] == "table exists"
