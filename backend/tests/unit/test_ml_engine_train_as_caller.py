"""Security regression tests for ML training running as the caller (NOVA-104).

Finding #1 (Critical) of the Nova security review in NOVA-103:
``POST /api/v1/ml/train`` never forwarded the caller's identity, so
``MLEngineService.train_model`` always took the ``_fetch_training_data_as_system``
branch and executed the caller-supplied ``training_sql`` on the **root**
StarRocks connection. Any authenticated user could therefore run arbitrary SQL
with root privilege, bypassing StarRocks RBAC.

These tests pin the fixed contract at two levels:

* the real router through ``TestClient`` — the caller's identity must reach the
  service (pre-fix, the service was called with no ``username``/``password``);
* the service with a recording fake connection — training must open the
  caller's connection and ``SET ROLE``, never the root connection for data.

Assembled by hand (routers + dependency override) so no engine, Redis or MinIO
is required, matching ``test_internal_ml_endpoint_auth.py``.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.ml_engine.service import MLEngineService

TRAIN = "/api/v1/ml/train"
CALLER_PASSWORD = "caller-secret"

TRAIN_BODY = {
    "model_name": "churn_model",
    "model_type": "classification",
    "algorithm": "logistic",
    "training_sql": "SELECT age, churned FROM customers",
    "target_column": "churned",
    "feature_columns": ["age"],
    "test_size": 0.2,
    "database_name": "analytics",
}

TRAIN_RESULT = {
    "model_id": "model-1",
    "model_name": "churn_model",
    "model_type": "classification",
    "algorithm": "logistic",
    "version": 1,
    "status": "active",
    "training_rows": 42,
    "feature_columns": ["age"],
    "metrics": {"accuracy": 0.9},
    "message": "ok",
}


def _caller() -> dict:
    return {
        "username": "analyst",
        "session_id": "sess-1",
        "roles": ["analyst_role"],
        "active_role": "analyst_role",
        "encrypted_password": "fernet-blob",
    }


class TestRouterForwardsCallerIdentity:
    """AC 1: the HTTP endpoint must hand the caller's identity to the service."""

    @pytest.fixture
    def app_and_calls(self, monkeypatch):
        from app.core import deps as deps_module
        from app.modules.ml_engine import service as service_module
        from app.modules.ml_engine.router import router as ml_router

        calls: list[dict] = []

        async def fake_train_model(**kwargs):
            calls.append(kwargs)
            return TRAIN_RESULT

        monkeypatch.setattr(service_module.ml_engine_service, "train_model", fake_train_model)
        # The router decrypts the session blob; keep the test off the Fernet key.
        monkeypatch.setattr(
            "app.modules.ml_engine.router.decrypt_password",
            lambda encrypted: CALLER_PASSWORD,
        )

        async def fake_current_user():
            return _caller()

        app = FastAPI()
        app.include_router(ml_router, prefix="/api/v1/ml")
        app.dependency_overrides[deps_module.get_current_user] = fake_current_user
        return app, calls

    def test_caller_identity_reaches_the_service(self, app_and_calls):
        app, calls = app_and_calls
        with TestClient(app) as client:
            resp = client.post(TRAIN, json=TRAIN_BODY)

        assert resp.status_code == 200, resp.text
        assert len(calls) == 1
        kwargs = calls[0]
        # Pre-fix these three were absent, which is exactly what forced the
        # engine onto the root connection.
        assert kwargs["username"] == "analyst"
        assert kwargs["password"] == CALLER_PASSWORD
        assert kwargs["role"] == "analyst_role"
        assert kwargs["created_by"] == "analyst"

    def test_password_is_never_echoed_back(self, app_and_calls):
        app, _ = app_and_calls
        with TestClient(app) as client:
            resp = client.post(TRAIN, json=TRAIN_BODY)

        assert CALLER_PASSWORD not in resp.text


class _RecordingCursor:
    """Minimal asyncmy cursor stand-in that records every statement."""

    def __init__(self, rows: list | None = None) -> None:
        self.executed: list[str] = []
        self._rows = rows if rows is not None else []
        self.description = [(key,) for key in self._rows[0]] if self._rows else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.executed.append(sql)
        return 1

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return (1,)


class _UserConnFactory:
    """Records ``db.user_conn`` calls and exposes the cursor it handed out."""

    def __init__(self, training_rows: list[dict]) -> None:
        self.opened: list[dict] = []
        self.cursor = _RecordingCursor(training_rows)

    def __call__(self, username, password, database=None):
        self.opened.append({"username": username, "password": password, "database": database})
        cursor = self.cursor

        class _Conn:
            def cursor(self, *args, **kwargs):
                return cursor

            def close(self) -> None:
                pass

        class _Ctx:
            async def __aenter__(self):
                return _Conn()

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _training_rows() -> list[dict]:
    return [{"age": float(i), "churned": float(i % 2)} for i in range(30)]


def _pretrain_service(monkeypatch, user_conn) -> MLEngineService:
    """Service with stage configs and the post-training metadata write stubbed.

    Only the data-fetch connection is under test, so the root connection used
    for the metadata INSERT is replaced with a throwaway fake.
    """
    monkeypatch.setattr("app.modules.query.sql_pipeline.get_credential_params", lambda *a, **kw: {})

    async def no_stage_configs(_self, database_name):
        return {}

    monkeypatch.setattr(MLEngineService, "_load_stage_configs", no_stage_configs)
    monkeypatch.setattr("app.modules.ml_engine.service.db.user_conn", user_conn)

    async def metadata_conn():
        return _MetadataConn()

    svc = MLEngineService()
    monkeypatch.setattr(svc, "_connect", metadata_conn)
    return svc


class _MetadataConn:
    """Serves only the post-training metadata INSERTs on the root connection."""

    def __init__(self) -> None:
        self.cursor_obj = _RecordingCursor()

    def cursor(self, *args, **kwargs):
        return self.cursor_obj

    def close(self) -> None:
        pass


async def _train_as_caller(svc: MLEngineService) -> dict:
    return await svc.train_model(
        model_name="m",
        model_type="classification",
        algorithm="logistic",
        training_sql="SELECT age, churned FROM customers",
        target_column="churned",
        feature_columns=["age"],
        hyperparameters=None,
        test_size=0.0,
        database_name="analytics",
        created_by="analyst",
        username="analyst",
        password=CALLER_PASSWORD,
        role="analyst_role",
    )


class TestTrainingUsesTheCallerConnection:
    """AC 2: the engine connection used for training is the caller's."""

    async def test_user_conn_is_used(self, monkeypatch):
        factory = _UserConnFactory(_training_rows())
        svc = _pretrain_service(monkeypatch, factory)

        await _train_as_caller(svc)

        assert factory.opened == [
            {"username": "analyst", "password": CALLER_PASSWORD, "database": "analytics"}
        ]

    async def test_role_is_set_on_the_caller_connection(self, monkeypatch):
        factory = _UserConnFactory(_training_rows())
        svc = _pretrain_service(monkeypatch, factory)

        await _train_as_caller(svc)

        assert "SET ROLE analyst_role" in factory.cursor.executed
        # The caller's SELECT is what actually ran on that connection.
        assert "SELECT age, churned FROM customers" in factory.cursor.executed

    async def test_system_fetch_is_never_invoked_for_an_http_caller(self, monkeypatch):
        async def forbid_system(**kwargs):
            raise AssertionError("HTTP training must not use the system/root fetch")

        monkeypatch.setattr(MLEngineService, "_fetch_training_data_as_system", forbid_system)

        factory = _UserConnFactory(_training_rows())
        svc = _pretrain_service(monkeypatch, factory)

        await _train_as_caller(svc)


class TestMissingIdentityFailsClosed:
    """AC 4: no silent root fallback when a caller omits its identity."""

    async def test_no_credentials_is_refused(self, monkeypatch):
        svc = MLEngineService()

        async def forbid_system(**kwargs):
            raise AssertionError("must not fall back to the system connection")

        async def forbid_user(**kwargs):
            raise AssertionError("must not use a user connection without credentials")

        monkeypatch.setattr(MLEngineService, "_fetch_training_data_as_system", forbid_system)
        monkeypatch.setattr(MLEngineService, "_fetch_training_data_as_user", forbid_user)
        monkeypatch.setattr(
            "app.modules.query.sql_pipeline.get_credential_params", lambda *a, **kw: {}
        )

        async def no_stage_configs(_self, database_name):
            return {}

        monkeypatch.setattr(MLEngineService, "_load_stage_configs", no_stage_configs)

        with pytest.raises(ValueError, match="requires caller credentials"):
            await svc.train_model(
                model_name="m",
                model_type="classification",
                algorithm="logistic",
                training_sql="SELECT age, churned FROM customers",
                target_column="churned",
                feature_columns=["age"],
                hyperparameters=None,
                test_size=0.0,
                database_name=None,
                username=None,
                password=None,
            )

    async def test_system_path_requires_explicit_flag(self, monkeypatch):
        """The root path is reachable only via ``as_system=True``."""
        used = {}

        async def system_fetch(**kwargs):
            used["hit"] = True
            return _training_rows(), ["age", "churned"]

        monkeypatch.setattr(
            MLEngineService, "_fetch_training_data_as_system", staticmethod(system_fetch)
        )
        monkeypatch.setattr(
            "app.modules.query.sql_pipeline.get_credential_params", lambda *a, **kw: {}
        )

        async def no_stage_configs(_self, database_name):
            return {}

        monkeypatch.setattr(MLEngineService, "_load_stage_configs", no_stage_configs)

        svc = MLEngineService()

        async def metadata_conn():
            return _MetadataConn()

        monkeypatch.setattr(svc, "_connect", metadata_conn)

        await svc.train_model(
            model_name="m",
            model_type="classification",
            algorithm="logistic",
            training_sql="SELECT age, churned FROM customers",
            target_column="churned",
            feature_columns=["age"],
            hyperparameters=None,
            test_size=0.0,
            database_name=None,
            as_system=True,
        )

        assert used.get("hit") is True
