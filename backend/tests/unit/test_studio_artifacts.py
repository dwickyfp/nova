"""Studio artifacts store reproducible queries, never cached result rows."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.agents.artifact_repository import chart_template, validate_artifact_sql
from app.modules.agents.studio_router import refresh_artifact
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
