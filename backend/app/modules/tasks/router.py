"""Task Manager API router — submit, schedule, suspend, resume, drop tasks.

Endpoints:
  GET    /tasks              → list all tasks
  GET    /tasks/{name}       → get task detail
  POST   /tasks              → create (submit) a task
  PATCH  /tasks/{name}/suspend → suspend a periodic task
  PATCH  /tasks/{name}/resume  → resume a suspended task
  DELETE /tasks/{name}       → drop a task (query: force=false)
  GET    /tasks/{name}/runs  → list task run history
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import get_current_user

from .schemas import (
    TaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskRunListResponse,
)
from .service import task_service

router = APIRouter()
log = logging.getLogger(__name__)


# ── List tasks ─────────────────────────────────────────────────


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    _user: dict = Depends(get_current_user),
):
    """List all registered tasks from information_schema.tasks."""
    tasks = await task_service.list_tasks()
    return TaskListResponse(tasks=tasks, count=len(tasks))


# ── Get task detail ────────────────────────────────────────────


@router.get("/{name}", response_model=TaskResponse)
async def get_task(
    name: str,
    _user: dict = Depends(get_current_user),
):
    """Get detail for a single task by name."""
    task = await task_service.get_task(name)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{name}' not found")
    return task


# ── Create task ────────────────────────────────────────────────


@router.post("", status_code=201)
async def create_task(
    body: TaskCreate,
    _user: dict = Depends(get_current_user),
):
    """Submit a new task (one-shot or periodic)."""
    try:
        result = await task_service.create_task(body.model_dump())
    except Exception as exc:
        log.error("Create task failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc))
    return result


# ── Suspend task ───────────────────────────────────────────────


@router.patch("/{name}/suspend")
async def suspend_task(
    name: str,
    _user: dict = Depends(get_current_user),
):
    """Suspend (pause) a periodic task."""
    try:
        return await task_service.suspend_task(name)
    except Exception as exc:
        log.error("Suspend task '%s' failed: %s", name, exc, exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc))


# ── Resume task ────────────────────────────────────────────────


@router.patch("/{name}/resume")
async def resume_task(
    name: str,
    _user: dict = Depends(get_current_user),
):
    """Resume a suspended periodic task."""
    try:
        return await task_service.resume_task(name)
    except Exception as exc:
        log.error("Resume task '%s' failed: %s", name, exc, exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc))


# ── Drop task ──────────────────────────────────────────────────


@router.delete("/{name}")
async def drop_task(
    name: str,
    force: bool = Query(False),
    _user: dict = Depends(get_current_user),
):
    """Drop a task. Pass ``force=true`` for DROP TASK IF EXISTS … FORCE."""
    try:
        return await task_service.drop_task(name, force=force)
    except Exception as exc:
        log.error("Drop task '%s' failed: %s", name, exc, exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc))


# ── List task runs ─────────────────────────────────────────────


@router.get("/{name}/runs", response_model=TaskRunListResponse)
async def list_task_runs(
    name: str,
    _user: dict = Depends(get_current_user),
):
    """List execution history for a task from information_schema.task_runs."""
    runs = await task_service.list_task_runs(name)
    return TaskRunListResponse(runs=runs, count=len(runs))
