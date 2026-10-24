"""Execution and persistence for the viz core.

Every computation runs under the caller's own StarRocks session through
``QueryService`` — the viz core holds no connection of its own and broadens no
access. A chart computes in two steps that mirror Tableau's VizQL flow:

1. probe the source for its columns (and the caller's right to read it),
2. compile the worksheet and execute it, then build the presentation model.

Dashboards compose chart artifacts: each sheet zone re-compiles its worksheet
with the dashboard's filters merged in, which is what makes dashboard quick
filters and filter actions work on live data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.modules.agents.artifact_repository import artifact_repository
from app.modules.query.service import query_service
from app.modules.viz.compiler import (
    CompileError,
    compile_worksheet,
    source_probe_sql,
)
from app.modules.viz.presmodel import build as build_presmodel
from app.modules.viz.showme import suggest as showme_suggest
from app.modules.viz.spec import DashboardSpec, Filter, WorksheetSpec

logger = logging.getLogger(__name__)

MAX_COMPUTE_ROWS = 500


class VizError(ValueError):
    """A viz request cannot be served (compile, authorization or spec)."""


@dataclass
class ComputeResult:
    """One computed worksheet: presentation model plus its evidence."""

    presmodel: dict[str, Any]
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    elapsed_ms: float = 0.0


def canonical_source_sql(spec: WorksheetSpec) -> str:
    """The single read-only statement that rebuilds this worksheet's data."""
    if spec.source.kind == "sql":
        return spec.source.sql.strip()
    return f"SELECT * FROM {spec.source.database}.{spec.source.table}"


class VizService:
    async def probe_columns(self, spec: WorksheetSpec, user: dict[str, Any]) -> list[str]:
        """Learn the source's columns under the caller's own access."""
        probe = source_probe_sql(spec)
        results = await self._execute(probe, user, max_rows=1)
        return list(results.columns)

    async def compute(
        self,
        spec: WorksheetSpec,
        user: dict[str, Any],
        *,
        extra_filters: list[Filter] | None = None,
        database_name: str | None = None,
        schema_name: str | None = None,
    ) -> ComputeResult:
        """Compile and execute the worksheet, then build its presentation model."""
        effective = spec.with_extra_filters(extra_filters or [])
        columns = await self.probe_columns(effective, user)
        compiled = compile_worksheet(effective, known_columns=columns)
        result = await self._execute(compiled.sql, user)
        presmodel = build_presmodel(
            effective, list(result.columns), [list(row) for row in result.rows]
        )
        return ComputeResult(
            presmodel=presmodel,
            sql=compiled.sql,
            columns=list(result.columns),
            rows=[list(row) for row in result.rows],
            row_count=result.row_count,
            elapsed_ms=result.elapsed_ms,
        )

    def compute_in_memory(
        self, spec: WorksheetSpec, columns: list[str], rows: list[list[Any]]
    ) -> dict[str, Any]:
        """Compute the presentation model over rows the turn already holds."""
        return build_presmodel(spec, columns, rows)

    def suggest(self, columns: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
        """Ranked ShowMe suggestions for a result's columns."""
        from app.modules.viz.showme import classify_fields

        return showme_suggest(classify_fields(columns, rows))

    async def save_chart(
        self,
        *,
        user: dict[str, Any],
        title: str,
        spec: WorksheetSpec,
        agent_id: str | None = None,
        thread_id: str | None = None,
        database_name: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Persist a worksheet as a chart artifact (its source SQL is stored
        unchanged; refresh re-compiles and re-executes it)."""
        sql = canonical_source_sql(spec)
        return await artifact_repository.create(
            owner_name=user["username"],
            title=title,
            artifact_type="chart",
            sql_text=sql,
            database_name=database_name,
            schema_name=schema_name,
            viz_spec=spec.model_dump(mode="json"),
            agent_id=agent_id,
            thread_id=thread_id,
        )

    async def save_dashboard(
        self,
        *,
        user: dict[str, Any],
        title: str,
        spec: DashboardSpec,
        agent_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a zone layout as a dashboard artifact.

        Every sheet zone must reference a chart artifact the caller owns; a
        dashboard never widens access to another owner's data.
        """
        owned = await artifact_repository.list(owner_name=user["username"])
        owned_charts = {
            artifact["artifact_id"] for artifact in owned if artifact["artifact_type"] == "chart"
        }
        missing = [
            zone.artifact_id for zone in spec.sheet_zones() if zone.artifact_id not in owned_charts
        ]
        if missing:
            raise VizError("A dashboard zone references an unavailable chart artifact.")
        return await artifact_repository.create(
            owner_name=user["username"],
            title=title,
            artifact_type="dashboard",
            sql_text="",
            database_name=None,
            schema_name=None,
            viz_spec=spec.model_dump(mode="json"),
            agent_id=agent_id,
            thread_id=thread_id,
        )

    async def update_dashboard(
        self,
        artifact_id: str,
        *,
        user: dict[str, Any],
        title: str,
        spec: DashboardSpec,
        expected_updated_at: Any,
    ) -> dict[str, Any] | None:
        owned = await artifact_repository.list(owner_name=user["username"])
        owned_charts = {
            artifact["artifact_id"] for artifact in owned if artifact["artifact_type"] == "chart"
        }
        if any(zone.artifact_id not in owned_charts for zone in spec.sheet_zones()):
            raise VizError("A dashboard zone references an unavailable chart artifact.")
        return await artifact_repository.update(
            artifact_id,
            owner_name=user["username"],
            expected_updated_at=expected_updated_at,
            sql_text="",
            artifact_type="dashboard",
            viz_spec=spec.model_dump(mode="json"),
            chart_spec=None,
            title=title,
        )

    async def compute_dashboard(
        self,
        spec: DashboardSpec,
        user: dict[str, Any],
        *,
        filter_values: dict[str, Filter] | None = None,
        zone_filters: dict[str, list[Filter]] | None = None,
        owner_name: str | None = None,
    ) -> dict[str, Any]:
        """Compute every sheet zone under merged dashboard and action filters.

        ``filter_values`` are the current values of filter zones, keyed by
        filter-zone id; each applies to the sheets its ``apply_to`` names.
        ``zone_filters`` carry filter-action selections targeting one zone.
        ``owner_name`` resolves the referenced chart artifacts — a shared
        dashboard reads the owner's definitions while every query still
        executes under ``user``'s own session. Failures are reported per zone
        so one broken sheet never blanks the whole dashboard.
        """
        filter_values = filter_values or {}
        zone_filters = zone_filters or {}
        owned = {
            artifact["artifact_id"]: artifact
            for artifact in await artifact_repository.list(
                owner_name=owner_name or user["username"]
            )
        }
        zones_out: list[dict[str, Any]] = []
        for zone in spec.sheet_zones():
            artifact = owned.get(zone.artifact_id or "")
            entry: dict[str, Any] = {
                "zone_id": zone.zone_id,
                "artifact_id": zone.artifact_id,
            }
            if artifact is None or artifact.get("artifact_type") != "chart":
                entry["error"] = "This chart is no longer available."
                zones_out.append(entry)
                continue
            try:
                worksheet = WorksheetSpec.model_validate(artifact.get("viz_spec") or {})
            except ValueError:
                entry["error"] = "This chart has no worksheet to compute."
                zones_out.append(entry)
                continue
            effective_filters: list[Filter] = []
            for filter_zone in spec.zones:
                if filter_zone.kind != "filter" or filter_zone.filter is None:
                    continue
                targets = filter_zone.filter.apply_to
                if "all" in targets or zone.zone_id in targets:
                    chosen = filter_values.get(filter_zone.zone_id)
                    if chosen is not None:
                        effective_filters.append(chosen)
            effective_filters.extend(zone_filters.get(zone.zone_id, []))
            try:
                computed = await self.compute(
                    worksheet,
                    user,
                    extra_filters=effective_filters,
                    database_name=artifact.get("database_name"),
                    schema_name=artifact.get("schema_name"),
                )
            except (VizError, CompileError, ValueError) as exc:
                logger.warning("Dashboard zone %s failed: %s", zone.zone_id, exc)
                entry["error"] = str(exc)[:300]
                zones_out.append(entry)
                continue
            entry.update(
                {
                    "title": artifact.get("title"),
                    "presmodel": computed.presmodel,
                    "sql": computed.sql,
                    "row_count": computed.row_count,
                }
            )
            zones_out.append(entry)
        return {"title": spec.title, "zones": zones_out}

    async def _execute(self, sql: str, user: dict[str, Any], *, max_rows: int = MAX_COMPUTE_ROWS):
        active_role = None
        granted = user.get("roles") or []
        active = user.get("active_role")
        if active and active in granted:
            active_role = active
        results = await query_service.execute_statements(
            sql=sql,
            username=user["username"],
            encrypted_password=user["encrypted_password"],
            database=None,
            schema=None,
            role=active_role,
            max_rows=max_rows,
            session_id=user["session_id"],
            confirm_destructive=False,
        )
        result = results[0]
        if not result.success:
            raise VizError(result.error or "The viz query could not be executed.")
        return result


def legacy_dashboard_artifact_id(dashboard_id: str) -> str:
    """Deterministic artifact id for a migrated legacy dashboard."""
    return str(uuid5(NAMESPACE_URL, f"nova:viz:legacy-dashboard:{dashboard_id}"))


def zones_from_legacy_tiles(tiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert a legacy 6×4 tile layout into 24-column dashboard zones."""
    zones: list[dict[str, Any]] = []
    for index, tile in enumerate(tiles):
        if not isinstance(tile, dict):
            continue
        artifact_id = tile.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            continue
        zones.append(
            {
                "zone_id": f"zone_{index}",
                "kind": "sheet",
                "artifact_id": artifact_id,
                "rect": {
                    "x": min(int(tile.get("x", 0)) * 4, 20),
                    "y": int(tile.get("y", index)),
                    "w": max(4, min(int(tile.get("w", 3)) * 4, 24)),
                    "h": max(1, int(tile.get("h", 2))),
                },
            }
        )
    return zones


viz_service = VizService()
