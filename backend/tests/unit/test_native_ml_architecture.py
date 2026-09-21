"""Behavior gates for Nova's native, bounded ML architecture."""

from __future__ import annotations

import asyncio
import base64
import io
import time

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from app.modules.agents.registry import KNOWN_TOOLS, build_registry
from app.modules.agents.tool_catalog import BUILTIN_TOOLS
from app.modules.ml_engine.artifacts.serializer import deserialize_bundle, serialize_bundle
from app.modules.ml_engine.artifacts.store import MemoryArtifactStore
from app.modules.ml_engine.data.datasource import collect_bounded
from app.modules.ml_engine.data.mysql_fallback import PreferredDataSource
from app.modules.ml_engine.engines.anomaly import train_anomaly
from app.modules.ml_engine.engines.clustering import train_clustering
from app.modules.ml_engine.engines.forecast import train_forecast
from app.modules.ml_engine.engines.tabular import train_tabular
from app.modules.ml_engine.ephemeral.cache import EphemeralEntry, EphemeralRunCache
from app.modules.ml_engine.execution.job_runner import InlineJobRunner, MLJobRunner
from app.modules.ml_engine.preprocessing.preprocessor import FeaturePreprocessor
from app.modules.ml_engine.runtime.model_cache import ModelCache
from app.modules.ml_engine.runtime.model_runtime import ModelRuntime
from app.modules.ml_engine.service import MLEngineService
from app.modules.ml_engine.spec import (
    CorruptArtifact,
    DataBudgetExceeded,
    ExecutionBudget,
    InferenceSchemaMismatch,
    InvalidMLSpec,
    MLExecutionSpec,
    MLMode,
    MLSecurityContext,
    MLTask,
    TrainingTimeout,
)

SECURITY = MLSecurityContext(username="alice", password="secret", database="analytics")


def _slow_worker(delay: float) -> str:
    time.sleep(delay)
    return "finished"


def _spec(task: MLTask, **kwargs) -> MLExecutionSpec:
    values = {
        "task": task,
        "input_sql": "SELECT * FROM features",
        "security": SECURITY,
        "mode": MLMode.INTERACTIVE,
        "budget": ExecutionBudget(2, 100_000, 100_000_000),
    }
    values.update(kwargs)
    return MLExecutionSpec(**values)


def test_preprocessor_handles_mixed_types_and_unseen_categories() -> None:
    table = pa.table(
        {
            "amount": pa.array([1.0, None, 3.0, 4.0]),
            "country": pa.array(["ID", "SG", None, "ID"]),
            "active": pa.array([True, False, None, True]),
            "created": pa.array(pd.date_range("2026-01-01", periods=4, freq="D")),
        }
    )
    preprocessor = FeaturePreprocessor(table.column_names)
    trained = preprocessor.fit_transform(table)
    inference = pa.table(
        {
            "amount": [5.0],
            "country": ["NEW"],
            "active": [True],
            "created": [pd.Timestamp("2026-02-01")],
        }
    )
    transformed = preprocessor.transform(inference)
    assert trained.shape[0] == 4
    assert transformed.shape[0] == 1
    assert transformed.shape[1] == trained.shape[1]


@pytest.mark.parametrize("task", [MLTask.CLASSIFICATION, MLTask.REGRESSION])
def test_tabular_auto_evaluates_candidates_and_records_metadata(task: MLTask) -> None:
    rng = np.random.default_rng(42)
    x = rng.normal(size=120)
    target = (
        (x > 0).astype(str) if task is MLTask.CLASSIFICATION else 3 * x + rng.normal(0, 0.1, 120)
    )
    table = pa.table({"x": x, "group": ["a", "b"] * 60, "target": target})
    result = train_tabular(table, _spec(task, target_column="target"))
    assert result.training_rows == 120
    assert result.engine == "flaml"
    assert result.metrics["candidates_evaluated"] >= 1
    assert result.metrics["selected_estimator"] == result.algorithm
    assert "validation_score" in result.metrics
    predictions, _ = _predict(result.bundle, table.slice(0, 3))
    assert len(predictions) == 3


def test_forecast_is_chronological_multi_series_and_respects_horizon() -> None:
    rows = []
    for series in ("north", "south"):
        for day in range(40):
            rows.append(
                {
                    "store": series,
                    "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=day),
                    "revenue": float(day + (10 if series == "south" else 0)),
                }
            )
    result = train_forecast(
        pa.Table.from_pylist(rows),
        _spec(
            MLTask.FORECAST,
            target_column="revenue",
            timestamp_column="date",
            series_column="store",
            horizon=5,
            frequency="D",
        ),
    )
    assert len(result.results) == 10
    assert result.metrics["validation_strategy"] == "chronological_holdout"
    assert result.metrics["training_end"] < result.metrics["validation_start"]
    assert all(
        pd.Timestamp(row["timestamp"]) > pd.Timestamp("2026-02-09", tz="UTC")
        for row in result.results
    )


def test_forecast_infers_frequency_and_requires_timestamp() -> None:
    table = pa.table(
        {
            "date": pd.date_range("2026-01-01", periods=24, freq="D"),
            "revenue": np.arange(24, dtype=float),
        }
    )
    inferred = train_forecast(
        table,
        _spec(
            MLTask.FORECAST,
            target_column="revenue",
            timestamp_column="date",
            horizon=3,
            frequency=None,
        ),
    )
    assert inferred.metrics["frequency"] == "D"
    assert len(inferred.results) == 3
    with pytest.raises(InvalidMLSpec, match="timestamp_column"):
        _spec(MLTask.FORECAST, target_column="revenue", horizon=3).validate()


def test_unsupervised_anomaly_detection_needs_no_target_and_scores_outlier() -> None:
    values = np.r_[np.linspace(-1, 1, 99), 50.0]
    result = train_anomaly(
        pa.table({"id": np.arange(100), "value": values}),
        _spec(
            MLTask.ANOMALY_DETECTION,
            feature_columns=("value",),
            row_identifier="id",
            parameters={"contamination": 0.05},
        ),
    )
    scores = {row["row_id"]: row["anomaly_score"] for row in result.results}
    assert scores[99] == max(scores.values())
    assert "contamination" in result.metrics


def test_clustering_selects_nontrivial_deterministic_clusters() -> None:
    rng = np.random.default_rng(42)
    points = np.r_[rng.normal(-4, 0.2, (40, 2)), rng.normal(4, 0.2, (40, 2))]
    table = pa.table({"id": np.arange(80), "x": points[:, 0], "y": points[:, 1]})
    spec = _spec(
        MLTask.CLUSTERING,
        feature_columns=("x", "y"),
        row_identifier="id",
        parameters={"max_clusters": 4},
    )
    first = train_clustering(table, spec)
    second = train_clustering(table, spec)
    assert first.metrics["cluster_count"] >= 2
    assert [row["cluster_id"] for row in first.results] == [
        row["cluster_id"] for row in second.results
    ]


def test_clustering_rejects_pathological_single_cluster() -> None:
    table = pa.table({"x": np.ones(12), "y": np.ones(12)})
    with pytest.raises(ValueError, match="single cluster"):
        train_clustering(
            table,
            _spec(MLTask.CLUSTERING, feature_columns=("x", "y")),
        )


class _BatchSource:
    def __init__(self, batches: list[pa.RecordBatch]) -> None:
        self.batches = batches
        self.calls = 0

    async def stream(self, sql: str, security: MLSecurityContext):
        assert "SELECT" in sql
        for batch in self.batches:
            self.calls += 1
            yield batch


@pytest.mark.asyncio
async def test_arrow_batches_are_consumed_incrementally_and_bounded() -> None:
    source = _BatchSource(
        [
            pa.record_batch([pa.array([1, 2])], names=["x"]),
            pa.record_batch([pa.array([3, 4])], names=["x"]),
        ]
    )
    dataset = await collect_bounded(
        source, "SELECT x FROM t", SECURITY, ExecutionBudget(10, 4, 10_000)
    )
    assert dataset.table.num_rows == 4
    assert dataset.metrics.batches_read == 2
    assert source.calls == 2
    with pytest.raises(DataBudgetExceeded):
        await collect_bounded(source, "SELECT x FROM t", SECURITY, ExecutionBudget(10, 3, 10_000))

    empty = _BatchSource([])
    with pytest.raises(ValueError, match="returned no rows"):
        await collect_bounded(empty, "SELECT x FROM t", SECURITY, ExecutionBudget(10, 3, 10_000))


@pytest.mark.asyncio
async def test_arrow_transport_falls_back_before_first_batch(monkeypatch) -> None:
    class BrokenArrow:
        async def stream(self, sql, security):
            if False:
                yield
            raise OSError("flight unavailable")

    fallback = _BatchSource([pa.record_batch([pa.array([7, 8])], names=["x"])])
    source = PreferredDataSource()
    source.arrow = BrokenArrow()
    source.mysql = fallback
    monkeypatch.setattr("app.modules.ml_engine.data.mysql_fallback.settings.ML_ARROW_ENABLED", True)
    dataset = await collect_bounded(
        source, "SELECT x FROM t", SECURITY, ExecutionBudget(10, 10, 10_000)
    )
    assert dataset.table.column("x").to_pylist() == [7, 8]
    assert fallback.calls == 1


def test_artifact_checksum_rejects_corruption_and_new_store_uses_uri() -> None:
    from sklearn.linear_model import LinearRegression

    payload, checksum = serialize_bundle({"model": LinearRegression(), "task": "regression"})
    store = MemoryArtifactStore()
    uri = store.put("scope/model/v1/model.joblib", payload)
    assert uri.startswith("memory://")
    assert deserialize_bundle(store.get(uri), checksum)["task"] == "regression"
    with pytest.raises(CorruptArtifact):
        deserialize_bundle(payload + b"broken", checksum)


@pytest.mark.asyncio
async def test_legacy_base64_artifact_remains_loadable() -> None:
    from sklearn.linear_model import LinearRegression

    model = LinearRegression().fit([[0.0], [1.0]], [0.0, 2.0])
    buffer = io.BytesIO()
    joblib.dump(
        {"model": model, "task": "regression", "feature_columns": ["x"]}, buffer
    )
    runtime = ModelRuntime(repository=_NoopRepository(), store=MemoryArtifactStore())
    bundle = await runtime.load(
        {
            "model_id": "legacy",
            "version": 1,
            "artifact_uri": None,
            "model_binary": base64.b64encode(buffer.getvalue()).decode(),
        }
    )
    predictions, _ = runtime.predict_bundle(bundle, pa.table({"x": [3.0]}))
    assert predictions == pytest.approx([6.0])


def test_preprocessor_rejects_missing_inference_schema() -> None:
    processor = FeaturePreprocessor(["amount", "description"])
    processor.fit_transform(pa.table({"amount": [1.0, None], "description": ["alpha", "beta"]}))
    with pytest.raises(InferenceSchemaMismatch, match="description"):
        processor.transform(pa.table({"amount": [2.0]}))


@pytest.mark.asyncio
async def test_model_cache_singleflight_ttl_eviction_and_failed_load() -> None:
    cache = ModelCache(max_models=1, max_bytes=100, ttl_seconds=0.02)
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"model": calls}, 10

    first, second = await asyncio.gather(
        cache.get_or_load(("a", 1), loader), cache.get_or_load(("a", 1), loader)
    )
    assert first == second
    assert calls == 1
    await cache.get_or_load(("b", 1), loader)
    assert cache.metrics()["models"] == 1
    await asyncio.sleep(0.03)
    await cache.get_or_load(("b", 1), loader)
    assert calls == 3

    async def failed():
        raise RuntimeError("corrupt")

    with pytest.raises(RuntimeError):
        await cache.get_or_load(("bad", 1), failed)
    assert ("bad", 1) not in cache._entries


def test_agent_registry_exposes_structured_ml_execute() -> None:
    assert "ml_execute" in KNOWN_TOOLS
    assert "ml_execute" in BUILTIN_TOOLS
    registry = build_registry({"default_tools": ["ml_execute"]})
    tool = registry.get("ml_execute")
    assert tool is not None
    assert set(tool.parameters["properties"]["task"]["enum"]) == {
        "classification",
        "regression",
        "forecast",
        "anomaly_detection",
        "clustering",
    }


class _NoopRepository:
    async def record_run(self, **kwargs):
        return None


class _FailRegistrationRepository(_NoopRepository):
    def __init__(self) -> None:
        self.aborted: tuple[str, int] | None = None

    async def reserve_version(self, spec, *, feature_columns):
        return "model-1", 1

    async def register_version(self, **kwargs):
        raise RuntimeError("registry unavailable")

    async def abort_version(self, model_id, version):
        self.aborted = (model_id, version)


class _InspectableRunner(InlineJobRunner):
    def __init__(self) -> None:
        self.called = False

    async def run(self, func, *args, timeout_seconds: float, **kwargs):
        self.called = True
        return await super().run(func, *args, timeout_seconds=timeout_seconds, **kwargs)


class _TestService(MLEngineService):
    async def _prepare_user_sql(self, sql, security):
        return sql


@pytest.mark.asyncio
async def test_ephemeral_run_uses_runner_and_does_not_persist() -> None:
    rng = np.random.default_rng(7)
    source = _BatchSource(
        [
            pa.record_batch(
                [pa.array(rng.normal(size=40)), pa.array(rng.normal(size=40))],
                names=["x", "target"],
            )
        ]
    )
    runner = _InspectableRunner()
    service = _TestService(
        data_source=source,
        job_runner=runner,
        artifact_store=MemoryArtifactStore(),
        repository=_NoopRepository(),
        ephemeral_cache=EphemeralRunCache(60),
    )
    result = await service.execute(_spec(MLTask.REGRESSION, target_column="target", persist=False))
    assert runner.called is True
    assert result.model_id is None
    assert result.version is None
    assert service.ephemeral_cache.by_run(result.run_id, SECURITY.scope_key) is not None


@pytest.mark.asyncio
async def test_process_runner_enforces_hard_timeout() -> None:
    runner = MLJobRunner(max_workers=1)
    try:
        with pytest.raises(TrainingTimeout):
            await runner.run(_slow_worker, 2.0, timeout_seconds=0.1)
        assert runner._active == set()
        assert await runner.run(_slow_worker, 0.0, timeout_seconds=5.0) == "finished"
    finally:
        runner.close()


@pytest.mark.asyncio
async def test_failed_registration_rolls_back_reservation_and_artifact() -> None:
    rng = np.random.default_rng(11)
    source = _BatchSource(
        [
            pa.record_batch(
                [pa.array(rng.normal(size=40)), pa.array(rng.normal(size=40))],
                names=["x", "target"],
            )
        ]
    )
    repository = _FailRegistrationRepository()
    artifacts = MemoryArtifactStore()
    service = _TestService(
        data_source=source,
        job_runner=InlineJobRunner(),
        artifact_store=artifacts,
        repository=repository,
    )
    spec = _spec(
        MLTask.REGRESSION,
        target_column="target",
        persist=True,
        model_name="rollback_model",
        algorithm="ridge",
    )
    with pytest.raises(RuntimeError, match="registry unavailable"):
        await service.execute(spec)
    assert repository.aborted == ("model-1", 1)
    assert artifacts.objects == {}


def test_ephemeral_cache_enforces_scope_and_ttl() -> None:
    cache = EphemeralRunCache(0.01)
    entry = EphemeralEntry(
        run_id="run-1",
        fingerprint="fingerprint",
        scope_key="tenant-a:alice:db:schema",
        output=object(),
        artifact_payload=b"artifact",
        artifact_sha256="checksum",
        expires_at=time.monotonic() + 0.01,
    )
    cache.put(entry)
    assert cache.by_run("run-1", "tenant-b:alice:db:schema") is None
    time.sleep(0.02)
    assert cache.by_run("run-1", entry.scope_key) is None


def _predict(bundle, table):
    from app.modules.ml_engine.runtime.model_runtime import ModelRuntime

    return ModelRuntime.predict_bundle(bundle, table)
