"""Deterministic explanations and next steps shared by runtime and review."""

import math
from typing import cast

EXPLANATIONS = {
    "UNKNOWN": "The available measurements do not establish a root cause.",
    "STATISTICS": (
        "Statistics state supports a possible contributor, but does not establish "
        "that refreshing statistics will improve the query. Test causality in the snapshot."
    ),
    "RESOURCE_CONTENTION": (
        "Queue time and measured resource saturation support contention; inspect "
        "the enrolled resource budget before changing it."
    ),
    "OPERATOR_SKEW": (
        "Timings from instances of the same operator differ materially. "
        "The distribution of input rows and per-instance waits has not been established."
    ),
    "PLAN_CHANGE": (
        "A changed plan accompanies sustained regression; this is an association "
        "requiring an experiment."
    ),
    "EXCESSIVE_SCAN": "Measured scanned rows materially exceed result rows.",
    "CARDINALITY_ESTIMATE": "Measured operator rows differ materially from estimated rows.",
    "ENGINE_MEMORY_LIMIT": "An exact query-correlated engine log reports exceeded memory limits.",
    "ENGINE_TIMEOUT": "An exact query-correlated engine log reports a query timeout.",
}


def explain(category: str, measurements: dict, baseline: dict) -> str:
    text = EXPLANATIONS[category]

    def values(*keys):
        selected = [measurements.get(key) for key in keys]
        return (
            selected
            if all(type(value) in {int, float} and math.isfinite(value) for value in selected)
            else None
        )

    if pair := values("count", "p95_ms"):
        text += f" Current window: {pair[0]:g} executions, P95 {pair[1]:g} ms."
    comparisons = {
        "CARDINALITY_ESTIMATE": (
            ("estimated_rows", "actual_rows"),
            " Matched operator: estimated {0:g} rows, measured {1:g} rows. "
            "This establishes an estimate error, not its cause or the benefit of a change.",
        ),
        "EXCESSIVE_SCAN": (
            ("scanned_rows", "output_rows"),
            " Measured scan: {0:g} rows for {1:g} result rows. "
            "An aggregate can legitimately scan many rows; scan volume alone "
            "does not prove avoidable work.",
        ),
        "OPERATOR_SKEW": (
            ("operator_instance_count", "max_operator_ms", "median_operator_ms"),
            " Across {0:g} instances, maximum operator time is {1:g} ms and median {2:g} ms.",
        ),
        "RESOURCE_CONTENTION": (
            ("queue_ms", "execution_ms"),
            " The correlated execution queued for {0:g} ms and executed for {1:g} ms. "
            "These measurements do not identify which resource limit should change.",
        ),
    }
    if category in comparisons:
        keys, template = comparisons[category]
        if selected := values(*keys):
            text += template.format(*selected)
    if category in {"UNKNOWN", "PLAN_CHANGE", "STATISTICS"}:
        historical = baseline.get("historical_p95")
        if type(historical) in {int, float} and math.isfinite(cast(float, historical)):
            text += f" Historical P95 is {historical:g} ms."
        if baseline.get("eligible") is False:
            text += (
                " The regression sample/window requirement is not met; "
                "no percentile regression is established."
            )
        if measurements.get("statistics_stale") is True:
            text += " Statistics are stale; age alone cannot establish a latency cause."
    return text


def next_steps(detectors: set[str]) -> list[str]:
    steps = []
    for detector, step in (
        (
            "cardinality_error",
            "Use ANALYZE PROFILE for the same query ID to compare estimated and actual rows "
            "at the same plan node; inspect table growth and statistics health "
            "before a snapshot trial.",
        ),
        (
            "operator_skew",
            "Compare input rows, CPU and wait times across instances of the same profile operator. "
            "Test any proposed redistribution in the snapshot with identical "
            "parameters and controls.",
        ),
        (
            "scan_amplification",
            "Inspect scan predicates, partition pruning and scanned bytes "
            "in the matched plan/profile. "
            "Confirm whether the aggregate requires the scan before proposing an index or MV.",
        ),
        (
            "high_resource",
            "Inspect cumulative CPU, memory and spill counters from distinct query IDs; "
            "separate per-execution cost from frequency before selecting an optimization.",
        ),
        (
            "absolute_slow",
            "Capture the slow execution profile and compare queue, CPU, scan and spill time; "
            "check exact query-correlated timeout or memory-limit logs before assigning a cause.",
        ),
        (
            "plan_regression",
            "Compare the structured plans for identical parameters and verify the differing "
            "operators with execution profiles before an approved plan-baseline trial.",
        ),
        (
            "statistics",
            (
                "Test bounded statistics maintenance in the enrolled snapshot; "
                "require equivalence, repeatable gain and unaffected controls "
                "before policy evaluation."
            ),
        ),
        (
            "materialized_view",
            (
                "Test the aggregate MV in the enrolled snapshot; require real "
                "rewrite, freshness, equivalence, Ranger masking/filter proof "
                "and administrative approval."
            ),
        ),
        (
            "contention",
            (
                "Inspect resource-group limits and simultaneous demand; do not "
                "attribute queueing to bad SQL or change limits without an approved "
                "controlled experiment."
            ),
        ),
        (
            "latency_regression",
            (
                "Compare parameter-matched plans, profiles and statistics to "
                "identify a mechanism for the sustained regression."
            ),
        ),
        (
            "rare_slow",
            (
                "Observe further executions before investing in a structural "
                "change; compare total workload cost with frequent families."
            ),
        ),
        (
            "high_frequency",
            (
                "Prioritize by cumulative workload cost; collect selective profiles "
                "before proposing a structural change."
            ),
        ),
    ):
        if detector in detectors:
            steps.append(step)
    if not steps:
        steps.append(
            "Keep observing; no executable change is justified by the current "
            "evidence. If latency persists, capture a scoped query profile, parameter-matched "
            "plan and statistics health; compare queue, CPU, scan and estimate counters."
        )
    return steps
