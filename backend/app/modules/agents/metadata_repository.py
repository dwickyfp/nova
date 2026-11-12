"""Studio agent metadata in the caller's PostgreSQL transaction.

The prepared replacement for the ``NOVA_SYSTEM`` paths of the agent
repositories (``AgentRepository``, ``AgentRunJournal``, ``MissionService``,
``AutomationRepository``, memory, quality, sharing and Studio stores),
switched in at the all-domain cutover. Revision 0021 holds the rules; this
module issues the statements the services need, each in the one transaction
the caller opened. It closes the Wave D/F gaps in the new store:

* definitions change only from the revision the caller read (CAS), and the
  configuration snapshots commit with the pointer that names them;
* a run event batch and its high-water mark commit together; a reused
  sequence with different content is a conflict, an identical resend is not;
* writes to a run are bound to its owner, active role, login, security
  version and claim generation, so an older producer is fenced out;
* runs whose events were never written stay as they are and replay reports
  the missing history instead of waiting or inventing it;
* an automation due claim has one winner and only the winner records the run;
* revision-store records (quality, memory) refuse a fork and a cross-scope ID.

Nothing here stores or returns a credential: text that looks like one is
refused before it reaches the database, without echoing the value.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.core.metadata_actor import actor_for, current_actor, metadata_transaction
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext
from app.modules.assistant.skills import contains_credential_shape

SKILL_BODY_PREFIX = "nova-skill-body:v1:"
_SKILL_PART = 16000
_INLINE_SKILL_BYTES = 60000
RUN_TERMINAL = ("completed", "failed", "interrupted", "cancelled")
ROLE_CHANGED = "__role_changed__"
QUALITY_TABLES = {
    "cases": "config_agent_quality_cases",
    "runs": "config_agent_quality_runs",
    "monitoring": "config_agent_quality_monitoring",
    "proposals": "config_agent_improvement_proposals",
    "lessons": "config_agent_harness_lessons",
}
_AGENT_JSON = (
    "sample_questions", "default_tools", "default_skills", "discoverable_skills",
    "compiled_instructions", "semantic_model_ids", "semantic_view_ids", "resource_bindings",
    "harness_policy",
)
_AGENT_FIELDS = (
    "database_name", "schema_name", "name", "description", "avatar", "color",
    "model_provider_id", "model_name", "instructions_response", "instructions_orchestration",
    "response_style", "budget_seconds", "budget_tokens", "tool_not_accessible", "harness_mode",
    "policy", "semantic_model_id", "visibility", "budget_profile", "release_manifest_id",
    *_AGENT_JSON,
)
_SKILL_FIELDS = ("name", "description", "body", "scope")
_MCP_FIELDS = ("name", "description", "transport", "endpoint", "command", "args", "is_active")
_CUSTOM_TOOL_FIELDS = (
    "name", "description", "kind", "database_name", "function_name", "definition",
)
_AUTOMATION_FIELDS = (
    "title", "prompt", "schedule_kind", "schedule_expr", "timezone", "condition_json",
    "delivery_json", "enabled", "next_run_at",
)
_ARTIFACT_FIELDS = (
    "title", "sql_text", "database_name", "schema_name", "bi_kind", "bi_object_id",
    "bi_dataset_id",
)
_RESEARCH_FIELDS = ("status", "plan_json", "progress_json", "report")
_MEMORY_FIELDS = ("fact_key", "fact", "source_quote", "source_thread_id")


class CredentialRefused(ValueError):
    """Text shaped like a credential; the value itself is never echoed."""

    def __init__(self) -> None:
        super().__init__("Agent metadata must be credential-free")


def frame_sequence(frame: str) -> int:
    """The sequence inside an SSE ``data:`` frame, as the source journal reads it."""
    try:
        payload = json.loads(frame.split("data: ", 1)[1])
        sequence = payload["sequence"]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("The run event has no sequence.") from exc
    if type(sequence) is not int or sequence < 0:
        raise ValueError("The run event has no sequence.")
    return sequence


def _screen(*values: Any) -> None:
    for value in values:
        if value is None:
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        if contains_credential_shape(text):
            raise CredentialRefused()


def _positive(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("A positive revision or generation is required")
    return value


def _json_text(value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), default=str)


class AgentMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Agent metadata requires an active metadata transaction")
        row = await self._one(
            "SELECT current_setting('nova.principal',true)=%s "
            "AND current_setting('nova.active_role',true)=%s "
            "AND current_setting('nova.security_context_version',true)=%s "
            "AND current_setting('nova.session_id',true)=%s "
            "AND current_setting('nova.service_identity',true)=current_user AS valid",
            (self.actor.principal, self.actor.active_role,
             str(self.actor.security_context_version), self.actor.session_id or ""),
        )
        if not row or row["valid"] is not True:
            raise MetadataPermissionDenied()

    async def _one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cursor = await self.connection.execute(query, params)
        return await cursor.fetchone()

    async def _all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = await self.connection.execute(query, params)
        return list(await cursor.fetchall())

    async def _count(self, query: str, params: tuple[Any, ...]) -> int:
        cursor = await self.connection.execute(query, params)
        return cursor.rowcount

    # ── Agents ────────────────────────────────────────────────────────────

    @staticmethod
    def _agent_values(fields: dict[str, Any]) -> dict[str, Any]:
        unknown = set(fields) - set(_AGENT_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported agent fields: {sorted(unknown)}")
        _screen(*fields.values())
        return {
            key: Jsonb(value) if key in _AGENT_JSON and value is not None else value
            for key, value in fields.items()
        }

    async def create_agent(self, fields: dict[str, Any], *, agent_id: str | None = None,
                           config_revision: str | None = None) -> dict[str, Any]:
        await self._bound()
        values = self._agent_values(fields)
        columns = ["agent_id", "owner_name", "config_revision", *values]
        row = await self._one(
            f"INSERT INTO nova_meta.config_agents ({','.join(columns)}) VALUES ("
            + ",".join(["%s"] * len(columns)) + ") RETURNING *",
            (agent_id or str(uuid4()), self.actor.principal, config_revision, *values.values()),
        )
        assert row is not None
        return row

    async def lock_agent(self, agent_id: str) -> dict[str, Any]:
        await self._bound()
        row = await self._one(
            "SELECT * FROM nova_meta.config_agents WHERE agent_id=%s AND owner_name=%s FOR UPDATE",
            (agent_id, self.actor.principal))
        if row is None:
            raise MetadataConflict()
        return row

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """The agent if the caller owns it or their active role holds a grant on it."""
        await self._bound()
        return await self._one("SELECT * FROM nova_meta.config_agents WHERE agent_id=%s",
                               (agent_id,))

    async def list_agents(self) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_agents ORDER BY owner_name,name,agent_id")

    async def update_agent(
        self, agent_id: str, changes: dict[str, Any], *, expected_config_revision: str | None,
        snapshots: list[dict[str, Any]] = (),  # type: ignore[assignment]
        config_revision: str | None = None,
    ) -> dict[str, Any]:
        """Save snapshots and the new configuration together, from the revision read.

        Each snapshot is ``{"version_id", "configuration", "label"}``. A failed
        CAS raises, so the caller's transaction rolls back every snapshot too.
        """
        await self._bound()
        if not changes:
            raise ValueError("No agent changes")
        values = self._agent_values(changes)
        for snapshot in snapshots:
            await self.store_version(
                snapshot["version_id"], agent_id, snapshot["configuration"], snapshot["label"],
            )
        assignments = ",".join(f"{field}=%s" for field in values)
        row = await self._one(
            f"UPDATE nova_meta.config_agents SET {assignments},config_revision=%s,"
            "metadata_revision=metadata_revision+1 WHERE agent_id=%s AND owner_name=%s "
            "AND config_revision IS NOT DISTINCT FROM %s RETURNING *",
            (*values.values(), config_revision or str(uuid4()), agent_id, self.actor.principal,
             expected_config_revision),
        )
        if row is None:
            raise MetadataConflict()
        return row

    async def delete_agent(self, agent_id: str) -> bool:
        await self._bound()
        return await self._count(
            "DELETE FROM nova_meta.config_agents WHERE agent_id=%s AND owner_name=%s",
            (agent_id, self.actor.principal)) == 1

    # ── Agent access roles ────────────────────────────────────────────────

    async def list_agent_roles(self, agent_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_agent_roles WHERE agent_id=%s ORDER BY role_name",
            (agent_id,))

    async def replace_agent_roles(
        self, agent_id: str, grants: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Make the agent's role grants exactly ``grants`` (role → grant type), atomically."""
        await self._bound()
        await self.connection.execute(
            "DELETE FROM nova_meta.config_agent_roles WHERE agent_id=%s AND owner_name=%s "
            "AND role_name<>ALL(%s)", (agent_id, self.actor.principal, list(grants)))
        for role, grant_type in grants.items():
            await self.connection.execute(
                "INSERT INTO nova_meta.config_agent_roles (agent_id,role_name,owner_name,"
                "grant_type) VALUES (%s,%s,%s,%s) ON CONFLICT (agent_id,role_name) DO UPDATE "
                "SET grant_type=EXCLUDED.grant_type,verified_fingerprint=NULL,"
                "metadata_revision=config_agent_roles.metadata_revision+1 "
                "WHERE config_agent_roles.grant_type IS DISTINCT FROM EXCLUDED.grant_type",
                (agent_id, role, self.actor.principal, grant_type))
        return await self.list_agent_roles(agent_id)

    async def set_role_verification(
        self, agent_id: str, role: str, fingerprint: str | None, *, expected_revision: int,
    ) -> dict[str, Any]:
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_agent_roles SET verified_fingerprint=%s,"
            "metadata_revision=metadata_revision+1 WHERE agent_id=%s AND role_name=%s "
            "AND owner_name=%s AND metadata_revision=%s RETURNING *",
            (fingerprint, agent_id, role, self.actor.principal, _positive(expected_revision)))
        if row is None:
            raise MetadataConflict()
        return row

    # ── Immutable versions and release manifests ──────────────────────────

    async def store_version(
        self, version_id: str, agent_id: str, configuration: Any, label: str,
    ) -> dict[str, Any]:
        """Store a snapshot once; the same ID with other content is a conflict."""
        await self._bound()
        text = _json_text(configuration)
        _screen(text, label)
        row = await self._one(
            "INSERT INTO nova_meta.config_agent_versions (version_id,agent_id,owner_name,"
            "configuration,label) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (version_id) DO NOTHING "
            "RETURNING *", (version_id, agent_id, self.actor.principal, text, label))
        if row is not None:
            return row
        existing = await self._one(
            "SELECT * FROM nova_meta.config_agent_versions WHERE version_id=%s", (version_id,))
        if existing is None or existing["agent_id"] != agent_id:
            raise MetadataConflict()
        stored_content = json.dumps(json.loads(existing["configuration"]), sort_keys=True,
                                    separators=(",", ":"), allow_nan=False)
        proposed_content = json.dumps(json.loads(text or "null"), sort_keys=True,
                                      separators=(",", ":"), allow_nan=False)
        if stored_content != proposed_content:
            raise MetadataConflict()
        return existing

    async def get_version(self, version_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_agent_versions WHERE version_id=%s", (version_id,))

    async def store_manifest(
        self, manifest_id: str, agent_id: str, version_id: str, payload: Any,
    ) -> dict[str, Any]:
        await self._bound()
        text = _json_text(payload)
        _screen(text)
        row = await self._one(
            "INSERT INTO nova_meta.config_agent_release_manifests (manifest_id,agent_id,"
            "owner_name,version_id,payload) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (manifest_id) DO NOTHING RETURNING *",
            (manifest_id, agent_id, self.actor.principal, version_id, text))
        if row is not None:
            return row
        existing = await self.get_manifest(manifest_id)
        if existing is None or (existing["agent_id"], existing["version_id"],
                                existing["payload"]) != (agent_id, version_id, text):
            raise MetadataConflict()
        return existing

    async def get_manifest(self, manifest_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_agent_release_manifests WHERE manifest_id=%s",
            (manifest_id,))

    # ── Skills ────────────────────────────────────────────────────────────

    async def _store_skill_body(self, skill_id: str, body: str) -> str:
        if len(body.encode()) <= _INLINE_SKILL_BYTES and not body.startswith(SKILL_BODY_PREFIX):
            return body
        revision = str(uuid4())
        parts = [body[offset:offset + _SKILL_PART] for offset in range(0, len(body), _SKILL_PART)]
        async with self.connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO nova_meta.config_skill_body_chunks (skill_id,revision,part_index,"
                "owner_name,content) VALUES (%s,%s,%s,%s,%s)",
                [(skill_id, revision, index, self.actor.principal, part)
                 for index, part in enumerate(parts)])
        return f"{SKILL_BODY_PREFIX}{revision}:{len(parts)}"

    async def _load_skill_body(self, skill: dict[str, Any]) -> dict[str, Any]:
        body = skill.get("body") or ""
        if not body.startswith(SKILL_BODY_PREFIX):
            return skill
        revision, count = body[len(SKILL_BODY_PREFIX):].split(":")
        rows = await self._all(
            "SELECT part_index,content FROM nova_meta.config_skill_body_chunks WHERE skill_id=%s "
            "AND owner_name=%s AND revision=%s ORDER BY part_index",
            (skill["skill_id"], skill["owner_name"], revision))
        if len(rows) != int(count) or any(row["part_index"] != i for i, row in enumerate(rows)):
            raise ValueError("The skill document could not be loaded completely.")
        return {**skill, "body": "".join(row["content"] for row in rows)}

    async def create_skill(self, fields: dict[str, Any]) -> dict[str, Any]:
        await self._bound()
        _screen(fields.get("name"), fields.get("description"), fields.get("body"))
        skill_id = str(uuid4())
        body = await self._store_skill_body(skill_id, fields.get("body") or "")
        row = await self._one(
            "INSERT INTO nova_meta.config_agent_skills (skill_id,owner_name,name,description,"
            "body,scope) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (skill_id, self.actor.principal, fields["name"], fields.get("description", ""), body,
             fields.get("scope", "user")))
        assert row is not None
        return await self._load_skill_body(row)

    async def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        await self._bound()
        row = await self._one(
            "SELECT * FROM nova_meta.config_agent_skills WHERE skill_id=%s", (skill_id,))
        return await self._load_skill_body(row) if row else None

    async def list_skills(self, owner_name: str) -> list[dict[str, Any]]:
        """The owner's skills visible to the caller (all, or those a shared agent names)."""
        await self._bound()
        rows = await self._all(
            "SELECT * FROM nova_meta.config_agent_skills WHERE owner_name=%s ORDER BY name",
            (owner_name,))
        return [await self._load_skill_body(row) for row in rows]

    async def update_skill(
        self, skill_id: str, changes: dict[str, Any], *, expected_revision: int,
    ) -> dict[str, Any]:
        await self._bound()
        unknown = set(changes) - set(_SKILL_FIELDS)
        if unknown or not changes:
            raise ValueError(f"Unsupported skill changes: {sorted(unknown)}")
        _screen(*changes.values())
        values = dict(changes)
        if "body" in values:
            values["body"] = await self._store_skill_body(skill_id, values["body"] or "")
        row = await self._one(
            "UPDATE nova_meta.config_agent_skills SET "
            + ",".join(f"{field}=%s" for field in values)
            + ",metadata_revision=metadata_revision+1 WHERE skill_id=%s AND owner_name=%s "
            "AND metadata_revision=%s RETURNING *",
            (*values.values(), skill_id, self.actor.principal, _positive(expected_revision)))
        if row is None:
            raise MetadataConflict()
        return await self._load_skill_body(row)

    async def delete_skill(self, skill_id: str) -> bool:
        await self._bound()
        deleted = await self._count(
            "DELETE FROM nova_meta.config_agent_skills WHERE skill_id=%s AND owner_name=%s",
            (skill_id, self.actor.principal))
        await self.connection.execute(
            "DELETE FROM nova_meta.config_skill_body_chunks WHERE skill_id=%s AND owner_name=%s",
            (skill_id, self.actor.principal))
        return deleted == 1

    # ── MCP servers and tools ─────────────────────────────────────────────

    @staticmethod
    def _mcp_values(fields: dict[str, Any]) -> dict[str, Any]:
        unknown = set(fields) - set(_MCP_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported MCP server fields: {sorted(unknown)}")
        _screen(*fields.values())
        return {key: Jsonb(value) if key == "args" and value is not None else value
                for key, value in fields.items()}

    async def create_mcp_server(
        self, fields: dict[str, Any], *, owner_name: str | None = None
    ) -> dict[str, Any]:
        await self._bound()
        values = self._mcp_values(fields)
        columns = ["server_id", "owner_name", *values]
        row = await self._one(
            f"INSERT INTO nova_meta.config_mcp_servers ({','.join(columns)}) VALUES ("
            + ",".join(["%s"] * len(columns)) + ") RETURNING *",
            (str(uuid4()), (owner_name or self.actor.principal), *values.values()))
        assert row is not None
        return row

    async def update_mcp_server(
        self, server_id: str, changes: dict[str, Any], *, expected_revision: int,
        owner_name: str | None = None,
    ) -> dict[str, Any]:
        await self._bound()
        values = self._mcp_values(changes)
        if not values:
            raise ValueError("No MCP server changes")
        row = await self._one(
            "UPDATE nova_meta.config_mcp_servers SET "
            + ",".join(f"{field}=%s" for field in values)
            + ",metadata_revision=metadata_revision+1 WHERE server_id=%s AND owner_name=%s "
            "AND metadata_revision=%s RETURNING *",
            (
                *values.values(),
                server_id,
                (owner_name or self.actor.principal),
                _positive(expected_revision),
            ),
        )
        if row is None:
            raise MetadataConflict()
        return row

    async def set_mcp_status(
        self, server_id: str, status: str, *, owner_name: str | None = None
    ) -> bool:
        await self._bound()
        return (
            await self._count(
                "UPDATE nova_meta.config_mcp_servers SET last_status=%s WHERE server_id=%s "
                "AND owner_name=%s",
                (status[:32], server_id, (owner_name or self.actor.principal)),
            )
            == 1
        )

    async def list_mcp_servers(self, owner_name: str | None = None) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_mcp_servers WHERE owner_name=%s ORDER BY name",
            ((owner_name or self.actor.principal),))

    async def delete_mcp_server(self, server_id: str, *, owner_name: str | None = None) -> bool:
        """Remove the registration and the tools it contributed, together."""
        await self._bound()
        await self.connection.execute(
            "DELETE FROM nova_meta.config_tools WHERE owner_name=%s AND source=%s",
            ((owner_name or self.actor.principal), f"mcp:{server_id}"))
        return await self._count(
            "DELETE FROM nova_meta.config_mcp_servers WHERE server_id=%s AND owner_name=%s",
            (server_id, (owner_name or self.actor.principal))) == 1

    async def upsert_tool(
        self, fields: dict[str, Any], *, owner_name: str | None = None
    ) -> dict[str, Any]:
        """Register a tool idempotently on (owner, source, name)."""
        await self._bound()
        _screen(fields.get("description"), fields.get("input_schema"))
        schema = fields.get("input_schema") or {}
        existing = await self._all(
            "SELECT tool_id FROM nova_meta.config_tools WHERE owner_name=%s AND source=%s "
            "AND name=%s ORDER BY tool_id LIMIT 2 FOR UPDATE",
            (owner_name or self.actor.principal, fields["source"], fields["name"]))
        if len(existing) > 1:
            raise MetadataConflict()
        if existing:
            updated = await self._one(
                "UPDATE nova_meta.config_tools SET description=%s,input_schema=%s,is_enabled=%s,"
                "metadata_revision=metadata_revision+1 WHERE tool_id=%s RETURNING *",
                (fields.get("description", ""), Jsonb(schema), bool(fields.get("is_enabled", True)),
                 existing[0]["tool_id"]))
            if updated is None:
                raise MetadataConflict()
            return updated
        row = await self._one(
            "INSERT INTO nova_meta.config_tools (tool_id,owner_name,name,description,source,"
            "input_schema,is_enabled) VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (owner_name,source,name) WHERE provenance='runtime' DO UPDATE SET "
            "description=EXCLUDED.description,input_schema=EXCLUDED.input_schema,"
            "is_enabled=EXCLUDED.is_enabled,metadata_revision=config_tools.metadata_revision+1 "
            "RETURNING *",
            (
                str(uuid4()),
                (owner_name or self.actor.principal),
                fields["name"],
                fields.get("description", ""),
                fields["source"],
                Jsonb(schema),
                bool(fields.get("is_enabled", True)),
            ),
        )
        assert row is not None
        return row

    async def set_tool_enabled(
        self, tool_id: str, enabled: bool, *, owner_name: str | None = None
    ) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "UPDATE nova_meta.config_tools SET is_enabled=%s,metadata_revision=metadata_revision+1 "
            "WHERE tool_id=%s AND owner_name=%s RETURNING *",
            (enabled, tool_id, (owner_name or self.actor.principal)))

    async def list_tools(self, owner_name: str | None = None) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_tools WHERE owner_name=%s ORDER BY source,name",
            ((owner_name or self.actor.principal),))

    async def create_custom_tool(self, fields: dict[str, Any]) -> dict[str, Any]:
        await self._bound()
        unknown = set(fields) - set(_CUSTOM_TOOL_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported custom tool fields: {sorted(unknown)}")
        _screen(*fields.values())
        values = {key: Jsonb(value) if key == "definition" and value is not None else value
                  for key, value in fields.items()}
        columns = ["tool_id", "owner_name", *values]
        row = await self._one(
            f"INSERT INTO nova_meta.config_custom_tools ({','.join(columns)}) VALUES ("
            + ",".join(["%s"] * len(columns)) + ") RETURNING *",
            (str(uuid4()), self.actor.principal, *values.values()))
        assert row is not None
        return row

    async def list_custom_tools(self, owner_name: str) -> list[dict[str, Any]]:
        """The owner's custom tools visible to the caller (all, or those a shared agent names)."""
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_custom_tools WHERE owner_name=%s ORDER BY name",
            (owner_name,))

    async def get_mcp_server(self, server_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one("SELECT * FROM nova_meta.config_mcp_servers WHERE server_id=%s",
                               (server_id,))

    async def get_tool(self, tool_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one("SELECT * FROM nova_meta.config_tools WHERE tool_id=%s", (tool_id,))

    async def get_custom_tool(self, tool_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one("SELECT * FROM nova_meta.config_custom_tools WHERE tool_id=%s",
                               (tool_id,))

    async def update_custom_tool(
        self, tool_id: str, changes: dict[str, Any], *, expected_revision: int,
    ) -> dict[str, Any]:
        await self._bound()
        unknown = set(changes) - set(_CUSTOM_TOOL_FIELDS)
        if unknown or not changes:
            raise ValueError("Unsupported custom tool changes")
        _screen(*changes.values())
        values = {key: Jsonb(value) if key == "definition" and value is not None else value
                  for key, value in changes.items()}
        row = await self._one(
            "UPDATE nova_meta.config_custom_tools SET "
            + ",".join(f"{field}=%s" for field in values)
            + ",metadata_revision=metadata_revision+1 WHERE tool_id=%s AND owner_name=%s "
            "AND metadata_revision=%s RETURNING *",
            (*values.values(), tool_id, self.actor.principal, _positive(expected_revision)))
        if row is None:
            raise MetadataConflict()
        return row

    async def delete_custom_tool(self, tool_id: str) -> bool:
        await self._bound()
        return await self._count(
            "DELETE FROM nova_meta.config_custom_tools WHERE tool_id=%s AND owner_name=%s",
            (tool_id, self.actor.principal)) == 1

    async def add_agent_role(self, agent_id: str, role: str, grant_type: str) -> None:
        await self._bound()
        await self.connection.execute(
            "INSERT INTO nova_meta.config_agent_roles (agent_id,role_name,owner_name,grant_type) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT (agent_id,role_name) DO UPDATE "
            "SET grant_type=EXCLUDED.grant_type,verified_fingerprint=NULL,"
            "metadata_revision=config_agent_roles.metadata_revision+1 "
            "WHERE config_agent_roles.owner_name=EXCLUDED.owner_name",
            (agent_id, role, self.actor.principal, grant_type))

    async def remove_agent_role(self, agent_id: str, role: str) -> bool:
        await self._bound()
        return await self._count(
            "DELETE FROM nova_meta.config_agent_roles WHERE agent_id=%s AND role_name=%s "
            "AND owner_name=%s", (agent_id, role, self.actor.principal)) == 1

    async def list_versions(self, agent_id: str, owner: str, offset: int, limit: int) -> list[dict]:
        await self._bound()
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("Invalid agent version page")
        return await self._all(
            "SELECT version_id,label,created_at FROM nova_meta.config_agent_versions "
            "WHERE agent_id=%s AND owner_name=%s ORDER BY created_at DESC,version_id DESC "
            "LIMIT %s OFFSET %s", (agent_id, owner, limit, offset))

    async def find_manifests(
        self, agent_id: str, owner: str, *, manifest_id: str | None = None,
        version_id: str | None = None,
    ) -> list[dict[str, Any]]:
        await self._bound()
        if manifest_id is None and version_id is None:
            raise ValueError("A manifest or version identifier is required")
        column, value = ("manifest_id", manifest_id) if manifest_id else ("version_id", version_id)
        return await self._all(
            "SELECT * FROM nova_meta.config_agent_release_manifests "
            f"WHERE agent_id=%s AND owner_name=%s AND {column}=%s LIMIT 2",
            (agent_id, owner, value))

    # ── Shares ────────────────────────────────────────────────────────────

    async def create_share(
        self, object_type: str, object_id: str, target_type: str, target_name: str,
    ) -> dict[str, Any]:
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.config_studio_shares (share_id,object_type,object_id,"
            "owner_name,target_type,target_name) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (str(uuid4()), object_type, object_id, self.actor.principal, target_type,
             target_name))
        assert row is not None
        return row

    async def lock_share_object(self, object_type: str, object_id: str) -> None:
        await self._bound()
        await self.connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (json.dumps(["agent-shares", self.actor.principal, object_type, object_id]),))

    async def shares_for_object(self, object_type: str, object_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_studio_shares WHERE object_type=%s "
            "AND object_id=%s AND owner_name=%s ORDER BY created_at,share_id",
            (object_type, object_id, self.actor.principal))

    async def active_grants(
        self, object_type: str | None = None, *, object_id: str | None = None,
        owner_name: str | None = None, after: str = "", limit: int = 101,
        latest_first: bool = False,
    ) -> list[dict[str, Any]]:
        """Shares addressed to the caller or the caller's active role."""
        await self._bound()
        clauses = ["((target_type='user' AND target_name=%s) OR "
                   "(target_type='role' AND target_name=%s))", "object_id>%s"]
        params: list[Any] = [self.actor.principal, self.actor.active_role, after]
        for column, value in (("object_type", object_type), ("object_id", object_id),
                              ("owner_name", owner_name)):
            if value is not None:
                clauses.append(f"{column}=%s")
                params.append(value)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Share limit must be between one and 500")
        return await self._all(
            "SELECT * FROM nova_meta.config_studio_shares WHERE "
            + " AND ".join(clauses)
            + (
                " ORDER BY created_at DESC,share_id"
                if latest_first
                else " ORDER BY object_id,share_id"
            )
            + " LIMIT %s",
            (*params, limit),
        )

    async def shared_conversation(self, thread_id: str, owner: str) -> dict[str, Any] | None:
        await self._bound()
        row = await self._one(
            "SELECT nova_meta.shared_conversation(%s,%s) AS payload", (thread_id, owner))
        return row["payload"] if row else None

    async def delete_share(self, share_id: str) -> bool:
        await self._bound()
        return await self._count(
            "DELETE FROM nova_meta.config_studio_shares WHERE share_id=%s AND owner_name=%s",
            (share_id, self.actor.principal)) == 1

    # ── Runs and events ───────────────────────────────────────────────────

    async def start_run(self, run_id: str, agent_id: str, thread_id: str) -> dict[str, Any]:
        """Open a run claimed (generation 1) by this login and security version."""
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.config_agent_runs (run_id,owner_name,agent_id,thread_id,"
            "role_name,status) VALUES (%s,%s,%s,%s,%s,'running') RETURNING *",
            (run_id, self.actor.principal, agent_id, thread_id, self.actor.active_role))
        assert row is not None
        return row

    async def _claimed_run(self, run_id: str, generation: int) -> dict[str, Any]:
        row = await self._one(
            "SELECT * FROM nova_meta.config_agent_runs WHERE run_id=%s AND owner_name=%s "
            "AND role_name=%s FOR UPDATE", (run_id, self.actor.principal, self.actor.active_role))
        if (
            row is None or row["status"] != "running"
            or row["claim_generation"] != _positive(generation)
            or row["session_id"] != self.actor.session_id
            or row["security_version"] != self.actor.security_context_version
        ):
            raise MetadataConflict()
        return row

    async def append_events(self, run_id: str, frames: list[str], *, generation: int) -> int:
        """Publish an event batch and its high-water mark together; returns the mark.

        Sequences must increase within the batch. Frames already stored with
        identical content are accepted again (a resend after an unknown commit);
        a stored sequence with different content is a conflict.
        """
        await self._bound()
        if not frames or len(frames) > 32:
            raise ValueError("A run event batch must contain 1–32 frames.")
        sequences = [frame_sequence(frame) for frame in frames]
        if any(b <= a for a, b in zip(sequences, sequences[1:], strict=False)):
            raise ValueError("Run event sequence is not increasing.")
        run = await self._claimed_run(run_id, generation)
        stored = {
            row["sequence"]: row["frame"] for row in await self._all(
                "SELECT sequence,frame FROM nova_meta.config_agent_run_events WHERE run_id=%s "
                "AND sequence=ANY(%s)", (run_id, sequences))
        }
        fresh = []
        for sequence, frame in zip(sequences, frames, strict=True):
            if sequence in stored:
                if stored[sequence] != frame:
                    raise MetadataConflict()
            else:
                fresh.append((run_id, sequence, frame, generation))
        if fresh:
            async with self.connection.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO nova_meta.config_agent_run_events (run_id,sequence,frame,"
                    "claim_generation) VALUES (%s,%s,%s,%s)", fresh)
        high_water = max(run["last_sequence"], sequences[-1])
        if high_water != run["last_sequence"]:
            row = await self._one(
                "UPDATE nova_meta.config_agent_runs SET last_sequence=%s WHERE run_id=%s "
                "AND claim_generation=%s AND status='running' RETURNING last_sequence",
                (high_water, run_id, generation))
            if row is None:
                raise MetadataConflict()
        return int(high_water)

    async def heartbeat_run(self, run_id: str, *, generation: int) -> bool:
        await self._bound()
        try:
            await self._claimed_run(run_id, generation)
        except MetadataConflict:
            return False
        await self.connection.execute(
            "UPDATE nova_meta.config_agent_runs SET status=status WHERE run_id=%s", (run_id,))
        return True

    async def finish_run(self, run_id: str, status: str, *, generation: int) -> bool:
        await self._bound()
        if status not in RUN_TERMINAL:
            raise ValueError("Invalid run status")
        try:
            await self._claimed_run(run_id, generation)
        except MetadataConflict:
            return False
        return await self._count(
            "UPDATE nova_meta.config_agent_runs SET status=%s WHERE run_id=%s "
            "AND claim_generation=%s AND status='running'", (status, run_id, generation)) == 1

    async def claim_run(self, run_id: str, *, stale_after_seconds: float) -> int | None:
        """Take over a running run whose producer stopped renewing; returns the new claim."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_agent_runs SET claim_generation=claim_generation+1 "
            "WHERE run_id=%s AND owner_name=%s AND role_name=%s AND status='running' "
            "AND updated_at<=clock_timestamp()-make_interval(secs=>%s) RETURNING claim_generation",
            (run_id, self.actor.principal, self.actor.active_role, float(stale_after_seconds)))
        return int(row["claim_generation"]) if row else None

    async def interrupt_stale(self, run_id: str, *, stale_after_seconds: float) -> bool:
        """Close a run whose producer stopped renewing; its producer is fenced out."""
        await self._bound()
        await self.connection.execute(
            "UPDATE nova_meta.config_agent_runs SET status='interrupted',"
            "claim_generation=claim_generation+1 WHERE run_id=%s AND owner_name=%s "
            "AND role_name=%s AND status='running' "
            "AND updated_at<=clock_timestamp()-make_interval(secs=>%s)",
            (run_id, self.actor.principal, self.actor.active_role, float(stale_after_seconds)))
        run = await self.get_run(run_id)
        return run is not None and run["status"] == "interrupted"

    async def block_replay_after_role_change(self, run_id: str) -> bool:
        await self._bound()
        return await self._count(
            "UPDATE nova_meta.config_agent_runs SET role_name=%s WHERE run_id=%s "
            "AND owner_name=%s AND role_name=%s",
            (ROLE_CHANGED, run_id, self.actor.principal, self.actor.active_role)) == 1

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        """The run in the caller's active role; a role-changed run is not replayable."""
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_agent_runs WHERE run_id=%s AND owner_name=%s "
            "AND role_name=%s", (run_id, self.actor.principal, self.actor.active_role))

    async def current_run(self, agent_id: str, thread_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_agent_runs WHERE owner_name=%s AND agent_id=%s "
            "AND thread_id=%s AND role_name=%s AND security_version=%s AND status='running' "
            "ORDER BY started_at DESC,run_id DESC LIMIT 1",
            (self.actor.principal, agent_id, thread_id, self.actor.active_role,
             self.actor.security_context_version))

    async def history(self, run_id: str, after: int = -1, *, limit: int = 100) -> dict[str, Any]:
        """Exact stored frames after ``after``, with an explicit missing-history outcome.

        ``complete`` is false when the stored events stop before the run's
        high-water mark: those events were never written (historical runs) and
        are reported as unavailable, never waited for or invented.
        """
        await self._bound()
        run = await self.get_run(run_id)
        if run is None:
            return {"found": False, "frames": [], "complete": False}
        limit = min(max(limit, 1), 100)
        rows = await self._all(
            "SELECT sequence,frame FROM nova_meta.config_agent_run_events WHERE run_id=%s "
            "AND sequence>%s AND sequence<=%s ORDER BY sequence LIMIT %s",
            (run_id, after, run["last_sequence"], limit))
        reached = rows[-1]["sequence"] if rows else after
        more = len(rows) == limit and reached < run["last_sequence"]
        return {
            "found": True, "status": run["status"], "last_sequence": run["last_sequence"],
            "frames": [row["frame"] for row in rows], "more": more,
            "complete": more or reached >= run["last_sequence"],
        }

    async def delete_thread_runs(self, thread_id: str) -> int:
        """Remove the caller's run history for a thread (every role), with its events."""
        await self._bound()
        await self.connection.execute(
            "DELETE FROM nova_meta.config_agent_run_events e USING nova_meta.config_agent_runs r "
            "WHERE e.run_id=r.run_id AND r.thread_id=%s AND r.owner_name=%s",
            (thread_id, self.actor.principal))
        return await self._count(
            "DELETE FROM nova_meta.config_agent_runs WHERE thread_id=%s AND owner_name=%s",
            (thread_id, self.actor.principal))

    # ── Missions and deliverables ─────────────────────────────────────────

    async def create_mission(
        self, mission_id: str, thread_id: str, payload: Any, *, revision: int = 1,
    ) -> dict[str, Any]:
        await self._bound()
        text = _json_text(payload)
        _screen(text)
        row = await self._one(
            "INSERT INTO nova_meta.config_studio_missions (mission_id,owner_name,thread_id,"
            "role_name,revision,payload) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (mission_id, self.actor.principal, thread_id, self.actor.active_role,
             _positive(revision), text))
        assert row is not None
        return row

    async def get_mission(self, mission_id: str, *, session_scope: bool = True,
                          ) -> dict[str, Any] | None:
        """The Mission in the owner/role scope; ``session_scope`` also requires this login."""
        await self._bound()
        query = "SELECT * FROM nova_meta.config_studio_missions WHERE mission_id=%s"
        params: tuple[Any, ...] = (mission_id,)
        if session_scope:
            query += " AND session_id=%s AND security_version=%s"
            params += (self.actor.session_id, self.actor.security_context_version)
        return await self._one(query, params)

    async def save_mission(
        self, mission_id: str, payload: Any, *, expected_revision: int, resume: bool = False,
    ) -> dict[str, Any]:
        """Advance a Mission from the revision read.

        Without ``resume`` the Mission must already belong to this login and
        security version. ``resume`` is the explicit reauthorization that
        rebinds an older Mission to the current one.
        """
        await self._bound()
        text = _json_text(payload)
        _screen(text)
        scope = "" if resume else " AND session_id=%s AND security_version=%s"
        params: tuple[Any, ...] = () if resume else (
            self.actor.session_id, self.actor.security_context_version)
        row = await self._one(
            "UPDATE nova_meta.config_studio_missions SET revision=revision+1,payload=%s "
            f"WHERE mission_id=%s AND revision=%s{scope} RETURNING *",
            (text, mission_id, _positive(expected_revision), *params))
        if row is None:
            raise MetadataConflict()
        return row

    async def deliver(
        self, mission_id: str, request_digest: str, payload: Any,
    ) -> tuple[dict[str, Any], bool]:
        """Record one receipt per Mission request; a retry returns the existing one."""
        await self._bound()
        text = _json_text(payload)
        _screen(text)
        row = await self._one(
            "INSERT INTO nova_meta.config_studio_deliverables (deliverable_id,mission_id,"
            "owner_name,role_name,request_digest,payload) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (mission_id,request_digest) WHERE provenance='runtime' DO NOTHING "
            "RETURNING *",
            (str(uuid4()), mission_id, self.actor.principal, self.actor.active_role,
             request_digest, text))
        if row is not None:
            return row, True
        existing = await self._one(
            "SELECT * FROM nova_meta.config_studio_deliverables WHERE mission_id=%s "
            "AND request_digest=%s AND provenance='runtime'", (mission_id, request_digest))
        if existing is None or existing["payload"] != text:
            raise MetadataConflict()
        return existing, False

    # ── Automations ───────────────────────────────────────────────────────

    async def create_automation(self, agent_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        await self._bound()
        unknown = set(fields) - set(_AUTOMATION_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported automation fields: {sorted(unknown)}")
        values = {key: _json_text(value) if key.endswith("_json") else value
                  for key, value in fields.items()}
        _screen(*values.values())
        columns = ["automation_id", "agent_id", "owner_name", "role_name", *values]
        row = await self._one(
            f"INSERT INTO nova_meta.config_agent_automations ({','.join(columns)}) VALUES ("
            + ",".join(["%s"] * len(columns)) + ") RETURNING *",
            (str(uuid4()), agent_id, self.actor.principal, self.actor.active_role,
             *values.values()))
        assert row is not None
        return row

    async def update_automation(
        self, automation_id: str, changes: dict[str, Any], *, expected_revision: int,
    ) -> dict[str, Any]:
        await self._bound()
        unknown = set(changes) - set(_AUTOMATION_FIELDS)
        if unknown or not changes:
            raise ValueError(f"Unsupported automation changes: {sorted(unknown)}")
        values = {key: _json_text(value) if key.endswith("_json") else value
                  for key, value in changes.items()}
        _screen(*values.values())
        row = await self._one(
            "UPDATE nova_meta.config_agent_automations SET "
            + ",".join(f"{field}=%s" for field in values)
            + ",role_name=%s,metadata_revision=metadata_revision+1 WHERE automation_id=%s "
            "AND owner_name=%s AND metadata_revision=%s RETURNING *",
            (*values.values(), self.actor.active_role, automation_id, self.actor.principal,
             _positive(expected_revision)))
        if row is None:
            raise MetadataConflict()
        return row

    async def list_automations(self, agent_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_agent_automations WHERE agent_id=%s AND owner_name=%s "
            "ORDER BY created_at,automation_id", (agent_id, self.actor.principal))

    async def delete_automation(self, automation_id: str) -> bool:
        await self._bound()
        return await self._count(
            "DELETE FROM nova_meta.config_agent_automations WHERE automation_id=%s "
            "AND owner_name=%s", (automation_id, self.actor.principal)) == 1

    async def due_automations(self, now: datetime, *, limit: int = 20) -> list[dict[str, Any]]:
        """The automation worker's global due scan (service actor only)."""
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_agent_automations WHERE enabled AND next_run_at "
            "IS NOT NULL AND next_run_at<=%s ORDER BY next_run_at,automation_id LIMIT %s",
            (now, min(max(limit, 1), 100)))

    async def claim_automation(
        self, automation_id: str, *, seen_next_run_at: datetime, seen_generation: int,
    ) -> int | None:
        """Claim the due occurrence read by the scan; one worker wins its generation.

        The claim compares the generation the scan saw, so a lost worker's
        claim can be retaken explicitly while a racing duplicate cannot.
        """
        await self._bound()
        if type(seen_generation) is not int or seen_generation < 0:
            raise ValueError("A generation is required")
        row = await self._one(
            "UPDATE nova_meta.config_agent_automations SET run_generation=run_generation+1,"
            "last_status='running' WHERE automation_id=%s AND enabled AND next_run_at=%s "
            "AND run_generation=%s RETURNING run_generation",
            (automation_id, seen_next_run_at, seen_generation))
        return int(row["run_generation"]) if row else None

    async def record_automation_run(
        self, automation_id: str, *, generation: int, status: str, thread_id: str | None,
        ran_at: datetime, next_run_at: datetime | None,
    ) -> bool:
        """Advance the schedule, only for the worker holding the current claim."""
        await self._bound()
        if status == "running":
            raise ValueError("A recorded run has finished")
        return await self._count(
            "UPDATE nova_meta.config_agent_automations SET last_run_at=%s,last_status=%s,"
            "last_thread_id=%s,next_run_at=%s WHERE automation_id=%s AND run_generation=%s "
            "AND last_status='running'",
            (ran_at, status[:64], thread_id, next_run_at, automation_id,
             _positive(generation))) == 1

    # ── Memory ────────────────────────────────────────────────────────────

    async def create_memory(
        self, agent_id: str, fields: dict[str, Any], payload: Any,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create the projection, its first revision and evidence together."""
        await self._bound()
        unknown = set(fields) - set(_MEMORY_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported memory fields: {sorted(unknown)}")
        _screen(*fields.values(), payload, evidence)
        memory_id = str(uuid4())
        await self._memory_revision(memory_id, agent_id, 1, payload)
        row = await self._one(
            "INSERT INTO nova_meta.config_agent_memories (memory_id,user_name,agent_id,role_name,"
            "fact_key,fact,source_quote,source_thread_id,revision) VALUES "
            "(%s,%s,%s,%s,%s,%s,%s,%s,1) RETURNING *",
            (memory_id, self.actor.principal, agent_id, self.actor.active_role,
             fields["fact_key"], fields["fact"], fields["source_quote"],
             fields["source_thread_id"]))
        assert row is not None
        for evidence_id, item in (evidence or {}).items():
            await self.connection.execute(
                "INSERT INTO nova_meta.config_agent_memory_evidence (memory_id,evidence_id,"
                "user_name,agent_id,role_name,payload) VALUES (%s,%s,%s,%s,%s,%s)",
                (memory_id, evidence_id, self.actor.principal, agent_id,
                 self.actor.active_role, _json_text(item)))
        return row

    async def _memory_revision(self, memory_id: str, agent_id: str, revision: int,
                               payload: Any) -> None:
        await self.connection.execute(
            "INSERT INTO nova_meta.config_agent_memory_revisions (memory_id,revision,operation_id,"
            "user_name,agent_id,role_name,payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (memory_id, revision, uuid4().hex, self.actor.principal, agent_id,
             self.actor.active_role, _json_text(payload)))

    async def revise_memory(
        self, memory_id: str, changes: dict[str, Any], payload: Any, *, expected_revision: int,
    ) -> dict[str, Any]:
        await self._bound()
        unknown = set(changes) - set(_MEMORY_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported memory changes: {sorted(unknown)}")
        _screen(*changes.values(), payload)
        current = await self._one(
            "SELECT * FROM nova_meta.config_agent_memories WHERE memory_id=%s AND revision=%s "
            "FOR UPDATE", (memory_id, _positive(expected_revision)))
        if current is None:
            raise MetadataConflict()
        await self._memory_revision(memory_id, current["agent_id"], expected_revision + 1, payload)
        assignments = "".join(f",{field}=%s" for field in changes)
        row = await self._one(
            f"UPDATE nova_meta.config_agent_memories SET revision=%s{assignments} "
            "WHERE memory_id=%s AND revision=%s RETURNING *",
            (expected_revision + 1, *changes.values(), memory_id, expected_revision))
        if row is None:
            raise MetadataConflict()
        return row

    async def memory_revisions(self, memory_id: str) -> list[dict[str, Any]]:
        """Every retained operation, oldest first; a fork shows as a repeated revision."""
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_agent_memory_revisions WHERE memory_id=%s "
            "ORDER BY revision,operation_id", (memory_id,))

    # ── Quality revision stores ───────────────────────────────────────────

    async def save_quality_record(
        self, kind: str, record_id: str, agent_id: str, payload: Any, *, expected_revision: int,
    ) -> int:
        """Append the next revision; a fork, stale revision or foreign ID is refused."""
        await self._bound()
        table = QUALITY_TABLES[kind]
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("A revision is required")
        text = _json_text(payload)
        _screen(text)
        row = await self._one(
            f"INSERT INTO nova_meta.{table} (id,revision,operation_id,agent_id,principal,"
            "active_role,security_context_version,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "RETURNING revision",
            (record_id, expected_revision + 1, uuid4().hex, agent_id, self.actor.principal,
             self.actor.active_role, self.actor.security_context_version, text))
        assert row is not None
        return int(row["revision"])

    async def current_quality_records(
        self, kind: str, agent_id: str | None, record_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """The head revision of each record in the caller's scope; a forked head is refused."""
        await self._bound()
        table = QUALITY_TABLES[kind]
        rows = await self._all(
            "SELECT id,revision,payload,branches FROM (SELECT id,revision,payload,"
            "rank() OVER (PARTITION BY id ORDER BY revision DESC) rn,"
            "count(*) OVER (PARTITION BY id,revision) branches "
            f"FROM nova_meta.{table} WHERE security_context_version=%s"
            + (" AND agent_id=%s" if agent_id is not None else "")
            + (" AND id=%s" if record_id else "")
            + ") heads WHERE rn=1 ORDER BY id,revision LIMIT 101",
            (self.actor.security_context_version, *([agent_id] if agent_id is not None else []),
             *([record_id] if record_id else [])))
        if len(rows) > 100 or any(row["branches"] != 1 for row in rows):
            raise MetadataConflict()
        return rows

    # ── Verified-query candidates and rule proposals ──────────────────────

    async def propose_candidate(self, data: dict[str, Any]) -> str | None:
        """Store a pending candidate unless the plan is already pending or adopted."""
        await self._bound()
        plan = _json_text(data["semantic_plan"])
        _screen(data["question"], plan)
        row = await self._one(
            "INSERT INTO nova_meta.config_verified_query_candidates (candidate_id,agent_id,"
            "owner_name,proposed_by,view_id,view_version,question,semantic_plan,plan_sha256,"
            "source_thread_id,source_message_id,status) VALUES "
            "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending') "
            "ON CONFLICT (agent_id,view_id,plan_sha256) WHERE provenance='runtime' "
            "AND status IN ('pending','approved') DO NOTHING RETURNING candidate_id",
            (str(uuid4()), data["agent_id"], data["owner_name"], self.actor.principal,
             data["view_id"], data.get("view_version"), data["question"], plan,
             data["plan_sha256"], data["thread_id"], data["message_id"]))
        return str(row["candidate_id"]) if row else None

    async def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_verified_query_candidates WHERE candidate_id=%s "
            "AND owner_name=%s", (candidate_id, self.actor.principal))

    async def list_candidates(
        self, agent_id: str, status: str | None = None,
    ) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_verified_query_candidates WHERE agent_id=%s "
            "AND owner_name=%s" + (" AND status=%s" if status else "")
            + " ORDER BY created_at DESC,candidate_id LIMIT 200",
            (agent_id, self.actor.principal, *((status,) if status else ())))

    async def decide_candidate(self, candidate_id: str, status: str) -> bool:
        """Approve or reject once, as the agent owner; a second decision loses."""
        await self._bound()
        if status not in {"approved", "rejected"}:
            raise ValueError("A candidate is approved or rejected.")
        return await self._count(
            "UPDATE nova_meta.config_verified_query_candidates SET status=%s,"
            "metadata_revision=metadata_revision+1 WHERE candidate_id=%s AND owner_name=%s "
            "AND status='pending'", (status, candidate_id, self.actor.principal)) == 1

    async def create_rule_proposal(self, data: dict[str, Any]) -> dict[str, Any]:
        await self._bound()
        _screen(data.get("prior_expression"), data.get("proposed_expression"), data.get("details"))
        row = await self._one(
            "INSERT INTO nova_meta.config_agent_rule_proposals (proposal_id,owner_name,agent_id,"
            "role_name,memory_id,semantic_model_id,metric_name,prior_expression,"
            "proposed_expression,prior_fingerprint,proposed_fingerprint,status,proposal_kind,"
            "details) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s) RETURNING *",
            (
                data.get("proposal_id") or str(uuid4()),
                self.actor.principal,
                data["agent_id"],
                self.actor.active_role,
                data["memory_id"],
                data["semantic_model_id"],
                data["metric_name"],
                data["prior_expression"],
                data["proposed_expression"],
                data["prior_fingerprint"],
                data["proposed_fingerprint"],
                data.get("proposal_kind", "metric"),
                _json_text(data.get("details")),
            ),
        )
        assert row is not None
        return row

    async def get_rule_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_agent_rule_proposals WHERE proposal_id=%s "
            "AND owner_name=%s AND role_name=%s",
            (proposal_id, self.actor.principal, self.actor.active_role))

    async def list_rule_proposals(self, semantic_model_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_agent_rule_proposals WHERE owner_name=%s "
            "AND role_name=%s AND semantic_model_id=%s ORDER BY created_at DESC,proposal_id "
            "LIMIT 100", (self.actor.principal, self.actor.active_role, semantic_model_id))

    async def preview_rule_proposal(self, proposal_id: str) -> bool:
        await self._bound()
        return await self._count(
            "UPDATE nova_meta.config_agent_rule_proposals SET previewed_at=clock_timestamp(),"
            "metadata_revision=metadata_revision+1 WHERE proposal_id=%s AND owner_name=%s "
            "AND role_name=%s AND status='pending'",
            (proposal_id, self.actor.principal, self.actor.active_role)) == 1

    async def set_rule_proposal_status(self, proposal_id: str, status: str) -> bool:
        await self._bound()
        if status not in {"approved", "rejected"}:
            raise ValueError("A proposal is approved or rejected.")
        return await self._count(
            "UPDATE nova_meta.config_agent_rule_proposals SET status=%s,"
            "metadata_revision=metadata_revision+1 WHERE proposal_id=%s AND owner_name=%s "
            "AND role_name=%s AND status='pending'",
            (status, proposal_id, self.actor.principal, self.actor.active_role)) == 1

    # ── Deep research and Studio artifacts ────────────────────────────────

    async def create_research_run(self, agent_id: str, thread_id: str, question: str) -> str:
        await self._bound()
        _screen(question)
        row = await self._one(
            "INSERT INTO nova_meta.config_deep_research_runs (run_id,agent_id,owner_name,"
            "thread_id,question,status) VALUES (%s,%s,%s,%s,%s,'planning') RETURNING run_id",
            (str(uuid4()), agent_id, self.actor.principal, thread_id, question))
        assert row is not None
        return str(row["run_id"])

    async def update_research_run(
        self, run_id: str, changes: dict[str, Any], *, expected_revision: int,
    ) -> dict[str, Any]:
        await self._bound()
        unknown = set(changes) - set(_RESEARCH_FIELDS)
        if unknown or not changes:
            raise ValueError(f"Unsupported research changes: {sorted(unknown)}")
        values = {key: _json_text(value) if key.endswith("_json") else value
                  for key, value in changes.items()}
        _screen(*values.values())
        row = await self._one(
            "UPDATE nova_meta.config_deep_research_runs SET "
            + ",".join(f"{field}=%s" for field in values)
            + ",metadata_revision=metadata_revision+1 WHERE run_id=%s AND owner_name=%s "
            "AND metadata_revision=%s RETURNING *",
            (*values.values(), run_id, self.actor.principal, _positive(expected_revision)))
        if row is None:
            raise MetadataConflict()
        return row

    async def get_research_run(
        self, run_id: str, *, for_update: bool = False,
    ) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_deep_research_runs WHERE run_id=%s AND owner_name=%s"
            + (" FOR UPDATE" if for_update else ""),
            (run_id, self.actor.principal),
        )

    async def create_artifact(self, data: dict[str, Any]) -> dict[str, Any]:
        await self._bound()
        _screen(data.get("title"), data.get("sql_text"))
        row = await self._one(
            "INSERT INTO nova_meta.config_studio_artifacts (artifact_id,owner_name,agent_id,"
            "thread_id,title,artifact_type,sql_text,database_name,schema_name,bi_kind,"
            "bi_object_id,bi_dataset_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                data.get("artifact_id") or str(uuid4()),
                self.actor.principal,
                data.get("agent_id"),
                data.get("thread_id"),
                data["title"],
                data.get("artifact_type", "chart"),
                data["sql_text"],
                data.get("database_name"),
                data.get("schema_name"),
                data.get("bi_kind"),
                data.get("bi_object_id"),
                data.get("bi_dataset_id"),
            ),
        )
        assert row is not None
        return row

    async def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_studio_artifacts "
            "WHERE artifact_id=%s AND owner_name=%s",
            (artifact_id, self.actor.principal),
        )

    async def list_artifacts(self) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_studio_artifacts WHERE owner_name=%s "
            "ORDER BY updated_at DESC,artifact_id",
            (self.actor.principal,),
        )

    async def delete_artifact(self, artifact_id: str) -> bool:
        await self._bound()
        return (
            await self._count(
                "DELETE FROM nova_meta.config_studio_artifacts "
                "WHERE artifact_id=%s AND owner_name=%s",
                (artifact_id, self.actor.principal),
            )
            == 1
        )

    async def shared_dashboard(self, dashboard_id: str, owner: str) -> dict[str, Any] | None:
        await self._bound()
        row = await self._one(
            "SELECT nova_meta.shared_dashboard(%s,%s) AS projection", (dashboard_id, owner)
        )
        return row["projection"] if row else None

    async def update_artifact(
        self,
        artifact_id: str,
        changes: dict[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        """A revision CAS in place of the source's timestamp predicate."""
        await self._bound()
        unknown = set(changes) - set(_ARTIFACT_FIELDS)
        if unknown or not changes:
            raise ValueError(f"Unsupported artifact changes: {sorted(unknown)}")
        _screen(*changes.values())
        row = await self._one(
            "UPDATE nova_meta.config_studio_artifacts SET "
            + ",".join(f"{field}=%s" for field in changes)
            + ",metadata_revision=metadata_revision+1 WHERE artifact_id=%s AND owner_name=%s "
            "AND metadata_revision=%s RETURNING *",
            (*changes.values(), artifact_id, self.actor.principal, _positive(expected_revision)),
        )
        if row is None:
            raise MetadataConflict()
        return row

    # ── Legacy and imported-only stores ───────────────────────────────────

    async def legacy_dashboards(self) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_studio_dashboards WHERE owner_name=%s "
            "ORDER BY updated_at,dashboard_id", (self.actor.principal,))

    async def forget_legacy_dashboard(self, dashboard_id: str) -> bool:
        """Remove a legacy dashboard after its verified conversion."""
        await self._bound()
        return await self._count(
            "DELETE FROM nova_meta.config_studio_dashboards WHERE dashboard_id=%s "
            "AND owner_name=%s", (dashboard_id, self.actor.principal)) == 1

    async def delegated_resources(self, thread_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_studio_resources WHERE thread_id=%s "
            "AND session_id=%s AND security_version=%s ORDER BY message_id,attachment_index",
            (thread_id, self.actor.session_id, self.actor.security_context_version))


@asynccontextmanager
async def agent_metadata(
    owner: str | None = None, *, read_only: bool = False,
) -> AsyncIterator[AgentMetadataRepository]:
    # Shared reads retain the reader; the owner name is never a delegated credential.
    actor = (
        current_actor() if read_only or owner is None or owner == "__nova__" else actor_for(owner)
    )
    async with metadata_transaction(actor, read_only=read_only) as (connection, bound):
        yield AgentMetadataRepository(connection, bound)
