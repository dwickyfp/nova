"""Verified-query candidates proposed by answer feedback, adopted only by the owner.

A liked Studio answer that came from ``semantic_query`` carries its question,
its validated SemanticPlan, and the Semantic View version it ran against. That
is exactly what a verified query needs. Feedback never changes a governed view
by itself: it creates a *candidate*. The view owner approves it, which appends
the verified query to a new draft version through the existing, validated
``add_verified_query`` path, and publishing that version still runs the view's
regression check.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.agents.metadata_repository import agent_metadata

CANDIDATES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_VERIFIED_QUERY_CANDIDATES (
    candidate_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    proposed_by VARCHAR(128) NOT NULL,
    view_id VARCHAR(128) NOT NULL,
    view_version INT,
    question VARCHAR(2000) NOT NULL,
    semantic_plan JSON NOT NULL,
    plan_sha256 VARCHAR(64) NOT NULL,
    source_thread_id VARCHAR(64) NOT NULL,
    source_message_id VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    decided_by VARCHAR(128),
    created_at DATETIME NOT NULL,
    decided_at DATETIME
) PRIMARY KEY(candidate_id)
DISTRIBUTED BY HASH(candidate_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

_COLUMNS = (
    "candidate_id, agent_id, owner_name, proposed_by, view_id, view_version, question, "
    "semantic_plan, plan_sha256, source_thread_id, source_message_id, status, decided_by, "
    "created_at, decided_at"
)
MAX_CANDIDATES_PER_MESSAGE = 3


def plan_sha256(plan: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()


def candidates_from_steps(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Semantic query steps of one answer that can become verified queries."""
    found: list[dict[str, Any]] = []
    for step in steps or []:
        if step.get("kind") != "tool" or step.get("name") != "semantic_query":
            continue
        if step.get("status") not in {"done", None}:
            continue
        trace = step.get("trace_detail") or {}
        plan = trace.get("semantic_plan")
        view = trace.get("semantic_view") or {}
        question = str(trace.get("question") or "").strip()
        if not isinstance(plan, dict) or not plan.get("metrics") or not view.get("id"):
            continue
        if not question or trace.get("error"):
            continue
        found.append({
            "view_id": str(view["id"]),
            "view_version": view.get("version"),
            "question": question[:2000],
            "semantic_plan": plan,
        })
    unique = {(item["view_id"], plan_sha256(item["semantic_plan"])): item for item in found}
    return list(unique.values())[:MAX_CANDIDATES_PER_MESSAGE]


def _row(values: list[Any]) -> dict[str, Any]:
    record = dict(zip(_COLUMNS.split(", "), values, strict=True))
    plan = record.get("semantic_plan")
    record["semantic_plan"] = json.loads(plan) if isinstance(plan, str) else plan
    return record


class VerifiedCandidateRepository:
    async def propose(
        self, *, agent_id: str, owner_name: str, proposed_by: str, thread_id: str,
        message_id: str, candidate: dict[str, Any],
    ) -> str | None:
        async with agent_metadata(proposed_by) as store:
            if store.actor.principal != proposed_by:
                raise MetadataPermissionDenied()
            return await store.propose_candidate({
                **candidate, "agent_id": agent_id, "owner_name": owner_name,
                "thread_id": thread_id, "message_id": message_id,
                "plan_sha256": plan_sha256(candidate["semantic_plan"]),
            })

    async def list(self, *, agent_id: str, owner_name: str, status: str | None = None) -> list:
        async with agent_metadata(read_only=True) as store:
            if store.actor.principal != owner_name:
                raise MetadataPermissionDenied()
            return [_row([row.get(key) for key in _COLUMNS.split(", ")])
                    for row in await store.list_candidates(agent_id, status)]

    async def get(self, candidate_id: str, *, owner_name: str) -> dict[str, Any] | None:
        async with agent_metadata(read_only=True) as store:
            if store.actor.principal != owner_name:
                raise MetadataPermissionDenied()
            row = await store.get_candidate(candidate_id)
            return _row([row.get(key) for key in _COLUMNS.split(", ")]) if row else None

    async def decide(self, candidate_id: str, *, status: str, decided_by: str) -> None:
        if status not in {"approved", "rejected"}:
            raise ValueError("A candidate is approved or rejected.")
        async with agent_metadata(decided_by) as store:
            if store.actor.principal != decided_by:
                raise MetadataPermissionDenied()
            if not await store.decide_candidate(candidate_id, status):
                raise MetadataConflict()


verified_candidate_repository = VerifiedCandidateRepository()
