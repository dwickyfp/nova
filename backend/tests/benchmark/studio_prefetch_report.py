"""Offline Studio prefetch report, runnable against another backend source tree.

Run from the locked active backend, for example::

    uv run --locked python tests/benchmark/studio_prefetch_report.py \
        --backend-root /tmp/nova-hardening-baseline-source/backend \
        --output /tmp/nova-hardening-prefetch-baseline.json

Only the fixture waits. The real AssistantLoop owns scheduling and iteration.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

WAIT_SECONDS = 0.020
DEFAULT_SAMPLES = 5
MAX_SAMPLES = 9  # One additional structural probe keeps each case at <=10 turns.
BINDINGS = ("unbound", "mission", "ordered_hook")


def _fixture(*, barrier: bool):
    from app.modules.assistant.tools import ToolRegistry
    from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
    from tests.eval.harness import EvalTool

    class CountedProvider(ScriptedProvider):
        planning_calls = 0
        resolution_calls = 0

        async def plan_turn(self, **kwargs):
            self.planning_calls += 1
            return await super().plan_turn(**kwargs)

        async def resolve(self, **kwargs):
            self.resolution_calls += 1
            return await super().resolve(**kwargs)

    class SemanticRead(EvalTool):
        def __init__(self):
            super().__init__(
                "semantic_query",
                parameters={
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                },
                data={"semantic_plan": {"metrics": ["total_revenue"]}, "sql": "SELECT 1"},
            )
            self.active = 0
            self.peak = 0
            self.execution_order: list[str] = []
            self.arrived: set[str] = set()
            self.both_started = asyncio.Event()
            self.barrier_timeouts = 0

        async def run(self, invocation, context):
            question = invocation.arguments["question"]
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.execution_order.append(f"start:{question}")
            self.arrived.add(question)
            if len(self.arrived) == 2:
                self.both_started.set()
            try:
                outcome = await super().run(invocation, context)
                if barrier:
                    # A serial baseline must finish too; timeout records failure to rendezvous.
                    try:
                        async with asyncio.timeout(WAIT_SECONDS):
                            await self.both_started.wait()
                    except TimeoutError:
                        self.barrier_timeouts += 1
                else:
                    await asyncio.sleep(WAIT_SECONDS)
                context.last_result = {"columns": ["q"], "rows": [[question]]}
                return replace(
                    outcome,
                    summary=question,
                    table={
                        "title": question,
                        "columns": ["label", "total_revenue"],
                        "rows": [[question, 100 if question == "a" else 200]],
                    },
                    business_result={"fixture": True},
                )
            finally:
                self.execution_order.append(f"finish:{question}")
                self.active -= 1

    first = tool_call_frame("c1", name="semantic_query", arguments={"question": "a"})
    second = tool_call_frame("c2", name="semantic_query", arguments={"question": "b"})
    first["tool_calls"].extend(second["tool_calls"])
    provider = CountedProvider(
        script=[first, text_frame("a 100, b 200.")],
        turn_plan={
            "intent": "semantic_analytics",
            "tools": ["semantic_query"],
            "required_tools": ["semantic_query"],
            "ml_task": None,
        },
    )
    tool = SemanticRead()
    registry = ToolRegistry()
    registry.register(tool)
    return provider, tool, registry


async def run_sample(*, workflow: bool, binding: str, barrier: bool = False) -> dict[str, Any]:
    from app.core.config import settings
    from app.modules.assistant.service import AssistantLoop, LoopContext
    from app.modules.intelligence.engine import IntelligenceService
    from tests.benchmark.harness import allow, thread
    from tests.eval.harness import TurnResult

    if binding not in BINDINGS:
        raise ValueError("Unknown fixture binding")
    provider, tool, registry = _fixture(barrier=barrier)
    hook_calls: list[str] = []

    async def ordered_hook(invocation, _outcome, _context):
        question = invocation.arguments["question"]
        hook_calls.append(question)
        tool.execution_order.append(f"hook:{question}")
        return None

    context = LoopContext(
        user_name="bench",
        mission_id="fixture-mission" if binding == "mission" else None,
        business_result_hook=ordered_hook if binding == "ordered_hook" else None,
    )
    loop = AssistantLoop(provider=provider, registry=registry, iterative=True, max_calls_per_tool=6)
    fixture_thread = thread(read_only_grant=True)
    canonical_query = AsyncMock(side_effect=RuntimeError("Fixture excludes canonicalization I/O"))
    with (
        patch.object(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", workflow),
        patch.object(IntelligenceService, "query", canonical_query),
    ):
        started = time.perf_counter_ns()
        async with asyncio.timeout(5):
            frames = [
                frame
                async for frame in loop.run(
                    thread=fixture_thread,
                    user_content="Compare revenue for a and b.",
                    context=context,
                    resolve_consent=allow,
                )
            ]
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000

    result = TurnResult(frames=frames)
    tables = [
        json.loads(frame.split("data: ", 1)[1])
        for frame in frames if frame.startswith("event: table\n")
    ]
    table_order = [table["title"] for table in tables]
    valid = (
        result.finish_reason == "stop"
        and not result.error_codes
        and len(tool.runs) == 2
        and table_order == ["a", "b"]
        and context.last_result == {"columns": ["q"], "rows": [["b"]]}
        and tool.active == 0
    )
    if not valid:
        raise RuntimeError("Fixture did not complete both ordered semantic reads")
    return {
        "elapsed_ms": elapsed_ms,
        "peak_concurrency": tool.peak,
        "counts": {
            "provider_calls": provider.planning_calls + provider.calls,
            "provider_planning_calls": provider.planning_calls,
            "provider_action_calls": provider.calls,
            "provider_resolution_calls": provider.resolution_calls,
            "tool_calls": len(tool.runs),
            "hook_calls": len(hook_calls),
            "canonicalization_query_count": canonical_query.await_count,
        },
        "finish_reason": result.finish_reason,
        "table_order": table_order,
        "execution_order": tool.execution_order,
        "hook_order": hook_calls,
        "barrier_timeouts": tool.barrier_timeouts if barrier else None,
        "barrier_rendezvous": (tool.peak == 2 and tool.barrier_timeouts == 0) if barrier else None,
    }


def _sources() -> dict[str, Any]:
    from app.modules.assistant import service
    from app.modules.intelligence import engine
    from tests.benchmark import harness as provider_harness
    from tests.eval import harness as eval_harness

    backend = Path(service.__file__).resolve().parents[3]
    modules = (service, engine, provider_harness, eval_harness)
    files = {
        str(Path(module.__file__).resolve()): hashlib.sha256(
            Path(module.__file__).read_bytes()
        ).hexdigest()
        for module in modules
    }
    for path in files:
        if not Path(path).is_relative_to(backend):
            raise RuntimeError("Owners were imported from different backend source trees")
    files[str(backend / "uv.lock")] = hashlib.sha256((backend / "uv.lock").read_bytes()).hexdigest()
    return {"backend_root": str(backend), "sha256": files}


async def build_report(*, samples: int = DEFAULT_SAMPLES) -> dict[str, Any]:
    if not 1 <= samples <= MAX_SAMPLES:
        raise ValueError(f"Timed samples must be between 1 and {MAX_SAMPLES}")
    sources = _sources()
    cases = []
    for binding in BINDINGS:
        for workflow in (False, True):
            observations = [
                await run_sample(workflow=workflow, binding=binding) for _ in range(samples)
            ]
            probe = await run_sample(workflow=workflow, binding=binding, barrier=True)
            ordered = sorted(item["elapsed_ms"] for item in observations)
            cases.append({
                "binding": binding,
                "workflow_enabled": workflow,
                "samples": samples,
                "p50_ms": statistics.median(ordered),
                "p95_ms": ordered[math.ceil(0.95 * samples) - 1],
                "peak_concurrency": max(item["peak_concurrency"] for item in observations),
                "observations": observations,
                "structural_probe": {
                    key: value for key, value in probe.items() if key != "elapsed_ms"
                },
            })
    if sources != _sources():
        raise RuntimeError("Source changed during measurement; rerun against a stable source tree")
    return {
        "schema_version": 1,
        "sources": sources,
        "fixture": "AssistantLoop + ScriptedProvider + read-only EvalTool semantic_query",
        "wait_ms": WAIT_SECONDS * 1000,
        "percentile_method": "p50 median; p95 nearest rank ceil(0.95 * n)",
        "timing_scope": "Full loop.run drain, including lazy init; fixture setup/probe excluded",
        "provider_count_scope": "Scripted plan_turn plus stream/complete entries; resolve separate",
        "canonicalization_count_scope": "Awaited IntelligenceService.query entries; no SQL I/O",
        "structural_evidence": {
            "method": "Two distinct calls rendezvous on asyncio.Event before either can finish",
            "timeout_ms": WAIT_SECONDS * 1000,
            "timeout_meaning": "A serialized case cannot rendezvous; it is released and recorded",
            "existing_eval": "backend/tests/eval/test_parallel_read_only_tools.py",
        },
        "cases": cases,
        "limitations": [
            "Synthetic 20 ms waits expose scheduling; local wall-clock estimates are noisy.",
            "At most nine timed samples plus one structural probe per case; p95 is a small sample.",
            "No warmup; the first timed turn includes loop-internal cold initialization.",
            "No live provider, semantic compiler, StarRocks, Redis, storage or policy I/O.",
            "Provider counts describe fixture method invocations, not HTTP requests or billing.",
            "Mission is a scheduling marker; no Mission authorization or persistence runs.",
            "Ordered hook returns None; only dispatch ordering is exercised.",
            "Zero queries covers this fixture; no governed canonicalization cost is measured.",
            "No full INVESTIGATE comparison/driver cost, delegation, recovery or process load.",
            "Barrier and event order prove overlap; existing timing gates are unchanged.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-root", type=Path, help="Select owners before app/tests imports")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", help="Revision label; file hashes identify content")
    args = parser.parse_args()
    if args.backend_root is not None:
        backend = args.backend_root.resolve()
        if not (backend / "app/modules/assistant/service.py").is_file():
            parser.error("--backend-root must contain the Nova backend")
        sys.path.insert(0, str(backend))
    elif importlib.util.find_spec("app") is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    report = asyncio.run(build_report(samples=args.samples))
    report["source_revision"] = args.source_revision
    report["report_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "backend_root": report["sources"]["backend_root"],
        "cases": [
            {key: case[key] for key in (
                "binding", "workflow_enabled", "samples", "p50_ms", "p95_ms", "peak_concurrency",
            )}
            for case in report["cases"]
        ],
    }, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
