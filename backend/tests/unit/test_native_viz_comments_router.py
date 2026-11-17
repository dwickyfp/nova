"""Comment routes: bounded input, dashboards only, and the service decides access."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.core.deps import get_current_user
from app.core.exceptions import NovaException, register_exception_handlers
from app.modules.viz import router as module

USER = {"username": "carol", "active_role": "EDITOR", "assigned_roles": ["EDITOR"],
        "session_id": "s", "security_context_version": 1}


@pytest.fixture
async def api(monkeypatch):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(module.router, prefix="/viz")
    app.dependency_overrides[get_current_user] = lambda: USER
    service = SimpleNamespace(
        require_kind=AsyncMock(), comments=AsyncMock(return_value=[]),
        add_comment=AsyncMock(return_value={"comment_id": "c"}),
        resolve_comment=AsyncMock(return_value={"comment_id": "c"}), delete_comment=AsyncMock())
    monkeypatch.setattr(module, "service", service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as client:
        yield client, service


async def test_posting_trims_text_and_checks_the_dashboard(api):
    client, service = api
    board, note, page = uuid4(), uuid4(), uuid4()
    response = await client.post(f"/viz/dashboards/{board}/comments", json={
        "comment_id": str(note), "body": "  Check Bandung  ", "page_id": str(page)})
    assert response.status_code == 201
    service.require_kind.assert_awaited_once()
    assert service.add_comment.await_args.args[1:] == (board, note, "Check Bandung")
    assert service.add_comment.await_args.kwargs == {"page_id": page, "widget_id": None}


@pytest.mark.parametrize("body", [
    {"body": "   "},
    {"body": "x" * 2001},
    {"body": "ok", "widget_id": str(uuid4())},
    {"body": "ok", "author": "owner"},
])
async def test_invalid_comments_are_refused(api, body):
    client, service = api
    response = await client.post(f"/viz/dashboards/{uuid4()}/comments",
                                 json={"comment_id": str(uuid4()), **body})
    assert response.status_code == 422
    service.add_comment.assert_not_awaited()


async def test_resolve_delete_and_list_go_through_the_service(api):
    client, service = api
    board, note = uuid4(), uuid4()
    assert (await client.get(f"/viz/dashboards/{board}/comments")).status_code == 200
    resolved = await client.patch(f"/viz/dashboards/{board}/comments/{note}",
                                  json={"resolved": True})
    assert resolved.status_code == 200
    assert service.resolve_comment.await_args.args[1:] == (board, note, True)
    removed = await client.request("DELETE", f"/viz/dashboards/{board}/comments/{note}", json={})
    assert removed.status_code == 204


async def test_a_refusal_from_the_store_reaches_the_caller(api):
    client, service = api
    service.comments.side_effect = NovaException("Document not found", status_code=404)
    assert (await client.get(f"/viz/dashboards/{uuid4()}/comments")).status_code == 404
