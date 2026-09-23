from __future__ import annotations

from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException, UploadFile

from app.core.deps import get_current_user
from app.main import app as _app
from app.modules.assistant.consent import ConsentApproval, ConsentBroker
from app.modules.assistant.router import resolve_tool_call, resolve_upload_tool_call
from app.modules.assistant.schemas import ConsentDecisionRequest
from app.modules.assistant.state import thread_store
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.ui_actions import call_ui_operation_tool


@pytest.mark.asyncio
async def test_secure_approval_requires_the_owner_and_exact_password_field(monkeypatch) -> None:
    assert _app is not None
    broker = ConsentBroker()
    monkeypatch.setattr("app.modules.assistant.router.consent_broker", broker)
    thread = thread_store.create(user_name="alice", title="Test")
    future = broker.open(
        "create-user", thread_id=thread.thread_id, user_name="alice",
        classification="destructive", secure_fields=("password",),
    )
    try:
        with pytest.raises(HTTPException) as missing:
            await resolve_tool_call(
                "create-user", ConsentDecisionRequest(decision="allow_once"),
                user={"username": "alice"},
            )
        assert missing.value.status_code == 400
        with pytest.raises(HTTPException) as foreign:
            await resolve_tool_call(
                "create-user", ConsentDecisionRequest(
                    decision="allow_once", secure_input={"password": "private-marker"}
                ), user={"username": "bob"},
            )
        assert foreign.value.status_code == 404
        with pytest.raises(HTTPException) as extra:
            await resolve_tool_call(
                "create-user", ConsentDecisionRequest(
                    decision="allow_once",
                    secure_input={"password": "private-marker", "role": "admin"},
                ), user={"username": "alice"},
            )
        assert extra.value.status_code == 400
        response = await resolve_tool_call(
            "create-user", ConsentDecisionRequest(
                decision="allow_once", secure_input={"password": "private-marker"}
            ), user={"username": "alice"},
        )
        approval = await future
        assert response.status == "approved"
        assert isinstance(approval, ConsentApproval)
        assert approval.secure_input == {"password": "private-marker"}
        assert "private-marker" not in repr(approval)
        assert "private-marker" not in response.model_dump_json()
    finally:
        thread_store.clear()
        broker.clear()


@pytest.mark.asyncio
async def test_create_user_sends_secure_password_only_to_nova_api(monkeypatch) -> None:
    seen: list[dict] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **kwargs):
            seen.append(kwargs["json"])
            return httpx.Response(201, json={"username": "maya", "password_enabled": True})

    monkeypatch.setattr(
        "app.modules.assistant.tools.ui_actions.httpx.AsyncClient", lambda **_: FakeClient()
    )
    audit = AsyncMock(return_value="audit-1")
    monkeypatch.setattr("app.modules.assistant.tools.ui_actions.write_audit_log", audit)
    invocation = ToolInvocation(
        "create-user", "call_ui_operation", {
            "operation": "POST /api/v1/users", "body": {"username": "maya"},
        }
    )
    context = SimpleNamespace(
        user={"username": "admin", "session_id": "session-1"},
        secure_input={"password": "private-marker"},
        audit_session_id="thread-1",
    )
    assert "private-marker" not in call_ui_operation_tool.preview(invocation)
    outcome = await call_ui_operation_tool.run(invocation, context)
    assert outcome.ok
    assert seen == [{"username": "maya", "password": "private-marker"}]
    assert context.secure_input is None
    assert "private-marker" not in str(outcome)
    assert "private-marker" not in str(audit.await_args_list)


@pytest.mark.asyncio
async def test_create_user_secure_approval_passes_real_nova_role_gate(monkeypatch) -> None:
    user = {
        "username": "admin", "session_id": "session-1",
        "active_role": "ACCOUNTADMIN", "roles": ["ACCOUNTADMIN"],
        "assigned_roles": ["ACCOUNTADMIN"],
    }
    previous = dict(_app.dependency_overrides)
    _app.dependency_overrides[get_current_user] = lambda: user
    create_user = AsyncMock()
    monkeypatch.setattr("app.modules.users.router.user_service.create_user", create_user)
    monkeypatch.setattr(
        "app.modules.users.router.user_service.list_users", AsyncMock(return_value=[])
    )
    monkeypatch.setattr("app.modules.users.router.settings.RANGER_ENABLED", False)
    monkeypatch.setattr(
        "app.modules.assistant.tools.ui_actions.write_audit_log",
        AsyncMock(return_value="audit-1"),
    )
    try:
        outcome = await call_ui_operation_tool.run(
            ToolInvocation(
                "create-user", "call_ui_operation", {
                    "operation": "POST /api/v1/users", "body": {"username": "maya"},
                }
            ),
            SimpleNamespace(
                user=user, secure_input={"password": "private-marker"},
                audit_session_id="thread-1",
            ),
        )
    finally:
        _app.dependency_overrides.clear()
        _app.dependency_overrides.update(previous)
    assert outcome.ok
    assert create_user.await_args.kwargs["password"] == "private-marker"
    assert "private-marker" not in str(outcome)


@pytest.mark.asyncio
async def test_user_creation_failure_never_returns_password(monkeypatch) -> None:
    user = {
        "username": "admin", "session_id": "session-1",
        "active_role": "ACCOUNTADMIN", "roles": ["ACCOUNTADMIN"],
        "assigned_roles": ["ACCOUNTADMIN"],
    }
    previous = dict(_app.dependency_overrides)
    _app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr("app.modules.users.router.settings.RANGER_ENABLED", False)
    monkeypatch.setattr(
        "app.modules.users.router.user_service.create_user",
        AsyncMock(side_effect=RuntimeError("CREATE USER maya IDENTIFIED BY 'private-marker'")),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app), base_url="http://nova.internal"
        ) as client:
            response = await client.post(
                "/api/v1/users", json={"username": "maya", "password": "private-marker"}
            )
    finally:
        _app.dependency_overrides.clear()
        _app.dependency_overrides.update(previous)
    assert response.status_code == 400
    assert response.json()["detail"] == "Unable to create user"
    assert "private-marker" not in response.text


@pytest.mark.asyncio
async def test_secure_approval_cannot_create_user_without_admin_role(monkeypatch) -> None:
    user = {
        "username": "alice", "session_id": "session-1", "active_role": "analyst",
        "roles": ["analyst"], "assigned_roles": ["analyst"],
    }
    previous = dict(_app.dependency_overrides)
    _app.dependency_overrides[get_current_user] = lambda: user
    create_user = AsyncMock()
    monkeypatch.setattr("app.modules.users.router.user_service.create_user", create_user)
    monkeypatch.setattr(
        "app.modules.assistant.tools.ui_actions.write_audit_log",
        AsyncMock(return_value="audit-1"),
    )
    try:
        outcome = await call_ui_operation_tool.run(
            ToolInvocation(
                "create-user", "call_ui_operation", {
                    "operation": "POST /api/v1/users", "body": {"username": "maya"},
                }
            ),
            SimpleNamespace(
                user=user, secure_input={"password": "private-marker"},
                audit_session_id="thread-1",
            ),
        )
    finally:
        _app.dependency_overrides.clear()
        _app.dependency_overrides.update(previous)
    assert not outcome.ok
    assert "HTTP 403" in (outcome.error or "")
    assert "private-marker" not in str(outcome)
    create_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_stage_upload_approval_requires_owned_pending_call(monkeypatch) -> None:
    broker = ConsentBroker()
    monkeypatch.setattr("app.modules.assistant.router.consent_broker", broker)
    thread = thread_store.create(user_name="alice", title="Upload")
    future = broker.open(
        "stage-upload", thread_id=thread.thread_id, user_name="alice",
        classification="destructive", upload_required=True,
    )
    try:
        with pytest.raises(HTTPException) as foreign:
            await resolve_upload_tool_call(
                "stage-upload", UploadFile(file=BytesIO(b"data"), filename="data.csv"),
                user={"username": "bob"},
            )
        assert foreign.value.status_code == 404
        with pytest.raises(HTTPException) as json_approval:
            await resolve_tool_call(
                "stage-upload", ConsentDecisionRequest(decision="allow_once"),
                user={"username": "alice"},
            )
        assert json_approval.value.status_code == 400
        with pytest.raises(HTTPException) as traversal:
            await resolve_upload_tool_call(
                "stage-upload", UploadFile(file=BytesIO(b"data"), filename="../data.csv"),
                user={"username": "alice"},
            )
        assert traversal.value.status_code == 400
        result = await resolve_upload_tool_call(
            "stage-upload", UploadFile(file=BytesIO(b"private-file-data"), filename="data.csv"),
            user={"username": "alice"},
        )
        approval = await future
        assert result.status == "approved"
        assert isinstance(approval, ConsentApproval)
        assert approval.upload is not None
        assert approval.upload[1].read() == b"private-file-data"
        assert "private-file-data" not in str(result)
        assert "private-file-data" not in repr(approval)
        approval.upload[1].close()
    finally:
        thread_store.clear()
        broker.clear()


@pytest.mark.asyncio
async def test_stage_upload_passes_file_only_to_nova_api(monkeypatch) -> None:
    seen: list[bytes] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **kwargs):
            seen.append(kwargs["files"]["file"][1].read())
            assert "json" not in kwargs
            return httpx.Response(200, json={"success": True, "file": {"filename": "data.csv"}})

    monkeypatch.setattr(
        "app.modules.assistant.tools.ui_actions.httpx.AsyncClient", lambda **_: FakeClient()
    )
    monkeypatch.setattr(
        "app.modules.assistant.tools.ui_actions.write_audit_log",
        AsyncMock(return_value="audit-1"),
    )
    stream = BytesIO(b"private-file-data")
    invocation = ToolInvocation(
        "stage-upload", "call_ui_operation", {
            "operation": "POST /api/v1/stages/{stage_id}/files",
            "path_params": {"stage_id": "stage-1"},
        },
    )
    context = SimpleNamespace(
        user={"username": "alice", "session_id": "session-1"},
        secure_input=None, file_upload=("data.csv", stream, "text/csv"),
        audit_session_id="thread-1",
    )
    assert "private-file-data" not in call_ui_operation_tool.preview(invocation)
    outcome = await call_ui_operation_tool.run(invocation, context)
    assert outcome.ok
    assert seen == [b"private-file-data"]
    assert stream.closed
    assert context.file_upload is None
    assert "private-file-data" not in str(outcome)


@pytest.mark.asyncio
async def test_stage_upload_still_passes_nova_stage_access_gate(monkeypatch) -> None:
    user = {
        "username": "alice", "session_id": "session-1", "active_role": "analyst",
        "roles": ["analyst"], "assigned_roles": ["analyst"],
    }
    previous = dict(_app.dependency_overrides)
    _app.dependency_overrides[get_current_user] = lambda: user
    authorized = AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden"))
    upload = AsyncMock()
    monkeypatch.setattr("app.modules.stages.router._authorized_stage", authorized)
    monkeypatch.setattr("app.modules.stages.router.stage_service.upload_stream", upload)
    monkeypatch.setattr(
        "app.modules.assistant.tools.ui_actions.write_audit_log",
        AsyncMock(return_value="audit-1"),
    )
    stream = BytesIO(b"private-file-data")
    try:
        outcome = await call_ui_operation_tool.run(
            ToolInvocation(
                "stage-upload", "call_ui_operation", {
                    "operation": "POST /api/v1/stages/{stage_id}/files",
                    "path_params": {"stage_id": "stage-1"},
                },
            ),
            SimpleNamespace(
                user=user, secure_input=None,
                file_upload=("data.csv", stream, "text/csv"),
                audit_session_id="thread-1",
            ),
        )
    finally:
        _app.dependency_overrides.clear()
        _app.dependency_overrides.update(previous)
    assert not outcome.ok
    assert "HTTP 403" in (outcome.error or "")
    authorized.assert_awaited_once()
    upload.assert_not_awaited()
    assert stream.closed


@pytest.mark.asyncio
async def test_stage_upload_reaches_the_existing_nova_api_as_caller(monkeypatch) -> None:
    user = {
        "username": "alice", "session_id": "session-1", "active_role": "analyst",
        "roles": ["analyst"], "assigned_roles": ["analyst"],
    }
    previous = dict(_app.dependency_overrides)
    _app.dependency_overrides[get_current_user] = lambda: user

    @asynccontextmanager
    async def audited(*_args):
        yield

    stage = {"id": "stage-1", "name": "incoming"}
    authorized = AsyncMock(return_value=stage)

    async def upload_stream(_stage_id, filename, body):
        assert filename == "data.csv"
        assert body.read() == b"private-file-data"
        return {"filename": filename, "size": 17}

    monkeypatch.setattr("app.modules.stages.router._authorized_stage", authorized)
    monkeypatch.setattr("app.modules.stages.router._stage_mutation_audit", audited)
    monkeypatch.setattr("app.modules.stages.router.stage_service.upload_stream", upload_stream)
    monkeypatch.setattr(
        "app.modules.assistant.tools.ui_actions.write_audit_log",
        AsyncMock(return_value="audit-1"),
    )
    stream = BytesIO(b"private-file-data")
    try:
        outcome = await call_ui_operation_tool.run(
            ToolInvocation(
                "stage-upload", "call_ui_operation", {
                    "operation": "POST /api/v1/stages/{stage_id}/files",
                    "path_params": {"stage_id": "stage-1"},
                },
            ),
            SimpleNamespace(
                user=user, secure_input=None,
                file_upload=("data.csv", stream, "text/csv"),
                audit_session_id="thread-1",
            ),
        )
    finally:
        _app.dependency_overrides.clear()
        _app.dependency_overrides.update(previous)
    assert outcome.ok
    assert authorized.await_args.args == ("stage-1", user, "write")
    assert stream.closed
    assert "private-file-data" not in str(outcome)
