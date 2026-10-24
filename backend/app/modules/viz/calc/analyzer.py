"""Type and level checking for calculation formulas.

Every expression has a **data type** (number, string, date, datetime,
boolean) and an **evaluation level**:

* ``const`` — a literal, the same everywhere;
* ``row`` — evaluated per source row (fields, row functions, and LOD
  expressions, whose value is joined back to each row);
* ``aggregate`` — evaluated per mark (``SUM``, ``AVG``, ...);
* ``table`` — evaluated over the marks after aggregation (``RUNNING_SUM``,
  ``RANK``, ``LOOKUP``, ...).

Row and aggregate values cannot be mixed in one expression, table
calculations take aggregated arguments, and LOD bodies must be aggregates.
These are the rules that make a worksheet compile to one well-formed query,
so the editor reports them while the formula is written.

Calculated fields may reference columns and other calculated fields;
:func:`analyze_calculations` checks them all, rejects cycles, and returns
each calculation with its references inlined (:attr:`CalcInfo.expanded`), so
planning only ever sees source columns.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from typing import Literal

from app.modules.viz.calc.functions import FUNCTIONS, FunctionSpec
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
    children,
    parse,
)

DataType = Literal["number", "string", "date", "datetime", "boolean", "null", "unknown"]
Level = Literal["const", "row", "aggregate", "table"]

RESERVED_PREFIX = "__nova_"

#: An internal function: ``COUNT(*)``, the record-count measure. User
#: formulas cannot name it (the parser reserves leading double underscores).
COUNT_ROWS = "__COUNT_ROWS"

_ARITHMETIC = frozenset({"+", "-", "*", "/", "%", "^"})
_COMPARISON = frozenset({"=", "<>", "<", "<=", ">", ">="})


@dataclass(frozen=True)
class Typed:
    data_type: DataType
    level: Level


@dataclass
class CalcInfo:
    """One checked calculated field."""

    name: str
    formula: str
    node: Node
    expanded: Node
    data_type: DataType
    level: Level
    references: list[str] = field(default_factory=list)

    @property
    def role(self) -> Literal["dimension", "measure"]:
        if self.level in {"aggregate", "table"} or self.data_type == "number":
            return "measure"
        return "dimension"


def walk(node: Node) -> Iterator[Node]:
    """Pre-order traversal."""
    yield node
    for child in children(node):
        yield from walk(child)


def contains(node: Node, kind: type[Node]) -> bool:
    return any(isinstance(item, kind) for item in walk(node))


def _combine(levels: Iterable[Level], node: Node) -> Level:
    present = {level for level in levels if level != "const"}
    if not present:
        return "const"
    if "row" in present and present & {"aggregate", "table"}:
        raise CalcError(
            "Cannot mix aggregate and non-aggregate values here. Aggregate the field "
            "(for example SUM([Sales])) or wrap the aggregate in a level of detail "
            "expression.",
            node.start,
            node.end,
        )
    if "table" in present:
        return "table"
    if "aggregate" in present:
        return "aggregate"
    return "row"


def _accepts(expected: str, actual: DataType) -> bool:
    if actual in {"unknown", "null"} or expected == "any":
        return True
    if expected == "date":
        return actual in {"date", "datetime", "string"}
    return expected == actual


def _comparable(left: DataType, right: DataType) -> bool:
    if left in {"unknown", "null"} or right in {"unknown", "null"}:
        return True
    if left == right:
        return True
    temporal = {"date", "datetime", "string"}
    return left in temporal and right in temporal


def _unify(types: list[DataType], node: Node, what: str) -> DataType:
    known = [t for t in types if t not in {"null", "unknown"}]
    if not known:
        return "unknown" if "unknown" in types else "null"
    first = known[0]
    for other in known[1:]:
        if other == first:
            continue
        if {first, other} <= {"date", "datetime"}:
            first = "datetime"
            continue
        raise CalcError(
            f"{what} must all return the same type ({first} and {other}).", node.start, node.end
        )
    return first


def _type_name(data_type: str) -> str:
    return {"number": "a number", "string": "text", "boolean": "true/false", "date": "a date"}.get(
        data_type, data_type
    )


class Resolver:
    """Resolves ``[Field]`` references to columns or calculated fields."""

    def __init__(self, columns: dict[str, DataType], calcs: dict[str, str]) -> None:
        self.columns = columns
        self.formulas = calcs
        self.done: dict[str, CalcInfo] = {}
        self.stack: list[str] = []
        self._lower = {name.lower(): name for name in [*columns, *calcs]}

    def canonical(self, name: str) -> str | None:
        if name in self.columns or name in self.formulas:
            return name
        return self._lower.get(name.lower())

    def calc(self, name: str) -> CalcInfo:
        if name in self.done:
            return self.done[name]
        if name in self.stack:
            cycle = " → ".join([*self.stack[self.stack.index(name) :], name])
            raise CalcError(f"Calculated fields reference each other in a cycle: {cycle}.")
        self.stack.append(name)
        try:
            formula = self.formulas[name]
            node = parse(formula)
            checker = _Checker(self)
            typed = checker.check(node)
            expanded = self.expand(node)
            info = CalcInfo(
                name=name,
                formula=formula,
                node=node,
                expanded=expanded,
                data_type="number" if typed.data_type == "null" else typed.data_type,
                level="row" if typed.level == "const" else typed.level,
                references=sorted(checker.references),
            )
        finally:
            self.stack.pop()
        self.done[name] = info
        return info

    def expand(self, node: Node) -> Node:
        """Inline calculated-field references; columns stay field refs."""
        if isinstance(node, FieldRef):
            name = self.canonical(node.name)
            if name is not None and name in self.formulas:
                return self.calc(name).expanded
            return FieldRef(name or node.name, start=node.start, end=node.end)
        return _rebuild(node, [self.expand(child) for child in children(node)])


def _rebuild(node: Node, kids: list[Node]) -> Node:
    """A copy of ``node`` with its children replaced (same order as children())."""
    if isinstance(node, Unary):
        return replace(node, operand=kids[0])
    if isinstance(node, Binary):
        return replace(node, left=kids[0], right=kids[1])
    if isinstance(node, InList):
        return replace(node, subject=kids[0], options=tuple(kids[1:]))
    if isinstance(node, Call):
        return replace(node, args=tuple(kids))
    if isinstance(node, If):
        pairs = len(node.branches)
        branches = tuple((kids[2 * i], kids[2 * i + 1]) for i in range(pairs))
        otherwise = kids[2 * pairs] if node.otherwise is not None else None
        return replace(node, branches=branches, otherwise=otherwise)
    if isinstance(node, Case):
        pairs = len(node.whens)
        whens = tuple((kids[1 + 2 * i], kids[2 + 2 * i]) for i in range(pairs))
        otherwise = kids[1 + 2 * pairs] if node.otherwise is not None else None
        return replace(node, subject=kids[0], whens=whens, otherwise=otherwise)
    if isinstance(node, Lod):
        dims = tuple(kids[: len(node.dims)])
        return replace(node, dims=dims, body=kids[len(node.dims)])
    return node


class _Checker:
    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver
        self.references: set[str] = set()
        self.in_fixed = 0
        self.types: dict[Node, Typed] = {}

    def check(self, node: Node) -> Typed:
        method = getattr(self, f"_{type(node).__name__.rstrip('_').lower()}")
        typed: Typed = method(node)
        self.types[node] = typed
        return typed

    def _literal(self, node: Literal_) -> Typed:
        return Typed(node.kind, "const")  # type: ignore[arg-type]

    def _fieldref(self, node: FieldRef) -> Typed:
        name = self.resolver.canonical(node.name)
        if name is None:
            raise CalcError(f"The field [{node.name}] does not exist.", node.start, node.end)
        self.references.add(name)
        if name in self.resolver.columns:
            return Typed(self.resolver.columns[name], "row")
        try:
            info = self.resolver.calc(name)
        except CalcError as exc:
            if exc.start or exc.end:
                raise CalcError(
                    f"[{name}] has an error: {exc.message}", node.start, node.end
                ) from exc
            raise CalcError(exc.message, node.start, node.end) from exc
        return Typed(info.data_type, info.level)

    def _unary(self, node: Unary) -> Typed:
        operand = self.check(node.operand)
        if node.op == "NOT":
            if not _accepts("boolean", operand.data_type):
                raise CalcError("NOT needs a true/false value.", node.start, node.end)
            return Typed("boolean", operand.level)
        if not _accepts("number", operand.data_type) or operand.data_type == "string":
            raise CalcError("Negation needs a number.", node.start, node.end)
        return Typed("number", operand.level)

    def _binary(self, node: Binary) -> Typed:
        left = self.check(node.left)
        right = self.check(node.right)
        level = _combine([left.level, right.level], node)
        lt, rt = left.data_type, right.data_type
        if node.op in {"AND", "OR"}:
            if not (_accepts("boolean", lt) and _accepts("boolean", rt)):
                raise CalcError(
                    f"{node.op} needs true/false values on both sides.", node.start, node.end
                )
            return Typed("boolean", level)
        if node.op in _COMPARISON:
            if not _comparable(lt, rt):
                raise CalcError(
                    f"Cannot compare {_type_name(lt)} with {_type_name(rt)}.", node.start, node.end
                )
            return Typed("boolean", level)
        return Typed(self._arithmetic(node, lt, rt), level)

    def _arithmetic(self, node: Binary, lt: DataType, rt: DataType) -> DataType:
        loose = {"unknown", "null"}
        temporal = {"date", "datetime"}
        if node.op == "+":
            if lt == "string" or rt == "string":
                if {lt, rt} <= {"string", *loose}:
                    return "string"
                raise CalcError(
                    "Cannot add text and a number; convert with STR() first.", node.start, node.end
                )
            if lt in temporal and rt in {"number", *loose}:
                return lt
            if rt in temporal and lt in {"number", *loose}:
                return rt
        if node.op == "-":
            if lt in temporal and rt in temporal:
                return "number"
            if lt in temporal and rt in {"number", *loose}:
                return lt
        if lt in {"number", *loose} and rt in {"number", *loose}:
            return "number"
        raise CalcError(
            f"The {node.op} operator needs numbers here, not {_type_name(lt)} and "
            f"{_type_name(rt)}.",
            node.start,
            node.end,
        )

    def _inlist(self, node: InList) -> Typed:
        subject = self.check(node.subject)
        levels: list[Level] = [subject.level]
        for option in node.options:
            typed = self.check(option)
            if not _comparable(subject.data_type, typed.data_type):
                raise CalcError(
                    "IN values must match the type of the tested value.", option.start, option.end
                )
            levels.append(typed.level)
        return Typed("boolean", _combine(levels, node))

    def _if(self, node: If) -> Typed:
        levels: list[Level] = []
        values: list[DataType] = []
        for condition, value in node.branches:
            test = self.check(condition)
            if not _accepts("boolean", test.data_type):
                raise CalcError(
                    "An IF condition must be true/false.", condition.start, condition.end
                )
            result = self.check(value)
            levels += [test.level, result.level]
            values.append(result.data_type)
        if node.otherwise is not None:
            result = self.check(node.otherwise)
            levels.append(result.level)
            values.append(result.data_type)
        return Typed(_unify(values, node, "IF branches"), _combine(levels, node))

    def _case(self, node: Case) -> Typed:
        subject = self.check(node.subject)
        levels: list[Level] = [subject.level]
        values: list[DataType] = []
        for match, value in node.whens:
            typed = self.check(match)
            if not _comparable(subject.data_type, typed.data_type):
                raise CalcError(
                    "A WHEN value must match the CASE value's type.", match.start, match.end
                )
            result = self.check(value)
            levels += [typed.level, result.level]
            values.append(result.data_type)
        if node.otherwise is not None:
            result = self.check(node.otherwise)
            levels.append(result.level)
            values.append(result.data_type)
        return Typed(_unify(values, node, "CASE results"), _combine(levels, node))

    def _lod(self, node: Lod) -> Typed:
        if node.kind in {"include", "exclude"} and self.in_fixed:
            raise CalcError(
                f"{node.kind.upper()} depends on the view, so it cannot appear inside FIXED.",
                node.start,
                node.end,
            )
        for dim in node.dims:
            typed = self.check(dim)
            if typed.level not in {"row", "const"}:
                raise CalcError(
                    "Level of detail dimensions must be row-level fields.", dim.start, dim.end
                )
            if contains(self.resolver.expand(dim), Lod):
                raise CalcError(
                    "A level of detail dimension cannot itself be a level of detail expression.",
                    dim.start,
                    dim.end,
                )
        fixed = node.kind in {"fixed", "table"}
        self.in_fixed += int(fixed)
        try:
            body = self.check(node.body)
        finally:
            self.in_fixed -= int(fixed)
        if body.level != "aggregate":
            raise CalcError(
                "The expression inside a level of detail must be an aggregate, for example "
                "SUM([Sales]).",
                node.body.start,
                node.body.end,
            )
        return Typed(body.data_type, "row")

    def _call(self, node: Call) -> Typed:
        if node.name == COUNT_ROWS:
            return Typed("number", "aggregate")
        spec = FUNCTIONS.get(node.name)
        if spec is None:
            raise CalcError(f"Unknown function {node.name}.", node.start, node.end)
        count = len(node.args)
        if count < spec.min_args or (spec.max_args is not None and count > spec.max_args):
            raise CalcError(f"{spec.name} is written {spec.syntax}.", node.start, node.end)
        if spec.name in {"MIN", "MAX"} and count == 2:
            return self._row_call(node, spec)
        if spec.kind == "aggregate":
            return self._aggregate_call(node, spec)
        if spec.kind == "table":
            return self._table_call(node, spec)
        return self._row_call(node, spec)

    def _args(self, node: Call, spec: FunctionSpec) -> list[Typed]:
        typed: list[Typed] = []
        for index, arg in enumerate(node.args):
            kind = spec.param(index)
            if kind in {"part", "int", "direction"}:
                typed.append(self._constant_arg(arg, kind, spec))
                continue
            result = self.check(arg)
            if not _accepts(kind, result.data_type):
                raise CalcError(
                    f"{spec.name} expects {_type_name(kind)} for argument {index + 1}, not "
                    f"{_type_name(result.data_type)}.",
                    arg.start,
                    arg.end,
                )
            typed.append(result)
        return typed

    def _constant_arg(self, arg: Node, kind: str, spec: FunctionSpec) -> Typed:
        if kind == "part":
            if not (isinstance(arg, Literal_) and arg.kind == "string"):
                raise CalcError(
                    f"{spec.name} needs the date part as quoted text, like 'month'.",
                    arg.start,
                    arg.end,
                )
            if arg.value.lower() not in spec.parts:
                raise CalcError(
                    f"{spec.name} accepts {', '.join(repr(p) for p in spec.parts)}.",
                    arg.start,
                    arg.end,
                )
            return Typed("string", "const")
        if kind == "direction":
            if not (isinstance(arg, Literal_) and arg.value.lower() in {"asc", "desc"}):
                raise CalcError("The rank direction is 'asc' or 'desc'.", arg.start, arg.end)
            return Typed("string", "const")
        if constant_int(arg) is None:
            raise CalcError(f"{spec.name} needs a whole-number constant here.", arg.start, arg.end)
        return Typed("number", "const")

    def _row_call(self, node: Call, spec: FunctionSpec) -> Typed:
        typed = self._args(node, spec)
        level = _combine([t.level for t in typed], node)
        return Typed(self._returns(spec, typed, node), level)

    def _aggregate_call(self, node: Call, spec: FunctionSpec) -> Typed:
        typed = self._args(node, spec)
        for arg, result in zip(node.args, typed, strict=True):
            if result.level in {"aggregate", "table"}:
                raise CalcError(
                    f"{spec.name} cannot aggregate a value that is already aggregated; use a "
                    "level of detail expression for nested aggregation.",
                    arg.start,
                    arg.end,
                )
        if spec.name == "PERCENTILE" and typed[1].level != "const":
            raise CalcError(
                "The percentile must be a constant between 0 and 1.",
                node.args[1].start,
                node.args[1].end,
            )
        return Typed(self._returns(spec, typed, node), "aggregate")

    def _table_call(self, node: Call, spec: FunctionSpec) -> Typed:
        typed = self._args(node, spec)
        for arg, result in zip(node.args, typed, strict=True):
            if result.level == "row":
                raise CalcError(
                    f"{spec.name} works on aggregated values, for example "
                    f"{spec.name}(SUM([Sales])).",
                    arg.start,
                    arg.end,
                )
        if spec.name == "TOTAL" and node.args and typed[0].level == "table":
            raise CalcError(
                "TOTAL takes an aggregate, not another table calculation.", node.start, node.end
            )
        return Typed(self._returns(spec, typed, node), "table")

    @staticmethod
    def _returns(spec: FunctionSpec, typed: list[Typed], node: Call) -> DataType:
        rule = spec.returns
        if rule.startswith("arg:"):
            index = int(rule.split(":", 1)[1])
            data_type = typed[index].data_type if index < len(typed) else "unknown"
            return "unknown" if data_type == "null" else data_type
        if rule == "unify":
            values = [t.data_type for t in typed]
            if spec.name == "IIF":
                values = values[1:]
            return _unify(values, node, f"{spec.name} results")
        return rule  # type: ignore[return-value]


def constant_int(node: Node) -> int | None:
    """The integer a constant node denotes (``3`` or ``-3``), else None."""
    if isinstance(node, Unary) and node.op == "-":
        inner = constant_int(node.operand)
        return None if inner is None else -inner
    if isinstance(node, Literal_) and node.kind == "number":
        try:
            value = float(node.value)
        except ValueError:
            return None
        if value.is_integer():
            return int(value)
    return None


def analyze_calculations(
    columns: dict[str, DataType], calculations: dict[str, str]
) -> dict[str, CalcInfo]:
    """Check every calculated field; raise :class:`CalcError` naming the field."""
    for name in calculations:
        if name in columns:
            raise CalcError(f"The calculated field [{name}] has the same name as a column.")
        if name.startswith(RESERVED_PREFIX):
            raise CalcError(f"Field names cannot start with {RESERVED_PREFIX}.")
    resolver = Resolver(columns, calculations)
    infos: dict[str, CalcInfo] = {}
    for name in calculations:
        try:
            infos[name] = resolver.calc(name)
        except CalcError as exc:
            raise NamedCalcError(name, exc) from exc
    return infos


class NamedCalcError(CalcError):
    """A calculation error attributed to one calculated field."""

    def __init__(self, name: str, error: CalcError) -> None:
        super().__init__(f"[{name}]: {error.message}", error.start, error.end)
        self.field = name
        self.detail = error.message


def check_formula(
    formula: str,
    columns: dict[str, DataType],
    calculations: dict[str, str],
    *,
    name: str | None = None,
) -> CalcInfo:
    """Check one formula (the editor path) against the sheet's fields.

    ``name`` is the calculation being edited; its saved formula is replaced
    by ``formula`` so references to it from elsewhere are not counted.
    """
    others = {key: value for key, value in calculations.items() if key != name}
    probe_name = name or f"{RESERVED_PREFIX}draft"
    if name is not None and name in columns:
        raise CalcError(f"A column is already called [{name}].")
    resolver = Resolver(columns, {**others, probe_name: formula})
    return resolver.calc(probe_name)
