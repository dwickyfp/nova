from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.main import app as _app
from app.modules.agents.tools.create_agent import create_agent_tool
from app.modules.agents.tools.create_semantic_model import create_semantic_model_tool
from app.modules.assistant.tools import ToolInvocation
from app.modules.query.repository import QueryResult


@pytest.mark.asyncio
async def test_create_agent_cannot_silently_update_existing_name(monkeypatch) -> None:
    assert _app is not None
    list_agents = AsyncMock(return_value=[{"agent_id": "existing", "name": "analyst"}])
    update_agent = AsyncMock()
    monkeypatch.setattr(
        "app.modules.agents.tools.create_agent.agent_repository.list_agents", list_agents
    )
    monkeypatch.setattr(
        "app.modules.agents.tools.create_agent.agent_repository.update_agent", update_agent
    )
    outcome = await create_agent_tool.run(
        ToolInvocation("call", "create_agent", {"name": "analyst"}),
        SimpleNamespace(user_name="alice", database="sales"),
    )
    assert not outcome.ok
    assert "already exists" in (outcome.error or "")
    update_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_agent_rejects_unapproved_tool_name(monkeypatch) -> None:
    create_agent = AsyncMock()
    monkeypatch.setattr(
        "app.modules.agents.tools.create_agent.agent_repository.create_agent", create_agent
    )
    outcome = await create_agent_tool.run(
        ToolInvocation("call", "create_agent", {"name": "analyst", "tools": ["root_access"]}),
        SimpleNamespace(user_name="alice"),
    )
    assert not outcome.ok
    create_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_agent_audits_before_and_after_write(monkeypatch) -> None:
    events: list[str] = []

    async def audit(**fields):
        events.append(fields["status"])
        return "audit-1"

    async def create(*, owner_name, fields):
        assert events == ["PENDING"]
        assert owner_name == "alice"
        return {"name": fields["name"]}

    monkeypatch.setattr(
        "app.modules.agents.tools.create_agent.agent_repository.list_agents",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.modules.agents.tools.create_agent.agent_repository.create_agent", create
    )
    monkeypatch.setattr("app.modules.agents.tools.create_agent.write_audit_log", audit)
    outcome = await create_agent_tool.run(
        ToolInvocation("call", "create_agent", {"name": "analyst"}),
        SimpleNamespace(user_name="alice", database="sales", audit_session_id="thread-1"),
    )
    assert outcome.ok
    assert events == ["PENDING", "SUCCESS"]


@pytest.mark.asyncio
async def test_semantic_metadata_uses_callers_query_pipeline(monkeypatch) -> None:
    execute = AsyncMock(return_value=[QueryResult(
        columns=["Field", "Type", "Null", "Key"],
        rows=[["order_id", "BIGINT", "NO", "PRI"], ["amount", "DECIMAL", "YES", ""]],
        row_count=2,
    )])
    monkeypatch.setattr("app.modules.query.service.query_service.execute_statements", execute)
    context = SimpleNamespace(
        user={
            "username": "alice", "encrypted_password": "encrypted", "active_role": "analyst",
            "security_context_version": 3,
        },
        audit_session_id="thread-1",
    )
    metadata = await create_semantic_model_tool._fetch_metadata(["sales.orders"], context)
    assert metadata == [{
        "table": "sales.orders",
        "primary_key": "order_id",
        "columns": [{"name": "order_id", "type": "BIGINT"},
                    {"name": "amount", "type": "DECIMAL"}],
    }]
    assert execute.await_args.kwargs["username"] == "alice"
    assert execute.await_args.kwargs["role"] == "analyst"
    assert execute.await_args.kwargs["encrypted_password"] == "encrypted"
    assert execute.await_count == 2
    assert execute.await_args_list[0].kwargs["sql"].startswith("SELECT * FROM")
    assert execute.await_args_list[1].kwargs["sql"].startswith("DESCRIBE")


@pytest.mark.asyncio
async def test_semantic_metadata_denial_stops_before_provider(monkeypatch) -> None:
    execute = AsyncMock(return_value=[QueryResult(error="access denied")])
    generate = AsyncMock()
    monkeypatch.setattr("app.modules.query.service.query_service.execute_statements", execute)
    monkeypatch.setattr(create_semantic_model_tool, "_generate", generate)
    monkeypatch.setattr(
        "app.modules.agents.tools.create_semantic_model.agent_repository.list_semantic_models",
        AsyncMock(return_value=[]),
    )
    outcome = await create_semantic_model_tool.run(
        ToolInvocation("call", "create_semantic_model", {
            "name": "sales", "tables": ["sales.orders"]
        }),
        SimpleNamespace(
            user_name="alice",
            user={"username": "alice", "encrypted_password": "encrypted", "active_role": "analyst"},
            audit_session_id="thread-1",
        ),
    )
    assert not outcome.ok
    assert "could not be read" in (outcome.error or "")
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_semantic_create_cannot_silently_update_existing_name(monkeypatch) -> None:
    list_models = AsyncMock(return_value=[{"semantic_model_id": "existing", "name": "sales"}])
    update_model = AsyncMock()
    monkeypatch.setattr(
        "app.modules.agents.tools.create_semantic_model.agent_repository.list_semantic_models",
        list_models,
    )
    monkeypatch.setattr(
        "app.modules.agents.tools.create_semantic_model.agent_repository.update_semantic_model",
        update_model,
    )
    outcome = await create_semantic_model_tool.run(
        ToolInvocation("call", "create_semantic_model", {
            "name": "sales", "tables": ["sales.orders"]
        }),
        SimpleNamespace(
            user_name="alice",
            user={"username": "alice", "encrypted_password": "encrypted"},
        ),
    )
    assert not outcome.ok
    assert "already exists" in (outcome.error or "")
    update_model.assert_not_awaited()
