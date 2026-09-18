"""Pydantic schemas for Phase 9 task orchestration metadata.

Credential-invisible (AGENTS.md): no model here carries a password, token, or
secret. Where an external credential is referenced, only its *name* is stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ScheduleKind = Literal["manual", "interval", "cron"]
OverlapPolicy = Literal["skip", "queue", "allow"]
#: ``after`` is a normal dependency edge (child waits for parent); ``finalize``
#: is a FINALIZE edge (runs after its target completes, not as a dependency).
EdgeKind = Literal["after", "finalize"]
TriggerType = Literal["manual", "schedule", "stream", "reconcile"]
GraphRunState = Literal["pending", "running", "success", "failed", "cancelled"]
TaskRunState = Literal[
    "pending",
    "running",
    "success",
    "failed",
    "skipped",
    "abandoned",
]


class TaskCreate(BaseModel):
    """Request body for creating a task definition."""

    name: str = Field(..., min_length=1, max_length=256)
    database_name: str | None = Field(default=None, max_length=128)
    definition: str | None = None
    schedule_kind: ScheduleKind = "manual"
    schedule_expr: str | None = Field(default=None, max_length=256)
    timezone: str = Field(..., min_length=1, max_length=64)
    when_expr: str | None = None
    overlap_policy: OverlapPolicy = "skip"
    owner_role: str | None = Field(default=None, max_length=128)


class TaskUpdate(BaseModel):
    """Request body for updating a task definition."""

    name: str | None = Field(default=None, min_length=1, max_length=256)
    database_name: str | None = Field(default=None, max_length=128)
    definition: str | None = None
    schedule_kind: ScheduleKind | None = None
    schedule_expr: str | None = Field(default=None, max_length=256)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    when_expr: str | None = None
    overlap_policy: OverlapPolicy | None = None
    owner_role: str | None = Field(default=None, max_length=128)


class Task(TaskCreate):
    """A task definition row from CONFIG_TASKS."""

    id: str
    created_by: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EdgeCreate(BaseModel):
    """Request body for creating a graph edge."""

    parent_task: str = Field(..., min_length=1, max_length=256)
    child_task: str = Field(..., min_length=1, max_length=256)
    edge_kind: EdgeKind = "after"


class Edge(EdgeCreate):
    """An edge row from CONFIG_TASK_EDGES."""

    id: str
    graph_id: str
    created_at: datetime | None = None


class GraphRunCreate(BaseModel):
    """Request body for creating a graph run."""

    graph_id: str = Field(..., min_length=1, max_length=64)
    trigger_type: TriggerType = "manual"
    state: GraphRunState = "pending"
    #: Copied from the graph's root task at enqueue so the worker can enforce
    #: QUEUE versus ALLOW without re-reading the task definition.
    overlap_policy: OverlapPolicy = "skip"


class GraphRun(GraphRunCreate):
    """A graph-run row from CONFIG_TASK_GRAPH_RUNS.

    ``wal_marks`` is metadata only — partition names, IDs, and timestamps.
    """

    id: str
    wal_marks: dict[str, int] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskRunCreate(BaseModel):
    """Request body for creating a node run."""

    graph_run_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    attempt: int = Field(default=1, ge=1)
    state: TaskRunState = "pending"
    delegated: bool = True
    starrocks_query_id: str | None = Field(default=None, max_length=128)
    error_message: str | None = None


class TaskRun(TaskRunCreate):
    """A node-run row from CONFIG_TASK_RUNS."""

    id: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
