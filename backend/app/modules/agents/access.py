"""Agent access verification.

Given an agent and a role, this resolves the agent's dependencies and inspects
matching Ranger authorization policies, including bootstrap policies.

What is resolved:

* **custom function tools** → ``FUNCTION database.fn``;
* **semantic models bound to the agent** → each dataset's physical ``table``;
* **the agent's database** → ``USAGE`` on that database (needed to run queries).

No native StarRocks object grant is consulted. StarRocks roles are session
markers in full Ranger mode and would be a misleading authorization source.
"""

from __future__ import annotations

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


async def _show_grants(
    *,
    username: str,
    encrypted_password: str,
    role: str | None,
    session_id: str | None,
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
            role=None,
            session_id=session_id,
            max_rows=1000,
        )
        if result.error:
            logger.warning("Verify Access: grants read failed: %s", result.error)
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
    semantic model's datasets. A semantic model the caller cannot read is skipped
    (it is not the caller's), so a foreign model does not fabricate a dependency.
    """
    deps: list[tuple[str, str]] = []
    owner = agent.get("owner_name")

    from app.modules.agents.repository import agent_repository

    tools = await agent_repository.list_custom_tools(owner_name=owner or "")
    bound = set(agent.get("default_tools") or [])
    for tool in tools:
        if tool["kind"] != "function" or not tool.get("function_name"):
            continue
        if f"custom:{tool['name']}" in bound or tool["name"] in bound:
            db = tool.get("database_name") or ""
            deps.append(("function", f"{db}.{tool['function_name']}".strip(".")))

    model_ids = agent.get("semantic_model_ids") or (
        [agent["semantic_model_id"]] if agent.get("semantic_model_id") else []
    )
    for model_id in model_ids:
        model = await agent_repository.get_semantic_model(model_id, owner_name=owner or "")
        if model:
            for dataset in (model.get("definition") or {}).get("datasets") or []:
                source = dataset.get("source")
                if source:
                    deps.append(("table", source))

    if agent.get("database_name"):
        deps.append(("database", agent["database_name"]))

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
) -> list[AccessItem]:
    """Check every agent dependency against Ranger policy state."""
    del encrypted_password, session_id
    from app.modules.access_control.service import access_control_service

    deps = await resolve_agent_dependencies(agent)
    items: list[AccessItem] = []
    for kind, name in deps:
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
    payload = {
        "agent": {key: value for key, value in agent.items() if key not in {"created_at"}},
        "dependencies": await resolve_agent_dependencies(agent),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


async def has_verified_access(agent: dict, *, role_name: str, user: dict) -> bool:
    """A grant and a current successful permission check are both required."""
    from app.modules.agents.repository import agent_repository

    grants = await agent_repository.list_agent_roles(
        agent["agent_id"], owner_name=agent["owner_name"]
    )
    grant = next((item for item in grants if item["role_name"] == role_name), None)
    if not grant or not grant.get("verified_fingerprint"):
        return False
    try:
        if grant["verified_fingerprint"] != await access_fingerprint(agent):
            return False
        items = await verify_access(
            agent=agent,
            role_name=role_name,
            username=user["username"],
            encrypted_password=user.get("encrypted_password", ""),
            session_id=user.get("session_id"),
        )
        return all(item.granted for item in items)
    except Exception:  # noqa: BLE001 - authorization fails closed
        logger.warning("Agent access verification failed for role %s", role_name)
        return False


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
