"""Durable state for Auto runs; the existing direct-run journal remains readable."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.common.audit import write_audit_log
from app.core.database import db
from app.core.redis import session_store
from app.modules.agents.run_journal import run_journal
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools.redaction import is_credential_value

logger = logging.getLogger(__name__)

RUN_COLUMNS = (
    ("session_id", "VARCHAR(64)"),
    ("root_run_id", "VARCHAR(64)"),
    ("parent_run_id", "VARCHAR(64)"),
    ("depth", "INT"),
    ("objective", "TEXT"),
    ("payload", "JSON"),
    ("checkpoint", "JSON"),
    ("result_summary", "TEXT"),
    ("prompt_tokens", "BIGINT"),
    ("completion_tokens", "BIGINT"),
    ("lease_owner", "VARCHAR(64)"),
    ("generation", "INT"),
    ("security_version", "INT"),
    ("error_class", "VARCHAR(64)"),
)

MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_MESSAGES (
    message_id VARCHAR(64) NOT NULL,
    root_run_id VARCHAR(64) NOT NULL,
    sender_run_id VARCHAR(64) NOT NULL,
    recipient_run_id VARCHAR(64) NOT NULL,
    message_type VARCHAR(32) NOT NULL,
    correlation_id VARCHAR(64),
    reply_to VARCHAR(64),
    content TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    consumed_at DATETIME,
    origin VARCHAR(16) NOT NULL
) PRIMARY KEY(message_id)
DISTRIBUTED BY HASH(message_id) BUCKETS 4
ORDER BY (recipient_run_id, created_at)
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

SESSION_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS (
    event_id VARCHAR(64) NOT NULL,
    root_run_id VARCHAR(64) NOT NULL,
    session_sequence BIGINT,
    run_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    payload JSON,
    created_at DATETIME NOT NULL
) PRIMARY KEY(event_id)
DISTRIBUTED BY HASH(event_id) BUCKETS 4
ORDER BY (root_run_id, session_sequence)
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

RUN_SELECT = (
    "run_id, owner_name, agent_id, thread_id, role_name, status, session_id, "
    "root_run_id, parent_run_id, depth, objective, payload, checkpoint, "
    "result_summary, prompt_tokens, completion_tokens, lease_owner, generation, "
    "security_version, error_class, started_at, updated_at"
)
RUN_KEYS = tuple(part.strip() for part in RUN_SELECT.split(","))
TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
SESSION_EVENT_TYPES = frozenset(
    {
        "agent_queued",
        "agent_started",
        "agent_waiting",
        "agent_resumed",
        "agent_interrupted",
        "agent_message",
        "agent_completed",
        "agent_failed",
        "agent_cancelled",
        "delegation_plan",
        "tool_activity",
        "child_activity",
    }
)
TERMINAL_EVENT_TYPES = frozenset(
    {"agent_completed", "agent_failed", "agent_cancelled", "agent_interrupted"}
)
TERMINAL_STATUS_EVENTS = {
    "completed": "agent_completed",
    "failed": "agent_failed",
    "cancelled": "agent_cancelled",
    "interrupted": "agent_interrupted",
}


class AutoAdmissionUnavailable(RuntimeError):
    pass


EVENT_SEQUENCE_SCRIPT = """
local current = redis.call('GET', KEYS[1])
local floor = tonumber(ARGV[1])
if not current or tonumber(current) < floor then
    redis.call('SET', KEYS[1], floor)
end
local sequence = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], 86400)
return sequence
"""
EVENT_LOCK_SECONDS = 600
EVENT_LOCK_RENEW_SECONDS = 30


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _run(row: list[Any]) -> dict[str, Any]:
    data = dict(zip(RUN_KEYS, row, strict=True))
    data["payload"] = _json(data["payload"]) or {}
    data["checkpoint"] = _json(data["checkpoint"]) or {}
    return data


@asynccontextmanager
async def _event_lock(client: Any, root_run_id: str):
    lock = client.lock(
        f"nova:auto:event:lock:{root_run_id}",
        timeout=EVENT_LOCK_SECONDS,
        blocking_timeout=30,
    )
    async with lock:
        stopped = asyncio.Event()
        lost = asyncio.Event()

        async def renew() -> None:
            while not stopped.is_set():
                try:
                    await asyncio.wait_for(
                        stopped.wait(), timeout=EVENT_LOCK_RENEW_SECONDS
                    )
                    return
                except TimeoutError:
                    pass
                try:
                    if not await lock.extend(EVENT_LOCK_SECONDS, replace_ttl=True):
                        lost.set()
                        return
                except Exception as exc:
                    logger.warning("Auto event lock renewal failed: %s", type(exc).__name__)
                    lost.set()
                    return

        renewal = asyncio.create_task(renew())

        async def assert_owned() -> None:
            if lost.is_set() or not await lock.owned():
                raise RuntimeError("Auto event lock was lost")

        try:
            yield assert_owned
        finally:
            stopped.set()
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal


class HarnessRepository:
    @asynccontextmanager
    async def admission_lock(self, thread_id: str, owner_name: str):
        client = session_store._redis
        if client is None:
            raise AutoAdmissionUnavailable("Auto admission is temporarily unavailable")
        scope = hashlib.sha256(f"{owner_name}\0{thread_id}".encode()).hexdigest()
        body_failed = False
        try:
            async with _event_lock(client, f"admission:{scope}") as assert_owned:
                async def assert_admitted() -> None:
                    try:
                        await assert_owned()
                    except Exception as exc:
                        raise AutoAdmissionUnavailable(
                            "Auto admission lock was lost"
                        ) from exc

                try:
                    yield assert_admitted
                except BaseException:
                    body_failed = True
                    raise
        except Exception as exc:
            if body_failed:
                raise
            raise AutoAdmissionUnavailable("Auto admission lock is unavailable") from exc

    async def ensure_schema(self) -> None:
        await run_journal.ensure_schema()
        for column, kind in RUN_COLUMNS:
            await self._ensure_column("CONFIG_AGENT_RUNS", column, kind)
        await db.execute_system(MESSAGES_DDL)
        await self._ensure_column(
            "CONFIG_AGENT_MESSAGES", "origin", "VARCHAR(16) NOT NULL DEFAULT 'agent'"
        )
        await db.execute_system(SESSION_EVENTS_DDL)
        await self._ensure_column("CONFIG_AGENT_SESSION_EVENTS", "session_sequence", "BIGINT")
        await self._backfill_event_sequences()

    @staticmethod
    async def _backfill_event_sequences() -> None:
        legacy = await db.execute_system(
            "SELECT event_id, root_run_id FROM NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS "
            "WHERE session_sequence IS NULL ORDER BY root_run_id, created_at, event_id"
        )
        next_sequence: dict[str, int] = {}
        for event_id, root_run_id in legacy["rows"]:
            if root_run_id not in next_sequence:
                root = await db.execute_system(
                    "SELECT last_sequence FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS WHERE run_id = %s",
                    [root_run_id],
                )
                next_sequence[root_run_id] = int(root["rows"][0][0]) if root["rows"] else -1
            next_sequence[root_run_id] += 1
            sequence = next_sequence[root_run_id]
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS "
                "SET session_sequence = %s WHERE event_id = %s AND session_sequence IS NULL",
                [sequence, event_id],
            )
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET last_sequence = %s "
                "WHERE run_id = %s AND last_sequence < %s",
                [sequence, root_run_id, sequence],
            )

    async def _ensure_column(self, table: str, column: str, kind: str) -> None:
        for attempt in range(20):
            if await self._column_exists(table, column):
                return
            try:
                await db.execute_system(
                    f"ALTER TABLE NOVA_SYSTEM.{table} ADD COLUMN {column} {kind}"
                )
                return
            except Exception as exc:
                message = str(exc).lower()
                if "already exists" in message or "duplicate" in message:
                    return
                if "schema change" not in message or attempt == 19:
                    raise
                await asyncio.sleep(0.5)

    @staticmethod
    async def _column_exists(table: str, column: str) -> bool:
        result = await db.execute_system(
            "SELECT COLUMN_NAME FROM information_schema.columns "
            "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' AND TABLE_NAME = %s "
            "AND COLUMN_NAME = %s",
            [table, column],
        )
        return bool(result["rows"])

    async def create_root(
        self,
        *,
        owner_name: str,
        thread_id: str,
        role_name: str,
        session_id: str,
        security_version: int,
        objective: str,
        provider_id: str | None = None,
        model: str | None = None,
        user_message_id: str | None = None,
    ) -> dict[str, Any]:
        if not objective.strip() or len(objective) > 8000 or contains_credential_shape(objective):
            raise ValueError("Invalid Auto objective")
        return await self._create(
            run_id=str(uuid4()),
            root_run_id=None,
            parent_run_id=None,
            agent_id="__auto__",
            owner_name=owner_name,
            thread_id=thread_id,
            role_name=role_name,
            session_id=session_id,
            security_version=security_version,
            depth=0,
            objective=objective,
            payload={
                "provider_id": provider_id,
                "model": model,
                "user_message_id": user_message_id,
            },
            checkpoint={"phase": "plan"},
        )

    async def spawn(
        self,
        *,
        parent: dict[str, Any],
        agent_id: str,
        objective: str,
        operation_id: str,
        context: str = "",
        agent_name: str = "",
    ) -> dict[str, Any]:
        if parent["depth"] != 0 or parent["agent_id"] != "__auto__":
            raise ValueError("Only the Auto root may spawn a specialist")
        if not operation_id or len(operation_id) > 128:
            raise ValueError("Invalid spawn operation id")
        if (
            not objective.strip()
            or contains_credential_shape(objective)
            or contains_credential_shape(context)
            or contains_credential_shape(agent_name)
        ):
            raise ValueError("Invalid delegated context")
        run_id = str(uuid5(NAMESPACE_URL, f"nova:spawn:{parent['run_id']}:{operation_id}"))
        existing = await self.get(run_id)
        if existing:
            if (
                existing["parent_run_id"] != parent["run_id"]
                or existing["agent_id"] != agent_id
                or existing["objective"] != objective[:4000]
                or existing["payload"].get("context", "") != context[:8000]
            ):
                raise ValueError("Spawn operation id collision")
            return existing
        return await self._create(
            run_id=run_id,
            root_run_id=parent["run_id"],
            parent_run_id=parent["run_id"],
            agent_id=agent_id,
            owner_name=parent["owner_name"],
            thread_id=parent["thread_id"],
            role_name=parent["role_name"],
            session_id=parent["session_id"],
            security_version=parent["security_version"],
            depth=1,
            objective=objective[:4000],
            payload={"context": context[:8000], "agent_name": agent_name[:128]},
            checkpoint={"phase": "execute"},
        )

    async def _create(self, **fields: Any) -> dict[str, Any]:
        now = _now()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "(run_id, owner_name, agent_id, thread_id, role_name, status, "
            "last_sequence, started_at, updated_at, session_id, root_run_id, "
            "parent_run_id, depth, objective, payload, checkpoint, "
            "prompt_tokens, completion_tokens, generation, security_version) "
            "VALUES (%s, %s, %s, %s, %s, 'queued', -1, %s, %s, "
            "%s, %s, %s, %s, %s, %s, %s, 0, 0, 0, %s)",
            [
                fields["run_id"],
                fields["owner_name"],
                fields["agent_id"],
                fields["thread_id"],
                fields["role_name"],
                now,
                now,
                fields["session_id"],
                fields["root_run_id"],
                fields["parent_run_id"],
                fields["depth"],
                fields["objective"],
                json.dumps(fields["payload"]),
                json.dumps(fields["checkpoint"]),
                fields["security_version"],
            ],
        )
        await self.event(
            fields["root_run_id"] or fields["run_id"],
            fields["run_id"],
            "agent_queued",
            {
                "agent_id": fields["agent_id"],
                "agent_name": str(fields["payload"].get("agent_name") or "")[:128],
                "objective": fields["objective"],
            },
        )
        await self._audit_run(fields)
        return (await self.get(fields["run_id"])) or fields

    @staticmethod
    async def _audit_run(run: dict[str, Any]) -> None:
        await write_audit_log(
            event_type="AGENT_RUN",
            user_name=run["owner_name"],
            action="SPAWN" if run["depth"] else "START",
            object_type="AGENT_RUN",
            object_name=run["run_id"],
            status="SUCCESS",
            session_id=run["session_id"],
            active_role=run["role_name"],
            security_context_version=run["security_version"],
            query_id=str(uuid5(NAMESPACE_URL, f"nova:audit:run:{run['run_id']}")),
        )

    async def get(self, run_id: str) -> dict[str, Any] | None:
        for attempt in range(20):
            rows = await self._run_rows(
                f"SELECT {RUN_SELECT} FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                "WHERE run_id = %s",
                [run_id],
            )
            if rows:
                run = _run(rows[0])
                if run["run_id"] != run_id:
                    if attempt < 19:
                        await asyncio.sleep(0.1)
                        continue
                    raise RuntimeError("Agent run lookup returned a different run")
                if run["root_run_id"] is not None or run["agent_id"] == "__auto__":
                    return run
                return None
            if attempt < 19:
                await asyncio.sleep(0.1)
        return None

    @staticmethod
    async def _run_rows(sql: str, params: list) -> list[list]:
        for attempt in range(3):
            result = await db.execute_system(sql, params)
            if all(len(row) == len(RUN_KEYS) for row in result["rows"]):
                return result["rows"]
            if attempt < 2:
                await asyncio.sleep(0.05)
        raise RuntimeError(
            "Agent run metadata query returned "
            f"{[len(row) for row in result['rows']]} columns instead of {len(RUN_KEYS)}"
        )

    async def tree(self, root_run_id: str, *, owner_name: str, role_name: str) -> list[dict]:
        root = await self.get(root_run_id)
        if not root or root["owner_name"] != owner_name or root["role_name"] != role_name:
            return []
        children: dict[str, dict] = {}
        for attempt in range(5):
            rows = await self._run_rows(
                f"SELECT {RUN_SELECT} FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                "WHERE root_run_id = %s ORDER BY started_at, run_id",
                [root_run_id],
            )
            for row in rows:
                child = _run(row)
                if child["root_run_id"] == root_run_id and child["run_id"] != root_run_id:
                    children[child["run_id"]] = child
            if attempt < 4:
                await asyncio.sleep(0.05)
        ordered = sorted(children.values(), key=lambda item: (item["started_at"], item["run_id"]))
        return [root, *ordered]

    async def queued(self, *, limit: int = 32) -> list[dict]:
        rows = await self._run_rows(
            f"SELECT {RUN_SELECT} FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "WHERE status = 'queued' AND (root_run_id IS NOT NULL OR agent_id = '__auto__') "
            "ORDER BY started_at LIMIT %s",
            [limit],
        )
        return [_run(row) for row in rows]

    async def active_for_thread(self, thread_id: str, owner_name: str) -> bool:
        sql = (
            "SELECT %s AS scoped_thread, %s AS scoped_owner, COUNT(*) "
            "FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "WHERE thread_id = %s AND owner_name = %s AND agent_id = '__auto__' "
            "AND status IN ('queued', 'running', 'waiting_for_agent', "
            "'waiting_for_message', 'waiting_for_auth')"
        )
        confirmations = 0
        last_error: Exception | None = None
        for attempt in range(8):
            try:
                result = await db.execute_system(
                    sql, [thread_id, owner_name, thread_id, owner_name]
                )
                rows = result.get("rows") if isinstance(result, dict) else None
                if (
                    not isinstance(rows, (list, tuple))
                    or len(rows) != 1
                    or not isinstance(rows[0], (list, tuple))
                    or len(rows[0]) != 3
                    or rows[0][0] != thread_id
                    or rows[0][1] != owner_name
                    or isinstance(rows[0][2], bool)
                    or not str(rows[0][2]).isdigit()
                ):
                    raise ValueError("Auto admission query returned inconsistent rows")
                if int(rows[0][2]) > 0:
                    return True
                confirmations += 1
                if confirmations >= 4:
                    return False
            except Exception as exc:
                confirmations = 0
                last_error = exc
            if attempt < 7:
                await asyncio.sleep(0.05)
        raise AutoAdmissionUnavailable("Auto admission metadata is unavailable") from last_error

    async def roots_for_thread(
        self,
        thread_id: str,
        *,
        owner_name: str,
        role_name: str,
        limit: int = 20,
    ) -> list[dict]:
        roots: dict[str, dict] = {}
        inconsistent = False
        for attempt in range(8):
            try:
                rows = await self._run_rows(
                    f"SELECT {RUN_SELECT} FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                    "WHERE thread_id = %s AND owner_name = %s AND role_name = %s "
                    "AND agent_id = '__auto__' ORDER BY started_at DESC LIMIT %s",
                    [thread_id, owner_name, role_name, min(max(limit, 1), 100)],
                )
            except RuntimeError as exc:
                if not str(exc).startswith("Agent run metadata query returned"):
                    raise
                inconsistent = True
                if attempt < 7:
                    await asyncio.sleep(0.05)
                continue
            for row in rows:
                try:
                    run = _run(row)
                except (TypeError, ValueError):
                    inconsistent = True
                    continue
                if (
                    run["thread_id"] != thread_id
                    or run["owner_name"] != owner_name
                    or run["role_name"] != role_name
                    or run["agent_id"] != "__auto__"
                    or run["depth"] != 0
                    or run["root_run_id"] is not None
                ):
                    inconsistent = True
                    continue
                previous = roots.get(run["run_id"])
                if not previous or str(run["updated_at"]) >= str(previous["updated_at"]):
                    roots[run["run_id"]] = run
            if attempt < 7:
                await asyncio.sleep(0.05)
        if not roots and inconsistent:
            raise RuntimeError("Auto thread run query returned inconsistent rows")
        return sorted(
            roots.values(), key=lambda run: (str(run["started_at"]), run["run_id"]),
            reverse=True,
        )[:min(max(limit, 1), 100)]

    async def claim(self, run_id: str, worker_id: str) -> bool:
        result = await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET status = 'running', "
            "lease_owner = %s, generation = generation + 1, updated_at = %s "
            "WHERE run_id = %s AND status = 'queued'",
            [worker_id, _now(), run_id],
        )
        return result.get("affected", 0) == 1

    async def heartbeat(self, run_id: str, worker_id: str) -> None:
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET updated_at = %s "
            "WHERE run_id = %s AND status = 'running' AND lease_owner = %s",
            [_now(), run_id, worker_id],
        )

    async def refresh_owned_lease(
        self, run_id: str, *, lease_owner: str, generation: int
    ) -> bool:
        now = _now().replace(microsecond=0)
        result = await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "SET updated_at = IF(updated_at = %s, %s, %s) "
            "WHERE run_id = %s AND status = 'running' "
            "AND lease_owner = %s AND generation = %s",
            [now, now + timedelta(seconds=1), now, run_id, lease_owner, generation],
        )
        return result.get("affected", 0) == 1

    async def transition(
        self,
        run_id: str,
        *,
        from_status: str,
        to_status: str,
        lease_owner: str | None = None,
        generation: int | None = None,
        checkpoint: dict | None = None,
        summary: str | None = None,
        error_class: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> bool:
        if (lease_owner is None) != (generation is None):
            raise ValueError("A run transition must fence both lease owner and generation")
        allowed = {
            "running": {
                "queued",
                "waiting_for_agent",
                "waiting_for_message",
                "completed",
                "failed",
                "cancelled",
                "waiting_for_auth",
            },
            "waiting_for_agent": {"queued", "cancelled", "failed"},
            "waiting_for_message": {"queued", "cancelled", "failed"},
            "waiting_for_auth": {"queued", "cancelled", "failed"},
            "queued": {"cancelled"},
        }
        if to_status not in allowed.get(from_status, set()):
            raise ValueError(f"Invalid run transition: {from_status} -> {to_status}")
        assignments = ["status = %s", "updated_at = %s"]
        values: list[Any] = [to_status, _now()]
        for column, value in (
            ("checkpoint", json.dumps(checkpoint) if checkpoint is not None else None),
            ("result_summary", summary),
            ("error_class", error_class),
            ("prompt_tokens", prompt_tokens),
            ("completion_tokens", completion_tokens),
        ):
            if value is not None:
                assignments.append(f"{column} = %s")
                values.append(value)
        where = " WHERE run_id = %s AND status = %s"
        match_values: list[Any] = [run_id, from_status]
        if lease_owner is not None:
            where += " AND lease_owner = %s AND generation = %s"
            match_values.extend((lease_owner, generation))
        for attempt in range(8):
            result = await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET "
                + ", ".join(assignments)
                + where,
                [*values, *match_values],
            )
            if result.get("affected", 0) == 1:
                return True
            current = await db.execute_system(
                "SELECT status, lease_owner, generation FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                "WHERE run_id = %s"
                if lease_owner is not None
                else "SELECT status FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS WHERE run_id = %s",
                [run_id],
            )
            if current["rows"] and current["rows"][0][0] != from_status:
                return False
            if lease_owner is not None and current["rows"]:
                row = current["rows"][0]
                if len(row) == 3 and (
                    row[1] != lease_owner or str(row[2]) != str(generation)
                ):
                    return False
            if attempt < 7:
                await asyncio.sleep(0.05)
        return False

    async def add_usage(self, run_id: str, *, prompt_tokens: int, completion_tokens: int) -> None:
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET "
            "prompt_tokens = prompt_tokens + %s, "
            "completion_tokens = completion_tokens + %s, updated_at = %s "
            "WHERE run_id = %s AND status = 'running'",
            [max(0, prompt_tokens), max(0, completion_tokens), _now(), run_id],
        )

    async def recover_stale(self, *, stale_seconds: int = 90) -> list[str]:
        cutoff = _now() - timedelta(seconds=stale_seconds)
        result = await db.execute_system(
            "SELECT run_id, root_run_id FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "WHERE status = 'running' AND updated_at < %s "
            "AND (root_run_id IS NOT NULL OR agent_id = '__auto__')",
            [cutoff],
        )
        recovered = []
        for run_id, root_id in result["rows"]:
            # A coordinator checkpoint is replayable: spawn and message operation
            # ids are stable. A child may have run a mutating tool; never replay it.
            target = "queued" if str(root_id or run_id) == str(run_id) else "interrupted"
            changed = await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                "SET status = %s, error_class = 'worker_lost', updated_at = %s "
                "WHERE run_id = %s AND status = 'running' AND updated_at < %s",
                [target, _now(), run_id, cutoff],
            )
            if changed.get("affected", 0) == 1:
                recovered.append(str(run_id))
                try:
                    await self.event(
                        str(root_id or run_id),
                        str(run_id),
                        "agent_resumed" if target == "queued" else "agent_interrupted",
                        {},
                    )
                except ValueError as exc:
                    if str(exc) == "Conflicting Auto terminal event" and target == "interrupted":
                        try:
                            await self._restore_prior_terminal(str(root_id), str(run_id))
                        except RuntimeError as repair_error:
                            await db.execute_system(
                                "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                                "SET status = 'running', error_class = NULL, updated_at = %s "
                                "WHERE run_id = %s AND root_run_id = %s "
                                "AND status = 'interrupted' AND error_class = 'worker_lost'",
                                [
                                    _now() - timedelta(seconds=stale_seconds + 1),
                                    run_id,
                                    root_id,
                                ],
                            )
                            recovered.pop()
                            logger.warning(
                                "Auto terminal repair deferred for run %s: %s",
                                run_id,
                                type(repair_error).__name__,
                            )
                            continue
                    elif str(exc) != "Auto root no longer exists":
                        raise
                    else:
                        logger.warning(
                            "Auto recovery event skipped for run %s because root %s is not visible",
                            run_id,
                            root_id or run_id,
                        )
                if target == "interrupted":
                    await self.wake_parent(str(root_id or run_id))
        return recovered

    @staticmethod
    async def _prior_terminal(root_run_id: str, run_id: str) -> tuple[str, dict]:
        for attempt in range(12):
            result = await db.execute_system(
                "SELECT root_run_id, run_id, event_type, payload "
                "FROM NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS "
                "WHERE root_run_id = %s AND run_id = %s "
                "AND event_type IN ('agent_completed', 'agent_failed', "
                "'agent_cancelled', 'agent_interrupted')",
                [root_run_id, run_id],
            )
            rows = result.get("rows") if isinstance(result, dict) else None
            if isinstance(rows, list) and len(rows) == 1:
                row = rows[0]
                if (
                    isinstance(row, (list, tuple))
                    and len(row) == 4
                    and row[0] == root_run_id
                    and row[1] == run_id
                    and row[2] in TERMINAL_EVENT_TYPES
                ):
                    payload = _json(row[3]) or {}
                    if isinstance(payload, dict):
                        return str(row[2]), payload
            if attempt < 11:
                await asyncio.sleep(0.1)
        raise RuntimeError("Conflicting Auto terminal event could not be reconciled")

    async def _restore_prior_terminal(self, root_run_id: str, run_id: str) -> None:
        kind, payload = await self._prior_terminal(root_run_id, run_id)
        status = {
            "agent_completed": "completed",
            "agent_failed": "failed",
            "agent_cancelled": "cancelled",
            "agent_interrupted": "interrupted",
        }[kind]
        assignments = ["status = %s", "updated_at = %s"]
        values: list[Any] = [status, _now()]
        if status == "completed":
            assignments.extend(("result_summary = %s", "error_class = NULL"))
            values.append(str(payload.get("summary") or "")[:8000])
        elif status == "failed":
            assignments.append("error_class = %s")
            values.append(str(payload.get("error_class") or "")[:64])
        elif status == "cancelled":
            assignments.append("error_class = NULL")
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET " + ", ".join(assignments)
            + " WHERE run_id = %s AND root_run_id = %s "
            "AND status = 'interrupted' AND error_class = 'worker_lost'",
            [*values, run_id, root_run_id],
        )

    async def expire_waiting(self, *, max_age_seconds: int = 600) -> list[str]:
        cutoff = _now() - timedelta(seconds=max_age_seconds)
        result = await db.execute_system(
            "SELECT run_id FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "WHERE agent_id = '__auto__' AND started_at < %s "
            "AND status IN ('waiting_for_agent', 'waiting_for_message', 'waiting_for_auth')",
            [cutoff],
        )
        expired = []
        for row in result["rows"]:
            root_id = str(row[0])
            changed = await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                "SET status = 'failed', error_class = 'session_timeout', updated_at = %s "
                "WHERE run_id = %s AND started_at < %s "
                "AND status IN ('waiting_for_agent', 'waiting_for_message', 'waiting_for_auth')",
                [_now(), root_id, cutoff],
            )
            if changed.get("affected", 0) == 1:
                expired.append(root_id)
                await db.execute_system(
                    "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                    "SET status = 'cancelled', updated_at = %s "
                    "WHERE root_run_id = %s AND status IN "
                    "('queued', 'running', 'waiting_for_message', 'waiting_for_auth')",
                    [_now(), root_id],
                )
                await self.event(
                    root_id,
                    root_id,
                    "agent_failed",
                    {
                        "error_class": "session_timeout",
                    },
                )
        return expired

    async def send(
        self,
        *,
        sender: dict,
        recipient: dict,
        operation_id: str,
        message_type: str,
        content: str,
        correlation_id: str | None = None,
        reply_to: str | None = None,
        origin: str = "agent",
    ) -> str:
        root_id = sender["root_run_id"] or sender["run_id"]
        if root_id != (recipient["root_run_id"] or recipient["run_id"]):
            raise ValueError("Runs belong to different execution trees")
        if (
            sender["owner_name"] != recipient["owner_name"]
            or sender["role_name"] != recipient["role_name"]
        ):
            raise ValueError("Message crosses a security boundary")
        if sender["run_id"] == recipient["run_id"]:
            raise ValueError("A run cannot message itself")
        if not (sender["depth"] == 0 or recipient["depth"] == 0):
            raise ValueError("Specialists communicate through Auto")
        if message_type not in {"message", "question", "answer", "finding", "final", "control"}:
            raise ValueError("Invalid message type")
        if origin not in {"agent", "user"}:
            raise ValueError("Invalid message origin")
        if not operation_id or len(operation_id) > 128:
            raise ValueError("Invalid message operation id")
        if (
            not content
            or len(content) > 8000
            or contains_credential_shape(content)
            or is_credential_value(content)
        ):
            raise ValueError("Invalid agent message")
        message_id = str(uuid5(NAMESPACE_URL, f"nova:message:{sender['run_id']}:{operation_id}"))
        for attempt in range(5):
            existing = await db.execute_system(
                "SELECT message_id, recipient_run_id, message_type, correlation_id, "
                "reply_to, content, origin "
                "FROM NOVA_SYSTEM.CONFIG_AGENT_MESSAGES WHERE message_id = %s",
                [message_id],
            )
            if not existing["rows"]:
                break
            old = existing["rows"][0]
            if len(old) != 7 or str(old[0]) != message_id:
                if attempt == 4:
                    raise RuntimeError("Message id lookup returned inconsistent metadata")
                await asyncio.sleep(0.05)
                continue
            mismatch = (
                old[1] != recipient["run_id"],
                old[2] != message_type,
                old[3] != correlation_id,
                old[4] != reply_to,
                old[5] != content,
                old[6] != origin,
            )
            if not any(mismatch):
                return message_id
            if attempt == 4:
                logger.warning("Message operation id collision fields: %s", mismatch)
                raise ValueError("Message operation id was already used for different content")
            await asyncio.sleep(0.05)
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_MESSAGES "
            "(message_id, root_run_id, sender_run_id, recipient_run_id, message_type, "
            "correlation_id, reply_to, content, created_at, origin) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                message_id,
                root_id,
                sender["run_id"],
                recipient["run_id"],
                message_type,
                correlation_id,
                reply_to,
                content,
                _now(),
                origin,
            ],
        )
        await self.event(
            root_id,
            sender["run_id"],
            "agent_message",
            {
                "message_id": message_id,
                "sender_run_id": sender["run_id"],
                "recipient_run_id": recipient["run_id"],
                "message_type": message_type,
                "content": content,
                "origin": origin,
            },
        )
        if recipient["depth"] == 0:
            await self.wake_parent(root_id)
        elif recipient["status"] == "waiting_for_message":
            await self.transition(
                recipient["run_id"], from_status="waiting_for_message", to_status="queued"
            )
        await self._audit_message(sender, recipient, message_id)
        return message_id

    @staticmethod
    async def _audit_message(sender: dict, recipient: dict, message_id: str) -> None:
        await write_audit_log(
            event_type="AGENT_MESSAGE",
            user_name=sender["owner_name"],
            action="SEND",
            object_type="AGENT_RUN",
            object_name=recipient["run_id"],
            status="SUCCESS",
            session_id=sender["session_id"],
            active_role=sender["role_name"],
            security_context_version=sender["security_version"],
            query_id=str(uuid5(NAMESPACE_URL, f"nova:audit:message:{message_id}")),
        )

    async def pending_messages(self, recipient_run_id: str, *, limit: int = 20) -> list[dict]:
        keys = (
            "message_id", "sender_run_id", "message_type", "content",
            "correlation_id", "reply_to", "origin",
        )
        for attempt in range(8):
            result = await db.execute_system(
                "SELECT recipient_run_id, message_id, sender_run_id, message_type, "
                "content, correlation_id, reply_to, origin "
                "FROM NOVA_SYSTEM.CONFIG_AGENT_MESSAGES "
                "WHERE recipient_run_id = %s AND consumed_at IS NULL "
                "ORDER BY created_at, message_id LIMIT %s",
                [recipient_run_id, limit],
            )
            rows = result.get("rows") if isinstance(result, dict) else None
            if isinstance(rows, list) and all(
                isinstance(row, (list, tuple))
                and len(row) == 8
                and row[0] == recipient_run_id
                and row[3] in {"message", "question", "answer", "finding", "final", "control"}
                and row[7] in {"agent", "user"}
                for row in rows
            ):
                return [dict(zip(keys, row[1:], strict=True)) for row in rows]
            if attempt < 7:
                await asyncio.sleep(0.05)
        raise RuntimeError("Auto pending message query returned inconsistent rows")

    async def acknowledge_messages(self, recipient_run_id: str, message_ids: list[str]) -> None:
        for message_id in dict.fromkeys(message_ids):
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENT_MESSAGES SET consumed_at = %s "
                "WHERE message_id = %s AND recipient_run_id = %s AND consumed_at IS NULL",
                [_now(), message_id, recipient_run_id],
            )

    async def receive(self, recipient_run_id: str, *, limit: int = 20) -> list[dict]:
        messages = []
        for item in await self.pending_messages(recipient_run_id, limit=limit):
            changed = await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENT_MESSAGES SET consumed_at = %s "
                "WHERE message_id = %s AND recipient_run_id = %s AND consumed_at IS NULL",
                [_now(), item["message_id"], recipient_run_id],
            )
            if changed.get("affected", 0) == 1:
                messages.append(item)
        return messages

    async def wake_parent(self, root_run_id: str) -> bool:
        root = await self.get(root_run_id)
        if not root or root["status"] not in {"waiting_for_agent", "waiting_for_message"}:
            return False
        return await self.transition(root_run_id, from_status=root["status"], to_status="queued")

    async def event(self, root_run_id: str, run_id: str, kind: str, payload: dict) -> str:
        if kind not in SESSION_EVENT_TYPES:
            raise ValueError("Unsupported Auto session event type")
        client = session_store._redis
        if client is None:
            raise RuntimeError("Auto event coordinator is unavailable")
        sequence_key = f"nova:auto:event:sequence:{root_run_id}"
        terminal_key = f"nova:auto:event:terminal:{root_run_id}"
        deleted_key = f"nova:auto:event:deleted:{root_run_id}"
        async with _event_lock(client, root_run_id) as assert_owned:
            if await client.get(deleted_key):
                raise ValueError("Auto root no longer exists")
            root = None
            for attempt in range(5):
                current = await db.execute_system(
                    "SELECT last_sequence FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS WHERE run_id = %s",
                    [root_run_id],
                )
                if current["rows"]:
                    root = current["rows"][0]
                    break
                if attempt < 4:
                    await asyncio.sleep(0.05)
            if root is None:
                raise ValueError("Auto root no longer exists")

            terminal = kind in TERMINAL_EVENT_TYPES
            event_id = (
                str(uuid5(NAMESPACE_URL, f"nova:auto:terminal:{root_run_id}:{run_id}"))
                if terminal
                else str(uuid4())
            )
            sequence = None
            if terminal:
                cached = await client.hget(terminal_key, run_id)
                cached_sequence = None
                if cached is not None:
                    cached_value = cached.decode() if isinstance(cached, bytes) else str(cached)
                    cached_kind, separator, cached_value_sequence = cached_value.partition(":")
                    if separator != ":" or cached_kind != kind:
                        raise ValueError("Conflicting Auto terminal event")
                    try:
                        cached_sequence = int(cached_value_sequence)
                    except ValueError as exc:
                        raise RuntimeError("Invalid cached Auto terminal sequence") from exc
                    if cached_sequence < 0:
                        raise RuntimeError("Invalid cached Auto terminal sequence")
                empty_streak = 0
                for attempt in range(20):
                    rows = await self._synced_terminal_rows(root_run_id)
                    valid = isinstance(rows, (list, tuple)) and all(
                        isinstance(row, (list, tuple))
                        and len(row) == 3
                        and isinstance(row[0], str)
                        and row[1] in TERMINAL_EVENT_TYPES
                        and isinstance(row[2], int)
                        and row[2] >= 0
                        for row in rows
                    )
                    if valid:
                        existing = [row for row in rows if row[0] == run_id]
                        if existing:
                            if len(existing) != 1 or existing[0][1] != kind:
                                raise ValueError("Conflicting Auto terminal event")
                            return str(existing[0][2])
                        empty_streak += 1
                        if attempt >= 7 and empty_streak >= 2:
                            break
                    else:
                        empty_streak = 0
                    if attempt < 19:
                        await asyncio.sleep(0.05)
                else:
                    if cached_sequence is not None:
                        return str(cached_sequence)
                    raise RuntimeError("Auto terminal lookup returned inconsistent rows")
                if cached_sequence is not None:
                    sequence = cached_sequence
            if sequence is None:
                floor = max(int(root[0]), (time.time_ns() // 1_000_000) * 100 - 1)
                sequence = int(await client.eval(EVENT_SEQUENCE_SCRIPT, 1, sequence_key, floor))
                if terminal:
                    await client.hset(terminal_key, run_id, f"{kind}:{sequence}")
                    await client.expire(terminal_key, 86400)
            if sequence * 3 + 2 > 2**53 - 1:
                raise RuntimeError("Auto event sequence exceeds the client cursor range")
            await assert_owned()
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET last_sequence = %s "
                "WHERE run_id = %s AND last_sequence < %s",
                [sequence, root_run_id, sequence],
            )
            await assert_owned()
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS "
                "(event_id, root_run_id, session_sequence, run_id, event_type, "
                "payload, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [event_id, root_run_id, sequence, run_id, kind, json.dumps(payload), _now()],
            )
            return str(sequence)

    async def reconcile_terminal_children(self, root_run_id: str, children: list[dict]) -> None:
        for child in children:
            if child.get("root_run_id") != root_run_id or child.get("depth") != 1:
                raise ValueError("Auto reconciliation crossed a run boundary")
            kind = TERMINAL_STATUS_EVENTS.get(child["status"])
            if kind is None:
                continue
            await self.event(root_run_id, child["run_id"], kind, self._terminal_payload(child))

    @staticmethod
    def _terminal_payload(run: dict) -> dict:
        kind = TERMINAL_STATUS_EVENTS[run["status"]]
        if kind == "agent_completed":
            return {"summary": str(run.get("result_summary") or "")[:8000]}
        if kind == "agent_failed":
            return {"error_class": str(run.get("error_class") or "")[:64]}
        return {}

    async def ensure_terminal_event(self, root_run_id: str, run: dict) -> None:
        run_id = run["run_id"]
        if run_id != root_run_id and (
            run.get("root_run_id") != root_run_id or run.get("depth") != 1
        ):
            raise ValueError("Auto terminal reconciliation crossed a run boundary")
        kind = TERMINAL_STATUS_EVENTS.get(run["status"])
        if kind is None:
            return
        if run_id == root_run_id and kind == "agent_completed":
            raise ValueError("Completed Auto root requires its saved final answer")
        for attempt in range(5):
            rows = await self._synced_terminal_rows(root_run_id)
            valid = all(
                isinstance(row, (list, tuple)) and len(row) == 3
                and isinstance(row[0], str)
                and row[1] in TERMINAL_EVENT_TYPES
                and isinstance(row[2], int) and row[2] >= 0
                for row in rows
            )
            if valid:
                existing = [row for row in rows if row[0] == run_id]
                if existing:
                    if len(existing) != 1 or existing[0][1] != kind:
                        raise RuntimeError("Auto terminal event disagrees with run status")
                    return
            elif attempt == 4:
                raise RuntimeError("Auto terminal lookup returned inconsistent rows")
            if attempt < 4:
                await asyncio.sleep(0.05)
        await self.event(root_run_id, run_id, kind, self._terminal_payload(run))

    async def reconcile_cancelled_children(self, root_run_id: str) -> None:
        root = None
        for attempt in range(10):
            current = await self.get(root_run_id)
            if current and current["agent_id"] == "__auto__" and current["status"] == "cancelled":
                root = current
                break
            if attempt < 9:
                await asyncio.sleep(0.1)
        if root is None:
            raise RuntimeError("Cancelled Auto root did not become visible")
        known_ids = set((root.get("checkpoint") or {}).get("child_run_ids") or [])
        for attempt in range(5):
            tree = await self.tree(
                root_run_id, owner_name=root["owner_name"], role_name=root["role_name"]
            )
            children = {item["run_id"]: item for item in tree if item["depth"] == 1}
            for child_id in known_ids - children.keys():
                child = await self.get(child_id)
                if child and child["root_run_id"] == root_run_id and child["depth"] == 1:
                    children[child_id] = child
            if known_ids <= children.keys() and all(
                child["status"] in TERMINAL for child in children.values()
            ):
                for child in children.values():
                    await self.ensure_terminal_event(root_run_id, child)
                return
            if attempt < 4:
                await asyncio.sleep(0.1)
        raise RuntimeError("Cancelled Auto run has an incomplete child tree")

    async def _ensure_completed_terminals(self, root_run_id: str) -> dict[str, str]:
        root = await self.get(root_run_id)
        if not root or root["agent_id"] != "__auto__" or root["status"] != "completed":
            return {}
        checkpoint = root.get("checkpoint") or {}
        known_ids = set(checkpoint.get("child_run_ids") or [])
        if "child_run_ids" in checkpoint:
            journal = await self._synced_terminal_rows(root_run_id)
            terminal_kinds: dict[str, str] = {}
            for row in journal:
                if len(row) != 3 or not isinstance(row[2], int):
                    continue
                run_id, kind = row[:2]
                if run_id in terminal_kinds and terminal_kinds[run_id] != kind:
                    raise RuntimeError("Conflicting Auto terminal events")
                terminal_kinds[run_id] = kind
            expected_ids = known_ids | {root_run_id}
            if expected_ids <= terminal_kinds.keys():
                if terminal_kinds[root_run_id] != "agent_completed":
                    raise RuntimeError("Auto terminal event disagrees with run status")
                return {run_id: terminal_kinds[run_id] for run_id in expected_ids}
        for attempt in range(5):
            tree = await self.tree(
                root_run_id, owner_name=root["owner_name"], role_name=root["role_name"]
            )
            children = {item["run_id"]: item for item in tree if item["depth"] == 1}
            for child_id in known_ids - children.keys():
                child = await self.get(child_id)
                if child and child["root_run_id"] == root_run_id and child["depth"] == 1:
                    children[child_id] = child
            if known_ids <= children.keys() and all(
                child["status"] in TERMINAL for child in children.values()
            ):
                break
            if attempt < 4:
                await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Completed Auto run has an incomplete child tree")
        await self.reconcile_terminal_children(root_run_id, list(children.values()))

        final_id = str(uuid5(NAMESPACE_URL, f"nova:auto:final:{root_run_id}"))
        for attempt in range(10):
            messages = await assistant_repository.list_messages(
                root["thread_id"], user_name=root["owner_name"], synchronize=True
            )
            final = next(
                (
                    item for item in messages
                    if item["message_id"] == final_id and item["role"] == "assistant"
                ),
                None,
            )
            if final is not None:
                break
            if attempt < 9:
                await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Completed Auto run has no visible final message")
        await self.event(
            root_run_id, root_run_id, "agent_completed", {"answer": final["content"]}
        )
        return {
            **{
                child_id: TERMINAL_STATUS_EVENTS[child["status"]]
                for child_id, child in children.items()
            },
            root_run_id: "agent_completed",
        }

    async def events_after(
        self, root_run_id: str, cursor: str = "", *, limit: int = 100,
        ensure_complete: bool = False,
    ) -> list[dict]:
        after = int(cursor) if cursor else -1
        expected = await self._ensure_replay_terminals(root_run_id) if ensure_complete else {}
        return await self._events(root_run_id, after, limit, expected)

    async def events_page(
        self, root_run_id: str, after: int, *, limit: int = 100,
        ensure_complete: bool = False,
    ) -> list[dict]:
        expected = await self._ensure_replay_terminals(root_run_id) if ensure_complete else {}
        return await self._events(root_run_id, after, limit, expected)

    async def _ensure_replay_terminals(self, root_run_id: str) -> dict[str, str]:
        root = await self.get(root_run_id)
        if not root or root["agent_id"] != "__auto__":
            return {}
        if root["status"] == "completed":
            return await self._ensure_completed_terminals(root_run_id)
        kind = TERMINAL_STATUS_EVENTS.get(root["status"])
        if kind is None:
            return {}
        if root["status"] == "cancelled":
            await self.reconcile_cancelled_children(root_run_id)
        await self.ensure_terminal_event(root_run_id, root)
        return {root_run_id: kind}

    async def child_events_page(
        self,
        root_run_id: str,
        child_run_id: str,
        after: int,
        *,
        limit: int = 100,
    ) -> tuple[list[dict], int, bool]:
        selected: list[dict] = []
        cursor = after
        for _ in range(20):
            for attempt in range(10):
                batch = await self.events_page(
                    root_run_id, cursor, limit=100, ensure_complete=True
                )
                missing_start = (
                    cursor == -1
                    and len(batch) < 100
                    and any(
                        item["run_id"] == child_run_id
                        and item["type"] == "agent_completed"
                        for item in batch
                    )
                    and not any(
                        item["run_id"] == child_run_id
                        and item["type"] == "agent_started"
                        for item in batch
                    )
                )
                if not missing_start:
                    break
                if attempt < 9:
                    await asyncio.sleep(0.05)
            else:
                raise RuntimeError("Auto child timeline is missing its start event")
            for item in batch:
                cursor = int(item["event_id"])
                if item["run_id"] == child_run_id or (
                    item["type"] == "agent_message"
                    and item["payload"].get("recipient_run_id") == child_run_id
                ):
                    selected.append(item)
                    if len(selected) >= limit:
                        return selected, cursor, True
            if len(batch) < 100:
                return selected, cursor, False
        return selected, cursor, True

    @staticmethod
    async def _synced_terminal_rows(root_run_id: str) -> list[list]:
        async with db.system_conn() as conn, conn.cursor() as cursor:
            await cursor.execute("SYNC")
            await cursor.execute(
                "SELECT run_id, event_type, session_sequence "
                "FROM NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS "
                "WHERE root_run_id = %s AND event_type IN ('agent_completed', "
                "'agent_failed', 'agent_cancelled', 'agent_interrupted')",
                [root_run_id],
            )
            return [list(row) for row in await cursor.fetchall()]

    @staticmethod
    async def _synced_event_rows(root_run_id: str, after: int, limit: int) -> list[list]:
        async with db.system_conn() as conn, conn.cursor() as cursor:
            await cursor.execute("SYNC")
            await cursor.execute(
                "SELECT root_run_id, session_sequence, run_id, event_type, payload, created_at "
                "FROM NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS "
                "WHERE root_run_id = %s AND session_sequence > %s "
                "ORDER BY session_sequence LIMIT %s",
                [root_run_id, after, min(max(limit, 1), 100)],
            )
            return [list(row) for row in await cursor.fetchall()]

    @staticmethod
    async def _synced_completed_event_rows(
        root_run_id: str, after: int, limit: int
    ) -> tuple[list[list], list[list]]:
        async with db.system_conn() as conn, conn.cursor() as cursor:
            await cursor.execute("SYNC")
            await cursor.execute(
                "SELECT run_id, event_type, session_sequence "
                "FROM NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS "
                "WHERE root_run_id = %s AND event_type IN ('agent_completed', "
                "'agent_failed', 'agent_cancelled', 'agent_interrupted')",
                [root_run_id],
            )
            terminal_rows = [list(row) for row in await cursor.fetchall()]
            await cursor.execute(
                "SELECT root_run_id, session_sequence, run_id, event_type, payload, created_at "
                "FROM NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS "
                "WHERE root_run_id = %s AND session_sequence > %s "
                "ORDER BY session_sequence LIMIT %s",
                [root_run_id, after, min(max(limit, 1), 100)],
            )
            page_rows = [list(row) for row in await cursor.fetchall()]
            return page_rows, terminal_rows

    @staticmethod
    async def _events(
        root_run_id: str, after: int, limit: int,
        expected_terminals: dict[str, str] | None = None,
    ) -> list[dict]:
        expected = expected_terminals or {}
        attempts = 10 if expected else 5
        for attempt in range(attempts):
            if expected:
                rows, terminal_rows = await HarnessRepository._synced_completed_event_rows(
                    root_run_id, after, limit
                )
                terminals: dict[str, tuple[str, int]] = {}
                for row in terminal_rows:
                    if len(row) != 3 or not isinstance(row[2], int):
                        continue
                    previous = terminals.get(row[0])
                    if previous and previous[0] != row[1]:
                        raise RuntimeError("Conflicting Auto terminal events")
                    terminals[row[0]] = (row[1], row[2])
                if any(
                    run_id in terminals and terminals[run_id][0] != kind
                    for run_id, kind in expected.items()
                ):
                    raise RuntimeError("Auto terminal event disagrees with run status")
                complete = all(
                    terminals.get(run_id, (None,))[0] == kind
                    for run_id, kind in expected.items()
                )
                root_sequence = terminals.get(root_run_id, (None, -1))[1]
                ordered = all(
                    root_sequence > terminals[run_id][1]
                    for run_id in expected if run_id != root_run_id and run_id in terminals
                )
                visible_terminals = {
                    (row[2], row[3], row[1]) for row in rows if len(row) == 6
                }
                page_has_terminals = len(rows) >= min(max(limit, 1), 100) or all(
                    terminals[run_id][1] <= after
                    or (run_id, kind, terminals[run_id][1]) in visible_terminals
                    for run_id, kind in expected.items()
                    if run_id in terminals
                )
            else:
                rows = await HarnessRepository._synced_event_rows(root_run_id, after, limit)
                complete = ordered = page_has_terminals = True
            if all(
                len(row) == 6
                and row[0] == root_run_id
                and isinstance(row[1], int)
                and row[1] > after
                and row[3] in SESSION_EVENT_TYPES
                for row in rows
            ) and complete and ordered and page_has_terminals:
                return [
                    {
                        "event_id": row[1],
                        "run_id": row[2],
                        "type": row[3],
                        "payload": _json(row[4]) or {},
                        "created_at": row[5],
                    }
                    for row in rows
                ]
            if attempt < attempts - 1:
                await asyncio.sleep(0.05)
        raise RuntimeError("Auto event query did not return a complete ordered page")

    async def messages_for_tree(self, root_run_id: str) -> list[dict]:
        keys = (
            "message_id",
            "sender_run_id",
            "recipient_run_id",
            "message_type",
            "correlation_id",
            "reply_to",
            "content",
            "created_at",
            "consumed_at",
            "origin",
        )
        for attempt in range(5):
            result = await db.execute_system(
                "SELECT root_run_id, message_id, sender_run_id, recipient_run_id, "
                "message_type, correlation_id, reply_to, content, created_at, "
                "consumed_at, origin FROM NOVA_SYSTEM.CONFIG_AGENT_MESSAGES "
                "WHERE root_run_id = %s ORDER BY created_at, message_id LIMIT 500",
                [root_run_id],
            )
            rows = result["rows"]
            if all(
                len(row) == 11
                and row[0] == root_run_id
                and row[4] in {"message", "question", "answer", "finding", "final", "control"}
                and row[10] in {"agent", "user"}
                for row in rows
            ):
                return [dict(zip(keys, row[1:], strict=True)) for row in rows]
            if attempt < 4:
                await asyncio.sleep(0.05)
        raise RuntimeError("Auto message query returned inconsistent rows")

    async def cancel_tree(self, root_run_id: str) -> bool:
        for attempt in range(10):
            result = await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                "SET status = 'cancelled', updated_at = %s "
                "WHERE run_id = %s AND agent_id = '__auto__' AND depth = 0 "
                "AND status IN ('queued', 'running', 'waiting_for_agent', "
                "'waiting_for_message', 'waiting_for_auth')",
                [_now(), root_run_id],
            )
            if result.get("affected", 0) == 1:
                break
            root = await self.get(root_run_id)
            if not root or root["agent_id"] != "__auto__" or root["depth"] != 0:
                return False
            if root["status"] == "cancelled":
                break
            if root["status"] in TERMINAL:
                return False
            if attempt == 9:
                raise RuntimeError("Auto cancellation could not verify the root state")
            await asyncio.sleep(0.1)
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET status = 'cancelled', updated_at = %s "
            "WHERE root_run_id = %s AND depth = 1 "
            "AND status IN ('queued', 'running', 'waiting_for_message', 'waiting_for_auth')",
            [_now(), root_run_id],
        )
        return True

    async def cancel_child(self, root_run_id: str, child_run_id: str) -> bool:
        for attempt in range(10):
            result = await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                "SET status = 'cancelled', updated_at = %s "
                "WHERE run_id = %s AND root_run_id = %s AND depth = 1 "
                "AND status IN ('queued', 'running', 'waiting_for_message', 'waiting_for_auth')",
                [_now(), child_run_id, root_run_id],
            )
            if result.get("affected", 0) == 1:
                return True
            child = await self.get(child_run_id)
            if not child or child["root_run_id"] != root_run_id or child["depth"] != 1:
                return False
            if child["status"] == "cancelled":
                return True
            if child["status"] in TERMINAL:
                return False
            if attempt == 9:
                raise RuntimeError("Auto cancellation could not verify the child state")
            await asyncio.sleep(0.1)
        raise RuntimeError("Auto cancellation could not verify the child state")

    async def delete_thread(self, thread_id: str, *, owner_name: str) -> None:
        roots = await db.execute_system(
            "SELECT run_id FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "WHERE thread_id = %s AND owner_name = %s AND agent_id = '__auto__'",
            [thread_id, owner_name],
        )
        for row in roots["rows"]:
            root_id = str(row[0])
            client = session_store._redis
            if client is None:
                raise RuntimeError("Auto event coordinator is unavailable")
            async with _event_lock(client, root_id) as assert_owned:
                await client.set(f"nova:auto:event:deleted:{root_id}", "1", ex=86400)
                await assert_owned()
                await db.execute_system(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_MESSAGES WHERE root_run_id = %s",
                    [root_id],
                )
                await db.execute_system(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS WHERE root_run_id = %s",
                    [root_id],
                )
                await assert_owned()
                await client.delete(
                    f"nova:auto:event:sequence:{root_id}",
                    f"nova:auto:event:terminal:{root_id}",
                )


harness_repository = HarnessRepository()
