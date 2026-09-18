"""Migration Connector API router — v1 Assessment + Dry-run (NOVA-85).

Endpoints:
  GET  /engine                       → status of the operator-provided engine
  POST /connections                  → connect to a source StarRocks cluster
  POST /connections/{id}/enumerate   → enumerate databases/tables/views/MVs
  POST /connections/{id}/dry-run     → per-object migratable/lossy/skipped report

There is no execute/cutover endpoint, under any flag. Cutting over is a separate
issue gated on backup/restore (roadmap #7). ``test_migration_connector`` asserts
the absence, so a later addition has to argue with a failing test rather than
slip in.

Every response is ``SanitizingJSONResponse`` so a statement or engine message
that somehow escaped the service's redaction is stripped at the boundary.
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user
from app.modules.migration.repository import SourceConnectionError
from app.modules.migration.schemas import (
    DryRunRequest,
    DryRunResponse,
    EnumerateResponse,
    MigrationConnectRequest,
    MigrationConnectResponse,
    MigrationEngineStatus,
)
from app.modules.migration.service import migration_service
from app.modules.migration.source_store import MigrationSourceNotFound

router = APIRouter()

CurrentUser = Annotated[dict, Depends(get_current_user)]


@router.get(
    "/engine",
    response_model=MigrationEngineStatus,
    response_class=SanitizingJSONResponse,
)
async def engine_status(user: CurrentUser):
    """Report whether the operator-provided migration engine is available.

    Nova never bundles the binary; an absent path is reported, not worked
    around, and never triggers a download.
    """
    return migration_service.engine_status()


@router.post(
    "/connections",
    response_model=MigrationConnectResponse,
    response_class=SanitizingJSONResponse,
)
async def connect(body: MigrationConnectRequest, user: CurrentUser):
    """Probe a source StarRocks deployment and return an opaque connection id.

    The source password is accepted here and immediately encrypted server-side;
    it is not echoed, logged, or persisted in cleartext.
    """
    connection_id = str(uuid4())
    try:
        return await migration_service.connect(
            connection_id,
            host=body.host,
            port=body.port,
            username=body.username,
            password=body.password,
            database=body.database,
        )
    except SourceConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/connections/{connection_id}/enumerate",
    response_model=EnumerateResponse,
    response_class=SanitizingJSONResponse,
)
async def enumerate_objects(
    connection_id: str,
    database: str,
    user: CurrentUser,
):
    """Enumerate source objects for one database.

    Materialized views are read from ``information_schema.materialized_views``,
    reported back in ``materialized_view_source`` so the caller can assert it.
    """
    try:
        return await migration_service.enumerate(
            connection_id,
            database=database,
        )
    except MigrationSourceNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Source connection expired or unknown. Reconnect.",
        ) from exc


@router.post(
    "/connections/{connection_id}/dry-run",
    response_model=DryRunResponse,
    response_class=SanitizingJSONResponse,
)
async def dry_run(
    connection_id: str,
    body: DryRunRequest,
    user: CurrentUser,
):
    """Run the read-only assessment and return per-object verdicts.

    Never executes a migration: it shells out to nothing, writes to the source
    nothing, and writes to the target nothing.
    """
    try:
        return await migration_service.dry_run(
            connection_id,
            database=body.database,
        )
    except MigrationSourceNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Source connection expired or unknown. Reconnect.",
        ) from exc
