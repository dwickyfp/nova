from __future__ import annotations

import asyncio
import time

from app.common.audit import write_audit_log
from app.core.database import db

HISTORY_SORT_KEYS: dict[str, tuple[str, ...]] = {
    "CONFIG_ASSISTANT_MESSAGES": ("thread_id", "seq"),
    "CONFIG_ASSISTANT_THREADS": ("user_name", "updated_at", "thread_id"),
    "CONFIG_AGENT_MESSAGES": ("recipient_run_id", "created_at"),
    "CONFIG_AGENT_SESSION_EVENTS": ("root_run_id", "session_sequence"),
    "CONFIG_WORKSPACE_FILE_VERSIONS": ("entry_id", "version"),
    "AUDIT_LOG": ("event_time", "log_id"),
}


async def history_sort_key_plan() -> list[dict]:
    result = await db.execute_system(
        "SELECT TABLE_NAME, SORT_KEY FROM information_schema.tables_config "
        "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM'"
    )
    current = {
        row[0]: tuple(part.strip().strip("`") for part in (row[1] or "").split(","))
        for row in result["rows"]
    }
    return [
        {
            "table": table,
            "before": current[table],
            "after": keys,
            "sql": f"ALTER TABLE NOVA_SYSTEM.{table} ORDER BY ({', '.join(keys)})",
        }
        for table, keys in HISTORY_SORT_KEYS.items()
        if table in current and current[table] != keys
    ]


async def apply_history_sort_keys(*, actor: str, timeout_seconds: float = 600) -> list[str]:
    completed = []
    for change in await history_sort_key_plan():
        table = change["table"]
        audit = dict(
            event_type="schema_migration",
            user_name=actor,
            action="ALTER_SORT_KEY",
            object_type="TABLE",
            object_name=table,
            database_name="NOVA_SYSTEM",
            sql_text=change["sql"],
        )
        await write_audit_log(**audit, status="STARTED")
        await db.execute_system(change["sql"])
        deadline = time.monotonic() + timeout_seconds
        while True:
            result = await db.execute_system(
                "SHOW ALTER TABLE COLUMN FROM NOVA_SYSTEM "
                f"WHERE TableName = '{table}' ORDER BY CreateTime DESC LIMIT 1"
            )
            rows = result["rows"]
            if rows:
                job = dict(zip(result["columns"], rows[0], strict=True))
                state = job.get("State")
                if state == "CANCELLED":
                    await write_audit_log(
                        **audit, status="ERROR", error_message="Sort key migration cancelled"
                    )
                    raise RuntimeError(f"Sort key migration cancelled for {table}")
                if state == "FINISHED":
                    remaining = {item["table"] for item in await history_sort_key_plan()}
                    if table not in remaining:
                        await write_audit_log(**audit, status="SUCCESS")
                        completed.append(table)
                        break
            if time.monotonic() >= deadline:
                await write_audit_log(**audit, status="PENDING")
                raise TimeoutError(
                    f"Sort key migration still pending for {table}; inspect SHOW ALTER TABLE"
                )
            await asyncio.sleep(1)
    return completed
