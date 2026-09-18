"""L3 integration tests for the Migration Connector (Phase 11 v1, NOVA-85).

These run against a **real** StarRocks 4.1.4 through the Nova API — not a mock —
because the acceptance criterion is engine behaviour: enumeration must reflect
what the engine reports, MV DDL must come from
``SHOW CREATE MATERIALIZED VIEW`` (not ``SHOW CREATE VIEW``), and no credential
may leak on any surface.

Since the Finding 1 rework, a registered **source** is required and the reads go
to that source's cluster. The suite registers the local test engine as its own
source (``127.0.0.1:29030``), which is a real connection over the wire — the
enumerate/dry-run path opens it rather than using the admin pool.

The suite brings the engine up via ``docker-compose.test.yml`` by default; to
point at an already-running stack::

    NOVA_ORCH_SR_PORT=29030 uv run pytest tests/integration/test_migration_l3.py

Assertions never contain a real credential; sentinels are placeholders planted
through a fake secret provider so the redaction path is exercised.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid

import asyncmy
import pytest
import pytest_asyncio

from app.storage.secrets import SecretValue
from tests.integration._nova_system_ddl import ensure_audit_log

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = int(_EXPLICIT_PORT or "29030")
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")

#: Placeholder — never a real credential.
SENTINEL_KEY = "aws.s3.access_key"
SENTINEL_VALUE = "AKIA_L3_MIGRATION_SENTINEL_0001"
#: A reference the fake provider resolves to the sentinel. It is registered so
#: the redaction path runs with a real value, and asserted absent everywhere.
SENTINEL_REF = "aws://nova/l3/migration-sentinel"

MIGRATION_SOURCES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES (
    id                 VARCHAR(64) NOT NULL,
    name               VARCHAR(256) NOT NULL,
    host               VARCHAR(256) NOT NULL,
    port               INT NOT NULL DEFAULT "9030",
    username           VARCHAR(128) NOT NULL DEFAULT "root",
    secret_ref         VARCHAR(1024),
    comment            VARCHAR(1024),
    created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by         VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

pytestmark = pytest.mark.engine

#: A suite-local admin so the test does not depend on a seeded credential.
L3_ADMIN_USER = f"nova_l3_mig_{uuid.uuid4().hex[:8]}"
L3_ADMIN_PASSWORD = "nova_l3_mig_pw"


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
    conn = await asyncmy.connect(host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD)
    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
            with contextlib.suppress(Exception):
                return await cur.fetchall()
    finally:
        conn.close()
    return None


async def _ensure_schema() -> None:
    await ensure_audit_log(SR_HOST, SR_PORT, SR_USER, SR_PASSWORD)
    await _admin_execute("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    await _admin_execute(MIGRATION_SOURCES_DDL)


@pytest_asyncio.fixture(scope="session")
async def l3_admin_user(request):
    if "docker_services" in request.fixturenames:
        request.getfixturevalue("docker_services")
    if not await _reachable():
        pytest.skip("StarRocks not reachable")
    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP USER IF EXISTS '{L3_ADMIN_USER}'")
    await _admin_execute(f"CREATE USER '{L3_ADMIN_USER}' IDENTIFIED BY '{L3_ADMIN_PASSWORD}'")
    await _admin_execute(f"GRANT ALL ON *.* TO '{L3_ADMIN_USER}' WITH GRANT OPTION")
    yield L3_ADMIN_USER
    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP USER IF EXISTS '{L3_ADMIN_USER}'")


@pytest_asyncio.fixture
async def engine(request):
    if "docker_services" in request.fixturenames:
        request.getfixturevalue("docker_services")
    if not await _reachable():
        pytest.skip("StarRocks not reachable")
    await _ensure_schema()


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
async def source(admin_client):
    """Register the test engine as a source cluster and clean it up after.

    This is the Finding 1 contract: enumerate/dry-run read the **registered
    source**, so the suite must register one. The address is the local test
    engine, and no password is configured (the test engine's ``root`` has none).
    """
    name = _unique("l3_source")
    resp = await admin_client.post(
        "/api/v1/migration/sources",
        json={
            "name": name,
            "host": SR_HOST,
            "port": SR_PORT,
            "username": SR_USER,
            "comment": "l3",
        },
    )
    assert resp.status_code == 201, resp.text
    yield name
    with contextlib.suppress(Exception):
        await _admin_execute(
            f"DELETE FROM NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES WHERE name = '{name}'"
        )


@pytest_asyncio.fixture
async def namespace(engine):
    """A per-test database with a table, a view, and an async MV."""
    database = _unique("nova_mig_db")
    await _admin_execute(f"CREATE DATABASE {database}")

    # Base table — a duplicate-key table is enough for enumeration + MV build.
    await _admin_execute(
        f"CREATE TABLE {database}.base_t (id INT, dt DATE, v INT) "
        "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
        'PROPERTIES("replication_num"="1")'
    )
    await _admin_execute(f"INSERT INTO {database}.base_t VALUES (1, '2026-01-01', 10)")

    # Plain view.
    await _admin_execute(f"CREATE VIEW {database}.v_base AS SELECT id, v FROM {database}.base_t")

    # Async materialized view.
    await _admin_execute(
        f"CREATE MATERIALIZED VIEW {database}.mv_async "
        "REFRESH ASYNC EVERY(INTERVAL 1 HOUR) "
        f"AS SELECT dt, sum(v) AS total FROM {database}.base_t GROUP BY dt"
    )

    yield {
        "database": database,
        "table": "base_t",
        "view": "v_base",
        "mv_async": "mv_async",
    }

    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP DATABASE IF EXISTS {database}")


class TestEnumerationOnRealEngine:
    async def test_enumerates_table_view_and_mv(self, admin_client, source, namespace):
        resp = await admin_client.post(
            "/api/v1/migration/enumerate",
            json={"source": source, "database": namespace["database"]},
        )
        assert resp.status_code == 200, resp.text
        kinds = {o["name"]: o["kind"] for o in resp.json()["objects"]}

        assert kinds.get(namespace["table"]) == "table"
        assert kinds.get(namespace["view"]) == "view"
        # The MV surfaces from information_schema.materialized_views.
        assert kinds.get(namespace["mv_async"]) == "materialized_view"

    async def test_mv_is_not_reported_as_a_plain_view(self, admin_client, source, namespace):
        resp = await admin_client.post(
            "/api/v1/migration/enumerate",
            json={"source": source, "database": namespace["database"]},
        )
        objects = resp.json()["objects"]
        mv = next((o for o in objects if o["name"] == namespace["mv_async"]), None)
        assert mv is not None
        assert mv["kind"] == "materialized_view"

    async def test_enumeration_reads_the_registered_source_not_a_local_fallback(
        self, admin_client, namespace
    ):
        """Finding 1 — enumeration without a source is rejected, and an unknown
        source is a 404 rather than a silent read of the local engine."""
        no_source = await admin_client.post(
            "/api/v1/migration/enumerate", json={"database": namespace["database"]}
        )
        assert no_source.status_code == 422

        unknown = await admin_client.post(
            "/api/v1/migration/enumerate",
            json={"source": "does_not_exist", "database": namespace["database"]},
        )
        assert unknown.status_code == 404


class TestDryRunOnRealEngine:
    async def test_dry_run_returns_verdict_and_reason(self, admin_client, source, namespace):
        resp = await admin_client.post(
            "/api/v1/migration/dry-run",
            json={"source": source, "database": namespace["database"], "objects": []},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        by_name = {i["name"]: i for i in body["items"]}
        assert by_name[namespace["table"]]["verdict"] == "migratable"
        # The async MV is listed by information_schema.materialized_views and is
        # migratable through SHOW CREATE MATERIALIZED VIEW.
        assert by_name[namespace["mv_async"]]["verdict"] == "migratable"
        assert all(i["reason"] for i in body["items"])

        # QA Low finding: the sync-MV setup was a silent no-op on 4.1.4 (the
        # engine does not list sync MVs in information_schema.materialized_views
        # and a plain `CREATE MATERIALIZED VIEW ... AS SELECT` creates nothing
        # there). Pin the engine behaviour rather than assume it: if the engine
        # ever starts reporting a sync MV, this fails and the verdict assertion
        # must be added deliberately.
        assert "mv_sync" not in by_name

    async def test_async_mv_ddl_uses_show_create_materialized_view(
        self, admin_client, source, namespace
    ):
        """The async MV detail must carry REFRESH/PARTITION info that
        ``SHOW CREATE VIEW`` would drop."""
        resp = await admin_client.post(
            "/api/v1/migration/dry-run",
            json={
                "source": source,
                "database": namespace["database"],
                "objects": [namespace["mv_async"]],
            },
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        detail = item["detail"] or ""
        assert "MATERIALIZED VIEW" in detail.upper()
        # REFRESH is exactly what the canonical surface preserves.
        assert "REFRESH" in detail.upper()


class TestNoCredentialLeakOnRealEngine:
    async def test_secret_reference_password_never_reaches_response(
        self, admin_client, monkeypatch
    ):
        """AC4 — a password resolved from a secret reference must not be echoed.

        QA Low finding: the previous version asserted a sentinel it never
        planted, so the assertion could not fail. Here the fake provider returns
        the sentinel as the source password; the registered source is then
        unreachable (a closed port), so the connection attempt runs the real
        resolution path and the error response is the thing under test.
        """
        from app.modules.migration import source as source_module

        def _fake_resolve(reference: str, **_kwargs) -> SecretValue:
            assert reference == SENTINEL_REF
            return SecretValue(access_key="nova", secret_key=SENTINEL_VALUE)

        monkeypatch.setattr(source_module, "resolve_secret_reference", _fake_resolve)

        name = _unique("src_secret")
        resp = await admin_client.post(
            "/api/v1/migration/sources",
            json={
                "name": name,
                "host": "127.0.0.1",
                # A port nothing is listening on: the open fails, and the
                # response must not carry the resolved password.
                "port": 1,
                "username": SR_USER,
                "secret_ref": SENTINEL_REF,
            },
        )
        assert resp.status_code == 201, resp.text
        assert SENTINEL_VALUE not in resp.text

        resp = await admin_client.post(
            "/api/v1/migration/dry-run",
            json={"source": name, "database": "information_schema", "objects": []},
        )
        # Resolution ran (the provider asserts the reference) and the connection
        # failed; the failure body must not contain the password.
        assert resp.status_code == 502, resp.text
        assert SENTINEL_VALUE not in resp.text

        with contextlib.suppress(Exception):
            await _admin_execute(
                f"DELETE FROM NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES WHERE name = '{name}'"
            )

    async def test_source_registration_stores_no_password(self, admin_client, source):
        """The registry stores the address and secret *reference*, never a value."""
        rows = await _admin_execute(
            "SELECT id, name, host, port, username, secret_ref "
            "FROM NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES "
            f"WHERE name = '{source}'"
        )
        assert rows, "source row was not persisted"
        flat = str(rows)
        assert SENTINEL_VALUE not in flat
        assert SENTINEL_KEY not in flat
        # No password column exists at all.
        columns = await _admin_execute(
            "SELECT COLUMN_NAME FROM information_schema.columns "
            "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' "
            "AND TABLE_NAME = 'CONFIG_MIGRATION_SOURCES'"
        )
        names = {row[0].lower() for row in (columns or [])}
        assert "password" not in names


class TestAuditOnRealEngine:
    async def test_dry_run_writes_an_audit_row(self, admin_client, source, namespace):
        """Finding 2 — the operator-facing dry-run is recorded in AUDIT_LOG."""
        resp = await admin_client.post(
            "/api/v1/migration/dry-run",
            json={"source": source, "database": namespace["database"], "objects": []},
        )
        assert resp.status_code == 200, resp.text

        rows = await _admin_execute(
            "SELECT action, object_name, status FROM NOVA_SYSTEM.AUDIT_LOG "
            f"WHERE action = 'dry_run' AND object_name = '{source}' "
            "ORDER BY event_time DESC LIMIT 1"
        )
        assert rows, "no audit row was written for the dry-run"
        assert rows[0][0] == "dry_run"
        assert SENTINEL_VALUE not in str(rows)

    async def test_source_registration_writes_an_audit_row(self, admin_client):
        name = _unique("audit_src")
        resp = await admin_client.post(
            "/api/v1/migration/sources",
            json={"name": name, "host": SR_HOST, "port": SR_PORT, "username": SR_USER},
        )
        assert resp.status_code == 201, resp.text

        rows = await _admin_execute(
            "SELECT action, object_name FROM NOVA_SYSTEM.AUDIT_LOG "
            f"WHERE action = 'register_source' AND object_name = '{name}' "
            "ORDER BY event_time DESC LIMIT 1"
        )
        assert rows, "no audit row was written for source registration"
        assert SENTINEL_VALUE not in str(rows)

        with contextlib.suppress(Exception):
            await _admin_execute(
                f"DELETE FROM NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES WHERE name = '{name}'"
            )


class TestNoExecutePath:
    async def test_capabilities_declare_no_execute(self, admin_client):
        resp = await admin_client.get("/api/v1/migration/capabilities")
        assert resp.status_code == 200
        assert resp.json()["execute_available"] is False
