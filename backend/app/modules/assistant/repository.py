"""Persistent assistant thread storage — NOVA_SYSTEM.CONFIG_ASSISTANT_*.

Threads and their messages are stored in StarRocks (the single-database
invariant, AGENTS.md §3), so a conversation survives a reload or restart. The
previous design kept them in process memory (E5a); that made every thread vanish
on reload, which is the bug this replaces.

**Per-user isolation is a hard requirement.** Every read and write is filtered by
``user_name`` in SQL — not in Python after the fact — so one user's history can
never be returned to another even by a query bug in a caller. ``get``/``delete``
take an owner and return nothing for a thread that is not theirs.

No credential column exists here: a thread holds titles and message text only.
Consent grants stay in process memory (``state.py``): a grant is deliberately
ephemeral and must not be revived by a restart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.database import db

#: Primary Key tables support the UPDATE/DELETE this CRUD needs.
THREADS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS (
    thread_id         VARCHAR(64) NOT NULL,
    user_name         VARCHAR(128) NOT NULL,
    title             VARCHAR(256) NOT NULL,
    workspace_file_id VARCHAR(64),
    agent_id          VARCHAR(64),
    created_at        DATETIME NOT NULL,
    updated_at        DATETIME NOT NULL
) PRIMARY KEY(thread_id)
DISTRIBUTED BY HASH(thread_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

#: Additive migration for a table created before Agent Studio (Phase 12). A
#: thread carries the agent it belongs to; ``NULL`` is a Phase-10 thread with no
#: agent. StarRocks rejects a duplicate ADD COLUMN, so the caller treats a
#: "duplicate" error as success (the column already exists).
THREADS_AGENT_ID_DDL = (
    "ALTER TABLE NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS "
    "ADD COLUMN agent_id VARCHAR(64)"
)

#: One row per message, ordered by ``seq`` (a per-thread counter) so replay keeps
#: the exact turn order without relying on a timestamp tie-break. Distributed by
#: the primary key: a Primary Key table requires the distribution column to be a
#: key column.
MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES (
    message_id  VARCHAR(64) NOT NULL,
    thread_id   VARCHAR(64) NOT NULL,
    user_name   VARCHAR(128) NOT NULL,
    seq         INT NOT NULL,
    role        VARCHAR(16) NOT NULL,
    content     TEXT,
    created_at  DATETIME NOT NULL,
    agent_id    VARCHAR(64),
    model_name  VARCHAR(128),
    prompt_tokens     INT,
    completion_tokens INT,
    total_tokens      INT,
    steps             JSON,
    instructions      TEXT
) PRIMARY KEY(message_id)
DISTRIBUTED BY HASH(message_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

#: Additive migrations for a messages table created before Agent Studio and
#: before token tracking. Each is best-effort: a fresh table already has the
#: column, so "already exists" is the expected result and is ignored.
MESSAGES_ADD_COLUMNS_DDL = (
    "ALTER TABLE NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES ADD COLUMN {column} {type}"
)


def _dump(value: Any) -> str:
    """Serialize a JSON column value. StarRocks accepts a JSON string literal."""
    import json

    return json.dumps(value, separators=(",", ":"), default=str)


def _steps_or_empty(value: Any) -> list:
    """Coerce a stored ``steps`` column to a list.

    The engine may hand back a JSON column already decoded, or as a string,
    depending on the driver path. A malformed or absent value becomes an empty
    list rather than raising: a conversation must still open without its trace.
    """
    import json

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
    """Coerce a stored DATETIME to a naive-UTC ``datetime`` for the view layer."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value[:19], fmt)
            except ValueError:
                continue
    return _now()


class AssistantRepository:
    """CRUD for assistant threads and messages, always user-scoped."""

    async def ensure_schema(self) -> None:
        await db.execute_system(THREADS_DDL)
        # Additive: a pre-existing threads table predates ``agent_id``. A fresh
        # table already has it, so a duplicate-column error is expected and
        # ignored; any other error is a real schema problem and is re-raised.
        try:
            await db.execute_system(THREADS_AGENT_ID_DDL)
        except Exception as exc:  # noqa: BLE001 - only "already exists" is benign
            # StarRocks reports a duplicate ADD COLUMN as "already exists";
            # older builds may say "duplicate". Either means the column is
            # present, which is the desired end state.
            message = str(exc).lower()
            if "already exists" not in message and "duplicate" not in message:
                raise
        await db.execute_system(MESSAGES_DDL)
        # Additive columns on the messages table (agent binding + token usage).
        for column, column_type in (
            ("agent_id", "VARCHAR(64)"),
            ("model_name", "VARCHAR(128)"),
            ("prompt_tokens", "INT"),
            ("completion_tokens", "INT"),
            ("total_tokens", "INT"),
            ("steps", "JSON"),
            ("instructions", "TEXT"),
        ):
            try:
                await db.execute_system(
                    MESSAGES_ADD_COLUMNS_DDL.format(column=column, type=column_type)
                )
            except Exception as exc:  # noqa: BLE001 - "already exists" is benign
                message = str(exc).lower()
                if "already exists" not in message and "duplicate" not in message:
                    raise

    # ── Threads ────────────────────────────────────────────────

    async def create_thread(
        self,
        *,
        user_name: str,
        title: str | None = None,
        workspace_file_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict:
        thread_id = str(uuid4())
        now = _now()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS "
            "(thread_id, user_name, title, workspace_file_id, agent_id, "
            "created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                thread_id,
                user_name,
                (title or "New conversation").strip() or "New conversation",
                workspace_file_id,
                agent_id,
                now,
                now,
            ],
        )
        return {
            "thread_id": thread_id,
            "user_name": user_name,
            "title": (title or "New conversation").strip() or "New conversation",
            "workspace_file_id": workspace_file_id,
            "agent_id": agent_id,
            "created_at": now,
            "updated_at": now,
        }

    async def list_threads(
        self, *, user_name: str, agent_id: str | None = None
    ) -> list[dict]:
        clauses = ["t.user_name = %s"]
        params: list = [user_name]
        if agent_id is not None:
            clauses.append("t.agent_id = %s")
            params.append(agent_id)
        result = await db.execute_system(
            "SELECT t.thread_id, t.user_name, t.title, t.workspace_file_id, "
            "t.agent_id, t.created_at, t.updated_at, "
            "(SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES m "
            " WHERE m.thread_id = t.thread_id AND m.user_name = t.user_name) AS message_count "
            "FROM NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS t "
            f"WHERE {' AND '.join(clauses)} ORDER BY t.updated_at DESC",
            params,
        )
        return [_thread_row(row) for row in result["rows"]]

    async def get_thread(self, thread_id: str, *, user_name: str) -> dict | None:
        result = await db.execute_system(
            "SELECT t.thread_id, t.user_name, t.title, t.workspace_file_id, "
            "t.agent_id, t.created_at, t.updated_at, "
            "(SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES m "
            " WHERE m.thread_id = t.thread_id AND m.user_name = t.user_name) AS message_count "
            "FROM NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS t "
            "WHERE t.thread_id = %s AND t.user_name = %s",
            [thread_id, user_name],
        )
        if not result["rows"]:
            return None
        return _thread_row(result["rows"][0])

    async def rename_thread(
        self, thread_id: str, title: str, *, user_name: str
    ) -> dict | None:
        now = _now()
        result = await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS SET title = %s, updated_at = %s "
            "WHERE thread_id = %s AND user_name = %s",
            [title, now, thread_id, user_name],
        )
        if result.get("affected", 0) == 0:
            # UPDATE reports 0 affected when the row is absent *or* when the new
            # title equals the old one, so confirm existence with a read.
            return await self.get_thread(thread_id, user_name=user_name)
        return await self.get_thread(thread_id, user_name=user_name)

    async def delete_thread(self, thread_id: str, *, user_name: str) -> bool:
        # Delete messages first so an interrupted delete cannot leave orphans.
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES "
            "WHERE thread_id = %s AND user_name = %s",
            [thread_id, user_name],
        )
        result = await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS "
            "WHERE thread_id = %s AND user_name = %s",
            [thread_id, user_name],
        )
        return bool(result.get("affected", 0))

    async def touch_thread(self, thread_id: str, *, user_name: str) -> None:
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS SET updated_at = %s "
            "WHERE thread_id = %s AND user_name = %s",
            [_now(), thread_id, user_name],
        )

    # ── Messages ───────────────────────────────────────────────

    async def list_messages(self, thread_id: str, *, user_name: str) -> list[dict]:
        """Every message on a thread, oldest first, with its trace.

        ``steps`` is the assistant turn's recorded trace. It is returned so a
        reopened conversation can rebuild its process view: without it a reload
        shows the answers and silently drops the work that produced them.
        """
        result = await db.execute_system(
            "SELECT message_id, role, content, created_at, model_name, "
            "prompt_tokens, completion_tokens, total_tokens, steps "
            "FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES "
            "WHERE thread_id = %s AND user_name = %s ORDER BY seq ASC",
            [thread_id, user_name],
        )
        return [
            {
                "message_id": row[0],
                "role": row[1],
                "content": row[2] or "",
                "created_at": _iso(row[3]),
                "model_name": row[4] if len(row) > 4 else None,
                "prompt_tokens": row[5] if len(row) > 5 else None,
                "completion_tokens": row[6] if len(row) > 6 else None,
                "total_tokens": row[7] if len(row) > 7 else None,
                "steps": _steps_or_empty(row[8] if len(row) > 8 else None),
            }
            for row in result["rows"]
        ]

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
    ) -> dict:
        """Append one message and bump the thread's ``updated_at``.

        ``seq`` is the thread's current message count, so it is monotonic per
        thread and preserves order exactly.

        ``steps`` is the assistant turn's trace: the ordered list of what the
        loop did (reasoning, tool calls with their redacted SQL and status, the
        final answer). Stored on the assistant row so Observability can replay a
        turn without a second store. It carries redacted SQL only, never rows or
        credentials.

        ``usage`` is the provider's token report for the assistant turn, if any
        (``{prompt_tokens, completion_tokens, total_tokens}``). It is metadata
        about cost, never content, and is stored only on the assistant row.
        """
        count = await db.execute_system(
            "SELECT COUNT(*) AS n FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES "
            "WHERE thread_id = %s AND user_name = %s",
            [thread_id, user_name],
        )
        seq = int(count["rows"][0][0]) if count["rows"] else 0
        mid = message_id or str(uuid4())
        now = _now()
        prompt_tokens = _int_or_none(usage, "prompt_tokens")
        completion_tokens = _int_or_none(usage, "completion_tokens")
        total_tokens = _int_or_none(usage, "total_tokens")
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES "
            "(message_id, thread_id, user_name, seq, role, content, created_at, "
            "agent_id, model_name, prompt_tokens, completion_tokens, total_tokens, "
            "steps, instructions) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                mid,
                thread_id,
                user_name,
                seq,
                role,
                content,
                now,
                agent_id,
                model_name,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                _dump(steps or []),
                instructions,
            ],
        )
        await self.touch_thread(thread_id, user_name=user_name)
        return {
            "message_id": mid,
            "role": role,
            "content": content,
            "created_at": now,
            "agent_id": agent_id,
            "model_name": model_name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "steps": steps or [],
            "instructions": instructions,
        }


def _int_or_none(usage: dict | None, key: str) -> int | None:
    """Read one integer token count from a provider usage dict, or None."""
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _thread_row(row: list) -> dict:
    return {
        "thread_id": row[0],
        "user_name": row[1],
        "title": row[2],
        "workspace_file_id": row[3],
        "agent_id": row[4],
        "created_at": _iso(row[5]),
        "updated_at": _iso(row[6]),
        "message_count": int(row[7]) if len(row) > 7 and row[7] is not None else 0,
    }


#: Process-wide repository.
assistant_repository = AssistantRepository()


__all__ = ["AssistantRepository", "assistant_repository", "THREADS_DDL", "MESSAGES_DDL"]
