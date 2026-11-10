"""Durable notebook metadata in ``NOVA_SYSTEM``.

* ``CONFIG_NOTEBOOK_RUNTIMES``: one row per runtime lifecycle (start, ready,
  stop or failure), written by the worker that owns the runtime.
* ``CONFIG_NOTEBOOK_EXECUTIONS``: one row per finished execution, written once
  by the worker when it ends. Queued and running state lives in Redis, so no
  row is ever updated by two writers.
* ``CONFIG_NOTEBOOK_PACKAGES``: a notebook's requested packages (its manifest),
  restored into every new runtime.

Output payloads are objects in the Workspace bucket; rows keep their key.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.database import db

RUNTIMES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_NOTEBOOK_RUNTIMES (
    id               VARCHAR(64) NOT NULL,
    notebook_id      VARCHAR(64) NOT NULL,
    user_name        VARCHAR(128) NOT NULL,
    status           VARCHAR(24) NOT NULL,
    active_role      VARCHAR(128),
    image            VARCHAR(512),
    cpu              VARCHAR(16),
    memory           VARCHAR(16),
    generation       INT NOT NULL DEFAULT "1",
    error_message    STRING,
    created_at       DATETIME NOT NULL,
    ready_at         DATETIME,
    ended_at         DATETIME,
    end_reason       VARCHAR(32)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

EXECUTIONS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_NOTEBOOK_EXECUTIONS (
    id               VARCHAR(64) NOT NULL,
    notebook_id      VARCHAR(64) NOT NULL,
    user_name        VARCHAR(128) NOT NULL,
    runtime_id       VARCHAR(64) NOT NULL,
    run_id           VARCHAR(64) NOT NULL,
    cell_id          VARCHAR(64) NOT NULL,
    cell_kind        VARCHAR(16) NOT NULL,
    sql_engine       VARCHAR(16),
    source_revision  VARCHAR(80) NOT NULL,
    generation       INT NOT NULL DEFAULT "1",
    execution_count  INT,
    status           VARCHAR(16) NOT NULL,
    active_role      VARCHAR(128),
    error_name       VARCHAR(256),
    error_message    STRING,
    output_key       VARCHAR(1024),
    output_bytes     BIGINT,
    truncated        BOOLEAN NOT NULL DEFAULT "false",
    summary_json     STRING,
    queued_at        DATETIME NOT NULL,
    started_at       DATETIME,
    ended_at         DATETIME,
    duration_ms      BIGINT
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

PACKAGES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_NOTEBOOK_PACKAGES (
    notebook_id      VARCHAR(64) NOT NULL,
    user_name        VARCHAR(128) NOT NULL,
    requirements_json STRING NOT NULL,
    updated_at       DATETIME NOT NULL
) PRIMARY KEY(notebook_id)
DISTRIBUTED BY HASH(notebook_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

_EXEC_COLUMNS = (
    "id, notebook_id, user_name, runtime_id, run_id, cell_id, cell_kind, sql_engine, "
    "source_revision, generation, execution_count, status, active_role, error_name, "
    "error_message, output_key, output_bytes, truncated, summary_json, queued_at, "
    "started_at, ended_at, duration_ms"
)


def _row(columns: str, values: Any) -> dict[str, Any]:
    names = [name.strip() for name in columns.split(",")]
    return dict(zip(names, values, strict=True))


class NotebookRepository:
    async def ensure_schema(self) -> None:
        for ddl in (RUNTIMES_DDL, EXECUTIONS_DDL, PACKAGES_DDL):
            await db.execute_system(ddl)

    # ── Runtimes ──────────────────────────────────────────────────────────

    async def record_runtime(self, row: dict[str, Any]) -> None:
        """Upsert the runtime's lifecycle row (single writer: its worker)."""
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_NOTEBOOK_RUNTIMES (id, notebook_id, user_name, "
            "status, active_role, image, cpu, memory, generation, error_message, created_at, "
            "ready_at, ended_at, end_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "FROM_UNIXTIME(%s), FROM_UNIXTIME(%s), FROM_UNIXTIME(%s), %s)",
            [
                row["id"], row["notebook_id"], row["user_name"], row["status"],
                row.get("active_role"), row.get("image"), row.get("cpu"), row.get("memory"),
                int(row.get("generation") or 1), row.get("error_message"),
                row["created_at"], row.get("ready_at") or None, row.get("ended_at") or None,
                row.get("end_reason"),
            ],
        )

    # ── Executions ────────────────────────────────────────────────────────

    async def record_execution(self, row: dict[str, Any]) -> None:
        await db.execute_system(
            f"INSERT INTO NOVA_SYSTEM.CONFIG_NOTEBOOK_EXECUTIONS ({_EXEC_COLUMNS}) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "FROM_UNIXTIME(%s), FROM_UNIXTIME(%s), FROM_UNIXTIME(%s), %s)",
            [
                row["id"], row["notebook_id"], row["user_name"], row["runtime_id"],
                row["run_id"], row["cell_id"], row["cell_kind"], row.get("sql_engine"),
                row["source_revision"], int(row.get("generation") or 1),
                row.get("execution_count"), row["status"], row.get("active_role"),
                (row.get("error_name") or None) and str(row["error_name"])[:256],
                row.get("error_message"), row.get("output_key"), row.get("output_bytes"),
                bool(row.get("truncated")), json.dumps(row.get("summary") or {}),
                row["queued_at"], row.get("started_at") or None, row.get("ended_at") or None,
                row.get("duration_ms"),
            ],
        )

    async def get_execution(self, user: str, notebook_id: str, execution_id: str) -> dict | None:
        result = await db.execute_system(
            f"SELECT {_EXEC_COLUMNS} FROM NOVA_SYSTEM.CONFIG_NOTEBOOK_EXECUTIONS "
            "WHERE id = %s AND user_name = %s AND notebook_id = %s",
            [execution_id, user, notebook_id],
        )
        rows = result.get("rows") or []
        return _decode(_row(_EXEC_COLUMNS, rows[0])) if rows else None

    async def latest_executions(self, user: str, notebook_id: str) -> list[dict[str, Any]]:
        """The newest finished execution of each cell of a notebook."""
        result = await db.execute_system(
            f"SELECT {_EXEC_COLUMNS} FROM ("
            f"  SELECT {_EXEC_COLUMNS}, ROW_NUMBER() OVER ("
            "    PARTITION BY cell_id ORDER BY ended_at DESC, queued_at DESC) AS rn"
            "  FROM NOVA_SYSTEM.CONFIG_NOTEBOOK_EXECUTIONS"
            "  WHERE user_name = %s AND notebook_id = %s"
            ") latest WHERE rn = 1",
            [user, notebook_id],
        )
        return [_decode(_row(_EXEC_COLUMNS, row)) for row in result.get("rows") or []]

    # ── Packages ──────────────────────────────────────────────────────────

    async def get_packages(self, user: str, notebook_id: str) -> list[dict[str, Any]]:
        result = await db.execute_system(
            "SELECT requirements_json FROM NOVA_SYSTEM.CONFIG_NOTEBOOK_PACKAGES "
            "WHERE notebook_id = %s AND user_name = %s",
            [notebook_id, user],
        )
        rows = result.get("rows") or []
        if not rows:
            return []
        try:
            value = json.loads(rows[0][0] or "[]")
        except ValueError:
            return []
        return value if isinstance(value, list) else []

    async def set_packages(self, user: str, notebook_id: str, packages: list[dict]) -> None:
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_NOTEBOOK_PACKAGES (notebook_id, user_name, "
            "requirements_json, updated_at) VALUES (%s, %s, %s, NOW())",
            [notebook_id, user, json.dumps(packages)],
        )


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    try:
        row["summary"] = json.loads(row.pop("summary_json") or "{}")
    except ValueError:
        row["summary"] = {}
    for key in ("queued_at", "started_at", "ended_at"):
        value = row.get(key)
        if value is not None and hasattr(value, "isoformat"):
            row[key] = value.isoformat()
    row["truncated"] = bool(row.get("truncated"))
    return row


notebook_repository = NotebookRepository()
