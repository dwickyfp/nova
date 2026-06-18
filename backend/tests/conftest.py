"""Test fixtures for integration tests — real engines via Docker Compose."""

import asyncio
import subprocess
import time

import asyncmy
import boto3
import pytest
import redis.asyncio as aioredis


@pytest.fixture(scope="session")
def event_loop():
    """Override default event loop to be session-scoped."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def docker_services():
    """Spin up all test infrastructure once per test session."""
    subprocess.run(
        ["docker-compose", "-f", "docker-compose.test.yml", "up", "-d", "--wait"],
        check=True,
        cwd="backend",
    )
    time.sleep(10)
    yield
    subprocess.run(
        ["docker-compose", "-f", "docker-compose.test.yml", "down", "-v"],
        check=True,
        cwd="backend",
    )


@pytest.fixture(scope="session")
async def sr_root(docker_services):
    """Root connection to StarRocks. Creates test user + roles."""
    for i in range(60):
        try:
            conn = await asyncmy.connect(
                host="127.0.0.1", port=29030, user="root", password=""
            )
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
            await conn.close()
            return
        except Exception:
            if i == 59:
                raise
            await asyncio.sleep(1)


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
    try:
        client.create_bucket(Bucket="test-stage")
    except Exception:
        pass
    return client


@pytest.fixture(scope="session")
async def redis_client(docker_services):
    """Async Redis client for session store tests."""
    client = aioredis.from_url("redis://127.0.0.1:26379/0", decode_responses=True)
    yield client
    await client.close()


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
