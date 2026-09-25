"""Migration API. Source and target operations are dispatched to nova-worker."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.common.responses import SanitizingJSONResponse
from app.core.config import settings
from app.core.deps import get_current_user

from .jobs import (
    MigrationJobFailed,
    MigrationJobUnavailable,
    migration_job_repo,
    public_status,
    session_fingerprint,
    submit_job,
    wait_for_job,
)
from .schemas import (
    DatabasesRequest,
    DatabasesResponse,
    DryRunRequest,
    DryRunResponse,
    EngineStatusResponse,
    EnumerateRequest,
    EnumerateResponse,
    ExecuteBatchRequest,
    ExecuteRequest,
    MigrationJobAccepted,
    MigrationJobStatus,
    PlanRequest,
    PlanResponse,
    PreflightRequest,
    PreflightResponse,
    SourceConnectionListResponse,
    SourceConnectionRequest,
    SourceConnectionResponse,
    SourceConnectionTestRequest,
    SourceConnectionTestResponse,
)
from .service import migration_service

router = APIRouter()
logger = logging.getLogger(__name__)

CurrentUser = Annotated[dict, Depends(get_current_user)]


class _EngineRequest(BaseModel):
    source: str = ""


async def _worker_read(operation: str, body: BaseModel, user: dict) -> dict:
    accepted = await submit_job(operation, body, user)
    try:
        return await wait_for_job(accepted.job_id)
    except MigrationJobUnavailable as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except MigrationJobFailed as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _check_execute_gate(confirmation: str, expected: str, acknowledged: bool) -> None:
    if not settings.MIGRATION_EXECUTE_ENABLED:
        raise HTTPException(status_code=403, detail="Migration execute is disabled")
    if not acknowledged:
        raise HTTPException(status_code=422, detail="Acknowledge the migration omissions")
    if settings.MIGRATION_EXECUTE_REQUIRE_CONFIRMATION and confirmation != expected:
        raise HTTPException(status_code=422, detail="Migration confirmation does not match")


@router.get(
    "/capabilities",
    response_class=SanitizingJSONResponse,
)
async def capabilities(user: CurrentUser):
    """Expose the operator's execute gate to the client."""
    return {
        "phase": "11",
        "version": "v1",
        "phases": ["assess", "dry_run", "plan", "preflight", "execute"],
        # True because an execute endpoint now exists — but it is **gated**: it
        # refuses unless the operator enabled it (issue #7). ``write_operations``
        # mirrors that gate so a client shows the right affordance.
        "write_operations": settings.MIGRATION_EXECUTE_ENABLED,
        "execute_available": settings.MIGRATION_EXECUTE_ENABLED,
        "execute_gate": {"issue": "#7", "name": "backup/restore"},
        "mv_ddl_surface": "SHOW CREATE MATERIALIZED VIEW",
        "source_required": True,
        "execute_require_confirmation": settings.MIGRATION_EXECUTE_REQUIRE_CONFIRMATION,
    }


@router.get(
    "/engine",
    response_model=EngineStatusResponse,
    response_class=SanitizingJSONResponse,
)
async def engine_status(user: CurrentUser):
    """Check the operator-provided binary in the worker environment."""
    return await _worker_read("engine", _EngineRequest(), user)


@router.get(
    "/sources",
    response_model=SourceConnectionListResponse,
    response_class=SanitizingJSONResponse,
)
async def list_sources(user: CurrentUser):
    """List registered source connections (names, never secrets)."""
    return await migration_service.list_sources()


@router.post(
    "/sources/test",
    response_model=SourceConnectionTestResponse,
    response_class=SanitizingJSONResponse,
)
async def test_source(body: SourceConnectionTestRequest, user: CurrentUser):
    """Probe an unsaved source in nova-worker without persisting it."""
    return await _worker_read("test_source", body, user)


@router.post(
    "/sources",
    response_model=SourceConnectionResponse,
    status_code=201,
    response_class=SanitizingJSONResponse,
)
async def create_source(body: SourceConnectionRequest, user: CurrentUser):
    """Register a source StarRocks cluster by address.

    The password is never accepted here: ``secret_ref`` names a credential in
    the configured secret store and is resolved server-side at call time.
    """
    existing = await migration_service.list_sources()
    if any(connection.name == body.name for connection in existing.connections):
        raise HTTPException(
            status_code=400, detail=f"Source connection '{body.name}' already exists"
        )
    return await migration_service.create_source(
        name=body.name,
        host=body.host,
        port=body.port,
        username=body.username,
        secret_ref=body.secret_ref,
        comment=body.comment,
        username_actor=user["username"],
    )


@router.post(
    "/databases",
    response_model=DatabasesResponse,
    response_class=SanitizingJSONResponse,
)
async def databases(body: DatabasesRequest, user: CurrentUser):
    return await _worker_read("databases", body, user)


@router.post(
    "/enumerate",
    response_model=EnumerateResponse,
    response_class=SanitizingJSONResponse,
)
async def enumerate_objects(body: EnumerateRequest, user: CurrentUser):
    """List objects from the registered source in nova-worker."""
    return await _worker_read("enumerate", body, user)


@router.post(
    "/dry-run",
    response_model=DryRunResponse,
    response_class=SanitizingJSONResponse,
)
async def dry_run(body: DryRunRequest, user: CurrentUser):
    """Classify source objects without applying them."""
    return await _worker_read("dry_run", body, user)


@router.post(
    "/plan",
    response_model=PlanResponse,
    response_class=SanitizingJSONResponse,
)
async def plan(body: PlanRequest, user: CurrentUser):
    """Return a dependency-ordered plan and blocked objects."""
    return await _worker_read("plan", body, user)


@router.post(
    "/preflight",
    response_model=PreflightResponse,
    response_class=SanitizingJSONResponse,
)
async def preflight(body: PreflightRequest, user: CurrentUser):
    """Check the caller's grants and transfer storage without applying DDL."""
    return await _worker_read("preflight", body, user)


@router.post(
    "/execute",
    response_model=MigrationJobAccepted,
    status_code=202,
    response_class=SanitizingJSONResponse,
)
async def execute(body: ExecuteRequest, user: CurrentUser):
    _check_execute_gate(
        body.confirmation, body.target_database or body.database, body.acknowledge_omissions
    )
    return await submit_job("execute", body, user)


@router.post(
    "/execute-batch",
    response_model=MigrationJobAccepted,
    status_code=202,
    response_class=SanitizingJSONResponse,
)
async def execute_batch(body: ExecuteBatchRequest, user: CurrentUser):
    _check_execute_gate(
        body.confirmation,
        f"MIGRATE {len(body.databases)} DATABASES",
        body.acknowledge_omissions,
    )
    return await submit_job("execute_batch", body, user)


@router.get(
    "/jobs/{job_id}",
    response_model=MigrationJobStatus,
    response_class=SanitizingJSONResponse,
)
async def job_status(job_id: str, user: CurrentUser):
    try:
        job = await migration_job_repo.get(job_id)
    except Exception as exc:
        logger.warning(
            "migration job status read failed exception=%s.%s",
            type(exc).__module__,
            type(exc).__qualname__,
        )
        raise HTTPException(
            status_code=503, detail="Migration job status is temporarily unavailable"
        ) from None
    if job is None or job["actor"] != user["username"]:
        raise HTTPException(status_code=404, detail="Migration job not found")
    if job["active_role"] != user.get("active_role"):
        raise HTTPException(status_code=404, detail="Migration job not found")
    original_session = job["session_fingerprint"] == session_fingerprint(user["session_id"])
    if original_session:
        if int(job["security_context_version"]) != int(user.get("security_context_version") or 1):
            raise HTTPException(status_code=404, detail="Migration job not found")
    elif job["active_role"] and job["active_role"] not in (
        user.get("assigned_roles") or user.get("roles") or []
    ):
        raise HTTPException(status_code=404, detail="Migration job not found")
    return public_status(job)
