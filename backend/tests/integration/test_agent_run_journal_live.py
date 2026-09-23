"""Opt-in StarRocks test for scoped run replay and cleanup."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.core.database import db
from app.modules.agents.run_journal import run_journal


@pytest.mark.asyncio
async def test_run_journal_replays_once_and_scopes_role() -> None:
    if os.getenv("NOVA_RUN_LIVE_AGENT_JOURNAL") != "1":
        pytest.skip("Set NOVA_RUN_LIVE_AGENT_JOURNAL=1 for live StarRocks check")
    await db.init_system_pool()
    run_id = str(uuid4())
    owner = f"run-test-{uuid4().hex}"
    thread_id = str(uuid4())
    scope = dict(owner_name=owner, agent_id="test-agent", thread_id=thread_id,
                 role_name="test-role")
    started = False
    try:
        await run_journal.ensure_schema()
        await run_journal.start(run_id=run_id, **scope)
        started = True
        frames = [
            f'event: text_delta\ndata: {{"run_id":"{run_id}","sequence":{i},"text":"{i}"}}\n\n'
            for i in range(3)
        ]
        for frame in frames:
            await run_journal.append(run_id, frame)
        assert await run_journal.events_after(run_id, -1) == frames
        assert await run_journal.events_after(run_id, 1) == frames[2:]
        assert await run_journal.get(run_id, **scope) is not None
        assert await run_journal.get(run_id, **{**scope, "role_name": "other"}) is None
        await run_journal.finish(run_id, "completed")
        assert (await run_journal.get(run_id, **scope))["status"] == "completed"
        await run_journal.block_replay_after_role_change(run_id)
        assert await run_journal.get(run_id, **scope) is None
    finally:
        if started:
            await run_journal.delete_thread(thread_id, owner_name=owner)
        await db.close_system_pool()
