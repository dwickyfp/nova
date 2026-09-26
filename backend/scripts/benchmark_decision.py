"""Run opt-in live routing probes without changing saved Studio settings."""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from app.modules.ai_ml.decision_settings import DecisionSettings
from app.modules.assistant.decision import DecisionSession
from tests.benchmark.decision_cases import CASES, CATALOGS


async def run(args):
    settings = DecisionSettings(
        enabled=True,
        decision_model_id=args.model_id,
        light_model_id="benchmark-only",
        heavy_model_id="benchmark-only",
        timeout_seconds=args.timeout,
    )
    semaphore = asyncio.Semaphore(args.concurrency)

    async def evaluate(case):
        case_id, stage, request, expected = case
        async with semaphore:
            session = DecisionSession(settings)
            start = time.monotonic()
            if stage == "workload":
                actual = await session.classify_workload(request)
            else:
                scores = await session.relevance(stage, request, CATALOGS[stage])
                actual = sorted(key for key, value in scores.items() if value == 2)
            if actual == "none":
                actual = None
            correct = actual == (sorted(expected) if isinstance(expected, list) else expected)
            row = {
                "id": case_id,
                "stage": stage,
                "request": request,
                "expected": expected,
                "actual": actual,
                "correct": correct,
                "latency_ms": round((time.monotonic() - start) * 1000),
                "trace": session.trace,
            }
            print(
                json.dumps({key: row[key] for key in ("id", "actual", "correct", "latency_ms")}),
                flush=True,
            )
            return row

    selected = [
        case for case in CASES
        if case[0].startswith(args.split) and case[1] in {"workload", "agents", "tools_skills"}
    ]
    rows = await asyncio.gather(*(evaluate(case) for case in selected))
    durations = sorted(row["latency_ms"] for row in rows)
    summary = {
        "count": len(rows),
        "correct": sum(row["correct"] for row in rows),
        "p50_ms": statistics.median(durations),
        "p95_ms": durations[min(len(durations) - 1, int(len(durations) * 0.95))],
        "fallbacks": sum(row["trace"][-1]["status"] == "fallback" for row in rows),
        "input_tokens": sum(
            t.get("usage", {}).get("input_tokens", 0) for row in rows for t in row["trace"]
        ),
    }
    Path(args.output).write_text(
        json.dumps({"summary": summary, "results": rows}, indent=2, ensure_ascii=False)
    )
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--split", choices=["dev", "hold"], required=True)
    parser.add_argument("--timeout", type=float, default=4)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output", required=True)
    asyncio.run(run(parser.parse_args()))
