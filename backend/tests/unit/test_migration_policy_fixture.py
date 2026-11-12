"""The engine denial fixture must limit and restore its own real policy models."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.integrations.ranger.client import ranger_client
from tests.integration.test_migration_l3 import (
    _l3_admin_ranger_policies,
    _limited_migration_access,
)


@pytest.mark.parametrize("allowed", [set(), {"create database"}])
async def test_policy_fixture_restores_original_access_after_failure(monkeypatch, allowed):
    original = {policy.name: policy.to_api() for policy in _l3_admin_ranger_policies()}
    stored = deepcopy(original)
    identifiers = {name: index for index, name in enumerate(original, start=1)}

    async def get(name):
        return {**stored[name], "id": identifiers[name]} if name in stored else None

    async def delete(identifier):
        del stored[next(name for name, value in identifiers.items() if value == identifier)]

    async def put(policy):
        stored[policy.name] = policy.to_api()

    monkeypatch.setattr(ranger_client, "get_policy", AsyncMock(side_effect=get))
    monkeypatch.setattr(ranger_client, "delete_policy", AsyncMock(side_effect=delete))
    monkeypatch.setattr(ranger_client, "put_policy", AsyncMock(side_effect=put))
    with pytest.raises(RuntimeError, match="worker assertion"):
        async with _limited_migration_access(allowed):
            actual = {
                access["type"]
                for policy in stored.values()
                for item in policy["policyItems"]
                for access in item["accesses"]
            }
            assert actual == allowed
            raise RuntimeError("worker assertion")
    assert stored == original
