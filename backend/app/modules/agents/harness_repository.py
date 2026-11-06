"""Per-thread admission and run lookups over the shared agent run table."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from typing import Any

from app.core.database import db
from app.core.redis import session_store
from app.modules.agents.run_coordination import coordination_lock
from app.modules.agents.run_journal import run_journal

#: Columns earlier collaboration runs added to ``CONFIG_AGENT_RUNS``. Rows written
#: with them stay readable, so the columns stay.
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

RUN_SELECT = (
    "run_id, owner_name, agent_id, thread_id, role_name, status, session_id, "
    "root_run_id, parent_run_id, depth, objective, payload, checkpoint, "
    "result_summary, prompt_tokens, completion_tokens, lease_owner, generation, "
    "security_version, error_class, started_at, updated_at"
)
RUN_KEYS = tuple(part.strip() for part in RUN_SELECT.split(","))
ADMISSION_LOCK_SECONDS = 600
ADMISSION_LOCK_RENEW_SECONDS = 30


class AdmissionUnavailable(RuntimeError):
    pass


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _run(row: list[Any]) -> dict[str, Any]:
    data = dict(zip(RUN_KEYS, row, strict=True))
    data["payload"] = _json(data["payload"]) or {}
    data["checkpoint"] = _json(data["checkpoint"]) or {}
    return data


class HarnessRepository:
    @asynccontextmanager
    async def admission_lock(self, thread_id: str, owner_name: str):
        """Serialize changes to one owner's thread (its Missions and runs)."""
        client = session_store._redis
        if client is None:
            raise AdmissionUnavailable("Thread admission is temporarily unavailable")
        scope = hashlib.sha256(f"{owner_name}\0{thread_id}".encode()).hexdigest()
        body_failed = False
        try:
            async with coordination_lock(
                client, f"nova:auto:event:lock:admission:{scope}",
                ttl_seconds=ADMISSION_LOCK_SECONDS, renew_seconds=ADMISSION_LOCK_RENEW_SECONDS,
            ) as assert_owned:
                async def assert_admitted() -> None:
                    try:
                        await assert_owned()
                    except Exception as exc:
                        raise AdmissionUnavailable("Thread admission lock was lost") from exc

                try:
                    yield assert_admitted
                except BaseException:
                    body_failed = True
                    raise
        except Exception as exc:
            if body_failed:
                raise
            raise AdmissionUnavailable("Thread admission lock is unavailable") from exc

    async def ensure_schema(self) -> None:
        await run_journal.ensure_schema()
        for column, kind in RUN_COLUMNS:
            await self._ensure_column("CONFIG_AGENT_RUNS", column, kind)

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

    async def get(self, run_id: str) -> dict[str, Any] | None:
        """A participant run of a collaboration tree, or None for any other run."""
        for attempt in range(20):
            rows = await self._run_rows(
                f"SELECT {RUN_SELECT} FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS WHERE run_id = %s",
                [run_id],
            )
            if rows:
                run = _run(rows[0])
                if run["run_id"] != run_id:
                    if attempt < 19:
                        await asyncio.sleep(0.1)
                        continue
                    raise RuntimeError("Agent run lookup returned a different run")
                return run if run["root_run_id"] is not None else None
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


harness_repository = HarnessRepository()
