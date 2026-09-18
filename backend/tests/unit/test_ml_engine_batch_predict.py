"""Regression tests for the ``batch_predict`` / ``prediction_sql`` path.

``MLEngineService.batch_predict`` executed the caller's ``prediction_sql`` raw
on a connection carrying real storage credentials — same root cause as the
``training_sql`` defect, and now routed through the same
``sql_pipeline`` preparation (NOVA-28).

These are L1: the service is driven with a fake connection and a recording
cursor, so no engine is involved. The credential invariant is asserted on
structure rather than on the secret value (STANDARD SS10 rule 4).
"""

import pytest

from app.common.sql_guard import _POPULATED_CREDENTIAL, _normalized_value
from app.core.exceptions import ForbiddenSQLError
from app.modules.ml_engine.service import MLEngineService

STAGE_ACCESS_KEY = "STAGE_CFG_ACCESS"
STAGE_SECRET_KEY = "STAGE_CFG_SECRET"


def _stage_configs(stage_name: str = "stage1"):
    from app.modules.query.dialect.translator import StorageConfig

    return {
        stage_name: StorageConfig(
            storage_type="s3",
            endpoint="http://minio:9000",
            bucket="bucket",
            base_prefix="db/schema/stage1",
            access_key=STAGE_ACCESS_KEY,
            secret_key=STAGE_SECRET_KEY,
            region="us-east-1",
        )
    }


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(
        "app.modules.query.sql_pipeline.get_credential_params",
        lambda *a, **kw: {"aws.s3.access_key": "PIPE_A", "aws.s3.secret_key": "PIPE_S"},
    )

    async def stage_configs(_self, database_name):
        return _stage_configs()

    monkeypatch.setattr(MLEngineService, "_load_stage_configs", stage_configs)
    return MLEngineService()


def _no_populated_credential(sql: str) -> bool:
    for match in _POPULATED_CREDENTIAL.finditer(sql):
        value = _normalized_value(match.group("value"))
        if value and value != "***":
            return False
    return True


class TestPredictionSqlIsGuarded:
    """The guard must run on prediction_sql exactly as on the worksheet."""

    async def test_blocked_statement_never_reaches_the_engine(self, service, monkeypatch):
        executed: list[str] = []

        class Cursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, sql, params=None):
                executed.append(sql)

            async def fetchone(self):
                return None

            async def fetchall(self):
                return []

        class Conn:
            def cursor(self, *args, **kwargs):
                return Cursor()

            def close(self):
                pass

        async def fake_connect():
            return Conn()

        monkeypatch.setattr(service, "_connect", fake_connect)

        with pytest.raises(ForbiddenSQLError):
            await service.batch_predict(
                model_alias="m", prediction_sql="DROP ROLE ACCOUNTADMIN", database_name=None
            )

        # The guard fired during preparation, before any statement ran.
        assert executed == []

    @pytest.mark.parametrize(
        "sql",
        [
            "REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN",
            "ALTER ROLE ACCOUNTADMIN",
            "DROP /* ; */ ROLE ACCOUNTADMIN",
            "SELECT 1; DROP ROLE ACCOUNTADMIN",
        ],
    )
    async def test_every_accountadmin_form_is_blocked(self, service, sql):
        with pytest.raises(ForbiddenSQLError):
            await service.batch_predict(
                model_alias="m", prediction_sql=sql, database_name=None
            )


class TestPredictionSqlTranslation:
    """`@stage` in prediction SQL is rewritten and credential-injected."""

    async def test_stage_reference_is_translated(self, service):
        engine_sql = await service._prepare_user_sql(
            sql="SELECT * FROM @stage1.data.csv",
            database_name=None,
            what="prediction",
        )
        assert "@stage1" not in engine_sql
        assert "FILES(" in engine_sql
        assert f"'{STAGE_ACCESS_KEY}'" in engine_sql

    async def test_plain_select_passes_through_unchanged(self, service):
        engine_sql = await service._prepare_user_sql(
            sql="SELECT age FROM customers",
            database_name=None,
            what="prediction",
        )
        assert engine_sql == "SELECT age FROM customers"
        assert "aws.s3.access_key" not in engine_sql

    async def test_translation_error_names_the_stage(self, service):
        """An unresolvable stage fails as a ValueError, not a raw dialect error."""
        with pytest.raises(ValueError, match="not found|unknown|stage"):
            await service._prepare_user_sql(
                sql="SELECT * FROM @nosuchstage.data.csv",
                database_name=None,
                what="prediction",
            )


class TestPredictionSqlCredentialInvariant:
    """No credential may survive into the redacted form of prediction SQL."""

    async def test_redacted_form_is_credential_free(self, service):
        engine_sql = await service._prepare_user_sql(
            sql="SELECT * FROM @stage1.data.csv",
            database_name=None,
            what="prediction",
        )
        redacted = service._redacted_user_sql(engine_sql)

        # The engine form carries what @stage needs...
        assert STAGE_ACCESS_KEY in engine_sql
        assert STAGE_SECRET_KEY in engine_sql
        # ...and the form that may leave the process carries none of it.
        assert STAGE_ACCESS_KEY not in redacted
        assert STAGE_SECRET_KEY not in redacted
        assert _no_populated_credential(redacted)


CALLER_PASSWORD = "caller-secret"


def _caller() -> dict:
    return {
        "username": "analyst",
        "session_id": "sess-1",
        "roles": ["analyst_role"],
        "active_role": "analyst_role",
        "encrypted_password": "fernet-blob",
    }


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
        return None


class _UserConnFactory:
    """Records ``db.user_conn`` calls and exposes the cursor it handed out."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.opened: list[dict] = []
        self.cursor = _RecordingCursor(rows or [])

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


def _base64_model() -> str:
    import base64
    import io

    import joblib
    from sklearn.linear_model import LinearRegression

    model = LinearRegression().fit([[1.0], [2.0]], [1.0, 2.0])
    buf = io.BytesIO()
    joblib.dump({"model": model, "feature_columns": ["age"]}, buf)
    return base64.b64encode(buf.getvalue()).decode()


_BASE64_MODEL = _base64_model()


class _RootConn:
    """Serves alias/model metadata, then records any further statement.

    Only the two metadata SELECTs run on root; anything else a test sees here
    is the caller's SQL having leaked onto the system connection.
    """

    _ALIAS_ROW = {
        "model_id": "model-1",
        "version": 1,
        "model_name": "model",
        "model_type": "regression",
    }
    _VERSION_ROW = {"model_binary": _BASE64_MODEL}

    def __init__(self) -> None:
        self.cursor_obj = _RecordingCursor()

    def cursor(self, *args, **kwargs):
        return _RootCursor(self.cursor_obj)

    def close(self) -> None:
        pass


class _RootCursor:
    """Wraps the recording cursor to answer the metadata reads."""

    def __init__(self, inner: _RecordingCursor) -> None:
        self._inner = inner
        self._pending = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self._inner.executed.append(sql)
        self._pending = sql
        return 1

    async def fetchone(self):
        if "ML_MODEL_ALIASES" in self._pending:
            return _RootConn._ALIAS_ROW
        if "ML_MODEL_VERSIONS" in self._pending:
            return _RootConn._VERSION_ROW
        return None

    async def fetchall(self):
        return []

    @property
    def description(self):
        return None


def _prediction_rows() -> list[dict]:
    return [{"age": 1.0}, {"age": 2.0}]


def _pretrain_service(monkeypatch, user_conn) -> tuple[MLEngineService, _RootConn]:
    """Service whose root connection serves metadata only.

    Returns the service and the root connection so a test can assert the
    caller's SQL never reached it.
    """
    monkeypatch.setattr("app.modules.query.sql_pipeline.get_credential_params", lambda *a, **kw: {})

    async def no_stage_configs(_self, database_name):
        return {}

    monkeypatch.setattr(MLEngineService, "_load_stage_configs", no_stage_configs)
    monkeypatch.setattr("app.modules.ml_engine.service.db.user_conn", user_conn)

    root = _RootConn()

    async def metadata_conn():
        return root

    svc = MLEngineService()
    monkeypatch.setattr(svc, "_connect", metadata_conn)
    return svc, root


async def _predict_as_caller(svc: MLEngineService, sql: str = "SELECT age FROM customers") -> dict:
    return await svc.batch_predict(
        model_alias="m",
        prediction_sql=sql,
        database_name="analytics",
        username="analyst",
        password=CALLER_PASSWORD,
        role="analyst_role",
    )


class TestBatchPredictUsesTheCallerConnection:
    """NOVA-118 AC1: prediction SQL runs on ``db.user_conn`` + ``SET ROLE``."""

    async def test_user_conn_is_used_with_role(self, monkeypatch):
        factory = _UserConnFactory(_prediction_rows())
        svc, _root = _pretrain_service(monkeypatch, factory)

        await _predict_as_caller(svc)

        assert factory.opened == [
            {"username": "analyst", "password": CALLER_PASSWORD, "database": "analytics"}
        ]
        assert "SET ROLE analyst_role" in factory.cursor.executed
        assert "SELECT age FROM customers" in factory.cursor.executed

    async def test_prediction_sql_never_reaches_the_root_connection(self, monkeypatch):
        """NOVA-118 AC1/AC4: the user statement must not run on root."""
        factory = _UserConnFactory(_prediction_rows())
        svc, root = _pretrain_service(monkeypatch, factory)

        await _predict_as_caller(svc, sql="SELECT * FROM mysql.user")

        assert "SELECT * FROM mysql.user" not in root.cursor_obj.executed

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM mysql.user",
            "SELECT * FROM NOVA_SYSTEM.CONFIG_STAGES",
            "SET ROLE ACCOUNTADMIN",
            "CREATE TABLE t (x int)",
            "INSERT INTO t VALUES (1)",
            "GRANT SELECT ON *.* TO ROLE analyst",
        ],
    )
    async def test_ddl_dml_and_role_escalation_are_not_run_on_root(self, monkeypatch, sql):
        """The same statements QA enumerated must not touch the root conn."""
        factory = _UserConnFactory([{"x": 1}])
        svc, root = _pretrain_service(monkeypatch, factory)

        await _predict_as_caller(svc, sql=sql)

        # Root only ever served the two metadata reads.
        assert sql not in root.cursor_obj.executed


class TestBatchPredictIdentityRequired:
    """NOVA-118 AC2: a missing identity fails closed, never root fallback."""

    async def test_no_credentials_is_refused(self, monkeypatch):
        async def forbid_system(**kwargs):
            raise AssertionError("must not fall back to the system connection")

        async def forbid_user(**kwargs):
            raise AssertionError("must not use a user connection without credentials")

        monkeypatch.setattr(
            MLEngineService, "_fetch_prediction_data_as_system", forbid_system
        )
        monkeypatch.setattr(
            MLEngineService, "_fetch_prediction_data_as_user", forbid_user
        )
        svc, _root = _pretrain_service(monkeypatch, _UserConnFactory())

        with pytest.raises(ValueError, match="requires caller credentials"):
            await svc.batch_predict(
                model_alias="m",
                prediction_sql="SELECT age FROM customers",
                database_name=None,
            )

    async def test_system_path_requires_explicit_flag(self, monkeypatch):
        used = {}

        async def system_fetch(**kwargs):
            used["hit"] = True
            return _prediction_rows()

        monkeypatch.setattr(
            MLEngineService, "_fetch_prediction_data_as_system", staticmethod(system_fetch)
        )
        svc, _root = _pretrain_service(monkeypatch, _UserConnFactory())

        await svc.batch_predict(
            model_alias="m",
            prediction_sql="SELECT age FROM customers",
            database_name=None,
            as_system=True,
        )

        assert used.get("hit") is True


class TestBatchPredictRouterForwardsIdentity:
    """NOVA-118 AC2: the HTTP endpoint must hand the identity to the service."""

    @pytest.fixture
    def app_and_calls(self, monkeypatch):
        from fastapi import FastAPI

        from app.core import deps as deps_module
        from app.modules.ml_engine import service as service_module
        from app.modules.ml_engine.router import router as ml_router

        calls: list[dict] = []

        async def fake_batch_predict(*args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return {
                "model_alias": "m",
                "model_name": "model",
                "predictions": [],
                "total_rows": 0,
            }

        monkeypatch.setattr(
            service_module.ml_engine_service, "batch_predict", fake_batch_predict
        )
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
        from fastapi.testclient import TestClient

        app, calls = app_and_calls
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/ml/predict/batch",
                json={
                    "model_alias": "m",
                    "prediction_sql": "SELECT age FROM customers",
                    "database_name": "analytics",
                },
            )

        assert resp.status_code == 200, resp.text
        assert len(calls) == 1
        kwargs = calls[0]["kwargs"]
        assert kwargs["username"] == "analyst"
        assert kwargs["password"] == CALLER_PASSWORD
        assert kwargs["role"] == "analyst_role"
        assert CALLER_PASSWORD not in resp.text

    def test_missing_session_credential_fails_closed(self, app_and_calls, monkeypatch):
        from fastapi.testclient import TestClient

        app, calls = app_and_calls

        def boom(encrypted):
            raise ValueError("no key")

        monkeypatch.setattr("app.modules.ml_engine.router.decrypt_password", boom)

        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/ml/predict/batch",
                json={
                    "model_alias": "m",
                    "prediction_sql": "SELECT age FROM customers",
                    "database_name": "analytics",
                },
            )

        assert resp.status_code == 401
        assert calls == []
