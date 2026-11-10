"""Nova Notebook HTTP API.

Routers validate HTTP I/O only; documents, runtimes and executions are owned
by their services. Every route acts for the signed-in user on their own
Workspace notebooks.
"""

# ruff: noqa: B008 - ``Depends(...)`` defaults are FastAPI's injection idiom.

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.modules.notebooks.document import NotebookConflictError, NotebookFormatError
from app.modules.notebooks.documents import notebook_documents
from app.modules.notebooks.runtime import events
from app.modules.notebooks.runtime.service import (
    NotebookRuntimeError,
    notebook_runtime_service,
)
from app.modules.notebooks.schemas import (
    NotebookBody,
    NotebookCreate,
    NotebookEdit,
    NotebookEditResponse,
    NotebookResponse,
)
from app.modules.workspaces.schemas import WorkspaceEntry

router = APIRouter()


def _response(entry: dict, document) -> dict:
    return {
        "entry": WorkspaceEntry.model_validate(entry),
        "notebook": NotebookBody.model_validate(document.to_api()),
    }


@router.post("", response_model=NotebookResponse, status_code=201)
async def create_notebook(body: NotebookCreate, user: dict = Depends(get_current_user)):
    try:
        entry, document = await notebook_documents.create(
            user["username"], parent_path=body.parent_path, name=body.name, content=body.content
        )
    except NotebookFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _response(entry, document)


@router.get("/{entry_id}", response_model=NotebookResponse)
async def get_notebook(entry_id: str, user: dict = Depends(get_current_user)):
    try:
        entry, document = await notebook_documents.get(user["username"], entry_id)
    except NotebookFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _response(entry, document)


@router.patch("/{entry_id}", response_model=NotebookEditResponse)
async def edit_notebook(entry_id: str, body: NotebookEdit, user: dict = Depends(get_current_user)):
    try:
        entry, document, changed = await notebook_documents.edit(
            user["username"], entry_id, base_revision=body.base_revision, edits=body.edits
        )
    except NotebookConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NOTEBOOK_CONFLICT",
                "message": "The notebook changed in another tab. Reload to see the latest.",
                "current_revision": exc.current_revision,
            },
        ) from exc
    except NotebookFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**_response(entry, document), "changed_cell_ids": changed}


@router.get("/{entry_id}/export")
async def export_notebook(entry_id: str, user: dict = Depends(get_current_user)):
    name, text = await notebook_documents.export(user["username"], entry_id)
    safe_name = "".join(ch for ch in name if ch.isalnum() or ch in "._- ") or "notebook.ipynb"
    return Response(
        content=text,
        media_type="application/x-ipynb+json",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


# ── Runtime, executions and events ─────────────────────────────────────────


async def _call(awaitable: Awaitable[Any]) -> Any:
    try:
        return await awaitable
    except NotebookRuntimeError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
        ) from exc


class RunRequest(BaseModel):
    mode: Literal["cell", "all", "above", "below"] = "cell"
    cell_id: str | None = Field(default=None, max_length=64)
    base_revision: int | None = Field(default=None, ge=0)
    on_error: Literal["stop", "continue"] = "stop"


class CancelRequest(BaseModel):
    execution_id: str | None = Field(default=None, max_length=64)


class InstallRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    version: str | None = Field(default=None, max_length=40)


@router.get("/{entry_id}/runtime")
async def runtime_status(entry_id: str, user: dict = Depends(get_current_user)):
    return await _call(notebook_runtime_service.status(user, entry_id))


@router.post("/{entry_id}/runtime", status_code=202)
async def start_runtime(entry_id: str, user: dict = Depends(get_current_user)):
    return await _call(notebook_runtime_service.start(user, entry_id))


@router.post("/{entry_id}/runtime/restart", status_code=202)
async def restart_runtime(entry_id: str, user: dict = Depends(get_current_user)):
    return await _call(notebook_runtime_service.restart(user, entry_id))


@router.post("/{entry_id}/runtime/stop", status_code=202)
async def stop_runtime(entry_id: str, user: dict = Depends(get_current_user)):
    return await _call(notebook_runtime_service.stop(user, entry_id))


@router.get("/{entry_id}/runtime/metrics")
async def runtime_metrics(entry_id: str, user: dict = Depends(get_current_user)):
    return await _call(notebook_runtime_service.metrics(user, entry_id))


@router.post("/{entry_id}/executions", status_code=202)
async def run_cells(
    entry_id: str,
    body: RunRequest,
    user: dict = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, max_length=128),
):
    return await _call(
        notebook_runtime_service.run(
            user,
            entry_id,
            mode=body.mode,
            cell_id=body.cell_id,
            base_revision=body.base_revision,
            on_error=body.on_error,
            idempotency_key=idempotency_key,
        )
    )


@router.post("/{entry_id}/executions/cancel")
async def cancel_cells(
    entry_id: str, body: CancelRequest, user: dict = Depends(get_current_user)
):
    return await _call(notebook_runtime_service.cancel(user, entry_id, body.execution_id))


@router.get("/{entry_id}/executions/latest")
async def latest_executions(entry_id: str, user: dict = Depends(get_current_user)):
    return await _call(notebook_runtime_service.latest(user, entry_id))


@router.get("/{entry_id}/executions/{execution_id}")
async def get_execution(
    entry_id: str, execution_id: str, user: dict = Depends(get_current_user)
):
    return await _call(notebook_runtime_service.execution(user, entry_id, execution_id))


@router.get("/{entry_id}/events")
async def notebook_events(
    entry_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID", max_length=64),
):
    snapshot = await _call(notebook_runtime_service.status(user, entry_id))
    cursor = last_event_id or request.query_params.get("last_event_id")
    return StreamingResponse(
        events.stream(
            entry_id,
            last_event_id=cursor,
            snapshot=snapshot,
            is_disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{entry_id}/packages")
async def list_packages(entry_id: str, user: dict = Depends(get_current_user)):
    return await _call(notebook_runtime_service.packages(user, entry_id))


@router.post("/{entry_id}/packages", status_code=202)
async def install_package(
    entry_id: str, body: InstallRequest, user: dict = Depends(get_current_user)
):
    return await _call(
        notebook_runtime_service.install(user, entry_id, name=body.name, version=body.version)
    )


@router.delete("/{entry_id}/packages/{name}")
async def remove_package(entry_id: str, name: str, user: dict = Depends(get_current_user)):
    return await _call(notebook_runtime_service.remove_package(user, entry_id, name))


_UPLOAD_LIMIT = 200 * 1024 * 1024


@router.post("/{entry_id}/files", status_code=201)
async def upload_file(
    entry_id: str,
    file: UploadFile = File(...),
    folder: str = Form(default="data", max_length=64, pattern=r"^[A-Za-z0-9_-]*$"),
    user: dict = Depends(get_current_user),
):
    data = await file.read(_UPLOAD_LIMIT + 1)
    if len(data) > _UPLOAD_LIMIT:
        raise HTTPException(status_code=413, detail="The file is larger than 200 MB")
    result = await _call(
        notebook_runtime_service.upload(
            user, entry_id, name=file.filename or "", data=data, subfolder=folder
        )
    )
    return {
        "entry": WorkspaceEntry.model_validate(result["entry"]),
        "runtime_path": result["runtime_path"],
        "relative_path": result["relative_path"],
    }
