"""Assistant, access-control, Superset, migration and AI-usage metadata on real PG.

Owner isolation, append-only retries, CAS one-winner concurrency, workload
grants, maintenance, maintenance-only legacy import and credential refusal.
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio

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
from app.modules.access_control.domain import ProjectionState
from app.modules.access_control.metadata_repository import (
    AccessControlMetadataRepository,
    data_scope_assignment_id,
)
from app.modules.access_control.security_context import SecurityContext
from app.modules.assistant.metadata_repository import AssistantMetadataRepository
from app.modules.llm_functions.metadata_repository import LLMUsageMetadataRepository
from app.modules.migration.metadata_repository import MigrationMetadataRepository
from app.modules.superset.metadata_repository import SupersetMetadataRepository
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg
SHA = "a" * 64
REPOSITORIES = {
    "assistant": AssistantMetadataRepository, "access": AccessControlMetadataRepository,
    "superset": SupersetMetadataRepository, "migration": MigrationMetadataRepository,
    "usage": LLMUsageMetadataRepository,
}


class Fixture:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, kind="assistant", workload="web", *, name="alice",
                          role="ANALYST", read_only=False, service=False):
        if service:
            actor = SecurityContext(name, role, principal_type="SERVICE")
        else:
            actor = SecurityContext.from_session(session(name, active=role))
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            async with db.transaction(actor, read_only=read_only) as conn:
                yield REPOSITORIES[kind](conn, actor), conn
        finally:
            await db.close()

    async def admin(self, query, params=()):
        cursor = await self.environment.admin.execute(query, params)
        return await cursor.fetchall() if cursor.description else None

    @asynccontextmanager
    async def importer(self):
        """The one-shot migrator writing legacy rows under maintenance."""
        env = self.environment
        await self.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
        try:
            async with await psycopg.AsyncConnection.connect(
                env.role_config("nova_meta_migrator").dsn, autocommit=True,
            ) as conn, conn.transaction():
                await conn.execute("SET LOCAL ROLE nova_meta_owner")
                yield conn
        finally:
            await self.admin("UPDATE nova_meta.deployment_state SET maintenance=false")


@pytest_asyncio.fixture
async def meta(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="f" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return Fixture(environment, epoch)


def stamp(name="alice", role="ANALYST"):
    return {"principal": name, "active_role": role, "security_context_version": 5}


async def thread_with_answer(meta, *, name="alice"):
    async with meta.transaction(name=name) as (repo, _):
        thread = await repo.create_thread(title="Orders", thread_id="t-" + name)
        await repo.append_message(thread["thread_id"], role="user", content="How many?",
                                  message_id="q-" + name, security_context=stamp(name))
        answer, _ = await repo.append_message(
            thread["thread_id"], role="assistant", content="42", message_id="a-" + name,
            usage={"total_tokens": 9}, steps=[{"kind": "ui", "content_id": "c1"}],
            security_context=stamp(name))
    return thread, answer


# ── Assistant ─────────────────────────────────────────────────────────────


async def test_people_see_and_change_only_their_own_conversations(meta):
    thread, _ = await thread_with_answer(meta)
    async with meta.transaction(name="bob") as (repo, conn):
        assert await repo.get_thread(thread["thread_id"]) is None
        assert await repo.list_messages(thread["thread_id"]) == []
        assert await repo.set_feedback(thread["thread_id"], "a-alice", "like") is False
        assert await repo.delete_thread(thread["thread_id"]) is False
        cursor = await conn.execute("SELECT count(*) AS n FROM nova_meta.config_assistant_messages")
        assert (await cursor.fetchone())["n"] == 0
    # Bob cannot append to Alice's thread or reuse her ids.
    with pytest.raises(MetadataConflict):
        async with meta.transaction(name="bob") as (repo, _):
            await repo.append_message(thread["thread_id"], role="user", content="x")
    with pytest.raises(MetadataConflict):
        async with meta.transaction(name="bob") as (repo, _):
            await repo.create_thread(thread_id=thread["thread_id"])
    with pytest.raises(MetadataPermissionDenied):
        async with meta.transaction(name="bob") as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_assistant_threads (thread_id,user_name,title) "
                "VALUES ('forged','alice','x')")
    async with meta.transaction(name="alice") as (repo, _):
        assert [m["message_id"] for m in await repo.list_messages("t-alice")] == [
            "q-alice", "a-alice"]
        assert (await repo.get_thread("t-alice"))["message_count"] == 2
        assert await repo.set_feedback("t-alice", "a-alice", "like") is True
        assert await repo.set_feedback("t-alice", "q-alice", "like") is False


async def test_administrators_read_usage_across_people_but_change_nothing(meta):
    await thread_with_answer(meta)
    async with meta.transaction(name="root-admin", role="ACCOUNTADMIN") as (repo, conn):
        cursor = await conn.execute(
            "SELECT count(*) AS n FROM nova_meta.config_assistant_messages")
        assert (await cursor.fetchone())["n"] == 2
        assert await repo.set_feedback("t-alice", "a-alice", "dislike") is False
        assert await repo.delete_thread("t-alice") is False


async def test_messages_are_appended_once_and_conflicting_retries_are_refused(meta):
    await thread_with_answer(meta)

    async def append(content):
        async with meta.transaction() as (repo, _):
            return await repo.append_message("t-alice", role="user", content=content,
                                             message_id="retry-1", security_context=stamp())

    results = await asyncio.gather(*(append("again?") for _ in range(4)))
    assert sum(created for _, created in results) == 1
    assert {row["seq"] for row, _ in results} == {2}
    with pytest.raises(MetadataConflict):
        await append("something else")

    async def turn(index):
        async with meta.transaction() as (repo, _):
            return await repo.append_message("t-alice", role="user", content=str(index))

    rows = await asyncio.gather(*(turn(i) for i in range(5)))
    assert sorted(row["seq"] for row, _ in rows) == [3, 4, 5, 6, 7]
    with pytest.raises(MetadataIntegrityViolation):
        async with meta.transaction() as (_, conn):
            await conn.execute("UPDATE nova_meta.config_assistant_messages SET feedback='like' "
                               "WHERE message_id='retry-1'")
    with pytest.raises(MetadataPermissionDenied):
        async with meta.transaction() as (_, conn):
            await conn.execute("UPDATE nova_meta.config_assistant_messages SET content='edited' "
                               "WHERE message_id='retry-1'")


async def test_turns_are_stamped_with_the_bound_principal_and_role(meta):
    await thread_with_answer(meta)
    for forged in (stamp("bob"), stamp("alice", "ACCOUNTADMIN")):
        with pytest.raises(MetadataIntegrityViolation):
            async with meta.transaction() as (repo, _):
                await repo.append_message("t-alice", role="assistant", content="x",
                                          security_context=forged)
    with pytest.raises(MetadataIntegrityViolation):
        async with meta.transaction() as (repo, _):
            await repo.append_message("t-alice", role="system", content="x")


async def test_learning_progress_is_the_service_principals_only_change(meta):
    async with meta.transaction() as (repo, _):
        await repo.create_thread(thread_id="t-learn", agent_id="agent-1")
        await repo.append_message("t-learn", role="user", content="teach", message_id="m-l",
                                  agent_id="agent-1", learning_enabled=True)
        await repo.append_message("t-learn", role="user", content="plain", message_id="m-p")
    async with meta.transaction(workload="task", name="learning-worker",
                                service=True) as (repo, _):
        assert [row["message_id"] for row in await repo.pending_learning()] == ["m-l"]
        await repo.record_learning_trace("m-l", {"id": "s1", "kind": "plan"})
        await repo.record_learning_trace("m-l", {"id": "s1", "kind": "final"})
        assert await repo.mark_learning_complete("m-l") is True
        assert await repo.mark_learning_complete("m-l") is False
        cursor = await repo.connection.execute(
            "SELECT message_id FROM nova_meta.config_assistant_messages ORDER BY 1")
        assert [r["message_id"] for r in await cursor.fetchall()] == ["m-l"]  # never "m-p"
    async with meta.transaction() as (repo, _):
        stored = {m["message_id"]: m for m in await repo.list_messages("t-learn")}
    assert stored["m-l"]["steps"] == [{"id": "s1", "kind": "final"}]
    assert stored["m-l"]["learning_state"] == "complete"
    # A person in the task worker sees nothing; disabled learning hides the thread.
    async with meta.transaction(workload="task", name="alice") as (repo, _):
        assert await repo.pending_learning() == []
    with pytest.raises(MetadataIntegrityViolation):
        async with meta.transaction() as (repo, _):
            await repo.disable_learning("t-learn")
            await repo.connection.execute(
                "UPDATE nova_meta.config_assistant_threads SET learning_enabled=true "
                "WHERE thread_id='t-learn'")


async def test_ui_state_saves_from_the_revision_read_in_its_role(meta):
    await thread_with_answer(meta)
    state_id = hashlib.sha256(b"alice/t-alice/a-alice/c1").hexdigest()
    fields = {"thread_id": "t-alice", "message_id": "a-alice", "content_id": "c1",
              "program_sha256": SHA}
    async with meta.transaction() as (repo, _):
        first = await repo.put_ui_state(state_id, state={"x": 1}, expected_revision=0, **fields)
        assert first["revision"] == 1 and first["security_context_version"] == 5
    ready = asyncio.Barrier(2)

    async def save(value):
        async with meta.transaction() as (repo, _):
            await ready.wait()
            return await repo.put_ui_state(state_id, state={"x": value}, expected_revision=1,
                                           **fields)

    results = await asyncio.gather(save(2), save(3), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    # Another role neither reads nor overwrites the state saved in ANALYST.
    async with meta.transaction(role="ACCOUNTADMIN") as (repo, _):
        assert await repo.get_ui_state(state_id) is None
    with pytest.raises(MetadataIntegrityViolation):
        async with meta.transaction(role="ACCOUNTADMIN") as (repo, _):
            await repo.put_ui_state(state_id, state={"x": 9}, expected_revision=2, **fields)
    with pytest.raises(MetadataIntegrityViolation):
        async with meta.transaction() as (repo, _):
            await repo.put_ui_state("other", state={"x": 1}, expected_revision=0,
                                    **{**fields, "message_id": "q-alice"})


async def test_translations_are_shared_and_never_move_back(meta):
    async with meta.transaction(workload="agent", name="alice") as (repo, _):
        await repo.store_translations("id", 2, {"loop.ui_unavailable": "Belum bisa."})
    async with meta.transaction(workload="automation", name="bob") as (repo, _):
        await repo.store_translations("id", 1, {"loop.ui_unavailable": "Lama."})
        assert await repo.translations("id", 2) == {"loop.ui_unavailable": "Belum bisa."}
    with pytest.raises(MetadataIntegrityViolation):
        async with meta.transaction() as (repo, _):
            await repo.store_translations("pt-BR", 2, {"x": "y"})


# ── Access control ────────────────────────────────────────────────────────


async def test_projection_changes_need_the_revision_read_and_an_admin_role(meta):
    async with meta.transaction("access", name="sec", role="SECURITYADMIN") as (repo, _):
        row = await repo.put_role_projection("analyst_eu", ProjectionState.PROVISIONING,
                                             expected_revision=None)
    assert row["metadata_revision"] == 1 and row["updated_by"] == "sec"
    ready = asyncio.Barrier(2)

    async def move(state):
        async with meta.transaction("access", name="sec", role="SECURITYADMIN") as (repo, _):
            await ready.wait()
            return await repo.put_role_projection("analyst_eu", state, expected_revision=1)

    results = await asyncio.gather(move(ProjectionState.ACTIVE), move(ProjectionState.ERROR),
                                   return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    with pytest.raises(MetadataPermissionDenied):
        async with meta.transaction("access", role="ANALYST") as (repo, _):
            await repo.put_role_projection("analyst_eu", ProjectionState.DRIFTED,
                                           expected_revision=2)
    async with meta.transaction("access", role="ANALYST") as (_, conn):
        # Forged past the repository check: the row is not changeable in this role.
        cursor = await conn.execute("UPDATE nova_meta.config_security_role_projections "
                                    "SET state='DRIFTED',metadata_revision=3")
        assert cursor.rowcount == 0
    with pytest.raises(MetadataPermissionDenied):
        async with meta.transaction("access", role="ANALYST") as (_, conn):
            await conn.execute("INSERT INTO nova_meta.config_security_role_projections "
                               "(role_name,state) VALUES ('mine','ACTIVE')")
    with pytest.raises(MetadataPermissionDenied):
        async with meta.transaction("access", name="sec", role="SECURITYADMIN") as (repo, _):
            await repo.put_role_projection("ACCOUNTADMIN", ProjectionState.DELETING,
                                           expected_revision=None)
    async with meta.transaction("access", name="sec", role="SECURITYADMIN") as (repo, _):
        await repo.put_policy_state("nova/p1", {"id": 7, "guid": "g"},
                                    ProjectionState.PROPAGATING, expected_revision=None)
        assert await repo.health_counts() == {"pending_propagation": 1, "drift_count": 0}
        await repo.delete_role_projection("analyst_eu", expected_revision=2)
    audit = await meta.admin(
        "SELECT metadata_table,action,metadata_revision,user_name,active_role "
        "FROM nova_meta.audit_log WHERE metadata_table LIKE 'config_security%%' "
        "ORDER BY recorded_at")
    assert [(a["metadata_table"], a["action"], a["metadata_revision"]) for a in audit] == [
        ("config_security_role_projections", "CREATE", 1),
        ("config_security_role_projections", "UPDATE", 2),
        ("config_security_policies", "CREATE", 1),
        ("config_security_role_projections", "DELETE", 2),
    ]
    assert {(a["user_name"], a["active_role"]) for a in audit} == {("sec", "SECURITYADMIN")}


async def test_data_scopes_keep_the_source_identity_and_hide_values_from_audit(meta):
    async with meta.transaction("access", name="sec", role="ACCOUNTADMIN") as (repo, _):
        row = await repo.put_data_scope("bob", "ANALYST", "region", ["EU", "US", "EU"],
                                        expected_revision=None)
        assert row["assignment_id"] == data_scope_assignment_id("bob", "ANALYST", "region")
        assert row["scope_values_json"] == '["EU", "US"]'
        changed = await repo.put_data_scope("bob", "ANALYST", "region", ["APAC"],
                                            expected_revision=1)
        assert changed["metadata_revision"] == 2
    with pytest.raises(MetadataIntegrityViolation):
        async with meta.transaction("access", name="sec", role="ACCOUNTADMIN") as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_data_scope_assignments (assignment_id,principal,"
                "role_name,dimension_key,scope_values_json,state) "
                "VALUES ('chosen','bob','ANALYST','city','[]','PENDING')")
    audit = await meta.admin("SELECT metadata_key::text AS key FROM nova_meta.audit_log "
                             "WHERE metadata_table='config_data_scope_assignments'")
    assert audit and all("EU" not in a["key"] and "APAC" not in a["key"] for a in audit)


async def test_business_policy_revisions_have_one_winner(meta):
    policy = {"id": "default", "revision": 1, "maximum_cost": 10, "automatic_cost_limit": 1,
              "reviewer_roles": ["ACCOUNTADMIN"], "blocked_actions": [],
              "allow_high_risk": False}
    ready = asyncio.Barrier(3)

    async def save(cost):
        async with meta.transaction("access", name="owner", role="ACCOUNTADMIN") as (repo, _):
            await ready.wait()
            return await repo.save_business_policy({**policy, "maximum_cost": cost})

    results = await asyncio.gather(*(save(c) for c in (11, 12, 13)), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert all(isinstance(r, dict | MetadataConflict) for r in results)
    winner = next(r for r in results if isinstance(r, dict))
    assert winner["revision"] == 2 and winner["definition"]["revision"] == 2
    async with meta.transaction("access", workload="agent", name="bob") as (repo, _):
        assert (await repo.current_business_policy())["revision"] == 2
    with pytest.raises(MetadataConflict):  # edited from a revision that is no longer current
        async with meta.transaction("access", name="owner", role="ACCOUNTADMIN") as (repo, _):
            await repo.save_business_policy(policy)
    with pytest.raises(MetadataPermissionDenied):
        async with meta.transaction("access", name="sec", role="SECURITYADMIN") as (repo, _):
            await repo.save_business_policy({**policy, "revision": 2})
    with pytest.raises(MetadataPermissionDenied):
        async with meta.transaction("access", name="owner", role="ACCOUNTADMIN") as (_, conn):
            await conn.execute("UPDATE nova_meta.config_business_policies SET revision=9")


# ── Superset ──────────────────────────────────────────────────────────────


async def test_author_roles_change_as_a_set_from_the_revision_read(meta):
    async with meta.transaction("superset", name="owner", role="ACCOUNTADMIN") as (repo, _):
        assert await repo.author_roles() == (frozenset(), 0)
        assert await repo.replace_author_roles(["BI_AUTHOR", "ANALYST"],
                                               expected_revision=0) == (
            ["ANALYST", "BI_AUTHOR"], 1)
    ready = asyncio.Barrier(2)

    async def replace(roles):
        async with meta.transaction("superset", name="owner", role="ACCOUNTADMIN") as (repo, _):
            await ready.wait()
            return await repo.replace_author_roles(roles, expected_revision=1)

    results = await asyncio.gather(replace(["ANALYST"]), replace(["FINANCE"]),
                                   return_exceptions=True)
    assert sum(isinstance(r, tuple) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    winner = next(r for r in results if isinstance(r, tuple))
    async with meta.transaction("superset", name="viewer") as (repo, _):
        assert await repo.author_roles() == (frozenset(winner[0]), 2)
    with pytest.raises(MetadataIntegrityViolation):  # a row change without the set revision
        async with meta.transaction("superset", name="owner", role="ACCOUNTADMIN") as (_, conn):
            await conn.execute("INSERT INTO nova_meta.config_bi_author_roles (role_name,"
                               "created_by) VALUES ('SNEAKY','owner')")
    with pytest.raises(MetadataPermissionDenied):
        async with meta.transaction("superset", name="sec", role="SECURITYADMIN") as (repo, _):
            await repo.replace_author_roles([], expected_revision=2)
    actions = await meta.admin("SELECT metadata_table,action FROM nova_meta.audit_log "
                               "WHERE metadata_table LIKE 'config_bi_author%%'")
    assert ("config_bi_author_role_set", "UPDATE") in {(a["metadata_table"], a["action"])
                                                       for a in actions}


async def test_publications_belong_to_their_owner_and_role(meta):
    start = {"operation_id": "op-1", "idempotency_scope": "scope-1",
             "request_fingerprint": "fp-1", "target_mode": "new"}

    async def begin():
        async with meta.transaction("superset", workload="agent") as (repo, _):
            return await repo.start_publication(start)

    results = await asyncio.gather(*(begin() for _ in range(3)))
    assert sum(created for _, created in results) == 1
    async with meta.transaction("superset", workload="agent") as (repo, _):
        await repo.advance_publication("op-1", "VALIDATING", {"status": "CHART_READY",
                                                             "chart_id": 11})
        with pytest.raises(MetadataConflict):
            await repo.advance_publication("op-1", "VALIDATING", {"status": "ATTACHING"})
    async with meta.transaction("superset") as (repo, _):
        done = await repo.advance_publication(
            "op-1", "CHART_READY",
            {"status": "SUCCEEDED", "business_context": {"concept": "orders"}})
        assert done["owner_name"] == "alice" and done["active_role"] == "ANALYST"
        assert (await repo.publication_by_scope("scope-1"))["operation_id"] == "op-1"
        assert await repo.list_publications(unresolved=True) == []
    with pytest.raises(MetadataIntegrityViolation):  # a finished operation is frozen
        async with meta.transaction("superset") as (repo, _):
            await repo.advance_publication("op-1", "SUCCEEDED", {"status": "COMPENSATING"})
    with pytest.raises(MetadataIntegrityViolation):  # continued only in its own role
        async with meta.transaction("superset", role="ACCOUNTADMIN") as (repo, _):
            await repo.start_publication({**start, "operation_id": "op-2"})
            await repo.connection.execute(
                "UPDATE nova_meta.config_dataviz_publications SET error='x' "
                "WHERE operation_id='op-1'")
    with pytest.raises(MetadataConflict):
        async with meta.transaction("superset", name="bob") as (repo, _):
            await repo.start_publication(start)
    async with meta.transaction("superset", name="bob") as (repo, conn):
        assert await repo.get_publication("op-1") is None
        assert await repo.published_contexts([11, 12]) == {11: {"concept": "orders"}}
        cursor = await conn.execute(
            "SELECT count(*) AS n FROM nova_meta.config_dataviz_publications")
        assert (await cursor.fetchone())["n"] == 0
    with pytest.raises(MetadataPermissionDenied):
        async with meta.transaction("superset") as (_, conn):
            await conn.execute("DELETE FROM nova_meta.config_dataviz_publications")


async def test_semantic_dataset_map_names_the_last_publisher(meta):
    async with meta.transaction("superset", name="steward") as (repo, _):
        await repo.record_semantic_dataset("view-1", "orders", status="SYNCED",
                                           bi_dataset_id=5, version=1)
    async with meta.transaction("superset", name="steward-2") as (repo, _):
        row = await repo.record_semantic_dataset("view-1", "orders", status="FAILED",
                                                 message="metric not expressible")
        assert row["owner_name"] == "steward-2"
    async with meta.transaction("superset", workload="automation") as (repo, _):
        assert [r["dataset_name"] for r in await repo.synced_datasets("view-1")] == ["orders"]
    with pytest.raises(MetadataPermissionDenied):
        async with meta.transaction("superset", workload="agent") as (repo, _):
            await repo.forget_semantic_dataset("view-1", "orders")
    with pytest.raises(MetadataIntegrityViolation):
        async with meta.transaction("superset") as (repo, _):
            await repo.record_semantic_dataset("view-1", "x", status="STALE")


# ── Migration ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("secret", [
    "hunter2", "aws://", "aws://name with space", "vault://kv/db", "password=hunter2",
])
async def test_sources_refuse_anything_but_a_secret_reference(meta, secret):
    with pytest.raises(MetadataIntegrityViolation):
        async with meta.transaction("migration") as (repo, _):
            await repo.create_source(name="legacy", host="sr.example", port=9030,
                                     username="reader", secret_ref=secret)
    log = await meta.admin("SELECT count(*) AS n FROM nova_meta.audit_log "
                           "WHERE metadata_table='config_migration_sources'")
    assert log[0]["n"] == 0


async def test_sources_keep_references_and_audit_without_them(meta):
    references = ("aws://prod/starrocks", "arn:aws:secretsmanager:eu-west-1:123456789012:secret:sr")
    async with meta.transaction("migration") as (repo, _):
        for index, ref in enumerate(references):
            await repo.create_source(name=f"src-{index}", host="sr.example", port=9030,
                                     username="reader", secret_ref=ref)
        await repo.create_source(name="no-password", host="sr.example", port=9030,
                                 username="reader", secret_ref="")
    with pytest.raises(MetadataConflict):
        async with meta.transaction("migration") as (repo, _):
            await repo.create_source(name="src-0", host="x", port=1, username="u",
                                     secret_ref="")
    async with meta.transaction("migration", workload="ingestion", name="worker",
                                service=True) as (repo, _):
        assert [s["secret_ref"] for s in await repo.get_source("src-0")] == [references[0]]
    audit = await meta.admin(
        "SELECT object_name,metadata_key::text AS key FROM nova_meta.audit_log "
        "WHERE metadata_table='config_migration_sources'")
    assert len(audit) == 3
    assert all("aws" not in a["key"] and "aws" not in a["object_name"] for a in audit)


async def test_migration_jobs_are_owned_and_lease_fenced(meta):
    async def submit():
        async with meta.transaction("migration") as (repo, _):
            return await repo.create_job(job_id="job-1", operation="assess",
                                         session_fingerprint="f" * 64, source="src",
                                         request={"databases": ["sales"]})

    results = await asyncio.gather(*(submit() for _ in range(3)))
    assert sum(created for _, created in results) == 1
    with pytest.raises(MetadataIntegrityViolation):
        async with meta.transaction("migration") as (repo, _):
            await repo.create_job(operation="execute", session_fingerprint="f", source="src",
                                  request={"source": {"Password": "hunter2"}})
    async with meta.transaction("migration", name="bob") as (repo, _):
        assert await repo.get_job("job-1") is None
    async with meta.transaction("migration", workload="ingestion", name="worker",
                                service=True) as (repo, _):
        assert [job["id"] for job in await repo.queued()] == ["job-1"]

    async def claim():
        async with meta.transaction("migration", workload="ingestion", name="worker",
                                    service=True) as (repo, _):
            return await repo.claim("job-1")

    claims = await asyncio.gather(*(claim() for _ in range(3)))
    assert sorted(claims, key=lambda c: c or 0) == [None, None, 1]
    async with meta.transaction("migration", workload="ingestion", name="worker",
                                service=True) as (repo, _):
        assert await repo.heartbeat("job-1", generation=2) is False
        assert await repo.progress("job-1", generation=1, current_database="sales",
                                   result={"tables": 3}) is True
        with pytest.raises(ValueError):
            await repo.finish("job-1", generation=1, status="queued")
        assert await repo.finish("job-1", generation=1, status="succeeded",
                                 result={"tables": 4}) is True
        assert await repo.finish("job-1", generation=1, status="failed") is False
        assert await repo.prune_completed_reads() == 0  # finished less than a day ago
    async with meta.transaction("migration") as (repo, _):
        done = await repo.get_job("job-1")
        assert done["status"] == "succeeded" and done["finished_at"] is not None
        assert done["current_database"] is None
        assert await repo.delete_finished_job("job-1") is True


# ── Usage ─────────────────────────────────────────────────────────────────


async def test_usage_is_append_only_and_read_by_administrators(meta):
    totals = [("summarize", "fast", "model-a", "success", 3, 10, 5, 15, 120)] * 2
    async with meta.transaction("usage", name="gateway", service=True) as (repo, _):
        assert await repo.record(totals) == 2
    async with meta.transaction("usage", name="owner", role="ACCOUNTADMIN") as (repo, _):
        rows = await repo.function_tokens(*await _window(meta))
        assert len(rows) == 2 and rows[0]["record_id"] != rows[1]["record_id"]
        assert {row["provenance"] for row in rows} == {"runtime"}
    async with meta.transaction("usage", name="bob") as (repo, _):
        assert await repo.function_tokens(*await _window(meta)) == []
    for statement in ("UPDATE nova_meta.usage_llm_functions SET request_count=0",
                      "DELETE FROM nova_meta.usage_llm_functions"):
        with pytest.raises(MetadataPermissionDenied):
            async with meta.transaction("usage", name="owner", role="ACCOUNTADMIN") as (_, c):
                await c.execute(statement)
    with pytest.raises(MetadataIntegrityViolation):
        async with meta.transaction("usage", name="gateway", service=True) as (repo, _):
            await repo.record([("summarize", None, None, "success", 0, 0, 0, 0, 0)])


async def _window(meta):
    row = await meta.admin("SELECT now()-interval '1 hour' AS s, now()+interval '1 hour' AS e")
    return row[0]["s"], row[0]["e"]


# ── Workloads, maintenance and import ─────────────────────────────────────

#: workload -> tables it may read (anything else is refused outright).
READS = {
    "web": {"config_assistant_threads", "config_assistant_messages", "config_assistant_ui_state",
            "config_i18n_messages", "config_security_role_projections",
            "config_security_policies", "config_data_scope_assignments",
            "config_business_policies", "config_bi_author_roles", "config_bi_author_role_set",
            "config_dataviz_publications", "config_semantic_bi_datasets",
            "config_migration_sources", "config_migration_jobs", "usage_llm_functions"},
    "agent": {"config_assistant_threads", "config_assistant_messages",
              "config_assistant_ui_state", "config_i18n_messages", "config_business_policies",
              "config_dataviz_publications", "config_semantic_bi_datasets"},
    "task": {"config_assistant_threads", "config_assistant_messages"},
    "ingestion": {"config_migration_sources", "config_migration_jobs"},
}
READS["automation"] = READS["agent"]
TABLES = sorted(READS["web"])


@pytest.mark.parametrize("workload", [role.removeprefix("nova_meta_") for role in WORKLOAD_ROLES])
async def test_workload_privileges(meta, workload):
    allowed = READS.get(workload, set())
    for table in TABLES:
        if table in allowed:
            async with meta.transaction(workload=workload, read_only=True) as (_, conn):
                await conn.execute(f"SELECT count(*) FROM nova_meta.{table}")
        else:
            with pytest.raises(MetadataPermissionDenied):
                async with meta.transaction(workload=workload, read_only=True) as (_, conn):
                    await conn.execute(f"SELECT count(*) FROM nova_meta.{table}")
    if workload not in {"web", "agent", "automation"}:
        with pytest.raises(MetadataPermissionDenied):
            async with meta.transaction(workload=workload) as (repo, _):
                await repo.create_thread(thread_id="t-" + workload)
    if workload != "web":
        with pytest.raises(MetadataPermissionDenied):
            async with meta.transaction("access", workload=workload, name="sec",
                                        role="ACCOUNTADMIN") as (repo, _):
                await repo.put_role_projection("r_" + workload, ProjectionState.PENDING,
                                               expected_revision=None)


async def test_unbound_actor_is_refused(meta):
    async with meta.transaction() as (_, conn):
        await conn.execute("SELECT set_config('nova.service_identity','someone-else',true)")
        cursor = await conn.execute("SELECT count(*) AS n FROM nova_meta.config_i18n_messages")
        assert (await cursor.fetchone())["n"] == 0
    with pytest.raises(MetadataPermissionDenied):
        async with meta.transaction() as (repo, conn):
            await conn.execute("SELECT set_config('nova.principal','bob',true)")
            await repo.list_threads()


async def test_maintenance_blocks_writes_but_not_reads(meta):
    await thread_with_answer(meta)
    await meta.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    try:
        with pytest.raises(MetadataReadOnlyMaintenance):
            async with meta.transaction() as (repo, _):
                await repo.append_message("t-alice", role="user", content="x")
        async with meta.transaction(read_only=True) as (repo, _):
            assert len(await repo.list_messages("t-alice")) == 2
    finally:
        await meta.admin("UPDATE nova_meta.deployment_state SET maintenance=false")


async def test_import_keeps_legacy_rows_only_under_maintenance(meta):
    env = meta.environment
    async with await psycopg.AsyncConnection.connect(
        env.role_config("nova_meta_migrator").dsn, autocommit=True,
    ) as conn:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE nova_meta_owner")
                await conn.execute(
                    "INSERT INTO nova_meta.config_i18n_messages (language,message_id,version,"
                    "text) VALUES ('id','x',1,'y')")
    async with meta.importer() as conn:
        await conn.execute(
            "INSERT INTO nova_meta.config_assistant_threads (thread_id,user_name,title,"
            "learning_enabled,created_at,updated_at) VALUES ('legacy-t','alice','Old',false,"
            "NULL,NULL)")
        for message_id in ("legacy-1", "legacy-2"):  # a duplicated sequence number is kept
            await conn.execute(
                "INSERT INTO nova_meta.config_assistant_messages (message_id,thread_id,user_name,"
                "seq,role,content,created_at,feedback) VALUES (%s,'legacy-t','alice',0,'user',"
                "'old',NULL,'like')", (message_id,))
        await conn.execute(
            "INSERT INTO nova_meta.config_migration_sources (id,name,host,username,secret_ref) "
            "VALUES ('src-old','old','h','u','legacy/bare-name'),"
            "('src-old-2','old','h','u',NULL)")
        await conn.execute(
            "INSERT INTO nova_meta.config_business_policies (id,revision,definition) VALUES "
            "('default:aaaa',3,'{\"revision\":3}'),('default:bbbb',3,'{\"revision\":3}')")
        await conn.execute(
            "INSERT INTO nova_meta.usage_llm_functions (record_id,usage_id,recorded_at,"
            "function_name,status,request_count) VALUES (gen_random_uuid(),7,now(),'f','ok',1),"
            "(gen_random_uuid(),7,now(),'f','ok',1)")
    async with meta.transaction() as (repo, _):
        assert [m["message_id"] for m in await repo.list_messages("legacy-t")] == [
            "legacy-1", "legacy-2"]
        row, created = await repo.append_message("legacy-t", role="user", content="new")
        assert created and row["seq"] == 1
    async with meta.transaction("migration") as (repo, _):
        assert [s["id"] for s in await repo.get_source("old")] == ["src-old", "src-old-2"]
    with pytest.raises(MetadataConflict):  # an unreconciled source race is not picked
        async with meta.transaction("access", workload="agent") as (repo, _):
            await repo.current_business_policy()
    rows = await meta.admin("SELECT usage_id,provenance FROM nova_meta.usage_llm_functions")
    assert [(r["usage_id"], r["provenance"]) for r in rows] == [(7, "imported")] * 2
    audit = await meta.admin("SELECT count(*) AS n FROM nova_meta.audit_log "
                             "WHERE metadata_table LIKE 'config_%%'")
    assert audit[0]["n"] == 0
