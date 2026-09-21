"""Regression tests for Nova's native forecast and unsupervised DDL semantics."""

import pandas as pd
import pyarrow as pa
import pytest

from app.modules.ml_engine.engines.forecast import train_forecast
from app.modules.ml_engine.spec import (
    ExecutionBudget,
    MLExecutionSpec,
    MLMode,
    MLSecurityContext,
    MLTask,
)
from app.modules.query.dialect.ml_model import is_create_ml_model, parse_create_ml_model


class TestForecastParses:
    def test_forecast_contract_is_accepted(self):
        statement = parse_create_ml_model(
            """
            CREATE ML_MODEL sales_forecast
            TYPE = FORECAST
            TARGET = 'sales_amount'
            TIMESTAMP = `sale_date`
            SERIES = store_id
            HORIZON = 7
            FREQUENCY = 'D'
            MODE = BEST
            AS SELECT sale_date, sales_amount, store_id FROM training_view
            """
        )

        assert statement.model_name == "sales_forecast"
        assert statement.model_type == "forecast"
        assert statement.target_column == "sales_amount"
        assert statement.timestamp_column == "sale_date"
        assert statement.series_column == "store_id"
        assert statement.horizon == 7
        assert statement.frequency == "D"
        assert statement.mode == "best"

    @pytest.mark.parametrize("model_type", ["ANOMALY_DETECTION", "CLUSTERING"])
    def test_unsupervised_types_do_not_require_target(self, model_type):
        statement = parse_create_ml_model(
            f"CREATE ML_MODEL m TYPE = {model_type} AS SELECT * FROM readings"
        )
        assert statement.target_column is None

    def test_forecast_is_type_insensitive_and_still_detected(self):
        sql = (
            "create ml_model m type = forecast target = y timestamp = ts "
            "horizon = 2 as select ts, y from t"
        )
        assert is_create_ml_model(sql)
        assert parse_create_ml_model(sql).model_type == "forecast"

    def test_undocumented_type_still_rejected(self):
        with pytest.raises(ValueError, match="requires TYPE"):
            parse_create_ml_model(
                "CREATE ML_MODEL m TYPE = BANANA TARGET = y AS SELECT x, y FROM t"
            )

    def test_forecast_still_requires_target(self):
        with pytest.raises(ValueError, match="requires TARGET"):
            parse_create_ml_model(
                "CREATE ML_MODEL m TYPE = FORECAST TIMESTAMP = ts HORIZON = 2 "
                "AS SELECT ts, y FROM t"
            )


def test_forecast_uses_chronological_statsforecast_not_tabular_regression():
    rows = [
        {
            "sale_date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=day),
            "sales_amount": float(day * 2),
        }
        for day in range(40)
    ]
    spec = MLExecutionSpec(
        task=MLTask.FORECAST,
        input_sql="SELECT sale_date, sales_amount FROM training_view",
        security=MLSecurityContext("analyst", "secret"),
        mode=MLMode.INTERACTIVE,
        target_column="sales_amount",
        timestamp_column="sale_date",
        horizon=5,
        frequency="D",
        budget=ExecutionBudget(10, 1_000, 10_000_000),
    )

    result = train_forecast(pa.Table.from_pylist(rows), spec)

    assert result.engine == "statsforecast"
    assert result.metrics["validation_strategy"] == "chronological_holdout"
    assert result.metrics["training_end"] < result.metrics["validation_start"]
    assert len(result.results) == 5
