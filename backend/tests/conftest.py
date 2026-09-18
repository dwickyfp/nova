"""Test fixtures for integration tests — real engines via Docker Compose."""

import asyncio
import subprocess
import time
from contextlib import suppress
from pathlib import Path

import asyncmy
import boto3
import pytest
import redis.asyncio as aioredis

#: Repository's backend/ directory — the compose file and uv project live here.
#: Resolved from this file so the fixtures work regardless of the directory
#: pytest was launched from (CI runs `pytest tests/integration` from backend/).
BACKEND_DIR = Path(__file__).resolve().parent.parent
COMPOSE_FILE = "docker-compose.test.yml"


def _compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        check=True,
        cwd=BACKEND_DIR,
    )


@pytest.fixture(scope="session")
def docker_services():
    """Spin up all test infrastructure once per test session."""
    _compose("up", "-d", "--wait")
    time.sleep(10)
    yield
    _compose("down", "-v")


@pytest.fixture(scope="session")
async def sr_root(docker_services):
    """Root connection to StarRocks. Creates test user + roles.

    The connection is established by a retry loop, but ``yield`` sits *outside*
    it. With the yield inside the ``try`` a failure while finalizing the fixture
    (``conn.close()`` on an engine already torn down) was caught by the retry
    handler, which slept and resumed the loop — advancing the generator to a
    *second* yield. pytest-asyncio reports that as "Async generator fixture
    didn't stop", turning an otherwise green suite red at session teardown.
    """
    conn = None
    for i in range(60):
        try:
            conn = await asyncmy.connect(
                host="127.0.0.1", port=29030, user="root", password=""
            )
        except Exception:
            if i == 59:
                raise
            await asyncio.sleep(1)
            continue
        break

    async with conn.cursor() as cur:
        await cur.execute(
            "CREATE USER IF NOT EXISTS 'nova_admin' IDENTIFIED BY 'nova'"
        )
        await cur.execute("GRANT ALL ON *.* TO 'nova_admin' WITH GRANT OPTION")
        await cur.execute(
            "CREATE USER IF NOT EXISTS 'testanalyst' IDENTIFIED BY 'testpass'"
        )
        await cur.execute("CREATE ROLE IF NOT EXISTS 'test_analyst'")
        await cur.execute("GRANT 'test_analyst' TO 'testanalyst'")
        await cur.execute("GRANT SELECT ON *.* TO ROLE 'test_analyst'")
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def minio_client(docker_services):
    """MinIO S3 client with test bucket pre-created."""
    time.sleep(5)
    client = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:29000",
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
    )
    with suppress(Exception):
        client.create_bucket(Bucket="test-stage")
    return client


@pytest.fixture(scope="session")
async def redis_client(docker_services):
    """Async Redis client for session store tests."""
    client = aioredis.from_url("redis://127.0.0.1:26379/0", decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture(scope="function")
async def app(sr_root, minio_client, redis_client):
    """FastAPI app with test config overrides."""
    import app.core.config as cfg

    cfg.settings.STARROCKS_HOST = "127.0.0.1"
    cfg.settings.STARROCKS_FE_MYSQL_PORT = 29030
    cfg.settings.STARROCKS_ROOT_USER = "root"
    cfg.settings.STARROCKS_ROOT_PASSWORD = ""
    cfg.settings.REDIS_URL = "redis://127.0.0.1:26379/0"
    cfg.settings.S3_ENDPOINT = "http://127.0.0.1:29000"
    cfg.settings.SECRET_KEY = "test-secret-key-for-testing-only-32chars!"
    cfg.settings.SESSION_TTL_SECONDS = 300

    from app.main import create_app

    application = create_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture(scope="function")
async def client(app):
    """Async HTTP test client."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(scope="function")
async def admin_token(client):
    """Login as nova_admin and return JWT token."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "nova_admin", "password": "nova"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("AUTHENTICATED", "SETUP_REQUIRED")
    return data["access_token"]


@pytest.fixture(scope="function")
async def analyst_token(client, sr_root):
    """Login as testanalyst and return JWT token."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "testanalyst", "password": "testpass"},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]
