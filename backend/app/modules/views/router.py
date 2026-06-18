"""Views module — View + Materialized View management."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.common.sql_guard import guard_sql
from app.core.database import db
from app.core.deps import get_current_user
from app.modules.objects.repository import object_repo

router = APIRouter()


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


# ── View Endpoints ─────────────────────────────────────────────


@router.post("/create")
async def create_view(
    req: CreateViewRequest,
    user: dict = Depends(get_current_user),
):
    """Create a standard view."""
    replace = "OR REPLACE " if req.replace else ""
    cols = ""
    if req.columns:
        cols = f"({', '.join(req.columns)})"
    comment = f" COMMENT '{req.comment}'" if req.comment else ""

    sql = f"CREATE {replace}VIEW `{req.database}`.`{req.view_name}`{cols}{comment}\nAS {req.select_sql}"
    guard_sql(sql)

    try:
        await db.execute_system(sql)
        return {"success": True, "message": f"View '{req.database}.{req.view_name}' created"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/create-materialized")
async def create_materialized_view(
    req: CreateMaterializedViewRequest,
    user: dict = Depends(get_current_user),
):
    """Create a materialized view."""
    cols = ""
    if req.columns:
        cols = f"({', '.join(req.columns)})"

    comment = f" COMMENT '{req.comment}'" if req.comment else ""

    props = {**req.properties}
    props_sql = ", ".join(f'"{k}"="{v}"' for k, v in props.items())

    sql = f"CREATE MATERIALIZED VIEW `{req.database}`.`{req.mv_name}`{cols}{comment}\n"
    sql += f"DISTRIBUTED BY {req.distributed_by or 'HASH(*)'} BUCKETS {req.buckets}\n"
    sql += f"REFRESH {req.refresh_strategy}\n"
    sql += f"PROPERTIES({props_sql})\n"
    sql += f"AS {req.select_sql}"

    guard_sql(sql)

    try:
        await db.execute_system(sql)
        return {"success": True, "message": f"Materialized view '{req.database}.{req.mv_name}' created"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/drop")
async def drop_view(
    req: DropViewRequest,
    user: dict = Depends(get_current_user),
):
    """Drop a view or materialized view."""
    mv = "MATERIALIZED " if req.is_materialized else ""
    force = " FORCE" if req.force else ""
    sql = f"DROP {mv}VIEW{force} `{req.database}`.`{req.view_name}`"
    guard_sql(sql)

    try:
        await db.execute_system(sql)
        return {"success": True, "message": f"View '{req.database}.{req.view_name}' dropped"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{database}/{view}/ddl")
async def get_view_ddl(
    database: str,
    view: str,
    user: dict = Depends(get_current_user),
):
    """Get the CREATE VIEW DDL."""
    detail = await object_repo.get_view_detail(database, view)
    if not detail:
        raise HTTPException(status_code=404, detail=f"View '{database}.{view}' not found")
    return {"ddl": detail["ddl"], "database": database, "view": view}
