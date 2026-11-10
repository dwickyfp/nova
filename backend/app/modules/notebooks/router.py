"""Nova Notebook HTTP API.

Routers validate HTTP I/O only; documents, runtimes and executions are owned
by their services. Every route acts for the signed-in user on their own
Workspace notebooks.
"""

# ruff: noqa: B008 - ``Depends(...)`` defaults are FastAPI's injection idiom.

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.core.deps import get_current_user
from app.modules.notebooks.document import NotebookConflictError, NotebookFormatError
from app.modules.notebooks.documents import notebook_documents
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
