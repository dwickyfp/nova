"""HTTP schemas for Nova Notebook."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.workspaces.schemas import WorkspaceEntry


class NotebookCell(BaseModel):
    id: str
    kind: Literal["python", "sql", "markdown"]
    source: str
    name: str | None = None
    engine: Literal["duckdb", "nova"] | None = None
    title: str | None = None
    source_revision: str


class NotebookBody(BaseModel):
    revision: int
    cells: list[NotebookCell]


class NotebookResponse(BaseModel):
    entry: WorkspaceEntry
    notebook: NotebookBody


class NotebookCreate(BaseModel):
    parent_path: str = Field(default="", max_length=1024)
    name: str = Field(min_length=1, max_length=255)
    #: An existing ``.ipynb`` to import instead of the starter notebook.
    content: str | None = Field(default=None, max_length=8 * 1024 * 1024)


class NotebookEdit(BaseModel):
    base_revision: int = Field(ge=0)
    edits: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class NotebookEditResponse(NotebookResponse):
    #: Cells whose runnable source changed: their earlier outputs are stale.
    changed_cell_ids: list[str]
