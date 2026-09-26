"""Compare captured SQL results with the independent numeric oracle."""

from __future__ import annotations

from tests.benchmark.jev_multidomain.oracle import equivalent_rows


def query_matches(actual: dict, gold: dict) -> bool:
    columns = [str(c).rsplit(".", 1)[-1] for c in actual.get("columns", [])]
    required = gold["columns"]
    if not all(name in columns for name in required):
        return False
    indexes = [columns.index(name) for name in required]
    rows = actual.get("rows", [])
    if any(len(row) <= max(indexes) for row in rows):
        return False
    projected = [[row[index] for index in indexes] for row in rows]
    return equivalent_rows(projected, gold["rows"])


def execution_score(record: dict, expected: list[dict]) -> dict:
    matches = [
        {
            "metric": gold.get("metric", ", ".join(gold["columns"])),
            "correct": any(query_matches(actual, gold) for actual in record.get("queries", [])),
        }
        for gold in expected
    ]
    return {
        "applicable": bool(expected),
        "correct": all(item["correct"] for item in matches) if matches else None,
        "metrics": matches,
        "business_query_count": sum(bool(q.get("rows")) for q in record.get("queries", [])),
        "limitation": "Requires matching governed output aliases and complete oracle rows; "
        "equivalent renamed outputs need manual review.",
    }
