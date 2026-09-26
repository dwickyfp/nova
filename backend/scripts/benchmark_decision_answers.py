"""Live answer trajectories over synthetic read-only tool evidence; no customer queries."""

import argparse
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.modules.ai_ml.decision_settings import DecisionSettings
from app.modules.assistant.decision import DecisionSession
from app.modules.assistant.provider import AssistantProviderClient
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import thread
from tests.eval.harness import EvalTool, TurnResult

CASES = [
    ("concept", "Jelaskan table dan view dalam dua kalimat.", None),
    (
        "lookup",
        "Query total orders hari ini dari orders, lalu jawab jumlahnya.",
        {"columns": ["order_count"], "rows": [[128]]},
    ),
    (
        "compare",
        "Query revenue 2024 dan 2025 dari sales, hitung perubahan persentasenya.",
        {"columns": ["revenue_2024", "revenue_2025", "change_pct"], "rows": [[1000, 1200, 20]]},
    ),
    (
        "unknown",
        "Query berapa stok SKU A dari inventory. Jika data tidak tersedia, jangan tebak.",
        {"columns": ["sku", "stock"], "rows": []},
    ),
]


async def run(args):
    rows = []
    for name, request, table in CASES:
        for enabled in ((True,) if args.only_enabled else (False, True)):
            config = DecisionSettings(
                enabled=True,
                decision_model_id=args.decision_model,
                light_model_id=args.light_model_id or args.answer_model_id,
                heavy_model_id=args.answer_model_id,
                timeout_seconds=args.timeout,
            )
            session = DecisionSession(config) if enabled else None
            registry = ToolRegistry()
            tool = EvalTool(
                "query_execute",
                description=(
                    "Run read-only SQL over the authorized orders, sales, or inventory table. "
                    "Call this to retrieve current database facts."
                ),
                table=table,
                data=table,
                summary=f"{len(table['rows'])} rows returned" if table else "No data requested",
            )
            registry.register(tool)
            context = LoopContext(user_name="benchmark", agent_id="synthetic-benchmark")
            loop = AssistantLoop(
                provider=AssistantProviderClient(),
                registry=registry,
                max_iterations=4,
                time_budget_seconds=45,
            )
            start = time.monotonic()
            try:
                with patch(
                    "app.modules.assistant.decision.decision_session",
                    AsyncMock(return_value=session),
                ):
                    frames = [
                        frame
                        async for frame in loop.run(
                            thread=thread(read_only_grant=True),
                            user_content=request,
                            context=context,
                            provider_id=args.provider,
                            model=args.model,
                            resolve_consent=AsyncMock(return_value=False),
                        )
                    ]
                result = TurnResult(frames=frames)
                texts = [
                    json.loads(frame.split("data: ", 1)[1]).get("text", "")
                    for frame in frames
                    if frame.startswith("event: text_delta")
                ]
                row = {
                    "case": name,
                    "enabled": enabled,
                    "request": request,
                    "evidence": table,
                    "answer": "".join(texts),
                    "finish_reason": result.finish_reason,
                    "tool_calls": len(tool.runs),
                    "tables": [
                        json.loads(frame.split("data: ", 1)[1])
                        for frame in frames
                        if frame.startswith("event: table")
                    ],
                    "trace": session.trace if session else [],
                    "routed_model": context.model_name,
                    "latency_ms": round((time.monotonic() - start) * 1000),
                }
            except Exception as exc:
                row = {"case": name, "enabled": enabled, "error": type(exc).__name__}
            rows.append(row)
            Path(args.output).write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-model", required=True)
    parser.add_argument("--answer-model-id", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--light-model-id")
    parser.add_argument("--only-enabled", action="store_true")
    parser.add_argument("--timeout", type=float, default=4)
    asyncio.run(run(parser.parse_args()))
