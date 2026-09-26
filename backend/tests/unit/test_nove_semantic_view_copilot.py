"""Nove creates governed Semantic Views instead of returning only a draft."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.assistant.registry import build_registry
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.create_semantic_view import CreateSemanticViewTool


@pytest.fixture(autouse=True)
def _metric_explain(monkeypatch):
    execute = AsyncMock(return_value=SimpleNamespace(error=None))
    monkeypatch.setattr(
        "app.modules.assistant.tools.create_semantic_view.query_service.execute",
        execute,
    )
    return execute


def _context():
    return SimpleNamespace(
        user_name="alice",
        user={
            "username": "alice",
            "encrypted_password": "encrypted",
            "active_role": "analyst",
            "session_id": "session-1",
        },
        active_role="analyst",
        audit_session_id="session-1",
        schema_name="",
    )


def _spec():
    return {
        "name": "sales_view",
        "datasets": [
            {
                "name": "orders",
                "source": "sales.orders",
                "primary_key": "order_id",
                "fields": [
                    {"name": "order_id", "datatype": "Integer"},
                    {"name": "amount", "datatype": "Decimal"},
                ],
            }
        ],
        "metrics": [{"name": "revenue", "expression": "SUM(orders.amount)", "datatype": "Decimal"}],
    }


def _invocation(**overrides):
    return ToolInvocation(
        "call-1",
        "create_semantic_view",
        {
            "name": "sales_view",
            "tables": ["sales.orders"],
            "request": "Revenue by month",
            **overrides,
        },
    )


def test_nove_registers_typed_creation_without_api_actions():
    names = set(build_registry().names())
    assert {"create_semantic_view", "provision_user", "query_mutate"} <= names
    assert not {"list_ui_operations", "call_ui_operation"} & names


@pytest.mark.asyncio
async def test_create_semantic_view_validates_and_publishes(monkeypatch, _metric_explain):
    designer = SimpleNamespace(
        _fetch_metadata=AsyncMock(
            return_value=[
                {
                    "table": "sales.orders",
                    "columns": [{"name": "order_id"}, {"name": "amount"}],
                }
            ]
        ),
        _generate=AsyncMock(return_value=_spec()),
    )
    create = AsyncMock(return_value={"id": "view-1"})
    validate = AsyncMock(return_value={"valid": True, "errors": []})
    publish = AsyncMock(return_value={"id": "view-1", "status": "ACTIVE"})
    monkeypatch.setattr(
        "app.modules.assistant.tools.create_semantic_view.semantic_view_service.create",
        create,
    )
    monkeypatch.setattr(
        "app.modules.assistant.tools.create_semantic_view.semantic_view_service.validate",
        validate,
    )
    monkeypatch.setattr(
        "app.modules.assistant.tools.create_semantic_view.semantic_view_service.publish",
        publish,
    )
    tool = CreateSemanticViewTool(designer=designer)
    assert tool.classification == "destructive" and tool.requires_consent
    assert "publish" in tool.preview(_invocation())
    outcome = await tool.run(_invocation(), _context())
    assert outcome.ok
    assert outcome.data["status"] == "PUBLISHED"
    assert outcome.data["view_id"] == "view-1"
    assert create.await_args.args[0].definition.startswith("version: 0.1.1")
    assert create.await_args.args[1]["active_role"] == "analyst"
    validate.assert_awaited_once()
    publish.assert_awaited_once_with("view-1", 1, create.await_args.args[1])
    assert _metric_explain.await_args.kwargs["sql"].startswith("EXPLAIN SELECT")


@pytest.mark.asyncio
async def test_unreadable_table_or_invented_column_never_creates(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(
        "app.modules.assistant.tools.create_semantic_view.semantic_view_service.create",
        create,
    )
    denied = CreateSemanticViewTool(
        designer=SimpleNamespace(
            _fetch_metadata=AsyncMock(return_value=[]),
            _generate=AsyncMock(),
        )
    )
    outcome = await denied.run(_invocation(), _context())
    assert not outcome.ok
    denied._designer._generate.assert_not_awaited()

    invented = _spec()
    invented["datasets"][0]["fields"].append({"name": "secret_column"})
    tool = CreateSemanticViewTool(
        designer=SimpleNamespace(
            _fetch_metadata=AsyncMock(
                return_value=[
                    {
                        "table": "sales.orders",
                        "columns": [{"name": "order_id"}, {"name": "amount"}],
                    }
                ]
            ),
            _generate=AsyncMock(return_value=invented),
        )
    )
    outcome = await tool.run(_invocation(), _context())
    assert not outcome.ok
    assert "invented" in outcome.error
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_metric_column_is_rejected_before_create(monkeypatch, _metric_explain):
    invalid = _spec()
    invalid["metrics"][0]["expression"] = "SUM(orders.secret_column)"
    _metric_explain.return_value = SimpleNamespace(error="Unknown column")
    create = AsyncMock()
    monkeypatch.setattr(
        "app.modules.assistant.tools.create_semantic_view.semantic_view_service.create",
        create,
    )
    tool = CreateSemanticViewTool(designer=SimpleNamespace(
        _fetch_metadata=AsyncMock(return_value=[{
            "table": "sales.orders",
            "columns": [{"name": "order_id"}, {"name": "amount"}],
        }]),
        _generate=AsyncMock(return_value=invalid),
    ))
    outcome = await tool.run(_invocation(), _context())
    assert not outcome.ok
    assert "metric" in outcome.error
    assert "secret_column" in _metric_explain.await_args.kwargs["sql"]
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_validation_keeps_reviewable_draft(monkeypatch):
    designer = SimpleNamespace(
        _fetch_metadata=AsyncMock(
            return_value=[
                {
                    "table": "sales.orders",
                    "columns": [{"name": "order_id"}, {"name": "amount"}],
                }
            ]
        ),
        _generate=AsyncMock(return_value=_spec()),
    )
    monkeypatch.setattr(
        "app.modules.assistant.tools.create_semantic_view.semantic_view_service.create",
        AsyncMock(return_value={"id": "view-2"}),
    )
    monkeypatch.setattr(
        "app.modules.assistant.tools.create_semantic_view.semantic_view_service.validate",
        AsyncMock(return_value={"valid": False, "errors": ["Invalid metric"]}),
    )
    publish = AsyncMock()
    monkeypatch.setattr(
        "app.modules.assistant.tools.create_semantic_view.semantic_view_service.publish",
        publish,
    )
    outcome = await CreateSemanticViewTool(designer=designer).run(_invocation(), _context())
    assert outcome.ok
    assert outcome.data["status"] == "DRAFT"
    assert outcome.warnings == ["Invalid metric"]
    publish.assert_not_awaited()
