from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.deps import get_current_user
from app.main import app
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.ui_actions import (
    _catalog,
    _request_parts,
    _safe_value,
    call_ui_operation_tool,
    list_ui_operations_tool,
)


def _call(operation: str, **arguments: object) -> ToolInvocation:
    return ToolInvocation("test-call", "call_ui_operation", {"operation": operation, **arguments})


def test_catalog_covers_requested_ui_operations_and_excludes_private_routes() -> None:
    operations = _catalog()
    assert "PUT /api/v1/workspaces/files/{entry_id}" in operations
    assert "POST /api/v1/users" in operations
    assert "POST /api/v1/access-control/data-scopes" in operations
    assert "POST /api/v1/agents/mcp-servers" in operations
    assert "POST /api/v1/semantic-views" in operations
    assert "POST /api/v1/agents/semantic-models" not in operations
    assert "POST /api/v1/stages/{stage_id}/files" in operations
    assert "POST /api/v1/explorer/databases/{database}/stages/{stage}/files" in operations
    assert "PATCH /api/v1/assistant/threads/{thread_id}" in operations
    assert "DELETE /api/v1/agents/{agent_id}/threads/{thread_id}" in operations
    assert "POST /api/v1/users/{username}/reset-password" not in operations
    assert "POST /api/v1/assistant/threads/{thread_id}/messages" not in operations
    assert "PUT /api/v1/assistant/threads/{thread_id}/grant" not in operations
    assert "GET /api/v1/stages/{stage_id}/files/{filename}" not in operations


async def _browse(resource: str, method: str) -> dict[str, dict]:
    offset = 0
    found = {}
    while True:
        outcome = await list_ui_operations_tool.run(
            ToolInvocation(
                "browse",
                "list_ui_operations",
                {"resource": resource, "method": method, "offset": offset},
            ),
            None,
        )
        assert outcome.ok
        found.update({item["operation"]: item for item in outcome.data["operations"]})
        if outcome.data["next_offset"] is None:
            return found
        offset = outcome.data["next_offset"]


@pytest.mark.asyncio
async def test_browse_lists_exact_resources_and_operation_schema() -> None:
    resources = await list_ui_operations_tool.run(
        ToolInvocation("browse", "list_ui_operations", {}), None
    )
    assert resources.ok
    assert {"semantic-views", "workspaces", "users", "agents"} <= set(
        resources.data["resources"]
    )
    found = await _browse("semantic-views", "POST")
    create = found["POST /api/v1/semantic-views"]
    assert "name" in create["body_required"]
    assert "definition" in create["body_required"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resource", "method", "expected"),
    [
        ("workspaces", "PUT", "PUT /api/v1/workspaces/files/{entry_id}"),
        ("users", "POST", "POST /api/v1/users"),
        ("agents", "POST", "POST /api/v1/agents"),
        ("semantic-views", "POST", "POST /api/v1/semantic-views"),
        ("agents", "POST", "POST /api/v1/agents/mcp-servers"),
        ("agents", "PATCH", "PATCH /api/v1/agents/mcp-servers/{server_id}"),
        ("access-control", "POST", "POST /api/v1/access-control/data-scopes"),
        ("query", "POST", "POST /api/v1/query/execute"),
        ("stages", "POST", "POST /api/v1/stages/{stage_id}/files"),
        (
            "explorer",
            "POST",
            "POST /api/v1/explorer/databases/{database}/stages/{stage}/files",
        ),
    ],
)
async def test_browse_reaches_supported_action(resource, method, expected):
    assert expected in await _browse(resource, method)


def test_mutations_require_consent_and_preview_exact_payload() -> None:
    call = _call(
        "PUT /api/v1/workspaces/files/{entry_id}",
        path_params={"entry_id": "file-1"},
        body={"content": "SELECT 1"},
    )
    assert call_ui_operation_tool.classification_for(call) == "destructive"
    assert "PUT /api/v1/workspaces/files/file-1" in call_ui_operation_tool.preview(call)
    assert "SELECT 1" in call_ui_operation_tool.preview(call)


def test_sensitive_input_and_path_confusion_are_rejected() -> None:
    password = _call(
        "POST /api/v1/users",
        body={"username": "analyst", "password": "ordinary-password"},
    )
    with pytest.raises(ValueError, match="Credential-bearing"):
        _request_parts(password)
    path = _call(
        "PUT /api/v1/workspaces/files/{entry_id}",
        path_params={"entry_id": "../users"},
        body={"content": "SELECT 1"},
    )
    with pytest.raises(ValueError, match="Invalid path parameter"):
        _request_parts(path)
    assert _safe_value({"api_key": "unknown-format-secret"}) == {"api_key": "***"}
    assert _safe_value({"columns": ["password"], "rows": [["unknown-format-secret"]]}) == {
        "columns": ["password"], "rows": [["***"]]
    }
    mcp = _call(
        "POST /api/v1/agents/mcp-servers",
        body={"name": "reports", "transport": "http", "endpoint": "https://mcp.example.com"},
    )
    assert "https://mcp.example.com" in call_ui_operation_tool.preview(mcp)
    assert _safe_value({"args": ["--token", "private-marker"]}) == {
        "args": ["***", "***"]
    }
    with pytest.raises(ValueError, match="Credential-bearing"):
        _request_parts(_call(
            "POST /api/v1/agents/mcp-servers",
            body={"name": "hidden", "transport": "stdio", "command": "node",
                  "args": ["server.js", "--token", "private-marker"]},
        ))


def test_stage_file_delete_supports_nested_paths_without_traversal() -> None:
    operation = "DELETE /api/v1/stages/{stage_id}/files/{filename}"
    assert operation in _catalog()
    _, path, _, _, _ = _request_parts(_call(
        operation,
        path_params={"stage_id": "stage-1", "filename": "reports/2026/data.csv"},
    ))
    assert path == "/api/v1/stages/stage-1/files/reports/2026/data.csv"
    for filename in ("../other/file.csv", "reports/../secret", "/absolute.csv", "a//b"):
        with pytest.raises(ValueError, match="Invalid path parameter"):
            _request_parts(_call(
                operation,
                path_params={"stage_id": "stage-1", "filename": filename},
            ))


def test_explorer_upload_accepts_only_a_normalized_destination_filename() -> None:
    operation = "POST /api/v1/explorer/databases/{database}/stages/{stage}/files"
    args = {"path_params": {"database": "sales", "stage": "incoming"}}
    _, _, _, _, body = _request_parts(_call(
        operation, **args, body={"filename": "reports/2026/data.csv"}
    ))
    assert body == {"filename": "reports/2026/data.csv"}
    for bad_body in ({"file": "private bytes"}, {"filename": "../secret"}):
        with pytest.raises(ValueError):
            _request_parts(_call(operation, **args, body=bad_body))


def test_sql_write_uses_one_statement_and_explicit_confirmation() -> None:
    call = _call("POST /api/v1/query/execute", body={"sql": "CREATE TABLE t (id INT)"})
    _, _, _, _, body = _request_parts(call)
    assert body["confirm_destructive"] is True
    assert body["max_rows"] == 100
    assert "CREATE TABLE t" in call_ui_operation_tool.preview(call)
    with pytest.raises(ValueError, match="one SQL statement"):
        _request_parts(_call(
            "POST /api/v1/query/execute", body={"sql": "CREATE TABLE t (id INT); DROP TABLE t"}
        ))
    with pytest.raises(ValueError, match="Credential-bearing"):
        _request_parts(_call(
            "POST /api/v1/query/execute", body={"sql": "CREATE USER bob IDENTIFIED BY 'secret'"}
        ))
    with pytest.raises(ValueError, match="query_execute"):
        _request_parts(_call(
            "POST /api/v1/query/execute", body={"sql": "SET ROLE analyst"}
        ))


@pytest.mark.asyncio
async def test_action_uses_real_api_role_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    user = {
        "username": "analyst",
        "session_id": "session-1",
        "roles": ["analyst"],
        "assigned_roles": ["analyst"],
        "active_role": "analyst",
    }
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = lambda: user
    audit = AsyncMock(return_value="audit-1")
    monkeypatch.setattr("app.modules.assistant.tools.ui_actions.write_audit_log", audit)
    try:
        outcome = await call_ui_operation_tool.run(
            _call("GET /api/v1/users"),
            SimpleNamespace(user=user, audit_session_id="thread-1"),
        )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
    assert not outcome.ok
    assert "HTTP 403" in (outcome.error or "")
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_accountadmin_protection_survives_ui_bridge(monkeypatch) -> None:
    user = {
        "username": "admin", "session_id": "session-1", "active_role": "ACCOUNTADMIN",
        "roles": ["ACCOUNTADMIN"], "assigned_roles": ["ACCOUNTADMIN"],
    }
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = lambda: user
    audit = AsyncMock(return_value="audit-1")
    monkeypatch.setattr("app.modules.assistant.tools.ui_actions.write_audit_log", audit)
    try:
        outcome = await call_ui_operation_tool.run(
            _call(
                "DELETE /api/v1/access-control/roles/{name}",
                path_params={"name": "ACCOUNTADMIN"},
            ),
            SimpleNamespace(user=user, audit_session_id="thread-1"),
        )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
    assert not outcome.ok
    assert "ACCOUNTADMIN" in (outcome.error or "")
    assert [entry.kwargs["status"] for entry in audit.await_args_list] == [
        "PENDING", "FAILED"
    ]


@pytest.mark.asyncio
async def test_workspace_edit_uses_ui_endpoint_and_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    user = {
        "username": "alice",
        "session_id": "session-1",
        "roles": ["analyst"],
        "assigned_roles": ["analyst"],
        "active_role": "analyst",
    }
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = lambda: user
    update = AsyncMock(return_value={
        "id": "file-1", "name": "query.sql", "parent_path": "", "path": "query.sql",
        "entry_type": "file", "size_bytes": 8,
    })
    audit = AsyncMock(return_value="audit-1")
    monkeypatch.setattr("app.modules.workspaces.service.workspace_service.update_file", update)
    monkeypatch.setattr("app.modules.assistant.tools.ui_actions.write_audit_log", audit)
    try:
        outcome = await call_ui_operation_tool.run(
            _call(
                "PUT /api/v1/workspaces/files/{entry_id}",
                path_params={"entry_id": "file-1"},
                body={"content": "SELECT 1"},
            ),
            SimpleNamespace(user=user, audit_session_id="thread-1"),
        )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
    assert outcome.ok
    update.assert_awaited_once_with("alice", "file-1", "SELECT 1")
    assert outcome.data["result"]["entry"]["id"] == "file-1"
    assert audit.await_count == 2
    assert audit.await_args_list[0].kwargs["status"] == "PENDING"
    assert audit.await_args_list[1].kwargs["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_sql_http_200_with_statement_error_is_failure(monkeypatch) -> None:
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **_kwargs):
            return httpx.Response(200, json=[{
                "success": False, "error": "permission denied", "rows": [],
            }])

    audit = AsyncMock(return_value="audit-1")
    monkeypatch.setattr(
        "app.modules.assistant.tools.ui_actions.httpx.AsyncClient", lambda **_: FakeClient()
    )
    monkeypatch.setattr("app.modules.assistant.tools.ui_actions.write_audit_log", audit)
    user = {"username": "alice", "session_id": "session-1", "active_role": "analyst"}
    outcome = await call_ui_operation_tool.run(
        _call("POST /api/v1/query/execute", body={"sql": "CREATE TABLE t (id INT)"}),
        SimpleNamespace(user=user, audit_session_id="thread-1"),
    )
    assert not outcome.ok
    assert "permission denied" in (outcome.error or "")
    assert [entry.kwargs["status"] for entry in audit.await_args_list] == [
        "PENDING", "FAILED"
    ]


@pytest.mark.asyncio
async def test_active_nove_thread_cannot_delete_itself() -> None:
    user = {"username": "alice", "session_id": "session-1"}
    outcome = await call_ui_operation_tool.run(
        _call(
            "DELETE /api/v1/assistant/threads/{thread_id}",
            path_params={"thread_id": "current-thread"},
        ),
        SimpleNamespace(user=user, thread_id="current-thread"),
    )
    assert not outcome.ok
    assert "cannot delete itself" in (outcome.error or "")
