"""Immutable runtime dependency manifests; authorization is always evaluated live."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.core.database import db
from app.modules.agents.versions import configuration
from app.modules.assistant.skills import contains_credential_shape
from app.modules.intelligence.contracts import fingerprint
from app.modules.intelligence.engine_repository import metadata_lock

SCORER_SET_VERSION = "nova-agent-scorers:1"
RELEASES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_RELEASE_MANIFESTS (
    manifest_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    version_id VARCHAR(64) NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(manifest_id)
DISTRIBUTED BY HASH(manifest_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

RUNTIME_FIELDS = frozenset(
    {
        "database_name",
        "schema_name",
        "tool_not_accessible",
        "harness_mode",
        "model_provider_id",
        "model_name",
        "instructions_response",
        "instructions_orchestration",
        "response_style",
        "description",
        "default_tools",
        "default_skills",
        "discoverable_skills",
        "compiled_instructions",
        "resource_bindings",
        "semantic_view_ids",
        "policy",
        "budget_seconds",
        "budget_tokens",
        "budget_profile",
    }
)


def safe_content(value: Any) -> Any:
    from app.modules.assistant.tools.redaction import redact_row

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if contains_credential_shape(encoded) or redact_row(["manifest"], [encoded])[0] != encoded:
        raise HTTPException(422, "Release dependencies must be credential-free")
    if len(encoded.encode()) > 524288:
        raise HTTPException(422, "Release manifest exceeds the size limit")
    return json.loads(encoded)


def tool_contracts(registry) -> list[dict]:
    contracts = []
    for name in sorted(registry.names()):
        tool = registry.get(name)
        config = {}
        if hasattr(tool, "tool"):
            config = {
                key: tool.tool.get(key)
                for key in (
                    "tool_id",
                    "name",
                    "description",
                    "kind",
                    "database_name",
                    "function_name",
                    "definition",
                    "input_schema",
                    "source",
                )
                if key in tool.tool
            }
        if hasattr(tool, "server"):
            # Endpoints may contain credentials; inspect their safe observable identity only.
            from urllib.parse import urlsplit

            endpoint = urlsplit(str(tool.server.get("endpoint") or ""))
            config["server"] = {
                "server_id": tool.server.get("server_id"),
                "transport": tool.server.get("transport"),
                "origin": f"{endpoint.scheme}://{endpoint.hostname or ''}:{endpoint.port or ''}",
                "path": endpoint.path,
            }
        source_file = inspect.getsourcefile(type(tool))
        code_revision = fingerprint(Path(source_file).read_text()) if source_file else None
        contracts.append(
            {
                "code_revision": code_revision,
                "name": name,
                "description": str(tool.description),
                "schema": tool.parameters,
                "classification": tool.classification,
                "configuration_digest": fingerprint(safe_content(config)),
                "externally_mutable": hasattr(tool, "server"),
            }
        )
    return safe_content(contracts)


async def model_contract(provider_id: str, name: str) -> dict:
    from app.modules.ai_ml.service import ai_service

    provider = await ai_service.get_provider(provider_id)
    models = await ai_service.list_models(provider_id)
    model = next(
        (
            item
            for item in models
            if item.get("name") == name
            and item.get("is_active")
            and item.get("type") in {"llm", "decision"}
        ),
        None,
    )
    if not provider or not provider.get("is_active") or not model:
        raise HTTPException(409, "A pinned release model is unavailable")
    from urllib.parse import urlsplit

    endpoint = urlsplit(str(provider.get("endpoint") or ""))
    observable = {
        "type": provider.get("type"),
        "origin": f"{endpoint.scheme}://{endpoint.hostname or ''}:{endpoint.port or ''}",
        "path": endpoint.path,
        "provider_parameters": provider.get("default_params") or {},
        "model_parameters": model.get("default_params") or {},
        "context_window": model.get("context_window"),
        "max_tokens": model.get("max_tokens"),
    }
    return {
        "id": model["id"],
        "provider_id": provider_id,
        "name": name,
        "configuration_digest": fingerprint(safe_content(observable)),
        "externally_mutable": True,
    }


async def resource_contracts(agent: dict, user: dict) -> list[dict]:
    from app.modules.agents.resources import validate_resources

    await validate_resources(agent, user)
    refs = []
    bindings = agent.get("resource_bindings") or {}
    if bindings.get("search_indexes"):
        from app.modules.intelligence.search import search_service

        available = {str(item["name"]): item for item in await search_service.list(user)}
        for item in bindings["search_indexes"]:
            identifier = str(item.get("index") if isinstance(item, dict) else item)
            definition = available.get(identifier)
            if not definition:
                raise HTTPException(409, "A release search resource is unavailable")
            safe = {
                key: definition.get(key)
                for key in (
                    "id",
                    "name",
                    "version",
                    "active_version",
                    "source_relation",
                    "key_columns",
                    "content_columns",
                    "filter_columns",
                    "entity_id",
                    "model_alias",
                    "status",
                )
            }
            refs.append(
                {
                    "kind": "search_index",
                    "id": identifier,
                    "version": definition.get("active_version"),
                    "definition_digest": fingerprint(safe_content(safe)),
                }
            )
    if bindings.get("feature_groups"):
        from app.modules.intelligence.feature_store import feature_store

        available = {str(item["name"]): item for item in await feature_store.list_groups(user)}
        for item in bindings["feature_groups"]:
            identifier = str(item.get("index") if isinstance(item, dict) else item)
            definition = available.get(identifier)
            if not definition:
                raise HTTPException(409, "A release feature resource is unavailable")
            safe = {
                key: definition.get(key)
                for key in (
                    "id",
                    "name",
                    "version",
                    "active_version",
                    "source_relation",
                    "key_columns",
                    "feature_columns",
                    "entity_id",
                    "model_alias",
                    "status",
                )
            }
            refs.append(
                {
                    "kind": "feature_group",
                    "id": identifier,
                    "version": definition.get("active_version"),
                    "definition_digest": fingerprint(safe_content(safe)),
                }
            )
    return refs


async def get_manifest(
    agent_id: str, owner: str, *, manifest_id: str | None = None, version_id: str | None = None
) -> dict | None:
    column, value = ("manifest_id", manifest_id) if manifest_id else ("version_id", version_id)
    result = await db.execute_system(
        "SELECT payload FROM NOVA_SYSTEM.CONFIG_AGENT_RELEASE_MANIFESTS "
        f"WHERE agent_id=%s AND owner_name=%s AND {column}=%s LIMIT 2",
        [agent_id, owner, value],
    )
    if len(result["rows"]) > 1:
        raise HTTPException(409, "Release manifest requires reconciliation")
    if not result["rows"]:
        return None
    payload = result["rows"][0][0]
    return json.loads(payload) if isinstance(payload, str) else payload


async def capture_manifest(agent: dict, version_id: str, user: dict) -> dict:
    from app.observability.metrics import studio_operation

    with studio_operation("release", "capture"):
        return await _capture_manifest(agent, version_id, user)


async def _capture_manifest(agent: dict, version_id: str, user: dict) -> dict:
    from app.modules.agents.service import agent_service
    from app.modules.ai_ml.decision_settings import read_decision_settings, registered_model
    from app.modules.assistant.provider import assistant_provider
    from app.modules.intelligence.semantic_views import semantic_view_service

    existing = await get_manifest(agent["agent_id"], agent["owner_name"], version_id=version_id)
    if existing:
        return existing
    from app.modules.agents.instructions import compile_agent_instructions

    agent = {
        **agent,
        "compiled_instructions": compile_agent_instructions(
            response=agent.get("instructions_response", ""),
            orchestration=agent.get("instructions_orchestration", ""),
            description=agent.get("description", ""),
            response_style=agent.get("response_style"),
        ).as_dict(),
    }
    registry, prompt, _, _ = await agent_service.build_loop_inputs(
        {**agent, "release_manifest_id": None}
    )
    config = await assistant_provider.resolve(
        provider_id=agent.get("model_provider_id"), model=agent.get("model_name")
    )
    models = [await model_contract(config.provider_id, config.model)]
    routing = await read_decision_settings()
    if routing.enabled:
        for model_id, kind in (
            (routing.decision_model_id, "decision"),
            (routing.light_model_id, "llm"),
            (routing.heavy_model_id, "llm"),
        ):
            selected, _ = await registered_model(model_id, kind)
            contract = await model_contract(selected["provider_id"], selected["name"])
            if contract not in models:
                models.append(contract)
    semantics = []
    for view_id in agent.get("semantic_view_ids") or []:
        view = await semantic_view_service.get_active_for_agent(
            view_id, user, agent_id=agent["agent_id"]
        )
        if view is None:
            raise HTTPException(409, "A release Semantic View is unavailable")
        semantics.append(
            {"view_id": view_id, "version": view["version"], "fingerprint": view["fingerprint"]}
        )
    dependencies = safe_content(
        {
            "configuration": configuration(agent),
            "semantic_views": semantics,
            "skills": [
                vars(registry.skill_definitions[name])
                for name in sorted(registry.skill_definitions)
            ],
            "default_skills": list(registry.default_skills),
            "discoverable_skills": list(registry.discoverable_skills),
            "tools": tool_contracts(registry),
            "compiled_prompt": prompt,
            "compiled_instructions": agent["compiled_instructions"],
            "models": models,
            "routing": routing.model_dump(),
            "resources": await resource_contracts(agent, user),
            "policy_fingerprint": fingerprint({"agent_policy": agent.get("policy")}),
            "scorer_set_version": SCORER_SET_VERSION,
        }
    )
    manifest_id = fingerprint([agent["agent_id"], version_id, dependencies])
    manifest = {
        "id": manifest_id,
        "agent_id": agent["agent_id"],
        "version_id": version_id,
        "fingerprint": fingerprint(dependencies),
        "dependencies": dependencies,
        "created_at": datetime.now(UTC).isoformat(),
        "limitations": [
            "External model and MCP behavior can change without an immutable vendor revision."
        ],
    }
    async with metadata_lock(f"agent-release:{agent['agent_id']}:{version_id}"):
        existing = await get_manifest(agent["agent_id"], agent["owner_name"], version_id=version_id)
        if existing:
            return existing
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_RELEASE_MANIFESTS "
            "(manifest_id,agent_id,owner_name,version_id,payload,created_at) "
            "VALUES (%s,%s,%s,%s,%s,NOW())",
            [manifest_id, agent["agent_id"], agent["owner_name"], version_id, json.dumps(manifest)],
        )
        return manifest


async def load_runtime_manifest(agent: dict) -> dict | None:
    identifier = agent.get("release_manifest_id")
    if not identifier:
        return None
    manifest = await get_manifest(agent["agent_id"], agent["owner_name"], manifest_id=identifier)
    if not manifest or manifest["fingerprint"] != fingerprint(manifest["dependencies"]):
        raise HTTPException(409, "Pinned release manifest is unavailable or corrupt")
    dependencies = manifest["dependencies"]
    for model in dependencies["models"]:
        current = await model_contract(model["provider_id"], model["name"])
        if current != model:
            raise HTTPException(
                409, "Pinned release model configuration has drifted; evaluate a new draft"
            )
    return manifest


def assert_tool_contracts(manifest: dict, registry) -> None:
    if tool_contracts(registry) != manifest["dependencies"]["tools"]:
        raise HTTPException(
            409, "Pinned release tool dependencies have drifted; evaluate a new draft"
        )
