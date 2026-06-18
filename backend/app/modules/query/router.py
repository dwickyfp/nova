"""Query API router — execute SQL, explain, query history."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.modules.query.service import query_service

router = APIRouter()


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    database: str | None = None
    schema_name: str | None = Field(None, alias="schema")


class QueryResponse(BaseModel):
    success: bool
    columns: list[str] = []
    rows: list[list] = []
    row_count: int = 0
    affected_rows: int = 0
    elapsed_ms: float = 0.0
    original_sql: str = ""
    executed_sql: str = ""
    warnings: list[str] = []


@router.post("/execute", response_model=QueryResponse)
async def execute_query(
    req: QueryRequest,
    user: dict = Depends(get_current_user),
):
    """Execute a SQL statement with @stage dialect support.

    The SQL is parsed for @stage references, translated to FILES() calls
    with auto-detected format and injected credentials, then executed
    against StarRocks as the authenticated user.
    """
    result = await query_service.execute(
        sql=req.sql,
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        database=req.database,
        schema=req.schema_name,
    )

    return QueryResponse(
        success=True,
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        affected_rows=result.affected_rows,
        elapsed_ms=result.elapsed_ms,
        original_sql=result.original_sql,
        executed_sql=result.executed_sql,
        warnings=result.warnings,
    )


@router.post("/explain", response_model=QueryResponse)
async def explain_query(
    req: QueryRequest,
    user: dict = Depends(get_current_user),
):
    """Get the EXPLAIN plan for a SQL statement.

    Translates @stage references first, then returns the execution plan.
    """
    result = await query_service.explain(
        sql=req.sql,
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        database=req.database,
    )

    return QueryResponse(
        success=True,
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        elapsed_ms=result.elapsed_ms,
        original_sql=result.original_sql,
        executed_sql=result.executed_sql,
        warnings=result.warnings,
    )
