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
    repo = _FakeRepo()
    monkeypatch.setattr(
        "app.modules.assistant.router.assistant_repository", repo, raising=True
    )

    app.dependency_overrides[deps_module.get_current_user] = fake_current_user
    thread_store.clear()

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, broker, current, repo

    app.dependency_overrides.clear()
    thread_store.clear()


async def _open_pending(broker: ConsentBroker) -> None:
    broker.open("victim-call", thread_id="alice-thread", user_name="alice")


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
            user_name=user_name,
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
    client, broker, current, _repo = app_and_broker
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


# ── allow_session is gated on the call's classification (NOVA-122) ───────────
#
# The route used to set the grant on *any* ``allow_session`` decision, so a
# direct API caller could record consent the UI never presented (it only offers
# always-allow for ``classification === 'read_only'``). These pin that the
# decision is validated against the pending call's class.


async def test_allow_session_on_a_read_only_call_sets_the_grant(monkeypatch):
    broker = ConsentBroker()
    monkeypatch.setattr(
        "app.modules.assistant.router.consent_broker", broker, raising=True
    )
    thread = thread_store.create(user_name="alice", title="A")
    broker.open(
        "call-1",
        thread_id=thread.thread_id,
        user_name="alice",
        classification="read_only",
    )

    response = await resolve_tool_call(
        "call-1",
        ConsentDecisionRequest(decision="allow_session"),
        user={"username": "alice"},
    )

    assert response.grant_active is True
    assert response.status == "approved"
    assert thread.consent.always_allow_read_only is True


@pytest.mark.parametrize("classification", ["destructive", "denied"])
async def test_allow_session_on_a_non_read_only_call_is_rejected(
    monkeypatch, classification
):
    """The grant is not set, and the pending call is left for a valid decision."""
    from fastapi import HTTPException

    broker = ConsentBroker()
    monkeypatch.setattr(
        "app.modules.assistant.router.consent_broker", broker, raising=True
    )
    thread = thread_store.create(user_name="alice", title="A")
    broker.open(
        "call-1",
        thread_id=thread.thread_id,
        user_name="alice",
        classification=classification,
    )

    with pytest.raises(HTTPException) as exc:
        await resolve_tool_call(
            "call-1",
            ConsentDecisionRequest(decision="allow_session"),
            user={"username": "alice"},
        )

    assert exc.value.status_code == 400
    assert thread.consent.always_allow_read_only is False
    # Not consumed: the owner can still resolve it with allow_once.
    assert broker.owner_of("call-1") == (thread.thread_id, "alice")

    response = await resolve_tool_call(
        "call-1",
        ConsentDecisionRequest(decision="allow_once"),
        user={"username": "alice"},
    )
    assert response.status == "approved"
    assert response.grant_active is False
    assert thread.consent.always_allow_read_only is False


def test_allow_session_on_a_non_read_only_call_is_400_over_http(app_and_broker):
    """Same gate at the HTTP boundary the defect was reported on."""
    client, broker, current, repo = app_and_broker
    thread = _seed_thread(repo, user_name="alice")
    _open_pending_classification_sync(
        broker, "call-1", thread.thread_id, "alice", "destructive"
    )

    current["username"] = "alice"
    resp = client.post(
        "/api/v1/assistant/tool-calls/call-1/decision",
        json={"decision": "allow_session"},
    )

    assert resp.status_code == 400
    runtime = thread_store.get(thread.thread_id, user_name="alice")
    assert runtime is not None
    assert runtime.consent.always_allow_read_only is False


# ── Reset grant (NOVA-100) ───────────────────────────────────────────────────
#
# ``DELETE /threads/{thread_id}/grant`` had no committed test at all, so a
# regression in ``_require_thread`` or ``ConsentPolicy`` could silently stop
# revocation — leaving ``query_execute`` auto-approving read-only calls without
# a prompt (spec §6). These tests pin the owner/foreign boundary and the
# auto-approve reversal that NOVA-96 item 4 requires.


def test_owner_revoke_clears_the_grant(app_and_broker):
    client, _broker, current, repo = app_and_broker
    thread = _seed_thread(repo, user_name="alice")
    thread_store.get(thread.thread_id, user_name="alice").consent.always_allow_read_only = True

    current["username"] = "alice"
    resp = client.delete(f"/api/v1/assistant/threads/{thread.thread_id}/grant")

    assert resp.status_code == 204
    runtime = thread_store.get(thread.thread_id, user_name="alice")
    assert runtime.consent.always_allow_read_only is False


def test_foreign_revoke_is_404_and_leaves_the_owner_grant_intact(app_and_broker):
    client, _broker, current, repo = app_and_broker
    thread = _seed_thread(repo, user_name="alice")
    thread_store.get(thread.thread_id, user_name="alice").consent.always_allow_read_only = True

    current["username"] = "bob"
    resp = client.delete(f"/api/v1/assistant/threads/{thread.thread_id}/grant")

    # 404, never 403: a foreign id must not leak existence.
    assert resp.status_code == 404
    runtime = thread_store.get(thread.thread_id, user_name="alice")
    assert runtime.consent.always_allow_read_only is True


# ── Set grant (composer approval-mode) ───────────────────────────────────────
#
# ``PUT /threads/{thread_id}/grant`` lets the composer's approval-mode selector
# pre-grant read-only queries before the first turn. These pin the owner/foreign
# boundary, that setting is idempotent and cannot disturb another thread, and
# that ``false`` clears a live grant the same way ``DELETE`` does.


def test_owner_sets_the_grant_on_a_fresh_thread(app_and_broker):
    client, _broker, current, repo = app_and_broker
    thread = _seed_thread(repo, user_name="alice")

    current["username"] = "alice"
    resp = client.put(
        f"/api/v1/assistant/threads/{thread.thread_id}/grant",
        json={"always_allow_read_only": True},
    )

    assert resp.status_code == 200
    assert resp.json() == {"grant_active": True}
    runtime = thread_store.get(thread.thread_id, user_name="alice")
    assert runtime.consent.always_allow_read_only is True


def test_owner_clears_the_grant_with_false(app_and_broker):
    client, _broker, current, repo = app_and_broker
    thread = _seed_thread(repo, user_name="alice")
    thread_store.get(thread.thread_id, user_name="alice").consent.always_allow_read_only = True

    current["username"] = "alice"
    resp = client.put(
        f"/api/v1/assistant/threads/{thread.thread_id}/grant",
        json={"always_allow_read_only": False},
    )

    assert resp.status_code == 200
    assert resp.json() == {"grant_active": False}
    runtime = thread_store.get(thread.thread_id, user_name="alice")
    assert runtime.consent.always_allow_read_only is False


def test_foreign_set_is_404_and_leaves_the_owner_grant_untouched(app_and_broker):
    client, _broker, current, repo = app_and_broker
    thread = _seed_thread(repo, user_name="alice")

    current["username"] = "bob"
    resp = client.put(
        f"/api/v1/assistant/threads/{thread.thread_id}/grant",
        json={"always_allow_read_only": True},
    )

    assert resp.status_code == 404
    runtime = thread_store.get(thread.thread_id, user_name="alice")
    assert runtime.consent.always_allow_read_only is False


def test_set_grant_does_not_reset_a_grant_on_another_thread(app_and_broker):
    client, _broker, current, repo = app_and_broker
    alice_thread = _seed_thread(repo, user_name="alice")
    other = _seed_thread(repo, user_name="alice", title="B")
    thread_store.get(other.thread_id, user_name="alice").consent.always_allow_read_only = True

    current["username"] = "alice"
    resp = client.put(
        f"/api/v1/assistant/threads/{alice_thread.thread_id}/grant",
        json={"always_allow_read_only": True},
    )

    assert resp.status_code == 200
    assert (
        thread_store.get(other.thread_id, user_name="alice").consent.always_allow_read_only
        is True
    )


class _FakeProvider:
    """Returns queued assistant messages instead of calling a real LLM."""

    def __init__(self, script: list[dict]) -> None:
        self._script = list(script)

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


async def test_revoked_grant_prompts_again_on_the_next_read_only_call(
    app_and_broker,
):
    """The reset is real: after it, a read-only call asks instead of auto-approving."""
    client, _broker, current, repo = app_and_broker
    seeded = _seed_thread(repo, user_name="alice")
    thread = thread_store.get(seeded.thread_id, user_name="alice")
    thread.consent.always_allow_read_only = True

    current["username"] = "alice"
    resp = client.delete(f"/api/v1/assistant/threads/{seeded.thread_id}/grant")
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
