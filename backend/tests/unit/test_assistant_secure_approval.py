from __future__ import annotations

from io import BytesIO
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
