"""Tables module — DDL operations for StarRocks tables.

Every statement is built from allow-listed identifiers, column types and
distribution/partition clauses (``app.common.identifiers``) and executed on
the **caller's** StarRocks connection, so the engine's RBAC decides whether the
operation is permitted. ``guard_sql`` still runs on the assembled statement;
this module is a layer above the unchanged guard (NOVA-89).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.common.identifiers import (
    check_column_type,
    check_comment,
    check_distributed_by,
    check_identifier,
    check_partition_by,
    check_partition_value,
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


class CreateTableRequest(BaseModel):
    database: str
    table: str
    columns: list[dict]  # [{"name": "id", "type": "INT", "nullable": false, "key": "primary"}]
    engine: str = Field(
        "olap", description="olap, mysql, elasticsearch, hive, iceberg, jdbc"
    )
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
    action: str = Field(
        ...,
        description=(
            "ADD_COLUMN, DROP_COLUMN, MODIFY_COLUMN, RENAME, "
            "ADD_PARTITION, DROP_PARTITION"
        ),
    )
    column_name: str | None = None
    column_type: str | None = None
    new_name: str | None = None
    partition_name: str | None = None
    partition_value: str | None = None


class DropTableRequest(BaseModel):
    database: str
    table: str
    force: bool = False


# ── Builders ───────────────────────────────────────────────────


def _build_columns(columns: list[dict]) -> str:
    """Render column definitions, validating name, type, nullability, default."""
    col_defs = []
    for col in columns:
        name = check_identifier(str(col.get("name", "")), field="column name")
        col_type = check_column_type(str(col.get("type", "")))
        nullable = "NULL" if col.get("nullable", True) else "NOT NULL"
        default = ""
        if col.get("default") is not None:
            default = f"DEFAULT '{check_comment(str(col['default']))}'"
        col_defs.append(f"    `{name}` {col_type} {nullable} {default}".rstrip())
    return ",\n".join(col_defs)


def _build_properties(properties: dict) -> str:
    parts = [
        f'"{check_property_key(k)}"="{check_property_value(str(v))}"'
        for k, v in properties.items()
    ]
    return ", ".join(parts)


# ── Endpoints ──────────────────────────────────────────────────


@router.post("/create")
async def create_table(
    req: CreateTableRequest,
    user: CurrentUser,
    conn: UserConnection,
):
    """Create a new table on the caller's connection."""
    database = check_identifier(req.database, field="database")
    table = check_identifier(req.table, field="table name")
    cols_sql = _build_columns(req.columns)

    pk_sql = ""
    if req.keys:
        pk_cols = ", ".join(check_identifier(k, field="key column") for k in req.keys)
        pk_sql = f"\nPRIMARY KEY({pk_cols})"

    props_sql = _build_properties(req.properties)
    comment_sql = (
        f"\nCOMMENT '{check_comment(req.comment)}'" if req.comment else ""
    )
    distributed_by = check_distributed_by(req.distributed_by)
    if (
        not isinstance(req.buckets, int)
        or isinstance(req.buckets, bool)
        or req.buckets <= 0
    ):
        raise HTTPException(
            status_code=400, detail="buckets must be a positive integer"
        )

    ddl = f"""CREATE TABLE `{database}`.`{table}` (
{cols_sql}
){pk_sql}{comment_sql}
DISTRIBUTED BY {distributed_by} BUCKETS {req.buckets}
PROPERTIES({props_sql})"""

    if req.partition_by:
        partition_by = check_partition_by(req.partition_by)
        ddl = ddl.replace(
            "DISTRIBUTED BY",
            f"PARTITION BY {partition_by}\nDISTRIBUTED BY",
            1,
        )

    guard_sql(ddl)

    try:
        async with conn.cursor() as cur:
            await cur.execute(ddl)
        return {
            "success": True,
            "ddl": ddl,
            "message": f"Table '{database}.{table}' created",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/alter")
async def alter_table(
    req: AlterTableRequest,
    user: CurrentUser,
    conn: UserConnection,
):
    """Alter a table — add/drop/modify columns, rename, partitions."""
    action = req.action.upper()
    db_name = check_identifier(req.database, field="database")
    tbl = check_identifier(req.table, field="table name")

    if action == "ADD_COLUMN":
        if not req.column_name or not req.column_type:
            raise HTTPException(
                status_code=400, detail="column_name and column_type required"
            )
        column_name = check_identifier(req.column_name, field="column name")
        column_type = check_column_type(req.column_type)
        sql = f"ALTER TABLE `{db_name}`.`{tbl}` ADD COLUMN `{column_name}` {column_type}"

    elif action == "DROP_COLUMN":
        if not req.column_name:
            raise HTTPException(status_code=400, detail="column_name required")
        column_name = check_identifier(req.column_name, field="column name")
        sql = f"ALTER TABLE `{db_name}`.`{tbl}` DROP COLUMN `{column_name}`"

    elif action == "MODIFY_COLUMN":
        if not req.column_name or not req.column_type:
            raise HTTPException(
                status_code=400, detail="column_name and column_type required"
            )
        column_name = check_identifier(req.column_name, field="column name")
        column_type = check_column_type(req.column_type)
        sql = f"ALTER TABLE `{db_name}`.`{tbl}` MODIFY COLUMN `{column_name}` {column_type}"

    elif action == "RENAME":
        if not req.new_name:
            raise HTTPException(status_code=400, detail="new_name required")
        new_name = check_identifier(req.new_name, field="new table name")
        sql = f"ALTER TABLE `{db_name}`.`{tbl}` RENAME `{new_name}`"

    elif action == "ADD_PARTITION":
        if not req.partition_name or not req.partition_value:
            raise HTTPException(
                status_code=400, detail="partition_name and partition_value required"
            )
        partition_name = check_identifier(req.partition_name, field="partition name")
        partition_value = check_partition_value(req.partition_value)
        sql = (
            f"ALTER TABLE `{db_name}`.`{tbl}` ADD PARTITION "
            f"`{partition_name}` VALUES {partition_value}"
        )

    elif action == "DROP_PARTITION":
        if not req.partition_name:
            raise HTTPException(status_code=400, detail="partition_name required")
        partition_name = check_identifier(req.partition_name, field="partition name")
        sql = f"ALTER TABLE `{db_name}`.`{tbl}` DROP PARTITION `{partition_name}`"

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    guard_sql(sql)

    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
        return {
            "success": True,
            "sql": sql,
            "message": f"Table '{db_name}.{tbl}' altered ({action})",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/drop")
async def drop_table(
    req: DropTableRequest,
    user: CurrentUser,
    conn: UserConnection,
):
    """Drop a table on the caller's connection."""
    database = check_identifier(req.database, field="database")
    table = check_identifier(req.table, field="table name")
    force_sql = " FORCE" if req.force else ""
    sql = f"DROP TABLE{force_sql} `{database}`.`{table}`"
    guard_sql(sql)

    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
        return {"success": True, "message": f"Table '{database}.{table}' dropped"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{database}/{table}/ddl")
async def get_table_ddl(
    database: str,
    table: str,
    user: CurrentUser,
):
    """Get the CREATE TABLE DDL for a table."""
    check_identifier(database, field="database")
    check_identifier(table, field="table name")
    detail = await object_repo.get_table_detail(database, table)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Table '{database}.{table}' not found")
    return {"ddl": detail["ddl"], "database": database, "table": table}
