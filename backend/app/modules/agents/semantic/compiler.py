"""Compile validated :class:`SemanticPlan` objects into StarRocks SQL."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Any

from app.modules.agents.semantic.ir import Additivity, SemanticFieldIR, SemanticModelIR
from app.modules.agents.semantic.planning import (
    SemanticGraph,
    SemanticOrder,
    SemanticPlan,
    SemanticPlanError,
    validate_plan,
)
from app.modules.agents.semantic.time_ranges import (
    ExecutionTimeContext,
    comparison_bounds,
    range_predicates,
    resolve_time_range,
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class UnsafeFanoutError(SemanticPlanError):
    pass


class AdditivityError(SemanticPlanError):
    pass


class MultiFactCompilationError(SemanticPlanError):
    pass


@dataclass(frozen=True)
class CompiledSemanticQuery:
    sql: str
    model_fingerprint: str
    warnings: tuple[str, ...] = ()
    relationship_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Core:
    """An aggregated query without ORDER BY/LIMIT, and its output columns."""

    sql: str
    columns: tuple[str, ...]
    relationships: tuple[str, ...]
    warnings: tuple[str, ...]


class SemanticCompiler:
    def compile(
        self, model: SemanticModelIR, plan: SemanticPlan, *,
        time_context: ExecutionTimeContext | None = None,
    ) -> CompiledSemanticQuery:
        if time_context and (
            plan.time is None or plan.time.range != time_context.range
            or plan.time.compare != time_context.comparison
        ):
            raise SemanticPlanError("Execution time context does not match the validated plan.")
        errors = validate_plan(model, plan)
        if errors:
            raise SemanticPlanError("; ".join(errors))
        metrics = [metric for name in plan.metrics if (metric := model.metric(name))]
        if len({metric.base_dataset for metric in metrics if metric.base_dataset}) > 1:
            core = self._drill_across(model, plan, time_context=time_context)
        else:
            core = self._single(model, plan, time_context=time_context)
        sql, columns = core.sql, list(core.columns)
        if plan.having or plan.transforms or plan.top_n_per_group:
            sql, columns = _wrap(sql, columns, plan)
        lines = [sql]
        order_by = plan.order_by
        if plan.limit and not order_by and plan.metrics:
            # LIMIT without ORDER BY returns arbitrary rows; a limit means "the top".
            order_by = (SemanticOrder(plan.metrics[0]),)
        if order_by:
            order_parts = []
            for order_item in order_by:
                if order_item.field not in columns:
                    raise SemanticPlanError(f"Order field {order_item.field!r} is not selected.")
                order_parts.append(
                    f"{_quote_output(order_item.field)} {order_item.direction.upper()}"
                )
            lines.append("ORDER BY " + ", ".join(order_parts))
        if plan.limit:
            lines.append(f"LIMIT {plan.limit}")
        return CompiledSemanticQuery(
            sql="\n".join(lines),
            model_fingerprint=model.fingerprint,
            warnings=tuple(dict.fromkeys((*core.warnings, *(time_context.warnings
                                                        if time_context else ())))),
            relationship_path=core.relationships,
        )

    def _drill_across(
        self, model: SemanticModelIR, plan: SemanticPlan, *,
        time_context: ExecutionTimeContext | None = None,
    ) -> _Core:
        """Metrics of several fact datasets, each aggregated alone, joined on shared keys.

        Each fact is compiled as its own single-fact query at the requested grain,
        so no fact is fanned out by another. The results are joined on the
        requested dimensions, which must be reachable from every fact directly or
        through a declared conformed dimension.
        """
        if plan.time and plan.time.compare:
            raise MultiFactCompilationError(
                "Period comparison across metrics of different fact datasets is not supported."
            )
        groups: dict[str, list[str]] = {}
        for name in plan.metrics:
            metric = model.metric(name)
            assert metric is not None
            groups.setdefault(metric.base_dataset, []).append(name)
        graph = SemanticGraph(model)

        def reach(base: str, field_name: str) -> str | None:
            candidates = [
                field_name,
                *(
                    member
                    for group in model.conformed_dimensions
                    if field_name in group
                    for member in group
                    if member != field_name
                ),
            ]
            for candidate in candidates:
                field = model.field(candidate)
                if field is None:
                    continue
                if field.dataset == base:
                    return candidate
                try:
                    path = graph.path(base, field.dataset)
                except SemanticPlanError:
                    continue
                if not path.ambiguous:
                    return candidate
            return None

        keys = (["period"] if plan.time and plan.time.grain else []) + list(plan.dimensions)
        cores: list[tuple[str, _Core, list[str]]] = []
        for index, (base, names) in enumerate(groups.items()):
            mapped_dimensions, alias_map = [], {}
            for name in plan.dimensions:
                target = reach(base, name)
                if target is None:
                    raise MultiFactCompilationError(
                        f"Dimension {name!r} is not shared with {base!r}. Declare it in "
                        "conformed_dimensions to compare these metrics by it."
                    )
                mapped_dimensions.append(target)
                alias_map[target] = name
            mapped_filters = []
            for item in plan.filters:
                target = reach(base, item.field)
                if target is None:
                    raise MultiFactCompilationError(
                        f"Filter field {item.field!r} does not apply to {base!r}."
                    )
                mapped_filters.append(replace(item, field=target))
            named = []
            for name in plan.named_filters:
                named_filter = model.named_filter(name)
                if named_filter is None or (
                    named_filter.dataset
                    and reach(base, _any_field(model, named_filter.dataset)) is None
                ):
                    raise MultiFactCompilationError(
                        f"Named filter {name!r} does not apply to {base!r}."
                    )
                named.append(name)
            time = None
            if plan.time:
                metric = model.metric(names[0])
                dimension = (metric.default_time_dimension if metric else None) or next(
                    (field.name for field in _dataset_fields(model, base) if field.is_time), None
                )
                if dimension is None:
                    raise MultiFactCompilationError(f"{base!r} has no time dimension.")
                time = replace(plan.time, dimension=dimension)
                alias_map[dimension] = "period"
            sub_plan = SemanticPlan(
                metrics=tuple(names),
                dimensions=tuple(mapped_dimensions),
                filters=tuple(mapped_filters),
                named_filters=tuple(named),
                time=time,
            )
            cores.append((f"f{index}", self._single(
                model, sub_plan, alias_map=alias_map, time_context=time_context
            ), names))

        ctes = ",\n".join(f"{alias} AS (\n{core.sql}\n)" for alias, core, _names in cores)
        select = []
        for key in keys:
            select.append(
                "COALESCE("
                + ", ".join(f"{alias}.{_quote_output(key)}" for alias, _c, _n in cores)
                + f") AS {_quote_output(key)}"
            )
        for alias, _core, names in cores:
            select.extend(f"{alias}.{_quote(name)} AS {_quote(name)}" for name in names)
        first = cores[0][0]
        body = f"FROM {first}"
        for position, (alias, _core, _names) in enumerate(cores[1:], start=1):
            if not keys:
                body += f"\nCROSS JOIN {alias}"
                continue
            previous = [item[0] for item in cores[:position]]
            conditions = [
                (
                    f"COALESCE({', '.join(f'{p}.{_quote_output(key)}' for p in previous)})"
                    if len(previous) > 1
                    else f"{previous[0]}.{_quote_output(key)}"
                )
                + f" <=> {alias}.{_quote_output(key)}"
                for key in keys
            ]
            body += f"\nFULL OUTER JOIN {alias} ON " + " AND ".join(conditions)
        sql = f"WITH {ctes}\nSELECT\n  " + ",\n  ".join(select) + "\n" + body
        return _Core(
            sql=sql,
            columns=(*keys, *plan.metrics),
            relationships=tuple(
                name for _alias, core, _names in cores for name in core.relationships
            ),
            warnings=tuple(warning for _alias, core, _names in cores for warning in core.warnings),
        )

    def _single(
        self,
        model: SemanticModelIR,
        plan: SemanticPlan,
        *,
        alias_map: dict[str, str] | None = None,
        time_context: ExecutionTimeContext | None = None,
    ) -> _Core:
        errors = validate_plan(model, plan)
        if errors:
            raise SemanticPlanError("; ".join(errors))
        alias_map = alias_map or {}

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

        metric_datasets = {metric.base_dataset for metric in metrics if metric.base_dataset}
        if len(metric_datasets) > 1:
            raise MultiFactCompilationError(
                "Metrics span multiple base datasets. Nova will not join fact metrics "
                "without a proven shared aggregation grain."
            )

        needed_datasets = {
            field.dataset
            for field in [*dimension_fields, *filter_fields, time_field]
            if field is not None and field.dataset != base_dataset
        }
        needed_datasets.update(metric_datasets - {base_dataset})
        from app.modules.agents.semantic.derived import compile_metric_expression
        from app.modules.agents.semantic.expressions import referenced_datasets

        for metric in metrics:
            for expression in (compile_metric_expression(model, metric), *metric.filters):
                needed_datasets.update(
                    referenced_datasets(expression, metric.base_dataset) - {base_dataset}
                )
        for name in plan.named_filters:
            named = model.named_filter(name)
            if named and named.dataset and named.dataset != base_dataset:
                needed_datasets.add(named.dataset)
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
                _qualified_expression(time_field), plan.time.range, plan.time.compare, time_context
            )
            select_parts.append(f"{comparison} AS `comparison_period`")
            group_parts.append(comparison)
            if plan.time.range and resolve_time_range(plan.time.range).in_progress:
                warnings.append(
                    f"{plan.time.range} is still in progress, so both periods cover the "
                    "same days to date; the previous period is not a full period."
                )
        if plan.time and time_field is not None and plan.time.grain:
            expression = _qualified_expression(time_field)
            grouped_time = f"DATE_TRUNC('{_safe_grain(plan.time.grain)}', {expression})"
            time_alias = alias_map.get(plan.time.dimension, plan.time.dimension)
            select_parts.append(f"{grouped_time} AS {_quote_output(time_alias)}")
            group_parts.append(grouped_time)
        for requested, field in zip(plan.dimensions, dimensions, strict=True):
            if field is None:
                continue
            if plan.time and field == time_field and plan.time.grain:
                continue
            expression = _qualified_expression(field)
            select_parts.append(
                f"{expression} AS {_quote_output(alias_map.get(requested, requested))}"
            )
            group_parts.append(expression)
        for metric in metrics:
            expression = compile_metric_expression(model, metric)
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
                    f"{_relationship_expression(model, left, left_col)} = "
                    f"{_relationship_expression(model, right, right_col)}"
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
            filter_predicates.append(
                f"({_qualify_expression(named.expression, named.dataset or base_dataset)})"
            )
        for metric in metrics:
            for metric_filter in metric.filters:
                named = model.named_filter(metric_filter)
                expression = named.expression if named is not None else metric_filter
                dataset = named.dataset if named is not None else metric.base_dataset
                filter_predicates.append(
                    f"({_qualify_expression(expression, dataset or metric.base_dataset)})"
                )
        if plan.time and time_field is not None and plan.time.range:
            time_expression = _qualified_expression(time_field)
            filter_predicates.extend(
                _comparison_predicates(
                    time_expression, plan.time.range, plan.time.compare, time_context
                )
                if plan.time.compare
                else _time_predicates(time_expression, plan.time.range, time_context)
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
        columns: list[str] = []
        if plan.time and plan.time.compare:
            columns.append("comparison_period")
        if plan.time and plan.time.grain and time_field is not None:
            columns.append(alias_map.get(plan.time.dimension, plan.time.dimension))
        columns.extend(
            alias_map.get(requested, requested)
            for requested, field in zip(plan.dimensions, dimensions, strict=True)
            if field is not None and not (plan.time and field == time_field and plan.time.grain)
        )
        columns.extend(metric.name for metric in metrics)
        return _Core(
            sql="\n".join(lines),
            columns=tuple(columns),
            relationships=tuple(relationship.name for relationship in relationships),
            warnings=tuple(warnings),
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
            and (
                plan.time.dimension in metric.non_additive_dimensions
                or (
                    not metric.non_additive_dimensions
                    and plan.time.dimension not in metric.allowed_dimensions
                )
            )
        ):
            raise AdditivityError(
                f"Metric {metric.name!r} cannot be summed across time grain {plan.time.grain!r}."
            )


def _dataset_fields(model: SemanticModelIR, dataset: str) -> tuple[SemanticFieldIR, ...]:
    target = model.dataset(dataset)
    return target.fields if target else ()


def _any_field(model: SemanticModelIR, dataset: str) -> str:
    target = model.dataset(dataset)
    return target.fields[0].name if target and target.fields else ""


def _wrap(sql: str, columns: list[str], plan: SemanticPlan) -> tuple[str, list[str]]:
    """Window calculations, metric conditions, and top-N per group over an aggregate.

    Windows are computed over every aggregated row first; ``having`` and the
    top-N filter apply afterwards, so a share is a share of the full total.
    """
    compare = bool(plan.time and plan.time.compare)
    time_column = next(
        (column for column in columns if plan.time and column in {plan.time.dimension, "period"}),
        None,
    )
    other_dimensions = [
        column for column in columns if column in plan.dimensions and column != time_column
    ]
    windows: list[str] = []
    added: list[str] = []
    for transform in plan.transforms:
        metric = _quote(transform.metric)
        partition = "PARTITION BY q.`comparison_period`" if compare else ""
        if transform.kind == "share_of_total":
            name = f"{transform.metric}_share_pct"
            expression = f"100.0 * q.{metric} / NULLIF(SUM(q.{metric}) OVER ({partition}), 0)"
        elif transform.kind == "rank":
            name = f"{transform.metric}_rank"
            if plan.top_n_per_group is not None:
                # Ranked within the same groups the top-N keeps.
                partition = "PARTITION BY " + ", ".join(
                    f"q.{_quote_output(column)}" for column in plan.top_n_per_group.partition_by
                )
            prefix = partition + " " if partition else ""
            expression = f"RANK() OVER ({prefix}ORDER BY q.{metric} DESC)"
        else:
            name = f"{transform.metric}_running_total"
            partition_by = ", ".join(f"q.{_quote_output(column)}" for column in other_dimensions)
            partition = f"PARTITION BY {partition_by} " if partition_by else ""
            expression = (
                f"SUM(q.{metric}) OVER ({partition}ORDER BY q.{_quote_output(str(time_column))} "
                "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
            )
        windows.append(f"{expression} AS {_quote(name)}")
        added.append(name)
    if plan.top_n_per_group is not None:
        top = plan.top_n_per_group
        partition = ", ".join(f"q.{_quote_output(column)}" for column in top.partition_by)
        windows.append(
            f"ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY q.{_quote(top.metric)} DESC)"
            " AS `_nova_row`"
        )
    inner_select = ", ".join([*(f"q.{_quote_output(column)}" for column in columns), *windows])
    conditions = [
        f"w.{_quote(item.metric)} {item.operator} {_literal(item.value)}" for item in plan.having
    ]
    if plan.top_n_per_group is not None:
        conditions.append(f"w.`_nova_row` <= {int(plan.top_n_per_group.n)}")
    output = [*columns, *added]
    wrapped = (
        "SELECT "
        + ", ".join(f"w.{_quote_output(column)}" for column in output)
        + f"\nFROM (\nSELECT {inner_select}\nFROM (\n{sql}\n) AS q\n) AS w"
    )
    if conditions:
        wrapped += "\nWHERE " + " AND ".join(conditions)
    return wrapped, output


def _quote_output(identifier: str) -> str:
    # Qualified semantic time names are one output alias, not SQL qualification.
    if not all(_IDENT.fullmatch(part) for part in identifier.split(".")):
        raise SemanticPlanError(f"Unsafe semantic output identifier {identifier!r}.")
    return f"`{identifier}`"


def _quote(identifier: str) -> str:
    if not _IDENT.match(identifier):
        raise SemanticPlanError(f"Unsafe semantic identifier {identifier!r}.")
    return f"`{identifier}`"


def quote_semantic_identifier(identifier: str) -> str:
    return _quote(identifier)


def _quote_source(source: str) -> str:
    parts = source.split(".")
    if not 1 <= len(parts) <= 3:
        raise SemanticPlanError(f"Invalid dataset source {source!r}.")
    return ".".join(_quote(part) for part in parts)


def quote_semantic_source(source: str) -> str:
    return _quote_source(source)


def _qualified_expression(field: SemanticFieldIR) -> str:
    expression = field.expression.strip()
    if _IDENT.match(expression):
        return f"{_quote(field.dataset)}.{_quote(expression)}"
    return _qualify_expression(expression, field.dataset)


def _qualify_metric(expression: str, dataset: str) -> str:
    return _qualify_expression(expression, dataset)


def _qualify_expression(expression: str, dataset: str) -> str:
    from app.modules.agents.semantic.expressions import qualify_expression

    return qualify_expression(expression, dataset)


def _relationship_expression(model: SemanticModelIR, dataset: str, field_name: str) -> str:
    dataset_ir = model.dataset(dataset)
    if dataset_ir is None:
        raise SemanticPlanError(f"Relationship references unknown dataset {dataset!r}.")
    field = dataset_ir.field(field_name)
    if field is None:
        raise SemanticPlanError(
            f"Relationship field {dataset}.{field_name} is not defined in the semantic model."
        )
    return _qualified_expression(field)


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
        if isinstance(value, float) and not math.isfinite(value):
            raise SemanticPlanError("Numeric filters must be finite.")
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def _safe_grain(value: str) -> str:
    grain = value.lower()
    if grain not in {"day", "week", "month", "quarter", "year"}:
        raise SemanticPlanError(f"Unsupported time grain {value!r}.")
    return grain


def _time_predicates(
    expression: str, range_value: str, time_context: ExecutionTimeContext | None = None,
) -> list[str]:
    if time_context:
        start, end = time_context.current.sql_bounds()
        return [f"{expression} >= {start}", f"{expression} < {end}"]
    return range_predicates(expression, range_value)


def _comparison_sql_bounds(time_context, range_value, comparison):
    if time_context:
        if time_context.range != range_value or time_context.comparison != comparison:
            raise SemanticPlanError("Execution time context does not match the validated plan.")
        if not time_context.baseline:
            raise SemanticPlanError("Comparison has no pinned baseline.")
        return (*time_context.current.sql_bounds(), *time_context.baseline.sql_bounds())
    return comparison_bounds(range_value, comparison)


def _comparison_expression(
    expression: str, range_value: str | None, comparison: str | None,
    time_context: ExecutionTimeContext | None = None,
) -> str:
    current_start, _current_end, _prior_start, _prior_end = _comparison_sql_bounds(
        time_context, range_value, comparison)
    return (
        f"CASE WHEN {expression} >= {current_start} "
        "THEN 'current_period' ELSE 'previous_period' END"
    )


def _comparison_predicates(
    expression: str, range_value: str, comparison: str | None,
    time_context: ExecutionTimeContext | None = None,
) -> list[str]:
    current_start, current_end, prior_start, prior_end = _comparison_sql_bounds(
        time_context, range_value, comparison)
    return [
        f"(({expression} >= {current_start} AND {expression} < {current_end}) OR "
        f"({expression} >= {prior_start} AND {expression} < {prior_end}))"
    ]
