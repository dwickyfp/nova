"""Owner-scoped Studio usage and conversation traces from PostgreSQL."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.metadata_actor import current_actor, metadata_transaction
from app.core.metadata_db import MetadataPermissionDenied
from app.modules.assistant.metadata_repository import AssistantMetadataRepository


@asynccontextmanager
async def _store(owner_name: str) -> AsyncIterator[AssistantMetadataRepository]:
    actor = current_actor()
    if actor.principal != owner_name:
        raise MetadataPermissionDenied()
    async with metadata_transaction(actor, read_only=True) as (connection, bound):
        yield AssistantMetadataRepository(connection, bound)


async def usage_summary(
    *,
    owner_name: str,
    agent_id: str | None = None,
    days: int = 7,
) -> dict[str, Any]:
    if type(days) is not int or not 1 <= days <= 90:
        raise ValueError("Usage window must be between one and 90 days")
    since = (datetime.now(UTC) - timedelta(days=days - 1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    async with _store(owner_name) as store:
        totals, daily = await store.usage_rollup(since, agent_id)
    by_day = {str(row["day"]): row for row in daily}
    series = []
    for offset in range(days):
        day = (since + timedelta(days=offset)).date().isoformat()
        row = by_day.get(day, {})
        series.append(
            {
                "date": day,
                "sessions": int(row.get("sessions") or 0),
                "tokens": int(row.get("tokens") or 0),
            }
        )
    return {
        "total_sessions": int(totals["sessions"]),
        "total_tokens": int(totals["tokens"]),
        "total_active_users": int(totals["users"]),
        "series": series,
    }


async def list_sessions(
    *,
    owner_name: str,
    agent_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    async with _store(owner_name) as store:
        rows = await store.observed_sessions(agent_id, limit=limit)
    return [
        {
            **row,
            "message_count": int(row["message_count"]),
            "total_tokens": int(row["total_tokens"]),
        }
        for row in rows
    ]


async def thread_trace(
    *,
    owner_name: str,
    agent_id: str,
    thread_id: str,
) -> dict[str, Any] | None:
    async with _store(owner_name) as store:
        thread = await store.get_thread(thread_id)
        if thread is None or thread["agent_id"] != agent_id:
            return None
        messages = await store.list_messages(thread_id)
    turns = []
    for row in messages:
        steps = row["steps"]
        if isinstance(steps, str):
            steps = json.loads(steps)
        turns.append(
            {
                key: row[key]
                for key in (
                    "message_id",
                    "seq",
                    "role",
                    "model_name",
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "created_at",
                    "instructions",
                )
            }
            | {"content": row["content"] or "", "steps": steps or []}
        )
    return {
        key: thread[key]
        for key in (
            "thread_id",
            "title",
            "user_name",
            "agent_id",
            "created_at",
            "updated_at",
        )
    } | {"total_tokens": sum(turn["total_tokens"] or 0 for turn in turns), "turns": turns}
