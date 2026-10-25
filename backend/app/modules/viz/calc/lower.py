"""Lower checked formulas to StarRocks SQL.

The lowerer turns an expanded syntax tree (calculated fields already
inlined) into one SQL expression. Where an expression's value lives depends
on the query layer, so the compiler supplies callbacks:

* ``column(name)`` — a source column in the current relation;
* ``lod(node)`` — the joined column holding a level-of-detail value;
* ``aggregate(node)`` — in post-aggregation layers, the column holding an
  aggregate already computed (``None`` lowers the aggregate inline);
* ``table(node)`` — the column holding a table calculation.

Every function maps onto a StarRocks built-in checked against the pinned
engine; literals are validated or escaped and identifiers are quoted by the
compiler's callbacks, never interpolated raw.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from app.modules.viz.calc.analyzer import COUNT_ROWS, Resolver, Typed, _Checker, constant_int
from app.modules.viz.calc.syntax import (
    Binary,
    CalcError,
    Call,
    Case,
    FieldRef,
    If,
    InList,
    Literal_,
    Lod,
    Node,
    Unary,
)

_NUMBER = re.compile(r"^(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")

_DATEPART_SQL = {
    "year": "YEAR",
    "quarter": "QUARTER",
    "month": "MONTH",
    "week": "WEEK",
    "weekday": "DAYOFWEEK",
    "day": "DAY",
    "dayofyear": "DAYOFYEAR",
    "hour": "HOUR",
    "minute": "MINUTE",
    "second": "SECOND",
}

_SIMPLE_ROW = {
    "ABS": "ABS",
    "CEILING": "CEIL",
    "FLOOR": "FLOOR",
    "POWER": "POWER",
    "SQRT": "SQRT",
    "EXP": "EXP",
    "LN": "LN",
    "SIGN": "SIGN",
    "LEN": "CHAR_LENGTH",
    "LEFT": "LEFT",
    "RIGHT": "RIGHT",
    "MID": "SUBSTR",
    "UPPER": "UPPER",
    "LOWER": "LOWER",
    "TRIM": "TRIM",
    "LTRIM": "LTRIM",
    "RTRIM": "RTRIM",
    "STARTSWITH": "STARTS_WITH",
    "ENDSWITH": "ENDS_WITH",
    "REPLACE": "REPLACE",
    "REGEXP_MATCH": "REGEXP",
    "REGEXP_REPLACE": "REGEXP_REPLACE",
    "IFNULL": "IFNULL",
    "YEAR": "YEAR",
    "QUARTER": "QUARTER",
    "MONTH": "MONTH",
    "WEEK": "WEEK",
    "DAY": "DAY",
}

_SIMPLE_AGGREGATE = {
    "SUM": "SUM",
    "AVG": "AVG",
    "MIN": "MIN",
    "MAX": "MAX",
    "COUNT": "COUNT",
    "STDEV": "STDDEV_SAMP",
    "STDEVP": "STDDEV_POP",
    "VAR": "VAR_SAMP",
    "VARP": "VAR_POP",
}

_RUNNING = {
    "RUNNING_SUM": "SUM",
    "RUNNING_AVG": "AVG",
    "RUNNING_MIN": "MIN",
    "RUNNING_MAX": "MAX",
    "RUNNING_COUNT": "COUNT",
}

_WINDOW = {
    "WINDOW_SUM": "SUM",
    "WINDOW_AVG": "AVG",
    "WINDOW_MIN": "MIN",
    "WINDOW_MAX": "MAX",
    "WINDOW_COUNT": "COUNT",
}

_RANKS = {
    "RANK": ("RANK", "desc"),
    "RANK_DENSE": ("DENSE_RANK", "desc"),
    "RANK_UNIQUE": ("ROW_NUMBER", "desc"),
    "RANK_PERCENTILE": ("PERCENT_RANK", "asc"),
}

TABLE_FUNCTIONS = frozenset(
    {*_RUNNING, *_WINDOW, *_RANKS, "LOOKUP", "TOTAL", "INDEX", "FIRST", "LAST", "SIZE"}
)


def quote_literal(value: str) -> str:
    if "\x00" in value:
        raise CalcError("Text cannot contain null bytes.")
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def infer_types(node: Node, columns: Mapping[str, str]) -> dict[Node, Typed]:
    """Types and levels of every sub-expression of an expanded tree."""
    checker = _Checker(Resolver(dict(columns), {}))  # type: ignore[arg-type]
    checker.check(node)
    return checker.types


class Lowerer:
    def __init__(
        self,
        *,
        types: dict[Node, Typed],
        column: Callable[[str], str],
        lod: Callable[[Lod], str] | None = None,
        aggregate: Callable[[Call], str | None] | None = None,
        table: Callable[[Call], str] | None = None,
    ) -> None:
        self.types = types
        self.column = column
        self.lod_ref = lod
        self.aggregate_ref = aggregate
        self.table_ref = table

    def type_of(self, node: Node) -> str:
        typed = self.types.get(node)
        return typed.data_type if typed is not None else "unknown"

    def sql(self, node: Node) -> str:
        if isinstance(node, Literal_):
            return self._literal(node)
        if isinstance(node, FieldRef):
            return self.column(node.name)
        if isinstance(node, Lod):
            if self.lod_ref is None:
                raise CalcError("A level of detail expression is not available here.")
            return self.lod_ref(node)
        if isinstance(node, Unary):
            operand = self.sql(node.operand)
            return f"(NOT {operand})" if node.op == "NOT" else f"(-{operand})"
        if isinstance(node, Binary):
            return self._binary(node)
        if isinstance(node, InList):
            options = ", ".join(self.sql(option) for option in node.options)
            return f"({self.sql(node.subject)} IN ({options}))"
        if isinstance(node, If):
            parts = [f"WHEN {self.sql(c)} THEN {self.sql(v)}" for c, v in node.branches]
            if node.otherwise is not None:
                parts.append(f"ELSE {self.sql(node.otherwise)}")
            return f"(CASE {' '.join(parts)} END)"
        if isinstance(node, Case):
            parts = [f"WHEN {self.sql(m)} THEN {self.sql(v)}" for m, v in node.whens]
            if node.otherwise is not None:
                parts.append(f"ELSE {self.sql(node.otherwise)}")
            return f"(CASE {self.sql(node.subject)} {' '.join(parts)} END)"
        if isinstance(node, Call):
            return self._call(node)
        raise CalcError("This expression cannot be translated to SQL.", node.start, node.end)

    @staticmethod
    def _literal(node: Literal_) -> str:
        if node.kind == "number":
            if not _NUMBER.match(node.value):
                raise CalcError("Invalid number.", node.start, node.end)
            return node.value
        if node.kind == "string":
            return quote_literal(node.value)
        if node.kind == "date":
            return f"CAST({quote_literal(node.value)} AS DATE)"
        if node.kind == "datetime":
            return f"CAST({quote_literal(node.value)} AS DATETIME)"
        if node.kind == "boolean":
            return "TRUE" if node.value == "TRUE" else "FALSE"
        return "NULL"

    def _binary(self, node: Binary) -> str:
        left = self.sql(node.left)
        right = self.sql(node.right)
        lt, rt = self.type_of(node.left), self.type_of(node.right)
        temporal = {"date", "datetime"}
        if node.op in {"AND", "OR", "=", "<>", "<", "<=", ">", ">="}:
            return f"({left} {node.op} {right})"
        if node.op == "+":
            if lt == "string" or rt == "string":
                return f"CONCAT({left}, {right})"
            if lt in temporal:
                return f"TIMESTAMPADD(DAY, {right}, {left})"
            if rt in temporal:
                return f"TIMESTAMPADD(DAY, {left}, {right})"
        if node.op == "-":
            if lt in temporal and rt in temporal:
                return f"DATEDIFF({left}, {right})"
            if lt in temporal:
                return f"TIMESTAMPADD(DAY, -({right}), {left})"
        if node.op == "^":
            return f"POWER({left}, {right})"
        return f"({left} {node.op} {right})"

    def _call(self, node: Call) -> str:
        name = node.name
        if name == COUNT_ROWS:
            return self._aggregate_or_inline(node, lambda: "COUNT(*)")
        if name in TABLE_FUNCTIONS:
            if self.table_ref is None:
                raise CalcError(
                    f"{name} is a table calculation and is only available on a view.",
                    node.start,
                    node.end,
                )
            return self.table_ref(node)
        if name in {"MIN", "MAX"} and len(node.args) == 2:
            fn = "LEAST" if name == "MIN" else "GREATEST"
            return f"{fn}({self.sql(node.args[0])}, {self.sql(node.args[1])})"
        if name in _SIMPLE_AGGREGATE or name in {"COUNTD", "MEDIAN", "PERCENTILE", "ATTR"}:
            return self._aggregate_or_inline(node, lambda: self.aggregate_sql(node))
        return self._row_call(node)

    def _aggregate_or_inline(self, node: Call, inline: Callable[[], str]) -> str:
        if self.aggregate_ref is not None:
            ref = self.aggregate_ref(node)
            if ref is not None:
                return ref
        return inline()

    def aggregate_sql(self, node: Call) -> str:
        """The aggregate itself (never a hoisted reference)."""
        name = node.name
        if name == COUNT_ROWS:
            return "COUNT(*)"
        args = [self.sql(arg) for arg in node.args]
        if name in _SIMPLE_AGGREGATE:
            return f"{_SIMPLE_AGGREGATE[name]}({args[0]})"
        if name == "COUNTD":
            return f"COUNT(DISTINCT {args[0]})"
        if name == "MEDIAN":
            return f"PERCENTILE_CONT(CAST({args[0]} AS DOUBLE), 0.5)"
        if name == "PERCENTILE":
            fraction = node.args[1]
            if not (isinstance(fraction, Literal_) and fraction.kind == "number"):
                raise CalcError(
                    "The percentile must be a number between 0 and 1.", fraction.start, fraction.end
                )
            if not 0 <= float(fraction.value) <= 1:
                raise CalcError(
                    "The percentile must be between 0 and 1.", fraction.start, fraction.end
                )
            return f"PERCENTILE_CONT(CAST({args[0]} AS DOUBLE), {fraction.value})"
        if name == "ATTR":
            return f"(CASE WHEN MIN({args[0]}) = MAX({args[0]}) THEN MIN({args[0]}) END)"
        raise CalcError(f"{name} is not an aggregate.", node.start, node.end)

    def _row_call(self, node: Call) -> str:
        name = node.name
        args = [self.sql(arg) for arg in node.args]
        if name in _SIMPLE_ROW:
            return f"{_SIMPLE_ROW[name]}({', '.join(args)})"
        if name == "IIF":
            otherwise = args[3] if len(args) > 3 else "NULL"
            return (
                f"(CASE WHEN {args[0]} THEN {args[1]} WHEN NOT ({args[0]}) THEN {args[2]} "
                f"ELSE {otherwise} END)"
            )
        if name == "ZN":
            return f"COALESCE({args[0]}, 0)"
        if name == "ISNULL":
            return f"({args[0]} IS NULL)"
        if name == "ROUND":
            return f"ROUND({args[0]}, {args[1] if len(args) > 1 else 0})"
        if name == "SQUARE":
            return f"POWER({args[0]}, 2)"
        if name == "LOG":
            if len(args) == 1:
                return f"LOG10({args[0]})"
            return f"(LN({args[0]}) / LN({args[1]}))"
        if name == "DIV":
            return f"({args[0]} DIV {args[1]})"
        if name == "PI":
            return "PI()"
        if name == "CONTAINS":
            return f"(LOCATE({args[1]}, {args[0]}) > 0)"
        if name == "FIND":
            start = f", {args[2]}" if len(args) > 2 else ""
            return f"LOCATE({args[1]}, {args[0]}{start})"
        if name == "SPLIT":
            index = constant_int(node.args[2])
            if index is None or index < 1:
                raise CalcError("SPLIT counts pieces from 1.", node.args[2].start, node.args[2].end)
            return f"SPLIT_PART({args[0]}, {args[1]}, {index})"
        if name == "REGEXP_EXTRACT":
            return f"REGEXP_EXTRACT({args[0]}, {args[1]}, 1)"
        if name in {"DATEPART", "DATENAME", "DATETRUNC", "DATEADD", "DATEDIFF"}:
            return self._date_call(node, args)
        if name == "TODAY":
            return "CURRENT_DATE()"
        if name == "NOW":
            return "NOW()"
        if name == "MAKEDATE":
            pieces = ", '-', ".join(f"CAST({arg} AS BIGINT)" for arg in args)
            return f"STR_TO_DATE(CONCAT({pieces}), '%Y-%m-%d')"
        if name == "INT":
            if self.type_of(node.args[0]) == "string":
                return f"CAST(CAST({args[0]} AS DOUBLE) AS BIGINT)"
            return f"CAST({args[0]} AS BIGINT)"
        if name == "FLOAT":
            return f"CAST({args[0]} AS DOUBLE)"
        if name == "STR":
            return f"CAST({args[0]} AS VARCHAR)"
        if name == "DATE":
            return f"CAST({args[0]} AS DATE)"
        if name == "DATETIME":
            return f"CAST({args[0]} AS DATETIME)"
        raise CalcError(f"{name} cannot be translated to SQL.", node.start, node.end)

    @staticmethod
    def _part(node: Call) -> str:
        part = node.args[0]
        assert isinstance(part, Literal_)
        return part.value.lower()

    def _date_call(self, node: Call, args: list[str]) -> str:
        part = self._part(node)
        name = node.name
        if name == "DATEPART":
            return f"{_DATEPART_SQL[part]}({args[1]})"
        if name == "DATENAME":
            if part == "month":
                return f"MONTHNAME({args[1]})"
            if part == "weekday":
                return f"DAYNAME({args[1]})"
            return f"CAST({_DATEPART_SQL[part]}({args[1]}) AS VARCHAR)"
        if name == "DATETRUNC":
            return f"DATE_TRUNC('{part}', {args[1]})"
        if name == "DATEADD":
            return f"TIMESTAMPADD({part.upper()}, {args[1]}, {args[2]})"
        # DATEDIFF counts boundaries crossed: compare the truncated dates.
        start = f"DATE_TRUNC('{part}', {args[1]})"
        end = f"DATE_TRUNC('{part}', {args[2]})"
        if part == "quarter":
            return f"(TIMESTAMPDIFF(MONTH, {start}, {end}) DIV 3)"
        return f"TIMESTAMPDIFF({part.upper()}, {start}, {end})"


def window_sql(
    node: Call,
    args: list[str],
    *,
    partition: list[str],
    order: list[str],
) -> str:
    """One table-calculation function as a window expression.

    ``partition`` restarts the calculation; ``order`` is the addressing
    direction. ``TOTAL`` is lowered by the compiler, which re-aggregates its
    argument's terms.
    """
    name = node.name
    partition_by = f"PARTITION BY {', '.join(partition)}" if partition else ""
    order_by = f"ORDER BY {', '.join(order)}" if order else ""

    def over(*parts: str) -> str:
        return "OVER (" + " ".join(part for part in parts if part) + ")"

    if name in _RUNNING:
        if not order:
            return f"{_RUNNING[name]}({args[0]}) {over(partition_by)}"
        frame = "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
        return f"{_RUNNING[name]}({args[0]}) {over(partition_by, order_by, frame)}"
    if name in _WINDOW:
        fn = _WINDOW[name]
        if len(node.args) == 1:
            return f"{fn}({args[0]}) {over(partition_by)}"
        first = constant_int(node.args[1])
        last = constant_int(node.args[2]) if len(node.args) > 2 else 0
        assert first is not None and last is not None
        if first > last:
            raise CalcError(f"{name} needs first <= last.", node.start, node.end)
        if not order:
            return f"{fn}({args[0]}) {over(partition_by)}"
        frame = f"ROWS BETWEEN {_offset(first)} AND {_offset(last)}"
        return f"{fn}({args[0]}) {over(partition_by, order_by, frame)}"
    if name == "LOOKUP":
        offset = constant_int(node.args[1])
        assert offset is not None
        if offset == 0:
            return args[0]
        if not order:
            return "NULL"
        fn = "LAG" if offset < 0 else "LEAD"
        return f"{fn}({args[0]}, {abs(offset)}) {over(partition_by, order_by)}"
    if name == "INDEX":
        return f"ROW_NUMBER() {over(partition_by, order_by)}"
    if name == "FIRST":
        return f"(1 - ROW_NUMBER() {over(partition_by, order_by)})"
    if name == "LAST":
        return f"(COUNT(*) {over(partition_by)} - ROW_NUMBER() {over(partition_by, order_by)})"
    if name == "SIZE":
        return f"COUNT(*) {over(partition_by)}"
    if name in _RANKS:
        fn, default = _RANKS[name]
        direction = default
        if len(node.args) > 1:
            literal = node.args[1]
            assert isinstance(literal, Literal_)
            direction = literal.value.lower()
        return f"{fn}() {over(partition_by, f'ORDER BY {args[0]} {direction.upper()}')}"
    raise CalcError(f"{name} cannot be translated to SQL.", node.start, node.end)


def _offset(value: int) -> str:
    if value == 0:
        return "CURRENT ROW"
    if value < 0:
        return f"{-value} PRECEDING"
    return f"{value} FOLLOWING"
