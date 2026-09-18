"""Views module — View + Materialized View management.

Every DDL statement is built only from allow-listed identifiers and clauses
(``app.common.identifiers``) and executed on the **caller's** StarRocks
connection, so StarRocks RBAC — not the root pool — decides whether the
statement is allowed. ``guard_sql`` still runs on the assembled statement; this
module is a layer above the unchanged guard (NOVA-89).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.common.identifiers import (
    check_column_alias,
    check_comment,
    check_distributed_by,
    check_identifier,
    check_property_key,
    check_property_value,
)
from app.common.sql_guard import guard_sql
from app.core.deps import get_current_user, get_user_connection
from app.modules.objects.repository import object_repo

router = APIRouter()

#: ``Annotated`` dependency aliases (the tree's convention; a ``Depends()`` in
#: an argument default trips ruff B008). DDL runs on the caller's connection.
CurrentUser = Annotated[dict, Depends(get_current_user)]
UserConnection = Annotated[Any, Depends(get_user_connection)]


# ── Schemas ────────────────────────────────────────────────────


class CreateViewRequest(BaseModel):
    database: str
    view_name: str
    select_sql: str = Field(..., description="The SELECT statement for the view")
    columns: list[str] | None = Field(None, description="Optional column aliases")
    comment: str | None = None
    replace: bool = False


class CreateMaterializedViewRequest(BaseModel):
    database: str
    mv_name: str
    select_sql: str = Field(..., description="The SELECT statement for the MV")
    columns: list[str] | None = None
    partition_by: str | None = None
    distributed_by: str | None = None
    buckets: int = 10
    refresh_strategy: str = Field("ASYNC", description="SYNC, ASYNC, MANUAL")
    properties: dict = Field(default_factory=lambda: {"replication_num": "1"})
    comment: str | None = None


class DropViewRequest(BaseModel):
    database: str
    view_name: str
    is_materialized: bool = False
    force: bool = False


def _build_columns(columns: list[str] | None) -> str:
    """Render the optional ``(col, ...)`` alias list from validated aliases."""
    if not columns:
        return ""
    return "(" + ", ".join(check_column_alias(c) for c in columns) + ")"


def _build_comment(comment: str | None) -> str:
    return f" COMMENT '{check_comment(comment)}'" if comment else ""


def _build_properties(properties: dict) -> str:
    """Render ``PROPERTIES(...)`` from allow-listed keys and scalar values.

    Credential-named keys are already refused at other boundaries; here every
    key and value is validated as a token, which is what prevents a value from
    closing the quotes it sits between.
    """
    parts = [
        f'"{check_property_key(k)}"="{check_property_value(str(v))}"'
        for k, v in properties.items()
    ]
    return ", ".join(parts)


# ── View Endpoints ─────────────────────────────────────────────


@router.post("/create")
async def create_view(
    req: CreateViewRequest,
    user: CurrentUser,
    conn: UserConnection,
):
    """Create a standard view on the caller's connection."""
    database = check_identifier(req.database, field="database")
    view_name = check_identifier(req.view_name, field="view name")
    columns = _build_columns(req.columns)
    comment = _build_comment(req.comment)
    replace = "OR REPLACE " if req.replace else ""

    sql = (
        f"CREATE {replace}VIEW `{database}`.`{view_name}`{columns}{comment}\n"
        f"AS {req.select_sql}"
    )
    guard_sql(sql)

    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
        return {"success": True, "message": f"View '{database}.{view_name}' created"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/create-materialized")
async def create_materialized_view(
    req: CreateMaterializedViewRequest,
    user: CurrentUser,
    conn: UserConnection,
):
    """Create a materialized view on the caller's connection."""
    database = check_identifier(req.database, field="database")
    mv_name = check_identifier(req.mv_name, field="materialized view name")
    columns = _build_columns(req.columns)
    comment = _build_comment(req.comment)
    props_sql = _build_properties(req.properties)

    distributed_by = (
        check_distributed_by(req.distributed_by) if req.distributed_by else "HASH(*)"
    )
    if (
        not isinstance(req.buckets, int)
        or isinstance(req.buckets, bool)
        or req.buckets <= 0
    ):
        raise HTTPException(
            status_code=400, detail="buckets must be a positive integer"
        )
    refresh = (req.refresh_strategy or "ASYNC").upper()
    if refresh not in ("SYNC", "ASYNC", "MANUAL"):
        raise HTTPException(
            status_code=400, detail=f"Invalid refresh strategy: {req.refresh_strategy}"
        )

    sql = f"CREATE MATERIALIZED VIEW `{database}`.`{mv_name}`{columns}{comment}\n"
    sql += f"DISTRIBUTED BY {distributed_by} BUCKETS {req.buckets}\n"
    sql += f"REFRESH {refresh}\n"
    sql += f"PROPERTIES({props_sql})\n"
    sql += f"AS {req.select_sql}"

    guard_sql(sql)

    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
        return {
            "success": True,
            "message": f"Materialized view '{database}.{mv_name}' created",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/drop")
async def drop_view(
    req: DropViewRequest,
    user: CurrentUser,
    conn: UserConnection,
):
    """Drop a view or materialized view on the caller's connection."""
    database = check_identifier(req.database, field="database")
    view_name = check_identifier(req.view_name, field="view name")
    mv = "MATERIALIZED " if req.is_materialized else ""
    force = " FORCE" if req.force else ""
    sql = f"DROP {mv}VIEW{force} `{database}`.`{view_name}`"
    guard_sql(sql)

    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
        return {"success": True, "message": f"View '{database}.{view_name}' dropped"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{database}/{view}/ddl")
async def get_view_ddl(
    database: str,
    view: str,
    user: CurrentUser,
):
    """Get the CREATE VIEW DDL."""
    check_identifier(database, field="database")
    check_identifier(view, field="view name")
    detail = await object_repo.get_view_detail(database, view)
    if not detail:
        raise HTTPException(status_code=404, detail=f"View '{database}.{view}' not found")
    return {"ddl": detail["ddl"], "database": database, "view": view}
