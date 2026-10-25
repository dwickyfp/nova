"""Execution and persistence for the viz core.

Every computation runs under the caller's own StarRocks session through
``QueryService`` — the viz core holds no connection of its own and broadens no
access. A chart computes in two steps:

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
from app.modules.viz.calc import CalcError, check_formula
from app.modules.viz.calc.analyzer import DataType
from app.modules.viz.compiler import (
    PROBE_ROWS,
    CompileError,
    compile_worksheet,
    infer_column_types,
    source_probe_sql,
)
from app.modules.viz.presmodel import build as build_presmodel
from app.modules.viz.showme import from_intent
from app.modules.viz.showme import suggest as showme_suggest
from app.modules.viz.spec import (
    ChartIntent,
    DashboardSpec,
    FieldRef,
    Filter,
    VizSourceSql,
    WorksheetSpec,
)

logger = logging.getLogger(__name__)

MAX_COMPUTE_ROWS = 500

#: Most distinct members a filter card lists.
MAX_DOMAIN_VALUES = 500


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
    #: The data pane: source columns and calculated fields with their types.
    fields: list[dict[str, Any]] = field(default_factory=list)
    spec: dict[str, Any] | None = None


@dataclass
class SourceSchema:
    """The source's columns, inferred data types, and a few sample rows."""

    columns: list[str]
    types: dict[str, DataType]
    sample: list[list[Any]] = field(default_factory=list)


def data_pane(
    schema: SourceSchema, spec: WorksheetSpec, calcs: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Fields the authoring data pane lists, dimensions before measures."""
    fields: list[dict[str, Any]] = []
    for name in schema.columns:
        data_type = schema.types.get(name, "unknown")
        fields.append(
            {
                "name": name,
                "kind": "column",
                "data_type": data_type,
                "role": "measure" if data_type == "number" else "dimension",
                "level": "row",
            }
        )
    for calc in spec.calculations:
        info = (calcs or {}).get(calc.name)
        fields.append(
            {
                "name": calc.name,
                "kind": "calculation",
                "data_type": info.data_type if info else "unknown",
                "role": info.role if info else "measure",
                "level": info.level if info else "row",
                "formula": calc.formula,
                "addressing": calc.addressing,
            }
        )
    return fields

def canonical_source_sql(spec: WorksheetSpec) -> str:
    """The single read-only statement that rebuilds this worksheet's data."""
    if spec.source.kind == "sql":
        return spec.source.sql.strip()
    return f"SELECT * FROM {spec.source.database}.{spec.source.table}"


class VizService:
    async def probe_columns(self, spec: WorksheetSpec, user: dict[str, Any]) -> list[str]:
        """Learn the source's columns under the caller's own access."""
        return (await self.probe_schema(spec, user)).columns

    async def probe_schema(self, spec: WorksheetSpec, user: dict[str, Any]) -> SourceSchema:
        """Learn the source's columns and their data types from a small sample,
        under the caller's own access."""
        probe = source_probe_sql(spec, rows=PROBE_ROWS)
        results = await self._execute(probe, user, max_rows=PROBE_ROWS)
        columns = list(results.columns)
        sample = [list(row) for row in results.rows]
        return SourceSchema(columns, infer_column_types(columns, sample), sample)

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
        schema = await self.probe_schema(effective, user)
        compiled = compile_worksheet(
            effective, known_columns=schema.columns, column_types=schema.types
        )
        result = await self._execute(compiled.sql, user)
        resolved = compiled.spec or effective
        presmodel = build_presmodel(
            resolved,
            list(result.columns),
            [list(row) for row in result.rows],
            preserve_order=compiled.ordered,
        )
        return ComputeResult(
            presmodel=presmodel,
            sql=compiled.sql,
            columns=list(result.columns),
            rows=[list(row) for row in result.rows],
            row_count=result.row_count,
            elapsed_ms=result.elapsed_ms,
            fields=data_pane(schema, effective, compiled.calculations),
            spec=resolved.model_dump(mode="json"),
        )

    async def describe(self, spec: WorksheetSpec, user: dict[str, Any]) -> dict[str, Any]:
        """The data pane for a worksheet's source, without computing the view."""
        schema = await self.probe_schema(spec, user)
        try:
            compiled = compile_worksheet(
                spec, known_columns=schema.columns, column_types=schema.types
            )
            calcs: dict[str, Any] = compiled.calculations
        except CompileError:
            calcs = {}
        return {
            "fields": data_pane(schema, spec, calcs),
            "sample_columns": schema.columns,
            "sample_rows": schema.sample[:50],
        }

    async def check_calculation(
        self,
        spec: WorksheetSpec,
        user: dict[str, Any],
        *,
        formula: str,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Check one formula against the worksheet's fields (the formula
        editor). Only the probe touches data, under the caller's access."""
        schema = await self.probe_schema(spec, user)
        try:
            info = check_formula(
                formula,
                dict(schema.types),
                spec.calculation_formulas(),
                name=name,
            )
        except CalcError as exc:
            return {
                "valid": False,
                "error": {"message": exc.message, "start": exc.start, "end": exc.end},
            }
        return {
            "valid": True,
            "data_type": info.data_type,
            "level": info.level,
            "role": info.role,
            "references": info.references,
        }

    async def field_domain(
        self,
        spec: WorksheetSpec,
        user: dict[str, Any],
        *,
        field_name: str,
        date_part: str | None = None,
    ) -> dict[str, Any]:
        """Members (or the range) of one field, for a filter card.

        It compiles a one-field view of the same source — with the
        worksheet's context filters — so calculated fields work and every
        value comes from the caller's own session.
        """
        schema = await self.probe_schema(spec, user)
        probe = WorksheetSpec(
            source=spec.source,
            calculations=spec.calculations,
            filters=[item for item in spec.filters if item.context],
            columns=[FieldRef(field=field_name, date_part=date_part)],  # type: ignore[arg-type]
            marks={"type": "text"},  # type: ignore[arg-type]
            sort=[{"field": field_name, "direction": "asc"}],  # type: ignore[list-item]
            limit=MAX_DOMAIN_VALUES + 1,
        )
        compiled = compile_worksheet(probe, known_columns=schema.columns, column_types=schema.types)
        field_type = compiled.output_fields[0].field_type if compiled.output_fields else "nominal"
        domain: dict[str, Any] = {"field": field_name, "field_type": field_type}
        if field_type == "quantitative" and date_part is None:
            # A range filter needs the bounds, not every member.
            bounds = probe.model_copy(
                update={
                    "columns": [],
                    "rows": [
                        FieldRef(field=field_name, aggregation="min", discrete=False),
                        FieldRef(field=field_name, aggregation="max", discrete=False),
                    ],
                    "sort": [],
                    "limit": None,
                }
            )
            ranged = compile_worksheet(
                bounds, known_columns=schema.columns, column_types=schema.types
            )
            result = await self._execute(ranged.sql, user, max_rows=1)
            low, high = (list(result.rows[0]) + [None, None])[:2] if result.rows else (None, None)
            domain.update({"min": low, "max": high, "values": [], "truncated": False})
            return domain
        result = await self._execute(compiled.sql, user, max_rows=MAX_DOMAIN_VALUES + 1)
        values = [row[0] for row in result.rows]
        domain.update(
            {
                "values": values[:MAX_DOMAIN_VALUES],
                "truncated": len(values) > MAX_DOMAIN_VALUES,
            }
        )
        return domain

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
                worksheet = await self._worksheet_for(artifact, user)
            except (ValueError, VizError):
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

    async def _worksheet_for(self, artifact: dict[str, Any], user: dict[str, Any]) -> WorksheetSpec:
        """The chart's worksheet; a pre-worksheet chart derives one from its
        query (run under the caller's session) and saved encoding."""
        if artifact.get("viz_spec"):
            return WorksheetSpec.model_validate(artifact["viz_spec"])
        if not artifact.get("sql_text"):
            raise VizError("This chart has no query.")
        result = await self._execute(artifact["sql_text"], user)
        derived = derive_worksheet(
            artifact, list(result.columns), [list(row) for row in result.rows]
        )
        if derived is None:
            raise VizError("This chart has no worksheet to compute.")
        return WorksheetSpec.model_validate(derived[0])

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


_VEGA_CHART_TYPES = {
    "bar": "bar",
    "line": "line",
    "area": "area",
    "point": "scatter",
    "circle": "scatter",
    "square": "scatter",
    "arc": "pie",
    "rect": "heatmap",
    "text": "text_table",
}


def derive_worksheet(
    artifact: dict[str, Any], columns: list[str], rows: list[list[Any]]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """A worksheet (and its presentation model) for a pre-worksheet chart.

    The chart's Vega encoding names its axes; the Show Me engine places the
    rest. Nothing is stored: the editor saves it when the user edits.
    """
    if artifact.get("artifact_type") != "chart" or not columns or not artifact.get("sql_text"):
        return None
    vega = artifact.get("chart_spec") or {}
    mark = vega.get("mark")
    mark = mark.get("type") if isinstance(mark, dict) else mark
    encoding = vega.get("encoding") or {}

    def field_of(channel: str) -> str | None:
        value = (encoding.get(channel) or {}).get("field")
        return value if isinstance(value, str) and value in columns else None

    x = field_of("x") or field_of("color") if mark == "arc" else field_of("x")
    y = field_of("theta") if mark == "arc" else field_of("y")
    try:
        intent = ChartIntent(
            chart_type=_VEGA_CHART_TYPES.get(str(mark), "auto"),  # type: ignore[arg-type]
            x=x,
            y=y,
            color=field_of("color") if mark != "arc" else None,
        )
        spec = from_intent(
            intent,
            columns,
            rows,
            source=VizSourceSql(sql=artifact["sql_text"]),
            title=artifact.get("title") or "",
        )
        presmodel = build_presmodel(spec, columns, rows)
    except ValueError:
        return None
    return spec.model_dump(mode="json"), presmodel


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
