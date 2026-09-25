from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.agents.registry import KNOWN_TOOLS
from app.modules.assistant.app_context import NoveAppContext
from app.modules.assistant.registry import build_registry
from app.modules.assistant.tools import ToolInvocation, requires_consent
from app.modules.assistant.tools.query_context import (
    inspect_query_error_tool,
    verify_query_repair_tool,
)


def _event(
    event_id: str,
    event_type: str,
    execution_id: str | None = None,
    correlation_id: str | None = None,
    **payload,
) -> dict:
    return {
        "id": event_id,
        "timestamp": "2026-09-24T10:00:00Z",
        "source": "execution" if event_type.startswith("query_") else "assistant",
        "type": event_type,
        "surfaceId": "workspace.sql",
        "executionId": execution_id,
        "correlationId": correlation_id,
        "status": "failure" if event_type == "query_failed" else "success",
        "payload": {"documentId": "file-1", **payload},
    }


def _context(status: str, execution_id: str, events: list[dict]) -> SimpleNamespace:
    app = NoveAppContext.model_validate(
        {
            "version": 1,
            "surface": {"id": "workspace.sql", "route": "/workspaces"},
            "entity": {"type": "workspace_file", "id": "file-1"},
            "editor": {"documentId": "file-1", "language": "sql"},
            "execution": {
                "type": "sql",
                "executionId": execution_id,
                "status": status,
                "errorMessage": "Unknown column revenue" if status == "error" else None,
                "rowCount": 3 if status == "success" else 0,
            },
            "events": events,
        }
    )
    return SimpleNamespace(app_context=app, agent_id=None)


def _call(name: str, **arguments) -> ToolInvocation:
    return ToolInvocation("call-1", name, arguments)


def test_query_context_tools_are_nove_only_and_need_no_consent() -> None:
    names = build_registry().names()
    assert {"inspect_query_error", "verify_query_repair"} <= set(names)
    assert not {"inspect_query_error", "verify_query_repair"} & KNOWN_TOOLS
    assert not requires_consent(inspect_query_error_tool)
    assert not requires_consent(verify_query_repair_tool)


@pytest.mark.asyncio
async def test_inspect_query_error_uses_matching_execution_event_only() -> None:
    events = [
        _event("old", "query_failed", "old-exec", sql="SELECT old"),
        _event("current", "query_failed", "exec-1", sql="SELECT revenue FROM sales"),
    ]
    result = await inspect_query_error_tool.run(
        _call("inspect_query_error"), _context("error", "exec-1", events)
    )
    assert result.ok is True
    assert result.data["execution_id"] == "exec-1"
    assert result.data["sql"] == "SELECT revenue FROM sales"
    assert result.data["error_category"] == "column"
    assert result.data["repair_applied"] is False
    assert result.data["verification"] == "not_run"


@pytest.mark.asyncio
async def test_inspect_query_error_does_not_use_stale_or_secret_sql() -> None:
    wrong_document = _event(
        "wrong", "query_failed", "exec-1", sql="SELECT wrong FROM old_file"
    )
    wrong_document["payload"]["documentId"] = "file-2"
    result = await inspect_query_error_tool.run(
        _call("inspect_query_error"), _context("error", "exec-1", [wrong_document])
    )
    assert result.ok is True
    assert result.data["sql"] is None

    secret = _event(
        "secret", "query_failed", "exec-1", sql="SELECT * FROM users WHERE password='hidden'"
    )
    result = await inspect_query_error_tool.run(
        _call("inspect_query_error"), _context("error", "exec-1", [secret])
    )
    assert result.ok is True
    assert result.data["sql"] is None
    assert "hidden" not in str(result)

    result = await inspect_query_error_tool.run(
        _call("inspect_query_error"), _context("success", "exec-2", [secret])
    )
    assert result.ok is False
    assert "no error" in result.error


@pytest.mark.asyncio
async def test_inspect_query_error_uses_latest_assistant_artifact_run() -> None:
    failed_card = _event(
        "card-failed", "query_failed", "card-exec", sql="SELECT bad FROM sales",
        errorMessage="Unknown column bad",
    )
    failed_card["artifactId"] = "card-1"
    app = NoveAppContext.model_validate({
        "version": 1,
        "surface": {"id": "workspace.sql", "route": "/workspaces"},
        "editor": {"documentId": "file-1", "language": "sql"},
        "events": [failed_card],
    })
    context = SimpleNamespace(app_context=app, agent_id=None)
    result = await inspect_query_error_tool.run(_call("inspect_query_error"), context)
    assert result.ok is True
    assert result.data["execution_id"] == "card-exec"
    assert result.data["sql"] == "SELECT bad FROM sales"
    assert result.data["error_category"] == "column"

    completed_card = _event("card-success", "query_completed", "card-rerun")
    completed_card["artifactId"] = "card-1"
    app = NoveAppContext.model_validate({
        "version": 1,
        "surface": {"id": "workspace.sql", "route": "/workspaces"},
        "editor": {"documentId": "file-1", "language": "sql"},
        "events": [failed_card, completed_card],
    })
    result = await inspect_query_error_tool.run(
        _call("inspect_query_error"), SimpleNamespace(app_context=app, agent_id=None)
    )
    assert result.ok is False
    assert "no error" in result.error


@pytest.mark.asyncio
async def test_verify_query_repair_requires_patch_then_correlated_success() -> None:
    events = [
        _event("failed", "query_failed", "exec-1", sql="SELECT bad"),
        _event("patch", "editor_patch_applied", correlation_id="artifact-1", persisted=True),
        _event(
            "success",
            "query_completed",
            "exec-2",
            correlation_id="artifact-1",
            sql="SELECT corrected",
        ),
    ]
    result = await verify_query_repair_tool.run(
        _call("verify_query_repair", correlation_id="artifact-1"),
        _context("success", "exec-2", events),
    )
    assert result.ok is True
    assert result.data["status"] == "verified_success"
    assert result.data["persisted"] is True
    assert result.data["row_count"] == 3
    assert result.evidence["patch_event_id"] == "patch"
    assert result.evidence["execution_event_id"] == "success"


@pytest.mark.asyncio
async def test_verify_query_repair_rejects_patch_only_or_unlinked_success() -> None:
    patch = _event("patch", "editor_patch_applied", correlation_id="artifact-1")
    success = _event("success", "query_completed", "exec-2", correlation_id="artifact-2")
    result = await verify_query_repair_tool.run(
        _call("verify_query_repair"), _context("success", "exec-2", [patch, success])
    )
    assert result.ok is False
    assert result.error_class == "POSTCONDITION_UNVERIFIED"

    result = await verify_query_repair_tool.run(
        _call("verify_query_repair"), _context("error", "exec-1", [patch])
    )
    assert result.ok is False
    assert result.error_class == "POSTCONDITION_UNVERIFIED"


@pytest.mark.asyncio
async def test_verify_query_repair_rejects_patch_after_rerun() -> None:
    events = [
        _event("success", "query_completed", "exec-2", correlation_id="artifact-1"),
        _event("patch", "editor_patch_applied", correlation_id="artifact-1"),
    ]
    result = await verify_query_repair_tool.run(
        _call("verify_query_repair"), _context("success", "exec-2", events)
    )
    assert result.ok is False
    assert result.error_class == "POSTCONDITION_UNVERIFIED"
