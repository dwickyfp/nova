from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
import zlib
from dataclasses import dataclass, replace
from typing import Any

from .classification import public_exclusion
from .contracts import Case, EffectCheck, FunctionSignature


def argument_types(signature: str) -> list[str]:
    text = signature.split("(", 1)[1].rsplit(")", 1)[0]
    pieces: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char in "(<":
            depth += 1
        elif char in ")>":
            depth -= 1
        elif char == "," and depth == 0:
            pieces.append(text[start:index].strip())
            start = index + 1
    if text.strip():
        pieces.append(text[start:].strip())
    return pieces


def quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def concrete_type(raw: str) -> str:
    raw = raw.replace("...", "").strip()
    if raw.startswith("ARRAY<"):
        return "ARRAY<" + concrete_type(raw[6:-1]) + ">"
    if raw.startswith("DECIMAL"):
        return {
            "DECIMAL32": "DECIMAL(9,4)",
            "DECIMAL64": "DECIMAL(18,4)",
            "DECIMAL128": "DECIMAL(30,4)",
            "DECIMAL256": "DECIMAL(50,4)",
            "DECIMALV2": "DECIMALV2(18,4)",
        }.get(raw, "DECIMAL(18,4)")
    return {
        "ANY_ELEMENT": "BIGINT",
        "ANY_ARRAY": "ARRAY<BIGINT>",
        "ANY_MAP": "MAP<BIGINT,BIGINT>",
        "ANY_STRUCT": "STRUCT<k BIGINT>",
        "INVALID_TYPE": "BIGINT",
        "NULL_TYPE": "BIGINT",
    }.get(raw, raw)


def typed(raw_type: str, value: Any) -> str:
    sql_type = concrete_type(raw_type)
    if sql_type == "BITMAP":
        return f"to_bitmap({int(value)})"
    if sql_type == "HLL":
        return f"hll_hash({quote(str(value))})"
    if sql_type == "PERCENTILE":
        return f"percentile_hash({float(value)})"
    if sql_type == "TIME":
        origin = dt.datetime(2024, 1, 1)
        end = origin + dt.timedelta(seconds=float(value))
        return f"timediff('{end.isoformat(sep=' ')}','{origin.isoformat(sep=' ')}')"
    if sql_type == "VARBINARY":
        payload = value if isinstance(value, bytes) else str(value).encode("utf-8")
        return f"CAST(unhex('{payload.hex()}') AS VARBINARY)"
    if sql_type == "JSON":
        return f"parse_json({quote(json.dumps(value, separators=(',', ':')))})"
    if sql_type == "VARIANT":
        return f"CAST(parse_json({quote(json.dumps(value))}) AS VARIANT)"
    if sql_type.startswith("ARRAY"):
        values = value if isinstance(value, list) else [value]
        element_type = raw_type[6:-1] if raw_type.startswith("ARRAY<") else "BIGINT"
        return "[" + ",".join(typed(element_type, item) for item in values) + "]"
    if sql_type.startswith("MAP"):
        return "map{1:1}"
    if sql_type.startswith("STRUCT"):
        return "named_struct('k', 1)"
    if value is None:
        literal = "NULL"
    elif isinstance(value, bool):
        literal = "TRUE" if value else "FALSE"
    elif isinstance(value, str):
        literal = quote(value)
    else:
        literal = str(value)
    return f"CAST({literal} AS {sql_type})"


@dataclass(frozen=True)
class Recipe:
    arguments: tuple[Any, ...]
    expected: Any
    oracle: str = "equals"
    raw_arguments: bool = False


SCALARS: dict[str, Recipe] = {}


def register(names: str, arguments: tuple, expected: Any, oracle="equals", *, raw=False):
    for name in names.split():
        SCALARS[name] = Recipe(arguments, expected, oracle, raw)


register("abs positive negative", (1,), 1, "number")
SCALARS["negative"] = Recipe((1,), -1, "number")
register("acos asin atan", (0,), 0, "number")
SCALARS["acos"] = Recipe((1,), 0, "number")
register("cos cosh", (0,), 1, "number")
register("sin sinh tan tanh", (0,), 0, "number")
register("cot", (math.pi / 4,), 1, "number")
register("atan2", (0, 1), 0, "number")
register("ceil ceiling floor dceil dfloor round dround truncate", (1,), 1, "number")
register("exp dexp", (0,), 1, "number")
register("ln log log2 log10 dlog1 dlog10", (1,), 0, "number")
register("pow power dpow fpow", (2, 3), 8, "number")
register("sqrt dsqrt", (4,), 2, "number")
register("cbrt", (8,), 2, "number")
register("square", (3,), 9, "number")
register("sign", (-2,), -1, "number")
register("degrees", (0,), 0, "number")
register("radians", (180,), math.pi, "number")
register("pi", (), math.pi, "number")
register("e", (), math.e, "number")
register("add", (1, 1), 2, "number")
register("subtract", (3, 1), 2, "number")
register("multiply", (2, 3), 6, "number")
register("divide", (4, 2), 2, "number")
register("int_divide", (5, 2), 2, "number")
register("mod pmod fmod", (5, 2), 1, "number")
register("bitand", (6, 3), 2, "number")
register("bitor", (6, 3), 7, "number")
register("bitxor", (6, 3), 5, "number")
register("bitnot", (1,), -2, "number")
register("bit_shift_left bitshiftleft", (1, 2), 4, "number")
register(
    "bit_shift_right bitshiftright bit_shift_right_logical bitshiftrightlogical",
    (8, 2),
    2,
    "number",
)
register("bin", (5,), [["101"]])
register("conv", ("10", 10, 2), [["1010"]])
register("hex", ("a",), [["61"]])
register("unhex", ("61",), [["a"]])
register("hex_decode_binary", ("61",), [[{"hex": "61"}]])
register("hex_decode_string", ("61",), [["a"]])
register("ascii", ("abc",), 97, "number")
register("char", (65,), [["A"]])
register("lower lcase", ("AbC",), [["abc"]])
register("upper ucase", ("AbC",), [["ABC"]])
register("initcap", ("hello world",), [["Hello World"]])
register("length char_length character_length", ("abc",), 3, "number")
register("concat", ("a", "b"), [["ab"]])
register("concat_ws", (",", "a", "b"), [["a,b"]])
register("left strleft", ("abcd", 2), [["ab"]])
register("right strright", ("abcd", 2), [["cd"]])
register("substring substr", ("abcd", 2, 2), [["bc"]])
register("substring_index", ("a.b.c", ".", 2), [["a.b"]])
register("repeat", ("ab", 2), [["abab"]])
register("replace replace_old", ("abcabc", "b", "X"), [["aXcaXc"]])
register("reverse", ("abc",), [["cba"]])
register("trim ltrim rtrim", (" abc ",), [["abc"]])
SCALARS["ltrim"] = Recipe((" abc",), [["abc"]])
SCALARS["rtrim"] = Recipe(("abc ",), [["abc"]])
register("lpad", ("a", 3, "0"), [["00a"]])
register("rpad", ("a", 3, "0"), [["a00"]])
register("space", (3,), [["   "]])
register("strcmp", ("a", "a"), 0, "number")
register("instr locate strpos", ("abc", "b"), 2, "number")
SCALARS["locate"] = Recipe(("b", "abc"), 2, "number")
register("starts_with", ("abc", "ab"), 1, "number")
register("ends_with", ("abc", "bc"), 1, "number")
register("find_in_set", ("b", "a,b,c"), 2, "number")
register("field", ("b", "a", "b"), 2, "number")
register("split", ("a,b", ","), ["a", "b"], "json")
register("split_part", ("a,b,c", ",", 2), [["b"]])
register("append_trailing_char_if_absent", ("abc", "/"), [["abc/"]])
register("null_or_empty", ("",), 1, "number")
register("url_encode", ("a b",), [["a%20b"]])
register("url_decode", ("a%20b",), [["a b"]])
register("url_extract_host", ("https://example.com/a",), [["example.com"]])
register("url_extract_parameter", ("https://example.com/?x=one", "x"), [["one"]])
register("parse_url", ("https://example.com/a", "HOST"), [["example.com"]])
register("translate", ("abc", "ac", "XY"), [["XbY"]])
register("to_base64", ("abc",), [[base64.b64encode(b"abc").decode()]])
register("from_base64", ("YWJj",), [["abc"]])
register("base64_decode_binary", ("YWJj",), [[{"hex": "616263"}]])
register("base64_decode_string", ("YWJj",), [["abc"]])
register("crc32", ("abc",), zlib.crc32(b"abc"), "number")
register("sha2", ("abc", 256), [[hashlib.sha256(b"abc").hexdigest()]])
register("md5 md5sum", ("abc",), [[hashlib.md5(b"abc").hexdigest()]])
register("regexp_count", ("ababa", "a"), 3, "number")
register("regexp_extract", ("a12b", "([0-9]+)", 1), [["12"]])
register("regexp_extract_all", ("a12b34", "([0-9]+)", 1), ["12", "34"], "json")
register("regexp_replace", ("a12b34", "[0-9]+", "X"), [["aXbX"]])
register("regexp_split", ("a,b", ","), ["a", "b"], "json")
register("like", ("abc", "a%"), 1, "number")
register("regexp", ("abc", "^a"), 1, "number")
register("inet_aton", ("127.0.0.1",), 2130706433, "number")
register("format_bytes", (1024,), [["1.00 KB"]])
register("ngram_search ngram_search_case_insensitive", ("abc", "abc"), 1, "number")

register("year", ("2024-01-02",), 2024, "number")
register("month quarter", ("2024-01-02",), 1, "number")
register("day dayofmonth dayofyear", ("2024-01-02",), 2, "number")
register("dayofweek", ("2024-01-02",), 3, "number")
register("dayofweek_iso weekday", ("2024-01-02",), 2, "number")
SCALARS["weekday"] = Recipe(("2024-01-02",), 1, "number")
register("dayname", ("2024-01-02",), [["Tuesday"]])
register("monthname", ("2024-01-02",), [["January"]])
register("hour", ("2024-01-02 03:04:05",), 3, "number")
register("minute", ("2024-01-02 03:04:05",), 4, "number")
register("second", ("2024-01-02 03:04:05",), 5, "number")
register("week week_iso weekofyear", ("2024-01-07",), 1, "number")
register("yearweek", ("2024-01-07",), 202401, "number")
register("date to_date", ("2024-01-02 03:04:05",), [["2024-01-02"]])
register("datediff days_diff", ("2024-01-03", "2024-01-01"), 2, "number")
register("date_format", ("2024-01-02", "%Y-%m-%d"), [["2024-01-02"]])
register("str2date", ("2024-01-02", "%Y-%m-%d"), [["2024-01-02"]])
register("str_to_date", ("2024-01-02 03:04:05", "%Y-%m-%d %H:%i:%s"), [["2024-01-02 03:04:05"]])
register("date_trunc", ("day", "2024-01-02 03:04:05"), [["2024-01-02 00:00:00"]])
register("last_day", ("2024-02-02",), [["2024-02-29"]])
register("makedate", (2024, 2), [["2024-01-02"]])
register("to_iso8601", ("2024-01-02",), [["2024-01-02"]])
register("convert_tz", ("2024-01-02 00:00:00", "+00:00", "+07:00"), [["2024-01-02 07:00:00"]])
register("years_add", ("2024-01-02", 1), [["2025-01-02 00:00:00"]])
register("years_sub", ("2024-01-02", 1), [["2023-01-02 00:00:00"]])
register("months_add add_months", ("2024-01-02", 1), [["2024-02-02 00:00:00"]])
register("months_sub", ("2024-01-02", 1), [["2023-12-02 00:00:00"]])
register("days_add", ("2024-01-02", 1), [["2024-01-03 00:00:00"]])
register("days_sub", ("2024-01-02", 1), [["2024-01-01 00:00:00"]])
register("hours_add", ("2024-01-02", 1), [["2024-01-02 01:00:00"]])
register("hours_sub", ("2024-01-02", 1), [["2024-01-01 23:00:00"]])
register("seconds_add", ("2024-01-02", 1), [["2024-01-02 00:00:01"]])
register("seconds_sub", ("2024-01-02", 1), [["2024-01-01 23:59:59"]])
register("weeks_add", ("2024-01-02", 1), [["2024-01-09 00:00:00"]])
register("weeks_sub", ("2024-01-02", 1), [["2023-12-26 00:00:00"]])
register("quarters_add", ("2024-01-02", 1), [["2024-04-02 00:00:00"]])
register("quarters_sub", ("2024-01-02", 1), [["2023-10-02 00:00:00"]])


def with_return_type_check(case: Case, function: FunctionSignature) -> Case:
    if function.kind == "Scalar" and function.name in {
        "day", "dayofmonth", "hour", "minute", "month", "second", "year",
        "get_json_int", "unix_timestamp",
    }:
        # The registry has identical arguments with different integer return widths.
        mysql_type = {"TINYINT": 1, "SMALLINT": 2, "INT": 3, "BIGINT": 8}
        return replace(case, expected_column_types=(mysql_type[function.return_type],))
    if function.kind == "Aggregate" and case.error_code is None and not case.prerequisite:
        mysql_type = {
            "BOOLEAN": 1, "TINYINT": 1, "SMALLINT": 2, "INT": 3, "BIGINT": 8,
            "LARGEINT": 254, "FLOAT": 4, "DOUBLE": 5, "DATE": 10, "DATETIME": 12,
            "DECIMAL32": 246, "DECIMAL64": 246, "DECIMAL128": 246,
            "DECIMAL256": 246, "DECIMALV2": 246,
        }
        if function.return_type in mysql_type:
            return replace(
                case, binding_column_types=(mysql_type[function.return_type],),
                binding_source=(
                    "https://github.com/StarRocks/starrocks/blob/"
                    "4a9848edf03f5c936dac664b2d52527f48e72eb0/"
                    "fe/fe-core/src/main/java/com/starrocks/catalog/FunctionSet.java"
                ),
            )
    return case


def scalar_case(function: FunctionSignature) -> Case | None:
    if function.name == "null_or_empty" and argument_types(function.signature) == ["ANY_ARRAY"]:
        return Case(
            f"function.{function.key}", "complex",
            "SELECT null_or_empty(CAST([] AS ARRAY<BIGINT>))", [[1]],
            signatures=(function.key,), expected_column_types=(1,),
            effects=(
                EffectCheck("SELECT null_or_empty([CAST(1 AS BIGINT)])", [[0]]),
                EffectCheck("SELECT null_or_empty([CAST(NULL AS BIGINT)])", [[0]]),
                EffectCheck("SELECT null_or_empty(CAST(NULL AS ARRAY<BIGINT>))", [[1]]),
            ),
        )
    if function.name == "unix_timestamp":
        types = argument_types(function.signature)
        if not types:
            case = Case(
                f"function.{function.key}", "scalar", "SELECT unix_timestamp()",
                oracle="unix_now", signatures=(function.key,),
            )
        elif types in (["DATE"], ["DATETIME"], ["VARCHAR", "VARCHAR"]):
            arguments = (
                [typed(types[0], "2040-01-01")]
                if len(types) == 1 else [quote("2040-01-01"), quote("%Y-%m-%d")]
            )
            epoch = int(dt.datetime(2040, 1, 1, tzinfo=dt.UTC).timestamp())
            case = Case(
                f"function.{function.key}", "scalar",
                "SELECT unix_timestamp(" + ",".join(arguments) + ")", [[epoch]],
                setup=("SET time_zone = '+00:00'",), signatures=(function.key,),
            )
        else:
            return None
        return with_return_type_check(case, function)
    recipe = SCALARS.get(function.name)
    if not recipe:
        return None
    types = argument_types(function.signature)
    if types and types[-1] == "...":
        types[-1] = types[-2] + "..."
    values = list(recipe.arguments)
    if len(types) != len(values):
        if types and types[-1].endswith("...") and len(values) >= len(types):
            types.extend([types[-1]] * (len(values) - len(types)))
        elif function.name in {"ceil", "floor", "round", "truncate", "dround"} and len(types) == 2:
            values.append(0)
        else:
            return None
    expected, oracle = recipe.expected, recipe.oracle
    if function.name == "hex" and types == ["BIGINT"]:
        values = [97]
    if function.name == "reverse" and types == ["INVALID_TYPE"]:
        return Case(
            f"function.{function.key}",
            "scalar",
            "SELECT reverse([1,2,3])",
            [3, 2, 1],
            "json",
            signatures=(),
        )
    if function.name == "field" and types and types[0] != "VARCHAR":
        values = [2, 1, 2]
    if function.name == "to_iso8601" and types == ["DATETIME"]:
        expected = [["2024-01-02T00:00:00.000000"]]
    if function.name == "date_trunc" and types == ["VARCHAR", "DATE"]:
        expected = [["2024-01-02"]]
    try:
        arguments = (
            values
            if recipe.raw_arguments
            else [typed(sql_type, value) for sql_type, value in zip(types, values, strict=True)]
        )
    except (ValueError, TypeError):
        return None
    if function.name == "date_trunc":
        arguments[0] = quote(str(values[0]))
    operators = {
        "add": "+",
        "subtract": "-",
        "multiply": "*",
        "divide": "/",
        "int_divide": "DIV",
        "bitshiftleft": "BITSHIFTLEFT",
        "bitshiftright": "BITSHIFTRIGHT",
        "bitshiftrightlogical": "BITSHIFTRIGHTLOGICAL",
    }
    if function.name in operators:
        if function.name in {"add", "subtract", "multiply"} and types[0] in {"SMALLINT", "INT"}:
            input_type = {"SMALLINT": "TINYINT", "INT": "SMALLINT"}[types[0]]
            arguments = [typed(input_type, value) for value in values]
        expression = f"({arguments[0]} {operators[function.name]} {arguments[1]})"
    else:
        expression = f"{function.name}({', '.join(arguments)})"
    sql = "SELECT " + expression
    if (
        function.name in {"add", "subtract", "multiply"}
        and types == ["BOOLEAN", "BOOLEAN"]
    ):
        sql = f"SELECT NULL {operators[function.name]} NULL"
        expected, oracle = [[None]], "equals"
    binding_types = ()
    binding_source = None
    if function.name in operators or function.name == "mod":
        mysql_type = {
            "BOOLEAN": 1, "TINYINT": 1, "SMALLINT": 2, "INT": 3, "BIGINT": 8, "LARGEINT": 254,
            "FLOAT": 4, "DOUBLE": 5, "DECIMALV2": 246, "DECIMAL32": 246,
            "DECIMAL64": 246, "DECIMAL128": 246, "DECIMAL256": 246,
        }
        binding_types = (mysql_type[function.return_type],)
        binding_source = (
            "https://github.com/StarRocks/starrocks/blob/"
            "4a9848edf03f5c936dac664b2d52527f48e72eb0/"
            "fe/fe-core/src/main/java/com/starrocks/sql/analyzer/"
            + ("FunctionAnalyzer.java#L1368" if function.name == "mod" else
               "ExpressionAnalyzer.java#L1992")
        )
    return with_return_type_check(
        Case(
            f"function.{function.key}", "scalar", sql, expected, oracle,
            signatures=(function.key,), binding_column_types=binding_types,
            binding_source=binding_source,
        ),
        function,
    )


def function_cases(functions: list[FunctionSignature]) -> list[Case]:
    from .aggregates import aggregate_case
    from .complex_functions import complex_case
    from .conditional_functions import conditional_case
    from .geospatial import geospatial_case
    from .metric_functions import metric_case
    from .table_functions import table_case
    from .window_functions import window_case

    cases: list[Case] = []
    for function in functions:
        if public_exclusion(function):
            continue
        case = aggregate_case(function, functions)
        if case is None and function.kind == "Aggregate":
            case = window_case(function)
        if case is None and function.kind == "Scalar":
            case = geospatial_case(function) or scalar_case(function)
            if case is None:
                case = complex_case(function)
            if case is None:
                case = conditional_case(function)
            if case is None:
                case = metric_case(function)
        if case is None and function.kind == "Table":
            case = table_case(function)
        if case:
            cases.append(with_return_type_check(case, function))
    return cases
