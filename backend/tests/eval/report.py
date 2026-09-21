"""Print an agentic-harness scorecard (NOVA-124).

    cd backend && uv run python -m tests.eval.report

Runs every golden scenario and prints a one-line result per scenario plus a
summary. Add ``--json`` for a machine-readable report. This is the human-facing
view of the same checks ``test_eval_scenarios.py`` enforces; the pytest suite is
the gate, this is the readout.

Exit code is non-zero if any scenario fails, so the command is CI-usable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from tests.eval.harness import evaluate, run_scenario
from tests.eval.scenarios import all_scenarios


async def _run() -> list[dict]:
    report: list[dict] = []
    for scenario in all_scenarios():
        result = await run_scenario(scenario)
        outcome = evaluate(scenario, result)
        report.append(
            {
                "scenario": outcome.scenario,
                "passed": outcome.passed,
                "checks": [
                    {"name": c.name, "passed": c.passed, "detail": c.detail} for c in outcome.checks
                ],
                "provider_calls": result.provider_calls,
                "finish_reason": result.finish_reason,
                "tool_runs": result.tool_runs,
                "context_stats": result.context_stats,
            }
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the agentic-harness eval")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args()

    report = asyncio.run(_run())

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for entry in report:
            status = "PASS" if entry["passed"] else "FAIL"
            print(f"{status}  {entry['scenario']}")
            if not entry["passed"]:
                for check in entry["checks"]:
                    if not check["passed"]:
                        print(f"      - {check['name']}: {check['detail']}")

        total = len(report)
        passed = sum(1 for entry in report if entry["passed"])
        checks_total = sum(len(e["checks"]) for e in report)
        checks_passed = sum(sum(1 for c in e["checks"] if c["passed"]) for e in report)
        print()
        print(f"scenarios: {passed}/{total}   checks: {checks_passed}/{checks_total}")

    return 1 if any(not entry["passed"] for entry in report) else 0


if __name__ == "__main__":
    sys.exit(main())
