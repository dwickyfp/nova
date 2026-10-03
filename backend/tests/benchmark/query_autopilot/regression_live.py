"""Evaluate unchanged-query measurements with declared clock provenance.

Natural-window acceptance verifies each observed timestamp. Controlled-clock
fixtures remain labeled and never write backdated historical observations.
"""

from dataclasses import asdict
from datetime import datetime, timedelta

from app.modules.query_autopilot.detection import Facts, detect, diagnose, serialize_findings
from app.modules.query_autopilot.models import Policy, utcnow
from app.modules.query_autopilot.statistics import Distribution, Window, compare_baseline


def regression_case(contention: dict) -> dict:
    before = contention["phases"]["uncontended"]
    after = contention["phases"]["contended"]
    history = before["measured_latencies_ms"]
    recent = after["measured_latencies_ms"]
    natural = contention.get("wall_clock_history") is True
    clock = (
        datetime.fromisoformat(contention.get("recent_anchor") or contention["history_anchor"])
        + timedelta(minutes=30 if contention.get("recent_anchor") else 120)
        if natural
        else utcnow().replace(minute=0, second=0, microsecond=0)
    )
    policy = Policy()

    def distribution(samples):
        value = Distribution()
        for sample in samples:
            value.add(sample)
        return value

    historical = [
        Window(
            datetime.fromisoformat(contention["history_anchor"]) + timedelta(minutes=30 * index)
            if natural else clock - timedelta(minutes=30 * (4 - index)),
            distribution(history[index * 40 : (index + 1) * 40]),
        )
        for index in range(3)
    ]
    previous = Window(clock - timedelta(minutes=30), distribution(recent[:30]))
    current = Window(clock, distribution(recent[30:60]))
    earlier = compare_baseline(previous, historical)
    # Evaluate the same deterministic threshold before marking it sustained.
    earlier_findings = detect(
        Facts(previous.distribution, earlier, previous_regression=True), policy
    )
    baseline = compare_baseline(current, historical)
    bound = [
        item for item in after["observations"]
        if item.get("pending_observed") is True and item.get("limit_occupied") is True
        and isinstance(item.get("facts"), dict)
        and type(item["facts"].get("queue_ms")) in {int, float}
        and type(item["facts"].get("execution_ms")) in {int, float}
    ]
    selected = max(bound, key=lambda item: item["facts"]["queue_ms"]) if bound else None
    facts = Facts(
        current.distribution,
        baseline,
        evidence_ids=(selected["query_id"],) if selected else (),
        queue_ms=selected["facts"]["queue_ms"] if selected else None,
        execution_ms=selected["facts"]["execution_ms"] if selected else None,
        saturated=bool(selected),
        previous_regression=any(f.detector == "latency_regression" for f in earlier_findings),
    )
    findings = detect(facts, policy)
    unsustained = detect(Facts(current.distribution, baseline, previous_regression=False), policy)
    insufficient = detect(
        Facts(
            distribution(recent[:19]),
            compare_baseline(Window(clock, distribution(recent[:19])), historical),
            previous_regression=True,
        ),
        policy,
    )
    timestamps_valid = True
    if natural:
        anchor = datetime.fromisoformat(contention["history_anchor"])
        for phase, size, offset in ((before, 40, 0), (after, 30, 3)):
            timestamps_valid &= len(phase["observations"]) == len(phase["measured_latencies_ms"])
            for index, observation in enumerate(phase["observations"]):
                phase_anchor = (
                    datetime.fromisoformat(contention["recent_anchor"])
                    if phase is after and contention.get("recent_anchor")
                    else anchor + timedelta(minutes=30 * offset)
                )
                start = phase_anchor + timedelta(minutes=30 * (index // size))
                observed = datetime.fromisoformat(observation["observed_at"])
                timestamps_valid &= start <= observed < start + timedelta(minutes=30)
    absolute_slow_expected = (
        current.distribution.count >= 20
        and (current.distribution.quantile(0.95) or 0) >= policy.absolute_slow_ms
    )
    accepted = (
        len(history) >= 120
        and len(recent) >= 60
        and timestamps_valid
        and before["results_equivalent"]
        and after["results_equivalent"]
        and any(f.detector == "latency_regression" for f in findings)
        and any(f.detector == "absolute_slow" for f in findings) == absolute_slow_expected
        and not any(f.detector == "latency_regression" for f in unsustained + insufficient)
    )
    return {
        "case": "D",
        "status": "PASS" if accepted else "FAIL",
        "ground_truth": "same_query_slowed_by_controlled_admission_queue",
        "evidence_kind": "real_engine_wall_clock_windows"
        if natural
        else "real_engine_latencies_with_controlled_detector_clock",
        "wall_clock_historical_acceptance": ("PASS" if accepted else "FAIL")
        if natural
        else "NOT_RUN",
        "clock_method": "three 40-sample complete history windows and two 30-sample recent windows",
        "history_written_to_nova": bool(contention.get("durable_pipeline")),
        "absolute_slow_expected": absolute_slow_expected,
        "baseline": asdict(baseline),
        "findings": serialize_findings(findings),
        "diagnosis": [asdict(item) for item in diagnose(findings, facts)],
        "facts": {
            "count": current.distribution.count,
            "total_ms": current.distribution.total,
            "p95_ms": current.distribution.quantile(0.95),
            "saturated": bool(selected),
            "bound_pending_samples": len(bound),
            "counter_pair_sample_count": 1 if selected else 0,
            **({"queue_ms": facts.queue_ms, "execution_ms": facts.execution_ms}
               if selected else {}),
        },
        "negative_variants": {
            "unsustained_rejected": not any(
                f.detector == "latency_regression" for f in unsustained
            ),
            "insufficient_current_rejected": not any(
                f.detector == "latency_regression" for f in insufficient
            ),
        },
        "before_query_ids": [o["query_id"] for o in before["observations"]],
        "after_query_ids": [o["query_id"] for o in after["observations"]],
        "production_actions": 0,
    }
