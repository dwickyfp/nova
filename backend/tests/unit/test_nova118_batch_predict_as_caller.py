"""Regression tests for user-scoped, vectorized batch prediction (NOVA-118)."""

import pyarrow as pa
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.ml_engine.execution.job_runner import InlineJobRunner
from app.modules.ml_engine.service import MLEngineService

BATCH_PATH = "/api/v1/ml/predict/batch"
SECRET_PASSWORD = "s3cr3t-caller-password"
CALLER_SQL = "SELECT age FROM customers"

REQUEST_BODY = {
    "model_alias": "churn_model",
    "prediction_sql": CALLER_SQL,
    "database_name": "prod_db",
}

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


class TestRouteForwardsCallerIdentity:
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

        monkeypatch.setattr(service_module.ml_engine_service, "batch_predict", fake_batch_predict)
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
            response = client.post(BATCH_PATH, json=REQUEST_BODY)

        assert response.status_code == 200, response.text
        assert captured["username"] == "analyst"
        assert captured["password"] == SECRET_PASSWORD
        assert captured["role"] == "analyst"
        assert captured["prediction_sql"] == CALLER_SQL
        assert captured.get("as_system", False) is False

    def test_password_is_not_echoed_back(self, monkeypatch):
        captured: dict = {}
        app = self._client(monkeypatch, captured)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(BATCH_PATH, json=REQUEST_BODY)
        assert SECRET_PASSWORD not in response.text

    def test_route_without_readable_credential_fails_closed(self, monkeypatch):
        from app.core import deps as deps_module
        from app.modules.ml_engine import router as router_module
        from app.modules.ml_engine.router import router as ml_router

        def boom(_encrypted):
            raise ValueError("no key")

        monkeypatch.setattr(router_module, "decrypt_password", boom)

        async def caller():
            return _user()

        app = FastAPI()
        app.include_router(ml_router, prefix="/api/v1/ml")
        app.dependency_overrides[deps_module.get_current_user] = caller
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(BATCH_PATH, json=REQUEST_BODY)
        assert response.status_code == 401, response.text


class RecordingSource:
    def __init__(self) -> None:
        self.calls = []

    async def stream(self, sql, security):
        self.calls.append((sql, security))
        yield pa.record_batch([pa.array([1.0])], names=["age"])


class StopAfterExtractionRuntime:
    async def predict_alias(self, *args, **kwargs):
        raise RuntimeError("stop-after-fetch")


class ProxyCursor:
    description = (("age",),)

    def __init__(self) -> None:
        self.sql = None
        self._read = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, sql):
        self.sql = sql

    async def fetchmany(self, _size):
        if self._read:
            return []
        self._read = True
        return [(4.0,), (5.0,)]


class ProxyConnection:
    def __init__(self) -> None:
        self.last_cursor = None

    def cursor(self):
        self.last_cursor = ProxyCursor()
        return self.last_cursor


@pytest.fixture
def service(monkeypatch):
    source = RecordingSource()
    instance = MLEngineService(
        data_source=source,
        job_runner=InlineJobRunner(),
        runtime=StopAfterExtractionRuntime(),
    )
    instance.recording_source = source
    return instance


class TestPredictionSqlUsesCallerScope:
    @pytest.mark.parametrize("sql", QA_ALLOWED_STATEMENTS)
    async def test_allowed_statement_keeps_caller_context(self, service, sql):
        with pytest.raises(RuntimeError, match="stop-after-fetch"):
            await service.batch_predict(
                model_alias="churn_model",
                prediction_sql=sql,
                database_name="prod_db",
                username="analyst",
                password=SECRET_PASSWORD,
                role="analyst",
            )

        executed_sql, security = service.recording_source.calls[-1]
        assert executed_sql == sql
        assert security.username == "analyst"
        assert security.password == SECRET_PASSWORD
        assert security.database == "prod_db"
        assert security.role == "analyst"

    async def test_no_credential_fails_before_extraction(self, service):
        with pytest.raises(ValueError, match="credentials"):
            await service.batch_predict(
                model_alias="churn_model",
                prediction_sql=CALLER_SQL,
                database_name=None,
            )
        assert service.recording_source.calls == []

    async def test_explicit_system_scope_is_opt_in(self, service, monkeypatch):
        async def successful_predict(*args, **kwargs):
            del args, kwargs
            return {"model_name": "m", "version": 1}, [2.0], None

        monkeypatch.setattr(service.runtime, "predict_alias", successful_predict)
        result = await service.batch_predict(
            model_alias="churn_model",
            prediction_sql=CALLER_SQL,
            database_name=None,
            as_system=True,
        )

        _, security = service.recording_source.calls[-1]
        assert security.username
        assert security.username != "analyst"
        assert result["total_rows"] == 1

    async def test_authenticated_proxy_connection_needs_no_password(self, service, monkeypatch):
        async def successful_predict(*args, **kwargs):
            del args, kwargs
            return {"model_name": "m", "version": 1}, [8.0, 10.0], None

        monkeypatch.setattr(service.runtime, "predict_alias", successful_predict)
        connection = ProxyConnection()
        result = await service.batch_predict(
            model_alias="churn_model",
            prediction_sql=CALLER_SQL,
            database_name="prod_db",
            username="analyst",
            connection=connection,
        )

        assert connection.last_cursor.sql == CALLER_SQL
        assert result["total_rows"] == 2
        assert [row["prediction"] for row in result["predictions"]] == [8.0, 10.0]
        assert service.recording_source.calls == []
