import json

import pytest

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
