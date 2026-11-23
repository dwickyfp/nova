"""NOVA-70 — the consent decision endpoint must be owner-scoped.

QA reproduced an IDOR: ``POST /tool-calls/{id}/decision`` resolved a pending
call on ``tool_call_id`` alone, so any authenticated user who knew the id could
approve or deny another user's call. These tests drive the HTTP route with a
swappable ``get_current_user`` dependency to prove the fix at the boundary the
defect was found on — not just at the broker.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import deps as deps_module
from app.core.exceptions import register_exception_handlers
from app.modules.assistant.consent import ConsentBroker
from app.modules.assistant.router import resolve_tool_call
from app.modules.assistant.router import router as assistant_router
from app.modules.assistant.schemas import ConsentDecisionRequest
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import thread_store
from app.modules.assistant.tools import ToolInvocation, ToolOutcome, ToolRegistry


def _signed_in(username):
    return {
        "username": username, "session_id": "session-" + username,
        "active_role": "analyst", "roles": ["analyst"],
        "assigned_roles": ["analyst"], "security_context_version": 1,
    }


class _FakeRepo:
    """In-memory stand-in for ``assistant_repository``.

    These tests exercise the router's ownership boundary without a live engine.
    The fake mirrors the real repository's contract — every method is
    user-scoped — so the production code path is the one under test.
    """

    def __init__(self) -> None:
        self._threads: dict[str, dict] = {}
        self._messages: dict[str, list[dict]] = {}

    def seed(self, *, thread_id: str, user_name: str, title: str = "A") -> dict:
        from datetime import UTC, datetime

        now = datetime.now(UTC).replace(tzinfo=None)
        row = {
            "thread_id": thread_id,
            "user_name": user_name,
            "title": title,
            "workspace_file_id": None,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        }
        self._threads[thread_id] = row
        self._messages[thread_id] = []
        return row

    async def list_threads(self, *, user_name):
        return [t for t in self._threads.values() if t["user_name"] == user_name]

    async def create_thread(self, *, user_name, title=None, workspace_file_id=None):
        from uuid import uuid4

        return self.seed(
            thread_id=str(uuid4()), user_name=user_name, title=title or "New conversation"
        )

    async def get_thread(self, thread_id, *, user_name):
        row = self._threads.get(thread_id)
        if row is None or row["user_name"] != user_name:
            return None
        return row

    async def rename_thread(self, thread_id, title, *, user_name):
        row = await self.get_thread(thread_id, user_name=user_name)
        if row is None:
            return None
        row["title"] = title
        return row

    async def delete_thread(self, thread_id, *, user_name):
        row = await self.get_thread(thread_id, user_name=user_name)
        if row is None:
            return False
        del self._threads[thread_id]
        self._messages.pop(thread_id, None)
        return True

    async def list_messages(self, thread_id, *, user_name):
        row = await self.get_thread(thread_id, user_name=user_name)
        if row is None:
            return []
        return list(self._messages.get(thread_id, []))

    async def append_message(self, thread_id, *, user_name, role, content, message_id=None):
        from datetime import UTC, datetime
        from uuid import uuid4

        row = await self.get_thread(thread_id, user_name=user_name)
        if row is None:
            return {}
        message = {
            "message_id": message_id or str(uuid4()),
            "role": role,
            "content": content,
            "created_at": datetime.now(UTC).replace(tzinfo=None),
        }
        self._messages.setdefault(thread_id, []).append(message)
        row["message_count"] = len(self._messages[thread_id])
        return message


@pytest.fixture
def app_and_broker(monkeypatch):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(assistant_router, prefix="/api/v1/assistant")

    current = {"username": "alice"}

    async def fake_current_user():
        return {
            **_signed_in(current["username"]),
            **current,
            "encrypted_password": "",
        }

    # Route the module's singleton broker through a per-test instance so tests
    # cannot leak pending entries into each other.
    broker = ConsentBroker()
    monkeypatch.setattr("app.modules.assistant.router.consent_broker", broker, raising=True)
    repo = _FakeRepo()
    monkeypatch.setattr("app.modules.assistant.router.assistant_repository", repo, raising=True)

    app.dependency_overrides[deps_module.get_current_user] = fake_current_user
    thread_store.clear()

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, broker, current, repo

    app.dependency_overrides.clear()
    thread_store.clear()


async def _open_pending(broker: ConsentBroker) -> None:
    broker.open("victim-call", thread_id="alice-thread", user=_signed_in("alice"))


class _SeededThread:
    def __init__(self, thread_id: str, user_name: str) -> None:
        self.thread_id = thread_id
        self.user_name = user_name


def _seed_thread(repo: _FakeRepo, *, user_name: str, title: str = "A") -> _SeededThread:
    """A thread that exists in both the repo (durable) and the runtime store.

    The router reads the thread from the repository and the grant from the
    in-memory store, so a test that exercises either path needs both seeded.
    """
    from uuid import uuid4

    thread_id = str(uuid4())
    repo.seed(thread_id=thread_id, user_name=user_name, title=title)
    thread_store.register(thread_id=thread_id, user_name=user_name, title=title)
    return _SeededThread(thread_id, user_name)


def _open_pending_sync(broker: ConsentBroker) -> None:
    """Create a pending entry from a sync test.

    The future lands on a throwaway loop; the HTTP rejection tests never resolve
    it (the route refuses a foreign caller before touching the future), so this
    is safe and keeps them independent of TestClient's loop.
    """
    import asyncio

    asyncio.run(_open_pending(broker))


def _open_pending_classification_sync(
    broker: ConsentBroker,
    tool_call_id: str,
    thread_id: str,
    user_name: str,
    classification: str,
) -> None:
    """Open a pending entry with an explicit classification, from a sync test."""
    import asyncio

    async def _open() -> None:
        broker.open(
            tool_call_id,
            thread_id=thread_id,
            user=_signed_in(user_name),
            classification=classification,
        )

    asyncio.run(_open())


@pytest.fixture(autouse=True)
def _clean_thread_store():
    thread_store.clear()
    yield
    thread_store.clear()


async def test_owner_can_resolve_their_own_pending_call(monkeypatch):
    """Owner path (same loop as the broker) resolves and marks approved."""
    broker = ConsentBroker()
    monkeypatch.setattr("app.modules.assistant.router.consent_broker", broker, raising=True)
    thread = thread_store.create(user_name="alice", title="A")
    future = broker.open("victim-call", thread_id=thread.thread_id, user=_signed_in("alice"))

    response = await resolve_tool_call(
        "victim-call",
        ConsentDecisionRequest(decision="allow_once"),
        user=_signed_in("alice"),
    )

    assert response.status == "approved"
    assert await future is True


async def test_foreign_user_gets_404_and_cannot_resolve_same_loop(monkeypatch):
    """The owner check runs before any resolve, so a foreign id is refused."""
    from fastapi import HTTPException

    broker = ConsentBroker()
    monkeypatch.setattr("app.modules.assistant.router.consent_broker", broker, raising=True)
    thread = thread_store.create(user_name="alice", title="A")
    future = broker.open("victim-call", thread_id=thread.thread_id, user=_signed_in("alice"))

    with pytest.raises(HTTPException) as exc:
        await resolve_tool_call(
            "victim-call",
            ConsentDecisionRequest(decision="allow_once"),
            user=_signed_in("bob"),
        )
    assert exc.value.status_code == 404
    assert future.done() is False
    assert broker.owner_of("victim-call", user=_signed_in("alice")) == (thread.thread_id, "alice")


def test_foreign_user_gets_404_and_cannot_resolve(app_and_broker):
    client, broker, current, _repo = app_and_broker
    _open_pending_sync(broker)

    current["username"] = "bob"
    resp = client.post(
        "/api/v1/assistant/tool-calls/victim-call/decision",
        json={"decision": "allow_once"},
    )
    assert resp.status_code == 404
    # The entry survives for its real owner.
    assert broker.owner_of("victim-call", user=_signed_in("alice")) == ("alice-thread", "alice")


def test_foreign_user_cannot_deny_either(app_and_broker):
    client, broker, current, _repo = app_and_broker
    _open_pending_sync(broker)

    current["username"] = "bob"
    resp = client.post(
        "/api/v1/assistant/tool-calls/victim-call/decision",
        json={"decision": "deny"},
    )
    assert resp.status_code == 404


def test_unknown_tool_call_id_is_404(app_and_broker):
    client, _broker, _current, _repo = app_and_broker
    resp = client.post(
        "/api/v1/assistant/tool-calls/does-not-exist/decision",
        json={"decision": "allow_once"},
    )
    assert resp.status_code == 404


def test_a_conversation_wide_grant_no_longer_exists(app_and_broker):
    """Read-only runs without asking, so there is nothing to grant or revoke."""
    client, broker, current, repo = app_and_broker
    thread = _seed_thread(repo, user_name="alice")
    _open_pending_classification_sync(broker, "call-1", thread.thread_id, "alice", "destructive")
    response = client.post(
        "/api/v1/assistant/tool-calls/call-1/decision", json={"decision": "allow_session"},
    )
    assert response.status_code == 422
    assert broker.owner_of("call-1", user=_signed_in("alice")) == (thread.thread_id, "alice")
    for method in ("put", "delete"):
        assert getattr(client, method)(
            f"/api/v1/assistant/threads/{thread.thread_id}/grant",
        ).status_code in {404, 405}


class _FakeProvider:
    """Returns queued assistant messages instead of calling a real LLM."""

    def __init__(self, script: list[dict]) -> None:
        self._script = list(script)

    async def plan_turn(self, *, user_content, available_tools):
        from tests.benchmark.harness import scripted_turn_plan

        return scripted_turn_plan(self._script)

    async def resolve(self, **_kwargs):
        return object()

    async def complete(self, *, messages, tools=None, provider=None):
        return self._script.pop(0)

    async def stream(self, *, messages, tools=None, provider=None):
        # The loop consumes the streaming path; emit the queued reply as one
        # delta followed by the assembled message, as the real client does.
        message = await self.complete(messages=messages, tools=tools, provider=provider)
        text = message.get("content") or ""
        if text:
            yield ("delta", text)
        yield ("message", message)


class _StubTool:
    name = "query_execute"
    classification = "read_only"
    description = "run sql"
    parameters = {"type": "object", "properties": {"sql": {"type": "string"}}}

    def preview(self, invocation: ToolInvocation) -> str:
        return invocation.arguments.get("sql", "")

    async def run(self, invocation, context):
        return ToolOutcome(ok=True, summary="1 row")


def _tool_call(call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "query_execute",
            "arguments": json.dumps({"sql": "SELECT 1"}),
        },
    }


async def test_a_read_only_call_runs_without_asking(app_and_broker):
    """No grant, no prompt: the caller's role governs what a read-only call reads."""
    _client, _broker, _current, repo = app_and_broker
    seeded = _seed_thread(repo, user_name="alice")
    thread = thread_store.get(seeded.thread_id, user_name="alice")
    provider = _FakeProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1")]},
            {"role": "assistant", "content": "answer"},
        ]
    )
    registry = ToolRegistry()
    registry.register(_StubTool())
    loop = AssistantLoop(provider=provider, registry=registry)

    asked: list[str] = []

    async def resolver(inv, cls):
        asked.append(cls)
        return True

    frames = [
        frame
        async for frame in loop.run(
            thread=thread,
            user_content="SELECT 1",
            context=LoopContext(user_name="alice"),
            resolve_consent=resolver,
        )
    ]

    assert any(frame.startswith("event: tool_call") for frame in frames)
    assert not any(frame.startswith("event: input_required") for frame in frames)
    assert asked == []


@pytest.mark.parametrize("surface", ["assistant", "agent"])
@pytest.mark.parametrize("decision", ["allow_once", "deny"])
@pytest.mark.parametrize("changed", [
    {"session_id": "second-login"},
    {"session_id": None},
    {"active_role": "reviewer", "assigned_roles": ["reviewer"]},
    {"security_context_version": 2},
    {"username": "bob"},
])
async def test_pending_approval_requires_the_original_full_identity(
    monkeypatch, surface, decision, changed,
):
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.modules.agents import router as agent_router
    from app.modules.assistant import router as assistant_module

    broker = ConsentBroker()
    for module in (assistant_module, agent_router):
        monkeypatch.setattr(module, "consent_broker", broker)
    monkeypatch.setattr(agent_router, "_require_agent", AsyncMock())
    original = _signed_in("alice")
    thread = thread_store.create(user_name="alice", title="Approval identity")
    future = broker.open("scoped", thread_id=thread.thread_id, user=original)
    altered = {**original, **changed}
    body = ConsentDecisionRequest(decision=decision)
    try:
        with pytest.raises(HTTPException) as refused:
            if surface == "agent":
                await agent_router.resolve_agent_tool_call("agent", "scoped", body, altered)
            else:
                await resolve_tool_call("scoped", body, altered)
        assert refused.value.status_code == 404
        assert not future.done()
        assert broker.owner_of("scoped", user=altered) is None
        assert broker.owner_of("scoped", user=original) == (thread.thread_id, "alice")
        assert not broker.resolve("scoped", True, user=altered)
        assert broker.resolve("scoped", True, user=original)
        assert await future is True
        assert not broker.resolve("scoped", True, user=original)
    finally:
        broker.cancel_all()


async def test_proposal_keeps_an_immutable_copy_of_the_security_identity():
    broker = ConsentBroker()
    original = _signed_in("alice")
    mutable = dict(original)
    future = broker.open("stable", thread_id="thread", user=mutable)
    mutable["session_id"] = "replacement-login"
    try:
        assert not broker.resolve("stable", True, user=mutable)
        assert broker.resolve("stable", False, user=original)
        assert await future is False
    finally:
        broker.cancel_all()


@pytest.mark.parametrize(
    "user", [{"username": "alice"}, {**_signed_in("alice"), "session_id": None}]
)
async def test_proposal_without_a_complete_login_fails_closed(user):
    from app.core.exceptions import InvalidCredentialsError

    broker = ConsentBroker()
    with pytest.raises(InvalidCredentialsError):
        broker.open("invalid", thread_id="thread", user=user)
    assert broker.owner_of("invalid", user=_signed_in("alice")) is None
