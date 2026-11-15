"""Typed query inputs stay private until the authenticated driver's execute call."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, TypeAlias

from app.core.timezones import resolve_timezone
from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import ParsedStatement, parse_statement

ParameterType: TypeAlias = Literal["string", "boolean", "integer", "decimal", "date", "timestamp"]
ScalarValue: TypeAlias = str | bool | int | Decimal | date | datetime | None
ParameterValue: TypeAlias = ScalarValue | tuple[ScalarValue, ...]
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_TYPES = {"string", "boolean", "integer", "decimal", "date", "timestamp"}


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    name: str
    type: ParameterType
    nullable: bool = False
    multiple: bool = False
    precision: int | None = None
    scale: int | None = None

    def __post_init__(self) -> None:
        if type(self.name) is not str or not _NAME.fullmatch(self.name) or self.type not in _TYPES:
            raise SemanticError("Invalid parameter declaration")
        if type(self.nullable) is not bool or type(self.multiple) is not bool:
            raise SemanticError("Invalid parameter declaration")
        if self.type == "decimal":
            if (
                type(self.precision) is not int or type(self.scale) is not int
                or not 1 <= self.precision <= 38 or not 0 <= self.scale <= self.precision
            ):
                raise SemanticError("Decimal parameters require explicit precision and scale")
        elif self.precision is not None or self.scale is not None:
            raise SemanticError("Only decimal parameters declare precision and scale")


def parameter_sql_type(spec: ParameterSpec) -> str:
    if spec.type == "decimal":
        return f"DECIMAL({spec.precision},{spec.scale})"
    return {
        "string": "VARCHAR(65536)", "boolean": "BOOLEAN", "integer": "BIGINT",
        "date": "DATE", "timestamp": "DATETIME",
    }[spec.type]


def _validate_scalar(spec: ParameterSpec, value: ScalarValue) -> int:
    if value is None:
        if spec.nullable:
            return 0
        raise SemanticError("A non-null parameter value is required")
    valid = False
    if spec.type == "string":
        valid = type(value) is str and len(value) <= 65536
    elif spec.type == "boolean":
        valid = type(value) is bool
    elif spec.type == "integer":
        valid = type(value) is int and -(2**63) <= value < 2**63
    elif spec.type == "decimal" and type(value) is Decimal and value.is_finite():
        _, digits, exponent = value.as_tuple()
        if isinstance(exponent, int):
            while digits and digits[-1] == 0 and exponent < 0:
                digits, exponent = digits[:-1], exponent + 1
            integer_digits = max(len(digits) + exponent, 0) if value else 0
            valid = (
                spec.precision is not None and spec.scale is not None
                and integer_digits <= spec.precision - spec.scale
                and (not value or max(-exponent, 0) <= spec.scale)
            )
    elif spec.type == "date":
        valid = type(value) is date
    elif spec.type == "timestamp":
        valid = (
            type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None
        )
    if not valid:
        raise SemanticError("Parameter value does not match its declared type or bounds")
    try:
        size = len(str(value).encode("utf-8"))
    except UnicodeEncodeError:
        raise SemanticError("Parameter text must be valid UTF-8") from None
    if spec.type == "string" and size > 65536:
        raise SemanticError("Parameter text exceeds its UTF-8 byte limit")
    return size


@dataclass(frozen=True, slots=True)
class BoundParameters:
    specs: tuple[ParameterSpec, ...]
    values: tuple[tuple[str, ParameterValue], ...] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.specs) is not tuple or type(self.values) is not tuple:
            raise SemanticError("Parameter bindings must be immutable")
        if not 1 <= len(self.specs) <= 32 or len(self.values) != len(self.specs):
            raise SemanticError("Query requires between one and 32 declared parameters")
        if any(type(spec) is not ParameterSpec for spec in self.specs):
            raise SemanticError("Invalid parameter declaration")
        if any(type(pair) is not tuple or len(pair) != 2 for pair in self.values):
            raise SemanticError("Invalid parameter bindings")
        names = [spec.name for spec in self.specs]
        if len(set(names)) != len(names) or {name for name, _ in self.values} != set(names):
            raise SemanticError("Parameter declarations and values must match exactly")
        if len({name for name, _ in self.values}) != len(self.values):
            raise SemanticError("Duplicate parameter value")
        values = dict(self.values)
        size = 0
        for spec in self.specs:
            value = values[spec.name]
            if spec.multiple:
                if type(value) is not tuple or not 1 <= len(value) <= 500:
                    raise SemanticError("List parameters require between one and 500 values")
                size += sum(_validate_scalar(spec, item) for item in value)
            else:
                if isinstance(value, tuple):
                    raise SemanticError("Scalar parameters cannot accept lists")
                size += _validate_scalar(spec, value)
            if size > 1_048_576:
                raise SemanticError("Parameter payload exceeds the byte limit")


@dataclass(frozen=True, slots=True)
class ParameterSlot:
    name: str
    start: int
    end: int


def parameter_slots(
    parsed: ParsedStatement, specs: tuple[ParameterSpec, ...]
) -> tuple[ParameterSlot, ...]:
    """Bind source spans, including sole IN-list operands, without examining values."""
    if (len(specs) > 32 or any(type(spec) is not ParameterSpec for spec in specs)
            or len({spec.name for spec in specs}) != len(specs)):
        raise SemanticError("Parameter declarations must be unique and bounded")
    declared = {spec.name: spec for spec in specs}
    slots = []
    for node in walk_nodes(parsed.statement_context):
        if type(node).__name__ != "NamedParameterContext":
            continue
        text = parsed.normalized_sql[node.start.start : node.stop.stop + 1]
        name = text[1:]
        if not _NAME.fullmatch(name) or name not in declared:
            raise SemanticError("Query contains an undeclared or invalid named parameter")
        if declared[name].multiple:
            parent = node.parentCtx
            while parent is not None and type(parent).__name__ != "ExpressionListContext":
                parent = parent.parentCtx
            if (
                parent is None
                or type(parent.parentCtx).__name__ != "InListContext"
                or parent.start.start != node.start.start
                or parent.stop.stop != node.stop.stop
            ):
                raise SemanticError("List parameters must be the sole operand of IN or NOT IN")
        parent = node.parentCtx
        while parent is not None and type(parent).__name__ != "SelectSingleContext":
            parent = parent.parentCtx
        if parent is not None and parent.identifier() is None and parent.string() is None:
            raise SemanticError("A parameterized projection requires an explicit column alias")
        slots.append(ParameterSlot(name, node.start.start, node.stop.stop + 1))
        if len(slots) > 256:
            raise SemanticError("Query exceeds the parameter reference limit")
    if {slot.name for slot in slots} != set(declared):
        raise SemanticError("Declared parameters must match the query exactly")
    return tuple(sorted(slots, key=lambda slot: slot.start))


def _session_datetime(value: datetime, zone: str) -> datetime:
    try:
        return value.astimezone(resolve_timezone(zone)).replace(tzinfo=None)
    except (ValueError, OverflowError):
        raise SemanticError("Timestamp cannot be represented in the query timezone") from None


def driver_parameters(
    sql: str, bound: BoundParameters, zone: str
) -> tuple[str, tuple[ScalarValue, ...]]:
    """Lower after session-function rewrites; the driver alone quotes input values."""
    parsed = parse_statement(sql)
    from app.sql_frontend.read_queries import validate_read_query

    validate_read_query(parsed)
    slots = parameter_slots(parsed, bound.specs)
    values = dict(bound.values)
    specs = {spec.name: spec for spec in bound.specs}
    parts: list[str] = []
    arguments: list[ScalarValue] = []
    size = 0
    end = 0
    for slot in slots:
        parts.append(sql[end : slot.start].replace("%", "%%"))
        value = values[slot.name]
        items = value if isinstance(value, tuple) else (value,)
        size += sum(len(str(item).encode("utf-8")) for item in items)
        if len(arguments) + len(items) > 4096 or size > 1_048_576:
            raise SemanticError("Expanded query parameters exceed the execution limit")
        sql_type = parameter_sql_type(specs[slot.name])
        parts.append(", ".join(f"CAST(%s AS {sql_type})" for _ in items))
        arguments.extend(
            _session_datetime(item, zone) if isinstance(item, datetime)
            # Quote decimals as exact text before the declared DECIMAL cast;
            # otherwise the FE can infer a floating-point numeric literal.
            else format(item, f".{specs[slot.name].scale}f") if isinstance(item, Decimal)
            else item for item in items
        )
        end = slot.end
    parts.append(sql[end:].replace("%", "%%"))
    return "".join(parts), tuple(arguments)
