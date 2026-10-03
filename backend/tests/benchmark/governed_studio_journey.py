"""Repeat the unchanged patched-Ranger journey; report successful pytest call durations."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

import pytest

TEST_NAME = "test_actions_require_review_consent_verify_schedule_and_live_authorization"
TEST_PATH = Path(__file__).resolve().parents[1] / "integration/test_governed_studio_ranger.py"
COUNT_KEYS = (
    "provider_calls",
    "tool_calls",
    "participants",
    "context_tokens",
    "total_tokens",
    "metadata_reads",
)


def _sample_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Samples must be an integer between 3 and 20") from exc
    if not 3 <= count <= 20:
        raise argparse.ArgumentTypeError("Samples must be between 3 and 20")
    return count


class JourneySamples:
    def __init__(self, samples: int) -> None:
        if not 3 <= samples <= 20:
            raise ValueError("Journey samples must be between 3 and 20")
        self.samples = samples
        self.collected = 0
        self.phases: dict[str, set[str]] = {}
        self.durations: dict[str, float] = {}
        self.errors: set[str] = set()

    def pytest_generate_tests(self, metafunc: Any) -> None:
        if metafunc.function.__name__ == TEST_NAME:
            if "governed" not in metafunc.fixturenames:
                self.errors.add("governed_fixture_missing")
                return
            metafunc.parametrize(
                "governed",
                range(self.samples),
                indirect=True,
                ids=[f"sample-{index + 1:02d}" for index in range(self.samples)],
            )

    def pytest_collection_finish(self, session: Any) -> None:
        self.collected = len(session.items)
        if self.collected != self.samples or any(
            item.originalname != TEST_NAME for item in session.items
        ):
            self.errors.add("unexpected_collection")
            raise pytest.UsageError("Journey collection does not match requested samples")

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed or report.skipped:
            self.errors.add("collection_failed_or_skipped")

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.nodeid not in self.phases and len(self.phases) >= self.samples:
            self.errors.add("unexpected_sample_report")
            return
        phases = self.phases.setdefault(report.nodeid, set())
        if report.when in phases:
            self.errors.add("duplicate_phase_report")
        phases.add(report.when)
        if not report.passed or hasattr(report, "wasxfail"):
            self.errors.add("sample_failed_skipped_or_xfailed")
        if report.when == "call":
            if not math.isfinite(report.duration) or report.duration < 0:
                self.errors.add("invalid_call_duration")
            elif report.passed and not hasattr(report, "wasxfail"):
                self.durations[report.nodeid] = report.duration

    def report(self, exit_code: int) -> dict[str, Any]:
        if exit_code != 0:
            self.errors.add("pytest_nonzero_exit")
        if self.collected != self.samples:
            self.errors.add("unexpected_collection")
        if (
            len(self.durations) != self.samples
            or len(self.phases) != self.samples
            or any(phases != {"setup", "call", "teardown"} for phases in self.phases.values())
        ):
            self.errors.add("missing_sample_phase_or_duration")
        values = [value * 1000 for _, value in sorted(self.durations.items())]
        timing = None
        if not self.errors:
            ordered = sorted(values)
            rank = math.ceil(0.95 * len(ordered))
            timing = {
                "samples": len(ordered),
                "p50_ms": statistics.median(ordered),
                "p95_ms": ordered[rank - 1],
                "p95_sample_rank": rank,
                "min_ms": ordered[0],
                "max_ms": ordered[-1],
            }
        return {
            "schema_version": 1,
            "status": "failed" if self.errors else "passed",
            "scope": "controlled_patched_ranger_business_journey_pytest_call_phase",
            "contract": f"tests/integration/{TEST_PATH.name}::{TEST_NAME}",
            "requested_samples": self.samples,
            "collected_samples": self.collected,
            "completed_call_samples": len(values),
            "pytest_exit_code": exit_code,
            "call_duration_samples_ms": values,
            "timing": timing,
            "percentiles": "p50 median; p95 nearest rank ceil(0.95 * n), one-based",
            "excluded_timing": ["pytest collection", "fixture setup", "fixture teardown"],
            "counts": {},
            "measurement": {
                "unavailable": {
                    key: ["complete_journey_count_scope_uninstrumented"] for key in COUNT_KEYS
                }
            },
            "errors": sorted(self.errors),
            "limitations": [
                "Small sample smoke measurement; p95 is the maximum with fewer than 20 samples.",
                "Shared host load and service contention confound timing; no absolute timing gate.",
                "Nova API uses in-process HTTP ASGI; SQL and Ranger policy I/O use real services.",
                "The unchanged contract tests policy, consent, Action, verification and Outcome.",
                "Scripted semantic planning; seeded observations and advanced observation time.",
                "No real inference vendor latency, business intervention effect, or causal proof.",
                "Counts are unavailable; native fixture counts cannot establish journey totals.",
                "Fixture setup and cleanup are excluded; this does not establish a production SLA.",
            ],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=_sample_count, default=3, help="Journey samples (3–20)")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("/tmp/nova-governed-studio-journey.json"),
        help="Bounded JSON report path; failures also produce a report",
    )
    args = parser.parse_args(argv)
    plugin = JourneySamples(args.samples)
    exit_code = int(
        pytest.main(
            [f"{TEST_PATH}::{TEST_NAME}", "-q", "--tb=short", "-ra"],
            plugins=[plugin],
        )
    )
    report = plugin.report(exit_code)
    encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False)
    if len(encoded) > 16_000:
        raise RuntimeError("Journey report exceeds its bounded size")
    args.report.write_text(encoded + "\n")
    print("JOURNEY_BENCHMARK " + json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
