"""Studio artifacts store reproducible queries, never cached result rows."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents.artifact_editor import propose_artifact_edit
from app.modules.agents.artifact_repository import (
    ArtifactRepository,
    chart_template,
    validate_artifact_sql,
)
from app.modules.agents.studio_router import apply_artifact_edit, refresh_artifact
from app.modules.agents.studio_schemas import (
    ArtifactApplyRequest,
    ArtifactDraft,
    ArtifactEditRequest,
)
from app.modules.query.repository import QueryResult


def test_artifact_sql_must_be_one_read_only_statement() -> None:
    assert validate_artifact_sql(" SELECT 1; ") == "SELECT 1"
    with pytest.raises(ValueError, match="one read-only"):
        validate_artifact_sql("SELECT 1; SELECT 2")
    with pytest.raises(ValueError, match="Only read-only"):
        validate_artifact_sql("DROP TABLE sales.orders")


def test_chart_template_removes_every_persisted_row() -> None:
    stored = chart_template(
        {
            "mark": "bar",
            "title": "Revenue",
            "data": {"values": [{"region": "secret", "revenue": 42}]},
            "datasets": {"old": [{"region": "also-secret"}]},
            "encoding": {
                "x": {"field": "region", "type": "nominal"},
                "y": {"field": "revenue", "type": "quantitative"},
            },
        },
        title="Revenue",
    )
    assert stored["data"] == {"values": []}
    assert "datasets" not in stored
    assert "secret" not in str(stored)


@pytest.mark.asyncio
async def test_refresh_reexecutes_sql_with_current_session(monkeypatch) -> None:
    artifact = {
        "artifact_id": "art-1",
        "owner_name": "nova_admin",
        "agent_id": "agent-1",
        "thread_id": "thread-1",
        "title": "Revenue",
        "artifact_type": "table",
        "sql_text": "SELECT region, revenue FROM sales.summary",
        "database_name": "sales",
        "schema_name": "analytics",
        "chart_spec": None,
        "created_at": "2026-09-21 08:00:00",
        "updated_at": "2026-09-21 08:00:00",
    }
    get_artifact = AsyncMock(return_value=artifact)
    execute = AsyncMock(
        return_value=[
            QueryResult(
                columns=["region", "revenue"],
                rows=[["West", 120]],
                row_count=1,
                elapsed_ms=4.2,
                original_sql=artifact["sql_text"],
            )
        ]
    )
    monkeypatch.setattr("app.modules.agents.studio_router.artifact_repository.get", get_artifact)
    monkeypatch.setattr(
        "app.modules.agents.studio_router.query_service.execute_statements", execute
    )

    response = await refresh_artifact(
        "art-1",
        user={
            "username": "nova_admin",
            "encrypted_password": "ciphertext",
            "session_id": "session-1",
            "roles": ["analyst"],
            "active_role": "analyst",
        },
    )

    assert response.rows == [["West", 120]]
    execute.assert_awaited_once_with(
        sql=artifact["sql_text"],
        username="nova_admin",
        encrypted_password="ciphertext",
        database="sales",
        schema="analytics",
        role="analyst",
        max_rows=500,
        session_id="session-1",
        confirm_destructive=False,
    )


@pytest.mark.parametrize("mark", ["bar", "line"])
@pytest.mark.asyncio
async def test_chat_edit_previews_a_chart_without_saving(monkeypatch, mark: str) -> None:
    artifact = {
        "artifact_id": "art-1",
        "title": "Revenue by region",
        "artifact_type": "table",
        "sql_text": "SELECT region, revenue FROM sales.summary",
        "chart_spec": None,
        "database_name": "sales",
        "schema_name": "analytics",
    }
    user = {
        "username": "analyst",
        "encrypted_password": "ciphertext",
        "session_id": "session-1",
        "roles": ["analyst"],
        "active_role": "analyst",
    }
    execute = AsyncMock(
        return_value=[
            QueryResult(
                columns=["region", "revenue"],
                rows=[["West", "120.5"]],
                row_count=1,
            )
        ]
    )
    resolve = AsyncMock(return_value=object())
    complete = AsyncMock(
        return_value={
            "content": json.dumps(
                {
                    "kind": "proposal",
                    "message": f"I made a {mark} chart.",
                    "sql_text": artifact["sql_text"],
                    "view": mark,
                    "x_column": "region",
                    "y_column": "revenue",
                }
            )
        }
    )
    monkeypatch.setattr(
        "app.modules.agents.artifact_editor.query_service.execute_statements", execute
    )
    monkeypatch.setattr("app.modules.agents.artifact_editor.assistant_provider.resolve", resolve)
    monkeypatch.setattr("app.modules.agents.artifact_editor.assistant_provider.complete", complete)

    response = await propose_artifact_edit(
        artifact,
        ArtifactEditRequest(instruction="Make this a bar chart", columns=["region", "revenue"]),
        user,
    )

    assert response.draft is not None
    assert response.draft.artifact_type == "chart"
    assert response.draft.chart_spec["mark"] == mark
    assert response.draft.chart_spec["data"] == {"values": []}
    assert response.rows == [["West", "120.5"]]
    assert execute.await_count == 1
    sent = complete.await_args.kwargs["messages"]
    assert "ciphertext" not in str(sent)


@pytest.mark.asyncio
async def test_chat_edit_rejects_non_read_only_sql(monkeypatch) -> None:
    artifact = {
        "title": "Revenue",
        "artifact_type": "table",
        "sql_text": "SELECT revenue FROM sales.summary",
        "chart_spec": None,
        "database_name": "sales",
        "schema_name": "analytics",
    }
    user = {
        "username": "analyst",
        "encrypted_password": "ciphertext",
        "session_id": "session-1",
        "roles": [],
    }
    execute = AsyncMock(return_value=[QueryResult(columns=["revenue"], rows=[[1]])])
    monkeypatch.setattr(
        "app.modules.agents.artifact_editor.query_service.execute_statements", execute
    )
    monkeypatch.setattr(
        "app.modules.agents.artifact_editor.assistant_provider.resolve",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "app.modules.agents.artifact_editor.assistant_provider.complete",
        AsyncMock(
            return_value={
                "content": json.dumps(
                    {
                        "kind": "proposal",
                        "message": "Done",
                        "sql_text": "DROP TABLE sales.summary",
                        "view": "table",
                    }
                )
            }
        ),
    )

    with pytest.raises(ValueError, match="Only read-only"):
        await propose_artifact_edit(
            artifact, ArtifactEditRequest(instruction="Delete the table"), user
        )
    assert execute.await_count == 0


@pytest.mark.asyncio
async def test_apply_edit_checks_owner_revision_and_audits(monkeypatch) -> None:
    artifact = {
        "artifact_id": "art-1",
        "owner_name": "analyst",
        "title": "Revenue",
        "artifact_type": "table",
        "sql_text": "SELECT region, revenue FROM sales.summary",
        "chart_spec": None,
        "database_name": "sales",
        "schema_name": "analytics",
        "created_at": "2026-09-21 08:00:00",
        "updated_at": "2026-09-21 08:00:00",
    }
    updated = {
        **artifact,
        "sql_text": "SELECT region FROM sales.summary",
        "updated_at": "2026-09-21 09:00:00",
    }
    get = AsyncMock(return_value=artifact)
    update = AsyncMock(return_value=updated)
    execute = AsyncMock(
        return_value=[
            QueryResult(
                columns=["region"],
                rows=[["West"]],
                row_count=1,
            )
        ]
    )
    audit = AsyncMock()
    monkeypatch.setattr("app.modules.agents.studio_router.artifact_repository.get", get)
    monkeypatch.setattr("app.modules.agents.studio_router.artifact_repository.update", update)
    monkeypatch.setattr(
        "app.modules.agents.artifact_editor.query_service.execute_statements", execute
    )
    monkeypatch.setattr("app.modules.agents.studio_router.write_audit_log", audit)
    body = ArtifactApplyRequest(
        draft=ArtifactDraft(
            sql_text=updated["sql_text"],
            artifact_type="table",
            chart_spec=None,
        ),
        expected_updated_at="2026-09-21T08:00:00",
    )
    user = {
        "username": "analyst",
        "encrypted_password": "ciphertext",
        "session_id": "session-1",
        "roles": ["analyst"],
        "active_role": "analyst",
    }

    response = await apply_artifact_edit("art-1", body, user=user)
    assert response.artifact.sql_text == updated["sql_text"]
    update.assert_awaited_once()
    assert update.await_args.kwargs["owner_name"] == "analyst"
    assert update.await_args.kwargs["expected_updated_at"] == body.expected_updated_at
    audit.assert_awaited_once()

    update.return_value = None
    with pytest.raises(HTTPException, match="This artifact changed"):
        await apply_artifact_edit("art-1", body, user=user)


@pytest.mark.asyncio
async def test_repository_update_requires_matching_owner_and_revision(monkeypatch) -> None:
    execute = AsyncMock(return_value={"affected": 0})
    get = AsyncMock()
    monkeypatch.setattr("app.modules.agents.artifact_repository.db.execute_system", execute)
    repository = ArtifactRepository()
    monkeypatch.setattr(repository, "get", get)
    expected = datetime(2026, 9, 21, 8, 0, 0)

    result = await repository.update(
        "art-1",
        owner_name="analyst",
        expected_updated_at=expected,
        sql_text="SELECT region FROM sales.summary",
        artifact_type="table",
        chart_spec=None,
        title="Revenue",
    )

    assert result is None
    get.assert_not_awaited()
    statement, parameters = execute.await_args.args
    assert "owner_name = %s AND updated_at = %s" in statement
    assert parameters[-3:] == ["art-1", "analyst", expected]
