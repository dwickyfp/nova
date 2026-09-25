"""Scripted Auto trajectory from a specialist query to the final answer."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import harness_worker as module
from app.modules.agents.harness_worker import AgentHarnessWorker
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import EvalTool


@pytest.mark.parametrize(
    "synthesis_text",
    ["Mobile App recognized revenue was 999.", "Mobile App leads every metric."],
)
@pytest.mark.parametrize("replan_failure", [False, True])
@pytest.mark.asyncio
async def test_auto_carries_query_evidence_and_withholds_unsupported_numbers(
    monkeypatch: pytest.MonkeyPatch,
    synthesis_text: str,
    replan_failure: bool,
) -> None:
    question = (
        "Bandingkan recognized revenue, gross margin, "
        "dan order count per channel untuk 2025."
    )
    table = {
        "columns": ["sales_channel", "recognized_revenue", "gross_margin_pct", "order_count"],
        "rows": [
            ["Mobile App", "125.00", "0.37", "3"],
            ["Store", "110.00", "0.36", "2"],
            ["Marketplace", "100.00", "0.38", "4"],
            ["Website", "80.00", "0.35", "1"],
            ["WhatsApp B2B", "60.00", "0.39", "1"],
        ],
    }
    root = {
        "run_id": "root-eval",
        "root_run_id": None,
        "agent_id": "__auto__",
        "owner_name": "eval",
        "role_name": "analyst",
        "thread_id": "thread-eval",
        "session_id": "session-eval",
        "security_version": 1,
        "depth": 0,
        "objective": question,
        "payload": {"provider_id": "scripted", "model": "eval"},
        "checkpoint": {"processed_child_ids": ["sales-eval"]},
        "status": "waiting_for_agent",
        "generation": 1,
        "lease_owner": "worker-eval",
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    child = {
        **root,
        "run_id": "sales-eval",
        "root_run_id": "root-eval",
        "parent_run_id": "root-eval",
        "agent_id": "sales",
        "depth": 1,
        "status": "running",
        "payload": {},
        "checkpoint": {},
        "result_summary": None,
    }

    class Repository:
        def __init__(self) -> None:
            self.runs = {"root-eval": root, "sales-eval": child}
            self.events: list[tuple[str, str]] = []
            self.activity: list[dict] = []

        async def get(self, run_id: str) -> dict | None:
            return self.runs.get(run_id)

        async def tree(self, *_args, **_kwargs) -> list[dict]:
            return list(self.runs.values())

        async def pending_messages(self, *_args, **_kwargs) -> list[dict]:
            return []

        async def acknowledge_messages(self, *_args, **_kwargs) -> None:
            return None

        async def add_usage(self, *_args, **_kwargs) -> None:
            return None

        async def event(self, _root_id: str, run_id: str, kind: str, payload: dict) -> str:
            self.events.append((run_id, kind))
            if run_id == "sales-eval" and kind == "child_activity":
                self.activity.append(payload)
            return str(len(self.events))

        async def reconcile_terminal_children(
            self, _root_id: str, children: list[dict]
        ) -> None:
            assert children == [child]
            assert ("sales-eval", "agent_completed") in self.events

        async def wake_parent(self, _root_id: str) -> bool:
            return True

        async def transition(
            self, run_id: str, *, from_status: str, to_status: str, **updates
        ) -> bool:
            run = self.runs[run_id]
            assert run["status"] == from_status
            run["status"] = to_status
            if "checkpoint" in updates:
                run["checkpoint"] = updates["checkpoint"]
            if "summary" in updates:
                run["result_summary"] = updates["summary"]
            if "prompt_tokens" in updates:
                run["prompt_tokens"] = updates["prompt_tokens"]
            if "completion_tokens" in updates:
                run["completion_tokens"] = updates["completion_tokens"]
            return True

    class Provider(ScriptedProvider):
        synthesis_messages: list[dict] | None = None

        async def complete(self, *, messages: list[dict], **_kwargs) -> dict:
            self.synthesis_messages = messages
            self.calls += 1
            return {"content": synthesis_text, "usage": {}}

    tool = EvalTool("query_execute", summary="1 row", table=table)
    registry = ToolRegistry()
    registry.register(tool)
    provider = Provider(
        [
            tool_call_frame(
                "query-1",
                sql=(
                    "SELECT sales_channel, recognized_revenue, gross_margin_pct, "
                    "order_count FROM sales"
                ),
            ),
            text_frame("Mobile App recognized revenue was 999."),
        ]
    )
    repository = Repository()
    worker = AgentHarnessWorker(repository)
    user = {
        "username": "eval",
        "active_role": "analyst",
        "assigned_roles": ["analyst"],
        "security_context_version": 1,
    }
    agent = {
        "agent_id": "sales",
        "owner_name": "eval",
        "policy": "auto_read_only",
    }
    monkeypatch.setattr(module, "assistant_provider", provider)
    monkeypatch.setattr(module.agent_repository, "get_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(
        module.agent_service,
        "build_loop_inputs",
        AsyncMock(return_value=(registry, "Specialist", 30, 1000)),
    )
    monkeypatch.setattr(module.memory_repository, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "has_verified_access", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value=user))
    persisted: list[dict] = []

    async def append_visible(*args, **kwargs):
        persisted.append({"message_id": kwargs["message_id"]})

    monkeypatch.setattr(module.assistant_repository, "list_messages", AsyncMock(
        side_effect=lambda *args, **kwargs: list(persisted)
    ))
    append_message = AsyncMock(side_effect=append_visible)
    monkeypatch.setattr(module.assistant_repository, "append_message", append_message)

    await worker._execute_child(child, user, asyncio.Event())

    assert [invocation.tool_name for invocation in tool.runs] == ["query_execute"]
    assert child["status"] == "completed"
    assert child["checkpoint"]["evidence_tables"][0]["rows"] == table["rows"]
    assert child["checkpoint"]["needs_data"] is True
    assert "999" not in child["result_summary"]
    assert "| Mobile App | 125.00 | 0.37 | 3 |" in child["result_summary"]
    kinds = [item["event_type"] for item in repository.activity]
    assert "plan" in kinds
    assert "thinking" in kinds
    assert "tool_call" in kinds
    assert "tool_detail" in kinds
    assert "table" in kinds
    assert kinds[-1] == "answer"
    assert repository.activity[-1]["text"] == child["result_summary"]
    assert repository.events[-1] == ("sales-eval", "agent_completed")

    root["status"] = "running"
    if replan_failure:
        root["checkpoint"] = {"processed_child_ids": []}
        monkeypatch.setattr(
            module.auto_planner,
            "plan",
            AsyncMock(
                side_effect=RuntimeError("Agent capability query returned inconsistent rows")
            ),
        )
    await worker._coordinate(root, user, asyncio.Event())

    assert root["status"] == "completed"
    assert provider.calls == 3
    assert provider.synthesis_messages is not None
    findings = json.loads(provider.synthesis_messages[1]["content"])["findings"]
    assert "| Mobile App | 125.00 | 0.37 | 3 |" in findings[0]["summary"]
    assert append_message.await_count == 1
    answer = append_message.await_args.kwargs["content"]
    assert "999" not in answer
    assert "leads every metric" not in answer
    assert "| Mobile App | 125,00 | 37,00% | 3 |" in answer
    assert "| WhatsApp B2B | 60,00 | 39,00% | 1 |" in answer
    assert "recognized revenue: tertinggi Mobile App; terendah WhatsApp B2B" in answer
    assert "gross margin pct: tertinggi WhatsApp B2B; terendah Website" in answer
    assert "order count: tertinggi Marketplace; terendah Website" in answer
    if replan_failure:
        assert ("root-eval", "delegation_plan") in repository.events
    assert repository.events[-1] == ("root-eval", "agent_completed")
