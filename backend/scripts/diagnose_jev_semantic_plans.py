"""Capture planner responses for a development failure, without a Smart quality score."""

import asyncio
import json
import time
from types import SimpleNamespace

from tests.benchmark.jev_multidomain.environment import ARTIFACTS, public_trace


async def main() -> None:
    from app.core.database import db
    from app.modules.agents.semantic.ir import SemanticModelIR
    from app.modules.agents.semantic.ossie import parse_ossie
    from app.modules.agents.tools.semantic_query import SemanticQueryTool
    from app.modules.ai_ml.decision_settings import registered_model
    from app.modules.assistant.provider import AssistantProviderClient

    await db.init_system_pool()
    try:
        environment = json.loads((ARTIFACTS / "environment.json").read_text())
        ir = SemanticModelIR.from_ossie(
            parse_ossie((ARTIFACTS / "finance.ossie.yaml").read_text()).as_dict()
        )
        question = "Total operating expenses (OPEX) for Q3 2025, a single company total."
        rows = []
        for size in ["light", "heavy"]:
            model, _ = await registered_model(
                environment["decision_settings"][size + "_model_id"], "llm"
            )
            client = AssistantProviderClient(timeout_seconds=60)
            original = client.complete
            row = {
                "component": "semantic_plan",
                "workload_model": size,
                "model": model["name"],
                "question": question,
            }

            async def capture(record=row, complete=original, **kwargs):
                record["messages"] = kwargs["messages"]
                record["response_format"] = kwargs.get("response_format")
                response = await complete(**kwargs)
                record["response"] = response
                return response

            client.complete = capture
            tool = SemanticQueryTool(provider=client)
            context = SimpleNamespace(
                model_provider_id=model["provider_id"], model_name=model["name"], usage={}
            )
            start = time.monotonic()
            try:
                plan = await tool._generate_plan(
                    ir, tool._retriever.retrieve(ir, question).as_dict(), question, context
                )
                row["valid"] = True
                row["plan"] = plan.as_dict()
            except Exception as exc:
                row["valid"] = False
                row["error_class"] = type(exc).__name__
            row["latency_seconds"] = time.monotonic() - start
            rows.append(public_trace(row))
            print(
                json.dumps(
                    {key: row[key] for key in ["workload_model", "valid", "latency_seconds"]}
                ),
                flush=True,
            )
        (ARTIFACTS / "semantic-planner-diagnostic.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2)
        )
    finally:
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
