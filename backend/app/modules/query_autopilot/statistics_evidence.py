from __future__ import annotations

import re


def statistics_summary(columns: list[str], rows: list) -> dict:
    if not rows:
        return {"facts": {"statistics_missing": True}, "tables": []}
    summaries = []
    for row in rows:
        data = dict(zip(columns, row, strict=True))
        health = data.get("Healthy")
        health_match = re.fullmatch(r"([0-9.]+)%", str(health))
        numbers = dict(
            re.findall(
                r"\b(tableRowCount|tableRowCountInStatistics|deltaRowCount)=(\d+)",
                str(data.get("TableHealthyMetrics", "")),
            )
        )
        current = int(numbers["tableRowCount"]) if "tableRowCount" in numbers else None
        baseline = (
            int(numbers["tableRowCountInStatistics"])
            if "tableRowCountInStatistics" in numbers
            else None
        )
        summaries.append(
            {
                "database": data.get("Database"),
                "table": data.get("Table"),
                "health_percent": float(health_match.group(1)) if health_match else None,
                "row_count": current,
                "statistics_row_count": baseline,
                "growth_ratio": current / baseline if current is not None and baseline else None,
            }
        )
    healths = [s["health_percent"] for s in summaries if s["health_percent"] is not None]
    growth = [s["growth_ratio"] for s in summaries if s["growth_ratio"] is not None]
    facts: dict[str, float | bool] = {"statistics_missing": False}
    if healths:
        facts["statistics_stale"] = min(healths) < 70
    if growth:
        facts["table_growth_ratio"] = max(growth)
    return {"facts": facts, "tables": summaries}


def resource_summary(columns: list[str], rows: list) -> dict:
    metrics: dict[str, dict[str, float]] = {}
    for row in rows:
        data = dict(zip(columns, row, strict=True))
        if data.get("NAME") not in {
            "resource_group_cpu_use_ratio",
            "resource_group_cpu_limit_ratio",
            "resource_group_mem_in_use",
            "resource_group_mem_limit",
        }:
            continue
        metrics.setdefault(str(data["BE_ID"]), {})[data["NAME"]] = float(data["VALUE"])
    comparisons = []
    for values in metrics.values():
        for used, limit in (
            ("resource_group_cpu_use_ratio", "resource_group_cpu_limit_ratio"),
            ("resource_group_mem_in_use", "resource_group_mem_limit"),
        ):
            if used in values and values.get(limit, 0) > 0:
                comparisons.append(values[used] >= values[limit] * 0.9)
    return {
        "facts": {"saturated": any(comparisons)} if comparisons else {},
        "measurements": metrics,
        "causality": "concurrent_resource_snapshot_requires_query_queue_evidence",
    }
