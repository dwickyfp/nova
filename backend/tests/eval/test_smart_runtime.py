"""Scripted providers drive the production worker, control tools, and shared assistant loop."""

from __future__ import annotations

import asyncio
import copy
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.collaboration_tools import COLLABORATION_TOOLS, register_collaboration_tools
from app.modules.agents.harness_worker import AgentHarnessWorker
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, thread
from tests.eval.harness import EvalTool, TurnResult
from tests.unit.test_smart_collaboration import USER
from tests.unit.test_smart_collaboration import collaboration as collaboration


def call(name: str, **arguments) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": name,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


@pytest.mark.asyncio
async def test_worker_checkpoints_native_decimal_results_without_losing_precision(
    collaboration, monkeypatch,
):
    from app.modules.agents import harness_worker as module

    repo, root = collaboration
    spawned = await root.spawn_agent(
        agent="finance", task_name="native", objective="Revenue", operation_id="native"
    )
    query = EvalTool("semantic_query", parameters={"type": "object", "properties": {
        "question": {"type": "string"}}, "required": ["question"]},
        table={"columns": ["revenue"], "rows": [[Decimal("3368049065451.00")]]},
        data={"semantic_plan": {"metrics": ["revenue"]}, "sql": "SELECT revenue FROM sales"})

    async def build(agent):
        registry = ToolRegistry()
        registry.register(query)
        return registry, "Use the semantic query for revenue.", 30, 24000

    monkeypatch.setattr(module.agent_service, "build_loop_inputs", build)
    monkeypatch.setattr(module.agent_repository, "get_agent", AsyncMock(return_value={
        "agent_id": "finance", "owner_name": "alice", "policy": "auto_read_only",
    }))
    monkeypatch.setattr(module, "has_verified_access", AsyncMock(return_value=True))
    monkeypatch.setattr(module.memory_repository, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "assistant_provider", ScriptedProvider([
        call("semantic_query", question="Revenue"), text_frame("Revenue is 3368049065451.00"),
    ]))
    worker = AgentHarnessWorker(repo)
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value=USER))
    await worker.process(spawned["current_turn_id"], "test")
    result = await repo.get(spawned["current_turn_id"])
    assert result["status"] == "completed", repo.events[-6:]
    evidence = result["checkpoint"]["verified_evidence"]
    assert evidence["tables"]["evidence_1"]["rows"] == [["3368049065451.00"]]
    json.dumps(result["checkpoint"])


@pytest.mark.asyncio
async def test_root_uses_coordinator_token_budget_for_final_synthesis(collaboration, monkeypatch):
    from app.modules.agents import harness_worker as module

    repo, root = collaboration
    repo.runs[root.root_id]["status"] = "queued"
    answer = text_frame("The table stores rows; a view defines a query.")
    answer["usage"] = {"prompt_tokens": 21000, "completion_tokens": 10, "total_tokens": 21010}
    monkeypatch.setattr(module, "assistant_provider", ScriptedProvider([answer]))
    monkeypatch.setattr(module.agent_service, "build_loop_inputs", AsyncMock(return_value=(
        ToolRegistry(), "Explain database concepts.", 30, 30000,
    )))
    monkeypatch.setattr(module.memory_repository, "list", AsyncMock(return_value=[]))
    saved = []

    async def append(*args, **kwargs):
        saved.append(kwargs)
        return kwargs

    monkeypatch.setattr(module.assistant_repository, "append_message", append)
    monkeypatch.setattr(module.assistant_repository, "list_messages",
                        AsyncMock(side_effect=lambda *args, **kwargs: saved))
    worker = AgentHarnessWorker(repo)
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value=USER))
    await worker.process(root.root_id, "test")
    assert (await repo.get(root.root_id))["status"] == "completed", repo.events[-6:]
    assert saved[0]["content"] == answer["content"]


@pytest.mark.asyncio
async def test_golden_worker_trajectory(collaboration, monkeypatch):
    from app.modules.agents import harness_worker as module

    repo, root = collaboration
    root_id = root.root_id
    repo.runs[root_id]["status"] = "queued"
    root_targets = ["/root/finance", "/root/marketing"]
    scripts = {
        "/root": [
            call("discover_agents", capability="revenue and marketing"),
            call("spawn_agent", agent="finance", task_name="finance", objective="Quantify revenue"),
            call(
                "spawn_agent",
                agent="marketing",
                task_name="marketing",
                objective="Evaluate campaigns",
            ),
            call("wait_agent", targets=root_targets, timeout=2),
            call("wait_agent", targets=root_targets, timeout=2),
            call("list_agents"),
            text_frame(
                "Finance's regional revenue finding aligns with Marketing's cohort evidence; "
                "campaign association requires further validation."
            ),
        ],
        "/root/finance": [
            call(
                "send_message",
                target="/root/marketing",
                content="Jakarta is the largest contributor to the revenue decline",
            ),
            text_frame("Revenue decline is concentrated in Jakarta"),
        ],
        "/root/marketing": [
            call(
                "spawn_agent",
                agent="cohort",
                task_name="cohort",
                objective="Check Jakarta acquisition",
            ),
            call(
                "wait_agent",
                targets=["/root/marketing/cohort"],
                timeout=2,
            ),
            call(
                "send_message",
                target="/root/finance",
                content="Jakarta acquisition conversion also declined",
            ),
            call("followup_task", target="/root/finance", task="Correlate campaign evidence"),
            call(
                "spawn_agent", agent="forecast", task_name="forecast", objective="Optional forecast"
            ),
            call(
                "interrupt_agent",
                target="/root/marketing/forecast",
            ),
            call("wait_agent", targets=["/root/finance"], timeout=2),
            text_frame("Marketing cohort evidence confirms weaker acquisition in Jakarta"),
        ],
        "/root/marketing/cohort": [text_frame("Jakarta acquisition conversion declined")],
        "/root/marketing/forecast": [
            call("wait_agent", targets=[], timeout=2),
            text_frame("Optional forecast"),
        ],
        "/root/finance:followup": [
            call("list_agents"),
            text_frame("Enterprise revenue findings align with the campaign cohort"),
        ],
    }
    snapshots: dict[str, list[list[dict]]] = {}
    steering_sent = False

    class TeamProvider(ScriptedProvider):
        async def plan_turn(self, *, user_content, available_tools):
            return {"intent": "direct_answer", "tools": available_tools, "required_tools": []}

        async def stream(self, *, messages, **kwargs):
            nonlocal steering_sent
            path = re.search(r"You are (/root[^ ]*) in a governed", str(messages)).group(1)
            if path == "/root/finance" and "Correlate campaign evidence" in str(messages):
                path += ":followup"
                if not steering_sent:
                    steering_sent = True
                    await root.send_message(
                        target="/root/finance",
                        content="Prioritize enterprise customers",
                        operation_id="user-steering",
                        origin="user",
                    )
            if path == "/root/finance":
                async with repo.changed:
                    await asyncio.wait_for(
                        repo.changed.wait_for(
                            lambda: any(
                                row["payload"].get("agent_path") == "/root/marketing"
                                for row in repo.runs.values()
                            )
                        ),
                        2,
                    )
            snapshots.setdefault(path, []).append(copy.deepcopy(messages))
            script = scripts[path]
            message = script.pop(0) if len(script) > 1 else script[0]
            if message.get("content"):
                yield "delta", message["content"]
            yield "message", message

    provider = TeamProvider([])
    monkeypatch.setattr(module, "assistant_provider", provider)
    monkeypatch.setattr(
        module.agent_repository,
        "get_agent",
        AsyncMock(
            side_effect=lambda agent_id, **kwargs: {
                "agent_id": agent_id,
                "owner_name": "alice",
                "policy": "auto_read_only",
            }
        ),
    )
    monkeypatch.setattr(module, "has_verified_access", AsyncMock(return_value=True))
    monkeypatch.setattr(
        module.agent_service,
        "build_loop_inputs",
        AsyncMock(side_effect=lambda agent: (ToolRegistry(), "Evidence-based analysis", 30, 24000)),
    )
    monkeypatch.setattr(module.memory_repository, "list", AsyncMock(return_value=[]))
    saved_messages = []

    async def append_message(*args, **kwargs):
        saved_messages.append(kwargs)
        return kwargs

    monkeypatch.setattr(
        module.assistant_repository,
        "list_messages",
        AsyncMock(side_effect=lambda *a, **kw: saved_messages),
    )
    monkeypatch.setattr(module.assistant_repository, "append_message", append_message)
    worker = AgentHarnessWorker(repo)
    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value=USER))
    tasks: dict[str, asyncio.Task] = {}

    async def drive():
        while repo.runs[root_id]["status"] not in {"completed", "failed", "interrupted"}:
            for row in list(repo.runs.values()):
                if row["status"] == "queued" and row["run_id"] not in tasks:
                    tasks[row["run_id"]] = asyncio.create_task(
                        worker.process(row["run_id"], "test")
                    )
            async with repo.changed:
                revision = repo.revision
                await asyncio.wait_for(
                    repo.changed.wait_for(lambda revision=revision: repo.revision != revision), 3
                )
        await asyncio.gather(*tasks.values())

    await asyncio.wait_for(drive(), 15)
    assert repo.runs[root_id]["status"] == "completed", repo.events
    assert (
        saved_messages
        and "Finance" in saved_messages[0]["content"]
        and "Marketing" in saved_messages[0]["content"]
    )
    sessions = await root.list_agents()
    finance = next(item for item in sessions if item["agent_path"] == "/root/finance")
    assert finance["turn_count"] == 2
    assert any(item["depth"] == 2 for item in sessions)
    assert "Prioritize enterprise customers" in str(snapshots["/root/finance:followup"][-1])
    assert "Jakarta is the largest contributor" in str(snapshots["/root/marketing"])
    assert any(event["type"] == "agent_interrupted" for event in repo.events)
    assert len(repo.runs) == len(set(repo.runs)) == 6
    assert len({row["message_id"] for row in repo.messages.values()}) == len(repo.messages)
    from app.modules.agents import router

    monkeypatch.setattr(router, "harness_repository", repo)
    # Scope validation is covered separately; exercise the production tree projection here.
    monkeypatch.setattr(
        router, "_scoped_auto_root", AsyncMock(return_value=await repo.get(root_id))
    )
    response = await router.get_auto_run_tree(root_id, USER)
    assert len(response["sessions"]) == 5
    assert len(response["runs"]) == 6
    assert any(item["agent_path"] == "/root/marketing/cohort" for item in response["sessions"])


@pytest.mark.asyncio
async def test_loop_restores_checkpoint_without_repeating_spawn(collaboration):
    repo, root = collaboration
    registry = ToolRegistry()
    register_collaboration_tools(registry, root)
    saved = {}

    async def checkpoint(state):
        saved.clear()
        saved.update(copy.deepcopy(state))
        if state["iteration"] == 1:
            raise ConnectionError("worker crash")

    batch = call("spawn_agent", agent="finance", task_name="finance", objective="Analyze finance")
    batch["tool_calls"] += call(
        "spawn_agent", agent="marketing", task_name="marketing", objective="Analyze marketing"
    )["tool_calls"]
    batch["tool_calls"][1]["id"] = "spawn-marketing"
    first = AssistantLoop(
        provider=ScriptedProvider([batch, text_frame("Complete")]), registry=registry
    )
    context = LoopContext(
        user_name="alice", collaboration_tools=tuple(COLLABORATION_TOOLS), collaboration_root=True
    )
    with pytest.raises(ConnectionError):
        _ = [
            frame
            async for frame in first.run(
                thread=thread(read_only_grant=True),
                user_content="Help",
                context=context,
                resolve_consent=AsyncMock(return_value=True),
                save_state=checkpoint,
            )
        ]
    assert len(repo.runs) == 2
    assert len(saved["deferred_calls"]) == 1
    restored = AssistantLoop(
        provider=ScriptedProvider([text_frame("The delegated work is available in the tree")]),
        registry=registry,
    )
    frames = [
        frame
        async for frame in restored.run(
            thread=thread(read_only_grant=True),
            user_content="Help",
            context=context,
            resolve_consent=AsyncMock(return_value=True),
            resume_state=saved,
        )
    ]
    assert TurnResult(frames=frames).finish_reason == "stop"
    assert len(repo.runs) == 3
    assert len({row["payload"]["agent_path"] for row in repo.runs.values()}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("after_consume_checkpoint", [False, True])
async def test_nested_worker_restart_preserves_mailbox_and_completes_root(
    collaboration, monkeypatch, after_consume_checkpoint
):
    from app.modules.agents import harness_worker as module

    repo, root = collaboration
    repo.runs[root.root_id]["status"] = "queued"
    crashed = False
    observed = []

    class RestartProvider(ScriptedProvider):
        async def plan_turn(self, *, user_content, available_tools):
            return {"intent": "direct_answer", "tools": available_tools, "required_tools": []}

        async def stream(self, *, messages, **kwargs):
            nonlocal crashed
            path = re.search(r"You are (/root[^ ]*) in a governed", str(messages)).group(1)
            history = str(messages)
            if path == "/root":
                if not any(row["depth"] == 1 for row in repo.runs.values()):
                    message = call(
                        "spawn_agent",
                        agent="finance",
                        task_name="finance",
                        objective="Assess revenue",
                    )
                elif not any(
                    row["depth"] == 1 and row["status"] == "completed" for row in repo.runs.values()
                ):
                    message = call("wait_agent", targets=["/root/finance"], timeout=2)
                else:
                    message = text_frame(
                        "Finance and its forecast evidence indicate the same regional trend."
                    )
            elif path == "/root/finance":
                if not any(row["depth"] == 2 for row in repo.runs.values()):
                    message = call(
                        "spawn_agent",
                        agent="forecast",
                        task_name="forecast",
                        objective="Forecast regional trend",
                    )
                elif not crashed:
                    async with repo.changed:
                        await asyncio.wait_for(
                            repo.changed.wait_for(
                                lambda: any(
                                    row["content"] == "Forecast evidence survives restart"
                                    for row in repo.messages.values()
                                )
                            ),
                            3,
                        )
                    if (
                        after_consume_checkpoint
                        and "Forecast evidence survives restart" not in history
                    ):
                        message = call("list_agents")
                    else:
                        crashed = True
                        raise asyncio.CancelledError(
                            "worker stopped at the selected mailbox boundary"
                        )
                else:
                    observed.append(history)
                    message = text_frame(
                        "Finance confirms the regional trend using the retained forecast evidence"
                    )
            else:
                tool_messages = [m for m in messages if m.get("role") == "tool"]
                message = (
                    text_frame("Forecast confirms the regional trend")
                    if tool_messages
                    else call(
                        "send_message", target="..", content="Forecast evidence survives restart"
                    )
                )
            if message.get("content"):
                yield "delta", message["content"]
            yield "message", message

    monkeypatch.setattr(module, "assistant_provider", RestartProvider([]))
    monkeypatch.setattr(
        module.agent_repository,
        "get_agent",
        AsyncMock(
            side_effect=lambda agent_id, **kwargs: {
                "agent_id": agent_id,
                "owner_name": "alice",
                "policy": "auto_read_only",
            }
        ),
    )
    monkeypatch.setattr(module, "has_verified_access", AsyncMock(return_value=True))
    monkeypatch.setattr(
        module.agent_service,
        "build_loop_inputs",
        AsyncMock(side_effect=lambda agent: (ToolRegistry(), "Use verified evidence", 30, 24000)),
    )
    monkeypatch.setattr(module.memory_repository, "list", AsyncMock(return_value=[]))
    saved = []

    async def append(*args, **kwargs):
        saved.append(kwargs)
        return kwargs

    monkeypatch.setattr(
        module.assistant_repository, "list_messages", AsyncMock(side_effect=lambda *a, **kw: saved)
    )
    monkeypatch.setattr(module.assistant_repository, "append_message", append)
    pending = {}
    recovered = []

    async def drive():
        while repo.runs[root.root_id]["status"] not in {"completed", "failed", "interrupted"}:
            for row in list(repo.runs.values()):
                if row["status"] == "queued" and row["run_id"] not in pending:
                    worker = AgentHarnessWorker(repo)
                    monkeypatch.setattr(worker, "_user_for", AsyncMock(return_value=USER))
                    pending[row["run_id"]] = asyncio.create_task(
                        worker.process(row["run_id"], "test")
                    )
            revision = repo.revision

            async def changed(revision=revision):
                async with repo.changed:
                    await repo.changed.wait_for(lambda: repo.revision != revision)

            wake = asyncio.create_task(changed())
            done, _ = await asyncio.wait(
                [*pending.values(), wake], return_when=asyncio.FIRST_COMPLETED
            )
            if wake not in done:
                wake.cancel()
            await asyncio.gather(wake, return_exceptions=True)
            done.discard(wake)
            for task in done:
                run_id = next(key for key, value in pending.items() if value is task)
                del pending[run_id]
                if task.cancelled():
                    row = repo.runs[run_id]
                    assert row["checkpoint"]["loop"]["safe_to_resume"]
                    row["updated_at"] = datetime.now(UTC).replace(tzinfo=None) - timedelta(
                        seconds=120
                    )
                    recovered.extend(await repo.recover_stale())
                else:
                    task.result()
        await asyncio.gather(*pending.values())

    try:
        await asyncio.wait_for(drive(), 15)
    except TimeoutError:

        def stack(task):
            value = task.get_coro()
            frames = []
            while value:
                frames.append(str(value))
                value = getattr(value, "cr_await", None) or getattr(value, "ag_await", None)
            return frames

        pytest.fail(
            str(
                {
                    "runs": [
                        (row["payload"]["agent_path"], row["status"], row.get("error_class"))
                        for row in repo.runs.values()
                    ],
                    "pending": {key: stack(task) for key, task in pending.items()},
                    "events": repo.events[-3:],
                    "crashed": crashed,
                    "recovered": recovered,
                }
            )
        )
    assert crashed and len(recovered) == 1
    assert repo.runs[root.root_id]["status"] == "completed", repo.events
    assert len(repo.runs) == 3
    assert "Forecast evidence survives restart" in observed[-1]
    assert observed[-1].count("Forecast evidence survives restart") == 1
    forecast = next(row for row in repo.runs.values() if row["depth"] == 2)
    assert (
        sum(
            row["sender_run_id"] == forecast["run_id"] and row["message_type"] == "final"
            for row in repo.messages.values()
        )
        == 1
    )
    assert (
        sum(
            event["run_id"] == forecast["run_id"] and event["type"] == "agent_completed"
            for event in repo.events
        )
        == 1
    )
    assert (
        sum(
            row["content"] == "Forecast evidence survives restart" for row in repo.messages.values()
        )
        == 1
    )
    assert len(saved) == 1
