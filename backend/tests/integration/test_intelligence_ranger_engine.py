"""Opt-in lifecycle acceptance on an isolated patched FE and Ranger installation."""

import asyncio
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.common.news_entitlement import NEWS_ENABLED
from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.integrations.ranger.client import RangerClient
from app.integrations.ranger.schemas import RangerPolicy
from app.main import create_app
from app.modules.agents.semantic.serialize import to_ossie_document
from tests.benchmark.business_intelligence.client import StudioClient
from tests.benchmark.business_intelligence.model import expression, metric

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        os.getenv("NOVA_INTELLIGENCE_RANGER_ACCEPTANCE") != "1",
        reason="Requires an isolated patched-FE Ranger acceptance installation",
    ),
]


async def browser_checkpoint(stage, **resources):
    if os.getenv("NOVA_INTELLIGENCE_BROWSER_ACCEPTANCE") != "1":
        return
    checkpoint = Path("/tmp/intelligence-browser-checkpoint.json")
    completed = Path("/tmp/intelligence-browser-checkpoint.done")
    completed.unlink(missing_ok=True)
    checkpoint.write_text(json.dumps({"stage": stage, **resources}))
    try:
        async with asyncio.timeout(900):
            while not completed.exists():
                await asyncio.sleep(2)
        assert completed.read_text().strip() == stage
    finally:
        checkpoint.unlink(missing_ok=True)
        completed.unlink(missing_ok=True)


async def test_lifecycle_rechecks_same_role_principals_masks_and_revocation(monkeypatch):
    if not settings.RANGER_ENABLED:
        raise RuntimeError("Ranger must be enabled for this acceptance")
    from app.common.nova_system import init_task_orchestration
    from app.modules.intelligence import decisions, engine
    from app.modules.intelligence.engine_schema import ensure_engine_schema
    from app.modules.intelligence.semantic_view_schema import ensure_semantic_view_schema

    await db.init_system_pool()
    await session_store.init()
    await ensure_engine_schema()
    await ensure_semantic_view_schema()
    await init_task_orchestration()
    suffix = uuid4().hex[:12]
    table = "intelligence_" + suffix
    ranger, policies = RangerClient(), []
    end = datetime(2026, 9, 20, tzinfo=ZoneInfo("Asia/Jakarta"))
    monkeypatch.setattr(engine, "utc_now", lambda: end)
    monkeypatch.setattr(decisions, "utc_now", lambda: end)
    app = create_app()
    try:
        await db.execute_system(
            f"CREATE TABLE analytics.{table} (id BIGINT, city VARCHAR(64), "
            "amount DECIMAL(18,2), observed_at DATETIME) PRIMARY KEY(id) "
            "DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1')"
        )
        rows = []
        for week in range(7):
            for city, multiplier in (("Jakarta", 1), ("Bandung", 2)):
                for _sample in range(3):
                    rows.append(
                        [
                            len(rows) + 1,
                            city,
                            multiplier * (60 if week == 0 else 100),
                            (end - timedelta(weeks=week, hours=12)).replace(tzinfo=None),
                        ]
                    )
        await db.execute_system(
            f"INSERT INTO analytics.{table} VALUES " + ",".join(["(%s,%s,%s,%s)"] * len(rows)),
            [value for row in rows for value in row],
        )
        for kind in ("access", "scope"):
            template = await ranger.get_policy(
                f"nova-managed/{kind}/marketing/default_catalog/analytics/sales"
            )
            assert template
            template = deepcopy(template)
            for key in ("id", "guid"):
                template.pop(key, None)
            template["name"] = f"nova-intelligence-acceptance/{suffix}/{kind}"
            template["resources"]["table"]["values"] = [table]
            policies.append(await ranger.put_policy(RangerPolicy.model_validate(template)))

        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://nova.test"
            ) as ah,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://nova.test"
            ) as bh,
        ):
            alice, bob = StudioClient(ah), StudioClient(bh)
            # News is off for every account until an administrator enables it.
            for reader in ("alice", "bob"):
                await db.execute_system(
                    "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
                    "(user_name, pref_key, pref_value, updated_at) VALUES (%s, %s, 'true', NOW())",
                    [reader, NEWS_ENABLED],
                )
            await alice.login("alice", "NovaAlice2026!", "marketing")
            await bob.login("bob", "NovaBob2026!", "marketing")

            async def wait_rows(client, expected):
                for _ in range(30):
                    response = await client.http.post(
                        "/api/v1/query/execute",
                        json={
                            "sql": f"SELECT city FROM analytics.{table}",
                        },
                    )
                    if response.status_code == 200:
                        payload = response.json()[0]
                        if (
                            payload.get("success")
                            and {row[0] for row in payload["rows"]} == expected
                        ):
                            return
                    await asyncio.sleep(2)
                raise AssertionError("Patched FE did not enforce the expected scoped rows")

            await wait_rows(alice, {"Jakarta"})
            await wait_rows(bob, {"Bandung"})
            definition = {
                "version": "0.1.1",
                "name": "ranger_revenue",
                "datasets": [
                    {
                        "name": "orders",
                        "source": f"analytics.{table}",
                        "primary_key": ["id"],
                        "fields": [
                            {"name": "id", "expression": expression("id"), "datatype": "Integer"},
                            {
                                "name": "amount",
                                "expression": expression("amount"),
                                "datatype": "Decimal",
                            },
                            {
                                "name": "city",
                                "expression": expression("city"),
                                "datatype": "String",
                                "dimension": {},
                            },
                            {
                                "name": "ordered_at",
                                "expression": expression("observed_at"),
                                "datatype": "DateTime",
                                "dimension": {"is_time": True},
                            },
                        ],
                    }
                ],
                "relationships": [],
                "metrics": [
                    metric(
                        "revenue", "orders", "SUM(orders.amount)", unit="currency", currency="IDR"
                    ),
                    metric("orders", "orders", "COUNT(*)"),
                ],
            }
            view = await alice.request(
                "POST",
                "semantic-views",
                {
                    "name": "ranger_revenue",
                    "database": "analytics",
                    "schema_name": suffix,
                    "definition": json.dumps(to_ossie_document(definition)),
                },
            )
            view_id = view["id"]
            validation = await alice.request(
                "POST", f"semantic-views/{view_id}/versions/1/validate"
            )
            assert validation["valid"]
            await alice.request("POST", f"semantic-views/{view_id}/versions/1/publish", {})
            view = await alice.request("GET", f"semantic-views/{view_id}")
            semantic = {
                "view_id": view_id,
                "version": 1,
                "fingerprint": view["versions"][0]["fingerprint"],
            }
            agent = await alice.request(
                "POST",
                "agents",
                {
                    "name": "Ranger lifecycle " + suffix,
                    "semantic_view_ids": [view_id],
                    "policy": "auto_read_only",
                },
            )
            agent_id = agent["agent_id"]
            await alice.request(
                "POST",
                f"agents/{agent_id}/access",
                {
                    "role_name": "marketing",
                    "grant_type": "USAGE",
                },
            )
            checked = await alice.request(
                "POST",
                f"agents/{agent_id}/access/verify",
                {
                    "role_name": "marketing",
                },
            )
            assert checked["all_granted"], checked["items"]
            monitor = await alice.request(
                "POST",
                "intelligence/monitors",
                {
                    "operation_id": str(uuid4()),
                    "configuration": {
                        "name": "Ranger revenue",
                        "agent_id": agent_id,
                        "semantic": semantic,
                        "plan": {"metrics": ["revenue", "orders"]},
                        "value_column": "revenue",
                        "count_column": "orders",
                        "time_dimension": "orders.ordered_at",
                        "minimum_samples": 2,
                        "driver_dimensions": ["orders.city"],
                    },
                },
            )
            incident = await alice.request(
                "POST",
                f"intelligence/monitors/{monitor['id']}/run",
                {
                    "start": (end - timedelta(days=1)).isoformat(),
                    "end": end.isoformat(),
                },
            )
            news_id = incident["news_id"]
            assert news_id
            news = await alice.request("GET", f"intelligence/news/{news_id}")
            assert news["before"] == 300 and news["after"] == 180
            investigation = await alice.request("POST", f"intelligence/news/{news_id}/investigate")
            decision = await alice.request(
                "POST",
                "intelligence/decisions",
                {
                    "operation_id": str(uuid4()),
                    "title": "Ranger governed recovery",
                    "investigation_id": investigation["id"],
                    "learning_enabled": False,
                    "outcome_window": {
                        "start": end.isoformat(),
                        "end": (end + timedelta(days=1)).isoformat(),
                    },
                    "options": [
                        {
                            "id": "recommend",
                            "description": "Conditional recommendation",
                            "simulation": {
                                "action_type": "inventory_transfer",
                                "baseline_units": 180,
                                "price": 1,
                                "unit_cost": 0.2,
                                "expected_unit_change": 10,
                                "unit_change_uncertainty": 5,
                                "action_cost": 0,
                                "capacity": 1000,
                                "max_budget": 0,
                            },
                        }
                    ],
                },
            )
            decision_id = decision["id"]
            await alice.request(
                "POST",
                f"intelligence/decisions/{decision_id}/shares",
                {
                    "object_type": "decision",
                    "object_id": decision_id,
                    "target_type": "user",
                    "target_name": "bob",
                },
            )
            response = await bob.http.get(f"/api/v1/intelligence/decisions/{decision_id}")
            assert response.status_code in {403, 404, 409}
            shared = await bob.request("GET", "intelligence/decisions/shared")
            assert decision_id not in {row["id"] for row in shared["items"]}
            graph = await alice.request("POST", f"intelligence/decisions/{decision_id}/context")
            assert (await alice.request("GET", f"intelligence/context/{graph['root_id']}/graph"))[
                "nodes"
            ]
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://nova.test"
            ) as role_http:
                role_client = StudioClient(role_http)
                await role_client.login("alice", "NovaAlice2026!", "marketing")
                await role_client.request("POST", "auth/switch-role", {"role": "finance"})
                response = await role_http.get(f"/api/v1/intelligence/decisions/{decision_id}")
                assert response.status_code in {403, 404, 409}
                await role_client.request("POST", "auth/switch-role", {"role": "marketing"})
                response = await role_http.get(f"/api/v1/intelligence/decisions/{decision_id}")
                assert response.status_code in {403, 404, 409}
                await role_client.request("POST", "auth/logout")
            assert (await alice.request("GET", f"intelligence/decisions/{decision_id}"))[
                "id"
            ] == decision_id
            await browser_checkpoint("populated", news_id=news_id, decision_id=decision_id)
            mask = RangerPolicy.model_validate(
                {
                    "service": settings.RANGER_SERVICE_NAME,
                    "name": f"nova-intelligence-acceptance/{suffix}/mask",
                    "policyType": 1,
                    "resources": {
                        **deepcopy(policies[0]["resources"]),
                        "column": {"values": ["amount"]},
                    },
                    "dataMaskPolicyItems": [
                        {
                            "roles": ["marketing"],
                            "accesses": [{"type": "select", "isAllowed": True}],
                            "dataMaskInfo": {"dataMaskType": "CUSTOM", "valueExpr": "0"},
                        }
                    ],
                }
            )
            policies.append(await ranger.put_policy(mask))
            for _ in range(30):
                response = await ah.get(f"/api/v1/intelligence/decisions/{decision_id}")
                if response.status_code == 409:
                    break
                assert response.status_code == 200
                await asyncio.sleep(2)
            else:
                raise AssertionError("Changed mask did not invalidate stored evidence")
            response = await ah.get(f"/api/v1/intelligence/context/{graph['root_id']}/graph")
            assert response.status_code in {403, 404, 409}
            await browser_checkpoint("stale", news_id=news_id, decision_id=decision_id)
            for policy in reversed(policies):
                await ranger.delete_policy(policy["id"])
                assert await ranger.get_policy(policy["name"]) is None
            policies.clear()
            for _ in range(30):
                response = await ah.post(
                    f"/api/v1/semantic-views/{view_id}/query",
                    json={"metrics": ["revenue"], "limit": 2},
                )
                if response.status_code in {403, 404, 422}:
                    break
                await asyncio.sleep(2)
            else:
                raise AssertionError(
                    f"Revoked query status={response.status_code}: {response.text[:2000]}"
                )
            response = await ah.get(f"/api/v1/intelligence/decisions/{decision_id}")
            assert response.status_code in {403, 404, 409}
            assert (await ah.get(f"/api/v1/semantic-views/{view_id}")).status_code == 404
            await browser_checkpoint("empty_denied", news_id=news_id, decision_id=decision_id)
    finally:
        for policy in reversed(policies):
            await ranger.delete_policy(policy["id"])
        await db.execute_system(f"DROP TABLE IF EXISTS analytics.{table}")
        await session_store.close()
        await db.close_system_pool()
