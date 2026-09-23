"""Query API router — execute SQL, explain, query history."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.common.responses import SanitizingJSONResponse
from app.common.sql_guard import (
    is_destructive_sql,
    is_unscoped_mutation,
)
from app.core.config import settings
from app.core.deps import get_current_user
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.auth.service import auth_service
from app.modules.query.service import query_service
from app.proxy.session import parse_role_statement

router = APIRouter()

#: ``Annotated`` dependency alias (the tree's convention; a ``Depends()`` in an
#: argument default trips ruff B008).
CurrentUser = Annotated[dict, Depends(get_current_user)]

#: Re-exported for the query-response contract described in AGENTS.md §2: the
#: class moved to ``app/common/responses`` so the global exception handler can
#: reuse it without closing an import cycle (``core.exceptions`` →
#: ``common.responses`` → ``common.sql_guard`` → ``core.exceptions``). The name
#: stays importable from here because both routers and tests read it from this
#: module.
__all__ = ["SanitizingJSONResponse", "router"]


def _resolve_active_role(user: dict) -> str | None:
    """Return the role the engine must activate for this request.

    The session's ``active_role`` (set at login and changed through
    ``POST /auth/switch-role``) is the single source of truth for the role a
    statement runs under. Request bodies do not get to choose it: a body
    ``role`` used to be forwarded to ``SET ROLE`` unvalidated, which let any
    caller attempt a role it had not been granted and let the UI show one role
    while executing under another (the bottom-left switcher versus the
    workspace tab picker).

    Ranger sessions require an assigned active role. Native StarRocks RBAC
    sessions may have no active role; any named role is still validated against
    the session's grants.
    """
    if not settings.RANGER_ENABLED and user.get("active_role") is None:
        return None
    try:
        return SecurityContext.from_session(user).active_role
    except SecurityContextError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _history_subject(user: dict, requested_user: str | None) -> str:
    if not requested_user or requested_user == user["username"]:
        return user["username"]
    if _resolve_active_role(user) != "ACCOUNTADMIN":
        raise HTTPException(
            status_code=403,
            detail="ACCOUNTADMIN is required to view another user's history",
        )
    return requested_user


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    database: str | None = None
    schema_name: str | None = Field(None, alias="schema")
    #: Accepted for backward compatibility but ignored — the active role comes
    #: from the session (:func:`_resolve_active_role`), never the request body.
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
    error: str | None = None


class CompletionItem(BaseModel):
    label: str
    type: str
    insert_text: str | None = None
    detail: str | None = None
    size: int | None = None
    last_modified: datetime | str | None = None


class CompletionResponse(BaseModel):
    items: list[CompletionItem]


@router.post(
    "/execute",
    response_model=list[QueryResponse],
    response_class=SanitizingJSONResponse,
)
async def execute_query(
    req: QueryRequest,
    user: CurrentUser,
):
    """Execute one or more SQL statements (split by `;`).

    Each statement runs through the full pipeline (guard → parse → translate → execute → audit).
    Stops on first error — returns results collected so far plus an error result.
    Always returns a list (single statement → list with one element).
    """
    try:
        requested_role = parse_role_statement(req.sql)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if requested_role is not None:
        target = user.get("default_role") if requested_role.upper() == "DEFAULT" else requested_role
        if not target:
            raise HTTPException(status_code=403, detail="No explicit default role is configured")
        switched = await auth_service.switch_role(user["session_id"], target)
        return [
            QueryResponse(
                success=True,
                columns=["CURRENT_ROLE()", "SECURITY_CONTEXT_VERSION"],
                rows=[[switched["active_role"], switched["security_context_version"]]],
                row_count=1,
                original_sql=req.sql,
                executed_sql="",
            )
        ]

    results = await query_service.execute_statements(
        tenant=user.get("tenant", "default"),
        security_context_version=user.get("security_context_version", 1),
        sql=req.sql,
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        database=req.database,
        schema=req.schema_name,
        role=_resolve_active_role(user),
        max_rows=req.max_rows,
        session_id=user["session_id"],
        confirm_destructive=req.confirm_destructive,
        file_id=req.file_id,
    )

    responses = []
    for result in results:
        # ``success`` is the statement's own verdict, carried explicitly on the
        # result (``QueryResult.error``) rather than inferred from the shape of
        # what came back. The shape heuristic that used to live here —
        # ``bool(warnings) and not columns and row_count == 0`` — read a
        # successful ``@stage`` DML statement as a failure: ``COPY INTO`` has no
        # ``description`` so it returns no columns and no rows, while
        # ``translate_stage_query`` appends a warning on its success path.
        # ``warnings`` remains informational only.
        responses.append(
            QueryResponse(
                success=result.success,
                columns=result.columns,
                rows=result.rows,
                row_count=result.row_count,
                affected_rows=result.affected_rows,
                elapsed_ms=result.elapsed_ms,
                original_sql=result.original_sql,
                executed_sql=result.executed_sql,
                warnings=result.warnings,
                destructive=is_destructive_sql(result.original_sql),
                needs_confirmation=is_destructive_sql(result.original_sql)
                or is_unscoped_mutation(result.original_sql),
                error=result.error,
            )
        )
    return responses


@router.post(
    "/explain",
    response_model=QueryResponse,
    response_class=SanitizingJSONResponse,
)
async def explain_query(
    req: QueryRequest,
    user: CurrentUser,
):
    """Get the EXPLAIN plan for a SQL statement.

    Translates @stage references first, then returns the execution plan.
    """
    result = await query_service.explain(
        sql=req.sql,
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        database=req.database,
        role=_resolve_active_role(user),
        schema=req.schema_name,
    )

    # ``success`` is derived from ``error``, so the marker has to reach the
    # response: omitting it here makes a refused ``@stage`` translation
    # (``"success": false`` on the result) serialise as ``"error": null`` and
    # leaves the client no reason for the failure.
    return QueryResponse(
        success=result.success,
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        affected_rows=result.affected_rows,
        elapsed_ms=result.elapsed_ms,
        original_sql=result.original_sql,
        executed_sql=result.executed_sql,
        warnings=result.warnings,
        error=result.error,
    )


@router.get("/context")
async def get_query_context(user: CurrentUser):
    return await query_service.get_context(
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        active_role=_resolve_active_role(user),
    )


@router.get("/completions", response_model=CompletionResponse)
async def get_query_completions(
    kind: str,
    user: CurrentUser,
    prefix: str = "",
    database: str | None = None,
    schema: str | None = None,
    role: str | None = None,
    table: str | None = None,
    stage: str | None = None,
    folder: str | None = None,
):
    result = await query_service.get_completions(
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        kind=kind,
        prefix=prefix,
        database=database,
        schema=schema,
        role=_resolve_active_role(user),
        table=table,
        stage=stage,
        folder=folder,
    )
    return CompletionResponse(**result)


class HistoryItem(BaseModel):
    log_id: str
    query_id: str
    event_time: str
    user_name: str
    object_name: str
    action: str
    sql_text: str
    status: str
    duration_ms: int | None = None
    rows_affected: int | None = None
    error_message: str | None = None
    file_id: str | None = None
    database_name: str | None = None
    schema_name: str | None = None
    session_id: str | None = None


class HistoryResponse(BaseModel):
    items: list[HistoryItem]
    total: int


@router.get("/history", response_model=HistoryResponse)
async def get_query_history(
    user: CurrentUser,
    file_id: str | None = None,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    search: str | None = None,
    database_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_duration_ms: int | None = None,
    user_name: str | None = None,
):
    """Get query execution history for the current user.

    Filter by file_id to get history for a specific workspace file,
    or omit to get all history. Admin users can pass user_name to
    view another user's history.
    """
    effective_user = _history_subject(user, user_name)
    result = await query_service.get_history(
        username=effective_user,
        file_id=file_id,
        status=status,
        limit=limit,
        offset=offset,
        search=search,
        database_name=database_name,
        date_from=date_from,
        date_to=date_to,
        min_duration_ms=min_duration_ms,
    )
    return HistoryResponse(**result)


class HistoryStatsResponse(BaseModel):
    total: int
    avg_duration_ms: float | None = None
    error_count: int
    success_count: int
    error_rate: float


@router.get("/history/stats", response_model=HistoryStatsResponse)
async def get_query_history_stats(
    user: CurrentUser,
    file_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    database_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_duration_ms: int | None = None,
    user_name: str | None = None,
):
    """Get aggregate statistics for query execution history.

    Uses the same filter parameters as the history endpoint.
    """
    effective_user = _history_subject(user, user_name)
    result = await query_service.get_history_stats(
        username=effective_user,
        file_id=file_id,
        status=status,
        search=search,
        database_name=database_name,
        date_from=date_from,
        date_to=date_to,
        min_duration_ms=min_duration_ms,
    )
    return HistoryStatsResponse(**result)
