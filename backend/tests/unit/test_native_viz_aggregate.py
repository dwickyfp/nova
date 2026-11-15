"""Aggregate planning: quoted identifiers, typed bound values, exact output types."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.viz.aggregate import plan_aggregate, relative_bounds
from app.modules.viz.contracts.datasets import DatasetField
from app.modules.viz.contracts.filters import FilterTree, RelativeWindow
from app.modules.viz.contracts.queries import DatasetQueryRequest
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parameters import BoundParameters
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.read_queries import validate_read_query

DATASET = UUID("aaaaaaaa-0000-4000-8000-000000000001")


def field(field_id, column, logical, physical, role="dimension", timezone=None):
    return DatasetField(field_id=field_id, name=column.title(), source_column=column,
                        logical_type=logical, physical_type=physical, semantic_role=role,
                        nullable=True, timezone=timezone)


FIELDS = (
    field("f_city", "city", "string", "VARCHAR(32)"),
    field("f_amount", "amount", "decimal", "DECIMAL(18,2)", "measure"),
    field("f_qty", "qty", "integer", "BIGINT", "measure"),
    field("f_day", "sale_day", "date", "DATE"),
    field("f_at", "sold`at", "timestamp", "DATETIME", timezone="Asia/Jakarta"),
    field("f_ratio", "ratio", "float", "DOUBLE", "measure"),
)


def predicate(field_id, op, *values, **extra):
    return {"type": "predicate", "op": op, "values": list(values), **extra,
            "field": {"dataset_id": str(DATASET), "dataset_revision": 3, "field_id": field_id}}


def request(**values):
    return DatasetQueryRequest(revision=3, **values)


def plan(req, now=None):
    return plan_aggregate("SELECT * FROM sales", FIELDS, req, dataset_id=str(DATASET),
                          reserved_parameters={"nova_q0"}, now=now)


def test_group_by_with_exact_measure_types_and_deterministic_order():
    result = plan(request(
        dimensions=[{"field_id": "f_city"}, {"field_id": "f_day", "date_grain": "month"}],
        measures=[{"field_id": "f_amount", "aggregation": "sum"},
                  {"aggregation": "count"}, {"field_id": "f_qty", "aggregation": "avg"},
                  {"field_id": "f_ratio", "aggregation": "max"}],
        sort=[{"column": "m0", "direction": "desc"}], limit=50,
    ))
    assert result.sql == (
        "SELECT `city` AS `d0`,CAST(date_trunc('month', `sale_day`) AS DATE) AS `d1`,"
        "CAST(SUM(CAST(`amount` AS DECIMAL(38,2))) AS DECIMAL(38,2)) AS `m0`,"
        "COUNT(*) AS `m1`,CAST(AVG(CAST(`qty` AS DECIMAL(38,0))) AS DECIMAL(38,6)) AS `m2`,"
        "MAX(`ratio`) AS `m3`\nFROM (SELECT * FROM sales\n) AS `nova_dataset`"
        "\nGROUP BY `city`,CAST(date_trunc('month', `sale_day`) AS DATE)"
        "\nORDER BY `m0` DESC,`d0` ASC,`d1` ASC\nLIMIT 51"
    )
    assert [c.logical_type for c in result.columns] == [
        "string", "date", "decimal", "integer", "decimal", "float"]
    assert [c.source_column for c in result.output_fields] == [c.key for c in result.columns]
    assert result.columns[3].name == "Rows" and not result.specs
    validate_read_query(parse_statement(result.sql))


def test_filter_values_are_typed_parameters_never_sql_text():
    canary = "x' OR 1=1; -- %_\\"
    tree = FilterTree(expression={"type": "and", "children": [
        predicate("f_city", "in", {"type": "string", "value": canary},
                  {"type": "string", "value": "Bandung"}),
        {"type": "not",
         "child": predicate("f_amount", "gte", {"type": "decimal", "value": "10.125"})},
        predicate("f_city", "like_literal", {"type": "string", "value": "50%_off"}),
        predicate("f_day", "between", {"type": "date", "value": "2024-02-01"},
                  {"type": "date", "value": "2024-02-29"}),
        predicate("f_at", "is_null"),
    ]})
    result = plan(request(measures=[{"aggregation": "count"}], filters=tree))
    assert canary not in result.sql and "Bandung" not in result.sql and "10.125" not in result.sql
    assert "`sold``at` IS NULL" in result.sql
    assert "(`city` IN (:nova_q0_))" in result.sql  # avoids the reserved dataset parameter
    names = [spec.name for spec in result.specs]
    assert names == ["nova_q0_", "nova_q1", "nova_q2", "nova_q3", "nova_q4"]
    values = dict(result.values)
    assert values["nova_q0_"] == (canary, "Bandung")
    assert values["nova_q1"] == Decimal("10.125") and result.specs[1].scale == 3
    assert values["nova_q2"] == "%50\\%\\_off%"
    assert values["nova_q3"] == date(2024, 2, 1)
    bound = BoundParameters(tuple(result.specs), tuple(result.values))
    assert bound.specs[0].multiple
    validate_read_query(parse_statement(result.sql))


def test_relative_windows_are_half_open_calendar_ranges():
    now = datetime(2024, 3, 1, 1, 0, tzinfo=UTC)  # already 1 March in Jakarta
    zone = "Asia/Jakarta"
    assert relative_bounds(RelativeWindow(unit="month", mode="previous", timezone=zone), now) == (
        date(2024, 2, 1), date(2024, 3, 1))
    assert relative_bounds(RelativeWindow(unit="day", count=7, timezone=zone), now) == (
        date(2024, 2, 24), date(2024, 3, 2))
    assert relative_bounds(RelativeWindow(unit="year", mode="to_date", timezone=zone), now) == (
        date(2024, 1, 1), date(2024, 3, 2))
    assert relative_bounds(RelativeWindow(unit="quarter", mode="previous",
                                          fiscal_start_month=4, timezone=zone), now) == (
        date(2023, 10, 1), date(2024, 1, 1))
    sunday_week = RelativeWindow(unit="week", mode="to_date", week_start="sunday", timezone=zone)
    assert relative_bounds(sunday_week, now) == (date(2024, 2, 25), date(2024, 3, 2))
    result = plan(request(measures=[{"aggregation": "count"}], filters=FilterTree(
        expression=predicate("f_at", "relative_date",
                             relative={"unit": "month", "mode": "previous", "timezone": zone}))),
        now=now)
    assert "(`sold``at` >= :nova_q0_ AND `sold``at` < :nova_q1)" in result.sql
    start = dict(result.values)["nova_q0_"]
    assert start.isoformat() == "2024-02-01T00:00:00+07:00"


@pytest.mark.parametrize(("measure", "message"), [
    ({"field_id": "f_city", "aggregation": "sum"}, "numeric"),
    ({"field_id": "missing", "aggregation": "count"}, "unknown field"),
])
def test_invalid_measures_are_refused(measure, message):
    with pytest.raises(SemanticError, match=message):
        plan(request(measures=[measure]))


@pytest.mark.parametrize("bad", [
    predicate("f_city", "eq", {"type": "integer", "value": "1"}),
    predicate("f_amount", "like_literal", {"type": "string", "value": "1"}),
    predicate("f_city", "eq", {"type": "string", "value": "x"}, stage="aggregate"),
    {**predicate("f_city", "eq", {"type": "string", "value": "x"}),
     "field": {"dataset_id": str(DATASET), "dataset_revision": 2, "field_id": "f_city"}},
])
def test_mismatched_filters_are_refused(bad):
    with pytest.raises(SemanticError):
        plan(request(measures=[{"aggregation": "count"}], filters=FilterTree(expression=bad)))


def test_request_shape_validation():
    with pytest.raises(ValidationError):
        DatasetQueryRequest(revision=1)
    with pytest.raises(ValidationError):
        DatasetQueryRequest(revision=1, measures=[{"aggregation": "sum"}])
    with pytest.raises(ValidationError):
        DatasetQueryRequest(revision=1, dimensions=[{"field_id": "a"}], sort=[{"column": "m0"}])
    with pytest.raises(SemanticError, match="date grain"):
        plan(request(dimensions=[{"field_id": "f_city", "date_grain": "day"}]))


def test_distinct_values_for_filter_options():
    result = plan(request(dimensions=[{"field_id": "f_city"}], limit=200))
    assert result.sql.endswith("GROUP BY `city`\nORDER BY `d0` ASC\nLIMIT 201")
