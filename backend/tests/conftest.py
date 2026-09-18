"""Test fixtures for integration tests — real engines via Docker Compose.

The fixture below is the *only* place the Docker stack is started. It is
deliberately forgiving: a missing Docker daemon, a failed `compose up`, or a
published port already held by another checkout's stack all make the engine
suites **skip**, with the reason attached, rather than error. A missing local
prerequisite is not a regression (NOVA-131).

Two roots caused the old fail-instead-of-skip behaviour:

1. ``docker compose up`` was called with ``check=True`` and no guard, so a
   failure propagated out of a session-scoped fixture as an *error* for every
   dependent test.
2. The published ports (``28030/29030/29000/26379``) are fixed, so two Nova
   checkouts running at once collide with ``Bind for 0.0.0.0:28030 failed: port
   is already allocated``.

The fix preflights the ports and treats that as a skip condition. This is the
"detect a busy port and skip with a clear reason" option, chosen over remapping
every checkout on purpose: the engine suites only run for real in CI, where a
dedicated runner holds the ports uncontended, so auto-remapping would add moving
parts without changing a single CI outcome. A local collision is a *skip*, and
the reason says which port and which stack holds it.

The ports the stack publishes are the ones named in ``docker-compose.test.yml``
(``28030/29030/29000/26379``); they are fixed, so the preflight checks exactly
the ports the compose file will try to bind.
"""

import asyncio
import os
import socket
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

#: Published host ports, matching ``docker-compose.test.yml``. Kept in one place
#: so the preflight and the fixtures cannot drift from the compose file.
STARROCKS_PORT = 29030
STARROCKS_HTTP_PORT = 28030
MINIO_PORT = 29000
REDIS_PORT = 26379

#: Ports the stack must own for the engine suites to mean anything.
_STACK_PORTS = {
    "StarRocks MySQL": STARROCKS_PORT,
    "StarRocks HTTP": STARROCKS_HTTP_PORT,
    "MinIO S3": MINIO_PORT,
    "Redis": REDIS_PORT,
}

#: When set, an unavailable stack is a hard failure instead of a skip. CI does
#: not need it — it provisions its own stack and its all-skipped guard already
#: rejects a vacuous run — but a developer who wants a local red-to-prove can
#: opt in. Skipping is the default because that is the case a missing
#: prerequisite deserves.
_REQUIRE_ENGINE = os.getenv("NOVA_REQUIRE_ENGINE", "").strip() not in ("", "0", "false")


def _compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        check=True,
        cwd=BACKEND_DIR,
    )


def _port_is_busy(port: int) -> bool:
    """True when something already accepts connections on ``127.0.0.1:port``.

    A plain connect probe, not a bind: the stack binds ``0.0.0.0``, but the
    suites reach it on loopback, so a listener there is enough to predict the
    ``Bind ... port is already allocated`` failure and skip before Docker has a
    chance to partially start containers.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _starrocks_answers() -> bool:
    """True when this compose project already has a healthy engine running.

    Distinguishes "our stack is already up" from "some other process holds the
    port". CI starts the stack in an earlier workflow step and then runs
    pytest, so a bound port there is the *expected* state and must not be read
    as a collision. ``docker compose ps`` scoped to this project answers it
    without a database client: a healthy running ``starrocks-fe`` means the
    ports belong to us.
    """
    try:
        probe = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "ps", "--status", "running"],
            capture_output=True,
            text=True,
            cwd=BACKEND_DIR,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if probe.returncode != 0:
        return False
    return "starrocks-fe" in probe.stdout


def _busy_stack_ports() -> list[str]:
    return [
        f"{name} (:{port})"
        for name, port in _STACK_PORTS.items()
        if _port_is_busy(port)
    ]


def _skip_or_fail(reason: str) -> None:
    if _REQUIRE_ENGINE:
        pytest.fail(f"{reason} (NOVA_REQUIRE_ENGINE is set)")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def docker_services():
    """Spin up all test infrastructure once per test session.

    Skips — never errors — when Docker is absent, ``compose up`` fails, or the
    published ports are held by a foreign stack (a concurrent checkout or a QA
    sandbox). A session-scoped skip propagates to every dependent test, so the
    engine suites report skips with the reason instead of a wall of errors.

    An already-running, reachable StarRocks on the test port is *not* a
    collision: CI starts the stack before pytest, and a developer may point at
    a live engine. It is reused as-is.
    """
    busy = _busy_stack_ports()
    if busy and not _starrocks_answers():
        _skip_or_fail(
            "Docker test stack ports already in use: "
            + ", ".join(busy)
            + ". Another Nova stack is running — stop it, or point this run at "
            "an external engine with NOVA_ORCH_SR_PORT."
        )
    if busy:
        # Ports held by a stack that answers: already up, nothing to start.
        yield
        return

    try:
        _compose("up", "-d", "--wait")
    except FileNotFoundError:
        _skip_or_fail("Docker CLI not found; skipping the engine stack")
    except subprocess.CalledProcessError as exc:
        # `compose up -d` can leave a partially-created project behind before it
        # fails. Tear that down so a skipped run does not poison a later one;
        # never touch other projects, `-f docker-compose.test.yml down` is
        # scoped to this file's project.
        with suppress(Exception):
            _compose("down", "-v")
        _skip_or_fail(
            "docker compose up failed; the engine stack is unavailable "
            f"(exit {exc.returncode}). Run it by hand to see the error: "
            "docker compose -f docker-compose.test.yml up -d --wait"
        )

    time.sleep(10)
    yield
    _compose("down", "-v")


@pytest.fixture(autouse=True)
def _engine_stack_gate(request):
    """Require the shared stack for every ``engine``-marked test.

    Several engine modules connect straight to ``127.0.0.1:29030`` instead of
    declaring ``docker_services`` as a dependency, and their old guard —
    ``if "docker_services" in request.fixturenames`` — was dead code: a fixture
    fetched with ``request.getfixturevalue`` is never listed in
    ``fixturenames``, so the guard was always false and the stack was never
    started. Those modules then ran against whatever happened to hold the port.

    This gate is **function-scoped** on purpose. A session-scoped fixture sees
    the session node, where no ``engine`` marker exists, so it would return
    early for the whole run; per-test is the only scope where the marker is
    visible. The fixture it resolves — ``docker_services`` — is still
    session-scoped, so the stack is started (or skipped) exactly once.

    Opt out by pinning an external engine — ``NOVA_ORCH_SR_PORT`` is the
    existing convention for "I already have a stack, do not start one" — so a
    developer pointing at a live engine is not overridden.
    """
    if not request.node.get_closest_marker("engine"):
        return
    if os.getenv("NOVA_ORCH_SR_PORT"):
        return
    request.getfixturevalue("docker_services")


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
                host="127.0.0.1", port=STARROCKS_PORT, user="root", password=""
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
        endpoint_url=f"http://127.0.0.1:{MINIO_PORT}",
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
    )
    with suppress(Exception):
        client.create_bucket(Bucket="test-stage")
    return client


@pytest.fixture(scope="session")
async def redis_client(docker_services):
    """Async Redis client for session store tests."""
    client = aioredis.from_url(
        f"redis://127.0.0.1:{REDIS_PORT}/0", decode_responses=True
    )
    yield client
    await client.aclose()


@pytest.fixture(scope="function")
async def app(sr_root, minio_client, redis_client):
    """FastAPI app with test config overrides."""
    import app.core.config as cfg

    cfg.settings.STARROCKS_HOST = "127.0.0.1"
    cfg.settings.STARROCKS_FE_MYSQL_PORT = STARROCKS_PORT
    cfg.settings.STARROCKS_ROOT_USER = "root"
    cfg.settings.STARROCKS_ROOT_PASSWORD = ""
    cfg.settings.REDIS_URL = f"redis://127.0.0.1:{REDIS_PORT}/0"
    cfg.settings.S3_ENDPOINT = f"http://127.0.0.1:{MINIO_PORT}"
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
