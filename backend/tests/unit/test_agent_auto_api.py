from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.modules.agents import router
from app.modules.agents.harness_repository import AutoAdmissionUnavailable
from app.modules.assistant.schemas import AgentMessageRequest


def _user(name: str, role: str) -> dict:
    return {
        "username": name, "session_id": "session", "active_role": role,
        "assigned_roles": [role], "roles": [role], "security_context_version": 1,
    }


def test_auto_tree_is_scoped_to_owner_role_and_thread(monkeypatch) -> None:
    root = {
        "run_id": "root", "root_run_id": None, "parent_run_id": None,
        "agent_id": "__auto__", "depth": 0, "owner_name": "alice",
        "role_name": "analyst", "thread_id": "thread", "objective": "Revenue?",
        "status": "waiting_for_agent", "result_summary": None,
        "prompt_tokens": 2, "completion_tokens": 1,
        "started_at": "2026-09-24", "updated_at": "2026-09-24",
        "error_class": None,
    }
    monkeypatch.setattr(router.harness_repository, "get", AsyncMock(return_value=root))
    monkeypatch.setattr(router.harness_repository, "tree", AsyncMock(return_value=[root]))
    thread = AsyncMock(return_value={"thread_id": "thread", "agent_id": "__auto__"})
    monkeypatch.setattr(router, "_require_agent_thread", thread)
    current = {"user": _user("alice", "analyst")}
    app = FastAPI()
    app.include_router(router.router, prefix="/agents")
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    client = TestClient(app)

    response = client.get("/agents/auto/runs/root")
    assert response.status_code == 200
    assert response.json()["runs"][0]["status"] == "waiting_for_agent"
    current["user"] = _user("bob", "analyst")
    assert client.get("/agents/auto/runs/root").status_code == 404
    current["user"] = _user("alice", "finance")
    assert client.get("/agents/auto/runs/root").status_code == 404
    assert thread.await_count == 1


def test_user_message_to_child_uses_trusted_root_identity(monkeypatch) -> None:
    root = {"run_id": "root", "root_run_id": None, "parent_run_id": None,
            "agent_id": "__auto__", "depth": 0, "owner_name": "alice",
            "role_name": "analyst", "thread_id": "thread"}
    child = {"run_id": "finance", "root_run_id": "root", "parent_run_id": "root",
             "agent_id": "finance", "depth": 1, "status": "running"}
    monkeypatch.setattr(router.harness_repository, "get", AsyncMock(
        side_effect=lambda run_id: root if run_id == "root" else child
    ))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={}))
    send = AsyncMock(return_value="message-1")
    monkeypatch.setattr(router.harness_repository, "send", send)
    app = FastAPI()
    app.include_router(router.router, prefix="/agents")
    app.dependency_overrides[get_current_user] = lambda: _user("alice", "analyst")
    client = TestClient(app)

    response = client.post("/agents/auto/runs/root/children/finance/messages", json={
        "operation_id": "client-1", "content": "Focus on Jakarta",
        "sender_run_id": "forged-run",
    })
    assert response.status_code == 200
    assert response.json()["message_id"] == "message-1"
    assert send.await_args.kwargs["sender"] is root
    assert send.await_args.kwargs["recipient"] == child
    assert send.await_args.kwargs["origin"] == "user"
    assert send.await_args.kwargs["operation_id"] == "user:client-1"
    child["root_run_id"] = "other-root"
    assert client.post("/agents/auto/runs/root/children/finance/messages", json={
        "operation_id": "client-2", "content": "Cross tree",
    }).status_code == 404


def test_child_timeline_is_scoped_and_replays_in_order(monkeypatch) -> None:
    root = {
        "run_id": "root", "root_run_id": None, "parent_run_id": None,
        "agent_id": "__auto__", "depth": 0, "owner_name": "alice",
        "role_name": "analyst", "thread_id": "thread",
    }
    child = {
        "run_id": "child", "root_run_id": "root", "parent_run_id": "root",
        "agent_id": "sales", "depth": 1, "owner_name": "alice",
        "role_name": "analyst", "thread_id": "thread", "objective": "Compare revenue",
        "status": "running", "result_summary": None, "error_class": None,
        "prompt_tokens": 12, "completion_tokens": 3,
    }
    monkeypatch.setattr(router.harness_repository, "get", AsyncMock(
        side_effect=lambda run_id: root if run_id == "root" else child
    ))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={}))
    monkeypatch.setattr(router.agent_repository, "get_agent", AsyncMock(
        return_value={"name": "Sales Agent"}
    ))
    page = AsyncMock(return_value=([
        {"event_id": 9, "run_id": "child", "type": "child_activity",
         "payload": {"event_type": "tool_call", "tool_name": "query_execute"}},
    ], 9, False))
    monkeypatch.setattr(router.harness_repository, "child_events_page", page)
    current = {"user": _user("alice", "analyst")}
    app = FastAPI()
    app.include_router(router.router, prefix="/agents")
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    client = TestClient(app)

    response = client.get("/agents/auto/runs/root/children/child/timeline?after=5")
    assert response.status_code == 200
    assert response.json()["run"]["agent_name"] == "Sales Agent"
    assert response.json()["events"][0]["event_id"] == 9
    assert response.json()["next_cursor"] == 9
    page.assert_awaited_once_with("root", "child", 5, limit=100)

    monkeypatch.setattr(router.agent_repository, "get_agent", AsyncMock(
        side_effect=router.AgentMetadataUnavailable("metadata read failed")
    ))
    fallback = client.get("/agents/auto/runs/root/children/child/timeline?after=5")
    assert fallback.status_code == 200
    assert fallback.json()["run"]["agent_name"] == "Specialist"

    child["payload"] = {"agent_name": "Sales Agent"}
    stored_name = client.get("/agents/auto/runs/root/children/child/timeline?after=5")
    assert stored_name.status_code == 200
    assert stored_name.json()["run"]["agent_name"] == "Sales Agent"

    current["user"] = _user("bob", "analyst")
    assert client.get("/agents/auto/runs/root/children/child/timeline").status_code == 404
    current["user"] = _user("alice", "finance")
    assert client.get("/agents/auto/runs/root/children/child/timeline").status_code == 404
    current["user"] = _user("alice", "analyst")
    assert client.get("/agents/auto/runs/root/children/forged/timeline").status_code == 404
    child["root_run_id"] = "different"
    assert client.get("/agents/auto/runs/root/children/child/timeline").status_code == 404


def test_cancel_completed_auto_run_does_not_replace_terminal_event(monkeypatch) -> None:
    root = {
        "run_id": "root", "root_run_id": None, "parent_run_id": None,
        "agent_id": "__auto__", "depth": 0, "owner_name": "alice",
        "role_name": "analyst", "thread_id": "thread", "status": "completed",
    }
    monkeypatch.setattr(router.harness_repository, "get", AsyncMock(return_value=root))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={}))
    cancel = AsyncMock(return_value=False)
    event = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(router.harness_repository, "cancel_tree", cancel)
    monkeypatch.setattr(router.harness_repository, "event", event)
    monkeypatch.setattr(router, "write_audit_log", audit)
    app = FastAPI()
    app.include_router(router.router, prefix="/agents")
    app.dependency_overrides[get_current_user] = lambda: _user("alice", "analyst")

    response = TestClient(app).post("/agents/auto/runs/root/cancel")
    assert response.status_code == 409
    cancel.assert_awaited_once_with("root")
    event.assert_not_awaited()
    audit.assert_not_awaited()


def test_cancel_auto_root_reconciles_children_before_root_terminal(monkeypatch) -> None:
    root = {
        "run_id": "root", "root_run_id": None, "parent_run_id": None,
        "agent_id": "__auto__", "depth": 0, "owner_name": "alice",
        "role_name": "analyst", "thread_id": "thread", "status": "running",
    }
    monkeypatch.setattr(router.harness_repository, "get", AsyncMock(return_value=root))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={}))
    monkeypatch.setattr(router.harness_repository, "cancel_tree", AsyncMock(return_value=True))
    order: list[str] = []

    async def reconcile(_root_id: str) -> None:
        order.append("children")

    async def event(_root_id: str, run_id: str, kind: str, _payload: dict) -> str:
        assert run_id == "root" and kind == "agent_cancelled"
        order.append("root")
        return "2"

    monkeypatch.setattr(router.harness_repository, "reconcile_cancelled_children", reconcile)
    monkeypatch.setattr(router.harness_repository, "event", event)
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    app = FastAPI()
    app.include_router(router.router, prefix="/agents")
    app.dependency_overrides[get_current_user] = lambda: _user("alice", "analyst")

    response = TestClient(app).post("/agents/auto/runs/root/cancel")

    assert response.status_code == 200
    assert response.json() == {"status": "cancelled"}
    assert order == ["children", "root"]


def test_cancelled_child_drawer_repairs_missing_terminal_before_replay(monkeypatch) -> None:
    root = {
        "run_id": "root", "root_run_id": None, "parent_run_id": None,
        "agent_id": "__auto__", "depth": 0, "owner_name": "alice",
        "role_name": "analyst", "thread_id": "thread", "status": "cancelled",
    }
    child = {
        "run_id": "child", "root_run_id": "root", "parent_run_id": "root",
        "agent_id": "sales", "depth": 1, "owner_name": "alice",
        "role_name": "analyst", "thread_id": "thread", "objective": "Compare revenue",
        "status": "cancelled", "result_summary": None, "error_class": None,
        "prompt_tokens": 12, "completion_tokens": 3,
    }
    monkeypatch.setattr(router.harness_repository, "get", AsyncMock(
        side_effect=lambda run_id: root if run_id == "root" else child
    ))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={}))
    monkeypatch.setattr(router.agent_repository, "get_agent", AsyncMock(
        return_value={"name": "Sales Agent"}
    ))
    order: list[str] = []

    async def ensure(_root_id: str, run: dict) -> None:
        assert run is child
        order.append("repair")

    async def page(_root_id: str, _child_id: str, _after: int, *, limit: int):
        assert limit == 100
        order.append("replay")
        return ([{"event_id": 8, "run_id": "child", "type": "agent_cancelled",
                  "payload": {}}], 8, False)

    monkeypatch.setattr(router.harness_repository, "ensure_terminal_event", ensure)
    monkeypatch.setattr(router.harness_repository, "child_events_page", page)
    app = FastAPI()
    app.include_router(router.router, prefix="/agents")
    app.dependency_overrides[get_current_user] = lambda: _user("alice", "analyst")

    response = TestClient(app).get("/agents/auto/runs/root/children/child/timeline")

    assert response.status_code == 200
    assert response.json()["events"][0]["type"] == "agent_cancelled"
    assert order == ["repair", "replay"]


@pytest.mark.asyncio
async def test_concurrent_auto_posts_admit_only_one_root(monkeypatch) -> None:
    lock = asyncio.Lock()
    active = False

    @asynccontextmanager
    async def admission_lock(thread_id: str, owner_name: str):
        assert (thread_id, owner_name) == ("thread", "alice")
        async with lock:
            async def assert_owned() -> None:
                assert lock.locked()

            yield assert_owned

    async def append_message(*args, **kwargs):
        await asyncio.sleep(0.02)
        return {"message_id": "question-1"}

    async def create_root(**kwargs):
        nonlocal active
        active = True
        return {"run_id": "root-1"}

    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value={}))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(
        return_value={"thread_id": "thread", "title": "Comparison"}
    ))
    monkeypatch.setattr(router.harness_repository, "admission_lock", admission_lock)
    monkeypatch.setattr(router.harness_repository, "active_for_thread", AsyncMock(
        side_effect=lambda *_args: active
    ))
    append = AsyncMock(side_effect=append_message)
    create = AsyncMock(side_effect=create_root)
    monkeypatch.setattr(router.assistant_repository, "append_message", append)
    monkeypatch.setattr(router.harness_repository, "create_root", create)
    body = AgentMessageRequest(content="Compare channel revenue")
    user = _user("alice", "analyst")
    results = await asyncio.gather(
        router.send_agent_message("__auto__", "thread", body, None, user),
        router.send_agent_message("__auto__", "thread", body, None, user),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in results) == 1
    rejected = next(item for item in results if isinstance(item, Exception))
    assert isinstance(rejected, HTTPException) and rejected.status_code == 409
    append.assert_awaited_once()
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_admission_outage_is_503_without_appending_a_message(monkeypatch) -> None:
    @asynccontextmanager
    async def unavailable(_thread_id: str, _owner_name: str):
        raise AutoAdmissionUnavailable("Redis unavailable")
        yield

    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value={}))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(
        return_value={"thread_id": "thread", "title": "Comparison"}
    ))
    monkeypatch.setattr(router.harness_repository, "admission_lock", unavailable)
    append = AsyncMock()
    monkeypatch.setattr(router.assistant_repository, "append_message", append)
    with pytest.raises(HTTPException) as error:
        await router.send_agent_message(
            "__auto__", "thread", AgentMessageRequest(content="Compare channels"),
            None, _user("alice", "analyst"),
        )
    assert error.value.status_code == 503
    append.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id, synchronize", [("__auto__", True), ("sales", False)])
async def test_auto_thread_detail_synchronizes_final_message_read(
    monkeypatch, agent_id: str, synchronize: bool
) -> None:
    thread = {
        "thread_id": "thread", "title": "Comparison", "agent_id": agent_id,
        "created_at": "2026-09-25T00:00:00", "updated_at": "2026-09-25T00:00:00",
    }
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value={}))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value=thread))
    list_messages = AsyncMock(return_value=[])
    monkeypatch.setattr(router.assistant_repository, "list_messages", list_messages)

    await router.get_agent_thread(agent_id, "thread", user=_user("alice", "analyst"))

    list_messages.assert_awaited_once_with(
        "thread", user_name="alice", synchronize=synchronize
    )


async def _frames(root_id: str, after: int) -> list[str]:
    return [frame async for frame in router._stream_auto_events(root_id, after)]


@pytest.mark.asyncio
async def test_auto_sse_replay_keeps_final_message_identity(monkeypatch) -> None:
    async def page(root_id: str, after: int, *, ensure_complete: bool = False):
        assert ensure_complete is True
        if after >= 0:
            return []
        return [{"event_id": 0, "run_id": root_id, "type": "agent_completed",
                 "payload": {"answer": "Recognized revenue fell."}}]

    monkeypatch.setattr(router.harness_repository, "events_page", page)
    first = await _frames("root", -1)
    replay = await _frames("root", 1)
    assert [frame.split("\n", 1)[0] for frame in first] == [
        "event: agent_completed", "event: text_delta", "event: done"
    ]
    assert [frame.split("\n", 1)[0] for frame in replay] == ["event: done"]
    first_done = json.loads(first[-1].split("data: ", 1)[1])
    replay_done = json.loads(replay[-1].split("data: ", 1)[1])
    assert first_done["message_id"] == replay_done["message_id"]
    assert replay_done["sequence"] == 2
