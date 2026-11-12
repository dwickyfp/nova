from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile

from app.modules.assistant.consent import ConsentApproval, ConsentBroker
from app.modules.assistant.read_grants import stamp
from app.modules.assistant.schemas import ConsentDecisionRequest
from app.modules.assistant.state import thread_store
from tests.unit.test_assistant_consent_ownership import _signed_in


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["assistant", "agent"])
@pytest.mark.parametrize("decision", ["allow_once", "deny"])
@pytest.mark.parametrize("active", [True, False])
async def test_one_tool_decision_reports_the_existing_read_grant(
    monkeypatch, surface, decision, active,
):
    from app.modules.agents import router as agents
    from app.modules.assistant import router as assistant

    user = _signed_in("alice")
    broker = ConsentBroker()
    module = assistant if surface == "assistant" else agents
    monkeypatch.setattr(module, "consent_broker", broker)
    monkeypatch.setattr(agents, "_require_agent", AsyncMock(return_value={"agent_id": "agent-1"}))
    thread = thread_store.create(user_name="alice", title="Synthetic decision fixture")
    thread.consent.always_allow_read_only = active
    thread.consent.granted_under = stamp(user) if active else None
    future = broker.open(
        "write-1", thread_id=thread.thread_id, user=user, classification="destructive",
    )
    try:
        request = ConsentDecisionRequest(decision=decision)
        if surface == "assistant":
            response = await assistant.resolve_tool_call("write-1", request, user=user)
        else:
            response = await agents.resolve_agent_tool_call(
                "agent-1", "write-1", request, user=user,
            )
        assert await future is (decision == "allow_once")
        assert response.status == ("approved" if decision == "allow_once" else "denied")
        assert thread.consent.covers("read_only", user=user) is active
        assert not thread.consent.covers("destructive", user=user)
        assert response.grant_active is active
    finally:
        broker.clear()
        thread_store.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False])
async def test_file_approval_reports_the_existing_read_grant(monkeypatch, active):
    from app.modules.assistant import router as assistant

    user = _signed_in("alice")
    broker = ConsentBroker()
    monkeypatch.setattr(assistant, "consent_broker", broker)
    thread = thread_store.create(user_name="alice", title="Synthetic upload fixture")
    thread.consent.always_allow_read_only = active
    thread.consent.granted_under = stamp(user) if active else None
    future = broker.open(
        "upload-1", thread_id=thread.thread_id, user=user,
        classification="destructive", upload_required=True,
    )
    upload = UploadFile(file=BytesIO(b"synthetic,file\n1,2\n"), filename="fixture.csv")
    approval = None
    try:
        response = await assistant.resolve_upload_tool_call("upload-1", upload, user=user)
        approval = await future
        assert isinstance(approval, ConsentApproval) and approval.upload is not None
        assert approval.upload[0] == "fixture.csv"
        assert approval.upload[1].read() == b"synthetic,file\n1,2\n"
        assert response.status == "approved"
        assert thread.consent.covers("read_only", user=user) is active
        assert not thread.consent.covers("destructive", user=user)
        assert response.grant_active is active
    finally:
        if isinstance(approval, ConsentApproval) and approval.upload:
            approval.upload[1].close()
        await upload.close()
        broker.clear()
        thread_store.clear()
