from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

from .classification import public_exclusion
from .contracts import FunctionSignature

ENGINE_COMMIT = "4a9848edf03f5c936dac664b2d52527f48e72eb0"
SOURCE_ROOT = f"https://github.com/StarRocks/starrocks/blob/{ENGINE_COMMIT}"
BASELINE_PATH = Path(__file__).with_name("baseline.json")


async def read_functions(connection) -> list[FunctionSignature]:
    items: list[FunctionSignature] = []
    async with connection.cursor() as cursor:
        for offset in range(0, 100_000, 400):
            await asyncio.wait_for(
                cursor.execute(f"SHOW FULL BUILTIN FUNCTIONS LIMIT 400 OFFSET {offset}"), timeout=30
            )
            rows = await cursor.fetchall()
            for row in rows:
                properties = json.loads(row[4]) if len(row) > 4 and row[4] else {}
                fid = properties.get("fid")
                items.append(
                    FunctionSignature(
                        str(row[0]), str(row[1]), str(row[2]), int(fid) if fid is not None else None
                    )
                )
            if len(rows) < 400:
                break
        else:
            raise RuntimeError("Function inventory pagination did not terminate")
    keys = [item.key for item in items]
    if not items or len(keys) != len(set(keys)):
        raise RuntimeError("Function inventory is empty or contains duplicate identities")
    return sorted(items, key=lambda item: (item.signature, item.return_type, item.kind))


def statement_rules() -> list[str]:
    path = Path(__file__).parents[3] / "app/sql_dialect/grammar/StarRocks.g4"
    source = path.read_text()
    block = source[source.index("\nstatement\n") : source.index("\n// show-predicate-clauses")]
    return re.findall(r"^\s*[:|]\s*(\w+)", block, re.MULTILINE)


def inventory_document(functions: list[FunctionSignature]) -> dict:
    from .engine_overloads import engine_inventory
    from .logical_overloads import scalar_inventory

    entries = [
        vars(item) | {"key": item.key, "exclusion": public_exclusion(item)} for item in functions
    ]
    rules = statement_rules()
    fingerprint = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
    return {
        "engine_commit": ENGINE_COMMIT,
        "sources": [
            f"{SOURCE_ROOT}/gensrc/script/functions.py",
            f"{SOURCE_ROOT}/fe/fe-core/src/main/java/com/starrocks/catalog/FunctionSet.java",
        ],
        "fingerprint": fingerprint,
        "function_names": len({item.name for item in functions}),
        "catalog_metadata_lossy": True,
        "catalog_metadata_source": (
            SOURCE_ROOT + "/fe/fe-core/src/main/java/com/starrocks/catalog/Function.java"
        ),
        "function_signatures": entries,
        "scalar_logical_overloads": scalar_inventory(functions),
        "engine_logical_overloads": engine_inventory(functions),
        "statement_rules": rules,
    }


def baseline_differences(document: dict) -> dict:
    if not BASELINE_PATH.exists():
        return {"missing_baseline": True}
    baseline = json.loads(BASELINE_PATH.read_text())
    old = {item["key"] for item in baseline["function_signatures"]}
    current = {item["key"] for item in document["function_signatures"]}
    return {
        "added": sorted(current - old),
        "removed": sorted(old - current),
        "added_statements": sorted(
            set(document["statement_rules"]) - set(baseline["statement_rules"])
        ),
        "removed_statements": sorted(
            set(baseline["statement_rules"]) - set(document["statement_rules"])
        ),
        "added_engine_overloads": sorted(
            {item["key"] for item in document.get("engine_logical_overloads", [])}
            - {item["key"] for item in baseline.get("engine_logical_overloads", [])}
        ),
        "removed_engine_overloads": sorted(
            {item["key"] for item in baseline.get("engine_logical_overloads", [])}
            - {item["key"] for item in document.get("engine_logical_overloads", [])}
        ),
    }
