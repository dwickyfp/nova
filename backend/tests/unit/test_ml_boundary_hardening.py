"""Regression gates for Nova ML process, stream, cache, and SQL boundaries."""

from __future__ import annotations

import asyncio
import threading
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression

from app.common.ml_intercept import (
    detect_ml_forecast,
    detect_ml_predict,
    rewrite_ml_predict_projection,
)
from app.modules.agents.tools.ml_execute import MLExecuteTool
from app.modules.assistant.tools import ToolInvocation
from app.modules.ml_engine.artifacts.serializer import serialize_bundle
from app.modules.ml_engine.artifacts.store import MemoryArtifactStore
from app.modules.ml_engine.data.arrow_flight import ArrowFlightDataSource
from app.modules.ml_engine.engines.anomaly import train_anomaly
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.engines.forecast import train_forecast
from app.modules.ml_engine.engines.tabular import train_tabular
from app.modules.ml_engine.ephemeral.cache import EphemeralEntry, EphemeralRunCache
from app.modules.ml_engine.execution.executor import BoundedMLExecutor
from app.modules.ml_engine.execution.job_runner import MLJobRunner
from app.modules.ml_engine.execution.worker import MLWorkerJob, MLWorkerResult
from app.modules.ml_engine.runtime.model_runtime import ModelRuntime
from app.modules.ml_engine.service import MLEngineService
from app.modules.ml_engine.spec import (
    ExecutionBudget,
    MLExecutionSpec,
    MLSecurityContext,
    MLTask,
)

SECURITY = MLSecurityContext("alice", "secret", database="analytics")
BUDGET = ExecutionBudget(5, 10_000, 10_000_000)


def _long_worker() -> None:
    time.sleep(30)


@pytest.mark.asyncio
async def test_flight_lifecycle_stays_on_one_thread_and_queue_is_bounded(monkeypatch):
    source = ArrowFlightDataSource(batch_size=1, queue_depth=2)
    thread_ids: list[int] = []
    queue_sizes: list[int] = []

    def reader(sql, security):
        del sql, security
        thread_ids.append(threading.get_ident())  # open/execute
        try:
            for value in range(8):
                thread_ids.append(threading.get_ident())  # read
                yield pa.record_batch([pa.array([value])], names=["value"])
        finally:
            thread_ids.append(threading.get_ident())  # close

    original_put = source._put

    def recording_put(loop, queue, item, stopped):
        result = original_put(loop, queue, item, stopped)
        queue_sizes.append(queue.qsize())
        return result

    monkeypatch.setattr(source, "_open_reader", reader)
    monkeypatch.setattr(source, "_put", recording_put)
    values = []
    async for batch in source.stream("SELECT value", SECURITY):
        values.extend(batch.column(0).to_pylist())
        await asyncio.sleep(0.005)

    assert values == list(range(8))
    assert len(set(thread_ids)) == 1
    assert max(queue_sizes) <= 2


@pytest.mark.asyncio
async def test_flight_cancellation_stops_producer_and_closes_reader(monkeypatch):
    source = ArrowFlightDataSource(batch_size=1, queue_depth=1)
    closed = threading.Event()
    produced = threading.Event()

    def reader(sql, security):
        del sql, security
        try:
            value = 0
            while True:
                produced.set()
                yield pa.record_batch([pa.array([value])], names=["value"])
                value += 1
        finally:
            closed.set()

    monkeypatch.setattr(source, "_open_reader", reader)

    async def consume():
        stream = source.stream("SELECT value", SECURITY)
        try:
            async for _batch in stream:
                await asyncio.sleep(60)
        finally:
            await stream.aclose()

    task = asyncio.create_task(consume())
    await asyncio.to_thread(produced.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.wait(2)


@pytest.mark.asyncio
async def test_job_cancellation_terminates_spawned_worker():
    runner = MLJobRunner(max_workers=1)
    task = asyncio.create_task(runner.run(_long_worker, timeout_seconds=60))
    for _ in range(100):
        if runner._active:
            break
        await asyncio.sleep(0.01)
    processes = tuple(runner._active)
    assert len(processes) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runner._active == set()
    assert all(not process.is_alive() for process in processes)


class _DescriptorRunner:
    worker_direct = True

    def __init__(self) -> None:
        self.job: MLWorkerJob | None = None

    async def run_job(self, job, *, timeout_seconds):
        assert timeout_seconds == BUDGET.timeout_seconds
        self.job = job
        return MLWorkerResult(
            output=TrainingOutput(
                bundle={"task": "regression", "model": LinearRegression()},
                engine="sklearn",
                algorithm="linear",
                metrics={},
                feature_columns=["x"],
                training_rows=20,
            ),
            extraction=type(
                "Metrics",
                (),
                {
                    "rows_read": 20,
                    "bytes_read": 160,
                    "batches_read": 2,
                    "largest_batch_bytes": 80,
                    "final_materialized_bytes": 160,
                    "duration_ms": 1.0,
                    "transport": "ArrowFlightDataSource",
                },
            )(),
        )


class _RunRepository:
    async def record_run(self, **kwargs):
        del kwargs


class _PreparedService(MLEngineService):
    async def _prepare_user_sql(self, sql, security):
        del security
        return sql


@pytest.mark.asyncio
async def test_orchestrator_dispatches_descriptor_not_arrow_table(monkeypatch):
    runner = _DescriptorRunner()
    event_loop_thread = threading.get_ident()
    serialization_threads: list[int] = []

    def recording_serialize(bundle):
        serialization_threads.append(threading.get_ident())
        return serialize_bundle(bundle)

    monkeypatch.setattr("app.modules.ml_engine.service.encrypt_password", lambda value: "cipher")
    monkeypatch.setattr("app.modules.ml_engine.service.serialize_bundle", recording_serialize)
    service = _PreparedService(
        job_runner=runner,
        artifact_store=MemoryArtifactStore(),
        repository=_RunRepository(),
    )
    result = await service.execute(
        MLExecutionSpec(
            task=MLTask.REGRESSION,
            input_sql="SELECT x, y FROM features",
            security=SECURITY,
            target_column="y",
            budget=BUDGET,
        )
    )
    assert result.training_rows == 20
    assert runner.job is not None
    assert runner.job.spec.security.password == ""
    assert runner.job.security.encrypted_password == "cipher"
    assert not any(isinstance(value, pa.Table) for value in vars(runner.job).values())
    assert result.telemetry["ipc_mode"] == "worker_direct_flight"
    assert result.telemetry["ipc_bytes"] == 0
    assert serialization_threads and serialization_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_cancel_after_upload_marks_cancelled_and_removes_partial_artifact(monkeypatch):
    register_started = asyncio.Event()

    class Repository:
        def __init__(self) -> None:
            self.statuses: list[str] = []
            self.aborted: list[tuple[str, int]] = []

        async def reserve_version(self, spec, *, feature_columns):
            del spec, feature_columns
            return "model-1", 1

        async def register_version(self, **kwargs):
            del kwargs
            register_started.set()
            await asyncio.Event().wait()

        async def abort_version(self, model_id, version):
            self.aborted.append((model_id, version))
            return True

        async def record_run(self, **kwargs):
            self.statuses.append(kwargs["status"])

    repository = Repository()
    store = MemoryArtifactStore()
    monkeypatch.setattr("app.modules.ml_engine.service.encrypt_password", lambda value: "cipher")
    service = _PreparedService(
        job_runner=_DescriptorRunner(),
        artifact_store=store,
        repository=repository,
    )
    task = asyncio.create_task(
        service.execute(
            MLExecutionSpec(
                task=MLTask.REGRESSION,
                input_sql="SELECT x, y FROM features",
                security=SECURITY,
                target_column="y",
                persist=True,
                model_name="cancelled-model",
                budget=BUDGET,
            )
        )
    )
    await asyncio.wait_for(register_started.wait(), timeout=2)
    assert store.objects
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert repository.statuses[-1] == "cancelled"
    assert repository.aborted == [("model-1", 1)]
    assert store.objects == {}


@pytest.mark.asyncio
async def test_ml_execute_classification_is_invocation_scoped_under_concurrency():
    tool = MLExecuteTool()
    persistent = ToolInvocation("a", "ml_execute", {"persist": True})
    ephemeral = ToolInvocation("b", "ml_execute", {"persist": False})

    async def classify(invocation):
        await asyncio.sleep(0)
        return tool.classification_for(invocation)

    values = await asyncio.gather(
        *(classify(persistent if index % 2 else ephemeral) for index in range(100))
    )
    assert values[0::2] == ["read_only"] * 50
    assert values[1::2] == ["destructive"] * 50
    assert tool.classification == "read_only"


def test_ml_predict_balanced_parser_preserves_projection_and_nested_expressions():
    sql = """SELECT customer_id,
        ML_PREDICT('production', COALESCE(total_spend, 0),
                   LOG(CASE WHEN order_count > 0 THEN order_count ELSE 1 END)) AS churn
        FROM customers WHERE CAST(active AS BOOLEAN)"""
    call = detect_ml_predict(sql)
    assert call is not None
    rewrite = rewrite_ml_predict_projection(sql, call)
    assert rewrite.alias == "production"
    assert rewrite.prediction_index == 1
    assert rewrite.prediction_name == "churn"
    assert rewrite.feature_columns == ("__ml_feature_0", "__ml_feature_1")
    assert rewrite.feature_sql.startswith("SELECT customer_id,")
    assert "COALESCE(total_spend, 0)" in rewrite.feature_sql
    assert "CASE WHEN order_count > 0" in rewrite.feature_sql
    assert "ML_PREDICT" not in rewrite.feature_sql


def test_ml_forecast_parser_supports_alias_version_series_and_confidence():
    alias = detect_ml_forecast(
        "SELECT * FROM ML_FORECAST(MODEL => 'sales', HORIZON => 30, "
        "SERIES => 'west', CONFIDENCE => 90)"
    )
    assert alias is not None
    assert (alias.model_alias, alias.horizon, alias.series, alias.confidence_level) == (
        "sales",
        30,
        "west",
        90,
    )
    version = detect_ml_forecast(
        "SELECT * FROM ML_FORECAST(MODEL_ID => 'model-1', VERSION => 7, HORIZON => 5)"
    )
    assert version is not None
    assert (version.model_id, version.version, version.horizon) == ("model-1", 7, 5)


@pytest.mark.asyncio
async def test_query_service_intercepts_ml_forecast_as_table_semantics(monkeypatch):
    from app.modules.ml_engine import service as ml_service_module
    from app.modules.query import service as query_service_module
    from app.modules.query.service import QueryService

    captured: dict = {}

    async def forecast_alias(alias, horizon, **kwargs):
        captured.update(alias=alias, horizon=horizon, **kwargs)
        return {
            "forecast": [
                {
                    "timestamp": "2026-02-01",
                    "series": "west",
                    "prediction": 42.0,
                    "lower": 40.0,
                    "upper": 44.0,
                }
            ]
        }

    async def no_audit(**kwargs):
        del kwargs

    monkeypatch.setattr(ml_service_module.ml_engine_service, "forecast_alias", forecast_alias)
    monkeypatch.setattr(query_service_module, "write_audit_log", no_audit)
    result = await QueryService().execute(
        "SELECT * FROM ML_FORECAST(MODEL => 'sales', HORIZON => 1, SERIES => 'west')",
        username="alice",
        encrypted_password="unused",
        database="analytics",
    )
    assert result.columns == ["timestamp", "series", "prediction", "lower", "upper"]
    assert result.rows == [["2026-02-01", "west", 42.0, 40.0, 44.0]]
    assert captured == {
        "alias": "sales",
        "horizon": 1,
        "owner_name": "alice",
        "database_name": "analytics",
        "level": 95,
        "series": "west",
    }


def test_hyperparameters_reach_the_explicit_estimator():
    x = np.arange(60, dtype=float)
    output = train_tabular(
        pa.table({"x": x, "target": x * 2}),
        MLExecutionSpec(
            task=MLTask.REGRESSION,
            input_sql="SELECT x, target FROM t",
            security=SECURITY,
            target_column="target",
            algorithm="random_forest",
            parameters={"test_size": 0.2, "estimator_parameters": {"n_estimators": 17}},
            budget=BUDGET,
        ),
    )
    assert isinstance(output.bundle["model"], RandomForestRegressor)
    assert output.bundle["model"].n_estimators == 17
    assert output.metrics["hyperparameters"]["n_estimators"] == 17


def test_anomaly_selection_is_rank_normalized_not_raw_scale():
    values = np.r_[np.linspace(-1, 1, 79), 100.0]
    output = train_anomaly(
        pa.table({"id": np.arange(80), "value": values}),
        MLExecutionSpec(
            task=MLTask.ANOMALY_DETECTION,
            input_sql="SELECT id, value FROM t",
            security=SECURITY,
            feature_columns=("value",),
            row_identifier="id",
            parameters={"contamination": 0.05},
            budget=BUDGET,
        ),
    )
    assert output.metrics["selection_method"] == "rank_normalized_consensus_agreement"
    assert "candidate_scores" not in output.metrics
    assert max(row["anomaly_score"] for row in output.results) == 1.0
    outlier = next(row for row in output.results if row["row_id"] == 79)
    assert outlier["anomaly_score"] == 1.0


def test_forecast_validates_each_series_and_can_drop_with_warning():
    rows = [
        {"store": "healthy", "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i), "y": i}
        for i in range(30)
    ] + [
        {"store": "short", "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i), "y": i}
        for i in range(3)
    ]
    base = dict(
        task=MLTask.FORECAST,
        input_sql="SELECT store, date, y FROM t",
        security=SECURITY,
        target_column="y",
        timestamp_column="date",
        series_column="store",
        horizon=4,
        algorithm="naive",
        budget=BUDGET,
    )
    with pytest.raises(ValueError, match="insufficient: short"):
        train_forecast(pa.Table.from_pylist(rows), MLExecutionSpec(**base))
    output = train_forecast(
        pa.Table.from_pylist(rows),
        MLExecutionSpec(**base, parameters={"insufficient_series_policy": "drop"}),
    )
    assert output.metrics["dropped_series"] == ["short"]
    assert output.metrics["warnings"]
    assert {row["series"] for row in output.results} == {"healthy"}


@pytest.mark.asyncio
async def test_persisted_forecast_reloads_and_uses_forecast_semantics(monkeypatch):
    table = pa.table(
        {
            "date": pd.date_range("2026-01-01", periods=40, freq="D"),
            "revenue": np.arange(40, dtype=float),
        }
    )
    output = train_forecast(
        table,
        MLExecutionSpec(
            task=MLTask.FORECAST,
            input_sql="SELECT date, revenue FROM sales",
            security=SECURITY,
            target_column="revenue",
            timestamp_column="date",
            horizon=3,
            algorithm="naive",
            budget=BUDGET,
        ),
    )
    payload, checksum = serialize_bundle(output.bundle)
    store = MemoryArtifactStore()
    uri = store.put("forecast/model.joblib", payload)

    class Repository:
        async def get_version(self, *args, **kwargs):
            del args, kwargs
            return {
                "model_id": "forecast-1",
                "version": 1,
                "model_name": "sales",
                "artifact_uri": uri,
                "artifact_sha256": checksum,
            }

    from app.modules.ml_engine.runtime import model_runtime as runtime_module

    event_loop_thread = threading.get_ident()
    deserialization_threads: list[int] = []
    original_deserialize = runtime_module.deserialize_bundle

    def recording_deserialize(*args, **kwargs):
        deserialization_threads.append(threading.get_ident())
        return original_deserialize(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "deserialize_bundle", recording_deserialize)
    runtime = ModelRuntime(repository=Repository(), store=store)
    _, forecast = await runtime.forecast_version(
        "forecast-1", 1, 5, owner_name="alice", database_name="analytics"
    )
    assert forecast.num_rows == 5
    assert forecast.column_names == ["timestamp", "series", "prediction", "lower", "upper"]
    assert min(forecast.column("timestamp").to_pylist()) > pd.Timestamp("2026-02-09", tz="UTC")
    assert deserialization_threads and deserialization_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_bounded_executor_runs_cpu_work_off_event_loop():
    executor = BoundedMLExecutor(max_workers=1, name="test-ml-inference")
    try:
        event_loop_thread = threading.get_ident()
        worker_thread = await executor.run(threading.get_ident)
        assert worker_thread != event_loop_thread
    finally:
        executor.close()


def test_primary_prediction_path_returns_arrow_table():
    model = LinearRegression().fit([[0.0], [1.0]], [0.0, 2.0])
    result = ModelRuntime.predict_bundle_table(
        {"task": "regression", "model": model, "feature_columns": ["x"]},
        pa.table({"id": [1, 2], "x": [2.0, 3.0]}),
    )
    assert isinstance(result, pa.Table)
    assert result.column_names == ["id", "x", "prediction"]
    assert result.column("prediction").to_pylist() == pytest.approx([4.0, 6.0])


def test_ephemeral_cache_enforces_lru_bytes_and_cleans_artifacts():
    evicted: list[str] = []
    cache = EphemeralRunCache(
        60,
        max_entries=2,
        max_memory_bytes=40,
        on_evict=lambda entry: evicted.append(entry.artifact_uri),
    )
    for index in range(3):
        cache.put(
            EphemeralEntry(
                run_id=f"r{index}",
                fingerprint=f"f{index}",
                scope_key="s",
                output=object(),
                artifact_sha256="c",
                expires_at=time.monotonic() + 60,
                artifact_uri=f"memory://{index}",
                artifact_payload=b"1234567890",
            )
        )
    assert len(cache._by_run) <= 2
    assert cache.memory_bytes <= 40
    assert evicted
