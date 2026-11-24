"""Model-quota budget for live runs (DESIGN §8).

Each unit's cost is estimated from recorded runs: its own latest recorded
``usage.total_tokens``, else the median of its category, else the median of
all turns. A run gets a token budget; the runner stops cleanly before a unit
whose estimate exceeds what is left, and never counts a provider refusal as
a Studio failure.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def recorded_tokens(record: dict[str, Any]) -> int | None:
    usage = (record.get("checks") or {}).get("usage") or (record.get("view") or {}).get("usage")
    if isinstance(usage, str):  # older views stored the dict's repr; not parsed
        return None
    value = (usage or {}).get("total_tokens") if isinstance(usage, dict) else None
    return int(value) if isinstance(value, int | float) and value > 0 else None


@dataclass
class Estimates:
    per_unit: dict[str, int] = field(default_factory=dict)
    per_category: dict[str, int] = field(default_factory=dict)
    overall: int = 25_000

    @classmethod
    def from_runs(cls, directories: list[Path]) -> Estimates:
        """Later directories override earlier ones for the same unit."""
        per_unit: dict[str, int] = {}
        categories: dict[str, list[int]] = {}
        for directory in directories:
            for path in sorted((directory / "turns").glob("*.json")):
                record = json.loads(path.read_text())
                tokens = recorded_tokens(record)
                if tokens is None:
                    continue
                per_unit[str(record["id"])] = tokens
                category = str((record.get("unit") or {}).get("category") or "unknown")
                categories.setdefault(category, []).append(tokens)
        medians = {c: int(statistics.median(v)) for c, v in categories.items()}
        every = [t for values in categories.values() for t in values]
        return cls(per_unit, medians, int(statistics.median(every)) if every else 25_000)

    def estimate(self, unit_id: str, category: str | None = None) -> int:
        if unit_id in self.per_unit:
            return self.per_unit[unit_id]
        return self.per_category.get(str(category), self.overall)

    def category_estimate(self, unit_id: str, category: str | None) -> int:
        """The category median (DESIGN §8): robust to one expensive recorded turn."""
        return self.per_category.get(str(category), self.per_unit.get(unit_id, self.overall))


@dataclass
class Budget:
    total: int
    estimates: Estimates
    spent: int = 0
    stopped_before: str | None = None

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.spent)

    def allows(self, unit_id: str, category: str | None) -> bool:
        if self.stopped_before is not None:
            return False
        if self.estimates.category_estimate(unit_id, category) > self.remaining:
            self.stopped_before = unit_id
            return False
        return True

    def spend(self, record: dict[str, Any]) -> None:
        self.spent += recorded_tokens(record) or 0

    def summary(self) -> dict[str, Any]:
        return {"token_budget": self.total, "spent": self.spent, "remaining": self.remaining,
                "stopped_before": self.stopped_before}
