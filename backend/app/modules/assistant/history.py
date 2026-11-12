from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.modules.assistant.repository import (
    AssistantThreadListUnavailable,
    _iso,
    _security_or_none,
    _steps_or_empty,
    _store,
    _thread_row,
)


def _scope(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _encode(scope: str, position: str | int, row_id: str) -> str:
    payload = json.dumps([1, scope, position, row_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode(cursor: str, scope: str, *, message: bool = False) -> tuple[Any, str]:
    try:
        if len(cursor) > 1024:
            raise ValueError
        version, saved_scope, position, row_id = json.loads(
            base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        )
        if version != 1 or saved_scope != scope or not isinstance(row_id, str):
            raise ValueError
        if not 1 <= len(row_id) <= 64:
            raise ValueError
        if message:
            if type(position) is not int or position < 0:
                raise ValueError
        else:
            position = datetime.fromisoformat(position)
            if position.tzinfo is not None:
                raise ValueError
        return position, row_id
    except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
        raise ValueError("Invalid history cursor") from exc


def _limit(value: int) -> int:
    if not 1 <= value <= 100:
        raise ValueError("History limit must be between 1 and 100")
    return value


class HistoryRepository:
    async def threads(
        self,
        *,
        user_name: str,
        limit: int = 50,
        cursor: str | None = None,
        agent_ids: tuple[str, ...] | None = None,
        all_agents: bool = False,
    ) -> tuple[list[dict], str | None]:
        _limit(limit)
        if agent_ids and all_agents:
            raise ValueError("Choose specific agents or all agents")
        scope = _scope("threads", user_name, agent_ids, all_agents)
        after = None
        if cursor:
            updated_at, thread_id = _decode(cursor, scope)
            after = (updated_at.replace(tzinfo=UTC), thread_id)
        async with _store(user_name, read_only=True) as store:
            rows = await store.thread_page(
                limit=limit + 1, agent_ids=agent_ids or None, all_agents=all_agents, after=after,
            )
        ids = [row.get("thread_id") for row in rows]
        if len(set(ids)) != len(ids) or any(
            not row.get("thread_id") or row.get("user_name") != user_name
            or (agent_ids and row.get("agent_id") not in agent_ids)
            or (all_agents and row.get("agent_id") is None)
            or (not agent_ids and not all_agents and row.get("agent_id") is not None)
            for row in rows
        ):
            raise AssistantThreadListUnavailable("Conversation history is temporarily unavailable")
        page = [_thread_row(row) for row in rows[:limit]]
        next_cursor = (
            _encode(scope, page[-1]["updated_at"].isoformat(), page[-1]["thread_id"])
            if len(rows) > limit
            else None
        )
        return page, next_cursor

    async def messages(
        self,
        thread_id: str,
        *,
        user_name: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        _limit(limit)
        scope = _scope("messages", user_name, thread_id)
        before = _decode(cursor, scope, message=True) if cursor else None
        async with _store(user_name, read_only=True) as store:
            rows = await store.message_page(thread_id, limit=limit + 1, before=before)
        ids = [row.get("message_id") for row in rows]
        if len(set(ids)) != len(ids) or any(
            not row.get("message_id") or type(row.get("seq")) is not int or row["seq"] < 0
            for row in rows
        ):
            raise AssistantThreadListUnavailable("Conversation history is temporarily unavailable")
        selected = rows[:limit]
        next_cursor = (
            _encode(scope, selected[-1]["seq"], selected[-1]["message_id"])
            if len(rows) > limit else None
        )
        messages = [
            {
                "message_id": row["message_id"],
                "role": row["role"],
                "content": row["content"] or "",
                "created_at": _iso(row["created_at"]),
                "model_name": row["model_name"],
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "total_tokens": row["total_tokens"],
                "steps": _steps_or_empty(row["steps"]),
                "security_context": _security_or_none(row["security_context"]),
                "feedback": row["feedback"],
                "attachments": _steps_or_empty(row["attachments"]),
            }
            for row in reversed(selected)
        ]
        return messages, next_cursor


history_repository = HistoryRepository()
