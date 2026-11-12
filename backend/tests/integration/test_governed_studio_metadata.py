"""Real-engine persistence, upgrade, and replay contracts for governed Studio."""

import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from psycopg import sql

from app.core.metadata_migrations import migrate
from app.core.metadata_scope import acting_as
from app.modules.access_control.security_context import SecurityContext
from app.modules.agents import quality
from app.modules.agents.releases import get_manifest
from app.modules.agents.repository import agent_repository
from app.modules.agents.run_journal import run_journal
from app.modules.intelligence.action_contracts import Action, ActionEvent, ActionReceipt
from app.modules.intelligence.contracts import MonitorConfiguration, PolicyResult, SemanticRef
from app.modules.intelligence.engine_repository import intelligence_repository
from tests.integration import test_intelligence_metadata

intelligence_db = test_intelligence_metadata.intelligence_db

pytestmark = pytest.mark.engine


@pytest.fixture
async def governed_db(intelligence_db):
    scope = intelligence_db
    user = {
        "username": scope.principal,
        "active_role": scope.active_role,
        "session_id": scope.session_id,
        "security_context_version": scope.security_context_version,
        "assigned_roles": [scope.active_role, "OTHER"],
    }
    return scope, user


async def test_fresh_schema_and_additive_migration_match(governed_db, engine_metadata):
    for _ in range(2):
        await migrate(engine_metadata.role_config("nova_meta_migrator"), app_version="e" * 40)
    for table in (
        "config_agent_release_manifests",
        *[t.lower() for t in quality.QUALITY_TABLES.values()],
        "config_studio_missions",
        "config_studio_resources",
        "config_intelligence_actions",
    ):
        result = await engine_metadata.admin.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='nova_meta' AND table_name=%s",
            (table,),
        )
        assert await result.fetchall()


async def test_quality_case_revisions_and_security_scope(governed_db):
    scope, user = governed_db
    agent_id = uuid4().hex * 2
    case = await quality.save_record(
        "cases",
        {
            "id": uuid4().hex * 2,
            "agent_id": agent_id,
            "name": "Original timeframe",
            "prompt": "Compare Q1 revenue",
            "mandatory": True,
            "critical": True,
            "assertions": [
                {"scorer": "semantic_selection", "required": True, "expected": {"period": "Q1"}}
            ],
        },
        user,
    )
    assert (await quality.records("cases", agent_id, user))[0] == case
    changed = await quality.save_record(
        "cases", {**case, "name": "Pinned period"}, user, case["revision"]
    )
    assert changed["revision"] == 2
    with pytest.raises(HTTPException, match="changed"):
        await quality.save_record("cases", {**case, "name": "Stale write"}, user, case["revision"])
    for change in ({"active_role": "OTHER"}, {"security_context_version": 2}):
        other = {**user, **change}
        async with acting_as(SecurityContext.from_session(other)):
            assert await quality.records("cases", agent_id, other) == []
    foreign = {**user, "username": scope.principal + "-foreign"}
    async with acting_as(SecurityContext.from_session(foreign)):
        with pytest.raises(HTTPException) as error:
            await quality.save_record(
                "cases",
                changed,
                {**user, "username": scope.principal + "-foreign"},
                changed["revision"],
            )
    assert error.value.status_code == 404


@pytest.mark.parametrize(
    ("kind", "payload", "change"),
    [
        (
            "runs",
            {"status": "running", "source": "production", "cases": []},
            {"status": "completed"},
        ),
        (
            "monitoring",
            {"enabled": False, "sample_rate": 0.1, "max_traces": 20},
            {"max_traces": 10},
        ),
        (
            "proposals",
            {"status": "proposed", "category": "regression_case"},
            {"status": "accepted"},
        ),
    ],
)
async def test_full_size_quality_ids_fit_engine_primary_key(
    governed_db, engine_metadata, kind, payload, change
):
    _, user = governed_db
    identifier, agent_id = uuid4().hex * 2, uuid4().hex * 2
    original = await quality.save_record(
        kind, {"id": identifier, "agent_id": agent_id, **payload}, user
    )
    assert await quality.records(kind, agent_id, user, identifier) == [original]
    revised = await quality.save_record(kind, {**original, **change}, user, original["revision"])
    assert revised["revision"] == 2
    assert await quality.records(kind, agent_id, user, identifier) == [revised]
    cursor = await engine_metadata.admin.execute(
        sql.SQL(
            "SELECT id,revision,operation_id FROM nova_meta.{} WHERE id=%s ORDER BY revision"
        ).format(sql.Identifier(quality.QUALITY_TABLES[kind].lower())),
        (identifier,),
    )
    rows = await cursor.fetchall()
    assert [(r["id"], r["revision"]) for r in rows] == [(identifier, 1), (identifier, 2)]
    assert all(len(r["id"]) == 64 and len(r["operation_id"]) == 32 for r in rows)
    assert len({r["operation_id"] for r in rows}) == 2
    other = {**user, "security_context_version": 2}
    async with acting_as(SecurityContext.from_session(other)):
        assert await quality.records(kind, agent_id, other, identifier) == []


async def test_manifest_readback_never_crosses_agent_or_owner(governed_db, engine_metadata):
    scope, _ = governed_db
    identifier, agent_id = uuid4().hex, uuid4().hex
    manifest = {
        "id": identifier,
        "version_id": "draft",
        "dependencies": {"scorer_set_version": "1"},
    }
    async with engine_metadata.importer() as connection:
        await connection.execute(
            "INSERT INTO nova_meta.config_agent_release_manifests "
            "(manifest_id,agent_id,owner_name,version_id,payload,created_at) "
            "VALUES (%s,%s,%s,%s,%s,clock_timestamp())",
            (identifier, agent_id, scope.principal, "draft", json.dumps(manifest)),
        )
    assert await get_manifest(agent_id, scope.principal, manifest_id=identifier) == manifest
    assert (
        await get_manifest(agent_id, scope.principal + "-foreign", manifest_id=identifier) is None
    )
    assert await get_manifest("other-agent", scope.principal, version_id="draft") is None


async def test_action_intent_and_receipt_survive_new_repository_instance(governed_db):
    scope, _ = governed_db
    semantic = SemanticRef(view_id="view", version=1, fingerprint="semantic")
    action = Action(
        id=uuid4().hex,
        scope=scope,
        decision_id=uuid4().hex,
        decision_revision=1,
        decision_digest="decision",
        option_id="monitor",
        semantic=semantic,
        adapter_id="monitor-v1",
        idempotency_key="action-operation",
        request_digest="request",
        status="executing",
        dispatch_attempts=1,
        dispatch_fence="original-worker",
        configuration=MonitorConfiguration(
            name="Governed revenue monitor",
            agent_id="agent",
            semantic=semantic,
            plan={"metrics": ["revenue"]},
            value_column="revenue",
            count_column="sample_count",
            time_dimension="observed_at",
            enabled=True,
        ),
        policy=PolicyResult(
            decision="REQUIRE_APPROVAL",
            reason="Review",
            policy_id="policy",
            policy_revision=1,
            context_digest="policy-context",
        ),
    )
    saved = await intelligence_repository.save("actions", action)
    event = ActionEvent(
        id=uuid4().hex,
        scope=scope,
        action_id=action.id,
        decision_id=action.decision_id,
        action_revision=saved.revision,
        event="executing",
        actor=scope.principal,
        context_digest="dispatch-intent",
        dispatch_fence=action.dispatch_fence,
    )
    await intelligence_repository.save("action_events", event)
    from app.modules.intelligence.engine_repository import IntelligenceRepository

    restored = await IntelligenceRepository().get("actions", action.id, scope, Action)
    assert restored.dispatch_attempts == 1 and restored.dispatch_fence == "original-worker"
    assert restored.receipt is None and restored.status == "executing"
    assert (
        await IntelligenceRepository().get(
            "actions", action.id, scope.model_copy(update={"active_role": "OTHER"}), Action
        )
        is None
    )
    restored_event = await IntelligenceRepository().get(
        "action_events", event.id, scope, ActionEvent
    )
    assert restored_event.action_revision == saved.revision
    assert restored_event.dispatch_fence == restored.dispatch_fence
    receipt = ActionReceipt(
        monitor_id="fixture-monitor",
        monitor_revision=1,
        task_id="fixture-schedule",
        schedule_enabled=True,
    )
    dispatched = await intelligence_repository.save(
        "actions",
        restored.model_copy(update={"status": "verification_required", "receipt": receipt}),
        expected_revision=restored.revision,
    )
    assert dispatched.revision == 2
    latest = await IntelligenceRepository().get("actions", action.id, scope, Action)
    assert latest.receipt == receipt
    assert (
        await IntelligenceRepository().get("actions", action.id, scope, Action, revision=1)
    ).receipt is None
    with pytest.raises(HTTPException) as error:
        await intelligence_repository.save(
            "actions",
            restored.model_copy(update={"status": "failed"}),
            expected_revision=restored.revision,
        )
    assert error.value.status_code == 409


async def test_unknown_scope_usage_is_excluded_from_new_learning(governed_db, engine_metadata):
    scope, _ = governed_db
    async with engine_metadata.importer() as connection:
        await connection.execute(
            "INSERT INTO nova_meta.audit_semantic_query_usage "
            "(usage_id,owner_name,semantic_model_id,model_fingerprint,succeeded,created_at) "
            "VALUES ('legacy-usage',%s,'view','f',true,clock_timestamp())",
            (scope.principal,),
        )
    assert (
        await agent_repository.list_semantic_usage(
            owner_name=scope.principal,
            active_role=scope.active_role,
            security_context_version=scope.security_context_version,
            semantic_model_id="view",
            model_fingerprint="f",
            semantic_version=1,
        )
        == []
    )
    cursor = await engine_metadata.admin.execute(
        "SELECT active_role,security_context_version,semantic_version,provenance "
        "FROM nova_meta.audit_semantic_query_usage WHERE usage_id='legacy-usage'"
    )
    assert await cursor.fetchone() == {
        "active_role": None,
        "security_context_version": None,
        "semantic_version": None,
        "provenance": "imported",
    }


async def test_legacy_rows_survive_owning_initializer_upgrade(intelligence_db, engine_metadata):
    scope = intelligence_db
    async with engine_metadata.importer() as connection:
        await connection.execute(
            "INSERT INTO nova_meta.config_agents "
            "(agent_id,owner_name,name,instructions_response,created_at,updated_at) "
            "VALUES ('legacy-agent',%s,'Legacy finance','Keep the original timeframe',"
            "clock_timestamp(),clock_timestamp())",
            (scope.principal,),
        )
        await connection.execute(
            "INSERT INTO nova_meta.config_agent_runs "
            "(run_id,owner_name,agent_id,thread_id,role_name,status,last_sequence,"
            "started_at,updated_at) VALUES "
            "('legacy-run',%s,'legacy-agent','legacy-thread',%s,'completed',3,"
            "clock_timestamp(),clock_timestamp())",
            (scope.principal, scope.active_role),
        )
    for _ in range(2):
        await agent_repository.ensure_schema()
        await run_journal.ensure_schema()
        await migrate(engine_metadata.role_config("nova_meta_migrator"), app_version="e" * 40)
    agent = await agent_repository.get_agent("legacy-agent", owner_name=scope.principal)
    assert agent["name"] == "Legacy finance"
    assert agent["instructions_response"] == "Keep the original timeframe"
    assert agent["release_manifest_id"] is None
    assert await get_manifest("legacy-agent", scope.principal, version_id="legacy") is None
    changed = await agent_repository.update_agent(
        "legacy-agent",
        owner_name=scope.principal,
        fields={"instructions_response": "Preserve Q1 on follow-up"},
    )
    assert changed["instructions_response"] == "Preserve Q1 on follow-up"
    assert changed["release_manifest_id"] is None
    cursor = await engine_metadata.admin.execute(
        "SELECT status,last_sequence,session_id,security_version,provenance "
        "FROM nova_meta.config_agent_runs WHERE run_id='legacy-run'"
    )
    assert await cursor.fetchone() == {
        "status": "completed",
        "last_sequence": 3,
        "session_id": None,
        "security_version": None,
        "provenance": "imported",
    }
    arguments = dict(
        owner_name=scope.principal,
        active_role=scope.active_role,
        security_context_version=scope.security_context_version,
        semantic_model_id="view",
        model_fingerprint="definition",
        semantic_version=1,
    )
    assert await agent_repository.list_semantic_usage(**arguments) == []
    await agent_repository.record_semantic_usage(
        **arguments,
        metrics=["revenue"],
        dimensions=[],
        filter_shape=[],
        time_grain=None,
        execution_latency_ms=1,
        succeeded=True,
    )
    learned = await agent_repository.list_semantic_usage(**arguments)
    assert len(learned) == 1 and learned[0]["metrics"] == ["revenue"]
    await run_journal.start(
        run_id="scoped-run",
        owner_name=scope.principal,
        agent_id="legacy-agent",
        thread_id="legacy-thread",
        role_name=scope.active_role,
        session_id=scope.session_id,
        security_version=scope.security_context_version,
    )
    cursor = await engine_metadata.admin.execute(
        "SELECT session_id,security_version FROM nova_meta.config_agent_runs "
        "WHERE run_id='scoped-run'"
    )
    assert await cursor.fetchone() == {
        "session_id": scope.session_id,
        "security_version": scope.security_context_version,
    }
