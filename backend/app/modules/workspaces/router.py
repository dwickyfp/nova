"""Workspace router: files, folders, shared workspaces and persisted state.

Every request resolves the workspace first (``spaces.space_service``): the
person's own, or a shared one they own or reach through an assigned role.
New items name their workspace (``space``); existing ones are found by id.
Changes need edit permission; managing a shared workspace needs ownership.
"""

# ruff: noqa: B008 — `Depends(...)` in a default is FastAPI's dependency
# injection idiom used throughout this codebase. The rule's suggested rewrite
# would break the DI graph.

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.modules.workspaces.actor import acting_as
from app.modules.workspaces.schemas import (
    WorkspaceEntry,
    WorkspaceFileCreate,
    WorkspaceFileResponse,
    WorkspaceFileUpdate,
    WorkspaceFileVersion,
    WorkspaceFileVersionResponse,
    WorkspaceFileVersionsResponse,
    WorkspaceFolderCreate,
    WorkspaceRenameRequest,
    WorkspaceSharesUpdate,
    WorkspaceSpace,
    WorkspaceSpaceCreate,
    WorkspaceStateRequest,
    WorkspaceTreeResponse,
)
from app.modules.workspaces.service import (
    stop_deleted_notebooks,
    subtree_ids,
    workspace_service,
)
from app.modules.workspaces.spaces import Space, space_service

router = APIRouter()


def _space(space: Space) -> WorkspaceSpace:
    return WorkspaceSpace(**space.public())


# ── Trees ─────────────────────────────────────────────────────────────────


@router.get("/tree", response_model=WorkspaceTreeResponse)
async def get_workspace_tree(user: dict = Depends(get_current_user)):
    """The personal workspace, with the person's saved tabs and layout."""
    space = space_service.personal(user)
    tree = await workspace_service.get_tree(user["username"])
    return {**tree, "root_name": "Personal Workspace", "space": _space(space)}


@router.get("/spaces")
async def list_workspaces(user: dict = Depends(get_current_user)):
    """The personal workspace and every shared one this person can open."""
    spaces = await space_service.visible(user)
    return {"spaces": [_space(space) for space in spaces]}


@router.post("/spaces", status_code=201)
async def create_workspace(body: WorkspaceSpaceCreate, user: dict = Depends(get_current_user)):
    space = await space_service.create(user, body.name)
    return {"space": _space(space)}


@router.get("/spaces/{space_id}/tree", response_model=WorkspaceTreeResponse)
async def get_shared_tree(space_id: str, user: dict = Depends(get_current_user)):
    space = await space_service.resolve(user, space_id)
    entries = await workspace_service.list_entries(space.namespace)
    return WorkspaceTreeResponse(
        root_name=space.name,
        space=_space(space),
        entries=[WorkspaceEntry.model_validate(entry) for entry in entries],
    )


@router.patch("/spaces/{space_id}")
async def rename_workspace(
    space_id: str, body: WorkspaceSpaceCreate, user: dict = Depends(get_current_user)
):
    space = await space_service.rename(user, space_id, body.name)
    return {"space": _space(space)}


@router.delete("/spaces/{space_id}")
async def delete_workspace(space_id: str, user: dict = Depends(get_current_user)):
    await space_service.delete(user, space_id)
    return {"success": True}


@router.get("/spaces/{space_id}/shares")
async def get_workspace_shares(space_id: str, user: dict = Depends(get_current_user)):
    return {"shares": await space_service.shares(user, space_id)}


@router.put("/spaces/{space_id}/shares")
async def set_workspace_shares(
    space_id: str, body: WorkspaceSharesUpdate, user: dict = Depends(get_current_user)
):
    shares = await space_service.set_shares(
        user, space_id, [share.model_dump() for share in body.shares]
    )
    return {"shares": shares}


# ── Files and folders ─────────────────────────────────────────────────────


@router.post("/files", response_model=WorkspaceFileResponse, status_code=201)
async def create_workspace_file(
    body: WorkspaceFileCreate,
    user: dict = Depends(get_current_user),
):
    space = await space_service.resolve(user, body.space, edit=True)
    with acting_as(user):
        entry = await workspace_service.create_file(
            space.namespace, body.parent_path, body.name, body.content
        )
    return WorkspaceFileResponse(
        entry=WorkspaceEntry.model_validate(entry), content=body.content, space=_space(space)
    )


@router.get("/files/{entry_id}", response_model=WorkspaceFileResponse)
async def get_workspace_file(entry_id: str, user: dict = Depends(get_current_user)):
    space = await space_service.for_entry(user, entry_id)
    entry, content = await workspace_service.get_file(space.namespace, entry_id)
    return WorkspaceFileResponse(
        entry=WorkspaceEntry.model_validate(entry), content=content, space=_space(space)
    )


@router.put("/files/{entry_id}", response_model=WorkspaceFileResponse)
async def update_workspace_file(
    entry_id: str,
    body: WorkspaceFileUpdate,
    user: dict = Depends(get_current_user),
):
    space = await space_service.for_entry(user, entry_id, edit=True)
    with acting_as(user):
        entry = await workspace_service.update_file(space.namespace, entry_id, body.content)
    return WorkspaceFileResponse(
        entry=WorkspaceEntry.model_validate(entry), content=body.content, space=_space(space)
    )


@router.delete("/files/{entry_id}")
async def delete_workspace_file(entry_id: str, user: dict = Depends(get_current_user)):
    space = await space_service.for_entry(user, entry_id, edit=True)
    with acting_as(user):
        removed = subtree_ids(await workspace_service.list_entries(space.namespace), entry_id)
        await workspace_service.delete_entry(space.namespace, entry_id)
    await stop_deleted_notebooks(removed)
    return {"success": True}


@router.get("/files/{entry_id}/versions", response_model=WorkspaceFileVersionsResponse)
async def list_workspace_file_versions(
    entry_id: str, user: dict = Depends(get_current_user)
):
    space = await space_service.for_entry(user, entry_id)
    versions = await workspace_service.list_file_versions(space.namespace, entry_id)
    return WorkspaceFileVersionsResponse(
        versions=[WorkspaceFileVersion(**version) for version in versions]
    )


@router.get(
    "/files/{entry_id}/versions/{version}",
    response_model=WorkspaceFileVersionResponse,
)
async def get_workspace_file_version(
    entry_id: str, version: int, user: dict = Depends(get_current_user)
):
    space = await space_service.for_entry(user, entry_id)
    record, content = await workspace_service.get_file_version_record(
        space.namespace, entry_id, version
    )
    return WorkspaceFileVersionResponse(
        version=WorkspaceFileVersion(**record), content=content
    )


@router.post("/folders", status_code=201)
async def create_workspace_folder(
    body: WorkspaceFolderCreate,
    user: dict = Depends(get_current_user),
):
    space = await space_service.resolve(user, body.space, edit=True)
    with acting_as(user):
        entry = await workspace_service.create_folder(space.namespace, body.parent_path, body.name)
    return {"entry": entry, "space": _space(space)}


@router.post("/rename")
async def rename_workspace_entry(
    body: WorkspaceRenameRequest,
    user: dict = Depends(get_current_user),
):
    """Rename an entry, or move it to another folder of the same workspace."""
    space = await space_service.for_entry(user, body.id, edit=True)
    with acting_as(user):
        entry = await workspace_service.rename_entry(
            space.namespace, body.id, body.name, body.parent_path
        )
    return {"entry": entry}


@router.put("/state")
async def save_workspace_state(
    body: WorkspaceStateRequest,
    user: dict = Depends(get_current_user),
):
    with acting_as(user):
        await _save_state(user, body)
    return {"success": True}


async def _save_state(user: dict, body: WorkspaceStateRequest) -> None:
    await workspace_service.save_state(
        user["username"],
        open_tabs=body.open_tabs,
        active_tab=body.active_tab,
        sidebar_collapsed=body.sidebar_collapsed,
        assistant_collapsed=body.assistant_collapsed,
        last_database=body.last_database,
        last_schema=body.last_schema,
        last_role=body.last_role,
    )
