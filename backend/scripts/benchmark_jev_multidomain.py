"""Run the real Nova multi-domain benchmark. Provider credentials are never output."""

import argparse
import asyncio
import json

from tests.benchmark.jev_multidomain.environment import configure_local_services, prepare


async def main(args):
    if args.local_docker:
        configure_local_services()
    from app.core.database import db
    from app.core.redis import session_store

    await db.init_system_pool()
    await session_store.init()
    try:
        if args.action == "prepare":
            result = await prepare()
            print(json.dumps({"status": "prepared", "row_counts": result["row_counts"]}))
        elif args.action == "oracle":
            from tests.benchmark.jev_multidomain.oracle import build_oracle

            print(json.dumps(await build_oracle()))
        elif args.action == "update-views":
            from tests.benchmark.jev_multidomain.environment import update_views

            print(json.dumps(await update_views()))
        elif args.action == "cohorts":
            from tests.benchmark.jev_multidomain.cohorts import prepare_cohorts

            print(json.dumps(await prepare_cohorts()))
        elif args.action == "verify-oracle":
            from tests.benchmark.jev_multidomain.oracle import verify_semantic_queries

            print(json.dumps(await verify_semantic_queries()))
        elif args.action in {"run", "probe"}:
            from pathlib import Path

            from tests.benchmark.jev_multidomain.environment import ARTIFACTS
            from tests.benchmark.jev_multidomain.runner import LiveBenchmark

            source = Path(args.case_file) if args.case_file else ARTIFACTS / "ground_truth.json"
            cases = json.loads(source.read_text())["cases"]
            if args.split:
                cases = [c for c in cases if c["split"] == args.split]
            if args.ids:
                cases = [c for c in cases if c["id"] in args.ids.split(",")]
            if args.limit:
                cases = cases[: args.limit]
            if args.action == "probe":
                from tests.benchmark.jev_multidomain.routing_probe import probe

                await probe(cases, Path(args.output), args.variant, args.concurrency, args.interval)
            else:
                await LiveBenchmark(args.arm, timeout=args.timeout, variant=args.variant).run(
                    cases, Path(args.output), concurrency=args.concurrency
                )
        elif args.action == "judge":
            from pathlib import Path

            from tests.benchmark.jev_multidomain.judge import evaluate

            await evaluate(Path(args.input), Path(args.output), args.concurrency)
    finally:
        await db.close_system_pool()
        await session_store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "prepare",
            "oracle",
            "run",
            "judge",
            "update-views",
            "probe",
            "cohorts",
            "verify-oracle",
        ],
    )
    parser.add_argument("--input")
    parser.add_argument("--case-file")
    parser.add_argument("--local-docker", action="store_true")
    parser.add_argument("--arm", choices=["on", "off", "controlled"], default="on")
    parser.add_argument(
        "--variant",
        choices=["full", "reverse", "description", "wording", "none", "minimal", "sales"],
        default="full",
    )
    parser.add_argument("--split", choices=["dev", "holdout"])
    parser.add_argument("--ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--interval", type=float, default=8)
    parser.add_argument(
        "--output", default="../docs/benchmarks/jev-multidomain-20260925/pilot.jsonl"
    )
    asyncio.run(main(parser.parse_args()))
