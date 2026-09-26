"""Agent Studio persistence and read-only legacy semantic migration data.

Every table is a Primary Key table (``AGENTS.md`` §7). Published Semantic Views
are stored by the intelligence module; ``CONFIG_SEMANTIC_MODELS`` is historical.

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

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.database import db


class AgentMetadataUnavailable(RuntimeError):
    """A scoped StarRocks metadata read could not be trusted."""


async def _read_rows(
    sql: str,
    params: list[Any],
    valid_row: Callable[[list[Any]], bool],
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
    config_revision            VARCHAR(64)
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
    "policy, semantic_model_id, semantic_model_ids, semantic_view_ids, visibility, "
    "created_at, updated_at, resource_bindings, config_revision"
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
        "policy": policy or "auto_read_only",
        "semantic_model_id": semantic_model_id,
        "semantic_model_ids": legacy_ids,
        "semantic_view_ids": _view_ids(semantic_view_ids, legacy_ids),
        "visibility": visibility or "private",
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
        "resource_bindings": _as_json(resource_bindings) or {},
        "config_revision": config_revision,
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
        try:
            await db.execute_system(AGENTS_SEMANTIC_VIEW_IDS_DDL)
        except Exception as exc:  # noqa: BLE001 - duplicate column is benign
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
        for column, column_type in (
            ("verified_fingerprint", "VARCHAR(64)"),
            ("verified_at", "DATETIME"),
        ):
            try:
                await db.execute_system(
                    f"ALTER TABLE NOVA_SYSTEM.CONFIG_AGENT_ROLES ADD COLUMN {column} {column_type}"
                )
            except Exception as exc:  # noqa: BLE001 - duplicate column is benign
                if "already exists" not in str(exc).lower() and "duplicate" not in str(exc).lower():
                    raise
        await db.execute_system(CUSTOM_TOOLS_DDL)
        await db.execute_system(VERIFIED_QUERIES_DDL)
        await db.execute_system(SEMANTIC_USAGE_DDL)
        from app.modules.agents.versions import VERSIONS_DDL

        await db.execute_system(VERSIONS_DDL)

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
        agent_id = str(uuid4())
        now = _now()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENTS ("
            "agent_id, owner_name, database_name, schema_name, name, description, "
            "avatar, color, model_provider_id, model_name, instructions_response, "
            "instructions_orchestration, response_style, sample_questions, "
            "budget_seconds, budget_tokens, tool_not_accessible, default_tools, "
            "default_skills, discoverable_skills, compiled_instructions, harness_mode, "
            "policy, semantic_model_id, semantic_model_ids, semantic_view_ids, "
            "visibility, created_at, updated_at, resource_bindings, config_revision"
            ") VALUES (" + ", ".join(["%s"] * 31) + ")",
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
                None,
                None,
                _dump(_view_ids_from_fields(fields)),
                fields.get("visibility", "private"),
                now,
                now,
                _dump(fields.get("resource_bindings") or {}),
                str(uuid4()),
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
        rows = await _read_rows(
            f"SELECT {_AGENT_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENTS "
            f"WHERE {where} ORDER BY updated_at DESC",
            params,
            lambda row: (
                len(row) == 31
                and row[1] == owner_name
                and (not database_name or row[2] == database_name)
                and (not search or search.lower() in str(row[4]).lower())
            ),
        )
        return [_agent_row(row) for row in rows]

    async def get_agent(self, agent_id: str, *, owner_name: str) -> dict | None:
        rows = await _read_rows(
            f"SELECT {_AGENT_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENTS "
            "WHERE agent_id = %s AND owner_name = %s",
            [agent_id, owner_name],
            lambda row: len(row) == 31 and row[0] == agent_id and row[1] == owner_name,
        )
        if not rows:
            return None
        return _agent_row(rows[0])

    async def get_shared_agent(self, agent_id: str, *, role_name: str) -> dict | None:
        rows = await _read_rows(
            f"SELECT {_AGENT_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENTS "
            "WHERE agent_id = %s AND visibility = 'shared'",
            [agent_id],
            lambda row: len(row) == 31 and row[0] == agent_id and row[26] == "shared",
        )
        if not rows:
            return None
        agent = _agent_row(rows[0])
        grants = await self.list_agent_roles(agent_id, owner_name=agent["owner_name"])
        if not any(grant["role_name"] == role_name for grant in grants):
            return None
        return agent

    async def list_shared_agents(self, *, role_name: str) -> list[dict]:
        grants = await _read_rows(
            "SELECT agent_id, role_name FROM NOVA_SYSTEM.CONFIG_AGENT_ROLES WHERE role_name = %s",
            [role_name],
            lambda row: len(row) == 2 and row[1] == role_name,
        )
        agent_ids = [str(row[0]) for row in grants]
        if not agent_ids:
            return []
        placeholders = ", ".join(["%s"] * len(agent_ids))
        rows = await _read_rows(
            f"SELECT {_AGENT_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENTS "
            f"WHERE visibility = 'shared' AND agent_id IN ({placeholders}) "
            "ORDER BY updated_at DESC",
            agent_ids,
            lambda row: len(row) == 31 and row[0] in agent_ids and row[26] == "shared",
        )
        return [_agent_row(row) for row in rows]

    async def update_agent(self, agent_id: str, *, owner_name: str, fields: dict,
                           expected_revision: str | None = None,
                           check_revision: bool = False) -> dict | None:
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
        if "semantic_view_ids" in fields:
            assignments.append("semantic_view_ids = %s")
            params.append(_dump(_view_ids_from_fields(fields)))
        for column in (
            "sample_questions",
            "default_tools",
            "default_skills",
            "discoverable_skills",
            "compiled_instructions",
            "resource_bindings",
        ):
            if column in fields:
                assignments.append(f"{column} = %s")
                params.append(_dump(fields[column] or []))
        if assignments:
            from app.modules.agents.versions import agent_versions, revision_id

            prior = await self.get_agent(agent_id, owner_name=owner_name)
            if prior is None:
                return None
            if check_revision and prior.get("config_revision") != expected_revision:
                from fastapi import HTTPException

                raise HTTPException(409, "Agent changed. Reload and compare before publishing.")
            await agent_versions.store(
                prior, label="Previous configuration", version_id=revision_id(prior)
            )
            next_revision = str(uuid4())
            await agent_versions.store(
                {**prior, **fields}, label="Saved configuration", version_id=next_revision
            )
            assignments.append("config_revision = %s")
            params.append(next_revision)
            assignments.append("updated_at = %s")
            params.append(_now())
            params.extend([agent_id, owner_name])
            condition = " AND config_revision IS NULL"
            if prior.get("config_revision") is not None:
                condition = " AND config_revision = %s"
                params.append(prior["config_revision"])
            result = await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENTS SET "
                + ", ".join(assignments)
                + " WHERE agent_id = %s AND owner_name = %s"
                + condition,
                params,
            )
            if result.get("affected") != 1:
                from fastapi import HTTPException

                raise HTTPException(409, "Agent changed. Reload and compare before publishing.")
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
        rows = await _read_rows(
            "SELECT agent_id, owner_name, role_name, grant_type, "
            "verified_fingerprint, verified_at "
            "FROM NOVA_SYSTEM.CONFIG_AGENT_ROLES "
            "WHERE agent_id = %s AND owner_name = %s ORDER BY role_name ASC",
            [agent_id, owner_name],
            lambda row: len(row) == 6 and row[0] == agent_id and row[1] == owner_name,
        )
        return [
            {
                "role_name": row[2],
                "grant_type": row[3] or "USAGE",
                "verified_fingerprint": row[4],
                "verified_at": _iso(row[5]) if row[5] else None,
            }
            for row in rows
        ]

    async def set_agent_role_verification(
        self,
        agent_id: str,
        *,
        owner_name: str,
        role_name: str,
        fingerprint: str | None,
    ) -> None:
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_ROLES "
            "SET verified_fingerprint = %s, verified_at = %s, updated_at = %s "
            "WHERE agent_id = %s AND owner_name = %s AND role_name = %s",
            [fingerprint, _now() if fingerprint else None, _now(), agent_id, owner_name, role_name],
        )

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
