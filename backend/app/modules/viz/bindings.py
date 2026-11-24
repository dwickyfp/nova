"""Validate saved field identities against the exact referenced dataset revision."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from uuid import UUID

from .contracts.charts import (
    ChartDefinition,
    Dimension,
    Measure,
    ReferenceBand,
    ReferenceLine,
    TableCalculation,
    TopN,
)
from .contracts.common import Contract
from .contracts.dashboards import DashboardDefinition
from .contracts.datasets import DatasetDefinition, DatasetField, Metric, Parameter, Relationship
from .contracts.filters import (
    FieldBinding,
    FieldMapping,
    FilterDefinition,
    ParameterBinding,
    Predicate,
)


def _nodes(value: object) -> Iterator[Contract]:
    if isinstance(value, Contract):
        yield value
        for name in type(value).model_fields:
            yield from _nodes(getattr(value, name))
    elif isinstance(value, tuple):
        for item in value:
            yield from _nodes(item)


def document_bindings(document: Contract) -> Iterator[FieldBinding | ParameterBinding]:
    for node in _nodes(document):
        if isinstance(node, (FieldBinding, ParameterBinding)):
            yield node


def validate_control_binding(
    binding: FieldBinding | ParameterBinding, dataset: DatasetDefinition,
) -> None:
    if binding.dataset_id != dataset.dataset_id:
        raise ValueError("Control source does not match the dataset")
    if isinstance(binding, ParameterBinding):
        if binding.parameter_id not in {item.parameter_id for item in dataset.parameters}:
            raise ValueError("Control references an undeclared dataset parameter")
    elif binding.field_id not in {
        *(field.field_id for field in dataset.fields),
        *(str(metric.metric_id) for metric in dataset.metrics),
    }:
        raise ValueError("Control references an unknown dataset field")


def validate_document_controls(
    document: Contract, datasets: Mapping[tuple[UUID, int], DatasetDefinition],
) -> None:
    def field(binding: FieldBinding) -> DatasetField | Metric:
        source = datasets[(binding.dataset_id, binding.dataset_revision)]
        validate_control_binding(binding, source)
        fields: dict[str, DatasetField | Metric] = {
            **{item.field_id: item for item in source.fields},
            **{str(item.metric_id): item for item in source.metrics},
        }
        return fields[binding.field_id]

    for node in _nodes(document):
        if isinstance(node, Predicate):
            source = field(node.field)
            level = source.level if isinstance(source, Metric) else "row"
            if node.stage != level:
                raise ValueError("Filter stage must match its field's evaluation level")
            if isinstance(source, DatasetField):
                if node.op == "relative_date" and source.logical_type not in {"date", "timestamp"}:
                    raise ValueError("Relative dates require a temporal field")
                if any(value.type != source.logical_type for value in node.values):
                    raise ValueError("Filter operands must match the bound field type")
        elif isinstance(node, FieldMapping):
            left, right = field(node.source), field(node.target)
            if isinstance(left, Metric) or isinstance(right, Metric):
                raise ValueError("Field mappings require source fields, not aggregated metrics")
            if (left.logical_type, left.timezone, left.unit, left.currency) != (
                right.logical_type, right.timezone, right.unit, right.currency,
            ):
                raise ValueError("Mapped fields require matching types, timezones and units")
        elif isinstance(node, FilterDefinition):
            if node.parameter is not None:
                binding = node.parameter
                dataset = datasets[(binding.dataset_id, binding.dataset_revision)]
                validate_control_binding(binding, dataset)
                parameter = next(item for item in dataset.parameters
                                 if item.parameter_id == binding.parameter_id)
                payload = parameter.model_dump()
                payload["defaults"] = [value.model_dump() for value in node.defaults]
                Parameter.model_validate(payload)
                if node.include_null and not parameter.nullable:
                    raise ValueError("This parameter does not accept null")
            elif node.field is not None:
                source = field(node.field)
                if isinstance(source, Metric):
                    raise ValueError("Dashboard field controls require a source field")
                if any(value.type != source.logical_type for value in node.defaults):
                    raise ValueError("Control defaults must match the bound field type")
                allowed = {
                    "date_picker": {"date", "timestamp"},
                    "date_range": {"date", "timestamp"},
                    "relative_date": {"date", "timestamp"},
                    "text_entry": {"string"}, "boolean_switch": {"boolean"},
                    "number_range_slider": {"integer", "decimal", "float"},
                }.get(node.kind)
                if source.logical_type == "geometry" or (
                    allowed is not None and source.logical_type not in allowed
                ):
                    raise ValueError("Control type is incompatible with its source field")


def validate_chart_binding(chart: ChartDefinition, dataset: DatasetDefinition) -> None:
    if chart.dataset_id != dataset.dataset_id:
        raise ValueError("Chart source does not match the dataset")
    fields: dict[str, DatasetField | Metric] = {
        **{field.field_id: field for field in dataset.fields},
        **{str(metric.metric_id): metric for metric in dataset.metrics},
    }

    def resolve(identity: str) -> DatasetField | Metric:
        if identity not in fields:
            raise ValueError("Chart references a field absent from its dataset revision")
        return fields[identity]

    dimensions = {node.field_id for node in _nodes(chart.encoding) if isinstance(node, Dimension)}
    encoded_measures = {
        (node.field_id, node.aggregation, node.axis)
        for node in _nodes(chart.encoding) if isinstance(node, Measure)
    }

    measures: dict[str, set[tuple[str | None, str | None]]] = {}
    for node in _nodes(chart):
        if isinstance(node, Dimension):
            source = resolve(node.field_id)
            if isinstance(source, Metric) or source.logical_type == "geometry":
                raise ValueError("A dimension requires a scalar dataset field")
            if node.date_grain and source.logical_type not in {"date", "timestamp"}:
                raise ValueError("Date bucketing requires a temporal field")
        elif isinstance(node, Measure):
            source = resolve(node.field_id)
            if isinstance(source, Metric):
                if source.level != "row" and node.aggregation != "agg":
                    raise ValueError("An aggregate metric cannot be aggregated again")
                if source.level == "row" and node.aggregation == "agg":
                    raise ValueError("A row metric needs an explicit aggregation")
            elif node.aggregation == "agg":
                raise ValueError("AGG requires a declared aggregate metric")
            elif (node.aggregation not in {"count", "count_distinct", "attr"}
                  and source.logical_type not in {"integer", "decimal", "float"}):
                raise ValueError("This aggregation requires a numeric field")
            unit = ("count", None) if node.aggregation in {"count", "count_distinct"} else (
                source.unit, source.currency,
            )
            if node.format.kind == "currency" and node.format.currency != unit[1]:
                raise ValueError("Currency formatting must match the measure's declared currency")
            measures.setdefault(node.axis, set()).add(unit)
        elif isinstance(node, FieldBinding):
            if (node.dataset_id != chart.dataset_id
                    or node.dataset_revision != chart.dataset_revision):
                raise ValueError("Chart filters must bind to its pinned dataset revision")
            resolve(node.field_id)
        elif isinstance(node, (ReferenceLine, ReferenceBand, TableCalculation, TopN)):
            measure = node.measure
            if (measure.field_id, measure.aggregation, measure.axis) not in encoded_measures:
                raise ValueError("Presentation settings must bind an encoded measure")
            if isinstance(node, TableCalculation):
                if set(node.addressing) | set(node.partition_by) != dimensions:
                    raise ValueError(
                        "Calculation addressing and partitions must cover chart dimensions"
                    )
            elif isinstance(node, TopN) and node.dimension.field_id not in dimensions:
                raise ValueError("Top N requires an encoded dimension")

    # These encodings contain direct field IDs rather than Dimension/Measure nodes.
    for name in ("x", "y", "size", "latitude", "longitude", "field_id"):
        identity = getattr(chart.encoding, name, None)
        if isinstance(identity, str):
            source = resolve(identity)
            if (isinstance(source, Metric)
                    or source.logical_type not in {"integer", "decimal", "float"}):
                raise ValueError("This encoding requires a numeric source field")
    for identity in getattr(chart.encoding, "detail", ()):
        resolve(identity)
    for item in chart.sort:
        resolve(item.field_id)
    for identity in chart.tooltip.fields:
        resolve(identity)
    if chart.encoding.kind == "cartesian" and any(len(units) > 1 for units in measures.values()):
        raise ValueError("Measures with different units require separate axes")


def validate_relationship_binding(
    local: DatasetDefinition, relation: Relationship, foreign: DatasetDefinition,
) -> None:
    if relation.dataset_id != foreign.dataset_id:
        raise ValueError("Relationship source does not match its dataset")
    local_fields = {field.field_id: field for field in local.fields}
    foreign_fields = {field.field_id: field for field in foreign.fields}
    for key in relation.keys:
        left, right = local_fields.get(key.local_field_id), foreign_fields.get(key.foreign_field_id)
        if left is None or right is None:
            raise ValueError("Relationship references an unknown field")
        if (left.logical_type, left.physical_type, left.timezone) != (
            right.logical_type, right.physical_type, right.timezone,
        ):
            raise ValueError("Relationship keys require matching declared types and timezone")


def validate_widget_bindings(
    dashboard: DashboardDefinition, charts: Mapping[tuple[UUID, int], ChartDefinition],
) -> None:
    for _, visual in dashboard.visuals():
        source = charts[(visual.chart_ref.chart_id, visual.chart_ref.revision)]
        for binding in document_bindings(visual):
            if (binding.dataset_id != source.dataset_id
                    or binding.dataset_revision != source.dataset_revision):
                raise ValueError("Widget filters must bind to its pinned chart dataset")
