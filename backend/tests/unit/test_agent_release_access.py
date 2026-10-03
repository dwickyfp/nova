"""Release pins fix semantic dependencies while caller permissions remain live."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.modules.access_control.service import access_control_service
from app.modules.agents import access, releases
from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.intelligence.contracts import fingerprint
from app.modules.intelligence.semantic_views import semantic_view_service
from app.modules.query.service import query_service

USER = {
    "username": "reader",
    "encrypted_password": "sealed",
    "active_role": "analyst",
    "session_id": "session",
    "security_context_version": 7,
}


async def _verify(agent):
    return await access.verify_access(
        agent=agent,
        role_name=USER["active_role"],
        username=USER["username"],
        encrypted_password=USER["encrypted_password"],
        session_id=USER["session_id"],
        security_context_version=USER["security_context_version"],
    )


@pytest.fixture
def release_access(monkeypatch):
    definition = parse_ossie("""
version: 0.1.1
name: sales
datasets:
  - name: orders
    source: sales.orders
    primary_key: [id]
    fields:
      - name: id
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: id
""").as_dict()
    semantic_fingerprint = SemanticModelIR.from_ossie(definition).fingerprint
    agent = {
        "agent_id": "sales-agent",
        "owner_name": "owner",
        "semantic_view_ids": ["sales-view"],
        "database_name": "sales",
        "release_manifest_id": "release",
    }
    dependencies = {
        "models": [],
        "semantic_views": [{
            "view_id": "sales-view", "version": 1, "fingerprint": semantic_fingerprint,
        }],
    }
    manifest = {
        "id": "release", "dependencies": dependencies, "fingerprint": fingerprint(dependencies),
    }
    get_manifest = AsyncMock(return_value=manifest)
    monkeypatch.setattr(releases, "get_manifest", get_manifest)
    view = {
        "id": "sales-view", "name": "sales", "owner_name": "owner", "visibility": "PRIVATE",
        "status": "ACTIVE", "active_version": 1, "database_name": "sales",
    }
    rows = {1: {"status": "ACTIVE", "definition": definition,
                "fingerprint": semantic_fingerprint}}
    get_version = AsyncMock(side_effect=lambda _id, version: rows.get(version))
    monkeypatch.setattr(semantic_view_service, "_get", AsyncMock(return_value=view))
    monkeypatch.setattr(semantic_view_service, "_version", get_version)
    get_active = AsyncMock(wraps=semantic_view_service.get_active_for_agent)
    monkeypatch.setattr(semantic_view_service, "get_active_for_agent", get_active)
    monkeypatch.setattr(agent_repository, "list_custom_tools", AsyncMock(return_value=[]))
    shared_agent = AsyncMock(return_value=agent)
    monkeypatch.setattr(agent_repository, "get_shared_agent", shared_agent)
    grants = AsyncMock(return_value=[])
    monkeypatch.setattr(agent_repository, "list_agent_roles", grants)
    allowed = {"sales.orders", "sales.*"}

    async def effective_access(**kwargs):
        return {"object_access": ["SELECT", "USAGE"] if kwargs["resource"] in allowed else []}

    effective = AsyncMock(side_effect=effective_access)
    monkeypatch.setattr(settings, "RANGER_ENABLED", True)
    monkeypatch.setattr(access_control_service, "effective_access", effective)
    query = AsyncMock(return_value=SimpleNamespace(error=None))
    monkeypatch.setattr(query_service, "execute", query)
    return SimpleNamespace(
        agent=agent, manifest=manifest, get_manifest=get_manifest, view=view, rows=rows,
        get_version=get_version, get_active=get_active, grants=grants, allowed=allowed,
        effective=effective, query=query, shared_agent=shared_agent,
    )


def _publish_new_source(state):
    definition = deepcopy(state.rows[1]["definition"])
    definition["datasets"][0]["source"] = "private.payroll"
    state.rows[2] = {"status": "ACTIVE", "definition": definition,
                     "fingerprint": SemanticModelIR.from_ossie(definition).fingerprint}
    state.rows[1]["status"] = "DEPRECATED"
    state.view["active_version"] = 2


async def test_release_stays_accessible_after_new_unauthorized_active_version(release_access):
    state = release_access
    verified = await access.access_fingerprint(state.agent)
    state.grants.return_value = [{"role_name": "analyst", "verified_fingerprint": verified}]
    assert await access.has_verified_access(state.agent, role_name="analyst", user=USER)

    _publish_new_source(state)

    assert await access.resolve_agent_dependencies(state.agent) == [
        ("semantic_view", "sales-view"), ("table", "sales.orders"), ("database", "sales"),
    ]
    assert await access.access_fingerprint(state.agent) == verified
    assert all(item.granted for item in await _verify(state.agent))
    assert await access.has_verified_access(state.agent, role_name="analyst", user=USER)
    state.get_active.assert_not_awaited()
    assert {call.args[1] for call in state.get_version.await_args_list} == {1}
    assert state.get_manifest.await_args.args == ("sales-agent", "owner")
    assert state.get_manifest.await_args.kwargs == {"manifest_id": "release"}
    assert {call.kwargs["resource"] for call in state.effective.await_args_list} == {
        "sales.orders", "sales.*",
    }
    assert all(call.kwargs["principal"] == "reader" for call in state.effective.await_args_list)
    assert all(call.kwargs["active_role"] == "analyst" for call in state.effective.await_args_list)
    assert all(call.kwargs["username"] == "reader" for call in state.query.await_args_list)
    assert all(call.kwargs["role"] == "analyst" for call in state.query.await_args_list)
    assert all(call.kwargs["session_id"] == "session" for call in state.query.await_args_list)
    assert all(call.kwargs["security_context_version"] == 7
               for call in state.query.await_args_list)
    assert state.shared_agent.await_args.kwargs == {"role_name": "analyst"}


@pytest.mark.parametrize("operation", [access.resolve_agent_dependencies,
                                        access.access_fingerprint, _verify])
@pytest.mark.parametrize("failure", ["missing_manifest", "corrupt_manifest", "malformed_manifest",
                                      "missing_pin_collection", "missing_pins", "unbound_pin",
                                      "duplicate_pin", "missing_version", "invalid_version",
                                      "missing_fingerprint"])
async def test_invalid_manifest_pins_fail_without_adopting_latest(
    release_access, operation, failure,
):
    state = release_access
    pins = state.manifest["dependencies"]["semantic_views"]
    if failure == "missing_manifest":
        state.get_manifest.return_value = None
    elif failure == "corrupt_manifest":
        state.manifest["fingerprint"] = "changed"
    elif failure == "malformed_manifest":
        del state.manifest["dependencies"]["models"]
    elif failure == "missing_pin_collection":
        del state.manifest["dependencies"]["semantic_views"]
    elif failure == "missing_pins":
        pins.clear()
    elif failure == "unbound_pin":
        pins[0]["view_id"] = "other-view"
    elif failure == "duplicate_pin":
        pins.append(dict(pins[0]))
    elif failure == "missing_version":
        del pins[0]["version"]
    elif failure == "invalid_version":
        pins[0]["version"] = True
    elif failure == "missing_fingerprint":
        del pins[0]["fingerprint"]
    if failure != "corrupt_manifest":
        state.manifest["fingerprint"] = fingerprint(state.manifest["dependencies"])

    with pytest.raises(HTTPException) as error:
        await operation(state.agent)

    assert error.value.status_code == 409
    assert "pinned release" in error.value.detail.lower()
    state.get_version.assert_not_awaited()
    state.get_active.assert_not_awaited()
    state.query.assert_not_awaited()


@pytest.mark.parametrize("operation", [access.resolve_agent_dependencies,
                                        access.access_fingerprint, _verify])
@pytest.mark.parametrize("failure", ["missing_version", "draft_version", "fingerprint",
                                      "definition", "inactive_view"])
async def test_unavailable_or_drifted_pinned_definition_fails_closed(
    release_access, operation, failure,
):
    state = release_access
    _publish_new_source(state)
    if failure == "missing_version":
        del state.rows[1]
    elif failure == "draft_version":
        state.rows[1]["status"] = "DRAFT"
    elif failure == "fingerprint":
        state.rows[1]["fingerprint"] = "changed"
    elif failure == "definition":
        state.rows[1]["definition"]["datasets"][0]["source"] = "private.payroll"
    elif failure == "inactive_view":
        state.view["status"] = "DEPRECATED"

    with pytest.raises(HTTPException) as error:
        await operation(state.agent)

    assert error.value.status_code == 409
    assert "pinned" in error.value.detail.lower()
    assert all(call.args[1] == 1 for call in state.get_version.await_args_list)
    state.get_active.assert_not_awaited()
    state.query.assert_not_awaited()


async def test_pinned_permissions_are_rechecked_after_revocation(release_access):
    state = release_access
    _publish_new_source(state)
    verified = await access.access_fingerprint(state.agent)
    state.grants.return_value = [{"role_name": "analyst", "verified_fingerprint": verified}]
    assert await access.has_verified_access(state.agent, role_name="analyst", user=USER)

    state.allowed.remove("sales.orders")

    assert await access.access_fingerprint(state.agent) == verified
    items = await _verify(state.agent)
    assert [(item.kind, item.granted) for item in items] == [
        ("semantic_view", False), ("table", False), ("database", True),
    ]
    assert not await access.has_verified_access(state.agent, role_name="analyst", user=USER)


async def test_pinned_access_denies_engine_revocation_even_when_policy_read_allows(release_access):
    state = release_access
    verified = await access.access_fingerprint(state.agent)
    state.grants.return_value = [{"role_name": "analyst", "verified_fingerprint": verified}]
    assert await access.has_verified_access(state.agent, role_name="analyst", user=USER)

    state.query.return_value.error = "permission denied"

    assert not (await _verify(state.agent))[0].granted
    assert not await access.has_verified_access(state.agent, role_name="analyst", user=USER)


async def test_pinned_access_rechecks_entity_authorization(release_access, monkeypatch):
    state = release_access
    verified = await access.access_fingerprint(state.agent)
    state.grants.return_value = [{"role_name": "analyst", "verified_fingerprint": verified}]
    assert await access.has_verified_access(state.agent, role_name="analyst", user=USER)
    entity_access = AsyncMock(return_value=False)
    monkeypatch.setattr(semantic_view_service, "_entity_access", entity_access)

    assert not await access.has_verified_access(state.agent, role_name="analyst", user=USER)
    assert entity_access.await_args.args[1] == USER


@pytest.mark.parametrize("grant", [None, {"role_name": "analyst", "verified_fingerprint": None},
                                  {"role_name": "analyst", "verified_fingerprint": "stale"},
                                  {"role_name": "other", "verified_fingerprint": "current"}])
async def test_release_requires_current_verified_role_grant(release_access, grant):
    state = release_access
    verified = await access.access_fingerprint(state.agent)
    state.grants.return_value = [{"role_name": "analyst", "verified_fingerprint": verified}]
    assert await access.has_verified_access(state.agent, role_name="analyst", user=USER)

    state.grants.return_value = [grant] if grant else []

    assert not await access.has_verified_access(state.agent, role_name="analyst", user=USER)


async def test_private_view_sharing_revocation_denies_pinned_access(release_access):
    state = release_access
    verified = await access.access_fingerprint(state.agent)
    state.grants.return_value = [{"role_name": "analyst", "verified_fingerprint": verified}]
    assert await access.has_verified_access(state.agent, role_name="analyst", user=USER)
    state.shared_agent.return_value = None

    assert not (await _verify(state.agent))[0].granted
    assert not await access.has_verified_access(state.agent, role_name="analyst", user=USER)


async def test_legacy_agent_still_follows_latest_active_definition(release_access):
    state = release_access
    state.agent.pop("release_manifest_id")
    previous = await access.access_fingerprint(state.agent)
    _publish_new_source(state)

    assert await access.resolve_agent_dependencies(state.agent) == [
        ("semantic_view", "sales-view"), ("table", "private.payroll"), ("database", "sales"),
    ]
    assert await access.access_fingerprint(state.agent) != previous
    assert not (await _verify(state.agent))[0].granted
    state.get_active.assert_awaited_once()
    state.get_manifest.assert_not_awaited()
