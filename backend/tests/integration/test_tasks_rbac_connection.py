"""End-to-end RBAC test for ``GET /tasks`` — the D9.8 security defect.

The defect: ``TaskService`` opened a **root** connection, so ``GET /tasks``
returned every task to every signed-in user even though the engine filters
``information_schema.tasks`` by caller privilege. This test drives the real
dependency chain —

    HTTP → get_current_user → get_user_connection → TaskService

— against a real engine, so it proves the filter is applied end to end, not just
that a connection was passed.

On the **old** code this fails loudly: the low-privilege user receives the
privileged user's task. On the **new** code it passes: the low-privilege user
sees nothing, the granted user sees the task.

Runs against a real StarRocks. By default the suite brings up
``docker-compose.test.yml``; to point at an already-running engine::

    NOVA_ORCH_SR_PORT=9030 uv run pytest tests/integration/test_tasks_rbac_connection.py

When StarRocks is unreachable the module skips rather than fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from uuid import uuid4

import asyncmy
import pytest
import pytest_asyncio

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = int(_EXPLICIT_PORT or "29030")
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")
_USE_SHARED_STACK = _EXPLICIT_PORT is None

PRIVILEGED_PASSWORD = "privpass"
LOW_PRIV_PASSWORD = "lowpass"


async def _reachable() -> bool:
    try:
        conn = await asyncio.wait_for(
            asyncmy.connect(host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD),
            timeout=5,
        )
    except Exception:
        return False
    conn.close()
    return True


async def _admin_execute(sql: str) -> None:
    conn = await asyncmy.connect(host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD)
    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
    finally:
        conn.close()


# The dev/test engine ships without init-nova.sql, so ``NOVA_SYSTEM.AUDIT_LOG``
# is absent and login (which audits) fails. This is a minimal stand-in with the
# columns ``write_audit_log`` inserts; the production table (partitioned,
# DUPLICATE KEY) lives in init-nova.sql. A Primary-Key table keeps it simple and
# satisfies the ``log_id`` key the audit writer expects to be generated.
_AUDIT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_LOG (
    log_id        BIGINT NOT NULL AUTO_INCREMENT,
    query_id      VARCHAR(36),
    event_type    VARCHAR(64) NOT NULL,
    event_time    DATETIME NOT NULL,
    user_name     VARCHAR(128),
    ip_address    VARCHAR(45),
    object_type   VARCHAR(64),
    object_name   VARCHAR(512),
    action        VARCHAR(128),
    sql_text      TEXT,
    status        VARCHAR(32),
    error_message TEXT,
    duration_ms   BIGINT,
    rows_affected BIGINT,
    client_ip     VARCHAR(45),
    session_id    VARCHAR(64),
    rewritten_sql TEXT,
    file_id       VARCHAR(64),
    database_name VARCHAR(128),
    schema_name   VARCHAR(128)
) PRIMARY KEY(log_id)
DISTRIBUTED BY HASH(log_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


async def _ensure_audit_log() -> None:
    with contextlib.suppress(Exception):
        await _admin_execute("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    await _admin_execute(_AUDIT_LOG_DDL)


@pytest_asyncio.fixture
async def rbac_users(request):
    """Create a privileged user (with a grant) and a low-privilege user.

    Yields ``(privileged_username, low_privilege_username, task_name, db_name,
    role_name)``. The privileged user owns a task in a database the low-privilege
    user has no grant on — exactly the leak ``GET /tasks`` used to expose.
    """
    if _USE_SHARED_STACK and "docker_services" in request.fixturenames:
        request.getfixturevalue("docker_services")
    if not await _reachable():
        pytest.skip("StarRocks not reachable")
    await _ensure_audit_log()

    suffix = uuid4().hex[:8]
    priv_user = f"nova_priv_{suffix}"
    low_user = f"nova_low_{suffix}"
    role_name = f"nova_priv_role_{suffix}"
    db_name = f"nova_rbac_db_{suffix}"
    task_name = f"etl_priv_{suffix}"

    await _admin_execute(
        f"CREATE USER IF NOT EXISTS '{priv_user}' IDENTIFIED BY '{PRIVILEGED_PASSWORD}'"
    )
    await _admin_execute(
        f"CREATE USER IF NOT EXISTS '{low_user}' IDENTIFIED BY '{LOW_PRIV_PASSWORD}'"
    )
    await _admin_execute(f"CREATE ROLE {role_name}")
    await _admin_execute(f"GRANT {role_name} TO '{priv_user}'")
    # Activate the role by default so the user's own session connection inherits
    # the database grant without an explicit SET ROLE.
    await _admin_execute(f"SET DEFAULT ROLE {role_name} TO '{priv_user}'")
    await _admin_execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
    await _admin_execute(f"GRANT ALL ON {db_name}.* TO ROLE {role_name}")
    await _admin_execute(
        f"CREATE TABLE IF NOT EXISTS {db_name}.t (id INT) "
        'PROPERTIES("replication_num"="1")'
    )

    # Submit on the privileged user's own connection — the fixed POST /tasks
    # path — so the engine records them as the task's CREATOR.
    priv_conn = await asyncmy.connect(
        host=SR_HOST,
        port=SR_PORT,
        user=priv_user,
        password=PRIVILEGED_PASSWORD,
        autocommit=True,
    )
    try:
        async with priv_conn.cursor() as cur:
            await cur.execute(f"SET ROLE {role_name}")
            await cur.execute(f"USE {db_name}")
            await cur.execute(f"SUBMIT TASK {task_name} AS INSERT INTO t SELECT 1")
    finally:
        priv_conn.close()

    yield priv_user, low_user, task_name, db_name, role_name

    for stmt in (
        f"DROP TASK IF EXISTS {db_name}.{task_name}",
        f"DROP USER IF EXISTS '{low_user}'",
        f"DROP USER IF EXISTS '{priv_user}'",
        f"DROP ROLE IF EXISTS {role_name}",
        f"DROP DATABASE IF EXISTS {db_name}",
    ):
        with contextlib.suppress(Exception):
            await _admin_execute(stmt)


@pytest_asyncio.fixture
async def rbac_client(rbac_users):
    """The real FastAPI app wired to the test engine + Redis, no docker helper.

    The shared ``client`` fixture would pull in ``docker_services`` (which shells
    out to ``docker-compose``); this test only needs the already-running stack,
    so it configures the same ports/Redis directly and skips otherwise.
    """
    from app.core.config import settings
    from app.core.database import db
    from app.core.redis import session_store
    from app.main import create_app

    settings.STARROCKS_HOST = SR_HOST
    settings.STARROCKS_FE_MYSQL_PORT = SR_PORT
    settings.STARROCKS_ROOT_USER = SR_USER
    settings.STARROCKS_ROOT_PASSWORD = SR_PASSWORD
    settings.REDIS_URL = os.getenv("NOVA_TEST_REDIS_URL", "redis://127.0.0.1:26379/0")
    settings.SECRET_KEY = "test-secret-key-for-testing-only-32chars!"

    await db.init_system_pool()
    application = create_app()
    async with application.router.lifespan_context(application):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    await db.close_system_pool()
    with contextlib.suppress(Exception):
        await session_store.close()


@pytest_asyncio.fixture
async def rbac_tokens(rbac_client, rbac_users):
    """Log both users in through the real auth flow; return their JWTs."""
    priv_user, low_user, _task, _db, _role = rbac_users
    priv_login = await rbac_client.post(
        "/api/v1/auth/login",
        json={"username": priv_user, "password": PRIVILEGED_PASSWORD},
    )
    low_login = await rbac_client.post(
        "/api/v1/auth/login",
        json={"username": low_user, "password": LOW_PRIV_PASSWORD},
    )
    assert priv_login.status_code == 200, priv_login.text
    assert low_login.status_code == 200, low_login.text
    return priv_login.json()["access_token"], low_login.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestGetTasksIsFilteredByCallerPrivilege:
    async def test_privileged_user_sees_the_task(
        self, rbac_client, rbac_users, rbac_tokens
    ):
        priv_token, _low_token = rbac_tokens
        _priv_user, _low_user, task_name, _db, _role = rbac_users
        resp = await rbac_client.get("/api/v1/tasks", headers=_auth(priv_token))
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["tasks"]]
        assert task_name in names

    async def test_low_privilege_user_does_not_see_the_task(
        self, rbac_client, rbac_users, rbac_tokens
    ):
        _priv_token, low_token = rbac_tokens
        _priv_user, _low_user, task_name, _db, _role = rbac_users
        resp = await rbac_client.get("/api/v1/tasks", headers=_auth(low_token))
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["tasks"]]
        assert task_name not in names, (
            "low-privilege user saw a task they have no grant on — "
            "the request ran on a root connection"
        )

    async def test_low_privilege_user_gets_404_on_detail(
        self, rbac_client, rbac_users, rbac_tokens
    ):
        _priv_token, low_token = rbac_tokens
        _priv_user, _low_user, task_name, _db, _role = rbac_users
        resp = await rbac_client.get(
            f"/api/v1/tasks/{task_name}", headers=_auth(low_token)
        )
        assert resp.status_code == 404

    async def test_get_tasks_resolves_the_user_connection(
        self, rbac_client, rbac_users, rbac_tokens
    ):
        """The router must supply the caller's connection, not open its own.

        The override records which username the dependency was resolved for and
        then defers to the real implementation, so the request succeeds only
        because the connection is injected from the caller's session.
        """
        from fastapi import Depends

        from app.core import deps as deps_module
        from app.core.deps import get_current_user

        current_user_dep = Depends(get_current_user)
        app = rbac_client._transport.app
        opened: list[str] = []
        original = deps_module.get_user_connection

        async def spy(user: dict = current_user_dep):
            opened.append(user["username"])
            async for conn in original(user):
                yield conn

        app.dependency_overrides[original] = spy
        try:
            priv_token, _low_token = rbac_tokens
            _priv_user, _low_user, _task, _db, _role = rbac_users
            resp = await rbac_client.get("/api/v1/tasks", headers=_auth(priv_token))
            assert resp.status_code == 200
            assert opened, "get_user_connection was never called for GET /tasks"
        finally:
            app.dependency_overrides.clear()

