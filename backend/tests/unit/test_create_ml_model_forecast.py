"""Regression tests for NOVA-17 defect 7 — ``CREATE ML_MODEL ... TYPE = FORECAST``.

``AGENTS.md:245`` and ``docs/19-machine-learning.md:184`` both document
``TYPE = FORECAST`` (and ``docs/19-machine-learning.md:322`` lists
``ANOMALY_DETECTION`` as a stored model type), but the compact-DDL parser
accepted only ``CLASSIFICATION|REGRESSION`` and raised a *syntax* error for the
documented forms. This pins the accepted set in both directions — every
documented type parses, and an undocumented one still fails usefully — and pins
that a forecast model is trained and scored as a continuous-target problem
rather than falling through the classification branch.

Placed at L1: the parser and the algorithm registry are pure, so no engine or
Docker stack is involved.
"""

import pytest

from app.modules.ml_engine.service import ALGORITHMS, _pick_algorithm
from app.modules.query.dialect.ml_model import (
    is_create_ml_model,
    parse_create_ml_model,
)


class TestForecastParses:
    def test_type_forecast_is_accepted(self):
        stmt = parse_create_ml_model(
            """
            CREATE ML_MODEL sales_forecast
            TYPE = FORECAST
            TARGET = sales_amount
            AS SELECT date, sales_amount, store_id FROM training_view
            """
        )

        assert stmt.model_name == "sales_forecast"
        assert stmt.model_type == "forecast"
        assert stmt.target_column == "sales_amount"
        assert stmt.training_sql == (
            "SELECT date, sales_amount, store_id FROM training_view"
        )

    def test_type_anomaly_detection_is_accepted(self):
        stmt = parse_create_ml_model(
            "CREATE ML_MODEL sensors TYPE = ANOMALY_DETECTION "
            "TARGET = anomaly AS SELECT * FROM readings"
        )
        assert stmt.model_type == "anomaly_detection"

    def test_forecast_is_type_insensitive_and_still_detected(self):
        assert is_create_ml_model(
            "CREATE ML_MODEL m TYPE = FORECAST TARGET = y AS SELECT 1"
        )
        stmt = parse_create_ml_model(
            "create ml_model m type = forecast target = y as select x, y from t"
        )
        assert stmt.model_type == "forecast"

    def test_undocumented_type_still_rejected(self):
        with pytest.raises(ValueError, match="requires TYPE"):
            parse_create_ml_model(
                "CREATE ML_MODEL m TYPE = BANANA TARGET = y AS SELECT x, y FROM t"
            )

    def test_forecast_still_requires_target(self):
        with pytest.raises(ValueError, match="requires TARGET"):
            parse_create_ml_model(
                "CREATE ML_MODEL m TYPE = FORECAST AS SELECT x, y FROM t"
            )


class TestForecastTrainsAsRegression:
    def test_registry_has_the_documented_types(self):
        for model_type in ("forecast", "anomaly_detection"):
            assert model_type in ALGORITHMS, model_type

    @pytest.mark.parametrize("n_rows", [100, 5_000, 20_000])
    def test_auto_algorithm_picks_a_regressor_for_forecast(self, n_rows):
        from sklearn.base import is_regressor

        chosen = _pick_algorithm("forecast", "auto", n_rows)
        assert chosen in ALGORITHMS["forecast"]
        # A forecast must land on a regressor, never a classifier.
        assert is_regressor(ALGORITHMS["forecast"][chosen]())

    def test_auto_algorithm_picks_a_classifier_for_anomaly_detection(self):
        from sklearn.base import is_classifier

        chosen = _pick_algorithm("anomaly_detection", "auto", 50)
        assert chosen in ALGORITHMS["anomaly_detection"]
        assert is_classifier(ALGORITHMS["anomaly_detection"][chosen]())

    def test_forecast_algorithms_are_regressors(self):
        from sklearn.base import is_regressor

        for name, cls in ALGORITHMS["forecast"].items():
            assert is_regressor(cls()), name

    def test_anomaly_detection_algorithms_are_classifiers(self):
        from sklearn.base import is_classifier

        for name, cls in ALGORITHMS["anomaly_detection"].items():
            assert is_classifier(cls()), name


# ── A forecast trains end to end ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forecast_type_trains_and_scores_as_regression(monkeypatch):
    """A `TYPE = FORECAST` model trains, and reports regression metrics.

    Pins the end-to-end consequence of accepting the type: it must reach the
    regression evaluator (`mse`/`r2`), not fall into a classifier branch that
    would fail on a continuous target.
    """
    from app.modules.ml_engine.service import MLEngineService

    service = MLEngineService()
    rows = [{"feature": float(i), "target": float(i) * 2.0} for i in range(40)]

    async def fake_fetch_system(**kwargs):
        assert kwargs["training_sql"] == "SELECT feature, target FROM t"
        return rows, ["feature", "target"]

    class _AckCursor:
        def __init__(self) -> None:
            self._rows: list[list] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql, params=None):
            if "COALESCE(MAX(version)" in sql:
                self._rows = [[1]]
            else:
                self._rows = []
            return 1

        async def fetchone(self):
            return self._rows[0] if self._rows else None

    class _Conn:
        def cursor(self, *args, **kwargs):
            return _AckCursor()

        def close(self) -> None:
            pass

    async def fake_connect():
        return _Conn()

    monkeypatch.setattr(service, "_fetch_training_data_as_system", fake_fetch_system)
    monkeypatch.setattr(service, "_connect", fake_connect)

    result = await service.train_model(
        model_name="sales_forecast",
        model_type="forecast",
        algorithm="auto",
        training_sql="SELECT feature, target FROM t",
        target_column="target",
        feature_columns=None,
        hyperparameters=None,
        test_size=0.2,
        database_name=None,
        created_by="analyst",
        # This test pins forecast-as-regression training, not the RBAC contract;
        # it stubs the system fetch directly, so it opts into the system path
        # explicitly. Without the flag `train_model` correctly fails closed for
        # a caller with no credentials (NOVA-104/#112).
        as_system=True,
    )

    assert result["model_type"] == "forecast"
    assert "mse" in result["metrics"] and "r2" in result["metrics"]
    assert "accuracy" not in result["metrics"]
