from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True, slots=True)
class SqlType:
    kind: str
    bits: int | None = None
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    element: SqlType | None = None
    key: SqlType | None = None
    value: SqlType | None = None
    fields: tuple[tuple[str, SqlType], ...] = ()
    raw: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class UnknownType(SqlType):
    kind: str = "UNKNOWN"


_INTEGER_BITS = {
    "TINYINT": 8,
    "SMALLINT": 16,
    "INT": 32,
    "INTEGER": 32,
    "BIGINT": 64,
    "LARGEINT": 128,
}


class _TypeParser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens = re.findall(r"`(?:``|[^`])*`|[A-Za-z_][A-Za-z_0-9]*|\d+|[<>,():]", source)
        self.index = 0
        compact = re.sub(r"\s+", "", source)
        if "".join(self.tokens) != compact:
            raise ValueError("Unsupported type metadata")

    def take(self, expected: str | None = None) -> str:
        if self.index >= len(self.tokens):
            raise ValueError("Incomplete SQL type")
        token = self.tokens[self.index]
        if expected and token != expected:
            raise ValueError("Invalid SQL type parameters")
        self.index += 1
        return token

    def peek(self) -> str:
        return self.tokens[self.index] if self.index < len(self.tokens) else ""

    def parse(self, depth: int = 0) -> SqlType:
        if depth > 32:
            raise ValueError("SQL type nesting exceeds limit")
        name = self.take().upper()
        parameters = []
        if self.peek() == "(":
            self.take("(")
            parameters.append(int(self.take()))
            if self.peek() == ",":
                self.take(",")
                parameters.append(int(self.take()))
            self.take(")")
        if name in _INTEGER_BITS and not parameters:
            return SqlType("INTEGER", bits=_INTEGER_BITS[name])
        if name in {"BOOL", "BOOLEAN"} and not parameters:
            return SqlType("BOOLEAN")
        if (
            name
            in {
                "FLOAT",
                "DOUBLE",
                "DATE",
                "DATETIME",
                "JSON",
                "BINARY",
                "VARBINARY",
                "BITMAP",
                "HLL",
                "PERCENTILE",
            }
            and not parameters
        ):
            return SqlType(name)
        if name in {"CHAR", "VARCHAR", "STRING"}:
            if len(parameters) > 1 or (parameters and parameters[0] <= 0):
                raise ValueError("Invalid string length")
            return SqlType(
                "VARCHAR" if name == "STRING" else name,
                length=parameters[0] if parameters else None,
            )
        if name in {
            "DECIMAL",
            "DECIMALV2",
            "DECIMALV3",
            "DECIMAL32",
            "DECIMAL64",
            "DECIMAL128",
            "DECIMAL256",
        }:
            if not parameters:
                return SqlType("DECIMAL")
            if (
                len(parameters) != 2
                or not 0 <= parameters[1] <= parameters[0] <= 76
                or parameters[0] < 1
            ):
                raise ValueError("Invalid decimal precision or scale")
            return SqlType("DECIMAL", precision=parameters[0], scale=parameters[1])
        if parameters:
            raise ValueError("Unknown parameterized type")
        if name == "ARRAY":
            self.take("<")
            element = self.parse(depth + 1)
            self.take(">")
            return SqlType(name, element=element)
        if name == "MAP":
            self.take("<")
            key = self.parse(depth + 1)
            self.take(",")
            value = self.parse(depth + 1)
            self.take(">")
            return SqlType(name, key=key, value=value)
        if name == "STRUCT":
            self.take("<")
            fields = []
            while True:
                field_name = self.take().strip("`").replace("``", "`")
                if self.peek() == ":":
                    self.take(":")
                fields.append((field_name, self.parse(depth + 1)))
                if self.peek() != ",":
                    break
                self.take(",")
            self.take(">")
            return SqlType(name, fields=tuple(fields))
        raise ValueError("Unknown SQL type")


def parse_sql_type(raw: str) -> SqlType:
    from dataclasses import replace

    try:
        parser = _TypeParser(raw)
        result = parser.parse()
        if parser.peek():
            raise ValueError("Unexpected SQL type suffix")
        return replace(result, raw=raw)
    except (ValueError, IndexError, RecursionError):
        return UnknownType(raw=raw)


class TypeCompatibility(Enum):
    EXACT = "exact"
    IMPLICIT_CAST = "implicit_cast"
    SAFE_WIDEN = "safe_widen"
    LOSSY = "lossy"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


class TypeCompatibilityChecker:
    @classmethod
    def compare(cls, source: SqlType, target: SqlType) -> TypeCompatibility:
        result = TypeCompatibility
        if "UNKNOWN" in {source.kind, target.kind}:
            return result.UNKNOWN
        if source == target and source.kind not in {"ARRAY", "MAP", "STRUCT"}:
            if source.kind in {"DECIMAL", "VARCHAR", "CHAR"} and (
                source.precision is None if source.kind == "DECIMAL" else source.length is None
            ):
                return result.UNKNOWN
            return result.EXACT
        if source.kind == target.kind == "INTEGER":
            if source.bits is None or target.bits is None:
                return result.UNKNOWN
            return result.SAFE_WIDEN if source.bits < target.bits else result.LOSSY
        if (source.kind, target.kind) == ("FLOAT", "DOUBLE"):
            return result.SAFE_WIDEN
        if (source.kind, target.kind) == ("DOUBLE", "FLOAT"):
            return result.LOSSY
        if source.kind == target.kind and source.kind in {"VARCHAR", "CHAR"}:
            if source.length is None or target.length is None:
                return result.UNKNOWN
            return result.SAFE_WIDEN if source.length < target.length else result.LOSSY
        if source.kind == target.kind == "DECIMAL":
            if (
                source.precision is None
                or source.scale is None
                or target.precision is None
                or target.scale is None
            ):
                return result.UNKNOWN
            fits = (
                source.scale <= target.scale
                and source.precision - source.scale <= target.precision - target.scale
            )
            return result.SAFE_WIDEN if fits else result.LOSSY
        if source.kind == target.kind == "ARRAY":
            if source.element is None or target.element is None:
                return result.UNKNOWN
            return cls.compare(source.element, target.element)
        if source.kind == target.kind == "MAP":
            if (
                source.key is None
                or target.key is None
                or source.value is None
                or target.value is None
            ):
                return result.UNKNOWN
            keys = cls.compare(source.key, target.key)
            if keys != result.EXACT:
                return (
                    result.UNKNOWN
                    if keys in {result.SAFE_WIDEN, result.UNKNOWN}
                    else result.INCOMPATIBLE
                )
            return cls.compare(source.value, target.value)
        if source.kind == target.kind == "STRUCT":
            if tuple(n.casefold() for n, _ in source.fields) != tuple(
                n.casefold() for n, _ in target.fields
            ):
                return result.INCOMPATIBLE
            outcomes = [
                cls.compare(a[1], b[1]) for a, b in zip(source.fields, target.fields, strict=True)
            ]
            for severity in (
                result.INCOMPATIBLE,
                result.UNKNOWN,
                result.LOSSY,
                result.IMPLICIT_CAST,
                result.SAFE_WIDEN,
            ):
                if severity in outcomes:
                    return severity
            return result.EXACT
        return result.INCOMPATIBLE

    @classmethod
    def query_assignment(cls, source: SqlType, target: SqlType) -> TypeCompatibility:
        result = cls.compare(source, target)
        numeric = {"INTEGER", "FLOAT", "DOUBLE", "DECIMAL"}
        if (
            result == TypeCompatibility.INCOMPATIBLE
            and source.kind in numeric
            and target.kind in numeric
        ):
            return TypeCompatibility.IMPLICIT_CAST
        return result

    @classmethod
    def safe_schema_widen(cls, source: SqlType, target: SqlType) -> bool:
        return cls.compare(source, target) in {
            TypeCompatibility.EXACT,
            TypeCompatibility.SAFE_WIDEN,
        }
