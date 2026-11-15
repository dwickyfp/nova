"""Persisted Studio artifacts backed by reproducible, read-only SQL.

An artifact stores presentation metadata and the redacted statement needed to
rebuild it. Query rows are deliberately absent from the table: opening an
artifact re-executes under the caller's current StarRocks session, so both
data and RBAC remain current.

Studio artifacts are historical: nothing creates them any more, and native
Dataviz documents (:mod:`app.modules.viz`) replace them. A row's ``viz_spec``
(a viz-core worksheet or ``DashboardSpec``) or Vega-Lite ``chart_spec``
describes it. Rows that pointed at the retired BI integration (``bi_object_id``
set) carry no definition of their own. Imported presentation definitions remain
in PG for historical recovery; application processes never add or drop schema
columns.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.common.sql_guard import redact_sql_credentials, split_sql_statements
from app.core.metadata_actor import current_actor
from app.core.metadata_db import MetadataPermissionDenied
from app.modules.agents.metadata_repository import agent_metadata
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
    bi_kind      VARCHAR(16),
    bi_object_id BIGINT,
    bi_dataset_id BIGINT,
    created_at   DATETIME NOT NULL,
    updated_at   DATETIME NOT NULL
) PRIMARY KEY(artifact_id)
DISTRIBUTED BY HASH(artifact_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

_BASE_COLUMNS = (
    "artifact_id, owner_name, agent_id, thread_id, title, artifact_type, "
    "sql_text, database_name, schema_name, created_at, updated_at, "
    "bi_kind, bi_object_id, bi_dataset_id"
)
#: Presentation definitions of artifacts.
LEGACY_COLUMNS = ("chart_spec", "viz_spec")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
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
        created_at,
        updated_at,
        bi_kind,
        bi_object_id,
        bi_dataset_id,
        *legacy,
    ) = values
    del bi_kind, bi_dataset_id
    chart_spec, viz_spec = ([*legacy, None, None])[:2]
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
        #: Pointed at the retired BI integration: no definition of its own.
        "retired": bool(bi_object_id),
    }


def _public(row: dict[str, Any]) -> dict[str, Any]:
    values = [row.get(name.strip()) for name in _BASE_COLUMNS.split(",")]
    # A migrated table artifact is still rendered as a chart with its original SQL.
    values[5] = "chart" if row["artifact_type"] == "table" else row["artifact_type"]
    legacy = [row.get(name) for name in LEGACY_COLUMNS] if not row.get("bi_object_id") else []
    return _row(values + legacy)


def _require_owner(owner: str) -> None:
    if current_actor().principal != owner:
        raise MetadataPermissionDenied()


class ArtifactRepository:
    async def list(self, *, owner_name: str) -> list[dict[str, Any]]:
        _require_owner(owner_name)
        async with agent_metadata(read_only=True) as store:
            rows = await store.list_artifacts()
        return [_public(row) for row in rows]

    async def get(self, artifact_id: str, *, owner_name: str) -> dict[str, Any] | None:
        _require_owner(owner_name)
        async with agent_metadata(read_only=True) as store:
            row = await store.get_artifact(artifact_id)
        return _public(row) if row else None

    async def shared_dashboard(
        self, dashboard_id: str, *, owner_name: str
    ) -> dict[str, Any] | None:
        from app.modules.viz.spec import DashboardSpec, WorksheetSpec

        async with agent_metadata(read_only=True) as store:
            result = await store.shared_dashboard(dashboard_id, owner_name)
        if result is None:
            return None
        # Specs contain field shelves named "rows" and categorical filter values;
        # validate their types instead of deleting those presentation properties.
        try:
            result["viz_spec"] = DashboardSpec.model_validate(result["viz_spec"]).model_dump()
            for chart in result["charts"]:
                if chart.get("viz_spec") is not None:
                    chart["viz_spec"] = WorksheetSpec.model_validate(chart["viz_spec"]).model_dump()
                if chart.get("chart_spec") is not None:
                    chart["chart_spec"] = chart_template(chart["chart_spec"], title=chart["title"])
        except ValueError:
            return None
        return result


artifact_repository = ArtifactRepository()
