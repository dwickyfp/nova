"""Sharing shows the work, never the owner's rows; results re-run as the viewer."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import sharing, studio_router
from app.modules.agents.sharing import shared_messages, shared_steps, strip_rows

VIEWER = {"username": "bob", "encrypted_password": "enc", "assigned_roles": ["analyst"],
          "roles": ["analyst"], "active_role": "analyst", "session_id": "s"}
STEPS = [
    {"kind": "tool", "name": "semantic_query", "tool_call_id": "c1", "status": "done",
     "trace_detail": {"question": "revenue per kota", "semantic_view": {"id": "v1"},
                      "semantic_plan": {"metrics": ["total_revenue"]},
                      "generated_sql": "SELECT ...", "rows": [["Jakarta", 1]]}},
    {"kind": "answer_verification", "unsupported": ["5"], "evidence_columns": []},
    {"kind": "table", "rows": [["Jakarta", 900]], "columns": ["city", "revenue"]},
]


def test_snapshots_keep_plans_and_drop_every_row():
    kept = shared_steps(STEPS)
    assert kept[0]["trace_detail"] == {
        "question": "revenue per kota", "semantic_view": {"id": "v1"},
        "semantic_plan": {"metrics": ["total_revenue"]},
    }
    assert "rows" not in str(kept)
    assert strip_rows({"a": {"rows": [1], "data": {"x": 1}, "ok": True}}) == {"a": {"ok": True}}


def test_shared_ui_never_exports_private_form_inputs_or_embedded_snapshots():
    messages = shared_messages([
        {"role": "assistant", "content": "Public fallback", "steps": [
            {"kind": "ui", "program": "private program", "evidence": {"id": [{"amount": 42}]}}
        ]},
        {"role": "user", "content": "Email: private@example.com", "steps": [
            {"kind": "ui_action", "state": {"$email": "private@example.com"}}
        ]},
        {"role": "assistant", "content": "private@example.com", "steps": STEPS},
    ])
    assert messages[0]["content"] == "Public fallback"
    assert all(not message["steps"] for message in messages)
    assert "private@example.com" not in str(messages)


async def test_visible_shares_match_the_user_or_an_assigned_role(monkeypatch):
    execute = AsyncMock(side_effect=[{}, {"rows": []}])
    monkeypatch.setattr(sharing.db, "execute_system", execute)
    await sharing.share_repository.visible(VIEWER)
    sql, params = execute.call_args.args
    assert "target_type = 'user' AND target_name = %s" in sql
    assert "target_type = 'role' AND target_name IN (%s)" in sql
    assert params == ["bob", "analyst"]


async def test_an_unshared_thread_is_not_found(monkeypatch):
    monkeypatch.setattr(sharing.share_repository, "grant_for", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await studio_router.open_shared_thread("t1", user=VIEWER)
    assert caught.value.status_code == 404


async def test_a_shared_thread_opens_without_rows(monkeypatch):
    from app.modules.assistant.repository import assistant_repository

    monkeypatch.setattr(sharing.share_repository, "grant_for",
                        AsyncMock(return_value={"owner_name": "alice"}))
    monkeypatch.setattr(assistant_repository, "get_thread",
                        AsyncMock(return_value={"title": "Sales", "agent_id": "a1"}))
    monkeypatch.setattr(assistant_repository, "list_messages", AsyncMock(return_value=[
        {"message_id": "m1", "role": "assistant", "content": "Jakarta leads.", "steps": STEPS},
    ]))
    monkeypatch.setattr(studio_router, "write_audit_log", AsyncMock())
    opened = await studio_router.open_shared_thread("t1", user=VIEWER)
    assert opened["owner_name"] == "alice"
    assert "rows" not in str(opened["messages"])


async def test_a_result_re_runs_as_the_viewer_and_is_refused_without_access(monkeypatch):
    from app.modules.assistant.repository import assistant_repository
    from app.modules.intelligence.semantic_views import semantic_view_service

    monkeypatch.setattr(sharing.share_repository, "grant_for",
                        AsyncMock(return_value={"owner_name": "alice"}))
    monkeypatch.setattr(assistant_repository, "get_thread",
                        AsyncMock(return_value={"agent_id": "a1"}))
    monkeypatch.setattr(assistant_repository, "list_messages",
                        AsyncMock(return_value=[{"role": "assistant", "steps": STEPS}]))
    monkeypatch.setattr(semantic_view_service, "get_active_for_agent",
                        AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await studio_router.refresh_shared_result("t1", "c1", user=VIEWER)
    assert caught.value.status_code == 403
    viewer_arg = semantic_view_service.get_active_for_agent.call_args.args[1]
    assert viewer_arg["username"] == "bob"


async def test_only_a_dashboard_artifact_shares_and_computes_as_the_viewer(monkeypatch):
    """A shared dashboard is a dashboard artifact; its zones re-run as the viewer."""

    from app.modules.viz.service import viz_service

    monkeypatch.setattr(sharing.share_repository, "grant_for",
                        AsyncMock(return_value={"owner_name": "alice"}))
    monkeypatch.setattr(studio_router.artifact_repository, "get", AsyncMock(return_value={
        "artifact_id": "d1", "title": "D", "artifact_type": "dashboard",
        "viz_spec": {"version": 1, "title": "D", "zones": [
            {"zone_id": "z1", "kind": "sheet", "artifact_id": "art1",
             "rect": {"x": 0, "y": 0, "w": 12, "h": 2}},
        ]},
    }))
    compute = AsyncMock(return_value={"title": "D", "zones": [
        {"zone_id": "z1", "artifact_id": "art1", "presmodel": {"row_count": 1}},
    ]})
    monkeypatch.setattr(viz_service, "compute_dashboard", compute)

    computed = await studio_router.compute_shared_dashboard(
        "d1",
        body=studio_router.DashboardComputeRequest(spec={}),
        user=VIEWER,
    )
    assert computed["zones"][0]["presmodel"]["row_count"] == 1
    # The owner's definitions, the viewer's credentials.
    assert compute.call_args.kwargs["owner_name"] == "alice"
    assert compute.call_args.args[1]["username"] == "bob"


async def test_a_non_dashboard_artifact_cannot_be_shared_as_one(monkeypatch):
    monkeypatch.setattr(sharing.share_repository, "grant_for",
                        AsyncMock(return_value={"owner_name": "alice"}))
    monkeypatch.setattr(studio_router.artifact_repository, "get", AsyncMock(return_value={
        "artifact_id": "c1", "title": "Chart", "artifact_type": "chart",
        "viz_spec": None,
    }))
    with pytest.raises(HTTPException) as caught:
        await studio_router.open_shared_dashboard("c1", user=VIEWER)
    assert caught.value.status_code == 404
