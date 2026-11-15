"""Parameter declarations constrain values without changing governed query structure."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.modules.viz.contracts.authoring import DatasetPreviewRequest
from app.modules.viz.contracts.parameters import Parameter, ParameterInput
from app.modules.viz.parameters import bind_parameters
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parameters import driver_parameters


def value(kind, text):
    return {"type": kind, "value": text}


def given(*values, name="input"):
    return ParameterInput(parameter_id=name, values=values)


@pytest.mark.parametrize("kind,wire,expected,extra", [
    ("integer", "9223372036854775807", 2**63 - 1, {}),
    ("decimal", "12345678901234567890123456.123456789012",
     Decimal("12345678901234567890123456.123456789012"), {"precision": 38, "scale": 12}),
    ("boolean", False, False, {}),
    ("date", "2024-02-29", date(2024, 2, 29), {}),
    ("timestamp", "2024-11-03T01:30:00-05:00",
     datetime.fromisoformat("2024-11-03T06:30:00+00:00"), {}),
    ("string", "O'Reilly'; DROP TABLE t; -- \\ %", "O'Reilly'; DROP TABLE t; -- \\ %", {}),
])
def test_private_values_retain_exact_types_at_the_existing_binding_boundary(
    kind, wire, expected, extra,
):
    declaration = Parameter(parameter_id="input", type=kind, **extra)
    supplied = given(value(kind, wire))
    bound = bind_parameters((declaration,), (supplied,))
    assert bound.values == (("input", expected),)
    assert "values=" not in repr(supplied) and "values=" not in repr(bound)
    request = DatasetPreviewRequest(revision=1, parameters=(supplied,))
    assert "parameters=" not in repr(request)
    sql, args = driver_parameters("SELECT :input AS result", bound, "UTC")
    assert "%s" in sql and ":input" not in sql
    assert len(args) == 1 and declaration.defaults == ()


def test_defaults_enum_nullable_omission_and_explicit_null_remain_distinct():
    declaration = Parameter(parameter_id="input", type="enum", required=False, nullable=True,
                            defaults=(value("string", "East"),),
                            allowed=(value("string", "East"), value("string", "West")))
    assert bind_parameters((declaration,), ()).values == (("input", "East"),)
    assert bind_parameters((declaration,), (given(None),)).values == (("input", None),)
    optional = Parameter(parameter_id="input", type="integer", required=False, nullable=True)
    assert bind_parameters((optional,), ()).values == (("input", None),)
    required = optional.model_copy(update={"required": True})
    with pytest.raises(SemanticError, match="required"):
        bind_parameters((required,), ())


def test_list_nulls_remain_sql_operands_and_cannot_accept_an_empty_all_shortcut():
    declaration = Parameter(parameter_id="input", type="integer", multiple=True, nullable=True)
    bound = bind_parameters((declaration,), (given(value("integer", "1"), None),))
    sql, args = driver_parameters("SELECT id FROM t WHERE id IN (:input)", bound, "UTC")
    assert sql == "SELECT id FROM t WHERE id IN (CAST(%s AS BIGINT), CAST(%s AS BIGINT))"
    assert args == (1, None)
    with pytest.raises(ValidationError):
        given()


@pytest.mark.parametrize("changes", [
    {"type": "decimal"}, {"precision": 10}, {"scale": 2},
    {"type": "decimal", "precision": 5, "scale": 6},
    {"required": False}, {"required": "true"}, {"nullable": 1},
    {"type": "enum"},
    {"defaults": (value("string", "wrong-type"),)},
    {"defaults": (value("integer", "1"), value("integer", "2"))},
    {"minimum": value("integer", "2"), "maximum": value("integer", "1")},
    {"minimum": value("string", "not-a-number")},
    {"type": "string", "minimum": value("string", "A")},
    {"allowed": (value("integer", "1"), value("integer", "1"))},
    {"allowed": (value("integer", "1"),), "defaults": (value("integer", "2"),)},
    {"minimum": value("integer", "2"), "defaults": (value("integer", "1"),)},
    {"maximum": value("integer", "2"), "allowed": (value("integer", "3"),)},
    {"sensitive": True, "minimum": value("integer", "1")},
    {"type": "decimal", "precision": 5, "scale": 2, "defaults": (value("decimal", "0.001"),)},
])
def test_invalid_or_lossy_declarations_fail_before_persistence(changes):
    with pytest.raises(ValidationError):
        Parameter(**{"parameter_id": "input", "type": "integer", **changes})


@pytest.mark.parametrize("kind,lower,upper,inside,outside,extra", [
    ("integer", "9007199254740992", "9223372036854775807", "9007199254740993",
     "9007199254740991", {}),
    ("decimal", "0.000000000001", "999.999999999999", "1.2300", "0.000000000000",
     {"precision": 38, "scale": 12}),
    ("date", "2024-02-29", "2024-03-01", "2024-02-29", "2024-02-28", {}),
    ("timestamp", "2024-11-03T01:30:00-05:00", "2024-11-03T07:00:00Z",
     "2024-11-03T06:30:00Z", "2024-11-03T01:30:00-04:00", {}),
])
def test_inclusive_ranges_compare_exact_values_and_absolute_instants(
    kind, lower, upper, inside, outside, extra,
):
    declaration = Parameter(parameter_id="input", type=kind, minimum=value(kind, lower),
                            maximum=value(kind, upper), **extra)
    bind_parameters((declaration,), (given(value(kind, inside)),))
    with pytest.raises(SemanticError, match="violates") as error:
        bind_parameters((declaration,), (given(value(kind, outside)),))
    assert outside not in str(error.value)


def test_decimal_allowed_values_use_exact_numeric_equality():
    declaration = Parameter(parameter_id="input", type="decimal", precision=5, scale=2,
                            allowed=(value("decimal", "1.20"),),
                            defaults=(value("decimal", "1.2"),))
    assert bind_parameters((declaration,), ()).values == (("input", Decimal("1.2")),)


@pytest.mark.parametrize("supplied", [
    (given(value("integer", "1"), name="unknown"),),
    (given(value("integer", "1")), given(value("integer", "2"))),
    (given(value("string", "private-marker")),), (given(None),),
    (given(value("integer", "1"), value("integer", "2")),),
    (given(value("float", 1.0)),),
])
def test_invalid_bindings_never_echo_input_values(supplied):
    declaration = Parameter(parameter_id="input", type="integer")
    with pytest.raises(SemanticError) as error:
        bind_parameters((declaration,), supplied)
    assert "private-marker" not in str(error.value)


def test_sensitive_input_is_refused_at_the_unaccepted_engine_boundary():
    declaration = Parameter(parameter_id="input", type="string", sensitive=True)
    with pytest.raises(SemanticError, match="protected engine execution") as error:
        bind_parameters((declaration,), (given(value("string", "private-marker")),))
    assert "private-marker" not in str(error.value)
    assert bind_parameters((), ()) is None
    with pytest.raises(SemanticError, match="Unknown"):
        bind_parameters((), (given(value("integer", "1")),))


def test_preview_wire_rejects_duplicate_input_identity():
    with pytest.raises(ValidationError, match="unique"):
        DatasetPreviewRequest(revision=1, parameters=(given(None), given(None)))
