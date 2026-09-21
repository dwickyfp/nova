"""Security regression tests for user-scoped native ML extraction (NOVA-104)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.ml_engine.data.mysql_fallback import MySQLBatchDataSource
from app.modules.ml_engine.service import MLEngineService
from app.modules.ml_engine.spec import MLSecurityContext

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
            response = client.post(TRAIN, json=TRAIN_BODY)

        assert response.status_code == 200, response.text
        assert calls[0]["username"] == "analyst"
        assert calls[0]["password"] == CALLER_PASSWORD
        assert calls[0]["role"] == "analyst_role"
        assert calls[0]["created_by"] == "analyst"

    def test_password_is_never_echoed_back(self, app_and_calls):
        app, _ = app_and_calls
        with TestClient(app) as client:
            response = client.post(TRAIN, json=TRAIN_BODY)
        assert CALLER_PASSWORD not in response.text


class _RecordingCursor:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.description = [("age",), ("churned",)]
        self._batches = [[(20.0, 0), (30.0, 1)], []]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        del params
        self.executed.append(sql)

    async def fetchmany(self, size):
        assert size > 0
        return self._batches.pop(0)


class _UserConnectionFactory:
    def __init__(self) -> None:
        self.opened: list[dict] = []
        self.cursor = _RecordingCursor()

    def __call__(self, username, password, database=None):
        self.opened.append({"username": username, "password": password, "database": database})
        cursor = self.cursor

        class Connection:
            def cursor(self):
                return cursor

        class Context:
            async def __aenter__(self):
                return Connection()

            async def __aexit__(self, *exc):
                return False

        return Context()


@pytest.mark.asyncio
async def test_mysql_fallback_executes_as_caller_and_sets_role(monkeypatch):
    factory = _UserConnectionFactory()
    monkeypatch.setattr("app.modules.ml_engine.data.mysql_fallback.db.user_conn", factory)
    source = MySQLBatchDataSource(batch_size=10)
    security = MLSecurityContext(
        username="analyst",
        password=CALLER_PASSWORD,
        database="analytics",
        role="analyst_role",
    )

    batches = [batch async for batch in source.stream(TRAIN_BODY["training_sql"], security)]

    assert factory.opened == [
        {
            "username": "analyst",
            "password": CALLER_PASSWORD,
            "database": "analytics",
        }
    ]
    assert factory.cursor.executed == [
        "SET ROLE analyst_role",
        "SELECT age, churned FROM customers",
    ]
    assert batches[0].num_rows == 2


@pytest.mark.asyncio
async def test_missing_identity_fails_closed_before_extraction():
    service = MLEngineService()
    with pytest.raises(ValueError, match="requires caller credentials"):
        await service.train_model(
            model_name="m",
            model_type="classification",
            algorithm="auto",
            training_sql="SELECT age, churned FROM customers",
            target_column="churned",
            feature_columns=["age"],
            hyperparameters=None,
            test_size=0.2,
            database_name="analytics",
        )
