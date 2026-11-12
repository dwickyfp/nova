"""Learning telemetry uses the existing private message journal and retains retries."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents.knowledge import LearningCallDetail, LearningCallTrace
from app.modules.assistant import repository
from app.modules.assistant.metadata_repository import AssistantMetadataRepository


@pytest.fixture
def journal(monkeypatch):
    state = {"steps": None, "present": True}
    writable = SimpleNamespace(valid=True)

    async def read(sql, params):
        assert "FOR UPDATE" in sql and "learning_state='pending'" in sql
        assert "user_name=coalesce(%s,user_name)" in sql
        assert params == ("source", "alice")
        return {"steps": deepcopy(state["steps"])} if state["present"] else None

    async def write(sql, params):
        assert "learning_state='pending'" in sql and params[1] == "source"
        if writable.valid:
            state["steps"] = deepcopy(params[0].obj)
        return SimpleNamespace(rowcount=1 if writable.valid else 0)

    store = AssistantMetadataRepository(
        SimpleNamespace(execute=AsyncMock(side_effect=write)), repository.learning_actor(),
    )
    monkeypatch.setattr(store, "_bound", AsyncMock())
    calls = AsyncMock(side_effect=read)
    monkeypatch.setattr(store, "_one", calls)

    @asynccontextmanager
    async def scoped(*, actor):
        assert actor == repository.learning_actor()
        yield store

    monkeypatch.setattr(repository, "_store", scoped)
    return state, writable, calls


def trace(ident, phase):
    return LearningCallTrace(
        id=ident,
        status="done" if phase == "response" else "running",
        trace_detail=LearningCallDetail(phase=phase),
    ).model_dump(mode="json")


async def test_attempt_updates_do_not_duplicate_a_call_and_retries_retain_history(journal):
    state, _, _ = journal
    for phase in ("resolve", "request", "response"):
        await repository.assistant_repository.record_learning_trace(
            "source", user_name="alice", trace=trace("first", phase)
        )
    assert len(state["steps"]) == 1 and state["steps"][0]["status"] == "done"
    await repository.assistant_repository.record_learning_trace(
        "source", user_name="alice", trace=trace("retry", "request")
    )
    assert [step["id"] for step in state["steps"]] == ["first", "retry"]
    assert all(step["trace_detail"]["cost_dollars"] is None for step in state["steps"])


async def test_long_provider_outage_does_not_permanently_exhaust_source_retries(journal):
    state, _, _ = journal
    state["steps"] = [trace(f"failed-{index}", "error") for index in range(100)]
    await repository.assistant_repository.record_learning_trace(
        "source", user_name="alice", trace=trace("recovered", "response")
    )
    assert len(state["steps"]) == 101
    assert state["steps"][-1]["id"] == "recovered"
    assert state["steps"][-1]["status"] == "done"


@pytest.mark.parametrize("reason", ["missing_source", "broken_history", "source_changed"])
async def test_unavailable_source_or_journal_never_overwrites_evidence(journal, reason):
    state, lease, calls = journal
    if reason == "missing_source":
        state["present"] = False
    if reason == "broken_history":
        state["steps"] = "invalid-json"
    if reason == "source_changed":
        lease.valid = False
    before = deepcopy(state)
    with pytest.raises(HTTPException) as error:
        await repository.assistant_repository.record_learning_trace(
            "source", user_name="alice", trace=trace("attempt", "resolve")
        )
    assert error.value.status_code == 409
    assert state == before and calls.await_count == 1
