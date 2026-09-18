"""Assistant Stage B tests — module skeleton, loop, state, and event contract.

These are unit tests: no network, no StarRocks. The provider is faked and the
tool registry is driven with a stub tool, so the loop's boundedness and consent
rules are exercised directly.

Coverage mirrors the spec's acceptance criteria for T-B1/T-B2/T-B3:

* endpoints require auth (checked via the router's dependency graph);
* provider config is read through the masked path and never returned;
* the iteration cap and time budget terminate a runaway turn;
* a read-only grant auto-approves, a destructive call never does;
* a denied call is surfaced and the turn ends without a retry;
* the SSE frames match the frozen event names.
"""

from __future__ import annotations

import json

import pytest

from app.modules.assistant import events
from app.modules.assistant.consent import ConsentBroker, ConsentConflictError
from app.modules.assistant.provider import AssistantProviderClient, AssistantProviderError
from app.modules.assistant.schemas import ToolCallView
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import (
    AssistantMessage,
    AssistantThread,
    ConsentPolicy,
    ThreadStore,
)
from app.modules.assistant.tools import (
    ToolInvocation,
    ToolOutcome,
    ToolRegistry,
)

# ── Fakes ────────────────────────────────────────────────────────────────────


class FakeProvider:
    """Returns queued assistant messages instead of calling a real LLM."""

    def __init__(self, script: list[dict]) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    async def resolve(self):
        from app.modules.assistant.provider import ProviderConfig

        return ProviderConfig(
            provider_id="p1", model="m1", endpoint="http://x/v1/chat/completions", api_key="k"
        )

    async def complete(self, *, messages, tools=None, provider=None):
        self.calls.append({"messages": messages, "tools": tools})
        if not self._script:
            return {"role": "assistant", "content": "done"}
        return self._script.pop(0)


def _tool_call(call_id: str, name: str = "query_execute", args: dict | None = None) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {"sql": "SELECT 1"})},
    }


class StubTool:
    def __init__(self, classification: str = "read_only", ok: bool = True) -> None:
        self.name = "query_execute"
        self.classification = classification
        self.description = "run sql"
        self.parameters = {"type": "object", "properties": {"sql": {"type": "string"}}}
        self.invocations: list[ToolInvocation] = []
        self._ok = ok

    def preview(self, invocation: ToolInvocation) -> str:
        return invocation.arguments.get("sql", "")

    async def run(self, invocation, context):
        self.invocations.append(invocation)
        if self._ok:
            return ToolOutcome(ok=True, summary="1 row")
        return ToolOutcome(ok=False, summary="", error="permission denied")


def _thread() -> AssistantThread:
    return AssistantThread(thread_id="t1", user_name="alice", title="T")


async def _collect(agen) -> list[str]:
    return [frame async for frame in agen]


def _frame_event(frame: str) -> str:
    return frame.splitlines()[0].removeprefix("event: ")


def _frame_data(frame: str) -> dict:
    for line in frame.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return {}


# ── Event contract ───────────────────────────────────────────────────────────


def test_event_names_and_shapes_are_frozen():
    assert _frame_event(events.text_delta("hi")) == "text_delta"
    assert _frame_data(events.text_delta("hi")) == {"text": "hi"}

    view = ToolCallView(
        tool_call_id="c1", tool_name="query_execute", sql_preview="SELECT 1"
    )
    assert _frame_event(events.tool_call(view)) == "tool_call"
    assert _frame_data(events.tool_call(view))["status"] == "pending"

    assert _frame_event(events.tool_status("c1", "done")) == "tool_status"
    assert _frame_data(events.tool_status("c1", "done")) == {
        "tool_call_id": "c1",
        "status": "done",
    }

    assert _frame_event(events.done("m1")) == "done"
    assert _frame_data(events.done("m1"))["finish_reason"] == "stop"

    assert _frame_event(events.error("x", "y")) == "error"
    assert _frame_data(events.error("x", "y")) == {"code": "x", "message": "y"}

    assert _frame_event(events.ping()) == "ping"


# ── State (E5a) ──────────────────────────────────────────────────────────────


def test_thread_store_scopes_threads_to_owner():
    store = ThreadStore()
    thread = store.create(user_name="alice", title="A")
    assert store.get(thread.thread_id, user_name="alice") is thread
    assert store.get(thread.thread_id, user_name="bob") is None
    assert store.delete(thread.thread_id, user_name="bob") is False
    assert store.delete(thread.thread_id, user_name="alice") is True
    assert store.get(thread.thread_id, user_name="alice") is None


def test_consent_policy_only_covers_read_only():
    policy = ConsentPolicy(always_allow_read_only=True)
    assert policy.covers("read_only") is True
    assert policy.covers("destructive") is False
    assert policy.covers("denied") is False

    assert ConsentPolicy(always_allow_read_only=False).covers("read_only") is False


def test_thread_state_is_not_persisted_anywhere():
    """E5a: state is in-memory only; a new store starts empty."""
    store = ThreadStore()
    store.create(user_name="alice")
    assert len(store.list_for_user("alice")) == 1
    assert ThreadStore().list_for_user("alice") == []


# ── Loop boundedness ─────────────────────────────────────────────────────────


async def test_loop_emits_text_and_done_on_a_plain_answer():
    provider = FakeProvider([{"role": "assistant", "content": "hello"}])
    loop = AssistantLoop(provider=provider, registry=ToolRegistry())
    frames = await _collect(
        loop.run(
            thread=_thread(),
            user_content="hi",
            context=LoopContext(user_name="alice"),
            resolve_consent=lambda inv, cls: _allow(),
        )
    )
    assert [_frame_event(f) for f in frames] == ["text_delta", "done"]
    assert _frame_data(frames[0])["text"] == "hello"


# ── NOVA-69: exactly one user message per turn ───────────────────────────────


async def test_router_stored_user_message_is_not_duplicated_to_the_provider():
    """The router stores the turn's user message before calling the loop.

    The provider must still receive it exactly once (NOVA-69).
    """
    provider = FakeProvider([{"role": "assistant", "content": "ok"}])
    loop = AssistantLoop(provider=provider, registry=ToolRegistry())
    thread = _thread()
    # What send_message does before invoking the loop.
    thread.messages.append(
        AssistantMessage(message_id="u1", role="user", content="SELECT 1")
    )

    await _collect(
        loop.run(
            thread=thread,
            user_content="SELECT 1",
            context=LoopContext(user_name="alice"),
            resolve_consent=lambda inv, cls: _allow(),
        )
    )

    sent = provider.calls[0]["messages"]
    user_entries = [m for m in sent if m["role"] == "user"]
    assert len(user_entries) == 1
    assert user_entries[0]["content"] == "SELECT 1"


async def test_two_turns_do_not_duplicate_or_lose_user_messages():
    provider = FakeProvider(
        [
            {"role": "assistant", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]
    )
    loop = AssistantLoop(provider=provider, registry=ToolRegistry())
    thread = _thread()

    # Turn 1
    thread.messages.append(
        AssistantMessage(message_id="u1", role="user", content="one")
    )
    await _collect(
        loop.run(
            thread=thread,
            user_content="one",
            context=LoopContext(user_name="alice"),
            resolve_consent=lambda inv, cls: _allow(),
        )
    )
    thread.messages.append(
        AssistantMessage(message_id="a1", role="assistant", content="first")
    )

    # Turn 2
    thread.messages.append(
        AssistantMessage(message_id="u2", role="user", content="two")
    )
    await _collect(
        loop.run(
            thread=thread,
            user_content="two",
            context=LoopContext(user_name="alice"),
            resolve_consent=lambda inv, cls: _allow(),
        )
    )

    second = provider.calls[1]["messages"]
    user_entries = [m["content"] for m in second if m["role"] == "user"]
    assert user_entries == ["one", "two"]


async def test_build_messages_keeps_a_genuine_repeat_question():
    """Two identical questions in a row are two turns, not one duplicated."""
    loop = AssistantLoop(provider=FakeProvider([]), registry=ToolRegistry())
    thread = _thread()
    thread.messages.append(
        AssistantMessage(message_id="u1", role="user", content="same")
    )
    thread.messages.append(
        AssistantMessage(message_id="a1", role="assistant", content="answer")
    )
    thread.messages.append(
        AssistantMessage(message_id="u2", role="user", content="same")
    )
    messages = loop._build_messages(thread, "same")
    user_entries = [m["content"] for m in messages if m["role"] == "user"]
    assert user_entries == ["same", "same"]


async def test_loop_stops_at_the_iteration_cap():
    # A model that always proposes a tool call with a tool that succeeds: the
    # loop must stop at the cap rather than run forever.
    provider = FakeProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call(f"c{i}")]}
            for i in range(20)
        ]
    )
    registry = ToolRegistry()
    registry.register(StubTool())
    loop = AssistantLoop(provider=provider, registry=registry, max_iterations=3)

    frames = await _collect(
        loop.run(
            thread=_thread(),
            user_content="go",
            context=LoopContext(user_name="alice"),
            resolve_consent=lambda inv, cls: _allow(),
        )
    )
    assert _frame_event(frames[-1]) == "done"
    assert _frame_data(frames[-1])["finish_reason"] == "iteration_cap"
    assert len(provider.calls) == 3


async def test_loop_enforces_time_budget(monkeypatch):
    provider = FakeProvider(
        [{"role": "assistant", "content": "", "tool_calls": [_tool_call("c1")]} for _ in range(20)]
    )
    registry = ToolRegistry()
    registry.register(StubTool())
    loop = AssistantLoop(
        provider=provider, registry=registry, max_iterations=20, time_budget_seconds=0.0
    )
    frames = await _collect(
        loop.run(
            thread=_thread(),
            user_content="go",
            context=LoopContext(user_name="alice"),
            resolve_consent=lambda inv, cls: _allow(),
        )
    )
    assert _frame_event(frames[-1]) == "done"
    assert _frame_data(frames[-1])["finish_reason"] == "timeout"


# ── Consent (E2b) ────────────────────────────────────────────────────────────


async def test_read_only_grant_skips_the_approval_prompt():
    provider = FakeProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1")]},
            {"role": "assistant", "content": "answer"},
        ]
    )
    registry = ToolRegistry()
    registry.register(StubTool(classification="read_only"))
    loop = AssistantLoop(provider=provider, registry=registry)
    thread = _thread()
    thread.consent.always_allow_read_only = True

    asked = []

    async def resolver(inv, cls):
        asked.append(inv)
        return True

    frames = await _collect(
        loop.run(
            thread=thread,
            user_content="go",
            context=LoopContext(user_name="alice"),
            resolve_consent=resolver,
        )
    )
    # No tool_call frame (auto-approved), but the tool ran.
    assert "tool_call" not in [_frame_event(f) for f in frames]
    assert asked == []


async def test_destructive_call_never_auto_approves_even_with_a_grant():
    provider = FakeProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1")]},
            {"role": "assistant", "content": "ok"},
        ]
    )
    registry = ToolRegistry()
    registry.register(StubTool(classification="destructive"))
    loop = AssistantLoop(provider=provider, registry=registry)
    thread = _thread()
    thread.consent.always_allow_read_only = True

    asked = []

    async def resolver(inv, cls):
        asked.append(cls)
        return True

    frames = await _collect(
        loop.run(
            thread=thread,
            user_content="go",
            context=LoopContext(user_name="alice"),
            resolve_consent=resolver,
        )
    )
    assert "tool_call" in [_frame_event(f) for f in frames]
    assert asked == ["destructive"]


async def test_denied_tool_call_is_surfaced_and_not_rerun():
    provider = FakeProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1")]},
            {"role": "assistant", "content": "understood"},
        ]
    )
    tool = StubTool()
    registry = ToolRegistry()
    registry.register(tool)
    loop = AssistantLoop(provider=provider, registry=registry)

    frames = await _collect(
        loop.run(
            thread=_thread(),
            user_content="go",
            context=LoopContext(user_name="alice"),
            resolve_consent=lambda inv, cls: _deny(),
        )
    )
    assert _frame_event(frames[0]) == "tool_call"
    assert any(
        _frame_event(f) == "tool_status" and _frame_data(f)["status"] == "denied"
        for f in frames
    )
    assert tool.invocations == []


async def test_tool_failure_terminates_the_turn():
    provider = FakeProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1")]},
            {"role": "assistant", "content": "should not be reached"},
        ]
    )
    registry = ToolRegistry()
    registry.register(StubTool(ok=False))
    loop = AssistantLoop(provider=provider, registry=registry)

    frames = await _collect(
        loop.run(
            thread=_thread(),
            user_content="go",
            context=LoopContext(user_name="alice"),
            resolve_consent=lambda inv, cls: _allow(),
        )
    )
    assert _frame_event(frames[-1]) == "done"
    assert _frame_data(frames[-1])["finish_reason"] == "error"
    assert _frame_event(frames[-2]) == "error"
    # Only one provider call: the failure did not lead to a retry loop.
    assert len(provider.calls) == 1


# ── Provider credential handling ─────────────────────────────────────────────


async def test_provider_resolve_skips_providers_without_a_readable_key(monkeypatch):
    client = AssistantProviderClient()

    async def fake_list_providers():
        return [
            {"id": "p1", "name": "NoKey", "is_active": True, "has_api_key": False},
            {
                "id": "p2",
                "name": "WithKey",
                "is_active": True,
                "has_api_key": True,
                "endpoint": "http://h:8000/v1",
            },
        ]

    async def fake_key(provider_id):
        return "secret-key" if provider_id == "p2" else None

    async def fake_models(provider_id):
        return [{"id": "m1", "name": "model-x", "is_active": True}]

    monkeypatch.setattr(
        "app.modules.assistant.provider.ai_service.list_providers", fake_list_providers
    )
    monkeypatch.setattr(
        "app.modules.assistant.provider.ai_service.get_provider_api_key", fake_key
    )
    monkeypatch.setattr(
        "app.modules.assistant.provider.ai_service.list_models", fake_models
    )

    config = await client.resolve()
    assert config.provider_id == "p2"
    assert config.model == "model-x"
    assert config.endpoint.endswith("/v1/chat/completions")


async def test_provider_raises_when_nothing_is_configured(monkeypatch):
    client = AssistantProviderClient()

    async def empty():
        return []

    monkeypatch.setattr("app.modules.assistant.provider.ai_service.list_providers", empty)
    with pytest.raises(AssistantProviderError):
        await client.resolve()


def test_chat_endpoint_composition_matches_ai_functions():
    compose = AssistantProviderClient._chat_endpoint
    assert compose("http://h:8000/v1") == "http://h:8000/v1/chat/completions"
    assert compose("http://h:8000/v1/chat/completions") == "http://h:8000/v1/chat/completions"
    assert compose("http://h:8000") == "http://h:8000/v1/chat/completions"


# ── Consent broker ───────────────────────────────────────────────────────────


async def test_consent_broker_resolves_a_waiting_future():
    broker = ConsentBroker()
    future = broker.open("c1", thread_id="t1", user_name="alice")
    assert broker.thread_for("c1") == "t1"
    assert broker.owner_of("c1") == ("t1", "alice")
    assert broker.resolve("c1", True, user_name="alice") is True
    assert await future is True
    assert broker.resolve("c1", True, user_name="alice") is False


async def test_consent_broker_cancels_all_on_shutdown():
    broker = ConsentBroker()
    future = broker.open("c1", thread_id="t1", user_name="alice")
    broker.cancel_all()
    assert await future is None


async def test_consent_broker_forgets_the_thread_once_resolved():
    broker = ConsentBroker()
    broker.open("c1", thread_id="t1", user_name="alice")
    broker.resolve("c1", True, user_name="alice")
    assert broker.thread_for("c1") is None


# ── NOVA-121: a duplicate tool_call_id must not clobber a pending entry ──────


async def test_consent_broker_rejects_a_duplicate_tool_call_id():
    """The second ``open`` for a pending id raises and touches nothing."""
    broker = ConsentBroker()
    first = broker.open("dup", thread_id="t1", user_name="alice")

    with pytest.raises(ConsentConflictError):
        broker.open("dup", thread_id="t2", user_name="bob")

    # The first entry is intact — same owner, same future.
    assert broker.owner_of("dup") == ("t1", "alice")
    assert broker.resolve("dup", True, user_name="alice") is True
    assert await first is True


async def test_duplicate_open_does_not_orphan_the_first_future():
    """The original future must still resolve — not hang to the loop budget."""
    broker = ConsentBroker()
    first = broker.open("dup", thread_id="t1", user_name="alice")
    with pytest.raises(ConsentConflictError):
        broker.open("dup", thread_id="t1", user_name="alice")

    assert first.done() is False
    broker.resolve("dup", False, user_name="alice")
    assert await first is False


async def test_clashing_id_is_reusable_once_the_first_entry_is_gone():
    """The conflict is only while pending; a resolved id can be opened again."""
    broker = ConsentBroker()
    broker.open("reuse", thread_id="t1", user_name="alice")
    broker.resolve("reuse", True, user_name="alice")

    second = broker.open("reuse", thread_id="t2", user_name="bob")
    assert broker.owner_of("reuse") == ("t2", "bob")
    assert broker.resolve("reuse", True, user_name="bob") is True
    assert await second is True


# ── NOVA-70: consent resolution is owner-scoped ──────────────────────────────


async def test_foreign_user_cannot_resolve_someone_elses_tool_call():
    """A non-owner must not approve or deny another user's pending call."""
    broker = ConsentBroker()
    victim = broker.open("victim-call", thread_id="alice-thread", user_name="alice")

    # Bob, a different authenticated user, tries to approve it.
    assert broker.resolve("victim-call", True, user_name="bob") is False
    assert victim.done() is False  # the entry is left for the real owner

    # Alice can still resolve it afterwards.
    assert broker.resolve("victim-call", True, user_name="alice") is True
    assert await victim is True


async def test_foreign_deny_does_not_terminate_the_owners_call():
    broker = ConsentBroker()
    victim = broker.open("victim-call", thread_id="alice-thread", user_name="alice")
    assert broker.resolve("victim-call", False, user_name="mallory") is False
    assert victim.done() is False
    assert broker.resolve("victim-call", True, user_name="alice") is True
    assert await victim is True


# ── Auth requirement ─────────────────────────────────────────────────────────


def test_every_assistant_route_requires_authentication():
    """No assistant endpoint may be reachable without ``get_current_user``."""
    from fastapi.routing import APIRoute

    from app.modules.assistant.router import router

    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        dependency_names = {
            getattr(dep.call, "__name__", "") for dep in route.dependant.dependencies
        }
        assert "get_current_user" in dependency_names, route.path


# ── helpers ──────────────────────────────────────────────────────────────────


async def _allow() -> bool:
    return True


async def _deny() -> bool:
    return False
