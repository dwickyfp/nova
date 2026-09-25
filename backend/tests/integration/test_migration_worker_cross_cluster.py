"""Run a two-database migration through a separate nova-worker process.

This test is opt-in because it needs two live StarRocks clusters. Point the
normal integration fixtures at the *target* stack and set
``NOVA_MIGRATION_SOURCE_PORT`` to the other cluster's MySQL port. The source
cluster must allow passwordless root access from the test host.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import sys
from uuid import uuid4

import asyncmy
import pytest
import yaml

from app.core.config import load_nova_app_config, settings
from app.modules.migration.jobs import migration_job_repo
from tests.conftest import BACKEND_DIR, engine_host_ports

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        not os.getenv("NOVA_MIGRATION_SOURCE_PORT"),
        reason="set NOVA_MIGRATION_SOURCE_PORT to opt in to cross-cluster migration",
    ),
]


def _name(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def _sql(conn: asyncmy.Connection, statement: str, args: tuple = ()) -> list[tuple]:
    async with conn.cursor() as cursor:
        await cursor.execute(statement, args)
        if cursor.description:
            return list(await cursor.fetchall())
    return []


async def _stop_worker(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _process_snapshot(conn: asyncmy.Connection) -> list[dict[str, object]]:
    """Capture connection state without the SQL text, which can contain secrets."""
    async with conn.cursor() as cursor:
        await cursor.execute("SHOW FULL PROCESSLIST")
        columns = [column[0].lower() for column in cursor.description]
        rows = await cursor.fetchall()
    return [
        {
            key: row[columns.index(key)]
            for key in ("user", "command", "time", "state")
            if key in columns
        }
        for row in rows
    ]


@pytest.fixture
def cross_cluster_storage(tmp_path, monkeypatch):
    """Give both engines a single host-reachable transfer bucket."""
    bucket = f"nova-migration-{uuid4().hex[:16]}"
    minio_port = engine_host_ports()["minio"]
    config_path = tmp_path / "migration-test.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "storage": {
                    "connections": {
                        "production": {
                            "type": "minio",
                            "endpoint": f"http://host.docker.internal:{minio_port}",
                            "bucket": bucket,
                            "access_key": "${MINIO_ACCESS_KEY}",
                            "secret_key": "${MINIO_SECRET_KEY}",
                            "path_style": True,
                        }
                    }
                },
                "workspace": {"storage_connection": "production"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "NOVA_CONFIG_PATH", str(config_path))
    load_nova_app_config.cache_clear()
    yield config_path, bucket
    load_nova_app_config.cache_clear()


@pytest.mark.asyncio
async def test_batch_executes_in_standalone_worker_across_two_clusters(
    cross_cluster_storage, client, minio_client, monkeypatch
):
    source_port = int(os.environ["NOVA_MIGRATION_SOURCE_PORT"])
    target_ports = engine_host_ports()
    assert source_port != target_ports["starrocks-fe"], "source and target must differ"

    config_path, bucket = cross_cluster_storage
    source_host = os.getenv("NOVA_MIGRATION_SOURCE_HOST", "127.0.0.1")
    source_conn = await asyncmy.connect(
        host=source_host, port=source_port, user="root", password="", autocommit=True
    )
    target_conn = await asyncmy.connect(
        host="127.0.0.1",
        port=target_ports["starrocks-fe"],
        user="root",
        password="",
        autocommit=True,
    )
    databases = [_name("nova_cross_a"), _name("nova_cross_b")]
    source_name = _name("nova_cross_source")
    operator = _name("nova_cross_op")
    worker_user = _name("nova_cross_worker")
    operator_password = uuid4().hex
    worker_password = uuid4().hex
    job_id: str | None = None
    process: asyncio.subprocess.Process | None = None
    bucket_created = False

    try:
        minio_client.create_bucket(Bucket=bucket)
        bucket_created = True
        for index, database in enumerate(databases, start=1):
            await _sql(source_conn, f"CREATE DATABASE `{database}`")
            await _sql(
                source_conn,
                f"CREATE TABLE `{database}`.`events` (id INT, amount INT) "
                "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
                'PROPERTIES("replication_num"="1")',
            )
            await _sql(
                source_conn,
                f"INSERT INTO `{database}`.`events` VALUES ({index}, {index * 10})",
            )
            await _sql(
                source_conn,
                f"CREATE VIEW `{database}`.`v_events` AS "
                f"SELECT id, amount FROM `{database}`.`events`",
            )

        await _sql(
            target_conn,
            f"CREATE USER '{operator}' IDENTIFIED BY %s",
            (operator_password,),
        )
        await _sql(target_conn, f"GRANT ALL ON *.* TO '{operator}' WITH GRANT OPTION")
        await _sql(target_conn, f"GRANT CREATE DATABASE ON CATALOG default_catalog TO '{operator}'")
        await _sql(
            target_conn,
            "GRANT CREATE TABLE, CREATE VIEW, CREATE MATERIALIZED VIEW "
            f"ON ALL DATABASES TO '{operator}'",
        )
        await _sql(
            target_conn,
            f"CREATE USER '{worker_user}' IDENTIFIED BY %s",
            (worker_password,),
        )

        login = await client.post(
            "/api/v1/auth/login",
            json={"username": operator, "password": operator_password},
        )
        assert login.status_code == 200, f"operator login returned {login.status_code}"
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        # If a read or write runs inside the ASGI process, the test fails even
        # when both clusters happen to be available to the backend host.
        from app.modules.migration.service import migration_service

        async def backend_must_not_migrate(*args, **kwargs):
            raise AssertionError("migration work ran in the API process")

        monkeypatch.setattr(migration_service, "databases", backend_must_not_migrate)
        monkeypatch.setattr(migration_service, "execute", backend_must_not_migrate)
        monkeypatch.setattr(migration_service, "test_source_connection", backend_must_not_migrate)
        monkeypatch.setattr(settings, "MIGRATION_EXECUTE_ENABLED", True)

        worker_env = os.environ.copy()
        worker_env.update(
            {
                "STARROCKS_HOST": "127.0.0.1",
                "STARROCKS_FE_MYSQL_PORT": str(target_ports["starrocks-fe"]),
                "STARROCKS_ROOT_USER": "root",
                "STARROCKS_ROOT_PASSWORD": "",
                "REDIS_URL": f"redis://127.0.0.1:{target_ports['redis']}/0",
                "NOVA_CONFIG_PATH": str(config_path),
                "MINIO_ACCESS_KEY": "minioadmin",
                "MINIO_SECRET_KEY": "minioadmin",
                "S3_ENDPOINT": f"http://127.0.0.1:{target_ports['minio']}",
                "SECRET_KEY": settings.SECRET_KEY,
                "FERNET_KEY": settings.FERNET_KEY,
                "SESSION_TTL_SECONDS": str(settings.SESSION_TTL_SECONDS),
                "WORKER_IMPERSONATION_USER": worker_user,
                "WORKER_IMPERSONATION_PASSWORD": worker_password,
                "RANGER_ENABLED": "false",
                "MIGRATION_EXECUTE_ENABLED": "true",
                "NOVA_METRICS_HOST": "127.0.0.1",
                "NOVA_METRICS_WORKER_PORT": str(_free_port()),
            }
        )
        gate_probe = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from app.core.config import settings; print(int(settings.MIGRATION_EXECUTE_ENABLED))",
            cwd=BACKEND_DIR,
            env=worker_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        gate_output, _ = await gate_probe.communicate()
        assert gate_probe.returncode == 0 and gate_output.strip() == b"1"
        worker_log_path = config_path.parent / "worker.log"
        with worker_log_path.open("wb") as worker_log:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "app.worker",
                cwd=BACKEND_DIR,
                env=worker_env,
                stdout=worker_log,
                stderr=asyncio.subprocess.STDOUT,
            )

        probe = await client.post(
            "/api/v1/migration/sources/test",
            json={
                "source": source_name,
                "host": source_host,
                "port": source_port,
                "username": "root",
            },
        )
        assert probe.status_code == 200 and probe.json() == {"connected": True}
        assert await _sql(
            target_conn,
            "SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES WHERE name = %s",
            (source_name,),
        ) == [(0,)]

        registration = await client.post(
            "/api/v1/migration/sources",
            json={
                "name": source_name,
                "host": source_host,
                "port": source_port,
                "username": "root",
            },
        )
        assert registration.status_code == 201, (
            f"source registration returned {registration.status_code}"
        )

        discovery = await client.post("/api/v1/migration/databases", json={"source": source_name})
        assert discovery.status_code == 200, (
            f"worker discovery returned {discovery.status_code}; process={process.returncode}"
        )
        assert set(databases).issubset(set(discovery.json()["databases"]))

        preflight = await client.post(
            "/api/v1/migration/preflight",
            json={
                "source": source_name,
                "database": databases[0],
                "create_database": True,
                "include_data": True,
            },
        )
        assert preflight.status_code == 200, f"preflight returned {preflight.status_code}"
        assert preflight.json()["ok"], {
            "missing": preflight.json()["missing"],
            "storage_reason": preflight.json()["storage_reason"],
        }

        accepted = await client.post(
            "/api/v1/migration/execute-batch",
            json={
                "source": source_name,
                "databases": databases,
                "create_database": True,
                "include_data": True,
                "acknowledge_omissions": True,
                "confirmation": "MIGRATE 2 DATABASES",
            },
        )
        assert accepted.status_code == 202, f"batch returned {accepted.status_code}"
        assert accepted.json()["status"] == "queued"
        assert accepted.json()["databases"] == databases
        job_id = accepted.json()["job_id"]

        deadline = asyncio.get_running_loop().time() + 180
        while True:
            status_response = await client.get(f"/api/v1/migration/jobs/{job_id}")
            assert status_response.status_code == 200
            status = status_response.json()
            if status["status"] in {"succeeded", "partial", "failed", "interrupted"}:
                break
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail(
                    str(
                        {
                            "job_status": status["status"],
                            "current_database": status["current_database"],
                            "database_results": [
                                (item["database"], item["status"], item["error"])
                                for item in status["results"]
                            ],
                            "source_processes": await _process_snapshot(source_conn),
                            "target_processes": await _process_snapshot(target_conn),
                            "worker_log": str(worker_log_path),
                        }
                    )
                )
            await asyncio.sleep(0.5)

        assert status["status"] == "succeeded", {
            "job_status": status["status"],
            "database_results": [
                (item["database"], item["status"], item["error"]) for item in status["results"]
            ],
        }
        assert {item["database"] for item in status["results"]} == set(databases)
        assert all(item["status"] == "succeeded" for item in status["results"])
        assert all(item["rows_moved"] == 1 for item in status["results"])
        assert all(item["data"] and item["data"][0]["verified"] for item in status["results"])

        for index, database in enumerate(databases, start=1):
            assert await _sql(target_conn, f"SELECT id, amount FROM `{database}`.`events`") == [
                (index, index * 10)
            ]
            assert await _sql(target_conn, f"SELECT id, amount FROM `{database}`.`v_events`") == [
                (index, index * 10)
            ]
    finally:
        if process is not None:
            await _stop_worker(process)
        if job_id is not None:
            with contextlib.suppress(Exception):
                await migration_job_repo.delete(job_id)
        with contextlib.suppress(Exception):
            await _sql(
                target_conn,
                "DELETE FROM NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES WHERE name = %s",
                (source_name,),
            )
        for database in databases:
            with contextlib.suppress(Exception):
                await _sql(target_conn, f"DROP DATABASE IF EXISTS `{database}` FORCE")
            with contextlib.suppress(Exception):
                await _sql(source_conn, f"DROP DATABASE IF EXISTS `{database}` FORCE")
        for username in (operator, worker_user):
            with contextlib.suppress(Exception):
                await _sql(target_conn, f"DROP USER IF EXISTS '{username}'")
        if bucket_created:
            with contextlib.suppress(Exception):
                objects = minio_client.list_objects_v2(Bucket=bucket).get("Contents", [])
                for obj in objects:
                    minio_client.delete_object(Bucket=bucket, Key=obj["Key"])
                minio_client.delete_bucket(Bucket=bucket)
        source_conn.close()
        target_conn.close()
