"""Agent access verification.

Given an agent and a role, this resolves the agent's dependencies and inspects
matching Ranger authorization policies, including bootstrap policies.

What is resolved:

* **custom function tools** → ``FUNCTION database.fn``;
* **Semantic Views bound to the agent** → the active version and its sources;
* **the agent's database** → ``USAGE`` on that database (needed to run queries).

In Ranger mode only policy authorization establishes data access. Explicit
native-RBAC mode probes resources with the caller and checks native function
USAGE grants without invoking functions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


@dataclass
class AccessItem:
    kind: str
    name: str
    granted: bool
    detail: str = ""


def _grant_rows(rows: list[list]) -> list[str]:
    """Flatten SHOW GRANTS rows into lowercase text for matching."""
    flat: list[str] = []
    for row in rows:
        flat.append(" ".join(str(cell) for cell in row if cell is not None))
    return flat


def matches_object(grants: list[str], kind: str, name: str) -> bool:
    """True when any grant line covers ``kind name``.

    The engine's grant text varies by build, so matching is deliberately loose:
    a line counts if it carries the privilege family for the object kind and the
    object name (case-insensitively), or a wildcard that includes it.
    """
    needle = name.lower()
    for line in grants:
        text = line.lower()
        if needle not in text and "*.*" not in text and "all" not in text:
            continue
        if kind == "function" and "function" not in text and "*.*" not in text:
            continue
        if kind == "table" and "table" not in text and "*.*" not in text and "select" not in text:
            continue
        if kind == "database" and "database" not in text and "*.*" not in text:
            continue
        # The name appears, or a wildcard covers it.
        if needle in text or "*.*" in text:
            return True
    return False


def _native_function_usage(grants: list[str], name: str) -> bool:
    """Admit only explicit USAGE grants for a matching native function scope."""
    database, separator, function = name.lower().partition(".")
    if not separator:
        return False
    for line in grants:
        text = line.replace("`", "").strip().lower()
        matched = re.fullmatch(
            (
                "grant (usage|all(?: privileges)?) on (?:function|functions) "
                "([a-z0-9_$.]+)(?:\\([^)]*\\))? to role [a-z0-9_$]+"
            ),
            text,
        )
        if matched and matched[2] == name.lower():
            return True
        matched = re.fullmatch(
            (
                "grant (usage|all(?: privileges)?) on all functions in database "
                "([a-z0-9_$]+) to role [a-z0-9_$]+"
            ),
            text,
        )
        if matched and matched[2] == database:
            return True
    return False


async def _show_grants(
    *,
    username: str,
    encrypted_password: str,
    role: str | None,
    session_id: str | None,
    security_context_version: int = 1,
) -> list[str]:
    """Read the role's grants from the engine, on the caller's connection."""
    from app.modules.query.service import query_service

    sql = f"SHOW GRANTS TO ROLE `{_safe_role(role)}`" if role else "SHOW GRANTS"
    try:
        result = await query_service.execute(
            sql=sql,
            username=username,
            encrypted_password=encrypted_password,
            database=None,
            role=role,
            session_id=session_id,
            security_context_version=security_context_version,
            max_rows=1000,
        )
        if result.error:
            logger.warning("Verify Access: grants read failed")
            return []
        return _grant_rows(result.rows)
    except Exception:  # noqa: BLE001 - a failed read is reported as unknown
        logger.warning("Verify Access: could not read grants for role %s", role)
        return []


_ROLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _safe_role(role: str) -> str:
    """Reject anything that is not a plain identifier before it enters SQL."""
    if not _ROLE_RE.match(role):
        raise ValueError(f"unsafe role name: {role!r}")
    return role


async def resolve_agent_dependencies(agent: dict) -> list[tuple[str, str]]:
    """The (kind, name) objects an agent depends on, from its configuration.

    ``function`` names are fully qualified; ``table`` names come from each bound
    View's active definition. Missing or unpublished Views remain explicit
    dependencies, so access verification fails closed.
    """
    deps: list[tuple[str, str]] = []
    owner = agent.get("owner_name")

    from app.modules.agents.repository import agent_repository
    from app.modules.agents.semantic.access import bound_view_ids
    from app.modules.intelligence.semantic_views import semantic_view_service

    tools = await agent_repository.list_custom_tools(owner_name=owner or "")
    bound = set(agent.get("default_tools") or [])
    for tool in tools:
        if tool["kind"] != "function" or not tool.get("function_name"):
            continue
        if f"custom:{tool['name']}" in bound or tool["name"] in bound:
            db = tool.get("database_name") or ""
            deps.append(("function", f"{db}.{tool['function_name']}".strip(".")))

    for view_id in bound_view_ids(agent):
        deps.append(("semantic_view", view_id))
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

            view = await semantic_view_service.get_active_for_agent(
                name,
                {
                    "username": username,
                    "encrypted_password": encrypted_password,
                    "active_role": role_name,
                    "session_id": session_id,
                    "security_context_version": security_context_version,
                },
                agent_id=agent.get("agent_id"),
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
        from app.core.config import settings

        if not settings.RANGER_ENABLED:
            from app.modules.query.service import query_service

            parts = name.split(".")
            if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in parts):
                items.append(
                    AccessItem(kind=kind, name=name, granted=False, detail="Invalid resource name")
                )
                continue
            quoted = ".".join(f"`{part}`" for part in parts)
            if kind == "function":
                grants = await _show_grants(
                    username=username,
                    encrypted_password=encrypted_password,
                    role=role_name,
                    session_id=session_id,
                    security_context_version=security_context_version,
                )
                granted = _native_function_usage(grants, name)
            else:
                result = await query_service.execute(
                    sql=f"SELECT * FROM {quoted} LIMIT 1" if kind == "table" else "SELECT 1",
                    username=username,
                    encrypted_password=encrypted_password,
                    database=parts[-2] if kind == "table" else name,
                    role=role_name,
                    session_id=session_id,
                    security_context_version=security_context_version,
                    max_rows=1,
                )
                granted = result.error is None
            items.append(
                AccessItem(
                    kind=kind,
                    name=name,
                    granted=granted,
                    detail="Native-RBAC permission check passed"
                    if granted
                    else "Native-RBAC permission denied",
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

    versions = []
    for view_id in bound_view_ids(agent):
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
