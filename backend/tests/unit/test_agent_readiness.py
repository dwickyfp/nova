"""Readiness flags what makes a Studio agent answer badly, before users find it."""

from __future__ import annotations

import pytest
import yaml

from app.modules.agents.readiness import assess
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from tests.benchmark.studio_accuracy.model import bench_model

AGENT = {"default_tools": ["semantic_query", "data_to_chart"], "budget_profile": "analyst",
         "sample_questions": ["Penjualan per kota bulan ini", "Berapa gaji karyawan?"]}


def by_id(result):
    return {check["id"].split(":")[0]: check for check in result["checks"]}


def test_a_well_built_view_is_ready_and_reports_sample_coverage():
    result = assess(AGENT, [{"_scoped_ir": bench_model()}])
    checks = by_id(result)
    assert result["ready"]
    assert checks["dimensions"]["status"] == "ok"
    assert checks["sample_questions"]["detail"].startswith("1 of 2 resolve")


def _without_plain_dimensions(node):
    """The demo model before 2026-09-28: text fields had no ``dimension:`` key."""
    if isinstance(node, dict):
        return {key: _without_plain_dimensions(value) for key, value in node.items()
                if not (key == "dimension" and value == {})}
    if isinstance(node, list):
        return [_without_plain_dimensions(item) for item in node]
    return node


def test_text_fields_that_cannot_group_are_flagged():
    with open("app/modules/agents/examples/nova_sales.ossie.yaml") as source:
        fixed = yaml.safe_load(source)
    original = yaml.safe_dump(_without_plain_dimensions(fixed))
    ir = SemanticModelIR.from_ossie(parse_ossie(original).as_dict())
    checks = by_id(assess(AGENT, [{"_scoped_ir": ir}]))
    assert checks["dimensions"]["status"] == "warn"
    assert "shipping_city" in checks["dimensions"]["detail"]
    ir = SemanticModelIR.from_ossie(parse_ossie(yaml.safe_dump(fixed)).as_dict())
    assert by_id(assess(AGENT, [{"_scoped_ir": ir}]))["dimensions"]["status"] == "ok"


def test_no_view_or_data_tool_is_not_ready():
    result = assess({"default_tools": [], "budget_profile": "fast"}, [])
    checks = by_id(result)
    assert not result["ready"]
    assert checks["semantic_view"]["status"] == "fail"
    assert checks["tools"]["status"] == "fail"
    assert checks["budget"]["status"] == "warn"


async def test_the_owner_manages_the_agent_without_a_role_grant(monkeypatch):
    """Readiness and automations are configuration: ownership, not the role grant, gates them."""
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.modules.agents import router as agents_router
    from app.modules.agents.repository import agent_repository

    owned = {"agent_id": "a1", "owner_name": "alice"}
    monkeypatch.setattr(agent_repository, "get_agent", AsyncMock(
        side_effect=lambda agent_id, owner_name: owned if owner_name == "alice" else None
    ))
    verified = AsyncMock(return_value=False)
    monkeypatch.setattr(agents_router, "has_verified_access", verified)
    alice = {"username": "alice", "active_role": "PUBLIC", "session_id": "s"}
    assert await agents_router._require_owned_agent("a1", alice) == owned
    verified.assert_not_awaited()
    with pytest.raises(HTTPException) as denied:
        await agents_router._require_owned_agent("a1", {**alice, "username": "bob"})
    assert denied.value.status_code in {403, 404}
