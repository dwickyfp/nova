"""Pinned field identities, metric aggregation and units fail closed before save."""

from uuid import uuid4

import pytest

from app.modules.viz.bindings import (
    document_bindings,
    validate_chart_binding,
    validate_control_binding,
    validate_document_controls,
    validate_relationship_binding,
)
from app.modules.viz.contracts.charts import ChartDefinition
from app.modules.viz.contracts.datasets import DatasetDefinition, Relationship
from app.modules.viz.contracts.filters import (
    FieldBinding,
    FieldMapping,
    FilterDefinition,
    FilterTree,
    ParameterBinding,
)


def dataset(**changes):
    return DatasetDefinition.model_validate({
        "dataset_id": str(uuid4()), "title": "Sales",
        "source": {"kind": "table", "catalog": "default_catalog", "database": "sales",
                   "object_name": "orders", "physical_id": "test-table-id"},
        "schema_fingerprint": "a" * 64,
        "fields": [
            {"field_id": name, "name": name, "source_column": name, "logical_type": logical,
             "physical_type": physical, "nullable": True, "semantic_role": role,
             "unit": unit, "currency": currency}
            for name, logical, physical, role, unit, currency in [
                ("region", "string", "VARCHAR(30)", "dimension", None, None),
                ("date", "date", "DATE", "dimension", None, None),
                ("amount", "decimal", "DECIMAL(18,2)", "measure", "currency", "IDR"),
                ("count", "integer", "BIGINT", "measure", "count", None),
            ]
        ], **changes,
    })


def chart(source, **changes):
    return ChartDefinition.model_validate({
        "chart_id": str(uuid4()), "title": "Revenue", "dataset_id": str(source.dataset_id),
        "dataset_revision": 3, "visual": "bar", "encoding": {
            "kind": "cartesian", "dimensions": [{"field_id": "region"}],
            "measures": [{"field_id": "amount", "aggregation": "sum"}],
        }, **changes,
    })


def test_field_bindings_use_identity_and_pinned_revision_not_display_labels():
    source = dataset()
    document = chart(source)
    validate_chart_binding(document, source)
    with pytest.raises(ValueError, match="does not match"):
        validate_chart_binding(document, dataset())
    for identity in ("Revenue", "missing"):
        bad = document.model_dump()
        bad["encoding"]["measures"][0]["field_id"] = identity
        with pytest.raises(ValueError, match="absent"):
            validate_chart_binding(ChartDefinition.model_validate(bad), source)


@pytest.mark.parametrize("identity,aggregation", [("region", "sum"), ("amount", "agg")])
def test_invalid_field_aggregation_is_rejected(identity, aggregation):
    source = dataset()
    document = chart(source).model_dump()
    document["encoding"]["measures"][0].update(field_id=identity, aggregation=aggregation)
    with pytest.raises(ValueError):
        validate_chart_binding(ChartDefinition.model_validate(document), source)


def test_float_measure_and_range_bind_without_exact_numeric_coercion():
    source = dataset()
    fields = tuple(
        f.model_copy(update={"logical_type": "float", "physical_type": "DOUBLE"})
        if f.field_id == "amount" else f
        for f in source.fields
    )
    source = source.model_copy(update={"fields": fields})
    validate_chart_binding(chart(source), source)
    control = FilterDefinition.model_validate({
        "filter_id": str(uuid4()), "title": "Amount", "kind": "number_range_slider",
        "scope": {"kind": "global"}, "field": {
            "dataset_id": str(source.dataset_id), "dataset_revision": 3, "field_id": "amount",
        }, "defaults": [{"type": "float", "value": 0.125}, {"type": "float", "value": 1.5}],
    })
    validate_control_binding(control.field, source)
    validate_document_controls(control, {(source.dataset_id, 3): source})


def test_date_bucketing_requires_temporal_field():
    source = dataset()
    document = chart(source).model_dump()
    document["encoding"]["dimensions"][0]["date_grain"] = "month"
    with pytest.raises(ValueError, match="temporal"):
        validate_chart_binding(ChartDefinition.model_validate(document), source)
    document["encoding"]["dimensions"][0]["field_id"] = "date"
    validate_chart_binding(ChartDefinition.model_validate(document), source)


def test_mixed_measure_units_require_separate_axes_and_currency_is_not_relabelled():
    source = dataset()
    document = chart(source).model_dump()
    measures = document["encoding"]["measures"] = list(document["encoding"]["measures"])
    measures.append({"field_id": "count", "aggregation": "sum"})
    with pytest.raises(ValueError, match="separate axes"):
        validate_chart_binding(ChartDefinition.model_validate(document), source)
    measures[-1]["axis"] = "right"
    validate_chart_binding(ChartDefinition.model_validate(document), source)
    measures[0]["format"] = {"kind": "currency", "currency": "USD"}
    with pytest.raises(ValueError, match="currency"):
        validate_chart_binding(ChartDefinition.model_validate(document), source)
    measures[0]["format"]["currency"] = "IDR"
    validate_chart_binding(ChartDefinition.model_validate(document), source)


def test_aggregate_metric_cannot_be_summed_a_second_time():
    metric = {"metric_id": str(uuid4()), "revision": 1, "name": "Revenue",
              "formula": "SUM(amount)", "level": "aggregate", "grain": ["region"],
              "unit": "currency", "currency": "IDR", "scope": "dashboard_local"}
    source = dataset(metrics=[metric])
    document = chart(source).model_dump()
    measure = document["encoding"]["measures"][0]
    measure["field_id"] = metric["metric_id"]
    with pytest.raises(ValueError, match="again"):
        validate_chart_binding(ChartDefinition.model_validate(document), source)
    measure["aggregation"] = "agg"
    validate_chart_binding(ChartDefinition.model_validate(document), source)


def test_filter_from_another_dataset_or_revision_cannot_be_injected():
    source = dataset()
    document = chart(source).model_dump()
    document["filters"] = {"expression": {
        "type": "predicate", "field": {"dataset_id": str(source.dataset_id),
            "dataset_revision": 2, "field_id": "region"}, "op": "is_null",
    }}
    with pytest.raises(ValueError, match="pinned"):
        validate_chart_binding(ChartDefinition.model_validate(document), source)
    document["filters"]["expression"]["field"]["dataset_revision"] = 3
    validate_chart_binding(ChartDefinition.model_validate(document), source)


def test_relationship_keys_must_exist_and_match_physical_types():
    local, foreign = dataset(), dataset()
    relation = Relationship.model_validate({
        "relationship_id": str(uuid4()), "dataset_id": str(foreign.dataset_id),
        "dataset_revision": 1, "cardinality": "many:1", "join": "left",
        "keys": [{"local_field_id": "region", "foreign_field_id": "region"}],
    })
    validate_relationship_binding(local, relation, foreign)
    for key in ("missing", "count"):
        payload = relation.model_dump()
        payload["keys"][0]["foreign_field_id"] = key
        with pytest.raises(ValueError):
            validate_relationship_binding(local, Relationship.model_validate(payload), foreign)


def test_parameter_controls_resolve_declared_parameters_separately_from_fields():
    source = dataset(parameters=[{"parameter_id": "region_input", "type": "string"}])
    binding = ParameterBinding(dataset_id=source.dataset_id, dataset_revision=3,
                               parameter_id="region_input")
    validate_control_binding(binding, source)
    control = FilterDefinition.model_validate({
        "filter_id": str(uuid4()), "title": "Region", "kind": "parameter_control",
        "parameter": binding.model_dump(), "scope": {"kind": "global"},
    })
    assert list(document_bindings(control)) == [binding]
    with pytest.raises(ValueError, match="undeclared"):
        validate_control_binding(binding.model_copy(update={"parameter_id": "region"}), source)
    with pytest.raises(ValueError, match="unknown"):
        validate_control_binding(FieldBinding(dataset_id=source.dataset_id, dataset_revision=3,
                                              field_id="region_input"), source)
    with pytest.raises(ValueError, match="does not match"):
        validate_control_binding(binding, dataset())


def test_metric_filter_identity_is_distinct_from_its_display_name():
    identity = uuid4()
    source = dataset(metrics=[{
        "metric_id": identity, "revision": 1, "name": "Revenue", "formula": "SUM(amount)",
        "level": "aggregate", "grain": ["region"], "unit": "currency", "currency": "IDR",
        "scope": "dashboard_local",
    }])
    binding = FieldBinding(dataset_id=source.dataset_id, dataset_revision=3,
                           field_id=str(identity))
    validate_control_binding(binding, source)
    with pytest.raises(ValueError, match="unknown"):
        validate_control_binding(binding.model_copy(update={"field_id": "Revenue"}), source)


def control(source, **changes):
    return FilterDefinition.model_validate({
        "filter_id": uuid4(), "title": "Region", "kind": "single_value",
        "field": {"dataset_id": source.dataset_id, "dataset_revision": 3,
                  "field_id": "region"}, "scope": {"kind": "global"}, **changes,
    })


@pytest.mark.parametrize("parameter,defaults,include_null", [
    ({"type": "string", "sensitive": True}, [{"type": "string", "value": "private-input"}], False),
    ({"type": "integer"}, [{"type": "string", "value": "42"}], False),
    ({"type": "string"}, [{"type": "string", "value": "a"},
                           {"type": "string", "value": "b"}], False),
    ({"type": "enum", "allowed": [{"type": "string", "value": "a"}]},
     [{"type": "string", "value": "b"}], False),
    ({"type": "string", "nullable": False}, [], True),
])
def test_parameter_control_cannot_bypass_dataset_constraints(parameter, defaults, include_null):
    source = dataset(parameters=[{"parameter_id": "input", **parameter}])
    document = control(source, kind="parameter_control", field=None,
                       parameter={"dataset_id": source.dataset_id, "dataset_revision": 3,
                                  "parameter_id": "input"},
                       defaults=defaults, include_null=include_null)
    with pytest.raises(ValueError):
        validate_document_controls(document, {(source.dataset_id, 3): source})


def test_sensitive_parameter_cannot_persist_values_as_an_allowlist():
    with pytest.raises(ValueError, match="allowed values must not be persisted"):
        dataset(parameters=[{"parameter_id": "private_input", "type": "string",
                             "sensitive": True, "allowed": [{"type": "string", "value": "input"}]}])


def test_declared_nullable_list_parameter_accepts_matching_control_defaults():
    source = dataset(parameters=[{"parameter_id": "input", "type": "string",
                                  "multiple": True, "nullable": True}])
    document = control(source, kind="parameter_control", field=None,
                       parameter={"dataset_id": source.dataset_id, "dataset_revision": 3,
                                  "parameter_id": "input"}, include_null=True,
                       defaults=[{"type": "string", "value": "a"},
                                 {"type": "string", "value": "b"}])
    validate_document_controls(document, {(source.dataset_id, 3): source})


@pytest.mark.parametrize("changes", [
    {"kind": "date_picker"}, {"kind": "number_range_slider"},
    {"defaults": [{"type": "integer", "value": "1"}]},
])
def test_field_control_resolves_its_real_type_even_without_defaults(changes):
    source = dataset()
    with pytest.raises(ValueError):
        validate_document_controls(control(source, **changes), {(source.dataset_id, 3): source})


def test_filter_stage_and_operands_match_the_resolved_field():
    source = dataset()
    payload = {"type": "predicate", "field": {
        "dataset_id": source.dataset_id, "dataset_revision": 3, "field_id": "region",
    }, "op": "eq", "values": [{"type": "string", "value": "Jakarta"}]}
    sources = {(source.dataset_id, 3): source}
    validate_document_controls(FilterTree.model_validate({"expression": payload}), sources)
    for changes in [
        {"stage": "aggregate"}, {"values": [{"type": "integer", "value": "1"}]},
        {"op": "relative_date", "values": [], "relative": {"unit": "month"}},
    ]:
        with pytest.raises(ValueError):
            validate_document_controls(FilterTree.model_validate({
                "expression": {**payload, **changes},
            }), sources)


def test_explicit_cross_dataset_mapping_requires_compatible_units_and_types():
    left, right = dataset(), dataset()
    sources = {(left.dataset_id, 3): left, (right.dataset_id, 3): right}
    payload = {"source": {"dataset_id": left.dataset_id, "dataset_revision": 3,
                          "field_id": "region"},
               "target": {"dataset_id": right.dataset_id, "dataset_revision": 3,
                          "field_id": "region"}}
    validate_document_controls(FieldMapping.model_validate(payload), sources)
    for target in ("count", "amount", "date"):
        payload["target"]["field_id"] = target
        with pytest.raises(ValueError, match="matching types"):
            validate_document_controls(FieldMapping.model_validate(payload), sources)
