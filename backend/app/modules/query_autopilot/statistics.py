"""Mergeable latency histograms with conservative, reproducible comparisons."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

# Milliseconds, <1% relative quantile error above 1 ms; zero has a separate bucket.
_BASE = 1.01


@dataclass
class Distribution:
    bins: dict[int, int] = field(default_factory=dict)
    count: int = 0
    total: float = 0
    maximum: float = 0

    def add(self, milliseconds: float, count: int = 1) -> None:
        if not math.isfinite(milliseconds) or milliseconds < 0 or count < 1:
            raise ValueError("Invalid latency sample")
        index = 0 if milliseconds < 1 else 1 + math.ceil(math.log(milliseconds, _BASE))
        self.bins[index] = self.bins.get(index, 0) + count
        self.count += count
        self.total += milliseconds * count
        self.maximum = max(self.maximum, milliseconds)

    def merge(self, other: Distribution) -> None:
        for index, count in other.bins.items():
            self.bins[index] = self.bins.get(index, 0) + count
        self.count += other.count
        self.total += other.total
        self.maximum = max(self.maximum, other.maximum)

    def quantile(self, percentile: float) -> float | None:
        if not 0 < percentile <= 1:
            raise ValueError("Percentile must be in (0, 1]")
        if not self.count or (percentile >= 0.99 and self.count < 100):
            return None
        target, seen = math.ceil(self.count * percentile), 0
        for index, count in sorted(self.bins.items()):
            seen += count
            if seen >= target:
                return min(self.maximum, 1 if index == 0 else _BASE ** (index - 1))
        return None

    def as_dict(self) -> dict:
        return {
            "bins": {str(k): v for k, v in self.bins.items()},
            "count": self.count,
            "total": self.total,
            "maximum": self.maximum,
        }

    @classmethod
    def from_dict(cls, value: dict) -> Distribution:
        return cls(
            {int(k): int(v) for k, v in value["bins"].items()},
            value["count"],
            value["total"],
            value["maximum"],
        )


@dataclass(frozen=True)
class Window:
    start: datetime
    distribution: Distribution
    complete: bool = True
    successful: bool = True


def mean_gain_evidence(before: Distribution, after: Distribution) -> dict:
    def moments(value):
        average = value.total / value.count if value.count else None
        if average is None or value.count < 2:
            return average, None
        squared = 0.0
        for index, count in value.bins.items():
            lower = 0 if index == 0 else _BASE ** max(0, index - 2)
            upper = 1 if index == 0 else _BASE ** (index - 1)
            distance = max(
                abs(min(value.maximum, lower) - average),
                abs(min(value.maximum, upper) - average),
            )
            squared += count * distance * distance
        return average, squared / (value.count - 1)

    old, old_variance = moments(before)
    new, new_variance = moments(after)
    margin = None
    if old_variance is not None and new_variance is not None:
        margin = 1.96 * math.sqrt(old_variance / before.count + new_variance / after.count)
        if not math.isfinite(margin):
            margin = None
    return {
        "before_mean_ms": old,
        "after_mean_ms": new,
        "mean_difference_margin_ms": margin,
        "repeatable_gain": margin is not None and old - new > margin,
        "method": "mean_difference_with_histogram_variance_upper_bound",
        "causality": "observational_only",
    }


@dataclass(frozen=True)
class Baseline:
    eligible: bool
    historical_count: int
    current_count: int
    windows: int
    historical_p95: float | None
    current_p95: float | None
    upper_envelope: float | None
    seasonal: bool
    reason: str | None


def compare_baseline(current: Window, history: list[Window]) -> Baseline:
    usable = [
        w
        for w in history
        if w.complete
        and w.successful
        and w.start + timedelta(minutes=30) <= current.start
        and w.start >= current.start - timedelta(days=14)
        and w.distribution.count
    ]
    seasonal_windows = [
        w
        for w in history
        if w.complete
        and w.successful
        and w.start + timedelta(minutes=30) <= current.start
        and w.start >= current.start - timedelta(days=90)
        and w.start.weekday() == current.start.weekday()
        and w.start.hour == current.start.hour
        and w.distribution.count >= 20
    ]
    seasonal = len({w.start.date() for w in seasonal_windows}) >= 4 and (
        current.start - min((w.start for w in seasonal_windows), default=current.start)
    ) >= timedelta(days=28)
    if seasonal:
        usable = seasonal_windows
    merged = Distribution()
    for window in usable:
        merged.merge(window.distribution)
    eligible = merged.count >= 100 and len(usable) >= 3 and current.distribution.count >= 20
    values = [w.distribution.quantile(0.95) for w in usable]
    p95s = [v for v in values if v is not None]
    center = median(p95s) if p95s else 0
    mad = median(abs(v - center) for v in p95s) if p95s else 0
    return Baseline(
        eligible,
        merged.count,
        current.distribution.count,
        len(usable),
        merged.quantile(0.95),
        current.distribution.quantile(0.95),
        center + 3 * 1.4826 * mad if p95s else None,
        seasonal,
        None if eligible else "insufficient_complete_windows_or_samples",
    )
