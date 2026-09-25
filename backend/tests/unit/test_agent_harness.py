from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest
import yaml

from app.modules.agents import auto_planner as planning
from app.modules.agents import capabilities as capability_module
from app.modules.agents import repository as agent_repository_module
from app.modules.agents.auto_planner import (
    AutoPlanner,
    Candidate,
    rank_candidates,
    semantic_matches,
)
from app.modules.agents.capabilities import CapabilityManifest, CapabilityRepository
from app.modules.agents.harness_repository import AutoAdmissionUnavailable, HarnessRepository
from app.modules.agents.harness_tools import RequestSpecialistTool, SendAgentMessageTool
from app.modules.agents.harness_worker import (
    AgentHarnessWorker,
    AuthenticationUnavailable,
    BudgetExceeded,
    _render_evidence_tables,
    _table_evidence,
)
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.assistant.tools import ToolInvocation, ToolRegistry


def _run(run_id: str, *, depth: int = 0, status: str = "queued") -> dict:
    return {
        "run_id": run_id,
        "root_run_id": None if depth == 0 else "root",
        "parent_run_id": None if depth == 0 else "root",
        "agent_id": "__auto__" if depth == 0 else run_id,
        "owner_name": "alice",
        "role_name": "analyst",
        "thread_id": "thread",
        "session_id": "session",
        "security_version": 1,
        "depth": depth,
        "objective": "Why did revenue fall?",
        "payload": {},
        "checkpoint": {},
        "status": status,
        "generation": 1,
        "lease_owner": "worker",
    }


class _EventLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.extensions = 0

    async def __aenter__(self):
        await self._lock.acquire()
        return self

    async def __aexit__(self, *_args):
        self._lock.release()

    async def extend(self, timeout: int, *, replace_ttl: bool) -> bool:
        self.extensions += 1
        return self._lock.locked() and timeout == 600 and replace_ttl

    async def owned(self) -> bool:
        return self._lock.locked()


class _EventRedis:
    def __init__(self) -> None:
        self.values: dict[str, int | str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.locks: dict[str, _EventLock] = {}

    def lock(self, name: str, **kwargs):
        assert kwargs["timeout"] == 600
        return self.locks.setdefault(name, _EventLock())

    async def eval(self, script: str, keys: int, key: str, floor: int) -> int:
        assert keys == 1 and "INCR" in script
        sequence = max(int(self.values.get(key, -1)), int(floor)) + 1
        self.values[key] = sequence
        return sequence


    async def get(self, key: str) -> int | str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, **kwargs) -> None:
        self.values[key] = value

    async def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def expire(self, key: str, seconds: int) -> None:
        assert seconds == 86400

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.values.pop(key, None)
            self.hashes.pop(key, None)


@pytest.mark.asyncio
async def test_auto_admission_lock_serializes_same_owner_thread(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    redis = _EventRedis()
    monkeypatch.setattr(module.session_store, "_redis", redis)
    repository = HarnessRepository()
    acquired = asyncio.Event()

    async def second_request() -> None:
        async with repository.admission_lock("thread", "alice") as assert_owned:
            await assert_owned()
            acquired.set()

    async with repository.admission_lock("thread", "alice") as assert_owned:
        await assert_owned()
        second = asyncio.create_task(second_request())
        await asyncio.sleep(0)
        assert not acquired.is_set()
    await asyncio.wait_for(second, timeout=1)
    assert acquired.is_set()
    assert len(redis.locks) == 1


@pytest.mark.asyncio
async def test_auto_admission_fails_closed_without_a_redis_lock(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    monkeypatch.setattr(module.session_store, "_redis", None)
    with pytest.raises(AutoAdmissionUnavailable):
        async with HarnessRepository().admission_lock("thread", "alice"):
            pytest.fail("Admission must not proceed without the lock")


@pytest.mark.asyncio
async def test_auto_admission_retries_a_transient_empty_active_run_read(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    reads = 0

    async def execute(sql: str, params=None):
        nonlocal reads
        assert "COUNT(*)" in sql
        assert params == ["thread", "alice", "thread", "alice"]
        reads += 1
        return {"rows": [["thread", "alice", 0 if reads == 1 else 1]]}

    monkeypatch.setattr(module.db, "execute_system", execute)
    assert await HarnessRepository().active_for_thread("thread", "alice") is True
    assert reads == 2


@pytest.mark.asyncio
async def test_auto_admission_confirms_an_empty_thread_before_allowing_a_run(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    execute = AsyncMock(return_value={"rows": [["thread", "alice", 0]]})
    monkeypatch.setattr(module.db, "execute_system", execute)
    assert await HarnessRepository().active_for_thread("thread", "alice") is False
    assert execute.await_count == 4


@pytest.mark.asyncio
async def test_auto_admission_rejects_persistent_mismatched_scope(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    execute = AsyncMock(return_value={"rows": [["another-thread", "alice", 0]]})
    monkeypatch.setattr(module.db, "execute_system", execute)
    with pytest.raises(AutoAdmissionUnavailable):
        await HarnessRepository().active_for_thread("thread", "alice")
    assert execute.await_count == 8


def _candidate(name: str, owns: str, metric: str, aliases: list[str]) -> Candidate:
    return Candidate(
        agent_id=name,
        name=name.title(),
        manifest=CapabilityManifest(
            delegation_description=f"Owns {owns}", capability_tags=[owns], owns=[owns]
        ),
        metrics=(
            {
                "name": metric,
                "synonyms": aliases,
                "owner_domain": owns,
                "supporting_domains": [],
                "authority": "actual",
            },
        ),
    )


def test_auto_table_preview_marks_omitted_rows() -> None:
    evidence = _table_evidence(
        {"columns": ["channel", "revenue"], "rows": [[f"C{index}", index] for index in range(21)]}
    )
    assert evidence is not None
    assert len(evidence["rows"]) == 20
    assert evidence["truncated"] is True
    assert "Some result rows or columns were omitted" in _render_evidence_tables([evidence])


def test_auto_table_evidence_redacts_credential_columns_and_values() -> None:
    secret_column = "feature_" + "x" * 80 + "Password"
    evidence = _table_evidence(
        {
            "columns": ["channel", secret_column, "reference"],
            "rows": [["Direct", "opaque-private-value", "sk-abcdefghijklmnopqrst"]],
        }
    )

    assert evidence is not None
    assert evidence["rows"] == [["Direct", "***", "***"]]
    rendered = _render_evidence_tables([evidence])
    assert "opaque-private-value" not in rendered
    assert "sk-abcdefghijklmnopqrst" not in rendered


@pytest.mark.asyncio
async def test_ml_tool_redacts_table_and_provider_data(monkeypatch) -> None:
    from app.modules.agents.tools import ml_execute as ml_module

    result = SimpleNamespace(
        run_id="ml-run",
        task="clustering",
        mode="interactive",
        selected_algorithm="kmeans",
        selected_engine="native",
        training_rows=2,
        cache_hit=False,
        results=[
            {"channel": "Direct", "reference": "sk-abcdefghijklmnopqrst"},
            {"channel": "Retail", "clientSecret": "opaque-private-value"},
        ],
    )
    monkeypatch.setattr(ml_module, "decrypt_password", lambda _value: "decrypted")
    monkeypatch.setattr(ml_module.ml_engine_service, "execute", AsyncMock(return_value=result))

    outcome = await ml_module.MLExecuteTool().run(
        ToolInvocation("call", "ml_execute", {"task": "clustering", "input_sql": "SELECT 1"}),
        SimpleNamespace(user={"username": "alice", "encrypted_password": "encrypted"}),
    )

    assert outcome.ok
    assert outcome.table is not None
    assert outcome.table["rows"] == [
        ["Direct", "***", None],
        ["Retail", None, "***"],
    ]
    assert outcome.data["results"] == [
        {"channel": "Direct", "reference": "***", "clientSecret": None},
        {"channel": "Retail", "reference": None, "clientSecret": "***"},
    ]
    envelope = json.dumps(outcome.envelope(tool_name="ml_execute"))
    assert "opaque-private-value" not in envelope
    assert "sk-abcdefghijklmnopqrst" not in envelope


def test_semantic_resolution_uses_declared_alias_and_ownership() -> None:
    finance = _candidate("finance", "finance", "recognized_revenue", ["omzet"])
    marketing = _candidate("marketing", "marketing", "attributed_revenue", ["campaign revenue"])
    matches = semantic_matches("Berapa omzet bulan ini?", [finance, marketing])
    assert [(m["metric"], m["owner_domain"]) for m in matches] == [
        ("recognized_revenue", "finance")
    ]
    assert semantic_matches("Campaign revenue versus omzet", [finance, marketing]) == [
        *semantic_matches("omzet", [finance]),
        *semantic_matches("campaign revenue", [marketing]),
    ]


def test_ossie_preserves_metric_business_ownership() -> None:
    sample = (
        Path(__file__).resolve().parents[2]
        / "app/modules/agents/examples/nova_sales.ossie.yaml"
    )
    document = yaml.safe_load(sample.read_text())
    metric = document["metrics"][0]
    metric.update(
        owner_domain="finance",
        supporting_domains=["marketing"],
        authority="financial_actual",
        synonyms=["omzet"],
    )
    parsed = parse_ossie(yaml.safe_dump(document))
    stored = parsed.model["metrics"][0]
    assert stored["owner_domain"] == "finance"
    assert stored["supporting_domains"] == ["marketing"]
    assert stored["authority"] == "financial_actual"
    assert stored["synonyms"] == ["omzet"]


def test_capability_manifest_round_trips_and_owner_ranks_first() -> None:
    finance = _candidate("finance", "finance", "recognized_revenue", ["omzet"])
    marketing = Candidate(
        agent_id="marketing",
        name="Marketing",
        manifest=CapabilityManifest(
            delegation_description="Campaign attribution",
            capability_tags=["marketing"],
            owns=["attributed_revenue"],
        ),
        metrics=(
            {
                "name": "recognized_revenue",
                "synonyms": ["omzet"],
                "owner_domain": "finance",
                "supporting_domains": ["marketing"],
            },
        ),
    )
    assert (
        CapabilityManifest.model_validate_json(finance.manifest.model_dump_json())
        == finance.manifest
    )
    matches = semantic_matches("Kenapa omzet turun?", [marketing, finance])
    ranked = rank_candidates("Kenapa omzet turun?", [marketing, finance], matches)
    assert ranked[0].agent_id == "finance"
    assert CapabilityManifest(available_to_auto=False).available_to_auto is False


@pytest.mark.asyncio
async def test_capability_lookup_retries_a_mismatched_starrocks_row(monkeypatch) -> None:
    agent = {"agent_id": "finance", "owner_name": "alice"}
    manifest = CapabilityManifest(owns=["revenue"])
    execute = AsyncMock(
        side_effect=[
            {"rows": [["other-agent", "alice", "invalid-json"]]},
            {"rows": [["finance", "alice", manifest.model_dump_json()]]},
        ]
    )
    monkeypatch.setattr(capability_module.db, "execute_system", execute)

    actual = await CapabilityRepository().get(agent)

    assert execute.await_count == 2
    assert actual == manifest


@pytest.mark.asyncio
async def test_capability_lookup_never_enables_auto_after_inconsistent_read(monkeypatch) -> None:
    execute = AsyncMock(
        side_effect=[
            {"rows": [["another-agent", "alice", "{}"]]},
            *({"rows": []} for _ in range(4)),
        ]
    )
    monkeypatch.setattr(capability_module.db, "execute_system", execute)

    with pytest.raises(agent_repository_module.AgentMetadataUnavailable):
        await CapabilityRepository().get({"agent_id": "finance", "owner_name": "alice"})


@pytest.mark.asyncio
async def test_discovery_retries_transient_metadata_before_declaring_no_specialist(
    monkeypatch,
) -> None:
    agent = {"agent_id": "finance", "owner_name": "alice", "name": "Finance Agent"}
    list_agents = AsyncMock(
        side_effect=[
            agent_repository_module.AgentMetadataUnavailable("temporary"),
            [agent],
        ]
    )
    monkeypatch.setattr(planning.agent_repository, "list_agents", list_agents)
    monkeypatch.setattr(planning.agent_repository, "list_shared_agents", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        planning, "session_security", lambda _user: SimpleNamespace(active_role="analyst")
    )
    monkeypatch.setattr(planning, "has_verified_access", AsyncMock(return_value=True))
    monkeypatch.setattr(
        planning.capability_repository,
        "get",
        AsyncMock(return_value=CapabilityManifest(owns=["recognized_revenue"])),
    )
    monkeypatch.setattr(planning, "bound_view_ids", lambda _agent: [])

    candidates = await planning.authorized_candidates({"username": "alice"})

    assert [candidate.agent_id for candidate in candidates] == ["finance"]
    assert list_agents.await_count == 2


@pytest.mark.asyncio
async def test_discovery_surfaces_persistent_metadata_failure(monkeypatch) -> None:
    list_agents = AsyncMock(
        side_effect=agent_repository_module.AgentMetadataUnavailable("temporary")
    )
    monkeypatch.setattr(planning.agent_repository, "list_agents", list_agents)
    monkeypatch.setattr(
        planning, "session_security", lambda _user: SimpleNamespace(active_role="analyst")
    )

    with pytest.raises(planning.AgentDiscoveryUnavailable):
        await planning.authorized_candidates({"username": "alice"})

    assert list_agents.await_count == 3


@pytest.mark.asyncio
async def test_planner_rejects_inaccessible_and_duplicate_agent_ids(monkeypatch) -> None:
    candidates = [
        _candidate("finance", "finance", "recognized_revenue", ["omzet"]),
        _candidate("marketing", "marketing", "attributed_revenue", ["campaign revenue"]),
    ]
    monkeypatch.setattr(planning, "authorized_candidates", AsyncMock(return_value=candidates))

    class Provider:
        async def complete(self, *, messages):
            assert len(json.loads(messages[1]["content"])["agents"]) == 2
            return {
                "content": json.dumps(
                    {
                        "intent": "diagnostic",
                        "plan_summary": "Check recognized revenue.",
                        "assignments": [
                            {"agent_id": "finance", "objective": "Quantify the decline."},
                            {"agent_id": "secret", "objective": "Leak data."},
                            {"agent_id": "finance", "objective": "Repeat."},
                        ],
                        "steering": [],
                    }
                )
            }

    plan = await AutoPlanner(Provider()).plan(
        question="Kenapa omzet turun?", user={"username": "alice"}
    )
    assert [assignment.agent_id for assignment in plan.assignments] == ["finance"]
    assert plan.semantic_matches[0]["metric"] == "recognized_revenue"


@pytest.mark.asyncio
async def test_planner_uses_a_specialists_chat_model(monkeypatch) -> None:
    candidate = _candidate("finance", "finance", "recognized_revenue", ["omzet"])
    candidate = replace(
        candidate, model_provider_id="chat-provider", model_name="chat-model"
    )
    monkeypatch.setattr(planning, "authorized_candidates", AsyncMock(return_value=[candidate]))

    class Provider:
        async def resolve(self, *, provider_id, model):
            assert (provider_id, model) == ("chat-provider", "chat-model")
            return "selected-chat-model"

        async def complete(self, *, messages, provider):
            assert provider == "selected-chat-model"
            return {"content": '{"assignments": []}'}

    await AutoPlanner(Provider()).plan(question="Check omzet", user={})


@pytest.mark.asyncio
async def test_discovery_never_returns_unverified_or_opted_out_agents(monkeypatch) -> None:
    agents = [
        {"agent_id": name, "owner_name": "alice", "name": name.title()}
        for name in ("finance", "secret", "disabled")
    ]
    monkeypatch.setattr(
        planning.agent_repository, "list_agents", AsyncMock(return_value=agents)
    )
    monkeypatch.setattr(
        planning.agent_repository, "list_shared_agents", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        planning.agent_repository, "get_semantic_model", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        planning.capability_repository, "get", AsyncMock(
            side_effect=lambda agent: CapabilityManifest(
                available_to_auto=agent["agent_id"] != "disabled"
            )
        )
    )
    monkeypatch.setattr(
        planning, "has_verified_access", AsyncMock(
            side_effect=lambda agent, **kwargs: agent["agent_id"] != "secret"
        )
    )
    user = {"username": "alice", "active_role": "analyst",
            "assigned_roles": ["analyst"], "security_context_version": 1}
    assert [item.agent_id for item in await planning.authorized_candidates(user)] == ["finance"]


@pytest.mark.asyncio
async def test_planner_caps_total_children_and_depth(monkeypatch) -> None:
    candidates = [_candidate(f"agent-{index}", f"domain-{index}", f"metric-{index}", [])
                  for index in range(6)]
    monkeypatch.setattr(planning, "authorized_candidates", AsyncMock(return_value=candidates))

    class Provider:
        async def complete(self, **kwargs):
            return {"content": json.dumps({"intent": "compare", "assignments": [
                {"agent_id": item.agent_id, "objective": "Check evidence"}
                for item in candidates
            ]})}

    plan = await AutoPlanner(Provider()).plan(question="Compare all domains", user={})
    assert len(plan.assignments) == 4
    with pytest.raises(ValueError, match="Auto root"):
        await HarnessRepository().spawn(
            parent=_run("finance", depth=1), agent_id="nested",
            objective="Run nested task", operation_id="nested",
        )


def test_session_wall_budget_expires() -> None:
    root = _run("root")
    root["started_at"] = datetime.now(UTC) - timedelta(minutes=11)
    with pytest.raises(BudgetExceeded):
        AgentHarnessWorker._check_wall_budget(root)


@pytest.mark.asyncio
async def test_spawn_is_immediate_and_idempotent(monkeypatch) -> None:
    repository = HarnessRepository()
    root = _run("root")
    created: dict | None = None
    creates = 0

    async def get(_run_id: str):
        return created

    async def create(**fields):
        nonlocal created, creates
        creates += 1
        created = {**fields, "status": "queued"}
        return created

    monkeypatch.setattr(repository, "get", get)
    monkeypatch.setattr(repository, "_create", create)
    monkeypatch.setattr(repository, "_audit_run", AsyncMock())
    first = await repository.spawn(
        parent=root,
        agent_id="finance",
        agent_name="Finance Agent",
        objective="Check revenue",
        operation_id="finance:1",
    )
    second = await repository.spawn(
        parent=root,
        agent_id="finance",
        agent_name="Finance Agent",
        objective="Check revenue",
        operation_id="finance:1",
    )
    assert creates == 1
    assert first == second
    assert first["status"] == "queued"
    assert first["parent_run_id"] == first["root_run_id"] == "root"
    assert first["role_name"] == "analyst"
    assert first["depth"] == 1
    assert first["payload"]["agent_name"] == "Finance Agent"
    with pytest.raises(ValueError, match="collision"):
        await repository.spawn(
            parent=root,
            agent_id="finance",
            objective="A different task",
            operation_id="finance:1",
        )


@pytest.mark.asyncio
async def test_message_is_durable_and_consumed_once(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    rows: dict[str, list] = {}

    async def execute(sql: str, params=None):
        params = params or []
        if sql.startswith("SELECT message_id, recipient_run_id, message_type"):
            row = rows.get(params[0])
            existing = [row[0], row[6], row[2], row[4], row[5], row[3], row[8]] if row else None
            return {"rows": [existing] if existing else []}
        if sql.startswith("INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_MESSAGES"):
            rows[params[0]] = [
                params[0],
                params[2],
                params[4],
                params[7],
                params[5],
                params[6],
                params[3],
                None,
                params[9],
            ]
            return {"affected": 1}
        if sql.startswith("SELECT recipient_run_id, message_id, sender_run_id"):
            return {
                "rows": [[row[6], *row[:6], row[8]] for row in rows.values()
                         if row[6] == params[0] and row[7] is None]
            }
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_MESSAGES"):
            row = rows.get(params[1])
            if row and row[7] is None:
                row[7] = params[0]
                return {"affected": 1}
            return {"affected": 0}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    monkeypatch.setattr(module, "write_audit_log", AsyncMock(return_value="audit"))
    repository = HarnessRepository()
    repository.event = AsyncMock(return_value="event")
    repository.wake_parent = AsyncMock(return_value=True)
    child = _run("finance", depth=1)
    child["status"] = "running"
    root = _run("root", status="waiting_for_agent")
    first = await repository.send(
        sender=child,
        recipient=root,
        operation_id="finding:1",
        message_type="finding",
        content="Recognized revenue fell 22%.",
    )
    second = await repository.send(
        sender=child,
        recipient=root,
        operation_id="finding:1",
        message_type="finding",
        content="Recognized revenue fell 22%.",
    )
    assert first == second
    with pytest.raises(ValueError, match="operation id"):
        await repository.send(
            sender=child,
            recipient=root,
            operation_id="finding:1",
            message_type="finding",
            content="A conflicting finding.",
        )
    assert len(rows) == 1
    assert len(await repository.receive("root")) == 1
    assert await repository.receive("root") == []
    repository.wake_parent.assert_awaited_once_with("root")


@pytest.mark.asyncio
async def test_child_coordination_tools_use_the_runtime_sender_and_operation_id() -> None:
    child = _run("finance", depth=1, status="running")
    root = _run("root", status="waiting_for_agent")
    repository = type("Repository", (), {"send": AsyncMock(return_value="message-1")})()

    finding = await SendAgentMessageTool(repository, child, root).run(
        ToolInvocation("call-1", "send_agent_message", {
            "message_type": "finding", "content": "Revenue declined in Jakarta."
        }),
        None,
    )
    assert finding.ok and finding.data == {"message_id": "message-1"}
    repository.send.assert_awaited_with(
        sender=child,
        recipient=root,
        operation_id="call-1",
        message_type="finding",
        content="Revenue declined in Jakarta.",
    )

    request = await RequestSpecialistTool(repository, child, root).run(
        ToolInvocation("call-2", "request_specialist", {
            "capability": "marketing attribution", "reason": "Campaign traffic fell"
        }),
        None,
    )
    assert request.ok
    repository.send.assert_awaited_with(
        sender=child,
        recipient=root,
        operation_id="call-2",
        message_type="question",
        content="Specialist requested for marketing attribution: Campaign traffic fell",
    )


@pytest.mark.asyncio
async def test_message_cannot_cross_user_or_role_boundary() -> None:
    repo = HarnessRepository()
    root = _run("root")
    child = _run("finance", depth=1)
    child["owner_name"] = "bob"
    with pytest.raises(ValueError, match="security boundary"):
        await repo.send(
            sender=child,
            recipient=root,
            operation_id="leak",
            message_type="finding",
            content="Private result",
        )
    child["owner_name"] = "alice"
    child["role_name"] = "admin"
    with pytest.raises(ValueError, match="security boundary"):
        await repo.send(
            sender=child,
            recipient=root,
            operation_id="leak",
            message_type="finding",
            content="Private result",
        )


@pytest.mark.asyncio
async def test_wait_transition_and_wake_are_conditional(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    queries: list[str] = []

    async def execute(sql: str, params=None):
        queries.append(sql)
        return {"affected": 1}

    monkeypatch.setattr(module.db, "execute_system", execute)
    repository = HarnessRepository()
    assert await repository.transition(
        "root",
        from_status="running",
        to_status="waiting_for_agent",
        checkpoint={"phase": "coordinate"},
    )
    assert "status = %s" in queries[-1]
    assert "status = %s" in queries[-1].split("WHERE", 1)[1]
    with pytest.raises(ValueError):
        await repository.transition("root", from_status="completed", to_status="queued")


@pytest.mark.asyncio
async def test_fenced_transition_rejects_another_worker_lease(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    updates = []

    async def execute(sql: str, params=None):
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS"):
            updates.append((sql, params))
            return {"affected": 0}
        if sql.startswith("SELECT status, lease_owner, generation"):
            return {"rows": [["running", "new-lease", 3]]}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    assert not await repo.transition(
        "root",
        from_status="running",
        to_status="failed",
        lease_owner="old-lease",
        generation=2,
    )
    assert len(updates) == 1
    assert "lease_owner = %s AND generation = %s" in updates[0][0]
    assert updates[0][1][-4:] == ["root", "running", "old-lease", 2]
    with pytest.raises(ValueError, match="both lease owner and generation"):
        await repo.transition(
            "root", from_status="running", to_status="failed", lease_owner="old-lease"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("affected,expected", [(1, True), (0, False)])
async def test_owned_lease_probe_uses_fenced_update(monkeypatch, affected, expected) -> None:
    from app.modules.agents import harness_repository as module

    queries = []

    async def execute(sql: str, params=None):
        queries.append((sql, params))
        return {"affected": affected}

    monkeypatch.setattr(module.db, "execute_system", execute)
    assert await HarnessRepository().refresh_owned_lease(
        "root", lease_owner="own-lease", generation=2
    ) is expected
    assert len(queries) == 1
    sql, params = queries[0]
    assert "SET updated_at = IF(updated_at = %s, %s, %s)" in sql
    assert "status = 'running' AND lease_owner = %s AND generation = %s" in sql
    assert params[1] == params[0] + timedelta(seconds=1)
    assert params[2] == params[0]
    assert params[3:] == ["root", "own-lease", 2]


@pytest.mark.asyncio
async def test_worker_runs_two_children_concurrently(monkeypatch) -> None:
    children = {name: _run(name, depth=1) for name in ("finance", "marketing")}
    started: set[str] = set()
    both_started = asyncio.Event()
    release = asyncio.Event()

    class Repository:
        async def claim(self, run_id, worker_id):
            children[run_id]["status"] = "running"
            children[run_id]["lease_owner"] = worker_id
            return True

        async def get(self, run_id):
            return children[run_id]

        async def event(self, *args):
            return "event"

        async def heartbeat(self, *args):
            return None

    worker = AgentHarnessWorker(Repository())
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value={"username": "alice"}))

    async def execute(child, user, cancelled):
        started.add(child["run_id"])
        if len(started) == 2:
            both_started.set()
        await release.wait()

    monkeypatch.setattr(worker, "_execute_child", execute)
    tasks = [asyncio.create_task(worker.process(name, "worker")) for name in children]
    await asyncio.wait_for(both_started.wait(), timeout=1)
    assert started == {"finance", "marketing"}
    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_worker_waits_for_its_new_lease_before_using_a_checkpoint(monkeypatch) -> None:
    run = _run("finance", depth=1, status="queued")
    run["checkpoint"] = {"phase": "new"}
    seen: list[dict] = []

    class Repository:
        lease_id: str = ""
        reads = 0

        async def claim(self, run_id, worker_id):
            self.lease_id = worker_id
            return True

        async def get(self, run_id):
            self.reads += 1
            if self.reads == 1:
                return {**run, "status": "running", "lease_owner": "old-lease",
                        "checkpoint": {"phase": "old"}}
            return {**run, "status": "running", "lease_owner": self.lease_id}

        async def event(self, *args):
            return "0"

        async def heartbeat(self, *args):
            return None

    repository = Repository()
    worker = AgentHarnessWorker(repository)
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value={"username": "alice"}))

    async def execute(child, user, cancelled):
        seen.append(child["checkpoint"])

    monkeypatch.setattr(worker, "_execute_child", execute)
    await worker.process("finance", "worker")
    assert repository.reads >= 2
    assert seen == [{"phase": "new"}]


@pytest.mark.asyncio
async def test_worker_rechecks_a_stale_lease_snapshot_after_planning(monkeypatch) -> None:
    from app.modules.agents import harness_worker as module

    run = _run("root", status="running")
    reads = iter([{**run, "generation": 0}, run])
    repository = SimpleNamespace(get=AsyncMock(side_effect=lambda *_: next(reads)))
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    await AgentHarnessWorker(repository)._assert_running(run)
    sleep.assert_awaited_once_with(0.1)


@pytest.mark.asyncio
async def test_worker_accepts_owned_lease_after_invisible_snapshots(monkeypatch) -> None:
    from app.modules.agents import harness_worker as module

    run = _run("root", status="running")
    repository = SimpleNamespace(
        get=AsyncMock(return_value=None),
        refresh_owned_lease=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    await AgentHarnessWorker(repository)._assert_running(run)
    assert repository.get.await_count == 5
    repository.refresh_owned_lease.assert_awaited_once_with(
        "root", lease_owner="worker", generation=1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("depth", [0, 1])
async def test_lost_lease_exits_without_failing_new_worker_run(monkeypatch, depth) -> None:
    run_id = "root" if depth == 0 else "sales"
    run = _run(run_id, depth=depth, status="running")
    run.update(lease_owner="old-lease", generation=2)
    reads = 0
    events = []
    probe = AsyncMock(return_value=False)

    class Repository:
        transition = AsyncMock()

        async def get(self, _run_id):
            nonlocal reads
            reads += 1
            if reads == 1:
                return run
            return {**run, "lease_owner": "new-lease", "generation": 3}

        async def event(self, _root_id, _run_id, kind, _payload):
            events.append(kind)

        async def heartbeat(self, *_args):
            return None

        async def refresh_owned_lease(self, run_id, **kwargs):
            return await probe(run_id, **kwargs)

    repository = Repository()
    worker = AgentHarnessWorker(repository)
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value={"username": "alice"}))

    async def check_lease(item, *_args):
        await worker._assert_running(item)

    monkeypatch.setattr(worker, "_coordinate" if depth == 0 else "_execute_child", check_lease)
    await worker._process_claimed(run_id, "old-lease")
    assert reads >= 6
    assert events == ["agent_started"]
    probe.assert_awaited_once_with(run_id, lease_owner="old-lease", generation=2)
    repository.transition.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("depth", [0, 1])
async def test_worker_failure_uses_its_lease_fence(monkeypatch, depth) -> None:
    run_id = "root" if depth == 0 else "sales"
    run = _run(run_id, depth=depth, status="running")
    run.update(lease_owner="own-lease", generation=2)
    events = []
    transition = AsyncMock(return_value=False)

    class Repository:
        async def get(self, _run_id):
            return run

        async def event(self, _root_id, _run_id, kind, _payload):
            events.append(kind)

        async def heartbeat(self, *_args):
            return None

        async def transition(self, run_id, **kwargs):
            return await transition(run_id, **kwargs)

    worker = AgentHarnessWorker(Repository())
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value={"username": "alice"}))

    async def fail(*_args):
        raise RuntimeError("Provider failed")

    monkeypatch.setattr(worker, "_coordinate" if depth == 0 else "_execute_child", fail)
    await worker._process_claimed(run_id, "own-lease")
    assert transition.await_args.kwargs["lease_owner"] == "own-lease"
    assert transition.await_args.kwargs["generation"] == 2
    assert transition.await_args.kwargs["to_status"] == "failed"
    assert events == ["agent_started"]


@pytest.mark.asyncio
async def test_unrecorded_child_start_requeues_before_running_tools(monkeypatch) -> None:
    from app.modules.agents import harness_worker as module

    child = _run("sales", depth=1, status="running")
    repository = SimpleNamespace(
        get=AsyncMock(return_value=child),
        event=AsyncMock(side_effect=ValueError("Auto root no longer exists")),
        heartbeat=AsyncMock(),
        transition=AsyncMock(return_value=True),
    )
    worker = AgentHarnessWorker(repository)
    execute = AsyncMock()
    monkeypatch.setattr(worker, "_execute_child", execute)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())

    await worker._process_claimed("sales", "worker")

    assert repository.event.await_count == 3
    repository.transition.assert_awaited_once()
    transition = repository.transition.await_args
    assert transition.kwargs["from_status"] == "running"
    assert transition.kwargs["to_status"] == "queued"
    assert transition.kwargs["lease_owner"] == "worker"
    assert transition.kwargs["generation"] == 1
    assert transition.kwargs["checkpoint"]["start_event_attempts"] == 1
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unrecorded_child_start_fails_after_bounded_retries(monkeypatch) -> None:
    from app.modules.agents import harness_worker as module

    child = _run("sales", depth=1, status="running")
    child["checkpoint"]["start_event_attempts"] = 5
    repository = SimpleNamespace(
        get=AsyncMock(return_value=child),
        event=AsyncMock(side_effect=ValueError("Auto root no longer exists")),
        heartbeat=AsyncMock(),
        transition=AsyncMock(return_value=True),
        wake_parent=AsyncMock(),
    )
    worker = AgentHarnessWorker(repository)
    execute = AsyncMock()
    monkeypatch.setattr(worker, "_execute_child", execute)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())

    await worker._process_claimed("sales", "worker")

    assert repository.transition.await_args.kwargs["to_status"] == "failed"
    assert repository.transition.await_args.kwargs["error_class"] == "start_event_unavailable"
    repository.wake_parent.assert_awaited_once_with("root")
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_rejects_role_change(monkeypatch) -> None:
    from app.modules.agents import harness_worker as module

    worker = AgentHarnessWorker()
    session = {"username": "alice", "active_role": "marketing", "security_context_version": 1}
    monkeypatch.setattr(module.session_store, "get", AsyncMock(return_value=session))
    with pytest.raises(AuthenticationUnavailable):
        await worker._user_for(_run("root"))


@pytest.mark.asyncio
async def test_specialist_preserves_authorized_table_when_draft_fails_verification(
    monkeypatch,
) -> None:
    from app.modules.agents import harness_worker as module

    root = _run("root", status="waiting_for_agent")
    child = _run("sales", depth=1, status="running")
    child.update(prompt_tokens=0, completion_tokens=0, result_summary=None)
    saved: dict = {}
    emitted: list[tuple[str, dict]] = []

    class Repository:
        async def get(self, run_id):
            return {"root": root, "sales": child}.get(run_id)

        async def tree(self, *args, **kwargs):
            return [root, child]

        async def pending_messages(self, *args, **kwargs):
            return [{
                "message_id": "m1", "origin": "user", "content": "Use the 2025 period"
            }]

        async def transition(self, run_id, **kwargs):
            saved.update(kwargs)
            return True

        async def acknowledge_messages(self, *args, **kwargs):
            return None

        async def event(self, _root, _run, kind, payload):
            emitted.append((kind, payload))
            return "1"

        async def wake_parent(self, *args, **kwargs):
            return True

    class FakeLoop:
        def __init__(self, **kwargs):
            pass

        async def run(self, **kwargs):
            await kwargs["on_checkpoint"]()
            yield (
                'event: plan\ndata: {"steps":[{"id":"query","text":"Check channels",'
                '"status":"running"}]}\n\n'
            )
            yield (
                'event: thinking\ndata: {"phase":"act","text":"Choosing a query",'
                '"status":"done"}\n\n'
            )
            yield (
                'event: tool_call\ndata: {"tool_call_id":"c1",'
                '"tool_name":"semantic_query","status":"running"}\n\n'
            )
            yield 'event: tool_status\ndata: {"tool_call_id":"c1","status":"done"}\n\n'
            yield (
                'event: tool_detail\ndata: {"tool_call_id":"c1",'
                '"text":"Five channels returned"}\n\n'
            )
            yield (
                'event: text_delta\ndata: {"text":"I could not verify every number '
                'in the drafted answer."}\n\n'
            )
            for index in range(3):
                yield (
                    'event: table\ndata: {"columns":["channel","revenue"],'
                    f'"rows":[["Exploration {index}","{index}"]]}}\n\n'
                )
            yield (
                'event: table\ndata: {"columns":["sales_channel","recognized_revenue"],'
                '"rows":[["Mobile App","3368049065451.00"]]}\n\n'
            )
            yield 'event: done\ndata: {"finish_reason":"stop"}\n\n'

    agent = {"agent_id": "sales", "owner_name": "alice", "policy": "auto_read_only"}
    user = {"username": "alice", "active_role": "analyst", "assigned_roles": ["analyst"]}
    monkeypatch.setattr(module.agent_repository, "get_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(module.agent_service, "build_loop_inputs", AsyncMock(
        return_value=(ToolRegistry(), "system", 30, 1000)
    ))
    monkeypatch.setattr(module.memory_repository, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "has_verified_access", AsyncMock(return_value=True))
    monkeypatch.setattr(module, "AssistantLoop", FakeLoop)
    worker = AgentHarnessWorker(Repository())
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value=user))

    await worker._execute_child(child, user, asyncio.Event())

    assert saved["to_status"] == "completed"
    assert saved["lease_owner"] == "worker"
    assert saved["generation"] == 1
    assert saved["checkpoint"]["evidence_tables"][-1]["rows"] == [
        ["Mobile App", "3368049065451.00"]
    ]
    assert saved["checkpoint"]["evidence_tables_omitted"] is True
    assert "| Mobile App | 3368049065451.00 |" in saved["summary"]
    assert "could not verify" not in saved["summary"]
    activity = [payload for kind, payload in emitted if kind == "child_activity"]
    assert [item["event_type"] for item in activity[:7]] == [
        "thinking", "thinking", "plan", "thinking", "tool_call", "tool_status",
        "tool_detail",
    ]
    assert "Access verified" in activity[0]["text"]
    assert "12 steps" in activity[0]["text"]
    assert "Read 1 new coordination message" in activity[1]["text"]
    assert any(item["event_type"] == "answer" for item in activity)
    assert all("Use the 2025 period" not in str(item) for item in activity)


@pytest.mark.asyncio
async def test_session_events_get_unique_sequences_under_concurrent_writers(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    monkeypatch.setattr(module.session_store, "_redis", _EventRedis())
    saved: list[tuple[str, int, str]] = []

    async def execute(sql: str, params=None):
        if sql.startswith("SELECT last_sequence"):
            await asyncio.sleep(0)
            return {"rows": [[-1]]}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET last_sequence"):
            return {"affected": 1}
        if sql.startswith("INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS"):
            saved.append((params[0], params[2], params[4]))
            return {"affected": 1}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    await asyncio.gather(
        *(
            repo.event("root", name, "agent_message", {"from": name})
            for name in ("finance", "marketing", "finance", "marketing")
        )
    )
    assert len(saved) == 4
    assert len({event_id for event_id, _, _ in saved}) == 4
    sequences = [sequence for _, sequence, _ in saved]
    assert sequences == sorted(set(sequences))
    assert all(sequence * 3 + 2 <= 2**53 - 1 for sequence in sequences)


@pytest.mark.asyncio
async def test_event_retries_a_temporarily_invisible_root(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    monkeypatch.setattr(module.session_store, "_redis", _EventRedis())
    reads = 0
    inserted = []

    async def execute(sql: str, params=None):
        nonlocal reads
        if sql.startswith("SELECT last_sequence"):
            reads += 1
            return {"rows": [] if reads == 1 else [[-1]]}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET last_sequence"):
            return {"affected": 1}
        if sql.startswith("INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS"):
            inserted.append(params)
            return {"affected": 1}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    repo = HarnessRepository()
    repo._synced_terminal_rows = AsyncMock(return_value=[])
    assert int(await repo.event("root", "child", "agent_interrupted", {})) > 0
    assert reads == 2
    assert len(inserted) == 1
    assert sleep.await_count >= 1
    assert sleep.await_args_list[0].args == (0.05,)


@pytest.mark.asyncio
async def test_terminal_events_are_idempotent_and_reconciled_before_root_completion(
    monkeypatch,
) -> None:
    from app.modules.agents import harness_repository as module

    monkeypatch.setattr(module.session_store, "_redis", _EventRedis())
    saved: dict[str, list] = {}

    async def execute(sql: str, params=None):
        if sql.startswith("SELECT last_sequence"):
            return {"rows": [[-1]]}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET last_sequence"):
            return {"affected": 1}
        if sql.startswith("INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS"):
            await asyncio.sleep(0)
            saved[params[0]] = list(params)
            return {"affected": 1}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    async def terminal_rows(_root_id: str) -> list[list]:
        return [
            [row[3], row[4], row[2]]
            for row in saved.values()
            if row[1] == "root" and row[4] in module.TERMINAL_EVENT_TYPES
        ]

    repo._synced_terminal_rows = terminal_rows
    finance = _run("finance", depth=1, status="completed")
    finance["result_summary"] = "Finance result"
    marketing = _run("marketing", depth=1, status="completed")
    marketing["result_summary"] = "Marketing result"
    first = await repo.event(
        "root", "marketing", "agent_completed", {"summary": "Marketing result"}
    )
    await repo.reconcile_terminal_children("root", [finance, marketing])
    await repo.event("root", "root", "agent_completed", {"answer": "Final answer"})
    assert await repo.event(
        "root", "marketing", "agent_completed", {"summary": "Marketing result"}
    ) == first
    ordered = sorted(saved.values(), key=lambda row: row[2])
    assert [(row[3], row[4]) for row in ordered] == [
        ("marketing", "agent_completed"),
        ("finance", "agent_completed"),
        ("root", "agent_completed"),
    ]
    assert len({row[2] for row in saved.values()}) == 3


@pytest.mark.asyncio
async def test_terminal_event_retry_reuses_sequence_when_insert_is_temporarily_invisible(
    monkeypatch,
) -> None:
    from app.modules.agents import harness_repository as module

    monkeypatch.setattr(module.session_store, "_redis", _EventRedis())
    inserted: list[list] = []

    async def execute(sql: str, params=None):
        if sql.startswith("SELECT last_sequence"):
            return {"rows": [[-1]]}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET last_sequence"):
            return {"affected": 1}
        if sql.startswith("INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS"):
            inserted.append(list(params))
            return {"affected": 1}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    repo._synced_terminal_rows = AsyncMock(return_value=[])
    first = await repo.event("root", "finance", "agent_completed", {"summary": "Done"})
    second = await repo.event("root", "finance", "agent_completed", {"summary": "Done"})
    assert first == second
    assert inserted[0][:5] == inserted[1][:5]


@pytest.mark.asyncio
async def test_terminal_replay_keeps_old_sequence_after_redis_restart_and_empty_read(
    monkeypatch,
) -> None:
    from app.modules.agents import harness_repository as module
    from app.modules.agents import router as agent_router

    redis = _EventRedis()
    monkeypatch.setattr(module.session_store, "_redis", redis)
    original_sequence = 123
    terminal_reads = 0
    writes = 0

    async def execute(sql: str, params=None):
        nonlocal writes
        if sql.startswith("SELECT last_sequence"):
            return {"rows": [[original_sequence]]}
        if sql.startswith((
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET last_sequence",
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS",
        )):
            writes += 1
            raise AssertionError("Replay must not rewrite a durable terminal event")
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    async def terminal_rows(_root_id: str) -> list[list]:
        nonlocal terminal_reads
        terminal_reads += 1
        return [] if terminal_reads == 1 else [
            ["root", "agent_completed", original_sequence]
        ]

    repo._synced_terminal_rows = terminal_rows
    assert await repo.event("root", "root", "agent_completed", {"answer": "Final answer"}) == "123"
    assert terminal_reads == 2
    assert writes == 0
    assert redis.hashes == {}

    async def page(root_run_id: str, after: int, *, ensure_complete: bool):
        assert root_run_id == "root" and ensure_complete
        return [{
            "event_id": original_sequence,
            "run_id": "root",
            "type": "agent_completed",
            "payload": {"answer": "Final answer"},
        }] if after < original_sequence else []

    monkeypatch.setattr(agent_router.harness_repository, "events_page", page)
    frames = [frame async for frame in agent_router._stream_auto_events("root", -1)]
    assert sum(frame.startswith("event: agent_completed") for frame in frames) == 1
    assert sum(frame.startswith("event: text_delta") for frame in frames) == 1
    assert sum(frame.startswith("event: done") for frame in frames) == 1


@pytest.mark.asyncio
async def test_paged_child_timeline_survives_malformed_terminal_replay(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    redis = _EventRedis()
    redis.hashes["nova:auto:event:terminal:root"] = {
        "child": "agent_completed:11", "root": "agent_completed:12"
    }
    monkeypatch.setattr(module.session_store, "_redis", redis)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    repo = HarnessRepository()
    root = {
        **_run("root", status="completed"),
        "checkpoint": {"child_run_ids": ["child"]},
    }
    child = {**_run("child", depth=1, status="completed"), "result_summary": "Done"}
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=root))
    monkeypatch.setattr(repo, "tree", AsyncMock(return_value=[root, child]))
    terminal_reads = 0

    async def terminal_rows(_root_id: str) -> list[list]:
        nonlocal terminal_reads
        terminal_reads += 1
        return [["wrong-root"]] if terminal_reads == 1 else [
            ["child", "agent_completed", 11], ["root", "agent_completed", 12]
        ]

    monkeypatch.setattr(repo, "_synced_terminal_rows", terminal_rows)
    final_id = str(uuid5(NAMESPACE_URL, "nova:auto:final:root"))
    monkeypatch.setattr(
        module.assistant_repository, "list_messages",
        AsyncMock(return_value=[{
            "message_id": final_id, "role": "assistant", "content": "Final answer"
        }]),
    )
    writes = 0

    async def execute(sql: str, params=None):
        nonlocal writes
        if sql.startswith("SELECT last_sequence"):
            return {"rows": [[12]]}
        if sql.startswith((
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET last_sequence",
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS",
        )):
            writes += 1
            raise AssertionError("A malformed terminal replay must not rewrite events")
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    now = datetime.now(UTC)
    event_rows = [
        ["root", 10, "child", "agent_started", "{}", now],
        ["root", 11, "child", "agent_completed", "{}", now],
        ["root", 12, "root", "agent_completed", "{}", now],
    ]

    async def completed_page(root_run_id: str, after: int, limit: int):
        assert root_run_id == "root"
        return (
            [row for row in event_rows if row[1] > after][:limit],
            [["child", "agent_completed", 11], ["root", "agent_completed", 12]],
        )

    monkeypatch.setattr(HarnessRepository, "_synced_completed_event_rows", completed_page)
    first, cursor, more = await repo.child_events_page("root", "child", -1, limit=1)
    second, final_cursor, more_after = await repo.child_events_page(
        "root", "child", cursor, limit=1
    )

    assert [event["type"] for event in first] == ["agent_started"]
    assert [event["type"] for event in second] == ["agent_completed"]
    assert (cursor, final_cursor, more, more_after) == (10, 11, True, True)
    assert terminal_reads >= 2
    assert writes == 0
    assert "nova:auto:event:sequence:root" not in redis.values


@pytest.mark.asyncio
async def test_terminal_event_recovers_after_one_malformed_synced_lookup(
    monkeypatch,
) -> None:
    from app.modules.agents import harness_repository as module

    redis = _EventRedis()
    monkeypatch.setattr(module.session_store, "_redis", redis)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    reads = 0
    inserted: list[list] = []

    async def execute(sql: str, params=None):
        if sql.startswith("SELECT last_sequence"):
            return {"rows": [[12]]}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET last_sequence"):
            return {"affected": 1}
        if sql.startswith("INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS"):
            inserted.append(list(params))
            return {"affected": 1}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()

    async def terminal_rows(_root_id: str) -> list[list]:
        nonlocal reads
        reads += 1
        return [["wrong-root"]] if reads == 1 else []

    repo._synced_terminal_rows = terminal_rows
    sequence = await repo.event("root", "root", "agent_cancelled", {})
    assert int(sequence) > 12
    assert reads == 8
    assert len(inserted) == 1
    assert inserted[0][3:5] == ["root", "agent_cancelled"]


@pytest.mark.asyncio
async def test_uncertain_terminal_lookup_without_cache_never_allocates_sequence(
    monkeypatch,
) -> None:
    from app.modules.agents import harness_repository as module

    redis = _EventRedis()
    monkeypatch.setattr(module.session_store, "_redis", redis)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    writes = 0
    terminal_reads = 0

    async def execute(sql: str, params=None):
        nonlocal writes
        if sql.startswith("SELECT last_sequence"):
            return {"rows": [[12]]}
        if sql.startswith((
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET last_sequence",
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS",
        )):
            writes += 1
            raise AssertionError("An uncertain terminal read must not write")
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    async def terminal_rows(_root_id: str) -> list[list]:
        nonlocal terminal_reads
        terminal_reads += 1
        return [] if terminal_reads % 2 else [["wrong-root"]]

    repo._synced_terminal_rows = terminal_rows
    with pytest.raises(RuntimeError, match="terminal lookup returned inconsistent rows"):
        await repo.event("root", "child", "agent_completed", {})
    assert terminal_reads == 20
    assert writes == 0
    assert "nova:auto:event:sequence:root" not in redis.values


@pytest.mark.asyncio
async def test_synced_terminal_lookup_rejects_conflicting_saved_kind(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    monkeypatch.setattr(module.session_store, "_redis", _EventRedis())

    async def execute(sql: str, params=None):
        if sql.startswith("SELECT last_sequence"):
            return {"rows": [[12]]}
        raise AssertionError("Conflicting terminal must not write")

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    repo._synced_terminal_rows = AsyncMock(return_value=[
        ["root", "agent_completed", 12]
    ])
    with pytest.raises(ValueError, match="Conflicting Auto terminal event"):
        await repo.event("root", "root", "agent_cancelled", {})


@pytest.mark.asyncio
async def test_event_lock_renews_during_a_slow_starrocks_write(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    redis = _EventRedis()
    monkeypatch.setattr(module.session_store, "_redis", redis)
    monkeypatch.setattr(module, "EVENT_LOCK_RENEW_SECONDS", 0.001)
    inserted = []

    async def execute(sql: str, params=None):
        if sql.startswith("SELECT last_sequence"):
            return {"rows": [[-1]]}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET last_sequence"):
            await asyncio.sleep(0.01)
            return {"affected": 1}
        if sql.startswith("INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS"):
            inserted.append(params)
            return {"affected": 1}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    await HarnessRepository().event("root", "finance", "agent_message", {})
    assert inserted
    assert redis.locks["nova:auto:event:lock:root"].extensions >= 1


@pytest.mark.asyncio
async def test_delete_thread_clears_ephemeral_event_coordination(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    redis = _EventRedis()
    redis.values["nova:auto:event:sequence:root"] = 4
    redis.hashes["nova:auto:event:terminal:root"] = {"finance": "agent_completed:3"}
    monkeypatch.setattr(module.session_store, "_redis", redis)

    async def execute(sql: str, params=None):
        if sql.startswith("SELECT run_id FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS"):
            return {"rows": [["root"]]}
        if sql.startswith("DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_"):
            return {"affected": 1}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    await HarnessRepository().delete_thread("thread", owner_name="alice")
    assert redis.values == {"nova:auto:event:deleted:root": "1"}
    assert redis.hashes == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("root_affected", [0, 1])
async def test_cancel_tree_only_cancels_children_after_active_root_changes(
    monkeypatch, root_affected
) -> None:
    from app.modules.agents import harness_repository as module

    updates = []

    async def execute(sql: str, params=None):
        updates.append((sql, params))
        return {"affected": root_affected if len(updates) == 1 else 2}

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    repo.get = AsyncMock(return_value={
        "run_id": "root", "agent_id": "__auto__", "depth": 0,
        "status": "completed",
    })
    assert await repo.cancel_tree("root") is bool(root_affected)
    assert "run_id = %s AND agent_id = '__auto__' AND depth = 0" in updates[0][0]
    assert len(updates) == (2 if root_affected else 1)
    if root_affected:
        assert "root_run_id = %s AND depth = 1" in updates[1][0]


@pytest.mark.asyncio
async def test_cancel_tree_continues_when_update_applies_with_zero_affected(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    updates: list[str] = []

    async def execute(sql: str, params=None):
        updates.append(sql)
        return {"affected": 0 if len(updates) < 3 else 1}

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    repo.get = AsyncMock(side_effect=[
        {"run_id": "root", "agent_id": "__auto__", "depth": 0,
         "status": "running"},
        {"run_id": "root", "agent_id": "__auto__", "depth": 0,
         "status": "cancelled"},
    ])
    assert await repo.cancel_tree("root")
    assert len(updates) == 3
    assert all("run_id = %s AND agent_id = '__auto__'" in sql for sql in updates[:2])
    assert "root_run_id = %s AND depth = 1" in updates[2]


@pytest.mark.asyncio
async def test_cancel_child_accepts_verified_zero_affected_and_rejects_other_tree(
    monkeypatch,
) -> None:
    from app.modules.agents import harness_repository as module

    updates: list[tuple[str, list]] = []

    async def execute(sql: str, params=None):
        updates.append((sql, params))
        return {"affected": 0}

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    repo.get = AsyncMock(return_value={
        "run_id": "child", "root_run_id": "root", "depth": 1,
        "status": "cancelled",
    })
    assert await repo.cancel_child("root", "child")
    assert "run_id = %s AND root_run_id = %s AND depth = 1" in updates[0][0]
    assert updates[0][1][1:] == ["child", "root"]

    repo.get = AsyncMock(return_value={
        "run_id": "child", "root_run_id": "other-root", "depth": 1,
        "status": "cancelled",
    })
    assert not await repo.cancel_child("root", "child")


@pytest.mark.asyncio
async def test_run_lookup_retries_a_mismatched_starrocks_row(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    def row(run_id: str, agent_id: str, depth: int) -> list:
        values = {key: None for key in module.RUN_KEYS}
        values.update(
            run_id=run_id,
            agent_id=agent_id,
            depth=depth,
            payload="{}",
            checkpoint="{}",
        )
        return [values[key] for key in module.RUN_KEYS]

    reads = iter([[row("child", "specialist", 1)], [row("root", "__auto__", 0)]])
    repository = HarnessRepository()

    async def run_rows(sql, params):
        return next(reads)

    monkeypatch.setattr(repository, "_run_rows", run_rows)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    assert (await repository.get("root"))["agent_id"] == "__auto__"


@pytest.mark.asyncio
async def test_event_replay_retries_a_mismatched_starrocks_row(monkeypatch) -> None:
    now = datetime.now(UTC)
    reads = iter(
        [
            [["root", 0, "root", "Two-agent live smoke test", "{}", now]],
            [["root", 0, "root", "delegation_plan", "{}", now]],
        ]
    )
    monkeypatch.setattr(
        HarnessRepository,
        "_synced_event_rows",
        AsyncMock(side_effect=lambda *_: next(reads)),
    )
    from app.modules.agents import harness_repository as module

    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    events = await HarnessRepository().events_after("root")
    assert [(item["event_id"], item["type"]) for item in events] == [
        (0, "delegation_plan")
    ]


@pytest.mark.asyncio
async def test_event_replay_accepts_specialist_tool_activity(monkeypatch) -> None:
    now = datetime.now(UTC)
    execute = AsyncMock(
        return_value=[
            ["root", 4, "finance", "tool_activity", '{"tool_name":"query_execute"}', now]
        ]
    )
    monkeypatch.setattr(HarnessRepository, "_synced_event_rows", execute)

    events = await HarnessRepository().events_after("root", "3")

    assert events == [
        {
            "event_id": 4,
            "run_id": "finance",
            "type": "tool_activity",
            "payload": {"tool_name": "query_execute"},
            "created_at": now,
        }
    ]
    assert execute.await_count == 1


@pytest.mark.asyncio
async def test_completed_root_repairs_missing_terminal_events_from_saved_state(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    root = _run("root", status="completed")
    root["checkpoint"] = {"child_run_ids": ["finance", "marketing"]}
    finance = _run("finance", depth=1, status="completed")
    marketing = _run("marketing", depth=1, status="failed")
    marketing["error_class"] = "ToolError"
    repo = HarnessRepository()
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=root))
    monkeypatch.setattr(repo, "tree", AsyncMock(return_value=[root, finance, marketing]))
    monkeypatch.setattr(repo, "_synced_terminal_rows", AsyncMock(return_value=[]))
    final_id = str(uuid5(NAMESPACE_URL, "nova:auto:final:root"))
    monkeypatch.setattr(module.assistant_repository, "list_messages", AsyncMock(
        return_value=[{"message_id": final_id, "role": "assistant", "content": "Final answer"}]
    ))
    emitted: list[tuple[str, str, dict]] = []

    async def event(_root_id, run_id, kind, payload):
        emitted.append((run_id, kind, payload))
        return str(len(emitted))

    monkeypatch.setattr(repo, "event", event)
    page = AsyncMock(return_value=[])
    monkeypatch.setattr(repo, "_events", page)

    await repo.events_after("root", ensure_complete=True)

    page.assert_awaited_once_with("root", -1, 100, {
        "finance": "agent_completed", "marketing": "agent_failed", "root": "agent_completed"
    })
    assert [(run_id, kind) for run_id, kind, _ in emitted] == [
        ("finance", "agent_completed"), ("marketing", "agent_failed"),
        ("root", "agent_completed"),
    ]
    assert emitted[-1][2] == {"answer": "Final answer"}
    assert module.assistant_repository.list_messages.await_args.kwargs["synchronize"] is True


@pytest.mark.asyncio
async def test_completed_root_with_full_terminal_journal_performs_no_event_writes(
    monkeypatch,
) -> None:
    root = _run("root", status="completed")
    root["checkpoint"] = {"child_run_ids": ["finance", "marketing"]}
    repo = HarnessRepository()
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=root))
    monkeypatch.setattr(repo, "_synced_terminal_rows", AsyncMock(return_value=[
        ["finance", "agent_completed", 10],
        ["marketing", "agent_failed", 11],
        ["root", "agent_completed", 12],
    ]))
    tree = AsyncMock()
    event = AsyncMock()
    monkeypatch.setattr(repo, "tree", tree)
    monkeypatch.setattr(repo, "event", event)
    page = AsyncMock(return_value=[])
    monkeypatch.setattr(repo, "_events", page)

    await repo.events_after("root", ensure_complete=True)

    page.assert_awaited_once_with("root", -1, 100, {
        "finance": "agent_completed", "marketing": "agent_failed", "root": "agent_completed"
    })
    tree.assert_not_awaited()
    event.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_root_replay_repairs_child_before_root_terminal(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    root = _run("root", status="cancelled")
    root["checkpoint"] = {"child_run_ids": ["child"]}
    child = _run("child", depth=1, status="cancelled")
    repo = HarnessRepository()
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=root))
    monkeypatch.setattr(repo, "tree", AsyncMock(return_value=[root, child]))
    monkeypatch.setattr(repo, "_synced_terminal_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    emitted: list[tuple[str, str, dict]] = []

    async def event(_root_id: str, run_id: str, kind: str, payload: dict) -> str:
        emitted.append((run_id, kind, payload))
        return str(len(emitted))

    monkeypatch.setattr(repo, "event", event)
    page = AsyncMock(return_value=[])
    monkeypatch.setattr(repo, "_events", page)

    await repo.events_after("root", ensure_complete=True)

    assert emitted == [
        ("child", "agent_cancelled", {}),
        ("root", "agent_cancelled", {}),
    ]
    page.assert_awaited_once_with("root", -1, 100, {"root": "agent_cancelled"})


@pytest.mark.asyncio
@pytest.mark.parametrize("status, kind", [
    ("failed", "agent_failed"), ("cancelled", "agent_cancelled"),
])
async def test_repaired_root_terminal_ends_sse(monkeypatch, status: str, kind: str) -> None:
    from app.modules.agents import harness_repository as module
    from app.modules.agents import router as agent_router

    root = _run("root", status=status)
    root["error_class"] = "ToolError"
    repo = HarnessRepository()
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=root))
    monkeypatch.setattr(repo, "_synced_terminal_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "reconcile_cancelled_children", AsyncMock())
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    event = AsyncMock(return_value="10")
    monkeypatch.setattr(repo, "event", event)
    monkeypatch.setattr(repo, "_events", AsyncMock(return_value=[{
        "event_id": 10, "run_id": "root", "type": kind, "payload": {},
    }]))
    monkeypatch.setattr(agent_router.harness_repository, "events_page", repo.events_page)

    frames = [frame async for frame in agent_router._stream_auto_events("root", -1)]

    assert event.await_args.args[:3] == ("root", "root", kind)
    assert event.await_args.args[3] == (
        {"error_class": "ToolError"} if status == "failed" else {}
    )
    assert sum(frame.startswith("event: error") for frame in frames) == 1
    assert sum(frame.startswith("event: done") for frame in frames) == 1


@pytest.mark.asyncio
async def test_terminal_replay_keeps_matching_event_and_rejects_conflict(monkeypatch) -> None:
    repo = HarnessRepository()
    root = _run("root", status="failed")
    event = AsyncMock()
    monkeypatch.setattr(repo, "event", event)
    rows = AsyncMock(return_value=[["root", "agent_failed", 10]])
    monkeypatch.setattr(repo, "_synced_terminal_rows", rows)

    await repo.ensure_terminal_event("root", root)
    event.assert_not_awaited()

    rows.return_value = [["root", "agent_completed", 10]]
    with pytest.raises(RuntimeError, match="disagrees with run status"):
        await repo.ensure_terminal_event("root", root)
    event.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_event_page_waits_for_all_well_shaped_terminals(monkeypatch) -> None:
    now = datetime.now(UTC)
    child = ["root", 10, "finance", "agent_completed", "{}", now]
    final = ["root", 11, "root", "agent_completed", '{"answer":"Done"}', now]
    read = AsyncMock(side_effect=[
        ([child], [["finance", "agent_completed", 10]]),
        ([child, final], [["finance", "agent_completed", 10],
                          ["root", "agent_completed", 11]]),
    ])
    monkeypatch.setattr(HarnessRepository, "_synced_completed_event_rows", read)
    from app.modules.agents import harness_repository as module

    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    events = await HarnessRepository._events(
        "root", -1, 100, {"finance": "agent_completed", "root": "agent_completed"}
    )

    assert [(item["run_id"], item["type"]) for item in events] == [
        ("finance", "agent_completed"), ("root", "agent_completed")
    ]
    assert read.await_count == 2


@pytest.mark.asyncio
async def test_completed_event_page_rejects_conflicting_terminal(monkeypatch) -> None:
    now = datetime.now(UTC)
    read = AsyncMock(return_value=(
        [["root", 11, "root", "agent_completed", "{}", now]],
        [["root", "agent_cancelled", 11]],
    ))
    monkeypatch.setattr(HarnessRepository, "_synced_completed_event_rows", read)

    with pytest.raises(RuntimeError, match="disagrees with run status"):
        await HarnessRepository._events("root", -1, 100, {"root": "agent_completed"})


@pytest.mark.asyncio
async def test_event_replay_syncs_and_selects_on_one_starrocks_session(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    calls = []

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, sql, params=None):
            calls.append((sql, params))

        async def fetchall(self):
            return [("root", 1, "finance", "agent_completed", "{}", datetime.now(UTC))]

    class Connection:
        def cursor(self):
            return Cursor()

    @asynccontextmanager
    async def system_conn():
        yield Connection()

    monkeypatch.setattr(module.db, "system_conn", system_conn)
    rows = await HarnessRepository._synced_event_rows("root", -1, 100)
    assert rows[0][:4] == ["root", 1, "finance", "agent_completed"]
    assert calls[0] == ("SYNC", None)
    assert "FROM NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS" in calls[1][0]
    assert calls[1][1] == ["root", -1, 100]


@pytest.mark.asyncio
async def test_message_replay_retries_a_short_starrocks_row(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    now = datetime.now(UTC)
    reads = iter(
        [
            {"rows": [["root", "message-1"]]},
            {"rows": [["root", "message-1", "child", "root", "finding", None,
                       None, "A bounded finding", now, now, "agent"]]},
        ]
    )
    monkeypatch.setattr(module.db, "execute_system", AsyncMock(side_effect=lambda *_: next(reads)))
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    messages = await HarnessRepository().messages_for_tree("root")
    assert messages[0]["message_id"] == "message-1"
    assert messages[0]["consumed_at"] == now


@pytest.mark.asyncio
async def test_stale_recovery_requeues_root_but_never_replays_child_tools(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    updates: list[tuple[str, str]] = []

    async def execute(sql: str, params=None):
        if sql.startswith("SELECT run_id, root_run_id"):
            return {"rows": [["root", None], ["finance", "root"]]}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS"):
            updates.append((params[2], params[0]))
            return {"affected": 1}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    repo.event = AsyncMock(return_value="0")
    repo.wake_parent = AsyncMock(return_value=True)
    assert await repo.recover_stale() == ["root", "finance"]
    assert updates == [("root", "queued"), ("finance", "interrupted")]
    repo.wake_parent.assert_awaited_once_with("root")


@pytest.mark.asyncio
async def test_stale_recovery_survives_a_deleted_root(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    running = True
    root_reads = 0
    updates = []

    async def execute(sql: str, params=None):
        nonlocal running, root_reads
        if sql.startswith("SELECT run_id, root_run_id"):
            return {"rows": [["orphan", "deleted-root"]] if running else []}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS "):
            updates.append((sql, params))
            if running:
                running = False
                return {"affected": 1}
            return {"affected": 0}
        if sql.startswith("SELECT last_sequence"):
            root_reads += 1
            return {"rows": []}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(module.session_store, "_redis", _EventRedis())
    repo = HarnessRepository()
    repo.wake_parent = AsyncMock(return_value=False)
    assert await repo.recover_stale() == ["orphan"]
    assert await repo.recover_stale() == []
    assert len(updates) == 1
    assert updates[0][1][0] == "interrupted"
    assert root_reads == 5
    repo.wake_parent.assert_awaited_once_with("deleted-root")


@pytest.mark.asyncio
async def test_stale_recovery_does_not_hide_other_event_errors(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    async def execute(sql: str, params=None):
        if sql.startswith("SELECT run_id, root_run_id"):
            return {"rows": [["child", "root"]]}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS "):
            return {"affected": 1}
        raise AssertionError(sql)

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    repo.event = AsyncMock(side_effect=ValueError("Unsupported Auto session event type"))
    with pytest.raises(ValueError, match="Unsupported Auto session event type"):
        await repo.recover_stale()


@pytest.mark.asyncio
async def test_root_cancellation_cascades_and_child_cancel_is_scoped(monkeypatch) -> None:
    from app.modules.agents import harness_repository as module

    calls: list[tuple[str, list]] = []

    async def execute(sql: str, params=None):
        calls.append((sql, params))
        return {"affected": 1}

    monkeypatch.setattr(module.db, "execute_system", execute)
    repo = HarnessRepository()
    assert await repo.cancel_tree("root")
    assert "run_id = %s AND agent_id = '__auto__'" in calls[0][0]
    assert calls[0][1][1:] == ["root"]
    assert "root_run_id = %s AND depth = 1" in calls[1][0]
    assert calls[1][1][1:] == ["root"]
    assert await repo.cancel_child("root", "finance")
    assert "depth = 1" in calls[2][0]
    assert calls[2][1][1:] == ["finance", "root"]


@pytest.mark.asyncio
async def test_coordinator_suspends_steers_and_resumes_from_checkpoint(monkeypatch) -> None:
    from app.modules.agents import harness_worker as module
    from app.modules.agents.auto_planner import Assignment, DelegationPlan

    root = _run("root", status="running")
    root.update(prompt_tokens=0, completion_tokens=0)
    runs = {"root": root}
    messages: list[dict] = []
    acknowledgements: list[str] = []
    outbound: list[tuple[str, str]] = []
    events: list[tuple[str, str]] = []
    plans = [
        DelegationPlan(
            "diagnostic",
            (),
            (
                Assignment("finance", "Quantify recognized revenue"),
                Assignment("marketing", "Analyze campaign attribution"),
            ),
            (),
            "Parallel evidence",
        ),
        DelegationPlan(
            "diagnostic",
            (),
            (),
            ({"agent_id": "marketing", "message": "Focus on Jakarta Enterprise"},),
            "Steer marketing",
        ),
        DelegationPlan("synthesis", (), (), (), "Evidence complete"),
    ]

    class Repository:
        async def get(self, run_id):
            return runs.get(run_id)

        async def tree(self, root_id, **kwargs):
            return list(runs.values())

        async def pending_messages(self, run_id, limit=20):
            return [m for m in messages if m["message_id"] not in acknowledgements][:limit]

        async def acknowledge_messages(self, run_id, ids):
            acknowledgements.extend(ids)

        async def add_usage(self, *args, **kwargs):
            return None

        async def event(self, root_id, run_id, kind, payload):
            events.append((run_id, kind))
            return str(len(events))

        async def reconcile_terminal_children(self, root_id, children):
            for child in children:
                if child["status"] == "completed":
                    await self.event(root_id, child["run_id"], "agent_completed", {})

        async def spawn(self, *, parent, agent_id, objective, context, operation_id, agent_name):
            child = _run(agent_id, depth=1)
            child.update(
                objective=objective, result_summary=None, prompt_tokens=0, completion_tokens=0
            )
            runs[agent_id] = child
            return child

        async def send(self, *, sender, recipient, operation_id, message_type, content):
            outbound.append((recipient["run_id"], content))
            return operation_id

        async def transition(
            self, run_id, *, from_status, to_status, checkpoint=None, summary=None, **kwargs
        ):
            run = runs[run_id]
            assert kwargs["lease_owner"] == run["lease_owner"]
            assert kwargs["generation"] == run["generation"]
            if run["status"] != from_status:
                return False
            run["status"] = to_status
            if checkpoint is not None:
                run["checkpoint"] = checkpoint
            if summary is not None:
                run["result_summary"] = summary
            return True

        async def wake_parent(self, root_id):
            if runs[root_id]["status"] == "waiting_for_agent":
                runs[root_id]["status"] = "queued"
                return True
            return False

    async def plan(**kwargs):
        return plans.pop(0)

    monkeypatch.setattr(module.auto_planner, "plan", plan)
    monkeypatch.setattr(
        module.agent_repository,
        "get_agent",
        AsyncMock(side_effect=lambda agent_id, **kwargs: {"agent_id": agent_id}),
    )
    monkeypatch.setattr(module, "has_verified_access", AsyncMock(return_value=True))
    monkeypatch.setattr(
        module.assistant_provider,
        "complete",
        AsyncMock(
            return_value={
                "content": "Revenue fell; campaign evidence supports a contribution.",
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            }
        ),
    )
    persisted: list[dict] = []

    async def append_visible(*args, **kwargs):
        persisted.append({"message_id": kwargs["message_id"]})

    monkeypatch.setattr(module.assistant_repository, "list_messages", AsyncMock(
        side_effect=lambda *args, **kwargs: list(persisted)
    ))
    append = AsyncMock(side_effect=append_visible)
    monkeypatch.setattr(module.assistant_repository, "append_message", append)
    worker = AgentHarnessWorker(Repository())
    user = {
        "username": "alice",
        "active_role": "analyst",
        "assigned_roles": ["analyst"],
        "security_context_version": 1,
    }
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value=user))

    await worker._coordinate(root, user, asyncio.Event())
    assert {key for key in runs if key != "root"} == {"finance", "marketing"}
    assert root["status"] == "waiting_for_agent"
    assert not plans[0].assignments

    messages.append(
        {
            "message_id": "finding-1",
            "sender_run_id": "finance",
            "message_type": "finding",
            "content": "Jakarta Enterprise down 22%",
        }
    )
    await worker.repository.wake_parent("root")
    root["status"] = "running"
    await worker._coordinate(root, user, asyncio.Event())
    assert outbound == [("marketing", "Focus on Jakarta Enterprise")]
    assert acknowledgements == ["finding-1"]
    assert root["checkpoint"]["coordination_messages"][0]["content"].endswith("22%")
    assert root["status"] == "waiting_for_agent"

    for child in (runs["finance"], runs["marketing"]):
        child["status"] = "completed"
        child["result_summary"] = f"{child['agent_id']} evidence"
    runs["finance"]["checkpoint"] = {
        "evidence_tables": [
            {"columns": ["sales_channel", "recognized_revenue"],
             "rows": [["Mobile App", "3368049065451.00"]]}
        ]
    }
    runs["finance"]["result_summary"] = "A" * 5000
    module.assistant_provider.complete.return_value = {
        "content": "Mobile App revenue was 999.",
        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
    }
    await worker.repository.wake_parent("root")
    root["status"] = "running"
    await worker._coordinate(root, user, asyncio.Event())
    assert root["status"] == "completed"
    provider_messages = module.assistant_provider.complete.await_args.kwargs["messages"]
    provider_context = json.loads(provider_messages[1]["content"])
    assert provider_context["findings"][0]["query_result_tables"][0]["rows"] == [
        ["Mobile App", "3368049065451.00"]
    ]
    assert append.await_count == 1
    assert "| Mobile App | 3,368,049,065,451.00 |" in append.await_args.kwargs["content"]
    assert "999" not in append.await_args.kwargs["content"]
    assert events[-1] == ("root", "agent_completed")
    assert not plans


@pytest.mark.asyncio
async def test_coordinator_rejects_new_numbers_without_query_evidence(monkeypatch) -> None:
    from app.modules.agents import harness_worker as module

    root = _run("root", status="running")
    root.update(
        prompt_tokens=0,
        completion_tokens=0,
        payload={"provider_id": "fake", "model": "fake"},
        checkpoint={"processed_child_ids": ["sales"]},
    )
    child = _run("sales", depth=1, status="completed")
    child.update(
        result_summary="The query returned no rows.",
        checkpoint={"needs_data": True, "evidence_tables": []},
        prompt_tokens=0,
        completion_tokens=0,
    )

    class Repository:
        async def get(self, run_id):
            return {"root": root, "sales": child}.get(run_id)

        async def tree(self, *args, **kwargs):
            return [root, child]

        async def pending_messages(self, *args, **kwargs):
            return []

        async def add_usage(self, *args, **kwargs):
            return None

        async def transition(self, *args, **kwargs):
            return True

        async def event(self, *args, **kwargs):
            return "1"

        async def reconcile_terminal_children(self, *args, **kwargs):
            return None

        async def acknowledge_messages(self, *args, **kwargs):
            return None

    user = {"username": "alice", "active_role": "analyst", "assigned_roles": ["analyst"]}
    monkeypatch.setattr(
        module.assistant_provider,
        "complete",
        AsyncMock(return_value={"content": "Revenue was 999.", "usage": {}}),
    )
    monkeypatch.setattr(module.assistant_provider, "resolve", AsyncMock(return_value=object()))
    persisted: list[dict] = []

    async def append_visible(*args, **kwargs):
        persisted.append({"message_id": kwargs["message_id"]})

    monkeypatch.setattr(module.assistant_repository, "list_messages", AsyncMock(
        side_effect=lambda *args, **kwargs: list(persisted)
    ))
    append = AsyncMock(side_effect=append_visible)
    monkeypatch.setattr(module.assistant_repository, "append_message", append)
    worker = AgentHarnessWorker(Repository())
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value=user))

    await worker._coordinate(root, user, asyncio.Event())

    assert "999" not in append.await_args.kwargs["content"]
    assert "tidak ada hasil query" in append.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_replayed_coordinator_does_not_append_final_answer_twice(monkeypatch) -> None:
    from app.modules.agents import harness_worker as module

    root = _run("root", status="running")
    root.update(prompt_tokens=10, completion_tokens=5)
    stored: list[dict] = []
    emitted: list[str] = []
    transitions = 0

    class Repository:
        async def get(self, run_id):
            return root

        async def transition(self, run_id, *, from_status, to_status, **kwargs):
            nonlocal transitions
            assert kwargs["lease_owner"] == root["lease_owner"]
            assert kwargs["generation"] == root["generation"]
            transitions += 1
            if transitions == 1:
                return False  # Worker exits after the message, before completion commits.
            if root["status"] != from_status:
                return False
            root["status"] = to_status
            return True

        async def event(self, root_id, run_id, kind, payload):
            emitted.append(kind)

        async def reconcile_terminal_children(self, *args, **kwargs):
            return None

    async def append(thread_id, **kwargs):
        stored.append({"message_id": kwargs["message_id"]})

    monkeypatch.setattr(module.assistant_repository, "list_messages", AsyncMock(
        side_effect=lambda *args, **kwargs: list(stored)
    ))
    monkeypatch.setattr(module.assistant_repository, "append_message", append)
    worker = AgentHarnessWorker(Repository())
    user = {"username": "alice", "active_role": "analyst",
            "assigned_roles": ["analyst"], "security_context_version": 1}
    await worker._finish_root(root, user, "Answer", [])
    await worker._finish_root(root, user, "Answer", [])
    assert len(stored) == 1
    assert stored[0]["message_id"]
    assert emitted == ["agent_completed"]


@pytest.mark.asyncio
async def test_auto_root_completes_only_after_final_message_is_visible(monkeypatch) -> None:
    from app.modules.agents import harness_worker as module

    root = _run("root", status="running")
    stored: list[str] = []
    reads_after_write = 0
    transitions: list[str] = []

    class Repository:
        async def get(self, run_id):
            return root

        async def reconcile_terminal_children(self, *args, **kwargs):
            return None

        async def transition(self, run_id, *, from_status, to_status, **kwargs):
            assert reads_after_write == 3
            transitions.append(to_status)
            root["status"] = to_status
            return True

        async def event(self, *args, **kwargs):
            return "1"

    async def list_messages(*args, **kwargs):
        nonlocal reads_after_write
        assert kwargs["synchronize"] is True
        if not stored:
            return []
        reads_after_write += 1
        return [] if reads_after_write < 3 else [{"message_id": stored[0]}]

    async def append_message(*args, **kwargs):
        stored.append(kwargs["message_id"])

    monkeypatch.setattr(module.assistant_repository, "list_messages", list_messages)
    append = AsyncMock(side_effect=append_message)
    monkeypatch.setattr(module.assistant_repository, "append_message", append)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    user = {"username": "alice", "active_role": "analyst", "assigned_roles": ["analyst"]}

    await AgentHarnessWorker(Repository())._finish_root(root, user, "Answer", [])

    assert append.await_count == 1
    assert transitions == ["completed"]
    assert root["status"] == "completed"


@pytest.mark.asyncio
async def test_auto_root_never_completes_with_an_invisible_final_message(monkeypatch) -> None:
    from app.modules.agents import harness_worker as module

    root = _run("root", status="running")

    class Repository:
        async def get(self, run_id):
            return root

        async def reconcile_terminal_children(self, *args, **kwargs):
            return None

        async def transition(self, *args, **kwargs):
            pytest.fail("An invisible answer cannot be marked completed")

    monkeypatch.setattr(module.assistant_repository, "list_messages", AsyncMock(return_value=[]))
    append = AsyncMock()
    monkeypatch.setattr(module.assistant_repository, "append_message", append)
    monkeypatch.setattr(module, "FINAL_MESSAGE_VISIBILITY_ATTEMPTS", 3)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    user = {"username": "alice", "active_role": "analyst", "assigned_roles": ["analyst"]}

    with pytest.raises(RuntimeError, match="did not become visible"):
        await AgentHarnessWorker(Repository())._finish_root(root, user, "Answer", [])

    assert append.await_count == 1
    assert root["status"] == "running"


@pytest.mark.asyncio
async def test_coordinator_can_add_a_specialist_after_scout_finding(monkeypatch) -> None:
    from app.modules.agents import harness_worker as module
    from app.modules.agents.auto_planner import Assignment, DelegationPlan

    root = _run("root", status="running")
    root.update(prompt_tokens=0, completion_tokens=0)
    finance = _run("finance", depth=1, status="completed")
    finance.update(result_summary="Campaign lead volume may explain the decline.")
    runs = {"root": root, "finance": finance}
    spawned: list[str] = []

    class Repository:
        async def tree(self, *args, **kwargs):
            return list(runs.values())

        async def get(self, run_id):
            return runs[run_id]

        async def pending_messages(self, *args, **kwargs):
            return []

        async def add_usage(self, *args, **kwargs):
            return None

        async def event(self, *args, **kwargs):
            return "0"

        async def spawn(self, *, parent, agent_id, objective, context, operation_id, agent_name):
            spawned.append(agent_id)
            child = _run(agent_id, depth=1)
            child["result_summary"] = None
            runs[agent_id] = child
            return child

        async def transition(self, run_id, *, from_status, to_status, checkpoint, **kwargs):
            assert root["status"] == from_status
            root["status"] = to_status
            root["checkpoint"] = checkpoint
            return True

        async def acknowledge_messages(self, *args, **kwargs):
            return None

    monkeypatch.setattr(module.auto_planner, "plan", AsyncMock(return_value=DelegationPlan(
        "diagnostic", (), (Assignment("marketing", "Check campaign leads"),), (),
        "Finance suggested Marketing",
    )))
    monkeypatch.setattr(module.agent_repository, "get_agent", AsyncMock(
        return_value={"agent_id": "marketing"}
    ))
    monkeypatch.setattr(module, "has_verified_access", AsyncMock(return_value=True))
    worker = AgentHarnessWorker(Repository())
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value={"username": "alice"}))
    await worker._coordinate(root, {"username": "alice"}, asyncio.Event())
    assert spawned == ["marketing"]
    assert root["status"] == "waiting_for_agent"
    assert root["checkpoint"]["processed_child_ids"] == ["finance"]
