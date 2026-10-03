"""Real StarRocks/Redis acceptance for lifecycle journals and immutable memory."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.modules.agents.knowledge import KnowledgeReview, KnowledgeState
from app.modules.agents.memory import memory_repository
from app.modules.intelligence.contracts import (
    Confidence,
    ContextNode,
    EvidenceRef,
    NewsItem,
    Scope,
    SemanticRef,
    Window,
)
from app.modules.intelligence.engine_repository import intelligence_repository as repository
from app.modules.intelligence.engine_schema import ENGINE_TABLES, ensure_engine_schema
from tests.integration._stack import require_shared_stack, shared_stack_host_port

pytestmark = pytest.mark.engine


@pytest.fixture
async def intelligence_db(request, monkeypatch, docker_services):
    require_shared_stack(request)
    monkeypatch.setattr(settings, "STARROCKS_HOST", "127.0.0.1")
    monkeypatch.setattr(
        settings,
        "STARROCKS_FE_MYSQL_PORT",
        shared_stack_host_port("NOVA_TEST_FE_MYSQL_PORT", 29030),
    )
    monkeypatch.setattr(settings, "STARROCKS_ROOT_USER", "root")
    monkeypatch.setattr(settings, "STARROCKS_ROOT_PASSWORD", "")
    monkeypatch.setattr(
        settings,
        "REDIS_URL",
        f"redis://127.0.0.1:{shared_stack_host_port('NOVA_TEST_REDIS_PORT', 26379)}/0",
    )
    await db.init_system_pool()
    await session_store.init()
    await db.execute_system("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    await ensure_engine_schema()
    await memory_repository.ensure_schema()
    owner = "intelligence-test-" + uuid4().hex
    try:
        yield Scope(
            principal=owner,
            active_role="ANALYST",
            security_context_version=1,
            session_id="fixture-session",
        )
    finally:
        for table in ENGINE_TABLES.values():
            await db.execute_system(f"DELETE FROM NOVA_SYSTEM.{table} WHERE principal=%s", [owner])
        for table in (
            "CONFIG_AGENT_MEMORIES",
            "CONFIG_AGENT_MEMORY_REVISIONS",
            "CONFIG_AGENT_MEMORY_EVIDENCE",
        ):
            await db.execute_system(f"DELETE FROM NOVA_SYSTEM.{table} WHERE user_name=%s", [owner])
        await session_store.close()
        await db.close_system_pool()


async def test_runtime_bootstrap_and_migration_are_idempotent(intelligence_db):
    await ensure_engine_schema()
    migration = Path("migrations/20261001_intelligence_engine.sql").read_text()
    for statement in migration.split(";"):
        if statement.strip():
            await db.execute_system(statement)
    await memory_repository.ensure_schema()
    bootstrap = (
        Path("../docker/init-nova.sql")
        .read_text()
        .split("-- Nova Intelligence Engine: immutable revisions and governed lifecycle", 1)[1]
        .split("-- End Nova Intelligence Engine schema", 1)[0]
        .strip()
    )
    assert bootstrap == migration.split("\n", 1)[1].strip()


async def test_full_size_ids_roundtrip_and_foreign_principal_isolation(intelligence_db):
    scope = intelligence_db
    record = ContextNode(
        id=uuid4().hex * 2, scope=scope, kind="domain", name="Finance", reference_id="finance"
    )
    saved = await repository.save("nodes", record)
    assert (await repository.get("nodes", saved.id, scope, ContextNode)) == saved
    assert (
        await repository.get(
            "nodes", saved.id, scope.model_copy(update={"principal": "other"}), ContextNode
        )
        is None
    )
    changed = await repository.save(
        "nodes", saved.model_copy(update={"name": "Finance authority"}), expected_revision=1
    )
    assert changed.revision == 2
    assert (
        await repository.get("nodes", saved.id, scope, ContextNode, revision=1)
    ).name == "Finance"


async def test_cooldown_crosses_calendar_bucket_and_respects_direction(intelligence_db):
    scope = intelligence_db
    end = datetime(2026, 9, 20, 23, 59, tzinfo=UTC)
    ref = SemanticRef(view_id="test-view", version=1, fingerprint="test-definition")
    evidence = EvidenceRef(
        id=uuid4().hex,
        source_type="user_statement",
        source_id="test-message",
        method="fixture-v1",
        scope=scope,
        digest="f" * 64,
        observed_at=end,
    )
    incident = NewsItem(
        id=uuid4().hex * 2,
        scope=scope,
        monitor_id="test-monitor",
        title="Revenue drop",
        summary="Arithmetic change",
        semantic=ref,
        window=Window(start=end - timedelta(days=1), end=end),
        before=100,
        after=60,
        change=-40,
        severity="warning",
        dedup_key=uuid4().hex,
        confidence=Confidence(dimension="detection", method="fixture-v1"),
        evidence=[evidence],
    )
    await repository.save("news", incident)
    candidate = incident.model_copy(
        update={
            "id": uuid4().hex * 2,
            "window": Window(start=end - timedelta(hours=23), end=end + timedelta(minutes=2)),
        }
    )
    assert (await repository.recent_incident(candidate, 24)).id == incident.id
    assert await repository.recent_incident(candidate.model_copy(update={"change": 40}), 24) is None
    assert (
        await repository.recent_incident(
            candidate.model_copy(update={"scope": scope.model_copy(update={"principal": "other"})}),
            24,
        )
        is None
    )


async def test_knowledge_evidence_conflicts_review_and_deleted_projection(intelligence_db):
    scope = intelligence_db
    args = dict(
        user_name=scope.principal,
        agent_id="finance",
        role_name=scope.active_role,
        fact_key="recognized_revenue",
        source_thread_id="fixture-thread",
        existing_id=None,
    )
    ident = await memory_repository.upsert(
        **args,
        fact="Revenue excludes cancelled orders.",
        source_quote="Revenue excludes cancelled orders.",
        source_message_id="one",
    )
    await memory_repository.upsert(
        **args,
        fact="Revenue also subtracts posted refunds.",
        source_quote="Revenue also subtracts posted refunds.",
        source_message_id="two",
    )
    rows = await memory_repository.list(
        user_name=scope.principal, agent_id="finance", role_name=scope.active_role
    )
    assert len(rows) == 1 and rows[0]["knowledge"]["state"] == "CONFLICTED"
    user = {
        "username": scope.principal,
        "active_role": scope.active_role,
        "roles": [scope.active_role],
    }
    body = KnowledgeReview(
        operation_id="review-one",
        expected_revision=2,
        operation="resolve",
        selected_fact="Revenue also subtracts posted refunds.",
        note="Company definition confirmed",
    )
    reviewed = await memory_repository.review(ident, agent_id="finance", user=user, request=body)
    assert reviewed == await memory_repository.review(
        ident, agent_id="finance", user=user, request=body
    )
    assert reviewed.state == KnowledgeState.HYPOTHESIS
    assert len(reviewed.evidence_ids) == 2
    assert await memory_repository.delete(
        ident, user_name=scope.principal, agent_id="finance", role_name=scope.active_role
    )
    assert (
        await memory_repository.list(
            user_name=scope.principal, agent_id="finance", role_name=scope.active_role
        )
        == []
    )
    history = await memory_repository._revisions(
        ident, scope.principal, "finance", scope.active_role
    )
    assert len(history) == 4 and history[0].state == KnowledgeState.SUPERSEDED
