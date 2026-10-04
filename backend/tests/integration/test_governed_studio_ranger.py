"""Opt-in acceptance against an initialized, isolated patched FE and Ranger.

The policy, database, users, and roles are unique to each fixture. No provider
inference is needed: semantic plans are supplied through the validated planner
contract while their SQL, policies, masks, journals, and actions execute live.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.common.nova_system import init_nova_system, init_task_orchestration
from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.core.security import decode_token, decrypt_password
from app.integrations.ranger.client import RangerClient
from app.integrations.ranger.schemas import RangerPolicy
from app.main import create_app
from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.service import access_control_service
from app.modules.agents import quality
from app.modules.agents.harness_repository import harness_repository
from app.modules.agents.mission import MissionService
from app.modules.agents.releases import capture_manifest, load_runtime_manifest
from app.modules.agents.repository import agent_repository
from app.modules.agents.resource_delegation import ResourceDelegation
from app.modules.agents.run_journal import run_journal
from app.modules.agents.semantic.access import load_authorized_models
from app.modules.agents.semantic.serialize import to_ossie_document
from app.modules.agents.service import agent_service
from app.modules.agents.tools.semantic_query import SemanticQueryTool
from app.modules.agents.versions import agent_versions
from app.modules.ai_ml.service import ai_service
from app.modules.assistant.consent import consent_broker
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.security import observation_context, session_security
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.intelligence.contracts import Scope, fingerprint
from app.modules.intelligence.engine_schema import ensure_engine_schema
from app.modules.intelligence.semantic_view_schema import ensure_semantic_view_schema
from app.modules.task_orchestration.internal_handlers import InternalTaskConfiguration, run_once
from app.modules.task_orchestration.repository import task_orchestration_repository
from tests.benchmark.business_intelligence.client import StudioClient
from tests.benchmark.business_intelligence.model import expression, metric

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        os.getenv("NOVA_GOVERNED_STUDIO_RANGER_ACCEPTANCE") != "1",
        reason="Requires initialized isolated patched-FE/Ranger installation; see acceptance notes",
    ),
]


async def _user(client: StudioClient) -> dict:
    token = client.http.headers["Authorization"].removeprefix("Bearer ")
    sid = decode_token(token)["sid"]
    session = await session_store.get(sid)
    assert session is not None
    return {**session, "session_id": sid}


async def _query(client: StudioClient, sql: str) -> dict:
    response = await client.http.post("/api/v1/query/execute", json={"sql": sql})
    assert response.status_code == 200, f"Query HTTP {response.status_code}"
    return response.json()[0]


async def _eventually(check, description: str):
    async with asyncio.timeout(90):
        while True:
            if await check():
                return
            await asyncio.sleep(1)
    raise AssertionError(description)


def _definition(database: str, *, multiplier: int = 1) -> dict:
    return {
        "version": "0.1.1",
        "name": "governed_revenue",
        "datasets": [
            {
                "name": "orders",
                "source": f"{database}.sales",
                "primary_key": ["id"],
                "fields": [
                    {"name": "id", "expression": expression("id"), "datatype": "Integer"},
                    {"name": "amount", "expression": expression("amount"), "datatype": "Decimal"},
                    {"name": "complete", "expression": expression("complete"),
                     "datatype": "Integer"},
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
                "revenue",
                "orders",
                f"SUM(orders.amount) * {multiplier}",
                unit="currency",
                currency="IDR",
            ),
            metric("orders", "orders", "COUNT(*)"),
            metric("coverage", "orders", "MIN(orders.complete)", unit="ratio"),
        ],
    }


async def _publish(client: StudioClient, view_id: str, version: int):
    checked = await client.request("POST", f"semantic-views/{view_id}/versions/{version}/validate")
    assert checked["valid"], "Live semantic validation must succeed"
    await client.request("POST", f"semantic-views/{view_id}/versions/{version}/publish", {})


@pytest.fixture
async def governed(monkeypatch):
    assert settings.RANGER_ENABLED, "An unpatched/native stack cannot satisfy this acceptance"
    assert settings.RANGER_STRICT_SINGLE_ACTIVE_ROLE
    for flag in (
        "STUDIO_BUSINESS_WORKFLOW_ENABLED",
        "STUDIO_ACTIONS_ENABLED",
        "STUDIO_QUALITY_ENABLED",
    ):
        monkeypatch.setattr(settings, flag, True)
    await db.init_system_pool()
    await session_store.init()
    await init_nova_system()
    await init_task_orchestration()
    await ensure_engine_schema()
    await ensure_semantic_view_schema()
    await agent_repository.ensure_schema()
    await assistant_repository.ensure_schema()
    await harness_repository.ensure_schema()
    await run_journal.ensure_schema()
    await MissionService().ensure_schema()
    await ResourceDelegation().ensure_schema()
    await db.execute_system(
        "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
        "(user_name,pref_key,pref_value,updated_at) "
        "VALUES ('__system__','setup_complete','true',NOW())"
    )
    suffix = uuid4().hex[:10]
    database, role, inactive = ("gov_data_" + suffix, "gov_read_" + suffix, "gov_none_" + suffix)
    principal, other = "gov_a_" + suffix, "gov_b_" + suffix
    password = secrets.token_urlsafe(24)
    operator = SecurityContext(principal="nova_admin", active_role="ACCOUNTADMIN")
    ranger, policies, clients = RangerClient(), [], []
    provider = None
    try:
        for name in (role, inactive):
            await access_control_service.create_role(operator, name)
        for username in (principal, other):
            await db.execute_system(f"CREATE USER '{username}' IDENTIFIED BY %s", [password])
            for name in (role, inactive):
                await access_control_service.assign_role(operator, role=name, username=username)
            await db.execute_system(f"SET DEFAULT ROLE `{role}` TO '{username}'")
        await db.execute_system(f"CREATE DATABASE {database}")
        await db.execute_system(
            f"CREATE TABLE {database}.sales (id BIGINT, city VARCHAR(64), amount DECIMAL(18,2), "
            "observed_at DATETIME, complete TINYINT) PRIMARY KEY(id) "
            "DISTRIBUTED BY HASH(id) BUCKETS 1 "
            "PROPERTIES('replication_num'='1')"
        )
        end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        rows = [
            [
                week * 6 + index + 1,
                city,
                (60 if week == 0 else 100) * multiplier,
                (end - timedelta(weeks=week, hours=12)).replace(tzinfo=None),
                1,
            ]
            for week in range(7)
            for index, (city, multiplier) in enumerate([("Jakarta", 1)] * 3 + [("Bandung", 2)] * 3)
        ]
        await db.execute_system(
            f"INSERT INTO {database}.sales VALUES " + ",".join(["(%s,%s,%s,%s,%s)"] * len(rows)),
            [value for row in rows for value in row],
        )
        resources = {
            "catalog": {"values": ["default_catalog"]},
            "database": {"values": [database]},
            "table": {"values": ["*"]},
            "column": {"values": ["*"]},
        }
        for policy in [
            {
                "name": f"gov-studio/{suffix}/access",
                "policyType": 0,
                "policyItems": [
                    {"roles": [role], "accesses": [{"type": "select", "isAllowed": True}]}
                ],
            },
            {
                "name": f"gov-studio/{suffix}/rows",
                "policyType": 2,
                "rowFilterPolicyItems": [
                    {
                        "users": [username],
                        "accesses": [{"type": "select", "isAllowed": True}],
                        "rowFilterInfo": {"filterExpr": f"city = '{city}'"},
                    }
                    for username, city in ((principal, "Jakarta"), (other, "Bandung"))
                ],
            },
        ]:
            policies.append(
                await ranger.put_policy(
                    RangerPolicy.model_validate(
                        {
                            "service": settings.RANGER_SERVICE_NAME,
                            "resources": resources if policy["policyType"] == 0 else {
                                key: {"values": ["sales"]} if key == "table" else value
                                for key, value in resources.items() if key != "column"
                            },
                            **policy,
                        }
                    )
                )
            )
        application = create_app()
        for username in (principal, other):
            http = httpx.AsyncClient(
                transport=httpx.ASGITransport(application),
                base_url="http://governed.test",
                timeout=120,
            )
            client = StudioClient(http)
            clients.append(client)
            await client.login(username, password, role)
        primary, secondary = clients

        async def scoped_rows():
            left = await _query(primary, f"SELECT city FROM {database}.sales")
            right = await _query(secondary, f"SELECT city FROM {database}.sales")
            return (
                left.get("success")
                and right.get("success")
                and {row[0] for row in left["rows"]} == {"Jakarta"}
                and {row[0] for row in right["rows"]} == {"Bandung"}
            )

        await _eventually(scoped_rows, "Patched FE did not apply same-role principal filters")
        assert (await _query(primary, "SELECT CURRENT_ROLE()"))["rows"] == [[role]]
        provider = await ai_service.create_provider(
            {
                "name": "Acceptance metadata " + suffix,
                "type": "openai",
                "endpoint": "https://example.com/v1",
                "api_key": secrets.token_urlsafe(24),
            },
            principal,
        )
        assert provider
        model = await ai_service.create_model(
            {
                "provider_id": provider["id"],
                "name": "acceptance-metadata",
                "type": "llm",
                "max_tokens": 8192,
            },
            principal,
        )
        assert model
        definition = _definition(database)
        view = await primary.request(
            "POST",
            "semantic-views",
            {
                "name": "governed_revenue",
                "database": database,
                "definition": json.dumps(to_ossie_document(definition)),
            },
        )
        await _publish(primary, view["id"], 1)
        view = await primary.request("GET", f"semantic-views/{view['id']}")
        semantic = {
            "view_id": view["id"],
            "version": 1,
            "fingerprint": view["versions"][0]["fingerprint"],
        }
        agent = await primary.request(
            "POST",
            "agents",
            {
                "name": "Governed acceptance " + suffix,
                "semantic_view_ids": [view["id"]],
                "model_provider_id": provider["id"],
                "model_name": model["name"],
                "default_tools": ["semantic_query"],
                "policy": "auto_read_only",
            },
        )
        await primary.request("POST", f"agents/{agent['agent_id']}/access", {
            "role_name": role, "grant_type": "USAGE",
        })
        verification = await primary.request("POST", f"agents/{agent['agent_id']}/access/verify", {
            "role_name": role,
        })
        assert verification["all_granted"], "Acceptance agent role must be verified"
        thread = await primary.request(
            "POST",
            f"agents/{agent['agent_id']}/threads",
            {
                "title": "Governed acceptance",
                "learning_enabled": False,
            },
        )
        yield SimpleNamespace(
            primary=primary,
            secondary=secondary,
            user=await _user(primary),
            agent=agent,
            thread=thread,
            database=database,
            role=role,
            inactive=inactive,
            principal=principal,
            other=other,
            password=password,
            operator=operator,
            ranger=ranger,
            policies=policies,
            resources=resources,
            semantic=semantic,
            definition=definition,
            end=end,
        )
    finally:
        original_error = sys.exc_info()[0] is not None
        cleanup_errors = []

        async def clean(operation):
            try:
                await operation
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)

        consent_broker.cancel_all()
        for client in clients:
            token = client.http.headers.get("Authorization", "").removeprefix("Bearer ")
            if token:
                await clean(session_store.delete(decode_token(token)["sid"]))
            await clean(client.http.aclose())
        if provider:
            await clean(ai_service.delete_provider(provider["id"]))
        for policy in reversed(policies):
            await clean(ranger.delete_policy(policy["id"]))
        await clean(db.execute_system(f"DROP DATABASE IF EXISTS {database}"))
        for username in (principal, other):
            await clean(db.execute_system(f"DROP USER IF EXISTS '{username}'"))
        for name in (role, inactive):
            await clean(access_control_service.drop_role(operator, name))
        await clean(session_store.close())
        await clean(db.close_system_pool())
        if cleanup_errors and not original_error:
            pytest.fail(f"Acceptance cleanup failed: {cleanup_errors}")


async def _pin(fixture):
    agent = fixture.agent
    version = await agent_versions.store(agent, version_id=str(uuid4()), label="Ranger acceptance")
    manifest = await capture_manifest(agent, version["version_id"], fixture.user)
    pinned = await agent_repository.update_agent(
        agent["agent_id"],
        owner_name=fixture.principal,
        fields={"release_manifest_id": manifest["id"]},
        expected_revision=agent["config_revision"],
        check_revision=True,
    )
    assert await load_runtime_manifest(pinned) == manifest
    fixture.agent = pinned
    verified = await fixture.primary.request(
        "POST", f"agents/{pinned['agent_id']}/access/verify", {"role_name": fixture.role}
    )
    assert verified["all_granted"]
    return manifest


async def _semantic(fixture, manifest, user=None):
    user = user or fixture.user
    context = LoopContext(
        user_name=user["username"],
        user=user,
        role=user["active_role"],
        session_id=user["session_id"],
        thread_id=fixture.thread["thread_id"],
        agent_id=fixture.agent["agent_id"],
        semantic_view_ids=[fixture.semantic["view_id"]],
        release_manifest=manifest,
        primary_plan={"view": fixture.semantic["view_id"], "plan": {
            "metrics": ["revenue"], "dimensions": [], "filters": [], "named_filters": [],
            "time": None, "order_by": [], "limit": None, "unresolved_concepts": [],
        }},
    )
    models = await load_authorized_models(context)
    assert len(models) == 1 and models[0]["version"] == 1
    outcome = await SemanticQueryTool().run(
        ToolInvocation(str(uuid4()), "semantic_query", {"question": "Total revenue"}),
        context,
    )
    assert outcome.ok, "Real governed semantic execution failed"
    assert outcome.evidence["version"] == 1
    return outcome


async def test_release_pins_keep_live_principal_role_masks_and_revocation(governed):
    fixture = governed
    manifest = await _pin(fixture)
    before = await _semantic(fixture, manifest)
    assert float(before.table["rows"][0][0]) == 1980
    peer = await _semantic(fixture, manifest, await _user(fixture.secondary))
    assert float(peer.table["rows"][0][0]) == 3960
    await fixture.primary.request(
        "POST",
        f"semantic-views/{fixture.semantic['view_id']}/versions",
        {
            "definition": json.dumps(
                to_ossie_document(_definition(fixture.database, multiplier=10))
            ),
        },
    )
    await _publish(fixture.primary, fixture.semantic["view_id"], 2)
    assert float((await _semantic(fixture, manifest)).table["rows"][0][0]) == 1980
    response = await fixture.primary.http.put(
        f"/api/v1/agents/{fixture.agent['agent_id']}", json={"instructions_response": "Change live"}
    )
    assert response.status_code == 409
    await agent_service.build_loop_inputs(fixture.agent)
    async with db.user_conn(
        fixture.principal, decrypt_password(fixture.user["encrypted_password"])
    ) as conn, conn.cursor() as cursor:
        for selection in ("NONE", "ALL"):
            await cursor.execute(f"SET ROLE {selection}")
            with pytest.raises(Exception, match="(?i)(role|denied|access)"):
                await cursor.execute(f"SELECT amount FROM {fixture.database}.sales")
    mask = await fixture.ranger.put_policy(
        RangerPolicy.model_validate(
            {
                "service": settings.RANGER_SERVICE_NAME,
                "name": "gov-studio/mask/" + uuid4().hex,
                "policyType": 1,
                "resources": {**deepcopy(fixture.resources), "column": {"values": ["amount"]}},
                "dataMaskPolicyItems": [
                    {
                        "roles": [fixture.role],
                        "accesses": [{"type": "select", "isAllowed": True}],
                        "dataMaskInfo": {"dataMaskType": "CUSTOM", "valueExpr": "0"},
                    }
                ],
            }
        )
    )
    fixture.policies.append(mask)

    async def masked():
        return float((await _semantic(fixture, manifest)).table["rows"][0][0]) == 0

    await _eventually(masked, "Pinned releases must observe newly applied masks")
    await fixture.primary.request("POST", "auth/switch-role", {"role": fixture.inactive})
    denied = await _query(fixture.primary, f"SELECT amount FROM {fixture.database}.sales")
    assert not denied["success"], "Assigned but inactive access role leaked into FE"
    with pytest.raises(HTTPException):
        await _semantic(fixture, manifest, await _user(fixture.primary))
    await fixture.primary.request("POST", "auth/switch-role", {"role": fixture.role})
    fixture.user = await _user(fixture.primary)
    await access_control_service.revoke_role(
        fixture.operator, role=fixture.role, username=fixture.principal
    )
    with pytest.raises(HTTPException):
        await _semantic(fixture, manifest)


async def test_missions_resources_replay_and_role_version_boundaries(governed):
    fixture = governed
    client, user, thread_id = fixture.primary, fixture.user, fixture.thread["thread_id"]
    mission = await client.request(
        "POST",
        f"agents/studio/threads/{thread_id}/missions",
        {
            "objective": "Investigate governed revenue",
            "operation_id": "mission-ranger",
            "work_intent": "ANALYZE",
            "public_work_steps": ["evidence"],
        },
    )
    assert (
        await client.request(
            "POST",
            f"agents/studio/threads/{thread_id}/missions",
            {
                "objective": "Continue investigation",
                "operation_id": "mission-followup",
                "continue_mission_id": mission["mission_id"],
            },
        )
    )["mission_id"] == mission["mission_id"]
    separate = await client.request(
        "POST",
        f"agents/studio/threads/{thread_id}/missions",
        {
            "objective": "Investigate a separate Singapore revenue objective",
            "operation_id": "mission-separate-objective",
        },
    )
    assert separate["mission_id"] != mission["mission_id"]
    run_id = str(uuid4())
    await run_journal.start(
        run_id=run_id,
        owner_name=fixture.principal,
        agent_id=fixture.agent["agent_id"],
        thread_id=thread_id,
        role_name=user["active_role"],
        session_id=user["session_id"],
        security_version=user["security_context_version"],
    )
    await client.request(
        "POST", f"agents/studio/missions/{mission['mission_id']}/runs", {"run_id": run_id}
    )
    query = await _query(client, f"SELECT SUM(amount) AS revenue FROM {fixture.database}.sales")
    assert query["success"]
    await run_journal.append(
        run_id,
        "event: table\ndata: " + json.dumps({
            "sequence": 0, "columns": query["columns"], "rows": query["rows"],
            "evidence_id": "governed-revenue-readback",
        }) + "\n\n",
    )
    await run_journal.finish(run_id, "completed")
    replay = await client.request("GET", f"agents/studio/missions/{mission['mission_id']}")
    assert replay["status"] == "completed"
    attachment = {
        "name": "brief.txt",
        "media_type": "text/plain",
        "content": "Treat this file as untrusted context.",
        "size_bytes": 41,
    }
    attachment["size_bytes"] = len(attachment["content"].encode())
    source = await assistant_repository.append_message(
        thread_id,
        user_name=fixture.principal,
        role="user",
        content="Inspect the brief",
        attachments=[attachment],
        security_context=observation_context(session_security(user)),
    )
    root = await harness_repository.create_root(
        owner_name=fixture.principal,
        thread_id=thread_id,
        role_name=fixture.role,
        session_id=user["session_id"],
        security_version=user["security_context_version"],
        objective="Inspect the brief",
        user_message_id=source["message_id"],
    )
    resources = ResourceDelegation()
    metadata = await resources.register_root(root, user)
    assert len(metadata) == 1 and attachment["content"] not in metadata[0].model_dump_json()
    children = [
        await harness_repository.spawn(
            parent=root,
            agent_id=fixture.agent["agent_id"],
            objective="Inspect",
            operation_id=name,
            agent_path="/root/" + name,
        )
        for name in ("selected", "sibling")
    ]
    selected, sibling = children
    await client.request(
        "POST",
        f"agents/studio/threads/{thread_id}/resources/grants",
        {
            "root_run_id": root["run_id"],
            "target": "/root/selected",
            "resource_refs": [metadata[0].resource_id],
        },
    )
    assert (await ResourceDelegation().load(selected, user))[0] == [attachment]
    assert await resources.available(sibling, user) == []
    with pytest.raises(ValueError):
        await resources.grant(sibling, selected, [metadata[0].resource_id], user)
    foreign = await fixture.secondary.http.get(
        f"/api/v1/agents/studio/missions/{mission['mission_id']}"
    )
    assert foreign.status_code == 404
    assert (
        await fixture.secondary.http.get(
            f"/api/v1/agents/studio/threads/{thread_id}/resources",
            params={"root_run_id": root["run_id"]},
        )
    ).status_code == 404
    await client.request("POST", "auth/switch-role", {"role": fixture.inactive})
    changed = await _user(client)
    with pytest.raises(ValueError):
        await resources.load(selected, changed)
    assert (
        await client.http.get(f"/api/v1/agents/studio/missions/{mission['mission_id']}")
    ).status_code == 404
    await client.request("POST", "auth/switch-role", {"role": fixture.role})
    assert (
        await client.http.get(f"/api/v1/agents/studio/missions/{mission['mission_id']}")
    ).status_code == 404
    with pytest.raises(ValueError):
        await resources.load(selected, await _user(client))


async def test_automatic_investigation_resume_keeps_exact_pins_and_live_authorization(governed):
    from app.modules.agents.business_results import governed_result
    from app.modules.agents.semantic.plan_contract import validate_generated_plan
    from app.modules.agents.semantic.planning import SemanticPlan, SemanticTime
    from app.modules.agents.semantic.time_ranges import resolve_execution_time
    from app.modules.intelligence.engine import intelligence_service

    fixture = governed
    client = fixture.primary
    plan = SemanticPlan(metrics=("revenue",), time=SemanticTime(
        "ordered_at", range="last_7_days", compare="previous_period",
    ))
    fixed = resolve_execution_time(
        plan.time.range, plan.time.compare, now=fixture.end, timezone="Asia/Jakarta",
    )
    await db.execute_system(
        f"INSERT INTO {fixture.database}.sales VALUES "
        "(3001,'Jakarta',60,%s,1),(3002,'Jakarta',100,%s,1)",
        [(window.end - timedelta(days=1)).replace(tzinfo=None)
         for window in (fixed.current, fixed.baseline)],
    )
    mission = await client.request(
        "POST", f"agents/studio/threads/{fixture.thread['thread_id']}/missions",
        {"objective": "Investigate the authorized weekly revenue decline",
         "operation_id": "automatic-resume", "work_intent": "INVESTIGATE"},
    )
    wire_plan = json.loads(json.dumps(plan.as_dict()))
    validate_generated_plan(wire_plan)
    context = LoopContext(
        user_name=fixture.principal, user=fixture.user, role=fixture.role,
        session_id=fixture.user["session_id"], thread_id=fixture.thread["thread_id"],
        agent_id=fixture.agent["agent_id"], mission_id=mission["mission_id"],
        semantic_view_ids=[fixture.semantic["view_id"]], execution_now=fixture.end,
        execution_timezone=fixed.timezone, effective_work_intent="INVESTIGATE",
        primary_plan={"view": fixture.semantic["view_id"], "plan": wire_plan},
    )
    invocation = ToolInvocation(str(uuid4()), "semantic_query", {"question": "Weekly decline"})
    outcome = await SemanticQueryTool().run(invocation, context)
    assert outcome.ok and outcome.business_result["seed"]
    seed = outcome.business_result["seed"]
    original = await intelligence_service.automatic_investigation(
        seed, fixture.user, mission_id=mission["mission_id"], agent_id=context.agent_id,
    )
    hook_result = await governed_result(invocation, outcome, context)
    result = hook_result.public_event
    assert result is not None and hook_result.provider_observation is not None
    assert hook_result.provider_observation["id"] == original["investigation"].id
    assert hook_result.provider_observation["revision"] == original["investigation"].revision
    assert result["status"] == "complete" and result["investigation"]["hypotheses"]
    assert all(h["causal_status"] != "supported_effect"
               for h in result["investigation"]["hypotheses"])
    assert original["comparison"].id == result["comparison_id"]
    assert original["investigation"].id == result["investigation"]["id"]
    old_binding = original["investigation"].scope
    monitor = await intelligence_service.get("monitors", original["comparison"].monitor_id,
                                              fixture.user)
    assert not monitor.enabled and monitor.count_column is None
    prior = await client.request("GET", f"agents/studio/missions/{mission['mission_id']}")
    await client.request("POST", "auth/switch-role", {"role": fixture.inactive})
    await client.request("POST", "auth/switch-role", {"role": fixture.role})
    current = await _user(client)
    assert current["security_context_version"] != fixture.user["security_context_version"]
    resumed = await client.request(
        "POST", f"agents/studio/missions/{mission['mission_id']}/resume",
        {"expected_revision": prior["revision"], "operation_id": "automatic-new-binding"},
    )
    recovered = await intelligence_service.automatic_investigation(
        seed, current, mission_id=mission["mission_id"], agent_id=context.agent_id,
    )
    assert recovered["comparison"].id == original["comparison"].id
    assert recovered["investigation"].id == original["investigation"].id
    assert recovered["investigation"].scope == old_binding
    assert recovered["investigation"].revision == original["investigation"].revision
    ref = next(r for r in resumed["object_refs"] if r["kind"] == "investigation")
    historical = await client.request(
        "GET", f"agents/studio/missions/{mission['mission_id']}/objects/investigation/{ref['id']}"
        f"?revision={ref['revision']}",
    )
    assert "scope" not in historical
    assert historical["id"] == recovered["investigation"].id
    assert historical["revision"] == recovered["investigation"].revision
    with pytest.raises(HTTPException):
        await intelligence_service.automatic_investigation(
            seed, fixture.user, mission_id=mission["mission_id"], agent_id=context.agent_id,
        )
    await access_control_service.revoke_role(
        fixture.operator, role=fixture.role, username=fixture.principal,
    )
    with pytest.raises(HTTPException):
        await intelligence_service.automatic_investigation(
            seed, current, mission_id=mission["mission_id"], agent_id=context.agent_id,
        )


async def test_production_scoring_is_bound_scoped_and_cannot_replay_mutations(governed):
    fixture = governed
    manifest = await _pin(fixture)
    user, agent_id = fixture.user, fixture.agent["agent_id"]
    case = await fixture.primary.request(
        "POST",
        f"agents/{agent_id}/quality/cases",
        {
            "name": "Persisted policy regression",
            "prompt": "Observe revenue",
            "production_match": "all_traces",
            "assertions": [{"scorer": "policy_compliance", "expected": {"authorized": True}}],
        },
    )
    forbidden = await fixture.primary.http.put(
        f"/api/v1/agents/{agent_id}/quality/monitoring", json={"enabled": True, "sample_rate": 1}
    )
    assert forbidden.status_code == 403, "Background work requires an explicit binding"
    await task_orchestration_repository.bind_role_execution_user(
        fixture.role, fixture.principal, "nova_admin"
    )
    config = await fixture.primary.request(
        "PUT",
        f"agents/{agent_id}/quality/monitoring",
        {
            "enabled": True,
            "sample_rate": 1,
            "max_traces": 20,
            "cadence_minutes": 60,
        },
    )
    steps = [
        {"kind": "tool", "name": "execute_business_action", "args": {"action_id": "must-not-run"}},
        {
            "kind": "quality_observation",
            "manifest_id": manifest["id"],
            "version_id": manifest["version_id"],
            "duration_ms": 1,
            "facts": {"policy_compliance": {"authorized": True}},
            "counts": {"provider_calls": 0, "tool_calls": 1},
        },
    ]
    source = await assistant_repository.append_message(
        fixture.thread["thread_id"],
        user_name=fixture.principal,
        role="assistant",
        content="Persisted trace fixture",
        agent_id=agent_id,
        steps=steps,
        security_context=observation_context(session_security(user)),
    )
    await assistant_repository.append_message(
        fixture.thread["thread_id"],
        user_name=fixture.principal,
        role="assistant",
        content="Expired scope trace",
        agent_id=agent_id,
        steps=steps,
        security_context={
            **observation_context(session_security(user)),
            "security_context_version": user["security_context_version"] + 1,
        },
    )
    request = InternalTaskConfiguration(scope=Scope.from_user(user), record_id=config["id"])
    before = await db.execute_system("SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_INTELLIGENCE_ACTIONS")
    result = await run_once("agents.quality", request, user)
    assert result["scored"] == 1
    assert (await run_once("agents.quality", request, user))["scored"] == 0
    assert (
        await db.execute_system("SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_INTELLIGENCE_ACTIONS")
        == before
    )
    runs = await quality.records("runs", agent_id, user)
    assert len(runs) == 1 and runs[0]["source_message_id"] == source["message_id"]
    assert runs[0]["cases"][0]["id"] == case["id"] and not runs[0]["promotion_eligible"]
    from app.modules.task_orchestration.credentials import CredentialUnavailable

    with pytest.raises(CredentialUnavailable):
        await run_once("agents.quality", request, await _user(fixture.secondary))
    await task_orchestration_repository.bind_role_execution_user(
        fixture.role, fixture.other, "nova_admin"
    )
    with pytest.raises(HTTPException) as denied:
        await run_once("agents.quality", request, user)
    assert denied.value.status_code == 403


async def _consented(fixture, action, *, compensate=False, allow=True):
    operation_id = str(uuid4())
    path = "compensate" if compensate else "execute"
    pending = asyncio.create_task(
        fixture.primary.http.post(
            f"/api/v1/intelligence/actions/{action['id']}/{path}",
            json={
                "operation_id": operation_id,
                "expected_revision": action["revision"],
                "thread_id": fixture.thread["thread_id"],
            },
        )
    )
    call_id = fingerprint([action["id"], action["revision"], operation_id, compensate])
    try:

        async def awaiting():
            if pending.done():
                response = pending.result()
                raise AssertionError(f"Action exited before consent: HTTP {response.status_code}")
            return consent_broker.owner_of(call_id) is not None

        await _eventually(awaiting, "Action did not request the existing consent broker")
        stranger = await fixture.secondary.http.post(
            f"/api/v1/agents/{fixture.agent['agent_id']}/tool-calls/{call_id}/decision",
            json={"decision": "allow_once"},
        )
        assert stranger.status_code == 404
        assert consent_broker.owner_of(call_id) == (fixture.thread["thread_id"], fixture.principal)
        permanent = await fixture.primary.http.post(
            f"/api/v1/agents/{fixture.agent['agent_id']}/tool-calls/{call_id}/decision",
            json={"decision": "allow_session"},
        )
        assert permanent.status_code == 400, "Mutations cannot receive a session grant"
        await fixture.primary.request(
            "POST",
            f"agents/{fixture.agent['agent_id']}/tool-calls/{call_id}/decision",
            {"decision": "allow_once" if allow else "deny"},
        )
        response = await pending
        assert response.status_code == 200, f"Action HTTP {response.status_code}"
        return response.json()
    finally:
        if not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending


async def test_actions_require_review_consent_verify_schedule_and_live_authorization(
    governed, monkeypatch,
):
    fixture = governed
    client = fixture.primary
    from app.modules.access_control.business_policy import (
        BusinessPolicy,
        read_business_policy,
        save_business_policy,
    )

    current_policy = await read_business_policy()
    await save_business_policy(
        BusinessPolicy(reviewer_roles=[fixture.role], revision=current_policy.revision),
        {
            "username": "nova_admin",
            "active_role": "ACCOUNTADMIN",
        },
    )
    await task_orchestration_repository.bind_role_execution_user(
        fixture.role,
        fixture.principal,
        "nova_admin",
    )
    manifest = await _pin(fixture)
    mission = await client.request(
        "POST", f"agents/studio/threads/{fixture.thread['thread_id']}/missions",
        {"objective": "Investigate revenue decline and observe recovery",
         "operation_id": "controlled-journey", "work_intent": "INVESTIGATE",
         "public_work_steps": ["investigate", "evidence", "scenarios", "decide", "approve",
                               "execute", "verify", "observe"]},
    )

    async def link(kind, record):
        nonlocal mission
        mission = await client.request(
            "POST", f"agents/studio/missions/{mission['mission_id']}/objects",
            {"expected_revision": mission["revision"],
             "object_ref": {"kind": kind, "id": record["id"], "revision": record["revision"]}},
        )

    comparison = await client.request(
        "POST",
        "intelligence/investigations/from-chat",
        {
            "operation_id": str(uuid4()),
            "configuration": {
                "name": "Governed sales comparison",
                "agent_id": fixture.agent["agent_id"],
                "semantic": fixture.semantic,
                "plan": {"metrics": ["revenue", "orders", "coverage"]},
                "value_column": "revenue",
                "count_column": "orders",
                "completeness_column": "coverage",
                "minimum_samples": 2,
                "driver_dimensions": ["city"],
                "time_dimension": "ordered_at",
                "enabled": False,
            },
            "current_window": {
                "start": (fixture.end - timedelta(weeks=1)).isoformat(),
                "end": fixture.end.isoformat(),
            },
            "baseline_window": {
                "start": (fixture.end - timedelta(weeks=2)).isoformat(),
                "end": (fixture.end - timedelta(weeks=1)).isoformat(),
            },
        },
    )
    assert comparison["status"] == "complete" and comparison["investigation"]
    await link("investigation", comparison["investigation"])
    assert all(h["causal_status"] != "supported_effect"
               for h in comparison["investigation"]["hypotheses"])
    outcome_start = datetime.now(UTC) + timedelta(minutes=2)
    outcome_end = outcome_start + timedelta(weeks=1)
    decision = await client.request(
        "POST",
        "intelligence/decisions",
        {
            "operation_id": str(uuid4()),
            "title": "Observe recovery",
            "investigation_id": comparison["investigation"]["id"],
            "learning_enabled": False,
            "thread_id": fixture.thread["thread_id"],
            "outcome_window": {
                "start": outcome_start.isoformat(),
                "end": outcome_end.isoformat(),
            },
            "options": [
                {
                    "id": "observe",
                    "description": "Observe sales after review",
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
    for operation in ("select", "approve"):
        decision = await client.request(
            "POST",
            f"intelligence/decisions/{decision['id']}/operations",
            {
                "operation_id": str(uuid4()),
                "expected_revision": decision["revision"],
                "operation": operation,
                **({"option_id": "observe"} if operation == "select" else {}),
            },
        )
    await link("decision", decision)
    payload = {
        "idempotency_key": str(uuid4()),
        "decision_id": decision["id"],
        "expected_decision_revision": decision["revision"],
        "option_id": "observe",
        "configuration": {
            "name": "Observe authorized revenue",
            "agent_id": fixture.agent["agent_id"],
            "semantic": fixture.semantic,
            "plan": {"metrics": ["revenue", "orders", "coverage"]},
            "value_column": "revenue",
            "count_column": "orders",
            "completeness_column": "coverage",
            "minimum_samples": 2,
            "time_dimension": "ordered_at",
            "enabled": True,
        },
    }
    action = await client.request("POST", "intelligence/actions/preview", payload)
    assert action["status"] == "awaiting_approval" and action["dispatch_attempts"] == 0
    premature = await client.http.post(
        f"/api/v1/intelligence/actions/{action['id']}/execute",
        json={
            "operation_id": str(uuid4()),
            "expected_revision": action["revision"],
            "thread_id": fixture.thread["thread_id"],
        },
    )
    assert premature.status_code in {403, 409}
    foreign = await fixture.secondary.http.get(f"/api/v1/intelligence/actions/{action['id']}")
    assert foreign.status_code == 404
    action = await client.request(
        "POST",
        f"intelligence/actions/{action['id']}/review",
        {
            "operation_id": str(uuid4()),
            "expected_revision": action["revision"],
            "operation": "approve",
        },
    )
    action = await _consented(fixture, action)
    assert action["status"] == "verified" and action["verification"]["complete"]
    assert action["dispatch_attempts"] == 1 and action["receipt"]["schedule_enabled"]
    await link("action", action)
    pending_outcome = await client.request(
        "POST", f"intelligence/decisions/{decision['id']}/evaluate-outcome",
    )
    assert pending_outcome["status"] == "pending" and pending_outcome["actual"] is None
    assert pending_outcome["action_ids"] == [action["id"]]
    assert pending_outcome["dimensions"]["attributed_business_impact"] is None
    await link("outcome", pending_outcome)
    # Advance only the observation clock; SQL, policy, storage, and consent stay live.
    # These seeded future observations test lineage, not a real intervention effect.
    from zoneinfo import ZoneInfo

    from app.modules.intelligence import engine

    observed_at = (outcome_start + timedelta(days=1)).astimezone(
        ZoneInfo("Asia/Jakarta")
    ).replace(tzinfo=None)
    await db.execute_system(
        f"INSERT INTO {fixture.database}.sales VALUES "
        "(1001,'Jakarta',80,%s,1),(1002,'Jakarta',80,%s,1),(1003,'Jakarta',80,%s,1)",
        [observed_at] * 3,
    )
    with monkeypatch.context() as clock:
        clock.setattr(engine, "utc_now", lambda: outcome_end + timedelta(seconds=1))
        outcome = await client.request(
            "POST", f"intelligence/decisions/{decision['id']}/evaluate-outcome",
        )
    assert outcome["status"] == "complete" and outcome["actual"] == 240
    assert outcome["completeness"] == 1 and outcome["evidence"]
    assert outcome["action_ids"] == [action["id"]] and outcome["learning_refs"] == []
    assert outcome["attribution"] == "observed_after"
    assert outcome["dimensions"]["attributed_business_impact"] is None
    assert outcome["dimensions"]["action_business_effect_verified"] is None
    await link("outcome", outcome)
    assert mission["status"] == "completed"
    context = await client.request("POST", f"intelligence/decisions/{decision['id']}/context")
    graph = await client.request("GET", f"intelligence/context/{context['root_id']}/graph")
    assert any(node["kind"] == "outcome" and node["reference_id"] == outcome["id"]
               for node in graph["nodes"])
    memo = await client.request(
        "POST", f"agents/studio/missions/{mission['mission_id']}/deliverables",
        {"expected_revision": mission["revision"], "kind": "decision_memo",
         "operation_id": "controlled-memo"},
    )
    assert outcome["id"] in memo["markdown"]
    agent_id = fixture.agent["agent_id"]
    await client.request(
        "POST", f"agents/{agent_id}/quality/cases",
        {"name": "Controlled dispatch budget", "prompt": "Observe recovery",
         "production_match": "all_traces", "assertions": [
             {"scorer": "action_verification", "expected": {"verified": True}},
             {"scorer": "efficiency", "expected": {"tool_calls": 0}},
         ]},
    )
    configuration = await client.request(
        "PUT", f"agents/{agent_id}/quality/monitoring",
        {"enabled": True, "sample_rate": 1, "max_traces": 20, "cadence_minutes": 60},
    )
    await assistant_repository.append_message(
        fixture.thread["thread_id"], user_name=fixture.principal, role="assistant",
        content="Controlled action verification", agent_id=agent_id,
        security_context=observation_context(session_security(fixture.user)),
        steps=[{"kind": "tool", "name": "execute_business_action"}, {
            "kind": "quality_observation", "manifest_id": manifest["id"],
            "version_id": manifest["version_id"], "counts": {"tool_calls": 1},
            "facts": {"action_verification": {"verified": action["verification"]["complete"]}},
        }],
    )
    assessment = await run_once(
        "agents.quality", InternalTaskConfiguration(
            scope=Scope.from_user(fixture.user), record_id=configuration["id"],
        ), fixture.user,
    )
    assert assessment["scored"] == 1
    proposals = await client.request("GET", f"agents/{agent_id}/quality/proposals")
    assert proposals["items"] and proposals["items"][0]["status"] == "proposed"
    schedule = await task_orchestration_repository.get_task(action["receipt"]["task_id"])
    assert schedule["owner_role"] == fixture.role and schedule["created_by"] == fixture.principal
    frozen = json.loads(schedule["handler_config"])
    assert frozen["scope"] == Scope.from_user(fixture.user).model_dump(
        mode="json",
        exclude={"session_id"},
    ) | {"session_id": None}
    repeated = await client.request("POST", "intelligence/actions/preview", payload)
    assert repeated["id"] == action["id"] and repeated["dispatch_attempts"] == 1
    changed = deepcopy(payload)
    changed["configuration"]["name"] = "Changed monitor"
    assert (
        await client.http.post("/api/v1/intelligence/actions/preview", json=changed)
    ).status_code == 409
    compensated = await _consented(fixture, action, compensate=True)
    assert compensated["status"] == "compensated" and compensated["compensation"]["complete"]
    assert compensated["receipt"] == action["receipt"]
    assert not compensated["compensation_receipt"]["schedule_enabled"]
    disabled = await task_orchestration_repository.get_task(schedule["id"])
    assert disabled["schedule_kind"] == "manual" and disabled["schedule_expr"] is None
    for resolution in ("deny", "cancel"):
        alternative = {**payload, "idempotency_key": str(uuid4())}
        candidate = await client.request("POST", "intelligence/actions/preview", alternative)
        if resolution == "cancel":
            closed = await client.request(
                "POST",
                f"intelligence/actions/{candidate['id']}/cancel",
                {
                    "operation_id": str(uuid4()),
                    "expected_revision": candidate["revision"],
                    "thread_id": fixture.thread["thread_id"],
                },
            )
            assert closed["status"] == "cancelled" and closed["dispatch_attempts"] == 0
        else:
            candidate = await client.request(
                "POST",
                f"intelligence/actions/{candidate['id']}/review",
                {
                    "operation_id": str(uuid4()),
                    "expected_revision": candidate["revision"],
                    "operation": "approve",
                },
            )
            closed = await _consented(fixture, candidate, allow=False)
            assert closed["status"] == "denied" and closed["dispatch_attempts"] == 0
    await access_control_service.revoke_role(
        fixture.operator, role=fixture.role, username=fixture.principal
    )
    revoked = await client.http.post(
        "/api/v1/intelligence/actions/preview",
        json={
            **payload,
            "idempotency_key": str(uuid4()),
        },
    )
    assert revoked.status_code in {403, 404, 409}, "Revoked assignments cannot create actions"
