"""Questions answered from the open dashboard's own datasets.

The Dataviz assistant answers what, who, when, where, why and how about the
dashboard on screen (docs/arch-19-studio-dataviz.md, D6). Its numbers come
from the same datasets the charts draw, through Superset's chart data API in
the caller's own BI session, so Nova's proxy and Ranger decide the rows and the
answer agrees with the charts.

* Only datasets the dashboard uses can be queried.
* Metrics are the dataset's saved metrics or a simple aggregate of one of its
  columns; dimensions must be its columns. Nothing else reaches Superset.
* The dashboard's native filters are applied when their state is verified
  (read from Superset's filter-state API); each one applied, or not applicable
  to this dataset, is reported. Unverified filters are never guessed.
* A comparison period runs the same query twice and ranks every member by its
  contribution to the change. A contribution says where a change happened, not
  why it happened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .client import BiError, SupersetClient

MAX_ROWS = 200
_AGGREGATE = re.compile(r"^(SUM|AVG|MIN|MAX|COUNT|COUNT_DISTINCT)\(([A-Za-z_][\w]*)\)$", re.I)
_PERIOD = re.compile(r"^\d{4}-\d{2}-\d{2} : \d{4}-\d{2}-\d{2}$")


class QueryRefused(ValueError):
    """The request names something the dashboard's dataset does not have."""


@dataclass
class DashboardAnswer:
    title: str
    columns: list[str]
    rows: list[list[Any]]
    facts: dict[str, Any] = field(default_factory=dict)


def _metric(name: str, saved: set[str], columns: set[str]) -> tuple[Any, str]:
    if name in saved:
        return name, name
    match = _AGGREGATE.match(name.strip())
    if not match or match.group(2) not in columns:
        raise QueryRefused(
            f"{name!r} is neither a saved metric of this dataset nor an aggregate of one of its "
            "columns (SUM, AVG, MIN, MAX, COUNT or COUNT_DISTINCT)."
        )
    aggregate, column = match.group(1).upper(), match.group(2)
    label = f"{aggregate}({column})"
    return {
        "expressionType": "SIMPLE",
        "column": {"column_name": column},
        "aggregate": aggregate,
        "label": label,
    }, label


GRAINS = {"day": "P1D", "week": "P1W", "month": "P1M", "quarter": "P3M", "year": "P1Y"}
_WORD = re.compile(r"[a-z0-9]+")


def resolve_dimension(name: str, described: dict[str, str]) -> str | None:
    """The dataset column a business word names, from column names and their
    descriptions (``salesman`` -> ``employee_name``), or None when it is not
    one clear column. Identifier columns lose to a readable one."""
    if name in described:
        return name
    words = set(_WORD.findall(name.lower()))
    if not words:
        return None
    hits = [
        column
        for column, text in described.items()
        if words <= set(_WORD.findall(f"{column} {text}".lower().replace("_", " ")))
    ]
    readable = [c for c in hits if not c.endswith("_id") and c != "id"] or hits
    named = [c for c in readable if c.endswith("_name")]
    chosen = named if len(named) == 1 else readable
    return chosen[0] if len(chosen) == 1 else None


_DAY = r"\d{4}-\d{2}-\d{2}"


def normalize_period(value: str | None) -> str | None:
    """A period in the one form Superset takes, from the forms a model writes:
    ``A : B``, ``A:B``, ``A to B``, ``A - B`` (end excluded) or a month ``YYYY-MM``.
    Returns None for an empty value or one that names no dates."""
    from datetime import date

    text = (value or "").strip()
    if not text:
        return None
    month = re.fullmatch(r"(\d{4})-(\d{2})", text)
    if month:
        year, number = int(month.group(1)), int(month.group(2))
        if not 1 <= number <= 12:
            return text
        start = date(year, number, 1)
        end = date(year + number // 12, number % 12 + 1, 1)
        return f"{start.isoformat()} : {end.isoformat()}"
    pair = re.fullmatch(rf"({_DAY})\s*(?::|to|-|–)\s*({_DAY})", text)
    if pair:
        return f"{pair.group(1)} : {pair.group(2)}"
    return text if re.search(r"\d", text) else None


def period_label(window: str | None) -> str:
    """``2026-10`` for a whole calendar month, else ``start..last day``."""
    from datetime import date, timedelta

    if not window:
        return ""
    start_text, end_text = (part.strip() for part in window.split(":"))
    start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
    next_month = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    if start.day == 1 and end == next_month:
        return start.strftime("%Y-%m")
    return f"{start.isoformat()}..{(end - timedelta(days=1)).isoformat()}"


def _period_label(value: Any, grain: str) -> Any:
    if not isinstance(value, int | float):
        return value
    from datetime import UTC, datetime

    moment = datetime.fromtimestamp(value / 1000, UTC)
    if grain == "P1M":
        return moment.strftime("%Y-%m")
    if grain == "P1Y":
        return moment.strftime("%Y")
    if grain == "P3M":
        return f"{moment.year}-Q{(moment.month - 1) // 3 + 1}"
    return moment.strftime("%Y-%m-%d")


def _round(value: Any) -> Any:
    """Two decimals, the precision an answer quotes: a float tail such as
    1492211974.5277934 would make the quoted Rp1.492.211.974,53 look invented."""
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, list):
        return [_round(item) for item in value]
    if isinstance(value, dict):
        return {key: _round(item) for key, item in value.items()}
    return value


def _rounded(answer: DashboardAnswer) -> DashboardAnswer:
    answer.rows = _round(answer.rows)
    answer.facts = _round(answer.facts)
    return answer


async def _rows(bi: SupersetClient, dataset_id: int, query: dict[str, Any]) -> list[dict]:
    body = await bi.request(
        "POST",
        "/api/v1/chart/data",
        body={
            "datasource": {"id": dataset_id, "type": "table"},
            "force": False,
            "queries": [query],
            "result_format": "json",
            "result_type": "full",
        },
    )
    result = (body.get("result") or [{}])[0]
    return list(result.get("data") or [])


async def query_dashboard_dataset(
    user: dict[str, Any],
    *,
    dashboard_id: int,
    dataset_id: int,
    metrics: list[str],
    dimensions: list[str] | None = None,
    native_filters_key: str | None = None,
    period: str | None = None,
    compare_period: str | None = None,
    time_column: str | None = None,
    row_limit: int = 50,
    time_grain: str | None = None,
) -> DashboardAnswer:
    from .dashboard_context import dashboard_context

    context = await dashboard_context(user, dashboard_id, native_filters_key=native_filters_key)
    if not context.get("available"):
        raise QueryRefused(context.get("reason") or "This dashboard is not available.")
    used = {chart["source"].get("dataset_id") for chart in context["charts"]}
    if dataset_id not in used:
        raise QueryRefused("That dataset is not used by the open dashboard.")
    adjusted: list[str] = []
    written_period = period
    period = normalize_period(period)
    written_compare = compare_period
    compare_period = normalize_period(compare_period)
    if written_compare and compare_period is None and not re.search(r"\d", written_compare):
        adjusted.append(f"compare_period {written_compare!r} names no dates, so no comparison")
        written_compare = None
    for value, written in ((period, written_period), (compare_period, written_compare)):
        if written and (value is None or not _PERIOD.match(value)):
            raise QueryRefused("Periods are written 'YYYY-MM-DD : YYYY-MM-DD' (end excluded).")
    if compare_period and not period:
        raise QueryRefused("A comparison needs the period it is compared with.")
    if compare_period and compare_period.replace(" ", "") == (period or "").replace(" ", ""):
        compare_period = None
        adjusted.append("compare_period equals period, so no comparison was made")

    async with SupersetClient.for_user(user, author=False) as bi:
        try:
            detail = (await bi.request("GET", f"/api/v1/dataset/{dataset_id}")).get("result") or {}
        except BiError as exc:
            raise QueryRefused("The dataset is not readable with your active role.") from exc
        columns = {str(c["column_name"]) for c in detail.get("columns") or []}
        temporal = [str(c["column_name"]) for c in detail.get("columns") or [] if c.get("is_dttm")]
        saved = {str(m["metric_name"]) for m in detail.get("metrics") or []}
        described = {
            str(c["column_name"]): str(c.get("verbose_name") or "")
            + " "
            + str(c.get("description") or "")
            for c in detail.get("columns") or []
        }
        groupby, missing = [], []
        for requested in [d for d in dimensions or [] if d]:
            column = resolve_dimension(requested, described)
            if column is None:
                missing.append(requested)
            elif column not in groupby:
                groupby.append(column)
                if column != requested:
                    adjusted.append(f"dimension {requested!r} read as column {column!r}")
        if missing:
            raise QueryRefused(
                "Not columns of this dataset: "
                + ", ".join(missing)
                + ". Its columns: "
                + ", ".join(sorted(columns))[:1500]
            )
        if not metrics:
            raise QueryRefused("Name at least one metric.")
        built = [_metric(name, saved, columns) for name in metrics[:6]]
        time_col = time_column or detail.get("main_dttm_col") or (temporal[0] if temporal else None)
        if period and (not time_col or time_col not in columns):
            raise QueryRefused("This dataset has no time column to apply a period to.")

        applied, skipped = [], []
        filters: list[dict[str, Any]] = []
        verified = context["filter_state_status"] == "verified"
        for item in context["filters"]:
            value = item.get("value") if verified else None
            if value in (None, [], ""):
                continue
            if item.get("column") in columns:
                values = value if isinstance(value, list) else [value]
                filters.append({"col": item["column"], "op": "IN", "val": values})
                applied.append(
                    {"filter": item.get("name"), "column": item["column"], "values": values}
                )
            else:
                skipped.append(
                    {"filter": item.get("name"), "reason": "not a column of this dataset"}
                )

        grain = GRAINS.get((time_grain or "month").lower(), "P1M")

        def query(window: str | None) -> dict[str, Any]:
            extra = (
                [{"col": time_col, "op": "TEMPORAL_RANGE", "val": window}]
                if window and time_col
                else []
            )
            first = built[0][0]
            return {
                "columns": [
                    {
                        "columnType": "BASE_AXIS",
                        "expressionType": "SQL",
                        "sqlExpression": d,
                        "label": d,
                        "timeGrain": grain,
                    }
                    if d in temporal
                    else d
                    for d in groupby
                ],
                "metrics": [metric for metric, _ in built],
                "filters": [*filters, *extra],
                "orderby": [[first, False]] if groupby else [],
                "row_limit": max(1, min(row_limit, MAX_ROWS)),
                "extras": {},
            }

        if compare_period and set(groupby) & set(temporal):
            # The periods already split time; a time dimension would only
            # misalign the two sides of the comparison.
            dropped = [d for d in groupby if d in temporal]
            groupby = [d for d in groupby if d not in temporal]
            adjusted.append(
                "time dimension " + ", ".join(dropped) + " dropped: the periods are compared"
            )
        if compare_period and len(groupby) > 1:
            adjusted.append(
                "a change is broken down by one dimension at a time: used "
                + repr(groupby[0])
                + ", not "
                + ", ".join(repr(d) for d in groupby[1:])
            )
            groupby = groupby[:1]
        current = await _rows(bi, dataset_id, query(period))
        previous = await _rows(bi, dataset_id, query(compare_period)) if compare_period else None
        for row in [*current, *(previous or [])]:
            for d in groupby:
                if d in temporal:
                    row[d] = _period_label(row.get(d), grain)
        # Totals come from their own queries: a breakdown can be cut at its row
        # limit, and a total summed from cut rows would be wrong.
        totals_now = (
            (await _rows(bi, dataset_id, {**query(period), "columns": [], "orderby": []}) or [{}])[
                0
            ]
            if groupby
            else None
        )
        totals_then = (
            (
                await _rows(bi, dataset_id, {**query(compare_period), "columns": [], "orderby": []})
                or [{}]
            )[0]
            if groupby and compare_period
            else None
        )

    labels = [label for _, label in built]
    facts: dict[str, Any] = {
        "dashboard_id": dashboard_id,
        "dataset_id": dataset_id,
        "dataset": detail.get("table_name"),
        "filter_state_status": context["filter_state_status"],
        "filters_applied": applied,
        "filters_not_applicable": skipped,
        "period": period,
        "compare_period": compare_period,
        "adjusted": adjusted,
        "time_grain": next((name for name, code in GRAINS.items() if code == grain), None)
        if set(groupby) & set(temporal)
        else None,
        "time_column": time_col if period else None,
        "dimensions": groupby,
        # What a Dataviz chart needs to draw this answer again on the same
        # dataset (no rows, no SQL): saved metric names or column aggregates.
        "measures": [
            {"saved": metric}
            if isinstance(metric, str)
            else {"column": metric["column"]["column_name"], "aggregate": metric["aggregate"]}
            for metric, _label in built
        ],
        "source_kind": next(
            (
                c["source"]["kind"]
                for c in context["charts"]
                if c["source"].get("dataset_id") == dataset_id
            ),
            None,
        ),
        "concepts": [
            concept
            for chart in context["charts"]
            if chart["source"].get("dataset_id") == dataset_id
            for concept in chart["concepts"]
        ][:8],
    }
    limit = max(1, min(row_limit, MAX_ROWS))
    facts["truncated"] = bool(groupby) and (
        len(current) >= limit or (previous is not None and len(previous) >= limit)
    )
    pad = [""] * max(0, len(groupby) - 1)
    title = f"{', '.join(labels)}" + (f" by {', '.join(groupby)}" if groupby else "")
    if previous is None:
        rows = [
            [row.get(d) for d in groupby] + [row.get(label) for label in labels] for row in current
        ]
        if totals_now is not None:
            rows.insert(0, ["Total", *pad, *(totals_now.get(label) for label in labels)])
        return _rounded(
            DashboardAnswer(title=title, columns=[*groupby, *labels], rows=rows, facts=facts)
        )

    # Contribution of each member to the change of the first metric.
    first = labels[0]

    def key(row: dict) -> tuple:
        return tuple(str(row.get(d)) for d in groupby)

    before = {key(row): row for row in previous}
    after = {key(row): row for row in current}
    if groupby:
        total_after = float((totals_now or {}).get(first) or 0)
        total_before = float((totals_then or {}).get(first) or 0)
    else:
        total_after = float((current or [{}])[0].get(first) or 0)
        total_before = float((previous or [{}])[0].get(first) or 0)
    change = total_after - total_before

    def pct(now: float, then: float) -> float | None:
        return round((now - then) / then * 100, 2) if then else None

    merged = []
    for member in dict.fromkeys([*after, *before]) if groupby else []:
        now = float((after.get(member) or {}).get(first) or 0)
        then = float((before.get(member) or {}).get(first) or 0)
        delta = now - then
        share = round(delta / change * 100, 2) if change else None
        merged.append([*member, now, then, delta, pct(now, then), share])
    merged.sort(key=lambda row: abs(row[-3]), reverse=True)
    merged.insert(
        0,
        [
            "Total",
            *pad,
            total_after,
            total_before,
            change,
            pct(total_after, total_before),
            100.0 if change else None,
        ][0 if groupby else 1 :],
    )
    by_value = sorted(merged[1:], key=lambda row: row[len(groupby)], reverse=True)
    facts |= {
        # Rows below Total are ordered by the size of their change, so the
        # first one is the biggest mover, not the biggest member.
        "row_order": "by absolute change (largest mover first)",
        # Each mover with its own numbers, so a share is never read off the
        # neighbouring row of a wide table.
        "top_contributors": [
            {
                "member": " / ".join(str(v) for v in row[: len(groupby)]),
                "change": row[len(groupby) + 2],
                "change_pct": row[len(groupby) + 3],
                "share_of_change_pct": row[len(groupby) + 4],
            }
            for row in merged[1:6]
        ],
        "largest_in_period": [
            {"member": " / ".join(str(v) for v in row[: len(groupby)]), first: row[len(groupby)]}
            for row in by_value[:3]
        ],
        "total": total_after,
        "total_previous": total_before,
        "change": change,
        "change_pct": pct(total_after, total_before),
        "measure": first,
    }
    return _rounded(
        DashboardAnswer(
            title=f"Change in {first}" + (f" by {', '.join(groupby)}" if groupby else ""),
            columns=[
                *groupby,
                # Each value column names its period, so "net revenue rose from
                # X in September to Y in October" can be checked against them.
                f"{first.replace('_', ' ')} {period_label(period)}",
                f"{first.replace('_', ' ')} {period_label(compare_period)}",
                "change",
                "change_pct",
                "share_of_change_pct",
            ],
            rows=merged[: MAX_ROWS + 1],
            facts=facts,
        )
    )
