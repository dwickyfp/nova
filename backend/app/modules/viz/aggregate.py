"""Plan one governed aggregate SELECT over a dataset's authorized source query.

Only catalog-bound column names become identifiers, and they are always quoted.
Every filter operand is a typed engine parameter; values never enter SQL text.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.sql_frontend.binding.starrocks import _identifier
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parameters import ParameterSpec, ParameterValue

from .contracts.common import (
    BooleanValue,
    DateValue,
    DecimalValue,
    FloatValue,
    IntegerValue,
    Scalar,
    StringValue,
    TimestampValue,
)
from .contracts.datasets import DatasetField
from .contracts.filters import And, Expression, Not, Or, Predicate, RelativeWindow
from .contracts.queries import DatasetQueryRequest, QueryColumn

NUMERIC = {"integer", "decimal", "float"}
TEMPORAL = {"date", "timestamp"}
_COMPARE = {"eq": "=", "neq": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}


@dataclass
class AggregatePlan:
    sql: str
    columns: tuple[QueryColumn, ...]
    output_fields: tuple[DatasetField, ...]
    specs: list[ParameterSpec] = field(default_factory=list)
    values: list[tuple[str, ParameterValue]] = field(default_factory=list)


def _decimal_type(field_: DatasetField) -> tuple[int, int]:
    text = field_.physical_type.lower()
    if "(" in text:
        try:
            precision, scale = (int(part) for part in text.split("(", 1)[1].rstrip(")").split(","))
            return precision, scale
        except ValueError:
            pass
    return 38, 9


def _measure_sql(field_: DatasetField | None, aggregation: str) -> tuple[str, str]:
    """Return the expression and its exact output logical type."""
    if field_ is None:
        return "COUNT(*)", "integer"
    column = _identifier(field_.source_column)
    kind = field_.logical_type
    if aggregation == "count":
        return f"COUNT({column})", "integer"
    if aggregation == "count_distinct":
        return f"COUNT(DISTINCT {column})", "integer"
    if aggregation in {"min", "max"}:
        if kind not in NUMERIC | TEMPORAL | {"string"}:
            raise SemanticError("This field cannot be ordered")
        return f"{aggregation.upper()}({column})", kind
    if kind not in NUMERIC:
        raise SemanticError("Sum and average require a numeric field")
    if kind == "float":
        return f"CAST({aggregation.upper()}({column}) AS DOUBLE)", "float"
    scale = _decimal_type(field_)[1] if kind == "decimal" else 0
    if aggregation == "sum":
        # Widen before summing so integer totals cannot overflow BIGINT silently.
        return f"CAST(SUM(CAST({column} AS DECIMAL(38,{scale}))) AS DECIMAL(38,{scale}))", "decimal"
    average = f"AVG(CAST({column} AS DECIMAL(38,{scale})))"
    return f"CAST({average} AS DECIMAL(38,{max(scale, 6)}))", "decimal"


def _dimension_sql(field_: DatasetField, grain: str | None) -> str:
    column = _identifier(field_.source_column)
    if grain is None:
        return column
    if field_.logical_type not in TEMPORAL:
        raise SemanticError("A date grain requires a date or timestamp field")
    truncated = f"date_trunc('{grain}', {column})"
    return f"CAST({truncated} AS DATE)" if field_.logical_type == "date" else truncated


def _shift_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _unit_start(day: date, window: RelativeWindow) -> date:
    if window.unit == "day":
        return day
    if window.unit == "week":
        offset = day.weekday() if window.week_start == "iso" else (day.weekday() + 1) % 7
        return day - timedelta(days=offset)
    months = {"month": 1, "quarter": 3, "year": 12}[window.unit]
    shifted = (day.month - window.fiscal_start_month) % 12
    first = _shift_months(date(day.year, day.month, 1), -(shifted % months))
    return first


def _advance(start: date, window: RelativeWindow, count: int) -> date:
    if window.unit == "day":
        return start + timedelta(days=count)
    if window.unit == "week":
        return start + timedelta(weeks=count)
    return _shift_months(start, count * {"month": 1, "quarter": 3, "year": 12}[window.unit])


def relative_bounds(window: RelativeWindow, now: datetime) -> tuple[date, date]:
    """Half-open local calendar bounds [start, end) for a relative window."""
    today = now.astimezone(ZoneInfo(window.timezone)).date()
    current = _unit_start(today, window)
    if window.mode == "previous":
        return _advance(current, window, -window.count), current
    if window.mode == "to_date":
        return current, today + timedelta(days=1)
    # Rolling: the current partial unit plus count-1 complete units before it.
    return _advance(current, window, -(window.count - 1)), today + timedelta(days=1)


def _python_value(value: Scalar) -> tuple[str, ParameterValue, int | None, int | None]:
    if isinstance(value, StringValue):
        return "string", value.value, None, None
    if isinstance(value, BooleanValue):
        return "boolean", value.value, None, None
    if isinstance(value, IntegerValue):
        return "integer", int(value.value), None, None
    if isinstance(value, DecimalValue | FloatValue):
        text = value.value if isinstance(value, DecimalValue) else repr(value.value)
        number = Decimal(text)
        if not number.is_finite():
            raise SemanticError("Filter numbers must be finite")
        exponent = number.as_tuple().exponent
        scale = max(0, -exponent) if isinstance(exponent, int) else 0
        if scale > 37 or len(number.as_tuple().digits) > 38:
            raise SemanticError("Filter number exceeds 38 digits")
        return "decimal", number, 38, scale
    if isinstance(value, DateValue):
        return "date", date.fromisoformat(value.value), None, None
    if isinstance(value, TimestampValue):
        return "timestamp", datetime.fromisoformat(value.value), None, None
    raise SemanticError("Unsupported filter value")


def _compatible(field_: DatasetField, kind: str) -> bool:
    logical = field_.logical_type
    if logical in NUMERIC:
        return kind in {"integer", "decimal"}
    if logical == "timestamp":
        return kind in {"timestamp", "date"}
    return logical == kind


class _Filters:
    def __init__(self, plan: AggregatePlan, fields: dict[str, DatasetField], dataset_id: str,
                 revision: int, reserved: set[str], now: datetime) -> None:
        self.plan, self.fields, self.now = plan, fields, now
        self.dataset_id, self.revision = dataset_id, revision
        self.reserved = reserved
        self.counter = 0

    def _bind(self, kind: str, value: ParameterValue, precision: int | None, scale: int | None,
              multiple: bool = False) -> str:
        name = f"nova_q{self.counter}"
        while name in self.reserved:
            name += "_"
        self.counter += 1
        self.reserved.add(name)
        self.plan.specs.append(ParameterSpec(name, kind, multiple=multiple,  # type: ignore[arg-type]
                                             precision=precision, scale=scale))
        self.plan.values.append((name, value))
        return f":{name}"

    def _operand(self, target: DatasetField, value: Scalar) -> str:
        kind, python, precision, scale = _python_value(value)
        if not _compatible(target, kind):
            raise SemanticError("Filter value type does not match its field")
        if kind == "date" and target.logical_type == "timestamp":
            python = datetime.combine(python, datetime.min.time(),  # type: ignore[arg-type]
                                      ZoneInfo(target.timezone or "UTC"))
            kind = "timestamp"
        return self._bind(kind, python, precision, scale)

    def compile(self, node: Expression) -> str:
        if isinstance(node, And | Or):
            joiner = " AND " if isinstance(node, And) else " OR "
            return "(" + joiner.join(self.compile(child) for child in node.children) + ")"
        if isinstance(node, Not):
            return f"(NOT {self.compile(node.child)})"
        return self._predicate(node)

    def _predicate(self, node: Predicate) -> str:
        if node.stage != "row":
            raise SemanticError("Only row-level filters are supported in dataset queries")
        if (str(node.field.dataset_id) != self.dataset_id
                or node.field.dataset_revision != self.revision):
            raise SemanticError("Filter field belongs to another dataset revision")
        target = self.fields.get(node.field.field_id)
        if target is None:
            raise SemanticError("Filter references an unknown field")
        column = _identifier(target.source_column)
        op = node.op
        if op == "is_null":
            return f"({column} IS NULL)"
        if op == "is_not_null":
            return f"({column} IS NOT NULL)"
        if op in _COMPARE:
            return f"({column} {_COMPARE[op]} {self._operand(target, node.values[0])})"
        if op == "between":
            low, high = (self._operand(target, value) for value in node.values)
            return f"({column} BETWEEN {low} AND {high})"
        if op in {"in", "not_in"}:
            converted = [_python_value(value) for value in node.values]
            kinds = {item[0] for item in converted}
            if len(kinds) != 1 or not _compatible(target, kinds.pop()):
                raise SemanticError("Filter value type does not match its field")
            kind = converted[0][0]
            if kind == "date" and target.logical_type == "timestamp":
                raise SemanticError("Timestamp members require timestamp values")
            precision = max((item[2] or 0) for item in converted) or None
            scale = max((item[3] or 0) for item in converted) if kind == "decimal" else None
            if kind == "decimal":
                precision = 38
            members: tuple = tuple(item[1] for item in converted)
            slot = self._bind(kind, members, precision, scale, multiple=True)
            return f"({column} {'NOT IN' if op == 'not_in' else 'IN'} ({slot}))"
        if op == "like_literal":
            if target.logical_type != "string":
                raise SemanticError("Text matching requires a text field")
            raw = node.values[0].value
            assert isinstance(raw, str)
            escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return f"({column} LIKE {self._bind('string', f'%{escaped}%', None, None)})"
        if op == "relative_date":
            if target.logical_type not in TEMPORAL or node.relative is None:
                raise SemanticError("Relative dates require a date or timestamp field")
            start, end = relative_bounds(node.relative, self.now)
            if target.logical_type == "date":
                low = self._bind("date", start, None, None)
                high = self._bind("date", end, None, None)
            else:
                zone = ZoneInfo(node.relative.timezone)
                low = self._bind("timestamp", datetime.combine(start, datetime.min.time(), zone),
                                 None, None)
                high = self._bind("timestamp", datetime.combine(end, datetime.min.time(), zone),
                                  None, None)
            return f"({column} >= {low} AND {column} < {high})"
        raise SemanticError("Unsupported filter operator")


def plan_aggregate(
    inner_sql: str,
    fields: tuple[DatasetField, ...],
    request: DatasetQueryRequest,
    *,
    dataset_id: str,
    reserved_parameters: set[str],
    now: datetime | None = None,
) -> AggregatePlan:
    by_id = {item.field_id: item for item in fields}
    select: list[str] = []
    columns: list[QueryColumn] = []
    outputs: list[DatasetField] = []
    dimension_sql: list[str] = []
    for index, dimension in enumerate(request.dimensions):
        source = by_id.get(dimension.field_id)
        if source is None:
            raise SemanticError("Query references an unknown field")
        key = f"d{index}"
        dimension_sql.append(_dimension_sql(source, dimension.date_grain))
        select.append(f"{dimension_sql[-1]} AS `{key}`")
        columns.append(QueryColumn(
            key=key, name=source.name, logical_type=source.logical_type, role="dimension",
            field_id=source.field_id, date_grain=dimension.date_grain, timezone=source.timezone,
        ))
        outputs.append(DatasetField(
            field_id=key, name=key, source_column=key, logical_type=source.logical_type,
            physical_type=source.physical_type, semantic_role="dimension", nullable=True,
            timezone=source.timezone,
        ))
    for index, measure in enumerate(request.measures):
        source = None
        if measure.field_id is not None:
            source = by_id.get(measure.field_id)
            if source is None:
                raise SemanticError("Query references an unknown field")
        expression, logical = _measure_sql(source, measure.aggregation)
        key = f"m{index}"
        select.append(f"{expression} AS `{key}`")
        label = "Rows" if source is None else source.name
        columns.append(QueryColumn(
            key=key, name=label, logical_type=logical, role="measure",  # type: ignore[arg-type]
            field_id=source.field_id if source else None, aggregation=measure.aggregation,
            timezone=source.timezone if source and logical == "timestamp" else None,
        ))
        outputs.append(DatasetField(
            field_id=key, name=key, source_column=key, logical_type=logical,  # type: ignore[arg-type]
            physical_type="DECIMAL(38,9)" if logical == "decimal" else "BIGINT",
            semantic_role="measure", nullable=True,
            timezone=source.timezone if source and logical == "timestamp" else None,
        ))
    plan = AggregatePlan(sql="", columns=tuple(columns), output_fields=tuple(outputs))
    where = ""
    if request.filters is not None:
        compiler = _Filters(plan, by_id, dataset_id, request.revision, set(reserved_parameters),
                            now or datetime.now(UTC))
        where = f"\nWHERE {compiler.compile(request.filters.expression)}"
    group = ""
    if request.dimensions:
        group = "\nGROUP BY " + ",".join(dimension_sql)
    order_keys = [f"`{item.column}` {item.direction.upper()}" for item in request.sort]
    explicit = {item.column for item in request.sort}
    order_keys += [f"`d{i}` ASC" for i in range(len(request.dimensions)) if f"d{i}" not in explicit]
    order = "\nORDER BY " + ",".join(order_keys) if order_keys else ""
    plan.sql = (
        f"SELECT {','.join(select)}\nFROM ({inner_sql}\n) AS `nova_dataset`"
        f"{where}{group}{order}\nLIMIT {request.limit + 1}"
        + (f" OFFSET {request.offset}" if request.offset else "")
    )
    return plan
