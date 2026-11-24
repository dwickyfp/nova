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

    async def complete(agent, messages, *_):
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


async def test_the_report_answers_the_users_question_from_tables_and_dates_in_the_data(
    env, monkeypatch,
):
    """Re-bench deep_research-001/002/003: the report was written for the last
    investigation (the loop variable shadowed the user's question), from answer
    text only, for periods counted back from today outside data ending in July 2025."""
    import json
    from datetime import date

    import app.modules.agents.turns as turns
    from app.modules.agents.semantic.coverage import DataWindow

    monkeypatch.setattr(deep_research, "_data_window", AsyncMock(
        return_value=DataWindow(first=date(2024, 8, 1), last=date(2025, 7, 5))))
    seen = []

    async def complete(agent, messages, usage):
        seen.append(json.loads(messages[1]["content"]))
        usage["total_tokens"] = usage.get("total_tokens", 0) + 100
        if len(seen) == 1:
            return '{"investigations": ["revenue by month", "customers by revenue change"]}'
        return "Revenue fell in July 2025 to 1,200."

    monkeypatch.setattr(deep_research, "_complete", complete)
    rows = [[f"2025-{month:02d}", 1000 + month] for month in range(1, 30)]

    async def turn(agent, *, user, question, **_):
        return TurnOutput(text=f"answer to {question}", finish_reason="stop",
                          tables={"evidence_1": {"columns": ["month", "revenue"], "rows": rows}},
                          usage={"prompt_tokens": 40, "completion_tokens": 10,
                                 "total_tokens": 50})

    monkeypatch.setattr(turns, "run_agent_turn", turn)
    await deep_research.run("r1", AGENT, USER, "What drives revenue?", "t1",
                            repository=Repository())
    planner, writer = seen
    assert planner["catalog"]["data_window"] == {"first": "2024-08-01", "last": "2025-07-05"}
    assert writer["question"] == "What drives revenue?"
    first = writer["findings"][0]["tables"]["i0:evidence_1"]
    assert first["columns"] == ["month", "revenue"]
    assert len(first["rows"]) == deep_research.REPORT_TABLE_ROWS and first["row_count"] == 29
    published = env.await_args
    assert published.args[3] == "What drives revenue?"
    # Planner and writer (100 each) plus both investigations (50 each).
    assert published.args[6] == {"prompt_tokens": 80, "completion_tokens": 20,
                                 "total_tokens": 300}


def test_usage_adds_only_well_formed_counts():
    total: dict[str, int] = {}
    deep_research.add_usage(total, {"prompt_tokens": 3, "total_tokens": True})
    deep_research.add_usage(total, None)
    deep_research.add_usage(total, {"prompt_tokens": -1, "completion_tokens": 2})
    assert total == {"prompt_tokens": 3, "completion_tokens": 2}


async def test_the_planners_data_window_is_read_as_the_caller(monkeypatch):
    from datetime import date
    from types import SimpleNamespace

    from app.modules.agents.semantic.runtime import semantic_ir_to_definition
    from app.modules.agents.tools.semantic_query import SemanticQueryTool
    from app.modules.query.service import query_service
    from tests.unit.test_semantic_intelligence import sales_model

    async def execute(**kwargs):
        assert kwargs["username"] == "alice" and kwargs["role"] == "analyst"
        day = "2024-08-01" if " ASC" in kwargs["sql"].upper() else "2025-07-05"
        return [SimpleNamespace(columns=["order_date", "total_revenue"], rows=[[day, 1]],
                                error=None)]

    monkeypatch.setattr(query_service, "execute_statements", execute)
    context = SimpleNamespace(user=USER, active_role="analyst")
    model = {"definition": semantic_ir_to_definition(sales_model())}
    window = await SemanticQueryTool(provider=AsyncMock()).data_window_of(model, context)
    assert (window.first, window.last) == (date(2024, 8, 1), date(2025, 7, 5))
    no_login = SimpleNamespace(user={"username": "alice"})
    assert await SemanticQueryTool(provider=AsyncMock()).data_window_of(model, no_login) is None
