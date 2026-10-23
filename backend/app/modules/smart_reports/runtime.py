"""Deterministic report-to-semantic adapter; all data access stays in Semantic Views."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import HTTPException

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import (
    SemanticFilter,
    SemanticOrder,
    SemanticPlan,
    SemanticTime,
    SemanticTransform,
)
from app.modules.agents.semantic.time_ranges import resolve_execution_time
from app.modules.intelligence.semantic_views import semantic_view_service
from app.modules.query.admission import QueryCapacityError, audit_refusal, query_admission
from app.modules.smart_reports.capabilities import SUPPORTED_CHARTS
from app.modules.smart_reports.repository import digest
from app.modules.smart_reports.schemas import (
    FilterDefinition,
    FilterValue,
    ReportSpecV1,
    ReportViewStateV1,
    Widget,
    WidgetResult,
)
from app.modules.smart_reports.security import require_current_session
from app.observability.metrics import REPORT_WIDGET_RESULTS, studio_operation


def scalar_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: scalar_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scalar_json(v) for v in value]
    return value


def applies(spec: ReportSpecV1, definition: FilterDefinition, widget: Widget) -> bool:
    if widget.id in definition.exclude_widgets:
        return False
    if definition.scope == "report":
        return True
    if definition.scope == "widgets":
        return widget.id in definition.targets
    for page in spec.pages:
        for section in page.sections:
            if widget.id in {w.id for w in section.widgets}:
                return (page.id if definition.scope == "page" else section.id) in definition.targets
    return False


def value_filters(
    field: str,
    value: FilterValue,
    *,
    kind: str,
    now: datetime,
    timezone: str,
    fiscal_start_month: int = 1,
    week_start: int = 0,
) -> list[SemanticFilter]:
    if value.kind == "unset":
        return []
    if value.kind == "multi":
        # Empty IN remains empty; it must never become an unfiltered query.
        return [SemanticFilter(field, "IN", value.values)]
    if value.kind == "single":
        item = value.values[0]
        if kind == "text":
            text = str(item).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return [SemanticFilter(field, "LIKE", f"%{text}%")]
        return [SemanticFilter(field, "IS NULL" if item is None else "=", item)]
    lower, upper = value.minimum, value.maximum
    if value.relative:
        window = resolve_execution_time(
            value.relative,
            now=now,
            timezone=timezone,
            fiscal_start_month=fiscal_start_month,
            week_start=week_start,
        )
        lower = window.current.start.replace(tzinfo=None).isoformat(sep=" ")
        upper = window.current.end.replace(tzinfo=None).isoformat(sep=" ")
    result = []
    if lower is not None:
        result.append(SemanticFilter(field, ">=", lower))
    if upper is not None:
        result.append(SemanticFilter(field, "<" if value.kind == "date_range" else "<=", upper))
    if not result:
        raise ValueError("A range requires at least one bound")
    return result


def widget_plan(
    spec: ReportSpecV1, state: ReportViewStateV1, widget: Widget, now: datetime
) -> tuple[SemanticPlan, list[str]]:
    query = widget.query
    if query is None:
        raise ValueError("Map this widget to a semantic source")
    filters = [SemanticFilter(f.field, f.operator, f.value) for f in query.filters]
    not_applied = []
    for definition in spec.filters:
        if not applies(spec, definition, widget):
            continue
        value = state.filters.get(definition.id, definition.default)
        field = (
            definition.field
            if definition.source == query.source
            else definition.mappings.get(query.source)
        )
        if not field:
            if definition.required:
                raise ValueError("A required filter cannot apply to this widget")
            not_applied.append(definition.id)
            continue
        if definition.required and value.kind == "unset":
            raise ValueError("Choose a value for the required filter")
        filters.extend(
            value_filters(
                field,
                value,
                kind=definition.kind,
                now=now,
                timezone=spec.timezone,
                **spec.calendar.model_dump(),
            )
        )
    for rule in spec.interactions:
        if widget.id in rule.targets and rule.source_widget in state.cross_filters:
            source_query = spec.widgets()[rule.source_widget].query
            field = (
                rule.field
                if source_query and source_query.source == query.source
                else rule.mappings.get(query.source)
            )
            if not field:
                raise ValueError("Cross-filter has no governed dimension mapping")
            filters.append(SemanticFilter(field, "IN", state.cross_filters[rule.source_widget]))
    filters.extend(
        SemanticFilter(f.field, f.operator, f.value) for f in state.drill_paths.get(widget.id, [])
    )
    dimensions = state.group_by.get(widget.id, query.dimensions)
    orders = [
        SemanticOrder(s.field, s.direction) for s in state.sort.get(widget.id, query.order_by)
    ]
    time = SemanticTime(**query.time.model_dump()) if query.time else None
    group_keys = (["period"] if time and time.grain else []) + dimensions
    if time and time.compare:
        group_keys.append("comparison_period")
    for key in group_keys:
        if key not in {s.field for s in orders}:
            orders.append(SemanticOrder(key, "asc"))
    paged = widget.kind in {"table", "pivot"}
    limit = state.page_size + 1 if paged else query.limit or (1000 if group_keys else None)
    if widget.kind == "kpi":
        limit = None
    plan = SemanticPlan(
        metrics=tuple(query.metrics),
        dimensions=tuple(dimensions),
        filters=tuple(filters),
        named_filters=tuple(query.named_filters),
        calculations=tuple(c.model_dump() for c in query.calculations),
        time=time,
        time_output="period",
        fiscal_start_month=spec.calendar.fiscal_start_month,
        week_start=spec.calendar.week_start,
        order_by=tuple(orders),
        limit=limit,
        transforms=tuple(SemanticTransform(t.metric, t.kind) for t in query.transforms),
        offset=state.page_index.get(widget.id, 0) * state.page_size if paged and group_keys else 0,
    )
    return plan, not_applied


class ReportRuntime:
    async def validate(self, spec: ReportSpecV1, user: dict) -> None:
        models = {}
        for source in spec.sources:
            _view, row = await semantic_view_service._readable_version(
                source.view_id, source.version, user
            )
            if (
                row["status"] not in {"ACTIVE", "DEPRECATED"}
                or row["fingerprint"] != source.fingerprint
            ):
                raise HTTPException(422, "Report semantic dependency changed or is not published")
            models[source.id] = SemanticModelIR.from_ossie(row["definition"])
        for widget in spec.widgets().values():
            if not widget.query:
                continue
            query = widget.query
            if widget.kind not in SUPPORTED_CHARTS:
                raise ValueError("Unsupported report renderer capability")
            selected = set(query.metrics + query.dimensions + [c.name for c in query.calculations])
            if query.time and query.time.grain:
                selected.add("period")
            if query.time and query.time.compare:
                selected.add("comparison_period")
            encoding = widget.encoding
            if any(
                f not in selected
                for f in [
                    encoding.x,
                    encoding.series,
                    encoding.value,
                    encoding.latitude,
                    encoding.longitude,
                    *encoding.y,
                ]
                if f
            ):
                raise ValueError("Chart encoding references an unselected field")
            if widget.kind not in {"table", "kpi"} and not query.metrics:
                raise ValueError("A chart requires a selected metric")
            if (
                encoding.series
                and encoding.series not in query.dimensions
                and not (
                    encoding.series == "comparison_period" and query.time and query.time.compare
                )
            ):
                raise ValueError("Chart series must be a dimension")
            if (
                query.time
                and query.time.compare
                and encoding.series not in {None, "comparison_period"}
            ):
                raise ValueError("Comparison charts reserve series for the comparison period")
            if widget.kind not in {"table", "kpi", "pivot", "map"}:
                x = encoding.x or (query.dimensions[0] if query.dimensions else "period")
                if set(query.dimensions) - {x, encoding.series}:
                    raise ValueError("Every chart grouping dimension requires an axis or series")
                if widget.kind in {"donut", "funnel"} and (
                    encoding.series or query.time and query.time.compare
                ):
                    raise ValueError("This chart supports one categorical grouping")
            model = models[query.source]
            SemanticCompiler().compile(
                model,
                SemanticPlan(
                    metrics=tuple(query.metrics),
                    dimensions=tuple(query.dimensions),
                    named_filters=tuple(query.named_filters),
                    calculations=tuple(c.model_dump() for c in query.calculations),
                    filters=tuple(
                        SemanticFilter(f.field, f.operator, f.value) for f in query.filters
                    ),
                    time=SemanticTime(**query.time.model_dump()) if query.time else None,
                    time_output="period",
                    fiscal_start_month=spec.calendar.fiscal_start_month,
                    week_start=spec.calendar.week_start,
                    transforms=tuple(SemanticTransform(t.metric, t.kind) for t in query.transforms),
                ),
                time_context=resolve_execution_time(
                    query.time.range,
                    query.time.compare,
                    now=datetime.now(UTC),
                    timezone=spec.timezone,
                    **spec.calendar.model_dump(),
                )
                if query.time and query.time.range
                else None,
            )
            if widget.detail_fields:
                from app.modules.smart_reports.details import validate_detail_fields

                validate_detail_fields(model, widget.detail_fields)
            for name in widget.drill_dimensions:
                drill_field = model.field(name)
                if not drill_field or drill_field.kind.value != "dimension":
                    raise ValueError("Unknown drill dimension")
        for definition in spec.filters:
            filter_field = models[definition.source].field(definition.field)
            if not filter_field or filter_field.kind.value != "dimension":
                raise ValueError("Unknown filter dimension")
            for source_id, field_name in definition.mappings.items():
                mapped_field = models[source_id].field(field_name)
                if not mapped_field or mapped_field.kind.value != "dimension":
                    raise ValueError("Unknown mapped filter dimension")
        for interaction in spec.interactions:
            source_widget = spec.widgets()[interaction.source_widget]
            if not source_widget.query or interaction.field not in source_widget.query.dimensions:
                raise ValueError("Cross-filter field must be a selected source dimension")
            if source_widget.kind not in {"table", "pivot"} and interaction.field != (
                source_widget.encoding.x or source_widget.query.dimensions[0]
            ):
                raise ValueError("Chart cross-filter must use its categorical axis")
            for source_id, field_name in interaction.mappings.items():
                mapped_field = models[source_id].field(field_name)
                if not mapped_field or mapped_field.kind.value != "dimension":
                    raise ValueError("Unknown cross-filter mapping")

    async def render(
        self,
        spec: ReportSpecV1,
        state: ReportViewStateV1,
        user: dict,
        widget_ids: list[str],
        *,
        as_of: datetime | None = None,
    ) -> dict:
        await require_current_session(user)
        now = as_of or datetime.now(UTC)
        widgets = spec.widgets()
        requested = widget_ids or [
            w.id
            for p in spec.pages
            if p.id == state.active_page_id
            for s in p.sections
            for w in s.widgets
        ]
        if len(requested) > 24 or not set(requested) <= set(widgets):
            raise HTTPException(422, "Invalid report widget selection")
        from app.modules.query.service import delegated_connection_for

        semaphore = asyncio.Semaphore(1 if delegated_connection_for(user["username"]) else 4)
        queries: dict[str, asyncio.Task] = {}

        async def execute(source, plan):
            key = digest([source.model_dump(), plan.as_dict(), now.isoformat()])

            async def run():
                async with semaphore, asyncio.timeout(60), query_admission.slot("reports"):
                    _view, row = await semantic_view_service._readable_version(
                        source.view_id, source.version, user
                    )
                    if row["fingerprint"] != source.fingerprint:
                        raise ValueError("Semantic source fingerprint changed")
                    context = (
                        resolve_execution_time(
                            plan.time.range,
                            plan.time.compare,
                            now=now,
                            timezone=spec.timezone,
                            **spec.calendar.model_dump(),
                        )
                        if plan.time and plan.time.range
                        else None
                    )
                    return await semantic_view_service.execute_plan(
                        source.view_id, source.version, plan, user, time_context=context
                    )

            if key not in queries:
                queries[key] = asyncio.create_task(run())
            return await queries[key]

        async def render_one(widget: Widget) -> WidgetResult:
            if widget.mapping_required:
                return WidgetResult(
                    widget_id=widget.id,
                    status="requires_mapping",
                    completeness="error",
                    message="Map this widget to a published semantic metric",
                )
            try:
                plan, excluded = widget_plan(spec, state, widget, now)
                assert widget.query is not None
                source = next(s for s in spec.sources if s.id == widget.query.source)
                if widget.kind == "pivot":
                    from app.modules.smart_reports.pivot import pivot_result

                    pivot = await pivot_result(widget, plan, source, execute, state)
                    return WidgetResult(
                        widget_id=widget.id,
                        status="success" if pivot["rows"] else "empty",
                        **scalar_json(pivot),
                        completeness="paged",
                        not_applied=excluded,
                        query_fingerprint=digest(
                            [source.model_dump(), plan.as_dict(), now.isoformat(), "pivot"]
                        ),
                        semantic_view_id=source.view_id,
                        semantic_version=source.version,
                        evaluated_at=now,
                    )
                result = await execute(source, plan)
                rows, columns = result["rows"], result["columns"]
                paged = widget.kind in {"table", "pivot"}
                more = paged and len(rows) > state.page_size
                totals = {}
                if paged and plan.metrics:
                    from app.modules.agents.semantic.calculations import total_calculations

                    total_plan = replace(
                        plan,
                        dimensions=(),
                        order_by=(),
                        limit=None,
                        offset=0,
                        transforms=(),
                        calculations=total_calculations(plan.calculations, plan.metrics),
                        time=replace(plan.time, grain=None, compare=None) if plan.time else None,
                    )
                    total = await execute(source, total_plan)
                    if len(total["rows"]) == 1 and not total.get("truncated"):
                        totals = dict(zip(total["columns"], total["rows"][0], strict=True))
                completeness: Literal["paged", "truncated", "complete"] = (
                    "paged"
                    if paged
                    else "truncated"
                    if result.get("truncated") or bool(plan.limit and len(rows) >= plan.limit)
                    else "complete"
                )
                return WidgetResult(
                    widget_id=widget.id,
                    status="success" if rows else "empty",
                    columns=columns,
                    rows=scalar_json(rows[: state.page_size] if paged else rows),
                    totals=scalar_json(totals),
                    completeness=completeness,
                    has_more=more,
                    not_applied=excluded,
                    warnings=result.get("warnings", []),
                    time_context=result.get("time_context"),
                    query_fingerprint=digest(
                        [source.model_dump(), plan.as_dict(), now.isoformat()]
                    ),
                    query_id=result.get("query_id"),
                    semantic_view_id=source.view_id,
                    semantic_version=source.version,
                    evaluated_at=now,
                )
            except QueryCapacityError:
                await audit_refusal(
                    username=user["username"],
                    source="reports",
                    target=state.report_id,
                    session_id=user.get("session_id"),
                )
                return WidgetResult(
                    widget_id=widget.id,
                    status="error",
                    completeness="error",
                    message="Nova query capacity reached. Retry shortly.",
                )
            except HTTPException as exc:
                return WidgetResult(
                    widget_id=widget.id,
                    status="denied" if exc.status_code in {401, 403, 404} else "error",
                    completeness="error",
                    message=(
                        "This widget is unavailable under the current access or source definition"
                    ),
                )
            except TimeoutError:
                return WidgetResult(
                    widget_id=widget.id,
                    status="timeout",
                    completeness="error",
                    message="Query timed out. Retry this widget.",
                )
            except ValueError as exc:
                return WidgetResult(
                    widget_id=widget.id,
                    status="incompatible",
                    completeness="error",
                    message=str(exc)
                    if "filter" in str(exc).lower()
                    else "Widget configuration is incompatible with its semantic source",
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "Report widget execution failed", extra={"widget_id": widget.id}
                )
                return WidgetResult(
                    widget_id=widget.id,
                    status="error",
                    completeness="error",
                    message="Widget query failed; retry or review the source",
                )

        try:
            with studio_operation("report", "execute"):
                results = await asyncio.gather(
                    *(render_one(widgets[i]) for i in dict.fromkeys(requested))
                )
            for result in results:
                REPORT_WIDGET_RESULTS.labels(widgets[result.widget_id].kind, result.status).inc()
        finally:
            for task in queries.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*queries.values(), return_exceptions=True)
        await require_current_session(user)
        return {
            "report_id": state.report_id,
            "spec_revision": state.spec_revision,
            "state_revision": state.state_revision,
            "state_fingerprint": digest(state.model_dump()),
            "as_of": now.isoformat(),
            "status": "partial_success"
            if any(r.completeness == "error" for r in results)
            else "success",
            "widgets": [r.model_dump(mode="json") for r in results],
        }


report_runtime = ReportRuntime()
