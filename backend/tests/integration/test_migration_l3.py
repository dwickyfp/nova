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
import redis.asyncio as aioredis

from app.modules.migration.job_worker import MigrationJobWorker
from app.storage.secrets import SecretValue
from tests.integration._nova_system_ddl import ensure_audit_log
from tests.integration._stack import (
    require_shared_stack,
    shared_stack_host_port,
)

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = _EXPLICIT_PORT or shared_stack_host_port("NOVA_TEST_FE_MYSQL_PORT", 29030)
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


@pytest_asyncio.fixture(autouse=True)
async def migration_worker(app):
    """Run the worker dispatcher alongside the in-process API for L3 tests."""
    from app.core.config import settings

    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    stop = asyncio.Event()
    task = asyncio.create_task(MigrationJobWorker(redis).run_forever(stop))
    try:
        yield
    finally:
        stop.set()
        await task
        await redis.aclose()


async def _await_execute(client, response, *, timeout_seconds: float = 120) -> dict:
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        status = await client.get(f"/api/v1/migration/jobs/{job_id}")
        assert status.status_code == 200, status.text
        body = status.json()
        if body["status"] in {"succeeded", "partial", "failed", "interrupted"}:
            return body
        await asyncio.sleep(0.25)
    pytest.fail(f"migration job {job_id} did not settle")


#: A suite-local admin so the test does not depend on a seeded credential.
L3_ADMIN_USER = f"nova_l3_mig_{uuid.uuid4().hex[:8]}"
L3_ADMIN_PASSWORD = "nova_l3_mig_pw"


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _retarget(ddl: str, source_db: str, target_db: str, object_name: str) -> str:
    """Point a reconstructed DDL at the target database.

    StarRocks emits ``SHOW CREATE`` DDL with an **unqualified** object name
    (``CREATE TABLE `t` ...``) while internal references keep the source
    database (``... FROM source_db.other``). A real execute path therefore needs
    two rewrites: qualify the created object with the target database, and
    repoint every source-database reference inside the body. This helper is the
    test's stand-in for that retargeter — it is deliberately not production code
    until 11-B lands.
    """
    head, _, body = ddl.partition("(")
    # Qualify only the leading object name, not argument lists of a function.
    marker = f"`{object_name}`"
    if marker in head:
        head = head.replace(marker, f"`{target_db}`.`{object_name}`", 1)
    retargeted = head + "(" + body
    return retargeted.replace(f"`{source_db}`", f"`{target_db}`").replace(
        f"{source_db}.", f"{target_db}."
    )


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
    require_shared_stack(request)
    if not await _reachable():
        pytest.skip("StarRocks not reachable")
    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP USER IF EXISTS '{L3_ADMIN_USER}'")
    await _admin_execute(f"CREATE USER '{L3_ADMIN_USER}' IDENTIFIED BY '{L3_ADMIN_PASSWORD}'")
    # ``GRANT ALL ON *.*`` covers table-level operations only. Migration execute
    # creates databases, tables, views, MVs and functions, which need the
    # explicit privileges below — the same grant set a migration operator needs
    # on the target (see the CREATE DATABASE ON CATALOG form in the StarRocks
    # GRANT docs).
    for grant in (
        "GRANT ALL ON *.* TO '{u}' WITH GRANT OPTION",
        "GRANT CREATE DATABASE ON CATALOG default_catalog TO '{u}'",
        "GRANT CREATE TABLE, CREATE VIEW, CREATE MATERIALIZED VIEW ON ALL DATABASES TO '{u}'",
    ):
        await _admin_execute(grant.format(u=L3_ADMIN_USER))
    yield L3_ADMIN_USER
    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP USER IF EXISTS '{L3_ADMIN_USER}'")


@pytest_asyncio.fixture
async def engine(request):
    require_shared_stack(request)
    if not await _reachable():
        pytest.skip("StarRocks not reachable")
    await _ensure_schema()


@pytest_asyncio.fixture(scope="session")
async def transfer_bucket(request):
    """Ensure the data-movement stage bucket exists on the test MinIO.

    Data movement writes to ``s3://<connection.bucket>/migration-staging``; the
    test stack's minio-init only creates ``test-stage``, so the connection's
    bucket is created here. The bucket is engine-reachable at ``minio:<port>``
    (same number both sides in ``docker-compose.test.yml``).
    """
    require_shared_stack(request)
    import boto3
    from botocore.config import Config

    from app.core.config import get_storage_connection, load_nova_app_config

    config = load_nova_app_config()
    connection = get_storage_connection(None)
    endpoint = connection.endpoint.replace("127.0.0.1", "127.0.0.1")
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=connection.access_key,
            aws_secret_access_key=connection.secret_key,
            config=Config(s3={"addressing_style": "path"}),
            region_name="us-east-1",
        )
        existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
        if connection.bucket not in existing:
            s3.create_bucket(Bucket=connection.bucket)
    except Exception:
        # A missing bucket or unreachable MinIO surfaces as a skip in the test
        # body, not a hard failure of the whole suite.
        pass
    yield connection.bucket
    assert config is not None


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


class TestExecuteGateOnRealEngine:
    async def test_capabilities_declare_execute_gated(self, admin_client):
        resp = await admin_client.get("/api/v1/migration/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        # Execute is present but gated; the gate is off in the test environment.
        assert "execute" in body["phases"]
        assert body["execute_gate"] == {"issue": "#7", "name": "backup/restore"}
        assert body["execute_available"] is False

    async def test_execute_refused_while_gate_closed(self, admin_client, source, namespace):
        """A caller cannot reach the engine while the operator has not opened
        the #7 gate."""
        resp = await admin_client.post(
            "/api/v1/migration/execute",
            json={
                "source": source,
                "database": namespace["database"],
                "target_database": f"{namespace['database']}_exec",
                "acknowledge_omissions": True,
                "confirmation": f"{namespace['database']}_exec",
            },
        )
        assert resp.status_code == 403, resp.text
        assert "disabled" in resp.json()["detail"].lower()

    async def test_execute_applies_when_gate_open(
        self, admin_client, source, namespace, monkeypatch
    ):
        """With the gate open, execute creates the target objects for real.

        Proves the whole 11-B path: plan → execute through the query pipeline →
        target exists with matching schema. The gate is enabled only for this
        test via monkeypatch; production starts gated off.
        """
        from app.core.config import settings

        monkeypatch.setattr(settings, "MIGRATION_EXECUTE_ENABLED", True, raising=False)
        monkeypatch.setattr(
            settings, "MIGRATION_EXECUTE_REQUIRE_CONFIRMATION", False, raising=False
        )
        db = namespace["database"]
        target = f"{db}_exec"
        with contextlib.suppress(Exception):
            await _admin_execute(f"DROP DATABASE IF EXISTS {target}")

        resp = await admin_client.post(
            "/api/v1/migration/execute",
            json={
                "source": source,
                "database": db,
                "target_database": target,
                "acknowledge_omissions": True,
            },
        )
        body = await _await_execute(admin_client, resp)
        assert body["status"] == "succeeded", body
        result = body["results"][0]
        assert result["failed"] == 0, body
        assert result["succeeded"] >= 3  # database + table + view + MV

        try:
            dbs = await _admin_execute("SHOW DATABASES")
            assert (target,) in dbs
            src_cols = await _admin_execute(
                "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.columns "
                f"WHERE TABLE_SCHEMA = '{db}' AND TABLE_NAME = '{namespace['table']}' "
                "ORDER BY ORDINAL_POSITION"
            )
            dst_cols = await _admin_execute(
                "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.columns "
                f"WHERE TABLE_SCHEMA = '{target}' AND TABLE_NAME = '{namespace['table']}' "
                "ORDER BY ORDINAL_POSITION"
            )
            assert src_cols and src_cols == dst_cols
        finally:
            with contextlib.suppress(Exception):
                await _admin_execute(f"DROP DATABASE IF EXISTS {target}")

    async def test_preflight_reports_ok_for_privileged_caller(
        self, admin_client, source, namespace
    ):
        resp = await admin_client.post(
            "/api/v1/migration/preflight",
            json={
                "source": source,
                "database": namespace["database"],
                "target_database": f"{namespace['database']}_pf",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True, body
        assert body["missing"] == []
        # The privileges the plan needs are all present.
        assert {c["privilege"] for c in body["checks"]} >= {
            "CREATE DATABASE",
            "CREATE TABLE",
            "CREATE VIEW",
        }

    async def test_preflight_reports_missing_privilege(
        self, admin_client, source, namespace, monkeypatch
    ):
        """A caller without table-creation rights is told before execute.

        The L3 admin holds the grants; here the caller's own ``SHOW GRANTS`` is
        stubbed to a table-less set so the analyzer's refusal is exercised
        against a real engine connection.
        """
        from app.modules.migration import service as service_module

        limited = [
            (
                "'x'@'%'",
                "default_catalog",
                "GRANT CREATE DATABASE ON CATALOG default_catalog TO USER 'x'@'%'",
            ),
        ]
        original = service_module.MigrationService._read_caller_grants

        async def _limited(self, **kwargs):
            return limited

        monkeypatch.setattr(service_module.MigrationService, "_read_caller_grants", _limited)
        try:
            resp = await admin_client.post(
                "/api/v1/migration/preflight",
                json={
                    "source": source,
                    "database": namespace["database"],
                    "target_database": f"{namespace['database']}_pf2",
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["ok"] is False
            assert "CREATE TABLE" in body["missing"]
        finally:
            monkeypatch.setattr(service_module.MigrationService, "_read_caller_grants", original)

    async def test_execute_blocked_by_preflight(self, admin_client, source, namespace, monkeypatch):
        """Execute fails fast (409) when the preflight finds a missing privilege."""
        from app.core.config import settings
        from app.modules.migration import service as service_module

        monkeypatch.setattr(settings, "MIGRATION_EXECUTE_ENABLED", True, raising=False)
        monkeypatch.setattr(
            settings, "MIGRATION_EXECUTE_REQUIRE_CONFIRMATION", False, raising=False
        )
        monkeypatch.setattr(settings, "MIGRATION_EXECUTE_PREFLIGHT", True, raising=False)

        async def _no_grants(self, **kwargs):
            return []

        monkeypatch.setattr(service_module.MigrationService, "_read_caller_grants", _no_grants)
        resp = await admin_client.post(
            "/api/v1/migration/execute",
            json={
                "source": source,
                "database": namespace["database"],
                "target_database": f"{namespace['database']}_pf3",
                "acknowledge_omissions": True,
            },
        )
        body = await _await_execute(admin_client, resp)
        assert body["status"] == "failed", body
        assert body["results"][0]["error"] == "preflight_failed"

    async def test_execute_moves_data_with_verification(
        self, admin_client, source, namespace, monkeypatch, transfer_bucket
    ):
        """11-C: schema + data. Export to a stage, import to the target, verify.

        Requires the source cluster and the target to reach the same object
        storage. Skips when the test MinIO is not engine-reachable, because that
        is an environment precondition, not a Nova defect.
        """
        from app.core.config import settings

        monkeypatch.setattr(settings, "MIGRATION_EXECUTE_ENABLED", True, raising=False)
        monkeypatch.setattr(
            settings, "MIGRATION_EXECUTE_REQUIRE_CONFIRMATION", False, raising=False
        )
        db = namespace["database"]
        table = namespace["table"]
        target = f"{db}_data"

        # Seed extra rows so the digest is non-trivial.
        await _admin_execute(f"INSERT INTO {db}.{table} VALUES (2, '2026-01-02', 20)")
        await _admin_execute(f"INSERT INTO {db}.{table} VALUES (3, '2026-01-03', 30)")

        with contextlib.suppress(Exception):
            await _admin_execute(f"DROP DATABASE IF EXISTS {target}")

        resp = await admin_client.post(
            "/api/v1/migration/execute",
            json={
                "source": source,
                "database": db,
                "target_database": target,
                "acknowledge_omissions": True,
                "include_data": True,
            },
        )
        body = await _await_execute(admin_client, resp)
        result = body["results"][0]
        try:
            copy = next((c for c in result["data"] if c["table"] == table), None)
            assert copy is not None, f"no copy result for {table}: {result['data']}"
            if copy["errors"] and any(
                "endpoint" in e.lower() or "connect" in e.lower() or "s3" in e.lower()
                for e in copy["errors"]
            ):
                pytest.skip(f"object storage not engine-reachable: {copy['errors']}")
            assert copy["verified"] is True, copy
            assert copy["digest_match"] is True, copy
            assert copy["rows_imported"] == 3
            assert result["rows_moved"] == 3

            # Independent check straight on the target.
            target_count = await _admin_execute(f"SELECT COUNT(*) FROM {target}.{table}")
            source_count = await _admin_execute(f"SELECT COUNT(*) FROM {db}.{table}")
            assert target_count == source_count == ((3,),)
        finally:
            with contextlib.suppress(Exception):
                await _admin_execute(f"DROP DATABASE IF EXISTS {target}")

    async def test_execute_is_idempotent_on_rerun(
        self, admin_client, source, namespace, monkeypatch
    ):
        """Running execute twice must not fail — the second run is a no-op."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "MIGRATION_EXECUTE_ENABLED", True, raising=False)
        monkeypatch.setattr(
            settings, "MIGRATION_EXECUTE_REQUIRE_CONFIRMATION", False, raising=False
        )
        db = namespace["database"]
        target = f"{db}_idem"
        with contextlib.suppress(Exception):
            await _admin_execute(f"DROP DATABASE IF EXISTS {target}")
        payload = {
            "source": source,
            "database": db,
            "target_database": target,
            "acknowledge_omissions": True,
        }
        try:
            first = await admin_client.post("/api/v1/migration/execute", json=payload)
            first_body = await _await_execute(admin_client, first)
            assert first_body["results"][0]["failed"] == 0
            second = await admin_client.post("/api/v1/migration/execute", json=payload)
            second_body = await _await_execute(admin_client, second)
            # Second run creates nothing new but must not fail.
            assert second_body["results"][0]["failed"] == 0
        finally:
            with contextlib.suppress(Exception):
                await _admin_execute(f"DROP DATABASE IF EXISTS {target}")


class TestSchemaRoundTrip:
    """The acceptance harness for a real cutover: reconstruct DDL from dry-run
    details and apply it to a fresh target, then compare the two engines.

    Execute is not implemented (gated on #7), so this test performs the apply
    step itself — it is the target any future execute path must satisfy. A
    ``migratable`` verdict that cannot be replayed here is a false positive in
    the report, which is exactly the failure this suite exists to catch.
    """

    async def test_function_detail_is_reconstructable_and_runs(
        self, admin_client, source, namespace
    ):
        """A SQL UDF's reconstructed DDL must CREATE and evaluate identically."""
        db = namespace["database"]
        try:
            await _admin_execute(f"CREATE FUNCTION {db}.f_add(x INT, y INT) RETURNS x + y")
        except Exception as exc:  # pragma: no cover - depends on engine config
            if "enable_udf" in str(exc) or "UDF is not enabled" in str(exc):
                pytest.skip("engine has UDFs disabled (enable_udf=false)")
            raise

        resp = await admin_client.post(
            "/api/v1/migration/dry-run",
            json={"source": source, "database": db, "objects": ["f_add"]},
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        assert item["kind"] == "function"
        # The body is recoverable from SHOW FULL FUNCTIONS, so a definition must
        # be present even though the verdict is lossy (arg names are inferred).
        assert item["detail"], "function detail must be reconstructed, not None"
        assert "CREATE FUNCTION" in item["detail"].upper()

        target = f"{db}_rt"
        await _admin_execute(f"CREATE DATABASE {target}")
        try:
            await _admin_execute(_retarget(item["detail"], db, target, "f_add"))
            src = await _admin_execute(f"SELECT {db}.f_add(3, 4)")
            dst = await _admin_execute(f"SELECT {target}.f_add(3, 4)")
            assert src == dst == ((7,),)
        finally:
            with contextlib.suppress(Exception):
                await _admin_execute(f"DROP DATABASE IF EXISTS {target}")

    async def test_table_schema_matches_after_replay(self, admin_client, source, namespace):
        """A table classified ``migratable`` must produce an equivalent schema.

        Distribution-level properties (``replication_num``, buckets) are
        deployment-specific and are expected to differ; the column set and key
        model are what must survive.
        """
        db = namespace["database"]
        table = namespace["table"]
        resp = await admin_client.post(
            "/api/v1/migration/dry-run",
            json={"source": source, "database": db, "objects": [table]},
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        assert item["verdict"] == "migratable"
        assert item["detail"] and "CREATE TABLE" in item["detail"].upper()

        target = f"{db}_rt"
        await _admin_execute(f"CREATE DATABASE {target}")
        try:
            await _admin_execute(_retarget(item["detail"], db, target, table))
            src_cols = await _admin_execute(
                "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_KEY FROM information_schema.columns "
                f"WHERE TABLE_SCHEMA = '{db}' AND TABLE_NAME = '{table}' ORDER BY ORDINAL_POSITION"
            )
            dst_cols = await _admin_execute(
                "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_KEY FROM information_schema.columns "
                f"WHERE TABLE_SCHEMA = '{target}' AND TABLE_NAME = '{table}' "
                "ORDER BY ORDINAL_POSITION"
            )
            assert src_cols == dst_cols
        finally:
            with contextlib.suppress(Exception):
                await _admin_execute(f"DROP DATABASE IF EXISTS {target}")

    async def test_view_definition_matches_after_replay(self, admin_client, source, namespace):
        db = namespace["database"]
        view = namespace["view"]
        resp = await admin_client.post(
            "/api/v1/migration/dry-run",
            json={"source": source, "database": db, "objects": [view]},
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        assert item["verdict"] == "migratable"
        assert item["detail"] and "CREATE VIEW" in item["detail"].upper()

        target = f"{db}_rt"
        await _admin_execute(f"CREATE DATABASE {target}")
        try:
            # The view body references the source table, so replay it too.
            table_ddl = await _admin_execute(f"SHOW CREATE TABLE {db}.{namespace['table']}")
            await _admin_execute(_retarget(table_ddl[0][1], db, target, namespace["table"]))
            await _admin_execute(_retarget(item["detail"], db, target, namespace["view"]))
            # Compare definitions, not rows: this suite moves schema, not data
            # (11-C). The only expected difference is the database qualifier.
            src_def = await _admin_execute(f"SHOW CREATE VIEW {db}.{view}")
            dst_def = await _admin_execute(f"SHOW CREATE VIEW {target}.{view}")
            assert src_def and dst_def
            normalize = lambda s: s.replace(target, db)  # noqa: E731
            assert normalize(dst_def[0][1]) == src_def[0][1]
        finally:
            with contextlib.suppress(Exception):
                await _admin_execute(f"DROP DATABASE IF EXISTS {target}")


class TestDataFidelityHarness:
    """Row-level fidelity checks for the (not yet implemented) data-movement phase.

    v1 moves schema only (roadmap 11-C). There is no ``INSERT INTO FILES()`` or
    cross-cluster copy path yet, so these tests perform the copy by hand and
    assert the comparison a future mover must satisfy: identical row count and
    an order-independent digest per table. They are the acceptance criteria for
    11-C, written now so the mover cannot land without them.
    """

    @staticmethod
    async def _row_count(db: str, table: str) -> int:
        rows = await _admin_execute(f"SELECT COUNT(*) FROM `{db}`.`{table}`")
        return int(rows[0][0]) if rows else -1

    async def test_table_schema_layout_roundtrips_empty(self, admin_client, source, namespace):
        """Replaying a schema yields an empty but structurally identical table.

        This is the boundary of v1: schema moves, rows do not. Pinning it stops
        a report reader from assuming a ``migratable`` table also carried data.
        """
        db = namespace["database"]
        table = namespace["table"]
        resp = await admin_client.post(
            "/api/v1/migration/dry-run",
            json={"source": source, "database": db, "objects": [table]},
        )
        item = resp.json()["items"][0]
        target = f"{db}_data"
        await _admin_execute(f"CREATE DATABASE {target}")
        try:
            await _admin_execute(_retarget(item["detail"], db, target, table))
            assert await self._row_count(db, table) == 1
            assert await self._row_count(target, table) == 0
        finally:
            with contextlib.suppress(Exception):
                await _admin_execute(f"DROP DATABASE IF EXISTS {target}")

    async def test_manual_copy_matches_row_count_and_digest(self, admin_client, source, namespace):
        """A hand-run copy must match on count and an order-independent digest.

        The digest is a sum over the only non-null column set the fixture
        guarantees; it is deterministic regardless of row order, which is what a
        parallel mover produces.
        """
        db = namespace["database"]
        table = namespace["table"]
        target = f"{db}_copy"
        await _admin_execute(f"CREATE DATABASE {target}")
        try:
            table_ddl = await _admin_execute(f"SHOW CREATE TABLE {db}.{table}")
            await _admin_execute(_retarget(table_ddl[0][1], db, target, table))
            # Stand-in for the future mover: read source rows, insert into target.
            rows = await _admin_execute(f"SELECT id, dt, v FROM {db}.{table}")
            for row in rows:
                await _admin_execute(
                    f"INSERT INTO {target}.{table} VALUES ({row[0]}, '{row[1]}', {row[2]})"
                )
            assert await self._row_count(db, table) == await self._row_count(target, table)
            src_digest = await _admin_execute(f"SELECT SUM(v) FROM {db}.{table}")
            dst_digest = await _admin_execute(f"SELECT SUM(v) FROM {target}.{table}")
            assert src_digest == dst_digest
        finally:
            with contextlib.suppress(Exception):
                await _admin_execute(f"DROP DATABASE IF EXISTS {target}")


class TestPlanApplyOnRealEngine:
    """The full 11-B path, executed by the test: request a plan, run every step
    against a real target, then verify the target matches the source.

    The apply endpoint does not exist (gated on #7); this suite is the executor.
    It proves the *production* retargeter + planner produce statements that
    actually run in dependency order on a fresh database — the strongest evidence
    a dry-run verdict is not a false positive.
    """

    async def test_plan_steps_apply_and_match_source(self, admin_client, source, namespace):
        db = namespace["database"]
        target = f"{db}_plan"

        resp = await admin_client.post(
            "/api/v1/migration/plan",
            json={"source": source, "database": db, "target_database": target},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["execute_available"] is False
        # Dependency order: database, then table(s), then view(s), then MV(s).
        kinds = [step["kind"] for step in body["steps"]]
        assert kinds[0] == "database"
        assert kinds.index("table") < kinds.index("view") < kinds.index("materialized_view")

        try:
            for step in body["steps"]:
                await _admin_execute(step["statement"])

            # The target database and its objects now exist.
            dbs = await _admin_execute("SHOW DATABASES")
            assert (target,) in dbs

            # Schema equality for the base table.
            src_cols = await _admin_execute(
                "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_KEY FROM information_schema.columns "
                f"WHERE TABLE_SCHEMA = '{db}' AND TABLE_NAME = '{namespace['table']}' "
                "ORDER BY ORDINAL_POSITION"
            )
            dst_cols = await _admin_execute(
                "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_KEY FROM information_schema.columns "
                f"WHERE TABLE_SCHEMA = '{target}' AND TABLE_NAME = '{namespace['table']}' "
                "ORDER BY ORDINAL_POSITION"
            )
            assert src_cols == dst_cols

            # The MV definition round-trips (REFRESH preserved, retargeted body).
            src_mv = await _admin_execute(
                f"SHOW CREATE MATERIALIZED VIEW {db}.{namespace['mv_async']}"
            )
            dst_mv = await _admin_execute(
                f"SHOW CREATE MATERIALIZED VIEW {target}.{namespace['mv_async']}"
            )
            assert src_mv and dst_mv
            assert "REFRESH" in dst_mv[0][1].upper()
            assert target in dst_mv[0][1]
            assert db not in dst_mv[0][1].replace(target, "")
        finally:
            with contextlib.suppress(Exception):
                await _admin_execute(f"DROP DATABASE IF EXISTS {target}")

    async def test_plan_reports_created_database_first(self, admin_client, source, namespace):
        db = namespace["database"]
        resp = await admin_client.post(
            "/api/v1/migration/plan",
            json={"source": source, "database": db, "target_database": f"{db}_ord"},
        )
        assert resp.status_code == 200, resp.text
        steps = resp.json()["steps"]
        assert steps[0]["kind"] == "database"
        assert steps[0]["order"] == 0
        assert [s["order"] for s in steps] == list(range(len(steps)))

    async def test_plan_blocks_objects_without_definition(self, admin_client, source, namespace):
        """A task has no reconstructed definition → it is blocked, not executed.

        ``CREATE TASK`` is not available on every 4.1.4 build, so the setup is
        skipped when the engine rejects it; the blocked-object contract itself is
        covered unconditionally by the unit suite.
        """
        db = namespace["database"]
        try:
            await _admin_execute(
                f"CREATE TASK {db}.t_noop SCHEDULE EVERY(INTERVAL 1 HOUR) AS SELECT 1"
            )
        except Exception as exc:  # pragma: no cover - depends on engine build
            if "CREATE TASK" in str(exc) or "No viable statement" in str(exc):
                pytest.skip("engine does not support CREATE TASK")
            raise
        try:
            resp = await admin_client.post(
                "/api/v1/migration/plan",
                json={"source": source, "database": db, "target_database": f"{db}_blk"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            blocked_names = {o["name"] for o in body["blocked"]}
            assert "t_noop" in blocked_names
            assert all(o["reason"] for o in body["blocked"])
            executed = {s["object_name"] for s in body["steps"]}
            assert "t_noop" not in executed
        finally:
            with contextlib.suppress(Exception):
                await _admin_execute(f"DROP TASK IF EXISTS {db}.t_noop")
