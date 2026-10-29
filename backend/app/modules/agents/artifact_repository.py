"""Persisted Studio artifacts backed by reproducible, read-only SQL.

An artifact stores presentation metadata and the redacted statement needed to
rebuild it. Query rows are deliberately absent from the table: opening an
artifact re-executes under the caller's current StarRocks session, so both
data and RBAC remain current.

Two artifact kinds exist:

* ``chart`` — a worksheet (``viz_spec``, the worksheet model in
  :mod:`app.modules.viz.spec`) plus the read-only source statement that
  rebuilds it. Charts saved before the viz core carry a sanitized Vega-Lite
  ``chart_spec`` instead and render through the legacy path until re-saved.
* ``dashboard`` — a zone layout (``viz_spec`` is a ``DashboardSpec``) over
  chart artifacts owned by the same user; it carries no SQL of its own.

An artifact saved since the Superset BI surface (docs/arch-17-superset.md) is a
pointer instead: ``bi_kind`` and ``bi_object_id`` name its Superset chart or
dashboard, ``bi_dataset_id`` the virtual dataset over ``sql_text``, and
``viz_spec``/``chart_spec`` stay empty. Superset holds the definition; this row
keeps ownership, thread and provenance.
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
    viz_spec     JSON,
    bi_kind      VARCHAR(16),
    bi_object_id BIGINT,
    bi_dataset_id BIGINT,
    created_at   DATETIME NOT NULL,
    updated_at   DATETIME NOT NULL
) PRIMARY KEY(artifact_id)
DISTRIBUTED BY HASH(artifact_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

ArtifactKind = Literal["chart", "dashboard"]

_COLUMNS = (
    "artifact_id, owner_name, agent_id, thread_id, title, artifact_type, "
    "sql_text, database_name, schema_name, chart_spec, viz_spec, created_at, updated_at, "
    "bi_kind, bi_object_id, bi_dataset_id"
)

#: Pointer columns added for Superset-backed artifacts.
BI_COLUMNS = (
    ("bi_kind", "VARCHAR(16)"),
    ("bi_object_id", "BIGINT"),
    ("bi_dataset_id", "BIGINT"),
)


def bi_paths(kind: str | None, object_id: int | None) -> tuple[str | None, str | None]:
    """``(view, edit)`` paths of a Superset object under ``/bi``."""
    if kind == "chart" and object_id:
        return (
            f"/bi/explore/?slice_id={object_id}&standalone=1",
            f"/bi/explore/?slice_id={object_id}",
        )
    if kind == "dashboard" and object_id:
        return (
            f"/bi/superset/dashboard/{object_id}/?standalone=2",
            f"/bi/superset/dashboard/{object_id}/?edit=true",
        )
    return None, None


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
        viz_spec,
        created_at,
        updated_at,
        bi_kind,
        bi_object_id,
        bi_dataset_id,
    ) = values
    kind = str(bi_kind) if bi_kind else None
    object_id = int(str(bi_object_id)) if bi_object_id else None
    view_path, edit_path = bi_paths(kind, object_id)
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
        "viz_spec": _json_object(viz_spec),
        "created_at": _datetime(created_at),
        "updated_at": _datetime(updated_at),
        "bi_kind": kind,
        "bi_object_id": object_id,
        "bi_dataset_id": int(str(bi_dataset_id)) if bi_dataset_id else None,
        "bi_view_path": view_path,
        "bi_edit_path": edit_path,
    }


def _stored_viz_spec(
    artifact_type: str, viz_spec: object | None
) -> dict[str, Any] | None:
    """Validate and store a worksheet/dashboard spec as plain JSON."""
    if artifact_type != "chart" and artifact_type != "dashboard":
        raise ValueError("Artifact type must be 'chart' or 'dashboard'.")
    if viz_spec is None:
        if artifact_type == "dashboard":
            raise ValueError("A dashboard artifact needs its zone layout.")
        return None
    parsed = _json_object(viz_spec)
    if parsed is None:
        raise ValueError("The viz specification must be a JSON object.")
    if artifact_type == "dashboard":
        from app.modules.viz.spec import DashboardSpec

        DashboardSpec.model_validate(parsed)
    else:
        from app.modules.viz.spec import WorksheetSpec

        WorksheetSpec.model_validate(parsed)
    return parsed


class ArtifactRepository:
    async def ensure_schema(self) -> None:
        await db.execute_system(ARTIFACTS_DDL)
        import contextlib

        from app.modules.viz.migration import ensure_viz_columns

        # An old install whose ALTER failed once still serves artifacts.
        with contextlib.suppress(Exception):  # pragma: no cover - upgrade path
            await ensure_viz_columns()
        await self.ensure_bi_columns()

    async def ensure_bi_columns(self) -> list[str]:
        """Add the Superset pointer columns an older table lacks."""
        described = await db.execute_system("DESCRIBE NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS")
        existing = {str(row[0]) for row in described["rows"]}
        added = []
        for name, column_type in BI_COLUMNS:
            if name not in existing:
                await db.execute_system(
                    "ALTER TABLE NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS "
                    f"ADD COLUMN {name} {column_type}"
                )
                added.append(name)
        return added

    async def create_bi(
        self,
        *,
        owner_name: str,
        title: str,
        artifact_type: ArtifactKind,
        sql_text: str,
        database_name: str | None,
        schema_name: str | None,
        bi_kind: str,
        bi_object_id: int,
        bi_dataset_id: int | None,
        agent_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Record a Superset chart or dashboard as an owned Studio artifact."""
        if bi_kind not in {"chart", "dashboard"} or bi_kind != artifact_type:
            raise ValueError("A BI artifact must point at a Superset object of its own kind.")
        artifact_id = str(uuid4())
        now = _now()
        stored_sql = validate_artifact_sql(sql_text) if artifact_type == "chart" else ""
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS ("
            "artifact_id, owner_name, agent_id, thread_id, title, artifact_type, "
            "sql_text, database_name, schema_name, chart_spec, viz_spec, "
            "bi_kind, bi_object_id, bi_dataset_id, created_at, updated_at"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s, %s, %s)",
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
                bi_kind,
                bi_object_id,
                bi_dataset_id,
                now,
                now,
            ],
        )
        created = await self.get(artifact_id, owner_name=owner_name)
        assert created is not None
        return created

    async def update_bi_source(
        self,
        artifact_id: str,
        *,
        owner_name: str,
        expected_updated_at: datetime,
        sql_text: str,
    ) -> dict[str, Any] | None:
        """Record the new SQL of a BI chart after Superset accepted it."""
        result = await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS SET sql_text = %s, updated_at = %s "
            "WHERE artifact_id = %s AND owner_name = %s AND updated_at = %s",
            [
                validate_artifact_sql(sql_text),
                _now(),
                artifact_id,
                owner_name,
                _datetime(expected_updated_at),
            ],
        )
        if not result.get("affected", 0):
            return None
        return await self.get(artifact_id, owner_name=owner_name)

    async def create(
        self,
        *,
        owner_name: str,
        title: str,
        artifact_type: ArtifactKind,
        sql_text: str,
        database_name: str | None,
        schema_name: str | None,
        chart_spec: object | None = None,
        viz_spec: object | None = None,
        agent_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        artifact_id = str(uuid4())
        now = _now()
        stored_viz = _stored_viz_spec(artifact_type, viz_spec)
        if artifact_type == "dashboard":
            stored_sql = ""
            stored_chart = None
        else:
            stored_sql = validate_artifact_sql(sql_text)
            stored_chart = (
                chart_template(chart_spec, title=title)
                if artifact_type == "chart" and chart_spec is not None and stored_viz is None
                else None
            )
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS ("
            "artifact_id, owner_name, agent_id, thread_id, title, artifact_type, "
            "sql_text, database_name, schema_name, chart_spec, viz_spec, "
            "created_at, updated_at"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                json.dumps(stored_viz, separators=(",", ":")) if stored_viz else None,
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

    async def update(
        self,
        artifact_id: str,
        *,
        owner_name: str,
        expected_updated_at: datetime,
        sql_text: str,
        artifact_type: ArtifactKind,
        chart_spec: object | None,
        viz_spec: object | None = None,
        title: str,
    ) -> dict[str, Any] | None:
        stored_viz = _stored_viz_spec(artifact_type, viz_spec)
        if artifact_type == "dashboard":
            stored_sql = ""
            stored_chart = None
        else:
            stored_sql = validate_artifact_sql(sql_text)
            stored_chart = (
                chart_template(chart_spec, title=title)
                if artifact_type == "chart" and chart_spec is not None and stored_viz is None
                else None
            )
        result = await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS "
            "SET sql_text = %s, artifact_type = %s, chart_spec = %s, viz_spec = %s, "
            "title = %s, updated_at = %s "
            "WHERE artifact_id = %s AND owner_name = %s AND updated_at = %s",
            [
                stored_sql,
                artifact_type,
                json.dumps(stored_chart, separators=(",", ":")) if stored_chart else None,
                json.dumps(stored_viz, separators=(",", ":")) if stored_viz else None,
                title,
                _now(),
                artifact_id,
                owner_name,
                _datetime(expected_updated_at),
            ],
        )
        if not result.get("affected", 0):
            return None
        return await self.get(artifact_id, owner_name=owner_name)


artifact_repository = ArtifactRepository()
