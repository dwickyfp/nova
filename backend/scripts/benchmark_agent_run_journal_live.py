"""Measure StarRocks write-through cost for a disposable Agent Studio run."""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from uuid import uuid4

from app.core.database import db
from app.modules.agents.run_journal import run_journal


async def main() -> None:
    await db.init_system_pool()
    run_id = str(uuid4())
    thread_id = str(uuid4())
    owner = f"journal-benchmark-{uuid4().hex}"
    timings_ms: list[float] = []
    frames: list[str] = []
    try:
        await run_journal.ensure_schema()
        await run_journal.start(
            run_id=run_id, owner_name=owner, agent_id="benchmark-agent",
            thread_id=thread_id, role_name="benchmark-role",
        )
        for sequence in range(20):
            frame = (
                "event: text_delta\ndata: "
                + json.dumps({"run_id": run_id, "sequence": sequence, "text": "benchmark"})
                + "\n\n"
            )
            frames.append(frame)
        for offset in range(0, len(frames), 32):
            started = time.perf_counter()
            await run_journal.append_batch(run_id, frames[offset:offset + 32])
            timings_ms.append((time.perf_counter() - started) * 1000)
        await run_journal.finish(run_id, "completed")
        replay = await run_journal.events_after(run_id, -1)
        print(json.dumps({
            "events": len(replay),
            "batches": len(timings_ms),
            "p50_batch_ms": round(statistics.median(timings_ms), 2),
            "p95_batch_ms": round(max(timings_ms), 2),
            "total_append_ms": round(sum(timings_ms), 2),
            "effective_ms_per_event": round(sum(timings_ms) / len(frames), 2),
            "replay_complete": len(replay) == 20,
        }, sort_keys=True))
    finally:
        await run_journal.delete_thread(thread_id, owner_name=owner)
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
