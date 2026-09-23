from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.assistant.registry import build_registry
from app.modules.assistant.tools import ToolInvocation, invocation_classification, role_access


def _context(active_role: str = "ACCOUNTADMIN") -> SimpleNamespace:
    return SimpleNamespace(
        user={
            "username": "nova_admin",
            "active_role": active_role,
            "assigned_roles": [active_role],
            "session_id": "session-1",
        },
        audit_session_id="session-1",
    )


def _invocation(name: str = "grant_role_access") -> ToolInvocation:
    return ToolInvocation(
        "grant-1",
        name,
        {
            "role": "ACCOUNTADMIN",
            "grants": [
                {"resource": "NOVA_SALES.fact_sales", "access": "SELECT"},
                {"resource": "NOVA_SALES", "access": "USAGE"},
            ],
        },
    )


def test_default_nove_registry_keeps_typed_role_access_with_api_bridge() -> None:
    names = build_registry().names()
    assert "inspect_role_access" in names
    assert "grant_role_access" in names
    assert "find_ui_operation" not in names
    assert "list_ui_operations" in names
    assert "call_ui_operation" in names
    classification = invocation_classification(role_access.grant_role_access_tool, _invocation())
    assert classification == "destructive"
    preview = role_access.grant_role_access_tool.preview(_invocation())
    assert "ACCOUNTADMIN" in preview
    assert "SELECT on NOVA_SALES.fact_sales" in preview
    assert "/api/" not in preview


@pytest.mark.asyncio
async def test_grant_role_access_uses_internal_service_and_exact_role(monkeypatch) -> None:
    calls = []

    class Service:
        async def list_roles(self):
            return [{"name": "ACCOUNTADMIN"}]

        async def grant_access(self, security, **kwargs):
            calls.append((security, kwargs))
            return {"id": len(calls)}

    async def audit(**kwargs):
        return f"audit-{kwargs['status']}"

    monkeypatch.setattr(role_access, "access_control_service", Service())
    monkeypatch.setattr(role_access, "write_audit_log", audit)

    result = await role_access.grant_role_access_tool.run(_invocation(), _context())

    assert result.ok is True
    assert [item[1]["role"] for item in calls] == ["ACCOUNTADMIN", "ACCOUNTADMIN"]
    assert calls[0][1]["table"] == "fact_sales"
    assert calls[0][1]["accesses"] == ["SELECT"]
    assert calls[1][1]["table"] == "*"
    assert calls[1][1]["accesses"] == ["USAGE"]
    assert result.data["status"] == "PROPAGATING"


@pytest.mark.asyncio
async def test_grant_role_access_denies_non_admin_before_service(monkeypatch) -> None:
    class Service:
        async def list_roles(self):
            raise AssertionError("unauthorized lookup reached service")

        async def grant_access(self, *args, **kwargs):
            raise AssertionError("unauthorized grant reached service")

    monkeypatch.setattr(role_access, "access_control_service", Service())
    result = await role_access.grant_role_access_tool.run(
        _invocation(), _context("rbac_city_reader")
    )
    assert result.ok is False
    assert "not authorized" in result.error


@pytest.mark.asyncio
async def test_inspect_role_access_reports_actual_ranger_permission(monkeypatch) -> None:
    class Service:
        async def list_roles(self):
            return [{"name": "ACCOUNTADMIN"}]

        async def effective_access(self, *, principal, active_role, resource):
            assert principal == "nova_admin"
            assert active_role == "ACCOUNTADMIN"
            return {"object_access": ["SELECT"] if resource.endswith("fact_sales") else []}

    async def audit(**kwargs):
        return "audit-inspect"

    monkeypatch.setattr(role_access, "access_control_service", Service())
    monkeypatch.setattr(role_access, "write_audit_log", audit)
    result = await role_access.inspect_role_access_tool.run(
        _invocation("inspect_role_access"), _context()
    )
    assert result.ok is True
    assert [item["granted"] for item in result.data["access"]] == [True, False]


@pytest.mark.asyncio
async def test_grant_role_access_rejects_missing_role_and_invalid_resource(monkeypatch) -> None:
    calls = []

    class Service:
        async def list_roles(self):
            return [{"name": "analyst"}]

        async def grant_access(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(role_access, "access_control_service", Service())
    missing = await role_access.grant_role_access_tool.run(_invocation(), _context())
    assert missing.ok is False
    assert "does not exist" in missing.error

    injected = _invocation()
    injected.arguments["grants"][0]["resource"] = "NOVA_SALES.fact_sales;DROP_ROLE"
    invalid = await role_access.grant_role_access_tool.run(injected, _context())
    assert invalid.ok is False
    assert "Invalid resource" in invalid.error
    assert calls == []
