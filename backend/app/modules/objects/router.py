"""Object Browser API router — database/table/view browsing endpoints.

These endpoints power the Snowsight-style sidebar tree:
  GET /databases          → list all databases
  GET /databases/{name}   → database detail + object counts
  GET /objects/{db}       → list objects in database (tables, views, MVs)
  GET /tables/{db}/{tbl}  → table detail (columns, DDL, status)
  GET /views/{db}/{vw}    → view detail (columns, definition)
  GET /columns/{db}/{tbl} → column list
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.modules.objects.service import object_service

router = APIRouter()


# ── Databases ──────────────────────────────────────────────────


@router.get("/databases")
async def list_databases(
    role: str | None = None,
    user: dict = Depends(get_current_user),
):
    """List all databases with object counts (sidebar root)."""
    databases = await object_service.browse_tree(
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        role=role,
    )
    return {"databases": databases, "count": len(databases)}


@router.get("/databases/{database}")
async def get_database(
    database: str,
    role: str | None = None,
    user: dict = Depends(get_current_user),
):
    """Get database detail with object breakdown."""
    detail = await object_service.get_database_detail(
        database,
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        role=role,
    )
    if not detail:
        raise HTTPException(status_code=404, detail=f"Database '{database}' not found")
    return detail


# ── Objects (tables + views + MVs) ────────────────────────────


@router.get("/databases/{database}/objects")
async def list_objects(
    database: str,
    type: str = "all",
    role: str | None = None,
    user: dict = Depends(get_current_user),
):
    """List objects in a database. Filter by type: all, table, view, materialized_view."""
    if type not in ("all", "table", "view", "materialized_view"):
        raise HTTPException(status_code=400, detail=f"Invalid type: {type}")
    return await object_service.list_objects(
        database,
        type,
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        role=role,
    )


# ── Tables ─────────────────────────────────────────────────────


@router.get("/databases/{database}/tables")
async def list_tables(
    database: str,
    role: str | None = None,
    user: dict = Depends(get_current_user),
):
    """List all tables in a database."""
    tables = await object_service.list_objects(
        database,
        "table",
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        role=role,
    )
    return {"tables": tables["tables"], "count": len(tables["tables"])}


@router.get("/databases/{database}/tables/{table}")
async def get_table(
    database: str,
    table: str,
    role: str | None = None,
    user: dict = Depends(get_current_user),
):
    """Get table detail — columns, DDL, status."""
    detail = await object_service.get_table_detail(
        database,
        table,
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        role=role,
    )
    if not detail:
        raise HTTPException(status_code=404, detail=f"Table '{database}.{table}' not found")
    return detail


# ── Views ──────────────────────────────────────────────────────


@router.get("/databases/{database}/views")
async def list_views(
    database: str,
    role: str | None = None,
    user: dict = Depends(get_current_user),
):
    """List all views in a database."""
    views = await object_service.list_objects(
        database,
        "view",
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        role=role,
    )
    return {"views": views["views"], "count": len(views["views"])}


@router.get("/databases/{database}/views/{view}")
async def get_view(
    database: str,
    view: str,
    role: str | None = None,
    user: dict = Depends(get_current_user),
):
    """Get view detail — definition, columns."""
    detail = await object_service.get_view_detail(
        database,
        view,
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        role=role,
    )
    if not detail:
        raise HTTPException(status_code=404, detail=f"View '{database}.{view}' not found")
    return detail


# ── Columns ────────────────────────────────────────────────────


@router.get("/databases/{database}/tables/{table}/columns")
async def get_columns(
    database: str,
    table: str,
    role: str | None = None,
    user: dict = Depends(get_current_user),
):
    """Get column list for a table or view."""
    columns = await object_service.get_columns(
        database,
        table,
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        role=role,
    )
    return {"columns": columns, "count": len(columns)}


@router.get("/databases/{database}/tables/{table}/ddl")
async def get_table_ddl(
    database: str,
    table: str,
    role: str | None = None,
    user: dict = Depends(get_current_user),
):
    """Get the CREATE TABLE DDL for a table."""
    detail = await object_service.get_table_detail(
        database,
        table,
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        role=role,
    )
    if not detail:
        raise HTTPException(status_code=404, detail=f"Table '{database}.{table}' not found")
    return {"ddl": detail["ddl"], "database": database, "table": table}


@router.get("/databases/{database}/schemas")
async def list_schemas(
    database: str,
    role: str | None = None,
    user: dict = Depends(get_current_user),
):
    schemas = await object_service.list_schemas(
        database,
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        role=role,
    )
    return {"schemas": schemas, "count": len(schemas)}


@router.get("/databases/{database}/schemas/{schema}/tree")
async def get_schema_tree(
    database: str,
    schema: str,
    role: str | None = None,
    user: dict = Depends(get_current_user),
):
    return await object_service.get_schema_tree(
        database,
        schema,
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        role=role,
    )
