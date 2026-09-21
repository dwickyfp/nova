"""Monitoring router — API endpoints for monitoring dashboards.

Endpoints under /api/v1/monitoring:
  GET  /queries/history        → paginated query execution history
  GET  /queries/stats          → aggregate query stats (cards)
  GET  /audit                  → paginated audit trail (all event types)
  GET  /queries/active         → live processlist
  POST /queries/kill           → kill a running query
  GET  /tasks/runs             → paginated task run history
  GET  /tasks                  → list defined tasks
  GET  /cost/history           → paginated query cost history
  GET  /cost/aggregation       → time-bucketed cost aggregation (chart)
  GET  /metrics/fe             → FE metrics summary
  GET  /runtime-health         → Redis, scheduler, and worker liveness
  GET  /loads                  → paginated data load history
  GET  /loads/stats            → load stats summary
  GET  /alerts                 → production alert rules evaluated live
  GET  /readiness              → read-only production readiness audit

Authorization: every endpoint is gated with ``require_role``. Reads use
``READ_ROLES``; ``POST /queries/kill`` uses the (identical) ``KILL_ROLES`` set
kept separate so tightening the kill surface later is a one-line change. See the
constants below for why the system-pool reads still need a backend gate.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.deps import require_role
from app.modules.monitoring.runtime_health import RuntimeHealthResponse
from app.modules.monitoring.service import monitoring_service
from app.modules.users.router import ADMIN_ROLES as ADMIN_ROLES

router = APIRouter()

# Monitoring reads run through the system pool (``db.execute_system``), so
# StarRocks' own grant filter never sees the caller and every row is readable
# regardless of privilege. The backend is therefore the only access boundary on
# this surface.
#
# Roles permitted to read monitoring data. Reused, not re-declared, from
# ``modules/users/router.py`` (same list ``/users`` and task orchestration use):
# ``ACCOUNTADMIN`` plus the StarRocks user/security administration roles. Because
# the reads cross every user's audit trail, cost history and processlist, a
# non-admin holding only query grants must not reach them.
READ_ROLES = ADMIN_ROLES

# Roles permitted to kill a running query. Narrower than ``READ_ROLES`` on
# purpose: ``kill_query`` runs ``KILL QUERY`` on the root pool, so it terminates
# *another* user's query and is a cross-tenant denial-of-service primitive. Only
# the cluster/security administrators may hold it — the same role set as the
# rest of the admin surface.
KILL_ROLES = ADMIN_ROLES

# Built once so routes use module-level dependencies instead of calling
# ``Depends(...)`` in argument defaults (ruff B008).
require_read = Depends(require_role(*READ_ROLES))
require_kill = Depends(require_role(*KILL_ROLES))


# ── Response Models ──────────────────────────────────────────────────


class PaginatedResponse(BaseModel):
    items: list[dict]
    total: int


class QueryStatsResponse(BaseModel):
    total: int
    avg_duration_ms: float
    error_count: int
    success_count: int
    error_rate: float


class KillQueryRequest(BaseModel):
    connection_id: int


class MetricsResponse(BaseModel):
    metrics: dict


class LoadStatsResponse(BaseModel):
    total: int
    finished: int
    cancelled: int
    loading: int
    other: int


# ── Query History ────────────────────────────────────────────────────


@router.get("/queries/history", response_model=PaginatedResponse)
async def get_query_history(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user_name: str | None = None,
    status: str | None = None,
    database_name: str | None = None,
    min_duration_ms: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    search: str | None = None,
    user: dict = require_read,
):
    """Paginated query execution history (event_type='query')."""
    result = await monitoring_service.get_query_history(
        limit=limit,
        offset=offset,
        user_name=user_name,
        status=status,
        database_name=database_name,
        min_duration_ms=min_duration_ms,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )
    return PaginatedResponse(**result)


@router.get("/queries/stats", response_model=QueryStatsResponse)
async def get_query_stats(
    date_from: str | None = None,
    date_to: str | None = None,
    user: dict = require_read,
):
    """Aggregate query stats: total, avg duration, error rate."""
    result = await monitoring_service.get_query_history_stats(
        date_from=date_from,
        date_to=date_to,
    )
    return QueryStatsResponse(**result)


# ── Audit Trail ──────────────────────────────────────────────────────


@router.get("/audit", response_model=PaginatedResponse)
async def get_audit_trail(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event_type: str | None = None,
    user_name: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    user: dict = require_read,
):
    """Paginated audit trail — all event types from AUDIT_LOG."""
    result = await monitoring_service.get_audit_trail(
        limit=limit,
        offset=offset,
        event_type=event_type,
        user_name=user_name,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(**result)


# ── Active Queries ───────────────────────────────────────────────────


@router.get("/queries/active")
async def get_active_queries(
    user: dict = require_read,
):
    """Current running queries via SHOW PROCESSLIST."""
    return await monitoring_service.get_active_queries()


@router.post("/queries/kill")
async def kill_query(
    req: KillQueryRequest,
    user: dict = require_kill,
):
    """Kill a running query by connection ID."""
    success = await monitoring_service.kill_query(req.connection_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to kill query")
    return {"success": True}


# ── Tasks ────────────────────────────────────────────────────────────


@router.get("/tasks/runs", response_model=PaginatedResponse)
async def get_task_runs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    task_name: str | None = None,
    state: str | None = None,
    user: dict = require_read,
):
    """Paginated task run history from information_schema.task_runs."""
    result = await monitoring_service.get_task_runs(
        limit=limit,
        offset=offset,
        task_name=task_name,
        state=state,
    )
    return PaginatedResponse(**result)


@router.get("/tasks")
async def get_tasks(
    user: dict = require_read,
):
    """List defined async tasks from information_schema.tasks."""
    return await monitoring_service.get_tasks()


# ── Query Cost ───────────────────────────────────────────────────────


@router.get("/cost/history", response_model=PaginatedResponse)
async def get_cost_history(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user_name: str | None = None,
    database_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    user: dict = require_read,
):
    """Paginated query cost history (duration, rows affected)."""
    result = await monitoring_service.get_query_cost_history(
        limit=limit,
        offset=offset,
        user_name=user_name,
        database_name=database_name,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(**result)


@router.get("/cost/aggregation")
async def get_cost_aggregation(
    group_by: str = "hour",
    date_from: str | None = None,
    date_to: str | None = None,
    user: dict = require_read,
):
    """Time-bucketed cost aggregation for chart data."""
    return await monitoring_service.get_cost_aggregation(
        group_by=group_by,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/metrics/fe", response_model=MetricsResponse)
async def get_fe_metrics(
    user: dict = require_read,
):
    """Key FE metrics from information_schema.fe_metrics."""
    result = await monitoring_service.get_fe_metrics_summary()
    return MetricsResponse(metrics=result)


@router.get("/runtime-health", response_model=RuntimeHealthResponse)
async def get_runtime_health(
    user: dict = require_read,
) -> RuntimeHealthResponse:
    """Live health of Redis and Nova's standalone orchestration processes."""
    return await monitoring_service.get_runtime_health()


# ── Data Loads ───────────────────────────────────────────────────────


@router.get("/loads", response_model=PaginatedResponse)
async def get_data_loads(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    state: str | None = None,
    db_name: str | None = None,
    load_type: str | None = None,
    user: dict = require_read,
):
    """Paginated data load history from information_schema.loads."""
    result = await monitoring_service.get_data_loads(
        limit=limit,
        offset=offset,
        state=state,
        db_name=db_name,
        load_type=load_type,
    )
    return PaginatedResponse(**result)


@router.get("/loads/stats", response_model=LoadStatsResponse)
async def get_load_stats(
    user: dict = require_read,
):
    """Aggregate load stats: total, finished, cancelled, loading."""
    result = await monitoring_service.get_load_stats()
    return LoadStatsResponse(**result)


# ── Alerts ───────────────────────────────────────────────────────────


@router.get("/alerts")
async def get_alerts(
    user: dict = require_read,
):
    """Evaluate production alert rules against live engine state.

    Each rule reports ``firing`` / ``ok`` / ``unknown`` plus a severity, so the
    UI can distinguish "healthy" from "the engine did not expose this metric".
    """
    return await monitoring_service.get_alerts()


# ── Production readiness audit ───────────────────────────────────────


@router.get("/readiness")
async def get_readiness(
    user: dict = require_read,
):
    """Read-only production readiness audit.

    Returns per-category findings with one of ``PASS`` / ``NEEDS_CHANGE`` /
    ``BLOCKED`` / ``REVIEW`` plus an overall verdict. Nothing is mutated: the
    audit only reads engine state, and external gates it cannot prove are
    reported as ``REVIEW`` rather than passed silently.
    """
    return await monitoring_service.get_readiness()
