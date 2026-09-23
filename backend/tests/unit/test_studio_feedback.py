from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.agents import router
from app.modules.assistant import events, repository
from app.modules.assistant.schemas import AgentMessageRequest, MessageFeedbackRequest


@pytest.mark.parametrize("feedback", ["like", "dislike", None])
async def test_feedback_scopes_read_and_write_to_owner_thread_and_assistant(monkeypatch, feedback):
    execute = AsyncMock(return_value={"rows": [["m1"]]})
    monkeypatch.setattr(repository.db, "execute_system", execute)
    assert await repository.assistant_repository.set_feedback(
        "t1", "m1", feedback, user_name="alice"
    )
    for call in execute.call_args_list:
        sql, params = call.args
        assert "message_id = %s AND thread_id = %s AND user_name = %s AND role = 'assistant'" in sql
        assert params[-3:] == ["m1", "t1", "alice"]
    assert execute.call_args_list[1].args[1][0] == feedback


async def test_missing_or_foreign_message_is_not_updated(monkeypatch):
    execute = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(repository.db, "execute_system", execute)
    assert not await repository.assistant_repository.set_feedback(
        "t1", "foreign", "like", user_name="alice"
    )
    assert execute.await_count == 1


async def test_replay_includes_stored_feedback(monkeypatch):
    execute = AsyncMock(
        return_value={
            "rows": [
                [
                    "m1", "assistant", "Answer", "2026-09-23 00:00:00", "model",
                    20, 10, 30, [], None, "dislike",
                ]
            ]
        }
    )
    monkeypatch.setattr(repository.db, "execute_system", execute)
    messages = await repository.assistant_repository.list_messages("t1", user_name="alice")
    assert router._message_view(messages[0]).feedback == "dislike"


async def test_feedback_endpoint_checks_agent_thread_and_audits(monkeypatch):
    agent = AsyncMock()
    thread = AsyncMock()
    save = AsyncMock(return_value=True)
    audit = AsyncMock()
    monkeypatch.setattr(router, "_require_agent", agent)
    monkeypatch.setattr(router, "_require_agent_thread", thread)
    monkeypatch.setattr(router.assistant_repository, "set_feedback", save)
    monkeypatch.setattr(router, "write_audit_log", audit)
    result = await router.update_message_feedback(
        "a1", "t1", "m1", MessageFeedbackRequest(feedback="like"), {"username": "alice"}
    )
    assert result.feedback == "like"
    agent.assert_awaited_once_with("a1", {"username": "alice"})
    thread.assert_awaited_once_with("t1", "a1", "alice")
    assert audit.call_args.kwargs["decision"] == "like"
    save.return_value = False
    with pytest.raises(HTTPException) as error:
        await router.update_message_feedback(
            "a1", "t1", "missing", MessageFeedbackRequest(feedback=None), {"username": "alice"}
        )
    assert error.value.status_code == 404
    assert audit.await_count == 1


def test_invalid_feedback_is_rejected():
    with pytest.raises(ValidationError):
        MessageFeedbackRequest(feedback="invalid")


async def test_stream_persists_the_terminal_message_id_before_sending_done(monkeypatch):
    from app.modules.assistant.tools import ToolRegistry

    agent = {"agent_id": "a1", "name": "Analyst", "owner_name": "alice", "database_name": "sales"}
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={"title": "Sales"}))
    monkeypatch.setattr(
        router.agent_service,
        "build_loop_inputs",
        AsyncMock(return_value=(ToolRegistry(), "System", 60, None)),
    )
    monkeypatch.setattr(router, "_generate_thread_title", AsyncMock(return_value="Sales"))
    monkeypatch.setattr(router.assistant_repository, "list_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.assistant_repository, "rename_thread", AsyncMock())
    append = AsyncMock()
    monkeypatch.setattr(router.assistant_repository, "append_message", append)
    monkeypatch.setattr(router.run_journal, "start", AsyncMock())
    monkeypatch.setattr(router.run_journal, "append", AsyncMock())
    monkeypatch.setattr(router.run_journal, "append_batch", AsyncMock())
    monkeypatch.setattr(router.run_journal, "finish", AsyncMock())

    class ScriptedLoop:
        def __init__(self, **kwargs):
            pass

        async def run(self, **kwargs):
            yield events.text_delta("Answer")
            yield events.done("persisted-answer", usage={"total_tokens": 30})

    monkeypatch.setattr(router, "AssistantLoop", ScriptedLoop)
    response = await router.send_agent_message(
        "a1",
        "feedback-stream",
        AgentMessageRequest(content="Sales"),
        SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
        {
            "username": "alice",
            "active_role": "analyst",
            "roles": ["analyst"],
            "session_id": "feedback-session",
        },
    )
    frames = []
    async for frame in response.body_iterator:
        frames.append(frame)
        if frame.startswith("event: done"):
            assert append.call_args.kwargs["role"] == "assistant"
            assert append.call_args.kwargs["message_id"] == "persisted-answer"
    assert any(frame.startswith("event: done") for frame in frames)
