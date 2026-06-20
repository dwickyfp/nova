from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.modules.functions.schemas import (
    BuiltInFunctionListResponse,
    UDFCreate,
    UDFListResponse,
)
from app.modules.functions.service import function_service

router = APIRouter()


@router.get("", response_model=BuiltInFunctionListResponse)
async def list_built_in_functions(
    category: Optional[str] = None,
    search: Optional[str] = None,
    _user=Depends(get_current_user),
):
    """List built-in functions, optionally filtered by category or search term."""
    functions, categories = function_service.list_built_in(category=category, search=search)
    return BuiltInFunctionListResponse(
        functions=functions,
        categories=categories,
        count=len(functions),
    )


@router.get("/udf", response_model=UDFListResponse)
async def list_udfs(
    database: Optional[str] = None,
    _user=Depends(get_current_user),
):
    """List user-defined functions via SHOW FULL FUNCTIONS."""
    try:
        functions = await function_service.list_udfs(database=database)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list UDFs: {e}")
    return UDFListResponse(functions=functions, count=len(functions))


@router.post("/udf", response_model=dict, status_code=201)
async def create_udf(
    data: UDFCreate,
    _user=Depends(get_current_user),
):
    """Create a new user-defined function."""
    try:
        sql = await function_service.create_udf(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create UDF: {e}")
    return {"message": "Function created", "sql": sql}


@router.delete("/udf/{database}/{name}", response_model=dict)
async def drop_udf(
    database: str,
    name: str,
    _user=Depends(get_current_user),
):
    """Drop a user-defined function."""
    try:
        sql = await function_service.drop_udf(database, name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to drop UDF: {e}")
    return {"message": f"Function {database}.{name} dropped", "sql": sql}
