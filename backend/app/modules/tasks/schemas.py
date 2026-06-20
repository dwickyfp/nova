"""Pydantic schemas for the Task Manager API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class TaskCreate(BaseModel):
    """Body for POST /tasks — submit a new task."""

    name: str = Field(..., min_length=1, max_length=256)
    sql: str = Field(..., min_length=1)  # The INSERT/CTAS statement
    database: str = Field(default="", max_length=256)
    schedule_type: Literal["once", "periodic"] = "once"
    interval: str | None = None  # e.g. '1 HOUR', '1 DAY'
    start_time: str | None = None  # datetime string
    properties: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Task responses
# ---------------------------------------------------------------------------

class TaskResponse(BaseModel):
    """A single task from information_schema.tasks."""

    name: str
    database: str
    state: str  # ACTIVE or PAUSE
    schedule: str  # Manual, Every(interval), etc
    sql: str | None = None
    created_at: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)


class TaskListResponse(BaseModel):
    """Response for GET /tasks."""

    tasks: list[TaskResponse]
    count: int


# ---------------------------------------------------------------------------
# Task-run responses
# ---------------------------------------------------------------------------

class TaskRunResponse(BaseModel):
    """A single run from information_schema.task_runs."""

    task_name: str
    create_time: str
    finish_time: str | None = None
    state: str  # PENDING, RUNNING, SUCCESS, FAILED, MERGED, SKIPPED
    error_message: str | None = None
    properties: dict | None = None


class TaskRunListResponse(BaseModel):
    """Response for GET /tasks/{name}/runs."""

    runs: list[TaskRunResponse]
    count: int
