"""Acceptance: a Studio answer's native chart reads its rows as the viewer.

``POST /agents/studio/dataviz/drafts/{ref}/query`` runs the chart's recorded
statement as the caller under the active role on the patched FE, so Ranger row
filters and column masks apply. A reference replayed by another user, a chart
made under another role, and a role without SELECT are refused.

Every user, role, database and policy is unique to the run and removed
afterwards. No model is called: the chart is built by the real
``data_to_chart`` tool (which inspects the statement as the caller) and
recorded the way the loop records it.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.common.nova_system import init_nova_system
from app.core.config import settings
from app.core.database import db
from app.core.metadata_actor import acting_as
from app.core.redis import session_store
from app.core.security import decode_token
from app.integrations.ranger.client import RangerClient
from app.integrations.ranger.schemas import RangerPolicy
from app.main import create_app
from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.service import access_control_service
from app.modules.agents.tools.data_to_chart import DataToChartTool
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.security import observation_context, session_security
from app.modules.assistant.tools import ToolInvocation
from app.modules.viz.studio_drafts import DraftLocator, sign
from tests.benchmark.business_intelligence.client import StudioClient

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        os.getenv("NOVA_STUDIO_DRAFT_CHARTS_RANGER_ACCEPTANCE") != "1",
        reason="Creates users, roles and Ranger policies on a patched FE; opt in explicitly",
    ),
]

ROWS = [(1, "Jakarta", "Andi", 100), (2, "Jakarta", "Budi", 50),
        (3, "Medan", "Citra", 70), (4, "Bandung", "Dewi", 30)]


async def _session(client: StudioClient) -> dict[str, Any]:
    sid = decode_token(client.http.headers["Authorization"].removeprefix("Bearer "))["sid"]
    return {**(await session_store.get(sid)), "session_id": sid}


async def _record_chart(user: dict[str, Any], sql: str, x: str) -> tuple[str, dict[str, Any]]:
    """Build the chart as ``user`` and record it as the loop does; return its ref."""
    actor = SecurityContext.from_session(user)
    with acting_as(actor):
        thread = await assistant_repository.create_thread(
            user_name=actor.principal, title="Draft charts")
    thread_id = thread["thread_id"]
    producer = {"kind": "tool", "name": "query_execute", "tool_call_id": "q-1",
                "status": "done", "preview": sql}
    context = SimpleNamespace(
        user=user, thread_id=thread_id, steps=[producer], evidence_tables={},
        last_result={"title": "Revenue", "columns": [x, "revenue"], "rows": [], "sql": sql},
    )
    outcome = await DataToChartTool().run(
        ToolInvocation("c-1", "data_to_chart", {"visual": "bar", "x": x, "y": ["revenue"]}),
        context,
    )
    assert outcome.ok, outcome.error
    content_id = f"run-{uuid4().hex[:8]}:1"
    ref = sign(DraftLocator(thread_id, content_id), actor.principal)
    component = outcome.component or {}
    with acting_as(actor):
        await assistant_repository.append_message(
            thread_id, user_name=actor.principal, role="assistant", content="Revenue.",
            steps=[producer, {
                "kind": "component", "content_index": 1, "content_id": content_id,
                "tool_call_id": "c-1", "component": "DatavizChart", "version": 1,
                "props": {**component["props"], "draft_ref": ref},
                "source": component["source"], "plan": component["plan"],
            }],
            security_context=observation_context(session_security(user)),
        )
    return ref, component["props"]["chart"]


def _request(chart: dict[str, Any]) -> dict[str, Any]:
    encoding = chart["encoding"]
    return {"revision": 1, "limit": 100,
            "dimensions": [{"field_id": encoding["dimensions"][0]["field_id"]}],
            "measures": [{"field_id": encoding["measures"][0]["field_id"],
                          "aggregation": "sum"}]}


async def _read(client: StudioClient, ref: str, chart: dict[str, Any]) -> httpx.Response:
    return await client.http.post(f"/api/v1/agents/studio/dataviz/drafts/{ref}/query",
                                  json=_request(chart))


def _values(response: httpx.Response) -> dict[Any, float]:
    assert response.status_code == 200, response.text
    body = response.json()
    return {(row[0] or {}).get("value"): float(row[1]["value"]) for row in body["rows"]}


async def test_draft_charts_read_as_the_viewer_under_ranger():
    await db.init_system_pool()
    await session_store.init()
    await init_nova_system()
    suffix = uuid4().hex[:10]
    database = "nvd_data_" + suffix
    analyst, other_role, admin_role, blind = (f"nvd_{name}_{suffix}"
                                              for name in ("an", "alt", "adm", "none"))
    users = {name: ("nvd_" + name + "_" + suffix, secrets.token_urlsafe(24))
             for name in ("alice", "admin", "blind")}
    operator = SecurityContext(principal="nova_admin", active_role="ACCOUNTADMIN")
    ranger, policies, clients = RangerClient(), [], []
    sql = f"SELECT region, customer, revenue FROM {database}.sales"
    # Every policy here names only this run's database and roles; none denies.
    policies_before = len(await ranger.list_policies())
    try:
        for role in (analyst, other_role, admin_role, blind):
            await access_control_service.create_role(operator, role)
        for key, role, extra in (("alice", analyst, other_role), ("admin", admin_role, None),
                                 ("blind", blind, None)):
            username, password = users[key]
            await db.execute_system(f"CREATE USER '{username}' IDENTIFIED BY %s", [password])
            for granted in filter(None, (role, extra)):
                await access_control_service.assign_role(operator, role=granted,
                                                         username=username)
            await db.execute_system(f"SET DEFAULT ROLE `{role}` TO '{username}'")
        await db.execute_system(f"CREATE DATABASE {database}")
        await db.execute_system(
            f"CREATE TABLE {database}.sales (id BIGINT, region VARCHAR(32), "
            "customer VARCHAR(32), revenue DECIMAL(12,2)) DUPLICATE KEY(id) "
            "DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1')")
        await db.execute_system(
            f"INSERT INTO {database}.sales VALUES " + ",".join(["(%s,%s,%s,%s)"] * len(ROWS)),
            [value for row in ROWS for value in row])
        table = {"catalog": {"values": ["default_catalog"]},
                 "database": {"values": [database]}, "table": {"values": ["sales"]}}
        for policy in (
            {"name": f"nvd/{suffix}/access", "policyType": 0,
             "resources": {**table, "column": {"values": ["*"]}},
             "policyItems": [{"roles": [analyst, other_role, admin_role],
                              "accesses": [{"type": "select", "isAllowed": True}]}]},
            {"name": f"nvd/{suffix}/rows", "policyType": 2, "resources": table,
             "rowFilterPolicyItems": [{
                 "roles": [analyst], "accesses": [{"type": "select", "isAllowed": True}],
                 "rowFilterInfo": {"filterExpr": "region = 'Jakarta'"}}]},
            {"name": f"nvd/{suffix}/mask", "policyType": 1,
             "resources": {**table, "column": {"values": ["customer"]}},
             "dataMaskPolicyItems": [{
                 "roles": [analyst], "accesses": [{"type": "select", "isAllowed": True}],
                 "dataMaskInfo": {"dataMaskType": "MASK_NULL"}}]},
        ):
            policies.append(await ranger.put_policy(RangerPolicy.model_validate(
                {"service": settings.RANGER_SERVICE_NAME, **policy})))

        application = create_app()

        async def login(key: str, role: str) -> StudioClient:
            client = StudioClient(httpx.AsyncClient(
                transport=httpx.ASGITransport(application), base_url="http://drafts.test",
                timeout=120))
            clients.append(client)
            await client.login(*users[key], role)
            return client

        alice, admin, blind_client = (await login("alice", analyst),
                                      await login("admin", admin_role),
                                      await login("blind", blind))
        alice_user, admin_user = await _session(alice), await _session(admin)

        # Ranger syncs new policies to the FE after a delay; wait for the filter.
        async with asyncio.timeout(120):
            while True:
                try:
                    by_region_ref, by_region = await _record_chart(alice_user, sql, "region")
                    filtered = await _read(alice, by_region_ref, by_region)
                    if filtered.status_code == 200 and set(_values(filtered)) == {"Jakarta"}:
                        break
                except AssertionError:
                    pass
                await asyncio.sleep(2)

        # Row filter: alice sees only Jakarta; the admin role sees every region.
        assert _values(filtered) == {"Jakarta": 150.0}
        admin_ref, admin_chart = await _record_chart(admin_user, sql, "region")
        assert _values(await _read(admin, admin_ref, admin_chart)) == {
            "Jakarta": 150.0, "Medan": 70.0, "Bandung": 30.0}

        # Column mask: alice's customer names are hidden; the admin's are not.
        by_customer_ref, by_customer = await _record_chart(alice_user, sql, "customer")
        assert set(_values(await _read(alice, by_customer_ref, by_customer))) == {None}
        admin_customer_ref, admin_customer = await _record_chart(admin_user, sql, "customer")
        assert set(_values(await _read(admin, admin_customer_ref, admin_customer))) == {
            "Andi", "Budi", "Citra", "Dewi"}

        # A reference replayed by another user is refused, even one who may read the data.
        replayed = await _read(admin, by_region_ref, by_region)
        assert replayed.status_code == 404
        assert "SELECT" not in replayed.text and "Jakarta" not in replayed.text

        # A role without SELECT is refused by the engine, not by a cached result.
        blind_user = await _session(blind_client)
        actor = SecurityContext.from_session(blind_user)
        with acting_as(actor):
            thread = await assistant_repository.create_thread(user_name=actor.principal)
        content_id = "run-blind:1"
        blind_ref = sign(DraftLocator(thread["thread_id"], content_id), actor.principal)
        recorded = _record_source(sql)
        with acting_as(actor):
            await assistant_repository.append_message(
                thread["thread_id"], user_name=actor.principal, role="assistant",
                content="Revenue.", steps=[
                    {"kind": "tool", "name": "query_execute", "tool_call_id": "q-1",
                     "status": "done", "preview": sql},
                    {"kind": "component", "content_index": 1, "content_id": content_id,
                     "component": "DatavizChart", "version": 1,
                     "props": {"title": "Revenue", "chart": by_region, "draft_ref": blind_ref},
                     "source": recorded},
                ],
                security_context=observation_context(session_security(blind_user)),
            )
        denied = await _read(blind_client, blind_ref, by_region)
        assert denied.status_code in (403, 404), denied.text
        assert "Jakarta" not in denied.text

        # After a role switch, a chart made under the earlier role is refused.
        switched = await alice.http.post("/api/v1/auth/switch-role", json={"role": other_role})
        assert switched.status_code == 200, switched.text
        after_switch = await _read(alice, by_region_ref, by_region)
        assert after_switch.status_code == 409
        assert "another role" in after_switch.json()["detail"]

        # Revoking the role's access refuses the next read; nothing is served from a cache.
        back = await alice.http.post("/api/v1/auth/switch-role", json={"role": analyst})
        assert back.status_code == 200, back.text
        alice_user = await _session(alice)
        fresh_ref, fresh = await _record_chart(alice_user, sql, "region")
        assert _values(await _read(alice, fresh_ref, fresh)) == {"Jakarta": 150.0}
        await ranger.delete_policy(policies.pop(0)["id"])
        async with asyncio.timeout(120):
            while (await _read(alice, fresh_ref, fresh)).status_code == 200:
                await asyncio.sleep(2)
        revoked = await _read(alice, fresh_ref, fresh)
        assert revoked.status_code in (403, 404, 422), revoked.text
        assert "Jakarta" not in revoked.text
    finally:
        original_error = sys.exc_info()[0] is not None
        errors: list[str] = []

        async def clean(operation):
            try:
                await operation
            except Exception as exc:  # noqa: BLE001 - every cleanup step runs
                errors.append(type(exc).__name__)

        for client in clients:
            token = client.http.headers.get("Authorization", "").removeprefix("Bearer ")
            if token:
                await clean(session_store.delete(decode_token(token)["sid"]))
            await clean(client.http.aclose())
        for policy in reversed(policies):
            await clean(ranger.delete_policy(policy["id"]))
        await clean(db.execute_system(f"DROP DATABASE IF EXISTS {database}"))
        for username, _ in users.values():
            await clean(db.execute_system(f"DROP USER IF EXISTS '{username}'"))
        for role in (analyst, other_role, admin_role, blind):
            await clean(access_control_service.drop_role(operator, role))
        try:
            if len(await ranger.list_policies()) != policies_before:
                errors.append("Ranger policy count changed")
        except Exception as exc:  # noqa: BLE001 - reported with the other cleanup errors
            errors.append(type(exc).__name__)
        await clean(session_store.close())
        await clean(db.close_system_pool())
        if errors and not original_error:
            pytest.fail(f"Acceptance cleanup failed: {errors}")


def _record_source(sql: str) -> dict[str, Any]:
    from app.modules.viz.studio_drafts import source_record

    return source_record(evidence_id=None, sql=sql, database=sql.split()[-1].split(".")[0],
                         producer_tool_call_id="q-1")
