"""Compile a worksheet specification into one StarRocks SELECT.

The semantic query built from the shelves, marks card, filters and
calculated fields becomes SQL for the engine. A view runs in this order,
each step a layer of the query:

1. the source (a read-only statement or a table), as a subquery;
2. context filters;
3. FIXED level-of-detail values, joined back to each row;
4. dimension (row-level) filters;
5. INCLUDE/EXCLUDE level-of-detail values, joined back to each row;
6. aggregation to the view's dimensions (its level of detail);
7. measure filters on aggregated values;
8. table calculations, as window functions over the aggregated marks.

A plain view (no LODs, measure filters or table calculations) compiles to a
single ``SELECT ... GROUP BY`` so the SQL tab stays readable.

An aggregate whose argument is purely level-of-detail values (``SUM({FIXED
[Region] : SUM([Sales])})``) aggregates each LOD group once — values coarser
than the view repeat per mark, finer ones roll up — instead of once per
source row.

Every identifier is backtick-quoted and validated; literal values are
validated or escaped, because field names, formulas and filter members are
user data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.common.sql_guard import split_sql_statements
from app.modules.viz.calc.analyzer import (
    COUNT_ROWS,
    RESERVED_PREFIX,
    CalcInfo,
    DataType,
    Resolver,
    Typed,
    analyze_calculations,
    walk,
)
from app.modules.viz.calc.functions import FUNCTIONS
from app.modules.viz.calc.lower import TABLE_FUNCTIONS, Lowerer, infer_types, window_sql
from app.modules.viz.calc.syntax import (
    Binary,
    CalcError,
    Call,
    Literal_,
    Lod,
    Node,
    children,
    parse,
)
from app.modules.viz.calc.syntax import (
    FieldRef as CalcField,
)
from app.modules.viz.spec import (
    Aggregation,
    FieldRef,
    FieldType,
    Filter,
    RelativeDateFilter,
    TableCalc,
    WorksheetSpec,
)

SOURCE_CTE = "nova_source"

#: Pill aggregations as calculation functions.
AGGREGATION_FUNCTIONS: dict[str, str] = {
    "sum": "SUM",
    "avg": "AVG",
    "median": "MEDIAN",
    "min": "MIN",
    "max": "MAX",
    "count": "COUNT",
    "distinct_count": "COUNTD",
    "stdev": "STDEV",
    "stdevp": "STDEVP",
    "var": "VAR",
    "varp": "VARP",
    "attr": "ATTR",
}

_DATE_LIKE = re.compile(r"^\d{4}-\d{2}(-\d{2})([ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?)?$|^\d{8}$")
_NUMBER_LIKE = re.compile(r"^-?\d+(\.\d+)?$")

#: Source rows sampled to learn column types (and to seed the data pane).
PROBE_ROWS = 100


class CompileError(ValueError):
    """The worksheet cannot be compiled safely."""


def quote_ident(name: str) -> str:
    """Backtick-quote a StarRocks identifier, rejecting anything unquoteable."""
    if not name or "\x00" in name:
        raise CompileError(f"Field name cannot be used in SQL: {name!r}")
    return f"`{name.replace('`', '``')}`"


def quote_literal(value: str) -> str:
    """Single-quote a string literal.

    StarRocks treats backslash as an escape inside literals, so backslashes
    are doubled before quotes are: ``a\\' OR 1=1`` stays one literal.
    """
    if "\x00" in value:
        raise CompileError("Filter values cannot contain null bytes.")
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _literal(value: float | str) -> str:
    if isinstance(value, bool):
        raise CompileError("A boolean is not a quantitative filter bound.")
    if isinstance(value, (int, float)):
        return str(value)
    text = value.strip()
    if _NUMBER_LIKE.match(text):
        return text
    if _DATE_LIKE.match(text):
        return quote_literal(text)
    raise CompileError(f"Filter bound must be a number or date: {value!r}")


def infer_column_types(columns: list[str], rows: list[list[Any]]) -> dict[str, DataType]:
    """Data types of source columns from sampled values (first non-null wins)."""
    types: dict[str, DataType] = {}
    for index, column in enumerate(columns):
        detected: DataType = "unknown"
        for row in rows:
            value = row[index] if index < len(row) else None
            if value is None:
                continue
            if isinstance(value, bool):
                detected = "boolean"
            elif isinstance(value, (int, float, Decimal)):
                detected = "number"
            elif isinstance(value, datetime):
                detected = "datetime"
            elif isinstance(value, date):
                detected = "date"
            else:
                detected = "string"
            break
        types[column] = detected
    return types


def field_type_for(data_type: str) -> FieldType:
    if data_type == "number":
        return "quantitative"
    if data_type in {"date", "datetime"}:
        return "temporal"
    return "nominal"


@dataclass
class CompiledQuery:
    """The compiled statement plus the output fields it promises.

    ``spec`` is the worksheet as resolved: pills over aggregate calculations
    carry ``aggregation="agg"`` and every pill carries its real field type.
    ``ordered`` is true when the SQL order is meaningful to the view (table
    calculations run along it), so renderers must not re-sort.
    """

    sql: str
    output_fields: list[FieldRef] = field(default_factory=list)
    spec: WorksheetSpec | None = None
    ordered: bool = False
    calculations: dict[str, CalcInfo] = field(default_factory=dict)


def source_expression(spec: WorksheetSpec) -> str:
    """The source as a subquery expression, validated to be one statement."""
    source = spec.source
    if source.kind == "table":
        return f"SELECT * FROM {quote_ident(source.database)}.{quote_ident(source.table)}"
    statements = split_sql_statements(source.sql)
    if len(statements) != 1:
        raise CompileError("A viz source must be exactly one SQL statement.")
    return f"({source.sql.strip()})"


def source_probe_sql(spec: WorksheetSpec, *, rows: int = 1) -> str:
    """A small probe used to learn the source's columns and types."""
    return f"SELECT * FROM {source_expression(spec)} AS {SOURCE_CTE} LIMIT {int(rows)}"


def validate_fields(spec: WorksheetSpec, known_columns: list[str]) -> None:
    """Reject a worksheet whose pills, filters, sorts or formulas name fields
    the source does not have."""
    compile_worksheet(spec, known_columns=known_columns)


def compile_worksheet(
    spec: WorksheetSpec,
    *,
    known_columns: list[str] | None = None,
    column_types: dict[str, DataType] | None = None,
) -> CompiledQuery:
    """Compile the worksheet to one StarRocks SELECT.

    ``known_columns``, when given, pins every referenced field to a real
    source column or calculated field; without it fields are still
    identifier-quoted, never raw. ``column_types`` (from a probe) lets
    formulas type-check against real data types.
    """
    try:
        return _Planner(spec, known_columns, column_types or {}).compile()
    except CalcError as exc:
        raise CompileError(exc.message) from exc


# ── Planning ───────────────────────────────────────────────────


@dataclass
class _Dim:
    pill: FieldRef
    node: Node
    alias: str


@dataclass
class _Measure:
    pill: FieldRef
    node: Node
    level: str
    addressing: list[str] | None = None


@dataclass
class _LodPlan:
    node: Lod
    index: int
    stage: str  # "fixed" or "view"
    keys: list[Node]


@dataclass
class _TablePlan:
    node: Call
    addressing: tuple[str, ...] | None
    index: int
    depth: int


def _is_aggregate_call(node: Node) -> bool:
    if not isinstance(node, Call):
        return False
    if node.name == COUNT_ROWS:
        return True
    spec = FUNCTIONS.get(node.name)
    if spec is None or spec.kind != "aggregate":
        return False
    return not (node.name in {"MIN", "MAX"} and len(node.args) == 2)


def _walk_outside_lod(node: Node):
    """Pre-order traversal that does not enter LOD bodies or dimensions."""
    yield node
    if isinstance(node, Lod):
        return
    for child in children(node):
        yield from _walk_outside_lod(child)


def _post_order(node: Node):
    for child in children(node):
        yield from _post_order(child)
    yield node


class _Planner:
    def __init__(
        self,
        spec: WorksheetSpec,
        known_columns: list[str] | None,
        column_types: dict[str, DataType],
    ) -> None:
        self.spec = spec
        self.known = known_columns is not None
        formulas = spec.calculation_formulas()
        if known_columns is not None:
            columns = {name: column_types.get(name, "unknown") for name in known_columns}
        else:
            columns = {name: "unknown" for name in self._referenced_names(formulas)}
        for name in columns:
            if name.startswith(RESERVED_PREFIX):
                raise CompileError(f"Column names cannot start with {RESERVED_PREFIX}.")
        self.columns: dict[str, DataType] = columns  # type: ignore[assignment]
        try:
            self.calcs = analyze_calculations(self.columns, formulas)
        except CalcError as exc:
            raise CompileError(exc.message) from exc
        self.resolver = Resolver(self.columns, formulas)
        self.resolver.done.update(self.calcs)
        self.types: dict[Node, Typed] = {}

    def _referenced_names(self, formulas: dict[str, str]) -> set[str]:
        names: set[str] = set()
        for pill in self.spec.all_fields():
            if pill.field != "*":
                names.add(pill.field)
        names.update(item.field for item in self.spec.filters)
        for formula in formulas.values():
            try:
                tree = parse(formula)
            except CalcError:
                continue
            names.update(node.name for node in walk(tree) if isinstance(node, CalcField))
        return {name for name in names if name not in formulas}

    # Field resolution

    def _field_node(self, name: str, what: str) -> tuple[Node, CalcInfo | None]:
        canonical = self.resolver.canonical(name)
        if canonical is None:
            raise CompileError(f"{what} {name!r} is not a column of this data source.")
        node = self.resolver.expand(CalcField(canonical))
        return node, self.calcs.get(canonical)

    def _typed(self, node: Node) -> Typed:
        if node not in self.types:
            self.types.update(infer_types(node, self.columns))
        return self.types[node]

    def _resolve_dimension(self, pill: FieldRef) -> tuple[FieldRef, Node]:
        node, info = self._field_node(pill.field, "Field")
        if info is not None and info.level in {"aggregate", "table"}:
            raise CompileError(
                f"[{pill.field}] is an aggregate calculation, so it can only be used as a measure."
            )
        if any(
            isinstance(item, Lod) and item.kind in {"include", "exclude"} for item in walk(node)
        ):
            raise CompileError(
                f"[{pill.field}] depends on the view's dimensions (INCLUDE/EXCLUDE), so it can "
                "only be used as a measure."
            )
        data_type = self._typed(node).data_type
        if pill.date_part is not None:
            if data_type not in {"date", "datetime", "string", "unknown"}:
                raise CompileError(f"[{pill.field}] is not a date, so it has no date levels.")
            function = "DATEPART" if pill.discrete else "DATETRUNC"
            node = Call(function, (Literal_("string", pill.date_part), node))
            self._typed(node)
            field_type: FieldType = "ordinal" if pill.discrete else "temporal"
        else:
            field_type = field_type_for(data_type)
            if pill.field_type == "ordinal" and field_type != "temporal":
                field_type = "ordinal"
        resolved = pill.model_copy(update={"field_type": field_type})
        return resolved, node

    def _resolve_measure(self, pill: FieldRef) -> tuple[FieldRef, Node, str, list[str] | None]:
        if pill.field == "*":
            if pill.aggregation != "count":
                raise CompileError("Only COUNT can be applied to all records.")
            return pill, Call(COUNT_ROWS, ()), "aggregate", None
        node, info = self._field_node(pill.field, "Field")
        if info is not None and info.level in {"aggregate", "table"}:
            resolved = pill.model_copy(
                update={"aggregation": "agg", "field_type": "quantitative", "discrete": False}
            )
            calc = self.spec.calculation(info.name)
            return resolved, node, info.level, (calc.addressing if calc else None)
        if pill.aggregation == "agg":
            raise CompileError(f"[{pill.field}] is not an aggregate; choose an aggregation for it.")
        function = AGGREGATION_FUNCTIONS[pill.aggregation or "sum"]
        data_type = self._typed(node).data_type
        if function in {
            "SUM",
            "AVG",
            "MEDIAN",
            "STDEV",
            "STDEVP",
            "VAR",
            "VARP",
        } and data_type not in {
            "number",
            "unknown",
        }:
            raise CompileError(
                f"{function} needs a number, but [{pill.field}] is {data_type}; use COUNT, "
                "COUNTD, MIN, MAX or ATTR."
            )
        aggregate = Call(function, (node,))
        self._typed(aggregate)
        result_type = self._typed(aggregate).data_type
        field_type = (
            "quantitative" if result_type in {"number", "unknown"} else field_type_for(result_type)
        )
        resolved = pill.model_copy(update={"field_type": field_type, "discrete": False})
        return resolved, aggregate, "aggregate", None

    # Compile

    def compile(self) -> CompiledQuery:
        spec = self.spec
        resolved: dict[int, FieldRef] = {}

        dims: list[_Dim] = []
        seen_dims: set[str] = set()
        for pill in spec.all_fields():
            if pill.is_measure:
                continue
            new_pill, node = self._resolve_dimension(pill)
            resolved[id(pill)] = new_pill
            if new_pill.label in seen_dims:
                continue
            seen_dims.add(new_pill.label)
            dims.append(_Dim(new_pill, node, f"__nova_d{len(dims)}"))

        measures: list[_Measure] = []
        seen_measures: set[str] = set()
        for pill in spec.all_fields():
            if not pill.is_measure:
                continue
            new_pill, node, level, addressing = self._resolve_measure(pill)
            resolved[id(pill)] = new_pill
            if new_pill.label in seen_measures:
                continue
            seen_measures.add(new_pill.label)
            measures.append(_Measure(new_pill, node, level, addressing))

        row_level = not dims and spec.marks.type in {"circle", "square"}
        if not measures and spec.marks.type not in {"bar", "text"}:
            # A count of rows is still renderable for bar/text views.
            count = FieldRef(
                field="*", field_type="quantitative", aggregation="count", discrete=False
            )
            measures.append(_Measure(count, Call(COUNT_ROWS, ()), "aggregate"))

        for calc in spec.table_calcs:
            measures.append(self._quick_table_calc(calc, measures))

        context_filters, row_filters, measure_filters = self._resolve_filters()
        resolved_spec = self._resolved_spec(resolved)

        lods = self._plan_lods(dims, measures, row_filters, measure_filters)
        tables = self._plan_tables(measures)
        output_fields = [dim.pill for dim in dims] + [measure.pill for measure in measures]

        simple = not lods and not tables and not measure_filters
        if row_level and simple and all(self._row_level_measure(m) for m in measures):
            sql = self._compile_row_level(dims, measures, context_filters + row_filters)
        elif simple:
            sql = self._compile_simple(dims, measures, context_filters + row_filters)
        else:
            sql = self._compile_layered(
                dims, measures, lods, tables, context_filters, row_filters, measure_filters
            )
        return CompiledQuery(
            sql=sql,
            output_fields=output_fields,
            spec=resolved_spec,
            ordered=bool(tables),
            calculations=self.calcs,
        )

    def _row_level_measure(self, measure: _Measure) -> bool:
        node = measure.node
        return (
            isinstance(node, Call)
            and node.name in {"SUM", "AVG", "MIN", "MAX", "MEDIAN", "ATTR"}
            and not any(isinstance(item, Lod) for item in walk(node))
        )

    def _resolved_spec(self, resolved: dict[int, FieldRef]) -> WorksheetSpec:
        spec = self.spec

        def pick(pill: FieldRef | None) -> FieldRef | None:
            return None if pill is None else resolved.get(id(pill), pill)

        marks = spec.marks.model_copy(
            update={
                "color": pick(spec.marks.color),
                "size": pick(spec.marks.size),
                "text": pick(spec.marks.text),
                "detail": pick(spec.marks.detail),
                "angle": pick(spec.marks.angle),
            }
        )
        return spec.model_copy(
            update={
                "columns": [pick(p) for p in spec.columns],
                "rows": [pick(p) for p in spec.rows],
                "marks": marks,
            }
        )

    def _quick_table_calc(self, calc: TableCalc, measures: list[_Measure]) -> _Measure:
        source = next(
            (m for m in measures if m.pill.field == calc.field or m.pill.label == calc.field),
            None,
        )
        if source is None:
            raise CompileError(
                f"Table calculation field {calc.field!r} is not a measure of this view."
            )
        if source.level == "table":
            raise CompileError("A quick table calculation needs an aggregate measure.")
        m = source.node
        previous = Call("LOOKUP", (m, Literal_("number", "-1")))
        if calc.kind == "running_total":
            node: Node = Call("RUNNING_SUM", (m,))
        elif calc.kind == "percent_of_total":
            node = Binary(
                "/", Binary("*", Literal_("number", "100.0"), m), Call("WINDOW_SUM", (m,))
            )
        elif calc.kind == "difference":
            node = Call("IFNULL", (Binary("-", m, previous), m))
        elif calc.kind == "percent_difference":
            node = Binary(
                "*",
                Call("ZN", (Binary("/", Binary("-", m, previous), previous),)),
                Literal_("number", "100.0"),
            )
        else:
            node = Call("RANK", (m,))
        self._typed(node)
        pill = FieldRef(
            field=f"{calc.kind}({source.pill.label})",
            field_type="quantitative",
            aggregation="agg",
            discrete=False,
        )
        return _Measure(pill, node, "table")

    def _resolve_filters(
        self,
    ) -> tuple[list[tuple[Filter, Node]], list[tuple[Filter, Node]], list[tuple[Filter, Node]]]:
        context: list[tuple[Filter, Node]] = []
        rows: list[tuple[Filter, Node]] = []
        measures: list[tuple[Filter, Node]] = []
        for item in self.spec.filters:
            node, info = self._field_node(item.field, "Filter field")
            level = info.level if info is not None else "row"
            if level == "table":
                raise CompileError(
                    f"Filtering on the table calculation [{item.field}] is not supported."
                )
            aggregation: Aggregation | None = getattr(item, "aggregation", None)
            if level == "aggregate" or aggregation:
                if item.kind != "quantitative":
                    raise CompileError(f"[{item.field}] is aggregated, so it needs a range filter.")
                if level != "aggregate":
                    if aggregation == "agg":
                        raise CompileError(
                            f"[{item.field}] is not an aggregate; choose an aggregation."
                        )
                    assert aggregation is not None
                    node = Call(AGGREGATION_FUNCTIONS[aggregation], (node,))
                if item.context:
                    raise CompileError("A measure filter cannot be a context filter.")
                self._typed(node)
                measures.append((item, node))
                continue
            has_lod = any(isinstance(n, Lod) for n in walk(node))
            if item.context and has_lod:
                raise CompileError(
                    f"[{item.field}] uses a level of detail expression, so it cannot be a "
                    "context filter."
                )
            if any(isinstance(n, Lod) and n.kind in {"include", "exclude"} for n in walk(node)):
                raise CompileError(
                    f"[{item.field}] depends on the view (INCLUDE/EXCLUDE); filter it as a measure."
                )
            self._typed(node)
            (context if item.context else rows).append((item, node))
        return context, rows, measures

    def _plan_lods(
        self,
        dims: list[_Dim],
        measures: list[_Measure],
        row_filters: list[tuple[Filter, Node]],
        measure_filters: list[tuple[Filter, Node]],
    ) -> list[_LodPlan]:
        roots = [d.node for d in dims] + [m.node for m in measures]
        roots += [node for _, node in row_filters] + [node for _, node in measure_filters]
        view_keys = [d.node for d in dims]
        plans: dict[Lod, _LodPlan] = {}
        for root in roots:
            for node in _post_order(root):
                if not isinstance(node, Lod) or node in plans:
                    continue
                if node.kind in {"fixed", "table"}:
                    stage, keys = "fixed", list(node.dims)
                elif node.kind == "include":
                    stage = "view"
                    keys = view_keys + [d for d in node.dims if d not in view_keys]
                else:
                    stage = "view"
                    keys = [k for k in view_keys if not self._excluded(k, node.dims)]
                plans[node] = _LodPlan(node, len(plans), stage, keys)
        # FIXED values are computed before any view-dependent value.
        ordered = [p for p in plans.values() if p.stage == "fixed"]
        ordered += [p for p in plans.values() if p.stage == "view"]
        return ordered

    @staticmethod
    def _excluded(key: Node, excluded: tuple[Node, ...]) -> bool:
        if key in excluded:
            return True
        # EXCLUDE [Order Date] also drops YEAR([Order Date]) and friends.
        return (
            isinstance(key, Call)
            and key.name in {"DATEPART", "DATETRUNC"}
            and len(key.args) == 2
            and key.args[1] in excluded
        )

    def _plan_tables(self, measures: list[_Measure]) -> list[_TablePlan]:
        plans: dict[tuple[Call, tuple[str, ...] | None], _TablePlan] = {}
        for measure in measures:
            if measure.level != "table":
                continue
            addressing = tuple(measure.addressing) if measure.addressing is not None else None
            for node in _post_order(measure.node):
                if not (isinstance(node, Call) and node.name in TABLE_FUNCTIONS):
                    continue
                key = (node, addressing)
                if key in plans:
                    continue
                depth = 1 + max(
                    (
                        plans[(inner, addressing)].depth
                        for inner in _walk_outside_lod(node)
                        if inner is not node
                        and isinstance(inner, Call)
                        and inner.name in TABLE_FUNCTIONS
                        and (inner, addressing) in plans
                    ),
                    default=0,
                )
                plans[key] = _TablePlan(node, addressing, len(plans), depth)
        return list(plans.values())

    # SQL generation

    def _row_lowerer(self, lod_columns: dict[Lod, str] | None = None) -> Lowerer:
        lods = lod_columns or {}

        def lod(node: Lod) -> str:
            if node not in lods:
                raise CompileError("A level of detail value is not available at this step.")
            return quote_ident(lods[node])

        return Lowerer(types=self.types, column=quote_ident, lod=lod)

    def _filter_sql(self, item: Filter, expression: str) -> list[str]:
        if item.kind == "quantitative":
            clauses = []
            if item.min is not None:
                clauses.append(f"{expression} >= {_literal(item.min)}")
            if item.max is not None:
                clauses.append(f"{expression} <= {_literal(item.max)}")
            if item.min is None and item.max is None:
                raise CompileError("A quantitative filter needs a min or a max.")
            return clauses
        if item.kind == "categorical":
            if not item.values:
                return [f"{expression} IS NULL" if not item.exclude else "1 = 1"]
            members = ", ".join(quote_literal(value) for value in item.values)
            null_part = f" OR {expression} IS NULL" if item.null_included else ""
            if item.exclude:
                return [f"NOT ({expression} IN ({members}){null_part})"]
            return [f"({expression} IN ({members}){null_part})"]
        return [_relative_date_clause(item, expression)]

    def _where(self, filters: list[tuple[Filter, Node]], lowerer: Lowerer) -> str:
        clauses: list[str] = []
        for item, node in filters:
            clauses.extend(self._filter_sql(item, lowerer.sql(node)))
        return " AND ".join(clauses)

    def _order_limit(self, dims: list[_Dim], measures: list[_Measure], *, layered: bool) -> str:
        parts: list[str] = []
        labels = {m.pill.label for m in measures}
        for sort in self.spec.sort:
            dim = next((d for d in dims if sort.field in {d.pill.label, d.pill.field}), None)
            if dim is not None:
                target = quote_ident(dim.pill.label)
            elif sort.field in labels:
                target = quote_ident(sort.field)
            else:
                measure = next((m for m in measures if m.pill.field == sort.field), None)
                if measure is None:
                    raise CompileError(
                        f"Sort field {sort.field!r} is not a column or measure of this view."
                    )
                target = quote_ident(measure.pill.label)
            parts.append(f"{target} {'ASC' if sort.direction == 'asc' else 'DESC'}")
        sql = ""
        if parts:
            sql += " ORDER BY " + ", ".join(parts)
        elif layered and dims and any(m.level == "table" for m in measures):
            sql += " ORDER BY " + ", ".join(quote_ident(d.pill.label) for d in dims)
        if self.spec.limit is not None:
            sql += f" LIMIT {int(self.spec.limit)}"
        return sql

    def _compile_simple(
        self, dims: list[_Dim], measures: list[_Measure], filters: list[tuple[Filter, Node]]
    ) -> str:
        lowerer = self._row_lowerer()
        select: list[str] = []
        for dim in dims:
            expression = lowerer.sql(dim.node)
            if isinstance(dim.node, CalcField) and dim.node.name == dim.pill.label:
                select.append(expression)
            else:
                select.append(f"{expression} AS {quote_ident(dim.pill.label)}")
        for measure in measures:
            select.append(f"{lowerer.sql(measure.node)} AS {quote_ident(measure.pill.label)}")
        sql = f"SELECT {', '.join(select)} FROM {source_expression(self.spec)} AS {SOURCE_CTE}"
        where = self._where(filters, lowerer)
        if where:
            sql += f" WHERE {where}"
        if dims:
            sql += " GROUP BY " + ", ".join(lowerer.sql(d.node) for d in dims)
        return sql + self._order_limit(dims, measures, layered=False)

    def _compile_row_level(
        self, dims: list[_Dim], measures: list[_Measure], filters: list[tuple[Filter, Node]]
    ) -> str:
        # An unaggregated scatter: one mark per source row.
        lowerer = self._row_lowerer()
        select = []
        for measure in measures:
            assert isinstance(measure.node, Call)
            select.append(
                f"{lowerer.sql(measure.node.args[0])} AS {quote_ident(measure.pill.label)}"
            )
        sql = f"SELECT {', '.join(select)} FROM {source_expression(self.spec)} AS {SOURCE_CTE}"
        where = self._where(filters, lowerer)
        if where:
            sql += f" WHERE {where}"
        return sql + self._order_limit(dims, measures, layered=False)

    def _compile_layered(
        self,
        dims: list[_Dim],
        measures: list[_Measure],
        lods: list[_LodPlan],
        tables: list[_TablePlan],
        context_filters: list[tuple[Filter, Node]],
        row_filters: list[tuple[Filter, Node]],
        measure_filters: list[tuple[Filter, Node]],
    ) -> str:
        ctes: list[tuple[str, str]] = [
            ("nova_source", f"SELECT * FROM {source_expression(self.spec)} AS nova_src")
        ]
        relation = "nova_source"
        lod_columns: dict[Lod, str] = {}

        if context_filters:
            where = self._where(context_filters, self._row_lowerer())
            ctes.append(("nova_context", f"SELECT * FROM {relation} WHERE {where}"))
            relation = "nova_context"

        def materialize(plan: _LodPlan) -> None:
            nonlocal relation
            lowerer = self._row_lowerer(lod_columns)
            keys = [lowerer.sql(key) for key in plan.keys]
            body = lowerer.sql(plan.node.body)
            table = f"nova_lod_{plan.index}"
            select = [f"{key} AS `__nova_k{i}`" for i, key in enumerate(keys)]
            select.append(f"{body} AS `__nova_v`")
            query = f"SELECT {', '.join(select)} FROM {relation}"
            if keys:
                query += " GROUP BY " + ", ".join(keys)
            ctes.append((table, query))
            column = f"__nova_lod_{plan.index}"
            joined = f"nova_rows_{plan.index}"
            if keys:
                condition = " AND ".join(
                    f"{key} <=> {table}.`__nova_k{i}`" for i, key in enumerate(keys)
                )
                join = f"LEFT JOIN {table} ON {condition}"
            else:
                join = f"CROSS JOIN {table}"
            ctes.append(
                (
                    joined,
                    f"SELECT {relation}.*, {table}.`__nova_v` AS {quote_ident(column)} "
                    f"FROM {relation} {join}",
                )
            )
            relation = joined
            lod_columns[plan.node] = column

        for plan in lods:
            if plan.stage == "fixed":
                materialize(plan)

        if row_filters:
            where = self._where(row_filters, self._row_lowerer(lod_columns))
            ctes.append(("nova_filtered", f"SELECT * FROM {relation} WHERE {where}"))
            relation = "nova_filtered"

        for plan in lods:
            if plan.stage == "view":
                materialize(plan)

        lods_by_node = {plan.node: plan for plan in lods}
        row_lowerer = self._row_lowerer(lod_columns)

        # Aggregate terms, hoisted so later layers can reference them.
        terms: dict[Call, int] = {}
        pure: set[Call] = set()

        def collect(node: Node) -> None:
            for item in _walk_outside_lod(node):
                if _is_aggregate_call(item) and item not in terms:
                    assert isinstance(item, Call)
                    terms[item] = len(terms)
                    if self._lod_pure(item):
                        pure.add(item)

        for measure in measures:
            collect(measure.node)
        for _, node in measure_filters:
            collect(node)
        for table_plan in tables:
            if table_plan.node.name == "TOTAL":
                for term in self._total_terms(table_plan.node):
                    if term not in terms:
                        terms[term] = len(terms)

        dim_sql = [row_lowerer.sql(d.node) for d in dims]
        view_select = [
            f"{sql} AS {quote_ident(d.alias)}" for sql, d in zip(dim_sql, dims, strict=True)
        ]
        for term, index in terms.items():
            if term not in pure:
                view_select.append(f"{row_lowerer.aggregate_sql(term)} AS `__nova_a{index}`")
        if not view_select:
            view_select.append("COUNT(*) AS `__nova_rows`")
        view = f"SELECT {', '.join(view_select)} FROM {relation}"
        if dims:
            view += " GROUP BY " + ", ".join(dim_sql)
        ctes.append(("nova_view", view))
        current = "nova_view"

        if pure:
            joins: list[str] = []
            extra: list[str] = []
            for term in pure:
                index = terms[term]
                keys = list(dim_sql)
                # Only the outermost LODs set the grain; nested ones are
                # already folded into their parent's value.
                for item in _walk_outside_lod(term.args[0]):
                    if isinstance(item, Lod):
                        for key in lods_by_node[item].keys:
                            key_sql = row_lowerer.sql(key)
                            if key_sql not in keys:
                                keys.append(key_sql)
                distinct = [f"{key} AS `__nova_g{i}`" for i, key in enumerate(keys)]
                distinct.append(f"{row_lowerer.sql(term.args[0])} AS `__nova_v`")
                rolled = replace(term, args=(CalcField("__nova_v"), *term.args[1:]))
                aggregate = Lowerer(types=self.types, column=quote_ident).aggregate_sql(rolled)
                group = [f"`__nova_g{i}`" for i in range(len(dims))]
                select = [f"`__nova_g{i}` AS {quote_ident(d.alias)}" for i, d in enumerate(dims)]
                select.append(f"{aggregate} AS `__nova_v`")
                query = (
                    f"SELECT {', '.join(select)} FROM (SELECT DISTINCT {', '.join(distinct)} "
                    f"FROM {relation}) AS nova_groups"
                )
                if group:
                    query += " GROUP BY " + ", ".join(group)
                table = f"nova_pure_{index}"
                ctes.append((table, query))
                if dims:
                    condition = " AND ".join(
                        f"{current}.{quote_ident(d.alias)} <=> {table}.{quote_ident(d.alias)}"
                        for d in dims
                    )
                    joins.append(f"LEFT JOIN {table} ON {condition}")
                else:
                    joins.append(f"CROSS JOIN {table}")
                extra.append(f"{table}.`__nova_v` AS `__nova_a{index}`")
            ctes.append(
                (
                    "nova_aggregated",
                    f"SELECT {current}.*, {', '.join(extra)} FROM {current} {' '.join(joins)}",
                )
            )
            current = "nova_aggregated"

        def aggregate_ref(node: Call) -> str | None:
            index = terms.get(node)
            return None if index is None else f"`__nova_a{index}`"

        table_columns: dict[tuple[Call, tuple[str, ...] | None], str] = {}

        def post_lowerer(addressing: tuple[str, ...] | None) -> Lowerer:
            def table_ref(node: Call) -> str:
                key = (node, addressing)
                if key not in table_columns:
                    raise CompileError("A table calculation is not available at this step.")
                return quote_ident(table_columns[key])

            def no_column(name: str) -> str:
                raise CompileError(
                    f"[{name}] is not aggregated here; aggregate it, for example SUM([{name}])."
                )

            return Lowerer(
                types=self.types, column=no_column, aggregate=aggregate_ref, table=table_ref
            )

        if measure_filters:
            where = self._where(measure_filters, post_lowerer(None))
            ctes.append(("nova_measures", f"SELECT * FROM {current} WHERE {where}"))
            current = "nova_measures"

        for depth in sorted({table_plan.depth for table_plan in tables}):
            select = [f"{current}.*"]
            for table_plan in tables:
                if table_plan.depth != depth:
                    continue
                partition, order = self._addressing(dims, table_plan.addressing)
                lowerer = post_lowerer(table_plan.addressing)
                if table_plan.node.name == "TOTAL":
                    expression = self._total_sql(table_plan.node, partition, terms)
                else:
                    args = [lowerer.sql(arg) for arg in table_plan.node.args]
                    expression = window_sql(table_plan.node, args, partition=partition, order=order)
                column = f"__nova_t{table_plan.index}"
                select.append(f"{expression} AS {quote_ident(column)}")
                table_columns[(table_plan.node, table_plan.addressing)] = column
            layer = f"nova_table_{depth}"
            ctes.append((layer, f"SELECT {', '.join(select)} FROM {current}"))
            current = layer

        final: list[str] = [f"{quote_ident(d.alias)} AS {quote_ident(d.pill.label)}" for d in dims]
        for measure in measures:
            addressing = tuple(measure.addressing) if measure.addressing is not None else None
            expression = post_lowerer(addressing).sql(measure.node)
            final.append(f"{expression} AS {quote_ident(measure.pill.label)}")
        if not final:
            final.append("`__nova_rows`")
        with_clause = ", ".join(f"{name} AS ({query})" for name, query in ctes)
        sql = f"WITH {with_clause} SELECT {', '.join(final)} FROM {current}"
        return sql + self._order_limit(dims, measures, layered=True)

    def _lod_pure(self, term: Call) -> bool:
        """True when the aggregate's argument is built only from LOD values."""
        if term.name == COUNT_ROWS or not term.args:
            return False
        argument = term.args[0]
        has_lod = False
        for node in _walk_outside_lod(argument):
            if isinstance(node, CalcField):
                return False
            if isinstance(node, Lod):
                has_lod = True
        return has_lod

    def _addressing(
        self, dims: list[_Dim], addressing: tuple[str, ...] | None
    ) -> tuple[list[str], list[str]]:
        if addressing is None:
            return [], [quote_ident(d.alias) for d in dims]
        along: list[_Dim] = []
        for name in addressing:
            for dim in dims:
                if name in {dim.pill.field, dim.pill.label} and dim not in along:
                    along.append(dim)
        partition = [quote_ident(d.alias) for d in dims if d not in along]
        return partition, [quote_ident(d.alias) for d in along]

    def _total_terms(self, node: Call) -> list[Call]:
        terms: list[Call] = []
        for item in _walk_outside_lod(node.args[0]):
            if not _is_aggregate_call(item):
                continue
            assert isinstance(item, Call)
            if item.name in {"SUM", "COUNT", "MIN", "MAX", COUNT_ROWS}:
                terms.append(item)
            elif item.name == "AVG":
                terms += [Call("SUM", item.args), Call("COUNT", item.args)]
            else:
                raise CompileError("TOTAL supports SUM, AVG, COUNT, MIN and MAX inside it.")
        for term in terms:
            self._typed(term)
        return terms

    def _total_sql(
        self,
        node: Call,
        partition: list[str],
        terms: dict[Call, int],
    ) -> str:
        over = f"OVER (PARTITION BY {', '.join(partition)})" if partition else "OVER ()"

        def regroup(term: Call) -> str:
            if term.name == "AVG":
                total = f"SUM(`__nova_a{terms[Call('SUM', term.args)]}`) {over}"
                count = f"SUM(`__nova_a{terms[Call('COUNT', term.args)]}`) {over}"
                return f"({total} / NULLIF({count}, 0))"
            column = f"`__nova_a{terms[term]}`"
            function = {"MIN": "MIN", "MAX": "MAX"}.get(term.name, "SUM")
            return f"{function}({column}) {over}"

        def no_column(name: str) -> str:
            raise CompileError(f"[{name}] is not aggregated inside TOTAL.")

        lowerer = Lowerer(types=self.types, column=no_column, aggregate=regroup)
        return lowerer.sql(node.args[0])


def _relative_date_clause(item: RelativeDateFilter, column: str) -> str:
    window = item.range
    if window == "last_n_days":
        return f"{column} >= DATE_SUB(CURRENT_DATE, INTERVAL {int(item.n)} DAY)"
    if window == "last_n_months":
        return f"{column} >= DATE_SUB(CURRENT_DATE, INTERVAL {int(item.n)} MONTH)"
    if window == "last_n_years":
        return f"{column} >= DATE_SUB(CURRENT_DATE, INTERVAL {int(item.n)} YEAR)"
    if window == "month_to_date":
        return f"{column} >= DATE_TRUNC(CURRENT_DATE, 'month')"
    return f"{column} >= DATE_TRUNC(CURRENT_DATE, 'year')"


def to_vega_lite(spec: WorksheetSpec, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A sanitized Vega-Lite mirror of the worksheet, for replay paths that
    still speak Vega (persisted answers, exports)."""
    mark = spec.marks.type
    mark_map = {
        "bar": "bar",
        "line": "line",
        "area": "area",
        "circle": "circle",
        "square": "square",
        "tick": "tick",
        "pie": "arc",
        "text": "text",
    }
    measures = spec.measures() or [
        FieldRef(field="*", field_type="quantitative", aggregation="count")
    ]
    dimensions = spec.dimensions()

    def vega_type(pill: FieldRef) -> str:
        if pill.field_type == "temporal":
            return "temporal"
        if not pill.discrete or pill.is_measure:
            return "quantitative"
        return "nominal"

    def encoding_field(pill: FieldRef) -> dict[str, Any]:
        return {"field": pill.label, "type": vega_type(pill)}

    encoding: dict[str, Any] = {}
    if mark == "pie":
        encoding["theta"] = encoding_field(measures[0])
        if dimensions:
            encoding["color"] = {"field": dimensions[0].label, "type": "nominal"}
    elif mark == "text":
        if dimensions:
            encoding["x"] = {"field": dimensions[0].label, "type": "nominal"}
            if len(dimensions) > 1:
                encoding["y"] = {"field": dimensions[1].label, "type": "nominal"}
        else:
            encoding["x"] = {"field": measures[0].label, "type": "quantitative"}
        encoding["text"] = encoding_field(measures[0])
    else:
        if dimensions:
            encoding["x"] = {
                "field": dimensions[0].label,
                "type": "temporal"
                if dimensions[0].field_type == "temporal"
                else ("quantitative" if not dimensions[0].discrete else "nominal"),
            }
            encoding["y"] = encoding_field(measures[0])
        elif len(measures) > 1:
            encoding["x"] = encoding_field(measures[0])
            encoding["y"] = encoding_field(measures[1])
        else:
            encoding["x"] = {"field": measures[0].label, "type": "quantitative"}
            encoding["y"] = {
                "field": measures[0].label,
                "type": "quantitative",
                "aggregate": "count",
            }
    if spec.marks.color is not None and mark != "pie":
        encoding["color"] = encoding_field(spec.marks.color)
    if spec.marks.size is not None and mark != "pie":
        encoding["size"] = encoding_field(spec.marks.size)
    spec_out: dict[str, Any] = {
        "$schema": "https://vega-lite.github.io/schema/vega-lite/v5.json",
        "mark": mark_map.get(mark, "bar"),
        "width": "container",
        "data": {"values": rows},
        "encoding": encoding,
    }
    if spec.title:
        spec_out["title"] = spec.title
    return spec_out


__all__ = [
    "AGGREGATION_FUNCTIONS",
    "PROBE_ROWS",
    "SOURCE_CTE",
    "Aggregation",
    "CompileError",
    "CompiledQuery",
    "compile_worksheet",
    "field_type_for",
    "infer_column_types",
    "quote_ident",
    "quote_literal",
    "source_expression",
    "source_probe_sql",
    "to_vega_lite",
    "validate_fields",
]
