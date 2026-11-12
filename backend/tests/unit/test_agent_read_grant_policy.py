"""A saved user approval and the agent's current read policy have different lifetimes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.redis import session_store
from app.modules.agents import router
from app.modules.assistant import events, read_grants
from app.modules.assistant.schemas import AgentMessageRequest
from app.modules.assistant.state import thread_store
from app.modules.assistant.tools import ToolRegistry
from tests.unit.test_neva_read_grants import FakeRedis


@pytest.mark.parametrize("explicit_approval", [False, True])
async def test_agent_policy_and_login_changes_recheck_the_current_approval(
    monkeypatch, explicit_approval,
):
    redis = FakeRedis()
    monkeypatch.setattr(session_store, "_redis", redis)
    agent = {"agent_id": "fixture", "name": "Fixture", "owner_name": "alice",
             "policy": "auto_read_only"}
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(
        router, "_require_agent_thread", AsyncMock(return_value={"title": "Fixture"}),
    )
    monkeypatch.setattr(router.agent_service, "build_loop_inputs", AsyncMock(
        return_value=(ToolRegistry(), "System", 60, None),
    ))
    monkeypatch.setattr(router, "_generate_thread_title", AsyncMock(return_value="Fixture"))
    monkeypatch.setattr(router.assistant_repository, "list_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.assistant_repository, "rename_thread", AsyncMock())
    monkeypatch.setattr(router.assistant_repository, "append_message", AsyncMock())
    monkeypatch.setattr(
        router.assistant_repository, "learning_enabled", AsyncMock(return_value=False),
    )
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    for operation in ("start", "append_batch", "finish"):
        monkeypatch.setattr(router.run_journal, operation, AsyncMock())
    grants = []

    class ScriptedLoop:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *, thread, context, **_kwargs):
            grants.append(thread.consent.covers("read_only", user=context.user))
            assert not thread.consent.covers("destructive", user=context.user)
            yield events.text_delta("Synthetic answer")
            yield events.done("reply", finish_reason="stop")

    monkeypatch.setattr(router, "AssistantLoop", ScriptedLoop)
    user = {"username": "alice", "active_role": "analyst", "roles": ["analyst", "sales"],
            "session_id": "login-one", "security_context_version": 1}
    thread_id = "read-grant-policy-thread"

    async def turn(identity):
        response = await router.send_agent_message(
            "fixture", thread_id, AgentMessageRequest(content="Synthetic question"),
            SimpleNamespace(is_disconnected=AsyncMock(return_value=False)), identity,
        )
        async with asyncio.timeout(1):
            frames = [frame async for frame in response.body_iterator]
            await asyncio.gather(*list(router._active_run_tasks))
        assert '"finish_reason":"stop"' in frames[-1]

    try:
        await turn(user)
        assert grants == [True]
        assert not redis.data
        if explicit_approval:
            runtime = thread_store.get(thread_id, user_name="alice")
            await read_grants.grant(runtime, user, thread_id, True)
        agent["policy"] = "ask_every_tool"
        await turn(user)
        await turn({**user, "session_id": "login-two"})
        await turn(user)
        await turn({**user, "active_role": "sales", "security_context_version": 2})
        assert grants == [True, explicit_approval, False, explicit_approval, False]
    finally:
        thread_store.remove(thread_id, user_name="alice")


async def test_overlapping_logins_do_not_share_live_consent(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(session_store, "_redis", redis)
    agent = {"agent_id": "fixture", "name": "Fixture", "owner_name": "alice",
             "policy": "auto_read_only"}
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(
        router, "_require_agent_thread", AsyncMock(return_value={"title": "Fixture"}),
    )
    monkeypatch.setattr(router.agent_service, "build_loop_inputs", AsyncMock(
        return_value=(ToolRegistry(), "System", 60, None),
    ))
    monkeypatch.setattr(router, "_generate_thread_title", AsyncMock(return_value="Fixture"))
    monkeypatch.setattr(router.assistant_repository, "list_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.assistant_repository, "rename_thread", AsyncMock())
    monkeypatch.setattr(router.assistant_repository, "append_message", AsyncMock())
    monkeypatch.setattr(
        router.assistant_repository, "learning_enabled", AsyncMock(return_value=False),
    )
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    for operation in ("start", "append_batch", "finish"):
        monkeypatch.setattr(router.run_journal, operation, AsyncMock())
    agent["policy"] = "ask_every_tool"
    entered = asyncio.Event()
    release = asyncio.Event()
    observed = {}

    class ScriptedLoop:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *, thread, context, **_kwargs):
            login = context.user["session_id"]
            if login == "login-one":
                entered.set()
                await release.wait()
            observed[login] = thread.consent.covers("read_only", user=context.user)
            yield events.text_delta("Synthetic answer")
            yield events.done("reply", finish_reason="stop")

    monkeypatch.setattr(router, "AssistantLoop", ScriptedLoop)
    user = {"username": "alice", "active_role": "analyst", "roles": ["analyst"],
            "session_id": "login-one", "security_context_version": 1}
    other = {**user, "session_id": "login-two"}
    thread_id = "overlapping-read-grant-thread"

    async def turn(identity):
        response = await router.send_agent_message(
            "fixture", thread_id, AgentMessageRequest(content="Synthetic question"),
            SimpleNamespace(is_disconnected=AsyncMock(return_value=False)), identity,
        )
        return [frame async for frame in response.body_iterator]

    first = None
    try:
        await read_grants.remember(other, thread_id, True)
        async with asyncio.timeout(2):
            first = asyncio.create_task(turn(user))
            await entered.wait()
            await turn(other)
            release.set()
            await first
        assert observed == {"login-one": False, "login-two": True}
    finally:
        release.set()
        if first is not None:
            await first
        await asyncio.gather(*list(router._active_run_tasks))
        thread_store.remove(thread_id, user_name="alice")
