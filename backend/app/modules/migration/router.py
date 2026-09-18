"""Migration Connector API router — Phase 11 v1 (Assessment + Dry-run).

Endpoints:
  GET  /migration/engine               → operator binary availability (no execution)
  GET  /migration/sources              → registered source connections
  POST /migration/sources              → register a source connection (name only)
  POST /migration/enumerate            → list source objects
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
from app.core.deps import get_current_user

from .schemas import (
    DryRunRequest,
    DryRunResponse,
    EngineStatusResponse,
    EnumerateRequest,
    EnumerateResponse,
    SourceConnectionListResponse,
    SourceConnectionRequest,
    SourceConnectionResponse,
)
from .service import migration_service

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
        "phases": ["assess", "dry_run"],
        "write_operations": False,
        "execute_available": False,
        "execute_gate": {"issue": "#7", "name": "backup/restore"},
        "mv_ddl_surface": "SHOW CREATE MATERIALIZED VIEW",
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
    """Register a source connection by storage-connection name.

    The credential lives in ``nova.yaml``/env and is resolved server-side; no
    secret is accepted by this request.
    """
    existing = await migration_service.list_sources()
    if any(connection.name == body.name for connection in existing.connections):
        raise HTTPException(
            status_code=400, detail=f"Source connection '{body.name}' already exists"
        )
    return await migration_service.create_source(
        name=body.name,
        storage_connection=body.storage_connection,
        comment=body.comment,
        username=user["username"],
    )


@router.post(
    "/enumerate",
    response_model=EnumerateResponse,
    response_class=SanitizingJSONResponse,
)
async def enumerate_objects(body: EnumerateRequest, user: CurrentUser):
    """Enumerate tables/views/MVs/functions/tasks/pipes/policies of a database.

    MVs are read from ``information_schema.materialized_views``.
    """
    return await migration_service.enumerate(body.database)


@router.post(
    "/dry-run",
    response_model=DryRunResponse,
    response_class=SanitizingJSONResponse,
)
async def dry_run(body: DryRunRequest, user: CurrentUser):
    """Classify each selected object: ``migratable`` / ``lossy`` / ``skipped``.

    Read-only. This endpoint never materialises or executes a cutover.
    """
    return await migration_service.dry_run(body.database, body.objects)
