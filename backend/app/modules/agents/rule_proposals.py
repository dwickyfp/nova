"""Reviewed promotion of a user's remembered rule into a semantic metric."""

from __future__ import annotations

import copy
import json
from typing import Any

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.agents.metadata_repository import AgentMetadataRepository, agent_metadata
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
    ,proposal_kind VARCHAR(32) DEFAULT 'metric'
    ,details JSON
) PRIMARY KEY(proposal_id)
DISTRIBUTED BY HASH(proposal_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

_COLUMNS = (
    "proposal_id", "owner_name", "agent_id", "role_name", "memory_id",
    "semantic_model_id", "metric_name", "prior_expression", "proposed_expression",
    "prior_fingerprint", "proposed_fingerprint", "status", "previewed_at",
    "reviewed_by", "created_at", "updated_at", "proposal_kind", "details",
)


def _proposal_row(row: list[Any]) -> dict[str, Any]:
    result = dict(zip(_COLUMNS, [*row, *(["metric", None] if len(row) == 16 else [])], strict=True))
    if isinstance(result.get("details"), str):
        result["details"] = json.loads(result["details"])
    return result


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
    @staticmethod
    def _scope(store: AgentMetadataRepository, owner: str, role: str) -> None:
        if store.actor.principal != owner or store.actor.active_role != role:
            raise MetadataPermissionDenied()

    async def create(self, fields: dict[str, Any]) -> dict[str, Any]:
        async with agent_metadata(fields["owner_name"]) as store:
            self._scope(store, fields["owner_name"], fields["role_name"])
            row = await store.create_rule_proposal(fields)
            return _proposal_row([row.get(key) for key in _COLUMNS])

    async def get(self, proposal_id: str, *, owner_name: str, role_name: str) -> dict | None:
        async with agent_metadata(read_only=True) as store:
            self._scope(store, owner_name, role_name)
            row = await store.get_rule_proposal(proposal_id)
            return _proposal_row([row.get(key) for key in _COLUMNS]) if row else None

    async def list(
        self, *, owner_name: str, role_name: str, semantic_model_id: str,
    ) -> list[dict]:
        async with agent_metadata(read_only=True) as store:
            self._scope(store, owner_name, role_name)
            return [
                _proposal_row([row.get(key) for key in _COLUMNS])
                for row in await store.list_rule_proposals(semantic_model_id)
            ]

    async def mark_previewed(self, proposal_id: str, *, owner_name: str, role_name: str) -> None:
        async with agent_metadata(owner_name) as store:
            self._scope(store, owner_name, role_name)
            if not await store.preview_rule_proposal(proposal_id):
                raise MetadataConflict()

    async def set_status(
        self, proposal_id: str, *, owner_name: str, role_name: str, status: str,
    ) -> None:
        async with agent_metadata(owner_name) as store:
            self._scope(store, owner_name, role_name)
            if not await store.set_rule_proposal_status(proposal_id, status):
                raise MetadataConflict()


rule_proposal_repository = RuleProposalRepository()
