from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class WorkspaceEntry(BaseModel):
    id: str
    name: str
    parent_path: str
    path: str
    entry_type: str
    object_key: str | None = None
    size_bytes: int = 0
    etag: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkspaceTreeResponse(BaseModel):
    root_name: str = "My Workspace"
    entries: list[WorkspaceEntry]
    open_tabs: list[str] = []
    active_tab: str | None = None
    sidebar_collapsed: bool = False
    defaults: dict[str, str | None] = {}


class WorkspaceFileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    parent_path: str = ""
    content: str = ""


class WorkspaceFolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    parent_path: str = ""


class WorkspaceFileUpdate(BaseModel):
    content: str
    database: str | None = None
    schema_name: str | None = Field(None, alias="schema")
    role: str | None = None


class WorkspaceRenameRequest(BaseModel):
    id: str
    name: str = Field(..., min_length=1, max_length=256)
    parent_path: str | None = None


class WorkspaceFileResponse(BaseModel):
    entry: WorkspaceEntry
    content: str


class WorkspaceStateRequest(BaseModel):
    open_tabs: list[str] = []
    active_tab: str | None = None
    sidebar_collapsed: bool = False
    last_database: str | None = None
    last_schema: str | None = None
    last_role: str | None = None
