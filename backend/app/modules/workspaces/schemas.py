from __future__ import annotations

from datetime import datetime
from typing import Literal

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


class WorkspaceSpace(BaseModel):
    """A workspace as a person sees it: their own, or one shared with a role of theirs."""

    id: str
    name: str
    kind: str
    owner: str
    permission: str


class WorkspaceTreeResponse(BaseModel):
    root_name: str = "Personal Workspace"
    space: WorkspaceSpace | None = None
    entries: list[WorkspaceEntry]
    open_tabs: list[str] = []
    active_tab: str | None = None
    sidebar_collapsed: bool = False
    assistant_collapsed: bool = False
    defaults: dict[str, str | None] = {}


class WorkspaceFileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    parent_path: str = ""
    content: str = ""
    #: The workspace to create it in; the personal workspace when omitted.
    space: str | None = None


class WorkspaceFolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    parent_path: str = ""
    space: str | None = None


class WorkspaceSpaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class WorkspaceShare(BaseModel):
    role: str = Field(..., min_length=1, max_length=128)
    permission: Literal["view", "edit"] = "view"


class WorkspaceSharesUpdate(BaseModel):
    shares: list[WorkspaceShare] = Field(default_factory=list, max_length=100)


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
    #: The workspace the file is in, with the person's permission there.
    space: WorkspaceSpace | None = None


class WorkspaceFileVersion(BaseModel):
    id: str
    entry_id: str
    version: int
    size_bytes: int = 0
    etag: str | None = None
    created_at: datetime | None = None


class WorkspaceFileVersionsResponse(BaseModel):
    versions: list[WorkspaceFileVersion]


class WorkspaceFileVersionResponse(BaseModel):
    version: WorkspaceFileVersion
    content: str


class WorkspaceStateRequest(BaseModel):
    open_tabs: list[str] = []
    active_tab: str | None = None
    sidebar_collapsed: bool = False
    assistant_collapsed: bool = False
    last_database: str | None = None
    last_schema: str | None = None
    last_role: str | None = None
