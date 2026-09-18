"""Regression tests for NOVA-118 — batch prediction must run as the caller.

``POST /api/v1/ml/predict/batch`` accepted a caller-supplied ``prediction_sql``
and executed it on the **root** StarRocks connection: no caller identity was
forwarded, so the statement ran as a superuser. QA proved the reachable surface
included ``SELECT * FROM mysql.user``, cross-tenant schemas, ``SET ROLE
ACCOUNTADMIN`` and non-destructive DDL/DML — the same RBAC-bypass class as
NOVA-104, on the sibling endpoint the training fix left behind.

These tests pin the fixed contract at the public boundary:

* the HTTP route forwards the session identity, and the prediction SQL runs on
  the caller's connection (not root);
* the root connection is used only to read Nova's own model/alias metadata;
* a request with no session identity fails closed rather than falling back to
  root;
* the system branch is reachable only through the explicit ``as_system``
  opt-in.

The router is assembled by hand (dependency override) so no engine, Redis or
MinIO is required, matching ``test_internal_ml_endpoint_auth.py`` and
``test_ml_engine_train_as_caller.py``.
"""

from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BATCH_PATH = "/api/v1/ml/predict/batch"
SECRET_PASSWORD = "s3cr3t-caller-password"
CALLER_SQL = "SELECT age FROM customers"

REQUEST_BODY = {
    "model_alias": "churn_model",
    "prediction_sql": CALLER_SQL,
    "database_name": "prod_db",
}

#: Statements the guard admits today (QA's NOVA-115 evidence). They must reach
#: the *caller's* connection — StarRocks then decides — never the root one.
QA_ALLOWED_STATEMENTS = [
    "SELECT * FROM NOVA_SYSTEM.CONFIG_STAGES",
    "SELECT * FROM mysql.user",
    "SELECT * FROM other_tenant_schema.secret_table",
    "SET ROLE ACCOUNTADMIN",
    "CREATE TABLE t (x int)",
    "INSERT INTO t VALUES (1)",
    "GRANT SELECT ON *.* TO ROLE analyst",
    "SELECT 1",
]


def _user():
    return {
        "username": "analyst",
        "session_id": "sess-1",
        "roles": ["analyst"],
        "active_role": "analyst",
        "encrypted_password": "enc",
    }


class _Cursor:
    """Async cursor stand-in that records every statement it is given."""

    description = None

    def __init__(self, rows=None, ones=None) -> None:
        self.statements: list[str] = []
        self._rows = rows if rows is not None else []
        self._ones = list(ones) if ones is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.statements.append(sql)
        return 1

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._ones.pop(0) if self._ones else None


class _RootSpy:
    """Records what the root connection is asked to run.

    The NOVA-118 invariant is structural: whatever the caller sent must not
    appear among the root connection's statements.
    """

    def __init__(self, cursor) -> None:
        self.cursor_obj = cursor
        self.closed = False

    def cursor(self, *args, **kwargs):
        return self.cursor_obj

    def close(self):
        self.closed = True


def _metadata_root():
    """A root spy serving the alias lookup then the version lookup.

    ``batch_predict`` opens a single cursor off the root connection and reuses
    it for both metadata reads, so the fake hands out one cursor whose
    ``fetchone`` yields the alias row first and the version row second.
    """
    cursor = _Cursor(
        ones=[
            {"model_id": "m1", "version": 1, "model_name": "m", "model_type": "regression"},
            {"model_binary": "e30="},
        ]
    )
    return _RootSpy(cursor), cursor


def _caller_conn(rows=None):
    """A ``db.user_conn`` stand-in the service opens as the caller."""
    rows = rows if rows is not None else [{"age": 1.0}]
    cursor = _Cursor(rows=rows)

    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def cursor(self, *args, **kwargs):
            return cursor

        def close(self):
            pass

    return _Conn(), cursor


@pytest.fixture
def service(monkeypatch):
    from app.modules.ml_engine.service import MLEngineService

    async def no_stage_configs(_self, database_name):
        return {}

    monkeypatch.setattr(MLEngineService, "_load_stage_configs", no_stage_configs)
    return MLEngineService()


def _patch_user_conn(monkeypatch, conn, calls=None):
    from app.core import database as database_module

    def fake_user_conn(username, password, database=None):
        if calls is not None:
            calls.append({"username": username, "password": password, "database": database})
        return conn

    monkeypatch.setattr(database_module.db, "user_conn", fake_user_conn)


class TestRouteForwardsCallerIdentity:
    """The route must hand the caller's identity to the service."""

    def _client(self, monkeypatch, captured):
        from app.core import deps as deps_module
        from app.modules.ml_engine import router as router_module
        from app.modules.ml_engine import service as service_module
        from app.modules.ml_engine.router import router as ml_router

        async def fake_batch_predict(**kwargs):
            captured.update(kwargs)
            return {
                "model_alias": "churn_model",
                "model_name": "churn_model",
                "predictions": [],
                "total_rows": 0,
            }

        monkeypatch.setattr(
            service_module.ml_engine_service, "batch_predict", fake_batch_predict
        )
        monkeypatch.setattr(router_module, "decrypt_password", lambda _enc: SECRET_PASSWORD)

        async def caller():
            return _user()

        app = FastAPI()
        app.include_router(ml_router, prefix="/api/v1/ml")
        app.dependency_overrides[deps_module.get_current_user] = caller
        return app

    def test_route_sends_username_password_and_role(self, monkeypatch):
        captured: dict = {}
        app = self._client(monkeypatch, captured)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(BATCH_PATH, json=REQUEST_BODY)

        assert resp.status_code == 200, resp.text
        assert captured["username"] == "analyst"
        assert captured["password"] == SECRET_PASSWORD
        assert captured["role"] == "analyst"
        assert captured["prediction_sql"] == CALLER_SQL
        # The route must never opt into the root connection on the caller's behalf.
        assert captured.get("as_system", False) is False

    def test_password_is_not_echoed_back(self, monkeypatch):
        captured: dict = {}
        app = self._client(monkeypatch, captured)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(BATCH_PATH, json=REQUEST_BODY)

        assert SECRET_PASSWORD not in resp.text

    def test_route_without_readable_credential_fails_closed(self, monkeypatch):
        """A session with no decryptable password is 401, not a root execution."""
        from app.core import deps as deps_module
        from app.modules.ml_engine import router as router_module
        from app.modules.ml_engine.router import router as ml_router

        def boom(_enc):
            raise ValueError("no key")

        monkeypatch.setattr(router_module, "decrypt_password", boom)

        async def caller():
            return _user()

        app = FastAPI()
        app.include_router(ml_router, prefix="/api/v1/ml")
        app.dependency_overrides[deps_module.get_current_user] = caller

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(BATCH_PATH, json=REQUEST_BODY)

        assert resp.status_code == 401, resp.text


class TestPredictionSqlNeverReachesRoot:
    """A root-connection spy must never see the caller's SQL."""

    @pytest.mark.parametrize("sql", QA_ALLOWED_STATEMENTS)
    async def test_qa_statements_only_reach_the_caller_connection(
        self, service, monkeypatch, sql
    ):
        root, alias_cursor = _metadata_root()
        user_conn, user_cursor = _caller_conn()
        _patch_user_conn(monkeypatch, user_conn)

        async def fake_connect():
            return root

        monkeypatch.setattr(service, "_connect", fake_connect)

        # The model bundle is opaque here; the point is where the SQL ran, not
        # the prediction. Fail deserialisation after the fetch and assert on the
        # recorded statements.
        with mock.patch(
            "app.modules.ml_engine.service.joblib.load",
            side_effect=RuntimeError("stop-after-fetch"),
        ), pytest.raises(RuntimeError, match="stop-after-fetch"):
            await service.batch_predict(
                model_alias="churn_model",
                prediction_sql=sql,
                database_name="prod_db",
                username="analyst",
                password=SECRET_PASSWORD,
                role="analyst",
            )

        # The caller's statement ran on the caller connection...
        assert sql in user_cursor.statements
        # ...and never on the root connection.
        assert all(sql not in stmt for stmt in alias_cursor.statements)
        # Root was only touched for Nova's own metadata.
        assert all(
            "NOVA_SYSTEM.ML_MODEL" in stmt for stmt in alias_cursor.statements
        )

    async def test_no_credential_fails_closed_not_root(self, service, monkeypatch):
        root, alias_cursor = _metadata_root()

        async def fake_connect():
            return root

        monkeypatch.setattr(service, "_connect", fake_connect)

        with pytest.raises(ValueError, match="credentials"):
            await service.batch_predict(
                model_alias="churn_model",
                prediction_sql=CALLER_SQL,
                database_name=None,
            )

        # The caller's SQL must not have been executed anywhere.
        assert all(CALLER_SQL not in stmt for stmt in alias_cursor.statements)


class TestCallerConnectionIsUsed:
    """The engine connection for prediction data is the caller's, not root."""

    async def test_prediction_fetches_on_the_user_connection(self, service, monkeypatch):
        root, _alias_cursor = _metadata_root()
        calls: list[dict] = []
        user_conn, user_cursor = _caller_conn()
        _patch_user_conn(monkeypatch, user_conn, calls)

        async def fake_connect():
            return root

        monkeypatch.setattr(service, "_connect", fake_connect)

        with mock.patch(
            "app.modules.ml_engine.service.joblib.load",
            side_effect=RuntimeError("stop-after-fetch"),
        ), pytest.raises(RuntimeError, match="stop-after-fetch"):
            await service.batch_predict(
                model_alias="churn_model",
                prediction_sql=CALLER_SQL,
                database_name="prod_db",
                username="analyst",
                password=SECRET_PASSWORD,
                role="analyst",
            )

        assert calls == [
            {"username": "analyst", "password": SECRET_PASSWORD, "database": "prod_db"}
        ]
        # SET ROLE runs on the caller's own connection before the SQL.
        assert "SET ROLE analyst" in user_cursor.statements
        assert CALLER_SQL in user_cursor.statements

    async def test_explicit_system_fetch_still_allowed(self, service, monkeypatch):
        """The opt-in keeps the branch available to a deliberate internal caller."""
        root, _alias_cursor = _metadata_root()

        async def fake_connect():
            return root

        monkeypatch.setattr(service, "_connect", fake_connect)

        used = {}

        async def fake_system_fetch(*, database_name, prediction_sql):
            used["called"] = True
            return [], []

        monkeypatch.setattr(service, "_fetch_prediction_data_as_system", fake_system_fetch)

        result = await service.batch_predict(
            model_alias="churn_model",
            prediction_sql=CALLER_SQL,
            database_name=None,
            as_system=True,
        )

        assert used.get("called") is True
        assert result["total_rows"] == 0
