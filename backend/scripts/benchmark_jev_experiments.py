"""Run the predeclared paired experiments, then the third-agent extension."""

import argparse
import asyncio
import json

from tests.benchmark.jev_multidomain.environment import ARTIFACTS, configure_local_services
from tests.benchmark.jev_multidomain.experiments import freeze_plan
from tests.benchmark.jev_multidomain.runner import LiveBenchmark


async def main(concurrency: int) -> None:
    configure_local_services()
    from app.core.database import db
    from app.core.redis import session_store

    await db.init_system_pool()
    await session_store.init()
    try:
        plan = freeze_plan()
        cases = json.loads((ARTIFACTS / "ground_truth.json").read_text())["cases"]
        for experiment in plan["experiments"]:
            if experiment["variant"] == "sales":
                while True:
                    path = ARTIFACTS / "final-on.jsonl"
                    attempted = {
                        json.loads(line)["case_id"] for line in path.read_text().splitlines()
                    }
                    if len(attempted) == len(cases):
                        break
                    await asyncio.sleep(15)
            subset = [case for case in cases if case["id"] in experiment["ids"]]
            print(
                json.dumps({"experiment": experiment["name"], "planned": len(subset)}), flush=True
            )
            await LiveBenchmark(experiment["arm"], variant=experiment["variant"]).run(
                subset, ARTIFACTS / (experiment["name"] + ".jsonl"), concurrency=concurrency
            )
        cohorts = json.loads((ARTIFACTS / "cohort-ground-truth.json").read_text())["cases"]
        await LiveBenchmark("on").run(
            cohorts, ARTIFACTS / "cohort-on.jsonl", concurrency=concurrency
        )
    finally:
        await db.close_system_pool()
        await session_store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=1)
    asyncio.run(main(parser.parse_args().concurrency))
