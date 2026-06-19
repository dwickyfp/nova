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
  GET  /loads                  → paginated data load history
  GET  /loads/stats            → load stats summary
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.modules.monitoring.service import monitoring_service

router = APIRouter()


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
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
):
    """Current running queries via SHOW PROCESSLIST."""
    return await monitoring_service.get_active_queries()


@router.post("/queries/kill")
async def kill_query(
    req: KillQueryRequest,
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
):
    """Time-bucketed cost aggregation for chart data."""
    return await monitoring_service.get_cost_aggregation(
        group_by=group_by,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/metrics/fe", response_model=MetricsResponse)
async def get_fe_metrics(
    user: dict = Depends(get_current_user),
):
    """Key FE metrics from information_schema.fe_metrics."""
    result = await monitoring_service.get_fe_metrics_summary()
    return MetricsResponse(metrics=result)


# ── Data Loads ───────────────────────────────────────────────────────


@router.get("/loads", response_model=PaginatedResponse)
async def get_data_loads(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    state: str | None = None,
    db_name: str | None = None,
    load_type: str | None = None,
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
):
    """Aggregate load stats: total, finished, cancelled, loading."""
    result = await monitoring_service.get_load_stats()
    return LoadStatsResponse(**result)
