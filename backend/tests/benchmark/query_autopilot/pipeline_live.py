"""Collect natural-window workload through Nova and inspect durable detection.

This explicit isolated-engine acceptance takes about two hours. It never
imports historical measurements or changes observation timestamps.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.core.database import db
from app.modules.query_autopilot.schema import ensure_schema
from tests.benchmark.query_autopilot.contention_live import contention_case
from tests.benchmark.query_autopilot.regression_live import regression_case


async def run(
    database: str, output: Path, resume_history: Path | None = None, *, blocker_seconds: int = 1,
) -> dict:
    if os.getenv("NOVA_AUTOPILOT_FIXTURE_STACK") != "1" or not database.startswith("autopilot_"):
        raise ValueError("Explicit isolated retail fixture required")
    output.mkdir(parents=True, exist_ok=True)
    await db.init_system_pool()
    try:
        await ensure_schema()
        contention = await contention_case(
            database, wall_clock_history=True, persist_workload=True,
            checkpoint=output / "pipeline-progress.json",
            resume_history=resume_history, blocker_seconds=blocker_seconds,
        )
        regression = regression_case(contention)
        durable = contention["durable_pipeline"]
        report = {
            "evidence_kind": "isolated_collected_wall_clock_workload",
            "status": "PASS" if contention["status"] == regression["status"]
            == durable["status"] == "PASS" else "FAIL",
            "contention": contention, "regression": regression,
            "durable_pipeline": durable, "production_actions": 0,
        }
        (output / "pipeline.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "pipeline.md").write_text(
            "# Collected workload acceptance\n\n"
            f"Status: {report['status']}; persisted executions: {durable['persisted']}.\n\n"
            "Three complete historical windows and two recent windows use real "
            "completion timestamps from QueryService. Incidents and baselines are "
            "read from NOVA_SYSTEM after aggregation. Fixture queue settings are restored.\n"
        )
        return report
    finally:
        await db.close_system_pool()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-history", type=Path)
    parser.add_argument("--blocker-seconds", type=int, choices=range(1, 11), default=1)
    args = parser.parse_args()
    report = asyncio.run(run(
        args.database, args.output, args.resume_history, blocker_seconds=args.blocker_seconds,
    ))
    print(json.dumps({"status": report["status"],
                      "persisted": report["durable_pipeline"]["persisted"]}))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
