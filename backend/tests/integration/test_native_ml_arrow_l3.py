"""L3 proof of the native ML worker boundary against StarRocks 4.1."""

from __future__ import annotations

import asyncio
import secrets
from uuid import uuid4

import asyncmy
import pytest
from cryptography.fernet import Fernet
from sklearn.linear_model import LinearRegression

from app.core.config import settings
from app.modules.ml_engine.artifacts.store import ObjectArtifactStore
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.registry.repository import ModelRegistryRepository, security_actor
from app.modules.ml_engine.service import MLEngineService
from app.modules.ml_engine.spec import (
    ExecutionBudget,
    MLExecutionSpec,
    MLSecurityContext,
    MLTask,
)
from tests.conftest import (
    engine_host_ports,
    require_stack,
    root_password,
    s3_credentials,
)

pytestmark = pytest.mark.engine


@pytest.fixture(autouse=True)
async def ml_workload(engine_workload):
    async with engine_workload("ml"):
        yield


@pytest.mark.asyncio
async def test_concurrent_version_reservation_promotes_only_ready(
    docker_services, metadata_inspect
):
    require_stack(docker_services)
    repository = ModelRegistryRepository()
    model_name = f"concurrent_{uuid4().hex[:12]}"
    spec = MLExecutionSpec(
        task=MLTask.REGRESSION,
        input_sql="SELECT x, target FROM features",
        security=MLSecurityContext("ml-fixture", "", database="NOVA_EXAMPLE", role="ANALYST"),
        persist=True,
        model_name=model_name,
        target_column="target",
        budget=ExecutionBudget(20, 1000, 10_000_000),
    )
    reservations = await asyncio.gather(
        *(repository.reserve_version(spec, feature_columns=["x"]) for _ in range(10))
    )
    model_ids = {model_id for model_id, _, _ in reservations}
    versions = sorted(version for _, version, _ in reservations)
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
                reservation_token=token,
                spec=spec,
                output=output,
                artifact_uri=f"nova-artifact://test/model/v{version}",
                artifact_sha256="0" * 64,
                artifact_size=1,
                training_duration_ms=1,
            )
            for _, version, token in reversed(reservations)
        )
    )
    _, failed_version, _ = await repository.reserve_version(spec, feature_columns=["x"])
    assert failed_version == 11
    assert (
        await repository.abort_version(
            model_id, failed_version, actor=security_actor(spec.security)
        )
        is True
    )

    rows = await metadata_inspect(
        "SELECT current_version FROM nova_meta.ml_models WHERE model_id=%s",
        (model_id,),
    )
    assert rows == [(10,)]
    rows = await metadata_inspect(
        "SELECT version,status FROM nova_meta.ml_model_versions WHERE model_id=%s ORDER BY version",
        (model_id,),
    )
    assert [row[0] for row in rows] == list(range(1, 12))
    assert [row[1] for row in rows[:10]] == ["READY"] * 10
    assert rows[-1][1] == "FAILED"


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
    monkeypatch.setenv("STARROCKS_FE_MYSQL_PORT", str(ports["starrocks-fe"]))
    from app.core.config import load_nova_app_config

    storage_settings = {
        "NOVA_CONFIG_PATH": str(tmp_path / "missing-config.yaml"),
        "S3_ENDPOINT": f"http://127.0.0.1:{ports['minio']}",
        "S3_BUCKET": "test-stage",
        "S3_ACCESS_KEY": s3_credentials()[0],
        "S3_SECRET_KEY": s3_credentials()[1],
    }
    for name, value in storage_settings.items():
        monkeypatch.setenv(name, value)
        monkeypatch.setattr(settings, name, value)
    load_nova_app_config.cache_clear()
    monkeypatch.setattr(settings, "FERNET_KEY", key)
    monkeypatch.setattr(settings, "STARROCKS_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "STARROCKS_FE_MYSQL_PORT", ports["starrocks-fe"])
    from app.core import security as security_module

    monkeypatch.setattr(security_module, "_fernet", None)

    root = await asyncmy.connect(
        host="127.0.0.1",
        port=ports["starrocks-fe"],
        user="root",
        password=root_password(),
        autocommit=True,
    )
    database = f"nova_ml_worker_{uuid4().hex[:10]}"
    username, role = f"{database}_user", f"{database}_role"
    password = secrets.token_urlsafe(24)
    security = MLSecurityContext(username, password, database=database, role=role)
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
        await cursor.execute(f"CREATE ROLE `{role}`")
        await cursor.execute(f"CREATE USER '{username}' IDENTIFIED BY %s", (password,))
        await cursor.execute(f"GRANT SELECT ON TABLE `{database}`.features TO ROLE `{role}`")
        await cursor.execute(f"GRANT `{role}` TO USER '{username}'")
        await cursor.execute(f"SET DEFAULT ROLE `{role}` TO '{username}'")

    class Repository(ModelRegistryRepository):
        async def record_run(self, **kwargs):
            del kwargs

    class Service(MLEngineService):
        async def _prepare_user_sql(self, sql, security):
            del security
            return sql

    service = Service(artifact_store=ObjectArtifactStore(), repository=Repository())
    result = None
    try:
        result = await service.execute(
            MLExecutionSpec(
                task=MLTask.REGRESSION,
                input_sql="SELECT x, target FROM features ORDER BY x",
                security=security,
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
                result.run_id, security.scope_key, actor=security_actor(security)
            )
        load_nova_app_config.cache_clear()
        async with root.cursor() as cursor:
            await cursor.execute(f"DROP USER IF EXISTS '{username}'")
            await cursor.execute(f"DROP ROLE IF EXISTS `{role}`")
            await cursor.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
        root.close()
