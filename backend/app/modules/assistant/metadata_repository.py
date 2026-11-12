"""Assistant conversations, interactive UI state and translations in PostgreSQL.

The storage behind :class:`AssistantRepository` and the translation cache in
``messages.py``. It runs in the caller's actor-bound
metadata transaction, and the database, not this class, enforces that a person
reads and writes only their own conversations. Compared with the source it:

* appends a message once by id: an identical retry returns the stored turn and
  a different turn under the same id is a conflict, never an overwrite;
* numbers turns under a row lock on the thread, so concurrent appends get
  distinct, gap-free sequence numbers instead of a racing ``COUNT(*)``;
* keeps a turn immutable after it is written; only the owner's feedback on an
  answer and the learning progress of a pending question change later;
* saves interactive UI state only from the revision the caller read, and only
  in the role that state belongs to.

Pending tool decisions still use the process-local ``consent.py`` broker.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

#: Fields that define a turn; a retry must repeat them exactly.
_TURN = (
    "thread_id", "role", "content", "agent_id", "model_name", "prompt_tokens",
    "completion_tokens", "total_tokens", "steps", "instructions", "security_context",
    "attachments", "learning_state",
)
_JSON = frozenset({"steps", "security_context", "attachments"})


def _tokens(usage: dict[str, Any] | None, key: str) -> int | None:
    value = (usage or {}).get(key)
    return value if type(value) is int and value >= 0 else None


class AssistantMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Assistant metadata requires an active metadata transaction")
        cursor = await self.connection.execute(
            "SELECT current_setting('nova.principal',true)=%s "
            "AND current_setting('nova.active_role',true)=%s "
            "AND current_setting('nova.security_context_version',true)=%s "
            "AND current_setting('nova.service_identity',true)=current_user AS valid",
            (self.actor.principal, self.actor.active_role,
             str(self.actor.security_context_version)),
        )
        row = await cursor.fetchone()
        if not row or row["valid"] is not True:
            raise MetadataPermissionDenied()

    async def _one(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        cursor = await self.connection.execute(query, params)
        return await cursor.fetchone()

    async def _all(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        cursor = await self.connection.execute(query, params)
        return list(await cursor.fetchall())

    async def quality_traces(self, agent_id: str, *, limit: int) -> list[dict[str, Any]]:
        await self._bound()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Quality trace limit must be between one and 100")
        return await self._all(
            "SELECT message_id,steps FROM nova_meta.config_assistant_messages "
            "WHERE agent_id=%s AND user_name=%s AND role='assistant' "
            "AND security_context->>'active_role'=%s "
            "AND security_context->>'security_context_version'=%s "
            "ORDER BY created_at DESC,message_id LIMIT %s",
            (agent_id, self.actor.principal, self.actor.active_role,
             str(self.actor.security_context_version), limit))

    async def usage_rollup(
        self, since: datetime, agent_id: str | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        await self._bound()
        where = "t.user_name=%s AND m.created_at>=%s"
        params: tuple[Any, ...] = (self.actor.principal, since)
        if agent_id:
            where += " AND t.agent_id=%s"
            params += (agent_id,)
        source = (
            " FROM nova_meta.config_assistant_messages m "
            "JOIN nova_meta.config_assistant_threads t ON t.thread_id=m.thread_id "
            "AND t.user_name=m.user_name WHERE " + where
        )
        totals = await self._one(
            "SELECT count(DISTINCT m.thread_id) AS sessions,count(DISTINCT m.user_name) AS users,"
            "coalesce(sum(m.total_tokens),0) AS tokens" + source, params)
        daily = await self._all(
            "SELECT (m.created_at AT TIME ZONE 'UTC')::date AS day,"
            "count(DISTINCT m.thread_id) AS sessions,coalesce(sum(m.total_tokens),0) AS tokens"
            + source + " GROUP BY day ORDER BY day", params)
        assert totals is not None
        return totals, daily

    async def observed_sessions(
        self, agent_id: str | None, *, limit: int,
    ) -> list[dict[str, Any]]:
        await self._bound()
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Session limit must be between one and 500")
        return await self._all(
            "SELECT t.thread_id,t.title,t.user_name,t.created_at,t.updated_at,"
            "(SELECT count(*) FROM nova_meta.config_assistant_messages m "
            "WHERE m.thread_id=t.thread_id AND m.user_name=t.user_name) AS message_count,"
            "(SELECT coalesce(sum(m.total_tokens),0) FROM nova_meta.config_assistant_messages m "
            "WHERE m.thread_id=t.thread_id AND m.user_name=t.user_name) AS total_tokens,"
            "coalesce((SELECT left(m.content,200) FROM nova_meta.config_assistant_messages m "
            "WHERE m.thread_id=t.thread_id AND m.user_name=t.user_name AND m.role='user' "
            "ORDER BY m.seq,m.message_id LIMIT 1),'') AS first_input "
            "FROM nova_meta.config_assistant_threads t WHERE t.user_name=%s"
            + (" AND t.agent_id=%s" if agent_id else "")
            + " ORDER BY t.updated_at DESC,t.thread_id LIMIT %s",
            (self.actor.principal, *((agent_id,) if agent_id else ()), limit))

    # ── Threads ───────────────────────────────────────────────────────────

    async def create_thread(
        self, *, title: str | None = None, workspace_file_id: str | None = None,
        agent_id: str | None = None, thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Create the caller's thread, or return it when this id is already theirs."""
        await self._bound()
        thread_id = thread_id or str(uuid4())
        row = await self._one(
            "INSERT INTO nova_meta.config_assistant_threads (thread_id,user_name,title,"
            "workspace_file_id,agent_id) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (thread_id) DO NOTHING RETURNING *",
            (thread_id, self.actor.principal,
             (title or "New conversation").strip() or "New conversation",
             workspace_file_id, agent_id),
        )
        if row is not None:
            return row
        existing = await self.get_thread(thread_id)
        if existing is None:  # the id belongs to someone else
            raise MetadataConflict()
        return existing

    async def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT t.*,(SELECT count(*) FROM nova_meta.config_assistant_messages m "
            "WHERE m.thread_id=t.thread_id AND m.user_name=t.user_name) AS message_count "
            "FROM nova_meta.config_assistant_threads t WHERE t.thread_id=%s AND t.user_name=%s",
            (thread_id, self.actor.principal),
        )

    async def list_threads(
        self, *, agent_id: str | None = None, all_agents: bool = False,
    ) -> list[dict[str, Any]]:
        await self._bound()
        if all_agents and agent_id is not None:
            raise ValueError("Choose either one agent or all agents")
        scope = ("t.agent_id IS NOT NULL" if all_agents
                 else "t.agent_id IS NULL" if agent_id is None else "t.agent_id=%s")
        params: tuple[Any, ...] = (self.actor.principal,)
        if agent_id is not None:
            params += (agent_id,)
        return await self._all(
            "SELECT t.*,(SELECT count(*) FROM nova_meta.config_assistant_messages m "
            "WHERE m.thread_id=t.thread_id AND m.user_name=t.user_name) AS message_count "
            "FROM nova_meta.config_assistant_threads t WHERE t.user_name=%s AND " + scope
            + " ORDER BY t.updated_at DESC,t.thread_id DESC", params,
        )

    async def thread_page(
        self, *, limit: int, agent_ids: tuple[str, ...] | None = None,
        all_agents: bool = False, after: tuple[Any, str] | None = None,
    ) -> list[dict[str, Any]]:
        """``limit`` of the caller's threads, newest first, after a keyset position."""
        await self._bound()
        if all_agents and agent_ids:
            raise ValueError("Choose specific agents or all agents")
        clauses = ["t.user_name=%s"]
        params: list[Any] = [self.actor.principal]
        if all_agents:
            clauses.append("t.agent_id IS NOT NULL")
        elif agent_ids:
            clauses.append("t.agent_id=ANY(%s)")
            params.append(list(agent_ids))
        else:
            clauses.append("t.agent_id IS NULL")
        if after is not None:
            clauses.append("(t.updated_at,t.thread_id)<(%s,%s)")
            params.extend(after)
        return await self._all(
            "SELECT t.*,(SELECT count(*) FROM nova_meta.config_assistant_messages m "
            "WHERE m.thread_id=t.thread_id AND m.user_name=t.user_name) AS message_count "
            "FROM nova_meta.config_assistant_threads t WHERE " + " AND ".join(clauses)
            + " ORDER BY t.updated_at DESC,t.thread_id DESC LIMIT %s",
            (*params, min(max(limit, 1), 101)),
        )

    async def rename_thread(self, thread_id: str, title: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "UPDATE nova_meta.config_assistant_threads SET title=%s,"
            "updated_at=clock_timestamp() WHERE thread_id=%s AND user_name=%s RETURNING *",
            (title, thread_id, self.actor.principal),
        )

    async def disable_learning(self, thread_id: str) -> bool:
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_assistant_threads SET learning_enabled=false "
            "WHERE thread_id=%s AND user_name=%s RETURNING thread_id",
            (thread_id, self.actor.principal),
        )
        return row is not None

    async def delete_thread(self, thread_id: str) -> bool:
        """Remove the caller's thread with its messages and interactive state."""
        await self._bound()
        for table in ("config_assistant_ui_state", "config_assistant_messages"):
            await self.connection.execute(
                f"DELETE FROM nova_meta.{table} WHERE thread_id=%s AND user_name=%s",
                (thread_id, self.actor.principal),
            )
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.config_assistant_threads WHERE thread_id=%s AND user_name=%s",
            (thread_id, self.actor.principal),
        )
        return cursor.rowcount == 1

    # ── Messages ──────────────────────────────────────────────────────────

    async def append_message(
        self, thread_id: str, *, role: str, content: str, message_id: str | None = None,
        agent_id: str | None = None, model_name: str | None = None,
        usage: dict[str, Any] | None = None, steps: list[Any] | None = None,
        instructions: str | None = None, security_context: dict[str, Any] | None = None,
        attachments: list[Any] | None = None, learning_enabled: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        """Append one turn; returns ``(row, created)``.

        Retrying with the same ``message_id`` and the same turn returns the stored
        row with ``created=False``; a different turn under that id is a conflict.
        """
        await self._bound()
        turn: dict[str, Any] = {
            "thread_id": thread_id, "role": role, "content": content, "agent_id": agent_id,
            "model_name": model_name, "prompt_tokens": _tokens(usage, "prompt_tokens"),
            "completion_tokens": _tokens(usage, "completion_tokens"),
            "total_tokens": _tokens(usage, "total_tokens"), "steps": steps or [],
            "instructions": instructions, "security_context": security_context or None,
            "attachments": attachments or [],
            "learning_state": "pending" if learning_enabled and role == "user" and agent_id
            else None,
        }
        message_id = message_id or str(uuid4())
        existing = await self._one(
            "SELECT * FROM nova_meta.config_assistant_messages WHERE message_id=%s",
            (message_id,),
        )
        if existing is not None:
            return self._same_turn(existing, turn), False
        thread = await self._one(
            "SELECT thread_id FROM nova_meta.config_assistant_threads "
            "WHERE thread_id=%s AND user_name=%s FOR UPDATE",
            (thread_id, self.actor.principal),
        )
        if thread is None:
            raise MetadataConflict()
        sequence = await self._one(
            "SELECT coalesce(max(seq)+1,0) AS seq FROM nova_meta.config_assistant_messages "
            "WHERE thread_id=%s", (thread_id,),
        )
        assert sequence is not None
        values = [Jsonb(turn[field]) if field in _JSON and turn[field] is not None
                  else turn[field] for field in _TURN]
        row = await self._one(
            "INSERT INTO nova_meta.config_assistant_messages (message_id,user_name,seq,"
            + ",".join(_TURN) + ") VALUES (%s,%s,%s," + ",".join(["%s"] * len(_TURN))
            + ") ON CONFLICT (message_id) DO NOTHING RETURNING *",
            (message_id, self.actor.principal, sequence["seq"], *values),
        )
        if row is None:
            # A concurrent identical retry won; it is visible only if it is ours.
            raced = await self._one(
                "SELECT * FROM nova_meta.config_assistant_messages WHERE message_id=%s",
                (message_id,),
            )
            if raced is None:
                raise MetadataConflict()
            return self._same_turn(raced, turn), False
        await self.connection.execute(
            "UPDATE nova_meta.config_assistant_threads SET updated_at=clock_timestamp() "
            "WHERE thread_id=%s AND user_name=%s", (thread_id, self.actor.principal),
        )
        return row, True

    @staticmethod
    def _same_turn(stored: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
        if any(stored[field] != turn[field] for field in _TURN):
            raise MetadataConflict()
        return stored

    async def list_messages(self, thread_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_assistant_messages WHERE thread_id=%s "
            "AND user_name=%s ORDER BY seq,message_id", (thread_id, self.actor.principal),
        )

    async def message_page(
        self, thread_id: str, *, limit: int, before: tuple[int, str] | None = None,
    ) -> list[dict[str, Any]]:
        """``limit`` of the caller's turns on a thread, newest first, before a position."""
        await self._bound()
        clauses = ["thread_id=%s AND user_name=%s"]
        params: list[Any] = [thread_id, self.actor.principal]
        if before is not None:
            clauses.append("(seq,message_id)<(%s,%s)")
            params.extend(before)
        return await self._all(
            "SELECT * FROM nova_meta.config_assistant_messages WHERE " + " AND ".join(clauses)
            + " ORDER BY seq DESC,message_id DESC LIMIT %s",
            (*params, min(max(limit, 1), 101)),
        )

    async def get_message(self, thread_id: str, message_id: str) -> dict[str, Any] | None:
        """One of the caller's own turns."""
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_assistant_messages WHERE thread_id=%s "
            "AND message_id=%s AND user_name=%s", (thread_id, message_id, self.actor.principal),
        )

    async def ui_action_seen(self, thread_id: str, operation_id: str) -> bool:
        """Whether an interactive action with this operation id is already a turn."""
        await self._bound()
        row = await self._one(
            "SELECT 1 AS seen FROM nova_meta.config_assistant_messages WHERE user_name=%s "
            "AND thread_id=%s AND role='user' AND jsonb_typeof(steps)='array' "
            "AND steps->0->>'operation_id'=%s LIMIT 1",
            (self.actor.principal, thread_id, operation_id),
        )
        return row is not None

    async def set_feedback(self, thread_id: str, message_id: str, feedback: str | None) -> bool:
        await self._bound()
        if feedback not in (None, "like", "dislike"):
            raise ValueError("Invalid feedback")
        row = await self._one(
            "UPDATE nova_meta.config_assistant_messages SET feedback=%s WHERE message_id=%s "
            "AND thread_id=%s AND user_name=%s AND role='assistant' RETURNING message_id",
            (feedback, message_id, thread_id, self.actor.principal),
        )
        return row is not None

    # ── Learning (scheduled service principal) ────────────────────────────

    async def pending_learning(
        self, *, limit: int = 100, after: str = "", user_name: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Questions still waiting to be learned, on threads that allow learning.

        The learning service principal scans every person; a person sees only
        their own questions (row security), optionally narrowed further here.
        """
        await self._bound()
        filters, params = ["m.message_id>%s"], [after]
        for column, value in (("m.user_name", user_name), ("m.agent_id", agent_id)):
            if value is not None:
                filters.append(column + "=%s")
                params.append(value)
        return await self._all(
            "SELECT m.message_id,m.thread_id,m.user_name,m.agent_id,m.security_context "
            "FROM nova_meta.config_assistant_messages m "
            "JOIN nova_meta.config_assistant_threads t "
            "ON t.thread_id=m.thread_id AND t.user_name=m.user_name "
            "WHERE m.learning_state='pending' AND m.role='user' AND t.learning_enabled "
            "AND " + " AND ".join(filters) + " ORDER BY m.message_id LIMIT %s",
            (*params, min(max(limit, 1), 100)),
        )

    async def learning_enabled(self, thread_id: str, user_name: str) -> bool:
        await self._bound()
        row = await self._one(
            "SELECT learning_enabled FROM nova_meta.config_assistant_threads "
            "WHERE thread_id=%s AND user_name=%s", (thread_id, user_name),
        )
        return bool(row and row["learning_enabled"] is True)

    async def learning_source(
        self, thread_id: str, message_id: str, user_name: str,
    ) -> dict[str, Any] | None:
        """A pending question on a thread that still allows learning."""
        await self._bound()
        return await self._one(
            "SELECT m.content,m.security_context,m.created_at "
            "FROM nova_meta.config_assistant_messages m "
            "JOIN nova_meta.config_assistant_threads t "
            "ON t.thread_id=m.thread_id AND t.user_name=m.user_name "
            "WHERE m.thread_id=%s AND m.message_id=%s AND m.user_name=%s AND m.role='user' "
            "AND m.learning_state='pending' AND t.learning_enabled",
            (thread_id, message_id, user_name),
        )

    async def disliked_agent_messages(self, agent_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT steps FROM nova_meta.config_assistant_messages "
            "WHERE agent_id=%s AND user_name=%s AND feedback='dislike' "
            "AND security_context->>'active_role'=%s "
            "AND security_context->>'security_context_version'=%s "
            "ORDER BY created_at DESC,message_id DESC LIMIT 200",
            (agent_id, self.actor.principal, self.actor.active_role,
             str(self.actor.security_context_version)),
        )

    async def liked_answers(self, *, limit: int = 2000) -> list[dict[str, Any]]:
        """Liked answers on threads that allow learning, newest first.

        Row security decides whose answers are visible: an administrator in
        their active role reads everyone's, anyone else only their own.
        """
        await self._bound()
        return await self._all(
            "SELECT m.message_id,m.user_name,m.steps FROM nova_meta.config_assistant_messages m "
            "JOIN nova_meta.config_assistant_threads t "
            "ON t.thread_id=m.thread_id AND t.user_name=m.user_name "
            "WHERE m.role='assistant' AND m.feedback='like' AND t.learning_enabled "
            "ORDER BY m.created_at DESC,m.message_id DESC LIMIT %s",
            (min(max(limit, 1), 5000),),
        )

    async def record_learning_trace(
        self, message_id: str, trace: dict[str, Any], *, user_name: str | None = None,
    ) -> None:
        """Insert or replace one trace step on a pending question, under its row lock."""
        await self._bound()
        if not isinstance(trace.get("id"), str):
            raise ValueError("A learning trace step needs an id")
        row = await self._one(
            "SELECT steps FROM nova_meta.config_assistant_messages WHERE message_id=%s "
            "AND role='user' AND learning_state='pending' AND user_name=coalesce(%s,user_name) "
            "FOR UPDATE", (message_id, user_name),
        )
        if row is None:
            raise MetadataConflict()
        if row["steps"] is not None and not isinstance(row["steps"], list):
            raise MetadataConflict()
        steps = row["steps"] or []
        if any(not isinstance(step, dict) for step in steps):
            raise MetadataConflict()
        position = next((i for i, step in enumerate(steps) if step.get("id") == trace["id"]), None)
        if position is None:
            steps.append(trace)
        else:
            steps[position] = trace
        cursor = await self.connection.execute(
            "UPDATE nova_meta.config_assistant_messages SET steps=%s WHERE message_id=%s "
            "AND learning_state='pending'", (Jsonb(steps), message_id),
        )
        if cursor.rowcount != 1:
            raise MetadataConflict()

    async def mark_learning_complete(
        self, message_id: str, *, user_name: str | None = None,
    ) -> bool:
        await self._bound()
        # No RETURNING: a completed question leaves the learning read scope.
        cursor = await self.connection.execute(
            "UPDATE nova_meta.config_assistant_messages SET learning_state='complete' "
            "WHERE message_id=%s AND learning_state='pending' "
            "AND user_name=coalesce(%s,user_name)", (message_id, user_name),
        )
        return cursor.rowcount == 1

    # ── Interactive UI state ──────────────────────────────────────────────

    async def get_ui_state(self, state_id: str) -> dict[str, Any] | None:
        """The caller's saved state in their current role, if any."""
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_assistant_ui_state WHERE state_id=%s "
            "AND user_name=%s AND active_role=%s",
            (state_id, self.actor.principal, self.actor.active_role),
        )

    async def put_ui_state(
        self, state_id: str, *, thread_id: str, message_id: str, content_id: str,
        program_sha256: str, state: dict[str, Any], expected_revision: int,
    ) -> dict[str, Any]:
        """Save state from ``expected_revision`` (0: nothing saved yet)."""
        await self._bound()
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("A non-negative revision is required")
        if expected_revision == 0:
            row = await self._one(
                "INSERT INTO nova_meta.config_assistant_ui_state (state_id,user_name,thread_id,"
                "message_id,content_id,active_role,security_context_version,program_sha256,"
                "revision,state) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s) "
                "ON CONFLICT (state_id) DO NOTHING RETURNING *",
                (state_id, self.actor.principal, thread_id, message_id, content_id,
                 self.actor.active_role, self.actor.security_context_version, program_sha256,
                 Jsonb(state)),
            )
        else:
            row = await self._one(
                "UPDATE nova_meta.config_assistant_ui_state SET state=%s,revision=revision+1 "
                "WHERE state_id=%s AND user_name=%s AND revision=%s RETURNING *",
                (Jsonb(state), state_id, self.actor.principal, expected_revision),
            )
        if row is None:
            raise MetadataConflict()
        return row

    # ── Translations (shared cache) ───────────────────────────────────────

    async def translations(self, language: str, version: int) -> dict[str, str]:
        await self._bound()
        rows = await self._all(
            "SELECT message_id,text FROM nova_meta.config_i18n_messages "
            "WHERE language=%s AND version=%s", (language, version),
        )
        return {row["message_id"]: row["text"] for row in rows}

    async def store_translations(self, language: str, version: int, texts: dict[str, str]) -> None:
        """Keep each translation unless a newer catalog version already replaced it."""
        await self._bound()
        for message_id, text in texts.items():
            await self.connection.execute(
                "INSERT INTO nova_meta.config_i18n_messages (language,message_id,version,text) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (language,message_id) DO UPDATE SET "
                "version=EXCLUDED.version,text=EXCLUDED.text "
                "WHERE config_i18n_messages.version<=EXCLUDED.version",
                (language, message_id, version, text),
            )
