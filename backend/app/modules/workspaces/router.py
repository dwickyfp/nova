from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.modules.workspaces.schemas import (
    WorkspaceFileCreate,
    WorkspaceFileResponse,
    WorkspaceFileUpdate,
    WorkspaceFolderCreate,
    WorkspaceRenameRequest,
    WorkspaceStateRequest,
    WorkspaceTreeResponse,
)
from app.modules.workspaces.service import workspace_service

router = APIRouter()


@router.get("/tree", response_model=WorkspaceTreeResponse)
async def get_workspace_tree(user: dict = Depends(get_current_user)):
    return await workspace_service.get_tree(user["username"])


@router.post("/files", response_model=WorkspaceFileResponse, status_code=201)
async def create_workspace_file(
    body: WorkspaceFileCreate,
    user: dict = Depends(get_current_user),
):
    entry = await workspace_service.create_file(
        user["username"], body.parent_path, body.name, body.content
    )
    return WorkspaceFileResponse(entry=entry, content=body.content)


@router.get("/files/{entry_id}", response_model=WorkspaceFileResponse)
async def get_workspace_file(entry_id: str, user: dict = Depends(get_current_user)):
    entry, content = await workspace_service.get_file(user["username"], entry_id)
    return WorkspaceFileResponse(entry=entry, content=content)


@router.put("/files/{entry_id}", response_model=WorkspaceFileResponse)
async def update_workspace_file(
    entry_id: str,
    body: WorkspaceFileUpdate,
    user: dict = Depends(get_current_user),
):
    entry = await workspace_service.update_file(user["username"], entry_id, body.content)
    return WorkspaceFileResponse(entry=entry, content=body.content)


@router.delete("/files/{entry_id}")
async def delete_workspace_file(entry_id: str, user: dict = Depends(get_current_user)):
    await workspace_service.delete_entry(user["username"], entry_id)
    return {"success": True}


@router.post("/folders", status_code=201)
async def create_workspace_folder(
    body: WorkspaceFolderCreate,
    user: dict = Depends(get_current_user),
):
    entry = await workspace_service.create_folder(user["username"], body.parent_path, body.name)
    return {"entry": entry}


@router.post("/rename")
async def rename_workspace_entry(
    body: WorkspaceRenameRequest,
    user: dict = Depends(get_current_user),
):
    entry = await workspace_service.rename_entry(
        user["username"], body.id, body.name, body.parent_path
    )
    return {"entry": entry}


@router.put("/state")
async def save_workspace_state(
    body: WorkspaceStateRequest,
    user: dict = Depends(get_current_user),
):
    await workspace_service.save_state(
        user["username"],
        open_tabs=body.open_tabs,
        active_tab=body.active_tab,
        sidebar_collapsed=body.sidebar_collapsed,
        last_database=body.last_database,
        last_schema=body.last_schema,
        last_role=body.last_role,
    )
    return {"success": True}
