from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from .aggregates import aggregate_case
from .classification import public_exclusion
from .complex_functions import complex_case
from .conditional_functions import conditional_case
from .contracts import Case, FunctionSignature
from .functions import scalar_case, with_return_type_check
from .geospatial import geospatial_case
from .logical_overloads import complex_recipe
from .metric_functions import metric_case
from .table_functions import table_case
from .window_functions import window_case

REGISTRY_PATH = Path(__file__).with_name("engine_registry.json")


def definition_key(row: dict) -> str:
    definition = {key: value for key, value in row.items() if key not in {"catalog", "logical_key"}}
    return hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]


def engine_inventory(functions: list[FunctionSignature]) -> list[dict]:
    from .inventory import ENGINE_COMMIT

    registry = json.loads(REGISTRY_PATH.read_text())
    if registry["engine_commit"] != ENGINE_COMMIT:
        raise RuntimeError("Concrete engine registry does not match the pinned engine")
    inspector = Path(__file__).with_name("DumpFunctions.java")
    if hashlib.sha256(inspector.read_bytes()).hexdigest() != registry["inspector_sha256"]:
        raise RuntimeError("Concrete registry inspector differs from its recorded provenance")
    entries = []
    catalogs = set()
    for row in registry["records"]:
        if definition_key(row) != row["logical_key"]:
            raise RuntimeError("Concrete engine definition identity is inconsistent")
        signature = row["name"] + "(" + ", ".join(row["arguments"]) + ")"
        catalog = row["catalog"]
        catalogs.add(FunctionSignature(*catalog[:3]).key)
        entries.append(
            {
                **row,
                "key": row["logical_key"],
                "signature": signature,
                "source": registry["source"],
                "source_sha256": registry["function_set_sha256"],
                "exclusion": public_exclusion(engine_function(row)),
            }
        )
    keys = [entry["key"] for entry in entries]
    if not entries or len(keys) != len(set(keys)):
        raise RuntimeError("Concrete engine definitions are empty or duplicated")
    missing = {function.key for function in functions} - catalogs
    if missing:
        raise RuntimeError(
            f"Runtime catalog has {len(missing)} definitions absent from the pinned registry"
        )
    return entries


def engine_function(entry: dict) -> FunctionSignature:
    kind = entry["catalog"][2]
    arguments = list(entry["arguments"])
    if entry["varargs"]:
        arguments.append("...")
    return FunctionSignature(
        entry["name"] + "(" + ", ".join(arguments) + ")", entry["return_type"], kind, entry["fid"]
    )


def engine_cases(entries: list[dict]) -> list[Case]:
    functions = [engine_function(entry) for entry in entries]
    cases = []
    for entry, function in zip(entries, functions, strict=True):
        if public_exclusion(function):
            continue
        # Hidden names require their public caller path, not direct invocation.
        if entry["name"].startswith("__"):
            continue
        case = None
        if function.kind == "Aggregate":
            descriptor = entry.get("state_descriptor")
            case = aggregate_case(
                function, functions, state_arguments=descriptor["arguments"] if descriptor else None
            ) or window_case(function)
        elif function.kind == "Table":
            case = table_case(function)
        elif function.kind == "Scalar":
            if entry.get("state_descriptor") is not None:
                descriptor = entry.get("state_descriptor")
                case = aggregate_case(
                    function,
                    functions,
                    state_arguments=descriptor["arguments"] if descriptor else None,
                )
            elif entry["arguments"] and entry["arguments"][0].startswith("ARRAY<"):
                adapted = {
                    **entry,
                    "arguments": [
                        "ARRAY_" + item[6:-1] if item.startswith("ARRAY<") else item
                        for item in entry["arguments"]
                    ],
                }
                case = complex_recipe(adapted)
                if entry["name"] in {"all_match", "any_match", "array_to_bitmap"}:
                    case = metric_case(function) or complex_case(function)
            else:
                case = (
                    geospatial_case(function)
                    or conditional_case(function)
                    or metric_case(function)
                    or complex_case(function)
                    or scalar_case(function)
                )
            if entry["name"] == "reverse" and entry["arguments"] == ["ANY_ARRAY"]:
                case = Case(
                    "reverse_array", "complex", "SELECT reverse([1,2,3])", [3, 2, 1], "json"
                )
        if case is not None:
            cases.append(
                replace(
                    with_return_type_check(case, function),
                    id="engine." + entry["key"],
                    signatures=(),
                    logical_overloads=(entry["key"],),
                    contract_source=entry["source"],
                )
            )
    return cases
