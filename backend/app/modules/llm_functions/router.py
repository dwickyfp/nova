"""LLM Function Management API router — alias CRUD and UDF registration endpoints.

Endpoints under /api/v1/ai:
  GET    /aliases                   → list all aliases
  POST   /aliases                   → create alias
  PUT    /aliases/{id}              → update alias
  DELETE /aliases/{id}              → delete alias
  POST   /aliases/register-udfs     → re-register all UDFs in StarRocks
  GET    /aliases/udf-status        → check which UDFs are registered
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.modules.llm_functions.schemas import (
    VALID_FUNCTION_TYPES,
    AliasCreate,
    AliasListResponse,
    AliasResponse,
    AliasUpdate,
    UDFRegisterResponse,
    UDFStatusItem,
    UDFStatusResponse,
)
from app.modules.llm_functions.service import llm_function_service

router = APIRouter()


# ── Aliases ───────────────────────────────────────────────────


@router.get("/aliases", response_model=AliasListResponse)
async def list_aliases(
    user: dict = Depends(get_current_user),
):
    """List all LLM function aliases."""
    aliases = await llm_function_service.list_aliases()
    alias_responses = [AliasResponse(**a) for a in aliases]
    return AliasListResponse(aliases=alias_responses, count=len(alias_responses))


@router.post("/aliases", response_model=AliasResponse, status_code=201)
async def create_alias(
    body: AliasCreate,
    user: dict = Depends(get_current_user),
):
    """Create a new LLM function alias and register the UDF."""
    if body.function_type not in VALID_FUNCTION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid function_type. Must be one of: {', '.join(sorted(VALID_FUNCTION_TYPES))}",
        )
    try:
        result = await llm_function_service.create_alias(
            body.model_dump(), user["username"]
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AliasResponse(**result)


@router.put("/aliases/{alias_id}", response_model=AliasResponse)
async def update_alias(
    alias_id: str,
    body: AliasUpdate,
    user: dict = Depends(get_current_user),
):
    """Update an existing alias."""
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "function_type" in data and data["function_type"] not in VALID_FUNCTION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid function_type. Must be one of: {', '.join(sorted(VALID_FUNCTION_TYPES))}",
        )
    try:
        result = await llm_function_service.update_alias(alias_id, data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail=f"Alias '{alias_id}' not found")
    return AliasResponse(**result)


@router.delete("/aliases/{alias_id}", status_code=204)
async def delete_alias(
    alias_id: str,
    user: dict = Depends(get_current_user),
):
    """Delete an alias."""
    deleted = await llm_function_service.delete_alias(alias_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Alias '{alias_id}' not found")


# ── UDF Management ────────────────────────────────────────────


@router.post("/aliases/register-udfs", response_model=UDFRegisterResponse)
async def register_udfs(
    user: dict = Depends(get_current_user),
):
    """Re-register all LLM UDFs in StarRocks.

    Call this after:
    - Creating/updating/deleting an alias
    - Changing provider API key or endpoint
    - Backend restart
    """
    result = await llm_function_service.register_all_udfs()
    details = [UDFStatusItem(**d) for d in result["details"]]
    return UDFRegisterResponse(
        success=result["success"],
        registered=result["registered"],
        failed=result["failed"],
        details=details,
    )


@router.get("/aliases/udf-status", response_model=UDFStatusResponse)
async def get_udf_status(
    user: dict = Depends(get_current_user),
):
    """Check which LLM UDFs are registered in StarRocks."""
    statuses = await llm_function_service.get_udf_status()
    items = [UDFStatusItem(**s) for s in statuses]
    registered = sum(1 for i in items if i.registered)
    failed = sum(1 for i in items if not i.registered)
    return UDFStatusResponse(
        functions=items,
        total=len(items),
        registered=registered,
        failed=failed,
    )
