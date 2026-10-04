import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from xml.etree import ElementTree

import pytest

from tests.integration.functional_sql.contracts import Case, EffectCheck, FunctionSignature, Outcome
from tests.integration.functional_sql.inventory import read_functions
from tests.integration.functional_sql.manifest import capability_manifest
from tests.integration.functional_sql.reporting import write_reports
from tests.integration.functional_sql.runtime import Target


def test_report_counts_only_passed_coverage_and_records_blockers_as_junit_errors(tmp_path):
    outcomes = [
        Outcome("ok", "scalar", "PASS", signatures=["one"]),
        Outcome("absent", "coverage", "BLOCKED", signatures=["two"]),
        Outcome("broken", "scalar", "FAIL", "SELECT '<script>'", detail="Wrong value"),
    ]
    inventory = {"function_signatures": [{"key": "one"}, {"key": "two"}]}
    summary = write_reports(tmp_path, outcomes, inventory, {})
    assert summary["status"] == "FAIL"
    assert summary["function_signatures_tested"] == 1
    suite = ElementTree.parse(tmp_path / "junit.xml").getroot()
    assert suite.attrib["skipped"] == "0"
    assert len(suite.findall("testcase/error")) == 1
    assert len(suite.findall("testcase/failure")) == 1
    assert "&lt;script&gt;" in (tmp_path / "report.html").read_text()
    assert "<script>" not in (tmp_path / "report.html").read_text()


def test_empty_results_cannot_pass(tmp_path):
    assert write_reports(tmp_path, [], {}, {})["status"] == "BLOCKED"


def test_case_selection_accepts_repeated_globs():
    from tests.integration.functional_sql.run import argument_parser

    args = argument_parser().parse_args(["--case", "engine.*", "--case", "function.123"])
    assert args.case == ["engine.*", "function.123"]


def test_user_authorized_group_exclusion_preserves_unexecuted_sql_and_reason():
    from tests.integration.functional_sql.run import exclude_case_groups

    cases = [
        Case(
            "security.role", "security", "SET ROLE ALL", error_code=1064,
            statement_rules=("setRoleStatement",),
        ),
        Case("query.value", "query", "SELECT 42", [[42]]),
    ]
    selected, excluded = exclude_case_groups(cases, ["security"], "User requested SQL-only work")
    assert [case.id for case in selected] == ["query.value"]
    assert excluded[0]["sql"] == "SET ROLE ALL"
    assert excluded[0]["verification"] == "USER_EXCLUDED"
    assert excluded[0]["reason"] == "User requested SQL-only work"
    manifest = capability_manifest({"statement_rules": ["setRoleStatement"]}, selected, excluded)
    assert manifest["statement_rules"][0]["verification"] == "USER_EXCLUDED"
    assert manifest["statement_rules"][0]["cases"] == []
    with pytest.raises(ValueError):
        exclude_case_groups(cases, ["security"], None)


@pytest.mark.parametrize(
    ("detail", "observed_error"),
    [
        ("TimeoutError", None),
        (
            "ProgrammingError: (1064, 'Tablet lost replicas')",
            {"code": 1064, "message": "Tablet lost replicas"},
        ),
    ],
)
async def test_engine_failure_blocks_remaining_queries_and_preserves_the_reproduction(
    monkeypatch, detail, observed_error
):
    from tests.integration.functional_sql import run as runner

    execute = AsyncMock(return_value=Outcome(
        "crash", "aggregate", "FAIL", detail=detail, observed_error=observed_error
    ))
    health = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(runner, "execute_case", execute)
    monkeypatch.setattr(runner, "check_engine_health", health)
    cases = [Case("crash", "aggregate", "SELECT 1"), Case("next", "scalar", "SELECT 2")]
    outcomes = []
    failure = await runner.execute_cases(cases, Target(), "fixture", outcomes)
    assert execute.await_count == 1
    assert health.await_count == 1
    assert [item.status for item in outcomes] == ["FAIL", "BLOCKED"]
    assert outcomes[1].sql == "SELECT 2"
    assert "health probe failed" in outcomes[1].detail
    assert failure == outcomes[1].detail


async def test_sql_failure_does_not_stop_remaining_cases_when_fixture_health_passes(monkeypatch):
    from tests.integration.functional_sql import run as runner

    execute = AsyncMock(side_effect=[
        Outcome("refusal", "aggregate", "FAIL", observed_error={"code": 1064}),
        Outcome("next", "scalar", "PASS"),
    ])
    health = AsyncMock()
    monkeypatch.setattr(runner, "execute_case", execute)
    monkeypatch.setattr(runner, "check_engine_health", health)
    outcomes = []
    failure = await runner.execute_cases(
        [Case("refusal", "aggregate", "SELECT bad()"), Case("next", "scalar", "SELECT 1")],
        Target(), "fixture", outcomes,
    )
    assert health.await_count == 1
    assert execute.await_count == 2
    assert [item.status for item in outcomes] == ["FAIL", "PASS"]
    assert failure is None


def test_manifest_preserves_every_uncovered_signature_and_statement():
    inventory = {
        "function_signatures": [
            {"key": "a", "signature": "abs(INT)", "kind": "Scalar"},
            {"key": "b", "signature": "new_function(INT)", "kind": "Scalar"},
        ],
        "statement_rules": ["queryStatement", "newStatement"],
    }
    cases = [Case("abs", "scalar", "SELECT abs(1)", [[1]], signatures=("a",))]
    manifest = capability_manifest(inventory, cases)
    assert len(manifest["function_signatures"]) == 2
    assert manifest["function_signatures"][1]["verification"] == "BLOCKED"
    assert manifest["statement_rules"][1]["verification"] == "BLOCKED"
    assert manifest["function_signatures"][0]["cases"][0]["expected"] == [[1]]


@pytest.mark.parametrize("rows", [[], [[None]], [[1, 2]], [[999]]])
def test_numeric_oracle_rejects_wrong_shapes_null_and_wrong_values(rows):
    with pytest.raises(AssertionError):
        Case("numeric", "scalar", "SELECT 3", 3, "number").check(rows, [])


def test_decimal_oracle_and_ai_placeholder_contracts():
    Case("decimal", "scalar", "SELECT 1", [["12.3400"]]).check([[Decimal("12.3400")]], [])
    with pytest.raises(AssertionError, match="placeholder"):
        Case("ai", "ai", "SELECT AI_COMPLETE('test')", "test", "label").check(
            [["ERROR: alias not configured"]], []
        )


async def test_inventory_paginates_past_proxy_result_limit():
    rows = [(f"fn_{index}(INT)", "INT", "Scalar") for index in range(820)]
    queries = []

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def execute(self, sql):
            queries.append(sql)
            self.offset = int(sql.rsplit(" ", 1)[1])

        async def fetchall(self):
            return rows[self.offset : self.offset + 400]

    class Connection:
        def cursor(self):
            return Cursor()

    inventory = await read_functions(Connection())
    assert len(inventory) == 820
    assert len(queries) == 3
    assert len({item.key for item in inventory}) == 820


def test_signature_identity_includes_kind_and_return_type():
    functions = [
        FunctionSignature("fn(INT)", "INT", "Scalar"),
        FunctionSignature("fn(INT)", "BIGINT", "Scalar"),
        FunctionSignature("fn(INT)", "INT", "Aggregate"),
    ]
    assert len({item.key for item in functions}) == 3
    assert json.loads(json.dumps(capability_manifest({}, [])))["function_signatures"] == []


def test_source_inventory_recovers_all_collapsed_array_min_overloads():
    from tests.integration.functional_sql.logical_overloads import logical_cases, scalar_inventory

    entries = scalar_inventory([FunctionSignature("array_min(INVALID_TYPE)", "BOOLEAN", "Scalar")])
    minimums = [entry for entry in entries if entry["name"] == "array_min"]
    assert len(minimums) == 15
    assert len({entry["fid"] for entry in minimums}) == 15
    cases = logical_cases(entries)
    assert len(cases) == 15
    boolean = next(case for case in cases if "CAST(TRUE AS BOOLEAN)" in case.sql)
    assert boolean.expected is False
    assert "CAST(FALSE AS BOOLEAN)" in boolean.sql
    decimal = next(case for case in cases if "DECIMAL(9,4)" in case.sql)
    decimal.check([[Decimal("1.0000")]], [])
    assert all(case.logical_overloads and not case.signatures for case in cases)


def test_logical_overload_coverage_is_separate_from_lossy_catalog_coverage(tmp_path):
    outcomes = [
        Outcome("ok", "complex", "PASS", logical_overloads=["boolean-array"]),
        Outcome("failed", "complex", "FAIL", logical_overloads=["integer-array"]),
    ]
    inventory = {
        "function_signatures": [{"key": "collapsed"}],
        "scalar_logical_overloads": [{"key": "boolean-array"}, {"key": "integer-array"}],
    }
    summary = write_reports(tmp_path, outcomes, inventory, {})
    assert summary["scalar_logical_overloads_tested"] == 1
    assert summary["scalar_logical_overloads_total"] == 2
    assert summary["function_signatures_tested"] == 0


def test_array_distinct_multiset_oracle_checks_values_and_multiplicity():
    case = Case("distinct", "complex", "SELECT array_distinct([3,1,2])", [1, 2, 3], "json_multiset")
    case.check([["[3,1,2]"]], [])
    for value in ["[1,2,2]", "[1,2]", "[1,2,3,3]"]:
        with pytest.raises(AssertionError):
            case.check([[value]], [])


def test_mysql_array_json_oracle_checks_the_native_element_strings_safely():
    case = Case(
        "json-array",
        "complex",
        "SELECT array_sort(json_values)",
        [{"a": 1}, {"a": 2}],
        "mysql_json_array",
    )
    case.check([["['{\"a\": 1}','{\"a\": 2}']"]], [])
    for value in [
        "['{\"a\": 2}','{\"a\": 1}']",
        "['{\"a\": 1}']",
        "['invalid']",
        "__import__('os').getcwd()",
    ]:
        with pytest.raises((AssertionError, ValueError, SyntaxError)):
            case.check([[value]], [])


def test_concrete_engine_registry_preserves_types_and_state_descriptors():
    from tests.integration.functional_sql.engine_overloads import engine_cases, engine_inventory

    entries = engine_inventory([])
    assert len(entries) == 7522
    assert len({entry["key"] for entry in entries}) == len(entries)
    arrays = {entry["arguments"][0] for entry in entries if entry["name"] == "array_min"}
    assert {"ARRAY<BOOLEAN>", "ARRAY<DATE>", "ARRAY<DECIMAL32>"} <= arrays
    cases = {case.id: case for case in engine_cases(entries)}
    state = next(
        entry
        for entry in entries
        if entry["name"] == "sum_merge"
        and entry.get("state_descriptor", {}).get("arguments") == ["TINYINT"]
    )
    case = cases["engine." + state["key"]]
    assert "sum_state(CAST(1 AS TINYINT))" in case.sql
    assert case.logical_overloads == (state["key"],) and not case.signatures
    assert case.prerequisite == "Aggregate-state trials require --isolated-stack"
    for name in ("sum_state", "sum_state_merge", "sum_state_union"):
        entry = next(
            entry
            for entry in entries
            if entry["name"] == name
            and entry.get("state_descriptor", {}).get("arguments") == ["TINYINT"]
        )
        assert "engine." + entry["key"] in cases
        assert "CAST(1 AS TINYINT)" in cases["engine." + entry["key"]].sql
    table = next(entry for entry in entries if entry["name"] == "json_each")
    assert cases["engine." + table["key"]].expected == [["a", "1"], ["b", "2"]]


def test_new_runtime_function_cannot_silently_escape_the_concrete_registry():
    from tests.integration.functional_sql.engine_overloads import engine_inventory

    with pytest.raises(RuntimeError, match="absent from the pinned registry"):
        engine_inventory([FunctionSignature("unresearched_new_function(INT)", "INT", "Scalar")])


def test_equal_values_do_not_prove_both_integer_return_overloads():
    from tests.integration.functional_sql.engine_overloads import engine_cases, engine_inventory
    from tests.integration.functional_sql.functions import with_return_type_check

    entries = [
        entry for entry in engine_inventory([])
        if entry["name"] == "year" and entry["arguments"] == ["DATETIME"]
    ]
    cases = engine_cases(entries)
    assert len(cases) == 1 and cases[0].expected_column_types == (2,)
    cases[0].check([[2024]], [("year", 2)])
    with pytest.raises(AssertionError, match="MySQL column types"):
        cases[0].check([[2024]], [("year", 3)])
    old = with_return_type_check(
        Case("old-year", "scalar", "SELECT year(now())", [[2024]]),
        FunctionSignature("year(DATETIME)", "INT", "Scalar"),
    )
    with pytest.raises(AssertionError, match="MySQL column types"):
        old.check([[2024]], [("year", 2)])


def test_sourced_shadowed_definitions_remain_in_inventory_without_positive_claims(tmp_path):
    from tests.integration.functional_sql.classification import public_exclusion
    from tests.integration.functional_sql.engine_overloads import engine_cases, engine_inventory

    entries = engine_inventory([])
    excluded = [entry for entry in entries if entry.get("exclusion")]
    assert len(excluded) == 19
    assert all(entry["exclusion"]["sources"] for entry in excluded)
    assert not engine_cases(excluded)
    manifest = capability_manifest({"engine_logical_overloads": excluded}, [])
    assert all(row["verification"] == "EXCLUDED" for row in manifest["engine_logical_overloads"])
    summary = write_reports(
        tmp_path,
        [Outcome("bad-claim", "scalar", "PASS", logical_overloads=[excluded[0]["key"]])],
        {"engine_logical_overloads": excluded},
        {},
    )
    assert summary["engine_logical_overloads_tested"] == 0
    assert summary["engine_logical_overloads_positive"] == 0
    assert summary["engine_logical_overloads_excluded"] == 19
    assert summary["engine_logical_overloads_required"] == 0
    assert public_exclusion(FunctionSignature("year(DATETIME)", "INT", "Scalar", 50010))
    assert public_exclusion(FunctionSignature("year(DATETIME)", "INT", "Scalar", 99999)) is None
    assert public_exclusion(FunctionSignature("year(DATE)", "INT", "Scalar", 50010)) is None
    different_return = FunctionSignature("year(DATETIME)", "SMALLINT", "Scalar", 50010)
    assert public_exclusion(different_return) is None
    assert public_exclusion(FunctionSignature("add(TINYINT,TINYINT)", "TINYINT", "Scalar", 0))
    assert public_exclusion(FunctionSignature("add(INT,INT)", "INT", "Scalar", 0)) is None
    assert public_exclusion(FunctionSignature("add(BOOLEAN,BOOLEAN)", "BOOLEAN", "Scalar", 0)) \
        is None


def test_public_unix_timestamp_recipes_cross_the_32_bit_boundary():
    from tests.integration.functional_sql.engine_overloads import engine_cases, engine_inventory

    entries = [entry for entry in engine_inventory([]) if entry["name"] == "unix_timestamp"]
    cases = engine_cases(entries)
    assert len(cases) == 4
    deterministic = [case for case in cases if case.oracle != "unix_now"]
    assert len(deterministic) == 3
    for case in deterministic:
        assert case.expected == [[2208988800]]
        assert case.setup == ("SET time_zone = '+00:00'",)
        assert case.expected_column_types == (8,)
        case.check([[2208988800]], [("unix_timestamp", 8)])
        with pytest.raises(AssertionError):
            case.check([[0]], [("unix_timestamp", 8)])


def test_array_null_or_empty_distinguishes_null_arrays_and_null_elements():
    from tests.integration.functional_sql.functions import function_cases

    case = function_cases([FunctionSignature("null_or_empty(ANY_ARRAY)", "BOOLEAN", "Scalar")])[0]
    assert "CAST([] AS ARRAY<BIGINT>)" in case.sql and case.expected == [[1]]
    assert [(effect.sql, effect.expected) for effect in case.effects] == [
        ("SELECT null_or_empty([CAST(1 AS BIGINT)])", [[0]]),
        ("SELECT null_or_empty([CAST(NULL AS BIGINT)])", [[0]]),
        ("SELECT null_or_empty(CAST(NULL AS ARRAY<BIGINT>))", [[1]]),
    ]


def test_operator_value_success_does_not_prove_the_registered_return_type():
    from tests.integration.functional_sql.contracts import UnverifiedBinding
    from tests.integration.functional_sql.functions import scalar_case

    case = scalar_case(FunctionSignature("add(INT, INT)", "INT", "Scalar"))
    case.check([[2]], [("add", 3)])
    with pytest.raises(UnverifiedBinding, match="Value oracle passed"):
        case.check([[2]], [("add", 8)])
    with pytest.raises(AssertionError, match="expected") as failure:
        case.check([[99]], [("add", 8)])
    assert not isinstance(failure.value, UnverifiedBinding)


def test_operator_recipes_bind_promoted_smallint_and_int_definitions():
    from tests.integration.functional_sql.functions import scalar_case

    for name in ("add", "subtract", "multiply"):
        for target, argument, code in (("SMALLINT", "TINYINT", 2), ("INT", "SMALLINT", 3)):
            case = scalar_case(FunctionSignature(f"{name}({target}, {target})", target, "Scalar"))
            assert case.sql.count(" AS " + argument + ")") == 2
            assert case.binding_column_types == (code,)
            assert "ExpressionAnalyzer.java" in case.binding_source
            case.check([[case.expected]], [(name, code)])
    large = scalar_case(FunctionSignature("add(LARGEINT, LARGEINT)", "LARGEINT", "Scalar"))
    assert large.binding_column_types == (254,)
    large.check([["2"]], [("add", 254)])
    null_case = scalar_case(FunctionSignature("add(BOOLEAN, BOOLEAN)", "BOOLEAN", "Scalar", 0))
    assert null_case.sql == "SELECT NULL + NULL"
    null_case.check([[None]], [("add", 1)])
    remainder = scalar_case(FunctionSignature("mod(FLOAT, FLOAT)", "FLOAT", "Scalar", 10255))
    assert remainder.sql.startswith("SELECT mod(")
    remainder.check([[1.0]], [("mod", 4)])


def test_return_width_check_is_preserved_in_catalog_and_scalar_source_recipes():
    from tests.integration.functional_sql.functions import function_cases
    from tests.integration.functional_sql.logical_overloads import logical_cases

    cases = function_cases([FunctionSignature("get_json_int(JSON, VARCHAR)", "BIGINT", "Scalar")])
    assert cases[0].expected_column_types == (8,)
    entry = {
        "advertised_name": True, "name": "year", "arguments": ["DATETIME"],
        "signature": "year(DATETIME)", "return_type": "SMALLINT", "fid": 50009,
        "key": "year-smallint", "source": "pinned-source",
    }
    assert logical_cases([entry])[0].expected_column_types == (2,)


def test_reports_count_concrete_engine_and_scalar_namespaces_separately(tmp_path):
    outcomes = [Outcome("ok", "complex", "PASS", logical_overloads=["scalar", "engine"])]
    inventory = {
        "scalar_logical_overloads": [{"key": "scalar"}],
        "engine_logical_overloads": [{"key": "engine"}, {"key": "untested"}],
    }
    summary = write_reports(tmp_path, outcomes, inventory, {})
    assert summary["scalar_logical_overloads_tested"] == 1
    assert summary["engine_logical_overloads_tested"] == 1
    assert summary["engine_logical_overloads_positive"] == 1
    assert summary["engine_logical_overloads_total"] == 2
    manifest = capability_manifest(inventory, [])
    assert all(entry["verification"] == "BLOCKED" for entry in manifest["engine_logical_overloads"])


def test_aggregate_recipes_preserve_declared_map_and_array_element_types():
    from tests.integration.functional_sql.aggregates import base_arguments
    from tests.integration.functional_sql.functions import typed

    args, expected, oracle = base_arguments("map_agg", ["DATE", "BOOLEAN"])
    assert args == ["DATE '2024-01-02'", "CAST(TRUE AS BOOLEAN)"]
    assert expected == {"2024-01-02": 1} and oracle == "mysql_map"
    args, expected, oracle = base_arguments("array_unique_agg", ["ARRAY<DATE>"])
    assert args == ["[DATE '2024-01-02']"] and expected == ["2024-01-02"]
    assert typed("ARRAY<DECIMAL32>", [1, 2]) == "[CAST(1 AS DECIMAL(9,4)),CAST(2 AS DECIMAL(9,4))]"
    assert typed("VARBINARY", 1) == "CAST(unhex('31') AS VARBINARY)"


@pytest.mark.parametrize(
    ("sql_type", "literal", "element"),
    [
        ("BOOLEAN", "CAST(TRUE AS BOOLEAN)", 1),
        ("CHAR", "CAST('a' AS CHAR)", "a"),
        ("DATE", "DATE '2024-01-02'", "2024-01-02"),
        ("DECIMAL32", "CAST(1 AS DECIMAL(9,4))", 1),
    ],
)
def test_array_aggregate_uses_the_declared_element_type(sql_type, literal, element):
    from tests.integration.functional_sql.aggregates import aggregate_case

    function = FunctionSignature(f"array_agg_distinct({sql_type})", "INVALID_TYPE", "Aggregate")
    case = aggregate_case(function, [function])
    assert case is not None
    assert literal in case.sql
    case.check([[json.dumps([element])]], [])
    with pytest.raises(AssertionError):
        case.check([[json.dumps(["wrong"])]], [])


@pytest.mark.parametrize(
    ("signature", "message"),
    [
        ("any_value(TIME)", "Time Type can not used"),
        ("avg(DATE)", "avg requires a numeric parameter"),
        ("avg_if(DATETIME, BOOLEAN)", "avg requires a numeric parameter"),
        ("approx_count_distinct(JSON)", "not support this aggregation function"),
        ("max(HLL)", "not support this aggregation function"),
        ("bitmap_union_int(LARGEINT)", "BITMAP_UNION_INT params only support Integer"),
    ],
)
def test_registered_but_rejected_aggregate_inputs_require_the_pinned_analyzer_refusal(
    signature, message
):
    from tests.integration.functional_sql.aggregates import aggregate_case

    function = FunctionSignature(signature, "BIGINT", "Aggregate")
    case = aggregate_case(function, [function])
    assert case.error_code == 1064
    assert case.error_contains == message
    assert "FunctionAnalyzer.java" in case.contract_source
    assert not case.prerequisite


def test_metric_aggregate_oracles_read_percentile_state_and_integer_cardinality():
    from tests.integration.functional_sql.aggregates import aggregate_case

    integer = FunctionSignature("bitmap_union_int(INT)", "BIGINT", "Aggregate")
    percentile = FunctionSignature("any_value(PERCENTILE)", "PERCENTILE", "Aggregate")
    integer_case = aggregate_case(integer, [integer])
    percentile_case = aggregate_case(percentile, [percentile])
    assert "bitmap_count" not in integer_case.sql
    assert "percentile_approx_raw" in percentile_case.sql
    integer_case.check([[1]], [])
    percentile_case.check([[1.0]], [])
    for case in (integer_case, percentile_case):
        with pytest.raises(AssertionError):
            case.check([[None]], [])
        with pytest.raises(AssertionError):
            case.check([[6]], [])


def test_numeric_aggregate_value_cannot_prove_another_registered_return_width():
    from tests.integration.functional_sql.aggregates import aggregate_case
    from tests.integration.functional_sql.contracts import UnverifiedBinding
    from tests.integration.functional_sql.functions import with_return_type_check

    function = FunctionSignature("any_value(INT)", "INT", "Aggregate")
    case = with_return_type_check(aggregate_case(function, [function]), function)
    case.check([[1]], [("value", 3)])
    with pytest.raises(UnverifiedBinding):
        case.check([[1]], [("value", 8)])
    with pytest.raises(AssertionError):
        case.check([[99]], [("value", 3)])


@pytest.mark.parametrize(
    ("name", "sql_type", "expected"),
    [
        ("corr", "INT", 1),
        ("corr_if", "BOOLEAN", 1),
        ("covar_pop", "INT", 35 / 12),
        ("covar_samp", "INT", 7 / 2),
        ("covar_pop_if", "BOOLEAN", 5 / 36),
        ("covar_samp", "BOOLEAN", 1 / 6),
    ],
)
def test_statistical_aggregate_uses_varying_columns_with_a_nonzero_oracle(
    name, sql_type, expected
):
    from tests.integration.functional_sql.aggregates import aggregate_case

    arguments = f"{sql_type}, {sql_type}" + (", BOOLEAN" if name.endswith("_if") else "")
    function = FunctionSignature(f"{name}({arguments})", "DOUBLE", "Aggregate")
    case = aggregate_case(function, [function])
    assert "CAST(n AS" in case.sql
    case.check([[expected]], [])
    with pytest.raises(AssertionError):
        case.check([[0]], [])


def test_ds_hll_accumulate_requires_isolation_even_without_an_explicit_state_suffix():
    from tests.integration.functional_sql.aggregates import STATE_PREREQUISITE, aggregate_case

    for signature in ("ds_hll_accumulate(INT)", "ds_hll_accumulate_if(INT, BOOLEAN)"):
        function = FunctionSignature(signature, "VARBINARY", "Aggregate")
        case = aggregate_case(function, [function])
        assert case.prerequisite == STATE_PREREQUISITE
        assert case.error_code is None


@pytest.mark.parametrize(
    ("name", "sql_type", "expected", "wrong"),
    [
        ("min_by", "INT", 1, 2),
        ("max_by", "INT", 2, 1),
        ("min_by", "BOOLEAN", [[False]], [[True]]),
        ("max_by", "JSON", {"a": 2}, {"a": 1}),
        ("min_by", "HLL", 0, 1),
        ("max_by", "BITMAP", 2, 1),
    ],
)
def test_by_key_aggregate_checks_selection_with_nonconstant_columns(
    name, sql_type, expected, wrong
):
    from tests.integration.functional_sql.aggregates import aggregate_case

    function = FunctionSignature(f"{name}({sql_type}, INT)", sql_type, "Aggregate")
    case = aggregate_case(function, [function])
    assert case.sql.count("CASE WHEN n%2=0") == 2
    observed = [[json.dumps(expected)]] if sql_type == "JSON" else (
        expected if sql_type == "BOOLEAN" else [[expected]]
    )
    bad = [[json.dumps(wrong)]] if sql_type == "JSON" else (
        wrong if sql_type == "BOOLEAN" else [[wrong]]
    )
    case.check(observed, [])
    with pytest.raises(AssertionError):
        case.check(bad, [])


@pytest.mark.parametrize("wire", ["{1:2}", '{"1":2}', "{1:2.0}"])
def test_native_mysql_map_oracle_accepts_numeric_keys_without_losing_value_types(wire):
    case = Case("map", "complex", "SELECT map{1:2}", {"1": 2}, "mysql_map")
    case.check([[wire]], [])
    with pytest.raises(AssertionError):
        case.check([["{1:True}"]], [])
    with pytest.raises(AssertionError):
        case.check([["{1:'2'}"]], [])


@pytest.mark.parametrize(
    "wire", ["{1:1,1:2}", '{"1":1,"1":2}', "{1:2,'1':2}", "[1,2]", "{1:danger()}"]
)
def test_native_mysql_map_oracle_rejects_duplicate_ambiguous_and_nonliteral_results(wire):
    case = Case("map", "complex", "SELECT map{1:2}", {"1": 2}, "mysql_map")
    with pytest.raises((AssertionError, ValueError)):
        case.check([[wire]], [])


def test_native_mysql_map_oracle_preserves_null_and_nested_values():
    case = Case("map", "complex", "SELECT map", {"1": None, "2": [1, None]}, "mysql_map")
    case.check([["{1:null,2:[1,null]}"]], [])
    with pytest.raises(AssertionError):
        case.check([["{1:0,2:[1,null]}"]], [])


def test_collapsed_array_and_null_window_types_cannot_claim_concrete_coverage():
    from tests.integration.functional_sql.functions import scalar_case
    from tests.integration.functional_sql.window_functions import window_case

    reverse = FunctionSignature("reverse(INVALID_TYPE)", "INVALID_TYPE", "Scalar")
    assert scalar_case(reverse).signatures == ()
    window = FunctionSignature("lag(NULL_TYPE)", "NULL_TYPE", "Aggregate")
    assert window_case(window).prerequisite is not None


def test_json_oracle_distinguishes_booleans_and_accepts_equivalent_json_numbers():
    multiset = Case("numeric", "complex", "SELECT 1", [1, 2, 3], "json_multiset")
    multiset.check([["[3.0000,1.0,2.00]"]], [])
    with pytest.raises(AssertionError):
        multiset.check([["[true,2,3]"]], [])
    with pytest.raises(AssertionError):
        Case("boolean", "complex", "SELECT 1", [True], "json").check([["[1]"]], [])


def test_aggregate_merge_uses_the_declared_intermediate_type_and_final_result():
    from tests.integration.functional_sql.aggregates import aggregate_case

    functions = [
        FunctionSignature("sum(INT)", "BIGINT", "Aggregate"),
        FunctionSignature("sum(DOUBLE)", "DOUBLE", "Aggregate"),
        FunctionSignature("sum_state(INT)", "BIGINT", "Scalar"),
        FunctionSignature("sum_state(DOUBLE)", "DOUBLE", "Scalar"),
        FunctionSignature("sum_merge(DOUBLE)", "DOUBLE", "Aggregate"),
        FunctionSignature("sum_union(DOUBLE)", "DOUBLE", "Aggregate"),
    ]
    for function in functions[-2:]:
        case = aggregate_case(function, functions)
        assert case is not None
        assert "CAST(1 AS DOUBLE)" in case.sql
        assert "CAST(1 AS INT)" not in case.sql
    incompatible = FunctionSignature("sum_merge(DOUBLE)", "BIGINT", "Aggregate")
    assert aggregate_case(incompatible, functions) is None


def test_metric_cardinality_does_not_reuse_hll_input_for_the_varchar_signature():
    from tests.integration.functional_sql.metric_functions import metric_case

    case = metric_case(FunctionSignature("hll_cardinality(VARCHAR)", "BIGINT", "Scalar"))
    assert "hll_cardinality(hll_serialize(" in case.sql


async def test_sql_success_cannot_pass_without_the_expected_data_effect():
    from tests.integration.functional_sql.run import execute_case

    class Cursor:
        description = [("n", 8)]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def execute(self, sql, parameters=None):
            self.rows = [(0,)] if sql.startswith("SELECT COUNT") else []

        async def fetchall(self):
            return self.rows

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    class TestTarget:
        connect = AsyncMock(return_value=Connection())

        @staticmethod
        def safe(sql):
            return sql

    case = Case(
        "insert",
        "dml",
        "INSERT INTO t VALUES (1)",
        [],
        effects=(EffectCheck("SELECT COUNT(*) FROM t", [[1]]),),
    )
    outcome = await execute_case(case, TestTarget(), "fixture")
    assert outcome.status == "FAIL"
    assert "got [[0]]" in outcome.detail
    assert outcome.observed_effects[0]["status"] == "FAIL"
    assert outcome.observed_effects[0]["rows"] == [[0]]


async def test_effect_timeout_never_reuses_a_connection_with_an_unread_response():
    from tests.integration.functional_sql.run import execute_case

    executed = []

    class Cursor:
        description = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def execute(self, sql, parameters=None):
            executed.append(sql)
            if sql == "SELECT n FROM task_output":
                raise TimeoutError("late response")

        async def fetchall(self):
            return []

    class Connection:
        closed = False

        def cursor(self):
            return Cursor()

        def close(self):
            self.closed = True

    connection = Connection()
    target = Target()
    from unittest.mock import patch

    with patch.object(Target, "connect", AsyncMock(return_value=connection)):
        outcome = await execute_case(
            Case(
                "task",
                "task",
                "SUBMIT TASK AS SELECT 1",
                [],
                effects=(EffectCheck("SELECT n FROM task_output", [[1]], timeout_seconds=30),),
            ),
            target,
            "fixture",
        )
    assert outcome.status == "FAIL"
    assert connection.closed
    assert executed.count("SELECT n FROM task_output") == 1


async def test_failed_task_run_is_not_a_successful_execution():
    from tests.integration.functional_sql.tasks import TaskFixtures

    api = AsyncMock()
    api.request.side_effect = [
        {"id": "fixture-run"},
        {"run": {"state": "failed"}, "node_runs": [{"state": "failed"}]},
    ]
    with pytest.raises(AssertionError, match="ended in failed"):
        await TaskFixtures(api, "fixture").execute_graph()


@pytest.mark.parametrize(
    "change",
    [
        {"target": "127.0.0.1:4426"},
        {"api_url": "http://127.0.0.1:8026"},
        {"user": "root"},
        {"database": "NOVA_SYSTEM"},
        {"models": [{"name": "customer_model", "database_name": "nova_sql_test_123456abcdef"}]},
        {"tasks": ["customer_task"]},
    ],
)
def test_fixture_recovery_rejects_other_principals_targets_and_unowned_objects(change):
    from tests.integration.functional_sql.recovery import validate_journal

    journal = {
        "target": "127.0.0.1:4406",
        "api_url": "http://127.0.0.1:8000",
        "user": "nova_admin",
        "database": "nova_sql_test_123456abcdef",
        **change,
    }
    with pytest.raises(AssertionError):
        validate_journal(journal, Target())


async def test_partial_ml_training_keeps_verified_results_and_cleanup_identity():
    from tests.integration.functional_sql.ml import MLFixtures

    model = {
        "model_id": "fixture-id",
        "model_name": "",
        "model_type": "regression",
        "version": 1,
        "training_rows": 36,
        "status": "succeeded",
    }

    async def request(method, path, **kwargs):
        if path == "/api/v1/ml/aliases":
            return {}
        if model["model_name"]:
            raise RuntimeError("classification dependency unavailable")
        model["model_name"] = kwargs["json"]["sql"].split()[2]
        return [{"success": True, "columns": list(model), "rows": [list(model.values())]}]

    api = AsyncMock()
    api.request.side_effect = request
    fixtures = MLFixtures(api, "nova_sql_test_123456abcdef")
    with pytest.raises(RuntimeError, match="dependency unavailable"):
        await fixtures.provision()
    assert len(fixtures.evidence) == 1
    assert fixtures.evidence[0]["kind"] == "regression"
    assert {case.id for case in fixtures.cases} == {"ml.predict_regression", "ml.materialize_relay"}
    assert len(fixtures.models) == 2
    assert fixtures.models[0]["id"] == "fixture-id"


def test_geospatial_registry_has_cases_for_all_pinned_public_functions():
    from dataclasses import asdict

    from tests.integration.functional_sql.engine_overloads import engine_cases, engine_inventory
    from tests.integration.functional_sql.functions import function_cases
    from tests.integration.functional_sql.geospatial import geospatial_data_cases
    from tests.integration.functional_sql.logical_overloads import logical_cases, scalar_inventory

    inventory = json.loads(
        (Path(__file__).parents[1] / 'integration/functional_sql/baseline.json').read_text()
    )
    functions = [FunctionSignature(row['signature'], row['return_type'], row['kind'])
                 for row in inventory['function_signatures']]
    spatial = [function for function in functions if function.name.startswith('st_')]
    assert len(spatial) == 15
    cases = function_cases(spatial)
    assert len(cases) == 15 and all(case.group == 'geospatial' and case.effects for case in cases)
    assert {key for case in cases for key in case.signatures} == {f.key for f in spatial}
    assert len([case for case in engine_cases(engine_inventory(functions))
                if case.group == 'geospatial']) == 15
    assert len([case for case in logical_cases(scalar_inventory(functions))
                if case.group == 'geospatial']) == 15
    json.dumps([asdict(case) for case in cases + geospatial_data_cases()])


@pytest.mark.parametrize('rows', [[], [[1, 'Jakarta']], [[1, 'Jakarta', None]],
                                 [[1, 'Jakarta', 111.0]], [[1, 'Jakarta', True]],
                                 [[1, 'Jakarta', float('nan')]], [[1, 'Jakarta', '12.5']]])
def test_geospatial_numeric_rows_oracle_rejects_bad_values_and_shapes(rows):
    case = Case('distance', 'geospatial', 'SELECT distance', [[1, 'Jakarta', 12.5]], 'numeric_rows')
    with pytest.raises(AssertionError):
        case.check(rows, [])


def test_geospatial_distance_oracle_checks_units_antimeridian_and_symmetry():
    import math

    from tests.integration.functional_sql.geospatial import EARTH_RADIUS_METERS, haversine_meters

    assert haversine_meters(0, 0, 0, 0) == 0
    assert math.isclose(haversine_meters(0, 0, 1, 0), math.pi * EARTH_RADIUS_METERS / 180)
    assert math.isclose(haversine_meters(179, 0, -179, 0), math.pi * EARTH_RADIUS_METERS / 90)
    assert math.isclose(haversine_meters(0, 90, 0, -90), math.pi * EARTH_RADIUS_METERS)
    assert haversine_meters(106.8272, -6.1754, 107.6191, -6.9175) == haversine_meters(
        107.6191, -6.9175, 106.8272, -6.1754
    )
