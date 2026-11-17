"""Dataset declarations and transient typed values share one validation boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parameters import BoundParameters, ParameterSpec, ParameterValue, ScalarValue

from .common import BooleanValue, Contract, Scalar, StringValue

ParameterId = str


def _value(value: Scalar) -> ScalarValue:
    if value.type == "integer":
        return int(value.value)
    if value.type == "decimal":
        return Decimal(value.value)
    if value.type == "date":
        return date.fromisoformat(value.value)
    if value.type == "timestamp":
        return datetime.fromisoformat(value.value)
    if isinstance(value, StringValue | BooleanValue):
        return value.value
    raise ValueError("This type is not supported by dataset parameters")


def _ordered(value: Scalar) -> Decimal | datetime:
    if value.type in {"integer", "decimal"}:
        return Decimal(value.value)
    if value.type == "date":
        return datetime.combine(date.fromisoformat(value.value), datetime.min.time(), UTC)
    if value.type == "timestamp":
        return datetime.fromisoformat(value.value)
    raise ValueError("Parameter ranges require a number, date or timestamp")


def _compare(left: Scalar, right: Scalar) -> int:
    a, b = _ordered(left), _ordered(right)
    if isinstance(a, Decimal) and isinstance(b, Decimal):
        return (a > b) - (a < b)
    if isinstance(a, datetime) and isinstance(b, datetime):
        return (a > b) - (a < b)
    raise ValueError("Parameter range types must agree")


class Parameter(Contract):
    parameter_id: ParameterId = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    type: Literal["string", "boolean", "integer", "decimal", "date", "timestamp", "enum"]
    required: bool = Field(default=True, strict=True)
    nullable: bool = Field(default=False, strict=True)
    sensitive: bool = Field(default=False, strict=True)
    multiple: bool = Field(default=False, strict=True)
    precision: int | None = Field(default=None, strict=True, ge=1, le=38)
    scale: int | None = Field(default=None, strict=True, ge=0, le=38)
    defaults: tuple[Scalar, ...] = Field(default=(), max_length=500)
    allowed: tuple[Scalar, ...] = Field(default=(), max_length=500)
    minimum: Scalar | None = None
    maximum: Scalar | None = None

    def specification(self) -> ParameterSpec:
        return ParameterSpec(
            self.parameter_id, "string" if self.type == "enum" else self.type,
            nullable=self.nullable, multiple=self.multiple,
            precision=self.precision, scale=self.scale,
        )

    def _decode(self, values: tuple[Scalar | None, ...]) -> tuple[ScalarValue, ...]:
        expected = "string" if self.type == "enum" else self.type
        if any(value is not None and value.type != expected for value in values):
            raise ValueError("Parameter values do not match its declared type")
        converted = tuple(_value(value) if value is not None else None for value in values)
        try:
            BoundParameters(
                (replace(self.specification(), multiple=True),),
                ((self.parameter_id, converted),),
            )
        except SemanticError:
            raise ValueError("Parameter values exceed their declared type or bounds") from None
        return converted

    def checked_values(self, values: tuple[Scalar | None, ...]) -> ParameterValue:
        if not self.multiple and len(values) != 1:
            raise ValueError("Scalar parameters require exactly one value")
        converted = self._decode(values)
        allowed = tuple(_value(value) for value in self.allowed)
        for wire, value in zip(values, converted, strict=True):
            if wire is None:
                continue
            if allowed and value not in allowed:
                raise ValueError("Parameter value is outside the allowed values")
            if self.minimum is not None and _compare(wire, self.minimum) < 0:
                raise ValueError("Parameter value is below its minimum")
            if self.maximum is not None and _compare(wire, self.maximum) > 0:
                raise ValueError("Parameter value is above its maximum")
        return converted if self.multiple else converted[0]

    @model_validator(mode="after")
    def compatible_values(self) -> Self:
        try:
            self.specification()
        except SemanticError:
            raise ValueError("Invalid parameter type, precision or scale declaration") from None
        if self.type == "enum" and not self.allowed:
            raise ValueError("Enum parameters require allowed values")
        if self.sensitive and (self.defaults or self.allowed):
            raise ValueError(
                "Sensitive parameter defaults and allowed values must not be persisted"
            )
        if self.sensitive and (self.minimum is not None or self.maximum is not None):
            raise ValueError("Sensitive parameter ranges must not persist private values")
        if not self.required and not self.defaults and not self.nullable:
            raise ValueError("Optional parameters require a default or nullable value")
        if self.allowed:
            allowed = self._decode(self.allowed)
            if len(set(allowed)) != len(allowed):
                raise ValueError("Allowed parameter values must be distinct")
        for bound in (self.minimum, self.maximum):
            if bound is not None:
                self._decode((bound,))
                _ordered(bound)
        if (self.minimum is not None and self.maximum is not None
                and _compare(self.minimum, self.maximum) > 0):
            raise ValueError("Parameter minimum exceeds its maximum")
        if self.allowed:
            for allowed_value in self.allowed:
                self.checked_values((allowed_value,))
        if self.defaults:
            self.checked_values(self.defaults)
        return self


class ParameterInput(Contract):
    parameter_id: ParameterId = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    values: tuple[Scalar | None, ...] = Field(min_length=1, max_length=500, repr=False)
