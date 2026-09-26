from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime
from typing import Any

from app.core.database import db
from app.modules.assistant.repository import (
    AssistantThreadListUnavailable,
    _iso,
    _security_or_none,
    _steps_or_empty,
    _thread_row,
    _valid_thread_list_row,
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
        clauses = ["t.user_name = %s"]
        params: list[Any] = [user_name]
        if all_agents:
            clauses.append("t.agent_id IS NOT NULL")
        elif agent_ids:
            clauses.append("t.agent_id IN (" + ",".join(["%s"] * len(agent_ids)) + ")")
            params.extend(agent_ids)
        else:
            clauses.append("t.agent_id IS NULL")
        if cursor:
            updated_at, thread_id = _decode(cursor, scope)
            clauses.append("(t.updated_at < %s OR (t.updated_at = %s AND t.thread_id < %s))")
            params.extend([updated_at, updated_at, thread_id])
        try:
            result = await db.execute_system(
                "SELECT t.thread_id, t.user_name, t.title, t.workspace_file_id, "
                "t.agent_id, t.created_at, t.updated_at, 0 "
                "FROM NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS t WHERE "
                + " AND ".join(clauses)
                + " ORDER BY t.updated_at DESC, t.thread_id DESC LIMIT %s",
                [*params, limit + 1],
            )
            rows = result["rows"]
            if not isinstance(rows, (list, tuple)) or len(rows) > limit + 1:
                raise ValueError("Invalid thread page")
            for row in rows:
                if not _valid_thread_list_row(
                    row,
                    user_name=user_name,
                    agent_id=agent_ids[0] if agent_ids else None,
                    all_agents=all_agents or bool(agent_ids),
                ) or (agent_ids and row[4] not in agent_ids):
                    raise ValueError("Invalid thread page")
            if len({row[0] for row in rows}) != len(rows):
                raise ValueError("Duplicate thread rows")
            page = [_thread_row(row) for row in rows[:limit]]
            next_cursor = (
                _encode(scope, page[-1]["updated_at"].isoformat(), page[-1]["thread_id"])
                if len(rows) > limit
                else None
            )
            if page:
                ids = [row["thread_id"] for row in page]
                counts = await db.execute_system(
                    "SELECT thread_id, COUNT(*) FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES "
                    "WHERE user_name = %s AND thread_id IN ("
                    + ",".join(["%s"] * len(ids))
                    + ") GROUP BY thread_id",
                    [user_name, *ids],
                )
                by_id = {}
                for row in counts["rows"]:
                    if len(row) != 2 or row[0] not in ids or type(row[1]) is not int or row[1] < 0:
                        raise ValueError("Invalid message counts")
                    by_id[row[0]] = row[1]
                for row in page:
                    row["message_count"] = by_id.get(row["thread_id"], 0)
            return page, next_cursor
        except Exception as exc:
            raise AssistantThreadListUnavailable(
                "Conversation history is temporarily unavailable"
            ) from exc

    async def messages(
        self,
        thread_id: str,
        *,
        user_name: str,
        limit: int = 50,
        cursor: str | None = None,
        synchronize: bool = False,
    ) -> tuple[list[dict], str | None]:
        _limit(limit)
        scope = _scope("messages", user_name, thread_id)
        params: list[Any] = [thread_id, user_name]
        where = "thread_id = %s AND user_name = %s"
        if cursor:
            seq, message_id = _decode(cursor, scope, message=True)
            where += " AND (seq < %s OR (seq = %s AND message_id < %s))"
            params.extend([seq, seq, message_id])
        sql = (
            "SELECT message_id, role, content, created_at, model_name, "
            "prompt_tokens, completion_tokens, total_tokens, steps, "
            "security_context, feedback, attachments, seq "
            "FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES WHERE "
            + where
            + " ORDER BY seq DESC, message_id DESC LIMIT %s"
        )
        params.append(limit + 1)
        try:
            if synchronize:
                async with db.system_conn() as conn, conn.cursor() as cur:
                    await cur.execute("SYNC")
                    await cur.execute(sql, params)
                    rows = await cur.fetchall()
            else:
                rows = (await db.execute_system(sql, params))["rows"]
            if not isinstance(rows, (list, tuple)) or len(rows) > limit + 1:
                raise ValueError("Invalid message page")
            for row in rows:
                if (
                    len(row) != 13
                    or not isinstance(row[0], str)
                    or not 1 <= len(row[0]) <= 64
                    or row[1] not in {"user", "assistant", "tool"}
                    or type(row[12]) is not int
                    or row[12] < 0
                ):
                    raise ValueError("Invalid message row")
            if len({row[0] for row in rows}) != len(rows):
                raise ValueError("Duplicate message rows")
        except Exception as exc:
            raise AssistantThreadListUnavailable(
                "Conversation history is temporarily unavailable"
            ) from exc
        selected = rows[:limit]
        next_cursor = (
            _encode(scope, selected[-1][12], selected[-1][0]) if len(rows) > limit else None
        )
        messages = [
            {
                "message_id": row[0],
                "role": row[1],
                "content": row[2] or "",
                "created_at": _iso(row[3]),
                "model_name": row[4],
                "prompt_tokens": row[5],
                "completion_tokens": row[6],
                "total_tokens": row[7],
                "steps": _steps_or_empty(row[8]),
                "security_context": _security_or_none(row[9]),
                "feedback": row[10],
                "attachments": _steps_or_empty(row[11]),
            }
            for row in reversed(selected)
        ]
        return messages, next_cursor


history_repository = HistoryRepository()
