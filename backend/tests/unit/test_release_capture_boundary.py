import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import releases
from app.modules.agents.service import agent_service
from app.modules.assistant.provider import assistant_provider
from app.modules.assistant.tools import ToolRegistry


@pytest.mark.parametrize("concurrent_manifest", [None, {"id": "first-publisher"}])
async def test_dependency_capture_precedes_transaction_and_first_manifest_wins(
    monkeypatch, concurrent_manifest,
):
    locked = False
    agent = {
        "agent_id": "agent", "owner_name": "alice", "name": "Revenue", "default_tools": [],
    }
    registry = ToolRegistry()
    registry.skill_definitions = {}
    registry.default_skills = []
    registry.discoverable_skills = []

    async def build_inputs(candidate):
        assert not locked
        assert candidate["release_manifest_id"] is None
        return registry, "Compiled prompt", [], []

    async def model_contract(provider_id, name):
        assert not locked
        return {"provider_id": provider_id, "name": name}

    async def resources(candidate, user):
        assert not locked
        return []

    async def ontology_pin():
        assert not locked
        return {"release_id": "ont_rel_0001", "manifest_hash": "a" * 64}

    store = AsyncMock()
    store.get_version.return_value = {
        "agent_id": "agent", "owner_name": "alice",
        "configuration": json.dumps(releases.configuration(agent)),
    }

    async def lock(agent_id):
        nonlocal locked
        assert agent_id == "agent"
        locked = True

    @asynccontextmanager
    async def transaction(owner):
        nonlocal locked
        assert owner == "alice"
        try:
            yield store
        finally:
            locked = False

    async def persist(manifest_id, agent_id, version_id, payload):
        assert locked
        assert agent_id == "agent" and version_id == "draft" and payload["id"] == manifest_id

    read = AsyncMock(side_effect=[None, concurrent_manifest])
    write = AsyncMock(side_effect=persist)
    store.lock_agent.side_effect = lock
    store.store_manifest = write
    monkeypatch.setattr(releases, "get_manifest", read)
    monkeypatch.setattr(releases, "agent_metadata", transaction)
    monkeypatch.setattr(releases, "model_contract", model_contract)
    monkeypatch.setattr(releases, "resource_contracts", resources)
    monkeypatch.setattr(releases, "ontology_release_pin", ontology_pin)
    monkeypatch.setattr(agent_service, "build_loop_inputs", build_inputs)
    monkeypatch.setattr(assistant_provider, "resolve", AsyncMock(return_value=SimpleNamespace(
        provider_id="provider", model="model",
    )))

    result = await releases.capture_manifest(agent, "draft", {"username": "alice"})
    assert read.await_count == 2
    assert not locked
    if concurrent_manifest:
        assert result == concurrent_manifest
        write.assert_not_awaited()
    else:
        write.assert_awaited_once()
        assert result["version_id"] == "draft"
        assert result["dependencies"]["ontology_release"]["release_id"] == "ont_rel_0001"
        assert result["fingerprint"] == releases.fingerprint(result["dependencies"])


def test_manifest_content_is_normalized_before_persistence():
    content = {"schema": {"enum": ("allow", "deny")}}
    canonical = releases.safe_content(content)
    assert canonical["schema"]["enum"] == ["allow", "deny"]
    assert releases.fingerprint(canonical) == releases.fingerprint(content)
