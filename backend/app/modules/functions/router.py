from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user, get_user_connection
from app.modules.functions.schemas import (
    BuiltInFunctionListResponse,
    UDFCreate,
    UDFListResponse,
)
from app.modules.functions.service import function_service

router = APIRouter()

#: ``Annotated`` dependency aliases (the convention used across the tree); a
#: ``Depends()`` in an argument default trips ruff B008. UDF DDL runs on the
#: caller's own connection so StarRocks RBAC is the decision point (NOVA-89).
CurrentUser = Annotated[dict, Depends(get_current_user)]
UserConnection = Annotated[Any, Depends(get_user_connection)]


@router.get("", response_model=BuiltInFunctionListResponse)
async def list_built_in_functions(
    _user: CurrentUser,
    category: str | None = None,
    search: str | None = None,
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
    _user: CurrentUser,
    database: str | None = None,
):
    """List user-defined functions via SHOW FULL FUNCTIONS."""
    try:
        functions, databases = await function_service.list_udfs_with_databases(
            database=database
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list UDFs: {e}") from e
    return UDFListResponse(
        functions=functions, count=len(functions), databases=databases
    )


@router.post("/udf", response_model=dict, status_code=201)
async def create_udf(
    data: UDFCreate,
    conn: UserConnection,
):
    """Create a new user-defined function as the caller."""
    try:
        sql = await function_service.create_udf(data, conn)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create UDF: {e}") from e
    return {"message": "Function created", "sql": sql}


@router.delete("/udf/{database}/{name}", response_model=dict)
async def drop_udf(
    database: str,
    name: str,
    conn: UserConnection,
):
    """Drop a user-defined function as the caller."""
    try:
        sql = await function_service.drop_udf(database, name, conn)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to drop UDF: {e}") from e
    return {"message": f"Function {database}.{name} dropped", "sql": sql}
