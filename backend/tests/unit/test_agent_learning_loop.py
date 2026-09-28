"""Feedback and memory: what a liked answer proposes, and which memories a turn sees."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import memory, verified_candidates
from app.modules.agents.verified_candidates import candidates_from_steps

PLAN = {"metrics": ["total_revenue"], "dimensions": ["city"]}


def semantic_step(**trace):
    return {
        "kind": "tool", "name": "semantic_query", "status": "done",
        "trace_detail": {
            "question": "revenue per kota bulan lalu",
            "semantic_view": {"id": "view-1", "version": 3},
            "semantic_plan": PLAN,
            **trace,
        },
    }


def test_a_liked_semantic_answer_yields_one_candidate_per_distinct_plan():
    steps = [semantic_step(), semantic_step(), {"kind": "tool", "name": "compute_metrics"}]
    candidates = candidates_from_steps(steps)
    assert candidates == [{
        "view_id": "view-1", "view_version": 3,
        "question": "revenue per kota bulan lalu", "semantic_plan": PLAN,
    }]


@pytest.mark.parametrize(
    "trace",
    [
        {"error": "The generated query failed to run."},
        {"semantic_plan": None},
        {"semantic_view": {}},
        {"question": ""},
    ],
)
def test_failed_or_incomplete_steps_propose_nothing(trace):
    assert candidates_from_steps([semantic_step(**trace)]) == []


async def test_a_plan_already_pending_is_not_proposed_twice(monkeypatch):
    execute = AsyncMock(side_effect=[{}, {"rows": [["existing"]]}])
    monkeypatch.setattr(verified_candidates.db, "execute_system", execute)
    created = await verified_candidates.verified_candidate_repository.propose(
        agent_id="a1", owner_name="owner", proposed_by="alice", thread_id="t1",
        message_id="m1", candidate=candidates_from_steps([semantic_step()])[0],
    )
    assert created is None
    assert not any("INSERT" in call.args[0] for call in execute.call_args_list)


async def test_decide_accepts_only_approve_or_reject():
    with pytest.raises(ValueError):
        await verified_candidates.verified_candidate_repository.decide(
            "c1", status="adopted", decided_by="owner"
        )


def _memories(count: int) -> list[dict]:
    return [
        {"memory_id": f"m{index}", "fact_key": f"rule_{index}", "fact": f"Fact number {index}"}
        for index in range(count)
    ]


def test_small_memory_sets_are_included_whole_even_without_shared_words():
    selected = memory.select_memories(
        [{"memory_id": "m1", "fact_key": "omzet_definition",
          "fact": "Omzet adalah penjualan bersih setelah retur."}],
        "Berapa pendapatan bulan lalu?",
    )
    assert [item["memory_id"] for item in selected] == ["m1"]


async def test_large_memory_sets_use_embeddings_when_configured(monkeypatch):
    memories = _memories(12)

    class Service:
        async def resolve_model(self, *, alias):
            return SimpleNamespace()

        async def embed_batch(self, texts, model):
            # texts[0] is the query; memory 7 points the same way, the rest do not.
            return [[1.0, 0.0], *([[0.0, 1.0]] * 7), [1.0, 0.0], *([[0.0, 1.0]] * 4)]

    import app.modules.ai_ml.embeddings as embeddings

    monkeypatch.setattr(embeddings, "EmbeddingService", Service)
    selected = await memory.select_relevant_memories(memories, "anything", limit=3)
    assert selected[0]["memory_id"] == "m7"
    assert len(selected) == 3


async def test_large_memory_sets_fall_back_to_words_without_embeddings(monkeypatch):
    import app.modules.ai_ml.embeddings as embeddings

    class Broken:
        async def resolve_model(self, *, alias):
            raise RuntimeError("no model")

    monkeypatch.setattr(embeddings, "EmbeddingService", Broken)
    memories = _memories(12)
    memories[5]["fact"] = "Omzet excludes returns"
    selected = await memory.select_relevant_memories(memories, "how is omzet defined", limit=3)
    assert selected[0]["memory_id"] == "m5"
