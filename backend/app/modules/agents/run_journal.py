"""Scoped, durable SSE journal for an Agent Studio turn."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import db

RUNS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_RUNS (
    run_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    thread_id VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    last_sequence BIGINT NOT NULL,
    started_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(run_id)
DISTRIBUTED BY HASH(run_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_RUN_EVENTS (
    run_id VARCHAR(64) NOT NULL,
    sequence BIGINT NOT NULL,
    frame TEXT NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(run_id, sequence)
DISTRIBUTED BY HASH(run_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _frame_sequence(frame: str) -> int:
    try:
        payload = json.loads(frame.split("data: ", 1)[1])
        return int(payload["sequence"])
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("The run event has no sequence.") from exc


class AgentRunJournal:
    async def ensure_schema(self) -> None:
        await db.execute_system(RUNS_DDL)
        await db.execute_system(EVENTS_DDL)

    async def start(
        self, *, run_id: str, owner_name: str, agent_id: str,
        thread_id: str, role_name: str,
    ) -> None:
        now = _now()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "(run_id, owner_name, agent_id, thread_id, role_name, status, "
            "last_sequence, started_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, 'running', -1, %s, %s)",
            [run_id, owner_name, agent_id, thread_id, role_name, now, now],
        )

    async def append(self, run_id: str, frame: str) -> int:
        return await self.append_batch(run_id, [frame])

    async def append_batch(self, run_id: str, frames: list[str]) -> int:
        if not frames or len(frames) > 32:
            raise ValueError("A run event batch must contain 1–32 frames.")
        sequences = [_frame_sequence(frame) for frame in frames]
        if any(
            current <= previous
            for previous, current in zip(sequences, sequences[1:], strict=False)
        ):
            raise ValueError("Run event sequence is not increasing.")
        now = _now()
        values: list[Any] = []
        for sequence, frame in zip(sequences, frames, strict=True):
            values.extend((run_id, sequence, frame, now))
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_RUN_EVENTS "
            "(run_id, `sequence`, frame, created_at) VALUES "
            + ", ".join(["(%s, %s, %s, %s)"] * len(frames)),
            values,
        )
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "SET last_sequence = %s, updated_at = %s "
            "WHERE run_id = %s AND status = 'running'",
            [sequences[-1], now, run_id],
        )
        return sequences[-1]

    async def finish(self, run_id: str, status: str) -> None:
        if status not in {"completed", "failed", "interrupted"}:
            raise ValueError("Invalid run status")
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET status = %s, updated_at = %s "
            "WHERE run_id = %s AND status = 'running'",
            [status, _now(), run_id],
        )

    async def heartbeat(self, run_id: str) -> None:
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET updated_at = %s "
            "WHERE run_id = %s AND status = 'running'",
            [_now(), run_id],
        )

    async def interrupt_stale(
        self, run_id: str, *, owner_name: str, agent_id: str,
        thread_id: str, role_name: str, stale_after_seconds: int = 60,
    ) -> bool:
        """Close a run whose producer has stopped renewing its lease."""
        state = await self.get(
            run_id, owner_name=owner_name, agent_id=agent_id,
            thread_id=thread_id, role_name=role_name,
        )
        if state is None or state["status"] != "running":
            return state is not None and state["status"] == "interrupted"
        updated = state["updated_at"]
        if isinstance(updated, str):
            updated = datetime.fromisoformat(updated)
        if updated.tzinfo is not None:
            updated = updated.astimezone(UTC).replace(tzinfo=None)
        cutoff = _now() - timedelta(seconds=stale_after_seconds)
        if updated > cutoff:
            return False
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "SET status = 'interrupted', updated_at = %s "
            "WHERE run_id = %s AND owner_name = %s AND agent_id = %s "
            "AND thread_id = %s AND role_name = %s AND status = 'running' "
            "AND updated_at <= %s",
            [_now(), run_id, owner_name, agent_id, thread_id, role_name, cutoff],
        )
        state = await self.get(
            run_id, owner_name=owner_name, agent_id=agent_id,
            thread_id=thread_id, role_name=role_name,
        )
        return state is not None and state["status"] == "interrupted"

    async def block_replay_after_role_change(self, run_id: str) -> None:
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "SET role_name = '__role_changed__', updated_at = %s WHERE run_id = %s",
            [_now(), run_id],
        )

    async def get(
        self, run_id: str, *, owner_name: str, agent_id: str,
        thread_id: str, role_name: str,
    ) -> dict[str, Any] | None:
        result = await db.execute_system(
            "SELECT run_id, status, last_sequence, updated_at "
            "FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS WHERE run_id = %s "
            "AND owner_name = %s AND agent_id = %s AND thread_id = %s AND role_name = %s",
            [run_id, owner_name, agent_id, thread_id, role_name],
        )
        if not result["rows"]:
            return None
        row = result["rows"][0]
        return dict(zip(("run_id", "status", "last_sequence", "updated_at"), row, strict=True))

    async def events_after(self, run_id: str, sequence: int, *, limit: int = 100) -> list[str]:
        result = await db.execute_system(
            "SELECT frame FROM NOVA_SYSTEM.CONFIG_AGENT_RUN_EVENTS "
            "WHERE run_id = %s AND `sequence` > %s "
            "ORDER BY `sequence` ASC LIMIT %s",
            [run_id, sequence, min(max(limit, 1), 100)],
        )
        return [str(row[0]) for row in result["rows"]]

    async def delete_thread(self, thread_id: str, *, owner_name: str) -> None:
        rows = await db.execute_system(
            "SELECT run_id FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "WHERE thread_id = %s AND owner_name = %s", [thread_id, owner_name],
        )
        for row in rows["rows"]:
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_RUN_EVENTS WHERE run_id = %s",
                [row[0]],
            )
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "WHERE thread_id = %s AND owner_name = %s", [thread_id, owner_name],
        )


run_journal = AgentRunJournal()
