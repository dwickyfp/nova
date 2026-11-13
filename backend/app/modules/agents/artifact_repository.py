"""Persisted Studio artifacts backed by reproducible, read-only SQL.

An artifact stores presentation metadata and the redacted statement needed to
rebuild it. Query rows are deliberately absent from the table: opening an
artifact re-executes under the caller's current StarRocks session, so both
data and RBAC remain current.

Every artifact is a pointer into BI (docs/arch-17-superset.md): ``bi_kind``
and ``bi_object_id`` name its Superset chart or dashboard, and
``bi_dataset_id`` the virtual dataset over ``sql_text``. Superset holds the
definition; this row keeps ownership, thread and provenance.

Artifacts saved before BI are *legacy*: ``bi_object_id`` is empty and the
row's ``viz_spec`` (a viz-core worksheet or ``DashboardSpec``) or Vega-Lite
``chart_spec`` describes it. Nothing writes those columns any more. A legacy
artifact is read-only until :mod:`app.modules.superset.conversion` converts it
in place. Imported presentation definitions remain in PG for historical recovery;
application processes never add or drop schema columns.
"""

from __future__ import annotations

import builtins
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from app.common.sql_guard import redact_sql_credentials, split_sql_statements
from app.core.metadata_actor import current_actor
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
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

ArtifactKind = Literal["chart", "dashboard"]

_BASE_COLUMNS = (
    "artifact_id, owner_name, agent_id, thread_id, title, artifact_type, "
    "sql_text, database_name, schema_name, created_at, updated_at, "
    "bi_kind, bi_object_id, bi_dataset_id"
)
#: Columns of artifacts saved before BI; present until every one is converted.
LEGACY_COLUMNS = ("chart_spec", "viz_spec")

#: Pointer columns added for Superset-backed artifacts.
BI_COLUMNS = (
    ("bi_kind", "VARCHAR(16)"),
    ("bi_object_id", "BIGINT"),
    ("bi_dataset_id", "BIGINT"),
)


def bi_paths(kind: str | None, object_id: int | None) -> tuple[str | None, str | None]:
    """``(view, edit)`` paths of a Superset object under ``/bi``.

    Studio frames these under its own header, so none of them shows Superset's
    navigation bar. Explore's ``standalone`` mode also drops the control panel,
    so the chart editor hides only the bar with ``uiConfig=4``; a dashboard
    keeps its title and edit controls with ``standalone=1``.
    """
    if kind == "chart" and object_id:
        return (
            f"/bi/explore/?slice_id={object_id}&standalone=1",
            f"/bi/explore/?slice_id={object_id}&uiConfig=4",
        )
    if kind == "dashboard" and object_id:
        return (
            f"/bi/superset/dashboard/{object_id}/?standalone=2",
            f"/bi/superset/dashboard/{object_id}/?edit=true&standalone=1",
        )
    return None, None


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
    chart_spec, viz_spec = ([*legacy, None, None])[:2]
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
        "legacy": object_id is None,
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
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        _require_owner(owner_name)
        if bi_kind not in {"chart", "dashboard"} or bi_kind != artifact_type:
            raise ValueError("A BI artifact must point at a Superset object of its own kind.")
        _bi_id(bi_object_id)
        if bi_dataset_id is not None:
            _bi_id(bi_dataset_id)
        stored_sql = validate_artifact_sql(sql_text) if artifact_type == "chart" else ""
        async with agent_metadata(owner_name) as store:
            row = await store.create_artifact(
                {
                    "artifact_id": artifact_id or str(uuid4()),
                    "title": title,
                    "artifact_type": artifact_type,
                    "sql_text": stored_sql,
                    "database_name": database_name,
                    "schema_name": schema_name,
                    "bi_kind": bi_kind,
                    "bi_object_id": bi_object_id,
                    "bi_dataset_id": bi_dataset_id,
                    "agent_id": agent_id,
                    "thread_id": thread_id,
                }
            )
        return _public(row)

    async def update_bi_source(
        self,
        artifact_id: str,
        *,
        owner_name: str,
        expected_updated_at: datetime,
        sql_text: str,
    ) -> dict[str, Any] | None:
        _require_owner(owner_name)
        statement = validate_artifact_sql(sql_text)
        try:
            async with agent_metadata(owner_name) as store:
                current = await store.get_artifact(artifact_id)
                if current is None or _datetime(current["updated_at"]) != _datetime(
                    expected_updated_at
                ):
                    return None
                row = await store.update_artifact(
                    artifact_id,
                    {"sql_text": statement},
                    expected_revision=current["metadata_revision"],
                )
        except MetadataConflict:
            return None
        return _public(row)

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

    async def delete(self, artifact_id: str, *, owner_name: str) -> bool:
        _require_owner(owner_name)
        async with agent_metadata(owner_name) as store:
            return await store.delete_artifact(artifact_id)

    async def list_legacy(self, *, owner_name: str) -> builtins.list[dict[str, Any]]:
        return [row for row in await self.list(owner_name=owner_name) if row["legacy"]]

    async def attach_bi(
        self,
        artifact_id: str,
        *,
        owner_name: str,
        bi_kind: str,
        bi_object_id: int,
        bi_dataset_id: int | None,
    ) -> dict[str, Any] | None:
        _require_owner(owner_name)
        _bi_id(bi_object_id)
        if bi_dataset_id is not None:
            _bi_id(bi_dataset_id)
        try:
            async with agent_metadata(owner_name) as store:
                current = await store.get_artifact(artifact_id)
                if current is None or current["bi_object_id"] is not None:
                    return None
                if bi_kind != _public(current)["artifact_type"]:
                    raise ValueError("The BI object must match the artifact kind.")
                row = await store.update_artifact(
                    artifact_id,
                    {
                        "bi_kind": bi_kind,
                        "bi_object_id": bi_object_id,
                        "bi_dataset_id": bi_dataset_id,
                    },
                    expected_revision=current["metadata_revision"],
                )
        except MetadataConflict:
            return None
        return _public(row)

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


def _bi_id(value: int) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError("A BI object identifier must be a positive integer.")


artifact_repository = ArtifactRepository()
