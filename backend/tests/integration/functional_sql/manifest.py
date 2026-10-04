from __future__ import annotations

from dataclasses import asdict

from .contracts import Case
from .inventory import SOURCE_ROOT


def capability_manifest(
    inventory: dict, cases: list[Case], user_exclusions: list[dict] | None = None
) -> dict:
    signatures: dict[str, list[dict]] = {}
    statements: dict[str, list[dict]] = {}
    logical: dict[str, list[dict]] = {}
    for case in cases:
        evidence = asdict(case)
        for key in case.signatures:
            signatures.setdefault(key, []).append(evidence)
        for rule in case.statement_rules:
            statements.setdefault(rule, []).append(evidence)
        for key in case.logical_overloads:
            logical.setdefault(key, []).append(evidence)
    user_exclusions = user_exclusions or []
    excluded_rules = {
        rule for case in user_exclusions for rule in case["statement_rules"]
    }
    functions = []
    for item in inventory.get("function_signatures", []):
        mapped = signatures.get(item["key"], [])
        name = item["signature"].split("(", 1)[0]
        functions.append(
            {
                **item,
                "surface": "internal name" if name.startswith("__") else "catalog entry",
                "source": SOURCE_ROOT
                + (
                    "/gensrc/script/functions.py"
                    if item["kind"] == "Scalar"
                    else "/fe/fe-core/src/main/java/com/starrocks/catalog/FunctionSet.java"
                ),
                "cases": mapped,
                "verification": "EXCLUDED" if item.get("exclusion") else
                "REQUIRES_EXECUTION" if mapped else "BLOCKED",
                "blocker": None if mapped or item.get("exclusion") else
                "No signature-specific input and oracle",
            }
        )
    rules = [
        {
            "rule": rule,
            "source": "backend/app/sql_dialect/grammar/StarRocks.g4#statement",
            "surface": "Nova extension" if rule.startswith("nova") else "grammar alternative",
            "cases": statements.get(rule, []),
            "verification": "REQUIRES_EXECUTION" if rule in statements else
            "USER_EXCLUDED" if rule in excluded_rules else "BLOCKED",
            "blocker": None if rule in statements or rule in excluded_rules else
            "No executable runtime contract case",
        }
        for rule in inventory.get("statement_rules", [])
    ]
    return {
        "engine_commit": inventory.get("engine_commit"),
        "user_excluded_cases": user_exclusions,
        "function_signatures": functions,
        "scalar_logical_overloads": [
            {
                **entry,
                "cases": logical.get(entry["key"], []),
                "verification": "EXCLUDED" if entry.get("exclusion") else
                "REQUIRES_EXECUTION" if entry["key"] in logical else "BLOCKED",
            }
            for entry in inventory.get("scalar_logical_overloads", [])
        ],
        "engine_logical_overloads": [
            {
                **entry,
                "cases": logical.get(entry["key"], []),
                "verification": "EXCLUDED" if entry.get("exclusion") else
                "REQUIRES_EXECUTION" if entry["key"] in logical else "BLOCKED",
            }
            for entry in inventory.get("engine_logical_overloads", [])
        ],
        "statement_rules": rules,
        "claim": "Mapped recipes require execution; inventory presence does not prove support",
    }
