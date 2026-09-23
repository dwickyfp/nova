"""Behavior gates for worker artifacts, SQL projections, and durable run metadata."""

import asyncio
import hashlib
import pickle
import threading
import time
from dataclasses import replace

import numpy as np
import pyarrow as pa
import pytest
from scipy.sparse import csr_matrix

from app.common.ml_intercept import detect_ml_predict, rewrite_ml_predict_projection
from app.modules.ml_engine.artifacts.store import MemoryArtifactStore
from app.modules.ml_engine.automl.metrics import higher_is_better
from app.modules.ml_engine.data.datasource import collect_bounded
from app.modules.ml_engine.data.mysql_fallback import PreferredDataSource
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.ephemeral.cache import EphemeralEntry, EphemeralRunCache
from app.modules.ml_engine.ephemeral.repository import sanitized_spec
from app.modules.ml_engine.execution.deadline import ExecutionDeadline
from app.modules.ml_engine.execution.inference_worker import MLInferenceJob, materialize_batches
from app.modules.ml_engine.execution.job_runner import MLJobRunner
from app.modules.ml_engine.execution.worker import MLWorkerJob, MLWorkerSecurity
from app.modules.ml_engine.preprocessing.memory import bounded_dense
from app.modules.ml_engine.runtime.model_cache import ModelCache
from app.modules.ml_engine.service import MLEngineService
from app.modules.ml_engine.spec import (
    ExecutionBudget,
    MLExecutionSpec,
    MLExecutionTimeout,
    MLMemoryBudgetExceeded,
    MLSecurityContext,
    MLTask,
    UnsupportedMLSQLExpression,
)
from tests.unit.ml_fakes import MemoryEphemeralRepository

SECURITY = MLSecurityContext("alice", "password", database="analytics")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT ML_PREDICT('m', x) FROM t",
        "WITH x AS (SELECT * FROM t) SELECT id, ML_PREDICT('m', x) p FROM x",
        "SELECT /* x,y */ ML_PREDICT('m', COALESCE(x, 0), LOG(y+1)) p FROM t",
        "SELECT id, ML_PREDICT('m', CASE WHEN x>0 THEN x ELSE 0 END) p FROM (SELECT * FROM t) q",
        "SELECT ML_PREDICT('m', CAST(x AS DECIMAL(10,2))) p FROM t",
    ],
)
def test_ast_feature_queries(sql):
    plan = rewrite_ml_predict_projection(sql, detect_ml_predict(sql))
    assert "ML_PREDICT" not in plan.feature_sql
    assert plan.predictions


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT ROUND(ML_PREDICT('m', x), 2) FROM t",
        "SELECT ML_PREDICT('m', x)+1 FROM t",
        "SELECT CAST(ML_PREDICT('m', x) AS DECIMAL(10,2)) FROM t",
        "SELECT CASE WHEN ML_PREDICT('m',x)>0.5 THEN 'yes' ELSE 'no' END FROM t",
        "SELECT id FROM t WHERE ML_PREDICT('m',x)>0",
        "SELECT DISTINCT ML_PREDICT('m',x) FROM t",
        "SELECT ML_PREDICT('m',x) FROM t ORDER BY 1",
        "SELECT * FROM (SELECT ML_PREDICT('m',x) FROM t) q",
        "SELECT ML_PREDICT('m',x) FROM t UNION SELECT x FROM q",
        "SELECT *, ML_PREDICT('m', x) FROM t",
    ],
)
def test_unsupported_semantics_fail_explicitly(sql):
    with pytest.raises(UnsupportedMLSQLExpression):
        rewrite_ml_predict_projection(sql, detect_ml_predict(sql))


def test_multiple_predictions_reuse_features_and_preserve_positions():
    sql = "SELECT id, ML_PREDICT('a', x) p1, ML_PREDICT('b', x,y) p2 FROM t"
    plan = rewrite_ml_predict_projection(sql, detect_ml_predict(sql))
    assert [(p.alias, p.prediction_index, p.prediction_name) for p in plan.predictions] == [
        ("a", 1, "p1"),
        ("b", 2, "p2"),
    ]
    assert plan.feature_sql.count("(x)") == 1


@pytest.mark.parametrize(
    "metric,expected",
    [("accuracy", True), ("r2", True), ("rmse", False), ("mae", False), ("log_loss", False)],
)
def test_metric_direction(metric, expected):
    assert higher_is_better(metric) is expected


def test_dense_guard_precedes_allocation():
    matrix = csr_matrix((500_000, 20_000), dtype=np.float64)
    with pytest.raises(MLMemoryBudgetExceeded):
        bounded_dense(matrix)


def test_fingerprint_replacement_survives_old_run_eviction():
    cache = EphemeralRunCache(60, max_entries=1)
    for run in ("old", "new"):
        cache.put(EphemeralEntry(run, "F", "scope", None, "sha", time.monotonic() + 60))
    assert cache.by_fingerprint("scope", "F").run_id == "new"


async def test_singleflight_locks_are_released_on_success_failure_and_cancel():
    cache = ModelCache(max_models=1, max_bytes=100, ttl_seconds=60)

    async def loader():
        await asyncio.sleep(0)
        return {"model": 1}, 1

    for i in range(100):
        await asyncio.gather(*(cache.get_or_load((str(i), 1), loader) for _ in range(3)))
    assert cache._locks == {}

    gate = asyncio.Event()
    task = asyncio.create_task(cache.get_or_load(("cancel", 1), gate.wait))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cache._locks == {} and cache._lock_users == {}
    assert cache._lock_users == {}

    async def fail():
        raise ValueError("bad")

    with pytest.raises(ValueError):
        await cache.get_or_load(("bad", 1), fail)
    assert cache._locks == {}


def test_deadline_and_exception_survive_ipc(monkeypatch):
    monkeypatch.setattr("app.modules.ml_engine.execution.deadline.time.monotonic", lambda: 4)
    assert ExecutionDeadline(10).remaining("training", reserve=2) == 4
    with pytest.raises(MLExecutionTimeout) as error:
        ExecutionDeadline(3).remaining("artifact_upload")
    assert pickle.loads(pickle.dumps(error.value)).stage == "artifact_upload"


class BatchSource:
    async def stream(self, sql, security):
        yield pa.record_batch({"x": np.arange(20.0), "target": np.arange(20.0)})


async def test_actual_fallback_transport(monkeypatch):
    source = PreferredDataSource()

    class Broken:
        queue_wait_seconds = 0

        async def stream(self, sql, security):
            raise OSError("unavailable")
            yield

    source.arrow = Broken()
    source.mysql = BatchSource()
    result = await collect_bounded(
        source, "SELECT x,target FROM t", SECURITY, ExecutionBudget(10, 100, 10000)
    )
    assert result.metrics.transport == "mysql_fallback"


class NoIPCModel:
    def __reduce__(self):
        if threading.current_thread().name == "QueueFeederThread":
            raise AssertionError("Estimator crossed worker IPC")
        return NoIPCModel, ()

    def predict(self, matrix):
        return np.zeros(matrix.shape[0])


def run_production_worker_with_fake_io():
    from app.modules.ml_engine.execution import worker

    store = MemoryArtifactStore()
    worker.decrypt_password = lambda value: value
    worker.PreferredDataSource = BatchSource
    worker.ObjectArtifactStore = lambda: store
    worker.train_tabular = lambda table, spec: TrainingOutput(
        {"model": NoIPCModel(), "feature_columns": ["x"]}, "sklearn", "fake", {}, ["x"], 20
    )
    result = worker.execute_worker_job(
        MLWorkerJob(
            "run",
            MLExecutionSpec(
                MLTask.REGRESSION,
                "",
                SECURITY,
                target_column="target",
                budget=ExecutionBudget(30, 100, 10000),
            ),
            "SELECT x,target FROM t",
            MLWorkerSecurity("alice", "password", "analytics", None, None, "default"),
            artifact_key="models/run/model.joblib",
        )
    )
    assert hashlib.sha256(store.get(result.artifact_uri)).hexdigest() == result.artifact_sha256
    return result


async def test_real_spawn_returns_only_verified_artifact_descriptor():
    runner = MLJobRunner(max_workers=1)
    try:
        result = await runner.run(run_production_worker_with_fake_io, timeout_seconds=60)
        assert result.output.bundle == {}
        assert result.artifact_uri and result.artifact_sha256
        assert result.artifact_size > 0
        assert result.worker_result_ipc_bytes < 10_000
    finally:
        runner.close()


async def test_million_row_materialization_stays_batched(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ML_INFERENCE_BATCH_ROWS", 4096)
    sizes = []

    class Model:
        def predict(self, matrix):
            sizes.append(len(matrix))
            return np.zeros(len(matrix))

    class Source:
        transport_used = "arrow_flight"

        async def stream(self, sql, security):
            for _ in range(100):
                yield pa.record_batch({"x": np.arange(10_000.0)})

    class Sink:
        def __init__(self):
            self.writes = 0

        def put(self, key, payload):
            self.writes += 1
            return "memory://" + key

        def delete(self, uri):
            pass

    sink = Sink()
    job = MLInferenceJob(
        "r", "", None, {}, "results/r", time.monotonic() + 60, 1_000_000, 20_000_000
    )
    result = await materialize_batches(
        job,
        {"model": Model(), "feature_columns": ["x"]},
        Source(),
        sink,
        SECURITY,
        "SELECT x FROM t",
    )
    assert result["result_rows"] == 1_000_000
    assert max(sizes) <= 4096
    assert len(sizes) == 300
    assert sink.writes == 301
    assert "rows" not in result and "model" not in result


async def test_promotion_reconstructs_spec_on_another_replica_and_cleanup_is_off_loop():
    from app.modules.ml_engine.artifacts.serializer import serialize_bundle

    store = MemoryArtifactStore()
    payload, sha = serialize_bundle({"model": NoIPCModel()})
    uri = store.put("ephemeral/run", payload)
    shared = MemoryEphemeralRepository()
    spec = MLExecutionSpec(
        MLTask.FORECAST,
        "SELECT day,y FROM t",
        SECURITY,
        target_column="y",
        timestamp_column="day",
        series_column="store",
        horizon=5,
        frequency="D",
        parameters={"password": "hidden", "alpha": 1},
    )
    entry = EphemeralEntry(
        "r",
        "f",
        SECURITY.scope_key,
        TrainingOutput({}, "statsforecast", "naive", {}, [], 100),
        sha,
        time.monotonic() + 60,
        task="forecast",
        artifact_uri=uri,
        artifact_size=len(payload),
        spec_snapshot=sanitized_spec(spec),
    )
    await shared.put(entry)

    class Registry:
        async def reserve_version(self, spec, **kwargs):
            self.spec = spec
            return "model", 1

        async def register_version(self, **kwargs):
            self.registered = kwargs

    registry = Registry()
    replica = MLEngineService(
        repository=registry, artifact_store=store, ephemeral_repository=shared
    )
    await replica.promote("r", model_name="forecast", security=SECURITY)
    assert registry.spec.timestamp_column == "day"
    assert registry.spec.horizon == 5 and registry.spec.series_column == "store"
    assert registry.spec.frequency == "D" and registry.spec.target_column == "y"
    assert registry.spec.parameters == {"alpha": 1}
    assert await shared.get("r", replace(SECURITY, tenant="other").scope_key) is None
    entry.expires_at = time.monotonic() - 1
    await shared.put(entry)
    threads = []
    original = store.delete

    def delete(uri):
        threads.append(threading.get_ident())
        original(uri)

    store.delete = delete
    assert await replica.cleanup_ephemeral() == 1
    assert await replica.cleanup_ephemeral() == 0
    assert threads and threads[0] != threading.get_ident()


@pytest.mark.parametrize("prefix", ["SELECT", "select", "SELECT /* comment */"])
def test_table_prediction_ast(prefix):
    from app.common.ml_intercept import detect_ml_predict_table

    assert detect_ml_predict_table(
        f"{prefix} * FROM ML_PREDICT_TABLE('model', 'SELECT x FROM t')"
    ) == ("model", "SELECT x FROM t")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM ML_PREDICT_TABLE('a'+'b', 'SELECT x FROM t')",
        "SELECT * FROM ML_PREDICT_TABLE('m', 'SELECT x FROM t') LIMIT 10",
        "INSERT INTO t SELECT * FROM ML_PREDICT_TABLE('m', 'SELECT x FROM t')",
        "SELECT * FROM ML_PREDICT_TABLE('m', 'SELECT x FROM t'); SELECT 1",
    ],
)
def test_table_prediction_rejects_outer_semantics(sql):
    from app.common.ml_intercept import detect_ml_predict_table

    with pytest.raises(UnsupportedMLSQLExpression):
        detect_ml_predict_table(sql)


def test_scope_delimiters_cannot_alias_another_identity():
    assert (
        replace(SECURITY, tenant="one:two", username="three").scope_key
        != replace(SECURITY, tenant="one", username="two:three").scope_key
    )


def test_categorical_expansion_remains_sparse_and_bounded():
    from scipy.sparse import issparse

    from app.modules.ml_engine.preprocessing.preprocessor import FeaturePreprocessor

    preprocessor = FeaturePreprocessor(["category"], max_categories=20)
    matrix = preprocessor.fit_transform(pa.table({"category": [str(i) for i in range(10000)]}))
    assert matrix.shape == (10000, 20)
    assert issparse(matrix)


@pytest.mark.parametrize("ready", [False, True])
async def test_abandoned_upload_lease_cleanup_preserves_ready_version(ready):
    shared = MemoryEphemeralRepository()
    store = MemoryArtifactStore()
    uri = store.put("models/m/1/model.joblib", b"model")
    temporary = store.put("models/m/1/model.joblib.upload-orphan", b"partial")

    class Registry:
        aborted = False

        async def get_version(self, *args, **kwargs):
            return {"artifact_uri": uri} if ready else None

        async def abort_version(self, *args):
            self.aborted = True

    registry = Registry()
    service = MLEngineService(
        repository=registry, artifact_store=store, ephemeral_repository=shared
    )
    await service._lease_upload(
        "r", SECURITY, uri, ExecutionDeadline(time.monotonic() - 100), model_id="m", version=1
    )
    assert await service.cleanup_ephemeral() == 1
    assert (uri in store.objects) is ready
    assert registry.aborted is not ready
    assert temporary not in store.objects
    assert await service.cleanup_ephemeral() == 0


async def test_result_cleanup_retries_after_manifest_deletion():
    class RetryRepository(MemoryEphemeralRepository):
        failures = 1

        async def delete_expired(self, entry):
            if self.failures:
                self.failures -= 1
                raise OSError("transient")
            await super().delete_expired(entry)

    shared = RetryRepository()
    store = MemoryArtifactStore()
    uri = store.put("results/scope/run/manifest.json", b"{}")
    store.put("results/scope/run/part-00000000.parquet", b"part")
    service = MLEngineService(artifact_store=store, ephemeral_repository=shared)
    await service._lease_upload(
        "r", SECURITY, uri, ExecutionDeadline(time.monotonic() - 100), result=True
    )
    assert await service.cleanup_ephemeral() == 0
    assert not store.objects
    assert await service.cleanup_ephemeral() == 1


def test_migration_sql_parses_and_keeps_legacy_models():
    from pathlib import Path

    from app.modules.query.dialect.parser import _parse_tree

    sql = (Path(__file__).parents[2] / "migrations/20260922_ml_ephemeral_runs.sql").read_text()
    _, _, errors = _parse_tree(sql)
    assert not errors
    assert "DROP " not in sql.upper()
    assert 'DEFAULT "default"' in sql
    assert "PRIMARY KEY(run_id)" in sql


async def test_bootstrap_tenant_migration_is_idempotent(monkeypatch):
    from app.common import nova_system

    columns = set()
    statements = []

    async def exists(table, column):
        return (table, column) in columns or table == "ML_MODEL_ALIASES"

    async def primary(table):
        return True

    async def execute(sql):
        statements.append(sql)
        for table, column, _ in nova_system.ML_COLUMN_MIGRATIONS:
            if f"{table} ADD COLUMN {column} " in sql:
                columns.add((table, column))

    monkeypatch.setattr(nova_system, "_column_exists", exists)
    monkeypatch.setattr(nova_system, "_is_primary_key_table", primary)
    monkeypatch.setattr(nova_system.db, "execute_system", execute)
    await nova_system.migrate_ml_metadata()
    await nova_system.migrate_ml_metadata()
    assert sum("ADD COLUMN tenant_name" in sql for sql in statements) == 1


@pytest.mark.parametrize("metric", ["rmse", "mae", "mse"])
def test_model_selection_minimizes_error_metrics(monkeypatch, metric):
    from sklearn.dummy import DummyRegressor

    from app.modules.ml_engine.automl.router import AutoMLRouter
    from app.modules.ml_engine.spec import MLMode

    router = AutoMLRouter()
    monkeypatch.setattr(
        router,
        "_candidates",
        lambda *args: [
            ("bad", DummyRegressor(strategy="constant", constant=10)),
            ("good", DummyRegressor(strategy="constant", constant=0)),
        ],
    )
    selection = router.select(
        task=MLTask.REGRESSION,
        mode=MLMode.INTERACTIVE,
        algorithm="manual",
        X_train=np.ones((4, 1)),
        X_valid=np.ones((2, 1)),
        y_train=np.zeros(4),
        y_valid=np.zeros(2),
        metric=metric,
        timeout_seconds=5,
    )
    assert selection.name == "good" and selection.score == 0


async def test_result_part_is_readable_from_new_replica_only_in_original_scope():
    import io
    import json

    import pyarrow.parquet as pq

    shared = MemoryEphemeralRepository()
    store = MemoryArtifactStore()
    buffer = io.BytesIO()
    pq.write_table(pa.table({"prediction": [1.0, 2.0]}), buffer)
    part = store.put("results/s/r/part-00000000.parquet", buffer.getvalue())
    manifest = store.put("results/s/r/manifest.json", json.dumps({"parts": [part]}).encode())
    await shared.put(
        EphemeralEntry(
            "r",
            "",
            SECURITY.scope_key,
            TrainingOutput({}, "inference", "", {}, [], 0),
            "",
            time.monotonic() + 60,
            artifact_uri=manifest,
            artifact_kind="result",
        )
    )
    replica = MLEngineService(artifact_store=store, ephemeral_repository=shared)
    page = await replica.result_page("r", 0, SECURITY)
    assert page["rows"] == [{"prediction": 1.0}, {"prediction": 2.0}]
    assert page["next_part"] is None
    for change in [
        {"tenant": "elsewhere"},
        {"username": "bob"},
        {"role": "other"},
        {"database": "other"},
        {"schema": "other"},
        {"security_context_version": 2},
    ]:
        with pytest.raises(ValueError, match="not found"):
            await replica.result_page("r", 0, replace(SECURITY, **change))


def test_forecast_budget_precedes_model_allocation():
    from app.core.config import settings
    from app.modules.ml_engine.runtime.model_runtime import ModelRuntime
    from app.modules.ml_engine.spec import DataBudgetExceeded

    class Model:
        uids = ["a", "b"]

        def predict(self, **kwargs):
            pytest.fail("Prediction must not allocate an over-budget forecast")

    with pytest.raises(DataBudgetExceeded):
        ModelRuntime.forecast_bundle_table(
            {"task": "forecast", "model": Model()}, settings.ML_RESULT_INLINE_MAX_ROWS
        )


async def test_multiple_predictions_execute_in_exact_projection_order():
    from app.modules.ml_engine.execution.executor import BoundedMLExecutor
    from app.modules.ml_engine.runtime.model_runtime import ModelRuntime

    class Source:
        async def stream(self, sql, security):
            yield pa.record_batch({"id": [1, 2], "__ml_feature_0": [10.0, 20.0]})

    class Model:
        def __init__(self, offset):
            self.offset = offset

        def predict(self, matrix):
            return matrix[:, 0] + self.offset

    class Runtime:
        executor = BoundedMLExecutor(max_workers=1)
        predict_bundle_table = staticmethod(ModelRuntime.predict_bundle_table)

        async def resolve_alias(self, alias, **kwargs):
            assert kwargs["tenant"] == "tenant-A"
            return {"alias": alias}

        async def load(self, metadata):
            return {"model": Model(1 if metadata["alias"] == "a" else 2), "feature_columns": ["x"]}

    class Service(MLEngineService):
        async def _prepare_user_sql(self, sql, security, **kwargs):
            assert security.tenant == "tenant-A"
            return sql

    sql = "SELECT ML_PREDICT('a', x) p1, id, ML_PREDICT('b', x) p2 FROM t"
    plan = rewrite_ml_predict_projection(sql, detect_ml_predict(sql))
    runtime = Runtime()
    service = Service(data_source=Source(), runtime=runtime)
    try:
        _, result = await service.batch_predict_projected(
            "a",
            plan.feature_sql,
            feature_source_columns=plan.feature_columns,
            prediction_index=plan.prediction_index,
            prediction_name=plan.prediction_name,
            predictions=plan.predictions,
            database_name="analytics",
            username="alice",
            password="password",
            tenant="tenant-A",
        )
    finally:
        runtime.executor.close()
    assert result.column_names == ["p1", "id", "p2"]
    assert result.to_pylist() == [
        {"p1": 11.0, "id": 1, "p2": 12.0},
        {"p1": 21.0, "id": 2, "p2": 22.0},
    ]
