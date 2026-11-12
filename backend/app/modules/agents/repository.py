"""Agent registry API projections backed by actor-bound PostgreSQL transactions.

Configuration changes and immutable version snapshots share one transaction.
Shared reads retain the caller's identity; a resource owner's name selects a
namespace and never supplies execution credentials. Legacy source DDL and
backfill helpers below are still being reconciled with the one-shot importer;
normal application startup does not run them.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.database import db
from app.core.metadata_actor import current_actor
from app.core.metadata_db import MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext
from app.modules.agents.metadata_repository import agent_metadata


class AgentMetadataUnavailable(RuntimeError):
    """A scoped StarRocks metadata read could not be trusted."""


async def _read_rows(
    sql: str,
    params: list[Any],
    valid_row: Callable[[list[Any] | tuple[Any, ...]], bool],
) -> list[list[Any]]:
    saw_bad_read = False
    empty_reads = 0
    for attempt in range(5):
        try:
            result = await db.execute_system(sql, params)
            rows = result.get("rows")
        except Exception:  # noqa: BLE001 - a later read may recover from a transient FE error
            rows = None
        if isinstance(rows, list) and rows and all(
            isinstance(row, list | tuple) and valid_row(row) for row in rows
        ):
            return rows
        if rows == []:
            empty_reads += 1
            if empty_reads >= 2 and not saw_bad_read:
                return []
        else:
            saw_bad_read = True
            empty_reads = 0
        if attempt < 4:
            await asyncio.sleep(0.05 * (attempt + 1))
    raise AgentMetadataUnavailable("Agent metadata is temporarily unavailable")

#: One row per agent. Only configuration; the behavioral contract is assembled
#: by ``service``/``prompt`` from these fields at run time.
AGENTS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENTS (
    agent_id                   VARCHAR(64) NOT NULL,
    owner_name                 VARCHAR(128) NOT NULL,
    database_name              VARCHAR(128),
    schema_name                VARCHAR(128),
    name                       VARCHAR(128) NOT NULL,
    description                TEXT,
    avatar                     VARCHAR(256),
    color                      VARCHAR(32),
    model_provider_id          VARCHAR(64),
    model_name                 VARCHAR(128),
    instructions_response      TEXT,
    instructions_orchestration TEXT,
    response_style             VARCHAR(64),
    sample_questions           JSON,
    budget_seconds             INT,
    budget_tokens              INT,
    tool_not_accessible        VARCHAR(16),
    default_tools              JSON,
    default_skills             JSON,
    discoverable_skills        JSON,
    compiled_instructions      JSON,
    harness_mode               VARCHAR(16),
    policy                     VARCHAR(32),
    semantic_model_id          VARCHAR(64),
    semantic_model_ids         JSON,
    semantic_view_ids          JSON,
    visibility                 VARCHAR(16),
    created_at                 DATETIME NOT NULL,
    updated_at                 DATETIME NOT NULL,
    resource_bindings          JSON,
    config_revision            VARCHAR(64),
    budget_profile             VARCHAR(16),
    release_manifest_id        VARCHAR(64),
    harness_policy             JSON
) PRIMARY KEY(agent_id)
DISTRIBUTED BY HASH(agent_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

#: Historical bindings remain readable for the one-time View migration.
#: New agent writes use ``semantic_view_ids`` only.
AGENTS_SEMANTIC_IDS_DDL = "ALTER TABLE NOVA_SYSTEM.CONFIG_AGENTS ADD COLUMN semantic_model_ids JSON"
AGENTS_SEMANTIC_VIEW_IDS_DDL = (
    "ALTER TABLE NOVA_SYSTEM.CONFIG_AGENTS ADD COLUMN semantic_view_ids JSON"
)

AGENT_INTELLIGENCE_COLUMNS = (
    ("resource_bindings", "JSON"),
    ("config_revision", "VARCHAR(64)"),
    ("discoverable_skills", "JSON"),
    ("compiled_instructions", "JSON"),
    ("harness_mode", "VARCHAR(16)"),
    ("budget_profile", "VARCHAR(16)"),
    ("release_manifest_id", "VARCHAR(64)"),
    ("harness_policy", "JSON"),
)

#: One row per user-defined skill (SKILL.md compatible). ``body`` is the whole
#: playbook; it is screened for credential shapes before insert.
AGENT_SKILLS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_SKILLS (
    skill_id    VARCHAR(64) NOT NULL,
    owner_name  VARCHAR(128) NOT NULL,
    name        VARCHAR(128) NOT NULL,
    description VARCHAR(1024),
    body        TEXT,
    scope       VARCHAR(16),
    created_at  DATETIME NOT NULL,
    updated_at  DATETIME NOT NULL
) PRIMARY KEY(skill_id)
DISTRIBUTED BY HASH(skill_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

SKILL_BODY_PREFIX = "nova-skill-body:v1:"
SKILL_BODY_CHUNKS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_SKILL_BODY_CHUNKS (
    skill_id VARCHAR(64) NOT NULL,
    revision VARCHAR(64) NOT NULL,
    part_index INT NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    content VARCHAR(65533) NOT NULL
) PRIMARY KEY(skill_id, revision, part_index)
DISTRIBUTED BY HASH(skill_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

#: Columns returned for an agent, in a stable order.
_AGENT_COLUMNS = (
    "agent_id, owner_name, database_name, schema_name, name, description, "
    "avatar, color, model_provider_id, model_name, instructions_response, "
    "instructions_orchestration, response_style, sample_questions, "
    "budget_seconds, budget_tokens, tool_not_accessible, default_tools, "
    "default_skills, discoverable_skills, compiled_instructions, harness_mode, "
    "policy, semantic_model_id, semantic_model_ids, semantic_view_ids, visibility, "
    "created_at, updated_at, resource_bindings, config_revision, "
    "budget_profile, release_manifest_id, harness_policy"
)
_AGENT_COLUMN_COUNT = len(_AGENT_COLUMNS.split(", "))


def _full_row(row: list[Any] | tuple[Any, ...]) -> bool:
    return len(row) in {_AGENT_COLUMN_COUNT, _AGENT_COLUMN_COUNT - 1}

_SKILL_COLUMNS = "skill_id, owner_name, name, description, body, scope, created_at, updated_at"

_MCP_COLUMNS = (
    "server_id, owner_name, name, description, transport, endpoint, command, "
    "args, is_active, last_status, created_at, updated_at"
)

_TOOL_COLUMNS = (
    "tool_id, owner_name, name, description, source, input_schema, is_enabled, "
    "created_at, updated_at"
)


#: One row per registered MCP server. A server is a connection descriptor: a
#: name, a transport (``stdio`` | ``http`` | ``sse``), an endpoint/command, and
#: optional headers/args. Credentials (if any) are handled by the operator's
#: environment, never stored here.
MCP_SERVERS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_MCP_SERVERS (
    server_id   VARCHAR(64) NOT NULL,
    owner_name  VARCHAR(128) NOT NULL,
    name        VARCHAR(128) NOT NULL,
    description TEXT,
    transport   VARCHAR(16) NOT NULL,
    endpoint    VARCHAR(1024),
    command     VARCHAR(1024),
    args        JSON,
    is_active   BOOLEAN NOT NULL DEFAULT "true",
    last_status VARCHAR(32),
    created_at  DATETIME NOT NULL,
    updated_at  DATETIME NOT NULL
) PRIMARY KEY(server_id)
DISTRIBUTED BY HASH(server_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

#: One row per tool exposed by a registry source. ``source`` is ``builtin`` for
#: Nova's own tools, or ``mcp:<server_id>`` for a tool discovered from an MCP
#: server. ``input_schema`` is the JSON Schema the model is given.
TOOLS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_TOOLS (
    tool_id      VARCHAR(64) NOT NULL,
    owner_name   VARCHAR(128) NOT NULL,
    name         VARCHAR(128) NOT NULL,
    description  TEXT,
    source       VARCHAR(160) NOT NULL,
    input_schema JSON,
    is_enabled   BOOLEAN NOT NULL DEFAULT "true",
    created_at   DATETIME NOT NULL,
    updated_at   DATETIME NOT NULL
) PRIMARY KEY(tool_id)
DISTRIBUTED BY HASH(tool_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


#: Access control: which StarRocks roles may use an agent. ``grant`` records
#: whether the role got USAGE or also OWNERSHIP, mirroring StarRocks' own
#: privilege names so Verify Access can compare like for like.
AGENT_ROLES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_ROLES (
    agent_id    VARCHAR(64) NOT NULL,
    role_name   VARCHAR(128) NOT NULL,
    owner_name  VARCHAR(128) NOT NULL,
    grant_type  VARCHAR(32) NOT NULL,
    verified_fingerprint VARCHAR(64),
    verified_at DATETIME,
    created_at  DATETIME NOT NULL,
    updated_at  DATETIME NOT NULL
) PRIMARY KEY(agent_id, role_name)
DISTRIBUTED BY HASH(agent_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

#: Custom tools defined in Nova. Two kinds:
#:   * ``function`` — a StarRocks FUNCTION/UDF, called as ``SELECT fn(args)``.
#:   * ``procedure`` — a Nova-side SQL procedure: an ordered list of statements
#:     with named parameters, since StarRocks has no callable stored procedure.
#: ``definition`` holds the kind-specific body (SQL text, parameter list).
CUSTOM_TOOLS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_CUSTOM_TOOLS (
    tool_id     VARCHAR(64) NOT NULL,
    owner_name  VARCHAR(128) NOT NULL,
    name        VARCHAR(128) NOT NULL,
    description TEXT,
    kind        VARCHAR(16) NOT NULL,
    database_name VARCHAR(128),
    function_name VARCHAR(256),
    definition  JSON,
    created_at  DATETIME NOT NULL,
    updated_at  DATETIME NOT NULL
) PRIMARY KEY(tool_id)
DISTRIBUTED BY HASH(tool_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: object) -> datetime:
    """Coerce a stored DATETIME to a naive-UTC ``datetime`` for the view layer."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value[:19], fmt)
            except ValueError:
                continue
    return _now()


def _as_json(value: object) -> Any:
    """Parse a JSON column that the driver may return as a str or a native value."""
    if value is None:
        return None
    if isinstance(value, list | dict):
        return value
    if isinstance(value, bytes | bytearray):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _semantic_ids(scalar: str | None, stored: Any) -> list[str]:
    """The agent's bound semantic models as a list.

    ``semantic_model_ids`` is the source of truth; older rows have only the
    scalar ``semantic_model_id``. This reconciles the two so a reader always
    gets a list: the stored list if present, else the scalar as a one-item list.
    """
    parsed = _as_json(stored)
    if isinstance(parsed, list) and parsed:
        return [str(x) for x in parsed if x]
    return [scalar] if scalar else []


def _view_ids_from_fields(fields: dict) -> list[str]:
    """Canonical binding; old field names are accepted only at the API edge."""
    value = fields.get("semantic_view_ids")
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item) for item in value if item))


def _view_ids(stored: Any, legacy_ids: list[str]) -> list[str]:
    """A null new column marks a row that predates the View migration."""
    parsed = _as_json(stored)
    if isinstance(parsed, list):
        return list(dict.fromkeys(str(item) for item in parsed if item))
    return legacy_ids


def _agent_row(row: list[Any]) -> dict:
    # Compatibility for tests and rolling upgrades reading the pre-intelligence
    # 25-column shape. New columns sit after ``default_skills``.
    if len(row) == 25:
        row = [*row[:19], [], {}, "auto", *row[19:]]
    if len(row) == 28:
        row = [*row[:25], None, *row[25:]]
    if len(row) == 29:
        row = [*row, None, None]
    # ``budget_profile`` is the newest column; rows read before it exist have 31.
    harness_policy = row[33] if len(row) > 33 else None
    release_manifest_id = row[32] if len(row) > 32 else None
    budget_profile = row[31] if len(row) > 31 else None
    row = list(row[:31])
    (
        agent_id,
        owner,
        database_name,
        schema_name,
        name,
        description,
        avatar,
        color,
        provider_id,
        model_name,
        instructions_response,
        instructions_orchestration,
        response_style,
        sample_questions,
        budget_seconds,
        budget_tokens,
        tool_not_accessible,
        default_tools,
        default_skills,
        discoverable_skills,
        compiled_instructions,
        harness_mode,
        policy,
        semantic_model_id,
        semantic_model_ids,
        semantic_view_ids,
        visibility,
        created_at,
        updated_at,
        resource_bindings,
        config_revision,
    ) = row
    legacy_ids = _semantic_ids(semantic_model_id, semantic_model_ids)
    return {
        "agent_id": agent_id,
        "owner_name": owner,
        "database_name": database_name,
        "schema_name": schema_name,
        "name": name,
        "description": description or "",
        "avatar": avatar,
        "color": color,
        "model_provider_id": provider_id,
        "model_name": model_name,
        "instructions_response": instructions_response or "",
        "instructions_orchestration": instructions_orchestration or "",
        "response_style": response_style,
        "sample_questions": _as_json(sample_questions) or [],
        "budget_seconds": budget_seconds,
        "budget_tokens": budget_tokens,
        "tool_not_accessible": tool_not_accessible or "accept",
        "default_tools": _as_json(default_tools) or [],
        "default_skills": _as_json(default_skills) or [],
        "discoverable_skills": _as_json(discoverable_skills) or [],
        "compiled_instructions": _as_json(compiled_instructions) or {},
        "harness_mode": harness_mode or "auto",
        "budget_profile": budget_profile or "analyst",
        "policy": policy or "auto_read_only",
        "semantic_model_id": semantic_model_id,
        "semantic_model_ids": legacy_ids,
        "semantic_view_ids": _view_ids(semantic_view_ids, legacy_ids),
        "visibility": visibility or "private",
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
        "resource_bindings": _as_json(resource_bindings) or {},
        "config_revision": config_revision,
        "release_manifest_id": release_manifest_id,
        "harness_policy": _as_json(harness_policy) or {},
    }


def _usage_actor(
    owner_name: str, active_role: str | None, security_context_version: int | None,
) -> SecurityContext:
    """The caller that owns a semantic usage row; incomplete identity fails closed."""
    if not active_role or security_context_version is None:
        raise ValueError("Semantic usage requires the caller's role and security version")
    actor = current_actor()
    if (actor.principal != owner_name or actor.active_role != active_role
            or actor.security_context_version != int(security_context_version)):
        raise MetadataPermissionDenied()
    return actor



def _usage_row(row: dict[str, Any]) -> dict:
    return {
        "owner_name": row["owner_name"],
        "active_role": row["active_role"],
        "security_context_version": row["security_context_version"],
        "semantic_model_id": row["semantic_model_id"],
        "model_fingerprint": row["model_fingerprint"],
        "semantic_version": row["semantic_version"],
        "metrics": row["metric_names"],
        "dimensions": row["dimension_names"],
        "filter_shape": row["filter_shape"],
        "time_grain": row["time_grain"],
        "succeeded": row["succeeded"],
        "execution_latency_ms": row["execution_latency_ms"],
    }


def _skill_row(row: list[Any]) -> dict:
    (skill_id, owner, name, description, body, scope, created_at, updated_at) = row
    return {
        "skill_id": skill_id,
        "owner_name": owner,
        "name": name,
        "description": description or "",
        "body": body or "",
        "scope": scope or "user",
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
    }


def _mcp_row(row: list[Any]) -> dict:
    (
        server_id,
        owner,
        name,
        description,
        transport,
        endpoint,
        command,
        args,
        is_active,
        last_status,
        created_at,
        updated_at,
    ) = row
    return {
        "server_id": server_id,
        "owner_name": owner,
        "name": name,
        "description": description or "",
        "transport": transport or "http",
        "endpoint": endpoint,
        "command": command,
        "args": _as_json(args) or [],
        "is_active": bool(is_active),
        "last_status": last_status,
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
    }


def _tool_row(row: list[Any]) -> dict:
    (
        tool_id,
        owner,
        name,
        description,
        source,
        input_schema,
        is_enabled,
        created_at,
        updated_at,
    ) = row
    return {
        "tool_id": tool_id,
        "owner_name": owner,
        "name": name,
        "description": description or "",
        "source": source,
        "input_schema": _as_json(input_schema) or {},
        "is_enabled": bool(is_enabled),
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
    }


def _agent_record(row: dict[str, Any]) -> dict:
    return _agent_row([row.get(key.strip()) for key in _AGENT_COLUMNS.split(",")])


def _agent_changes(fields: dict) -> dict:
    ignored = {"agent_id", "owner_name", "created_at", "updated_at", "config_revision",
               "semantic_model_id", "semantic_model_ids"}
    allowed = {key.strip() for key in _AGENT_COLUMNS.split(",")} - ignored
    values = {key: value for key, value in fields.items() if key in allowed}
    if "semantic_view_ids" in values:
        values["semantic_view_ids"] = _view_ids_from_fields(fields)
    return values


def _custom_tool_record(row: dict[str, Any]) -> dict:
    return _custom_tool_row([row.get(key) for key in (
        "tool_id", "owner_name", "name", "description", "kind", "database_name",
        "function_name", "definition", "created_at", "updated_at",
    )])


class AgentRepository:
    """CRUD for agents, semantic models, skills, MCP servers, and tools.

    Every method is owner-scoped: a row is only returned or mutated when its
    ``owner_name`` matches the caller, so a foreign id is indistinguishable from
    a missing one.
    """

    async def ensure_schema(self) -> None:
        """The one-shot PostgreSQL migrator owns the schema."""

    async def backfill_semantic_view_ids(self) -> int:
        """Persist legacy agent bindings after Semantic View IDs have been imported.

        Legacy model UUIDs are retained as View UUIDs, including review drafts.
        An explicit JSON ``[]`` is a revoked binding and must never be replaced.
        The guarded update also protects a concurrent agent edit after the scan.
        """
        legacy = await db.execute_system(
            "SELECT agent_id, semantic_model_id, semantic_model_ids "
            "FROM NOVA_SYSTEM.CONFIG_AGENTS "
            "WHERE semantic_view_ids IS NULL ORDER BY agent_id"
        )
        changed = 0
        for agent_id, scalar_id, stored_ids in legacy["rows"]:
            ids = _view_ids_from_fields(
                {"semantic_view_ids": _semantic_ids(scalar_id, stored_ids)}
            )
            result = await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENTS SET semantic_view_ids = %s "
                "WHERE agent_id = %s AND semantic_view_ids IS NULL",
                [_dump(ids), agent_id],
            )
            changed += int(result.get("affected", 0))
        return changed

    # ── Agents ─────────────────────────────────────────────────

    async def migrate_legacy_skill_authors(self) -> None:
        from app.common.audit import write_audit_log
        from app.modules.agents.skill_author import SKILL_AUTHOR_ID, is_legacy_skill_author

        result = await db.execute_system(
            f"SELECT {_AGENT_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENTS "
            "WHERE name = %s AND description = %s",
            ["Nova Studio", "Helps you write reusable skills."],
        )
        for raw in result["rows"]:
            agent = _agent_row(raw)
            if not is_legacy_skill_author(agent):
                continue
            old_id, owner = agent["agent_id"], agent["owner_name"]
            provenance = await db.execute_system(
                "SELECT query_id FROM NOVA_SYSTEM.AUDIT_LOG WHERE event_type = 'AGENT' "
                "AND action = 'CREATE' AND status = 'SUCCESS' "
                "AND object_name = %s AND user_name = %s LIMIT 1",
                [old_id, owner],
            )
            if not provenance["rows"]:
                continue
            for table in ("CONFIG_ASSISTANT_THREADS", "CONFIG_ASSISTANT_MESSAGES"):
                await db.execute_system(
                    f"UPDATE NOVA_SYSTEM.{table} SET agent_id = %s "
                    "WHERE agent_id = %s AND user_name = %s",
                    [SKILL_AUTHOR_ID, old_id, owner],
                )
            await write_audit_log(
                event_type="AGENT",
                user_name=owner,
                action="MIGRATE",
                object_type="AGENT",
                object_name=old_id,
                status="SUCCESS",
            )
            await self.delete_agent(old_id, owner_name=owner)

    async def create_agent(self, *, owner_name: str, fields: dict) -> dict:
        values = {
            "description": "", "instructions_response": "", "instructions_orchestration": "",
            "sample_questions": [], "default_tools": [], "default_skills": [],
            "discoverable_skills": [], "compiled_instructions": {}, "resource_bindings": {},
            "harness_policy": {}, "tool_not_accessible": "accept", "harness_mode": "auto",
            "policy": "auto_read_only", "visibility": "private", "budget_profile": "analyst",
            **_agent_changes(fields),
            "semantic_model_id": None, "semantic_model_ids": None,
            "semantic_view_ids": _view_ids_from_fields(fields),
        }
        async with agent_metadata(owner_name) as store:
            row = await store.create_agent(values, config_revision=str(uuid4()))
        return _agent_record(row)

    async def list_agents(
        self, *, owner_name: str, database_name: str | None = None, search: str | None = None,
    ) -> list[dict]:
        async with agent_metadata(read_only=True) as store:
            rows = await store.list_agents()
        rows = [row for row in rows if row["owner_name"] == owner_name
                and (not database_name or row["database_name"] == database_name)
                and (not search or search.casefold() in row["name"].casefold())]
        return [_agent_record(row) for row in sorted(
            rows, key=lambda row: (_iso(row["updated_at"]), row["agent_id"]), reverse=True)]

    async def get_agent(self, agent_id: str, *, owner_name: str) -> dict | None:
        async with agent_metadata(read_only=True) as store:
            row = await store.get_agent(agent_id)
        return _agent_record(row) if row and row["owner_name"] == owner_name else None

    async def get_shared_agent(self, agent_id: str, *, role_name: str) -> dict | None:
        async with agent_metadata(read_only=True) as store:
            if role_name != store.actor.active_role:
                raise MetadataPermissionDenied()
            row = await store.get_agent(agent_id)
            if not row or row["visibility"] != "shared":
                return None
            grants = await store.list_agent_roles(agent_id)
        return _agent_record(row) if any(r["role_name"] == role_name for r in grants) else None

    async def list_shared_agents(self, *, role_name: str) -> list[dict]:
        async with agent_metadata(read_only=True) as store:
            if role_name != store.actor.active_role:
                raise MetadataPermissionDenied()
            rows = await store.list_agents()
            shared = []
            for row in rows:
                if row["visibility"] == "shared" and any(
                    grant["role_name"] == role_name
                    for grant in await store.list_agent_roles(row["agent_id"])
                ):
                    shared.append(row)
        return [_agent_record(row) for row in sorted(
            shared, key=lambda row: (_iso(row["updated_at"]), row["agent_id"]), reverse=True)]

    async def update_agent(self, agent_id: str, *, owner_name: str, fields: dict,
                           expected_revision: str | None = None,
                           check_revision: bool = False) -> dict | None:
        from fastapi import HTTPException

        from app.modules.agents.releases import RUNTIME_FIELDS
        from app.modules.agents.versions import agent_versions, revision_id

        changes = _agent_changes(fields)
        async with agent_metadata(owner_name) as store:
            row = await store.get_agent(agent_id)
            if row is None or row["owner_name"] != owner_name:
                return None
            prior = _agent_record(row)
            if check_revision and prior.get("config_revision") != expected_revision:
                raise HTTPException(409, "Agent changed. Reload and compare before publishing.")
            if not changes:
                return prior
            if (prior.get("release_manifest_id") and RUNTIME_FIELDS & changes.keys()
                    and not changes.get("release_manifest_id")):
                raise HTTPException(409, "Runtime changes require an evaluated draft publication")
            await agent_versions.store(
                prior, label="Previous configuration", version_id=revision_id(prior))
            next_revision = str(uuid4())
            await agent_versions.store(
                {**prior, **changes}, label="Saved configuration", version_id=next_revision)
            updated = await store.update_agent(
                agent_id, changes, expected_config_revision=prior.get("config_revision"),
                config_revision=next_revision)
        return _agent_record(updated)

    async def delete_agent(self, agent_id: str, *, owner_name: str) -> bool:
        async with agent_metadata(owner_name) as store:
            return await store.delete_agent(agent_id)

    # ── Semantic usage ─────────────────────────────────────────
    # Stored in the Intelligence metadata store, written and read as the bound
    # caller: owner, active role and security context version are the actor's.

    async def list_semantic_usage(
        self, *, owner_name: str, active_role: str, security_context_version: int,
        semantic_model_id: str, model_fingerprint: str, semantic_version: int,
        limit: int = 2000,
    ) -> list[dict]:
        from app.modules.intelligence.metadata_repository import intelligence_metadata

        actor = _usage_actor(owner_name, active_role, security_context_version)
        async with intelligence_metadata(actor, read_only=True) as repo:
            rows = await repo.semantic_usage(
                semantic_model_id, model_fingerprint, semantic_version, limit=limit,
            )
        return [_usage_row(row) for row in rows]

    async def record_semantic_usage(
        self,
        *,
        owner_name: str,
        semantic_model_id: str,
        model_fingerprint: str,
        metrics: list[str],
        dimensions: list[str],
        filter_shape: list[dict],
        time_grain: str | None,
        execution_latency_ms: int | None,
        succeeded: bool,
        verified_query_id: str | None = None,
        active_role: str | None = None,
        security_context_version: int | None = None,
        semantic_version: int | None = None,
    ) -> None:
        """Persist redacted workload shape for feedback and MV recommendations."""
        from app.modules.intelligence.metadata_repository import intelligence_metadata

        actor = _usage_actor(owner_name, active_role, security_context_version)
        async with intelligence_metadata(actor) as repo:
            await repo.record_semantic_usage(
                {
                    "usage_id": str(uuid4()),
                    "semantic_model_id": semantic_model_id,
                    "model_fingerprint": model_fingerprint,
                    "metrics": list(metrics),
                    "dimensions": list(dimensions),
                    "filter_shape": list(filter_shape),
                    "time_grain": time_grain,
                    "execution_latency_ms": execution_latency_ms,
                    "succeeded": succeeded,
                    "semantic_version": semantic_version,
                },
                verified_query_id=verified_query_id,
            )

    # ── User skills ────────────────────────────────────────────

    async def _store_skill_body(self, skill_id: str, owner_name: str, body: str) -> str:
        if len(body.encode("utf-8")) <= 60000 and not body.startswith(SKILL_BODY_PREFIX):
            return body
        revision = str(uuid4())
        # 16,000 Unicode characters fit in a 65,533-byte column, including emoji.
        parts = [body[offset : offset + 16000] for offset in range(0, len(body), 16000)]
        for offset in range(0, len(parts), 8):
            batch = parts[offset : offset + 8]
            params = []
            for index, content in enumerate(batch, start=offset):
                params.extend([skill_id, owner_name, revision, index, content])
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CONFIG_SKILL_BODY_CHUNKS "
                "(skill_id, owner_name, revision, part_index, content) VALUES "
                + ", ".join(["(%s, %s, %s, %s, %s)"] * len(batch)),
                params,
            )
        # Publish only after all parts exist, so readers never see a partial revision.
        return f"{SKILL_BODY_PREFIX}{revision}:{len(parts)}"

    async def _load_skill_body(self, skill: dict) -> dict:
        body = skill["body"]
        if not body.startswith(SKILL_BODY_PREFIX):
            return skill
        revision, count = body[len(SKILL_BODY_PREFIX) :].split(":")
        result = await db.execute_system(
            "SELECT part_index, content FROM NOVA_SYSTEM.CONFIG_SKILL_BODY_CHUNKS "
            "WHERE skill_id = %s AND owner_name = %s AND revision = %s ORDER BY part_index",
            [skill["skill_id"], skill["owner_name"], revision],
        )
        rows = result["rows"]
        if len(rows) != int(count) or any(row[0] != index for index, row in enumerate(rows)):
            raise ValueError("The skill document could not be loaded completely.")
        return {**skill, "body": "".join(row[1] for row in rows)}

    async def create_skill(self, *, owner_name: str, fields: dict) -> dict:
        async with agent_metadata(owner_name) as store:
            row = await store.create_skill(fields)
        return _skill_row([row.get(key.strip()) for key in _SKILL_COLUMNS.split(",")])

    async def list_skills(self, *, owner_name: str) -> list[dict]:
        async with agent_metadata(read_only=True) as store:
            rows = await store.list_skills(owner_name)
        return [_skill_row([row.get(key.strip()) for key in _SKILL_COLUMNS.split(",")])
                for row in rows]

    async def get_skill(self, skill_id: str, *, owner_name: str) -> dict | None:
        async with agent_metadata(read_only=True) as store:
            row = await store.get_skill(skill_id)
        return (_skill_row([row.get(key.strip()) for key in _SKILL_COLUMNS.split(",")])
                if row and row["owner_name"] == owner_name else None)

    async def delete_skill(self, skill_id: str, *, owner_name: str) -> bool:
        async with agent_metadata(owner_name) as store:
            return await store.delete_skill(skill_id)

    async def update_skill(self, skill_id: str, *, owner_name: str, fields: dict) -> dict | None:
        async with agent_metadata(owner_name) as store:
            prior = await store.get_skill(skill_id)
            if not prior or prior["owner_name"] != owner_name:
                return None
            row = await store.update_skill(skill_id, {
                "name": fields["name"], "description": fields["description"],
                "body": fields["body"], "scope": "user",
            }, expected_revision=prior["metadata_revision"])
        return _skill_row([row.get(key.strip()) for key in _SKILL_COLUMNS.split(",")])

    # ── MCP servers ────────────────────────────────────────────

    async def create_mcp_server(self, *, owner_name: str, fields: dict) -> dict:
        async with agent_metadata(owner_name) as store:
            row = await store.create_mcp_server({
                "description": "", "transport": "http", "args": [], "is_active": True, **fields,
            }, owner_name=owner_name)
        return _mcp_row([row.get(key.strip()) for key in _MCP_COLUMNS.split(",")])

    async def list_mcp_servers(self, *, owner_name: str) -> list[dict]:
        async with agent_metadata(read_only=True) as store:
            rows = await store.list_mcp_servers(owner_name)
        return [_mcp_row([row.get(key.strip()) for key in _MCP_COLUMNS.split(",")])
                for row in rows if row["owner_name"] == owner_name]

    async def get_mcp_server(self, server_id: str, *, owner_name: str) -> dict | None:
        async with agent_metadata(read_only=True) as store:
            row = await store.get_mcp_server(server_id)
        return (_mcp_row([row.get(key.strip()) for key in _MCP_COLUMNS.split(",")])
                if row and row["owner_name"] == owner_name else None)

    async def update_mcp_server(
        self, server_id: str, *, owner_name: str, fields: dict,
    ) -> dict | None:
        async with agent_metadata(owner_name) as store:
            prior = await store.get_mcp_server(server_id)
            if not prior or prior["owner_name"] != owner_name:
                return None
            changes = {key: fields[key] for key in (
                "name", "description", "transport", "endpoint", "command", "args", "is_active",
            ) if key in fields}
            if changes:
                await store.update_mcp_server(
                    server_id,
                    changes,
                    expected_revision=prior["metadata_revision"],
                    owner_name=owner_name,
                )
            if "last_status" in fields:
                await store.set_mcp_status(server_id, fields["last_status"], owner_name=owner_name)
            row = await store.get_mcp_server(server_id)
            assert row is not None
        return _mcp_row([row.get(key.strip()) for key in _MCP_COLUMNS.split(",")])

    async def delete_mcp_server(self, server_id: str, *, owner_name: str) -> bool:
        async with agent_metadata(owner_name) as store:
            return await store.delete_mcp_server(server_id, owner_name=owner_name)

    # ── Tools ──────────────────────────────────────────────────

    async def upsert_tool(self, *, owner_name: str, fields: dict) -> dict:
        async with agent_metadata(owner_name) as store:
            row = await store.upsert_tool(fields, owner_name=owner_name)
        return _tool_row([row.get(key.strip()) for key in _TOOL_COLUMNS.split(",")])

    async def list_tools(self, *, owner_name: str) -> list[dict]:
        async with agent_metadata(read_only=True) as store:
            rows = await store.list_tools(owner_name)
        return [_tool_row([row.get(key.strip()) for key in _TOOL_COLUMNS.split(",")])
                for row in rows if row["owner_name"] == owner_name]

    async def get_tool(self, tool_id: str, *, owner_name: str) -> dict | None:
        async with agent_metadata(read_only=True) as store:
            row = await store.get_tool(tool_id)
        return (_tool_row([row.get(key.strip()) for key in _TOOL_COLUMNS.split(",")])
                if row and row["owner_name"] == owner_name else None)

    async def set_tool_enabled(
        self, tool_id: str, *, owner_name: str, enabled: bool
    ) -> dict | None:
        async with agent_metadata(owner_name) as store:
            row = await store.set_tool_enabled(tool_id, enabled, owner_name=owner_name)
        return (_tool_row([row.get(key.strip()) for key in _TOOL_COLUMNS.split(",")])
                if row else None)

    # ── Agent access roles ─────────────────────────────────────

    async def list_agent_roles(self, agent_id: str, *, owner_name: str) -> list[dict]:
        async with agent_metadata(read_only=True) as store:
            rows = await store.list_agent_roles(agent_id)
        return [{"role_name": row["role_name"], "grant_type": row["grant_type"] or "USAGE",
                 "verified_fingerprint": row["verified_fingerprint"],
                 "verified_at": _iso(row["verified_at"]) if row["verified_at"] else None}
                for row in rows if row["owner_name"] == owner_name]

    async def set_agent_role_verification(
        self, agent_id: str, *, owner_name: str, role_name: str, fingerprint: str | None,
    ) -> None:
        async with agent_metadata(owner_name) as store:
            grants = await store.list_agent_roles(agent_id)
            grant = next((row for row in grants if row["role_name"] == role_name
                          and row["owner_name"] == owner_name), None)
            if grant:
                await store.set_role_verification(
                    agent_id, role_name, fingerprint, expected_revision=grant["metadata_revision"])

    async def add_agent_role(
        self, agent_id: str, *, owner_name: str, role_name: str, grant_type: str = "USAGE",
    ) -> None:
        async with agent_metadata(owner_name) as store:
            await store.add_agent_role(agent_id, role_name, grant_type)

    async def remove_agent_role(self, agent_id: str, *, owner_name: str, role_name: str) -> bool:
        async with agent_metadata(owner_name) as store:
            return await store.remove_agent_role(agent_id, role_name)

    # ── Custom tools ───────────────────────────────────────────

    async def create_custom_tool(self, *, owner_name: str, fields: dict) -> dict:
        async with agent_metadata(owner_name) as store:
            row = await store.create_custom_tool({"description": "", "definition": {}, **fields})
        return _custom_tool_record(row)

    async def list_custom_tools(self, *, owner_name: str) -> list[dict]:
        async with agent_metadata(read_only=True) as store:
            rows = await store.list_custom_tools(owner_name)
        return [_custom_tool_record(row) for row in rows]

    async def get_custom_tool(self, tool_id: str, *, owner_name: str) -> dict | None:
        async with agent_metadata(read_only=True) as store:
            row = await store.get_custom_tool(tool_id)
        return _custom_tool_record(row) if row and row["owner_name"] == owner_name else None

    async def update_custom_tool(
        self, tool_id: str, *, owner_name: str, fields: dict,
    ) -> dict | None:
        changes = {key: fields[key] for key in (
            "name", "description", "kind", "database_name", "function_name", "definition",
        ) if key in fields}
        async with agent_metadata(owner_name) as store:
            row = await store.get_custom_tool(tool_id)
            if not row or row["owner_name"] != owner_name:
                return None
            if changes:
                row = await store.update_custom_tool(
                    tool_id, changes, expected_revision=row["metadata_revision"])
        return _custom_tool_record(row)

    async def delete_custom_tool(self, tool_id: str, *, owner_name: str) -> bool:
        async with agent_metadata(owner_name) as store:
            return await store.delete_custom_tool(tool_id)


def _custom_tool_row(row: list[Any]) -> dict:
    (
        tool_id,
        owner,
        name,
        description,
        kind,
        database_name,
        function_name,
        definition,
        created_at,
        updated_at,
    ) = row
    return {
        "tool_id": tool_id,
        "owner_name": owner,
        "name": name,
        "description": description or "",
        "kind": kind,
        "database_name": database_name,
        "function_name": function_name,
        "definition": _as_json(definition) or {},
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
    }


def _dump(value: Any) -> str:
    """Serialise a JSON column value. StarRocks accepts a JSON string literal."""
    return json.dumps(value, separators=(",", ":"))


#: Process-wide repository, imported by the service and router.
agent_repository = AgentRepository()
