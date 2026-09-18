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
        "phases": ["assess", "dry_run"],
        "write_operations": False,
        "execute_available": False,
        "execute_gate": {"issue": "#7", "name": "backup/restore"},
        "mv_ddl_surface": "SHOW CREATE MATERIALIZED VIEW",
        "source_required": True,
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
