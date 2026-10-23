"""Agent access verification.

Given an agent and a role, this resolves the agent's dependencies and inspects
matching Ranger authorization policies, including bootstrap policies.

What is resolved:

* **custom function tools** → ``FUNCTION database.fn``;
* **Semantic Views bound to the agent** → the release pin (or active version)
  and its sources;
* **the agent's database** → ``USAGE`` on that database (needed to run queries).

Only Ranger policy authorization establishes data access.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException

logger = logging.getLogger(__name__)


@dataclass
class AccessItem:
    kind: str
    name: str
    granted: bool
    detail: str = ""


async def _semantic_pins(agent: dict) -> dict[str, dict] | None:
    """Require complete semantic pins from the validated stored release."""
    if not agent.get("release_manifest_id"):
        return None
    from app.modules.agents.releases import load_runtime_manifest
    from app.modules.agents.semantic.access import bound_view_ids

    try:
        manifest = await load_runtime_manifest(agent)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, "Pinned release manifest is invalid") from exc
    dependencies = manifest.get("dependencies") if isinstance(manifest, dict) else None
    entries = dependencies.get("semantic_views") if isinstance(dependencies, dict) else None
    if not isinstance(entries, list):
        raise HTTPException(409, "Pinned release semantic bindings are unavailable")
    pins: dict[str, dict] = {}
    for pin in entries:
        if (
            not isinstance(pin, dict)
            or not isinstance(pin.get("view_id"), str)
            or not pin["view_id"]
            or type(pin.get("version")) is not int
            or pin["version"] < 1
            or not isinstance(pin.get("fingerprint"), str)
            or not pin["fingerprint"]
            or pin["view_id"] in pins
        ):
            raise HTTPException(409, "Pinned release semantic bindings are invalid")
        pins[pin["view_id"]] = pin
    if set(pins) != set(bound_view_ids(agent)):
        raise HTTPException(409, "Semantic bindings have drifted from the pinned release")
    return pins


async def _pinned_semantic_version(view_id: str, pin: dict) -> dict:
    """Validate pinned metadata without adopting the latest definition."""
    from app.modules.agents.semantic.ir import SemanticModelIR
    from app.modules.intelligence.semantic_views import semantic_view_service

    view = await semantic_view_service._get(view_id)
    if not view or view.get("status") != "ACTIVE":
        raise HTTPException(409, "Pinned Semantic View is unavailable")
    version = await semantic_view_service._version(view_id, pin["version"])
    if not version or version.get("status") not in {"ACTIVE", "DEPRECATED"}:
        raise HTTPException(409, "Pinned semantic version is unavailable")
    try:
        ir = SemanticModelIR.from_ossie(version.get("definition") or {})
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(409, "Pinned semantic definition has drifted") from exc
    if (
        not ir.datasets
        or version.get("fingerprint") != pin["fingerprint"]
        or ir.fingerprint != pin["fingerprint"]
    ):
        raise HTTPException(409, "Pinned semantic definition has drifted")
    return version


async def resolve_agent_dependencies(agent: dict) -> list[tuple[str, str]]:
    """The (kind, name) objects an agent depends on, from its configuration.

    ``function`` names are fully qualified; ``table`` names come from each bound
    View's pinned release definition, or active definition for legacy agents.
    Missing legacy Views remain explicit dependencies. Invalid release pins
    fail closed without adopting an active definition.
    """
    deps: list[tuple[str, str]] = []
    owner = agent.get("owner_name")

    from app.modules.agents.repository import agent_repository
    from app.modules.agents.semantic.access import bound_view_ids
    from app.modules.intelligence.semantic_views import semantic_view_service

    pins = await _semantic_pins(agent)
    tools = await agent_repository.list_custom_tools(owner_name=owner or "")
    bound = set(agent.get("default_tools") or [])
    for tool in tools:
        if tool["kind"] != "function" or not tool.get("function_name"):
            continue
        if f"custom:{tool['name']}" in bound or tool["name"] in bound:
            db = tool.get("database_name") or ""
            deps.append(("function", f"{db}.{tool['function_name']}".strip(".")))

    version: dict | None
    for view_id in bound_view_ids(agent):
        deps.append(("semantic_view", view_id))
        if pins is not None:
            version = await _pinned_semantic_version(view_id, pins[view_id])
        else:
            view = await semantic_view_service._get(view_id)
            if not view or view.get("status") != "ACTIVE" or not view.get("active_version"):
                continue
            version = await semantic_view_service._version(view_id, view["active_version"])
            if not version or version.get("status") != "ACTIVE":
                continue
        for dataset in (version.get("definition") or {}).get("datasets") or []:
            source = dataset.get("source")
            if source:
                deps.append(("table", source))

    if agent.get("database_name"):
        deps.append(("database", agent["database_name"]))

    resources = agent.get("resource_bindings") or {}
    deps.extend(("search_index", item["index"]) for item in resources.get("search_indexes", []))
    deps.extend(("feature_group", name) for name in resources.get("feature_groups", []))

    # De-duplicate while preserving order.
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for dep in deps:
        if dep not in seen:
            seen.add(dep)
            unique.append(dep)
    return unique


async def verify_access(
    *,
    agent: dict,
    role_name: str,
    username: str,
    encrypted_password: str,
    session_id: str | None,
    security_context_version: int = 1,
) -> list[AccessItem]:
    """Check every agent dependency against Ranger policy state."""
    from app.modules.access_control.service import access_control_service

    deps = await resolve_agent_dependencies(agent)
    pins = await _semantic_pins(agent)
    items: list[AccessItem] = []
    for kind, name in deps:
        if kind in {"search_index", "feature_group"}:
            from app.modules.agents.resources import authorized_resources

            resources = await authorized_resources(
                agent.get("resource_bindings") or {},
                {
                    "username": username,
                    "encrypted_password": encrypted_password,
                    "active_role": role_name,
                    "session_id": session_id,
                    "security_context_version": security_context_version,
                },
            )
            available = (
                {item["index"] for item in resources["search_indexes"]}
                if kind == "search_index"
                else set(resources["feature_groups"])
            )
            items.append(
                AccessItem(
                    kind=kind,
                    name=name,
                    granted=name in available,
                    detail="Resource is accessible"
                    if name in available
                    else "Resource is unavailable",
                )
            )
            continue
        if kind == "semantic_view":
            from app.modules.intelligence.semantic_views import semantic_view_service

            user = {
                "username": username,
                "encrypted_password": encrypted_password,
                "active_role": role_name,
                "session_id": session_id,
                "security_context_version": security_context_version,
            }
            if pins is not None:
                pin = pins[name]
                try:
                    view = await semantic_view_service.get_version_for_agent(
                        name, pin["version"], pin["fingerprint"], user,
                        agent_id=agent.get("agent_id"),
                    )
                except HTTPException as exc:
                    if exc.status_code != 404:
                        raise
                    view = None
            else:
                view = await semantic_view_service.get_active_for_agent(
                    name, user, agent_id=agent.get("agent_id"),
                )
            items.append(
                AccessItem(
                    kind=kind,
                    name=name,
                    granted=view is not None,
                    detail=(
                        "Published Semantic View is accessible"
                        if view
                        else "Semantic View is unavailable"
                    ),
                )
            )
            continue
        effective = await access_control_service.effective_access(
            principal=username,
            active_role=role_name,
            resource=f"{name}.*" if kind == "database" else name,
        )
        required = {"table": "SELECT", "database": "USAGE", "function": "EXECUTE"}[kind]
        privileges = {str(value).upper() for value in effective.get("object_access", [])}
        granted = required in privileges or "ALL" in privileges
        detail = (
            f"Ranger authorizes {role_name} for {name}."
            if granted
            else f"No Ranger permission authorizes {role_name} for {kind} {name}."
        )
        items.append(AccessItem(kind=kind, name=name, granted=granted, detail=detail))
    return items


async def access_fingerprint(agent: dict) -> str:
    """Bind verification to the agent configuration and resolved dependencies."""
    from app.modules.agents.semantic.access import bound_view_ids
    from app.modules.intelligence.semantic_views import semantic_view_service

    pins = await _semantic_pins(agent)
    versions = []
    version: dict | None
    for view_id in bound_view_ids(agent):
        if pins is not None:
            pin = pins[view_id]
            await _pinned_semantic_version(view_id, pin)
            versions.append((view_id, pin["version"], pin["fingerprint"]))
            continue
        view = await semantic_view_service._get(view_id)
        active_version = view.get("active_version") if view else None
        version = (
            await semantic_view_service._version(view_id, active_version)
            if active_version
            else None
        )
        versions.append((view_id, active_version, version.get("fingerprint") if version else None))
    # Loop limits (``budget_profile``) change cost, not what the agent can read,
    # so they do not invalidate a verified grant.
    configured = {
        key: value
        for key, value in agent.items()
        if key not in {"created_at", "config_revision", "resource_bindings", "budget_profile"}
    }
    resources = agent.get("resource_bindings") or {}
    if resources.get("search_indexes") or resources.get("feature_groups"):
        configured["resource_bindings"] = resources
    payload = {
        "agent": configured,
        "dependencies": await resolve_agent_dependencies(agent),
        "semantic_view_versions": versions,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


async def has_verified_access(agent: dict, *, role_name: str, user: dict) -> bool:
    """A grant and a current successful permission check are both required."""
    from app.modules.agents.repository import AgentMetadataUnavailable, agent_repository

    for attempt in range(3):
        try:
            grants = await agent_repository.list_agent_roles(
                agent["agent_id"], owner_name=agent["owner_name"]
            )
            grant = next((item for item in grants if item["role_name"] == role_name), None)
            if not grant or not grant.get("verified_fingerprint"):
                return False
            if grant["verified_fingerprint"] != await access_fingerprint(agent):
                return False
            items = await verify_access(
                agent=agent,
                role_name=role_name,
                username=user["username"],
                encrypted_password=user.get("encrypted_password", ""),
                session_id=user.get("session_id"),
                security_context_version=int(user.get("security_context_version") or 1),
            )
            return all(item.granted for item in items)
        except Exception:  # noqa: BLE001 - an errored check is unavailable, never a denial
            if attempt == 2:
                logger.warning("Agent access verification unavailable for role %s", role_name)
                raise AgentMetadataUnavailable("Agent access verification is unavailable") from None
            await asyncio.sleep(0.05 * (attempt + 1))
    raise AgentMetadataUnavailable("Agent access verification is unavailable")


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
