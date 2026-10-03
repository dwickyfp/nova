"""Scoped workload observations for reviewed semantic proposals, excluding query values."""

from __future__ import annotations

import json
import math
from collections import defaultdict

from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.intelligence.contracts import Scope, SemanticRef, fingerprint


def _json(value, expected):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, expected) else None


def aggregate_usage(rows: list[dict], scope: Scope, ref: SemanticRef, definition: dict) -> dict:
    """No raw question, SQL, filter value, or unknown-scope row enters learning."""
    model = SemanticModelIR.from_ossie(definition)
    metrics = {metric.name for metric in model.metrics if metric.visibility == "public"}
    dimensions = {
        field.name for dataset in model.datasets for field in dataset.fields
        if field.kind.value == "dimension"
    }
    fields = {field.name for dataset in model.datasets for field in dataset.fields}
    operators = {"=", "!=", ">", ">=", "<", "<=", "IN", "LIKE"}
    groups = defaultdict(list)
    excluded = 0
    for row in rows[:2000]:
        version = row.get("security_context_version")
        if (
            row.get("owner_name") != scope.principal or row.get("active_role") != scope.active_role
            or type(version) is not int or version != scope.security_context_version
            or row.get("semantic_model_id") != ref.view_id
            or row.get("model_fingerprint") != ref.fingerprint
            or (row.get("semantic_version") is not None and (
                type(row.get("semantic_version")) is not int
                or row["semantic_version"] != ref.version
            ))
        ):
            excluded += 1
            continue
        selected_metrics = _json(row.get("metrics"), list)
        selected_dimensions = _json(row.get("dimensions"), list)
        filters = _json(row.get("filter_shape"), list)
        grain = row.get("time_grain")
        if (
            not selected_metrics or selected_dimensions is None or filters is None
            or len(selected_metrics) > 32 or len(selected_dimensions) > 32 or len(filters) > 32
            or any(not isinstance(name, str) or name not in metrics for name in selected_metrics)
            or any(
                not isinstance(name, str) or name not in dimensions for name in selected_dimensions
            )
            or (grain is not None and (
                not isinstance(grain, str)
                or grain not in {"day", "week", "month", "quarter", "year"}
            ))
            or any(
                not isinstance(item, dict) or not isinstance(item.get("field"), str)
                or item["field"] not in fields or not isinstance(item.get("operator"), str)
                or item["operator"] not in operators for item in filters
            )
            or type(row.get("succeeded")) not in {bool, int}
            or row.get("succeeded") not in {False, True}
        ):
            excluded += 1
            continue
        shape = {
            "metrics": sorted(set(selected_metrics)),
            "dimensions": sorted(set(selected_dimensions)),
            "filter_shape": sorted(
                [{"field": item["field"], "operator": item["operator"]} for item in filters],
                key=lambda item: (item["field"], item["operator"]),
            ),
            "time_grain": grain,
        }
        groups[json.dumps(shape, sort_keys=True)].append(row)
    patterns = []
    for encoded, items in sorted(groups.items()):
        shape = json.loads(encoded)
        successes = sum(bool(item["succeeded"]) for item in items)
        latency = [
            value for item in items
            if isinstance((value := item.get("execution_latency_ms")), int | float)
            and not isinstance(value, bool) and math.isfinite(value) and value >= 0
        ]
        suggestions = []
        if successes < len(items):
            suggestions.append("review_failed_workload")
        if successes >= 3:
            suggestions.append("consider_verified_query")
        patterns.append({
            "pattern_id": fingerprint(shape), **shape,
            "observations": len(items), "successes": successes,
            "mean_latency_ms": round(sum(latency) / len(latency), 3) if latency else None,
            "suggestions": suggestions,
        })
    payload = {
        "method": "scoped-semantic-usage-v1",
        "scope": scope.model_dump(exclude={"session_id"}),
        "semantic": ref.model_dump(), "patterns": patterns,
    }
    return {
        **payload, "digest": fingerprint(payload), "excluded_rows": excluded,
        "bounded": True, "review_required": True,
        "authority": "usage_observation",
    }


async def load_scoped_usage(scope: Scope, ref: SemanticRef, definition: dict) -> dict:
    rows = await agent_repository.list_semantic_usage(
        owner_name=scope.principal, active_role=scope.active_role,
        security_context_version=scope.security_context_version,
        semantic_model_id=ref.view_id, model_fingerprint=ref.fingerprint,
        semantic_version=ref.version, limit=2000,
    )
    return aggregate_usage(rows, scope, ref, definition)
