"""Backup & recovery API router.

Endpoints under ``/api/v1/backup``:
  GET    /snapshots              → SHOW BACKUP [FROM <db>]
  POST   /snapshots              → BACKUP SNAPSHOT … (privileged)
  POST   /snapshots/restore      → RESTORE SNAPSHOT … (privileged)
  GET    /repositories           → SHOW REPOSITORIES
  POST   /repositories           → CREATE REPOSITORY … (privileged)
  GET    /recycle-bin            → SHOW CATALOG RECYCLE BIN
  POST   /recover                → RECOVER TABLE|DATABASE … (privileged)

Authorization is enforced **here**, not in the UI (AGENTS.md §6): every mutating
endpoint requires the caller's *active* role to be one of the StarRocks system
roles that can carry ``REPOSITORY``/``OPERATE`` on SYSTEM. The gate reads the
active role because the connection runs ``SET ROLE <active_role>`` — a granted
but inactive admin must be refused on a destructive surface (NOVA-94 finding
#3). The engine's own grant is the second gate, because each statement also runs
on the caller's connection.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user
from app.core.role_gates import require_active_role

from .schemas import (
    RecoverRequest,
    RecycleBinItem,
    RecycleBinResponse,
    RepositoryCreate,
    RepositoryResponse,
    SnapshotCreate,
    SnapshotListResponse,
    SnapshotResponse,
    SnapshotRestore,
)
from .service import BackupError, backup_service

router = APIRouter()

CurrentUser = Annotated[dict, Depends(get_current_user)]

#: Roles that may run a backup/restore/recover. These are the StarRocks system
#: roles whose privilege sets include ``REPOSITORY ON SYSTEM`` / ``OPERATE``;
#: ACCOUNTADMIN is the Nova super user that carries them explicitly (AGENTS.md
#: §6). A role not in this set is refused before any SQL is built.
BACKUP_ADMIN_ROLES = ("ACCOUNTADMIN", "cluster_admin", "db_admin")
#: Gate on the role the engine will *activate*, not the roles the user was
#: granted. Every statement runs as ``SET ROLE <active_role>`` (see
#: ``role_gates`` / NOVA-94 finding #3): a principal who holds ACCOUNTADMIN but
#: has switched to ``analyst`` must be refused on this destructive surface, and
#: a granted-roles check would have let it through.
_backup_admin = require_active_role(*BACKUP_ADMIN_ROLES)
BackupAdmin = Annotated[dict, Depends(_backup_admin)]


def _caller(user: dict) -> dict:
    return {
        "username": user["username"],
        "encrypted_password": user["encrypted_password"],
        "session_id": user.get("session_id"),
        "role": user.get("active_role"),
    }


# ── Snapshots ───────────────────────────────────────────────────


@router.get(
    "/snapshots",
    response_model=SnapshotListResponse,
    response_class=SanitizingJSONResponse,
)
async def list_snapshots(user: CurrentUser, database: str | None = None):
    """List snapshots via ``SHOW BACKUP``."""
    try:
        rows = await backup_service.list_snapshots(
            database=database, **_caller(user)
        )
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SnapshotListResponse(
        snapshots=[
            SnapshotResponse(
                name=str(r.get("SnapshotName") or r.get("Name") or ""),
                raw={k: str(v) for k, v in r.items()},
            )
            for r in rows
        ],
        count=len(rows),
    )


@router.post(
    "/snapshots",
    status_code=201,
    response_class=SanitizingJSONResponse,
)
async def create_snapshot(body: SnapshotCreate, user: BackupAdmin):
    """``BACKUP SNAPSHOT``. Privileged — admin roles only."""
    try:
        result = await backup_service.create_snapshot(body, **_caller(user))
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/snapshots/restore", response_class=SanitizingJSONResponse)
async def restore_snapshot(body: SnapshotRestore, user: BackupAdmin):
    """``RESTORE SNAPSHOT``. Privileged — admin roles only."""
    try:
        result = await backup_service.restore_snapshot(body, **_caller(user))
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


# ── Repositories ────────────────────────────────────────────────


@router.get(
    "/repositories",
    response_model=list[RepositoryResponse],
    response_class=SanitizingJSONResponse,
)
async def list_repositories(user: CurrentUser):
    """List backup repositories (redacted)."""
    try:
        rows = await backup_service.list_repositories(**_caller(user))
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [
        RepositoryResponse(
            name=str(r.get("Name") or r.get("name") or ""),
            raw={k: str(v) for k, v in r.items()},
        )
        for r in rows
    ]


@router.post("/repositories", status_code=201, response_class=SanitizingJSONResponse)
async def create_repository(body: RepositoryCreate, user: BackupAdmin):
    """``CREATE REPOSITORY`` from a named storage connection. Privileged."""
    try:
        result = await backup_service.create_repository(body, **_caller(user))
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


# ── Recycle bin / recovery ──────────────────────────────────────


@router.get(
    "/recycle-bin",
    response_model=RecycleBinResponse,
    response_class=SanitizingJSONResponse,
)
async def list_recycle_bin(user: CurrentUser):
    """List recoverable dropped objects via ``SHOW CATALOG RECYCLE BIN``."""
    try:
        rows = await backup_service.list_recycle_bin(**_caller(user))
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RecycleBinResponse(
        items=[
            RecycleBinItem(
                name=str(r.get("Name") or r.get("name") or ""),
                raw={k: str(v) for k, v in r.items()},
            )
            for r in rows
        ],
        count=len(rows),
    )


@router.post("/recover", response_class=SanitizingJSONResponse)
async def recover(body: RecoverRequest, user: BackupAdmin):
    """``RECOVER TABLE|DATABASE``. Privileged — admin roles only."""
    try:
        result = await backup_service.recover(body, **_caller(user))
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result
