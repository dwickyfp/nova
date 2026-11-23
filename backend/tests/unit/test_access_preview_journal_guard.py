"""The Stream journal guard must not break the effective-access preview elsewhere.

Agent Verify Access, catalog governance, stages and pipes read
``effective_access``; a pattern policy anywhere used to make every table
``UNSUPPORTED``, so the first stream hid every agent.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.modules.access_control.service import AccessControlService
from app.modules.agents import access as agent_access
from app.modules.streams.sources import journal_guard_policy


def _grant(database="NOVA_DEMO", table="*", access="select", role="marketing", **extra):
    resources = {"catalog": {"values": ["default_catalog"]}, "database": {"values": [database]}}
    if table is not None:
        resources |= {"table": {"values": [table]}, "column": {"values": ["*"]}}
    return {
        "service": settings.RANGER_SERVICE_NAME, "isEnabled": True, "policyType": 0,
        "resources": resources,
        "policyItems": [{"roles": [role], "accesses": [{"type": access, "isAllowed": True}]}],
        **extra,
    }


class _Ranger:
    def __init__(self, policies):
        self.policies = policies

    async def list_policies(self, **_):
        return self.policies

    async def get_user_attributes(self, principal):
        return {}


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setattr(settings, "STREAM_WRITER_ROLE", "nova_stream_writer_role")
    return journal_guard_policy().to_api()


async def _preview(policies, resource):
    return await AccessControlService(_Ranger(policies)).effective_access(
        principal="alice", active_role="marketing", resource=resource,
    )


@pytest.mark.parametrize(
    "resource", ["NOVA_DEMO.orders", "NOVA_DEMO.*", "default_catalog.NOVA_DEMO.customers"]
)
async def test_guard_leaves_unrelated_tables_supported(guard, resource):
    result = await _preview([guard, _grant(), _grant(table=None, access="usage")], resource)
    assert result["object_access_comparison_supported"] is True
    assert result["policy_health"] == "ACTIVE"
    assert "SELECT" in result["object_access"]


async def test_guard_still_makes_journal_tables_unsupported(guard):
    result = await _preview([guard, _grant()], "NOVA_DEMO.__nova_stream_changes_abc")
    assert result["object_access_comparison_supported"] is False
    assert result["object_access"] == []


@pytest.mark.parametrize("table", ["ord*", "ORD*", "o?ders", "o[r]ders"])
async def test_patterns_that_may_cover_the_table_still_fail_closed(table):
    deny = _grant(table=table, denyPolicyItems=[
        {"roles": ["marketing"], "accesses": [{"type": "select", "isAllowed": True}]}
    ])
    result = await _preview([deny, _grant()], "NOVA_DEMO.orders")
    assert result["object_access_comparison_supported"] is False
    assert result["object_access"] == []


async def test_exclusion_that_literally_names_the_table_still_fails_closed():
    excluded = _grant(table="orders")
    excluded["resources"]["table"]["isExcludes"] = True  # every table except orders
    result = await _preview([excluded], "NOVA_DEMO.orders")
    assert result["object_access_comparison_supported"] is False
    assert result["object_access"] == []


async def test_agent_verify_access_works_after_a_stream_exists(monkeypatch, guard):
    """Agent dependencies on ordinary tables and databases stay granted."""
    from app.modules.access_control import service as service_module

    preview = AccessControlService(
        _Ranger([guard, _grant(), _grant(table=None, access="usage")])
    )
    monkeypatch.setattr(service_module, "access_control_service", preview)

    async def deps(_agent):
        return [("database", "NOVA_DEMO"), ("table", "NOVA_DEMO.orders"),
                ("table", "NOVA_DEMO.customers")]

    async def pins(_agent):
        return None

    monkeypatch.setattr(agent_access, "resolve_agent_dependencies", deps)
    monkeypatch.setattr(agent_access, "_semantic_pins", pins)
    items = await agent_access.verify_access(
        agent={"agent_id": "a"}, role_name="marketing", username="alice",
        encrypted_password="x", session_id=None,
    )
    assert [item.granted for item in items] == [True, True, True], [i.detail for i in items]
