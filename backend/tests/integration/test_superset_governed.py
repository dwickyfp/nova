"""Phase 1 acceptance for Superset on the governed stack (docs/arch-17-superset.md).

Runs against a running Nova stack with the patched FE, Ranger, the Nova API,
the MySQL proxy and Superset. It creates its own principals, roles, Ranger
policies and data, and removes them afterwards. It never touches existing
users or data.

Required environment:

* ``NOVA_SUPERSET_URL`` — Superset's origin, e.g. ``http://127.0.0.1:8088``
  (pages live under ``/bi``). Unset skips the module.
* ``NOVA_SUPERSET_SERVICE_TOKEN`` — the shared secret, to read data tokens
  for the proxy-level checks.
* ``NOVA_API_URL`` (default ``http://127.0.0.1:8000``), ``NOVA_PROXY_HOST`` /
  ``NOVA_PROXY_PORT`` (default ``127.0.0.1:4406``).
* System settings (``STARROCKS_*``, ``RANGER_*``, ``REDIS_URL``) of the same
  stack, as for the other governed live tests.
* Optional ``NOVA_SUPERSET_LOG_CONTAINERS`` — comma-separated container names
  whose logs must not contain any issued secret.
* Optional ``NOVA_SUPERSET_DB_CONTAINER`` — the Superset metadata database
  container, to check that no credential is stored there.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import asyncmy
import httpx
import pytest

from app.core.config import settings
from app.core.database import db

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        not os.getenv("NOVA_SUPERSET_URL") or not os.getenv("NOVA_SUPERSET_SERVICE_TOKEN"),
        reason="Requires a governed Nova stack with Superset: NOVA_SUPERSET_URL and "
        "NOVA_SUPERSET_SERVICE_TOKEN",
    ),
]

API_URL = os.getenv("NOVA_API_URL", "http://127.0.0.1:8000")
BI_URL = os.getenv("NOVA_SUPERSET_URL", "").rstrip("/")
PROXY_HOST = os.getenv("NOVA_PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.getenv("NOVA_PROXY_PORT", "4406"))
PROPAGATION_SECONDS = 120


@dataclass
class Principal:
    username: str
    password: str
    bearer: str = ""
    handle: str = ""


class Bi:
    """A Superset browser session for one Nova BI handle."""

    def __init__(self, handle: str) -> None:
        # Superset's gunicorn closes idle keep-alive connections after seconds;
        # polling with gaps would reuse a closed one.
        self.http = httpx.AsyncClient(
            base_url=BI_URL,
            timeout=120,
            follow_redirects=False,
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        self.http.cookies.set("nova_bi", handle, path="/bi")
        self._csrf = ""
        self._database_id: int | None = None

    async def close(self) -> None:
        await self.http.aclose()

    async def headers(self) -> dict:
        if not self._csrf:
            response = await self.http.get("/bi/api/v1/security/csrf_token/")
            response.raise_for_status()
            self._csrf = response.json()["result"]
        return {"X-CSRFToken": self._csrf, "Referer": f"{BI_URL}/bi/sqllab/"}

    async def database_id(self) -> int:
        if self._database_id is None:
            response = await self.http.get("/bi/api/v1/database/")
            response.raise_for_status()
            names = {row["database_name"]: row["id"] for row in response.json()["result"]}
            self._database_id = names["Nova"]
        return self._database_id

    async def sql(self, sql: str, schema: str) -> httpx.Response:
        return await self.http.post(
            "/bi/api/v1/sqllab/execute/",
            headers=await self.headers(),
            json={
                "database_id": await self.database_id(),
                "sql": sql,
                "schema": schema,
                "catalog": "default_catalog",
                "runAsync": False,
                "client_id": secrets.token_hex(5),
                "queryLimit": 1000,
            },
        )


async def _chart_rows(bi: Bi, dataset_id: int) -> list[dict]:
    response = await bi.http.post(
        "/bi/api/v1/chart/data",
        headers=await bi.headers(),
        json={
            "datasource": {"id": dataset_id, "type": "table"},
            "queries": [{"columns": ["id", "email"], "orderby": [["id", True]], "row_limit": 100}],
            "result_type": "full",
            "result_format": "json",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"][0]["data"]


def _rows(response: httpx.Response) -> list[dict]:
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _login(api: httpx.AsyncClient, principal: Principal) -> None:
    response = await api.post(
        "/api/v1/auth/login",
        json={"username": principal.username, "password": principal.password},
    )
    assert response.status_code == 200, response.text
    principal.bearer = response.json()["access_token"]
    session = await api.post("/api/v1/superset/session", headers=_auth(principal))
    assert session.status_code == 200, session.text
    principal.handle = session.cookies["nova_bi"]
    cookie = session.headers["set-cookie"].lower()
    assert "httponly" in cookie and "path=/bi" in cookie and "samesite=strict" in cookie


def _auth(principal: Principal) -> dict:
    return {"Authorization": f"Bearer {principal.bearer}"}


async def _data_token(api: httpx.AsyncClient, principal: Principal) -> str:
    response = await api.post(
        "/api/v1/internal/superset/token",
        json={"handle": principal.handle},
        headers={"X-Nova-Superset-Token": os.environ["NOVA_SUPERSET_SERVICE_TOKEN"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


async def _proxy(username: str, password: str, database: str):
    return await asyncmy.connect(
        host=PROXY_HOST,
        port=PROXY_PORT,
        user=username,
        password=password,
        database=database,
        autocommit=True,
        connect_timeout=10,
    )


async def _eventually(check, *, seconds: float = PROPAGATION_SECONDS):
    """Retry ``check`` until it stops raising AssertionError (policy propagation)."""
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        try:
            return await check()
        except AssertionError:
            if asyncio.get_running_loop().time() > deadline:
                raise
            await asyncio.sleep(3)


@pytest.fixture
async def governed():
    from app.integrations.ranger.client import ranger_client
    from app.integrations.ranger.compiler import compile_mask_policy
    from app.integrations.ranger.schemas import RangerPolicy, RangerRole, RangerRoleMember
    from app.modules.access_control.security_context import SecurityContext
    from app.modules.access_control.service import access_control_service
    from app.modules.superset.access import author_roles, replace_author_roles

    await db.init_system_pool()
    suffix = uuid4().hex[:10]
    database = f"bi_accept_{suffix}"
    role_a, role_b = f"BI_A_{suffix}", f"BI_B_{suffix}"
    u1 = Principal(f"bi_u1_{suffix}", secrets.token_urlsafe(18) + "!aA1")
    u2 = Principal(f"bi_u2_{suffix}", secrets.token_urlsafe(18) + "!aA1")
    service = settings.RANGER_SERVICE_NAME
    cleanup: list = []

    async def policy(body: dict) -> None:
        created = await ranger_client.put_policy(RangerPolicy.model_validate(body))
        cleanup.append(lambda: ranger_client.delete_policy(created["id"]))

    def table(name: str, column: str = "*") -> dict:
        return {
            "catalog": {"values": ["default_catalog"]},
            "database": {"values": [database]},
            "table": {"values": [name]},
            "column": {"values": [column]},
        }

    try:
        for principal in (u1, u2):
            await ranger_client.put_user_attributes(principal.username, {})
        await ranger_client.put_role(
            RangerRole(name=role_a, users=[RangerRoleMember(name=u1.username)])
        )
        cleanup.append(lambda: ranger_client.delete_role(role_a))
        await ranger_client.put_role(
            RangerRole(
                name=role_b,
                users=[RangerRoleMember(name=u1.username), RangerRoleMember(name=u2.username)],
            )
        )
        cleanup.append(lambda: ranger_client.delete_role(role_b))
        await policy(
            {
                "name": f"bi-acceptance/{suffix}/catalog",
                "service": service,
                "resources": {"catalog": {"values": ["default_catalog"]}},
                "policyItems": [{"roles": [role_a, role_b], "accesses": [{"type": "usage"}]}],
            }
        )
        await policy(
            {
                "name": f"bi-acceptance/{suffix}/orders",
                "service": service,
                "resources": table("orders"),
                "policyItems": [{"roles": [role_a, role_b], "accesses": [{"type": "select"}]}],
            }
        )
        await policy(
            {
                "name": f"bi-acceptance/{suffix}/secrets",
                "service": service,
                "resources": table("secrets"),
                "policyItems": [{"roles": [role_b], "accesses": [{"type": "select"}]}],
            }
        )
        await policy(
            {
                "name": f"bi-acceptance/{suffix}/orders-east",
                "service": service,
                "policyType": 2,
                "resources": {k: v for k, v in table("orders").items() if k != "column"},
                "rowFilterPolicyItems": [
                    {
                        "roles": [role_a],
                        "accesses": [{"type": "select"}],
                        "rowFilterInfo": {"filterExpr": "region = 'EAST'"},
                    }
                ],
            }
        )
        mask = compile_mask_policy(
            role=role_a,
            catalog="default_catalog",
            database=database,
            table="orders",
            column="email",
            mask_type="MASK_NULL",
        ).to_api()
        mask["name"] = f"bi-acceptance/{suffix}/orders-email"
        await policy(mask)

        await db.execute_system(f"CREATE DATABASE `{database}`")
        cleanup.append(lambda: db.execute_system(f"DROP DATABASE IF EXISTS `{database}` FORCE"))
        await db.execute_system(
            f"CREATE TABLE `{database}`.orders (id INT, region VARCHAR(16), "
            "email VARCHAR(64), amount INT) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) "
            'BUCKETS 1 PROPERTIES("replication_num"="1")'
        )
        await db.execute_system(
            f"INSERT INTO `{database}`.orders VALUES "
            "(1,'EAST','a@example.com',10),(2,'WEST','b@example.com',20),"
            "(3,'EAST','c@example.com',30)"
        )
        await db.execute_system(
            f"CREATE TABLE `{database}`.secrets (id INT) DUPLICATE KEY(id) "
            'DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1")'
        )
        await db.execute_system(f"INSERT INTO `{database}`.secrets VALUES (42)")

        for role in (role_a, role_b):
            await db.execute_system(f"CREATE ROLE `{role}`")
            cleanup.append(lambda role=role: db.execute_system(f"DROP ROLE IF EXISTS `{role}`"))
        for principal in (u1, u2):
            await db.execute_system(
                f"CREATE USER '{principal.username}' IDENTIFIED BY %s", [principal.password]
            )
            cleanup.append(
                lambda p=principal: db.execute_system(f"DROP USER IF EXISTS '{p.username}'")
            )
        await db.execute_system(f"GRANT `{role_a}` TO USER '{u1.username}'")
        await db.execute_system(f"GRANT `{role_b}` TO USER '{u1.username}'")
        await db.execute_system(f"GRANT `{role_b}` TO USER '{u2.username}'")
        await db.execute_system(f"SET DEFAULT ROLE `{role_a}` TO '{u1.username}'")
        await db.execute_system(f"SET DEFAULT ROLE `{role_b}` TO '{u2.username}'")
        # Make both roles BI authors the way PUT /superset/author-roles does:
        # the managed reflection policy and the author list change together.
        previous_authors = sorted(await author_roles(fresh=True))
        admin = SecurityContext(principal="bi-acceptance", active_role="ACCOUNTADMIN")

        async def restore_authors():
            await access_control_service.put_bi_metadata_access(admin, previous_authors)
            await replace_author_roles(previous_authors, changed_by="acceptance")

        cleanup.append(restore_authors)
        authors = {*previous_authors, role_a, role_b}

        async def set_authors(role: str, *, author: bool) -> None:
            authors.add(role) if author else authors.discard(role)
            await access_control_service.put_bi_metadata_access(admin, sorted(authors))
            await replace_author_roles(sorted(authors), changed_by="acceptance")

        await set_authors(role_a, author=True)
        yield SimpleNamespace(
            database=database,
            role_a=role_a,
            role_b=role_b,
            u1=u1,
            u2=u2,
            ranger=ranger_client,
            RangerRole=RangerRole,
            RangerRoleMember=RangerRoleMember,
            set_authors=set_authors,
        )
    finally:
        failures = []
        for undo in reversed(cleanup):
            try:
                await undo()
            except Exception as exc:  # noqa: BLE001 - every undo must still run
                failures.append(exc)
        await db.close_system_pool()
        if failures:
            raise ExceptionGroup("superset acceptance cleanup failed", failures)


async def test_superset_phase_1_acceptance(governed):
    g = governed
    issued: list[str] = []
    async with httpx.AsyncClient(base_url=API_URL, timeout=60) as api:
        # Signed out: Superset sends pages to Nova and refuses its own sign-in.
        async with httpx.AsyncClient(base_url=BI_URL, timeout=60) as anonymous:
            page = await anonymous.get("/bi/superset/welcome/")
            assert page.status_code == 302
            assert page.headers["location"].endswith("/bi-launch?next=/bi/superset/welcome/")
            assert (await anonymous.get("/bi/api/v1/chart/")).status_code == 401
            for path in ("/bi/register/", "/bi/api/v1/security/login"):
                assert (await anonymous.post(path)).status_code == 404

        await _login(api, g.u1)
        await _login(api, g.u2)
        bi1, bi2 = Bi(g.u1.handle), Bi(g.u2.handle)
        try:
            orders = "SELECT id, region, email FROM orders ORDER BY id"

            # Two principals, two active roles, two governed results.
            async def u1_sees_east_masked():
                rows = _rows(await bi1.sql(orders, g.database))
                assert [r["id"] for r in rows] == [1, 3], rows
                assert all(r["email"] is None for r in rows), rows

            async def u2_sees_everything():
                rows = _rows(await bi2.sql(orders, g.database))
                assert [r["id"] for r in rows] == [1, 2, 3], rows
                assert rows[0]["email"] == "a@example.com", rows

            await _eventually(u1_sees_east_masked)
            await _eventually(u2_sees_everything)

            # The same chart, viewed by each principal, returns each one's rows.
            created = await bi1.http.post(
                "/bi/api/v1/dataset/",
                headers=await bi1.headers(),
                json={
                    "database": await bi1.database_id(),
                    "catalog": "default_catalog",
                    "schema": g.database,
                    "table_name": "orders",
                },
            )
            assert created.status_code == 201, created.text
            dataset_id = created.json()["id"]
            try:
                east = await _chart_rows(bi1, dataset_id)
                assert [r["id"] for r in east] == [1, 3]
                assert all(r["email"] is None for r in east)
                everything = await _chart_rows(bi2, dataset_id)
                assert [r["id"] for r in everything] == [1, 2, 3]
                assert everything[1]["email"] == "b@example.com"
            finally:
                await bi1.http.delete(
                    f"/bi/api/v1/dataset/{dataset_id}", headers=await bi1.headers()
                )

            # An assigned but inactive role grants nothing.
            refused = await bi1.sql("SELECT id FROM secrets", g.database)
            assert refused.status_code != 200 or refused.json().get("status") != "success"
            assert (_rows(await bi2.sql("SELECT id FROM secrets", g.database)))[0]["id"] == 42

            # Superset holds no data policy or connection power of its own.
            headers = await bi1.headers()
            created = await bi1.http.post(
                "/bi/api/v1/database/",
                headers=headers,
                json={"database_name": "x", "sqlalchemy_uri": "mysql://root@x/y"},
            )
            assert created.status_code in (401, 403, 404), created.text
            edited = await bi1.http.put(
                f"/bi/api/v1/database/{await bi1.database_id()}",
                headers=headers,
                json={"sqlalchemy_uri": "mysql://root@x/y"},
            )
            assert edited.status_code in (401, 403, 404), edited.text
            rls = await bi1.http.get("/bi/api/v1/rowlevelsecurity/")
            assert rls.status_code in (401, 403, 404), rls.text

            # ACCOUNTADMIN stays protected: Superset refuses DDL, the guard refuses it too.
            ddl = await bi1.sql("DROP ROLE ACCOUNTADMIN", g.database)
            assert ddl.status_code != 200 or ddl.json().get("status") != "success"
            token = await _data_token(api, g.u1)
            issued.append(token)
            connection = await _proxy(g.u1.username, token, g.database)
            try:
                async with connection.cursor() as cursor:
                    with pytest.raises(asyncmy.errors.MySQLError):
                        await cursor.execute("DROP ROLE ACCOUNTADMIN")
                    with pytest.raises(asyncmy.errors.MySQLError):
                        await cursor.execute(f"SET ROLE {g.role_b}")
                    await cursor.execute(orders)
                    assert [row[0] for row in await cursor.fetchall()] == [1, 3]
            finally:
                connection.close()

            # Every Superset query is audited with the real principal and role.
            async def audited():
                result = await db.execute_system(
                    "SELECT COUNT(*) FROM NOVA_SYSTEM.AUDIT_LOG WHERE user_name = %s "
                    "AND active_role = %s AND sql_text LIKE %s "
                    "AND event_time > DATE_SUB(NOW(), INTERVAL 1 HOUR)",
                    [g.u1.username, g.role_a, "%orders%"],
                )
                assert int(result["rows"][0][0]) > 0

            await _eventually(audited, seconds=60)

            # A role switch takes effect on the next query and retires old tokens.
            open_before = await _proxy(g.u1.username, token, g.database)
            switched = await api.post(
                "/api/v1/auth/switch-role", json={"role": g.role_b}, headers=_auth(g.u1)
            )
            assert switched.status_code == 200, switched.text
            rows = _rows(await bi1.sql(orders, g.database))
            assert [r["id"] for r in rows] == [1, 2, 3]
            assert rows[1]["email"] == "b@example.com"
            with pytest.raises(asyncmy.errors.OperationalError) as stale:
                await _proxy(g.u1.username, token, g.database)
            assert stale.value.args[0] == 1045
            try:
                async with open_before.cursor() as cursor:
                    with pytest.raises(asyncmy.errors.MySQLError):
                        await cursor.execute("SELECT 1")
            finally:
                with suppress(Exception):
                    open_before.close()
            issued.append(await _data_token(api, g.u1))

            # The Superset role follows the active role on every request.
            await g.set_authors(g.role_b, author=False)
            await asyncio.sleep(16)  # author mapping is cached per process for 15 s
            viewer = await bi2.sql(orders, g.database)
            assert viewer.status_code in (401, 403), viewer.text

            # Revoking the role assignment refuses the next query.
            await g.set_authors(g.role_b, author=True)
            await asyncio.sleep(16)
            assert [r["id"] for r in _rows(await bi2.sql(orders, g.database))] == [1, 2, 3]
            await db.execute_system(f"REVOKE `{g.role_b}` FROM USER '{g.u2.username}'")
            await g.ranger.put_role(
                g.RangerRole(name=g.role_b, users=[g.RangerRoleMember(name=g.u1.username)])
            )
            revoked = await bi2.sql(orders, g.database)
            assert revoked.status_code != 200 or revoked.json().get("status") != "success"

            # Signing out of Nova ends the BI session.
            assert (await api.post("/api/v1/auth/logout", headers=_auth(g.u1))).status_code == 200
            after = await bi1.http.get("/bi/api/v1/chart/")
            assert after.status_code == 401
        finally:
            await bi1.close()
            await bi2.close()

    # No issued secret or password appears in the service logs.
    containers = [c for c in os.getenv("NOVA_SUPERSET_LOG_CONTAINERS", "").split(",") if c]
    secrets_to_find = [*issued, g.u1.password, g.u2.password, g.u1.handle, g.u2.handle]
    for container in containers:
        logs = subprocess.run(
            ["docker", "logs", "--since", "30m", container],
            capture_output=True,
            text=True,
            check=True,
        )
        text = logs.stdout + logs.stderr
        leaked = [index for index, value in enumerate(secrets_to_find) if value in text]
        assert not leaked, f"{container} logs contain issued secrets #{leaked}"

    # Superset's metadata holds no usable credential: no mirror-user password
    # and no stored database password.
    metadata = os.getenv("NOVA_SUPERSET_DB_CONTAINER")
    if metadata:
        query = (
            "SELECT count(*) FROM ab_user WHERE username IN "
            f"('{g.u1.username}', '{g.u2.username}') AND password IS NOT NULL; "
            "SELECT count(*) FROM dbs WHERE password IS NOT NULL OR encrypted_extra IS NOT NULL;"
        )
        result = subprocess.run(
            ["docker", "exec", metadata, "psql", "-U", "superset", "-d", "superset", "-tAc", query],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.split() == ["0", "0"], result.stdout
        # The principals are gone from StarRocks; retire their mirror rows too.
        subprocess.run(
            [
                "docker",
                "exec",
                metadata,
                "psql",
                "-U",
                "superset",
                "-d",
                "superset",
                "-c",
                "UPDATE ab_user SET active = false WHERE username IN "
                f"('{g.u1.username}', '{g.u2.username}')",
            ],
            capture_output=True,
            check=True,
        )


STUDIO = "/api/v1/agents/studio/artifacts"
BY_REGION = "SELECT region, SUM(amount) AS total FROM orders GROUP BY region"
BAR = {
    "mark": "bar",
    "encoding": {
        "x": {"field": "region", "type": "nominal"},
        "y": {"field": "total", "type": "quantitative"},
    },
}


async def _chart_regions(bi: Bi, chart_id: int) -> dict[str, float]:
    """The chart's own stored query, run as ``bi``'s principal."""
    response = await bi.http.get(f"/bi/api/v1/chart/{chart_id}/data/")
    assert response.status_code == 200, response.text
    rows = response.json()["result"][0]["data"]
    return {row["region"]: next(v for k, v in row.items() if k != "region") for row in rows}


async def _audited(user: str, event: str, action: str) -> None:
    result = await db.execute_system(
        "SELECT COUNT(*) FROM NOVA_SYSTEM.AUDIT_LOG WHERE user_name = %s AND event_type = %s "
        "AND action = %s AND event_time > DATE_SUB(NOW(), INTERVAL 1 HOUR)",
        [user, event, action],
    )
    assert int(result["rows"][0][0]) > 0, (user, event, action)


async def test_superset_phases_3_and_4_acceptance(governed, monkeypatch):
    """Studio artifacts are Superset objects, built by Nova as the user."""
    from jose import jwt

    from app.core.redis import session_store
    from app.modules.agents.artifact_repository import artifact_repository
    from app.modules.agents.tools.dashboard_build import dashboard_build_tool
    from app.modules.assistant.service import LoopContext
    from app.modules.assistant.tools import ToolInvocation

    g = governed
    created: list[str] = []
    async with httpx.AsyncClient(base_url=API_URL, timeout=120) as api:
        await _login(api, g.u1)
        await _login(api, g.u2)
        bi1, bi2 = Bi(g.u1.handle), Bi(g.u2.handle)
        try:
            # Wait for the fixture's policies, then save a result as a BI chart.
            async def chart_saved():
                response = await api.post(
                    STUDIO,
                    headers=_auth(g.u1),
                    json={
                        "title": "Revenue by region",
                        "artifact_type": "chart",
                        "sql_text": BY_REGION,
                        "database_name": g.database,
                        "chart_spec": BAR,
                    },
                )
                assert response.status_code == 201, response.text
                return response.json()

            chart = await _eventually(chart_saved)
            created.append(chart["artifact_id"])
            assert chart["bi_kind"] == "chart" and chart["bi_object_id"]
            assert chart["bi_view_path"] == (
                f"/bi/explore/?slice_id={chart['bi_object_id']}&standalone=1"
            )
            assert chart["viz_spec"] is None and chart["chart_spec"] is None
            described = (await bi1.http.get(f"/bi/api/v1/chart/{chart['bi_object_id']}")).json()
            assert described["result"]["viz_type"] == "echarts_timeseries_bar"

            # The same chart returns each principal's governed rows.
            assert set(await _chart_regions(bi1, chart["bi_object_id"])) == {"EAST"}
            assert set(await _chart_regions(bi2, chart["bi_object_id"])) == {"EAST", "WEST"}

            # A dashboard artifact places BI charts the owner has.
            dashboard = await api.post(
                STUDIO,
                headers=_auth(g.u1),
                json={
                    "title": "Regional view",
                    "artifact_type": "dashboard",
                    "viz_spec": {
                        "version": 1,
                        "zones": [
                            {
                                "zone_id": "z0",
                                "kind": "sheet",
                                "artifact_id": chart["artifact_id"],
                                "rect": {"x": 0, "y": 0, "w": 24, "h": 2},
                            }
                        ],
                    },
                },
            )
            assert dashboard.status_code == 201, dashboard.text
            created.append(dashboard.json()["artifact_id"])
            dashboard_id = dashboard.json()["bi_object_id"]
            placed = await bi1.http.get(f"/bi/api/v1/dashboard/{dashboard_id}/charts")
            assert [c["id"] for c in placed.json()["result"]] == [chart["bi_object_id"]]

            # The agent tool builds BI charts and a BI dashboard as the user.
            await session_store.init()
            monkeypatch.setattr(settings, "SUPERSET_INTERNAL_URL", BI_URL)
            claims = jwt.get_unverified_claims(g.u1.bearer)
            context = LoopContext(user_name=g.u1.username)
            context.user = {
                "username": g.u1.username,
                "session_id": claims["sid"],
                "roles": [g.role_a, g.role_b],
                "active_role": g.role_a,
            }
            context.database = g.database
            context.last_result = {
                "columns": ["region", "total"],
                "rows": [["EAST", 40]],
                "sql": BY_REGION,
            }
            outcome = await dashboard_build_tool.run(
                ToolInvocation(
                    "call-1",
                    "dashboard_build",
                    {
                        "title": "Agent dashboard",
                        "charts": [
                            {"title": "Bars", "chart_type": "bar", "x": "region", "y": "total"},
                            {"title": "Share", "chart_type": "pie", "x": "region", "y": "total"},
                        ],
                    },
                ),
                context,
            )
            assert outcome.ok, outcome.error
            created.append(outcome.data["artifact_id"])
            created.extend(item["artifact_id"] for item in outcome.data["charts"])
            built = await artifact_repository.get(
                outcome.data["artifact_id"], owner_name=g.u1.username
            )
            assert built["bi_kind"] == "dashboard"
            agent_charts = await bi1.http.get(
                f"/bi/api/v1/dashboard/{built['bi_object_id']}/charts"
            )
            assert sorted(c["form_data"]["viz_type"] for c in agent_charts.json()["result"]) == [
                "echarts_timeseries_bar",
                "pie",
            ]

            # Applying a reviewed edit changes the dataset SQL behind the chart.
            current = (
                await api.get(f"{STUDIO}/{chart['artifact_id']}", headers=_auth(g.u1))
            ).json()
            applied = await api.patch(
                f"{STUDIO}/{chart['artifact_id']}",
                headers=_auth(g.u1),
                json={
                    "draft": {
                        "sql_text": BY_REGION.replace("SUM(amount)", "MAX(amount)"),
                        "artifact_type": "chart",
                    },
                    "expected_updated_at": current["updated_at"],
                },
            )
            assert applied.status_code == 200, applied.text
            assert (await _chart_regions(bi2, chart["bi_object_id"])) == {"EAST": 30, "WEST": 20}

            # The worksheet editor is for pre-BI artifacts only.
            legacy = await api.patch(
                f"{STUDIO}/{chart['artifact_id']}/spec",
                headers=_auth(g.u1),
                json={
                    "viz_spec": {},
                    "expected_updated_at": applied.json()["artifact"]["updated_at"],
                },
            )
            assert legacy.status_code == 409

            # Exports are audited; bundle imports do not exist (S8).
            exported = await bi1.http.get(
                f"/bi/api/v1/chart/export/?q=!({chart['bi_object_id']})",
                headers=await bi1.headers(),
            )
            assert exported.status_code == 200, exported.text
            for kind in ("dashboard", "chart", "dataset", "database", "assets"):
                imported = await bi1.http.post(
                    f"/bi/api/v1/{kind}/import/", headers=await bi1.headers()
                )
                assert imported.status_code == 404, (kind, imported.status_code)

            # Only Nova may frame BI (S14).
            page = await bi1.http.get("/bi/superset/welcome/")
            assert "frame-ancestors 'self'" in page.headers.get("content-security-policy", "")
            assert page.headers.get("x-frame-options", "").upper() == "SAMEORIGIN"

            async def actions_audited():
                await _audited(g.u1.username, "bi_artifact", "CREATE_BI_CHART")
                await _audited(g.u1.username, "bi_artifact", "CREATE_BI_DASHBOARD")
                await _audited(g.u1.username, "bi_artifact", "UPDATE_BI_CHART")
                await _audited(g.u1.username, "bi_action", "BI_CHANGE")
                await _audited(g.u1.username, "bi_action", "BI_EXPORT")

            await _eventually(actions_audited, seconds=60)

            # A role that is not a BI author cannot save BI artifacts.
            await g.set_authors(g.role_b, author=False)
            await asyncio.sleep(16)
            refused = await api.post(
                STUDIO,
                headers=_auth(g.u2),
                json={
                    "title": "x",
                    "artifact_type": "chart",
                    "sql_text": BY_REGION,
                    "database_name": g.database,
                },
            )
            assert refused.status_code == 403, refused.text

            # Deleting an artifact removes its Superset chart.
            removed = await api.delete(f"{STUDIO}/{chart['artifact_id']}", headers=_auth(g.u1))
            assert removed.status_code == 204
            created.remove(chart["artifact_id"])
            gone = await bi1.http.get(f"/bi/api/v1/chart/{chart['bi_object_id']}")
            assert gone.status_code == 404
        finally:
            for artifact_id in created:
                with suppress(Exception):
                    await api.delete(f"{STUDIO}/{artifact_id}", headers=_auth(g.u1))
            await bi1.close()
            await bi2.close()
            with suppress(Exception):
                await session_store.close()


SERVICE = {"X-Nova-Superset-Token": os.getenv("NOVA_SUPERSET_SERVICE_TOKEN", "")}
WORKER_CONTAINER = os.getenv("NOVA_SUPERSET_WORKER_CONTAINER", "")


async def _delegate(api: httpx.AsyncClient, username: str) -> httpx.Response:
    return await api.post(
        "/api/v1/internal/superset/delegate", json={"username": username}, headers=SERVICE
    )


async def _impersonation(owner: str, *, granted: bool) -> None:
    """Grant or revoke the worker's scoped impersonation of ``owner``."""
    from app.integrations.ranger.client import ranger_client
    from scripts.provision_task_worker_access import provision, scoped_policy

    if granted:
        await provision(owner)
        return
    existing = await ranger_client.get_policy(scoped_policy(owner).name)
    if existing:
        await ranger_client.delete_policy(existing["id"])


async def _chart_data(bi: Bi, chart_id: int) -> tuple[set[str], bool]:
    response = await bi.http.get(f"/bi/api/v1/chart/{chart_id}/data/")
    assert response.status_code == 200, response.text
    result = response.json()["result"][0]
    return {row["region"] for row in result["data"]}, bool(result.get("is_cached"))


async def _async_sql(bi: Bi, sql: str, schema: str) -> dict:
    response = await bi.http.post(
        "/bi/api/v1/sqllab/execute/",
        headers=await bi.headers(),
        json={
            "database_id": await bi.database_id(),
            "sql": sql,
            "schema": schema,
            "catalog": "default_catalog",
            "runAsync": True,
            "client_id": secrets.token_hex(5),
            "queryLimit": 1000,
        },
    )
    assert response.status_code in (200, 202), response.text
    return response.json()["query"]


async def _finished_query(bi: Bi, client_id: str, seconds: float = 120) -> dict:
    """Poll SQL Lab's own update feed until the async query leaves the queue."""
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        feed = await bi.http.get(
            "/bi/api/v1/query/updated_since", params={"q": "(last_updated_ms:0)"}
        )
        rows = [row for row in feed.json().get("result", []) if row["id"] == client_id]
        if rows and rows[0]["state"] in ("success", "failed", "stopped", "timed_out"):
            return rows[0]
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"async query {client_id} did not finish: {rows}")
        await asyncio.sleep(2)


@pytest.mark.skipif(not WORKER_CONTAINER, reason="Set NOVA_SUPERSET_WORKER_CONTAINER")
async def test_superset_phase_2_acceptance(governed):
    """Background work, caches and async SQL Lab under Nova's identity."""
    g = governed
    owner = g.u1.username
    created: list[str] = []
    report_id = None
    async with httpx.AsyncClient(base_url=API_URL, timeout=180) as api:
        await _login(api, g.u1)
        await _login(api, g.u2)
        bi1, bi2 = Bi(g.u1.handle), Bi(g.u2.handle)
        try:
            # A BI chart the alert, the cache and thumbnail checks use (once the
            # fixture's policies have reached the engine).
            async def chart_saved():
                response = await api.post(
                    STUDIO,
                    headers=_auth(g.u1),
                    json={
                        "title": "Cached regions",
                        "artifact_type": "chart",
                        "sql_text": BY_REGION,
                        "database_name": g.database,
                        "chart_spec": BAR,
                    },
                )
                assert response.status_code == 201, response.text
                return response.json()

            chart = await _eventually(chart_saved)
            created.append(chart["artifact_id"])
            chart_id = chart["bi_object_id"]
            # Delegation is refused for privileged, unknown and ungranted owners.
            for refused in ("root", "nova_admin", "bi_nobody_" + secrets.token_hex(3), owner):
                assert (await _delegate(api, refused)).status_code == 403, refused

            # Granted per owner, it runs as the owner under their default role.
            await _impersonation(owner, granted=True)
            try:

                async def delegated():
                    response = await _delegate(api, owner)
                    assert response.status_code == 200, response.text
                    return response.json()

                grant = await _eventually(delegated)
                assert grant["active_role"] == g.role_a
                token = (
                    await api.post(
                        "/api/v1/internal/superset/token",
                        json={"handle": grant["handle"]},
                        headers=SERVICE,
                    )
                ).json()["token"]
                connection = await _proxy(owner, token, g.database)
                try:
                    async with connection.cursor() as cursor:
                        await cursor.execute("SELECT region FROM orders ORDER BY id")
                        assert {row[0] for row in await cursor.fetchall()} == {"EAST"}
                        with pytest.raises(asyncmy.errors.MySQLError):
                            await cursor.execute(f"SET ROLE {g.role_b}")
                finally:
                    connection.close()

                # A scheduled alert runs in the worker as its owner.
                me = (await bi1.http.get("/bi/api/v1/me/")).json()["result"]
                report = await bi1.http.post(
                    "/bi/api/v1/report/",
                    headers=await bi1.headers(),
                    json={
                        "type": "Alert",
                        "name": f"bi acceptance {secrets.token_hex(3)}",
                        "chart": chart_id,
                        "report_format": "TEXT",
                        "crontab": "* * * * *",
                        "database": await bi1.database_id(),
                        "sql": f"SELECT COUNT(*) FROM {g.database}.orders",
                        "validator_type": "operator",
                        "validator_config_json": {"op": ">", "threshold": 0},
                        "recipients": [
                            {
                                "type": "Email",
                                "recipient_config_json": {"target": "nobody@example.invalid"},
                            }
                        ],
                        "owners": [me["id"]],
                        "active": True,
                        "grace_period": 1,
                        "working_timeout": 300,
                        "log_retention": 1,
                    },
                )
                assert report.status_code == 201, report.text
                report_id = report.json()["id"]

                async def alert_ran():
                    logs = await bi1.http.get(f"/bi/api/v1/report/{report_id}/log/")
                    rows = [
                        row for row in logs.json().get("result", []) if row.get("value") is not None
                    ]
                    assert rows, logs.text
                    return rows[0]

                run = await _eventually(alert_ran, seconds=240)
                # The owner's default role sees only the EAST rows.
                assert run["value"] == 2.0, run
            finally:
                await _impersonation(owner, granted=False)

            async def revoked():
                assert (await _delegate(api, owner)).status_code == 403

            await _eventually(revoked)

            # Cached chart data never crosses principals or roles.
            # Saving already ran the chart for this session, so it is cached for
            # it; the other principal still gets their own rows.
            assert await _chart_data(bi1, chart_id) == ({"EAST"}, True)
            assert (await _chart_data(bi2, chart_id))[0] == {"EAST", "WEST"}
            thumb_a = (await bi1.http.get(f"/bi/api/v1/chart/{chart_id}")).json()["result"]
            switched = await api.post(
                "/api/v1/auth/switch-role", json={"role": g.role_b}, headers=_auth(g.u1)
            )
            assert switched.status_code == 200, switched.text
            assert (await _chart_data(bi1, chart_id))[0] == {"EAST", "WEST"}

            # Thumbnails are drawn for, and keyed by, the requesting session.
            listing = await bi1.http.get(
                "/bi/api/v1/chart/",
                params={
                    "q": (
                        f"(filters:!((col:id,opr:eq,value:{chart_id})),columns:!(id,thumbnail_url))"
                    )
                },
            )
            thumbnail_url = listing.json()["result"][0]["thumbnail_url"]
            assert thumbnail_url and thumbnail_url != thumb_a.get("thumbnail_url")

            async def thumbnail_drawn():
                path = thumbnail_url if thumbnail_url.startswith("/bi/") else f"/bi{thumbnail_url}"
                image = await bi1.http.get(path)
                assert image.status_code == 200, image.status_code
                assert image.headers["content-type"].startswith("image/")

            await _eventually(thumbnail_drawn, seconds=180)
            switched = await api.post(
                "/api/v1/auth/switch-role", json={"role": g.role_a}, headers=_auth(g.u1)
            )
            assert switched.status_code == 200

            # Async SQL Lab runs in the worker as the submitting session...
            query = await _async_sql(bi1, "SELECT region FROM orders ORDER BY id", g.database)
            finished = await _finished_query(bi1, query["id"])
            assert finished["state"] == "success", finished
            results = await bi1.http.get(
                "/bi/api/v1/sqllab/results/",
                params={"q": f"(key:'{finished['resultsKey']}')"},
            )
            assert {row["region"] for row in results.json()["data"]} == {"EAST"}

            # ...and is refused once that session changed before it ran.
            subprocess.run(["docker", "stop", WORKER_CONTAINER], check=True, capture_output=True)
            try:
                queued = await _async_sql(bi1, "SELECT region FROM orders", g.database)
                switched = await api.post(
                    "/api/v1/auth/switch-role", json={"role": g.role_b}, headers=_auth(g.u1)
                )
                assert switched.status_code == 200
            finally:
                subprocess.run(
                    ["docker", "start", WORKER_CONTAINER], check=True, capture_output=True
                )
            refused = await _finished_query(bi1, queued["id"], seconds=180)
            assert refused["state"] == "failed", refused
            assert "session" in (refused.get("errorMessage") or "").lower(), refused
        finally:
            if report_id is not None:
                with suppress(Exception):
                    await bi1.http.delete(
                        f"/bi/api/v1/report/{report_id}", headers=await bi1.headers()
                    )
            for artifact_id in created:
                with suppress(Exception):
                    await api.delete(f"{STUDIO}/{artifact_id}", headers=_auth(g.u1))
            await bi1.close()
            await bi2.close()
