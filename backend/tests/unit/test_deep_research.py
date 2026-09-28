"""Deep Research: plan, bounded investigations, verified report, cancellation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import deep_research
from app.modules.agents.deep_research import _parse_plan
from app.modules.agents.turns import TurnOutput

AGENT = {"agent_id": "a1", "name": "Sales", "owner_name": "alice"}
USER = {"username": "alice", "encrypted_password": "enc", "active_role": "analyst",
        "assigned_roles": ["analyst"], "session_id": "s"}


def test_plan_parsing_is_bounded_and_falls_back_to_the_question():
    content = '{"investigations": ["a", "b", "a", "c", "d", "e", "f", "g"]}'
    assert _parse_plan(content, "q") == ["a", "b", "c", "d", "e", "f"]
    assert _parse_plan("not json", "q") == ["q"]
    assert _parse_plan('```json\n{"investigations": []}\n```', "q") == ["q"]


class Repository:
    def __init__(self):
        self.updates = []

    async def update(self, run_id, **fields):
        self.updates.append(fields)


@pytest.fixture
def env(monkeypatch):
    import app.modules.agents.semantic.access as access

    monkeypatch.setattr(access, "load_authorized_models", AsyncMock(return_value=[]))
    monkeypatch.setattr(deep_research, "write_audit_log", AsyncMock())
    publish = AsyncMock()
    monkeypatch.setattr(deep_research, "_publish", publish)
    return publish


async def test_investigations_run_and_the_report_keeps_only_verified_numbers(env, monkeypatch):
    import app.modules.agents.turns as turns

    replies = iter([
        '{"investigations": ["revenue per bulan", "revenue per kota"]}',
        "Revenue turun karena Bandung. Juli 1.200, Agustus 1.000, Bandung 999.",
    ])
    monkeypatch.setattr(deep_research, "_complete", AsyncMock(side_effect=lambda *_: next(replies)))
    running = {"now": 0, "max": 0}

    async def turn(agent, *, user, question, thread_id, resolve_consent, title):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0)
        running["now"] -= 1
        table = ({"columns": ["month", "total_revenue"],
                  "rows": [["2026-07", 1200], ["2026-08", 1000]]}
                 if "bulan" in question else
                 {"columns": ["city", "total_revenue"], "rows": [["Bandung", 300]]})
        return TurnOutput(text=f"answer to {question}", tables={"evidence_1": table},
                          finish_reason="stop")

    monkeypatch.setattr(turns, "run_agent_turn", turn)
    repository = Repository()
    await deep_research.run("r1", AGENT, USER, "Kenapa revenue turun?", "t1",
                            repository=repository)
    statuses = [update.get("status") for update in repository.updates if "status" in update]
    assert statuses == ["running", "writing", "done"]
    report = repository.updates[-1]["report"]
    assert "1.200" in report and "1.000" in report
    assert "999" not in report
    env.assert_awaited_once()


async def test_a_failed_planner_still_researches_the_question(env, monkeypatch):
    import app.modules.agents.turns as turns

    calls = []

    async def complete(agent, messages):
        if "Split the user's analytical question" in messages[0]["content"]:
            raise RuntimeError("planner down")
        return "Report."

    monkeypatch.setattr(deep_research, "_complete", complete)

    async def turn(agent, *, user, question, **_):
        calls.append(question)
        return TurnOutput(text="ok", finish_reason="stop")

    monkeypatch.setattr(turns, "run_agent_turn", turn)
    await deep_research.run("r1", AGENT, USER, "Why?", "t1", repository=Repository())
    assert calls == ["Why?"]


async def test_cancellation_stops_before_new_investigations(env, monkeypatch):
    import app.modules.agents.turns as turns

    monkeypatch.setattr(deep_research, "_complete", AsyncMock(
        return_value='{"investigations": ["a", "b", "c", "d"]}'))
    turn = AsyncMock()
    monkeypatch.setattr(turns, "run_agent_turn", turn)
    cancelled = asyncio.Event()
    cancelled.set()
    repository = Repository()
    await deep_research.run("r1", AGENT, USER, "Why?", "t1", repository=repository,
                            cancelled=cancelled)
    turn.assert_not_awaited()
    assert repository.updates[-1]["status"] == "cancelled"
