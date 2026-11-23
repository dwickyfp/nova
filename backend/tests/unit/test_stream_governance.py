"""A stream source must have no row filter or mask that could cover it (fail closed)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.access_control.service import AccessControlService
from app.modules.streams.schemas import StreamError
from app.modules.streams.sources import require_governance_supported


def _mask(database, table, *, excludes=False, recursive=False, policy_type=1, **extra):
    return {
        "service": settings.RANGER_SERVICE_NAME,
        "isEnabled": True,
        "policyType": policy_type,
        "resources": {
            "catalog": {"values": ["default_catalog"]},
            "database": {"values": [database]},
            "table": {"values": [table], "isExcludes": excludes, "isRecursive": recursive},
            "column": {"values": ["email"]},
        },
        "dataMaskPolicyItems": [{"roles": ["analyst"], "accesses": [{"type": "select"}],
                                 "dataMaskInfo": {"dataMaskType": "MASK"}}],
        **extra,
    }


@pytest.fixture
def ranger(monkeypatch):
    monkeypatch.setattr(settings, "RANGER_PASSWORD", "secret")
    from app.integrations.ranger.client import ranger_client

    state = {"policies": [], "tag": [], "service": {"name": settings.RANGER_SERVICE_NAME}}

    async def list_policies(*, page_size=100, service_name=None):
        return state["tag"] if service_name else state["policies"]

    async def get_service(_name):
        return state["service"]

    monkeypatch.setattr(ranger_client, "list_policies", list_policies)
    monkeypatch.setattr(ranger_client, "get_service", get_service)
    return state


async def _refused(state, policies) -> bool:
    state["policies"] = policies
    try:
        await require_governance_supported("sales", "orders")
    except StreamError as exc:
        assert exc.code == "STREAM_GOVERNANCE_UNSUPPORTED"
        return True
    return False


# The seven audit probes: each mask covers sales.orders and must be detected.
@pytest.mark.parametrize("database,table,excludes", [
    ("sales", "orders", False),
    ("sales", "*", False),
    ("sales", "ord*", False),
    ("sal*", "orders", False),
    ("sales", "ORDERS", False),
    ("sales", "other", True),
    ("sales", "ord?rs", False),
])
async def test_audit_probe_masks_are_detected(ranger, database, table, excludes):
    assert await _refused(ranger, [_mask(database, table, excludes=excludes)])


@pytest.mark.parametrize("policy", [
    _mask("sales", "orders", policy_type=2),
    _mask("SALES", "Orders"),
    _mask("sales", "o[rx]ders"),
    _mask("sales", "nothing", recursive=True),
    _mask("sales", "ord*", excludes=True),
    _mask("sales", "orders", conditions=[{"type": "ip-range", "values": ["10.0.0.0/8"]}]),
    _mask("sales", "orders", validitySchedules=[{"startTime": "2026/01/01 00:00:00"}]),
    _mask("sales", "orders", policyPriority=1),
    {**_mask("sales", "orders"), "resources": {"database": {"values": ["sales"]}}},
    {**_mask("sales", "orders"), "resources": {"url": {"values": ["s3://x"]}}},
    {**_mask("sales", "orders"), "resources": {}},
    {**_mask("sales", "x"), "resources": {
        **_mask("sales", "x")["resources"], "table": {"values": []}}},
])
async def test_anything_not_evaluated_exactly_counts_as_covering(ranger, policy):
    assert await _refused(ranger, [policy])


@pytest.mark.parametrize("policy", [
    _mask("hr", "salaries"),
    _mask("hr*", "orders"),
    _mask("sales", "ord?"),
    _mask("sales", "orders", excludes=True),
    _mask("sales", "ORDERS", excludes=True),
    {**_mask("sales", "orders"), "isEnabled": False},
    _mask("sales", "orders", policy_type=0),
])
async def test_policies_that_cannot_cover_the_source_are_ignored(ranger, policy):
    assert not await _refused(ranger, [policy])


async def test_no_ranger_is_refused(monkeypatch, ranger):
    monkeypatch.setattr(settings, "RANGER_PASSWORD", "")
    with pytest.raises(StreamError, match="need Ranger") as exc:
        await require_governance_supported("sales", "orders")
    assert exc.value.code == "STREAM_GOVERNANCE_UNSUPPORTED"


async def test_unreadable_policies_are_refused(monkeypatch, ranger):
    from app.integrations.ranger.client import ranger_client

    monkeypatch.setattr(ranger_client, "list_policies", AsyncMock(side_effect=RuntimeError()))
    with pytest.raises(StreamError, match="could not be read"):
        await require_governance_supported("sales", "orders")


async def test_missing_service_is_refused(ranger):
    ranger["service"] = None
    with pytest.raises(StreamError, match="could not be read"):
        await require_governance_supported("sales", "orders")


async def test_tag_based_masks_are_refused(ranger):
    ranger["service"] = {"name": settings.RANGER_SERVICE_NAME, "tagService": "tags"}
    ranger["tag"] = [
        {"isEnabled": True, "policyType": 1, "resources": {"tag": {"values": ["PII"]}}},
    ]
    with pytest.raises(StreamError, match="Tag-based"):
        await require_governance_supported("sales", "orders")


async def test_tag_service_without_masks_is_accepted(ranger):
    ranger["service"] = {"name": settings.RANGER_SERVICE_NAME, "tagService": "tags"}
    ranger["tag"] = [
        {"isEnabled": True, "policyType": 0, "resources": {"tag": {"values": ["PII"]}}},
        {"isEnabled": False, "policyType": 1, "resources": {"tag": {"values": ["PII"]}}},
    ]
    await require_governance_supported("sales", "orders")


def test_resource_may_cover_is_exact_for_plain_names():
    policy = _mask("sales", "orders")
    assert AccessControlService.resource_may_cover(policy, "default_catalog", "sales", "orders")
    assert not AccessControlService.resource_may_cover(
        policy, "default_catalog", "sales", "orders2"
    )
    assert not AccessControlService.resource_may_cover(policy, "other_catalog", "sales", "orders")
