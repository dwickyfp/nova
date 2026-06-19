"""Snippet router — CRUD endpoints for saved SQL queries."""

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.modules.snippets.schemas import (
    SnippetCreate,
    SnippetListResponse,
    SnippetResponse,
    SnippetUpdate,
)
from app.modules.snippets.service import snippet_service

router = APIRouter()


@router.get("/", response_model=SnippetListResponse)
async def list_snippets(user: dict = Depends(get_current_user)):
    """List user's snippets + shared snippets."""
    items = await snippet_service.list_snippets(user["username"])
    return SnippetListResponse(
        items=[SnippetResponse(**item) for item in items],
        total=len(items),
    )


@router.post("/", response_model=SnippetResponse, status_code=201)
async def create_snippet(req: SnippetCreate, user: dict = Depends(get_current_user)):
    """Create a new saved snippet."""
    snippet_id = await snippet_service.create_snippet(
        username=user["username"],
        name=req.name,
        sql_text=req.sql_text,
        database_name=req.database_name,
        schema_name=req.schema_name,
        is_shared=req.is_shared,
    )
    return SnippetResponse(
        id=snippet_id,
        user_name=user["username"],
        name=req.name,
        sql_text=req.sql_text,
        database_name=req.database_name,
        schema_name=req.schema_name,
        is_shared=req.is_shared,
    )


@router.put("/{snippet_id}")
async def update_snippet(
    snippet_id: str,
    req: SnippetUpdate,
    user: dict = Depends(get_current_user),
):
    """Update an existing snippet (name, sql, sharing)."""
    ok = await snippet_service.update_snippet(
        snippet_id=snippet_id,
        username=user["username"],
        name=req.name,
        sql_text=req.sql_text,
        is_shared=req.is_shared,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Snippet not found or not owned by you")
    return {"success": True}


@router.delete("/{snippet_id}", status_code=204)
async def delete_snippet(snippet_id: str, user: dict = Depends(get_current_user)):
    """Delete a snippet."""
    ok = await snippet_service.delete_snippet(snippet_id, user["username"])
    if not ok:
        raise HTTPException(status_code=404, detail="Snippet not found or not owned by you")
