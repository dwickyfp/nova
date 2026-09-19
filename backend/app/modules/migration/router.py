"""Migration Connector API router — Phase 11 v1 (Assessment + Dry-run).

Endpoints:
  GET  /migration/engine               → operator binary availability (no execution)
  GET  /migration/sources              → registered source clusters (address only)
  POST /migration/sources              → register a source cluster (address + secret_ref)
  POST /migration/enumerate            → list objects on a registered source
  POST /migration/dry-run              → per-object verdict (migratable/lossy/skipped)
  GET  /migration/capabilities         → the surfaces this v1 implements

**There is no Execute endpoint and no execute path behind any flag.** Cutover is
gated on issue #7 (backup/restore) and is out of scope here. A negative test
asserts the absence.

Every response class is ``SanitizingJSONResponse`` so an engine statement that
somehow escaped the service's redaction is stripped at the boundary
(AGENTS.md §2).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.common.responses import SanitizingJSONResponse
from app.core.config import settings
from app.core.deps import get_current_user

from .data_mover import DataMovementError
from .schemas import (
    DryRunRequest,
    DryRunResponse,
    EngineStatusResponse,
    EnumerateRequest,
    EnumerateResponse,
    ExecuteRequest,
    ExecuteResponse,
    PlanRequest,
    PlanResponse,
    PreflightRequest,
    PreflightResponse,
    SourceConnectionListResponse,
    SourceConnectionRequest,
    SourceConnectionResponse,
)
from .service import (
    MigrationExecuteConfirmationError,
    MigrationExecuteGateError,
    MigrationPreflightError,
    migration_service,
)
from .source import SourceConnectionError

router = APIRouter()

CurrentUser = Annotated[dict, Depends(get_current_user)]


@router.get(
    "/capabilities",
    response_class=SanitizingJSONResponse,
)
async def capabilities(user: CurrentUser):
    """Describe what v1 implements, and what it deliberately does not.

    Returning this from the server keeps the client from inventing an Execute
    action: the only phases listed are ``assess`` and ``dry_run``.
    """
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
    """Report whether the operator-provided ``starrocks-cluster-sync`` exists.

    Nova never bundles the binary; a missing binary is reported, not raised, so
    assessment and dry-run still work.
    """
    return await migration_service.engine_status()


@router.get(
    "/sources",
    response_model=SourceConnectionListResponse,
    response_class=SanitizingJSONResponse,
)
async def list_sources(user: CurrentUser):
    """List registered source connections (names, never secrets)."""
    return await migration_service.list_sources()


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
    "/enumerate",
    response_model=EnumerateResponse,
    response_class=SanitizingJSONResponse,
)
async def enumerate_objects(body: EnumerateRequest, user: CurrentUser):
    """Enumerate tables/views/MVs/functions/tasks/pipes/policies of a database.

    Reads the **registered source** (``body.source``). MVs are read from
    ``information_schema.materialized_views``.
    """
    try:
        return await migration_service.enumerate(body.source, body.database)
    except SourceConnectionError as exc:
        raise _source_http_error(exc) from exc


@router.post(
    "/dry-run",
    response_model=DryRunResponse,
    response_class=SanitizingJSONResponse,
)
async def dry_run(body: DryRunRequest, user: CurrentUser):
    """Classify each selected object on the registered source.

    Verdicts are ``migratable`` / ``lossy`` / ``skipped``. Read-only. This
    endpoint never materialises or executes a cutover.
    """
    try:
        return await migration_service.dry_run(
            body.source, body.database, body.objects, actor=user["username"]
        )
    except SourceConnectionError as exc:
        raise _source_http_error(exc) from exc


@router.post(
    "/plan",
    response_model=PlanResponse,
    response_class=SanitizingJSONResponse,
)
async def plan(body: PlanRequest, user: CurrentUser):
    """Build a dependency-ordered apply plan. Read-only — executes nothing.

    The plan lists the statements a cutover would run, in order, plus the objects
    that cannot be executed. There is no apply endpoint: execution is gated on
    #7 (backup/restore). This endpoint exists so an operator can inspect and
    review exactly what a future execute would do.
    """
    try:
        return await migration_service.plan(
            body.source,
            body.database,
            target_database=body.target_database,
            objects=body.objects,
            create_database=body.create_database,
            actor=user["username"],
        )
    except SourceConnectionError as exc:
        raise _source_http_error(exc) from exc


@router.post(
    "/preflight",
    response_model=PreflightResponse,
    response_class=SanitizingJSONResponse,
)
async def preflight(body: PreflightRequest, user: CurrentUser):
    """Check the caller's target privileges and shared storage. Read-only.

    Runs nothing: it reads the caller's own grants and, for data movement,
    reports whether a transfer stage is configured. Use it to fix grants before
    execute rather than discovering them per-object.
    """
    try:
        return await migration_service.preflight(
            body.source,
            body.database,
            target_database=body.target_database,
            objects=body.objects,
            create_database=body.create_database,
            include_data=body.include_data,
            actor=user["username"],
            encrypted_password=user["encrypted_password"],
            session_id=user.get("session_id"),
            role=user.get("active_role"),
        )
    except SourceConnectionError as exc:
        raise _source_http_error(exc) from exc


@router.post(
    "/execute",
    response_model=ExecuteResponse,
    response_class=SanitizingJSONResponse,
)
async def execute(body: ExecuteRequest, user: CurrentUser):
    """Apply a plan to the target database. Gated on #7 (backup/restore).

    Refuses (403) unless the operator enabled execute; refuses (422) unless the
    caller acknowledged the omissions and, when required, matched the target
    database confirmation. Statements run as the authenticated user, so
    StarRocks RBAC is the real authority. Each object gets its own result.
    """
    try:
        return await migration_service.execute(
            body.source,
            body.database,
            target_database=body.target_database,
            objects=body.objects,
            create_database=body.create_database,
            acknowledge_omissions=body.acknowledge_omissions,
            confirmation=body.confirmation,
            actor=user["username"],
            encrypted_password=user["encrypted_password"],
            session_id=user.get("session_id"),
            role=user.get("active_role"),
            include_data=body.include_data,
            stage_connection=body.stage_connection,
        )
    except DataMovementError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MigrationPreflightError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MigrationExecuteGateError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except MigrationExecuteConfirmationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SourceConnectionError as exc:
        raise _source_http_error(exc) from exc


def _source_http_error(exc: SourceConnectionError) -> HTTPException:
    """Map a source-resolution failure to the right status.

    An unknown source is a client error (404); a source that could not be
    reached or whose secret reference failed to resolve is an upstream failure
    (502). Neither ever falls back to the local engine. The message is passed
    through ``SanitizingJSONResponse`` like every other payload, and
    ``SourceConnectionError`` carries no credential.
    """
    message = str(exc)
    if message.startswith("Unknown migration source"):
        return HTTPException(status_code=404, detail=message)
    return HTTPException(status_code=502, detail=message)
