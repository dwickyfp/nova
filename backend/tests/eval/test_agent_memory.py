"""Cross-thread memory trajectory with a scripted extraction model."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import memory as memory_module
from app.modules.agents import router as agent_router
from app.modules.agents.memory import (
    AgentMemoryRepository,
    memory_prompt,
    remember_user_message,
    select_memories,
)


class MemoryStore(AgentMemoryRepository):
    def __init__(self):
        self.rows = []

    async def list(self, *, user_name, agent_id, role_name, limit=200):
        return [
            row
            for row in self.rows
            if row["user_name"] == user_name
            and row["agent_id"] == agent_id
            and row["role_name"] == role_name
        ][:limit]

    async def upsert(
        self,
        *,
        user_name,
        agent_id,
        role_name,
        fact_key,
        fact,
        source_quote,
        source_thread_id,
        existing_id,
    ):
        if existing_id:
            row = next(row for row in self.rows if row["memory_id"] == existing_id)
            row.update(fact=fact, source_quote=source_quote, source_thread_id=source_thread_id)
            return existing_id
        memory_id = str(len(self.rows) + 1)
        self.rows.append(
            dict(
                memory_id=memory_id,
                user_name=user_name,
                agent_id=agent_id,
                role_name=role_name,
                fact_key=fact_key,
                fact=fact,
                source_quote=source_quote,
                source_thread_id=source_thread_id,
            )
        )
        return memory_id


class ExtractionModel:
    def __init__(self, answers):
        self.answers = answers

    async def resolve(self, **_kwargs):
        return object()

    async def complete(self, **_kwargs):
        return {"content": json.dumps(self.answers.pop(0), ensure_ascii=False)}


@pytest.mark.asyncio
async def test_business_rule_survives_new_thread_and_is_corrected(monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr(memory_module, "write_audit_log", audit)
    repo = MemoryStore()
    provider = ExtractionModel(
        [
            [
                {
                    "key": "omzet_definition",
                    "fact": "Omzet = total invoice dibayar, tanpa PPN.",
                    "quote": "Omzet = total invoice dibayar, tanpa PPN",
                    "existing_id": None,
                }
            ],
            [
                {
                    "key": "omzet_definition",
                    "fact": "Omzet = total invoice dibayar dikurangi retur, tanpa PPN.",
                    "quote": "Omzet = total invoice dibayar dikurangi retur, tanpa PPN",
                    "existing_id": "1",
                }
            ],
        ]
    )
    args = dict(
        user_name="alice",
        agent_id="sales",
        role_name="analyst",
        provider_id=None,
        model=None,
        provider=provider,
        repository=repo,
    )
    assert (
        await remember_user_message(
            **args, thread_id="old", message="Omzet = total invoice dibayar, tanpa PPN."
        )
        == 1
    )
    new_thread_prompt = memory_prompt(
        select_memories(
            await repo.list(user_name="alice", agent_id="sales", role_name="analyst"),
            "Bagaimana hitung omzet?",
        )
    )
    assert "total invoice dibayar, tanpa PPN" in new_thread_prompt
    assert (
        await remember_user_message(
            **args,
            thread_id="new",
            message="Koreksi: Omzet = total invoice dibayar dikurangi retur, tanpa PPN.",
        )
        == 1
    )
    assert len(repo.rows) == 1
    assert "dikurangi retur" in repo.rows[0]["fact"]
    assert repo.rows[0]["source_thread_id"] == "new"
    assert audit.await_count == 2


@pytest.mark.asyncio
async def test_memory_isolation_and_untrusted_extraction(monkeypatch):
    monkeypatch.setattr(memory_module, "write_audit_log", AsyncMock())
    repo = MemoryStore()
    provider = ExtractionModel(
        [
            [
                {
                    "key": "omzet_definition",
                    "fact": "Omzet adalah pendapatan bersih.",
                    "quote": "not present in message",
                    "existing_id": None,
                }
            ],
        ]
    )
    written = await remember_user_message(
        user_name="alice",
        agent_id="sales",
        role_name="analyst",
        thread_id="t1",
        message="Omzet adalah total invoice lunas.",
        provider_id=None,
        model=None,
        provider=provider,
        repository=repo,
    )
    assert written == 0
    repo.rows.append(
        dict(
            memory_id="1",
            user_name="alice",
            agent_id="sales",
            role_name="analyst",
            fact_key="omzet_definition",
            fact="Omzet adalah total invoice lunas.",
            source_quote="Omzet",
            source_thread_id="t1",
        )
    )
    assert await repo.list(user_name="bob", agent_id="sales", role_name="analyst") == []
    assert await repo.list(user_name="alice", agent_id="finance", role_name="analyst") == []
    assert await repo.list(user_name="alice", agent_id="sales", role_name="viewer") == []
    assert "Omzet" not in memory_prompt([])


class RecordingDB:
    def __init__(self):
        self.calls = []

    async def execute_system(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        return {"rows": [], "affected": 1}


@pytest.mark.asyncio
async def test_repository_enforces_all_scope_fields_in_sql(monkeypatch):
    db = RecordingDB()
    monkeypatch.setattr(memory_module, "db", db)
    repo = AgentMemoryRepository()
    await repo.list(user_name="alice", agent_id="sales", role_name="analyst")
    await repo.delete("memory-1", user_name="alice", agent_id="sales", role_name="analyst")
    await repo.upsert(
        user_name="alice",
        agent_id="sales",
        role_name="analyst",
        fact_key="omzet",
        fact="Omzet adalah pembayaran lunas.",
        source_quote="Omzet adalah pembayaran lunas",
        source_thread_id="t1",
        existing_id="memory-1",
    )
    for sql, params in db.calls:
        assert "user_name = %s AND agent_id = %s AND role_name = %s" in sql
        assert "alice" in params and "sales" in params and "analyst" in params


@pytest.mark.asyncio
async def test_credentials_never_enter_extraction(monkeypatch):
    monkeypatch.setattr(memory_module, "write_audit_log", AsyncMock())
    provider = ExtractionModel([])
    written = await remember_user_message(
        user_name="alice",
        agent_id="sales",
        role_name="analyst",
        thread_id="t1",
        message="Password database adalah supersecret dan omzet adalah invoice lunas.",
        provider_id=None,
        model=None,
        provider=provider,
        repository=MemoryStore(),
    )
    assert written == 0
    assert provider.answers == []
    written = await remember_user_message(
        user_name="alice",
        agent_id="sales",
        role_name="analyst",
        thread_id="t1",
        message="Omzet adalah invoice lunas. Kunci saya sk-abcdefghijklmnopqrstuvwx",
        provider_id=None,
        model=None,
        provider=provider,
        repository=MemoryStore(),
    )
    assert written == 0


def test_retrieval_selects_business_rule_among_distractors():
    memories = [
        {"fact_key": f"preference_{i}", "fact": f"Preferensi laporan wilayah {i}."}
        for i in range(100)
    ]
    memories.insert(
        81,
        {
            "fact_key": "omzet_definition",
            "fact": "Omzet = invoice lunas dikurangi retur, tanpa PPN.",
        },
    )
    selected = select_memories(memories, "Bagaimana menghitung omzet?", limit=4)
    assert selected[0]["fact_key"] == "omzet_definition"
    assert len(memory_prompt(selected)) < 2800


@pytest.mark.asyncio
async def test_memory_endpoint_paginates_with_scope(monkeypatch):
    monkeypatch.setattr(agent_router, "_require_agent", AsyncMock(return_value={}))
    monkeypatch.setattr(
        agent_router,
        "session_security",
        lambda _user: SimpleNamespace(active_role="analyst"),
    )
    repo = SimpleNamespace(list=AsyncMock(return_value=[{"memory_id": str(i)} for i in range(101)]))
    monkeypatch.setattr(agent_router, "memory_repository", repo)
    result = await agent_router.list_agent_memories(
        "sales", limit=100, offset=200, user={"username": "alice"}
    )
    assert result["count"] == 100
    assert result["next_offset"] == 300
    assert len(result["memories"]) == 100
    repo.list.assert_awaited_once_with(
        user_name="alice", agent_id="sales", role_name="analyst", limit=101, offset=200
    )
