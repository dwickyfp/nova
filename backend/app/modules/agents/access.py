"""Agent access verification.

Given an agent and a role, this answers the question "can this role actually use
everything this agent depends on?" It resolves the agent's dependencies and
checks each against the role's grants.

What is resolved:

* **custom function tools** → ``FUNCTION database.fn``;
* **semantic models bound to the agent** → each dataset's physical ``table``;
* **the agent's database** → ``USAGE`` on that database (needed to run queries).

How it is checked: ``SHOW GRANTS TO ROLE <role>`` on the *engine*, parsed into
grant rows, then matched by object. This is the same source of truth the engine
enforces, so the answer reflects reality rather than Nova's own intent. The check
is read-only and runs on the caller's connection.

A role that holds ``ACCOUNTADMIN`` is reported as granted for everything: it is
the super-user role by Nova's own rule, so a per-object check would be
misleading.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

#: Roles that imply full reach regardless of per-object grants.
_SUPERUSER_ROLES = {"ACCOUNTADMIN", "root", "OPERATE", "DB_ADMIN"}


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
        model = await agent_repository.get_semantic_model(
            model_id, owner_name=owner or ""
        )
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
    """Check every agent dependency against ``role_name``'s grants."""
    if role_name.upper() in _SUPERUSER_ROLES:
        deps = await resolve_agent_dependencies(agent)
        return [
            AccessItem(
                kind=kind,
                name=name,
                granted=True,
                detail=f"{role_name} is a super-user role; object grants are implicit.",
            )
            for kind, name in deps
        ]

    grants = await _show_grants(
        username=username,
        encrypted_password=encrypted_password,
        role=role_name,
        session_id=session_id,
    )
    deps = await resolve_agent_dependencies(agent)
    items: list[AccessItem] = []
    for kind, name in deps:
        granted = matches_object(grants, kind, name)
        detail = (
            f"{role_name} has access to {name}."
            if granted
            else f"{role_name} is not granted {kind} {name}."
        )
        items.append(AccessItem(kind=kind, name=name, granted=granted, detail=detail))
    return items


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
