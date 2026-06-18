"""Tables module — DDL operations for StarRocks tables."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.common.sql_guard import guard_sql
from app.core.database import db
from app.core.deps import get_current_user
from app.modules.objects.repository import object_repo

router = APIRouter()


# ── Schemas ────────────────────────────────────────────────────


class CreateTableRequest(BaseModel):
    database: str
    table: str
    columns: list[dict]  # [{"name": "id", "type": "INT", "nullable": false, "key": "primary"}]
    engine: str = Field("olap", description="olap, mysql, elasticsearch, hive, iceberg, jdbc")
    keys: list[str] = Field(default_factory=list, description="Primary key columns")
    distributed_by: str = Field("HASH(id)", description="Distribution strategy")
    buckets: int = Field(10, description="Number of buckets")
    partition_by: str | None = None
    partition_values: list[str] | None = None
    properties: dict = Field(default_factory=lambda: {"replication_num": "1"})
    comment: str | None = None


class AlterTableRequest(BaseModel):
    database: str
    table: str
    action: str = Field(..., description="ADD_COLUMN, DROP_COLUMN, MODIFY_COLUMN, RENAME, ADD_PARTITION, DROP_PARTITION")
    column_name: str | None = None
    column_type: str | None = None
    new_name: str | None = None
    partition_name: str | None = None
    partition_value: str | None = None


class DropTableRequest(BaseModel):
    database: str
    table: str
    force: bool = False


# ── Endpoints ──────────────────────────────────────────────────


@router.post("/create")
async def create_table(
    req: CreateTableRequest,
    user: dict = Depends(get_current_user),
):
    """Create a new table.

    Translates the structured request into StarRocks DDL and executes it.
    """
    # Build column definitions
    col_defs = []
    for col in req.columns:
        nullable = "NULL" if col.get("nullable", True) else "NOT NULL"
        default = f"DEFAULT '{col['default']}'" if col.get("default") else ""
        col_defs.append(f"    `{col['name']}` {col['type']} {nullable} {default}".rstrip())

    cols_sql = ",\n".join(col_defs)

    # Primary key
    pk_sql = ""
    if req.keys:
        pk_cols = ", ".join(f"`{k}`" for k in req.keys)
        pk_sql = f"\nPRIMARY KEY({pk_cols})"

    # Properties
    props = {**req.properties}
    props_sql = ", ".join(f'"{k}"="{v}"' for k, v in props.items())

    # Comment
    comment_sql = f"\nCOMMENT '{req.comment}'" if req.comment else ""

    # Build DDL (StarRocks doesn't use ENGINE = syntax like MySQL)
    ddl = f"""CREATE TABLE `{req.database}`.`{req.table}` (
{cols_sql}
){pk_sql}{comment_sql}
DISTRIBUTED BY {req.distributed_by} BUCKETS {req.buckets}
PROPERTIES({props_sql})"""

    # Partition (if specified)
    if req.partition_by:
        # Insert partition clause before DISTRIBUTED
        ddl = ddl.replace(
            f"DISTRIBUTED BY",
            f"PARTITION BY {req.partition_by}\nDISTRIBUTED BY",
            1
        )

    guard_sql(ddl)

    try:
        await db.execute_system(ddl)
        return {"success": True, "ddl": ddl, "message": f"Table '{req.database}.{req.table}' created"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/alter")
async def alter_table(
    req: AlterTableRequest,
    user: dict = Depends(get_current_user),
):
    """Alter a table — add/drop/modify columns, rename, partitions."""
    action = req.action.upper()
    db_name = req.database
    tbl = req.table

    if action == "ADD_COLUMN":
        if not req.column_name or not req.column_type:
            raise HTTPException(status_code=400, detail="column_name and column_type required")
        sql = f"ALTER TABLE `{db_name}`.`{tbl}` ADD COLUMN `{req.column_name}` {req.column_type}"

    elif action == "DROP_COLUMN":
        if not req.column_name:
            raise HTTPException(status_code=400, detail="column_name required")
        sql = f"ALTER TABLE `{db_name}`.`{tbl}` DROP COLUMN `{req.column_name}`"

    elif action == "MODIFY_COLUMN":
        if not req.column_name or not req.column_type:
            raise HTTPException(status_code=400, detail="column_name and column_type required")
        sql = f"ALTER TABLE `{db_name}`.`{tbl}` MODIFY COLUMN `{req.column_name}` {req.column_type}"

    elif action == "RENAME":
        if not req.new_name:
            raise HTTPException(status_code=400, detail="new_name required")
        sql = f"ALTER TABLE `{db_name}`.`{tbl}` RENAME `{req.new_name}`"

    elif action == "ADD_PARTITION":
        if not req.partition_name or not req.partition_value:
            raise HTTPException(status_code=400, detail="partition_name and partition_value required")
        sql = f"ALTER TABLE `{db_name}`.`{tbl}` ADD PARTITION `{req.partition_name}` VALUES {req.partition_value}"

    elif action == "DROP_PARTITION":
        if not req.partition_name:
            raise HTTPException(status_code=400, detail="partition_name required")
        sql = f"ALTER TABLE `{db_name}`.`{tbl}` DROP PARTITION `{req.partition_name}`"

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    guard_sql(sql)

    try:
        await db.execute_system(sql)
        return {"success": True, "sql": sql, "message": f"Table '{db_name}.{tbl}' altered ({action})"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/drop")
async def drop_table(
    req: DropTableRequest,
    user: dict = Depends(get_current_user),
):
    """Drop a table."""
    force_sql = " FORCE" if req.force else ""
    sql = f"DROP TABLE{force_sql} `{req.database}`.`{req.table}`"
    guard_sql(sql)

    try:
        await db.execute_system(sql)
        return {"success": True, "message": f"Table '{req.database}.{req.table}' dropped"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{database}/{table}/ddl")
async def get_table_ddl(
    database: str,
    table: str,
    user: dict = Depends(get_current_user),
):
    """Get the CREATE TABLE DDL for a table."""
    detail = await object_repo.get_table_detail(database, table)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Table '{database}.{table}' not found")
    return {"ddl": detail["ddl"], "database": database, "table": table}
