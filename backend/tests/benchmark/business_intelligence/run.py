"""Opt-in live benchmark on an isolated Nova installation; never starts paid calls by default."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path

import httpx

from app.core.database import db
from app.core.redis import session_store
from app.main import create_app
from app.modules.assistant.provider import assistant_provider
from tests.benchmark.business_intelligence.client import StudioClient
from tests.benchmark.business_intelligence.dataset import verify_observations
from tests.benchmark.business_intelligence.experiment import run_experiment, save
from tests.benchmark.business_intelligence.freeze import freeze, verify
from tests.benchmark.business_intelligence.live_budget import LiveBudget, transport_fingerprint


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("freeze", "scripted", "live"))
    parser.add_argument("--provider-mode", choices=("scripted", "live"), default="live")
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--frozen", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--maximum-dollars", type=float)
    parser.add_argument("--input-dollars-per-million", type=float)
    parser.add_argument("--output-dollars-per-million", type=float)
    parser.add_argument("--maximum-calls", type=int, default=5000)
    parser.add_argument("--allow-paid-calls", action="store_true")
    parsed = parser.parse_args(argv)
    if parsed.operation == "scripted":
        parsed.provider_mode = "scripted"
        if parsed.repetitions < 1 or not parsed.output:
            parser.error("Scripted execution requires --output and at least one repetition")
    if parsed.operation == "live":
        if parsed.provider_mode != "live":
            parser.error("Live execution requires the live provider mode")
        if not parsed.allow_paid_calls:
            parser.error("Live execution requires explicit --allow-paid-calls")
        if parsed.repetitions < 3 or not parsed.output:
            parser.error("Live execution requires --output and at least three repetitions")
        if any(
            value is None or value <= 0
            for value in (
                parsed.maximum_dollars,
                parsed.input_dollars_per_million,
                parsed.output_dollars_per_million,
            )
        ):
            parser.error("Supply positive maximum cost and both current provider token rates")
    return parsed


async def main(args) -> None:
    from tests.benchmark.business_intelligence.learning_corpus import interactions
    from tests.benchmark.business_intelligence.scripted_provider import ScriptedBoundary

    boundary = (
        ScriptedBoundary(
            catalog_planning=True,
            statements={row.content: row.rule_key for row in interactions() if row.rule_key},
        )
        if args.provider_mode == "scripted"
        else None
    )
    with boundary.guard() if boundary else nullcontext():
        await execute(args, boundary=boundary)


async def execute(args, *, boundary=None) -> None:
    root = Path(__file__).resolve().parents[4]
    dataset = json.loads(args.dataset_manifest.read_text())
    if dataset.get("schema") != "NOVA_INTELLIGENCE_BENCH":
        raise ValueError("Use the isolated benchmark observation database")
    await db.init_system_pool()
    await session_store.init()
    try:
        config = await assistant_provider.resolve()
        if args.provider_mode == "live" and config.model != "deepseek-v4-1-flash":
            raise ValueError("Configure the requested deepseek-v4-1-flash default before freezing")
        provider = {
            "mode": args.provider_mode,
            "provider_id": config.provider_id,
            "model": config.model,
            "transport_fingerprint": transport_fingerprint(config),
            "capabilities": {
                **asdict(config.capabilities),
                "benchmark_memory_retrieval": "lexical",
            },
            "max_output_tokens": 2048,
        }
        dataset = await verify_observations(db.execute_system, args.dataset_manifest.parent)
        if args.operation == "freeze":
            freeze(root, dataset, provider, args.frozen)
            return
        manifest = json.loads((args.frozen / "manifest.json").read_text())
        verify(root, manifest, dataset, provider)
        username, password, role = (
            os.environ.get(key)
            for key in ("NOVA_BENCH_USER", "NOVA_BENCH_PASSWORD", "NOVA_BENCH_ROLE")
        )
        if not all((username, password, role)):
            raise ValueError("Set the benchmark identity through NOVA_BENCH_USER/PASSWORD/ROLE")
        budget = (
            LiveBudget(
                args.maximum_dollars,
                args.input_dollars_per_million,
                args.output_dollars_per_million,
                maximum_calls=args.maximum_calls,
                provider_id=config.provider_id,
                model=config.model,
                transport_fingerprint=provider["transport_fingerprint"],
            )
            if args.provider_mode == "live"
            else None
        )
        # Run production HTTP routes in the guarded process. An external server
        # would evade this process's provider budget and is deliberately unsupported.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()),
            base_url="http://nova.benchmark",
            timeout=180,
        ) as http:
            client = StudioClient(http)
            await client.login(username, password, role)
            try:
                with budget.guard() if budget else nullcontext():
                    await run_experiment(
                        client,
                        root=root,
                        frozen=args.frozen,
                        destination=args.output,
                        dataset=dataset,
                        provider=provider,
                        execute_gold=db.execute_system,
                        repetitions=args.repetitions,
                        verify_data=lambda: verify_observations(
                            db.execute_system, args.dataset_manifest.parent
                        ),
                    )
            finally:
                if args.output.exists() and budget:
                    save(
                        args.output / "cost-reservations.json",
                        {
                            "reserved_dollars": budget.reserved_dollars,
                            "reserved_call_attempts": budget.calls,
                            "actual_billed_cost": None,
                            "maximum_dollars": budget.maximum_dollars,
                        },
                    )
                elif args.output.exists() and boundary:
                    save(
                        args.output / "scripted-calls.json",
                        {"calls": boundary.calls, "paid_calls": 0},
                    )
                await client.request("POST", "auth/logout")
    finally:
        await session_store.close()
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main(arguments()))
