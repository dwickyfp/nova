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
* the system branch is reachable only through the explicit
  ``allow_system_fetch`` opt-in.

The router is assembled by hand (dependency override) so no engine, Redis or
MinIO is required, matching ``test_internal_ml_endpoint_auth.py`` and
``test_nova104_ml_train_caller_connection.py``.
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


def _user(roles=("analyst",), active_role="analyst"):
    return {
        "username": "analyst",
        "session_id": "sess-1",
        "roles": list(roles),
        "active_role": active_role,
        "encrypted_password": "enc",
    }


class _Cursor:
    """Async cursor stand-in that records every statement it is given."""

    description = None

    def __init__(self, rows=None, one=None) -> None:
        self.statements: list[str] = []
        self._rows = rows if rows is not None else []
        self._one = one
        self._ones = list(one) if isinstance(one, list) else None

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
        if self._ones is not None:
            return self._ones.pop(0)
        return self._one


class _FakeModel:
    """The smallest stand-in for a deserialized sklearn model."""

    def predict(self, X):
        return [0 for _ in range(len(X))]


class _RootSpy:
    """Records what the root connection is asked to run.

    The NOVA-118 invariant is structural: whatever the caller sent must not
    appear among the root connection's statements.
    """

    def __init__(self, rows=None, one=None) -> None:
        self.cursor_obj = _Cursor(rows=rows, one=one)
        self.closed = False

    def cursor(self, *args, **kwargs):
        return self.cursor_obj

    def close(self):
        self.closed = True


#: The two rows the root metadata read must yield, in order: alias, then
#: version binary.
_ALIAS_ROW = {"model_id": "m1", "version": 1, "model_name": "c", "model_type": "r"}
_VERSION_ROW = {"model_binary": "e30="}


def _root_with_alias_and_version() -> _RootSpy:
    """A root-connection spy whose cursor answers alias then version, in order."""
    return _RootSpy(one=[_ALIAS_ROW, _VERSION_ROW])


def _caller_conn(rows):
    """A ``db.user_conn`` stand-in the service opens as the caller."""

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


class TestRouteForwardsCallerIdentity:
    """Acceptance criterion 1: the route passes the caller's identity through."""

    def test_route_sends_username_password_and_role(self, monkeypatch):
        from app.core import deps as deps_module
        from app.modules.ml_engine import router as router_module
        from app.modules.ml_engine import service as service_module
        from app.modules.ml_engine.router import router as ml_router

        captured: dict = {}

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
        monkeypatch.setattr(
            router_module, "decrypt_password", lambda _enc: SECRET_PASSWORD
        )

        async def caller():
            return _user()

        app = FastAPI()
        app.include_router(ml_router, prefix="/api/v1/ml")
        app.dependency_overrides[deps_module.get_current_user] = caller

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(BATCH_PATH, json=REQUEST_BODY)

        assert resp.status_code == 200, resp.text
        assert captured["username"] == "analyst"
        assert captured["password"] == SECRET_PASSWORD
        assert captured["role"] == "analyst"
        assert captured["prediction_sql"] == CALLER_SQL

    def test_route_does_not_opt_into_the_system_connection(self, monkeypatch):
        """The pre-fix hole: no identity → root. The route must never opt in."""
        from app.core import deps as deps_module
        from app.modules.ml_engine import router as router_module
        from app.modules.ml_engine import service as service_module
        from app.modules.ml_engine.router import router as ml_router

        captured: dict = {}

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
        monkeypatch.setattr(
            router_module, "decrypt_password", lambda _enc: SECRET_PASSWORD
        )

        async def caller():
            return _user()

        app = FastAPI()
        app.include_router(ml_router, prefix="/api/v1/ml")
        app.dependency_overrides[deps_module.get_current_user] = caller

        with TestClient(app, raise_server_exceptions=False) as client:
            client.post(BATCH_PATH, json=REQUEST_BODY)

        assert captured.get("allow_system_fetch", False) is False

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
    """Acceptance criterion 4: a root-connection spy sees no caller SQL."""

    @pytest.mark.parametrize("sql", QA_ALLOWED_STATEMENTS)
    async def test_qa_statements_only_reach_the_caller_connection(
        self, service, monkeypatch, sql
    ):
        from app.core import database as database_module

        root = _root_with_alias_and_version()

        used: dict = {}

        # One row so the flow reaches model deserialization; the identity of the
        # connection the SQL ran on is the assertion, not the prediction.
        user_conn_obj, _user_cursor = _caller_conn([{"age": 1.0}])

        def fake_user_conn(username, password, database=None):
            used["username"] = username
            used["password"] = password
            used["database"] = database
            return user_conn_obj

        monkeypatch.setattr(database_module.db, "user_conn", fake_user_conn)

        async def fake_connect():
            return root

        monkeypatch.setattr(service, "_connect", fake_connect)

        with mock.patch(
            "app.modules.ml_engine.service.joblib.load",
            return_value={"model": _FakeModel(), "feature_columns": ["age"]},
        ):
            await service.batch_predict(
                model_alias="churn_model",
                prediction_sql=sql,
                database_name="prod_db",
                username="analyst",
                password=SECRET_PASSWORD,
                role="analyst",
            )

        # The caller's statement ran on the caller connection...
        assert sql in _user_cursor.statements
        # ...and never on the root connection.
        assert all(sql not in stmt for stmt in root.cursor_obj.statements)
        # Root was only touched for Nova's own metadata.
        assert all(
            "NOVA_SYSTEM.ML_MODEL" in stmt or stmt.startswith("USE ")
            for stmt in root.cursor_obj.statements
        )

    async def test_no_credential_fails_closed_not_root(self, service, monkeypatch):
        root = _root_with_alias_and_version()

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
        assert all(CALLER_SQL not in stmt for stmt in root.cursor_obj.statements)


class TestCallerConnectionIsUsed:
    """Acceptance criterion 2: the engine connection is the caller's, not root."""

    async def test_prediction_fetches_on_the_user_connection(self, service, monkeypatch):
        from app.core import database as database_module

        root = _root_with_alias_and_version()

        used: dict = {}
        user_conn_obj, user_cursor = _caller_conn([{"age": 1.0}])

        def fake_user_conn(username, password, database=None):
            used["username"] = username
            used["password"] = password
            used["database"] = database
            return user_conn_obj

        monkeypatch.setattr(database_module.db, "user_conn", fake_user_conn)

        async def fake_connect():
            return root

        monkeypatch.setattr(service, "_connect", fake_connect)

        with mock.patch(
            "app.modules.ml_engine.service.joblib.load",
            return_value={"model": _FakeModel(), "feature_columns": ["age"]},
        ):
            await service.batch_predict(
                model_alias="churn_model",
                prediction_sql=CALLER_SQL,
                database_name="prod_db",
                username="analyst",
                password=SECRET_PASSWORD,
                role="analyst",
            )

        assert used["username"] == "analyst"
        assert used["password"] == SECRET_PASSWORD
        assert used["database"] == "prod_db"
        # SET ROLE runs on the caller's own connection before the SQL.
        assert any(s.startswith("SET ROLE") for s in user_cursor.statements)
        assert CALLER_SQL in user_cursor.statements

    async def test_explicit_system_fetch_still_allowed(self, service, monkeypatch):
        """The opt-in keeps the branch available to a deliberate internal caller."""
        root = _root_with_alias_and_version()

        used: dict = {}

        async def fake_system_fetch(**_kwargs):
            used["called"] = True
            return [], []

        monkeypatch.setattr(
            service, "_fetch_prediction_data_as_system", fake_system_fetch
        )

        async def fake_connect():
            return root

        monkeypatch.setattr(service, "_connect", fake_connect)

        result = await service.batch_predict(
            model_alias="churn_model",
            prediction_sql=CALLER_SQL,
            database_name=None,
            allow_system_fetch=True,
        )

        assert used.get("called") is True
        assert result["total_rows"] == 0
