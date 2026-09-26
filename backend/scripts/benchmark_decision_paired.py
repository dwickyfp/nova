"""Paired production selection paths on synthetic data; never executes selected tools."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.modules.agents.auto_planner import Candidate, rank_candidates
from app.modules.agents.capabilities import CapabilityManifest
from app.modules.ai_ml.decision_settings import DecisionSettings
from app.modules.assistant.decision import DecisionSession, rank_agents
from app.modules.assistant.planning import plan_turn, refine_turn_plan, validate_turn_plan
from app.modules.assistant.provider import AssistantProviderClient
from app.modules.assistant.registry import build_registry
from tests.benchmark.decision_cases import CASES, CATALOGS


async def run(args):
    config = DecisionSettings(
        enabled=True,
        decision_model_id=args.decision_model,
        light_model_id="unused",
        heavy_model_id="unused",
    )
    provider_client = AssistantProviderClient()
    provider = await provider_client.resolve(provider_id=args.provider, model=args.model)
    registry = build_registry()
    rows = []
    previous = (
        {row["id"]: row for row in json.loads(Path(args.reuse).read_text())} if args.reuse else {}
    )
    for case_id, stage, request, expected in CASES:
        if stage not in {"agents", "tools_skills"}:
            continue
        if args.only_tools and stage != "tools_skills":
            continue
        session = DecisionSession(config)
        start = time.monotonic()
        try:
            if stage == "agents":
                candidates = [
                    Candidate(
                        agent_id=key,
                        name=key,
                        metrics=(),
                        manifest=CapabilityManifest(delegation_description=value),
                    )
                    for key, value in CATALOGS[stage].items()
                ]
                baseline_rank = rank_candidates(request, candidates, [])
                baseline = [item.agent_id for item in baseline_rank]
                actual = [
                    item.agent_id for item in await rank_agents(session, request, baseline_rank)
                ]
            else:
                old = previous.get(case_id, {}).get("baseline")
                if old:
                    plan = validate_turn_plan(
                        {
                            "intent": old["intent"],
                            "tools": old["tools"],
                            "required_tools": old["required"],
                            "skills": old["skills"],
                            "ml_task": old.get("ml_task"),
                        },
                        set(registry.names()),
                        set(registry.discoverable_skills),
                    )
                else:
                    plan = await asyncio.wait_for(
                        plan_turn(
                            provider_client=provider_client,
                            provider=provider,
                            registry=registry,
                            user_content=request,
                            has_previous_result="hasil sebelumnya" in request,
                        ),
                        45,
                    )
                refined = await refine_turn_plan(plan, registry, request, session)
                baseline = {
                    "intent": plan.route.intent.value,
                    "tools": plan.selected_tools,
                    "required": plan.route.required_capabilities,
                    "skills": plan.selected_skills,
                }
                actual = {
                    "intent": refined.route.intent.value,
                    "tools": refined.selected_tools,
                    "required": refined.route.required_capabilities,
                    "skills": refined.selected_skills,
                }
            row = {
                "id": case_id,
                "stage": stage,
                "request": request,
                "expected": expected,
                "baseline": baseline,
                "decision": actual,
                "trace": session.trace,
                "latency_ms": round((time.monotonic() - start) * 1000),
            }
        except Exception as exc:
            row = {"id": case_id, "error": type(exc).__name__}
        rows.append(row)
        Path(args.output).write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-model", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--reuse")
    parser.add_argument("--only-tools", action="store_true")
    asyncio.run(run(parser.parse_args()))
