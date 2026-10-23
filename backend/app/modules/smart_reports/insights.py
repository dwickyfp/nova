"""Deterministic observations from complete, authorized widget results."""

from decimal import Decimal, InvalidOperation, localcontext
from statistics import median

from fastapi import HTTPException

from app.modules.smart_reports.runtime import report_runtime
from app.modules.smart_reports.schemas import ReportContextRef, ReportSpecV1, WidgetResult
from app.modules.smart_reports.service import report_service


def number(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def observations(spec: ReportSpecV1, results: list[WidgetResult]) -> tuple[list[dict], list[str]]:
    insights: list[dict] = []
    excluded: list[str] = []
    widgets = spec.widgets()
    for result in results:
        widget = widgets[result.widget_id]
        if result.status != "success" or result.completeness != "complete":
            excluded.append(result.widget_id)
            continue
        if not widget.query:
            continue
        metrics = widget.encoding.y or (
            [widget.encoding.value] if widget.encoding.value else widget.query.metrics[:1]
        )
        for metric in metrics:
            alias = metric.replace(".", "__")
            index = next((i for i, f in enumerate(result.columns) if f in {metric, alias}), None)
            if index is None:
                continue
            evidence = {
                "widget_id": widget.id,
                "metric": metric,
                "query_fingerprint": result.query_fingerprint,
                "query_id": result.query_id,
                "semantic_view_id": result.semantic_view_id,
                "semantic_version": result.semantic_version,
                "evaluated_at": result.evaluated_at,
            }
            if widget.kind == "kpi" and "comparison_period" in result.columns:
                period_index = result.columns.index("comparison_period")
                periods = {row[period_index]: number(row[index]) for row in result.rows}
                current, previous = periods.get("current_period"), periods.get("previous_period")
                if current is not None and previous is not None:
                    with localcontext() as ctx:
                        ctx.prec = 76
                        delta = current - previous
                        rate = delta / abs(previous) if previous else None
                    insights.append(
                        {
                            **evidence,
                            "kind": "period_change",
                            "current": str(current),
                            "previous": str(previous),
                            "change": str(delta),
                            "relative_change": str(rate) if rate is not None else None,
                            "time_context": result.time_context,
                        }
                    )
                continue
            if widget.query.time and widget.query.time.compare:
                continue
            dimension = widget.encoding.x or next(iter(widget.query.dimensions), "period")
            dimension_alias = dimension.replace(".", "__")
            group_index = next(
                (
                    i
                    for i, field in enumerate(result.columns)
                    if field in {dimension, dimension_alias}
                ),
                None,
            )
            if group_index is None or widget.encoding.series:
                continue
            values = [(row[group_index], number(row[index])) for row in result.rows]
            # Null groups are legitimate, but missing measures are not zero observations.
            measured = [(key, value) for key, value in values if value is not None]
            if len(measured) < 2 or len({str(key) for key, _ in measured}) != len(measured):
                continue
            largest = max(value for _, value in measured)
            leaders = [key for key, value in measured if value == largest]
            insights.append(
                {
                    **evidence,
                    "kind": "largest_value",
                    "dimension": dimension,
                    "categories": leaders,
                    "value": str(largest),
                    "group_count": len(measured),
                }
            )
            with localcontext() as ctx:
                ctx.prec = 76
                total = number(result.totals.get(alias, result.totals.get(metric)))
                summed = sum((value for _, value in measured), Decimal(0))
                if total and total > 0 and summed == total and all(v >= 0 for _, v in measured):
                    insights.append(
                        {
                            **evidence,
                            "kind": "contribution",
                            "dimension": dimension,
                            "categories": leaders,
                            "value": str(largest),
                            "total": str(total),
                            "share": str(largest / total),
                            "group_count": len(measured),
                            "method": "Each listed category / independently queried total",
                        }
                    )
                if widget.query.time and widget.query.time.grain and len(measured) >= 8:
                    center = median(value for _, value in measured)
                    deviation = median(abs(value - center) for _, value in measured)
                    if deviation:
                        candidates = [
                            (Decimal("0.67448975") * abs(value - center) / deviation, key, value)
                            for key, value in measured
                        ]
                        for score, key, value in sorted(
                            candidates, key=lambda item: item[0], reverse=True
                        )[:5]:
                            if score > Decimal("3.5"):
                                insights.append(
                                    {
                                        **evidence,
                                        "kind": "unusual_value",
                                        "dimension": dimension,
                                        "categories": [key],
                                        "value": str(value),
                                        "median": str(center),
                                        "median_absolute_deviation": str(deviation),
                                        "score": str(score),
                                        "group_count": len(measured),
                                    "method": (
                                        "Modified z-score > 3.5 using median absolute deviation"
                                    ),
                                    }
                                )
    return insights, excluded


async def inspect_report(reference: ReportContextRef, user: dict) -> dict:
    spec, state = await report_service.view(reference.report_id, reference.view_session_id, user)
    if state.state_revision != reference.state_revision:
        raise HTTPException(409, "Report state changed; inspect the current view")
    if reference.selected_widget_id and reference.selected_widget_id != state.selected_widget_id:
        raise HTTPException(409, "Report selection changed; inspect the current selection")
    rendered = await report_runtime.render(
        spec, state, user, [reference.selected_widget_id] if reference.selected_widget_id else []
    )
    await report_service.verify_view(state, user)
    insights, excluded = observations(
        spec, [WidgetResult.model_validate(widget) for widget in rendered["widgets"]]
    )
    return {
        "reference": reference,
        "state_fingerprint": rendered["state_fingerprint"],
        "as_of": rendered["as_of"],
        "insights": insights,
        "excluded_widgets": excluded,
    }
