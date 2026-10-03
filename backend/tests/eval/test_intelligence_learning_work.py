"""Durable learning retries retain the source and its originating identity."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.redis import session_store
from app.modules.agents import memory, router
from app.modules.assistant.provider import assistant_provider
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.security import observation_context, session_security
from app.modules.intelligence.contracts import Scope
from app.modules.task_orchestration import access
from app.modules.task_orchestration.credentials import CredentialUnavailable
from app.modules.task_orchestration.internal_handlers import InternalTaskConfiguration, run_once

USER = {
    "username": "analyst",
    "active_role": "FINANCE",
    "roles": ["FINANCE"],
    "session_id": "source-session",
    "security_context_version": 1,
}


@pytest.fixture
def learning_source(monkeypatch):
    source = {
        "content": "Recognized revenue includes only completed orders.",
        "security_context": observation_context(session_security(USER)),
        "created_at": datetime(2026, 9, 20, tzinfo=UTC),
    }
    read = AsyncMock(return_value=source)
    complete = AsyncMock()
    monkeypatch.setattr(router, "_require_agent", AsyncMock())
    monkeypatch.setattr(assistant_repository, "learning_source", read)
    monkeypatch.setattr(assistant_repository, "mark_learning_complete", complete)
    monkeypatch.setattr(assistant_repository, "record_learning_trace", AsyncMock())
    monkeypatch.setattr(session_store, "get", AsyncMock(return_value=USER))
    monkeypatch.setattr(access, "verify_active_role", AsyncMock())
    config = InternalTaskConfiguration(
        scope=Scope.from_user(USER),
        agent_id="finance",
        source_thread_id="source-thread",
        source_message_id="source-message",
    )
    return config, read, complete


async def test_provider_outage_preserves_pending_source_then_retry_completes_once(
    learning_source, monkeypatch
):
    config, read, complete = learning_source
    monkeypatch.setattr(memory.memory_repository, "list", AsyncMock(return_value=[]))
    resolve = AsyncMock(side_effect=[RuntimeError("Provider unavailable"), object()])
    answer = AsyncMock(return_value={"content": "[]"})
    monkeypatch.setattr(assistant_provider, "resolve", resolve)
    monkeypatch.setattr(assistant_provider, "complete", answer)

    with pytest.raises(RuntimeError, match="Provider unavailable"):
        await run_once("intelligence.consolidate", config, USER)
    complete.assert_not_awaited()
    answer.assert_not_awaited()
    assert await run_once("intelligence.consolidate", config, USER) == {
        "status": "complete",
        "extracted": 0,
    }
    complete.assert_awaited_once_with("source-message", user_name="analyst")
    read.return_value = None
    assert await run_once("intelligence.consolidate", config, USER) == {
        "status": "excluded",
        "extracted": 0,
    }
    assert resolve.await_count == 2 and answer.await_count == 1
    complete.assert_awaited_once()


@pytest.mark.parametrize("reply", ["not JSON", '{"facts": []}', "[null]"])
async def test_malformed_provider_reply_remains_pending_and_can_resume(
    learning_source, monkeypatch, reply
):
    config, _, complete = learning_source
    monkeypatch.setattr(memory.memory_repository, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(assistant_provider, "resolve", AsyncMock(return_value=object()))
    answer = AsyncMock(side_effect=[{"content": reply}, {"content": "[]"}])
    monkeypatch.setattr(assistant_provider, "complete", answer)
    with pytest.raises(ValueError, match="invalid structured output"):
        await run_once("intelligence.consolidate", config, USER)
    complete.assert_not_awaited()
    assert await run_once("intelligence.consolidate", config, USER) == {
        "status": "complete",
        "extracted": 0,
    }
    complete.assert_awaited_once()


@pytest.mark.parametrize(
    "changed",
    [
        {"username": "another-principal"},
        {"active_role": "OPERATIONS", "roles": ["OPERATIONS"]},
        {"security_context_version": 2},
        {"session_id": "replacement-session"},
    ],
)
async def test_consolidation_rejects_changed_identity_before_model_or_source_access(
    learning_source, monkeypatch, changed
):
    config, read, complete = learning_source
    extract = AsyncMock()
    monkeypatch.setattr(memory, "remember_user_message", extract)
    with pytest.raises(CredentialUnavailable):
        await run_once("intelligence.consolidate", config, {**USER, **changed})
    read.assert_not_awaited()
    extract.assert_not_awaited()
    complete.assert_not_awaited()


async def test_source_context_mismatch_never_becomes_completed_work(learning_source, monkeypatch):
    config, read, complete = learning_source
    read.return_value["security_context"]["security_context_version"] = 2
    extract = AsyncMock()
    monkeypatch.setattr(memory, "remember_user_message", extract)
    with pytest.raises(CredentialUnavailable, match="expired security context"):
        await run_once("intelligence.consolidate", config, USER)
    extract.assert_not_awaited()
    complete.assert_not_awaited()


async def test_learning_records_public_usage_and_unknown_cost_before_completion(
    learning_source, monkeypatch
):
    from types import SimpleNamespace

    config, _, complete = learning_source
    monkeypatch.setattr(memory.memory_repository, "list", AsyncMock(return_value=[]))
    provider_config = SimpleNamespace(
        provider_id="registered", model="primary", api_key="private-marker"
    )
    monkeypatch.setattr(assistant_provider, "resolve", AsyncMock(return_value=provider_config))
    monkeypatch.setattr(
        assistant_provider,
        "complete",
        AsyncMock(
            return_value={
                "content": "[]",
                "reasoning_content": "private-marker",
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                    "extra": "private-marker",
                },
            }
        ),
    )
    trace = assistant_repository.record_learning_trace
    await run_once("intelligence.consolidate", config, USER)
    values = [call.kwargs["trace"] for call in trace.await_args_list]
    assert [value["trace_detail"]["phase"] for value in values] == [
        "resolve",
        "request",
        "response",
    ]
    assert len({value["id"] for value in values}) == 1
    assert values[-1]["status"] == "done"
    assert values[-1]["trace_detail"]["total_tokens"] == 15
    assert values[-1]["trace_detail"]["cost_dollars"] is None
    assert values[-1]["trace_detail"]["cost_status"] == "unavailable"
    assert "private-marker" not in repr(values)
    complete.assert_awaited_once()


async def test_learning_journal_failure_prevents_provider_call(learning_source, monkeypatch):
    config, _, complete = learning_source
    monkeypatch.setattr(memory.memory_repository, "list", AsyncMock(return_value=[]))
    trace = assistant_repository.record_learning_trace
    trace.side_effect = RuntimeError("Journal unavailable")
    resolve = AsyncMock()
    monkeypatch.setattr(assistant_provider, "resolve", resolve)
    with pytest.raises(RuntimeError, match="Journal unavailable"):
        await run_once("intelligence.consolidate", config, USER)
    resolve.assert_not_awaited()
    complete.assert_not_awaited()


async def test_context_change_during_extraction_preserves_usage_but_cannot_promote(
    learning_source, monkeypatch
):
    config, _, complete = learning_source
    monkeypatch.setattr(memory.memory_repository, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(assistant_provider, "resolve", AsyncMock(return_value=object()))

    async def changed(**kwargs):
        session_store.get.return_value = {**USER, "security_context_version": 2}
        return {"content": "[]", "usage": {"total_tokens": 19}}

    monkeypatch.setattr(assistant_provider, "complete", changed)
    with pytest.raises(CredentialUnavailable, match="expired or changed"):
        await run_once("intelligence.consolidate", config, USER)
    complete.assert_not_awaited()
    recorded = assistant_repository.record_learning_trace.await_args.kwargs["trace"]
    assert recorded["trace_detail"]["phase"] == "response"
    assert recorded["trace_detail"]["total_tokens"] == 19
