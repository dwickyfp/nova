"""Persisted Studio artifacts backed by reproducible, read-only SQL.

An artifact stores presentation metadata and the redacted statement needed to
rebuild it. Query rows are deliberately absent from the table: opening an
artifact executes the statement again with the caller's current StarRocks
session, so both data and RBAC remain current.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from app.common.sql_guard import redact_sql_credentials, split_sql_statements
from app.core.database import db
from app.modules.agents.tools.data_to_chart import sanitize_chart_spec
from app.modules.assistant.tools.policy import classify_statements

ARTIFACTS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS (
    artifact_id  VARCHAR(64) NOT NULL,
    owner_name   VARCHAR(128) NOT NULL,
    agent_id     VARCHAR(64),
    thread_id    VARCHAR(64),
    title        VARCHAR(256) NOT NULL,
    artifact_type VARCHAR(16) NOT NULL,
    sql_text     TEXT NOT NULL,
    database_name VARCHAR(128),
    schema_name  VARCHAR(128),
    chart_spec   JSON,
    created_at   DATETIME NOT NULL,
    updated_at   DATETIME NOT NULL
) PRIMARY KEY(artifact_id)
DISTRIBUTED BY HASH(artifact_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

_COLUMNS = (
    "artifact_id, owner_name, agent_id, thread_id, title, artifact_type, "
    "sql_text, database_name, schema_name, chart_spec, created_at, updated_at"
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value[:19], fmt)
            except ValueError:
                continue
    return _now()


def _json_object(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def validate_artifact_sql(sql: str) -> str:
    """Return a redacted, single read-only statement suitable for storage."""
    statements = split_sql_statements(sql)
    if len(statements) != 1:
        raise ValueError("An artifact must be backed by one read-only SQL statement.")
    classification, decisions = classify_statements(statements)
    if classification != "read_only" or not all(item.allowed for item in decisions):
        raise ValueError("Only read-only SQL can be saved as an artifact.")
    return redact_sql_credentials(statements[0]).strip()


def chart_template(spec: object, *, title: str) -> dict[str, Any]:
    """Sanitize a Vega-Lite spec and remove every embedded row.

    The returned template is safe to persist. Fresh query rows are injected by
    the client only after the artifact refresh endpoint has executed its SQL.
    """
    parsed = _json_object(spec)
    safe = sanitize_chart_spec(parsed, title=title) if parsed else None
    if safe is None:
        raise ValueError("The chart specification is not a supported Vega-Lite chart.")
    safe.pop("datasets", None)
    safe["data"] = {"values": []}
    return safe


def _row(values: list[object] | tuple[object, ...]) -> dict[str, Any]:
    (
        artifact_id,
        owner_name,
        agent_id,
        thread_id,
        title,
        artifact_type,
        sql_text,
        database_name,
        schema_name,
        chart_spec,
        created_at,
        updated_at,
    ) = values
    return {
        "artifact_id": str(artifact_id),
        "owner_name": str(owner_name),
        "agent_id": str(agent_id) if agent_id else None,
        "thread_id": str(thread_id) if thread_id else None,
        "title": str(title),
        "artifact_type": str(artifact_type),
        "sql_text": str(sql_text),
        "database_name": str(database_name) if database_name else None,
        "schema_name": str(schema_name) if schema_name else None,
        "chart_spec": _json_object(chart_spec),
        "created_at": _datetime(created_at),
        "updated_at": _datetime(updated_at),
    }


class ArtifactRepository:
    async def ensure_schema(self) -> None:
        await db.execute_system(ARTIFACTS_DDL)

    async def create(
        self,
        *,
        owner_name: str,
        title: str,
        artifact_type: Literal["chart", "table"],
        sql_text: str,
        database_name: str | None,
        schema_name: str | None,
        chart_spec: object | None = None,
        agent_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        artifact_id = str(uuid4())
        now = _now()
        stored_sql = validate_artifact_sql(sql_text)
        stored_chart = chart_template(chart_spec, title=title) if artifact_type == "chart" else None
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS ("
            "artifact_id, owner_name, agent_id, thread_id, title, artifact_type, "
            "sql_text, database_name, schema_name, chart_spec, created_at, updated_at"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                artifact_id,
                owner_name,
                agent_id,
                thread_id,
                title,
                artifact_type,
                stored_sql,
                database_name,
                schema_name,
                json.dumps(stored_chart, separators=(",", ":")) if stored_chart else None,
                now,
                now,
            ],
        )
        created = await self.get(artifact_id, owner_name=owner_name)
        assert created is not None
        return created

    async def list(self, *, owner_name: str) -> list[dict[str, Any]]:
        result = await db.execute_system(
            f"SELECT {_COLUMNS} FROM NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS "
            "WHERE owner_name = %s ORDER BY updated_at DESC",
            [owner_name],
        )
        return [_row(row) for row in result["rows"]]

    async def get(self, artifact_id: str, *, owner_name: str) -> dict[str, Any] | None:
        result = await db.execute_system(
            f"SELECT {_COLUMNS} FROM NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS "
            "WHERE artifact_id = %s AND owner_name = %s",
            [artifact_id, owner_name],
        )
        if not result["rows"]:
            return None
        return _row(result["rows"][0])

    async def delete(self, artifact_id: str, *, owner_name: str) -> bool:
        result = await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS "
            "WHERE artifact_id = %s AND owner_name = %s",
            [artifact_id, owner_name],
        )
        return bool(result.get("affected", 0))


artifact_repository = ArtifactRepository()
