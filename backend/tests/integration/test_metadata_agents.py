"""Agent metadata on the real PG fixture: CAS, journals, fencing, scope, import."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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
from app.modules.access_control.security_context import SecurityContext
from app.modules.agents.metadata_repository import AgentMetadataRepository, CredentialRefused
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg
USERS = {"web", "agent", "automation"}
CANARY = "Canary-7d41e2"


class Agents:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, workload="web", *, name="agent-owner", active="ANALYST",
                          login=None, version=5, actor=None, read_only=False):
        if actor is None:
            user = session(name, active=active, roles=[active, "ANALYST", "VIEWER"],
                           security_context_version=version)
            if login:
                user["session_id"] = login
            actor = SecurityContext.from_session(user)
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            async with db.transaction(actor, read_only=read_only) as conn:
                yield AgentMetadataRepository(conn, actor), conn
        finally:
            await db.close()

    async def admin(self, query, params=()):
        cursor = await self.environment.admin.execute(query, params)
        return await cursor.fetchall() if cursor.description else None

    @asynccontextmanager
    async def importer(self):
        async with await psycopg.AsyncConnection.connect(
            self.environment.role_config("nova_meta_migrator").dsn, autocommit=True,
        ) as conn, conn.transaction():
            await conn.execute("SET LOCAL ROLE nova_meta_owner")
            yield conn


@pytest_asyncio.fixture
async def agents(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="d" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return Agents(environment, epoch)


def frame(sequence, text="step"):
    return "data: " + json.dumps({"sequence": sequence, "type": "delta", "text": text}) + "\n\n"


async def create_agent(agents, **fields):
    async with agents.transaction() as (repo, _):
        return await repo.create_agent({"name": "Sales analyst", "default_skills": ["forecast"],
                                        "default_tools": ["margin"], **fields},
                                       config_revision="rev-0")


# ── Governed definitions ─────────────────────────────────────────────────


async def test_agent_changes_need_the_revision_they_read_and_keep_snapshots_atomic(agents):
    agent = await create_agent(agents)
    assert agent["owner_name"] == "agent-owner" and agent["provenance"] == "runtime"
    ready = asyncio.Barrier(2)

    async def edit(label):
        async with agents.transaction() as (repo, _):
            await ready.wait()
            return await repo.update_agent(
                agent["agent_id"], {"description": label}, expected_config_revision="rev-0",
                snapshots=[{"version_id": f"v-{label}", "configuration": {"d": label},
                            "label": "Saved configuration"}])

    results = await asyncio.gather(edit("a"), edit("b"), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    winner = next(r for r in results if isinstance(r, dict))
    assert winner["metadata_revision"] == 2 and winner["config_revision"] != "rev-0"
    # The loser's snapshot rolled back with its failed CAS.
    versions = await agents.admin("SELECT version_id FROM nova_meta.config_agent_versions")
    assert [v["version_id"] for v in versions] == [f"v-{winner['description']}"]
    with pytest.raises(MetadataIntegrityViolation):
        async with agents.transaction() as (_, conn):
            await conn.execute("UPDATE nova_meta.config_agents SET description='x',"
                               "metadata_revision=metadata_revision+1 WHERE agent_id=%s",
                               (agent["agent_id"],))
    audit = await agents.admin(
        "SELECT action,metadata_revision FROM nova_meta.audit_log "
        "WHERE metadata_table='config_agents' ORDER BY metadata_revision")
    assert [(a["action"], a["metadata_revision"]) for a in audit] == [("CREATE", 1), ("UPDATE", 2)]


async def test_snapshots_and_manifests_are_immutable(agents):
    agent = await create_agent(agents)
    async with agents.transaction() as (repo, _):
        await repo.store_version("v-1", agent["agent_id"], {"a": 1}, "Saved")
        again = await repo.store_version("v-1", agent["agent_id"], {"a": 1}, "Saved")
        assert again["configuration"] == '{"a":1}'
        await repo.store_manifest("m-1", agent["agent_id"], "v-1", {"tools": []})
    with pytest.raises(MetadataConflict):
        async with agents.transaction() as (repo, _):
            await repo.store_version("v-1", agent["agent_id"], {"a": 2}, "Saved")
    with pytest.raises(MetadataConflict):
        async with agents.transaction() as (repo, _):
            await repo.store_manifest("m-1", agent["agent_id"], "v-1", {"tools": [1]})
    with pytest.raises(MetadataIntegrityViolation):
        async with agents.transaction() as (repo, _):
            await repo.store_manifest("m-2", agent["agent_id"], "missing", {"tools": []})
    for statement in ("UPDATE nova_meta.config_agent_versions SET label='x'",
                      "DELETE FROM nova_meta.config_agent_release_manifests"):
        with pytest.raises(MetadataPermissionDenied):
            async with agents.transaction() as (_, conn):
                await conn.execute(statement)


async def test_immutable_version_compares_json_types_and_accepts_formatting_only(agents):
    agent = await create_agent(agents)
    async with agents.transaction() as (store, _):
        await store.store_version("typed", agent["agent_id"], {"number": 1}, "Saved")
        row = await store.store_version("typed", agent["agent_id"], '{ "number" : 1 }', "Again")
        assert row["label"] == "Saved"
    for changed in ({"number": True}, {"number": 1.0}, {"number": "1"}):
        with pytest.raises(MetadataConflict):
            async with agents.transaction() as (store, _):
                await store.store_version("typed", agent["agent_id"], changed, "Changed")


async def test_shared_agents_follow_the_active_role_only(agents):
    agent = await create_agent(agents, visibility="shared")
    async with agents.transaction() as (repo, _):
        await repo.create_skill({"name": "forecast", "body": "Use seasonality."})
        await repo.create_skill({"name": "private-notes", "body": "Owner only."})
        await repo.create_custom_tool({"name": "margin", "kind": "function",
                                       "function_name": "sales.margin"})
        await repo.create_custom_tool({"name": "payroll", "kind": "function"})
        roles = await repo.replace_agent_roles(agent["agent_id"], {"VIEWER": "USAGE"})
        assert [r["role_name"] for r in roles] == ["VIEWER"]
    # Another person with VIEWER assigned but ANALYST active sees nothing.
    async with agents.transaction("agent", name="colleague", read_only=True) as (repo, _):
        assert await repo.get_agent(agent["agent_id"]) is None
        assert await repo.list_skills("agent-owner") == []
    async with agents.transaction("agent", name="colleague", active="VIEWER",
                                  read_only=True) as (repo, _):
        assert (await repo.get_agent(agent["agent_id"]))["name"] == "Sales analyst"
        assert [s["name"] for s in await repo.list_skills("agent-owner")] == ["forecast"]
        assert [t["name"] for t in await repo.list_custom_tools("agent-owner")] == ["margin"]
        assert [r["role_name"] for r in await repo.list_agent_roles(agent["agent_id"])] == [
            "VIEWER"]
    # Only the owner may change the agent or its grants.
    with pytest.raises((MetadataPermissionDenied, MetadataConflict)):
        async with agents.transaction(name="colleague", active="VIEWER") as (repo, _):
            await repo.update_agent(agent["agent_id"], {"name": "Mine"},
                                    expected_config_revision="rev-0")
    with pytest.raises(MetadataPermissionDenied):
        async with agents.transaction(name="colleague", active="VIEWER") as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_agent_roles (agent_id,role_name,owner_name,"
                "grant_type) VALUES (%s,'ANALYST','agent-owner','USAGE')", (agent["agent_id"],))
    async with agents.transaction() as (repo, _):
        assert await repo.replace_agent_roles(agent["agent_id"], {}) == []
    async with agents.transaction("agent", name="colleague", active="VIEWER",
                                  read_only=True) as (repo, _):
        assert await repo.get_agent(agent["agent_id"]) is None


async def test_role_grants_are_revisioned_and_never_target_accountadmin(agents):
    agent = await create_agent(agents)
    async with agents.transaction() as (repo, _):
        await repo.replace_agent_roles(agent["agent_id"], {"VIEWER": "USAGE"})
        verified = await repo.set_role_verification(agent["agent_id"], "VIEWER", "f" * 64,
                                                    expected_revision=1)
        assert verified["metadata_revision"] == 2 and verified["verified_at"] is not None
    with pytest.raises(MetadataConflict):
        async with agents.transaction() as (repo, _):
            await repo.set_role_verification(agent["agent_id"], "VIEWER", None,
                                             expected_revision=1)
    with pytest.raises(MetadataIntegrityViolation):
        async with agents.transaction() as (repo, _):
            await repo.replace_agent_roles(agent["agent_id"], {"ACCOUNTADMIN": "USAGE"})


async def test_long_skill_bodies_publish_only_complete(agents):
    body = "é" * 40000
    async with agents.transaction() as (repo, _):
        skill = await repo.create_skill({"name": "long", "body": body})
        assert skill["body"] == body
        updated = await repo.update_skill(skill["skill_id"], {"description": "d"},
                                          expected_revision=1)
        assert updated["metadata_revision"] == 2
    with pytest.raises(MetadataIntegrityViolation):
        async with agents.transaction() as (_, conn):
            await conn.execute(
                "UPDATE nova_meta.config_agent_skills SET body='nova-skill-body:v1:nope:3',"
                "metadata_revision=metadata_revision+1 WHERE skill_id=%s", (skill["skill_id"],))
    async with agents.transaction() as (repo, _):
        assert await repo.delete_skill(skill["skill_id"]) is True
    assert await agents.admin("SELECT 1 FROM nova_meta.config_skill_body_chunks") == []


async def test_tools_register_idempotently_and_go_with_their_server(agents):
    async with agents.transaction() as (repo, _):
        server = await repo.create_mcp_server({"name": "crm", "transport": "http",
                                               "endpoint": "https://crm.example/mcp"})
        source = f"mcp:{server['server_id']}"
        first = await repo.upsert_tool({"name": "lookup", "source": source})
        second = await repo.upsert_tool({"name": "lookup", "source": source,
                                         "description": "Find a customer"})
        assert first["tool_id"] == second["tool_id"] and second["metadata_revision"] == 2
        assert await repo.set_mcp_status(server["server_id"], "connected") is True
        assert await repo.delete_mcp_server(server["server_id"]) is True
        assert await repo.list_tools() == []
    statuses = await agents.admin(
        "SELECT action FROM nova_meta.audit_log WHERE metadata_table='config_mcp_servers' "
        "ORDER BY recorded_at")
    assert [s["action"] for s in statuses] == ["CREATE", "DELETE"]


async def test_credentials_are_refused_without_being_echoed(agents, caplog):
    secret = f"password='{CANARY}'"
    for attempt in (
        lambda repo: repo.create_agent({"name": "x", "instructions_response": secret}),
        lambda repo: repo.create_skill({"name": "x", "body": secret}),
        lambda repo: repo.create_mcp_server({"name": "x", "transport": "stdio",
                                             "command": "run", "args": [secret]}),
    ):
        with pytest.raises(CredentialRefused) as refused:
            async with agents.transaction() as (repo, _):
                await attempt(repo)
        assert CANARY not in str(refused.value)
    # The database refuses a connector credential in an endpoint, with a fixed message.
    with pytest.raises(MetadataIntegrityViolation) as refused:
        async with agents.transaction() as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_mcp_servers (server_id,owner_name,name,transport,"
                "endpoint) VALUES ('s','agent-owner','x','http',%s)",
                (f"https://svc:{CANARY}@crm.example/mcp",))
    assert CANARY not in str(refused.value) and CANARY not in caplog.text
    rows = await agents.admin(
        "SELECT count(*) AS n FROM nova_meta.config_mcp_servers WHERE endpoint LIKE %s",
        (f"%{CANARY}%",))
    assert rows[0]["n"] == 0


# ── Run journals ─────────────────────────────────────────────────────────


async def test_event_batches_and_their_high_water_commit_together(agents):
    async with agents.transaction("agent") as (repo, _):
        run = await repo.start_run("run-1", "agent-1", "thread-1")
        assert (run["claim_generation"], run["last_sequence"]) == (1, -1)
        assert run["session_id"] == "login-agent-owner" and run["security_version"] == 5
        assert await repo.append_events("run-1", [frame(0), frame(1)], generation=1) == 1
    # A resend of identical frames after an unknown commit is accepted again.
    async with agents.transaction("agent") as (repo, _):
        assert await repo.append_events("run-1", [frame(1), frame(2)], generation=1) == 2
    # The same sequence with different content is a conflict; nothing is kept.
    with pytest.raises(MetadataConflict):
        async with agents.transaction("agent") as (repo, _):
            await repo.append_events("run-1", [frame(2, "other"), frame(3)], generation=1)
    # Events without their high-water mark cannot commit, nor can a mark without its event.
    with pytest.raises(MetadataIntegrityViolation):
        async with agents.transaction("agent") as (_, conn):
            await conn.execute("INSERT INTO nova_meta.config_agent_run_events (run_id,sequence,"
                               "frame,claim_generation) VALUES ('run-1',3,%s,1)", (frame(3),))
    with pytest.raises(MetadataIntegrityViolation):
        async with agents.transaction("agent") as (_, conn):
            await conn.execute("UPDATE nova_meta.config_agent_runs SET last_sequence=9 "
                               "WHERE run_id='run-1'")
    # A frame whose embedded sequence differs from the stored one is refused.
    with pytest.raises(MetadataIntegrityViolation):
        async with agents.transaction("agent") as (_, conn):
            await conn.execute("INSERT INTO nova_meta.config_agent_run_events (run_id,sequence,"
                               "frame,claim_generation) VALUES ('run-1',4,%s,1)", (frame(5),))
    async with agents.transaction("agent", read_only=True) as (repo, _):
        history = await repo.history("run-1")
        assert history["frames"] == [frame(0), frame(1), frame(2)]
        assert history["complete"] is True and history["last_sequence"] == 2
    frames = await agents.admin("SELECT frame FROM nova_meta.config_agent_run_events "
                                "WHERE run_id='run-1' ORDER BY sequence")
    assert [f["frame"] for f in frames] == [frame(0), frame(1), frame(2)]


async def test_concurrent_batches_publish_one_contiguous_journal(agents):
    async with agents.transaction("agent") as (repo, _):
        await repo.start_run("run-c", "agent-1", "thread-1")
    ready = asyncio.Barrier(2)

    async def publish(sequence, text):
        async with agents.transaction("agent") as (repo, _):
            await ready.wait()
            return await repo.append_events("run-c", [frame(sequence, text)], generation=1)

    results = await asyncio.gather(publish(0, "a"), publish(0, "b"), return_exceptions=True)
    assert sum(r == 0 for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1


async def test_a_stale_producer_is_fenced_out_of_the_run(agents):
    async with agents.transaction("agent") as (repo, _):
        await repo.start_run("run-2", "agent-1", "thread-1")
        await repo.append_events("run-2", [frame(0)], generation=1)
    await asyncio.sleep(0.05)
    # The same person resumes from another login; the old producer loses its claim.
    async with agents.transaction("agent", login="login-2") as (repo, _):
        assert await repo.claim_run("run-2", stale_after_seconds=0) == 2
        assert await repo.append_events("run-2", [frame(1)], generation=2) == 1
    async with agents.transaction("agent") as (repo, _):
        assert await repo.heartbeat_run("run-2", generation=1) is False
        assert await repo.finish_run("run-2", "failed", generation=1) is False
    with pytest.raises(MetadataConflict):
        async with agents.transaction("agent") as (repo, _):
            await repo.append_events("run-2", [frame(2)], generation=1)
    with pytest.raises(MetadataPermissionDenied):
        async with agents.transaction("agent") as (_, conn):
            await conn.execute("INSERT INTO nova_meta.config_agent_run_events (run_id,sequence,"
                               "frame,claim_generation) VALUES ('run-2',2,%s,2)", (frame(2),))
    # A newer security version cannot write under the old claim either.
    with pytest.raises(MetadataConflict):
        async with agents.transaction("agent", login="login-2", version=6) as (repo, _):
            await repo.append_events("run-2", [frame(2)], generation=2)
    async with agents.transaction("agent", login="login-2") as (repo, _):
        assert await repo.finish_run("run-2", "completed", generation=2) is True
    with pytest.raises(MetadataIntegrityViolation):
        async with agents.transaction("agent", login="login-2") as (_, conn):
            await conn.execute("UPDATE nova_meta.config_agent_runs SET status='running' "
                               "WHERE run_id='run-2'")


async def test_interrupting_and_role_changes_close_replay(agents):
    async with agents.transaction("web") as (repo, _):
        await repo.start_run("run-3", "agent-1", "thread-1")
    await asyncio.sleep(0.05)
    async with agents.transaction("web", login="login-other") as (repo, _):
        assert await repo.interrupt_stale("run-3", stale_after_seconds=0) is True
    with pytest.raises(MetadataConflict):
        async with agents.transaction("web") as (repo, _):
            await repo.append_events("run-3", [frame(0)], generation=1)
    async with agents.transaction("web") as (repo, _):
        assert await repo.block_replay_after_role_change("run-3") is True
        assert await repo.get_run("run-3") is None
        assert (await repo.history("run-3"))["found"] is False
    # Another person cannot see, claim or delete the owner's runs.
    async with agents.transaction("web", name="intruder") as (repo, conn):
        assert await repo.delete_thread_runs("thread-1") == 0
        cursor = await conn.execute("SELECT count(*) AS n FROM nova_meta.config_agent_runs")
        assert (await cursor.fetchone())["n"] == 0
    async with agents.transaction("web") as (repo, _):
        assert await repo.delete_thread_runs("thread-1") == 1


# ── Missions and automations ─────────────────────────────────────────────


async def test_missions_need_their_revision_and_explicit_resume(agents):
    async with agents.transaction("agent") as (repo, _):
        mission = await repo.create_mission("m-1", "thread-1", {"goal": "Revenue"})
        assert mission["session_id"] == "login-agent-owner" and mission["revision"] == 1
        saved = await repo.save_mission("m-1", {"goal": "Revenue", "step": 1},
                                        expected_revision=1)
        assert saved["revision"] == 2
        receipt, created = await repo.deliver("m-1", "a" * 64, {"artifact": "x"})
        again, created_again = await repo.deliver("m-1", "a" * 64, {"artifact": "x"})
        assert created and not created_again
        assert receipt["deliverable_id"] == again["deliverable_id"]
    with pytest.raises(MetadataConflict):
        async with agents.transaction("agent") as (repo, _):
            await repo.deliver("m-1", "a" * 64, {"artifact": "other"})
    # A new login sees the Mission but cannot advance it without resuming.
    async with agents.transaction("agent", login="login-2", version=6) as (repo, _):
        assert await repo.get_mission("m-1") is None
        assert await repo.get_mission("m-1", session_scope=False) is not None
    with pytest.raises(MetadataConflict):
        async with agents.transaction("agent", login="login-2", version=6) as (repo, _):
            await repo.save_mission("m-1", {"step": 2}, expected_revision=2)
    async with agents.transaction("agent", login="login-2", version=6) as (repo, _):
        resumed = await repo.save_mission("m-1", {"step": 2}, expected_revision=2, resume=True)
        assert (resumed["session_id"], resumed["security_version"]) == ("login-2", 6)
    async with agents.transaction("agent", active="VIEWER", read_only=True) as (repo, _):
        assert await repo.get_mission("m-1", session_scope=False) is None


async def test_one_worker_claims_a_due_automation_and_only_it_records(agents):
    due = datetime.now(UTC) - timedelta(minutes=1)
    async with agents.transaction() as (repo, _):
        automation = await repo.create_automation("agent-1", {
            "title": "Daily revenue", "prompt": "Summarize revenue", "schedule_kind": "interval",
            "schedule_expr": "1 DAY", "timezone": "Asia/Jakarta", "enabled": True,
            "next_run_at": due})
    service = SecurityContext("automation-worker", "ANALYST", principal_type="SERVICE",
                              session_id="automation-1")
    async with agents.transaction("automation", actor=service) as (repo, _):
        [found] = await repo.due_automations(datetime.now(UTC))
    ready = asyncio.Barrier(3)

    async def claim():
        async with agents.transaction("automation", actor=service) as (repo, _):
            await ready.wait()
            return await repo.claim_automation(
                found["automation_id"], seen_next_run_at=found["next_run_at"],
                seen_generation=found["run_generation"])

    results = await asyncio.gather(claim(), claim(), claim())
    assert sorted(results, key=lambda r: r or 0) == [None, None, 1]
    tomorrow = due + timedelta(days=1)
    async with agents.transaction("automation", actor=service) as (repo, _):
        assert await repo.record_automation_run(
            found["automation_id"], generation=7, status="completed", thread_id="t",
            ran_at=datetime.now(UTC), next_run_at=tomorrow) is False
        assert await repo.record_automation_run(
            found["automation_id"], generation=1, status="completed", thread_id="t",
            ran_at=datetime.now(UTC), next_run_at=tomorrow) is True
    # The worker cannot rewrite the owner's definition.
    with pytest.raises(MetadataPermissionDenied):
        async with agents.transaction("automation", actor=service) as (_, conn):
            await conn.execute("UPDATE nova_meta.config_agent_automations SET prompt='x'")
    async with agents.transaction() as (repo, _):
        updated = await repo.update_automation(automation["automation_id"], {"title": "Daily"},
                                               expected_revision=1)
        assert updated["run_generation"] == 1 and updated["last_status"] == "completed"
    actions = await agents.admin(
        "SELECT action FROM nova_meta.audit_log WHERE metadata_table='config_agent_automations' "
        "ORDER BY recorded_at")
    assert [a["action"] for a in actions] == ["CREATE", "UPDATE"]
    # A person's own transaction cannot scan others' automations.
    async with agents.transaction(name="someone-else", read_only=True) as (repo, _):
        assert await repo.due_automations(datetime.now(UTC) + timedelta(days=2)) == []
    with pytest.raises(MetadataIntegrityViolation):
        async with agents.transaction() as (repo, _):
            await repo.create_automation("__smart__", {
                "title": "Old", "prompt": "x", "schedule_kind": "cron",
                "schedule_expr": "0 * * * *", "timezone": "UTC", "enabled": True})


# ── Knowledge, quality and review ────────────────────────────────────────


async def test_memory_projection_commits_with_its_revision(agents):
    async with agents.transaction("agent") as (repo, _):
        memory = await repo.create_memory("agent-1", {
            "fact_key": "fiscal_year", "fact": "Starts in April", "source_quote": "April",
            "source_thread_id": "t-1"}, {"fact": "Starts in April"}, {"e1": {"message": "m"}})
        revised = await repo.revise_memory(memory["memory_id"], {"fact": "Starts in May"},
                                           {"fact": "Starts in May"}, expected_revision=1)
        assert revised["revision"] == 2
    with pytest.raises(MetadataConflict):
        async with agents.transaction("agent") as (repo, _):
            await repo.revise_memory(memory["memory_id"], {"fact": "June"}, {"fact": "June"},
                                     expected_revision=1)
    with pytest.raises(MetadataIntegrityViolation):
        async with agents.transaction("agent") as (_, conn):
            await conn.execute("UPDATE nova_meta.config_agent_memories SET revision=3,fact='x' "
                               "WHERE memory_id=%s", (memory["memory_id"],))
    async with agents.transaction("agent", active="VIEWER", read_only=True) as (repo, _):
        assert await repo.memory_revisions(memory["memory_id"]) == []


async def test_quality_records_refuse_forks_stale_revisions_and_foreign_ids(agents):
    async with agents.transaction() as (repo, _):
        assert await repo.save_quality_record("cases", "case-1", "agent-1", {"q": 1},
                                              expected_revision=0) == 1
    ready = asyncio.Barrier(2)

    async def save(value):
        async with agents.transaction() as (repo, _):
            await ready.wait()
            return await repo.save_quality_record("cases", "case-1", "agent-1", {"q": value},
                                                  expected_revision=1)

    results = await asyncio.gather(save(2), save(3), return_exceptions=True)
    assert sorted(r for r in results if isinstance(r, int)) == [2]
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    with pytest.raises(MetadataPermissionDenied):
        async with agents.transaction(name="intruder") as (repo, _):
            await repo.save_quality_record("cases", "case-1", "agent-1", {"q": 9},
                                           expected_revision=2)
    async with agents.transaction(read_only=True) as (repo, _):
        [head] = await repo.current_quality_records("cases", "agent-1")
        assert head["revision"] == 2
    with pytest.raises(MetadataPermissionDenied):
        async with agents.transaction("agent") as (repo, _):
            await repo.save_quality_record("runs", "run-q", "agent-1", {}, expected_revision=0)


async def test_candidates_are_unique_while_active_and_decided_once(agents):
    data = {"agent_id": "agent-1", "owner_name": "agent-owner", "view_id": "sales.v",
            "question": "Revenue by month", "semantic_plan": {"metrics": ["revenue"]},
            "plan_sha256": "a" * 64, "thread_id": "t", "message_id": "m"}

    async def propose():
        async with agents.transaction("agent", name="agent-owner") as (repo, _):
            return await repo.propose_candidate(data)

    results = await asyncio.gather(*(propose() for _ in range(4)))
    assert sum(r is not None for r in results) == 1
    candidate = next(r for r in results if r)

    async def decide(status):
        async with agents.transaction() as (repo, _):
            return await repo.decide_candidate(candidate, status)

    decisions = await asyncio.gather(decide("approved"), decide("rejected"))
    assert sorted(decisions) == [False, True]
    with pytest.raises(MetadataPermissionDenied):
        async with agents.transaction("agent") as (repo, _):
            await repo.decide_candidate(candidate, "approved")
    audit = await agents.admin("SELECT count(*) AS n FROM nova_meta.audit_log "
                               "WHERE metadata_table='config_verified_query_candidates'")
    assert audit[0]["n"] == 1


async def test_rule_proposals_research_artifacts_and_shares(agents):
    async with agents.transaction("agent") as (repo, _):
        proposal = await repo.create_rule_proposal({
            "agent_id": "agent-1", "memory_id": "mem", "semantic_model_id": "sm",
            "metric_name": "revenue", "prior_expression": "sum(a)", "proposed_expression":
            "sum(b)", "prior_fingerprint": "p", "proposed_fingerprint": "q"})
        research = await repo.create_research_run("agent-1", "t", "Why did revenue fall?")
        updated = await repo.update_research_run(research, {"status": "running",
                                                            "plan_json": [{"q": 1}]},
                                                 expected_revision=1)
        assert updated["plan_json"] == '[{"q":1}]'
        artifact = await repo.create_artifact({"title": "Revenue", "sql_text": "SELECT 1"})
        changed = await repo.update_artifact(artifact["artifact_id"], {"title": "Rev"},
                                             expected_revision=1)
        assert changed["metadata_revision"] == 2
    with pytest.raises(MetadataConflict):
        async with agents.transaction("agent") as (repo, _):
            await repo.update_artifact(artifact["artifact_id"], {"title": "x"},
                                       expected_revision=1)
    async with agents.transaction() as (repo, _):
        assert await repo.set_rule_proposal_status(proposal["proposal_id"], "approved") is True
        assert await repo.set_rule_proposal_status(proposal["proposal_id"], "rejected") is False
        share = await repo.create_share("thread", "t", "role", "VIEWER")
    with pytest.raises(MetadataConflict):
        async with agents.transaction() as (repo, _):
            await repo.create_share("thread", "t", "role", "VIEWER")
    async with agents.transaction(name="colleague", active="VIEWER", read_only=True) as (repo, _):
        assert [s["share_id"] for s in await repo.active_grants("thread")] == [share["share_id"]]
    async with agents.transaction(name="colleague", read_only=True) as (repo, _):
        assert await repo.active_grants("thread") == []


# ── Workloads, maintenance and import ────────────────────────────────────


@pytest.mark.parametrize("workload", [role.removeprefix("nova_meta_") for role in WORKLOAD_ROLES])
async def test_workload_privileges(agents, workload):
    agent = await create_agent(agents)
    if workload not in USERS:
        if workload == "task":
            async with agents.transaction(workload, read_only=True) as (repo, _):
                assert (await repo.get_agent(agent["agent_id"]))["name"] == "Sales analyst"
        else:
            with pytest.raises(MetadataPermissionDenied):
                async with agents.transaction(workload, read_only=True) as (repo, _):
                    await repo.get_agent(agent["agent_id"])
        with pytest.raises(MetadataPermissionDenied):
            async with agents.transaction(workload) as (repo, _):
                await repo.start_run(str(uuid4()), agent["agent_id"], "t")
        return
    async with agents.transaction(workload, read_only=True) as (repo, _):
        assert (await repo.get_agent(agent["agent_id"]))["name"] == "Sales analyst"
    async with agents.transaction(workload) as (repo, _):
        run = await repo.start_run(str(uuid4()), agent["agent_id"], "t")
        assert await repo.append_events(run["run_id"], [frame(0)], generation=1) == 0
    if workload != "web":
        for write in (lambda repo: repo.create_skill({"name": "s", "body": "b"}),
                      lambda repo: repo.delete_agent(agent["agent_id"]),
                      lambda repo: repo.save_quality_record("cases", "c", "a", {},
                                                            expected_revision=0)):
            with pytest.raises(MetadataPermissionDenied):
                async with agents.transaction(workload) as (repo, _):
                    await write(repo)


async def test_unbound_or_mismatched_actors_are_refused(agents):
    agent = await create_agent(agents)
    async with agents.transaction("agent") as (repo, conn):
        await conn.execute("SELECT set_config('nova.service_identity','nova_meta_web',true)")
        with pytest.raises(MetadataPermissionDenied):
            await repo.get_agent(agent["agent_id"])
        cursor = await conn.execute("SELECT count(*) AS n FROM nova_meta.config_agents")
        assert (await cursor.fetchone())["n"] == 0


async def test_maintenance_blocks_writes_and_keeps_reads(agents):
    agent = await create_agent(agents)
    await agents.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(MetadataReadOnlyMaintenance):
        async with agents.transaction("agent") as (repo, _):
            await repo.start_run("run-m", agent["agent_id"], "t")
    async with agents.transaction(read_only=True) as (repo, _):
        assert await repo.get_agent(agent["agent_id"])


async def test_imported_running_turn_keeps_history_and_requires_explicit_interruption(agents):
    await agents.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    async with agents.importer() as conn:
        await conn.execute(
            "INSERT INTO nova_meta.config_agent_runs (run_id,owner_name,agent_id,thread_id,"
            "role_name,status,last_sequence,started_at,updated_at,session_id,security_version) "
            "VALUES ('legacy-running','agent-owner','agent-1','thread-old','ANALYST',"
            "'running',3,'2026-09-01 10:00:00+00','2026-09-01 10:05:00+00','old-login',5)"
        )
        await conn.execute(
            "INSERT INTO nova_meta.config_agent_run_events (run_id,sequence,frame,created_at) "
            "VALUES ('legacy-running',0,%s,'2026-09-01 10:00:01+00')", (frame(0),)
        )
    await agents.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    async with agents.transaction(login="new-login", read_only=True) as (repo, _):
        before = await repo.get_run("legacy-running")
        history = await repo.history("legacy-running")
        assert before["status"] == "running" and before["claim_generation"] == 0
        assert history["frames"] == [frame(0)] and history["complete"] is False
    async with agents.transaction(login="new-login") as (repo, _):
        assert not await repo.heartbeat_run("legacy-running", generation=1)
        assert not await repo.finish_run("legacy-running", "completed", generation=1)
        assert await repo.interrupt_stale("legacy-running", stale_after_seconds=60)
    async with agents.transaction(login="old-login") as (repo, _):
        with pytest.raises(MetadataConflict):
            await repo.append_events("legacy-running", [frame(1)], generation=1)
    async with agents.transaction(login="new-login", read_only=True) as (repo, _):
        after = await repo.get_run("legacy-running")
        assert after["status"] == "interrupted" and after["claim_generation"] == 1
        assert after["provenance"] == "imported" and after["last_sequence"] == 3
        assert after["started_at"] == before["started_at"]
        history = await repo.history("legacy-running")
        assert history["frames"] == [frame(0)] and history["complete"] is False


async def test_legacy_import_needs_maintenance_and_keeps_rows_as_they_were(agents):
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        async with agents.importer() as conn:
            await conn.execute("INSERT INTO nova_meta.config_studio_dashboards (dashboard_id,"
                               "owner_name,title) VALUES ('d','agent-owner','x')")
    await agents.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    async with agents.importer() as conn:
        # A completed historical run whose events were never written, and one
        # whose journal stops early, are imported exactly as they are.
        for run_id, last in (("legacy-empty", 41), ("legacy-gap", 3)):
            await conn.execute(
                "INSERT INTO nova_meta.config_agent_runs (run_id,owner_name,agent_id,thread_id,"
                "role_name,status,last_sequence,started_at,updated_at,session_id,"
                "security_version) VALUES (%s,'agent-owner','agent-1','thread-old','ANALYST',"
                "'completed',%s,'2026-09-01 10:00:00+00','2026-09-01 10:05:00+00',NULL,NULL)",
                (run_id, last))
        for sequence in (0, 1):
            await conn.execute(
                "INSERT INTO nova_meta.config_agent_run_events (run_id,sequence,frame,created_at)"
                " VALUES ('legacy-gap',%s,%s,'2026-09-01 10:00:01+00')",
                (sequence, frame(sequence)))
        await conn.execute(
            "INSERT INTO nova_meta.config_agent_quality_cases (id,revision,operation_id,agent_id,"
            "principal,active_role,security_context_version,payload,created_at) VALUES "
            "('fork',1,'a','agent-1','agent-owner','ANALYST',5,'{}',NULL),"
            "('fork',1,'b','agent-1','agent-owner','ANALYST',5,'{}',NULL)")
        await conn.execute(
            "INSERT INTO nova_meta.config_studio_dashboards (dashboard_id,owner_name,title,"
            "layout_json) VALUES ('legacy-dash','agent-owner','Old tiles','{\"tiles\":[]}')")
        await conn.execute(
            "INSERT INTO nova_meta.config_agents (agent_id,owner_name,name,created_at,"
            "updated_at,config_revision,semantic_view_ids) VALUES ('legacy-agent','agent-owner',"
            "'Old','2026-01-01 00:00:00+00','2026-01-02 00:00:00+00',NULL,'[]')")
    await agents.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    rows = await agents.admin("SELECT run_id,provenance,last_sequence FROM "
                              "nova_meta.config_agent_runs ORDER BY run_id")
    assert [(r["run_id"], r["provenance"], r["last_sequence"]) for r in rows] == [
        ("legacy-empty", "imported", 41), ("legacy-gap", "imported", 3)]
    async with agents.transaction(read_only=True) as (repo, _):
        empty = await repo.history("legacy-empty")
        assert empty["frames"] == [] and empty["complete"] is False
        gap = await repo.history("legacy-gap")
        assert gap["frames"] == [frame(0), frame(1)] and gap["complete"] is False
        with pytest.raises(MetadataConflict):
            await repo.current_quality_records("cases", "agent-1")
        assert [d["dashboard_id"] for d in await repo.legacy_dashboards()] == ["legacy-dash"]
        legacy = await repo.get_agent("legacy-agent")
        assert legacy["semantic_view_ids"] == [] and legacy["semantic_model_ids"] is None
    # An imported fork stays; the next runtime revision lands above it once.
    async with agents.transaction() as (repo, _):
        assert await repo.save_quality_record("cases", "fork", "agent-1", {},
                                              expected_revision=1) == 2
        updated = await repo.update_agent("legacy-agent", {"name": "Renamed"},
                                          expected_config_revision=None)
        assert updated["metadata_revision"] == 2
        assert await repo.forget_legacy_dashboard("legacy-dash") is True
    # Workloads cannot write imported-only stores.
    with pytest.raises(MetadataPermissionDenied):
        async with agents.transaction() as (_, conn):
            await conn.execute("INSERT INTO nova_meta.config_studio_dashboards (dashboard_id,"
                               "owner_name,title) VALUES ('d2','agent-owner','x')")


@pytest_asyncio.fixture
async def runtime_registry(agents):
    from app.core.metadata_runtime import install_metadata_db, uninstall_metadata_db
    from app.modules.agents.repository import AgentRepository

    database = MetadataDatabase(agents.environment.role_config("nova_meta_web"))
    try:
        await database.open(schema_version=len(load_migrations()), metadata_epoch=agents.epoch)
        install_metadata_db(database)
        yield AgentRepository()
    finally:
        uninstall_metadata_db(database)
        await database.close()


async def test_access_verification_preserves_session_assignments_for_real_semantic_metadata(
    agents, runtime_registry, monkeypatch,
):
    from unittest.mock import AsyncMock

    from app.core.metadata_actor import acting_as
    from app.modules.access_control.service import access_control_service
    from app.modules.agents import access, studio_router
    from app.modules.agents.semantic.ir import SemanticModelIR
    from app.modules.agents.semantic.ossie import parse_ossie
    from app.modules.agents.studio_schemas import AccessCheckRequest
    from app.modules.intelligence.metadata_repository import IntelligenceMetadataRepository
    from app.modules.intelligence.semantic_views import semantic_view_service

    user = session("agent-owner", active="ANALYST")
    actor = SecurityContext.from_session(user)
    definition = parse_ossie("""
version: 0.1.1
name: sales
datasets:
  - name: orders
    source: sales.orders
    primary_key: [id]
    fields:
      - name: id
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: id
""").as_dict()
    fingerprint = SemanticModelIR.from_ossie(definition).fingerprint
    async with agents.transaction(actor=actor) as (_, conn):
        views = IntelligenceMetadataRepository(conn, actor)
        view = await views.create_view(
            catalog="default_catalog", database="sales", schema="sales", name="orders",
            view_id=str(uuid4()), definition=definition, fingerprint=fingerprint,
        )
        await views.record_validation(view["id"], 1,
                                      {"valid": True, "errors": [], "baseline_version": None})
        await views.publish(view["id"], 1, expected_revision=1, expected_active=None)

    # This case exercises PG metadata; governed source execution has its own engine gate.
    source_access = AsyncMock(return_value=True)
    monkeypatch.setattr(semantic_view_service, "_source_access", source_access)
    monkeypatch.setattr(access_control_service, "effective_access", AsyncMock(
        return_value={"object_access": ["SELECT", "USAGE"]},
    ))
    monkeypatch.setattr(studio_router, "write_audit_log", AsyncMock())
    with acting_as(actor):
        agent = await runtime_registry.create_agent(
            owner_name=actor.principal,
            fields={"name": "Sales", "database_name": "sales", "semantic_view_ids": [view["id"]]},
        )
        await runtime_registry.add_agent_role(
            agent["agent_id"], owner_name=actor.principal, role_name="ANALYST", grant_type="USAGE",
        )
        checked = await studio_router.verify_agent_access(
            agent["agent_id"], AccessCheckRequest(role_name="ANALYST"), user,
        )
        assert checked.all_granted and any(item.kind == "semantic_view" for item in checked.items)
        observed = SecurityContext.from_session(source_access.await_args.args[1])
        assert observed == actor
        assert await access.has_verified_access(agent, role_name="ANALYST", user=user)
        source_access.reset_mock()
        refused = await access.verify_access(
            agent=agent, role_name="ANALYST", username=actor.principal,
            encrypted_password="sealed", session_id=actor.session_id,
            security_context_version=actor.security_context_version, assigned_roles=("VIEWER",),
        )
        assert not next(item.granted for item in refused if item.kind == "semantic_view")
        source_access.assert_not_awaited()


async def test_memory_facade_deduplicates_deletes_and_restores_retained_history(
    agents,
    runtime_registry,
):
    from app.core.metadata_actor import acting_as
    from app.modules.agents.memory import AgentMemoryRepository

    repo = AgentMemoryRepository()
    actor = SecurityContext.from_session(session("agent-owner", active="ANALYST"))
    fields = dict(
        user_name=actor.principal,
        agent_id="finance",
        role_name=actor.active_role,
        fact_key="fiscal",
        fact="The fiscal year starts in April.",
        source_quote="The fiscal year starts in April.",
        source_thread_id="thread",
        source_message_id="message",
        existing_id=None,
    )
    with acting_as(actor):
        ids = await asyncio.gather(repo.upsert(**fields), repo.upsert(**fields))
        assert ids[0] == ids[1]
        rows = await repo.list(user_name=actor.principal, agent_id="finance", role_name="ANALYST")
        assert rows[0]["knowledge"]["revision"] == 1
        assert "provenance" not in rows[0]
        assert await repo.delete(
            ids[0], user_name=actor.principal, agent_id="finance", role_name="ANALYST"
        )
        assert (
            await repo.list(user_name=actor.principal, agent_id="finance", role_name="ANALYST")
            == []
        )
        assert await repo.upsert(**fields) == ids[0]
        rows = await repo.list(user_name=actor.principal, agent_id="finance", role_name="ANALYST")
        assert rows[0]["knowledge"]["revision"] == 3
        assert rows[0]["knowledge"]["state"] == "HYPOTHESIS"
        assert rows[0]["knowledge"]["visibility"] == "PRIVATE"
        history = await repo.revisions(
            ids[0], user_name=actor.principal, agent_id="finance", role_name="ANALYST"
        )
        assert [r.revision for r in history] == [3, 2, 1]
        assert history[1].state.value == "SUPERSEDED"
        evidence = await repo.evidence(
            ids[0],
            user_name=actor.principal,
            agent_id="finance",
            role_name="ANALYST",
            allowed=history[0].evidence_ids,
        )
        assert len(evidence) == 1
    with pytest.raises(MetadataPermissionDenied):
        async with agents.transaction(name="foreign-owner") as (store, _):
            await store.append_memory_revision(ids[0], "finance", 4, "a" * 32, '{"fact":"foreign"}')
    with acting_as(SecurityContext.from_session(session("reader", active="ANALYST"))):
        assert (
            await repo.list(user_name="agent-owner", agent_id="finance", role_name="ANALYST") == []
        )


@pytest.mark.parametrize("boundary", ["_write_projection", "_write_evidence"])
async def test_memory_facade_rolls_back_the_complete_change(
    agents, runtime_registry, monkeypatch, boundary
):
    from app.core.metadata_actor import acting_as
    from app.modules.agents.memory import AgentMemoryRepository

    repo = AgentMemoryRepository()
    original = getattr(repo, boundary)

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("interrupted memory write")

    monkeypatch.setattr(repo, boundary, fail)
    fields = dict(
        user_name="agent-owner",
        agent_id="finance",
        role_name="ANALYST",
        fact_key="fiscal",
        fact="The fiscal year starts in April.",
        source_quote="The fiscal year starts in April.",
        source_thread_id="thread",
        source_message_id="message",
        existing_id=None,
    )
    with acting_as(SecurityContext.from_session(session("agent-owner", active="ANALYST"))):
        with pytest.raises(RuntimeError, match="interrupted memory write"):
            await repo.upsert(**fields)
        for table in (
            "config_agent_memories",
            "config_agent_memory_revisions",
            "config_agent_memory_evidence",
        ):
            assert (await agents.admin(f"SELECT count(*) AS n FROM nova_meta.{table}"))[0]["n"] == 0
        monkeypatch.setattr(repo, boundary, original)
        await repo.upsert(**fields)
        assert (
            len(await repo.list(user_name="agent-owner", agent_id="finance", role_name="ANALYST"))
            == 1
        )


async def test_shared_memory_projection_requires_visible_agents_and_current_role(agents):
    agent = await create_agent(agents, visibility="shared", semantic_view_ids=["sales"])
    payload = {
        "memory_id": "m",
        "revision": 1,
        "fact": "Revenue excludes refunds.",
        "state": "VERIFIED",
        "visibility": "DOMAIN",
        "review_note": "private-review",
        "evidence_ids": ["private-evidence"],
        "semantic": {"view_id": "sales"},
        "definition": {"metric_name": "revenue"},
    }
    async with agents.transaction() as (repo, _):
        await repo.replace_agent_roles(agent["agent_id"], {"VIEWER": "USAGE"})
        memory = await repo.create_memory(
            agent["agent_id"],
            {
                "fact_key": "revenue",
                "fact": payload["fact"],
                "source_quote": "private-source",
                "source_thread_id": "private-thread",
            },
            payload,
        )
    async with agents.transaction(name="reader", active="VIEWER", read_only=True) as (repo, _):
        rows = await repo.shared_memories(agent["agent_id"])
        assert len(rows) == 1 and rows[0]["memory_id"] == memory["memory_id"]
        assert "private" not in str(rows)
        assert (
            await repo.get_memory(memory["memory_id"], "agent-owner", agent["agent_id"], "ANALYST")
            is None
        )
    async with agents.transaction(name="reader", active="ANALYST", read_only=True) as (repo, _):
        assert await repo.shared_memories(agent["agent_id"]) == []
    async with agents.transaction() as (repo, _):
        await repo.replace_agent_roles(agent["agent_id"], {})
    async with agents.transaction(name="reader", active="VIEWER", read_only=True) as (repo, _):
        assert await repo.shared_memories(agent["agent_id"]) == []


async def test_mission_facade_cursor_cas_preserves_public_revision_and_rolls_back_audit(
    agents, runtime_registry, monkeypatch,
):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.core.metadata_actor import acting_as
    from app.modules.agents import mission
    from app.modules.agents.mission_schema import MissionCreate

    @asynccontextmanager
    async def admission(*_args):
        yield AsyncMock()

    monkeypatch.setattr(mission.harness_repository, "admission_lock", admission)
    monkeypatch.setattr(mission, "require_thread", AsyncMock(return_value={"agent_id": "finance"}))
    user = session("agent-owner", active="ANALYST")
    service = mission.MissionService()
    body = MissionCreate(objective="Inspect revenue", operation_id="create-mission")
    with acting_as(SecurityContext.from_session(user)):
        created = await service.create("thread", body, user)
        again = await service.create("thread", body, user)
        assert created == again
        stale = created.model_copy(deep=True)
        created.projection_cursor["run"] = 0
        saved = await service._save(created, created.revision, AsyncMock(), advance_revision=False)
        assert saved.revision == created.revision and saved.updated_at == created.updated_at
        assert saved._storage_revision == created._storage_revision + 1
        with pytest.raises(HTTPException) as error:
            await service._save(stale, stale.revision, AsyncMock(), advance_revision=False)
        assert error.value.status_code == 409
        assert (await service.get(created.mission_id, user, project=False)) == saved
        monkeypatch.setattr(service, "audit", AsyncMock(side_effect=RuntimeError("audit failed")))
        with pytest.raises(RuntimeError, match="audit failed"):
            await service.create("another-thread", body, user)
    rows = await agents.admin("SELECT count(*) AS n FROM nova_meta.config_studio_missions")
    assert rows[0]["n"] == 1


async def test_runtime_registry_crud_versions_and_long_skills_use_the_real_uow(runtime_registry):
    from app.core.metadata_actor import acting_as
    from app.modules.agents.versions import agent_versions

    repo = runtime_registry
    actor = SecurityContext.from_session(session("agent-owner", active="ANALYST"))
    with acting_as(actor):
        created = await repo.create_agent(owner_name=actor.principal, fields={"name": "Analyst"})
        assert created["semantic_view_ids"] == [] and created["policy"] == "auto_read_only"
        assert created["resource_bindings"] == {} and created["config_revision"]
        assert len(await repo.list_agents(owner_name=actor.principal, search="analyst")) == 1
        updated = await repo.update_agent(
            created["agent_id"], owner_name=actor.principal, fields={"description": "Updated"},
            expected_revision=created["config_revision"], check_revision=True)
        assert updated["description"] == "Updated"
        assert updated["config_revision"] != created["config_revision"]
        versions = await agent_versions.list(created["agent_id"], actor.principal, 0)
        assert len(versions["versions"]) == 2 and not versions["has_more"]
        saved = await agent_versions.get(created["agent_id"], actor.principal,
                                         updated["config_revision"])
        assert saved["configuration"]["description"] == "Updated"
        with pytest.raises(Exception) as stale:
            await repo.update_agent(
                created["agent_id"],
                owner_name=actor.principal,
                fields={"name": "stale"},
                expected_revision=created["config_revision"],
                check_revision=True,
            )
        assert getattr(stale.value, "status_code", None) == 409
        assert (
            len((await agent_versions.list(created["agent_id"], actor.principal, 0))["versions"])
            == 2
        )

        body = "A governed recipe. " * 4000
        skill = await repo.create_skill(owner_name=actor.principal,
                                        fields={"name": "recipe", "body": body})
        assert skill["body"] == body
        revised = await repo.update_skill(
            skill["skill_id"],
            owner_name=actor.principal,
            fields={"name": "recipe", "body": "Revised", "description": ""},
        )
        assert revised["body"] == "Revised"
        assert (await repo.get_skill(skill["skill_id"], owner_name=actor.principal))[
            "body"
        ] == "Revised"
        assert await repo.delete_skill(skill["skill_id"], owner_name=actor.principal)
        assert await repo.list_skills(owner_name=actor.principal) == []

        custom = await repo.create_custom_tool(owner_name=actor.principal,
                                               fields={"name": "margin", "kind": "function"})
        custom = await repo.update_custom_tool(custom["tool_id"], owner_name=actor.principal,
                                               fields={"description": "Measure margin"})
        assert custom["description"] == "Measure margin"
        assert await repo.delete_custom_tool(custom["tool_id"], owner_name=actor.principal)
        assert await repo.delete_agent(created["agent_id"], owner_name=actor.principal)
        assert await repo.get_agent(created["agent_id"], owner_name=actor.principal) is None


async def test_platform_mcp_keeps_admin_actor_and_refuses_nonadmin_writes(runtime_registry):
    from app.core.metadata_actor import acting_as

    repo = runtime_registry
    admin = SecurityContext.from_session(session("platform-admin", active="ACCOUNTADMIN"))
    reader = SecurityContext.from_session(session("colleague", active="ANALYST"))
    with acting_as(admin):
        server = await repo.create_mcp_server(owner_name="__nova__", fields={
            "name": "Synthetic arithmetic", "endpoint": "https://mcp.example.test/tools"})
        tool = await repo.upsert_tool(owner_name="__nova__", fields={
            "name": "total", "source": "mcp:" + server["server_id"]})
        again = await repo.upsert_tool(owner_name="__nova__", fields={
            "name": "total", "source": "mcp:" + server["server_id"], "description": "Sum"})
        assert tool["tool_id"] == again["tool_id"]
        changed = await repo.update_mcp_server(server["server_id"], owner_name="__nova__",
                                              fields={"last_status": "connected"})
        assert changed["last_status"] == "connected"
    with acting_as(reader):
        assert [row["server_id"] for row in await repo.list_mcp_servers(owner_name="__nova__")] == [
            server["server_id"]
        ]
        assert [row["tool_id"] for row in await repo.list_tools(owner_name="__nova__")] == [
            tool["tool_id"]
        ]
        with pytest.raises(MetadataPermissionDenied):
            await repo.create_mcp_server(owner_name="__nova__", fields={
                "name": "Forbidden", "endpoint": "https://mcp.example.test/forbidden"})
        assert (
            await repo.set_tool_enabled(tool["tool_id"], owner_name="__nova__", enabled=False)
            is None
        )
        assert not await repo.delete_mcp_server(server["server_id"], owner_name="__nova__")
    with acting_as(admin):
        assert await repo.delete_mcp_server(server["server_id"], owner_name="__nova__")
        assert await repo.list_tools(owner_name="__nova__") == []


async def test_private_agent_hides_retained_role_grants_and_shared_resources(
    agents, runtime_registry
):
    from app.core.metadata_actor import acting_as

    agent = await create_agent(agents, visibility="private", default_tools=["custom:margin"])
    async with agents.transaction() as (store, _):
        await store.create_custom_tool({"name": "margin", "kind": "function"})
        await store.create_skill({"name": "forecast", "body": "Use seasonality."})
        await store.replace_agent_roles(agent["agent_id"], {"VIEWER": "USAGE"})
    reader = SecurityContext.from_session(session("colleague", active="VIEWER"))
    with acting_as(reader):
        assert await runtime_registry.get_agent(agent["agent_id"], owner_name="agent-owner") is None
        assert (
            await runtime_registry.get_shared_agent(agent["agent_id"], role_name="VIEWER") is None
        )
        assert await runtime_registry.list_skills(owner_name="agent-owner") == []
        assert await runtime_registry.list_custom_tools(owner_name="agent-owner") == []
    async with agents.transaction() as (store, _):
        await store.update_agent(agent["agent_id"], {"visibility": "shared"},
                                 expected_config_revision="rev-0")
    with acting_as(reader):
        assert (await runtime_registry.get_shared_agent(agent["agent_id"], role_name="VIEWER"))[
            "agent_id"
        ] == agent["agent_id"]
        assert [
            row["name"]
            for row in await runtime_registry.list_custom_tools(owner_name="agent-owner")
        ] == ["margin"]
        assert [
            row["name"] for row in await runtime_registry.list_skills(owner_name="agent-owner")
        ] == ["forecast"]
        with pytest.raises(MetadataPermissionDenied):
            await runtime_registry.get_shared_agent(agent["agent_id"], role_name="ANALYST")


async def test_facade_racing_edits_rollback_losing_snapshots(agents, runtime_registry, monkeypatch):
    from app.core.metadata_actor import acting_as
    from app.modules.agents.versions import agent_versions

    actor = SecurityContext.from_session(session("agent-owner", active="ANALYST"))
    with acting_as(actor):
        agent = await runtime_registry.create_agent(owner_name=actor.principal,
                                                     fields={"name": "Concurrent editors"})
    ready = asyncio.Barrier(2)
    original = AgentMetadataRepository.get_agent

    async def read_then_wait(store, agent_id):
        row = await original(store, agent_id)
        await ready.wait()
        return row

    monkeypatch.setattr(AgentMetadataRepository, "get_agent", read_then_wait)

    async def edit(name):
        with acting_as(actor):
            try:
                await runtime_registry.update_agent(
                    agent["agent_id"], owner_name=actor.principal, fields={"name": name},
                    expected_revision=agent["config_revision"], check_revision=True)
                return True
            except MetadataConflict:
                return False

    assert sorted(await asyncio.gather(edit("Editor one"), edit("Editor two"))) == [False, True]
    monkeypatch.setattr(AgentMetadataRepository, "get_agent", original)
    with acting_as(actor):
        current = await runtime_registry.get_agent(agent["agent_id"], owner_name=actor.principal)
        history = await agent_versions.list(agent["agent_id"], actor.principal, 0)
        assert {row["version_id"] for row in history["versions"]} == {
            agent["config_revision"], current["config_revision"],
        }


async def test_release_capture_commits_once_and_loads_via_runtime_facade(
    runtime_registry, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.core.metadata_actor import acting_as
    from app.modules.agents import releases
    from app.modules.agents.service import agent_service
    from app.modules.agents.versions import agent_versions
    from app.modules.assistant.provider import assistant_provider
    from app.modules.assistant.tools import ToolRegistry

    registry = ToolRegistry()
    registry.skill_definitions = {}
    registry.default_skills = []
    registry.discoverable_skills = []
    monkeypatch.setattr(
        agent_service,
        "build_loop_inputs",
        AsyncMock(return_value=(registry, "Synthetic prompt", 60, None)),
    )
    monkeypatch.setattr(
        assistant_provider,
        "resolve",
        AsyncMock(return_value=SimpleNamespace(provider_id="fixture", model="controlled")),
    )
    monkeypatch.setattr(
        releases,
        "model_contract",
        AsyncMock(return_value={"provider_id": "fixture", "model": "controlled"}),
    )
    monkeypatch.setattr(releases, "ontology_release_pin", AsyncMock(return_value=None))
    monkeypatch.setattr(releases, "resource_contracts", AsyncMock(return_value=[]))
    user = session("agent-owner", active="ANALYST")
    with acting_as(SecurityContext.from_session(user)):
        agent = await runtime_registry.create_agent(owner_name=user["username"], fields={
            "name": "Release test", "instructions_response": "Use known totals."})
        saved = await agent_versions.store(agent, label="Baseline")
        first, second = await asyncio.gather(*(
            releases.capture_manifest(agent, saved["version_id"], user) for _ in range(2)))
        assert first == second
        assert await releases.get_manifest(agent["agent_id"], user["username"],
                                           version_id=saved["version_id"]) == first
        other = await agent_versions.store(agent, label="Another version")
        with pytest.raises(HTTPException) as mismatch:
            await releases.capture_manifest(
                {**agent, "name": "Different"}, other["version_id"], user
            )
        assert mismatch.value.status_code == 409
        assert await releases.get_manifest(agent["agent_id"], user["username"],
                                           version_id=other["version_id"]) is None


async def test_research_facade_serializes_updates_and_preserves_owner_scope(runtime_registry):
    from app.core.metadata_actor import acting_as
    from app.modules.agents.deep_research import deep_research_repository as repo

    actor = SecurityContext.from_session(session("agent-owner", active="ANALYST"))
    with acting_as(actor):
        run_id = await repo.create(agent_id="a1", owner_name=actor.principal,
                                   thread_id="t1", question="Why did sales change?")
        created = await repo.get(run_id, owner_name=actor.principal)
        assert created["status"] == "planning" and created["progress"] == []
        assert "metadata_revision" not in created and "provenance" not in created
        await asyncio.gather(
            repo.update(run_id, status="running", plan=["Compare periods"]),
            repo.update(run_id, progress=[{"status": "running"}]),
        )
        current = await repo.get(run_id, owner_name=actor.principal)
        assert current["status"] == "running" and current["plan"] == ["Compare periods"]
        assert current["progress"] == [{"status": "running"}]
        with pytest.raises(ValueError, match="Unsupported research changes"):
            await repo.update(run_id, owner_name="other")
        with pytest.raises(MetadataPermissionDenied):
            await repo.get(run_id, owner_name="other")
        await repo.update(run_id, status="done", report="Verified totals.")
        with pytest.raises(MetadataIntegrityViolation):
            await repo.update(run_id, status="running")
    with acting_as(SecurityContext.from_session(session("other", active="ANALYST"))):
        assert await repo.get(run_id, owner_name="other") is None
        with pytest.raises(MetadataConflict):
            await repo.update(run_id, report="Overwritten")


async def test_share_facade_serializes_duplicate_and_quota_and_follows_active_role(
    runtime_registry, monkeypatch,
):
    from app.core.metadata_actor import acting_as
    from app.modules.agents import sharing

    monkeypatch.setattr(sharing, "MAX_SHARES_PER_OBJECT", 2)
    repo = sharing.share_repository
    owner = session("agent-owner", active="ANALYST")
    target = sharing.ShareTarget(object_type="thread", object_id="shared-thread",
                                 target_type="role", target_name="VIEWER")
    with acting_as(SecurityContext.from_session(owner)):
        first, second = await asyncio.gather(*(
            repo.create(owner_name="agent-owner", body=target) for _ in range(2)))
        assert first == second
        assert "provenance" not in first
        results = await asyncio.gather(*(
            repo.create(owner_name="agent-owner", body=target.model_copy(update={
                "target_type": "user", "target_name": name}))
            for name in ("bob", "charlie")
        ), return_exceptions=True)
        assert sum(isinstance(result, dict) for result in results) == 1
        assert sum(isinstance(result, ValueError) for result in results) == 1
        assert len(await repo.for_object("thread", "shared-thread", owner_name="agent-owner")) == 2
    viewer = session("reader", active="VIEWER", roles=["VIEWER", "ANALYST"])
    with acting_as(SecurityContext.from_session(viewer)):
        assert (await repo.visible(viewer))[0]["share_id"] == first["share_id"]
        assert await repo.grant_for(viewer, "thread", "shared-thread") == first
        with pytest.raises(MetadataPermissionDenied):
            await repo.visible({**viewer, "session_id": "other-login"})
        await repo.delete(first["share_id"], owner_name="reader")
        assert await repo.grant_for(viewer, "thread", "shared-thread") == first
    other_role = {**viewer, "active_role": "ANALYST"}
    with acting_as(SecurityContext.from_session(other_role)):
        assert await repo.visible(other_role) == []
    with acting_as(SecurityContext.from_session(owner)):
        await repo.delete(first["share_id"], owner_name="agent-owner")
    with acting_as(SecurityContext.from_session(viewer)):
        assert await repo.grant_for(viewer, "thread", "shared-thread") is None


async def test_verified_candidate_facade_deduplicates_and_only_owner_decides(runtime_registry):
    from app.core.metadata_actor import acting_as
    from app.modules.agents.verified_candidates import verified_candidate_repository as repo

    async def propose():
        return await repo.propose(agent_id="a1", owner_name="agent-owner", proposed_by="reader",
                                  thread_id="t1", message_id="m1", candidate={
                                      "view_id": "v1", "question": "What is revenue?",
                                      "semantic_plan": {"metrics": ["revenue"]}})

    with acting_as(SecurityContext.from_session(session("reader", active="VIEWER"))):
        ids = await asyncio.gather(propose(), propose())
        assert sum(item is None for item in ids) == 1
        candidate_id = next(item for item in ids if item)
        with pytest.raises(MetadataPermissionDenied):
            await repo.get(candidate_id, owner_name="agent-owner")
        with pytest.raises(MetadataConflict):
            await repo.decide(candidate_id, status="approved", decided_by="reader")
    with acting_as(SecurityContext.from_session(session("agent-owner", active="ANALYST"))):
        rows = await repo.list(agent_id="a1", owner_name="agent-owner", status="pending")
        assert len(rows) == 1 and rows[0]["proposed_by"] == "reader"
        assert "metadata_revision" not in rows[0]
        assert rows[0]["semantic_plan"] == {"metrics": ["revenue"]}
        await repo.decide(candidate_id, status="approved", decided_by="agent-owner")
        with pytest.raises(MetadataConflict):
            await repo.decide(candidate_id, status="rejected", decided_by="agent-owner")
        assert (await repo.get(candidate_id, owner_name="agent-owner"))[
            "decided_by"
        ] == "agent-owner"


async def test_shared_conversation_projection_keeps_viewer_identity_and_private_inputs(
    agents, runtime_registry,
):
    from app.core.metadata_actor import acting_as
    from app.modules.agents import sharing
    from app.modules.assistant.repository import assistant_repository as conversations

    owner = session("agent-owner", active="ANALYST")
    viewer = session("reader", active="VIEWER", roles=["VIEWER", "ANALYST"])
    repo = sharing.share_repository
    with acting_as(SecurityContext.from_session(owner)):
        thread = await conversations.create_thread(user_name="agent-owner", title="Shared totals")
        identifier = thread["thread_id"]
        await conversations.append_message(
            identifier,
            user_name="agent-owner",
            role="assistant",
            content="Verified report.",
            instructions="private-instruction-canary",
            attachments=[{"content": "private-file-canary"}],
            steps=[
                {
                    "kind": "tool",
                    "name": "semantic_query",
                    "tool_call_id": "c1",
                    "trace_detail": {
                        "question": "Revenue",
                        "rows": [["private-row-canary"]],
                        "semantic_plan": {
                            "metrics": ["revenue"],
                            "result": "private-nested-canary",
                        },
                        "semantic_view": {"id": "v1"},
                    },
                },
                {"kind": "ui", "program": "private-program-canary"},
            ],
        )
        await conversations.append_message(
            identifier, user_name="agent-owner", role="user", content="private-input-canary",
            steps=[{"kind": "ui_action", "state": {"data": "private-state-canary"}}],
        )
        await conversations.append_message(identifier, user_name="agent-owner", role="assistant",
                                             content="private-followup-canary")
        share = await repo.create(owner_name="agent-owner", body=sharing.ShareTarget(
            object_type="thread", object_id=identifier, target_type="role", target_name="VIEWER"))
    with acting_as(SecurityContext.from_session(viewer)):
        result = await repo.thread(viewer, identifier, owner_name="agent-owner")
        assert result["title"] == "Shared totals" and len(result["messages"]) == 3
        assert result["messages"][0]["content"] == "Verified report."
        assert result["messages"][0]["steps"][0]["trace_detail"]["semantic_plan"] == {
            "metrics": ["revenue"]}
        assert all(item["steps"] == [] for item in result["messages"][1:])
        assert "private-" not in json.dumps(result)
        with pytest.raises(MetadataPermissionDenied):
            await conversations.list_messages(identifier, user_name="agent-owner")
    async with agents.transaction(name="reader", active="VIEWER") as (store, conn):
        cursor = await conn.execute(
            "SELECT content FROM nova_meta.config_assistant_messages WHERE thread_id=%s",
            (identifier,))
        assert await cursor.fetchall() == []
        assert await store.shared_conversation(identifier, "wrong-owner") is None
    async with agents.transaction(workload="agent", name="reader", active="VIEWER") as (store, _):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await store.shared_conversation(identifier, "agent-owner")
    inactive = {**viewer, "active_role": "ANALYST"}
    with acting_as(SecurityContext.from_session(inactive)):
        assert await repo.thread(inactive, identifier, owner_name="agent-owner") is None
    with acting_as(SecurityContext.from_session(owner)):
        await repo.delete(share["share_id"], owner_name="agent-owner")
    with acting_as(SecurityContext.from_session(viewer)):
        assert await repo.thread(viewer, identifier, owner_name="agent-owner") is None


async def test_quality_facade_pins_inputs_and_rejects_concurrent_or_stale_identity(
    runtime_registry,
):
    from fastapi import HTTPException

    from app.core.metadata_actor import acting_as
    from app.modules.agents import quality

    user = session("agent-owner", active="ANALYST")
    with acting_as(SecurityContext.from_session(user)):
        record = await quality.save_record("cases", {"id": "case-facade", "agent_id": "a1",
                                                     "prompt": "Revenue?"}, user)
        results = await asyncio.gather(*(
            quality.save_record("cases", {**record, "prompt": value}, user, 1)
            for value in ("Revenue this month?", "Revenue last month?")
        ), return_exceptions=True)
        assert sum(isinstance(item, dict) for item in results) == 1
        assert sum(isinstance(item, (MetadataConflict, HTTPException)) for item in results) == 1
        current = (await quality.records("cases", "a1", user))[0]
        assert current["revision"] == 2
        run = await quality.save_record("runs", {"id": "run-facade", "agent_id": "a1",
                                                "status": "running", "cases": [current]}, user)
        with pytest.raises(HTTPException, match="frozen"):
            await quality.save_record("runs", {**run, "cases": []}, user, 1)
        await quality.save_record("runs", {**run, "status": "passed"}, user, 1)
        with pytest.raises(HTTPException, match="immutable"):
            await quality.save_record("runs", {**run, "status": "failed"}, user, 2)
        with pytest.raises(MetadataPermissionDenied):
            await quality.records("cases", "a1", {**user, "session_id": "other-login"})
    changed = {**user, "security_context_version": 6}
    with acting_as(SecurityContext.from_session(changed)):
        assert await quality.records("cases", "a1", changed) == []
        with pytest.raises(MetadataConflict):
            await quality.save_record("cases", {"id": "case-facade", "agent_id": "a1"}, changed)


async def test_quality_trace_facade_reads_only_current_role_version_and_limit(runtime_registry):
    from app.core.metadata_actor import acting_as
    from app.modules.assistant.repository import assistant_repository as repo
    from app.modules.assistant.security import observation_context

    user = session("agent-owner", active="ANALYST")
    for principal, role, version, marker in [
        ("agent-owner", "ANALYST", 5, "current-first"),
        ("agent-owner", "ANALYST", 5, "current-last"),
        ("agent-owner", "VIEWER", 5, "other-role"),
        ("agent-owner", "ANALYST", 4, "stale-version"),
        ("other", "ANALYST", 5, "other-user"),
    ]:
        actor = SecurityContext.from_session(session(principal, active=role,
                                                      security_context_version=version))
        with acting_as(actor):
            thread = await repo.create_thread(user_name=principal, agent_id="a1")
            await repo.append_message(thread["thread_id"], user_name=principal, role="assistant",
                                       content="Trace", agent_id="a1", message_id=marker,
                                       steps=[{"kind": "quality_observation", "marker": marker}],
                                       security_context=observation_context(actor))
    with acting_as(SecurityContext.from_session(user)):
        rows = await repo.quality_traces("a1", user, limit=1)
        assert [row["message_id"] for row in rows] == ["current-last"]
        assert len(await repo.quality_traces("a1", user, limit=100)) == 2
        with pytest.raises(ValueError):
            await repo.quality_traces("a1", user, limit=101)
        with pytest.raises(MetadataPermissionDenied):
            await repo.quality_traces("a1", {**user, "session_id": "other-login"}, limit=1)


async def test_usage_and_session_trace_facades_keep_owner_agent_and_time_scope(
    agents, runtime_registry,
):
    from app.core.metadata_actor import acting_as
    from app.modules.agents import observability
    from app.modules.assistant.repository import assistant_repository as repo

    actor = SecurityContext.from_session(session("agent-owner", active="ANALYST"))
    for principal, agent, identifier, tokens in [
        ("agent-owner", "a1", "included", 12),
        ("agent-owner", "a1", "old", 100),
        ("agent-owner", "a2", "different-agent", 200),
        ("foreign", "a1", "foreign", 400),
    ]:
        with acting_as(SecurityContext.from_session(session(principal, active="ANALYST"))):
            thread = await repo.create_thread(user_name=principal, agent_id=agent)
            await repo.append_message(
                thread["thread_id"],
                user_name=principal,
                agent_id=agent,
                message_id=identifier,
                role="user",
                content="Question " + identifier,
                usage={"total_tokens": tokens},
                steps=[{"kind": "test"}],
            )
            if identifier == "included":
                included = thread["thread_id"]
    # Import can preserve historic timestamps; runtime messages remain immutable.
    await agents.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    try:
        async with agents.importer() as conn:
            await conn.execute(
                "UPDATE nova_meta.config_assistant_messages SET created_at=%s "
                "WHERE message_id='old'",
                (datetime.now(UTC) - timedelta(days=3),),
            )
    finally:
        await agents.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    with acting_as(actor):
        usage = await observability.usage_summary(owner_name=actor.principal, agent_id="a1", days=1)
        assert usage == {
            "total_sessions": 1,
            "total_tokens": 12,
            "total_active_users": 1,
            "series": [{"date": datetime.now(UTC).date().isoformat(), "sessions": 1, "tokens": 12}],
        }
        sessions = await observability.list_sessions(owner_name=actor.principal, agent_id="a1")
        assert len(sessions) == 2
        current = next(row for row in sessions if row["thread_id"] == included)
        assert current["message_count"] == 1 and current["total_tokens"] == 12
        assert current["first_input"] == "Question included"
        trace = await observability.thread_trace(
            owner_name=actor.principal,
            agent_id="a1",
            thread_id=included,
        )
        assert trace["total_tokens"] == 12 and trace["turns"][0]["steps"] == [{"kind": "test"}]
        assert "security_context" not in trace["turns"][0]
        assert (
            await observability.thread_trace(
                owner_name=actor.principal,
                agent_id="a2",
                thread_id=included,
            )
            is None
        )
        with pytest.raises(MetadataPermissionDenied):
            await observability.usage_summary(owner_name="foreign")
        with pytest.raises(ValueError):
            await observability.list_sessions(owner_name=actor.principal, limit=501)
    with acting_as(SecurityContext.from_session(session("foreign", active="ANALYST"))):
        assert (
            await observability.thread_trace(
                owner_name="foreign",
                agent_id="a1",
                thread_id=included,
            )
            is None
        )


async def test_artifact_facade_cas_and_shared_membership_preserve_the_viewer(
    agents, runtime_registry
):
    from psycopg.types.json import Jsonb

    from app.core.metadata_actor import acting_as
    from app.modules.agents.artifact_repository import artifact_repository as repo
    from app.modules.agents.sharing import ShareTarget, share_repository

    owner = SecurityContext.from_session(session("agent-owner", active="ANALYST"))
    viewer = SecurityContext.from_session(session("reader", active="VIEWER"))
    worksheet = {
        "version": 1,
        "source": {"kind": "sql", "sql": "SELECT region, revenue FROM sales"},
        "rows": [{"field": "revenue", "aggregation": "sum", "field_type": "quantitative"}],
        "columns": [{"field": "region"}],
        "filters": [{"kind": "categorical", "field": "region", "values": ["West"]}],
    }
    dashboard = {
        "version": 1,
        "title": "Shared",
        "zones": [
            {
                "zone_id": "z1",
                "kind": "sheet",
                "artifact_id": "chart",
                "rect": {"x": 0, "y": 0, "w": 12, "h": 2},
            },
        ],
    }
    await agents.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    try:
        async with agents.importer() as conn:
            for identifier, kind, spec, title in [
                ("chart", "chart", worksheet, "Revenue"),
                ("hidden", "chart", worksheet, "private-unshared-canary"),
                ("board", "dashboard", dashboard, "Shared"),
            ]:
                await conn.execute(
                    "INSERT INTO nova_meta.config_studio_artifacts "
                    "(artifact_id,owner_name,title,artifact_type,sql_text,viz_spec,"
                    "created_at,updated_at) "
                    "VALUES (%s,'agent-owner',%s,%s,'SELECT 1',%s,now(),now())",
                    (identifier, title, kind, Jsonb(spec)),
                )
    finally:
        await agents.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    with acting_as(owner):
        created = await repo.create_bi(
            owner_name=owner.principal,
            title="BI chart",
            artifact_type="chart",
            sql_text="SELECT 1",
            database_name=None,
            schema_name=None,
            bi_kind="chart",
            bi_object_id=4,
            bi_dataset_id=7,
        )
        edits = await asyncio.gather(
            *[
                repo.update_bi_source(
                    created["artifact_id"],
                    owner_name=owner.principal,
                    expected_updated_at=created["updated_at"],
                    sql_text=f"SELECT {value}",
                )
                for value in (2, 3)
            ]
        )
        assert sum(row is not None for row in edits) == 1
        share = await share_repository.create(
            owner_name=owner.principal,
            body=ShareTarget(
                object_type="dashboard",
                object_id="board",
                target_type="role",
                target_name="VIEWER",
            ),
        )
    with acting_as(viewer):
        projection = await repo.shared_dashboard("board", owner_name=owner.principal)
        assert [row["artifact_id"] for row in projection["charts"]] == ["chart"]
        assert projection["charts"][0]["viz_spec"]["rows"][0]["field"] == "revenue"
        assert projection["charts"][0]["viz_spec"]["filters"][0]["values"] == ["West"]
        assert "private-unshared-canary" not in json.dumps(projection)
        assert await repo.shared_dashboard("hidden", owner_name=owner.principal) is None
        with pytest.raises(MetadataPermissionDenied):
            await repo.get("hidden", owner_name=owner.principal)
    async with agents.transaction(name="reader", active="VIEWER") as (_, conn):
        cursor = await conn.execute("SELECT * FROM nova_meta.config_studio_artifacts")
        assert await cursor.fetchall() == []
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT * FROM nova_meta.legacy_artifact_owners()")
    with acting_as(SecurityContext.from_session(session("reader", active="ANALYST"))):
        assert await repo.shared_dashboard("board", owner_name=owner.principal) is None
    with acting_as(owner):
        await share_repository.delete(share["share_id"], owner_name=owner.principal)
    with acting_as(viewer):
        assert await repo.shared_dashboard("board", owner_name=owner.principal) is None
    with acting_as(owner):
        converted = await repo.attach_bi(
            "chart", owner_name=owner.principal, bi_kind="chart", bi_object_id=9, bi_dataset_id=7
        )
        assert converted["artifact_id"] == "chart" and converted["viz_spec"] is None
        assert (
            await repo.attach_bi(
                "chart",
                owner_name=owner.principal,
                bi_kind="chart",
                bi_object_id=10,
                bi_dataset_id=7,
            )
            is None
        )
    async with agents.transaction(
        workload="maintenance",
        actor=SecurityContext(
            "nova-artifact-converter",
            "NOVA_WORKER",
            principal_type="SERVICE",
        ),
    ) as (_, conn):
        cursor = await conn.execute("SELECT * FROM nova_meta.legacy_artifact_owners()")
        assert await cursor.fetchall() == [{"owner_name": "agent-owner"}]


@asynccontextmanager
async def automation_runtime(agents):
    from app.core.metadata_runtime import install_metadata_db, uninstall_metadata_db

    database = MetadataDatabase(agents.environment.role_config("nova_meta_automation"))
    try:
        await database.open(schema_version=len(load_migrations()), metadata_epoch=agents.epoch)
        install_metadata_db(database)
        yield
    finally:
        uninstall_metadata_db(database)
        await database.close()


async def due_automation(agents, **fields):
    from app.modules.agents.automations import _row

    async with agents.transaction() as (repo, _):
        row = await repo.create_automation("finance", {
            "title": "Revenue", "prompt": "Report revenue.", "schedule_kind": "cron",
            "schedule_expr": "0 8 * * 1", "timezone": "UTC", "enabled": True,
            "next_run_at": datetime.now(UTC) - timedelta(minutes=1),
            **fields,
        })
    return _row(row)


async def test_automation_occurrence_definition_job_outbox_and_receipt_are_atomic(agents):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.modules.agents.automation_jobs import AutomationJobs
    from app.modules.agents.automations import RunResult

    automation = await due_automation(agents)
    jobs = AutomationJobs()
    async with automation_runtime(agents):
        results = await asyncio.gather(jobs.enqueue(automation), jobs.enqueue(automation))
        assert sorted(results) == [False, True]
        claims = await asyncio.gather(jobs.claim(uuid4()), jobs.claim(uuid4()))
        assert sum(row is not None for row in claims) == 1
        job = next(row for row in claims if row is not None)
        runner = SimpleNamespace(run=AsyncMock(return_value=RunResult("delivered:inbox", "thread")))
        await jobs.run(job, runner, datetime.now(UTC))
        runner.run.assert_awaited_once()
        assert await jobs.claim(uuid4()) is None
    state = await agents.admin("SELECT state FROM nova_meta.job_queue")
    assert state == [{"state": "COMPLETED"}]
    for table in ("agent_automation_occurrences", "agent_automation_receipts", "outbox_events",
                  "external_effect_intents", "external_effect_receipts"):
        assert (await agents.admin(f"SELECT count(*) AS n FROM nova_meta.{table}"))[0]["n"] == 1
    rows = await agents.admin(
        "SELECT last_status,last_thread_id FROM nova_meta.config_agent_automations")
    assert rows == [{"last_status": "delivered:inbox", "last_thread_id": "thread"}]


async def test_automation_recovery_never_repeats_an_armed_occurrence(agents):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.modules.agents.automation_jobs import AutomationJobs
    from app.modules.agents.automations import automation_repository

    automation = await due_automation(agents)
    jobs = AutomationJobs()
    async with automation_runtime(agents):
        assert await jobs.enqueue(automation)
        job = await jobs.claim(uuid4(), seconds=1)
        definition = await jobs.definition(job)
        prepared = await jobs.prepare(job, definition, datetime.now(UTC))
        await asyncio.sleep(1.05)
        recovered = await jobs.claim(uuid4())
        assert recovered["state"] == "VERIFYING"
        runner = SimpleNamespace(run=AsyncMock())
        await jobs.run(recovered, runner, datetime.now(UTC))
        runner.run.assert_not_awaited()
        with pytest.raises(MetadataConflict):
            await jobs.heartbeat(prepared)
        assert await automation_repository.due(datetime.now(UTC)) == []
        assert await jobs.enqueue(automation) is False
    assert (await agents.admin("SELECT state FROM nova_meta.job_queue"))[0]["state"] == "BLOCKED"
    rows = await agents.admin(
        "SELECT last_status,next_run_at FROM nova_meta.config_agent_automations")
    assert rows[0]["last_status"] == "VERIFYING"
    assert rows[0]["next_run_at"] == automation["next_run_at"]


async def test_changed_automation_cancels_unprepared_occurrence_without_running(agents):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.modules.agents.automation_jobs import AutomationJobs

    automation = await due_automation(agents)
    jobs = AutomationJobs()
    async with automation_runtime(agents):
        assert await jobs.enqueue(automation)
        job = await jobs.claim(uuid4())
        async with agents.transaction() as (repo, _):
            await repo.update_automation(automation["automation_id"], {"enabled": False},
                                         expected_revision=automation["revision"])
        runner = SimpleNamespace(run=AsyncMock())
        await jobs.run(job, runner, datetime.now(UTC))
        runner.run.assert_not_awaited()
    assert (await agents.admin("SELECT state FROM nova_meta.job_queue"))[0]["state"] == "CANCELLED"
    assert await agents.admin("SELECT id FROM nova_meta.agent_automation_receipts") == []


async def test_scheduled_quality_and_memory_are_scoped_without_agent_configuration_grants(agents):
    from app.modules.assistant.metadata_repository import AssistantMetadataRepository

    agent = await create_agent(agents)
    async with agents.transaction() as (repo, conn):
        conversations = AssistantMetadataRepository(conn, repo.actor)
        thread = await conversations.create_thread(agent_id=agent["agent_id"])
        await conversations.append_message(
            thread["thread_id"], role="assistant", content="Revenue is 12.",
            agent_id=agent["agent_id"], security_context={"principal": "agent-owner",
                "active_role": "ANALYST", "security_context_version": 5},
        )
        await repo.save_quality_record("cases", "case", agent["agent_id"], {"name": "Revenue"},
                                       expected_revision=0)
    async with agents.transaction("task") as (repo, conn):
        assert len(await repo.current_quality_records("cases", agent["agent_id"])) == 1
        assert await repo.save_quality_record(
            "runs", "run", agent["agent_id"], {"status": "passed"}, expected_revision=0,
        ) == 1
        memory = await repo.create_memory(agent["agent_id"], {
            "fact_key": "revenue", "fact": "Revenue is 12.", "source_quote": "Revenue is 12.",
            "source_thread_id": thread["thread_id"],
        }, {"fact": "Revenue is 12."})
        assert len(await AssistantMetadataRepository(conn, repo.actor).quality_traces(
            agent["agent_id"], limit=10)) == 1
    for scope in ({"name": "other-owner"}, {"active": "VIEWER"}):
        async with agents.transaction("task", **scope) as (repo, conn):
            assert await repo.current_quality_records("runs", agent["agent_id"]) == []
            assert await repo.get_memory(memory["memory_id"], "agent-owner",
                                          agent["agent_id"], "ANALYST") is None
            assert await AssistantMetadataRepository(conn, repo.actor).quality_traces(
                agent["agent_id"], limit=10) == []
    for mutate in (
        lambda repo: repo.create_agent({"name": "Forbidden"}),
        lambda repo: repo.save_quality_record("cases", "other", agent["agent_id"], {},
                                              expected_revision=0),
        lambda repo: repo.delete_agent(agent["agent_id"]),
        lambda repo: repo.start_run("forbidden-run", agent["agent_id"], "thread"),
    ):
        with pytest.raises(MetadataPermissionDenied):
            async with agents.transaction("task") as (repo, _):
                await mutate(repo)


@pytest.mark.parametrize("lose_receipt", [False, True])
async def test_external_delivery_has_exact_durable_intent_before_call_and_receipt(
    agents, monkeypatch, lose_receipt,
):
    from unittest.mock import AsyncMock

    from app.modules.agents import mcp_client
    from app.modules.agents.automation_jobs import AutomationJobs
    from app.modules.agents.automations import RunResult, _deliver
    from app.modules.agents.repository import agent_repository

    automation = await due_automation(agents, delivery_json={"mcp_tool_id": "send"})
    monkeypatch.setattr(agent_repository, "get_tool", AsyncMock(return_value={
        "source": "mcp", "server_id": "server", "name": "send_message",
    }))
    monkeypatch.setattr(agent_repository, "get_mcp_server", AsyncMock(return_value={
        "server_id": "server", "endpoint": "https://example.invalid/mcp",
    }))

    async def effect(_server, tool_name, arguments):
        rows = await agents.admin("SELECT request FROM nova_meta.agent_automation_deliveries")
        assert rows[0]["request"]["arguments"] == arguments == {"text": "Revenue: 42"}
        assert rows[0]["request"]["tool_name"] == tool_name == "send_message"
        assert len(rows[0]["request"]["server_configuration_digest"]) == 64
        return {"content": [{"type": "text", "text": "receipt-42"}]}

    call = AsyncMock(side_effect=effect)
    monkeypatch.setattr(mcp_client, "call_tool", call)

    class Runner:
        async def run(self, definition, **_kwargs):
            status = await _deliver(definition, definition["owner_name"], "Revenue: 42")
            return RunResult(status, "thread")

    jobs = AutomationJobs()
    if lose_receipt:
        monkeypatch.setattr(jobs, "delivery_receipt", AsyncMock(side_effect=ConnectionError()))
    async with automation_runtime(agents):
        assert await jobs.enqueue(automation)
        job = await jobs.claim(uuid4())
        await jobs.run(job, Runner(), datetime.now(UTC))
        if lose_receipt:
            assert await jobs.claim(uuid4()) is None
            assert await jobs.enqueue(automation) is False
    call.assert_awaited_once()
    assert await agents.admin("SELECT state FROM nova_meta.job_queue") == [
        {"state": "BLOCKED" if lose_receipt else "COMPLETED"}]
    receipt = await agents.admin(
        "SELECT response_digest,is_error FROM nova_meta.agent_automation_delivery_receipts")
    if lose_receipt:
        assert receipt == []
        current = await agents.admin(
            "SELECT last_status,next_run_at FROM nova_meta.config_agent_automations")
        assert current == [{"last_status": "VERIFYING", "next_run_at": automation["next_run_at"]}]
    else:
        assert len(receipt[0]["response_digest"]) == 64 and receipt[0]["is_error"] is False
