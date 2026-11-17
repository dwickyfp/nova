"""Scoped filter expressions preserve independent predicates on the same field."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from .common import Contract, FieldId, Name, Revision, Scalar


class FieldBinding(Contract):
    dataset_id: UUID
    dataset_revision: Revision
    field_id: FieldId


class ParameterBinding(Contract):
    dataset_id: UUID
    dataset_revision: Revision
    parameter_id: FieldId


class RelativeWindow(Contract):
    unit: Literal["day", "week", "month", "quarter", "year"]
    count: int = Field(default=1, strict=True, ge=1, le=1000)
    mode: Literal["previous", "rolling", "to_date"] = "rolling"
    timezone: str = Field(default="UTC", max_length=64)
    week_start: Literal["iso", "sunday"] = "iso"
    fiscal_start_month: int = Field(default=1, strict=True, ge=1, le=12)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Unknown time zone") from None
        return value


class Predicate(Contract):
    type: Literal["predicate"] = "predicate"
    field: FieldBinding
    op: Literal[
        "eq", "neq", "in", "not_in", "lt", "lte", "gt", "gte", "is_null",
        "is_not_null", "between", "like_literal", "relative_date",
    ]
    values: tuple[Scalar, ...] = Field(default=(), max_length=500)
    relative: RelativeWindow | None = None
    source: Literal["global", "page", "widget", "chart", "cross_filter"] = "chart"
    stage: Literal["row", "aggregate", "post_aggregate"] = "row"

    @model_validator(mode="after")
    def operand_shape(self) -> Self:
        arity = {
            "is_null": 0, "is_not_null": 0, "relative_date": 0, "between": 2,
        }.get(self.op, 1)
        if self.op in {"in", "not_in"}:
            if not self.values:
                raise ValueError("A member filter needs at least one value")
        elif len(self.values) != arity:
            raise ValueError("Incorrect number of filter operands")
        if (self.op == "relative_date") != (self.relative is not None):
            raise ValueError("Only relative date predicates accept a relative window")
        if len({value.type for value in self.values}) > 1:
            raise ValueError("Filter operands must have one declared type")
        if self.op == "like_literal" and self.values[0].type != "string":
            raise ValueError("Text matching requires a string")
        return self


class And(Contract):
    type: Literal["and"] = "and"
    children: tuple[Expression, ...] = Field(min_length=1, max_length=32)


class Or(Contract):
    type: Literal["or"] = "or"
    children: tuple[Expression, ...] = Field(min_length=1, max_length=32)


class Not(Contract):
    type: Literal["not"] = "not"
    child: Expression


Expression = Annotated[Predicate | And | Or | Not, Field(discriminator="type")]
And.model_rebuild()
Or.model_rebuild()
Not.model_rebuild()


class FilterTree(Contract):
    expression: Expression

    @field_validator("expression", mode="before")
    @classmethod
    def bounded_tree(cls, value: Any) -> Any:
        pending = [(value, 1)]
        nodes = 0
        while pending:
            node, depth = pending.pop()
            nodes += 1
            if nodes > 128 or depth > 8:
                raise ValueError("Filter exceeds 128 nodes or 8 levels")
            if isinstance(node, dict):
                children = node.get("children", ())
                if isinstance(children, (list, tuple)):
                    pending.extend((child, depth + 1) for child in children)
                if "child" in node:
                    pending.append((node["child"], depth + 1))
            elif isinstance(node, (And, Or)):
                pending.extend((child, depth + 1) for child in node.children)
            elif isinstance(node, Not):
                pending.append((node.child, depth + 1))
        return value


class FilterScope(Contract):
    kind: Literal["global", "page", "widget_subset", "chart_local"]
    page_id: UUID | None = None
    widget_ids: tuple[UUID, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def scope_shape(self) -> Self:
        if (self.kind == "page") != (self.page_id is not None):
            raise ValueError("Page scope requires exactly one page")
        if self.kind in {"widget_subset", "chart_local"}:
            if not self.widget_ids or len(set(self.widget_ids)) != len(self.widget_ids):
                raise ValueError("Widget scope requires distinct targets")
            if self.kind == "chart_local" and len(self.widget_ids) != 1:
                raise ValueError("Chart-local scope requires one widget")
        elif self.widget_ids:
            raise ValueError("Global/page scope cannot carry a widget subset")
        return self


class FieldMapping(Contract):
    source: FieldBinding
    target: FieldBinding
    # Cross-dataset mappings are authored explicitly, never inferred by label.
    transform: Literal["identity"] = "identity"


class FilterDefinition(Contract):
    filter_id: UUID
    title: Name
    kind: Literal[
        "single_value", "multi_value", "date_picker", "date_range", "text_entry",
        "number_range_slider", "relative_date", "boolean_switch", "parameter_control",
    ]
    field: FieldBinding | None = None
    parameter: ParameterBinding | None = None
    scope: FilterScope
    mappings: tuple[FieldMapping, ...] = Field(default=(), max_length=100)
    defaults: tuple[Scalar, ...] = Field(default=(), max_length=500)
    relative: RelativeWindow | None = None
    allow_all: bool = True
    include_null: bool = False
    searchable: bool = True
    depends_on: tuple[UUID, ...] = Field(default=(), max_length=63)

    @model_validator(mode="after")
    def control_shape(self) -> Self:
        is_parameter = self.kind == "parameter_control"
        if (is_parameter != (self.parameter is not None)
                or is_parameter == (self.field is not None)):
            raise ValueError("Bind a field filter or a dataset parameter, never both")
        if is_parameter and self.mappings:
            raise ValueError("Parameter controls cannot carry field mappings")
        if (self.kind == "relative_date") != (self.relative is not None):
            raise ValueError("Relative date controls require a window")
        if any(mapping.source != self.field for mapping in self.mappings):
            raise ValueError("A filter mapping must start from its source field")
        if len({mapping.target for mapping in self.mappings}) != len(self.mappings):
            raise ValueError("A filter may map to each target field only once")
        if self.filter_id in self.depends_on or len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("Cascading filters need distinct parent filters")
        if (self.kind in {"single_value", "date_picker", "text_entry", "boolean_switch"}
                and len(self.defaults) > 1):
            raise ValueError("This control accepts at most one default value")
        if self.kind in {"date_range", "number_range_slider"} and len(self.defaults) not in {0, 2}:
            raise ValueError("A range default requires both bounds")
        if self.kind == "relative_date" and self.defaults:
            raise ValueError("Relative dates derive their value from the window")
        types = {value.type for value in self.defaults}
        if len(types) > 1:
            raise ValueError("Control defaults must have one declared type")
        allowed_types = {
            "date_picker": {"date", "timestamp"}, "date_range": {"date", "timestamp"},
            "text_entry": {"string"}, "boolean_switch": {"boolean"},
            "number_range_slider": {"integer", "decimal", "float"},
        }.get(self.kind)
        if allowed_types is not None and not types <= allowed_types:
            raise ValueError("Default value type does not match the control")
        return self
