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


# ── Read-only orchestration API (PR 4a) ────────────────────────────────────────
#
# Response models for `/api/v1/task-orchestration`. They are read-only and
# credential-invisible by construction: every field is an id, a name, a state, a
# timing, or schedule metadata. No model here carries a password, token, or
# storage credential — the task *body* is not exposed either, because it can name
# a stage whose credentials Nova injects at execution time.
#
# `error_message` is present because the UI needs to show why a node failed; it
# is redacted by the router through the same helper the worker uses, so an engine
# error that echoed a rewritten `@stage` statement cannot leak credentials.


class GraphRunSummary(BaseModel):
    """The last run of a graph, as shown in the graph list."""

    id: str
    state: GraphRunState
    trigger_type: TriggerType
    overlap_policy: OverlapPolicy
    started_at: datetime | None = None
    finished_at: datetime | None = None


class GraphSummary(BaseModel):
    """One row of `GET /graphs`."""

    graph_id: str
    #: The schedule anchor: a root task with no incoming edge, when there is one.
    root_task: str | None = None
    node_count: int = 0
    schedule_kind: ScheduleKind | None = None
    schedule_expr: str | None = None
    timezone: str | None = None
    overlap_policy: OverlapPolicy = "skip"
    last_run: GraphRunSummary | None = None


class GraphListResponse(BaseModel):
    graphs: list[GraphSummary]
    count: int


class GraphNode(BaseModel):
    """A node of a graph definition, with its last observed run state."""

    name: str
    task_id: str
    schedule_kind: ScheduleKind
    schedule_expr: str | None = None
    timezone: str | None = None
    overlap_policy: OverlapPolicy = "skip"
    when_expr: str | None = None
    created_by: str | None = None
    #: `finalize` marks a finalizer: it runs after the dependency graph, not as a
    #: dependency. The UI must be able to distinguish it.
    is_finalizer: bool = False
    last_state: TaskRunState | None = None


class GraphEdge(BaseModel):
    """A directed edge, with the kind the UI needs to render it correctly."""

    parent_task: str
    child_task: str
    edge_kind: EdgeKind = "after"


class GraphDetailResponse(BaseModel):
    graph_id: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    node_count: int


class GraphRunResponse(BaseModel):
    """A graph-run row, without `wal_marks` (internal watermark bookkeeping)."""

    id: str
    graph_id: str
    trigger_type: TriggerType
    state: GraphRunState
    overlap_policy: OverlapPolicy
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None


class GraphRunListResponse(BaseModel):
    runs: list[GraphRunResponse]
    count: int


class NodeRunResponse(BaseModel):
    """One node-run row, with `error_message` already redacted."""

    id: str
    task_id: str | None = None
    attempt: int = 1
    state: TaskRunState
    delegated: bool = True
    starrocks_query_id: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None


class GraphRunDetailResponse(BaseModel):
    run: GraphRunResponse
    node_runs: list[NodeRunResponse]
