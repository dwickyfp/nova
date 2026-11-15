"""Wire primitives shared by dataset, chart and dashboard definitions."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


Name = Annotated[str, Field(min_length=1, max_length=256)]
FieldId = Annotated[str, Field(min_length=1, max_length=128)]
Revision = Annotated[int, Field(strict=True, ge=1, le=9_007_199_254_740_991)]
LogicalType = Literal[
    "string", "boolean", "integer", "decimal", "float", "date", "timestamp", "geometry"
]
Permission = Literal["view", "interact", "comment", "edit", "publish", "manage", "export"]
ArtifactKind = Literal["spreadsheet", "whiteboard", "html_report", "chart", "dashboard"]


class StringValue(Contract):
    type: Literal["string"] = "string"
    value: str = Field(max_length=4096)


class IntegerValue(Contract):
    type: Literal["integer"] = "integer"
    # Decimal strings avoid rounding INT64 values in JavaScript.
    value: str = Field(pattern=r"^-?(0|[1-9][0-9]{0,18})$", max_length=20)

    @field_validator("value")
    @classmethod
    def int64(cls, value: str) -> str:
        if not -(2**63) <= int(value) < 2**63:
            raise ValueError("Integer exceeds signed 64-bit range")
        return value


class DecimalValue(Contract):
    type: Literal["decimal"] = "decimal"
    value: str = Field(pattern=r"^-?(0|[1-9][0-9]{0,37})(\.[0-9]{1,38})?$", max_length=78)

    @field_validator("value")
    @classmethod
    def decimal128(cls, value: str) -> str:
        number = Decimal(value)
        if len(number.as_tuple().digits) > 38:
            raise ValueError("Decimal exceeds 38 digits of precision")
        return value


class BooleanValue(Contract):
    type: Literal["boolean"] = "boolean"
    value: bool = Field(strict=True)


class FloatValue(Contract):
    type: Literal["float"] = "float"
    value: float = Field(strict=True)

    @field_validator("value", mode="before")
    @classmethod
    def binary_number(cls, value: object) -> object:
        if type(value) is float:
            return value
        if type(value) is int:
            try:
                converted = float(value)
                if int(converted) == value:
                    return converted
            except (ValueError, OverflowError):
                pass
        raise ValueError("Floating values require a finite binary number")


class DateValue(Contract):
    type: Literal["date"] = "date"
    value: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

    @field_validator("value")
    @classmethod
    def calendar_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value


class TimestampValue(Contract):
    type: Literal["timestamp"] = "timestamp"
    value: str = Field(
        min_length=20, max_length=40,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$",
    )

    @field_validator("value")
    @classmethod
    def zoned_timestamp(cls, value: str) -> str:
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("Timestamp requires an explicit UTC offset")
        return value


Scalar = Annotated[
    StringValue | IntegerValue | DecimalValue | FloatValue
    | BooleanValue | DateValue | TimestampValue,
    Field(discriminator="type"),
]


class NumberFormat(Contract):
    kind: Literal["number", "currency", "percent", "scientific"] = "number"
    decimals: int = Field(default=2, strict=True, ge=0, le=15)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    locale: str = Field(default="en-US", pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$")

    @model_validator(mode="after")
    def currency_required(self) -> Self:
        if self.kind == "currency" and self.currency is None:
            raise ValueError("Currency formatting requires an explicit currency")
        return self
