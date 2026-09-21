"""Agent observability — usage rollups and per-thread traces.

Reads the assistant message store (``CONFIG_ASSISTANT_MESSAGES``) and the thread
store (``CONFIG_ASSISTANT_THREADS``) to answer two questions:

* **Overview**: over the last N days, how many sessions and tokens did an agent
  consume, and how active were users? Aggregated per day for a chart.
* **Observability**: which conversations happened, and for one conversation the
  full ordered turn list with model, token counts, and the message text.

The data source is Nova's own store, not the engine: threads and messages are
durable there, and token usage is recorded on each assistant row by the run
endpoint. Nothing here reads another user's rows without an explicit admin scope;
the endpoints pass the caller's owner filter.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import db

logger = logging.getLogger(__name__)


def _days_ago(days: int) -> datetime:
    return (datetime.now(UTC) - timedelta(days=days)).replace(tzinfo=None)


async def usage_summary(
    *, owner_name: str, agent_id: str | None = None, days: int = 7
) -> dict[str, Any]:
    """Totals and a daily series over the last ``days`` days for an owner/agent.

    ``total_sessions`` counts threads with at least one message in the window;
    ``total_tokens`` sums the assistant rows' ``total_tokens``;
    ``total_active_users`` counts distinct message authors. The series has one
    point per day so a chart has a value for every bucket, including zeros.
    """
    since = _days_ago(days)

    thread_filter = "t.user_name = %s"
    params: list[Any] = [owner_name]
    if agent_id:
        thread_filter += " AND t.agent_id = %s"
        params.append(agent_id)

    # Sessions: distinct threads with a message in the window.
    sessions = await db.execute_system(
        "SELECT COUNT(DISTINCT m.thread_id) AS n, "
        "COUNT(DISTINCT m.user_name) AS users, "
        "COALESCE(SUM(m.total_tokens), 0) AS tokens "
        "FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES m "
        "JOIN NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS t ON t.thread_id = m.thread_id "
        f"WHERE {thread_filter} AND m.created_at >= %s",
        [*params, since],
    )
    row = sessions["rows"][0] if sessions["rows"] else [0, 0, 0]
    total_sessions = int(row[0] or 0)
    total_users = int(row[1] or 0)
    total_tokens = int(row[2] or 0)

    # Daily series: message count per day (a proxy for sessions if a thread
    # spans days, but a stable, always-present series for the chart).
    daily_rows = await db.execute_system(
        "SELECT DATE(m.created_at) AS d, "
        "COUNT(DISTINCT m.thread_id) AS sessions, "
        "COALESCE(SUM(m.total_tokens), 0) AS tokens "
        "FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES m "
        "JOIN NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS t ON t.thread_id = m.thread_id "
        f"WHERE {thread_filter} AND m.created_at >= %s "
        "GROUP BY DATE(m.created_at) ORDER BY d ASC",
        [*params, since],
    )
    by_day: dict[str, tuple[int, int]] = {}
    for daily in daily_rows["rows"]:
        key = str(daily[0])[:10]
        by_day[key] = (int(daily[1] or 0), int(daily[2] or 0))

    series: list[dict[str, Any]] = []
    start = (datetime.now(UTC) - timedelta(days=days - 1)).replace(tzinfo=None)
    for offset in range(days):
        day = (start + timedelta(days=offset)).date().isoformat()
        sessions_count, tokens = by_day.get(day, (0, 0))
        series.append({"date": day, "sessions": sessions_count, "tokens": tokens})

    return {
        "total_sessions": total_sessions,
        "total_tokens": total_tokens,
        "total_active_users": total_users,
        "series": series,
    }


async def list_sessions(
    *, owner_name: str, agent_id: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Recent threads for an owner/agent, with their first input and stats."""
    clauses = ["t.user_name = %s"]
    params: list[Any] = [owner_name]
    if agent_id:
        clauses.append("t.agent_id = %s")
        params.append(agent_id)

    result = await db.execute_system(
        "SELECT t.thread_id, t.title, t.user_name, t.created_at, t.updated_at, "
        "COUNT(m.message_id) AS message_count, "
        "COALESCE(SUM(m.total_tokens), 0) AS total_tokens "
        "FROM NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS t "
        "LEFT JOIN NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES m ON m.thread_id = t.thread_id "
        f"WHERE {' AND '.join(clauses)} "
        "GROUP BY t.thread_id, t.title, t.user_name, t.created_at, t.updated_at "
        "ORDER BY t.updated_at DESC LIMIT %s",
        [*params, int(limit)],
    )
    sessions = [
        {
            "thread_id": row[0],
            "title": row[1],
            "user_name": row[2],
            "created_at": row[3],
            "updated_at": row[4],
            "message_count": int(row[5] or 0),
            "total_tokens": int(row[6] or 0),
            "first_input": "",
        }
        for row in result["rows"]
    ]
    # Fill the first user input per thread in one extra query rather than a
    # correlated subquery, which StarRocks does not support in this shape.
    if sessions:
        ids = [s["thread_id"] for s in sessions]
        placeholders = ",".join(["%s"] * len(ids))
        firsts = await db.execute_system(
            "SELECT thread_id, content FROM ("
            "  SELECT thread_id, content, "
            "    ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY seq ASC) AS rn "
            "  FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES "
            f"  WHERE role = 'user' AND thread_id IN ({placeholders})"
            ") ranked WHERE rn = 1",
            ids,
        )
        first_by_thread = {
            row[0]: (row[1] or "")[:200] for row in firsts["rows"]
        }
        for session in sessions:
            session["first_input"] = first_by_thread.get(session["thread_id"], "")
    return sessions


async def thread_trace(
    *, owner_name: str, thread_id: str
) -> dict[str, Any] | None:
    """One thread's ordered turns, with model and tokens per assistant turn."""
    thread = await db.execute_system(
        "SELECT thread_id, title, user_name, agent_id, created_at, updated_at "
        "FROM NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS "
        "WHERE thread_id = %s AND user_name = %s",
        [thread_id, owner_name],
    )
    if not thread["rows"]:
        return None
    t = thread["rows"][0]

    messages = await db.execute_system(
        "SELECT message_id, seq, role, content, model_name, "
        "prompt_tokens, completion_tokens, total_tokens, created_at, steps, "
        "instructions "
        "FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES "
        "WHERE thread_id = %s ORDER BY seq ASC",
        [thread_id],
    )
    turns = [
        {
            "message_id": row[0],
            "seq": int(row[1]),
            "role": row[2],
            "content": row[3] or "",
            "model_name": row[4],
            "prompt_tokens": row[5],
            "completion_tokens": row[6],
            "total_tokens": row[7],
            "created_at": row[8],
            "steps": row[9] if isinstance(row[9], list) else (json.loads(row[9]) if row[9] else []),
            "instructions": row[10],
        }
        for row in messages["rows"]
    ]
    total = sum((turn["total_tokens"] or 0) for turn in turns)
    return {
        "thread_id": t[0],
        "title": t[1],
        "user_name": t[2],
        "agent_id": t[3],
        "created_at": t[4],
        "updated_at": t[5],
        "total_tokens": total,
        "turns": turns,
    }
