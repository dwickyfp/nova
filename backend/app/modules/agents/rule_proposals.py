"""Reviewed promotion of a user's remembered rule into a semantic metric."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.database import db
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.runtime import validate_semantic_model_ir
from app.modules.assistant.skills import contains_credential_shape

PROPOSALS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_RULE_PROPOSALS (
    proposal_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    memory_id VARCHAR(64) NOT NULL,
    semantic_model_id VARCHAR(64) NOT NULL,
    metric_name VARCHAR(160) NOT NULL,
    prior_expression TEXT NOT NULL,
    proposed_expression TEXT NOT NULL,
    prior_fingerprint VARCHAR(128) NOT NULL,
    proposed_fingerprint VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    previewed_at DATETIME NULL,
    reviewed_by VARCHAR(128) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(proposal_id)
DISTRIBUTED BY HASH(proposal_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

VERSIONS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_SEMANTIC_MODEL_VERSIONS (
    version_id VARCHAR(64) NOT NULL,
    semantic_model_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    proposal_id VARCHAR(64) NOT NULL,
    model_fingerprint VARCHAR(128) NOT NULL,
    definition JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(version_id)
DISTRIBUTED BY HASH(version_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

_COLUMNS = (
    "proposal_id", "owner_name", "agent_id", "role_name", "memory_id",
    "semantic_model_id", "metric_name", "prior_expression", "proposed_expression",
    "prior_fingerprint", "proposed_fingerprint", "status", "previewed_at",
    "reviewed_by", "created_at", "updated_at",
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _proposal_row(row: list[Any]) -> dict[str, Any]:
    return dict(zip(_COLUMNS, row, strict=True))


def candidate_definition(
    definition: dict[str, Any], metric_name: str, expression: str
) -> tuple[dict[str, Any], str, str, str, str, str]:
    """Return a validated candidate and both compiled SQL snapshots."""
    expression = expression.strip()
    if not expression or len(expression) > 4000 or contains_credential_shape(expression):
        raise ValueError("The proposed expression is empty, too long, or contains credentials.")
    before = SemanticModelIR.from_ossie(definition)
    metric = before.metric(metric_name)
    if metric is None:
        raise ValueError("The metric does not exist in the governed semantic model.")
    if contains_credential_shape(metric.expression):
        raise ValueError("The existing metric expression contains credential-shaped data.")
    candidate = copy.deepcopy(definition)
    matching = [item for item in candidate.get("metrics") or [] if item.get("name") == metric_name]
    if len(matching) != 1:
        raise ValueError("The metric definition is ambiguous.")
    matching[0]["expression"] = expression
    after = SemanticModelIR.from_ossie(candidate)
    validation = validate_semantic_model_ir(after)
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))
    plan = SemanticPlan(metrics=(metric_name,))
    old_sql = SemanticCompiler().compile(before, plan).sql
    new_sql = SemanticCompiler().compile(after, plan).sql
    if before.fingerprint == after.fingerprint:
        raise ValueError("The proposed expression does not change the semantic model.")
    return candidate, metric.expression, before.fingerprint, after.fingerprint, old_sql, new_sql


class RuleProposalRepository:
    async def ensure_schema(self) -> None:
        await db.execute_system(PROPOSALS_DDL)
        await db.execute_system(VERSIONS_DDL)

    async def create(self, fields: dict[str, Any]) -> dict[str, Any]:
        proposal_id = str(uuid4())
        now = _now()
        values = [
            proposal_id, fields["owner_name"], fields["agent_id"], fields["role_name"],
            fields["memory_id"], fields["semantic_model_id"], fields["metric_name"],
            fields["prior_expression"], fields["proposed_expression"],
            fields["prior_fingerprint"], fields["proposed_fingerprint"], "pending",
            None, None, now, now,
        ]
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_RULE_PROPOSALS ("
            + ", ".join(_COLUMNS)
            + ") VALUES (" + ", ".join(["%s"] * len(_COLUMNS)) + ")",
            values,
        )
        return dict(zip(_COLUMNS, values, strict=True))

    async def get(self, proposal_id: str, *, owner_name: str, role_name: str) -> dict | None:
        result = await db.execute_system(
            "SELECT " + ", ".join(_COLUMNS)
            + " FROM NOVA_SYSTEM.CONFIG_AGENT_RULE_PROPOSALS "
            "WHERE proposal_id = %s AND owner_name = %s AND role_name = %s",
            [proposal_id, owner_name, role_name],
        )
        return _proposal_row(result["rows"][0]) if result["rows"] else None

    async def list(
        self, *, owner_name: str, role_name: str, semantic_model_id: str
    ) -> list[dict]:
        result = await db.execute_system(
            "SELECT " + ", ".join(_COLUMNS)
            + " FROM NOVA_SYSTEM.CONFIG_AGENT_RULE_PROPOSALS "
            "WHERE owner_name = %s AND role_name = %s AND semantic_model_id = %s "
            "ORDER BY created_at DESC LIMIT 100",
            [owner_name, role_name, semantic_model_id],
        )
        return [_proposal_row(row) for row in result["rows"]]

    async def mark_previewed(self, proposal_id: str, *, owner_name: str, role_name: str) -> None:
        now = _now()
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RULE_PROPOSALS "
            "SET previewed_at = %s, updated_at = %s "
            "WHERE proposal_id = %s AND owner_name = %s AND role_name = %s AND status = 'pending'",
            [now, now, proposal_id, owner_name, role_name],
        )

    async def set_status(
        self, proposal_id: str, *, owner_name: str, role_name: str, status: str
    ) -> None:
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RULE_PROPOSALS "
            "SET status = %s, reviewed_by = %s, updated_at = %s "
            "WHERE proposal_id = %s AND owner_name = %s AND role_name = %s AND status = 'pending'",
            [status, owner_name, _now(), proposal_id, owner_name, role_name],
        )

    async def save_version(
        self, *, model_id: str, owner_name: str, proposal_id: str,
        fingerprint: str, definition: dict[str, Any]
    ) -> str:
        version_id = str(uuid4())
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_SEMANTIC_MODEL_VERSIONS "
            "(version_id, semantic_model_id, owner_name, proposal_id, "
            "model_fingerprint, definition, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [version_id, model_id, owner_name, proposal_id, fingerprint,
             json.dumps(definition, ensure_ascii=False), _now()],
        )
        return version_id


rule_proposal_repository = RuleProposalRepository()
