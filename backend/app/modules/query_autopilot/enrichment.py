from __future__ import annotations

from datetime import datetime, timedelta

from app.modules.query_autopilot.models import Availability, Evidence, utcnow


async def measured_facts(
    repo, family: str, cohort: str, start: datetime, end: datetime
) -> tuple[dict, tuple[str, ...]]:
    records: list[Evidence] = []
    after = ""
    while True:
        page = await repo.page(
            "evidence", family_id=family, cohort_id=cohort, after=after, limit=1000
        )
        if not page:
            break
        records.extend(Evidence.model_validate(row) for row in page)
        after = page[-1]["id"]
    active = [
        e
        for e in records
        if e.effective_availability(utcnow()) == Availability.AVAILABLE
        and start
        <= datetime.fromisoformat(e.summary.get("observed_at", e.collected_at.isoformat()))
        < end
    ]
    allowed = {
        "cpu_ms",
        "scanned_rows",
        "output_rows",
        "estimated_rows",
        "actual_rows",
        "plan_changed",
        "statistics_stale",
        "statistics_missing",
        "table_growth_ratio",
        "max_operator_ms",
        "median_operator_ms",
        "operator_instance_count",
        "queue_ms",
        "execution_ms",
        "compatible_aggregates",
        "mv_eligible",
        "memory_limit_exceeded",
        "query_timed_out",
    }
    result: dict[str, float | int | bool] = {}
    used = []
    seen_queries: set[str] = set()
    queue_queries = set()
    paired = (
        {"scanned_rows", "output_rows"},
        {"estimated_rows", "actual_rows"},
        {"max_operator_ms", "median_operator_ms", "operator_instance_count"},
        {"queue_ms", "execution_ms"},
    )
    for record in sorted(active, key=lambda e: e.collected_at):
        if record.kind == "profile":
            if set(record.query_ids) & seen_queries:
                continue
            seen_queries.update(record.query_ids)
        facts = record.summary.get("facts", {})
        for group in paired:
            if group & facts.keys():
                for key in group:
                    result.pop(key, None)
        if {"queue_ms", "execution_ms"} & facts.keys():
            queue_queries = set(record.query_ids)
        for key, value in facts.items():
            if key not in allowed or type(value) not in {float, int, bool}:
                continue
            if key == "cpu_ms":
                result[key] = result.get(key, 0) + value
            else:
                result[key] = value
            if record.id not in used:
                used.append(record.id)
    for record in sorted(active, key=lambda e: e.collected_at):
        saturated = record.summary.get("facts", {}).get("saturated")
        if type(saturated) is bool and queue_queries & set(record.query_ids):
            result["saturated"] = saturated
            used.append(record.id)
    plans = sorted(
        (
            e
            for e in records
            if e.kind == "plan"
            and e.availability in {Availability.AVAILABLE, Availability.EXPIRED}
            and e.collected_at >= start - timedelta(days=90)
            and e.summary.get("hash")
        ),
        key=lambda e: e.collected_at,
    )
    historical = [e for e in plans if e.collected_at < start]
    current = [
        e
        for e in plans
        if start <= e.collected_at < end
        and e.effective_availability(utcnow()) == Availability.AVAILABLE
    ]
    if current:
        parameter = current[-1].summary.get("parameter_digest")
        historical = [
            e for e in historical if parameter and e.summary.get("parameter_digest") == parameter
        ]
    if historical and current:
        result["plan_changed"] = historical[-1].summary["hash"] != current[-1].summary["hash"]
        used.extend([historical[-1].id, current[-1].id])
    # Cardinality is matched by native plan-node ID, never by nearby log time.
    if current:
        estimates = {
            operator["node_id"]: operator.get("estimates", {}).get("cardinality")
            for operator in current[-1].summary.get("operators", [])
            if "node_id" in operator
        }
        pairs = []
        for record in active:
            if record.kind != "profile":
                continue
            # Node IDs are only local to a plan. An EXPLAIN from another
            # execution or parameter set cannot establish a cardinality error.
            if not set(record.query_ids) & set(current[-1].query_ids):
                continue
            for operator in record.summary.get("operators", []):
                actual = operator.get("counters", {}).get("PullRowNum")
                estimated = estimates.get(operator["node_id"])
                if actual is not None and estimated is not None:
                    pairs.append((estimated, actual, record.id))
        if pairs:
            estimated, actual, identifier = max(
                pairs, key=lambda p: max(p[0] / max(1, p[1]), p[1] / max(1, p[0]))
            )
            result.update(estimated_rows=estimated, actual_rows=actual)
            used.append(identifier)
    return result, tuple(dict.fromkeys(used))
