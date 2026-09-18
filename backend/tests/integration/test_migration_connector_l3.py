"""L3 integration tests for the Phase 11 v1 Migration Connector (NOVA-85).

These run against a **real** StarRocks 4.1.4 engine through the Nova API. The
engine acts as its own migration source: the test creates a database with a
table, a view, an async MV and a sync MV, connects the migration wizard to the
same engine, and asserts that

- enumeration returns tables/views/MVs and reads MVs from
  ``information_schema.materialized_views``;
- the dry-run classifies the async MV ``migratable`` and the sync MV ``lossy``
  with a declared reason (NOVA-84 Ruling B);
- the source password never appears in any response; and
- there is no execute path.

The suite brings the engine up via ``docker-compose.test.yml`` by default; to
point at an already-running stack::

    NOVA_ORCH_SR_PORT=29030 uv run pytest tests/integration/test_migration_connector_l3.py

Assertions never contain a real credential; the source password is a placeholder
that exists only to prove it does not come back.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid

import asyncmy
import pytest
import pytest_asyncio

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = int(_EXPLICIT_PORT or "29030")
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")

#: Placeholder credential — must never appear in a response.
SOURCE_SENTINEL = "SOURCE_PW_SENTINEL_DO_NOT_LEAK"

L3_ADMIN_USER = f"nova_mig_admin_{uuid.uuid4().hex[:8]}"
L3_ADMIN_PASSWORD = "nova_mig_admin_pw"

pytestmark = pytest.mark.engine


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


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


async def _admin_execute(sql: str):
    conn = await asyncmy.connect(
        host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
    finally:
        conn.close()


@pytest_asyncio.fixture(scope="session")
async def l3_admin_user(request):
    """A suite-owned admin account, dropped afterwards."""
    if "docker_services" in request.fixturenames:
        request.getfixturevalue("docker_services")
    if not await _reachable():
        pytest.skip("StarRocks not reachable")
    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP USER IF EXISTS '{L3_ADMIN_USER}'")
    await _admin_execute(
        f"CREATE USER '{L3_ADMIN_USER}' IDENTIFIED BY '{L3_ADMIN_PASSWORD}'"
    )
    await _admin_execute(
        f"GRANT ALL ON *.* TO '{L3_ADMIN_USER}' WITH GRANT OPTION"
    )
    yield L3_ADMIN_USER
    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP USER IF EXISTS '{L3_ADMIN_USER}'")


@pytest_asyncio.fixture
async def admin_client(engine, client, l3_admin_user):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": L3_ADMIN_USER, "password": L3_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return client


@pytest_asyncio.fixture
async def engine(request):
    if "docker_services" in request.fixturenames:
        request.getfixturevalue("docker_services")
    if not await _reachable():
        pytest.skip("StarRocks not reachable")


@pytest_asyncio.fixture
async def source_db(engine):
    """A source database with a table, a view, an async MV and a sync MV."""
    name = _unique("mig_src")
    await _admin_execute(f"CREATE DATABASE `{name}`")
    await _admin_execute(
        f"CREATE TABLE `{name}`.`orders` (id INT, amount DECIMAL(10,2)) "
        "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 4"
    )
    await _admin_execute(
        f"CREATE VIEW `{name}`.`v_orders` AS SELECT id FROM `{name}`.`orders`"
    )
    # Async MV carries REFRESH/PARTITION BY/PROPERTIES through the canonical
    # surface; sync MV does not (NOVA-84 Ruling B).
    await _admin_execute(
        f"CREATE MATERIALIZED VIEW `{name}`.`mv_async` "
        "REFRESH ASYNC EVERY(INTERVAL 1 HOUR) AS "
        f"SELECT id, amount FROM `{name}`.`orders`"
    )
    await _admin_execute(
        f"CREATE MATERIALIZED VIEW `{name}`.`mv_sync` AS "
        f"SELECT id, count(*) AS c FROM `{name}`.`orders` GROUP BY id"
    )
    try:
        yield name
    finally:
        with contextlib.suppress(Exception):
            await _admin_execute(f"DROP DATABASE IF EXISTS `{name}`")


async def _connect(admin_client) -> str:
    resp = await admin_client.post(
        "/api/v1/migration/connections",
        json={
            "host": SR_HOST,
            "port": SR_PORT,
            "username": L3_ADMIN_USER,
            "password": SOURCE_SENTINEL,
        },
    )
    assert resp.status_code == 200, resp.text
    assert SOURCE_SENTINEL not in resp.text
    return resp.json()["connection_id"]


class TestMigrationConnectorL3:
    async def test_enumerate_real_source_objects(self, admin_client, source_db):
        connection_id = await _connect(admin_client)
        resp = await admin_client.post(
            f"/api/v1/migration/connections/{connection_id}/enumerate",
            params={"database": source_db},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["materialized_view_source"] == (
            "information_schema.materialized_views"
        )
        by_kind: dict[str, set[str]] = {}
        for obj in body["objects"]:
            by_kind.setdefault(obj["kind"], set()).add(obj["name"])
        assert "orders" in by_kind.get("table", set())
        assert "v_orders" in by_kind.get("view", set())
        mvs = by_kind.get("materialized_view", set())
        assert "mv_async" in mvs
        assert "mv_sync" in mvs

    async def test_dry_run_real_verdicts(self, admin_client, source_db):
        connection_id = await _connect(admin_client)
        resp = await admin_client.post(
            f"/api/v1/migration/connections/{connection_id}/dry-run",
            json={"database": source_db},
        )
        assert resp.status_code == 200, resp.text
        assert SOURCE_SENTINEL not in resp.text
        body = resp.json()
        verdicts = {
            (o["kind"], o["name"]): o for o in body["objects"]
        }
        assert verdicts[("table", "orders")]["verdict"] == "migratable"
        assert verdicts[("view", "v_orders")]["verdict"] == "migratable"
        assert verdicts[("materialized_view", "mv_async")]["verdict"] == "migratable"
        sync_mv = verdicts[("materialized_view", "mv_sync")]
        assert sync_mv["verdict"] == "lossy"
        assert sync_mv["reason"]

    async def test_source_password_never_returns(self, admin_client, source_db):
        connection_id = await _connect(admin_client)
        for path, payload in (
            (f"/api/v1/migration/connections/{connection_id}/enumerate", None),
            (f"/api/v1/migration/connections/{connection_id}/dry-run", {"database": source_db}),
        ):
            if payload is None:
                resp = await admin_client.post(
                    path, params={"database": source_db}
                )
            else:
                resp = await admin_client.post(path, json=payload)
            assert resp.status_code == 200, resp.text
            assert SOURCE_SENTINEL not in resp.text

    async def test_no_execute_route_on_real_app(self, admin_client):
        schema = admin_client.get("/openapi.json").json()
        paths = schema.get("paths", {})
        migration_paths = [p for p in paths if "/migration" in p]
        assert migration_paths
        assert not any("execute" in p or "cutover" in p for p in migration_paths)
