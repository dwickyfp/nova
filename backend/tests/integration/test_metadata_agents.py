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


async def test_shared_agents_follow_the_active_role_only(agents):
    agent = await create_agent(agents)
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
