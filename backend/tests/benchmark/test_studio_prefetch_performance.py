"""Structural scheduling and fixture accounting checks for the bounded report."""

from __future__ import annotations

import json
import math

import pytest

from app.core.config import settings
from tests.benchmark.studio_prefetch_report import MAX_SAMPLES, build_report

pytestmark = pytest.mark.benchmark


async def test_prefetch_report_counts_overlap_and_ordered_boundaries():
    original_workflow = settings.STUDIO_BUSINESS_WORKFLOW_ENABLED
    report = await build_report(samples=1)
    assert settings.STUDIO_BUSINESS_WORKFLOW_ENABLED is original_workflow
    assert len(report["cases"]) == 6
    for case in report["cases"]:
        concurrent = case["binding"] == "unbound"
        assert case["peak_concurrency"] == (2 if concurrent else 1)
        assert all(math.isfinite(case[key]) and case[key] >= 0 for key in ("p50_ms", "p95_ms"))
        probe = case["structural_probe"]
        assert probe["barrier_rendezvous"] is concurrent
        assert probe["barrier_timeouts"] == (0 if concurrent else 1)
        for observed in [*case["observations"], probe]:
            assert observed["counts"] == {
                "provider_calls": 3,
                "provider_planning_calls": 1,
                "provider_action_calls": 2,
                "provider_resolution_calls": 1,
                "tool_calls": 2,
                "hook_calls": 2 if case["binding"] == "ordered_hook" else 0,
                "canonicalization_query_count": 0,
            }
            assert observed["finish_reason"] == "stop"
            assert observed["table_order"] == ["a", "b"]
            order = observed["execution_order"]
            if concurrent:
                assert max(order.index("start:a"), order.index("start:b")) < min(
                    order.index("finish:a"), order.index("finish:b")
                )
            else:
                assert order.index("finish:a") < order.index("start:b")
            if case["binding"] == "ordered_hook":
                assert observed["hook_order"] == ["a", "b"]
                assert order.index("hook:a") < order.index("start:b")
    encoded = json.dumps(report, allow_nan=False)
    assert "api_key" not in encoded and "encrypted_password" not in encoded


@pytest.mark.parametrize("samples", [0, MAX_SAMPLES + 1])
async def test_report_rejects_unbounded_sample_counts(samples):
    with pytest.raises(ValueError, match="Timed samples"):
        await build_report(samples=samples)
