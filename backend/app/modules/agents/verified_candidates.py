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
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.database import db

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


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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
    async def ensure_schema(self) -> None:
        await db.execute_system(CANDIDATES_DDL)

    async def propose(
        self,
        *,
        agent_id: str,
        owner_name: str,
        proposed_by: str,
        thread_id: str,
        message_id: str,
        candidate: dict[str, Any],
    ) -> str | None:
        """Store a pending candidate unless the same plan is already pending or adopted."""
        await self.ensure_schema()
        digest = plan_sha256(candidate["semantic_plan"])
        existing = await db.execute_system(
            "SELECT candidate_id FROM NOVA_SYSTEM.CONFIG_VERIFIED_QUERY_CANDIDATES "
            "WHERE agent_id = %s AND view_id = %s AND plan_sha256 = %s "
            "AND status IN ('pending', 'approved')",
            [agent_id, candidate["view_id"], digest],
        )
        if existing.get("rows"):
            return None
        candidate_id = str(uuid4())
        await db.execute_system(
            f"INSERT INTO NOVA_SYSTEM.CONFIG_VERIFIED_QUERY_CANDIDATES ({_COLUMNS}) VALUES ("
            + ", ".join(["%s"] * 15) + ")",
            [
                candidate_id, agent_id, owner_name, proposed_by, candidate["view_id"],
                candidate.get("view_version"), candidate["question"],
                json.dumps(candidate["semantic_plan"], default=str), digest,
                thread_id, message_id, "pending", None, _now(), None,
            ],
        )
        return candidate_id

    async def list(self, *, agent_id: str, owner_name: str, status: str | None = None) -> list:
        await self.ensure_schema()
        sql = (
            f"SELECT {_COLUMNS} FROM NOVA_SYSTEM.CONFIG_VERIFIED_QUERY_CANDIDATES "
            "WHERE agent_id = %s AND owner_name = %s"
        )
        params: list[Any] = [agent_id, owner_name]
        if status:
            sql += " AND status = %s"
            params.append(status)
        result = await db.execute_system(sql + " ORDER BY created_at DESC LIMIT 200", params)
        return [_row(row) for row in result.get("rows") or []]

    async def get(self, candidate_id: str, *, owner_name: str) -> dict[str, Any] | None:
        await self.ensure_schema()
        result = await db.execute_system(
            f"SELECT {_COLUMNS} FROM NOVA_SYSTEM.CONFIG_VERIFIED_QUERY_CANDIDATES "
            "WHERE candidate_id = %s AND owner_name = %s",
            [candidate_id, owner_name],
        )
        rows = result.get("rows") or []
        return _row(rows[0]) if rows else None

    async def decide(self, candidate_id: str, *, status: str, decided_by: str) -> None:
        if status not in {"approved", "rejected"}:
            raise ValueError("A candidate is approved or rejected.")
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_VERIFIED_QUERY_CANDIDATES "
            "SET status = %s, decided_by = %s, decided_at = %s "
            "WHERE candidate_id = %s AND status = 'pending'",
            [status, decided_by, _now(), candidate_id],
        )


verified_candidate_repository = VerifiedCandidateRepository()
