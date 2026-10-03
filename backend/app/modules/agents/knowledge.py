"""Immutable revision contracts for the existing agent-memory repository."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.modules.intelligence.contracts import Confidence, Contract, KnowledgeState, SemanticRef

KNOWLEDGE_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_MEMORY_REVISIONS (
    memory_id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    user_name VARCHAR(128) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(memory_id, revision, operation_id)
DISTRIBUTED BY HASH(memory_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

EVIDENCE_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_MEMORY_EVIDENCE (
    memory_id VARCHAR(64) NOT NULL,
    evidence_id VARCHAR(32) NOT NULL,
    user_name VARCHAR(128) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(memory_id, evidence_id)
DISTRIBUTED BY HASH(memory_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

KNOWLEDGE_BACKFILL = """
INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_MEMORY_REVISIONS
(memory_id,revision,operation_id,user_name,agent_id,role_name,payload,created_at)
SELECT m.memory_id,1,'legacy-private-statement-v1',m.user_name,m.agent_id,m.role_name,
       JSON_OBJECT('memory_id',m.memory_id,'revision',1,'fact',m.fact,
                   'state','HYPOTHESIS','visibility','PRIVATE','authority','user_statement'),
       m.created_at
FROM NOVA_SYSTEM.CONFIG_AGENT_MEMORIES m
LEFT JOIN NOVA_SYSTEM.CONFIG_AGENT_MEMORY_REVISIONS r ON m.memory_id=r.memory_id
WHERE r.memory_id IS NULL
"""


class KnowledgeRevision(Contract):
    memory_id: str
    revision: int = Field(ge=1)
    fact: str = Field(min_length=8, max_length=500)
    state: KnowledgeState = KnowledgeState.HYPOTHESIS
    visibility: Literal["PRIVATE", "DOMAIN"] = "PRIVATE"
    authority: str = Field(default="user_statement", max_length=128)
    semantic: SemanticRef | None = None
    definition: dict = Field(default_factory=dict)
    alternatives: list[str] = Field(default_factory=list, max_length=20)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    reviewed_by: str | None = None
    review_note: str = Field(default="", max_length=2000)
    needs_revalidation: bool = False
    review_operation_id: str | None = Field(default=None, max_length=64)
    review_request_hash: str | None = None
    recorded_at: datetime | None = None
    last_observed_at: datetime | None = None
    confidence: Confidence = Field(
        default_factory=lambda: Confidence(
            dimension="knowledge", method="knowledge-evidence-review-v1"
        )
    )


class KnowledgeReview(Contract):
    operation_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)
    operation: Literal["reject", "resolve", "verify"]
    selected_fact: str | None = Field(default=None, min_length=8, max_length=500)
    note: str = Field(min_length=1, max_length=2000)
    semantic: SemanticRef | None = None
    metric_name: str | None = Field(default=None, max_length=160)
    definition_kind: Literal["metric", "filter", "dimension", "heuristic"] = "metric"
    definition_name: str | None = Field(default=None, min_length=1, max_length=160)
    publish_shared: bool = False


class LearningCallDetail(Contract):
    method_version: Literal["user-statement-extraction-v1"] = "user-statement-extraction-v1"
    phase: Literal["resolve", "request", "response", "error"]
    prompt_tokens: int | None = Field(default=None, ge=0, le=10**12)
    completion_tokens: int | None = Field(default=None, ge=0, le=10**12)
    total_tokens: int | None = Field(default=None, ge=0, le=10**12)
    cost_dollars: None = None
    cost_status: Literal["unavailable"] = "unavailable"


class LearningCallTrace(Contract):
    id: str
    kind: Literal["provider"] = "provider"
    name: Literal["knowledge_consolidation"] = "knowledge_consolidation"
    status: Literal["running", "done", "error"]
    provider_id: str | None = Field(default=None, max_length=64)
    model_name: str | None = Field(default=None, max_length=256)
    trace_detail: LearningCallDetail


def admissible_statement(text: str) -> bool:
    """Structural admission; speech acts are checked in structured extraction output."""
    return bool(text.strip()) and "?" not in text and "://" not in text
