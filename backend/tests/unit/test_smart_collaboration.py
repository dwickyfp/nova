from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.agent_control import AgentControl
from app.modules.agents.auto_planner import Candidate
from app.modules.agents.capabilities import CapabilityManifest
from app.modules.agents.harness_repository import HarnessRepository
from app.modules.agents.identity import AgentPath, participant_id

USER = {
    "username": "alice",
    "active_role": "analyst",
    "assigned_roles": ["analyst"],
    "session_id": "login",
    "security_context_version": 1,
}


class DurableJournal(HarnessRepository):
    """In-memory I/O for production lifecycle and mailbox methods, shared across worker objects."""

    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}
        self.messages: dict[str, dict] = {}
        self.events: list[dict] = []
        self.lock = asyncio.Lock()
        self.changed = asyncio.Condition()
        self.revision = 0

    @asynccontextmanager
    async def admission_lock(self, *args):
        async with self.lock:
            yield AsyncMock()

    async def _create(self, **fields):
        self.runs[fields["run_id"]] = {
            "status": "queued",
            "generation": 1,
            "lease_owner": "worker",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "result_summary": None,
            "error_class": None,
            "started_at": datetime.now(),
            "updated_at": datetime.now(),
            **copy.deepcopy(fields),
        }
        await self.event(
            fields.get("root_run_id") or fields["run_id"], fields["run_id"], "agent_queued", {}
        )
        return copy.deepcopy(self.runs[fields["run_id"]])

    async def get(self, run_id):
        return copy.deepcopy(self.runs.get(run_id))

    async def claim(self, run_id, lease_id):
        row = self.runs[run_id]
        if row["status"] != "queued":
            return False
        row.update(status="running", lease_owner=lease_id, generation=row["generation"] + 1)
        return True

    async def heartbeat(self, run_id, lease_id):
        return None

    async def cancel_session(self, anchor):
        self.runs[anchor["run_id"]]["payload"]["session_cancelled"] = True

    async def tree(self, root_id, **kwargs):
        root = await self.get(root_id)
        if (
            not root
            or root["owner_name"] != kwargs["owner_name"]
            or root["role_name"] != kwargs["role_name"]
        ):
            return []
        return [
            root,
            *[
                copy.deepcopy(row)
                for row in self.runs.values()
                if row.get("root_run_id") == root_id
            ],
        ]

    async def transition(self, run_id, *, from_status, to_status, **kwargs):
        row = self.runs[run_id]
        if row["status"] != from_status:
            return False
        if kwargs.get("lease_owner") and (row["lease_owner"], row["generation"]) != (
            kwargs["lease_owner"],
            kwargs["generation"],
        ):
            return False
        row["status"] = to_status
        for field, value in kwargs.items():
            if value is not None:
                row["result_summary" if field == "summary" else field] = copy.deepcopy(value)
        return True

    async def event(self, root_id, run_id, kind, payload):
        if kind == "agent_message" and any(
            event["payload"].get("message_id") == payload.get("message_id")
            for event in self.events
            if event["type"] == kind
        ):
            return "existing"
        if kind in {
            "agent_completed",
            "agent_failed",
            "agent_interrupted",
            "agent_cancelled",
        } and any(event["run_id"] == run_id and event["type"] == kind for event in self.events):
            return "existing"
        self.events.append(
            {"root_id": root_id, "run_id": run_id, "type": kind, "payload": copy.deepcopy(payload)}
        )
        async with self.changed:
            self.revision += 1
            self.changed.notify_all()
        return str(self.revision)

    async def ensure_terminal_event(self, root_id, run):
        await self.event(root_id, run["run_id"], f"agent_{run['status']}", {})

    @asynccontextmanager
    async def notifications(self, root_id):
        revision = self.revision

        async def wake(timeout):
            nonlocal revision
            async with self.changed:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self.changed.wait_for(lambda: self.revision != revision), timeout
                    )
                revision = self.revision

        yield wake

    async def execute(self, sql, params):
        if sql.startswith("SELECT run_id, root_run_id"):
            return {
                "rows": [
                    [row["run_id"], row["root_run_id"]]
                    for row in self.runs.values()
                    if row["status"] == "running" and row["updated_at"] < params[0]
                ]
            }
        if sql.startswith(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET status = %s, error_class = 'worker_lost'"
        ):
            row = self.runs[params[2]]
            if row["status"] != "running" or row["updated_at"] >= params[3]:
                return {"affected": 0}
            row.update(status=params[0], error_class="worker_lost", updated_at=params[1])
            return {"affected": 1}
        if sql.startswith("SELECT message_id, recipient_run_id"):
            row = self.messages.get(params[0])
            return {
                "rows": [
                    [
                        row[key]
                        for key in (
                            "message_id",
                            "recipient_run_id",
                            "message_type",
                            "correlation_id",
                            "reply_to",
                            "content",
                            "origin",
                            "delivery_mode",
                        )
                    ]
                ]
                if row
                else []
            }
        if sql.startswith("INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_MESSAGES"):
            keys = (
                "message_id",
                "root_run_id",
                "sender_run_id",
                "recipient_run_id",
                "message_type",
                "correlation_id",
                "reply_to",
                "content",
                "created_at",
                "origin",
                "delivery_mode",
            )
            self.messages[params[0]] = {**dict(zip(keys, params, strict=True)), "consumed_at": None}
            return {"affected": 1}
        if sql.startswith("SELECT recipient_run_id, message_id"):
            keys = (
                "recipient_run_id",
                "message_id",
                "sender_run_id",
                "message_type",
                "content",
                "correlation_id",
                "reply_to",
                "origin",
            )
            rows = sorted(
                self.messages.values(), key=lambda row: (row["created_at"], row["message_id"])
            )
            return {
                "rows": [
                    [row[key] for key in keys]
                    for row in rows
                    if row["recipient_run_id"] == params[0]
                    and row["consumed_at"] is None
                    and row["delivery_mode"] == "QUEUE_ONLY"
                ][: params[1]]
            }
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_MESSAGES SET consumed_at"):
            row = self.messages[params[1]]
            if row["recipient_run_id"] != params[2] or row["consumed_at"] is not None:
                return {"affected": 0}
            row["consumed_at"] = params[0]
            return {"affected": 1}
        raise AssertionError(sql)


@pytest.fixture
async def collaboration(monkeypatch):
    from app.modules.agents import agent_control, harness_repository

    repo = DurableJournal()
    monkeypatch.setattr(harness_repository.db, "execute_system", repo.execute)
    monkeypatch.setattr(repo, "_audit_message", AsyncMock())
    monkeypatch.setattr(repo, "audit_interrupt", AsyncMock())
    candidates = [
        Candidate(agent_id=name, name=name.title(), manifest=CapabilityManifest(), metrics=())
        for name in ("finance", "marketing", "cohort", "forecast")
    ]
    monkeypatch.setattr(agent_control, "authorized_candidates", AsyncMock(return_value=candidates))
    root = await repo.create_root(
        owner_name="alice",
        thread_id="chat",
        role_name="analyst",
        session_id="login",
        security_version=1,
        objective="Explain revenue and campaign changes",
    )
    repo.runs[root["run_id"]]["status"] = "running"
    root = await repo.get(root["run_id"])
    return repo, AgentControl(repo, root, USER)


async def spawn(control, name, task_name=None):
    result = await control.spawn_agent(
        agent=name,
        task_name=task_name or name,
        objective=f"Analyze {name}",
        operation_id=task_name or name,
    )
    row = await control.repository.get(result["current_turn_id"])
    return AgentControl(control.repository, row, USER)






@pytest.mark.parametrize(
    "value,depth,parent",
    [
        ("/root", 0, None),
        ("/root/finance", 1, "/root"),
        ("/root/finance/forecast", 2, "/root/finance"),
    ],
)
def test_paths(value, depth, parent):
    path = AgentPath(value)
    assert path.depth == depth
    assert (path.parent().value if path.parent() else None) == parent
    assert path.root() == AgentPath()
    assert path.resolve(".") == path
    if depth:
        assert path.is_descendant_of(AgentPath())


@pytest.mark.parametrize(
    "path", ["root", "/other", "/root/", "/root//a", "/root/../a", "/root/UPPER", "/root/a.b"]
)
def test_invalid_paths(path):
    with pytest.raises(ValueError):
        AgentPath(path)


def test_relative_paths_cannot_escape():
    assert AgentPath("/root/finance").resolve("../marketing") == AgentPath("/root/marketing")
    with pytest.raises(ValueError):
        AgentPath().resolve("..")
    assert not AgentPath("/root/fin").is_ancestor_of(AgentPath("/root/finance"))


@pytest.mark.asyncio
async def test_spawn_resolves_only_unambiguous_authorized_names(collaboration):
    _, control = collaboration
    spawned = await control.spawn_agent(
        agent="Finance", task_name="named", objective="Analyze revenue", operation_id="named"
    )
    assert spawned["agent_id"] == "finance"
    with pytest.raises(ValueError, match="unavailable"):
        await control.spawn_agent(
            agent="Unlisted", task_name="unlisted", objective="Analyze", operation_id="unlisted"
        )


@pytest.mark.asyncio
async def test_specialist_prompt_queries_its_own_semantic_coverage(collaboration):
    from app.modules.agents.collaboration_tools import collaboration_prompt

    _, control = collaboration
    assert "discover_agents with the complete user request" in collaboration_prompt(control)
    specialist = await spawn(control, "finance")
    prompt = collaboration_prompt(specialist)
    assert "Use your configured semantic_query" in prompt
    assert "discover_agents with the complete user request" not in prompt


@pytest.mark.asyncio
async def test_message_retry_repairs_event_after_persistence_crash(collaboration, monkeypatch):
    repo, root = collaboration
    finance = await spawn(root, "finance")
    event = repo.event
    monkeypatch.setattr(repo, "event", AsyncMock(side_effect=ConnectionError("crash after insert")))
    with pytest.raises(ConnectionError):
        await root.send_message(
            target="/root/finance", content="New evidence", operation_id="repair"
        )
    assert len(await repo.pending_messages(participant_id(finance.caller))) == 1
    monkeypatch.setattr(repo, "event", event)
    await root.send_message(target="/root/finance", content="New evidence", operation_id="repair")
    await root.send_message(target="/root/finance", content="New evidence", operation_id="repair")
    assert len(repo.messages) == 1
    assert sum(item["type"] == "agent_message" for item in repo.events) == 1


@pytest.mark.asyncio
async def test_replaced_worker_cannot_mutate_collaboration(collaboration):
    repo, root = collaboration
    await spawn(root, "finance")
    stale = AgentControl(repo, root.caller, USER, enforce_lease=True)
    repo.runs[root.root_id]["generation"] += 1
    with pytest.raises(ValueError, match="lease"):
        await stale.spawn_agent(
            agent="marketing", task_name="marketing", objective="More work", operation_id="stale"
        )
    with pytest.raises(ValueError, match="lease"):
        await stale.send_message(
            target="/root/finance", content="Late evidence", operation_id="late"
        )
    with pytest.raises(ValueError, match="lease"):
        await stale.interrupt_agent(target="/root/finance")
    assert len(repo.runs) == 2
    assert not repo.messages


@pytest.mark.asyncio
async def test_cancel_idle_session_blocks_followups_and_subtree_is_scoped(collaboration):
    repo, root = collaboration
    finance = await spawn(root, "finance")
    marketing = await spawn(root, "marketing")
    cohort = await spawn(marketing, "cohort")
    repo.runs[marketing.caller["run_id"]]["status"] = "completed"
    await root.interrupt_agent(target="/root/marketing", cancel=True, subtree=True)
    assert repo.runs[cohort.caller["run_id"]]["status"] == "cancelled"
    assert repo.runs[finance.caller["run_id"]]["status"] == "queued"
    assert (
        next(item for item in await root.list_agents() if item["agent_path"] == "/root/marketing")[
            "status"
        ]
        == "cancelled"
    )
    with pytest.raises(ValueError, match="follow-up"):
        await root.followup_task(target="/root/marketing", task="Resume", operation_id="denied")
    repo.audit_interrupt.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_can_steer_root_without_starting_a_turn(collaboration):
    repo, root = collaboration
    await root.send_message(
        target="/root",
        content="Prioritize enterprise customers",
        operation_id="root-user",
        origin="user",
    )
    assert len(repo.runs) == 1
    assert (await repo.pending_messages(root.root_id))[0]["origin"] == "user"
    with pytest.raises(ValueError, match="itself"):
        await root.send_message(target="/root", content="Loop", operation_id="self")


@pytest.mark.asyncio
async def test_message_event_retry_keeps_its_cursor_after_redis_restart(monkeypatch):
    from app.modules.agents import harness_repository as module
    from tests.unit.test_agent_harness import _EventRedis

    saved = {}
    cursor = AsyncMock()
    selected = []

    async def cursor_execute(sql, params=None):
        if params:
            selected[:] = params

    cursor.execute.side_effect = cursor_execute
    cursor.fetchall.side_effect = lambda: [saved[selected[0]]] if selected[0] in saved else []
    cursor.__aenter__.return_value = cursor

    class Connection:
        def cursor(self):
            return cursor

    @asynccontextmanager
    async def connection():
        yield Connection()

    async def execute(sql, params=None):
        if sql.startswith("SELECT last_sequence"):
            return {"rows": [[-1]]}
        if sql.startswith("INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS"):
            saved[params[0]] = [params[1], params[2]]
        return {"affected": 1}

    monkeypatch.setattr(module.db, "execute_system", execute)
    monkeypatch.setattr(module.db, "system_conn", connection)
    monkeypatch.setattr(module.session_store, "_redis", _EventRedis())
    repo = HarnessRepository()
    sequence = await repo.event("root", "finance", "agent_message", {"message_id": "finding"})
    monkeypatch.setattr(module.session_store, "_redis", _EventRedis())
    replay = await repo.event("root", "finance", "agent_message", {"message_id": "finding"})
    assert replay == sequence
    assert len(saved) == 1


@pytest.mark.asyncio
async def test_schema_upgrade_adds_queue_only_default_without_rewriting_history(monkeypatch):
    from app.modules.agents import harness_repository as module

    repo = HarnessRepository()
    monkeypatch.setattr(module.run_journal, "ensure_schema", AsyncMock())
    monkeypatch.setattr(repo, "_backfill_event_sequences", AsyncMock())
    existing = set()
    statements = []

    async def execute(sql, params=None):
        statements.append(sql)
        if sql.startswith("ALTER TABLE"):
            existing.add((sql.split()[2].split(".")[1], sql.split()[5]))
        return {"rows": [], "affected": 1}

    monkeypatch.setattr(module.db, "execute_system", execute)
    monkeypatch.setattr(
        repo,
        "_column_exists",
        AsyncMock(side_effect=lambda table, column: (table, column) in existing),
    )
    await repo.ensure_schema()
    await repo.ensure_schema()
    delivery = [
        sql for sql in statements if sql.startswith("ALTER TABLE") and "delivery_mode" in sql
    ]
    assert delivery == [
        "ALTER TABLE NOVA_SYSTEM.CONFIG_AGENT_MESSAGES ADD COLUMN delivery_mode VARCHAR(16) NOT"
        " NULL DEFAULT 'QUEUE_ONLY'"
    ]
    assert not any(sql.startswith(("DELETE", "DROP", "UPDATE")) for sql in statements)


@pytest.mark.asyncio
async def test_golden_collaboration(collaboration):
    repo, root = collaboration
    assert len(await root.discover_agents("revenue")) == 4
    finance, marketing = await asyncio.gather(spawn(root, "finance"), spawn(root, "marketing"))
    for control in (finance, marketing):
        repo.runs[control.caller["run_id"]]["status"] = "running"
    await finance.send_message(
        target="../marketing",
        content="Jakarta contributes 68% of the decline",
        operation_id="finding",
    )
    inbox = await repo.pending_messages(participant_id(marketing.caller))
    assert inbox[0]["sender_run_id"] == finance.caller["run_id"]
    assert len(repo.runs) == 3
    await repo.acknowledge_messages(participant_id(marketing.caller), [inbox[0]["message_id"]])
    cohort = await spawn(marketing, "cohort")
    assert participant_id(cohort.caller) != participant_id(marketing.caller)
    assert cohort.caller["depth"] == 2
    repo.runs[cohort.caller["run_id"]].update(
        status="completed", result_summary="Jakarta conversion declined 24%"
    )
    tree = await root._tree()
    await repo.reconcile_collaboration(tree[0], tree)
    assert (await repo.pending_messages(participant_id(marketing.caller)))[0][
        "message_type"
    ] == "final"
    repo.runs[finance.caller["run_id"]].update(
        status="completed", result_summary="Revenue evidence"
    )
    await marketing.send_message(
        target="/root/finance", content="Campaign conversion fell", operation_id="reply"
    )
    assert len(repo.runs) == 4
    result = await marketing.followup_task(
        target="/root/finance", task="Correlate with campaign evidence", operation_id="followup"
    )
    assert result["agent_session_id"] == participant_id(finance.caller)
    assert result["turn_id"] != finance.caller["run_id"]
    await root.send_message(
        target="/root/finance",
        content="Prioritize enterprise customers",
        operation_id="steering",
        origin="user",
    )
    messages = await repo.pending_messages(participant_id(finance.caller))
    assert [row["origin"] for row in messages] == ["agent", "user"]
    forecast = await spawn(marketing, "forecast")
    await root.interrupt_agent(target=participant_id(forecast.caller))
    assert repo.runs[forecast.caller["run_id"]]["status"] == "interrupted"
    assert repo.runs[marketing.caller["run_id"]]["status"] == "running"
    assert repo.runs[root.caller["run_id"]]["status"] == "running"
    repo.runs[result["turn_id"]].update(
        status="completed", result_summary="Enterprise revenue and campaign findings agree"
    )
    repo.runs[marketing.caller["run_id"]].update(
        status="completed", result_summary="Campaign evidence from cohort"
    )
    waited = await root.wait_agent(targets=["/root/finance", "/root/marketing"], timeout=0)
    assert waited["reason"] == "completed"
    assert len(waited["agents"]) == 2
    assert len(await root.list_agents()) == 5
    assert (
        sum(
            row["type"] == "agent_message" and row["run_id"] == finance.caller["run_id"]
            for row in repo.events
        )
        == 1
    )


@pytest.mark.asyncio
async def test_recovery_retains_message_and_completion_once(collaboration):
    repo, root = collaboration
    finance = await spawn(root, "finance")
    forecast = await spawn(finance, "forecast")
    sent = await forecast.send_message(target="..", content="Forecast evidence", operation_id="one")
    recovered = AgentControl(repo, await repo.get(finance.caller["run_id"]), USER)
    assert (await repo.pending_messages(participant_id(recovered.caller)))[0]["message_id"] == sent[
        "message_id"
    ]
    await forecast.send_message(target="..", content="Forecast evidence", operation_id="one")
    assert len(repo.messages) == 1
    repo.runs[forecast.caller["run_id"]].update(
        status="completed", result_summary="Forecast completed"
    )
    tree = await root._tree()
    await repo.reconcile_collaboration(tree[0], tree)
    await repo.reconcile_collaboration(tree[0], tree)
    assert len(repo.messages) == 2
    assert sum(event["type"] == "agent_completed" for event in repo.events) == 1
    ids = [row["message_id"] for row in await repo.pending_messages(participant_id(finance.caller))]
    await repo.acknowledge_messages(participant_id(finance.caller), ids)
    assert await repo.pending_messages(participant_id(finance.caller)) == []


@pytest.mark.asyncio
async def test_followups_are_serial_and_idempotent(collaboration):
    repo, root = collaboration
    finance = await spawn(root, "finance")
    results = await asyncio.gather(
        *[
            root.followup_task(target="/root/finance", task="More evidence", operation_id="same")
            for _ in range(2)
        ]
    )
    assert results[0] == results[1]
    assert len(repo.runs) == 3
    assert repo.runs[results[0]["turn_id"]]["status"] == "waiting_for_turn"
    another = await root.followup_task(
        target="/root/finance", task="Check another cohort", operation_id="next"
    )
    repo.runs[finance.caller["run_id"]]["status"] = "completed"
    await repo.release_followups(root.root_id)
    assert repo.runs[results[0]["turn_id"]]["status"] == "queued"
    assert repo.runs[another["turn_id"]]["status"] == "waiting_for_turn"


@pytest.mark.asyncio
async def test_concurrency_releases_without_lifetime_cap(collaboration):
    repo, root = collaboration
    repo.runs[root.root_id]["payload"]["limits"] = {"max_concurrent_agents": 2}
    one = await spawn(root, "finance")
    await spawn(root, "marketing")
    with pytest.raises(ValueError, match="Concurrent"):
        await spawn(root, "cohort")
    for index in range(5):
        repo.runs[one.caller["run_id"]]["status"] = "completed"
        one = await spawn(root, "cohort", f"cohort-{index}")
    assert len(await root.list_agents()) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value",
    [
        ("owner_name", "bob"),
        ("role_name", "admin"),
        ("thread_id", "other-workspace"),
        ("session_id", "other-login"),
        ("security_version", 2),
    ],
)
async def test_boundary_denial(collaboration, key, value):
    repo, root = collaboration
    finance = await spawn(root, "finance")
    repo.runs[finance.caller["run_id"]][key] = value
    with pytest.raises(ValueError, match="boundary"):
        await root.send_message(target="/root/finance", content="private", operation_id="cross")
    assert not repo.messages


@pytest.mark.asyncio
async def test_depth_total_unknown_and_collision_guards(collaboration):
    repo, root = collaboration
    finance = await spawn(root, "finance")
    repo.runs[root.root_id]["payload"]["limits"] = {"max_agent_depth": 1}
    with pytest.raises(ValueError, match="depth"):
        await spawn(finance, "forecast")
    with pytest.raises(ValueError, match="unavailable"):
        await spawn(root, "unknown")
    with pytest.raises(ValueError, match="path already"):
        await root.spawn_agent(
            agent="marketing", task_name="finance", objective="Other", operation_id="other"
        )
    repo.runs[root.root_id]["payload"]["limits"] = {"max_total_agent_sessions": 2}
    repo.runs[finance.caller["run_id"]]["status"] = "completed"
    with pytest.raises(ValueError, match="Total agent session"):
        await spawn(root, "marketing")


@pytest.mark.asyncio
async def test_event_driven_wait_wakes_on_direct_message(collaboration):
    repo, root = collaboration
    finance, marketing = await spawn(root, "finance"), await spawn(root, "marketing")
    waiter = asyncio.create_task(finance.wait_agent(targets=["/root/marketing"], timeout=2))
    await marketing.send_message(
        target="/root/finance", content="New evidence", operation_id="wake"
    )
    assert (await waiter)["reason"] == "message"
    assert (await root.wait_agent(targets=["/root/marketing"], timeout=0))["reason"] == "timeout"


@pytest.mark.parametrize(
    "mode,expected",
    [("fresh", False), ("parent_summary", True), ("last_n_turns", True), ("full", True)],
)
def test_context_modes(mode, expected):
    parent = {
        "run_id": "a",
        "objective": "Parent objective",
        "result_summary": "Evidence",
        "payload": {},
    }
    context = AgentControl._bounded_context(parent, [parent], mode, "Explicit task")
    assert ("Parent objective" in context) is expected
    assert "Explicit task" in context


def test_context_never_copies_authentication():
    parent = {
        "run_id": "a",
        "objective": "Task",
        "payload": {},
        "encrypted_password": "hidden-value",
    }
    assert "hidden-value" not in AgentControl._bounded_context(parent, [parent], "full", "")
