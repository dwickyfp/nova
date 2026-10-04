"""Controlled current-attempt costs; no external provider, engine, or durable writes."""

from __future__ import annotations

import json
import math
import os
import statistics
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import settings
from app.modules.agents.mission import project_event, project_object
from app.modules.agents.mission_schema import Mission, ObjectRef, StageKind, WorkIntent
from app.modules.agents.quality import case_result, observed_trace
from app.modules.agents.quality_scoring import (
    Assertion,
    PromotionGates,
    gate_results,
    promotion_eligible,
    score_assertion,
    score_case,
)
from app.modules.assistant import provider as provider_module
from app.modules.assistant.evidence_health import assess_evidence, replay_evidence_health
from app.modules.assistant.measurements import AttemptMeasurements, measurement_scope
from app.modules.assistant.provider import AssistantProviderClient, ProviderConfig
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from app.modules.intelligence.contracts import Scope
from tests.benchmark.harness import allow, measure, thread, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult
from tests.unit.test_evidence_health import NOW, strong_facts
from tests.unit.test_studio_missions import USER

pytestmark = pytest.mark.benchmark
SAMPLES = 25
COUNT_KEYS = frozenset(
    {
        "provider_calls",
        "tool_calls",
        "participants",
        "total_tokens",
        "context_tokens",
        "metadata_reads",
    }
)
USAGE = {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
CASE = {
    "id": "controlled-turn",
    "revision": 1,
    "mandatory": True,
    "critical": True,
    "assertions": [
        {
            "scorer": "task_completeness",
            "expected": {"completed": True},
            "required": True,
        }
    ],
}


def _native_loop(monkeypatch, *, tool=False):
    requests = []
    registry = ToolRegistry()
    fixture_tool = EvalTool(
        "load_skill",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    )
    if tool:
        registry.register(fixture_tool)

    async def respond(request):
        body = json.loads(request.content)
        requests.append(bool(body.get("stream")))
        if not body.get("stream"):
            plan = {
                "intent": "capability_help" if tool else "direct_answer",
                "tools": ["load_skill"] if tool else [],
                "required_tools": [],
                "ml_task": None,
            }
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": json.dumps(plan)}}],
                    "usage": USAGE,
                },
            )
        call_tool = tool and sum(requests) % 2 == 1
        delta = (
            {
                "tool_calls": [
                    {
                        "index": 0,
                        **tool_call_frame(
                            "fixture-call", name="load_skill", arguments={"name": "sql"}
                        )["tool_calls"][0],
                    }
                ],
            }
            if call_tool
            else {"content": "The requested explanation is ready."}
        )
        payloads = [{"choices": [{"delta": delta}]}, {"choices": [], "usage": USAGE}]
        return httpx.Response(
            200,
            text="".join(f"data: {json.dumps(payload)}\n\n" for payload in payloads)
            + "data: [DONE]\n\n",
        )

    @asynccontextmanager
    async def client(**_kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as value:
            yield value

    monkeypatch.setattr(provider_module, "guarded_async_client", client)
    monkeypatch.setattr(provider_module, "resolve_and_validate_url", lambda _endpoint: None)
    monkeypatch.setattr(settings, "STUDIO_QUALITY_ENABLED", False)
    monkeypatch.setattr(
        "app.modules.assistant.decision.decision_session", AsyncMock(return_value=None)
    )
    provider = AssistantProviderClient(max_attempts=1, retry_base_seconds=0)
    provider.resolve = AsyncMock(
        return_value=ProviderConfig(
            "fixture-provider",
            "fixture-model",
            "https://fixture.invalid/v1",
            "fixture-key",
        )
    )
    loop = AssistantLoop(provider=provider, registry=registry, max_iterations=4)
    return loop, requests, fixture_tool


async def _turn(loop):
    context = LoopContext(user_name="bench", agent_id="fixture-agent", quality_evaluation=True)
    frames = [
        frame
        async for frame in loop.run(
            thread=thread(read_only_grant=True),
            user_content="Explain the available capability.",
            context=context,
            resolve_consent=allow,
        )
    ]
    assert TurnResult(frames=frames).finish_reason == "stop"
    trace = observed_trace(context.steps)
    observation = next(step for step in context.steps if step["kind"] == "quality_observation")
    assert observation["measurement"]["scope"] == "current_loop_attempt"
    return trace, observation["measurement"]


def _summary(timing):
    assert timing["iterations"] >= 20
    assert all(math.isfinite(value) and value >= 0 for value in timing.values())
    return {
        "samples": int(timing["iterations"]),
        "p50_ms": timing["median_us"] / 1000,
        "p95_ms": timing["p95_us"] / 1000,
        "min_ms": timing["min_us"] / 1000,
        "max_ms": timing["max_us"] / 1000,
    }


async def test_controlled_native_turn_scoring_and_projection_performance(monkeypatch):
    loop, requests, _tool = _native_loop(monkeypatch)
    traces = []
    measurements = []

    async def sample():
        trace, measured = await _turn(loop)
        traces.append(trace)
        measurements.append(measured)

    turn_timing = await measure(sample, iterations=SAMPLES)
    expected = {
        "provider_calls": 2,
        "tool_calls": 0,
        "participants": 1,
        "total_tokens": 50,
        "context_tokens": 20,
        "metadata_reads": 0,
    }
    assert requests == [False, True] * SAMPLES
    assert all(trace["counts"] == expected for trace in traces)
    assert all(set(trace["counts"]) == COUNT_KEYS for trace in traces)
    assert all(not measured["unavailable"] for measured in measurements)
    trace = traces[-1]
    gates = PromotionGates(performance="required", count_budgets=expected)
    result = case_result(CASE, trace, None, gates)
    assert promotion_eligible([CASE], [result], gates)
    assert gate_results([CASE], [result], gates)["performance"]["status"] == "passed"
    exceeded = {}
    for key, value in expected.items():
        if value:
            lower = PromotionGates(performance="required", count_budgets={key: value - 1})
            failed = case_result(CASE, trace, None, lower)
            assert failed["budget_scores"][0]["status"] == "fail"
            assert not promotion_eligible([CASE], [failed], lower)
            exceeded[key] = "fail"
        missing = {**trace, "counts": {k: v for k, v in expected.items() if k != key}}
        unavailable = case_result(
            CASE,
            missing,
            None,
            PromotionGates(performance="required", count_budgets={key: value}),
        )
        assert unavailable["budget_scores"][0]["status"] == "unavailable"
        assert not promotion_eligible(
            [CASE],
            [unavailable],
            PromotionGates(performance="required", count_budgets={key: value}),
        )
    assertions = [Assertion.model_validate(item) for item in CASE["assertions"]]
    case_scorer_duration_sums = []

    async def score_sample():
        status, scores = score_case(assertions, trace)
        assert status == "passed"
        assert all(score.scoring_duration_ms is not None for score in scores)

    async def case_sample():
        scored = case_result(CASE, trace, None, gates)
        assert scored["status"] == "passed"
        assert scored["duration_ms"] == trace["duration_ms"]
        recorded = [
            score["scoring_duration_ms"] for score in [*scored["scores"], *scored["budget_scores"]]
        ]
        assert all(value is not None and math.isfinite(value) and value >= 0 for value in recorded)
        case_scorer_duration_sums.append(sum(recorded))

    score_timing = await measure(score_sample, iterations=SAMPLES)
    case_timing = await measure(case_sample, iterations=SAMPLES)
    assert requests == [False, True] * SAMPLES
    projection_measurement = AttemptMeasurements()

    async def projection_sample():
        with measurement_scope(projection_measurement):
            health = assess_evidence(strong_facts(), assessed_at=NOW)
            assert replay_evidence_health(health.model_dump(mode="json")) == health
            mission = Mission(
                mission_id="fixture-mission",
                thread_id="fixture-thread",
                scope=Scope.from_user(USER),
                objective="Inspect the recorded evidence",
                work_intent=WorkIntent.PLAN,
                status="running",
                revision=1,
                operation_id="fixture-operation",
                created_at=NOW,
                updated_at=NOW,
            )
            project_event(mission, "fixture-run", "table", {"evidence_id": "fixture-evidence"})
            project_object(
                mission,
                ObjectRef(kind="decision", id="fixture-decision", revision=1),
                {
                    "status": "approved",
                    "options": [{"id": "fixture-option"}],
                    "evidence": [{"id": "fixture-evidence"}],
                },
            )
            assert mission.evidence_refs == ["fixture-evidence"]
            assert {stage.kind for stage in mission.stages} >= {
                StageKind.EVIDENCE,
                StageKind.SCENARIOS,
                StageKind.DECIDE,
                StageKind.APPROVE,
            }
            assert all(stage.status == "completed" for stage in mission.stages)

    before_projections = len(requests)
    projection_timing = await measure(projection_sample, iterations=SAMPLES)
    assert len(requests) == before_projections
    assert projection_measurement.provider_dispatches == 0
    assert projection_measurement.provider_invocations == 0
    report = {
        "schema_version": 1,
        "scope": "current_loop_attempt",
        "external_io": "none",
        "fixture": "native AssistantLoop + httpx.MockTransport; configuration resolution stubbed",
        "percentile_method": "median; sorted p95 sample at round(0.95 * (n - 1))",
        "native_turn": _summary(turn_timing),
        "counts_per_turn": expected,
        "scoring": {
            "score_case": _summary(score_timing),
            "case_result": _summary(case_timing),
            "recorded_per_scorer_duration_sum": {
                "samples": len(case_scorer_duration_sums),
                "mean_ms": statistics.fmean(case_scorer_duration_sums),
                "min_ms": min(case_scorer_duration_sums),
                "max_ms": max(case_scorer_duration_sums),
            },
        },
        "estimated_peak_context_tokens": {
            "min": min(item["estimated_peak_context_tokens"] for item in measurements),
            "max": max(item["estimated_peak_context_tokens"] for item in measurements),
        },
        "pure_evidence_and_mission_projection": {
            **_summary(projection_timing),
            "added_provider_calls": 0,
        },
        "configured_count_budget_checks": exceeded,
        "missing_count_budget_checks": {key: "unavailable" for key in sorted(COUNT_KEYS)},
        "limitations": [
            "Timing is local fixture overhead, not production or full business-journey latency.",
            "No real provider, metadata catalog, database, policy, consent UI, or storage I/O.",
            "Metadata reads are zero on this fixture path; no engine read latency was measured.",
            "Token usage is fixture response data, not vendor billing or tokenization.",
            "Projection measures pure public state reduction, not authorization or persistence.",
            "No cross-process, historical, resumed, or complete Smart participant totals.",
            "Wall-clock timing is reported and has no absolute noisy pass/fail threshold.",
        ],
    }
    encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False)
    assert len(encoded) < 12_000
    path = Path(
        os.environ.get(
            "NOVA_GOVERNED_PERFORMANCE_REPORT",
            "/tmp/nova-governed-studio-performance.json",
        )
    )
    assert path.is_absolute() and str(path).startswith("/tmp/")
    path.write_text(encoded + "\n")
    print("BENCHMARK " + json.dumps(report, sort_keys=True, allow_nan=False))


async def test_controlled_tool_budget_fails_and_nested_coverage_stays_unavailable(monkeypatch):
    loop, requests, tool = _native_loop(monkeypatch, tool=True)
    trace, measured = await _turn(loop)
    assert requests == [False, True, True]
    assert len(tool.runs) == trace["counts"]["tool_calls"] == 1
    assert measured["observed_counts"]["provider_calls"] == 3
    assert set(trace["counts"]) | set(measured["unavailable"]) == COUNT_KEYS
    assert "nested_tool_requests_unmeasured" in measured["unavailable"]["provider_calls"]
    tool_budget = PromotionGates(performance="required", count_budgets={"tool_calls": 0})
    failed = case_result(CASE, trace, None, tool_budget)
    assert failed["budget_scores"][0]["status"] == "fail"
    assert not promotion_eligible([CASE], [failed], tool_budget)
    for key in ("provider_calls", "participants", "total_tokens", "context_tokens"):
        assert (
            score_assertion(
                Assertion(scorer="efficiency", expected={key: 1_000_000}),
                trace,
            ).status
            == "unavailable"
        )
    print(
        "BENCHMARK_TOOL "
        + json.dumps(
            {
                "scope": "current_loop_attempt",
                "external_io": "none",
                "counts": trace["counts"],
                "observed_counts": measured["observed_counts"],
                "unavailable": measured["unavailable"],
                "tool_budget_zero": "fail",
                "required_tool_budget_blocks_promotion": True,
            },
            sort_keys=True,
            allow_nan=False,
        )
    )
