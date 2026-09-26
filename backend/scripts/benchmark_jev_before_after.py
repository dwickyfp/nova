"""Compare answer quality on the same questions with JEV disabled and enabled."""

import asyncio
import json
from datetime import UTC, datetime

from tests.benchmark.jev_multidomain.environment import ARTIFACTS, configure_local_services
from tests.benchmark.jev_multidomain.runner import LiveBenchmark


async def main() -> None:
    configure_local_services()
    from app.core.database import db
    from app.core.redis import session_store

    plan = json.loads((ARTIFACTS / "experiments.json").read_text())
    ids = next(item["ids"] for item in plan["experiments"] if item["name"] == "baseline-off")
    ground = json.loads((ARTIFACTS / "ground_truth.json").read_text())
    cases = {case["id"]: case for case in ground["cases"]}
    protocol_path = ARTIFACTS / "before-after-protocol.json"
    if not protocol_path.exists():
        protocol_path.write_text(
            json.dumps(
                {
                    "started_at": datetime.now(UTC).isoformat(),
                    "question": "Does enabling JEV improve Nova's final answer?",
                    "case_ids": ids,
                    "ground_truth_sha256": ground["sha256"],
                    "answer_model": "Same registered heavy model in both arms",
                    "arms": {"off": "JEV disabled", "controlled": "JEV enabled"},
                    "execution": "One case at a time; alternate arm order between pairs",
                    "scoring": "Parent LLM compares each answer to the frozen SQL oracle",
                    "prior_runs": "Retained as diagnostics; not included in this paired result",
                },
                indent=2,
            )
            + "\n"
        )
    await db.init_system_pool()
    await session_store.init()
    try:
        for index, case_id in enumerate(ids):
            arms = ["off", "controlled"] if index % 2 == 0 else ["controlled", "off"]
            for arm in arms:
                suffix = "off" if arm == "off" else "on"
                await LiveBenchmark(arm).run(
                    [cases[case_id]], ARTIFACTS / f"before-after-{suffix}.jsonl", concurrency=1
                )
    finally:
        await db.close_system_pool()
        await session_store.close()


if __name__ == "__main__":
    asyncio.run(main())
