"""Compile validated :class:`SemanticPlan` objects into StarRocks SQL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.modules.agents.semantic.ir import Additivity, SemanticFieldIR, SemanticModelIR
from app.modules.agents.semantic.planning import (
    SemanticGraph,
    SemanticPlan,
    SemanticPlanError,
    validate_plan,
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class UnsafeFanoutError(SemanticPlanError):
    pass


class AdditivityError(SemanticPlanError):
    pass


@dataclass(frozen=True)
class CompiledSemanticQuery:
    sql: str
    model_fingerprint: str
    warnings: tuple[str, ...] = ()
    relationship_path: tuple[str, ...] = ()


class SemanticCompiler:
    def compile(self, model: SemanticModelIR, plan: SemanticPlan) -> CompiledSemanticQuery:
        errors = validate_plan(model, plan)
        if errors:
            raise SemanticPlanError("; ".join(errors))

        metric_objects = [model.metric(name) for name in plan.metrics]
        metrics = [metric for metric in metric_objects if metric is not None]
        dimensions = [model.field(name) for name in plan.dimensions]
        dimension_fields = [field for field in dimensions if field is not None]
        filter_fields = [model.field(item.field) for item in plan.filters]
        time_field = model.field(plan.time.dimension) if plan.time else None

        base_dataset = (
            metrics[0].base_dataset
            if metrics and metrics[0].base_dataset
            else (dimension_fields[0].dataset if dimension_fields else "")
        )
        if not base_dataset:
            raise SemanticPlanError("The plan has no resolvable base dataset.")
        base = model.dataset(base_dataset)
        if base is None:
            raise SemanticPlanError(f"Unknown base dataset {base_dataset!r}.")

        for metric in metrics:
            self._validate_additivity(metric, plan)

        needed_datasets = {
            field.dataset
            for field in [*dimension_fields, *filter_fields, time_field]
            if field is not None and field.dataset != base_dataset
        }
        graph = SemanticGraph(model)
        relationships = []
        warnings: list[str] = []
        for target in sorted(needed_datasets):
            preferred = metrics[0].preferred_relationship_path if metrics else ()
            resolved = graph.path(base_dataset, target, preferred_names=preferred)
            if resolved.ambiguous:
                raise SemanticPlanError(
                    f"More than one relationship path connects {base_dataset!r} to {target!r}; "
                    "set preferred_relationship_path."
                )
            for relationship in resolved.relationships:
                if relationship not in relationships:
                    relationships.append(relationship)
            for metric in metrics:
                self._validate_fanout(metric.base_dataset, resolved.relationships)

        select_parts: list[str] = []
        group_parts: list[str] = []
        if plan.time and time_field is not None and plan.time.compare:
            comparison = _comparison_expression(
                _qualified_expression(time_field), plan.time.range
            )
            select_parts.append(f"{comparison} AS `comparison_period`")
            group_parts.append(comparison)
        if plan.time and time_field is not None and plan.time.grain:
            expression = _qualified_expression(time_field)
            grouped_time = f"DATE_TRUNC('{_safe_grain(plan.time.grain)}', {expression})"
            select_parts.append(f"{grouped_time} AS {_quote(plan.time.dimension)}")
            group_parts.append(grouped_time)
        for field in dimension_fields:
            if plan.time and field.name == plan.time.dimension and plan.time.grain:
                continue
            expression = _qualified_expression(field)
            select_parts.append(f"{expression} AS {_quote(field.name)}")
            group_parts.append(expression)
        for metric in metrics:
            expression = _qualify_metric(metric.expression, metric.base_dataset)
            select_parts.append(f"{expression} AS {_quote(metric.name)}")
        if not select_parts:
            raise SemanticPlanError("The semantic plan produces no projection.")

        joins: list[str] = []
        joined = {base_dataset}
        pending = list(relationships)
        while pending:
            progress = False
            for relationship in list(pending):
                if relationship.from_dataset in joined:
                    left, right = relationship.from_dataset, relationship.to_dataset
                    from_cols, to_cols = relationship.from_columns, relationship.to_columns
                elif relationship.to_dataset in joined:
                    left, right = relationship.to_dataset, relationship.from_dataset
                    from_cols, to_cols = relationship.to_columns, relationship.from_columns
                else:
                    continue
                target_dataset = model.dataset(right)
                if target_dataset is None:
                    raise SemanticPlanError(f"Relationship references unknown dataset {right!r}.")
                join_predicates = [
                    f"{_quote(left)}.{_quote(left_col)} = {_quote(right)}.{_quote(right_col)}"
                    for left_col, right_col in zip(from_cols, to_cols, strict=True)
                ]
                joins.append(
                    f"LEFT JOIN {_quote_source(target_dataset.source)} AS {_quote(right)} ON "
                    + " AND ".join(join_predicates)
                )
                joined.add(right)
                pending.remove(relationship)
                progress = True
            if not progress:
                raise SemanticPlanError(
                    "Relationship path could not be ordered from the base dataset."
                )

        filter_predicates: list[str] = []
        for filter_item in plan.filters:
            filter_field = model.field(filter_item.field)
            assert filter_field is not None
            filter_predicates.append(
                _filter_sql(
                    _qualified_expression(filter_field),
                    filter_item.operator,
                    filter_item.value,
                )
            )
        for name in plan.named_filters:
            named = model.named_filter(name)
            assert named is not None
            filter_predicates.append(f"({_safe_predicate(named.expression)})")
        for metric in metrics:
            for metric_filter in metric.filters:
                named = model.named_filter(metric_filter)
                expression = named.expression if named is not None else metric_filter
                filter_predicates.append(f"({_safe_predicate(expression)})")
        if plan.time and time_field is not None and plan.time.range:
            time_expression = _qualified_expression(time_field)
            filter_predicates.extend(
                _comparison_predicates(time_expression, plan.time.range)
                if plan.time.compare
                else _time_predicates(time_expression, plan.time.range)
            )

        lines = [
            "SELECT",
            "  " + ",\n  ".join(select_parts),
            f"FROM {_quote_source(base.source)} AS {_quote(base_dataset)}",
        ]
        lines.extend(joins)
        if filter_predicates:
            lines.append("WHERE " + "\n  AND ".join(filter_predicates))
        if group_parts and metrics:
            lines.append("GROUP BY " + ", ".join(group_parts))
        if plan.order_by:
            aliases = {metric.name for metric in metrics} | {
                field.name for field in dimension_fields
            }
            if plan.time and plan.time.compare:
                aliases.add("comparison_period")
            order_parts = []
            for order_item in plan.order_by:
                if order_item.field not in aliases:
                    raise SemanticPlanError(
                        f"Order field {order_item.field!r} is not selected."
                    )
                order_parts.append(
                    f"{_quote(order_item.field)} {order_item.direction.upper()}"
                )
            lines.append("ORDER BY " + ", ".join(order_parts))
        if plan.limit:
            lines.append(f"LIMIT {plan.limit}")
        return CompiledSemanticQuery(
            sql="\n".join(lines),
            model_fingerprint=model.fingerprint,
            warnings=tuple(warnings),
            relationship_path=tuple(relationship.name for relationship in relationships),
        )

    @staticmethod
    def _validate_fanout(base_dataset: str, relationships: tuple[Any, ...]) -> None:
        current = base_dataset
        for relationship in relationships:
            cardinality = relationship.cardinality.replace("-", "_").lower()
            if cardinality in {"", "unknown", "many_to_many", "n:n"}:
                raise UnsafeFanoutError(
                    f"Relationship {relationship.name!r} does not prove a fanout-safe "
                    "cardinality for this metric."
                )
            if current == relationship.from_dataset:
                if cardinality in {"one_to_many", "1_to_many", "1:n"}:
                    raise UnsafeFanoutError(
                        f"Metric grain would fan out across relationship {relationship.name!r}."
                    )
                current = relationship.to_dataset
            else:
                # Reverse traversal of many-to-one is a one-to-many fanout.
                if cardinality in {"many_to_one", "many_to_1", "n:1"}:
                    raise UnsafeFanoutError(
                        f"Metric grain would fan out across relationship {relationship.name!r}."
                    )
                current = relationship.from_dataset

    @staticmethod
    def _validate_additivity(metric: Any, plan: SemanticPlan) -> None:
        if (
            metric.additivity == Additivity.NON_ADDITIVE
            and plan.dimensions
            and (
                not metric.allowed_dimensions
                or any(dimension not in metric.allowed_dimensions for dimension in plan.dimensions)
            )
        ):
            raise AdditivityError(
                f"Metric {metric.name!r} is non-additive for the requested dimensions."
            )
        if (
            metric.additivity == Additivity.SEMI_ADDITIVE
            and plan.time
            and plan.time.grain
            and plan.time.dimension not in metric.allowed_dimensions
        ):
            raise AdditivityError(
                f"Metric {metric.name!r} cannot be summed across time grain {plan.time.grain!r}."
            )


def _quote(identifier: str) -> str:
    if not _IDENT.match(identifier):
        raise SemanticPlanError(f"Unsafe semantic identifier {identifier!r}.")
    return f"`{identifier}`"


def _quote_source(source: str) -> str:
    parts = source.split(".")
    if not 1 <= len(parts) <= 3:
        raise SemanticPlanError(f"Invalid dataset source {source!r}.")
    return ".".join(_quote(part) for part in parts)


def _qualified_expression(field: SemanticFieldIR) -> str:
    expression = field.expression.strip()
    if _IDENT.match(expression):
        return f"{_quote(field.dataset)}.{_quote(expression)}"
    return _qualify_metric(expression, field.dataset)


def _qualify_metric(expression: str, dataset: str) -> str:
    # Ossie expressions may already qualify logical dataset names. Bare column
    # references inside aggregate functions are qualified for deterministic SQL.
    text = expression.strip()
    if not text:
        raise SemanticPlanError("A selected semantic expression is empty.")
    if re.search(r"\b(select|from|join|;|--|/\*)\b", text, re.I):
        raise SemanticPlanError("Semantic expressions cannot contain statements or joins.")
    if _IDENT.match(text):
        return f"{_quote(dataset)}.{_quote(text)}"
    return text


def _safe_predicate(expression: str) -> str:
    text = expression.strip()
    if not text or re.search(r"\b(select|from|join|;|--|/\*)\b", text, re.I):
        raise SemanticPlanError("Metric filters must be bounded predicate expressions.")
    return text


def _filter_sql(expression: str, operator: str, value: Any) -> str:
    if operator == "IN":
        if not isinstance(value, list) or not value:
            raise SemanticPlanError("IN filter needs a non-empty list.")
        return f"{expression} IN ({', '.join(_literal(item) for item in value)})"
    return f"{expression} {operator} {_literal(value)}"


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _safe_grain(value: str) -> str:
    grain = value.lower()
    if grain not in {"day", "week", "month", "quarter", "year"}:
        raise SemanticPlanError(f"Unsupported time grain {value!r}.")
    return grain


def _time_predicates(expression: str, range_value: str) -> list[str]:
    if range_value == "current_month":
        return [
            f"{expression} >= DATE_TRUNC('month', CURRENT_DATE())",
            f"{expression} < DATE_ADD(DATE_TRUNC('month', CURRENT_DATE()), INTERVAL 1 MONTH)",
        ]
    if range_value == "previous_month":
        return [
            f"{expression} >= DATE_SUB(DATE_TRUNC('month', CURRENT_DATE()), INTERVAL 1 MONTH)",
            f"{expression} < DATE_TRUNC('month', CURRENT_DATE())",
        ]
    if range_value == "last_30_days":
        return [f"{expression} >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)"]
    raise SemanticPlanError(f"Unsupported time range {range_value!r}.")


def _comparison_expression(expression: str, range_value: str | None) -> str:
    if range_value == "current_month":
        boundary = "DATE_TRUNC('month', CURRENT_DATE())"
    elif range_value == "previous_month":
        boundary = "DATE_SUB(DATE_TRUNC('month', CURRENT_DATE()), INTERVAL 1 MONTH)"
    elif range_value == "last_30_days":
        boundary = "DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)"
    else:
        raise SemanticPlanError("Comparison needs a supported explicit time range.")
    return f"CASE WHEN {expression} >= {boundary} THEN 'current_period' ELSE 'previous_period' END"


def _comparison_predicates(expression: str, range_value: str) -> list[str]:
    if range_value == "current_month":
        return [
            f"{expression} >= DATE_SUB(DATE_TRUNC('month', CURRENT_DATE()), INTERVAL 1 MONTH)",
            f"{expression} < DATE_ADD(DATE_TRUNC('month', CURRENT_DATE()), INTERVAL 1 MONTH)",
        ]
    if range_value == "previous_month":
        return [
            f"{expression} >= DATE_SUB(DATE_TRUNC('month', CURRENT_DATE()), INTERVAL 2 MONTH)",
            f"{expression} < DATE_TRUNC('month', CURRENT_DATE())",
        ]
    if range_value == "last_30_days":
        return [f"{expression} >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)"]
    raise SemanticPlanError(f"Unsupported comparison range {range_value!r}.")
