"""Data provenance and completion checks for governed collaboration."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.common.sql_guard import strip_sql_comments


def sql_evidence_kind(sql: str) -> str:
    statement = strip_sql_comments(sql).strip()
    if re.match(r"(?:SHOW|DESC(?:RIBE)?|EXPLAIN)\b", statement, re.I) or re.search(
        r"\binformation_schema\s*\.", statement, re.I
    ):
        return "catalog"
    return "data"


def catalog_table(table: dict[str, Any]) -> bool:
    columns = {str(column).casefold() for column in table.get("columns") or []}
    return bool(columns) and (
        columns == {"database"}
        or any(column.startswith("tables_in_") for column in columns)
        or {"field", "type", "null", "key"} <= columns
    )


def nonredundant_business_tables(evidence: Any) -> dict[str, dict[str, Any]]:
    """Hide repeated projections only when their source, query scope, and cells agree."""
    tables = evidence.business_tables
    metadata = {item.evidence_id: item.metadata for item in evidence.items}

    def scope(key: str) -> dict[str, Any] | None:
        item = metadata.get(key, {})
        plan = item.get("semantic_plan")
        if not plan or not item.get("semantic_model_id"):
            return None
        return {
            "source": item.get("source_agent_id"),
            "model": item["semantic_model_id"],
            "fingerprint": item.get("model_fingerprint"),
            "plan": {name: value for name, value in plan.items()
                     if name not in {"metrics", "order_by", "limit"}},
        }

    kept = {}
    for key, table in tables.items():
        columns = table.get("columns") or []
        rows = table.get("rows") or []
        current_scope = scope(key)
        redundant = False
        if current_scope and columns and rows and not table.get("truncated"):
            for other_key, other in tables.items():
                other_columns = other.get("columns") or []
                if (other_key == key or other.get("truncated")
                        or scope(other_key) != current_scope
                        or not set(columns) <= set(other_columns)
                        or len(other_columns) == len(columns) and other_key not in kept):
                    continue
                positions = [other_columns.index(column) for column in columns]
                if any(len(row) != len(columns) for row in rows) or any(
                    len(row) != len(other_columns) for row in other.get("rows") or []
                ):
                    continue
                if Counter(tuple(str(cell) for cell in row) for row in rows) == Counter(
                    tuple(str(row[index]) for index in positions)
                    for row in other.get("rows") or []
                ):
                    redundant = True
                    break
        if not redundant:
            kept[key] = table
    return kept
