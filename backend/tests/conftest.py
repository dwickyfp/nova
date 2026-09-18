"""Test fixtures for integration tests — real engines via Docker Compose.

Bringing the stack up is best-effort: a developer without Docker (or with a
port already taken by another checkout's stack) must see the engine-marked
tests **skip**, not error. ``docker_services`` therefore records why a stack is
unavailable instead of raising, and every fixture that needs it skips on that
reason. CI is unaffected — it starts the stack itself and the L3 job refuses an
all-skipped run, so a skip here can never pass for green there.
"""

import asyncio
import os
import shutil
import socket
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
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

#: Compose file keys, NOT service keys: the values published on the host. They
#: keep the stock numbers unless a run overrides them (see :func:`engine_host_ports`).
PORT_ENV = {
    "starrocks-fe": "NOVA_TEST_FE_MYSQL_PORT",
    "starrocks-fe-http": "NOVA_TEST_FE_HTTP_PORT",
    "minio": "NOVA_TEST_MINIO_PORT",
    "redis": "NOVA_TEST_REDIS_PORT",
}
PORT_DEFAULTS = {
    "starrocks-fe": 29030,
    "starrocks-fe-http": 28030,
    "minio": 29000,
    "redis": 26379,
}


def engine_host_ports() -> dict[str, int]:
    """The host ports this run will publish, honouring the overrides.

    The app fixtures below must resolve the *same* ports the compose file
    publishes, or the suite would skip against a stack it just started.
    """
    return {
        key: int(os.getenv(env, str(default)))
        for key, (env, default) in (
            (key, (PORT_ENV[key], PORT_DEFAULTS[key])) for key in PORT_DEFAULTS
        )
    }


@dataclass(frozen=True)
class StackStatus:
    """Why ``docker_services`` could or could not provide the test stack."""

    reason: str | None = None

    @property
    def unavailable(self) -> bool:
        return self.reason is not None


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _busy_ports() -> list[int]:
    """Ports something already answers on — a *preflight* concern only.

    Before ``up`` a listening port means another checkout's stack holds it and
    compose would fail to bind. After ``up`` the same condition means the stack
    started correctly, so this must never be used as a post-up health check;
    use :func:`_unreachable_ports` there.
    """
    return [port for port in engine_host_ports().values() if _port_in_use(port)]


def _unreachable_ports() -> list[int]:
    """Published ports that refuse a connection — an *post-up* health check.

    A successfully started stack is exactly the case where its published ports
    are bound and listening, i.e. ``connect_ex(...) == 0``. The failure case is
    the opposite: a port with nothing behind it, so the connect is refused
    ("reachability" here means a completed TCP connection, not a busy port).
    """
    return [port for port in engine_host_ports().values() if not _port_in_use(port)]


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        check=check,
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )


def _preflight_failure() -> str | None:
    """The reason no stack can be brought up, or ``None`` if it can.

    Checked *before* running compose so the common cases produce a precise skip
    reason: no Docker binary, a daemon that is not running, or the published
    ports already held by another checkout's stack (compose would otherwise
    fail with "Bind for 0.0.0.0:29030 failed: port is already allocated").
    """
    if shutil.which("docker") is None:
        return "docker is not installed"

    try:
        daemon = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"docker daemon is not reachable ({exc.__class__.__name__})"
    if daemon.returncode != 0:
        return "docker daemon is not running"

    busy = _busy_ports()
    if busy:
        ports = ", ".join(str(port) for port in busy)
        return (
            f"test ports {ports} already in use (another Nova stack?) — "
            f"set {PORT_ENV['starrocks-fe']} / {PORT_ENV['minio']} / "
            f"{PORT_ENV['redis']} (and the matching *_PORT env) to free ports"
        )
    return None


@pytest.fixture(scope="session")
def docker_services() -> StackStatus:
    """Provide the test stack once per session, or record why it is absent.

    Never raises: a missing stack is a skip, not a failure. On success the
    compose project is torn down at session end.
    """
    reason = _preflight_failure()
    if reason is not None:
        yield StackStatus(reason=reason)
        return

    try:
        _compose("up", "-d", "--wait")
    except (OSError, subprocess.SubprocessError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        yield StackStatus(reason=f"docker compose up failed: {detail.strip()[:300]}")
        return
    time.sleep(10)

    # Post-up health check: a stack that came up correctly has every published
    # port answering, so the failure case here is a port that *refuses* a
    # connection — NOT a port that is in use. (Checking "in use" after `up`
    # would flag a perfectly healthy stack, which is the inversion this
    # replaced: the ports were bound by the stack we just started.)
    unreachable = _unreachable_ports()
    if unreachable:
        ports = ", ".join(str(port) for port in unreachable)
        _compose("down", "-v", check=False)
        yield StackStatus(reason=f"stack came up but test ports {ports} do not answer")
        return

    yield StackStatus()
    _compose("down", "-v", check=False)


def require_stack(docker_services: StackStatus) -> None:
    """Skip the calling test (or fixture) when the stack is unavailable."""
    if docker_services.unavailable:
        pytest.skip(f"real engine stack unavailable: {docker_services.reason}")


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
    require_stack(docker_services)
    port = engine_host_ports()["starrocks-fe"]

    conn = None
    for i in range(60):
        try:
            conn = await asyncmy.connect(
                host="127.0.0.1", port=port, user="root", password=""
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
    require_stack(docker_services)
    time.sleep(5)
    client = boto3.client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{engine_host_ports()['minio']}",
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
    )
    with suppress(Exception):
        client.create_bucket(Bucket="test-stage")
    return client


@pytest.fixture(scope="session")
async def redis_client(docker_services):
    """Async Redis client for session store tests."""
    require_stack(docker_services)
    client = aioredis.from_url(
        f"redis://127.0.0.1:{engine_host_ports()['redis']}/0", decode_responses=True
    )
    yield client
    await client.aclose()


@pytest.fixture(scope="function")
async def app(sr_root, minio_client, redis_client):
    """FastAPI app with test config overrides."""
    import app.core.config as cfg

    ports = engine_host_ports()

    cfg.settings.STARROCKS_HOST = "127.0.0.1"
    cfg.settings.STARROCKS_FE_MYSQL_PORT = ports["starrocks-fe"]
    cfg.settings.STARROCKS_ROOT_USER = "root"
    cfg.settings.STARROCKS_ROOT_PASSWORD = ""
    cfg.settings.REDIS_URL = f"redis://127.0.0.1:{ports['redis']}/0"
    cfg.settings.S3_ENDPOINT = f"http://127.0.0.1:{ports['minio']}"
    cfg.settings.SECRET_KEY = "test-secret-key-for-testing-only-32chars!"
    cfg.settings.FERNET_KEY = "8f3Q1sVx0m2pR7tY5uW9zB4cD6eF1gH3jK5lM7nO9pQ="
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
