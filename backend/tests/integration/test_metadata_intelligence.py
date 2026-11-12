"""Intelligence metadata on the real PG fixture: CAS, immutability, fencing, scope."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb

from app.core.metadata_bootstrap import WORKLOAD_ROLES
from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
    MetadataReadOnlyMaintenance,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.intelligence.metadata_repository import IntelligenceMetadataRepository
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg
VIEW_READERS = {"web", "agent", "automation", "task", "maintenance"}
RECORD_WRITERS = {"web", "agent", "automation", "task"}
SEARCH_READERS = {"web", "agent", "maintenance"}
VERSION = 5  # the security context version of ``session``


class Intelligence:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, workload="web", *, name="iq-alice", role="ANALYST",
                          read_only=False):
        user = session(name, active=role)
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            actor = SecurityContext.from_session(user)
            async with db.transaction(actor, read_only=read_only) as conn:
                yield IntelligenceMetadataRepository(conn, actor), conn
        finally:
            await db.close()

    async def admin(self, query, params=()):
        cursor = await self.environment.admin.execute(query, params)
        return await cursor.fetchall() if cursor.description else None


@pytest_asyncio.fixture
async def iq(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="e" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return Intelligence(environment, epoch)


DEFINITION = {"version": "0.1.1", "name": "sales", "datasets": []}
PASSING = {"valid": True, "errors": [], "baseline_version": None}


async def create_view(iq, name="sales", **changes):
    async with iq.transaction() as (repo, _):
        return await repo.create_view(
            catalog="default_catalog", database="shop", schema="shop", name=name,
            view_id=str(uuid4()), definition=DEFINITION, fingerprint="f" * 64, **changes,
        )


async def published_view(iq):
    view = await create_view(iq)
    async with iq.transaction() as (repo, _):
        await repo.record_validation(view["id"], 1, PASSING)
        return await repo.publish(view["id"], 1, expected_revision=1, expected_active=None)


def record(record_id, *, name="iq-alice", role="ANALYST", **fields):
    return {"id": record_id, "revision": 1, "created_at": "2026-11-12T00:00:00Z",
            "updated_at": "2026-11-12T00:00:00Z",
            "scope": {"principal": name, "active_role": role,
                      "security_context_version": VERSION, "session_id": "login-" + name},
            **fields}


def event(record_id, *, name="iq-alice", role="ANALYST", **fields):
    return {"id": record_id, "revision": 1,
            "actor": {"principal": name, "active_role": role,
                      "security_context_version": VERSION}, **fields}


# ── Semantic Views ──────────────────────────────────────────────────────────

async def test_views_start_as_the_creators_draft_and_names_are_unique(iq):
    view = await create_view(iq)
    assert (view["owner_name"], view["status"], view["metadata_revision"]) == (
        "iq-alice", "DRAFT", 1)
    assert view["provenance"] == "runtime" and view["active_version"] is None
    async with iq.transaction(read_only=True) as (repo, _):
        assert (await repo.get_version(view["id"], 1))["status"] == "DRAFT"
        found = await repo.find_view("default_catalog", "shop", "shop", "sales")
        assert found["id"] == view["id"]
    with pytest.raises(MetadataConflict):
        await create_view(iq)
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction() as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_semantic_views (catalog_name,database_name,"
                "schema_name,name,id,owner_name,status) VALUES ('c','d','s','x','v-x',"
                "'someone-else','DRAFT')")


async def test_concurrent_versions_from_one_latest_have_one_winner(iq):
    view = await create_view(iq)
    ready = asyncio.Barrier(2)

    async def add(label):
        async with iq.transaction() as (repo, _):
            await ready.wait()
            return await repo.add_version(view["id"], {**DEFINITION, "label": label}, "a" * 64,
                                          expected_latest=1)

    results = await asyncio.gather(add("a"), add("b"), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    # A writer that read an older latest version cannot fill a gap or overwrite.
    with pytest.raises(MetadataConflict):
        async with iq.transaction() as (repo, _):
            await repo.add_version(view["id"], DEFINITION, "b" * 64, expected_latest=1)
    with pytest.raises(MetadataConflict):
        async with iq.transaction() as (repo, _):
            await repo.add_version(view["id"], DEFINITION, "b" * 64, expected_latest=5)
    async with iq.transaction(read_only=True) as (repo, _):
        assert await repo.latest_version(view["id"]) == 2


async def test_publication_needs_a_validated_version_and_the_revision_read(iq):
    view = await create_view(iq)
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction() as (_, conn):
            await conn.execute(
                "UPDATE nova_meta.config_semantic_view_versions SET status='ACTIVE' "
                "WHERE view_id=%s", (view["id"],))
    async with iq.transaction() as (repo, _):
        failing = await repo.record_validation(view["id"], 1, {"valid": False})
        assert failing["status"] == "DRAFT" and failing["validated_at"] is not None
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction() as (repo, _):
            await repo.publish(view["id"], 1, expected_revision=1, expected_active=None)
    async with iq.transaction() as (repo, _):
        await repo.record_validation(view["id"], 1, PASSING)
    ready = asyncio.Barrier(2)

    async def publish():
        async with iq.transaction() as (repo, _):
            await ready.wait()
            return await repo.publish(view["id"], 1, expected_revision=1, expected_active=None)

    results = await asyncio.gather(publish(), publish(), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    winner = next(r for r in results if isinstance(r, dict))
    assert (winner["status"], winner["active_version"], winner["metadata_revision"]) == (
        "ACTIVE", 1, 2)
    # A pointer move from a stale active version is refused (the source's <=>).
    async with iq.transaction() as (repo, _):
        await repo.add_version(view["id"], {**DEFINITION, "v": 2}, "c" * 64, expected_latest=1)
        await repo.record_validation(view["id"], 2, {**PASSING, "baseline_version": 1})
    with pytest.raises(MetadataConflict):
        async with iq.transaction() as (repo, _):
            await repo.publish(view["id"], 2, expected_revision=2, expected_active=None)
    async with iq.transaction() as (repo, _):
        await repo.publish(view["id"], 2, expected_revision=2, expected_active=1)
        assert (await repo.get_version(view["id"], 1))["status"] == "DEPRECATED"
        assert (await repo.get_version(view["id"], 2))["activated_at"] is not None
    audits = await iq.admin(
        "SELECT action,object_name FROM nova_meta.audit_log WHERE metadata_table="
        "'config_semantic_views' ORDER BY log_id")
    assert [a["action"] for a in audits] == ["CREATE", "UPDATE", "UPDATE"]


async def test_published_versions_are_immutable(iq):
    view = await published_view(iq)
    for change, refusal in (("validation='{}'::jsonb", MetadataIntegrityViolation),
                            ("status='VALIDATED'", MetadataIntegrityViolation),
                            ("status='DRAFT'", MetadataIntegrityViolation),
                            ("definition='{}'::jsonb", MetadataPermissionDenied),
                            ("fingerprint='x'", MetadataPermissionDenied)):
        with pytest.raises(refusal):
            async with iq.transaction() as (_, conn):
                await conn.execute(
                    f"UPDATE nova_meta.config_semantic_view_versions SET {change} "
                    "WHERE view_id=%s AND version=1", (view["id"],))
    async with iq.transaction() as (repo, _):
        assert await repo.record_validation(view["id"], 1, {"valid": False}) is None
    with pytest.raises(MetadataPermissionDenied):
        async with iq.transaction() as (_, conn):
            await conn.execute(
                "UPDATE nova_meta.config_semantic_view_versions SET activated_at=now() "
                "WHERE view_id=%s", (view["id"],))
    # A view cannot point to a version that was never published.
    async with iq.transaction() as (repo, _):
        await repo.add_version(view["id"], DEFINITION, "d" * 64, expected_latest=1)
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction() as (_, conn):
            await conn.execute(
                "UPDATE nova_meta.config_semantic_views SET active_version=2,"
                "metadata_revision=metadata_revision+1 WHERE id=%s", (view["id"],))
    # A raw change without the next revision is refused.
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction() as (_, conn):
            await conn.execute("UPDATE nova_meta.config_semantic_views SET visibility='PRIVATE' "
                               "WHERE id=%s", (view["id"],))
    # Versions only go with their view.
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction() as (_, conn):
            await conn.execute("DELETE FROM nova_meta.config_semantic_view_versions "
                               "WHERE view_id=%s", (view["id"],))
    async with iq.transaction() as (repo, _):
        retired = await repo.deprecate(view["id"], expected_revision=2)
        assert (retired["status"], retired["active_version"]) == ("DEPRECATED", None)
        assert await repo.drop(view["id"], expected_revision=3) is True
        assert await repo.get_version(view["id"], 1) is None


async def test_news_settings_are_revisioned_and_attributed(iq):
    view = await create_view(iq)
    async with iq.transaction(name="iq-alice") as (repo, _):
        saved = await repo.save_news(view["id"], True, {"cadence_minutes": 60},
                                     expected_revision=1)
        assert saved["news_updated_by"] == "iq-alice" and saved["metadata_revision"] == 2
        assert [v["id"] for v in await repo.news_views()] == [view["id"]]
    with pytest.raises(MetadataConflict):
        async with iq.transaction() as (repo, _):
            await repo.save_news(view["id"], False, None, expected_revision=1)


# ── Revision stores ─────────────────────────────────────────────────────────

async def test_concurrent_revisions_have_one_winner_and_stale_writes_conflict(iq):
    async with iq.transaction() as (repo, _):
        first = await repo.save_record("monitors", record("m-1", threshold=1))
    assert first["revision"] == 1
    ready = asyncio.Barrier(2)

    async def edit(threshold):
        async with iq.transaction("task") as (repo, _):
            await ready.wait()
            return await repo.save_record("monitors", record("m-1", threshold=threshold),
                                          expected_revision=1)

    results = await asyncio.gather(edit(2), edit(3), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    with pytest.raises(MetadataConflict):
        async with iq.transaction() as (repo, _):
            await repo.save_record("monitors", record("m-1", threshold=9), expected_revision=1)
    async with iq.transaction() as (repo, _):
        current = await repo.get_record("monitors", "m-1")
        assert current["revision"] == 2 and current["written_by"] == "iq-alice"
        # Saving the same content again returns the stored revision, not a new one.
        again = await repo.save_record("monitors", current["payload"], expected_revision=2)
        assert again["revision"] == 2


async def test_records_are_isolated_by_owner_and_cannot_be_taken_over(iq):
    async with iq.transaction() as (repo, _):
        await repo.save_record("decisions", record("d-1", title="Raise price"))
        await repo.save_record("actions", record("a-1", decision_id="d-1"))
    async with iq.transaction(name="iq-bob") as (repo, _):
        assert await repo.get_record("decisions", "d-1") is None
        assert await repo.page("decisions") == []
    # A guessed id must never overwrite another principal's record.
    with pytest.raises(MetadataPermissionDenied):
        async with iq.transaction(name="iq-bob") as (repo, _):
            await repo.save_record("decisions", record("d-1", name="iq-bob"),
                                   expected_revision=1)
    async with iq.transaction(role="SALES") as (repo, _):
        assert await repo.get_record("decisions", "d-1") is None
    async with iq.transaction(name="iq-bob") as (repo, _):
        # The two shared lookups the owning services authorize themselves.
        assert (await repo.shared_decision("d-1", "iq-alice"))["title"] == "Raise price"
        assert await repo.shared_decision("d-1", "iq-bob") is None
        assert (await repo.action_for_review("a-1"))["decision_id"] == "d-1"
        # A reviewer can append to the owner's action without reading it back.
        reviewed = await repo.save_record(
            "actions", record("a-1", decision_id="d-1", approved=True), expected_revision=1)
        assert reviewed["revision"] == 2
    async with iq.transaction() as (repo, _):
        assert (await repo.get_record("actions", "a-1"))["written_by"] == "iq-bob"


@pytest.mark.parametrize("bad", [
    {"payload_id": "other"}, {"payload_revision": 7}, {"scope_principal": "iq-bob"},
])
async def test_revision_payload_must_match_its_row(iq, bad):
    payload = record("n-1")
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction() as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_context_nodes (id,revision,operation_id,principal,"
                "active_role,security_context_version,payload) VALUES (%s,%s,'op',%s,%s,%s,%s)",
                (bad.get("payload_id", "n-1"), bad.get("payload_revision", 1),
                 bad.get("scope_principal", "iq-alice"), "ANALYST", VERSION,
                 Jsonb(payload)))


async def test_revision_history_is_append_only(iq):
    async with iq.transaction() as (repo, _):
        await repo.save_record("nodes", record("n-2", name="Orders"))
    for statement in ("UPDATE nova_meta.config_context_nodes SET payload='{}'::jsonb",
                      "DELETE FROM nova_meta.config_context_nodes"):
        with pytest.raises(MetadataPermissionDenied):
            async with iq.transaction() as (_, conn):
                await conn.execute(statement)


async def test_business_events_record_their_writer(iq):
    async with iq.transaction("automation") as (repo, _):
        saved = await repo.save_record("incidents", event("i-1", title="Late orders"))
        assert saved["revision"] == 1
    async with iq.transaction(name="iq-bob") as (repo, _):
        # Deployment-wide: other principals read and extend it as themselves.
        assert (await repo.get_record("incidents", "i-1"))["actor"] == "iq-alice"
        await repo.save_record("incidents", event("i-1", name="iq-bob", title="x"),
                               expected_revision=1)
    with pytest.raises(MetadataPermissionDenied):
        async with iq.transaction(name="iq-bob") as (repo, _):
            await repo.save_record("incidents", event("i-1", name="iq-alice", title="y"),
                                   expected_revision=2)


async def test_news_editions_are_read_by_their_execution_scope(iq):
    edition = record("e-1", name="svc-news", role="NEWS",
                     semantic={"view_id": "v-1"}, edition_date="2026-11-12")
    async with iq.transaction("task", name="svc-news", role="NEWS") as (repo, _):
        await repo.save_record("editions", edition)
    async with iq.transaction(name="iq-reader") as (repo, _):
        latest = await repo.latest_edition("v-1", "svc-news", "NEWS", VERSION)
        assert latest["edition_date"] == "2026-11-12"
    with pytest.raises(MetadataPermissionDenied):
        async with iq.transaction("agent", name="svc-news", role="NEWS") as (repo, _):
            await repo.save_record("stories", record("s-1", name="svc-news", role="NEWS"))


# ── News feedback ───────────────────────────────────────────────────────────

async def test_feedback_is_private_to_each_reader(iq):
    async with iq.transaction() as (repo, _):
        await repo.set_feedback("s-1", "like", {"metric": "revenue"})
        await repo.set_feedback("s-1", "dislike", {"metric": "revenue"})
    async with iq.transaction(name="iq-bob") as (repo, conn):
        assert await repo.feedback() == []
        await conn.execute("DELETE FROM nova_meta.config_intelligence_story_feedback")
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction() as (repo, _):
            await repo.set_feedback("s-2", "love")
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction(name="iq-bob") as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_intelligence_story_feedback (user_name,story_id,"
                "reaction,features) VALUES ('iq-alice','s-3','like','{}')")
    async with iq.transaction() as (repo, _):
        rows = await repo.feedback()
        assert [(r["story_id"], r["reaction"]) for r in rows] == [("s-1", "dislike")]
        await repo.set_feedback("s-1", None)
        assert await repo.feedback() == []


# ── AI Search ───────────────────────────────────────────────────────────────

INDEX = {"name": "docs", "id": "index-1", "source_relation": "shop.docs",
         "key_columns": ["id"], "content_columns": ["body"], "filter_columns": []}
MODEL = {"model_id": None, "dimensions": 0, "metric": "cosine"}


async def create_index(iq):
    async with iq.transaction() as (repo, _):
        return await repo.create_index(INDEX, MODEL)


async def test_search_versions_are_created_once(iq):
    await create_index(iq)
    ready = asyncio.Barrier(2)

    async def rebuild(workload, automatic):
        async with iq.transaction(workload) as (repo, _):
            await ready.wait()
            return await repo.add_search_version("docs", "index-1", MODEL, expected_latest=1,
                                                 automatic=automatic)

    results = await asyncio.gather(rebuild("web", False), rebuild("maintenance", True),
                                   return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction() as (repo, _):
            await repo.add_search_version("docs", "wrong-index", MODEL, expected_latest=2)


async def test_a_builder_that_lost_its_claim_is_fenced(iq):
    await create_index(iq)
    async with iq.transaction("maintenance") as (repo, _):
        assert [b["version"] for b in await repo.pending_builds()] == [1]
        old = await repo.claim_build("docs", 1)
        assert await repo.record_build_progress("docs", 1, generation=old, source_rows=10,
                                                indexed_rows=4) is True
        # The poll retakes a lapsed build: the first builder is now stale.
        new = await repo.claim_build("docs", 1)
        assert (old, new) == (1, 2)
        assert await repo.record_build_progress("docs", 1, generation=old, source_rows=10,
                                                indexed_rows=9) is False
        assert await repo.finish_build("docs", 1, generation=old, indexed_rows=9) is False
        assert await repo.finish_build("docs", 1, generation=new, indexed_rows=10,
                                       watermark="w-1") is True
        version = await repo.get_search_version("docs", 1)
        assert (version["build_status"], version["indexed_rows"]) == ("READY", 10)
        assert version["completed_at"] is not None
        assert await repo.claim_build("docs", 1) is None
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction("maintenance") as (_, conn):
            await conn.execute("UPDATE nova_meta.config_ai_search_sync_state SET indexed_rows=1 "
                               "WHERE index_name='docs'")
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction("maintenance") as (_, conn):
            await conn.execute("UPDATE nova_meta.config_ai_search_versions SET "
                               "build_status='BUILDING' WHERE index_name='docs'")
    async with iq.transaction() as (repo, _):
        index = await repo.activate("docs", 1, expected_revision=1)
        assert (index["status"], index["active_version"]) == ("ACTIVE", 1)
    with pytest.raises(MetadataConflict):
        async with iq.transaction() as (repo, _):
            await repo.activate("docs", 1, expected_revision=1)
    actions = await iq.admin(
        "SELECT metadata_table,action FROM nova_meta.audit_log WHERE metadata_table IN "
        "('config_ai_search_indexes','config_ai_search_versions') ORDER BY log_id")
    assert [(a["metadata_table"], a["action"]) for a in actions] == [
        ("config_ai_search_indexes", "CREATE"), ("config_ai_search_versions", "CREATE"),
        ("config_ai_search_versions", "UPDATE"), ("config_ai_search_indexes", "UPDATE")]


async def test_search_logs_belong_to_their_user(iq):
    await create_index(iq)
    entry = {"id": "q-1", "index_name": "docs", "index_version": 1, "mode": "hybrid",
             "elapsed_ms": 4, "lexical_candidates": 3, "semantic_candidates": 0,
             "result_count": 3}
    async with iq.transaction("agent") as (repo, conn):
        await repo.log_query(entry)
        await repo.log_query(entry)  # the source key is not unique
        cursor = await conn.execute("SELECT user_name FROM nova_meta.ai_search_query_log")
        assert [r["user_name"] for r in await cursor.fetchall()] == ["iq-alice", "iq-alice"]
    async with iq.transaction() as (repo, _):
        await repo.record_evaluation({"id": "ev-1", "index_name": "docs", "index_version": 1,
                                      "mode": "hybrid", "query_count": 2,
                                      "metrics": {"recall": 1.0}})
    async with iq.transaction(name="iq-bob") as (_, conn):
        cursor = await conn.execute("SELECT count(*) AS n FROM nova_meta.ai_search_query_log")
        assert (await cursor.fetchone())["n"] == 0
    with pytest.raises(MetadataIntegrityViolation):
        async with iq.transaction() as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.ai_search_query_log (id,index_name,index_version,"
                "user_name,mode,elapsed_ms,lexical_candidates,semantic_candidates,result_count,"
                "status) VALUES ('q','docs',1,'iq-bob','hybrid',1,0,0,0,'SUCCESS')")


# ── Workloads, maintenance and import ───────────────────────────────────────

@pytest.mark.parametrize("workload", [role.removeprefix("nova_meta_") for role in WORKLOAD_ROLES])
async def test_workload_privileges(iq, workload):
    view = await create_view(iq)
    await create_index(iq)
    async with iq.transaction() as (repo, _):
        await repo.save_record("nodes", record("n-w"))
    if workload in VIEW_READERS:
        async with iq.transaction(workload, read_only=True) as (repo, _):
            assert (await repo.get_view(view["id"]))["name"] == "sales"
    else:
        with pytest.raises(MetadataPermissionDenied):
            async with iq.transaction(workload, read_only=True) as (repo, _):
                await repo.get_view(view["id"])
    if workload in RECORD_WRITERS:
        async with iq.transaction(workload) as (repo, _):
            assert (await repo.get_record("nodes", "n-w"))["revision"] == 1
            await repo.save_record("nodes", record("n-w", label=workload), expected_revision=1)
    else:
        with pytest.raises(MetadataPermissionDenied):
            async with iq.transaction(workload) as (repo, _):
                await repo.save_record("nodes", record("n-" + workload))
    if workload in SEARCH_READERS:
        async with iq.transaction(workload, read_only=True) as (repo, _):
            assert (await repo.get_index("docs"))["id"] == "index-1"
    else:
        with pytest.raises(MetadataPermissionDenied):
            async with iq.transaction(workload, read_only=True) as (repo, _):
                await repo.get_index("docs")
    if workload != "web":
        with pytest.raises(MetadataPermissionDenied):
            async with iq.transaction(workload) as (repo, _):
                await repo.set_feedback("s-1", "like")
    if workload not in {"web", "agent"}:
        with pytest.raises(MetadataPermissionDenied):
            async with iq.transaction(workload) as (repo, _):
                await repo.add_version(view["id"], DEFINITION, "e" * 64, expected_latest=1)
    if workload != "maintenance":
        with pytest.raises(MetadataPermissionDenied):
            async with iq.transaction(workload) as (repo, _):
                await repo.claim_build("docs", 1)


async def test_maintenance_blocks_writes(iq):
    view = await create_view(iq)
    await iq.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(MetadataReadOnlyMaintenance):
        async with iq.transaction() as (repo, _):
            await repo.add_version(view["id"], DEFINITION, "a" * 64, expected_latest=1)
    async with iq.transaction(read_only=True) as (repo, _):
        assert await repo.get_view(view["id"])


async def test_import_is_maintenance_only_and_keeps_legacy_rows(iq):
    env = iq.environment
    async with await psycopg.AsyncConnection.connect(
        env.role_config("nova_meta_migrator").dsn, autocommit=True,
    ) as conn:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE nova_meta_owner")
                await conn.execute(
                    "INSERT INTO nova_meta.config_intelligence_story_feedback (user_name,story_id,"
                    "reaction,features,updated_at) VALUES ('u','s','like','{}',now())")
        await iq.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE nova_meta_owner")
            await conn.execute(
                "INSERT INTO nova_meta.config_semantic_views (catalog_name,database_name,"
                "schema_name,name,id,owner_name,visibility,active_version,status,created_at,"
                "updated_at) VALUES ('default_catalog','shop','shop','legacy','legacy-view',"
                "'iq-alice',NULL,1,'ACTIVE','2026-01-01','2026-01-01')")
            await conn.execute(
                "INSERT INTO nova_meta.config_semantic_view_versions (view_id,version,definition,"
                "fingerprint,status,created_at) VALUES ('legacy-view',1,'{\"v\":1}','f','ACTIVE',"
                "'2026-01-01')")
            # Two operations at one revision: an unreconciled source branch.
            for op in ("op-a", "op-b"):
                await conn.execute(
                    "INSERT INTO nova_meta.config_intelligence_news (id,revision,operation_id,"
                    "principal,active_role,security_context_version,payload,created_at) "
                    "VALUES ('news-1',1,%s,'iq-alice','ANALYST',5,%s,'2026-01-01')",
                    (op, Jsonb(record("news-1", op=op))))
        await iq.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    async with iq.transaction() as (repo, _):
        legacy = await repo.get_view("legacy-view")
        assert (legacy["provenance"], legacy["visibility"], legacy["metadata_revision"]) == (
            "imported", None, 1)
        with pytest.raises(MetadataConflict):
            await repo.get_record("news", "news-1")
    with pytest.raises(MetadataConflict):
        async with iq.transaction() as (repo, _):
            await repo.save_record("news", record("news-1", op="fix"), expected_revision=1)
    async with iq.transaction() as (repo, _):
        # The imported published version keeps its proof; it can only be retired.
        retired = await repo.deprecate("legacy-view", expected_revision=1)
        assert retired["status"] == "DEPRECATED"
    rows = await iq.admin("SELECT count(*) AS n FROM nova_meta.config_intelligence_news")
    assert rows[0]["n"] == 2
