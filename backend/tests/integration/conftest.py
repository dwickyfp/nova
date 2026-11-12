"""Engine tests use an owned PG control plane alongside the governed data engine."""

import asyncio
import json
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest_asyncio
from psycopg.conninfo import conninfo_to_dict

from app.core.metadata_db import MetadataDatabase
from app.core.metadata_migrations import migrate
from app.core.metadata_runtime import install_metadata_db, metadata_db, uninstall_metadata_db
from app.core.metadata_schema import load_migrations
from tests.integration.test_metadata_bootstrap import disposable_environment


@pytest_asyncio.fixture(autouse=True)
async def engine_metadata(request, monkeypatch):
    """No reseed or cleanup of the analytical stack; the PG fixture owns its schema."""
    if request.node.get_closest_marker("engine") is None:
        yield None
        return
    async with disposable_environment() as environment:
        await environment.bootstrap()
        await migrate(environment.role_config("nova_meta_migrator"), app_version="e" * 40)
        if "app" in request.fixturenames or "proxy_server" in request.fixturenames:
            # The seeded engine fixture's governed stage points at its synthetic
            # MinIO object. Registration belongs to PG after cutover.
            async with environment.importer() as connection:
                await connection.execute(
                    "INSERT INTO nova_meta.config_stages "
                    "(id,name,database_name,schema_name,storage_connection,base_prefix,"
                    "created_by,namespace_kind) VALUES "
                    "('stage-products','products','NOVA_ANALYTICS','public','production',"
                    "'NOVA_ANALYTICS/public/products','root','LEGACY')"
                )
        epoch = str(uuid4())
        await environment.admin.execute(
            "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false",
            (epoch,),
        )
        parameters = conninfo_to_dict(environment.config.dsn)
        for key in ("host", "port", "sslrootcert"):
            monkeypatch.setenv("NOVA_METADATA_" + key.upper(), parameters[key])
        monkeypatch.setenv("NOVA_METADATA_SCHEMA_VERSION", str(len(load_migrations())))
        monkeypatch.setenv("NOVA_METADATA_EPOCH", epoch)
        monkeypatch.delenv("NOVA_METADATA_PASSWORDS_FILE", raising=False)
        monkeypatch.setenv(
            "NOVA_METADATA_PASSWORDS",
            json.dumps(
                {
                    "nova_meta_web": environment.credentials.passwords[
                        "nova_meta_web"
                    ].get_secret_value(),
                }
            ),
        )
        if "app" in request.fixturenames:
            # Exercise the actual lifespan's role login and pool installation.
            from app.core.config import settings

            monkeypatch.setattr(settings, "NOVA_PROCESS_ROLE", "web")
            yield environment
            return
        if "proxy_server" in request.fixturenames:
            # Its synchronous client runs against a separate event-loop thread.
            # The proxy fixture opens and closes its pool on that owning loop.
            yield environment
            return
        database = MetadataDatabase(environment.role_config("nova_meta_web"))
        await database.open(schema_version=len(load_migrations()), metadata_epoch=epoch)
        install_metadata_db(database)
        try:
            yield environment
        finally:
            uninstall_metadata_db(database)
            await database.close()


@pytest_asyncio.fixture
async def run_workload(engine_metadata, tmp_path):
    """A real worker process with its own PG login, alongside the API process."""
    from app.core.config import settings

    @asynccontextmanager
    async def start(role):
        worker_env = dict(os.environ)
        for name in type(settings).model_fields:
            value = getattr(settings, name)
            if value is not None:
                worker_env[name] = (
                    json.dumps(value) if isinstance(value, (bool, dict, list)) else str(value)
                )
        worker_env["NOVA_METADATA_PASSWORDS"] = json.dumps(
            {
                "nova_meta_" + role: engine_metadata.credentials.passwords[
                    "nova_meta_" + role
                ].get_secret_value(),
            }
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            metrics_port = listener.getsockname()[1]
        worker_env["NOVA_METRICS_HOST"] = "127.0.0.1"
        worker_env["NOVA_METRICS_INGESTION_WORKER_PORT"] = str(metrics_port)
        log = tmp_path / (role + ".log")
        log.touch(mode=0o600)
        with log.open("wb") as output:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "app.worker",
                "--role",
                role,
                cwd=Path(__file__).resolve().parents[2],
                env=worker_env,
                stdout=output,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                yield process
                assert process.returncode is None, "Worker exited before the scenario completed"
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=45)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                        raise AssertionError(
                            "Worker did not shut down within its test deadline"
                        ) from None

    yield start


@pytest_asyncio.fixture
async def enable_news(engine_metadata):
    from app.common.news_entitlement import set_news_enabled
    from app.core.metadata_scope import acting_as
    from app.modules.access_control.security_context import SecurityContext
    from tests.integration.test_metadata_workspaces import session

    operator = session("fixture-operator", active="ACCOUNTADMIN")

    async def enable(username):
        async with acting_as(SecurityContext.from_session(operator)):
            await set_news_enabled(username, enabled=True, actor=operator)

    return enable


@pytest_asyncio.fixture
async def metadata_inspect(engine_metadata):
    """Read assertions through the disposable fixture's admin, never an app grant."""

    async def query(statement, parameters=()):
        cursor = await engine_metadata.admin.execute(statement, parameters or None)
        return [tuple(row.values()) for row in await cursor.fetchall()]

    return query


@pytest_asyncio.fixture
async def engine_workload(engine_metadata):
    """Exercise sequential worker calls with their real, separately granted login."""

    @asynccontextmanager
    async def role(workload):
        current = metadata_db()
        cursor = await engine_metadata.admin.execute(
            "SELECT metadata_epoch FROM nova_meta.deployment_state WHERE singleton"
        )
        epoch = str((await cursor.fetchone())["metadata_epoch"])
        worker = MetadataDatabase(engine_metadata.role_config("nova_meta_" + workload))
        await worker.open(schema_version=len(load_migrations()), metadata_epoch=epoch)
        uninstall_metadata_db(current)
        install_metadata_db(worker)
        try:
            yield
        finally:
            uninstall_metadata_db(worker)
            install_metadata_db(current)
            await worker.close()

    yield role


@pytest_asyncio.fixture
async def task_definitions(engine_workload):
    """Definition commands use the web login before a task worker consumes them."""
    from functools import partial
    from types import SimpleNamespace

    from app.modules.task_orchestration.repository import task_orchestration_repository

    async def command(method, *args, **kwargs):
        async with engine_workload("web"):
            return await getattr(task_orchestration_repository, method)(*args, **kwargs)

    return SimpleNamespace(**{
        method: partial(command, method)
        for method in ("create_task", "create_edge", "delete_task", "delete_edge")
    })
