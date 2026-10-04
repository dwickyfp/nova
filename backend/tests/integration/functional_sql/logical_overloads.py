from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from .classification import public_exclusion
from .complex_functions import complex_case
from .conditional_functions import conditional_case
from .contracts import Case, FunctionSignature
from .functions import scalar_case, typed, with_return_type_check
from .geospatial import geospatial_case
from .metric_functions import metric_case

REGISTRY_PATH = Path(__file__).with_name("scalar_registry.json")


def sql_type(name: str) -> str:
    if name.startswith("ARRAY_"):
        return "ARRAY<" + name.removeprefix("ARRAY_") + ">"
    if name == "MAP_VARCHAR_VARCHAR":
        return "MAP<VARCHAR,VARCHAR>"
    return name


def scalar_inventory(functions: list[FunctionSignature]) -> list[dict]:
    from .inventory import ENGINE_COMMIT

    registry = json.loads(REGISTRY_PATH.read_text())
    if registry["engine_commit"] != ENGINE_COMMIT:
        raise RuntimeError("Scalar registry does not match the pinned engine")
    names = {function.name for function in functions if function.kind == "Scalar"}
    entries = []
    for row in registry["records"]:
        name = row["name"].lower()
        signature = name + "(" + ", ".join(sql_type(t) for t in row["arguments"]) + ")"
        entries.append(
            {
                **row,
                "name": name,
                "signature": signature,
                "key": hashlib.sha256(
                    f"scalar:{row['fid']}:{signature}:{row['return_type']}".encode()
                ).hexdigest()[:20],
                "source": registry["source"],
                "source_sha256": registry["source_sha256"],
                "advertised_name": name in names,
                "exclusion": public_exclusion(
                    FunctionSignature(signature, row["return_type"], "Scalar", row["fid"])
                ),
            }
        )
    return entries


def array_input(element_type: str) -> tuple[str, list]:
    if element_type == "BOOLEAN":
        values = [True, False, True]
    elif element_type == "DATE":
        values = ["2024-01-03", "2024-01-01", "2024-01-02"]
    elif element_type == "DATETIME":
        values = ["2024-01-03 00:00:00", "2024-01-01 00:00:00", "2024-01-02 00:00:00"]
    elif element_type in {"VARCHAR", "CHAR"}:
        values = ["c", "a", "b"]
    elif element_type == "JSON":
        values = [{"a": 3}, {"a": 1}, {"a": 2}]
    else:
        values = [3, 1, 2]
    expression = "[" + ",".join(typed(element_type, value) for value in values) + "]"
    return expression, values


def complex_recipe(entry: dict) -> Case | None:
    name = entry["name"]
    arguments = entry["arguments"]
    first_type = arguments[0] if arguments else ""
    if not first_type.startswith("ARRAY_"):
        return None
    element_type = first_type.removeprefix("ARRAY_")
    if element_type in {"UNKNOWN_TYPE", "BITMAP", "HLL", "PERCENTILE", "VARBINARY"}:
        return None
    array, values = array_input(element_type)
    expected = None
    oracle = "json"
    expression = f"{name}({array})"
    if name in {"array_min", "array_max"}:
        value = min(values) if name == "array_min" else max(values)
        oracle = "equals" if element_type in {"DATE", "DATETIME", "VARCHAR", "CHAR"} else "number"
        expected = [[value]] if oracle == "equals" else value
    elif name in {"array_sum", "array_avg"}:
        expected = sum(values) / (len(values) if name == "array_avg" else 1)
        oracle = "number"
    elif name in {"array_sort", "array_distinct", "reverse"}:
        if name == "array_sort":
            expected = (
                sorted(values, key=lambda item: item["a"])
                if element_type == "JSON"
                else sorted(values)
            )
        elif name == "reverse":
            expected = values[::-1]
        else:
            expected = list(dict.fromkeys(values)) if element_type != "JSON" else values
        # Distinct ordering is unspecified; sorting has its own overload test.
        if name == "array_distinct":
            oracle = "json_multiset"
    elif name in {"array_position", "array_contains"}:
        expression = f"{name}({array},{typed(element_type, values[1])})"
        expected = 2 if name == "array_position" else 1
        oracle = "number"
    elif name == "array_slice":
        expression = f"{name}({array},CAST(2 AS BIGINT)"
        if len(arguments) == 3:
            expression += ",CAST(2 AS BIGINT)"
        expression += ")"
        expected = values[1:]
    elif name == "array_difference":
        expected = [0] + [values[i] - values[i - 1] for i in range(1, len(values))]
    elif name == "array_concat":
        expression = f"{name}({array},{array})"
        expected = values * 2
    elif name in {"arrays_overlap", "array_contains_all", "array_contains_seq"}:
        expression = f"{name}({array},{array})"
        expected = 1
        oracle = "number"
    elif name == "array_intersect":
        expression = f"{name}({array},{array})"
        expected = list(dict.fromkeys(values))
        oracle = "json_multiset"
    elif name == "array_cum_sum":
        expected = [sum(values[:i]) for i in range(1, len(values) + 1)]
    elif name == "array_join":
        expression = f"{name}({array},','" + (",'null'" if len(arguments) == 3 else "") + ")"
        expected = [[",".join(str(value) for value in values)]]
        oracle = "equals"
    else:
        return None
    if element_type == "BOOLEAN" and oracle in {"json", "json_multiset"}:
        expected = [int(value) for value in expected]
    if element_type == "JSON" and oracle == "json":
        oracle = "mysql_json_array"
    return Case(
        "logical." + entry["key"],
        "complex",
        "SELECT " + expression,
        expected,
        oracle,
        contract_source=entry["source"],
        logical_overloads=(entry["key"],),
    )


def logical_cases(entries: list[dict]) -> list[Case]:
    cases = []
    for entry in entries:
        if not entry["advertised_name"] or entry.get("exclusion"):
            continue
        case = complex_recipe(entry)
        if case is None and not any(arg.startswith("ARRAY_") for arg in entry["arguments"]):
            signature = FunctionSignature(
                entry["signature"], entry["return_type"], "Scalar", entry["fid"]
            )
            case = (
                geospatial_case(signature)
                or conditional_case(signature)
                or metric_case(signature)
                or complex_case(signature)
                or scalar_case(signature)
            )
            if entry["name"] == "reverse" and entry["arguments"] == ["ANY_ARRAY"]:
                case = Case(
                    "reverse_array", "complex", "SELECT reverse([1,2,3])", [3, 2, 1], "json"
                )
            if case is not None:
                case = replace(
                    with_return_type_check(case, signature),
                    id="logical." + entry["key"],
                    signatures=(),
                    logical_overloads=(entry["key"],),
                    contract_source=entry["source"],
                )
        if case is not None:
            cases.append(case)
    return cases
