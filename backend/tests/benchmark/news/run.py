"""Print the offline News accuracy report.

    uv run python -m tests.benchmark.news.run [--json] [--write-gold]

Offline and free: an in-memory governed warehouse, no engine and no model call.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from tests.benchmark.news import dataset, warehouse
from tests.benchmark.news.evaluate import THRESHOLDS, evaluate, failures

GOLD_PATH = Path(__file__).parent / "gold" / "anomalies.json"


def gold_file() -> dict:
    """The labels and, for each, the readers the access oracle admits."""
    return {
        "dataset": {"seed": dataset.SEED, "days": dataset.DAYS,
                    "last_day": dataset.LAST_DAY.isoformat()},
        "injections": [
            {"id": item.id, "day": item.day.isoformat(), "dimension": item.dimension,
             "value": item.value, "factor": item.factor, "kind": item.kind, "note": item.note}
            for item in dataset.INJECTIONS
        ],
        "expected_stories": [
            item | {
                "visible_to": sorted(
                    name for name, principal in warehouse.PRINCIPALS.items()
                    if warehouse.may_see(principal, item["dimension"], item["value"])
                )
            }
            for item in dataset.gold()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-gold", action="store_true")
    args = parser.parse_args()
    if args.write_gold:
        GOLD_PATH.parent.mkdir(exist_ok=True)
        GOLD_PATH.write_text(json.dumps(gold_file(), indent=2) + "\n")
        print(f"wrote {GOLD_PATH}")
        return 0
    report = asyncio.run(evaluate())
    broken = failures(report)
    if args.json:
        print(json.dumps(report | {"failures": broken}, indent=2))
    else:
        for name, value in report.items():
            limit = f"   (limit {THRESHOLDS[name]})" if name in THRESHOLDS else ""
            print(f"{name:32} {value}{limit}")
        print("PASS" if not broken else "FAIL: " + "; ".join(broken))
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
