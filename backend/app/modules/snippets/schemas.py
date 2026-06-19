"""Pydantic schemas for snippet endpoints."""

from pydantic import BaseModel


class SnippetCreate(BaseModel):
    name: str
    sql_text: str
    database_name: str | None = None
    schema_name: str | None = None
    is_shared: bool = False


class SnippetUpdate(BaseModel):
    name: str | None = None
    sql_text: str | None = None
    is_shared: bool | None = None


class SnippetResponse(BaseModel):
    id: str
    user_name: str
    name: str
    sql_text: str
    database_name: str | None = None
    schema_name: str | None = None
    is_shared: bool
    created_at: str | None = None


class SnippetListResponse(BaseModel):
    items: list[SnippetResponse]
    total: int
