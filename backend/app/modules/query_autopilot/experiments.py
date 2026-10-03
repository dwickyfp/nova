"""Bounded before/after trials with full result proofs and an independent control."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from statistics import mean, stdev
from typing import Any

from app.modules.query_autopilot.correctness import ResultProof, equivalent
from app.modules.query_autopilot.models import Budget, digest


@dataclass(frozen=True)
class Measurement:
    latency_ms: float
    proof: ResultProof
    plan_hash: str | None = None
    resource: dict[str, float] | None = None
    query_id: str | None = None


@dataclass(frozen=True)
class ExperimentResult:
    status: str
    reason: str | None
    before: dict[str, Any]
    after: dict[str, Any]
    controls: dict[str, Any]
    correctness: str
    improvement: float | None
    snapshot: str | None
    repetitions: int
    result_comparison: dict[str, Any] | None = None

    @property
    def binding(self) -> str:
        return digest(asdict(self))


def summarize(samples: list[Measurement]) -> dict[str, Any]:
    values = [s.latency_ms for s in samples]
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "mean_ms": mean(values),
        "stddev_ms": stdev(values) if len(values) > 1 else 0,
        "p95_ms": sorted(values)[math.ceil(0.95 * len(values)) - 1],
        "plans": sorted({s.plan_hash for s in samples if s.plan_hash}),
        "query_ids": [s.query_id for s in samples if s.query_id],
        "resource": [s.resource for s in samples if s.resource is not None],
    }


class Experiment:
    def __init__(self, budget: Budget, *, minimum_gain: float = 0.1):
        self.budget, self.minimum_gain = budget, minimum_gain

    async def run(
        self,
        *,
        measure: Callable[[str, bool], Awaitable[Measurement]],
        snapshot: Callable[[], Awaitable[str | None]],
        apply: Callable[[], Awaitable[None]],
        revalidate: Callable[[], Awaitable[bool]],
        require_rewrite: Callable[[], Awaitable[bool]] | None = None,
    ) -> ExperimentResult:
        before: list[Measurement] = []
        after: list[Measurement] = []
        control_before: list[Measurement] = []
        control_after: list[Measurement] = []
        state = None
        correctness = "INCONCLUSIVE"
        comparison = None
        try:
            async with asyncio.timeout(self.budget.timeout_seconds):
                if not await revalidate():
                    raise ValueError("authorization_unavailable")
                state = await snapshot()
                if not state:
                    raise ValueError("snapshot_state_unavailable")
                for phase, samples, controls in (
                    ("before", before, control_before),
                    ("after", after, control_after),
                ):
                    if phase == "after":
                        if not await revalidate():
                            raise ValueError("authorization_changed")
                        await apply()
                    for _ in range(self.budget.warmups):
                        await measure(phase, False)
                        await measure(phase, True)
                    for _ in range(self.budget.repetitions):
                        if not await revalidate():
                            raise ValueError("authorization_changed")
                        samples.append(await measure(phase, False))
                        controls.append(await measure(phase, True))
                        if any(
                            not math.isfinite(m.latency_ms) or m.latency_ms < 0
                            for m in (samples[-1], controls[-1])
                        ):
                            raise ValueError("invalid_measured_latency")
                        if samples[-1].proof.reason or controls[-1].proof.reason:
                            raise ValueError(samples[-1].proof.reason or controls[-1].proof.reason)
                    if await snapshot() != state:
                        raise ValueError("snapshot_changed")
                reference, control = before[0].proof, control_before[0].proof
                if any(equivalent(reference, item.proof) is not True for item in before):
                    raise ValueError("non_deterministic_before_results")
                if any(
                    equivalent(control, item.proof) is not True
                    for item in control_before + control_after
                ):
                    raise ValueError("control_result_changed")
                changed = next(
                    (item.proof for item in after if equivalent(reference, item.proof) is not True),
                    None,
                )
                comparison = {
                    "before_proof": asdict(reference),
                    "after_proof": asdict(changed or after[0].proof),
                    "checked_target_samples": len(before) + len(after),
                    "checked_control_samples": len(control_before) + len(control_after),
                    "target_equivalent": changed is None,
                    "control_equivalent": True,
                    "snapshot_verified": True,
                }
                if changed:
                    return ExperimentResult(
                        "FAILED",
                        "result_changed",
                        summarize(before),
                        summarize(after),
                        {"before": summarize(control_before), "after": summarize(control_after)},
                        "DIFFERENT",
                        None,
                        state,
                        len(after),
                        comparison,
                    )
                correctness = "EQUIVALENT"
                if require_rewrite and not await require_rewrite():
                    raise ValueError("materialized_view_rewrite_or_freshness_unproven")
                a, b = summarize(before), summarize(after)
                ca, cb = summarize(control_before), summarize(control_after)
                gain = 1 - b["mean_ms"] / max(0.001, a["mean_ms"])
                uncertainty = 1.96 * math.sqrt(
                    a["stddev_ms"] ** 2 / a["count"] + b["stddev_ms"] ** 2 / b["count"]
                )
                control_regressed = cb["mean_ms"] > ca["mean_ms"] * 1.1 and cb["mean_ms"] - ca[
                    "mean_ms"
                ] > 1.96 * math.sqrt(
                    ca["stddev_ms"] ** 2 / ca["count"] + cb["stddev_ms"] ** 2 / cb["count"]
                )
                if control_regressed:
                    status, reason = "REGRESSED", "control_workload_regressed"
                elif gain <= -self.minimum_gain and b["mean_ms"] - a["mean_ms"] > uncertainty:
                    status, reason = "REGRESSED", "target_regressed"
                elif gain >= self.minimum_gain and a["mean_ms"] - b["mean_ms"] > uncertainty:
                    status, reason = "SUCCESS", None
                else:
                    status, reason = "NO_IMPROVEMENT", "benefit_not_repeatable_or_below_threshold"
                return ExperimentResult(
                    status,
                    reason,
                    a,
                    b,
                    {"before": ca, "after": cb},
                    correctness,
                    gain,
                    state,
                    len(after),
                    comparison,
                )
        except (TimeoutError, ValueError) as exc:
            reason = "experiment_time_budget" if isinstance(exc, TimeoutError) else str(exc)
            return ExperimentResult(
                "INCONCLUSIVE",
                reason,
                summarize(before),
                summarize(after),
                {"before": summarize(control_before), "after": summarize(control_after)},
                correctness,
                None,
                state,
                len(after),
                comparison,
            )
