"""Regression tests for NOVA-104 — ML training must run as the caller.

Finding #1 (Critical) of the NOVA-103 security audit: ``POST /api/v1/ml/train``
never forwarded the caller's identity, so ``train_model`` always took the
``_fetch_training_data_as_system`` branch and executed the caller's SQL on the
**root** StarRocks connection — a full RBAC bypass.

These tests pin the fixed contract at the public boundary:

* the HTTP route forwards the session identity, and training fetches rows on the
  caller's connection (not root);
* a request with no session identity fails closed rather than falling back to
  root;
* the system branch is reachable only through the explicit
  ``allow_system_fetch`` opt-in.

The router is assembled by hand (dependency override) so no engine, Redis or
MinIO is required, matching the pattern of ``test_internal_ml_endpoint_auth.py``.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

TRAIN_PATH = "/api/v1/ml/train"
SECRET_PASSWORD = "s3cr3t-caller-password"

REQUEST_BODY = {
    "model_name": "churn_model",
    "model_type": "regression",
    "algorithm": "linear",
    "training_sql": "SELECT age, churned FROM customers",
    "target_column": "churned",
    "feature_columns": ["age"],
    "test_size": 0.2,
}

TRAIN_RESULT = {
    "model_id": "m1",
    "model_name": "churn_model",
    "model_type": "regression",
    "algorithm": "linear",
    "version": 1,
    "status": "READY",
    "training_rows": 30,
    "feature_columns": ["age"],
    "metrics": {},
}


def _user(roles=("analyst",), active_role="analyst"):
    return {
        "username": "analyst",
        "session_id": "sess-1",
        "roles": list(roles),
        "active_role": active_role,
        "encrypted_password": "enc",
    }


@pytest.fixture
def train_app(monkeypatch):
    """The real ML router with the service captured, and no engine required."""
    from app.core import deps as deps_module
    from app.modules.ml_engine import router as router_module
    from app.modules.ml_engine import service as service_module
    from app.modules.ml_engine.router import router as ml_router

    captured: dict = {}

    async def fake_train_model(**kwargs):
        captured.update(kwargs)
        return TRAIN_RESULT

    monkeypatch.setattr(service_module.ml_engine_service, "train_model", fake_train_model)
    # The session stores an encrypted password; the route decrypts it before
    # forwarding. Patch the name the router actually calls so the test does not
    # depend on a real Fernet key.
    monkeypatch.setattr(
        router_module, "decrypt_password", lambda _enc: SECRET_PASSWORD
    )

    async def caller():
        return _user()

    app = FastAPI()
    app.include_router(ml_router, prefix="/api/v1/ml")
    app.dependency_overrides[deps_module.get_current_user] = caller
    return app, captured


class TestTrainForwardsCallerIdentity:
    """Acceptance criterion 1: the route passes the caller's identity through."""

    def test_route_sends_username_password_and_role(self, train_app):
        app, captured = train_app
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(TRAIN_PATH, json=REQUEST_BODY)

        assert resp.status_code == 200, resp.text
        assert captured["username"] == "analyst"
        assert captured["password"] == SECRET_PASSWORD
        assert captured["role"] == "analyst"

    def test_route_does_not_opt_into_the_system_connection(self, train_app):
        """The pre-fix hole: no identity → root. The route must never do that."""
        app, captured = train_app
        with TestClient(app, raise_server_exceptions=False) as client:
            client.post(TRAIN_PATH, json=REQUEST_BODY)

        assert captured.get("allow_system_fetch", False) is False


class TestServiceFailsClosedWithoutIdentity:
    """Acceptance criterion 4: no identity is not the same as "use root"."""

    async def test_train_without_identity_raises(self, monkeypatch):
        from app.modules.ml_engine.service import MLEngineService

        svc = MLEngineService()

        async def no_stage_configs(_self, database_name):
            return {}

        monkeypatch.setattr(MLEngineService, "_load_stage_configs", no_stage_configs)

        async def forbidden_system_fetch(**_kwargs):  # pragma: no cover - must not run
            raise AssertionError("system fetch must not be reached")

        monkeypatch.setattr(
            MLEngineService,
            "_fetch_training_data_as_system",
            staticmethod(forbidden_system_fetch),
        )

        with pytest.raises(ValueError, match="credentials"):
            await svc.train_model(
                model_name="m",
                model_type="regression",
                algorithm="linear",
                training_sql="SELECT age, churned FROM customers",
                target_column="churned",
                feature_columns=["age"],
                hyperparameters=None,
                test_size=0.2,
                database_name=None,
            )

    async def test_explicit_system_fetch_still_allowed(self, monkeypatch):
        """The opt-in keeps the branch available to a deliberate internal caller."""
        from app.modules.ml_engine.service import MLEngineService

        svc = MLEngineService()
        used: dict = {}

        async def no_stage_configs(_self, database_name):
            return {}

        monkeypatch.setattr(MLEngineService, "_load_stage_configs", no_stage_configs)

        async def fake_system_fetch(**_kwargs):
            used["called"] = True
            rows = [{"age": 1.0, "churned": 1.0} for _ in range(30)]
            return rows, ["age", "churned"]

        monkeypatch.setattr(
            MLEngineService,
            "_fetch_training_data_as_system",
            staticmethod(fake_system_fetch),
        )

        class _Cursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, sql, params=None):
                return 1

            async def fetchone(self):
                return (1,)

        class _Conn:
            def cursor(self, *args, **kwargs):
                return _Cursor()

            def close(self):
                pass

        async def fake_connect():
            return _Conn()

        monkeypatch.setattr(svc, "_connect", fake_connect)

        result = await svc.train_model(
            model_name="m",
            model_type="regression",
            algorithm="linear",
            training_sql="SELECT age, churned FROM customers",
            target_column="churned",
            feature_columns=["age"],
            hyperparameters=None,
            test_size=0.0,
            database_name=None,
            allow_system_fetch=True,
        )

        assert used.get("called") is True
        assert result["model_name"] == "m"


class TestCallerConnectionIsUsed:
    """Acceptance criterion 2: the engine connection is the caller's, not root."""

    async def test_training_fetches_on_the_user_connection(self, monkeypatch):
        from app.core import database as database_module
        from app.modules.ml_engine.service import MLEngineService

        svc = MLEngineService()
        used: dict = {}

        async def no_stage_configs(_self, database_name):
            return {}

        monkeypatch.setattr(MLEngineService, "_load_stage_configs", no_stage_configs)

        class _Cursor:
            description = None

            def __init__(self) -> None:
                self.statements: list[str] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, sql, params=None):
                self.statements.append(sql)
                return 1

            async def fetchall(self):
                return [{"age": float(i), "churned": float(i % 2)} for i in range(30)]

            async def fetchone(self):
                return (1,)

        class _UserConn:
            def __init__(self) -> None:
                self.cursor_obj = _Cursor()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def cursor(self, *args, **kwargs):
                return self.cursor_obj

            def close(self):
                pass

        class _Conn(_UserConn):
            pass

        user_conn = _UserConn()

        def fake_user_conn(username, password, database=None):
            used["username"] = username
            used["password"] = password
            return user_conn

        monkeypatch.setattr(database_module.db, "user_conn", fake_user_conn)

        # Root is still used to *persist* model metadata, but the training rows
        # must come from the caller's connection. Fail the system-fetch helper
        # so a regression to root-fetch is caught directly.
        async def forbidden_system_fetch(**_kwargs):  # pragma: no cover - must not run
            raise AssertionError("training data must not be fetched on the root connection")

        monkeypatch.setattr(
            MLEngineService,
            "_fetch_training_data_as_system",
            staticmethod(forbidden_system_fetch),
        )

        async def fake_connect():
            return _Conn()

        monkeypatch.setattr(svc, "_connect", fake_connect)

        result = await svc.train_model(
            model_name="m",
            model_type="regression",
            algorithm="linear",
            training_sql="SELECT age, churned FROM customers",
            target_column="churned",
            feature_columns=["age"],
            hyperparameters=None,
            test_size=0.0,
            database_name=None,
            username="analyst",
            password=SECRET_PASSWORD,
            role="analyst",
        )

        assert used["username"] == "analyst"
        assert used["password"] == SECRET_PASSWORD
        # SET ROLE ran on the caller's own connection before the training SQL.
        assert any(s.startswith("SET ROLE") for s in user_conn.cursor_obj.statements)
        assert result["model_name"] == "m"
