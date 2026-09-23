"""L3 proof that Nova reads StarRocks 4.1 through Arrow Flight SQL."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncmy
import pytest
from cryptography.fernet import Fernet
from sklearn.linear_model import LinearRegression

from app.core.config import settings
from app.modules.ml_engine.artifacts.store import ObjectArtifactStore
from app.modules.ml_engine.data.arrow_flight import ArrowFlightDataSource
from app.modules.ml_engine.data.datasource import collect_bounded
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.registry.repository import ModelRegistryRepository
from app.modules.ml_engine.service import MLEngineService
from app.modules.ml_engine.spec import (
    ExecutionBudget,
    MLExecutionSpec,
    MLSecurityContext,
    MLTask,
)
from tests.conftest import engine_host_ports, require_stack

pytestmark = pytest.mark.engine


@pytest.mark.asyncio
async def test_starrocks_arrow_flight_preserves_types_and_batches(docker_services, monkeypatch):
    require_stack(docker_services)
    ports = engine_host_ports()
    root = await asyncmy.connect(
        host="127.0.0.1",
        port=ports["starrocks-fe"],
        user="root",
        password="",
        autocommit=True,
    )
    database = f"nova_ml_arrow_{uuid4().hex[:10]}"
    async with root.cursor() as cursor:
        await cursor.execute(f"CREATE DATABASE `{database}`")
        await cursor.execute(
            f"CREATE TABLE `{database}`.features ("
            "id BIGINT, amount DOUBLE, category VARCHAR(32), event_time DATETIME"
            ") DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            'PROPERTIES("replication_num"="1")'
        )
        await cursor.execute(
            f"INSERT INTO `{database}`.features VALUES "
            "(1, 12.5, 'a', '2026-01-01 01:02:03'),"
            "(2, 18.0, 'b', '2026-01-02 04:05:06'),"
            "(3, 21.5, 'a', '2026-01-03 07:08:09')"
        )

    monkeypatch.setattr(settings, "STARROCKS_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "STARROCKS_ARROW_FLIGHT_PORT", ports["starrocks-fe-arrow"])
    try:
        result = await collect_bounded(
            ArrowFlightDataSource(batch_size=2),
            "SELECT id, amount, category, event_time FROM features ORDER BY id",
            MLSecurityContext("root", "", database=database),
            ExecutionBudget(20, 100, 1_000_000),
        )
        assert result.table.num_rows == 3
        assert result.metrics.batches_read == 2
        assert str(result.table.schema.field("id").type) == "int64"
        assert str(result.table.schema.field("amount").type) == "double"
        assert result.table.column("category").to_pylist() == ["a", "b", "a"]
    finally:
        async with root.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
        root.close()


@pytest.mark.asyncio
async def test_concurrent_version_reservation_promotes_only_ready(docker_services, monkeypatch):
    require_stack(docker_services)
    ports = engine_host_ports()
    monkeypatch.setattr(settings, "STARROCKS_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "STARROCKS_FE_MYSQL_PORT", ports["starrocks-fe"])
    monkeypatch.setattr(settings, "REDIS_URL", f"redis://127.0.0.1:{ports['redis']}/0")
    bootstrap = await asyncmy.connect(
        host="127.0.0.1",
        port=ports["starrocks-fe"],
        user="root",
        password="",
        autocommit=True,
    )
    async with bootstrap.cursor() as cursor:
        await cursor.execute("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
        await cursor.execute(
            "CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODELS ("
            "model_id VARCHAR(64) NOT NULL, model_type VARCHAR(64) NOT NULL, "
            "model_name VARCHAR(256) NOT NULL, target_column VARCHAR(128), "
            "feature_columns TEXT, hyperparameters TEXT, training_sql TEXT, "
            "database_name VARCHAR(128), schema_name VARCHAR(128), created_at DATETIME, "
            "created_by VARCHAR(128), current_version INT DEFAULT '0', updated_at DATETIME"
            ") PRIMARY KEY(model_id) DISTRIBUTED BY HASH(model_id) BUCKETS 1 "
            'PROPERTIES("replication_num"="1", "enable_persistent_index"="true")'
        )
        await cursor.execute(
            "SELECT COLUMN_NAME FROM information_schema.columns WHERE TABLE_SCHEMA='NOVA_SYSTEM' "
            "AND TABLE_NAME='ML_MODELS' AND COLUMN_NAME='tenant_name'"
        )
        if not await cursor.fetchone():
            from pathlib import Path

            from app.common.sql_guard import split_sql_statements

            migration = (
                Path(__file__).parents[2] / "migrations/20260922_ml_ephemeral_runs.sql"
            ).read_text()
            await cursor.execute(split_sql_statements(migration)[0])
        await cursor.execute(
            "CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODEL_VERSIONS ("
            "model_id VARCHAR(64) NOT NULL, version INT NOT NULL, status VARCHAR(32), "
            "training_rows BIGINT, metrics TEXT, artifact_uri VARCHAR(2048), "
            "artifact_sha256 VARCHAR(64), artifact_size BIGINT, task VARCHAR(64), "
            "framework VARCHAR(128), framework_version VARCHAR(64), algorithm VARCHAR(128), "
            "feature_schema TEXT, training_duration_ms BIGINT, reservation_token VARCHAR(64), "
            "ready_at DATETIME, failed_at DATETIME, failure_reason TEXT, model_binary TEXT, "
            "created_at DATETIME, created_by VARCHAR(128)"
            ") PRIMARY KEY(model_id,version) DISTRIBUTED BY HASH(model_id) BUCKETS 1 "
            'PROPERTIES("replication_num"="1", "enable_persistent_index"="true")'
        )
    bootstrap.close()
    repository = ModelRegistryRepository()
    model_name = f"concurrent_{uuid4().hex[:12]}"
    spec = MLExecutionSpec(
        task=MLTask.REGRESSION,
        input_sql="SELECT x, target FROM features",
        security=MLSecurityContext("root", "", database="NOVA_EXAMPLE"),
        persist=True,
        model_name=model_name,
        target_column="target",
        budget=ExecutionBudget(20, 1000, 10_000_000),
    )
    reservations = await asyncio.gather(
        *(repository.reserve_version(spec, feature_columns=["x"]) for _ in range(10))
    )
    model_ids = {model_id for model_id, _ in reservations}
    versions = sorted(version for _, version in reservations)
    assert len(model_ids) == 1
    assert versions == list(range(1, 11))
    model_id = next(iter(model_ids))
    output = TrainingOutput(
        bundle={"task": "regression", "model": LinearRegression()},
        engine="sklearn",
        algorithm="ridge",
        metrics={},
        feature_columns=["x"],
        training_rows=40,
    )
    await asyncio.gather(
        *(
            repository.register_version(
                model_id=model_id,
                version=version,
                spec=spec,
                output=output,
                artifact_uri=f"nova-artifact://test/model/v{version}",
                artifact_sha256="0" * 64,
                artifact_size=1,
                training_duration_ms=1,
            )
            for version in reversed(versions)
        )
    )
    _, failed_version = await repository.reserve_version(spec, feature_columns=["x"])
    assert failed_version == 11
    assert await repository.abort_version(model_id, failed_version) is True

    connection = await repository._connect()
    try:
        async with connection.cursor(asyncmy.cursors.DictCursor) as cursor:
            await cursor.execute(
                "SELECT current_version FROM NOVA_SYSTEM.ML_MODELS WHERE model_id=%s",
                (model_id,),
            )
            assert int((await cursor.fetchone())["current_version"]) == 10
            await cursor.execute(
                "SELECT version,status FROM NOVA_SYSTEM.ML_MODEL_VERSIONS "
                "WHERE model_id=%s ORDER BY version",
                (model_id,),
            )
            rows = await cursor.fetchall()
            assert [int(row["version"]) for row in rows] == list(range(1, 12))
            assert [row["status"] for row in rows[:10]] == ["READY"] * 10
            assert rows[-1]["status"] == "FAILED"
            await cursor.execute(
                "DELETE FROM NOVA_SYSTEM.ML_MODEL_VERSIONS WHERE model_id=%s", (model_id,)
            )
            await cursor.execute("DELETE FROM NOVA_SYSTEM.ML_MODELS WHERE model_id=%s", (model_id,))
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_real_worker_fetches_arrow_without_api_table(
    docker_services, minio_client, monkeypatch, tmp_path
):
    """Cross the spawn boundary with a descriptor and fetch inside the child."""
    require_stack(docker_services)
    ports = engine_host_ports()
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("FERNET_KEY", key)
    monkeypatch.setenv("STARROCKS_HOST", "127.0.0.1")
    monkeypatch.setenv("STARROCKS_ARROW_FLIGHT_PORT", str(ports["starrocks-fe-arrow"]))
    monkeypatch.setenv("STARROCKS_FE_MYSQL_PORT", str(ports["starrocks-fe"]))
    from app.core.config import load_nova_app_config

    storage_settings = {
        "NOVA_CONFIG_PATH": str(tmp_path / "missing-config.yaml"),
        "S3_ENDPOINT": f"http://127.0.0.1:{ports['minio']}",
        "S3_BUCKET": "test-stage",
        "S3_ACCESS_KEY": "minioadmin",
        "S3_SECRET_KEY": "minioadmin",
    }
    for name, value in storage_settings.items():
        monkeypatch.setenv(name, value)
        monkeypatch.setattr(settings, name, value)
    load_nova_app_config.cache_clear()
    monkeypatch.setattr(settings, "FERNET_KEY", key)
    monkeypatch.setattr(settings, "STARROCKS_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "STARROCKS_FE_MYSQL_PORT", ports["starrocks-fe"])
    monkeypatch.setattr(settings, "STARROCKS_ARROW_FLIGHT_PORT", ports["starrocks-fe-arrow"])
    from app.core import security as security_module

    monkeypatch.setattr(security_module, "_fernet", None)

    root = await asyncmy.connect(
        host="127.0.0.1",
        port=ports["starrocks-fe"],
        user="root",
        password="",
        autocommit=True,
    )
    database = f"nova_ml_worker_{uuid4().hex[:10]}"
    async with root.cursor() as cursor:
        await cursor.execute("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
        await cursor.execute(f"CREATE DATABASE `{database}`")
        await cursor.execute(
            f"CREATE TABLE `{database}`.features (id INT, x DOUBLE, target DOUBLE) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            'PROPERTIES("replication_num"="1")'
        )
        values = ",".join(f"({value},{value},{value * 2})" for value in range(1, 41))
        await cursor.execute(f"INSERT INTO `{database}`.features VALUES {values}")

    class Repository(ModelRegistryRepository):
        async def record_run(self, **kwargs):
            del kwargs

    class Service(MLEngineService):
        async def _prepare_user_sql(self, sql, security):
            del security
            return sql

    service = Service(artifact_store=ObjectArtifactStore(), repository=Repository())
    await service.ephemeral_repository.ensure_schema()
    result = None
    try:
        result = await service.execute(
            MLExecutionSpec(
                task=MLTask.REGRESSION,
                input_sql="SELECT x, target FROM features ORDER BY x",
                security=MLSecurityContext("root", "", database=database),
                target_column="target",
                algorithm="ridge",
                budget=ExecutionBudget(20, 1000, 10_000_000),
            )
        )
        assert result.training_rows == 40
        assert result.telemetry["ipc_mode"] == "worker_direct"
        assert result.telemetry["dataset_ipc_bytes"] == 0
        assert result.telemetry["trained_model_ipc_bytes"] == 0
        assert result.telemetry["worker_result_ipc_bytes"] > 0
        assert result.artifact_uri
        assert service.artifact_store.get(result.artifact_uri)
        assert result.telemetry["extraction_rows"] == 40
    finally:
        service.job_runner.close()
        if result:
            service.artifact_store.cleanup_upload(result.artifact_uri)
            await service.ephemeral_repository.remove(
                result.run_id, MLSecurityContext("root", "", database=database).scope_key
            )
        load_nova_app_config.cache_clear()
        async with root.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
        root.close()
