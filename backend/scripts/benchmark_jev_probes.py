"""Throttled ablations of the actual Jev relevance hook, distinct from Smart runs."""

import asyncio
import json

from tests.benchmark.jev_multidomain.environment import ARTIFACTS, configure_local_services
from tests.benchmark.jev_multidomain.experiments import freeze_plan
from tests.benchmark.jev_multidomain.routing_probe import probe


async def wait_complete(name: str, count: int) -> None:
    path = ARTIFACTS / name
    while not path.exists() or len(path.read_text().splitlines()) < count:
        await asyncio.sleep(15)


async def main() -> None:
    configure_local_services()
    from app.core.database import db
    from app.core.redis import session_store

    await db.init_system_pool()
    await session_store.init()
    try:
        cases = json.loads((ARTIFACTS / "ground_truth.json").read_text())["cases"]
        plan = freeze_plan()
        subset = [c for c in cases if c["id"] in plan["paired_case_ids"]]
        hard = [c for c in cases if c["id"] in plan["repeat_case_ids"]]
        await wait_complete("probe-full.jsonl", len(cases))
        for variant, selected in [
            ("reverse", cases),
            ("description", subset),
            ("wording", subset),
            ("none", subset),
            ("minimal", subset),
            ("repeat-2", hard),
            ("repeat-3", hard),
            ("sales", subset),
        ]:
            if variant == "sales":
                await wait_complete("final-on.jsonl", len(cases))
            print(json.dumps({"probe_variant": variant, "planned": len(selected)}), flush=True)
            await probe(
                selected,
                ARTIFACTS / f"probe-{variant}.jsonl",
                "full" if variant.startswith("repeat") else variant,
                interval=3,
            )
    finally:
        await db.close_system_pool()
        await session_store.close()


if __name__ == "__main__":
    asyncio.run(main())
