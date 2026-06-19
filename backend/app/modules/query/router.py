"""Query API router — execute SQL, explain, query history."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.common.sql_guard import is_destructive_sql, is_unscoped_mutation
from app.modules.query.service import query_service

router = APIRouter()


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    database: str | None = None
    schema_name: str | None = Field(None, alias="schema")
    role: str | None = None
    max_rows: int = Field(500, ge=1, le=5000)
    file_id: str | None = None
    confirm_destructive: bool = False


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
    destructive: bool = False
    needs_confirmation: bool = False


class CompletionResponse(BaseModel):
    items: list[dict]


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
        role=req.role,
        max_rows=req.max_rows,
        session_id=user["session_id"],
        confirm_destructive=req.confirm_destructive,
        file_id=req.file_id,
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
        destructive=is_destructive_sql(req.sql),
        needs_confirmation=is_destructive_sql(req.sql)
        or is_unscoped_mutation(req.sql),
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
        role=req.role,
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


@router.get("/context")
async def get_query_context(user: dict = Depends(get_current_user)):
    return await query_service.get_context(
        username=user["username"],
        encrypted_password=user["encrypted_password"],
    )


@router.get("/completions", response_model=CompletionResponse)
async def get_query_completions(
    kind: str,
    prefix: str = "",
    database: str | None = None,
    schema: str | None = None,
    role: str | None = None,
    table: str | None = None,
    stage: str | None = None,
    user: dict = Depends(get_current_user),
):
    result = await query_service.get_completions(
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        kind=kind,
        prefix=prefix,
        database=database,
        schema=schema,
        role=role,
        table=table,
        stage=stage,
    )
    return CompletionResponse(**result)


class HistoryItem(BaseModel):
    query_id: str
    event_time: str
    sql_text: str
    status: str
    duration_ms: int | None = None
    rows_affected: int | None = None
    error_message: str | None = None
    file_id: str | None = None
    database_name: str | None = None
    schema_name: str | None = None


class HistoryResponse(BaseModel):
    items: list[HistoryItem]
    total: int


@router.get("/history", response_model=HistoryResponse)
async def get_query_history(
    file_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    """Get query execution history for the current user.

    Filter by file_id to get history for a specific workspace file,
    or omit to get all history.
    """
    result = await query_service.get_history(
        username=user["username"],
        file_id=file_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return HistoryResponse(**result)
