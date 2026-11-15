"""Exact preview values, bounded responses and composable query pagination."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.exceptions import NovaException
from app.modules.viz.contracts.authoring import DatasetPreviewRequest
from app.modules.viz.contracts.common import FloatValue
from app.modules.viz.contracts.datasets import DatasetField
from app.modules.viz.preview import PreviewRows, preview_value
from app.sql_frontend.binding.models import BoundColumn
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.read_queries import preview_query


def field(kind, **changes):
    return DatasetField(
        field_id="value",
        source_column="value",
        name="Value",
        logical_type=kind,
        physical_type="varchar(4096)",
        semantic_role="dimension",
        nullable=True,
        **changes,
    )


@pytest.mark.parametrize(
    "kind,value,expected",
    [
        ("integer", 9223372036854775807, "9223372036854775807"),
        ("integer", -9223372036854775808, "-9223372036854775808"),
        (
            "decimal",
            Decimal("99999999999999999999999999.123456789012"),
            "99999999999999999999999999.123456789012",
        ),
        ("decimal", Decimal("1E-12"), "0.000000000001"),
        ("float", 1.7976931348623157e308, 1.7976931348623157e308),
        ("float", 5e-324, 5e-324),
        ("float", -0.125, -0.125),
        ("string", "=HYPERLINK('example')", "=HYPERLINK('example')"),
        ("boolean", 0, False),
        ("boolean", 1, True),
        ("boolean", False, False),
        ("date", date(2024, 2, 29), "2024-02-29"),
    ],
)
def test_values_do_not_guess_or_round(kind, value, expected):
    result = preview_value(field(kind), value)
    assert result.type == kind
    assert result.value == expected


@pytest.mark.parametrize(
    "kind,value",
    [
        ("integer", 9223372036854775808),
        ("integer", True),
        ("integer", 1.0),
        ("decimal", 0.1),
        ("decimal", Decimal("NaN")),
        ("decimal", Decimal("Infinity")),
        ("float", float("NaN")),
        ("float", float("inf")),
        ("float", float("-inf")),
        ("float", Decimal("0.125")),
        ("float", 9223372036854775807),
        ("float", True),
        ("string", "x" * 4097),
        ("string", b"private-value"),
        ("boolean", 2),
        ("date", datetime(2024, 2, 29)),
        ("date", "2024-02-29"),
        ("geometry", "POINT(1 2)"),
    ],
)
def test_incompatible_or_unbounded_values_are_refused(kind, value):
    with pytest.raises(ValueError):
        preview_value(field(kind), value)


def test_timestamp_uses_declared_zone_without_losing_microseconds():
    assert (
        preview_value(
            field("timestamp", timezone="Asia/Jakarta"), datetime(2024, 2, 29, 12, 30, 59, 123456)
        ).value
        == "2024-02-29T12:30:59.123456+07:00"
    )


@pytest.mark.parametrize("zone", ["+07:00", "-05:30", "+14:00", "-00:00"])
def test_timestamp_supports_fixed_session_offsets(zone):
    value = datetime(2024, 2, 29, 12, 30)
    result = preview_value(field("timestamp", timezone=zone), value)
    expected = "+00:00" if zone == "-00:00" else zone
    assert result.value == "2024-02-29T12:30:00" + expected


@pytest.mark.parametrize("zone", ["+14:01", "-15:00", "+07:60", "unknown-zone"])
def test_timestamp_refuses_invalid_session_zones(zone):
    with pytest.raises(ValueError):
        preview_value(field("timestamp", timezone=zone), datetime(2024, 2, 29))


@pytest.mark.parametrize("value", [datetime(2024, 11, 3, 1, 30), datetime(2024, 3, 10, 2, 30)])
def test_ambiguous_and_nonexistent_local_times_are_not_guessed(value):
    with pytest.raises(ValueError, match="Ambiguous"):
        preview_value(field("timestamp", timezone="America/New_York"), value)


def test_null_is_distinct_from_zero_and_empty_text():
    assert preview_value(field("string"), None) is None
    with pytest.raises(ValueError):
        preview_value(field("integer").model_copy(update={"nullable": False}), None)


@pytest.mark.parametrize("value", ["0.125", True, 2**53 + 1, 10**400, float("nan"), float("inf")])
def test_float_contract_refuses_coercion_precision_loss_and_nonfinite_values(value):
    with pytest.raises(ValidationError):
        FloatValue(value=value)


@pytest.mark.parametrize("value", [1.7976931348623157e308, 5e-324, -0.125, 0, 2**54, -(2**54)])
def test_float_contract_roundtrips_finite_binary_numbers(value):
    encoded = FloatValue(value=value)
    assert FloatValue.model_validate_json(encoded.model_dump_json()).value == value


def test_integral_float_accepts_json_without_a_decimal_point():
    value = FloatValue.model_validate_json('{"type":"float","value":18014398509481984}')
    assert value.value == 2**54


async def test_sink_keeps_only_requested_rows_and_reports_a_real_lookahead():
    sink = PreviewRows((field("integer"),), 2)
    await sink.begin(["value"], ())
    await sink.rows([[1], [2], [3]])
    assert [row[0].value for row in sink.values] == ["1", "2"]
    assert sink.has_more and sink.count == 3
    with pytest.raises(NovaException, match="Invalid preview result"):
        await sink.rows([[4]])


async def test_sink_rejects_total_encoded_bytes_before_storing_more_rows():
    fields = tuple(
        field("string").model_copy(update={"source_column": f"f{i}"}) for i in range(100)
    )
    sink = PreviewRows(fields, 100)
    await sink.begin([f.source_column for f in fields], ())
    with pytest.raises(NovaException) as error:
        for _ in range(4):
            await sink.rows([["x" * 4096] * 100])
    assert error.value.status_code == 413
    assert len(sink.values) <= 2


async def test_sink_never_echoes_invalid_values_and_rejects_changed_columns():
    sink = PreviewRows((field("integer"),), 1)
    with pytest.raises(NovaException, match="SCHEMA_STALE"):
        await sink.begin(["other"], ())
    await sink.begin(["value"], ())
    with pytest.raises(NovaException) as error:
        await sink.rows([["private-cell-marker"]])
    assert "private-cell-marker" not in str(error.value)
    assert sink.values == []


@pytest.mark.parametrize(
    "changes",
    [
        {"limit": 201},
        {"limit": True},
        {"offset": -1},
        {"offset": 10001},
        {"revision": 0},
        {"draft": "true"},
    ],
)
def test_preview_request_has_strict_limits(changes):
    with pytest.raises(ValidationError):
        DatasetPreviewRequest.model_validate({"revision": 1, **changes})


def test_sql_preview_preserves_cte_inner_limit_and_string_semicolons():
    original = "/* c */ WITH t AS (SELECT ';' AS v) SELECT v FROM t ORDER BY v LIMIT 7; -- end"
    sql = preview_query(
        parse_statement(original), (BoundColumn("v", "varchar", 1, True),), limit=3, offset=2
    )
    assert "WITH t AS (SELECT ';' AS v) SELECT v FROM t ORDER BY v LIMIT 7" in sql
    assert sql.endswith("AS `nova_preview` ORDER BY `v` LIMIT 3 OFFSET 2")
    assert sql.startswith("SELECT LEFT(`v`,4097) AS `v` FROM (")
    assert parse_statement(sql).statement_context is not None


@pytest.mark.parametrize(
    "sql",
    ["SELECT :private_value AS value", "DELETE FROM t", "SELECT 1; SELECT 2", "SELECT sleep(5)"],
)
def test_preview_rejects_unsafe_or_unbound_queries(sql):
    with pytest.raises((SemanticError, ValueError)):
        preview_query(
            parse_statement(sql), (BoundColumn("value", "bigint", 1, True),), limit=2, offset=0
        )
