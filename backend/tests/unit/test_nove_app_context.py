from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.modules.assistant.app_context import NoveAppContext, resolve_app_references
from app.modules.assistant.application_events import ApplicationEventBroker
from app.modules.assistant.schemas import MessageRequest
from app.modules.assistant.service import AssistantLoop, LoopContext, _fast_client_action
from app.modules.assistant.state import AssistantMessage, AssistantThread
from app.modules.assistant.tools import ToolInvocation, ToolRegistry
from app.modules.assistant.tools.client_capability import invoke_client_capability_tool


def _app(**fields) -> NoveAppContext:
    return NoveAppContext.model_validate(
        {
            "version": 1,
            "surface": {"id": "workspace.sql", "route": "/workspace", "title": "SQL"},
            "capabilities": ["surface.set_filter", "surface.refresh"],
            **fields,
        }
    )


def _event(*, surface: str = "workspace.sql", document: str = "doc-1") -> dict:
    return {
        "id": "evt-1",
        "timestamp": "2026-09-24T07:00:00Z",
        "source": "execution",
        "type": "query_failed",
        "surfaceId": surface,
        "executionId": "exec-1",
        "status": "failure",
        "payload": {"documentId": document, "errorMessage": "Unknown column revenue"},
    }


def test_message_accepts_versioned_context_and_legacy_fields() -> None:
    request = MessageRequest.model_validate(
        {
            "content": "fix this",
            "database": "sales",
            "schema": "analytics",
            "app_context": {
                "version": 1,
                "surface": {"id": "workspace.sql", "route": "/workspace"},
                "domain": {"database": "ignored", "schema": "default"},
                "entity": {"type": "table", "name": "orders"},
                "selection": {"type": "query", "ids": ["q1"]},
                "editor": {"documentId": "doc-1", "selectedText": "SELECT revenue"},
                "execution": {
                    "executionId": "exec-1",
                    "status": "error",
                    "errorMessage": "Unknown column",
                },
                "view": {"activeTab": "Results"},
                "capabilities": ["surface.refresh"],
                "unknownOptional": {"ignored": True},
            },
        }
    )
    assert request.database == "sales"
    assert request.schema_name == "analytics"
    assert request.app_context is not None
    assert request.app_context.domain.schema_name == "default"
    assert request.app_context.editor.document_id == "doc-1"
    assert "unknownOptional" not in request.app_context.prompt_data()
    assert MessageRequest(content="legacy turn").app_context is None


def test_context_rejects_invalid_and_oversized_payloads() -> None:
    with pytest.raises(ValidationError):
        _app(version=2)
    with pytest.raises(ValidationError):
        NoveAppContext.model_validate({"version": 1, "capabilities": []})
    with pytest.raises(ValidationError):
        _app(selection={"text": "x" * 25_000})
    with pytest.raises(ValidationError):
        _app(capabilities=["window.eval"] * 33)


def test_context_redacts_secrets_and_filters_stale_events() -> None:
    app = _app(
        entity={
            "type": "table",
            "id": "orders",
            "metadata": {"apiKey": "secret", "owner": "alice"},
        },
        editor={"documentId": "doc-2", "selectedText": "SELECT 1"},
        view={"filters": {"password": "hunter2", "status": "FAILED"}},
        events=[
            _event(document="doc-1"),
            {
                **_event(document="doc-2"),
                "id": "evt-2",
                "payload": {
                    "documentId": "doc-2",
                    "token": "secret",
                    "errorMessage": "Unknown column",
                },
            },
        ],
    )
    payload = app.prompt_data()
    rendered = json.dumps(payload)
    assert "hunter2" not in rendered
    assert '"apiKey"' not in rendered
    assert '"token"' not in rendered
    assert [event["id"] for event in payload["events"]] == ["evt-2"]


def test_context_scrubs_quoted_multiword_secrets_from_every_free_form_section() -> None:
    poisoned = 'password="correct horse battery staple" followed by private text'
    short_poisoned = 'password="multi word secret"'
    app = _app(
        surface={"id": poisoned, "route": poisoned, "title": poisoned},
        entity={
            "type": poisoned,
            "id": poisoned,
            "name": poisoned,
            "metadata": {"label": poisoned, "apiKey": "hidden"},
        },
        selection={
            "type": poisoned,
            "ids": [poisoned],
            "text": poisoned,
            "metadata": {"label": poisoned},
        },
        editor={
            "documentId": poisoned,
            "language": short_poisoned,
            "selectedText": poisoned,
        },
        execution={
            "type": poisoned,
            "executionId": poisoned,
            "errorCode": poisoned,
            "errorMessage": poisoned,
            "resultSchema": [{"name": poisoned, "type": poisoned}],
        },
        view={
            "activeTab": poisoned,
            "filters": {"label": poisoned},
            "search": poisoned,
            "sort": poisoned,
        },
        domain={
            "database": poisoned,
            "schema": poisoned,
            "role": poisoned,
            "semanticModel": poisoned,
            "providerId": poisoned,
        },
        capabilities=["surface.refresh", "token.leak"],
        events=[
            {
                "id": poisoned,
                "timestamp": "2026-09-24T07:00:00Z",
                "source": "surface",
                "type": poisoned,
                "surfaceId": poisoned,
                "correlationId": poisoned,
                "artifactId": poisoned,
                "executionId": poisoned,
                "payload": {"label": poisoned, "credentialNote": "hidden"},
            }
        ],
    )
    rendered = json.dumps(app.prompt_data())
    assert "correct horse battery staple" not in rendered
    assert "followed by private text" not in rendered
    assert "hidden" not in rendered
    assert app.surface.route == "***"
    assert app.view.search == "***"
    assert app.capabilities == ["surface.refresh"]
    assert app.entity.metadata == {"label": "***"}
    assert app.events[0].payload == {"label": "***"}


def test_context_scrubs_unlabelled_secret_formats_and_metadata_after_limit() -> None:
    app = _app(
        entity={"type": "query", "name": "sk-abcdefghijklmnopqrstuvwxyz012345"},
        view={
            "search": 'filter password="long private phrase"',
            "filters": {
                "location": "https://alice:unmarkedpass@example.test/data",
                "note": f'{"x" * 2048} password="hidden after limit"',
            },
        },
    )
    assert app.entity.name == "***"
    assert app.view.search == "***"
    assert app.view.filters == {"location": "***", "note": "***"}


def test_query_event_keeps_bounded_sql_for_repair() -> None:
    event = _event()
    event["payload"]["sql"] = "SELECT " + "x" * 3993
    app = _app(editor={"documentId": "doc-1"}, events=[event])
    assert len(app.prompt_data()["events"][0]["payload"]["sql"]) == 4000


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("fix this", "execution"),
        ("why did this fail?", "execution"),
        ("give this role access", "entity"),
        ("open it", "entity"),
        ("compare this with yesterday", "selection"),
    ],
)
def test_current_references_resolve_without_old_thread_state(phrase: str, expected: str) -> None:
    app = _app(
        entity={"type": "role", "id": "ANALYST", "name": "ANALYST"},
        selection={"type": "role", "ids": ["ANALYST"]},
        execution={
            "type": "query",
            "executionId": "e1",
            "status": "error",
            "errorMessage": "Denied",
        },
    )
    assert expected in resolve_app_references(phrase, app)


def test_fix_it_uses_current_failed_event_when_execution_is_absent() -> None:
    app = _app(editor={"documentId": "doc-1"}, events=[_event()])
    resolved = resolve_app_references("fix it", app)
    assert resolved["lastFailure"]["executionId"] == "exec-1"
    switched = _app(editor={"documentId": "doc-2"}, events=[_event()])
    assert "lastFailure" not in resolve_app_references("fix it", switched)
    closed = _app(events=[_event()])
    assert closed.current_events() == []


def test_application_event_acknowledges_only_matching_owner_surface_and_capability() -> None:
    broker = ApplicationEventBroker()
    broker.register_action(
        "thread-1",
        "alice",
        {
            "correlation_id": "action-1",
            "surface_id": "workspace.sql",
            "capability": "surface.set_filter",
        },
    )
    wrong = _event()
    wrong.update(
        {
            "type": "ui_action_completed",
            "source": "assistant",
            "correlationId": "action-1",
            "status": "success",
            "payload": {"capability": "tab.open"},
        }
    )
    from app.modules.assistant.app_context import NoveApplicationEvent

    assert (
        broker.publish("thread-1", "alice", NoveApplicationEvent.model_validate(wrong))
        == "unmatched"
    )
    assert broker.recent_events("thread-1", "alice", "workspace.sql") == []
    assert broker.recent_events("thread-1", "bob", "workspace.sql") == []
    forged = {**wrong, "id": "evt-forged", "payload": {"capability": "surface.set_filter"}}
    assert (
        broker.publish("thread-1", "bob", NoveApplicationEvent.model_validate(forged))
        == "unmatched"
    )
    assert (
        broker.publish(
            "thread-1",
            "alice",
            NoveApplicationEvent.model_validate(
                {**forged, "id": "evt-other", "correlationId": "fake"}
            ),
        )
        == "unmatched"
    )
    assert (
        broker.publish(
            "thread-1",
            "alice",
            NoveApplicationEvent.model_validate(
                {**forged, "id": "evt-surface", "surfaceId": "roles.detail"}
            ),
        )
        == "unmatched"
    )
    right = {**wrong, "id": "evt-2", "payload": {"capability": "surface.set_filter"}}
    assert (
        broker.publish("thread-1", "alice", NoveApplicationEvent.model_validate(right))
        == "verified"
    )
    assert len(broker.recent_events("thread-1", "alice", "workspace.sql")) == 1


def test_surface_switch_replaces_old_objective_and_evidence() -> None:
    loop = AssistantLoop(provider=object(), registry=ToolRegistry(), system_prompt="Nove")
    thread = AssistantThread(thread_id="t", user_name="alice", title="T")
    thread.messages.append(
        AssistantMessage(
            message_id="m1",
            role="assistant",
            content="Revenue fell",
            steps=[
                {
                    "kind": "active_state",
                    "state": {
                        "objective": "Analyze revenue",
                        "surface_id": "workspace.sql",
                        "last_evidence": ["revenue-evidence"],
                    },
                }
            ],
        )
    )
    role_app = NoveAppContext.model_validate(
        {
            "version": 1,
            "surface": {"id": "roles.detail", "route": "/roles/ANALYST"},
            "entity": {"type": "role", "id": "ANALYST"},
        }
    )
    context = LoopContext(user_name="alice", app_context=role_app)
    messages = loop._build_messages(thread, "Explain this", context)
    assert context.active_state["objective"] == "Explain this"
    assert context.active_state["surface_id"] == "roles.detail"
    assert context.active_state["last_evidence"] == []
    assert "ANALYST" in " ".join(str(message["content"]) for message in messages)


def test_client_domain_role_cannot_override_authenticated_role_in_prompt() -> None:
    loop = AssistantLoop(provider=object(), registry=ToolRegistry(), system_prompt="Nove")
    app = _app(domain={"role": "ACCOUNTADMIN", "database": "sales"})
    messages = loop._build_messages(
        AssistantThread(thread_id="t", user_name="alice", title="T"),
        "What can I access here?",
        LoopContext(user_name="alice", role="ANALYST", app_context=app),
    )
    app_prompt = next(
        message["content"]
        for message in messages
        if "<NOVA_APPLICATION_CONTEXT>" in str(message["content"])
    )
    assert '"role":"ANALYST"' in app_prompt
    assert "ACCOUNTADMIN" not in app_prompt


@pytest.mark.asyncio
async def test_client_tool_checks_surface_and_arguments() -> None:
    def invocation(capability, args):
        return ToolInvocation(
            tool_call_id="c1",
            tool_name="invoke_client_capability",
            arguments={"capability": capability, "args": args},
        )

    context = LoopContext(user_name="alice", app_context=_app())
    valid = await invoke_client_capability_tool.run(
        invocation("surface.set_filter", {"filter": "status", "value": "FAILED"}), context
    )
    assert valid.ok and valid.data["status"] == "pending"
    assert valid.metadata["client_action"]["surface_id"] == "workspace.sql"
    assert not (
        await invoke_client_capability_tool.run(invocation("tab.open", {"tab": "Results"}), context)
    ).ok
    assert not (
        await invoke_client_capability_tool.run(
            invocation("surface.set_filter", {"filter": "status"}), context
        )
    ).ok
    assert not (
        await invoke_client_capability_tool.run(
            invocation("surface.refresh", {"script": "alert(1)"}), context
        )
    ).ok


@pytest.mark.asyncio
async def test_fast_filter_emits_pending_action_without_planner_or_consent() -> None:
    class ProviderMustNotRun:
        async def resolve(self, **_kwargs):
            raise AssertionError("fast UI action invoked provider")

    registry = ToolRegistry()
    registry.register(invoke_client_capability_tool)
    loop = AssistantLoop(provider=ProviderMustNotRun(), registry=registry, system_prompt="Nove")
    context = LoopContext(
        user_name="alice",
        app_context=_app(
            surface={"id": "monitoring.query_history", "route": "/query-history"},
            view={"filters": {"status": ""}},
        ),
    )

    async def deny_consent(*_args):
        raise AssertionError("safe UI action prompted for consent")

    frames = [
        frame
        async for frame in loop.run(
            thread=AssistantThread(thread_id="t", user_name="alice", title="T"),
            user_content="show only failed queries",
            context=context,
            resolve_consent=deny_consent,
        )
    ]
    action = next(
        json.loads(frame.split("data: ", 1)[1])
        for frame in frames
        if frame.startswith("event: client_action")
    )
    assert action["capability"] == "surface.set_filter"
    assert action["args"] == {"filter": "status", "value": "FAILED"}
    assert any('"finish_reason":"client_action_pending"' in frame for frame in frames)
    assert "completed" not in "".join(frames).lower()


def test_failed_query_fast_path_does_not_filter_an_unrelated_surface() -> None:
    assert _fast_client_action("show failed queries", _app()) is None


@pytest.mark.asyncio
async def test_authenticated_client_action_is_audited_before_dispatch(monkeypatch) -> None:
    from app.modules.assistant.tools import client_capability

    audits = []

    async def record(**fields):
        audits.append(fields)
        return "audit-1"

    monkeypatch.setattr(client_capability, "write_audit_log", record)
    context = LoopContext(
        user_name="alice",
        user={"username": "alice", "encrypted_password": "must-not-leak"},
        thread_id="thread-1",
        app_context=_app(),
    )
    outcome = await invoke_client_capability_tool.run(
        ToolInvocation(
            tool_call_id="call-1",
            tool_name="invoke_client_capability",
            arguments={"capability": "surface.refresh", "args": {}},
        ),
        context,
    )
    assert outcome.ok is True
    assert outcome.evidence == {"audit_id": "audit-1"}
    assert audits == [{
        "event_type": "assistant_client_action",
        "user_name": "alice",
        "action": "surface.refresh",
        "object_type": "NOVA_SURFACE",
        "object_name": "workspace.sql",
        "status": "PENDING",
        "session_id": "thread-1",
        "active_role": None,
    }]
    assert "must-not-leak" not in str(outcome)

    async def unavailable(**_fields):
        raise RuntimeError("audit database unavailable")

    monkeypatch.setattr(client_capability, "write_audit_log", unavailable)
    rejected = await invoke_client_capability_tool.run(
        ToolInvocation(
            tool_call_id="call-2",
            tool_name="invoke_client_capability",
            arguments={"capability": "surface.refresh", "args": {}},
        ),
        context,
    )
    assert rejected.ok is False
    assert rejected.error_class == "AUDIT_UNAVAILABLE"
    assert "client_action" not in rejected.metadata
