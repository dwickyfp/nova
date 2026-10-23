"""Typed calculations over the compiler's aggregated result, never raw SQL."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_NAME = r"^[A-Za-z_][A-Za-z0-9_.]*$"


class CalculationOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(pattern=_NAME, max_length=128)
    direction: Literal["asc", "desc"] = "asc"


class CalculationExpression(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal[
        "field",
        "number",
        "add",
        "subtract",
        "multiply",
        "divide",
        "coalesce",
        "case",
        "eq",
        "ne",
        "gt",
        "ge",
        "lt",
        "le",
        "and",
        "or",
        "running_total",
        "moving_average",
        "rank",
        "dense_rank",
    ]
    field: str | None = Field(default=None, pattern=_NAME, max_length=128)
    value: str | None = Field(default=None, max_length=40)
    args: list[CalculationExpression] = Field(default_factory=list, max_length=3)
    partition_by: list[str] = Field(default_factory=list, max_length=12)
    order_by: list[CalculationOrder] = Field(default_factory=list, max_length=12)
    preceding: int | None = Field(default=None, ge=0, le=10000)

    @model_validator(mode="after")
    def shape(self) -> CalculationExpression:
        window = self.op in {"running_total", "moving_average", "rank", "dense_rank"}
        if not window and (self.partition_by or self.order_by or self.preceding is not None):
            raise ValueError("Only window calculations accept ordering and a frame")
        if self.op == "field":
            if not self.field or self.value is not None or self.args:
                raise ValueError("Field calculations require exactly a field")
        elif self.op == "number":
            if (
                self.field
                or self.args
                or not self.value
                or not re.fullmatch(r"-?\d+(?:\.\d+)?", self.value)
            ):
                raise ValueError("Number calculations require a finite decimal literal")
            if len(self.value.replace("-", "").replace(".", "")) > 38:
                raise ValueError("Calculation literal exceeds DECIMAL precision")
        else:
            if self.field is not None or self.value is not None:
                raise ValueError("Operators take arguments, not raw fields or values")
            arity = (
                3
                if self.op == "case"
                else 0
                if self.op in {"rank", "dense_rank"}
                else 1
                if window
                else 2
            )
            if len(self.args) != arity:
                raise ValueError("Invalid calculation arity")
        if window and not self.order_by:
            raise ValueError("Window calculations require explicit stable ordering")
        if self.op == "moving_average" and self.preceding is None:
            raise ValueError("Moving averages require a bounded frame")
        if self.op in {"rank", "dense_rank"} and self.preceding is not None:
            raise ValueError("Ranking does not accept a frame")
        return self


class Calculation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=_NAME, max_length=128)
    expression: CalculationExpression

    @model_validator(mode="after")
    def bounded(self) -> Calculation:
        pending = [(self.expression, 1)]
        count = 0
        while pending:
            expression, depth = pending.pop()
            count += 1
            if count > 64 or depth > 8:
                raise ValueError("Calculation exceeds supported complexity")
            pending.extend((child, depth + 1) for child in expression.args)
        return self


def quote(name: str, columns: list[str]) -> str:
    if name not in columns or not re.fullmatch(_NAME, name):
        raise ValueError("Calculation references an unselected field")
    return f"q.`{name}`"


def expression_sql(
    expression: CalculationExpression, columns: list[str], *, in_window=False
) -> str:
    op = expression.op
    if op == "field":
        return quote(expression.field or "", columns)
    if op == "number":
        value = Decimal(expression.value or "0")
        scale = max(0, -int(value.as_tuple().exponent))
        precision = max(1, len(value.as_tuple().digits), scale)
        # Inflating a literal to 38 digits can make CASE/COALESCE promote two
        # decimal branches to DOUBLE when their combined integer/scale exceeds 38.
        return f"CAST('{value}' AS DECIMAL({precision},{scale}))"
    window = op in {"running_total", "moving_average", "rank", "dense_rank"}
    if window and in_window:
        raise ValueError("Window calculations cannot be nested")
    args = [expression_sql(arg, columns, in_window=in_window or window) for arg in expression.args]
    binary = {
        "add": "+",
        "subtract": "-",
        "multiply": "*",
        "eq": "=",
        "ne": "!=",
        "gt": ">",
        "ge": ">=",
        "lt": "<",
        "le": "<=",
        "and": "AND",
        "or": "OR",
    }
    if op in binary:
        return f"({args[0]} {binary[op]} {args[1]})"
    if op == "divide":
        return f"({args[0]} / NULLIF({args[1]}, 0))"
    if op == "coalesce":
        return f"COALESCE({args[0]}, {args[1]})"
    if op == "case":
        return f"CASE WHEN {args[0]} THEN {args[1]} ELSE {args[2]} END"
    if window:
        partitions = [quote(field, columns) for field in expression.partition_by]
        order = [
            f"{quote(item.field, columns)} {item.direction.upper()}" for item in expression.order_by
        ]
        parts = (["PARTITION BY " + ", ".join(partitions)] if partitions else []) + [
            "ORDER BY " + ", ".join(order)
        ]
        if op in {"running_total", "moving_average"}:
            start = (
                "UNBOUNDED PRECEDING"
                if expression.preceding is None
                else f"{expression.preceding} PRECEDING"
            )
            parts.append(f"ROWS BETWEEN {start} AND CURRENT ROW")
        function = {
            "running_total": "SUM",
            "moving_average": "AVG",
            "rank": "RANK",
            "dense_rank": "DENSE_RANK",
        }[op]
        return f"{function}({', '.join(args)}) OVER ({' '.join(parts)})"
    raise ValueError("Unknown calculation")


def compile_calculations(
    sql: str, columns: list[str], definitions: tuple[dict, ...]
) -> tuple[str, list[str]]:
    if len(definitions) > 16:
        raise ValueError("At most 16 local calculations are supported")
    output = list(columns)
    for definition in definitions:
        calculation = Calculation.model_validate(definition)
        if calculation.name in output:
            raise ValueError("Calculation name conflicts with a selected field")
        expression = expression_sql(calculation.expression, output)
        sql = f"SELECT q.*, {expression} AS `{calculation.name}` FROM (\n{sql}\n) q"
        output.append(calculation.name)
    return sql, output


def is_window(expression: dict) -> bool:
    return expression.get("op") in {"running_total", "moving_average", "rank", "dense_rank"} or any(
        is_window(arg) for arg in expression.get("args", [])
    )


def total_calculations(definitions: tuple[dict, ...], metrics: tuple[str, ...]) -> tuple[dict, ...]:
    """Keep only calculations defined over ungrouped aggregate outputs."""
    available = set(metrics)
    result = []
    for definition in definitions:
        pending = [definition["expression"]]
        fields = set()
        while pending:
            expression = pending.pop()
            if expression.get("op") == "field":
                fields.add(expression["field"])
            pending.extend(expression.get("args", []))
        if not is_window(definition["expression"]) and fields <= available:
            result.append(definition)
            available.add(definition["name"])
    return tuple(result)
