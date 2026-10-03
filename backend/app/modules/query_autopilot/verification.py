"""Pair retained plans and measured resources across a production action."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import mean

from app.modules.query_autopilot.evidence import plan_diff
from app.modules.query_autopilot.models import Availability, Evidence, utcnow


async def verification_signals(
    repo, family: str, cohort: str, applied: datetime, *, started: datetime | None = None,
) -> dict:
    started = started or applied
    records: list[Evidence] = []
    cursor = ""
    while True:
        page = await repo.page(
            "evidence", family_id=family, cohort_id=cohort, after=cursor, limit=1000
        )
        if not page:
            break
        records.extend(Evidence.model_validate(item) for item in page)
        cursor = page[-1]["id"]
    before, after = [], []
    for record in records:
        if record.effective_availability(utcnow()) != Availability.AVAILABLE:
            continue
        observed = datetime.fromisoformat(
            record.summary.get("observed_at", record.collected_at.isoformat())
        )
        if started - timedelta(minutes=30) <= observed < started:
            before.append(record)
        elif applied <= observed < applied + timedelta(minutes=30):
            after.append(record)

    def plans(values):
        result = {}
        for record in sorted(values, key=lambda item: item.collected_at):
            parameter = record.summary.get("parameter_digest")
            if record.kind == "plan" and parameter and record.summary.get("operators"):
                result[parameter] = record
        return result

    old_plans, new_plans = plans(before), plans(after)
    comparisons = []
    for parameter in sorted(old_plans.keys() & new_plans.keys()):
        old, new = old_plans[parameter], new_plans[parameter]
        comparisons.append(
            {
                "before_evidence_id": old.id,
                "after_evidence_id": new.id,
                "parameter_digest": parameter,
                "before": old.summary,
                "after": new.summary,
                "diff": plan_diff(old.summary, new.summary),
            }
        )

    def resources(values):
        metrics, ids, seen = {}, [], set()
        for record in values:
            if record.kind != "profile" or not record.query_ids or set(record.query_ids) & seen:
                continue
            seen.update(record.query_ids)
            facts = record.summary.get("facts", {})
            retained = False
            for key in (
                "cpu_ms",
                "execution_ms",
                "peak_memory_bytes",
                "spill_bytes",
                "scanned_rows",
            ):
                value = facts.get(key)
                if type(value) in {float, int} and math.isfinite(value) and value >= 0:
                    metrics.setdefault(key, []).append(value)
                    retained = True
            if retained:
                ids.append(record.id)
        return {
            "evidence_ids": ids,
            "metrics": {
                key: {"mean": mean(values), "sample_count": len(values)}
                for key, values in metrics.items()
            },
        }

    old_resources, new_resources = resources(before), resources(after)
    common = old_resources["metrics"].keys() & new_resources["metrics"].keys()
    reasons = []
    if not comparisons:
        reasons.append("parameter_matched_production_plans_unavailable")
    if not common:
        reasons.append("production_resource_comparison_unavailable")
    return {
        "availability": "available" if not reasons else "unavailable",
        "reasons": reasons,
        "plans": comparisons,
        "resources": {"before": old_resources, "after": new_resources},
        "resource_provenance": "selectively_sampled_execution_profiles",
        "causality": "observational_post_application_window",
    }
