"""Persistent assistant threads, messages and interactive UI state.

Conversations live in PostgreSQL (``nova_meta.config_assistant_*``) behind
:class:`AssistantMetadataRepository`. Every call runs in its own actor-bound
metadata transaction: the caller's bound request actor, which must be the
``user_name`` the caller names, or the learning service principal for the
learning scan. Row security, not this class, keeps one person's history from
another; a thread that is not theirs reads as absent.

No credential column exists here: a thread holds titles and message text only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from app.core.metadata_actor import actor_for, metadata_transaction, service_actor
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext
from app.modules.assistant.metadata_repository import AssistantMetadataRepository

#: The scheduled learning scan and its consolidation steps run as this service
#: principal, never as the person whose question is being learned.
LEARNING_PRINCIPAL = "nova_learning"
LEARNING_ROLE = "PUBLIC"


def _dump(value: Any) -> str:
    """Canonical JSON text, used for the UI state digest."""
    return json.dumps(value, separators=(",", ":"), default=str)


def _steps_or_empty(value: Any) -> list:
    """Coerce a stored ``steps`` column to a list.

    The engine may hand back a JSON column already decoded, or as a string,
    depending on the driver path. A malformed or absent value becomes an empty
    list rather than raising: a conversation must still open without its trace.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: object) -> datetime:
    """Coerce a stored timestamp to a naive-UTC ``datetime`` for the view layer."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value[:19], fmt)
            except ValueError:
                continue
    return _now()


class AssistantThreadListUnavailable(RuntimeError):
    pass


def learning_actor() -> SecurityContext:
    return service_actor(LEARNING_PRINCIPAL, LEARNING_ROLE)


@asynccontextmanager
async def _store(
    user_name: str | None = None, *, actor: SecurityContext | None = None,
    read_only: bool = False,
) -> AsyncIterator[AssistantMetadataRepository]:
    bound = actor or actor_for(str(user_name))
    async with metadata_transaction(bound, read_only=read_only) as (connection, bound):
        yield AssistantMetadataRepository(connection, bound)


class AssistantRepository:
    """Threads and messages, always scoped to the bound caller."""

    async def ensure_schema(self) -> None:
        """PostgreSQL migrations own this schema; nothing is created at startup."""

    # ── Interactive UI state ───────────────────────────────────

    async def ui_source(self, thread_id: str, message_id: str, content_id: str, *,
                        user_name: str, security_context: dict) -> dict | None:
        async with _store(user_name, read_only=True) as store:
            row = await store.get_message(thread_id, message_id)
        if row is None:
            return None
        from app.modules.assistant.security import same_ui_context

        if row["role"] != "assistant" or not same_ui_context(
            _security_or_none(row["security_context"]), security_context
        ):
            return None
        blocks = [step for step in _steps_or_empty(row["steps"]) if isinstance(step, dict)
                  and step.get("kind") == "ui" and step.get("content_id") == content_id]
        return blocks[0] if len(blocks) == 1 else None

    @staticmethod
    def _state_id(thread_id: str, message_id: str, content_id: str, *, user_name: str,
                  security_context: dict, block: dict) -> str:
        return hashlib.sha256(_dump([
            user_name, thread_id, message_id, content_id, security_context["active_role"],
            block["program_sha256"],
        ]).encode()).hexdigest()

    @staticmethod
    def _state_view(row: dict | None, block: dict) -> dict:
        from app.modules.assistant.intelligent_ui import validate_state

        if row is None or row["program_sha256"] != block["program_sha256"]:
            return {"revision": 0, "state": block["state_defaults"],
                    "program_sha256": block["program_sha256"]}
        validate_state(row["state"], block["state_defaults"])
        return {"revision": int(row["revision"]), "state": row["state"],
                "program_sha256": row["program_sha256"]}

    async def get_ui_state(self, thread_id: str, message_id: str, content_id: str, *,
                           user_name: str, security_context: dict, block: dict) -> dict:
        # State follows the user's active role, like the answer it belongs to.
        state_id = self._state_id(thread_id, message_id, content_id, user_name=user_name,
                                  security_context=security_context, block=block)
        async with _store(user_name, read_only=True) as store:
            row = await store.get_ui_state(state_id)
        return self._state_view(row, block)

    async def put_ui_state(self, thread_id: str, message_id: str, content_id: str, *,
                           user_name: str, security_context: dict, block: dict,
                           revision: int, state: dict) -> dict:
        from app.modules.assistant.intelligent_ui import validate_state

        validate_state(state, block["state_defaults"])
        state_id = self._state_id(thread_id, message_id, content_id, user_name=user_name,
                                  security_context=security_context, block=block)
        try:
            async with _store(user_name) as store:
                saved = await store.put_ui_state(
                    state_id, thread_id=thread_id, message_id=message_id,
                    content_id=content_id, program_sha256=block["program_sha256"],
                    state=state, expected_revision=revision,
                )
        except MetadataConflict:
            raise HTTPException(
                409, "UI state changed on another device; reload saved state"
            ) from None
        return {"revision": int(saved["revision"]), "state": saved["state"],
                "program_sha256": saved["program_sha256"]}

    async def ui_action_seen(self, thread_id: str, operation_id: str, *, user_name: str) -> bool:
        async with _store(user_name, read_only=True) as store:
            return await store.ui_action_seen(thread_id, operation_id)

    # ── Learning ───────────────────────────────────────────────

    async def disable_learning(self, thread_id: str, *, user_name: str) -> None:
        async with _store(user_name) as store:
            await store.disable_learning(thread_id)

    async def quality_traces(self, agent_id: str, user: dict, *, limit: int) -> list[dict]:
        actor = actor_for(user["username"])
        if actor != SecurityContext.from_session(user):
            raise MetadataPermissionDenied()
        async with _store(actor=actor, read_only=True) as store:
            return await store.quality_traces(agent_id, limit=limit)

    async def liked_answers(self, *, limit: int = 2000) -> list[dict]:
        """Recent answers people liked on threads where they allowed learning.

        Runs as the bound caller: an administrator reads everyone's liked
        answers, anyone else only their own. Only the principal and the recorded
        steps are returned, never rows. Newest first.
        """
        from app.core.metadata_actor import current_actor

        async with _store(actor=current_actor(), read_only=True) as store:
            rows = await store.liked_answers(limit=limit)
        return [{"message_id": row["message_id"], "user_name": row["user_name"],
                 "steps": _steps_or_empty(row["steps"])} for row in rows]

    async def learning_enabled(self, thread_id: str, *, user_name: str) -> bool:
        async with _store(user_name, read_only=True) as store:
            return await store.learning_enabled(thread_id, user_name)

    async def learning_source(
        self, thread_id: str, message_id: str, *, user_name: str,
    ) -> dict | None:
        """A person's pending question, read by the learning service principal."""
        async with _store(actor=learning_actor(), read_only=True) as store:
            row = await store.learning_source(thread_id, message_id, user_name)
        if row is None:
            return None
        created_at = row["created_at"] or datetime.now(UTC)
        return {
            "content": row["content"],
            "security_context": _security_or_none(row["security_context"]),
            "created_at": created_at.replace(tzinfo=UTC) if created_at.tzinfo is None
            else created_at,
        }

    async def mark_learning_complete(self, message_id: str, *, user_name: str) -> None:
        async with _store(actor=learning_actor()) as store:
            await store.mark_learning_complete(message_id, user_name=user_name)

    async def record_learning_trace(self, message_id: str, *, user_name: str, trace: dict) -> None:
        try:
            async with _store(actor=learning_actor()) as store:
                await store.record_learning_trace(message_id, trace, user_name=user_name)
        except MetadataConflict:
            raise HTTPException(
                status_code=409, detail="Learning source is no longer pending"
            ) from None

    async def pending_learning(
        self, *, limit: int = 100, after: str = "", user_name: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict]:
        """Pending questions: one person's (as them) or everyone's (learning service)."""
        actor = learning_actor() if user_name is None else actor_for(user_name)
        async with _store(actor=actor, read_only=True) as store:
            rows = await store.pending_learning(
                limit=limit, after=after, user_name=user_name, agent_id=agent_id,
            )
        return [{"message_id": row["message_id"], "thread_id": row["thread_id"],
                 "user_name": row["user_name"], "agent_id": row["agent_id"],
                 "scope": _security_or_none(row["security_context"])} for row in rows]

    # ── Threads ────────────────────────────────────────────────

    async def create_thread(
        self, *, user_name: str, title: str | None = None,
        workspace_file_id: str | None = None, agent_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict:
        async with _store(user_name) as store:
            row = await store.create_thread(
                title=title, workspace_file_id=workspace_file_id, agent_id=agent_id,
                thread_id=thread_id,
            )
        return _thread_row(row)

    async def list_threads(
        self, *, user_name: str, agent_id: str | None = None, all_agents: bool = False
    ) -> list[dict]:
        async with _store(user_name, read_only=True) as store:
            rows = await store.list_threads(agent_id=agent_id, all_agents=all_agents)
        ids = [row.get("thread_id") for row in rows]
        if len(set(ids)) != len(ids) or any(
            not row.get("thread_id") or row.get("user_name") != user_name
            or (all_agents and row.get("agent_id") is None)
            or (not all_agents and row.get("agent_id") != agent_id)
            for row in rows
        ):
            raise AssistantThreadListUnavailable("Conversation history is temporarily unavailable")
        return [_thread_row(row) for row in rows]

    async def get_thread(self, thread_id: str, *, user_name: str) -> dict | None:
        async with _store(user_name, read_only=True) as store:
            row = await store.get_thread(thread_id)
        return _thread_row(row) if row is not None else None

    async def rename_thread(self, thread_id: str, title: str, *, user_name: str) -> dict | None:
        async with _store(user_name) as store:
            if await store.rename_thread(thread_id, title) is None:
                return None
            row = await store.get_thread(thread_id)
        return _thread_row(row) if row is not None else None

    async def delete_thread(self, thread_id: str, *, user_name: str) -> bool:
        async with _store(user_name) as store:
            return await store.delete_thread(thread_id)

    # ── Messages ───────────────────────────────────────────────

    async def list_messages(self, thread_id: str, *, user_name: str) -> list[dict]:
        """Every message on a thread, oldest first, with its trace.

        ``steps`` is the assistant turn's recorded trace. It is returned so a
        reopened conversation can rebuild its process view: without it a reload
        shows the answers and silently drops the work that produced them.
        """
        async with _store(user_name, read_only=True) as store:
            rows = await store.list_messages(thread_id)
        ids = [row.get("message_id") for row in rows]
        if len(set(ids)) != len(ids) or any(
            not row.get("message_id") or row.get("user_name") != user_name
            or row.get("thread_id") != thread_id
            or row.get("role") not in {"user", "assistant", "system", "tool"}
            or type(row.get("seq")) is not int or row["seq"] < 0
            for row in rows
        ):
            raise AssistantThreadListUnavailable("Conversation history is temporarily unavailable")
        return [
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
            for row in rows
        ]

    async def attachment_message(
        self, thread_id: str, message_id: str, *, user_name: str, security_context: dict
    ) -> dict | None:
        """Read one caller-owned attachment source; never return it in public projections."""
        async with _store(user_name, read_only=True) as store:
            row = await store.get_message(thread_id, message_id)
        if (row is None or row["role"] != "user"
                or _security_or_none(row["security_context"]) != security_context):
            return None
        return {"message_id": message_id, "attachments": _steps_or_empty(row["attachments"])}

    async def disliked_agent_messages(self, agent_id: str, *, user_name: str) -> list[dict]:
        async with _store(user_name, read_only=True) as store:
            return await store.disliked_agent_messages(agent_id)

    async def set_feedback(
        self, thread_id: str, message_id: str, feedback: str | None, *, user_name: str
    ) -> bool:
        if feedback not in (None, "like", "dislike"):
            raise ValueError("Invalid feedback")
        async with _store(user_name) as store:
            return await store.set_feedback(thread_id, message_id, feedback)

    async def append_message(
        self,
        thread_id: str,
        *,
        user_name: str,
        role: str,
        content: str,
        message_id: str | None = None,
        agent_id: str | None = None,
        model_name: str | None = None,
        usage: dict | None = None,
        steps: list | None = None,
        instructions: str | None = None,
        security_context: dict | None = None,
        attachments: list[dict] | None = None,
        learning_enabled: bool = False,
    ) -> dict:
        """Append one turn; the store numbers it under the thread's row lock.

        ``steps`` contains the redacted trace and visible structured content,
        including governed result grids. It must never contain credentials.
        ``usage`` is the provider's token report for the turn, if any. Retrying
        with the same ``message_id`` and the same turn returns the stored turn.
        """
        normalized = {key: _int_or_none(usage, key)
                      for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        async with _store(user_name) as store:
            row, _created = await store.append_message(
                thread_id, role=role, content=content, message_id=message_id,
                agent_id=agent_id, model_name=model_name, usage=normalized,
                steps=steps, instructions=instructions, security_context=security_context,
                attachments=attachments, learning_enabled=learning_enabled,
            )
        return {
            "message_id": row["message_id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": _iso(row["created_at"]),
            "agent_id": row["agent_id"],
            "model_name": row["model_name"],
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "total_tokens": row["total_tokens"],
            "steps": _steps_or_empty(row["steps"]),
            "instructions": row["instructions"],
            "security_context": _security_or_none(row["security_context"]),
            "attachments": _steps_or_empty(row["attachments"]),
        }


def _security_or_none(value: Any) -> dict | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _int_or_none(usage: dict | None, key: str) -> int | None:
    """Read one non-negative integer token count from a provider usage dict."""
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _thread_row(row: dict) -> dict:
    return {
        "thread_id": row["thread_id"],
        "user_name": row["user_name"],
        "title": row["title"],
        "workspace_file_id": row["workspace_file_id"],
        "agent_id": row["agent_id"],
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
        "message_count": int(row.get("message_count") or 0),
    }


#: Process-wide repository.
assistant_repository = AssistantRepository()


__all__ = [
    "AssistantRepository",
    "AssistantThreadListUnavailable",
    "LEARNING_PRINCIPAL",
    "assistant_repository",
    "learning_actor",
]
