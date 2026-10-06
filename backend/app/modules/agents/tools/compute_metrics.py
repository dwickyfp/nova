"""``compute_metrics``: exact arithmetic over a result this turn already verified.

Growth, share, rank, and CAGR are the numbers an analyst actually reports, and
they are rarely columns in a governed result. This tool computes them from an
authorized result table with ``Decimal`` arithmetic and returns a new table, so
every derived number the answer states is itself evidence.

It reads no data: its only input is a table an earlier tool call in this turn
(or the preceding turn) produced under the caller's role. It cannot widen what
the user can see, and it never guesses a missing value.
"""

from __future__ import annotations

from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext
from typing import Any

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

MAX_ROWS = 200
_PLACES = Decimal("0.0001")

OPERATIONS = (
    "pct_change",
    "difference",
    "share_of_total",
    "sum",
    "avg",
    "min",
    "max",
    "rank",
    "cagr",
    "contribution",
    "ratio",
    "combine",
)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "operation": {"type": "string", "enum": list(OPERATIONS)},
        "value_column": {
            "type": "string",
            "description": "Numeric column to compute on (the current period for contribution).",
        },
        "evidence_id": {
            "type": "string",
            "description": "Result to use; defaults to the latest data result.",
        },
        "label_column": {
            "type": "string",
            "description": "Column naming each row (period, city, ...). Defaults to the first "
            "non-numeric column.",
        },
        "from_label": {
            "type": "string", "description": "Start row for pct_change/difference/cagr.",
        },
        "to_label": {"type": "string", "description": "End row for pct_change/difference/cagr."},
        "compare_column": {
            "type": "string",
            "description": "Prior-period numeric column for contribution; the divisor "
            "column for ratio.",
        },
        "with_evidence_id": {
            "type": "string",
            "description": "For combine: the second result, joined to evidence_id on the "
            "label column both share (department, month, ...).",
        },
        "periods": {
            "type": "integer",
            "minimum": 1,
            "description": "Number of periods between from_label and to_label for cagr.",
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}


class ComputeError(ValueError):
    pass


def _decimal(value: Any, *, column: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ComputeError(f"Column {column!r} has a missing value.")
    try:
        number = Decimal(str(value).replace(",", "").rstrip("%").strip())
    except InvalidOperation as exc:
        raise ComputeError(f"Column {column!r} is not numeric.") from exc
    if not number.is_finite():
        raise ComputeError(f"Column {column!r} has a non-finite value.")
    return number


def _label_key(value: str) -> str:
    text = value.strip().casefold()
    return text[:-9] if text.endswith(" 00:00:00") else text


def _round(value: Decimal) -> str:
    return str(value.quantize(_PLACES))


def _percent(value: Decimal) -> str:
    """Percentage points with the sign, so -0.7134% is never read as a fraction (-71%)."""
    return f"{value.quantize(_PLACES)}%"


def _is_numeric(value: Any) -> bool:
    try:
        _decimal(value, column="")
    except ComputeError:
        return False
    return True


def combine(
    first: dict[str, Any], second: dict[str, Any], label_column: str | None = None
) -> dict[str, Any]:
    """Two verified results side by side, on the label column they share.

    Only rows whose label is in both results are kept, and a label must name one
    row in each: nothing is summed, guessed, or filled in.
    """
    tables = []
    for table in (first, second):
        columns = [str(column) for column in table.get("columns") or []]
        rows = [list(row) for row in (table.get("rows") or [])[:MAX_ROWS]]
        if not columns or not rows or any(len(row) != len(columns) for row in rows):
            raise ComputeError("Both results need complete rows to be combined.")
        tables.append((columns, rows))
    (left_columns, left_rows), (right_columns, right_rows) = tables
    shared = [
        column for column in left_columns
        if column.casefold() in {other.casefold() for other in right_columns}
        and not all(_is_numeric(row[left_columns.index(column)]) for row in left_rows)
    ]
    if label_column:
        shared = [column for column in shared if column.casefold() == label_column.casefold()]
    if len(shared) != 1:
        raise ComputeError(
            "The results must share exactly one label column to be combined. "
            f"First: {left_columns}. Second: {right_columns}."
        )
    label = shared[0]
    left_key = left_columns.index(label)
    right_key = next(
        index for index, column in enumerate(right_columns) if column.casefold() == label.casefold()
    )

    def keyed(rows: list[list[Any]], key: int) -> dict[str, list[Any]]:
        by_label: dict[str, list[Any]] = {}
        for row in rows:
            name = _label_key(str(row[key]))
            if name in by_label:
                raise ComputeError(f"{label} {row[key]!r} names more than one row.")
            by_label[name] = row
        return by_label

    left, right = keyed(left_rows, left_key), keyed(right_rows, right_key)
    matched = [name for name in left if name in right]
    if not matched:
        raise ComputeError(f"No {label} value appears in both results.")
    taken = {column.casefold() for column in left_columns}
    right_names = []
    for index, column in enumerate(right_columns):
        if index == right_key:
            continue
        right_names.append((index, column if column.casefold() not in taken else f"{column}_2"))
    unmatched = len(left) + len(right) - 2 * len(matched)
    return {
        "columns": [*left_columns, *(name for _, name in right_names)],
        "rows": [
            [*left[name], *(right[name][index] for index, _ in right_names)] for name in matched
        ],
        "summary": f"Combined {len(matched)} row(s) on {label}"
        + (f"; {unmatched} row(s) had no match and were left out." if unmatched else "."),
    }


def compute(table: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Pure computation; returns ``{"columns", "rows", "summary"}``."""
    columns = [str(column) for column in table.get("columns") or []]
    rows = [list(row) for row in (table.get("rows") or [])[:MAX_ROWS]]
    if not columns or not rows:
        raise ComputeError("The result has no rows to compute on.")
    if any(len(row) != len(columns) for row in rows):
        raise ComputeError("The result has incomplete rows.")
    operation = str(arguments.get("operation") or "")
    if operation not in OPERATIONS:
        raise ComputeError(f"Unsupported operation {operation!r}.")
    value_column = str(arguments.get("value_column") or "")
    if value_column not in columns:
        raise ComputeError(f"Column {value_column!r} is not in the result: {columns}.")
    value_index = columns.index(value_column)
    label_column = arguments.get("label_column")
    if label_column:
        if label_column not in columns:
            raise ComputeError(f"Label column {label_column!r} is not in the result.")
        label_index: int | None = columns.index(str(label_column))
    else:
        label_index = next(
            (
                index for index in range(len(columns))
                if index != value_index and not all(_is_numeric(row[index]) for row in rows)
            ),
            None,
        )
    label_name = columns[label_index] if label_index is not None else "row"
    labels = [
        str(row[label_index]) if label_index is not None else str(position + 1)
        for position, row in enumerate(rows)
    ]
    values = [_decimal(row[value_index], column=value_column) for row in rows]

    def row_of(key: str) -> int:
        label = str(arguments.get(key) or "")
        if not label:
            raise ComputeError(f"{operation} needs {key}.")
        matches = [index for index, item in enumerate(labels) if item == label]
        if not matches:
            # "2026-02-01" names the row "2026-02-01 00:00:00"; case is not meaningful.
            wanted = _label_key(label)
            matches = [index for index, item in enumerate(labels) if _label_key(item) == wanted]
        if len(matches) != 1:
            available = ", ".join(repr(item) for item in labels[:20])
            raise ComputeError(
                f"{key} {label!r} must match exactly one {label_name} value. "
                f"Available: {available}."
            )
        return matches[0]

    with localcontext() as context:
        context.prec = 34
        try:
            return _compute(
                operation, arguments, value_column, label_name, labels, values, row_of,
                rows=rows, columns=columns,
            )
        except (DivisionByZero, InvalidOperation) as exc:
            raise ComputeError("The computation divides by zero.") from exc


def _compute(
    operation: str,
    arguments: dict[str, Any],
    value_column: str,
    label_name: str,
    labels: list[str],
    values: list[Decimal],
    row_of: Any,
    *,
    rows: list[list[Any]],
    columns: list[str],
) -> dict[str, Any]:
    if operation in {"sum", "avg", "min", "max"}:
        result = {
            "sum": sum(values, Decimal(0)),
            "avg": sum(values, Decimal(0)) / len(values),
            "min": min(values),
            "max": max(values),
        }[operation]
        name = f"{operation}_{value_column}"
        return {
            "columns": [name, "row_count"],
            "rows": [[_round(result), len(values)]],
            "summary": f"{name} = {_round(result)} over {len(values)} row(s).",
        }
    if operation in {"pct_change", "difference"} and (
        arguments.get("from_label") or arguments.get("to_label")
    ):
        start, end = row_of("from_label"), row_of("to_label")
        delta = values[end] - values[start]
        if operation == "difference":
            return {
                "columns": ["from", "to", f"{value_column}_difference"],
                "rows": [[labels[start], labels[end], _round(delta)]],
                "summary": f"{value_column} changed by {_round(delta)} from {labels[start]} "
                f"to {labels[end]}.",
            }
        if values[start] == 0:
            raise ComputeError("Percent change from zero is undefined.")
        pct = delta / abs(values[start]) * 100
        return {
            "columns": ["from", "to", f"{value_column}_pct_change"],
            "rows": [[labels[start], labels[end], _percent(pct)]],
            "summary": f"{value_column} changed {_round(pct)}% from {labels[start]} to "
            f"{labels[end]}.",
        }
    if operation in {"pct_change", "difference"}:
        suffix = "pct_change" if operation == "pct_change" else "difference"
        out: list[list[Any]] = []
        for position, (label, value) in enumerate(zip(labels, values, strict=True)):
            if position == 0:
                out.append([label, _round(value), None])
                continue
            previous = values[position - 1]
            if operation == "difference":
                out.append([label, _round(value), _round(value - previous)])
            elif previous == 0:
                out.append([label, _round(value), None])
            else:
                change = (value - previous) / abs(previous) * 100
                out.append([label, _round(value), _percent(change)])
        return {
            "columns": [label_name, value_column, f"{value_column}_{suffix}"],
            "rows": out,
            "summary": f"Row-over-row {suffix} of {value_column} for {len(out)} row(s), "
            "in result order.",
        }
    if operation == "share_of_total":
        total = sum(values, Decimal(0))
        if total == 0:
            raise ComputeError("The total is zero, so shares are undefined.")
        if any(value < 0 for value in values):
            raise ComputeError("Shares of a total with negative values are misleading.")
        out = [
            [label, _round(value), _percent(value / total * 100)]
            for label, value in zip(labels, values, strict=True)
        ]
        return {
            "columns": [label_name, value_column, f"{value_column}_share_pct"],
            "rows": out,
            "summary": f"Share of total {value_column} ({_round(total)}) for {len(out)} row(s).",
        }
    if operation == "rank":
        order = sorted(range(len(values)), key=lambda index: values[index], reverse=True)
        out = [[position + 1, labels[index], _round(values[index])]
               for position, index in enumerate(order)]
        return {
            "columns": ["rank", label_name, value_column],
            "rows": out,
            "summary": f"{len(out)} row(s) ranked by {value_column}, highest first.",
        }
    if operation == "cagr":
        start, end = row_of("from_label"), row_of("to_label")
        periods = int(arguments.get("periods") or abs(end - start))
        if periods < 1:
            raise ComputeError("cagr needs at least one period.")
        if values[start] <= 0 or values[end] <= 0:
            raise ComputeError("cagr needs positive start and end values.")
        growth = (values[end] / values[start]) ** (Decimal(1) / Decimal(periods)) - 1
        return {
            "columns": ["from", "to", "periods", f"{value_column}_cagr_pct"],
            "rows": [[labels[start], labels[end], periods, _percent(growth * 100)]],
            "summary": f"{value_column} CAGR {_round(growth * 100)}% over {periods} period(s).",
        }
    compare_column = str(arguments.get("compare_column") or "")
    if operation == "ratio":
        # One column per unit of another, row by row (expense per employee).
        if compare_column not in columns or compare_column == value_column:
            raise ComputeError("ratio needs compare_column (the divisor column).")
        compare_index = columns.index(compare_column)
        divisors = [_decimal(row[compare_index], column=compare_column) for row in rows]
        name = f"{value_column}_per_{compare_column}"
        out = [
            [label, _round(value), _round(divisor),
             _round(value / divisor) if divisor != 0 else None]
            for label, value, divisor in zip(labels, values, divisors, strict=True)
        ]
        return {
            "columns": [label_name, value_column, compare_column, name],
            "rows": out,
            "summary": f"{name} for {len(out)} row(s); a zero divisor leaves the ratio empty.",
        }
    # contribution: each row's share of the total change between two columns.
    if compare_column not in columns:
        raise ComputeError("contribution needs compare_column (the prior-period column).")
    compare_index = columns.index(compare_column)
    prior = [_decimal(row[compare_index], column=compare_column) for row in rows]
    changes = [current - before for current, before in zip(values, prior, strict=True)]
    total_change = sum(changes, Decimal(0))
    out = [
        [
            label, _round(change),
            _percent(change / total_change * 100) if total_change != 0 else None,
        ]
        for label, change in zip(labels, changes, strict=True)
    ]
    return {
        "columns": [label_name, f"{value_column}_change", "share_of_total_change_pct"],
        "rows": out,
        "summary": f"Total change {_round(total_change)} split across {len(out)} row(s). "
        "These are arithmetic contributions, not proven causes.",
    }


class ComputeMetricsTool:
    name = "compute_metrics"
    description = (
        "Compute growth (pct_change), difference, share_of_total, sum/avg/min/max, rank, CAGR, "
        "contribution to change, or a ratio of two columns from a data result already returned "
        "in this conversation; combine joins two such results on the label column they share. "
        "Use it before stating any number that is not a result cell. It reads no new data."
    )
    parameters = PARAMETERS
    classification: ToolClassification = "read_only"
    #: Consent is inherited fail-closed like every tool: under an auto_read_only
    #: policy it runs without a prompt, under ask_every_tool it asks.
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        args = invocation.arguments or {}
        return f"compute_metrics: {args.get('operation', '')} of {args.get('value_column', '')}"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        args = dict(invocation.arguments or {})
        tables = getattr(context, "evidence_tables", None) or {}
        evidence_id = args.get("evidence_id")
        if evidence_id:
            table = tables.get(str(evidence_id))
            if table is None:
                return ToolOutcome(
                    ok=False, summary="", error="Unknown evidence_id.",
                    error_class="INVALID_TOOL_ARGUMENTS", recoverable=True,
                    safe_detail="Use an evidence_id of a data result from this turn.",
                    repair_context={"evidence_ids": list(tables)[-8:]},
                )
        else:
            table = getattr(context, "last_result", None)
            if not isinstance(table, dict) and tables:
                table = list(tables.values())[-1]
        if not isinstance(table, dict):
            return ToolOutcome(
                ok=False, summary="", error="Run a data query first.",
                error_class="NO_DATA_RESULT", recoverable=True,
                safe_detail="compute_metrics needs a data result from this conversation.",
            )
        try:
            if args.get("operation") == "combine":
                other = tables.get(str(args.get("with_evidence_id") or ""))
                if other is None or not evidence_id:
                    raise ComputeError(
                        "combine needs evidence_id and with_evidence_id of two data results: "
                        + ", ".join(list(tables)[-8:])
                    )
                result = combine(table, other, args.get("label_column"))
            else:
                result = compute(table, args)
        except ComputeError as exc:
            return ToolOutcome(
                ok=False, summary="", error=str(exc), error_class="INVALID_TOOL_ARGUMENTS",
                recoverable=True, safe_detail=str(exc),
                repair_context={"columns": list(table.get("columns") or [])[:40]},
            )
        if hasattr(context, "last_result"):
            # A chart or a further computation continues from this table.
            context.last_result = {
                "title": f"{args['operation']} of {args.get('value_column') or 'results'}",
                "columns": result["columns"], "rows": result["rows"],
            }
        return ToolOutcome(
            ok=True,
            summary=result["summary"],
            data={"columns": result["columns"], "rows": result["rows"]},
            table={
                "title": f"{args['operation']} of {args.get('value_column') or 'results'}",
                "columns": result["columns"],
                "rows": result["rows"],
            },
            evidence={"source": "computed_from_authorized_result"},
            metadata={"operation": args["operation"], "evidence_kind": "derived"},
        )


compute_metrics_tool = ComputeMetricsTool()
