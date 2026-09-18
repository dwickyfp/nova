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
from app.modules.assistant.consent import ConsentBroker
from app.modules.assistant.router import resolve_tool_call
from app.modules.assistant.router import router as assistant_router
from app.modules.assistant.schemas import ConsentDecisionRequest
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import thread_store
from app.modules.assistant.tools import ToolInvocation, ToolOutcome, ToolRegistry


@pytest.fixture
def app_and_broker(monkeypatch):
    app = FastAPI()
    app.include_router(assistant_router, prefix="/api/v1/assistant")

    current = {"username": "alice"}

    async def fake_current_user():
        return {
            "username": current["username"],
            "session_id": "sess-1",
            "roles": [],
            "active_role": None,
            "encrypted_password": "",
        }

    # Route the module's singleton broker through a per-test instance so tests
    # cannot leak pending entries into each other.
    broker = ConsentBroker()
    monkeypatch.setattr(
        "app.modules.assistant.router.consent_broker", broker, raising=True
    )

    app.dependency_overrides[deps_module.get_current_user] = fake_current_user
    thread_store.clear()

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, broker, current

    app.dependency_overrides.clear()
    thread_store.clear()


async def _open_pending(broker: ConsentBroker) -> None:
    broker.open("victim-call", thread_id="alice-thread", user_name="alice")


def _open_pending_sync(broker: ConsentBroker) -> None:
    """Create a pending entry from a sync test.

    The future lands on a throwaway loop; the HTTP rejection tests never resolve
    it (the route refuses a foreign caller before touching the future), so this
    is safe and keeps them independent of TestClient's loop.
    """
    import asyncio

    asyncio.run(_open_pending(broker))


@pytest.fixture(autouse=True)
def _clean_thread_store():
    thread_store.clear()
    yield
    thread_store.clear()


async def test_owner_can_resolve_their_own_pending_call(monkeypatch):
    """Owner path (same loop as the broker) resolves and marks approved."""
    broker = ConsentBroker()
    monkeypatch.setattr(
        "app.modules.assistant.router.consent_broker", broker, raising=True
    )
    thread = thread_store.create(user_name="alice", title="A")
    future = broker.open(
        "victim-call", thread_id=thread.thread_id, user_name="alice"
    )

    response = await resolve_tool_call(
        "victim-call",
        ConsentDecisionRequest(decision="allow_once"),
        user={"username": "alice"},
    )

    assert response.status == "approved"
    assert await future is True


async def test_foreign_user_gets_404_and_cannot_resolve_same_loop(monkeypatch):
    """The owner check runs before any resolve, so a foreign id is refused."""
    from fastapi import HTTPException

    broker = ConsentBroker()
    monkeypatch.setattr(
        "app.modules.assistant.router.consent_broker", broker, raising=True
    )
    thread = thread_store.create(user_name="alice", title="A")
    future = broker.open(
        "victim-call", thread_id=thread.thread_id, user_name="alice"
    )

    with pytest.raises(HTTPException) as exc:
        await resolve_tool_call(
            "victim-call",
            ConsentDecisionRequest(decision="allow_once"),
            user={"username": "bob"},
        )
    assert exc.value.status_code == 404
    assert future.done() is False
    assert broker.owner_of("victim-call") == (thread.thread_id, "alice")


def test_foreign_user_gets_404_and_cannot_resolve(app_and_broker):
    client, broker, current = app_and_broker
    _open_pending_sync(broker)

    current["username"] = "bob"
    resp = client.post(
        "/api/v1/assistant/tool-calls/victim-call/decision",
        json={"decision": "allow_once"},
    )
    assert resp.status_code == 404
    # The entry survives for its real owner.
    assert broker.owner_of("victim-call") == ("alice-thread", "alice")


def test_foreign_user_cannot_deny_either(app_and_broker):
    client, broker, current = app_and_broker
    _open_pending_sync(broker)

    current["username"] = "bob"
    resp = client.post(
        "/api/v1/assistant/tool-calls/victim-call/decision",
        json={"decision": "deny"},
    )
    assert resp.status_code == 404


def test_unknown_tool_call_id_is_404(app_and_broker):
    client, _broker, _current = app_and_broker
    resp = client.post(
        "/api/v1/assistant/tool-calls/does-not-exist/decision",
        json={"decision": "allow_once"},
    )
    assert resp.status_code == 404


async def test_owner_sets_the_grant_on_their_own_conversation_only(monkeypatch):
    """``allow_session`` sets the read-only grant on the owner's thread only."""
    broker = ConsentBroker()
    monkeypatch.setattr(
        "app.modules.assistant.router.consent_broker", broker, raising=True
    )
    alice_thread = thread_store.create(user_name="alice", title="A")
    bob_thread = thread_store.create(user_name="bob", title="B")
    broker.open(
        "victim-call", thread_id=alice_thread.thread_id, user_name="alice"
    )

    response = await resolve_tool_call(
        "victim-call",
        ConsentDecisionRequest(decision="allow_session"),
        user={"username": "alice"},
    )

    assert response.grant_active is True
    assert alice_thread.consent.always_allow_read_only is True
    assert bob_thread.consent.always_allow_read_only is False


# ── Reset grant (NOVA-100) ───────────────────────────────────────────────────
#
# ``DELETE /threads/{thread_id}/grant`` had no committed test at all, so a
# regression in ``_require_thread`` or ``ConsentPolicy`` could silently stop
# revocation — leaving ``query_execute`` auto-approving read-only calls without
# a prompt (spec §6). These tests pin the owner/foreign boundary and the
# auto-approve reversal that NOVA-96 item 4 requires.


def test_owner_revoke_clears_the_grant(app_and_broker):
    client, _broker, current = app_and_broker
    thread = thread_store.create(user_name="alice", title="A")
    thread.consent.always_allow_read_only = True

    current["username"] = "alice"
    resp = client.delete(f"/api/v1/assistant/threads/{thread.thread_id}/grant")

    assert resp.status_code == 204
    assert thread.consent.always_allow_read_only is False


def test_foreign_revoke_is_404_and_leaves_the_owner_grant_intact(app_and_broker):
    client, _broker, current = app_and_broker
    thread = thread_store.create(user_name="alice", title="A")
    thread.consent.always_allow_read_only = True

    current["username"] = "bob"
    resp = client.delete(f"/api/v1/assistant/threads/{thread.thread_id}/grant")

    # 404, never 403: a foreign id must not leak existence.
    assert resp.status_code == 404
    assert thread.consent.always_allow_read_only is True


class _FakeProvider:
    """Returns queued assistant messages instead of calling a real LLM."""

    def __init__(self, script: list[dict]) -> None:
        self._script = list(script)

    async def resolve(self):
        return object()

    async def complete(self, *, messages, tools=None, provider=None):
        return self._script.pop(0)


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


async def test_revoked_grant_prompts_again_on_the_next_read_only_call(
    app_and_broker,
):
    """The reset is real: after it, a read-only call asks instead of auto-approving."""
    client, _broker, current = app_and_broker
    thread = thread_store.create(user_name="alice", title="A")
    thread.consent.always_allow_read_only = True

    current["username"] = "alice"
    resp = client.delete(f"/api/v1/assistant/threads/{thread.thread_id}/grant")
    assert resp.status_code == 204

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
            user_content="go",
            context=LoopContext(user_name="alice"),
            resolve_consent=resolver,
        )
    ]

    # With the grant revoked, the loop surfaces the prompt and consults the
    # resolver — the opposite of the auto-approve that a live grant pins.
    assert any(frame.startswith("event: tool_call") for frame in frames)
    assert asked == ["read_only"]
