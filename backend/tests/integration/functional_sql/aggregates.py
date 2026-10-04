from __future__ import annotations

from statistics import pvariance, variance
from typing import Any

from .contracts import Case, FunctionSignature
from .functions import argument_types, concrete_type, quote, typed

BASES = set(
    [
        "any_value",
        "approx_count_distinct",
        "array_agg",
        "array_agg_distinct",
        "array_unique_agg",
        "avg",
        "bitmap_agg",
        "bitmap_intersect",
        "bitmap_union",
        "bitmap_union_count",
        "bitmap_union_int",
        "bool_or",
        "corr",
        "count",
        "covar_pop",
        "covar_samp",
        "ds_hll_accumulate",
        "ds_hll_count_distinct",
        "ds_theta_count_distinct",
        "group_concat",
        "hll_raw",
        "hll_raw_agg",
        "hll_union",
        "hll_union_agg",
        "max",
        "max_by",
        "min",
        "min_by",
        "multi_distinct_count",
        "multi_distinct_sum",
        "ndv",
        "percentile_approx",
        "percentile_approx_weighted",
        "percentile_cont",
        "percentile_disc",
        "percentile_disc_lc",
        "percentile_union",
        "std",
        "stddev",
        "stddev_pop",
        "stddev_samp",
        "sum",
        "var_pop",
        "var_samp",
        "variance",
        "variance_pop",
        "variance_samp",
        "map_agg",
        "sum_map",
    ]
)
SUFFIXES = ("_state_union", "_state_merge", "_combine", "_state", "_merge", "_union", "_if")
STATE_PREREQUISITE = "Aggregate-state trials require --isolated-stack"
ANALYZER_SOURCE = (
    "https://github.com/StarRocks/starrocks/blob/"
    "4a9848edf03f5c936dac664b2d52527f48e72eb0/"
    "fe/fe-core/src/main/java/com/starrocks/sql/analyzer/FunctionAnalyzer.java"
)


def aggregate_refusal(base: str, types: list[str]) -> str | None:
    if "TIME" in types:
        return "Time Type can not used"
    first = types[0] if types else ""
    non_numeric = first in {
        "BITMAP", "HLL", "PERCENTILE", "JSON", "VARBINARY", "VARIANT",
        "ANY_ARRAY", "ANY_MAP", "ANY_STRUCT", "ARRAY", "MAP", "STRUCT",
    } or first.startswith(("ARRAY<", "MAP<", "STRUCT<"))
    if base in {"sum", "avg"} and (non_numeric or first in {"DATE", "DATETIME"}):
        return base + " requires a numeric parameter"
    if base in {"min", "max"} and non_numeric:
        return "not support this aggregation function"
    if (
        base in {"ndv", "approx_count_distinct", "ds_theta_count_distinct", "ds_hll_count_distinct"}
        and non_numeric and first != "VARBINARY"
    ):
        return "not support this aggregation function"
    if base == "bitmap_union_int" and first not in {"TINYINT", "SMALLINT", "INT", "BIGINT"}:
        return "BITMAP_UNION_INT params only support Integer"
    if base in {"min_by", "max_by"} and len(types) == 2:
        key_type = types[1]
        if key_type in {
            "BITMAP", "HLL", "PERCENTILE", "JSON", "VARBINARY", "VARIANT",
            "ANY_ARRAY", "ANY_MAP", "ANY_STRUCT", "ARRAY", "MAP", "STRUCT",
        } or key_type.startswith(("ARRAY<", "MAP<", "STRUCT<")):
            return "not support order-by"
    return None


def family(name: str) -> tuple[str, str] | None:
    if name in BASES:
        return name, ""
    for suffix in SUFFIXES:
        if name.endswith(suffix) and name.removesuffix(suffix) in BASES:
            return name.removesuffix(suffix), suffix
    return None


def sample(sql_type: str) -> tuple[str, Any, str]:
    if sql_type in {"UNKNOWN_TYPE", "INVALID_TYPE", "NULL_TYPE"}:
        raise ValueError("Internal type has no public input constructor")
    if sql_type == "DATE":
        return "DATE '2024-01-02'", "2024-01-02", "equals"
    if sql_type == "DATETIME":
        return "CAST('2024-01-02 00:00:00' AS DATETIME)", "2024-01-02 00:00:00", "equals"
    if sql_type in {"VARCHAR", "CHAR"}:
        return f"CAST('a' AS {sql_type})", "a", "equals"
    if sql_type == "BOOLEAN":
        return "CAST(TRUE AS BOOLEAN)", True, "equals"
    if sql_type == "VARBINARY":
        return "CAST('a' AS VARBINARY)", {"hex": "61"}, "equals"
    if sql_type == "JSON":
        return "parse_json('{\"a\":1}')", {"a": 1}, "json"
    if sql_type in {"ANY_ARRAY", "ARRAY"}:
        return "CAST([1,2] AS ARRAY<BIGINT>)", [1, 2], "json"
    if sql_type.startswith("ARRAY<"):
        item_type = sql_type[6:-1]
        if item_type in {"BITMAP", "HLL", "PERCENTILE", "VARBINARY", "UNKNOWN_TYPE"}:
            raise ValueError("Array element requires its own public serialization oracle")
        expression, expected, _ = sample(item_type)
        if item_type == "BOOLEAN":
            expected = int(expected)
        return f"[{expression}]", [expected], "mysql_json_array" if item_type == "JSON" else "json"
    if sql_type in {"ANY_MAP", "MAP"}:
        return "map{1:1}", {"1": 1}, "mysql_map"
    if sql_type in {"ANY_STRUCT", "STRUCT"}:
        return "named_struct('k',CAST(1 AS BIGINT))", {"k": 1}, "json"
    return typed(sql_type, 1), 1, "number"


def paired_samples(sql_type: str) -> tuple[str, str, Any, Any, str]:
    lower, lower_value, oracle = sample(sql_type)
    if sql_type == "DATE":
        return lower, "DATE '2024-01-03'", lower_value, "2024-01-03", oracle
    if sql_type == "DATETIME":
        return (
            lower, "CAST('2024-01-03 00:00:00' AS DATETIME)",
            lower_value, "2024-01-03 00:00:00", oracle,
        )
    if sql_type in {"VARCHAR", "CHAR", "VARBINARY"}:
        upper_value = {"hex": "62"} if sql_type == "VARBINARY" else "b"
        return lower, typed(sql_type, "b"), lower_value, upper_value, oracle
    if sql_type == "BOOLEAN":
        return "CAST(FALSE AS BOOLEAN)", lower, False, True, "equals"
    if sql_type == "HLL":
        return "hll_empty()", lower, 0, 1, "number"
    if sql_type == "BITMAP":
        return lower, "bitmap_from_string('1,2')", 1, 2, "number"
    if sql_type == "JSON":
        return lower, "parse_json('{\"a\":2}')", lower_value, {"a": 2}, oracle
    if sql_type in {"ANY_MAP", "MAP"}:
        return lower, "map{2:2}", lower_value, {"2": 2}, oracle
    if sql_type in {"ANY_STRUCT", "STRUCT"}:
        return lower, "named_struct('k',CAST(2 AS BIGINT))", lower_value, {"k": 2}, oracle
    if sql_type in {"ANY_ARRAY", "ARRAY"}:
        return lower, "CAST([2,3] AS ARRAY<BIGINT>)", lower_value, [2, 3], oracle
    if sql_type.startswith("ARRAY<"):
        low, high, low_value, high_value, element_oracle = paired_samples(sql_type[6:-1])
        if sql_type[6:-1] == "BOOLEAN":
            low_value, high_value = int(low_value), int(high_value)
        return (
            f"[{low}]", f"[{high}]", [low_value], [high_value],
            "mysql_json_array" if element_oracle == "json" and sql_type[6:-1] == "JSON"
            else "json",
        )
    return lower, typed(sql_type, 2), lower_value, 2, oracle


def base_arguments(base: str, types: list[str]) -> tuple[list[str], Any, str]:
    args = [sample(item)[0] for item in types]
    first, expected, oracle = sample(types[0]) if types else ("", 1, "number")
    if base in {"max_by", "min_by"}:
        if len(types) != 2:
            raise ValueError("By-key aggregates require two declared inputs")
        value_low, value_high, low, high, oracle = paired_samples(types[0])
        key_low, key_high, *_ = paired_samples(types[1])
        args = [
            f"CASE WHEN n%2=0 THEN {value_low} ELSE {value_high} END",
            f"CASE WHEN n%2=0 THEN {key_low} ELSE {key_high} END",
        ]
        expected = high if base == "max_by" else low
    elif base in {"count"}:
        expected, oracle = 6, "number"
    elif base in {"sum", "multi_distinct_sum"}:
        expected, oracle = (1 if base == "multi_distinct_sum" else 6), "number"
    elif base in {
        "ndv",
        "approx_count_distinct",
        "multi_distinct_count",
        "ds_hll_count_distinct",
        "ds_theta_count_distinct",
        "bitmap_union_count",
        "hll_union_agg",
    }:
        expected, oracle = 1, "number"
    elif base in {
        "std",
        "stddev",
        "stddev_pop",
        "stddev_samp",
        "var_pop",
        "var_samp",
        "variance",
        "variance_pop",
        "variance_samp",
        "covar_pop",
        "covar_samp",
    }:
        expected, oracle = 0, "number"
    elif base == "corr":
        expected, oracle = 1, "number"
    elif base == "group_concat":
        if len(args) == 2:
            args[1] = quote(",")
        expected, oracle = [["a,a,a,a,a,a"]], "equals"
    elif base in {"array_agg", "array_agg_distinct"}:
        if len(types) != 1 or types[0] in {"BITMAP", "HLL", "PERCENTILE", "VARBINARY"}:
            raise ValueError("Array aggregate needs a verified element serialization oracle")
        element = sample(types[0])[1]
        if types[0] == "BOOLEAN":
            element = int(element)
        args = [first]
        expected, oracle = (
            [element] if base == "array_agg_distinct" else [element] * 6,
            "json",
        )
        if types[0] == "JSON":
            oracle = "mysql_json_array"
    elif base == "array_unique_agg":
        if len(types) != 1 or not (
            types[0].startswith("ARRAY<") or types[0] in {"ANY_ARRAY", "ARRAY"}
        ):
            raise ValueError("Unique array aggregate needs its declared array input")
        args = [first]
        expected, oracle = sample(types[0])[1:]
    elif base == "map_agg":
        if len(types) != 2 or any(
            item in {"BITMAP", "HLL", "PERCENTILE", "VARBINARY", "JSON", "VARIANT"}
            or item.startswith("ARRAY")
            or (item.startswith("ANY_") and item != "ANY_ELEMENT")
            for item in types
        ):
            raise ValueError("Map aggregate needs verified key and value serialization")
        key, value = sample(types[0])[1], sample(types[1])[1]
        if types[0] == "BOOLEAN":
            key = int(key)
        if types[1] == "BOOLEAN":
            value = int(value)
        expected, oracle = {str(key): value}, "mysql_map"
    elif base == "sum_map":
        args = ["map{'a':1}"]
        expected, oracle = {"a": 6}, "json"
    elif base.startswith("percentile_") and base != "percentile_union":
        args = [first, "0.5"] + (["10000"] if len(types) == 3 else [])
        if base == "percentile_approx_weighted":
            args = [first, "1", "0.5"] + (["10000"] if len(types) == 4 else [])
    if base in {"corr", "covar_pop", "covar_samp"}:
        args = [f"CAST(n AS {concrete_type(item)})" for item in types]
        values = [bool(n) for n in range(6)] if types[0] == "BOOLEAN" else list(range(6))
        expected = (
            1 if base == "corr" else
            pvariance(values) if base == "covar_pop" else variance(values)
        )
        oracle = "number"
    if base.startswith("ds_hll_") or base == "ds_theta_count_distinct":
        if len(args) > 1:
            args[1] = "12"
        if len(args) > 2:
            args[2] = quote("HLL_4")
    if oracle == "equals" and not isinstance(expected, list):
        expected = [[expected]]
    return args, expected, oracle


def state_input_types(
    function: FunctionSignature, functions: list[FunctionSignature], base: str, suffix: str
) -> list[str] | None:
    state_types = argument_types(function.signature)
    if not state_types or len(set(state_types)) != 1:
        return None
    candidates = []
    finalizes = suffix in {"_merge", "_state_merge"}
    for state in functions:
        if state.name != base + "_state" or state.return_type != state_types[0]:
            continue
        original = argument_types(state.signature)
        if original and original[-1] == "...":
            original.pop()
        if any(item in {"UNKNOWN_TYPE", "INVALID_TYPE", "NULL_TYPE"} for item in original):
            continue
        if finalizes and not any(
            item.name == base
            and argument_types(item.signature) == argument_types(state.signature)
            and item.return_type == function.return_type
            for item in functions
        ):
            continue
        candidates.append(original)
    # The state signature determines its intermediate type; the final result
    # alone cannot distinguish serialized states from primitive overloads.
    return min(candidates, key=lambda items: (len(items), items)) if candidates else None


def aggregate_case(
    function: FunctionSignature,
    functions: list[FunctionSignature],
    *,
    state_arguments: list[str] | None = None,
) -> Case | None:
    shape = family(function.name)
    if not shape:
        return None
    base, suffix = shape
    types = argument_types(function.signature)
    if types and types[-1] == "...":
        types.pop()
    original_types = types[:-1] if suffix == "_if" else types
    if suffix in {"_merge", "_union", "_state_merge", "_state_union"}:
        original_types = (
            state_arguments
            if state_arguments is not None
            else state_input_types(function, functions, base, suffix)
        )
        if original_types is None:
            return None
    if not original_types and base != "count":
        return None
    try:
        args, expected, oracle = base_arguments(base, original_types)
    except (ValueError, TypeError):
        return None
    parameters = ", ".join(args)
    if suffix == "_if":
        expression = f"{function.name}({parameters}{', ' if parameters else ''}TRUE)"
        sql = f"SELECT {expression} FROM numbers"
    elif suffix in {"_state", "_merge"}:
        sql = f"SELECT {base}_merge({base}_state({parameters})) FROM numbers"
    elif suffix in {"_combine", "_union"}:
        inner = (
            f"{base}_combine({parameters})"
            if suffix == "_combine"
            else (f"{base}_union({base}_state({parameters}))")
        )
        sql = f"SELECT {base}_merge(s) FROM (SELECT {inner} AS s FROM numbers) states"
    elif suffix == "_state_merge":
        sql = f"SELECT {base}_state_merge({base}_combine({parameters})) FROM numbers"
    elif suffix == "_state_union":
        sql = (
            f"SELECT {base}_state_merge({base}_state_union(s,s)) "
            f"FROM (SELECT {base}_combine({parameters}) AS s FROM numbers) states"
        )
        if base in {"sum", "count", "sum_map"}:
            expected = {"a": 12} if base == "sum_map" else 12
        if base == "group_concat":
            expected = [[",".join(["a"] * 12)]]
    else:
        sql = f"SELECT {base}({parameters}) FROM numbers"
    output_type = original_types[0] if original_types else ""
    refusal = aggregate_refusal(base, original_types) if suffix in {"", "_if"} else None
    if refusal:
        return Case(
            f"function.{function.key}", "aggregate", sql,
            error_code=1064, error_contains=refusal,
            signatures=(function.key,), contract_source=ANALYZER_SOURCE,
        )
    if base in {"bitmap_agg", "bitmap_union", "bitmap_intersect"} or (
        base in {"min", "max", "any_value", "min_by", "max_by"} and output_type == "BITMAP"
    ):
        inner = sql.replace(" FROM ", " AS v FROM ", 1)
        sql = f"SELECT bitmap_count(v) FROM ({inner}) result"
        expected, oracle = expected if base in {"min_by", "max_by"} else 1, "number"
    elif base in {"hll_raw", "hll_raw_agg", "hll_union"} or (
        base in {"min", "max", "any_value", "min_by", "max_by"} and output_type == "HLL"
    ):
        sql = f"SELECT hll_cardinality(v) FROM ({sql.replace(' FROM ', ' AS v FROM ', 1)}) result"
        expected, oracle = expected if base in {"min_by", "max_by"} else 1, "number"
    elif base == "bitmap_union_int":
        expected, oracle = 1, "number"
    elif base == "percentile_union" or (
        base in {"min", "max", "any_value", "min_by", "max_by"}
        and output_type == "PERCENTILE"
    ):
        inner = sql.replace(" FROM ", " AS v FROM ", 1)
        sql = f"SELECT percentile_approx_raw(v,0.5) FROM ({inner}) result"
        expected, oracle = expected if base in {"min_by", "max_by"} else 1, "number"
    elif base == "ds_hll_accumulate":
        inner = sql.replace(" FROM ", " AS v FROM ", 1)
        sql = f"SELECT ds_hll_estimate(v) FROM ({inner}) result"
        expected, oracle = 1, "number"
    return Case(
        f"function.{function.key}",
        "aggregate",
        sql,
        expected,
        oracle,
        prerequisite=STATE_PREREQUISITE
        if base == "ds_hll_accumulate" or suffix in {
            "_state", "_merge", "_combine", "_union", "_state_merge", "_state_union"
        }
        else None,
        signatures=(function.key,),
        contract_source=ANALYZER_SOURCE,
    )
