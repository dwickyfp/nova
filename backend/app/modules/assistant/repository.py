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
from uuid import uuid4

from app.core.database import db

#: Primary Key tables support the UPDATE/DELETE this CRUD needs.
THREADS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS (
    thread_id         VARCHAR(64) NOT NULL,
    user_name         VARCHAR(128) NOT NULL,
    title             VARCHAR(256) NOT NULL,
    workspace_file_id VARCHAR(64),
    created_at        DATETIME NOT NULL,
    updated_at        DATETIME NOT NULL
) PRIMARY KEY(thread_id)
DISTRIBUTED BY HASH(thread_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

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
    created_at  DATETIME NOT NULL
) PRIMARY KEY(message_id)
DISTRIBUTED BY HASH(message_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


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
        await db.execute_system(MESSAGES_DDL)

    # ── Threads ────────────────────────────────────────────────

    async def create_thread(
        self,
        *,
        user_name: str,
        title: str | None = None,
        workspace_file_id: str | None = None,
    ) -> dict:
        thread_id = str(uuid4())
        now = _now()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS "
            "(thread_id, user_name, title, workspace_file_id, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                thread_id,
                user_name,
                (title or "New conversation").strip() or "New conversation",
                workspace_file_id,
                now,
                now,
            ],
        )
        return {
            "thread_id": thread_id,
            "user_name": user_name,
            "title": (title or "New conversation").strip() or "New conversation",
            "workspace_file_id": workspace_file_id,
            "created_at": now,
            "updated_at": now,
        }

    async def list_threads(self, *, user_name: str) -> list[dict]:
        result = await db.execute_system(
            "SELECT t.thread_id, t.user_name, t.title, t.workspace_file_id, "
            "t.created_at, t.updated_at, "
            "(SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES m "
            " WHERE m.thread_id = t.thread_id AND m.user_name = t.user_name) AS message_count "
            "FROM NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS t "
            "WHERE t.user_name = %s ORDER BY t.updated_at DESC",
            [user_name],
        )
        return [_thread_row(row) for row in result["rows"]]

    async def get_thread(self, thread_id: str, *, user_name: str) -> dict | None:
        result = await db.execute_system(
            "SELECT t.thread_id, t.user_name, t.title, t.workspace_file_id, "
            "t.created_at, t.updated_at, "
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
        result = await db.execute_system(
            "SELECT message_id, role, content, created_at "
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
    ) -> dict:
        """Append one message and bump the thread's ``updated_at``.

        ``seq`` is the thread's current message count, so it is monotonic per
        thread and preserves order exactly.
        """
        count = await db.execute_system(
            "SELECT COUNT(*) AS n FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES "
            "WHERE thread_id = %s AND user_name = %s",
            [thread_id, user_name],
        )
        seq = int(count["rows"][0][0]) if count["rows"] else 0
        mid = message_id or str(uuid4())
        now = _now()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES "
            "(message_id, thread_id, user_name, seq, role, content, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [mid, thread_id, user_name, seq, role, content, now],
        )
        await self.touch_thread(thread_id, user_name=user_name)
        return {
            "message_id": mid,
            "role": role,
            "content": content,
            "created_at": now,
        }


def _thread_row(row: list) -> dict:
    return {
        "thread_id": row[0],
        "user_name": row[1],
        "title": row[2],
        "workspace_file_id": row[3],
        "created_at": _iso(row[4]),
        "updated_at": _iso(row[5]),
        "message_count": int(row[6]) if len(row) > 6 and row[6] is not None else 0,
    }


#: Process-wide repository.
assistant_repository = AssistantRepository()


__all__ = ["AssistantRepository", "assistant_repository", "THREADS_DDL", "MESSAGES_DDL"]
