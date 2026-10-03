"""Exact arithmetic decomposition of a caller-authorized two-period result."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("The comparison contains a missing numeric value.")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("The comparison contains a nonnumeric value.") from exc
    if not number.is_finite():
        raise ValueError("The comparison contains a non-finite value.")
    return number


def decompose_change(
    table: dict[str, Any],
    *,
    prior_period: str,
    current_period: str,
    revenue_column: str,
    units_column: str | None = None,
    returns_column: str | None = None,
    dimension_columns: list[str] | None = None,
) -> dict[str, Any]:
    """Explain arithmetic change, leaving unknown causes explicitly unassigned."""
    columns = [str(column) for column in table.get("columns") or []]
    if not columns or len(columns) > 100:
        raise ValueError("The result has no usable columns.")
    period_column = next((name for name in columns if name.lower() == "period"), None)
    if period_column is None or revenue_column not in columns:
        raise ValueError("The result needs period and revenue columns.")
    if units_column and units_column not in columns:
        raise ValueError("The selected units column is missing.")
    if returns_column and returns_column not in columns:
        raise ValueError("The selected returns column is missing.")
    if prior_period == current_period:
        raise ValueError("Choose two different periods.")
    rows = list(table.get("rows") or [])
    if len(rows) > 200:
        raise ValueError("The result exceeds the decomposition limit; narrow the query.")
    if any(len(row) != len(columns) for row in rows):
        raise ValueError("The result contains incomplete rows.")
    if dimension_columns:
        if (len(dimension_columns) > 3 or len(set(dimension_columns)) != len(dimension_columns)
                or any(name not in columns or name == period_column for name in dimension_columns)):
            raise ValueError("Select up to three distinct dimension columns.")
        return _dimension_change(
            rows, columns, period_column, prior_period, current_period,
            revenue_column, returns_column, dimension_columns,
        )
    labels = [str(row[columns.index(period_column)]) for row in rows]
    if labels.count(prior_period) != 1 or labels.count(current_period) != 1:
        raise ValueError("Each selected period must appear exactly once.")
    prior = rows[labels.index(prior_period)]
    current = rows[labels.index(current_period)]
    gross_prior = _decimal(prior[columns.index(revenue_column)])
    gross_current = _decimal(current[columns.index(revenue_column)])
    returns_prior = _decimal(prior[columns.index(returns_column)]) if returns_column else Decimal(0)
    returns_current = (
        _decimal(current[columns.index(returns_column)]) if returns_column else Decimal(0)
    )
    before = gross_prior - returns_prior
    after = gross_current - returns_current
    delta = after - before
    components: list[tuple[str, Decimal]] = []
    if units_column:
        units_prior = _decimal(prior[columns.index(units_column)])
        units_current = _decimal(current[columns.index(units_column)])
        if units_prior > 0 and units_current > 0:
            price_prior = gross_prior / units_prior
            price_current = gross_current / units_current
            units_delta = units_current - units_prior
            price_delta = price_current - price_prior
            volume_effect = units_delta * price_prior
            unit_value_effect = units_prior * price_delta
            components.extend(
                [
                    ("volume", volume_effect),
                    ("unit_value", unit_value_effect),
                    (
                        "interaction",
                        gross_current - gross_prior - volume_effect - unit_value_effect,
                    ),
                ]
            )
    if returns_column:
        components.append(("returns", -(returns_current - returns_prior)))
    explained = sum((value for _, value in components), Decimal(0))
    if delta != explained:
        components.append(("unassigned", delta - explained))
    return {
        "prior_period": prior_period,
        "current_period": current_period,
        "prior_net": str(before),
        "current_net": str(after),
        "net_change": str(delta),
        "components": [{"name": name, "change": str(value)} for name, value in components],
        "reconciled": sum((value for _, value in components), Decimal(0)) == delta,
    }


def _dimension_change(rows, columns, period, prior, current, revenue, returns, dimensions):
    periods = {prior: {}, current: {}}
    for row in rows:
        label = str(row[columns.index(period)])
        if label not in periods:
            continue
        key = json.dumps([row[columns.index(name)] for name in dimensions], default=str)
        if key in periods[label]:
            raise ValueError("Each dimension tuple must appear once per period.")
        periods[label][key] = _decimal(row[columns.index(revenue)]) - (
            _decimal(row[columns.index(returns)]) if returns else Decimal(0)
        )
    if not all(periods.values()):
        raise ValueError("Both selected periods need observations.")
    before, after = periods[prior], periods[current]
    changes = {key: after.get(key, Decimal(0)) - before.get(key, Decimal(0))
               for key in before.keys() | after.keys()}
    ranked = sorted(changes, key=lambda key: (-abs(changes[key]), key))[:10]
    total = sum(after.values(), Decimal(0)) - sum(before.values(), Decimal(0))
    residual = total - sum((changes[key] for key in ranked), Decimal(0))
    components = [{"name": key, "change": str(changes[key])} for key in ranked]
    components.append({"name": "unassigned", "change": str(residual)})
    return {
        "prior_period": prior, "current_period": current,
        "prior_net": str(sum(before.values(), Decimal(0))),
        "current_net": str(sum(after.values(), Decimal(0))), "net_change": str(total),
        "dimensions": dimensions, "components": components, "residual": str(residual),
        "reconciled": sum((_decimal(item["change"]) for item in components), Decimal(0)) == total,
        "causal_status": "arithmetic", "method": "exact-dimension-difference-v1",
    }


class DiagnoseChangeTool:
    name = "diagnose_change"
    description = (
        "Decompose the last authorized two-period result into exact volume, unit-value, "
        "interaction, and returns contributions when those columns exist. "
        "For grouped comparisons, rank dimensional contributions using dimension_columns. "
        "Unexplained change stays unassigned; arithmetic contribution is not causal proof."
    )
    parameters = {
        "type": "object",
        "properties": {
            "prior_period": {"type": "string"},
            "current_period": {"type": "string"},
            "revenue_column": {"type": "string"},
            "units_column": {"type": "string"},
            "returns_column": {"type": "string"},
            "dimension_columns": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        },
        "required": ["prior_period", "current_period", "revenue_column"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        args = invocation.arguments or {}
        return (
            "Diagnose the last authorized result: "
            f"{str(args.get('prior_period', ''))[:80]} → "
            f"{str(args.get('current_period', ''))[:80]}"
        )

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        table = getattr(context, "last_result", None)
        if not isinstance(table, dict):
            return ToolOutcome(ok=False, summary="", error="Run a two-period data query first.")
        args = invocation.arguments or {}
        try:
            result = decompose_change(
                table,
                prior_period=str(args.get("prior_period") or ""),
                current_period=str(args.get("current_period") or ""),
                revenue_column=str(args.get("revenue_column") or ""),
                units_column=str(args["units_column"]) if args.get("units_column") else None,
                returns_column=str(args["returns_column"]) if args.get("returns_column") else None,
                dimension_columns=args.get("dimension_columns"),
            )
        except ValueError as exc:
            return ToolOutcome(ok=False, summary="", error=str(exc))
        rows = [[item["name"], item["change"]] for item in result["components"]]
        rows.append(["net_change", result["net_change"]])
        return ToolOutcome(
            ok=True,
            summary=(
                f"Net change {result['net_change']} from {result['prior_period']} to "
                f"{result['current_period']}. Components reconcile exactly. "
                "These are arithmetic contributions, not proven root causes."
            ),
            data=result,
            table={
                "title": "Change decomposition",
                "columns": ["component", "change"],
                "rows": rows,
            },
            evidence={"source": "last_authorized_result"},
        )


diagnose_change_tool = DiagnoseChangeTool()
