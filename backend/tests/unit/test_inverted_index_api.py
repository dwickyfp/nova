"""Unit tests for the inverted-index surface (NOVA-111).

The endpoints run DDL on the caller's StarRocks connection; here that connection
is a fake whose cursor records the statement instead of executing it, and the
authenticated user is supplied through a dependency override. No engine, no
Redis — the assertions are on the *assembled SQL* and on the refusal paths,
which is the boundary this module owns.

The engine-side behaviour (does 4.1.4 accept the statement?) is an L3 concern
and lives in ``tests/integration/test_inverted_index_l3.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.indexes import router as indexes_router

USER = {
    "username": "alice",
    "roles": [],
    "session_id": "s-1",
    "encrypted_password": "enc",
}


class FakeCursor:
    """Records executed SQL; answers ``SHOW INDEX`` with canned rows."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, error: Exception | None = None):
        self.executed: list[str] = []
        self._rows = rows or []
        self._error = error
        self._is_dict = False

    async def execute(self, sql: str, *args: Any) -> None:
        if self._error is not None:
            raise self._error
        self.executed.append(sql)

    async def fetchall(self) -> list[Any]:
        return self._rows

    @property
    def description(self) -> Any:
        return None

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
        monkeypatch.setattr(indexes_router, "write_audit_log", audit)

    app = FastAPI()
    # The real app registers Nova's exception handlers, which turn an
    # ``InvalidIdentifierError`` (a ``ForbiddenSQLError``) into its declared
    # 403 rather than a bare 500. Without this the refusal tests would assert
    # the wrong contract.
    from app.core.exceptions import register_exception_handlers

    register_exception_handlers(app)
    app.include_router(indexes_router.router, prefix="/api/v1/indexes")
    app.dependency_overrides[indexes_router.get_current_user] = lambda: USER
    app.dependency_overrides[indexes_router.get_user_connection] = lambda: FakeConnection(cursor)
    return TestClient(app, raise_server_exceptions=False), cursor, audit


class TestCreateIndexSql:
    def test_gin_english_index_is_built_from_allow_listed_pieces(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/create",
            json={
                "database": "example_db",
                "table": "articles",
                "index_name": "idx_content",
                "column": "content",
                "kind": "GIN",
                "parser": "english",
            },
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert cursor.executed == [
            "ALTER TABLE `example_db`.`articles` ADD INDEX `idx_content` "
            '(`content`) USING GIN ("parser"="english")'
        ]

    def test_builtin_imp_lib_and_dict_gram_num_are_forwarded(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/create",
            json={
                "database": "example_db",
                "table": "articles",
                "index_name": "idx_title",
                "column": "title",
                "kind": "GIN",
                "parser": "standard",
                "imp_lib": "builtin",
                "dict_gram_num": 3,
            },
        )

        assert response.status_code == 200
        assert cursor.executed[0] == (
            "ALTER TABLE `example_db`.`articles` ADD INDEX `idx_title` "
            '(`title`) USING GIN ("parser"="standard", "imp_lib"="builtin", '
            '"dict_gram_num"="3")'
        )

    def test_bitmap_index_uses_the_plain_add_index_form(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/create",
            json={
                "database": "example_db",
                "table": "events",
                "index_name": "idx_kind",
                "column": "kind",
                "kind": "BITMAP",
            },
        )

        assert response.status_code == 200
        assert cursor.executed == [
            "ALTER TABLE `example_db`.`events` ADD INDEX `idx_kind` (`kind`)"
        ]

    def test_ngram_bloom_filter_index_carries_its_own_properties(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/create",
            json={
                "database": "example_db",
                "table": "logs",
                "index_name": "idx_msg_ngram",
                "column": "message",
                "kind": "NGRAM_BF",
                "properties": {"gram_num": "3", "bloom_filter_fpp": "0.01"},
            },
        )

        assert response.status_code == 200
        assert cursor.executed == [
            "ALTER TABLE `example_db`.`logs` ADD INDEX `idx_msg_ngram` (`message`) "
            'USING NGRAM_BF ("gram_num"="3", "bloom_filter_fpp"="0.01")'
        ]

    def test_gin_requires_an_explicit_parser(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/create",
            json={
                "database": "example_db",
                "table": "articles",
                "index_name": "idx_content",
                "column": "content",
                "kind": "GIN",
            },
        )

        assert response.status_code == 400
        assert "parser" in response.json()["detail"]
        assert cursor.executed == [], "no statement may reach the engine"

    def test_audit_records_success_with_the_object_name(self, monkeypatch) -> None:
        client, _, audit = make_client(monkeypatch=monkeypatch)

        client.post(
            "/api/v1/indexes/create",
            json={
                "database": "example_db",
                "table": "articles",
                "index_name": "idx_content",
                "column": "content",
                "kind": "GIN",
                "parser": "english",
            },
        )

        assert len(audit.calls) == 1
        call = audit.calls[0]
        assert call["action"] == "create_index"
        assert call["object_name"] == "example_db.articles.idx_content"
        assert call["status"] == "SUCCESS"
        assert call["user_name"] == "alice"


class TestCreateIndexRejections:
    """Every rejection happens before a statement is assembled or executed.

    An allow-list refusal is an ``InvalidIdentifierError``, which subclasses
    ``ForbiddenSQLError`` and therefore surfaces as **403** — the same contract
    every other DDL router uses (NOVA-89). A handler-raised ``HTTPException``
    (a missing required field) is 400; a pydantic validation error is 422.
    """

    def test_identifier_with_a_backtick_is_refused(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/create",
            json={
                "database": "example_db",
                "table": "articles`; DROP TABLE x; --",
                "index_name": "idx",
                "column": "content",
                "kind": "GIN",
                "parser": "english",
            },
        )

        assert response.status_code == 403
        assert cursor.executed == []

    def test_a_fragment_in_the_parser_is_refused(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/create",
            json={
                "database": "example_db",
                "table": "articles",
                "index_name": "idx",
                "column": "content",
                "kind": "GIN",
                "parser": 'english")); DROP TABLE `x`; --',
            },
        )

        assert response.status_code == 403
        assert cursor.executed == []

    def test_a_value_smuggled_through_properties_is_refused(self, monkeypatch) -> None:
        """A parser carried only in ``properties`` still goes through the validator.

        The explicit ``parser`` field would mask a smuggled one, so this test
        deliberately leaves it unset — the closed property key set is what must
        catch the fragment, not the field winning a race with it.
        """
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/create",
            json={
                "database": "example_db",
                "table": "articles",
                "index_name": "idx",
                "column": "content",
                "kind": "GIN",
                "properties": {"parser": 'english")); DROP TABLE `x`; --'},
            },
        )

        assert response.status_code == 403
        assert cursor.executed == []

    def test_an_unknown_property_key_is_refused(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/create",
            json={
                "database": "example_db",
                "table": "articles",
                "index_name": "idx",
                "column": "content",
                "kind": "GIN",
                "parser": "english",
                "properties": {"aws.s3.secret_key": "AKIA"},
            },
        )

        assert response.status_code == 403
        assert cursor.executed == []

    def test_an_unknown_kind_is_refused(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/create",
            json={
                "database": "example_db",
                "table": "articles",
                "index_name": "idx",
                "column": "content",
                "kind": "GIN) ; DROP TABLE x; --",
                "parser": "english",
            },
        )

        assert response.status_code == 403
        assert cursor.executed == []

    def test_a_negative_dict_gram_num_is_refused(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/create",
            json={
                "database": "example_db",
                "table": "articles",
                "index_name": "idx",
                "column": "content",
                "kind": "GIN",
                "parser": "english",
                "dict_gram_num": 0,
            },
        )

        # pydantic ge=1 rejects it before the handler runs.
        assert response.status_code == 422
        assert cursor.executed == []


class TestDropIndex:
    def test_drops_with_the_alter_table_form(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/drop",
            json={
                "database": "example_db",
                "table": "articles",
                "index_name": "idx_content",
            },
        )

        assert response.status_code == 200
        assert cursor.executed == ["ALTER TABLE `example_db`.`articles` DROP INDEX `idx_content`"]

    def test_a_bad_identifier_is_refused(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/drop",
            json={
                "database": "example_db",
                "table": "articles",
                "index_name": "idx`; DROP TABLE x; --",
            },
        )

        assert response.status_code == 403
        assert cursor.executed == []

    def test_engine_failure_is_audited_and_returns_400(self, monkeypatch) -> None:
        cursor = FakeCursor(error=RuntimeError("table not found"))
        client, _, audit = make_client(cursor=cursor, monkeypatch=monkeypatch)

        response = client.post(
            "/api/v1/indexes/drop",
            json={
                "database": "example_db",
                "table": "articles",
                "index_name": "idx_content",
            },
        )

        assert response.status_code == 400
        assert len(audit.calls) == 1
        assert audit.calls[0]["status"] == "ERROR"
        # The statement is recorded even though it failed, and the message is
        # the engine's, not a stack trace.
        assert "DROP INDEX" in audit.calls[0]["sql_text"]


class TestListIndexes:
    def test_groups_show_index_rows_by_index_name(self, monkeypatch) -> None:
        rows = [
            {"Key_name": "idx_content", "Column_name": "content", "Index_type": "GIN"},
            {"Key_name": "idx_title", "Column_name": "title", "Index_type": "GIN"},
        ]
        client, cursor, _ = make_client(cursor=FakeCursor(rows=rows), monkeypatch=monkeypatch)

        response = client.get("/api/v1/indexes/example_db/articles")

        assert response.status_code == 200
        body = response.json()
        assert [i["name"] for i in body["indexes"]] == ["idx_content", "idx_title"]
        assert body["indexes"][0]["columns"] == ["content"]
        assert cursor.executed == ["SHOW INDEX FROM `example_db`.`articles`"]

    def test_a_composite_index_reports_all_columns_once(self, monkeypatch) -> None:
        rows = [
            {"Key_name": "idx_multi", "Column_name": "title", "Index_type": "GIN"},
            {"Key_name": "idx_multi", "Column_name": "body", "Index_type": "GIN"},
        ]
        client, _, _ = make_client(cursor=FakeCursor(rows=rows), monkeypatch=monkeypatch)

        response = client.get("/api/v1/indexes/example_db/articles")

        assert response.status_code == 200
        assert response.json()["indexes"] == [
            {"name": "idx_multi", "columns": ["title", "body"], "type": "GIN", "comment": ""}
        ]

    def test_a_bad_database_identifier_is_refused(self, monkeypatch) -> None:
        client, cursor, _ = make_client(monkeypatch=monkeypatch)

        response = client.get("/api/v1/indexes/example_db%60%3B%20DROP%20TABLE%20x/articles")

        assert response.status_code == 403
        assert cursor.executed == []


class TestValidatePredicate:
    def test_accepts_the_documented_match_any_form(self) -> None:
        client, _, _ = make_client()

        response = client.post(
            "/api/v1/indexes/validate-predicate",
            json={"column": "content", "operator": "MATCH_ANY", "keyword": "machine learning"},
        )

        assert response.status_code == 200
        assert response.json()["predicate"] == "content MATCH_ANY 'machine learning'"

    @pytest.mark.parametrize("operator", ["MATCH", "MATCH_ANY", "MATCH_ALL"])
    def test_accepts_each_documented_predicate(self, operator: str) -> None:
        client, _, _ = make_client()

        response = client.post(
            "/api/v1/indexes/validate-predicate",
            json={"column": "content", "operator": operator, "keyword": "x"},
        )

        assert response.status_code == 200

    def test_rejects_an_unknown_operator(self) -> None:
        client, _, _ = make_client()

        response = client.post(
            "/api/v1/indexes/validate-predicate",
            json={"column": "content", "operator": "MATCH_MAYBE", "keyword": "x"},
        )

        assert response.status_code == 400

    def test_rejects_an_empty_keyword(self) -> None:
        client, _, _ = make_client()

        response = client.post(
            "/api/v1/indexes/validate-predicate",
            json={"column": "content", "operator": "MATCH", "keyword": "   "},
        )

        assert response.status_code == 400


class TestCapabilities:
    def test_reports_the_observed_engine_preconditions(self) -> None:
        client, _, _ = make_client()

        body = client.get("/api/v1/indexes/capabilities").json()

        assert body["kind"] == "inverted"
        assert "english" in body["parsers"]
        assert "clucene" in body["imp_libs"] and "builtin" in body["imp_libs"]
        assert set(body["predicates"]) == {"MATCH", "MATCH_ANY", "MATCH_ALL"}
        ids = {p["id"] for p in body["preconditions"]}
        assert ids == {
            "enable_experimental_gin",
            "replicated_storage",
            "async_schema_change",
            "pushdown_only",
        }
        assert all(p["message"] for p in body["preconditions"])


class TestEndpointsRequireAuth:
    """No dependency override → the bearer dependency rejects with 403/401."""

    def test_create_without_a_token_is_unauthorized(self) -> None:
        app = FastAPI()
        app.include_router(indexes_router.router, prefix="/api/v1/indexes")
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/api/v1/indexes/create",
            json={
                "database": "d",
                "table": "t",
                "index_name": "i",
                "column": "c",
                "kind": "GIN",
                "parser": "english",
            },
        )

        assert response.status_code in (401, 403)
