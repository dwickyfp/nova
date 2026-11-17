"""``SHOW PIPES``, ``DESC PIPE`` and ``information_schema.pipes`` / ``pipe_files``.

Pipes live in Nova's metadata, not in the engine, so their rows are rendered as
a typed derived table and the caller's own SQL (``WHERE``, ``ORDER BY``,
``LIMIT``, joins, projections) runs over it on the engine as the caller. Column
names are the StarRocks ones; ``OWNER_ROLE`` is added because a Nova pipe
belongs to a role. Only the pipes of the caller's active role are rendered
(administrators see every pipe).
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

PIPES_COLUMNS: tuple[tuple[str, str], ...] = (
    ("DATABASE_NAME", "VARCHAR(256)"), ("PIPE_ID", "VARCHAR(64)"),
    ("PIPE_NAME", "VARCHAR(256)"), ("PROPERTIES", "VARCHAR(1024)"),
    ("TABLE_NAME", "VARCHAR(512)"), ("STATE", "VARCHAR(16)"),
    ("LOAD_STATUS", "VARCHAR(1024)"), ("LAST_ERROR", "VARCHAR(4096)"),
    ("CREATED_TIME", "DATETIME"), ("OWNER_ROLE", "VARCHAR(128)"),
)
SHOW_COLUMNS = ("DATABASE_NAME", "PIPE_ID", "PIPE_NAME", "TABLE_NAME", "STATE",
                "LOAD_STATUS", "LAST_ERROR", "CREATED_TIME", "OWNER_ROLE")
FILES_COLUMNS: tuple[tuple[str, str], ...] = (
    ("DATABASE_NAME", "VARCHAR(256)"), ("PIPE_ID", "VARCHAR(64)"),
    ("PIPE_NAME", "VARCHAR(256)"), ("FILE_NAME", "VARCHAR(2048)"),
    ("FILE_VERSION", "VARCHAR(256)"), ("FILE_SIZE", "BIGINT"),
    ("LAST_MODIFIED", "DATETIME"), ("LOAD_STATE", "VARCHAR(16)"),
    ("STAGED_TIME", "DATETIME"), ("START_LOAD_TIME", "DATETIME"),
    ("FINISH_LOAD_TIME", "DATETIME"), ("ERROR_MSG", "VARCHAR(4096)"),
)
DESC_COLUMNS = ("DATABASE_NAME", "PIPE_ID", "PIPE_NAME", "TABLE_NAME", "SOURCE", "SQL",
                "PROPERTIES", "STATE", "OWNER_ROLE", "LAST_ERROR")
VIEWS = {"pipes": PIPES_COLUMNS, "pipe_files": FILES_COLUMNS}


def _quote(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def literal(value: Any, sql_type: str) -> str:
    if value is None:
        return f"CAST(NULL AS {sql_type})"
    if sql_type == "BIGINT":
        return f"CAST({int(value)} AS BIGINT)"
    if sql_type == "DATETIME":
        return f"CAST({_quote(str(value))} AS DATETIME)"
    return f"CAST({_quote(str(value))} AS {sql_type})"


def relation_sql(columns: tuple[tuple[str, str], ...], rows: list[list[Any]]) -> str:
    """A typed derived table, ``(SELECT ...)``, with ``columns`` as its names."""
    names = [name for name, _ in columns]
    if not rows:
        return "(SELECT " + ", ".join(
            f"{literal(None, sql_type)} AS `{name}`" for name, sql_type in columns
        ) + " WHERE 1 = 0)"
    inner = [f"__c{index}" for index in range(len(columns))]
    values = ", ".join(
        "(" + ", ".join(literal(value, sql_type)
                        for value, (_, sql_type) in zip(row, columns, strict=True)) + ")"
        for row in rows
    )
    return (
        "(SELECT " + ", ".join(f"{alias} AS `{name}`"
                               for alias, name in zip(inner, names, strict=True))
        + f" FROM (VALUES {values}) AS __nova_pipe_rows({', '.join(inner)}))"
    )


def _time(value: datetime | None, zone: str | None) -> str | None:
    if value is None:
        return None
    if zone:
        with contextlib.suppress(ValueError, KeyError):
            value = value.astimezone(ZoneInfo(zone))
    return value.strftime("%Y-%m-%d %H:%M:%S")


def pipe_rows(rows: list[dict[str, Any]], status: dict[str, dict[str, Any]],
              zone: str | None) -> list[list[Any]]:
    from app.modules.pipes.control import properties_of

    out = []
    for row in rows:
        counts = status.get(str(row["id"]), {})
        load_status = {
            "loadedFiles": counts.get("FINISHED", 0),
            "loadedBytes": counts.get("loaded_bytes", 0),
            "loadingFiles": counts.get("LOADING", 0) + counts.get("UNLOADED", 0),
            "errorFiles": counts.get("ERROR", 0),
            "loadedRows": counts.get("loaded_rows", 0),
            "LastLoadedTime": _time(counts.get("last_loaded"), zone),
        }
        out.append([
            row["database_name"], str(row["id"]), row["name"],
            json.dumps(properties_of(row).as_text(), separators=(",", ":")),
            row["target_table"], row["state"], json.dumps(load_status, separators=(",", ":")),
            row["last_error"], _time(row["created_at"], zone), row["owner_role"],
        ])
    return out


def file_rows(pipes: list[dict[str, Any]], files: list[dict[str, Any]],
              zone: str | None) -> list[list[Any]]:
    from app.modules.pipes.control import logical_file

    by_id = {str(row["id"]): row for row in pipes}
    out = []
    for item in files:
        pipe = by_id[str(item["pipe_id"])]
        out.append([
            pipe["database_name"], str(pipe["id"]), pipe["name"],
            logical_file(pipe, item["file_name"]), item["file_version"], item["file_size"],
            _time(item["last_modified"], zone), item["load_state"],
            _time(item["staged_time"], zone), _time(item["start_load_time"], zone),
            _time(item["finish_load_time"], zone), item["error_msg"],
        ])
    return out


def describe_row(row: dict[str, Any]) -> list[Any]:
    from app.modules.pipes.control import properties_of, source_of

    source = source_of(row)
    files = source["stage_label"] + "/" + "/".join(source["segments"])
    return [
        row["database_name"], str(row["id"]), row["name"], row["target_table"], files,
        row["definition"], json.dumps(properties_of(row).as_text(), separators=(",", ":")),
        row["state"], row["owner_role"], row["last_error"],
    ]


def show_sql(rows: list[list[Any]], *, like: str | None, where: str | None,
             order: str | None, limit: str | None) -> str:
    """SHOW PIPES as a query: ``NAME`` and ``STATE`` filter as in StarRocks."""
    relation = relation_sql(
        PIPES_COLUMNS + (("NAME", "VARCHAR(256)"),),
        [row + [row[2]] for row in rows],
    )
    sql = "SELECT " + ", ".join(f"`{name}`" for name in SHOW_COLUMNS) + f" FROM {relation} AS pipes"
    conditions = []
    if like is not None:
        conditions.append(f"`PIPE_NAME` LIKE {_quote(like)}")
    if where:
        conditions.append(f"({where})")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += f" ORDER BY {order}" if order else " ORDER BY `DATABASE_NAME`, `PIPE_NAME`"
    if limit:
        sql += " " + limit
    return sql


def information_schema_refs(parsed: Any) -> list[tuple[str, Any]]:
    """Table references to ``information_schema.pipes`` or ``.pipe_files``."""
    from app.sql_frontend.antlr_utils import walk_nodes
    from app.sql_frontend.streams import name_parts

    found = []
    for node in walk_nodes(parsed.statement_context):
        if type(node).__name__ != "TableAtomContext":
            continue
        parts = [part.lower() for part in name_parts(node.qualifiedName())]
        if parts[:1] == ["default_catalog"]:
            parts = parts[1:]
        if len(parts) == 2 and parts[0] == "information_schema" and parts[1] in VIEWS:
            found.append((parts[1], node))
    return found


def summary(row: dict[str, Any], status: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A pipe as the Database Explorer lists and shows it."""
    from app.modules.pipes.control import properties_of

    counts = status.get(str(row["id"]), {})
    properties = properties_of(row).as_text()
    load_status = {
        "loadedFiles": counts.get("FINISHED", 0),
        "loadedBytes": counts.get("loaded_bytes", 0),
        "loadingFiles": counts.get("LOADING", 0) + counts.get("UNLOADED", 0),
        "errorFiles": counts.get("ERROR", 0),
        "loadedRows": counts.get("loaded_rows", 0),
    }
    options = ", ".join(f'"{key}" = "{value}"' for key, value in properties.items())
    return {
        "name": row["name"], "database": row["database_name"], "pipe_id": str(row["id"]),
        "state": row["state"], "target_table": row["target_table"],
        "properties": properties, "load_status": json.dumps(load_status),
        "last_error": row["last_error"], "created_time": row["created_at"],
        "owner_role": row["owner_role"],
        "create_ddl": f"CREATE PIPE {row['database_name']}.{row['name']}\n"
                      f"PROPERTIES ({options})\nAS\n{row['definition']}",
    }


async def explorer_pipes(user: dict[str, Any], database: str) -> list[dict[str, Any]]:
    from app.modules.pipes.control import PipeCaller, pipe_control

    caller = PipeCaller(str(user.get("username")), user.get("active_role"),
                        user.get("session_id"), int(user.get("security_context_version") or 1))
    rows, status, _ = await pipe_control.visible(caller, database)
    return [summary(row, status) for row in rows]
