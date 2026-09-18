import json

import pytest

from app.modules.ml_engine.service import (
    _REGRESSION_LIKE_TYPES,
    ALGORITHMS,
    _pick_algorithm,
)
from app.modules.query.dialect.ml_model import (
    is_create_ml_model,
    parse_create_ml_model,
)
from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService


class TestCreateMLModelParser:
    def test_parse_compact_classification(self):
        stmt = parse_create_ml_model(
            """
            CREATE ML_MODEL churn_model
            TYPE = CLASSIFICATION
            TARGET = churned
            ALGORITHM = random_forest
            TEST_SIZE = 0.25
            AS SELECT age, income, churned FROM customers
            """
        )

        assert stmt.model_name == "churn_model"
        assert stmt.model_type == "classification"
        assert stmt.target_column == "churned"
        assert stmt.algorithm == "random_forest"
        assert stmt.test_size == 0.25
        assert stmt.training_sql == "SELECT age, income, churned FROM customers"

    def test_parse_optional_features_and_hyperparameters(self):
        stmt = parse_create_ml_model(
            """
            create ml_model `revenue_model`
            type = regression
            target = `revenue`
            features = (`visits`, spend)
            hyperparameters = JSON '{"n_estimators": 100}'
            as select visits, spend, revenue from fact_sales
            """
        )

        assert stmt.model_name == "revenue_model"
        assert stmt.model_type == "regression"
        assert stmt.target_column == "revenue"
        assert stmt.feature_columns == ["visits", "spend"]
        assert stmt.hyperparameters == {"n_estimators": 100}

    def test_detect_only_create_ml_model_prefix(self):
        assert is_create_ml_model("CREATE ML_MODEL churn TYPE = CLASSIFICATION")
        assert not is_create_ml_model("CREATE TABLE churn AS SELECT 1")

    def test_reject_missing_as_select(self):
        with pytest.raises(ValueError, match="Invalid CREATE ML_MODEL syntax"):
            parse_create_ml_model("CREATE ML_MODEL churn TYPE = CLASSIFICATION TARGET = y")

    def test_reject_missing_type(self):
        with pytest.raises(ValueError, match="requires TYPE"):
            parse_create_ml_model("CREATE ML_MODEL churn TARGET = y AS SELECT x, y FROM t")

    def test_reject_missing_target(self):
        with pytest.raises(ValueError, match="requires TARGET"):
            parse_create_ml_model(
                "CREATE ML_MODEL churn TYPE = CLASSIFICATION AS SELECT x, y FROM t"
            )

    def test_parse_forecast_type(self):
        """``TYPE = FORECAST`` is a documented surface and must not be rejected.

        NOVA-17 defect 7: the type pattern accepted only CLASSIFICATION|REGRESSION,
        so the DDL in ``AGENTS.md:245`` / ``docs/19-machine-learning.md:184``
        failed as a syntax error before the engine ever saw it. The parser emits
        the type verbatim (lowercased); the engine maps it onto its regression
        estimator family.
        """
        stmt = parse_create_ml_model(
            """
            CREATE ML_MODEL sales_forecast
            TYPE = FORECAST
            TARGET = sales_amount
            AS SELECT date, sales_amount FROM training_view
            """
        )

        assert stmt.model_name == "sales_forecast"
        assert stmt.model_type == "forecast"
        assert stmt.target_column == "sales_amount"
        assert stmt.algorithm == "auto"
        assert stmt.training_sql == "SELECT date, sales_amount FROM training_view"

    def test_parse_anomaly_detection_type(self):
        """The other documented Nova type, ``ANOMALY_DETECTION``, is accepted too.

        Both are recorded in ``docs/19-machine-learning.md:322``; the fix covers
        the documented set rather than widening the capture to any identifier
        (which would make ``TYPE = BANANA`` parse).
        """
        stmt = parse_create_ml_model(
            "CREATE ML_MODEL fraud TYPE = ANOMALY_DETECTION TARGET = is_fraud "
            "AS SELECT amount, is_fraud FROM txn"
        )
        assert stmt.model_type == "anomaly_detection"

    def test_reject_unknown_type(self):
        with pytest.raises(ValueError, match="requires TYPE"):
            parse_create_ml_model(
                "CREATE ML_MODEL m TYPE = BANANA TARGET = y AS SELECT x, y FROM t"
            )

    @pytest.mark.parametrize("model_type", ["FORECAST", "ANOMALY_DETECTION"])
    def test_documented_types_still_require_target_and_as_select(self, model_type):
        """Accepting the type does not relax the other required clauses.

        Acceptance criterion 2 pairs "the type is accepted" with "TARGET /
        AS SELECT still validated as before", so both omissions keep failing.
        """
        with pytest.raises(ValueError, match="requires TARGET"):
            parse_create_ml_model(f"CREATE ML_MODEL m TYPE = {model_type} AS SELECT x, y FROM t")

        with pytest.raises(ValueError, match="Invalid CREATE ML_MODEL syntax"):
            parse_create_ml_model(f"CREATE ML_MODEL m TYPE = {model_type} TARGET = y")


@pytest.mark.asyncio
async def test_query_service_routes_create_ml_model_to_ml_engine(monkeypatch):
    service = QueryService()

    async def fake_train_model(**kwargs):
        assert kwargs["model_name"] == "churn_model"
        assert kwargs["model_type"] == "classification"
        assert kwargs["training_sql"] == "SELECT age, churned FROM customers"
        assert kwargs["target_column"] == "churned"
        assert kwargs["database_name"] == "analytics"
        assert kwargs["created_by"] == "analyst"
        assert kwargs["username"] == "analyst"
        assert kwargs["password"] == "secret"
        assert kwargs["role"] == "analyst_role"
        return {
            "model_id": "model-1",
            "model_name": "churn_model",
            "model_type": "classification",
            "algorithm": "random_forest",
            "version": 1,
            "status": "active",
            "training_rows": 42,
            "feature_columns": ["age"],
            "metrics": {"accuracy": 0.9},
        }

    class FakeMLEngineService:
        train_model = staticmethod(fake_train_model)

    async def fake_audit_log(**kwargs):
        assert kwargs["status"] == "SUCCESS"
        assert kwargs["object_type"] == "ml_model"
        assert kwargs["object_name"] == "churn_model"

    async def fail_execute_as_user(*args, **kwargs):
        raise AssertionError("regular StarRocks execution should not be used")

    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda _: "secret")
    monkeypatch.setattr("app.modules.query.service.write_audit_log", fake_audit_log)
    monkeypatch.setattr(
        "app.modules.ml_engine.service.ml_engine_service",
        FakeMLEngineService(),
    )
    monkeypatch.setattr(service._repo, "execute_as_user", fail_execute_as_user)

    result = await service.execute(
        sql="""
        CREATE ML_MODEL churn_model
        TYPE = CLASSIFICATION
        TARGET = churned
        ALGORITHM = random_forest
        AS SELECT age, churned FROM customers
        """,
        username="analyst",
        encrypted_password="encrypted",
        database="analytics",
        role="analyst_role",
    )

    assert isinstance(result, QueryResult)
    assert result.columns == [
        "model_id",
        "model_name",
        "model_type",
        "algorithm",
        "version",
        "status",
        "training_rows",
        "feature_columns",
        "metrics",
    ]
    assert result.rows[0][0] == "model-1"
    assert result.rows[0][6] == 42
    assert json.loads(result.rows[0][7]) == ["age"]
    assert json.loads(result.rows[0][8]) == {"accuracy": 0.9}


@pytest.mark.asyncio
async def test_query_service_routes_forecast_to_ml_engine(monkeypatch):
    """``TYPE = FORECAST`` reaches the ML engine typed as ``forecast``.

    Parsing is not enough: the DDL must be routed with the model type the engine
    expects, or the accept is hollow. Pinned at the QueryService boundary.
    """
    service = QueryService()
    seen: dict = {}

    async def fake_train_model(**kwargs):
        seen.update(kwargs)
        return {
            "model_id": "model-2",
            "model_name": "sales_forecast",
            "model_type": "forecast",
            "algorithm": "gradient_boost",
            "version": 1,
            "status": "active",
            "training_rows": 120,
            "feature_columns": ["store_id"],
            "metrics": {"rmse": 1.0},
        }

    class FakeMLEngineService:
        train_model = staticmethod(fake_train_model)

    async def fake_audit_log(**kwargs):
        assert kwargs["status"] == "SUCCESS"
        assert kwargs["object_name"] == "sales_forecast"

    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda _: "secret")
    monkeypatch.setattr("app.modules.query.service.write_audit_log", fake_audit_log)
    monkeypatch.setattr(
        "app.modules.ml_engine.service.ml_engine_service",
        FakeMLEngineService(),
    )

    result = await service.execute(
        sql=(
            "CREATE ML_MODEL sales_forecast TYPE = FORECAST TARGET = total_sales "
            "AS SELECT store_id, total_sales FROM training_sales"
        ),
        username="analyst",
        encrypted_password="encrypted",
        database="analytics",
    )

    assert seen["model_type"] == "forecast"
    assert seen["model_name"] == "sales_forecast"
    assert seen["target_column"] == "total_sales"
    assert seen["training_sql"] == "SELECT store_id, total_sales FROM training_sales"
    assert result.rows[0][2] == "forecast"


def test_forecast_and_anomaly_detection_resolve_to_a_real_estimator():
    """Each documented type must map to a non-empty estimator family.

    Without an ``ALGORITHMS`` entry the accepted DDL would raise "Algorithm
    '<x>' not supported for <type>" — the raise moved, not removed. ``forecast``
    predicts a continuous value (regression estimators); ``anomaly_detection``
    reuses the classifiers.
    """
    assert set(ALGORITHMS["forecast"]) == set(ALGORITHMS["regression"])
    assert set(ALGORITHMS["anomaly_detection"]) == set(ALGORITHMS["classification"])
    assert frozenset({"regression", "forecast"}) == _REGRESSION_LIKE_TYPES


@pytest.mark.parametrize("n_rows", [10, 5000, 100_000])
def test_pick_algorithm_branches_on_estimator_family(n_rows):
    """``forecast`` auto-picks like regression; ``anomaly_detection`` like classification.

    The bug this guards: branching on the model-type *name* would hand
    ``anomaly_detection`` a regressor because it is not ``classification``.
    """
    assert _pick_algorithm("forecast", "auto", n_rows) == _pick_algorithm(
        "regression", "auto", n_rows
    )
    assert _pick_algorithm("anomaly_detection", "auto", n_rows) == _pick_algorithm(
        "classification", "auto", n_rows
    )
    assert _pick_algorithm("forecast", "auto", n_rows) in ALGORITHMS["forecast"]
    assert _pick_algorithm("anomaly_detection", "auto", n_rows) in ALGORITHMS["anomaly_detection"]


def test_forecast_accepts_an_explicit_algorithm():
    assert _pick_algorithm("forecast", "gradient_boost", 10) == "gradient_boost"


@pytest.mark.parametrize(
    "model_type", ["classification", "regression", "forecast", "anomaly_detection"]
)
def test_train_request_accepts_documented_model_types(model_type):
    """The HTTP training surface accepts the same types as the DDL.

    The DDL path bypasses ``TrainModelRequest``, but leaving the schema at
    ``classification|regression`` would make the two surfaces disagree about
    which model types exist.
    """
    from app.modules.ml_engine.schemas import TrainModelRequest

    req = TrainModelRequest(
        model_name="m",
        model_type=model_type,
        training_sql="SELECT x, y FROM t",
        target_column="y",
    )
    assert req.model_type == model_type


def test_train_request_rejects_unknown_model_type():
    from pydantic import ValidationError

    from app.modules.ml_engine.schemas import TrainModelRequest

    with pytest.raises(ValidationError):
        TrainModelRequest(
            model_name="m",
            model_type="banana",
            training_sql="SELECT x, y FROM t",
            target_column="y",
        )


@pytest.mark.asyncio
async def test_query_service_regular_sql_still_uses_repository(monkeypatch):
    service = QueryService()

    async def fake_execute_as_user(**kwargs):
        assert kwargs["sql"] == "SELECT 1"
        return QueryResult(columns=["1"], rows=[[1]], row_count=1, executed_sql="SELECT 1")

    async def fake_audit_log(**kwargs):
        assert kwargs["status"] == "SUCCESS"

    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda _: "secret")
    monkeypatch.setattr(service._repo, "execute_as_user", fake_execute_as_user)
    monkeypatch.setattr("app.modules.query.service.write_audit_log", fake_audit_log)

    result = await service.execute(
        sql="SELECT 1",
        username="analyst",
        encrypted_password="encrypted",
    )

    assert result.rows == [[1]]


@pytest.mark.asyncio
async def test_query_service_execute_statements_continues_until_error(monkeypatch):
    service = QueryService()
    seen: list[str] = []

    async def fake_execute(**kwargs):
        seen.append(kwargs["sql"])
        if kwargs["sql"] == "SELECT fail":
            raise ValueError("boom")
        return QueryResult(columns=["ok"], rows=[[kwargs["sql"]]], row_count=1)

    monkeypatch.setattr(service, "execute", fake_execute)

    results = await service.execute_statements(
        sql="SELECT 1; SELECT 2; SELECT fail; SELECT 3",
        username="analyst",
        encrypted_password="encrypted",
    )

    assert seen == ["SELECT 1", "SELECT 2", "SELECT fail"]
    assert len(results) == 3
    assert results[-1].warnings == ["boom"]
