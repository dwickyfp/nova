"""Score completed records as bounded benchmark batches arrive."""

import asyncio
import json

from tests.benchmark.jev_multidomain.environment import ARTIFACTS, configure_local_services
from tests.benchmark.jev_multidomain.experiments import freeze_plan
from tests.benchmark.jev_multidomain.judge import evaluate


async def main() -> None:
    configure_local_services()
    from app.core.database import db
    from app.core.redis import session_store

    await db.init_system_pool()
    await session_store.init()
    try:
        plan = freeze_plan()
        expected = {
            "final-on": 200,
            "cohort-on": 6,
            **{e["name"]: len(e["ids"]) for e in plan["experiments"]},
        }
        pilots = [
            "baseline-pilot-recovered",
            "metadata-v2-pilot",
            "corrected-controlled-pilot",
            "pre-evidence-fix",
        ]
        for _ in range(1080):
            complete = True
            for name in [*expected, *pilots]:
                source = ARTIFACTS / (name + ".jsonl")
                target = ARTIFACTS / ("judge-" + name + ".jsonl")
                if not source.exists():
                    if name in expected:
                        complete = False
                    continue
                await evaluate(source, target, concurrency=1)
                if name in expected and len(source.read_text().splitlines()) < expected[name]:
                    complete = False
            if complete:
                print(json.dumps({"judge": "all experiments complete"}), flush=True)
                break
            await asyncio.sleep(20)
    finally:
        await db.close_system_pool()
        await session_store.close()


if __name__ == "__main__":
    asyncio.run(main())
