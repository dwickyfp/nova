"""Sharing shows the work, never the owner's rows; results re-run as the viewer."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import sharing, studio_router
from app.modules.agents.sharing import shared_steps, strip_rows

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
                        AsyncMock(return_value=[{"steps": STEPS}]))
    monkeypatch.setattr(semantic_view_service, "get_active_for_agent",
                        AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await studio_router.refresh_shared_result("t1", "c1", user=VIEWER)
    assert caught.value.status_code == 403
    viewer_arg = semantic_view_service.get_active_for_agent.call_args.args[1]
    assert viewer_arg["username"] == "bob"


async def test_only_tiles_on_the_shared_dashboard_refresh(monkeypatch):
    monkeypatch.setattr(sharing.share_repository, "grant_for",
                        AsyncMock(return_value={"owner_name": "alice"}))
    layout = SimpleNamespace(tiles=[SimpleNamespace(artifact_id="art1")])
    monkeypatch.setattr(studio_router.dashboard_repository, "get",
                        AsyncMock(return_value={"layout": layout, "title": "D"}))
    with pytest.raises(HTTPException) as caught:
        await studio_router.refresh_shared_dashboard_artifact("d1", "art-other", user=VIEWER)
    assert caught.value.status_code == 404
    monkeypatch.setattr(studio_router.artifact_repository, "get", AsyncMock(return_value={
        "artifact_id": "art1", "title": "T", "artifact_type": "table", "chart_spec": None,
        "sql_text": "SELECT 1", "database_name": "db", "schema_name": None,
    }))
    execute = AsyncMock(return_value=[SimpleNamespace(
        success=True, columns=["x"], rows=[[1]], row_count=1)])
    monkeypatch.setattr(studio_router.query_service, "execute_statements", execute)
    refreshed = await studio_router.refresh_shared_dashboard_artifact("d1", "art1", user=VIEWER)
    assert refreshed["rows"] == [[1]]
    assert execute.call_args.kwargs["username"] == "bob"
