"""Authenticated Studio and semantic-review flows on the isolated engine stack."""

import json
import secrets
from uuid import uuid4

import httpx
import pytest

from app.core.database import db
from app.main import create_app
from app.modules.agents.memory import memory_repository
from app.modules.agents.repository import agent_repository
from app.modules.agents.rule_proposals import rule_proposal_repository
from app.modules.agents.run_journal import run_journal
from app.modules.assistant.repository import assistant_repository
from app.modules.intelligence.semantic_view_schema import ensure_semantic_view_schema
from tests.benchmark.business_intelligence.bootstrap import bootstrap
from tests.benchmark.business_intelligence.client import StudioClient
from tests.benchmark.business_intelligence.dataset import DATABASE, load
from tests.benchmark.business_intelligence.generate import generate
from tests.benchmark.business_intelligence.model import teaching_changes
from tests.integration import test_intelligence_metadata

intelligence_db = test_intelligence_metadata.intelligence_db

pytestmark = pytest.mark.engine


@pytest.fixture
async def studio_engine(intelligence_db, tmp_path):
    from app.common.nova_system import init_task_orchestration

    await init_task_orchestration()
    from app.common.nova_system import ML_RUNS_DDL

    await db.execute_system(ML_RUNS_DDL)
    await ensure_semantic_view_schema()
    for repository in (
        agent_repository,
        assistant_repository,
        memory_repository,
        rule_proposal_repository,
        run_journal,
    ):
        await repository.ensure_schema()
    generate(tmp_path / "data")
    await load(db.execute_system, tmp_path / "data", replace=True)
    suffix = uuid4().hex[:12]
    username, role = f"bi_{suffix}", f"BI_{suffix}"
    password = secrets.token_urlsafe(24)
    await db.execute_system(f"CREATE ROLE `{role}`")
    await db.execute_system(f"CREATE USER '{username}' IDENTIFIED BY %s", [password])
    await db.execute_system(f"GRANT SELECT ON ALL TABLES IN DATABASE {DATABASE} TO ROLE `{role}`")
    await db.execute_system(f"GRANT `{role}` TO USER '{username}'")
    await db.execute_system(f"SET DEFAULT ROLE `{role}` TO '{username}'")
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://nova.test", timeout=180
    ) as http:
        client = StudioClient(http)
        try:
            await client.login(username, password, role)
            yield client, suffix, tmp_path / "data"
        finally:
            try:
                if http.headers.get("Authorization"):
                    await client.request("POST", "auth/logout")
            finally:
                await db.execute_system(f"DROP USER '{username}'")
                await db.execute_system(f"DROP ROLE `{role}`")


async def test_randomized_analysis_uses_complete_assignment_cohort(studio_engine, monkeypatch):
    from statistics import variance
    from unittest.mock import AsyncMock

    from app.modules.agents.semantic.serialize import to_ossie_document
    from app.modules.intelligence import decision_lab
    from tests.benchmark.business_intelligence.model import expression

    client, suffix, _ = studio_engine
    table = "causal_" + suffix
    await db.execute_system(
        f"CREATE TABLE {DATABASE}.{table} "
        "(unit BIGINT, arm INT, outcome DOUBLE) PRIMARY KEY(unit) "
        "DISTRIBUTED BY HASH(unit) BUCKETS 1 PROPERTIES('replication_num'='1')"
    )
    try:
        rows = [(arm * 40 + i, arm, i + arm * 10) for arm in (0, 1) for i in range(40)]
        await db.execute_system(
            f"INSERT INTO {DATABASE}.{table} VALUES " + ",".join(["(%s,%s,%s)"] * len(rows)),
            [value for row in rows for value in row],
        )
        definition = {
            "version": "0.1.1",
            "name": "assignment_outcomes",
            "datasets": [
                {
                    "name": "assigned",
                    "source": f"{DATABASE}.{table}",
                    "primary_key": ["unit"],
                    "fields": [
                        {
                            "name": name,
                            "expression": expression(name),
                            "datatype": datatype,
                            **({"dimension": {}} if name != "outcome" else {}),
                        }
                        for name, datatype in (
                            ("unit", "Integer"),
                            ("arm", "Integer"),
                            ("outcome", "Decimal"),
                        )
                    ],
                }
            ],
            "relationships": [],
            "metrics": [
                {
                    "name": "outcome",
                    "base_dataset": "assigned",
                    "expression": expression("SUM(assigned.outcome)"),
                    "datatype": "Decimal",
                    "additivity": "additive",
                }
            ],
            "ai_context": {
                "experiments": {
                    "assignment": {
                        "design": "randomized",
                        "assignment": "independent",
                        "protocol_reference": "synthetic-complete-randomized-protocol",
                        "unit": "assigned.unit",
                        "arm": "assigned.arm",
                    }
                }
            },
        }
        import json

        view = await client.request(
            "POST",
            "semantic-views",
            {
                "name": "assignment_outcomes",
                "database": DATABASE,
                "schema_name": suffix,
                "definition": json.dumps(to_ossie_document(definition)),
            },
        )
        view_id = view["id"]
        assert (await client.request("POST", f"semantic-views/{view_id}/versions/1/validate"))[
            "valid"
        ]
        await client.request("POST", f"semantic-views/{view_id}/versions/1/publish", {})
        view = await client.request("GET", f"semantic-views/{view_id}")
        body = {
            "operation_id": uuid4().hex,
            "method": "causal_effect",
            "semantic": {
                "view_id": view_id,
                "version": 1,
                "fingerprint": view["versions"][0]["fingerprint"],
            },
            "plan": {
                "metrics": ["outcome"],
                "dimensions": ["assigned.unit", "assigned.arm"],
                "limit": 100,
            },
            "value_column": "outcome",
            "experiment": "assignment",
        }
        query = AsyncMock(wraps=decision_lab.intelligence_service.query)
        monkeypatch.setattr(decision_lab.intelligence_service, "query", query)
        result = await client.request("POST", "intelligence/decision-lab/analyses", body)
        assert result["effect"] == 10
        margin = 1.96 * (2 * variance(range(40)) / 40) ** 0.5
        assert result["lower_bound"] == pytest.approx(10 - margin)
        assert result["upper_bound"] == pytest.approx(10 + margin)
        assert result["causal_status"] == "supported_effect" and result["run_id"]
        assert (
            result["evidence"][0]["scope"]["principal"]
            == (await client.request("GET", "auth/me"))["username"]
        )
        assert query.await_count == 1
        body["operation_id"] = uuid4().hex
        body["plan"]["having"] = [{"metric": "outcome", "operator": ">", "value": 10}]
        response = await client.http.post("/api/v1/intelligence/decision-lab/analyses", json=body)
        assert response.status_code == 422
        assert "complete unfiltered assignment cohort" in response.json()["detail"]
        assert query.await_count == 1
    finally:
        await db.execute_system(f"DROP TABLE {DATABASE}.{table}")


async def test_bootstrap_and_review_use_production_api_with_restricted_identity(
    studio_engine, monkeypatch
):
    client, suffix, fixture_directory = studio_engine
    resources = await bootstrap(client, namespace=f"api_{suffix}")
    assert set(resources["agents"]) == {"Finance", "Marketing", "Operations", "Executive"}
    view_id = resources["view_id"]
    initial = await client.request("GET", f"semantic-views/{view_id}")
    assert initial["active_version"] == 1
    assert "net_booked_revenue" not in {
        item["name"] for item in initial["versions"][0]["definition"]["metrics"]
    }
    from tests.benchmark.business_intelligence.scripted_provider import ScriptedBoundary

    statement = (
        "Recognized revenue includes only completed and fulfilled orders, less posted refunds."
    )
    provider = ScriptedBoundary(statements={statement: "revenue_definition"})
    provider.install(monkeypatch)
    finance = resources["agents"]["Finance"]
    taught = await client.turn(finance, statement, learning=True)
    assert taught.finish_reason == "stop"
    excluded = await client.turn(
        finance, "This evaluation must never enter business memory.", learning=False
    )
    assert excluded.finish_reason == "stop"
    pending = await assistant_repository.pending_learning(
        user_name=resources["principal"], agent_id=finance, limit=100
    )
    assert [row["thread_id"] for row in pending] == [taught.thread_id]
    from app.core.redis import session_store
    from app.modules.intelligence.contracts import Scope
    from app.modules.task_orchestration.internal_handlers import InternalTaskConfiguration, run_once

    identity = await client.request("GET", "auth/me")
    user = {**await session_store.get(identity["session_id"]), "session_id": identity["session_id"]}
    config = InternalTaskConfiguration(
        scope=Scope.from_user(user),
        agent_id=finance,
        source_thread_id=taught.thread_id,
        source_message_id=pending[0]["message_id"],
    )
    queued = await client.request("POST", f"agents/{finance}/knowledge/consolidate")
    assert queued["queued"] == 1
    result = await run_once("intelligence.consolidate", config, user)
    assert result == {"status": "complete", "extracted": 1}
    assert await run_once("intelligence.consolidate", config, user) == {
        "status": "excluded",
        "extracted": 0,
    }
    history = await client.request("GET", f"agents/{finance}/threads/{taught.thread_id}")
    source_message = next(
        row for row in history["messages"] if row["message_id"] == config.source_message_id
    )
    traces = [step for step in source_message["steps"] if step["name"] == "knowledge_consolidation"]
    assert len(traces) == 1 and traces[0]["status"] == "done"
    assert traces[0]["trace_detail"]["cost_status"] == "unavailable"
    assert traces[0]["trace_detail"]["cost_dollars"] is None
    memories = await client.request("GET", f"agents/{finance}/memories")
    assert memories["memories"][0]["knowledge"]["state"] == "HYPOTHESIS"
    memory_id = memories["memories"][0]["memory_id"]
    evidence = await client.request("GET", f"agents/{finance}/memories/{memory_id}/evidence")
    source_ref = evidence["items"][0]["reference"]
    assert source_ref["scope"] == Scope.from_user(user).model_dump(
        mode="json", exclude={"session_id"}
    )
    assert "session_id" not in json.dumps(evidence)
    assert identity["session_id"] not in json.dumps(evidence)
    assert source_ref["source_id"] == config.source_message_id
    assert source_ref["observed_at"] is not None
    assert memories["memories"][0]["knowledge"]["confidence"]["value"] is None
    learned = await client.review_changes(
        view_id, resources["agents"]["Finance"], teaching_changes()
    )
    assert learned["active_version"] == 2
    for agent_id in resources["agents"].values():
        checked = await client.request(
            "POST", f"agents/{agent_id}/access/verify", {"role_name": resources["active_role"]}
        )
        assert checked["all_granted"]
    active = next(row for row in learned["versions"] if row["version"] == 2)
    semantic = {"view_id": view_id, "version": 2, "fingerprint": active["fingerprint"]}
    reviewed_ids = []
    for kind, name, statement in (
        ("metric", "net_booked_revenue", None),
        ("filter", "greater_jakarta", "Our core Jakarta scope uses orders located in Jakarta."),
        ("dimension", "orders.city", "City is the geographic dimension on each order."),
        ("heuristic", None, "Stockout recovery prioritizes locations with sustained demand."),
    ):
        if statement:
            memory_id = await memory_repository.upsert(
                user_name=user["username"],
                agent_id=finance,
                role_name=user["active_role"],
                fact_key=f"review_{kind}",
                fact=statement,
                source_quote=statement,
                source_thread_id=taught.thread_id,
                source_message_id=f"fixture-{kind}",
                existing_id=None,
            )
            revision = 1
        else:
            row = memories["memories"][0]
            memory_id, revision = row["memory_id"], row["knowledge"]["revision"]
        reviewed = await client.request(
            "POST",
            f"agents/{finance}/memories/{memory_id}/review",
            {
                "operation_id": f"public-review-{kind}",
                "expected_revision": revision,
                "operation": "verify",
                "publish_shared": True,
                "semantic": semantic,
                "definition_kind": kind,
                "definition_name": name,
                "note": "Reviewed against the published business definition and source evidence.",
            },
        )
        assert reviewed["state"] == "VERIFIED" and reviewed["visibility"] == "DOMAIN"
        reviewed_ids.append(memory_id)
    from app.modules.agents.memory import shared_memories

    shared = await shared_memories(resources["agents"]["Executive"], user)
    assert {row["memory_id"] for row in shared} == set(reviewed_ids)
    assert {row["source_agent_id"] for row in shared} == {finance}
    context = await client.request("POST", f"agents/{finance}/memories/{reviewed_ids[0]}/context")
    graph = await client.request("GET", f"intelligence/context/{context['root_id']}/graph")
    assert {node["kind"] for node in graph["nodes"]} == {"rule", "semantic_view"}
    assert len(graph["edges"]) == 1 and graph["edges"][0]["relationship"] == "applies_to"
    from app.modules.agents.semantic.planning import SemanticPlan

    question = "What is our net booked revenue across the complete observation dataset?"
    provider.query_plans[question] = SemanticPlan(
        metrics=("net_booked_revenue",), limit=5
    ).as_dict()
    answer = await client.turn(finance, question, learning=True)
    assert answer.finish_reason == "stop"
    assert any(
        step.get("name") == "semantic_query" and step.get("status") == "done"
        for step in answer.message["steps"]
    )
    await client.like(finance, answer)
    candidates = await client.request("GET", f"agents/{finance}/verified-query-candidates")
    assert candidates["count"] == 1
    assert candidates["candidates"][0]["status"] == "pending"
    result = await client.request(
        "POST",
        f"semantic-views/{view_id}/query",
        {
            "metrics": ["net_booked_revenue"],
            "limit": 5,
        },
    )
    from tests.benchmark.business_intelligence.gold.queries import recognized_revenue

    sql, params = recognized_revenue("2026-01-01", "2026-03-26")
    gold = await db.execute_system(sql, params)
    assert float(result["rows"][0][0]) == float(gold["rows"][0][0])

    from tests.benchmark.business_intelligence.evaluate import evaluate
    from tests.benchmark.business_intelligence.gold.cases import Case

    provider.catalog_planning = True
    development_question = (
        "Hitung net booked revenue di Surabaya, 2026-01-03 sampai sebelum 2026-01-11."
    )
    development_turn = await client.turn(finance, development_question, learning=False)
    development_case = Case(
        id="development-revenue-scoring",
        category="canonical_metric",
        question=development_question,
        learning_sensitive=True,
        expected_plan={
            "metrics": ["net_booked_revenue"],
            "filters": [{"field": "orders.city", "operator": "=", "value": "Surabaya"}],
            "time": {"dimension": "orders.ordered_at", "range": "2026-01-03..2026-01-10"},
            "limit": 100,
        },
        parameters={
            "metric": "net_booked_revenue",
            "city": "Surabaya",
            "start": "2026-01-03",
            "end": "2026-01-11",
            "daily": False,
        },
    )
    score = await evaluate(development_case, development_turn, db.execute_system, repetition=0)
    assert score["scores"]["answer_accuracy"] is True, development_turn.message["steps"]
    assert score["silent_wrong"] is False

    from tests.benchmark.business_intelligence.dataset import verify_observations
    from tests.benchmark.business_intelligence.outcome_training import TRAINING_DATABASE, prepare

    await prepare(db.execute_system)
    try:
        await db.execute_system(
            f"GRANT SELECT ON ALL TABLES IN DATABASE {TRAINING_DATABASE} "
            f"TO ROLE `{resources['active_role']}`"
        )
        training = await bootstrap(
            client, namespace=f"outcomes_{suffix}", database=TRAINING_DATABASE
        )
        training_view = await client.review_changes(
            training["view_id"], training["agents"]["Finance"], teaching_changes()
        )
        for agent_id in training["agents"].values():
            verified = await client.request(
                "POST", f"agents/{agent_id}/access/verify", {"role_name": training["active_role"]}
            )
            assert verified["all_granted"], verified["items"]
        stories = await exercise_development_stories(client, training, training_view, monkeypatch)
        assert (await client.request("GET", f"semantic-views/{view_id}"))["active_version"] == 2
        observation_manifest = await verify_observations(db.execute_system, fixture_directory)
        import os
        from pathlib import Path

        from tests.benchmark.business_intelligence.stories import write_report

        if os.getenv("NOVA_INTELLIGENCE_STORY_REPORT"):
            write_report(
                Path(os.environ["NOVA_INTELLIGENCE_STORY_REPORT"]),
                stories,
                observation_manifest=observation_manifest,
            )
    finally:
        await db.execute_system(f"DROP DATABASE {TRAINING_DATABASE}")
    # A denied union branch must fail authorization even when all branches are empty.
    await db.execute_system(
        f"CREATE TABLE {DATABASE}.private_probe (id INT) DUPLICATE KEY(id) "
        "DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1')"
    )
    try:
        await db.execute_system(
            f"REVOKE SELECT ON ALL TABLES IN DATABASE {DATABASE} "
            f"FROM ROLE `{resources['active_role']}`"
        )
        await db.execute_system(
            f"GRANT SELECT ON TABLE {DATABASE}.orders TO ROLE `{resources['active_role']}`"
        )
        from app.modules.intelligence.semantic_views import semantic_view_service

        assert not await semantic_view_service._source_access(
            {
                "datasets": [
                    {"source": f"{DATABASE}.orders"},
                    {"source": f"{DATABASE}.private_probe"},
                ]
            },
            user,
        )
    finally:
        await db.execute_system(f"DROP TABLE {DATABASE}.private_probe")


async def exercise_development_stories(client, resources, learned, monkeypatch):
    """Development fixtures only; these are not frozen B0/B1/B2 observations."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.modules.intelligence import decisions, engine

    active = next(row for row in learned["versions"] if row["version"] == learned["active_version"])
    semantic = {
        "view_id": resources["view_id"],
        "version": active["version"],
        "fingerprint": active["fingerprint"],
    }
    policy = await client.request("GET", "intelligence/business-policy")
    reports = []
    admin_name = "bi_admin_" + uuid4().hex[:10]
    admin_password = secrets.token_urlsafe(24)
    await db.execute_system("CREATE ROLE IF NOT EXISTS ACCOUNTADMIN")
    await db.execute_system(f"CREATE USER '{admin_name}' IDENTIFIED BY %s", [admin_password])
    await db.execute_system(f"GRANT ACCOUNTADMIN TO USER '{admin_name}'")
    async with httpx.AsyncClient(
        transport=client.http._transport, base_url="http://nova.test", timeout=180
    ) as http:
        admin = StudioClient(http)
        try:
            await admin.login(admin_name, admin_password, "ACCOUNTADMIN")
            await admin.request(
                "PUT",
                "intelligence/business-policy",
                {
                    **policy,
                    "maximum_cost": 2000,
                    "reviewer_roles": [resources["active_role"]],
                },
            )
            pending_outcomes = []
            for name, start_day, days, city, action in (
                ("Jakarta stockout recovery", 12, 7, "Jakarta", "inventory_transfer"),
                ("Payment-service degradation", 5, 4, None, "rollback"),
            ):
                start = datetime(2026, 3, start_day, tzinfo=ZoneInfo("Asia/Jakarta"))
                end = start + timedelta(days=days)
                monkeypatch.setattr(decisions, "utc_now", lambda end=end: end)
                monkeypatch.setattr(engine, "utc_now", lambda end=end: end)
                related_ids = []
                for label, metrics, time_field, filters in (
                    (
                        "Inventory availability" if city else "Payment-service latency",
                        ["inventory_units", "inventory_samples"]
                        if city
                        else ["payment_latency", "service_samples"],
                        "inventory_snapshots.observed_at"
                        if city
                        else "service_metrics.observed_at",
                        [
                            {"field": "inventory_snapshots.city", "operator": "=", "value": city},
                            {"field": "products.category", "operator": "=", "value": "Electronics"},
                        ]
                        if city
                        else [
                            {
                                "field": "service_metrics.service_id",
                                "operator": "=",
                                "value": "payment-service",
                            },
                        ],
                    ),
                    (
                        "Marketing spend",
                        ["campaign_spend", "spend_samples"],
                        "campaign_spend.spent_at",
                        [{"field": "campaign_spend.city", "operator": "=", "value": city}]
                        if city
                        else [],
                    ),
                ):
                    related = await client.request(
                        "POST",
                        "intelligence/monitors",
                        {
                            "operation_id": str(uuid4()),
                            "configuration": {
                                "name": label,
                                "agent_id": resources["agents"][
                                    "Operations" if label != "Marketing spend" else "Marketing"
                                ],
                                "semantic": semantic,
                                "plan": {"metrics": metrics, "filters": filters},
                                "value_column": metrics[0],
                                "count_column": metrics[1],
                                "time_dimension": time_field,
                                "minimum_samples": 2,
                                "window_hours": days * 24,
                            },
                        },
                    )
                    related_ids.append(related["id"])
                timeline = [
                    {
                        "kind": "deployment",
                        "time_dimension": "deployments.deployed_at",
                        "time_column": "deployments.deployed_at",
                        "identity_column": "deployments.deployment_id",
                        "label_columns": ["deployments.service_id"],
                        "preceding_hours": 0,
                        "plan": {
                            "dimensions": [
                                "deployments.deployed_at",
                                "deployments.deployment_id",
                                "deployments.service_id",
                            ],
                            "limit": 31,
                        },
                    }
                ]
                monitor = await client.request(
                    "POST",
                    "intelligence/monitors",
                    {
                        "operation_id": str(uuid4()),
                        "configuration": {
                            "name": name,
                            "agent_id": resources["agents"]["Finance"],
                            "semantic": semantic,
                            "plan": {
                                "metrics": [
                                    "net_booked_revenue",
                                    "order_count",
                                    "order_completeness",
                                ],
                                "filters": [
                                    {"field": "orders.city", "operator": "=", "value": city}
                                ]
                                if city
                                else [],
                            },
                            "value_column": "net_booked_revenue",
                            "count_column": "order_count",
                            "completeness_column": "order_completeness",
                            "time_dimension": "orders.ordered_at",
                            "driver_dimensions": ["orders.category"],
                            "related_monitor_ids": related_ids,
                            "timeline_sources": timeline,
                            "baseline_weeks": 6,
                            "relative_threshold": 0.05,
                            "minimum_samples": 2,
                            "window_hours": days * 24,
                        },
                    },
                )
                observed = await client.request(
                    "POST",
                    f"intelligence/monitors/{monitor['id']}/run",
                    {
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                    },
                )
                assert observed["news_id"], (name, observed["detection"])
                investigation = await client.request(
                    "POST", f"intelligence/news/{observed['news_id']}/investigate"
                )
                assert all(row["reconciled"] for row in investigation["decompositions"])
                assert all(
                    row["causal_status"] in {"association", "arithmetic"}
                    for row in investigation["hypotheses"]
                )
                deployments = [
                    row for row in investigation["timeline"] if row["kind"] == "deployment"
                ]
                assert deployments
                assert all(row["causal_status"] == "association" for row in deployments)
                cross_domain = {
                    row["label"]: row
                    for row in investigation["timeline"]
                    if row["kind"] == "related_observation"
                }
                assert (
                    cross_domain["Marketing spend"]["before"]
                    == cross_domain["Marketing spend"]["after"]
                )
                driver = cross_domain[
                    "Inventory availability" if city else "Payment-service latency"
                ]
                assert (
                    (driver["before"] > 0 and driver["after"] == 0)
                    if city
                    else (driver["after"] > 10 * driver["before"])
                )
                assert all(row["causal_status"] == "association" for row in cross_domain.values())
                from tests.benchmark.business_intelligence.outcome_training import TRAINING_DATABASE

                independent_cross_domain = {}
                for label, select, source, predicate in (
                    (
                        "Inventory availability" if city else "Payment-service latency",
                        "SUM(s.sellable_units)" if city else "AVG(s.latency_ms)",
                        "inventory_snapshots s JOIN "
                        + TRAINING_DATABASE
                        + ".products p ON s.product_id=p.product_id"
                        if city
                        else "service_metrics s",
                        "s.city='Jakarta' AND p.category='Electronics'"
                        if city
                        else "s.service_id='payment-service'",
                    ),
                    (
                        "Marketing spend",
                        "SUM(s.spend)",
                        "campaign_spend s",
                        "s.city='Jakarta'" if city else "1=1",
                    ),
                ):
                    time_column = "observed_at" if label != "Marketing spend" else "spent_at"
                    values = {}
                    for period, window_start in (
                        ("before", start - timedelta(weeks=1)),
                        ("after", start),
                    ):
                        gold = await db.execute_system(
                            f"SELECT {select} FROM {TRAINING_DATABASE}.{source} WHERE {predicate} "
                            f"AND s.{time_column}>=%s AND s.{time_column}<%s",
                            [
                                window_start.strftime("%Y-%m-%d %H:%M:%S"),
                                (window_start + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"),
                            ],
                        )
                        values[period] = float(gold["rows"][0][0])
                        assert values[period] == cross_domain[label][period]
                    independent_cross_domain[label] = values
                news = await client.request("GET", f"intelligence/news/{observed['news_id']}")
                units = news["after"] / 500000
                outcome_start = datetime(2026, 3, 26, tzinfo=ZoneInfo("Asia/Jakarta"))
                if pending_outcomes:
                    outcome_start += timedelta(days=7)
                decision = await client.request(
                    "POST",
                    "intelligence/decisions",
                    {
                        "operation_id": str(uuid4()),
                        "title": name,
                        "investigation_id": investigation["id"],
                        "learning_enabled": True,
                        "outcome_window": {
                            "start": outcome_start.isoformat(),
                            "end": (outcome_start + timedelta(days=days)).isoformat(),
                        },
                        "options": [
                            {
                                "id": "recovery",
                                "description": (
                                    "Conditional recovery assuming average unit value "
                                    "IDR 500000 and "
                                    "25% more units"
                                ),
                                "simulation": {
                                    "action_type": action,
                                    "baseline_units": units,
                                    "price": 500000,
                                    "unit_cost": 200000,
                                    "expected_unit_change": units * 0.25,
                                    "unit_change_uncertainty": units * 0.1,
                                    "action_cost": 1000,
                                    "capacity": units * 2,
                                    "max_budget": 2000,
                                },
                            },
                            {
                                "id": "conservative",
                                "description": "Conditional 10% recovery with a smaller allocation",
                                "simulation": {
                                    "action_type": action,
                                    "baseline_units": units,
                                    "price": 500000,
                                    "unit_cost": 200000,
                                    "expected_unit_change": units * 0.1,
                                    "unit_change_uncertainty": units * 0.05,
                                    "action_cost": 500,
                                    "capacity": units * 1.2,
                                    "max_budget": 2000,
                                },
                            },
                        ],
                    },
                )
                selected = await client.request(
                    "POST",
                    f"intelligence/decisions/{decision['id']}/operations",
                    {
                        "operation_id": str(uuid4()),
                        "expected_revision": decision["revision"],
                        "operation": "select",
                        "option_id": "recovery",
                    },
                )
                assert selected["status"] == "awaiting_approval"
                approved = await client.request(
                    "POST",
                    f"intelligence/decisions/{decision['id']}/operations",
                    {
                        "operation_id": str(uuid4()),
                        "expected_revision": selected["revision"],
                        "operation": "approve",
                    },
                )
                assert approved["status"] == "approved"
                assert len(decision["options"]) == 2
                from math import isclose

                for option in decision["options"]:
                    factor = 1.25 if option["id"] == "recovery" else 1.1
                    assert isclose(option["prediction"], news["after"] * factor)
                    assert isclose(
                        option["incremental_gross_profit"], news["after"] * (factor - 1) * 0.6
                    )
                assert {option["method"] for option in decision["options"]} == {
                    "conditional-unit-economics-v1"
                }
                pending_outcomes.append((decision, investigation, city, independent_cross_domain))
                waiting = await client.request(
                    "POST", f"intelligence/decisions/{decision['id']}/evaluate-outcome"
                )
                assert waiting["status"] == "pending" and waiting["actual"] is None

            # Reveal observations only after both decisions and their approvals exist.
            from tests.benchmark.business_intelligence.outcome_training import reveal_future_orders

            monkeypatch.setattr(
                engine, "utc_now", lambda: datetime(2026, 4, 7, tzinfo=ZoneInfo("Asia/Jakarta"))
            )
            for decision, _, _, _ in pending_outcomes:
                missing = await client.request(
                    "POST", f"intelligence/decisions/{decision['id']}/evaluate-outcome"
                )
                assert missing["status"] == "missing_data" and missing["actual"] is None
            before_learning = await client.request(
                "GET", f"agents/{resources['agents']['Finance']}/memories"
            )
            assert not before_learning["memories"]
            revealed = await reveal_future_orders(db.execute_system, days=11)
            assert revealed["end"] == "2026-04-06"
            for decision, investigation, city, independent_cross_domain in pending_outcomes:
                outcome = await client.request(
                    "POST", f"intelligence/decisions/{decision['id']}/evaluate-outcome"
                )
                assert outcome["status"] == "complete"
                assert outcome["attribution"] == "observed_after"
                from tests.benchmark.business_intelligence.outcome_training import TRAINING_DATABASE

                bounds = [
                    datetime.fromisoformat(decision["outcome_window"][key])
                    .astimezone(ZoneInfo("Asia/Jakarta"))
                    .strftime("%Y-%m-%d %H:%M:%S")
                    for key in ("start", "end")
                ]
                actual_gold = await db.execute_system(
                    f"SELECT SUM(gross_amount - refund_amount) FROM {TRAINING_DATABASE}.orders "
                    "WHERE status IN ('completed', 'fulfilled') "
                    "AND ordered_at >= %s AND ordered_at < %s" + (" AND city = %s" if city else ""),
                    bounds + ([city] if city else []),
                )
                assert outcome["actual"] == float(actual_gold["rows"][0][0])
                retry = await client.request(
                    "POST", f"intelligence/decisions/{decision['id']}/evaluate-outcome"
                )
                assert (retry["id"], retry["revision"]) == (outcome["id"], outcome["revision"])
                lineage = await client.request(
                    "GET", f"intelligence/decisions/{decision['id']}/lineage"
                )
                assert [row["event"] for row in lineage["events"]] == [
                    "created",
                    "selected",
                    "approved",
                ]
                assert lineage["news"]["revision"] == investigation["news_revision"]
                assert lineage["outcomes"][0]["id"] == outcome["id"]
                memories = await client.request(
                    "GET", f"agents/{resources['agents']['Finance']}/memories"
                )
                candidates = [
                    row
                    for row in memories["memories"]
                    if row["knowledge"]["definition"].get("outcome_id") == outcome["id"]
                ]
                assert len(candidates) == 1
                knowledge = candidates[0]["knowledge"]
                assert knowledge["state"] == "INFERRED"
                assert knowledge["visibility"] == "PRIVATE"
                assert knowledge["authority"] == "observed_outcome"
                assert knowledge["definition"]["outcome_revision"] == outcome["revision"]
                assert knowledge["semantic"] == semantic
                from tests.benchmark.business_intelligence.scripted_provider import ScriptedBoundary

                followup_question = (
                    "Review the net_booked_revenue prediction and observed outcome; "
                    "distinguish observation from causal attribution."
                )
                memory_reference = candidates[0]["memory_id"]

                class OutcomeBoundary(ScriptedBoundary):
                    context_checked = False

                    def answer(
                        self,
                        *,
                        messages,
                        question=followup_question,
                        reference=memory_reference,
                        actual=outcome["actual"],
                        **kwargs,
                    ):
                        if messages[-1].get("content") == question:
                            context = "\n".join(str(row.get("content") or "") for row in messages)
                            assert reference in context
                            assert "[INFERRED]" in context
                            assert "observed_after" in context
                            self.context_checked = True
                            return {
                                "role": "assistant",
                                "content": (
                                    f"Observed outcome {actual:g} IDR is private inferred "
                                    "knowledge. It does not establish an attributed causal effect."
                                ),
                                "usage": {"prompt_tokens": 30, "completion_tokens": 20},
                            }
                        return super().answer(messages=messages, **kwargs)

                outcome_boundary = OutcomeBoundary()
                outcome_boundary.install(monkeypatch)
                followup = await client.turn(
                    resources["agents"]["Finance"], followup_question, learning=False
                )
                assert followup.finish_reason == "stop" and outcome_boundary.context_checked
                assert "does not establish" in followup.message["content"]
                reports.append(
                    {
                        "title": decision["title"],
                        "news": lineage["news"],
                        "investigation": investigation,
                        "decision": lineage["decision"],
                        "events": lineage["events"],
                        "evidence": lineage["evidence"],
                        "outcome": outcome,
                        "knowledge": knowledge,
                        "independent_actual": float(actual_gold["rows"][0][0]),
                        "independent_cross_domain": independent_cross_domain,
                        "retry_retained_outcome_revision": True,
                        "outcome_states": ["pending", "missing_data", "complete"],
                        "studio_followup": {
                            "thread_id": followup.thread_id,
                            "message_id": followup.message_id,
                            "memory_id": memory_reference,
                            "knowledge_revision": knowledge["revision"],
                            "inferred_context_verified": outcome_boundary.context_checked,
                            "content": followup.message["content"],
                            "evidence_type": "scripted_provider_context_check",
                        },
                    }
                )
            current_view = await client.request("GET", f"semantic-views/{resources['view_id']}")
            assert current_view["active_version"] == active["version"]
        finally:
            try:
                await admin.login(admin_name, admin_password, "ACCOUNTADMIN")
                current = await admin.request("GET", "intelligence/business-policy")
                await admin.request(
                    "PUT",
                    "intelligence/business-policy",
                    {**policy, "revision": current["revision"]},
                )
                await admin.request("POST", "auth/logout")
            finally:
                await db.execute_system(f"DROP USER '{admin_name}'")
    return reports
