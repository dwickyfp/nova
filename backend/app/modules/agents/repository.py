"""Agent Studio persistence — NOVA_SYSTEM.CONFIG_AGENTS / _SEMANTIC_MODELS / _AGENT_SKILLS.

Every table is a Primary Key table (``AGENTS.md`` §7): agent configuration and
semantic definitions are low-volume and need UPDATE/DELETE.

The credential rule is structural, not conventional. None of these tables has a
column that can hold a statement, a result set, a password, a token, or an API
key. The closest thing is a skill ``body`` (a Markdown playbook) and a semantic
``definition`` (parsed Ossie metadata); both are screened with
``contains_credential_shape`` before insert and fail closed.

Rows are always scoped to their owner in SQL, so one user's agent can never be
read or mutated by another. An unknown or foreign id answers "not found" from the
caller's point of view; the router turns that into a 404, never a 403, so
existence does not leak.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.database import db

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
    visibility                 VARCHAR(16),
    created_at                 DATETIME NOT NULL,
    updated_at                 DATETIME NOT NULL
) PRIMARY KEY(agent_id)
DISTRIBUTED BY HASH(agent_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

#: Additive migration: an agent predates multi-model support and has only
#: ``semantic_model_id``. The list column holds all bound models; the scalar is
#: kept in sync with the first entry for a reader that still expects one.
AGENTS_SEMANTIC_IDS_DDL = "ALTER TABLE NOVA_SYSTEM.CONFIG_AGENTS ADD COLUMN semantic_model_ids JSON"

AGENT_INTELLIGENCE_COLUMNS = (
    ("discoverable_skills", "JSON"),
    ("compiled_instructions", "JSON"),
    ("harness_mode", "VARCHAR(16)"),
)

#: One row per semantic model. ``definition`` is the *parsed* Ossie metadata
#: (datasets/fields/metrics/relationships), never raw credential-bearing text.
SEMANTIC_MODELS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_SEMANTIC_MODELS (
    semantic_model_id VARCHAR(64) NOT NULL,
    owner_name        VARCHAR(128) NOT NULL,
    name              VARCHAR(128) NOT NULL,
    description       TEXT,
    database_name     VARCHAR(128),
    schema_name       VARCHAR(128),
    ossie_version     VARCHAR(32) NOT NULL,
    definition        JSON,
    source_file_id    VARCHAR(64),
    created_at        DATETIME NOT NULL,
    updated_at        DATETIME NOT NULL
) PRIMARY KEY(semantic_model_id)
DISTRIBUTED BY HASH(semantic_model_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

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
    "policy, semantic_model_id, semantic_model_ids, visibility, "
    "created_at, updated_at"
)

_SEMANTIC_COLUMNS = (
    "semantic_model_id, owner_name, name, description, database_name, "
    "schema_name, ossie_version, definition, source_file_id, created_at, updated_at"
)

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

VERIFIED_QUERIES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_SEMANTIC_VERIFIED_QUERIES (
    verified_query_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    semantic_model_id VARCHAR(64) NOT NULL,
    model_fingerprint VARCHAR(64) NOT NULL,
    question TEXT NOT NULL,
    semantic_plan JSON NOT NULL,
    verified_sql TEXT NOT NULL,
    expected_result_signature VARCHAR(256),
    verified_by VARCHAR(128) NOT NULL,
    verified_at DATETIME NOT NULL,
    tags JSON,
    usage_count BIGINT NOT NULL DEFAULT "0",
    success_count BIGINT NOT NULL DEFAULT "0"
) PRIMARY KEY(verified_query_id)
DISTRIBUTED BY HASH(verified_query_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

SEMANTIC_USAGE_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_SEMANTIC_QUERY_USAGE (
    usage_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    semantic_model_id VARCHAR(64) NOT NULL,
    model_fingerprint VARCHAR(64) NOT NULL,
    metric_names JSON,
    dimension_names JSON,
    filter_shape JSON,
    time_grain VARCHAR(32),
    execution_latency_ms BIGINT,
    scan_bytes BIGINT,
    succeeded BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(usage_id)
DISTRIBUTED BY HASH(usage_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: object) -> datetime:
    """Coerce a stored DATETIME to a naive-UTC ``datetime`` for the view layer."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
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


def _semantic_ids_from_fields(fields: dict) -> list[str]:
    """The list of bound model ids from a create/update payload.

    Accepts either ``semantic_model_ids`` (a list, preferred) or the legacy
    scalar ``semantic_model_id``. Returns a de-duplicated list preserving order.
    """
    value = fields.get("semantic_model_ids")
    ids: list[str] = []
    if isinstance(value, list):
        ids = [str(x) for x in value if x]
    elif fields.get("semantic_model_id"):
        ids = [str(fields["semantic_model_id"])]
    seen: set[str] = set()
    unique: list[str] = []
    for item in ids:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _first_semantic_id(fields: dict) -> str | None:
    """The scalar model id kept in sync with the list's first entry."""
    ids = _semantic_ids_from_fields(fields)
    return ids[0] if ids else None


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


def _agent_row(row: list[Any]) -> dict:
    # Compatibility for tests and rolling upgrades reading the pre-intelligence
    # 25-column shape. New columns sit after ``default_skills``.
    if len(row) == 25:
        row = [*row[:19], [], {}, "auto", *row[19:]]
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
        visibility,
        created_at,
        updated_at,
    ) = row
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
        "policy": policy or "auto_read_only",
        "semantic_model_id": semantic_model_id,
        "semantic_model_ids": _semantic_ids(semantic_model_id, semantic_model_ids),
        "visibility": visibility or "private",
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
    }


def _semantic_row(row: list[Any]) -> dict:
    (
        model_id,
        owner,
        name,
        description,
        database_name,
        schema_name,
        ossie_version,
        definition,
        source_file_id,
        created_at,
        updated_at,
    ) = row
    return {
        "semantic_model_id": model_id,
        "owner_name": owner,
        "name": name,
        "description": description or "",
        "database_name": database_name,
        "schema_name": schema_name,
        "ossie_version": ossie_version,
        "definition": _as_json(definition) or {},
        "source_file_id": source_file_id,
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
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


class AgentRepository:
    """CRUD for agents, semantic models, skills, MCP servers, and tools.

    Every method is owner-scoped: a row is only returned or mutated when its
    ``owner_name`` matches the caller, so a foreign id is indistinguishable from
    a missing one.
    """

    async def ensure_schema(self) -> None:
        await db.execute_system(AGENTS_DDL)
        # Additive: multi-semantic-model support on a pre-existing agents table.
        try:
            await db.execute_system(AGENTS_SEMANTIC_IDS_DDL)
        except Exception as exc:  # noqa: BLE001 - "already exists" is benign
            message = str(exc).lower()
            if "already exists" not in message and "duplicate" not in message:
                raise
        for column, column_type in AGENT_INTELLIGENCE_COLUMNS:
            try:
                await db.execute_system(
                    f"ALTER TABLE NOVA_SYSTEM.CONFIG_AGENTS ADD COLUMN {column} {column_type}"
                )
            except Exception as exc:  # noqa: BLE001 - duplicate column is benign
                message = str(exc).lower()
                if "already exists" not in message and "duplicate" not in message:
                    raise
        await db.execute_system(SEMANTIC_MODELS_DDL)
        await db.execute_system(AGENT_SKILLS_DDL)
        await db.execute_system(SKILL_BODY_CHUNKS_DDL)
        await db.execute_system(MCP_SERVERS_DDL)
        await db.execute_system(TOOLS_DDL)
        await db.execute_system(AGENT_ROLES_DDL)
        await db.execute_system(CUSTOM_TOOLS_DDL)
        await db.execute_system(VERIFIED_QUERIES_DDL)
        await db.execute_system(SEMANTIC_USAGE_DDL)

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
        agent_id = str(uuid4())
        now = _now()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENTS ("
            "agent_id, owner_name, database_name, schema_name, name, description, "
            "avatar, color, model_provider_id, model_name, instructions_response, "
            "instructions_orchestration, response_style, sample_questions, "
            "budget_seconds, budget_tokens, tool_not_accessible, default_tools, "
            "default_skills, discoverable_skills, compiled_instructions, harness_mode, "
            "policy, semantic_model_id, semantic_model_ids, "
            "visibility, created_at, updated_at"
            ") VALUES (" + ", ".join(["%s"] * 28) + ")",
            [
                agent_id,
                owner_name,
                fields.get("database_name"),
                fields.get("schema_name"),
                fields["name"],
                fields.get("description", ""),
                fields.get("avatar"),
                fields.get("color"),
                fields.get("model_provider_id"),
                fields.get("model_name"),
                fields.get("instructions_response", ""),
                fields.get("instructions_orchestration", ""),
                fields.get("response_style"),
                _dump(fields.get("sample_questions") or []),
                fields.get("budget_seconds"),
                fields.get("budget_tokens"),
                fields.get("tool_not_accessible", "accept"),
                _dump(fields.get("default_tools") or []),
                _dump(fields.get("default_skills") or []),
                _dump(fields.get("discoverable_skills") or []),
                _dump(fields.get("compiled_instructions") or {}),
                fields.get("harness_mode", "auto"),
                fields.get("policy", "auto_read_only"),
                _first_semantic_id(fields),
                _dump(_semantic_ids_from_fields(fields)),
                fields.get("visibility", "private"),
                now,
                now,
            ],
        )
        created = await self.get_agent(agent_id, owner_name=owner_name)
        assert created is not None  # just inserted
        return created

    async def list_agents(
        self,
        *,
        owner_name: str,
        database_name: str | None = None,
        search: str | None = None,
    ) -> list[dict]:
        clauses = ["owner_name = %s"]
        params: list[Any] = [owner_name]
        if database_name:
            clauses.append("database_name = %s")
            params.append(database_name)
        if search:
            clauses.append("name LIKE %s")
            params.append(f"%{search}%")
        where = " AND ".join(clauses)
        for _ in range(3):
            result = await db.execute_system(
                f"SELECT {_AGENT_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENTS "
                f"WHERE {where} ORDER BY updated_at DESC",
                params,
            )
            if result["rows"] and all(len(row) == 28 for row in result["rows"]):
                break
        if any(len(row) != 28 for row in result["rows"]):
            raise RuntimeError("Agent metadata query returned an invalid result")
        return [_agent_row(row) for row in result["rows"]]

    async def get_agent(self, agent_id: str, *, owner_name: str) -> dict | None:
        for _ in range(3):
            result = await db.execute_system(
                f"SELECT {_AGENT_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENTS "
                "WHERE agent_id = %s AND owner_name = %s",
                [agent_id, owner_name],
            )
            if result["rows"] and len(result["rows"][0]) == 28:
                break
        if not result["rows"]:
            return None
        if len(result["rows"][0]) != 28:
            raise RuntimeError("Agent metadata query returned an invalid result")
        return _agent_row(result["rows"][0])

    async def get_shared_agent(self, agent_id: str, *, role_name: str) -> dict | None:
        result = await db.execute_system(
            f"SELECT {_AGENT_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENTS "
            "WHERE agent_id = %s AND visibility = 'shared'",
            [agent_id],
        )
        if not result["rows"]:
            return None
        if len(result["rows"][0]) != 28:
            raise RuntimeError("Agent metadata query returned an invalid result")
        agent = _agent_row(result["rows"][0])
        grants = await self.list_agent_roles(agent_id, owner_name=agent["owner_name"])
        if not any(grant["role_name"] == role_name for grant in grants):
            return None
        return agent

    async def list_shared_agents(self, *, role_name: str) -> list[dict]:
        grants = await db.execute_system(
            "SELECT agent_id FROM NOVA_SYSTEM.CONFIG_AGENT_ROLES WHERE role_name = %s",
            [role_name],
        )
        agent_ids = [str(row[0]) for row in grants["rows"]]
        if not agent_ids:
            return []
        placeholders = ", ".join(["%s"] * len(agent_ids))
        result = await db.execute_system(
            f"SELECT {_AGENT_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENTS "
            f"WHERE visibility = 'shared' AND agent_id IN ({placeholders}) "
            "ORDER BY updated_at DESC",
            agent_ids,
        )
        if any(len(row) != 28 for row in result["rows"]):
            raise RuntimeError("Agent metadata query returned an invalid result")
        return [_agent_row(row) for row in result["rows"]]

    async def update_agent(self, agent_id: str, *, owner_name: str, fields: dict) -> dict | None:
        """Patch the provided fields only; unknown keys are ignored."""
        assignments: list[str] = []
        params: list[Any] = []
        for column in (
            "database_name",
            "schema_name",
            "name",
            "description",
            "avatar",
            "color",
            "model_provider_id",
            "model_name",
            "instructions_response",
            "instructions_orchestration",
            "response_style",
            "budget_seconds",
            "budget_tokens",
            "tool_not_accessible",
            "harness_mode",
            "policy",
            "visibility",
        ):
            if column in fields:
                assignments.append(f"{column} = %s")
                params.append(fields[column])
        # Semantic models: a list is the source of truth. When the payload
        # carries either form, write both the list and the scalar-first entry so
        # a reader that still expects one model stays correct.
        if "semantic_model_ids" in fields or "semantic_model_id" in fields:
            ids = _semantic_ids_from_fields(fields)
            assignments.append("semantic_model_ids = %s")
            params.append(_dump(ids))
            assignments.append("semantic_model_id = %s")
            params.append(ids[0] if ids else None)
        for column in (
            "sample_questions",
            "default_tools",
            "default_skills",
            "discoverable_skills",
            "compiled_instructions",
        ):
            if column in fields:
                assignments.append(f"{column} = %s")
                params.append(_dump(fields[column] or []))
        if assignments:
            assignments.append("updated_at = %s")
            params.append(_now())
            params.extend([agent_id, owner_name])
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENTS SET "
                + ", ".join(assignments)
                + " WHERE agent_id = %s AND owner_name = %s",
                params,
            )
        return await self.get_agent(agent_id, owner_name=owner_name)

    async def delete_agent(self, agent_id: str, *, owner_name: str) -> bool:
        result = await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_AGENTS WHERE agent_id = %s AND owner_name = %s",
            [agent_id, owner_name],
        )
        return bool(result.get("affected", 0))

    # ── Semantic models ────────────────────────────────────────

    async def create_semantic_model(self, *, owner_name: str, fields: dict) -> dict:
        model_id = str(uuid4())
        now = _now()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_SEMANTIC_MODELS ("
            "semantic_model_id, owner_name, name, description, database_name, "
            "schema_name, ossie_version, definition, source_file_id, created_at, updated_at"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                model_id,
                owner_name,
                fields["name"],
                fields.get("description", ""),
                fields.get("database_name"),
                fields.get("schema_name"),
                fields["ossie_version"],
                _dump(fields.get("definition") or {}),
                fields.get("source_file_id"),
                now,
                now,
            ],
        )
        created = await self.get_semantic_model(model_id, owner_name=owner_name)
        assert created is not None  # just inserted
        return created

    async def list_semantic_models(self, *, owner_name: str) -> list[dict]:
        result = await db.execute_system(
            f"SELECT {_SEMANTIC_COLUMNS} FROM NOVA_SYSTEM.CONFIG_SEMANTIC_MODELS "
            "WHERE owner_name = %s ORDER BY updated_at DESC",
            [owner_name],
        )
        return [_semantic_row(row) for row in result["rows"]]

    async def get_semantic_model(self, model_id: str, *, owner_name: str) -> dict | None:
        result = await db.execute_system(
            f"SELECT {_SEMANTIC_COLUMNS} FROM NOVA_SYSTEM.CONFIG_SEMANTIC_MODELS "
            "WHERE semantic_model_id = %s AND owner_name = %s",
            [model_id, owner_name],
        )
        if not result["rows"]:
            return None
        return _semantic_row(result["rows"][0])

    async def update_semantic_model(
        self, model_id: str, *, owner_name: str, fields: dict
    ) -> dict | None:
        assignments: list[str] = []
        params: list[Any] = []
        for column in (
            "name",
            "description",
            "database_name",
            "schema_name",
            "ossie_version",
            "source_file_id",
        ):
            if column in fields:
                assignments.append(f"{column} = %s")
                params.append(fields[column])
        if "definition" in fields:
            assignments.append("definition = %s")
            params.append(_dump(fields["definition"] or {}))
        if assignments:
            assignments.append("updated_at = %s")
            params.append(_now())
            params.extend([model_id, owner_name])
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_SEMANTIC_MODELS SET "
                + ", ".join(assignments)
                + " WHERE semantic_model_id = %s AND owner_name = %s",
                params,
            )
        return await self.get_semantic_model(model_id, owner_name=owner_name)

    async def delete_semantic_model(self, model_id: str, *, owner_name: str) -> bool:
        result = await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_SEMANTIC_MODELS "
            "WHERE semantic_model_id = %s AND owner_name = %s",
            [model_id, owner_name],
        )
        return bool(result.get("affected", 0))

    # ── Verified semantic queries ──────────────────────────────

    async def create_verified_query(self, *, owner_name: str, fields: dict) -> dict:
        verified_query_id = str(uuid4())
        now = _now()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_SEMANTIC_VERIFIED_QUERIES ("
            "verified_query_id, owner_name, semantic_model_id, model_fingerprint, "
            "question, semantic_plan, verified_sql, expected_result_signature, "
            "verified_by, verified_at, tags, usage_count, success_count"
            ") VALUES (" + ", ".join(["%s"] * 13) + ")",
            [
                verified_query_id,
                owner_name,
                fields["semantic_model_id"],
                fields["model_fingerprint"],
                fields["question"],
                _dump(fields["semantic_plan"]),
                fields["verified_sql"],
                fields.get("expected_result_signature"),
                owner_name,
                now,
                _dump(fields.get("tags") or []),
                0,
                0,
            ],
        )
        return {
            "verified_query_id": verified_query_id,
            "owner_name": owner_name,
            **fields,
            "verified_by": owner_name,
            "verified_at": now,
            "usage_count": 0,
            "success_count": 0,
        }

    async def list_verified_queries(self, semantic_model_id: str, *, owner_name: str) -> list[dict]:
        result = await db.execute_system(
            "SELECT verified_query_id, semantic_model_id, model_fingerprint, question, "
            "semantic_plan, verified_sql, expected_result_signature, verified_by, "
            "verified_at, tags, usage_count, success_count "
            "FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VERIFIED_QUERIES "
            "WHERE semantic_model_id = %s AND owner_name = %s ORDER BY usage_count DESC",
            [semantic_model_id, owner_name],
        )
        return [
            {
                "verified_query_id": row[0],
                "semantic_model_id": row[1],
                "model_fingerprint": row[2],
                "question": row[3],
                "semantic_plan": _as_json(row[4]) or {},
                "verified_sql": row[5],
                "expected_result_signature": row[6],
                "verified_by": row[7],
                "verified_at": _iso(row[8]),
                "tags": _as_json(row[9]) or [],
                "usage_count": int(row[10] or 0),
                "success_count": int(row[11] or 0),
            }
            for row in result["rows"]
        ]

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
    ) -> None:
        """Persist redacted workload shape for feedback and MV recommendations."""
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.AUDIT_SEMANTIC_QUERY_USAGE ("
            "usage_id, owner_name, semantic_model_id, model_fingerprint, metric_names, "
            "dimension_names, filter_shape, time_grain, execution_latency_ms, scan_bytes, "
            "succeeded, created_at) VALUES (" + ", ".join(["%s"] * 12) + ")",
            [
                str(uuid4()),
                owner_name,
                semantic_model_id,
                model_fingerprint,
                _dump(metrics),
                _dump(dimensions),
                _dump(filter_shape),
                time_grain,
                execution_latency_ms,
                None,
                succeeded,
                _now(),
            ],
        )
        if verified_query_id:
            success_increment = 1 if succeeded else 0
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_SEMANTIC_VERIFIED_QUERIES "
                "SET usage_count = usage_count + 1, success_count = success_count + %s "
                "WHERE verified_query_id = %s AND owner_name = %s",
                [success_increment, verified_query_id, owner_name],
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
        skill_id = str(uuid4())
        now = _now()
        body = await self._store_skill_body(skill_id, owner_name, fields.get("body", ""))
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SKILLS ("
            "skill_id, owner_name, name, description, body, scope, created_at, updated_at"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                skill_id,
                owner_name,
                fields["name"],
                fields.get("description", ""),
                body,
                fields.get("scope", "user"),
                now,
                now,
            ],
        )
        created = await self.get_skill(skill_id, owner_name=owner_name)
        assert created is not None  # just inserted
        return created

    async def list_skills(self, *, owner_name: str) -> list[dict]:
        result = await db.execute_system(
            f"SELECT {_SKILL_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENT_SKILLS "
            "WHERE owner_name = %s ORDER BY name ASC",
            [owner_name],
        )
        return [await self._load_skill_body(_skill_row(row)) for row in result["rows"]]

    async def get_skill(self, skill_id: str, *, owner_name: str) -> dict | None:
        result = await db.execute_system(
            f"SELECT {_SKILL_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENT_SKILLS "
            "WHERE skill_id = %s AND owner_name = %s",
            [skill_id, owner_name],
        )
        if not result["rows"]:
            return None
        return await self._load_skill_body(_skill_row(result["rows"][0]))

    async def delete_skill(self, skill_id: str, *, owner_name: str) -> bool:
        result = await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_SKILLS WHERE skill_id = %s AND owner_name = %s",
            [skill_id, owner_name],
        )
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_SKILL_BODY_CHUNKS "
            "WHERE skill_id = %s AND owner_name = %s",
            [skill_id, owner_name],
        )
        return bool(result.get("affected", 0))

    async def update_skill(self, skill_id: str, *, owner_name: str, fields: dict) -> dict | None:
        if not await self.get_skill(skill_id, owner_name=owner_name):
            return None
        body = await self._store_skill_body(skill_id, owner_name, fields["body"])
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_SKILLS "
            "SET name = %s, description = %s, body = %s, scope = %s, updated_at = %s "
            "WHERE skill_id = %s AND owner_name = %s",
            [
                fields["name"],
                fields["description"],
                body,
                "user",
                _now(),
                skill_id,
                owner_name,
            ],
        )
        return await self.get_skill(skill_id, owner_name=owner_name)

    # ── MCP servers ────────────────────────────────────────────

    async def create_mcp_server(self, *, owner_name: str, fields: dict) -> dict:
        server_id = str(uuid4())
        now = _now()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_MCP_SERVERS ("
            "server_id, owner_name, name, description, transport, endpoint, command, "
            "args, is_active, last_status, created_at, updated_at"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                server_id,
                owner_name,
                fields["name"],
                fields.get("description", ""),
                fields.get("transport", "http"),
                fields.get("endpoint"),
                fields.get("command"),
                _dump(fields.get("args") or []),
                bool(fields.get("is_active", True)),
                None,
                now,
                now,
            ],
        )
        created = await self.get_mcp_server(server_id, owner_name=owner_name)
        assert created is not None
        return created

    async def list_mcp_servers(self, *, owner_name: str) -> list[dict]:
        result = await db.execute_system(
            f"SELECT {_MCP_COLUMNS} FROM NOVA_SYSTEM.CONFIG_MCP_SERVERS "
            "WHERE owner_name = %s ORDER BY name ASC",
            [owner_name],
        )
        return [_mcp_row(row) for row in result["rows"]]

    async def get_mcp_server(self, server_id: str, *, owner_name: str) -> dict | None:
        result = await db.execute_system(
            f"SELECT {_MCP_COLUMNS} FROM NOVA_SYSTEM.CONFIG_MCP_SERVERS "
            "WHERE server_id = %s AND owner_name = %s",
            [server_id, owner_name],
        )
        if not result["rows"]:
            return None
        return _mcp_row(result["rows"][0])

    async def update_mcp_server(
        self, server_id: str, *, owner_name: str, fields: dict
    ) -> dict | None:
        assignments: list[str] = []
        params: list[Any] = []
        for column in (
            "name",
            "description",
            "transport",
            "endpoint",
            "command",
            "is_active",
            "last_status",
        ):
            if column in fields:
                assignments.append(f"{column} = %s")
                params.append(fields[column])
        if "args" in fields:
            assignments.append("args = %s")
            params.append(_dump(fields["args"] or []))
        if assignments:
            assignments.append("updated_at = %s")
            params.append(_now())
            params.extend([server_id, owner_name])
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_MCP_SERVERS SET "
                + ", ".join(assignments)
                + " WHERE server_id = %s AND owner_name = %s",
                params,
            )
        return await self.get_mcp_server(server_id, owner_name=owner_name)

    async def delete_mcp_server(self, server_id: str, *, owner_name: str) -> bool:
        # Remove the server's discovered tools first so a delete cannot orphan them.
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_TOOLS WHERE owner_name = %s AND source = %s",
            [owner_name, f"mcp:{server_id}"],
        )
        result = await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_MCP_SERVERS WHERE server_id = %s AND owner_name = %s",
            [server_id, owner_name],
        )
        return bool(result.get("affected", 0))

    # ── Tools ──────────────────────────────────────────────────

    async def upsert_tool(self, *, owner_name: str, fields: dict) -> dict:
        """Insert or update one tool, keyed by ``(owner, source, name)``.

        A discovery run re-registers the same tools, so this is idempotent on that
        key rather than always inserting.
        """
        source = fields["source"]
        name = fields["name"]
        existing = await db.execute_system(
            f"SELECT {_TOOL_COLUMNS} FROM NOVA_SYSTEM.CONFIG_TOOLS "
            "WHERE owner_name = %s AND source = %s AND name = %s",
            [owner_name, source, name],
        )
        now = _now()
        if existing["rows"]:
            row = _tool_row(existing["rows"][0])
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_TOOLS SET description = %s, "
                "input_schema = %s, is_enabled = %s, updated_at = %s "
                "WHERE tool_id = %s AND owner_name = %s",
                [
                    fields.get("description", ""),
                    _dump(fields.get("input_schema") or {}),
                    bool(fields.get("is_enabled", True)),
                    now,
                    row["tool_id"],
                    owner_name,
                ],
            )
            updated = await self.get_tool(row["tool_id"], owner_name=owner_name)
            assert updated is not None
            return updated

        tool_id = str(uuid4())
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_TOOLS ("
            "tool_id, owner_name, name, description, source, input_schema, "
            "is_enabled, created_at, updated_at"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                tool_id,
                owner_name,
                name,
                fields.get("description", ""),
                source,
                _dump(fields.get("input_schema") or {}),
                bool(fields.get("is_enabled", True)),
                now,
                now,
            ],
        )
        created = await self.get_tool(tool_id, owner_name=owner_name)
        assert created is not None
        return created

    async def list_tools(self, *, owner_name: str) -> list[dict]:
        result = await db.execute_system(
            f"SELECT {_TOOL_COLUMNS} FROM NOVA_SYSTEM.CONFIG_TOOLS "
            "WHERE owner_name = %s ORDER BY source ASC, name ASC",
            [owner_name],
        )
        return [_tool_row(row) for row in result["rows"]]

    async def get_tool(self, tool_id: str, *, owner_name: str) -> dict | None:
        result = await db.execute_system(
            f"SELECT {_TOOL_COLUMNS} FROM NOVA_SYSTEM.CONFIG_TOOLS "
            "WHERE tool_id = %s AND owner_name = %s",
            [tool_id, owner_name],
        )
        if not result["rows"]:
            return None
        return _tool_row(result["rows"][0])

    async def set_tool_enabled(
        self, tool_id: str, *, owner_name: str, enabled: bool
    ) -> dict | None:
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_TOOLS SET is_enabled = %s, updated_at = %s "
            "WHERE tool_id = %s AND owner_name = %s",
            [enabled, _now(), tool_id, owner_name],
        )
        return await self.get_tool(tool_id, owner_name=owner_name)

    # ── Agent access roles ─────────────────────────────────────

    async def list_agent_roles(self, agent_id: str, *, owner_name: str) -> list[dict]:
        result = await db.execute_system(
            "SELECT role_name, grant_type FROM NOVA_SYSTEM.CONFIG_AGENT_ROLES "
            "WHERE agent_id = %s AND owner_name = %s ORDER BY role_name ASC",
            [agent_id, owner_name],
        )
        return [{"role_name": row[0], "grant_type": row[1] or "USAGE"} for row in result["rows"]]

    async def add_agent_role(
        self, agent_id: str, *, owner_name: str, role_name: str, grant_type: str = "USAGE"
    ) -> None:
        now = _now()
        # Primary key is (agent_id, role_name); an upsert keeps a re-add idempotent.
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_ROLES "
            "WHERE agent_id = %s AND owner_name = %s AND role_name = %s",
            [agent_id, owner_name, role_name],
        )
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_ROLES "
            "(agent_id, owner_name, role_name, grant_type, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [agent_id, owner_name, role_name, grant_type, now, now],
        )

    async def remove_agent_role(self, agent_id: str, *, owner_name: str, role_name: str) -> bool:
        result = await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_ROLES "
            "WHERE agent_id = %s AND owner_name = %s AND role_name = %s",
            [agent_id, owner_name, role_name],
        )
        return bool(result.get("affected", 0))

    # ── Custom tools ───────────────────────────────────────────

    async def create_custom_tool(self, *, owner_name: str, fields: dict) -> dict:
        tool_id = str(uuid4())
        now = _now()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_CUSTOM_TOOLS ("
            "tool_id, owner_name, name, description, kind, database_name, "
            "function_name, definition, created_at, updated_at"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                tool_id,
                owner_name,
                fields["name"],
                fields.get("description", ""),
                fields["kind"],
                fields.get("database_name"),
                fields.get("function_name"),
                _dump(fields.get("definition") or {}),
                now,
                now,
            ],
        )
        created = await self.get_custom_tool(tool_id, owner_name=owner_name)
        assert created is not None
        return created

    async def list_custom_tools(self, *, owner_name: str) -> list[dict]:
        result = await db.execute_system(
            "SELECT tool_id, owner_name, name, description, kind, database_name, "
            "function_name, definition, created_at, updated_at "
            "FROM NOVA_SYSTEM.CONFIG_CUSTOM_TOOLS "
            "WHERE owner_name = %s ORDER BY name ASC",
            [owner_name],
        )
        return [_custom_tool_row(row) for row in result["rows"]]

    async def get_custom_tool(self, tool_id: str, *, owner_name: str) -> dict | None:
        result = await db.execute_system(
            "SELECT tool_id, owner_name, name, description, kind, database_name, "
            "function_name, definition, created_at, updated_at "
            "FROM NOVA_SYSTEM.CONFIG_CUSTOM_TOOLS "
            "WHERE tool_id = %s AND owner_name = %s",
            [tool_id, owner_name],
        )
        if not result["rows"]:
            return None
        return _custom_tool_row(result["rows"][0])

    async def update_custom_tool(
        self, tool_id: str, *, owner_name: str, fields: dict
    ) -> dict | None:
        assignments: list[str] = []
        params: list[Any] = []
        for column in ("name", "description", "kind", "database_name", "function_name"):
            if column in fields:
                assignments.append(f"{column} = %s")
                params.append(fields[column])
        if "definition" in fields:
            assignments.append("definition = %s")
            params.append(_dump(fields["definition"] or {}))
        if assignments:
            assignments.append("updated_at = %s")
            params.append(_now())
            params.extend([tool_id, owner_name])
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_CUSTOM_TOOLS SET "
                + ", ".join(assignments)
                + " WHERE tool_id = %s AND owner_name = %s",
                params,
            )
        return await self.get_custom_tool(tool_id, owner_name=owner_name)

    async def delete_custom_tool(self, tool_id: str, *, owner_name: str) -> bool:
        result = await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_CUSTOM_TOOLS WHERE tool_id = %s AND owner_name = %s",
            [tool_id, owner_name],
        )
        return bool(result.get("affected", 0))


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
