"""Component integration flows for the native ML execution architecture.

These tests connect the orchestrator, Arrow batches, worker boundary, artifact
serializer/store, registry semantics, runtime cache, aliases, and promotion.
They deliberately use in-memory infrastructure so they remain part of normal CI;
the StarRocks transport itself is covered by the engine test stack.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from app.modules.ml_engine.artifacts.store import MemoryArtifactStore
from app.modules.ml_engine.execution.job_runner import InlineJobRunner
from app.modules.ml_engine.runtime.model_runtime import ModelRuntime
from app.modules.ml_engine.service import MLEngineService
from app.modules.ml_engine.spec import (
    ExecutionBudget,
    MLExecutionSpec,
    MLMode,
    MLSecurityContext,
    MLTask,
)


class ArrowTables:
    def __init__(self, tables: dict[str, pa.Table]) -> None:
        self.tables = tables
        self.calls: list[tuple[str, str]] = []

    async def stream(self, sql, security):
        self.calls.append((sql, security.username))
        for batch in self.tables[sql].to_batches(max_chunksize=16):
            yield batch


class CountingRunner(InlineJobRunner):
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, func, *args, timeout_seconds, **kwargs):
        self.calls += 1
        return await super().run(func, *args, timeout_seconds=timeout_seconds, **kwargs)


class InMemoryRegistry:
    def __init__(self) -> None:
        self.models: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.versions: dict[tuple[str, int], dict[str, Any]] = {}
        self.aliases: dict[tuple[str, str, str], tuple[str, int]] = {}
        self.runs: list[dict[str, Any]] = []

    async def reserve_version(self, spec, *, feature_columns):
        scope = (
            spec.security.username,
            spec.security.database or "",
            str(spec.model_name),
        )
        model = self.models.get(scope)
        if model is None:
            model = {
                "model_id": str(uuid4()),
                "model_name": spec.model_name,
                "model_type": spec.task.value,
                "owner_name": spec.security.username,
                "database_name": spec.security.database or "",
                "current_version": 0,
                "feature_columns": feature_columns,
            }
            self.models[scope] = model
        model["current_version"] += 1
        return model["model_id"], model["current_version"]

    async def register_version(self, **values):
        output = values["output"]
        spec = values["spec"]
        self.versions[(values["model_id"], values["version"])] = {
            "model_id": values["model_id"],
            "version": values["version"],
            "model_name": spec.model_name,
            "model_type": spec.task.value,
            "owner_name": spec.security.username,
            "database_name": spec.security.database or "",
            "artifact_uri": values["artifact_uri"],
            "artifact_sha256": values["artifact_sha256"],
            "artifact_size": values["artifact_size"],
            "model_binary": None,
            "algorithm": output.algorithm,
        }

    async def record_run(self, **values):
        self.runs.append(values)

    async def get_version(self, model_id, version, *, owner_name, database_name):
        row = self.versions.get((model_id, version))
        if row is None:
            return None
        if row["owner_name"] != owner_name:
            return None
        if row["database_name"] != (database_name or ""):
            return None
        return dict(row)

    async def set_alias(self, alias, model_id, version, *, owner_name, database_name):
        if (
            await self.get_version(
                model_id,
                version,
                owner_name=owner_name,
                database_name=database_name,
            )
            is None
        ):
            raise ValueError("Model version not found")
        self.aliases[(owner_name, database_name or "", alias)] = (model_id, version)
        return {"alias_name": alias, "model_id": model_id, "version": version}

    async def resolve_alias(self, alias, *, owner_name, database_name):
        target = self.aliases.get((owner_name, database_name or "", alias))
        return dict(self.versions[target]) if target else None


class IntegrationService(MLEngineService):
    async def _prepare_user_sql(self, sql, security=None, **kwargs):
        del security, kwargs
        return sql


def _service(tables):
    registry = InMemoryRegistry()
    artifacts = MemoryArtifactStore()
    runner = CountingRunner()
    service = IntegrationService(
        data_source=ArrowTables(tables),
        job_runner=runner,
        artifact_store=artifacts,
        repository=registry,
    )
    return service, registry, artifacts, runner


def _spec(*, name: str, persist: bool, sql: str, security) -> MLExecutionSpec:
    return MLExecutionSpec(
        task=MLTask.CLASSIFICATION,
        input_sql=sql,
        security=security,
        mode=MLMode.INTERACTIVE,
        persist=persist,
        model_name=name if persist else None,
        algorithm="logistic",
        feature_columns=("x", "segment"),
        target_column="target",
        budget=ExecutionBudget(5, 10_000, 10_000_000),
    )


@pytest.mark.asyncio
async def test_persistent_version_alias_and_vectorized_prediction_flow():
    rng = np.random.default_rng(42)
    x = rng.normal(size=80)
    training = pa.table(
        {
            "x": x,
            "segment": ["a", "b"] * 40,
            "target": np.where(x > 0, "yes", "no"),
        }
    )
    features = training.select(["x", "segment"]).slice(0, 25)
    security = MLSecurityContext("alice", "secret", database="analytics")
    service, registry, artifacts, runner = _service({"train": training, "predict": features})

    first = await service.execute(_spec(name="churn", persist=True, sql="train", security=security))
    await service.create_alias(
        "production",
        first.model_id,
        first.version,
        owner_name="alice",
        database_name="analytics",
    )
    predicted = await service.batch_predict(
        "production",
        "predict",
        "analytics",
        username="alice",
        password="secret",
    )

    assert predicted["total_rows"] == 25
    assert len(predicted["predictions"]) == 25
    assert all("prediction" in row for row in predicted["predictions"])
    first_metadata = registry.versions[(first.model_id, 1)]
    assert first_metadata["artifact_uri"].startswith("memory://")
    assert first_metadata["model_binary"] is None
    assert artifacts.objects

    second = await service.execute(
        _spec(name="churn", persist=True, sql="train", security=security)
    )
    assert second.model_id == first.model_id
    assert second.version == 2
    await service.create_alias(
        "production",
        second.model_id,
        second.version,
        owner_name="alice",
        database_name="analytics",
    )
    resolved = await service.runtime.resolve_alias(
        "production", owner_name="alice", database_name="analytics"
    )
    assert resolved["version"] == 2

    old_metadata, old_predictions, _ = await service.runtime.predict_version(
        first.model_id,
        1,
        features,
        owner_name="alice",
        database_name="analytics",
    )
    assert old_metadata["version"] == 1
    assert len(old_predictions) == 25
    assert runner.calls == 2


@pytest.mark.asyncio
async def test_ephemeral_promotion_reuses_artifact_and_enforces_scope():
    rng = np.random.default_rng(7)
    x = rng.normal(size=50)
    table = pa.table(
        {
            "x": x,
            "segment": ["a", "b"] * 25,
            "target": np.where(x > 0, "yes", "no"),
        }
    )
    security = MLSecurityContext("alice", "secret", database="analytics")
    service, registry, artifacts, runner = _service({"train": table})

    ephemeral = await service.execute(
        _spec(name="unused", persist=False, sql="train", security=security)
    )
    assert ephemeral.model_id is None
    assert registry.versions == {}
    assert artifacts.objects == {}

    with pytest.raises(ValueError, match="belongs to another scope"):
        await service.promote(
            ephemeral.run_id,
            model_name="stolen",
            security=MLSecurityContext("bob", "secret", database="analytics"),
        )

    promoted = await service.promote(
        ephemeral.run_id,
        model_name="churn",
        security=security,
    )
    assert promoted.status == "promoted"
    assert promoted.version == 1
    assert runner.calls == 1
    assert len(artifacts.objects) == 1
    restarted_runtime = ModelRuntime(repository=registry, store=artifacts)
    _, predictions, _ = await restarted_runtime.predict_version(
        promoted.model_id,
        promoted.version,
        table.select(["x", "segment"]).slice(0, 5),
        owner_name="alice",
        database_name="analytics",
    )
    assert len(predictions) == 5
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_ephemeral_forecast_anomaly_and_clustering_flows():
    rng = np.random.default_rng(21)
    forecast = pa.table(
        {
            "date": pd.date_range("2026-01-01", periods=30, freq="D"),
            "revenue": np.arange(30, dtype=float),
        }
    )
    anomaly_values = np.r_[rng.normal(size=39), 30.0]
    anomaly = pa.table({"id": np.arange(40), "value": anomaly_values})
    clusters = np.r_[rng.normal(-3, 0.15, (20, 2)), rng.normal(3, 0.15, (20, 2))]
    clustering = pa.table(
        {"id": np.arange(40), "x": clusters[:, 0], "y": clusters[:, 1]}
    )
    security = MLSecurityContext("alice", "secret", database="analytics")
    service, registry, artifacts, runner = _service(
        {"forecast": forecast, "anomaly": anomaly, "clustering": clustering}
    )
    common = {
        "security": security,
        "mode": MLMode.INTERACTIVE,
        "persist": False,
        "budget": ExecutionBudget(10, 10_000, 10_000_000),
    }

    forecast_result = await service.execute(
        MLExecutionSpec(
            task=MLTask.FORECAST,
            input_sql="forecast",
            target_column="revenue",
            timestamp_column="date",
            horizon=4,
            **common,
        )
    )
    anomaly_result = await service.execute(
        MLExecutionSpec(
            task=MLTask.ANOMALY_DETECTION,
            input_sql="anomaly",
            feature_columns=("value",),
            row_identifier="id",
            **common,
        )
    )
    clustering_result = await service.execute(
        MLExecutionSpec(
            task=MLTask.CLUSTERING,
            input_sql="clustering",
            feature_columns=("x", "y"),
            row_identifier="id",
            **common,
        )
    )

    assert len(forecast_result.results) == 4
    assert forecast_result.metrics["validation_strategy"] == "chronological_holdout"
    assert max(row["anomaly_score"] for row in anomaly_result.results) == next(
        row["anomaly_score"] for row in anomaly_result.results if row["row_id"] == 39
    )
    assert clustering_result.metrics["cluster_count"] >= 2
    assert len(clustering_result.results) == 40
    assert registry.versions == {}
    assert artifacts.objects == {}
    assert runner.calls == 3
