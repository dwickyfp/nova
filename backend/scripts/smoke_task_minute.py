"""Live one-minute CREATE TASK smoke test against an isolated fixture.

Run only against a disposable StarRocks/Redis stack. The script creates uniquely
named users, a database, and a stream, then waits for a real scheduled execution.
It prints the observed task/run/result states without printing credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import redis.asyncio as aioredis

from app.common.nova_system import init_task_orchestration
from app.core.config import settings
from app.core.database import db
from app.modules.query.service import QueryService
from app.modules.task_orchestration.consumer import GraphRunConsumer
from app.modules.task_orchestration.execution import (
    DelegateExecutor,
    TaskSpec,
    native_attempt_name,
)
from app.modules.task_orchestration.reconciler import Reconciler
from app.modules.task_orchestration.repository import task_orchestration_repository as repository
from app.modules.task_orchestration.scheduler import SchedulerTick
from app.modules.task_orchestration.transport import RedisGraphRunTransport
from app.modules.task_orchestration.worker_service import WorkerService
from tests.integration._nova_system_ddl import AUDIT_LOG_DDL


class _FixtureRepository:
    """Limit the scheduler tick to this smoke task on a shared test engine."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    def __getattr__(self, name: str):
        return getattr(repository, name)

    async def list_tasks(self):
        task = await repository.get_task(self.task_id)
        return [task] if task is not None else []

    async def list_all_edges(self):
        return []


async def _count_rows(database: str) -> int:
    target = "generated" if os.environ.get("NOVA_SMOKE_BODY") == "ctas" else "sink"
    if target == "generated":
        exists = await db.execute_system(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = 'generated'",
            [database],
        )
        if not int(exists["rows"][0][0]):
            return 0
    result = await db.execute_system(f"SELECT COUNT(*) FROM {database}.{target}")
    return int(result["rows"][0][0])


async def main() -> None:
    suffix = uuid4().hex[:8]
    database = f"nova_minute_{suffix}"
    task_name = f"minute_{suffix}"
    graph_id = f"{database}.default.{task_name}"
    owner = f"nmt_owner_{suffix}"
    worker_user = f"nmt_worker_{suffix}"
    owner_role = f"nmt_owner_role_{suffix}"
    worker_role = f"nmt_worker_role_{suffix}"
    owner_password = secrets.token_hex(16)
    worker_password = secrets.token_hex(16)
    stream_key = f"nova:smoke:minute:{suffix}"
    schedule_name = os.environ.get("NOVA_SMOKE_SCHEDULE", "interval")
    body_name = os.environ.get("NOVA_SMOKE_BODY", "insert")
    target_runs = int(os.environ.get("NOVA_SMOKE_TARGET_RUNS", "1"))
    schedules = {
        "interval": "SCHEDULE EVERY(INTERVAL 1 MINUTE)",
        "interval_30s": "SCHEDULE EVERY(INTERVAL 30 SECOND)",
        "cron": "SCHEDULE = '* * * * * UTC'",
        "cron_using": "SCHEDULE = 'USING CRON * * * * * UTC'",
        "cron_step": "SCHEDULE = '*/1 * * * * UTC'",
    }
    assert schedule_name in schedules and body_name in {
        "insert", "insert_values", "overwrite", "ctas", "cache"
    }
    assert 1 <= target_runs <= 3
    print(json.dumps({"phase": "starting", "database": database,
                      "task": task_name}), flush=True)

    settings.STARROCKS_HOST = os.environ.get("NOVA_SMOKE_SR_HOST", "nova-starrocks-fe")
    settings.STARROCKS_FE_MYSQL_PORT = int(os.environ.get("NOVA_SMOKE_SR_PORT", "9030"))
    settings.STARROCKS_ROOT_USER = "root"
    settings.STARROCKS_ROOT_PASSWORD = os.environ.get("NOVA_SMOKE_SR_PASSWORD", "")
    existing_redis = urlsplit(settings.REDIS_URL)
    settings.REDIS_URL = os.environ.get("NOVA_SMOKE_REDIS_URL", "redis://nova-redis:6379/15")
    settings.NOVA_TIMEZONE = "UTC"
    settings.RANGER_ENABLED = False
    settings.TASK_STREAM_KEY = stream_key
    settings.TASK_STREAM_GROUP = f"nova-smoke-{suffix}"
    settings.WORKER_TASK_POLL_INTERVAL_SECONDS = 0.5
    settings.WORKER_TASK_POLL_TIMEOUT_SECONDS = 30.0

    await db.init_system_pool()
    print(json.dumps({"phase": "system_connected"}), flush=True)
    client = aioredis.from_url(
        settings.REDIS_URL,
        password=unquote(existing_redis.password) if existing_redis.password else None,
        decode_responses=True,
    )
    task_id: str | None = None
    completed = False
    try:
        await db.execute_system("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
        await db.execute_system(AUDIT_LOG_DDL)
        await init_task_orchestration()
        print(json.dumps({"phase": "metadata_ready"}), flush=True)
        await client.ping()
        print(json.dumps({"phase": "redis_connected"}), flush=True)
        await db.execute_system(f"CREATE DATABASE {database}")
        await db.execute_system(
            f"CREATE TABLE {database}.sink (event_id BIGINT NOT NULL, fired_at DATETIME) "
            "DUPLICATE KEY(event_id) DISTRIBUTED BY HASH(event_id) BUCKETS 1 "
            'PROPERTIES("replication_num"="1")'
        )
        await db.execute_system(
            f"CREATE TABLE {database}.source (event_id BIGINT NOT NULL) "
            "DUPLICATE KEY(event_id) DISTRIBUTED BY HASH(event_id) BUCKETS 1 "
            'PROPERTIES("replication_num"="1")'
        )
        await db.execute_system(f"INSERT INTO {database}.source VALUES (1)")
        await db.execute_system(
            f"CREATE USER '{owner}' IDENTIFIED BY '{owner_password}'"
        )
        await db.execute_system(
            f"CREATE USER '{worker_user}' IDENTIFIED BY '{worker_password}'"
        )
        await db.execute_system(f"CREATE ROLE {owner_role}")
        await db.execute_system(f"CREATE ROLE {worker_role}")
        await db.execute_system(f"GRANT {owner_role} TO USER '{owner}'")
        await db.execute_system(f"GRANT {worker_role} TO USER '{worker_user}'")
        await db.execute_system(f"GRANT INSERT ON {database}.sink TO '{owner}'")
        await db.execute_system(f"GRANT INSERT ON {database}.sink TO ROLE {owner_role}")
        await db.execute_system(f"GRANT ALL ON {database}.* TO ROLE {owner_role}")
        await db.execute_system(f"GRANT CREATE TABLE ON DATABASE {database} TO ROLE {owner_role}")
        await db.execute_system(
            f"GRANT IMPERSONATE ON USER '{owner}'@'%' TO ROLE {worker_role}"
        )
        await db.execute_system(
            f"GRANT IMPERSONATE ON USER '{owner}'@'%' TO '{worker_user}'"
        )
        print(json.dumps({"phase": "fixture_ready"}), flush=True)
        async with db.user_conn(worker_user, worker_password) as conn, conn.cursor() as cur:
                await cur.execute(f"SET ROLE {worker_role}")
                print(json.dumps({"phase": "worker_role_activated"}), flush=True)
                await cur.execute("SELECT CURRENT_ROLE()")
                current_role = await cur.fetchone()
                print(json.dumps({"phase": "worker_role_effective", "value": current_role[0]}),
                      flush=True)
                try:
                    await cur.execute(f"EXECUTE AS '{owner}'@'%' WITH NO REVERT")
                except Exception as exc:
                    # This statement contains only the generated account name.
                    print(json.dumps({"phase": "execute_as_error", "detail": str(exc)}),
                          flush=True)
                    await cur.execute(f"EXECUTE AS {owner} WITH NO REVERT")
                    print(json.dumps({"phase": "bare_execute_as_accepted"}), flush=True)
                print(json.dumps({"phase": "execute_as_accepted"}), flush=True)
                await cur.execute("SELECT CURRENT_USER()")
                identity = await cur.fetchone()
                print(json.dumps({"phase": "effective_user", "value": identity[0]}), flush=True)
                try:
                    await cur.execute(f"SET ROLE {owner_role}")
                except Exception as exc:
                    print(json.dumps({"phase": "owner_role_error", "detail": str(exc)}),
                          flush=True)
                    raise

        bodies = {
            "insert": f"INSERT INTO {database}.sink SELECT 1, NOW()",
            "insert_values": f"INSERT INTO {database}.sink VALUES (1, NOW())",
            "overwrite": f"INSERT OVERWRITE {database}.sink SELECT 2, NOW()",
            "ctas": (
                f"CREATE TABLE {database}.generated "
                'PROPERTIES("replication_num"="1") AS '
                f"SELECT event_id, NOW() AS fired_at FROM {database}.source"
            ),
            "cache": f"CACHE SELECT event_id FROM {database}.source",
        }
        statement = (
            f"CREATE TASK {database}.default.{task_name} "
            f"{schedules[schedule_name]} AS {bodies[body_name]}"
        )
        try:
            created = await QueryService().execute(
                statement, username=owner, encrypted_password="", database=database,
                schema="default", role=owner_role,
            )
        except Exception as exc:
            print(json.dumps({"phase": "create_task_error", "detail": str(exc)}),
                  flush=True)
            raise
        task_id = str(created.rows[0][0])
        task = await repository.get_task(task_id)
        assert task is not None
        assert task["schedule_kind"] == (
            "cron" if schedule_name.startswith("cron") else "interval"
        )
        print(json.dumps({"phase": "created", "graph_id": graph_id,
                          "schedule_expr": task["schedule_expr"],
                          "schedule": schedule_name, "body": body_name}), flush=True)

        consumer = GraphRunConsumer(client)
        await consumer.ensure_group()
        scheduler = SchedulerTick(_FixtureRepository(task_id), RedisGraphRunTransport(client))
        executor = DelegateExecutor(
            None, impersonation_user=worker_user, impersonation_password=worker_password,
            impersonation_role=worker_role,
            poll_interval=0.5, poll_timeout=30,
        )
        try:
            await executor.evaluate_when(
                "1 = 1",
                spec=TaskSpec(task_name, "SELECT 1", database, owner_role),
                owner=owner,
            )
            print(json.dumps({"phase": "executor_preflight_ok"}), flush=True)
        except Exception as exc:
            print(json.dumps({"phase": "executor_preflight_error", "detail": str(exc)}), flush=True)
            raise
        worker = WorkerService(repository, executor, consumer, Reconciler(repository))
        started = time.monotonic()
        deadline = started + max(100, (target_runs + 1) * 65)
        while time.monotonic() < deadline:
            plan = await scheduler.tick()
            if plan.due:
                print(json.dumps({"phase": "due", "count": len(plan.due)}), flush=True)
            await worker.run_once(block_ms=100)
            runs = await repository.list_graph_runs(graph_id)
            rows = await _count_rows(database) if body_name != "cache" or runs else 0
            if any(run["state"] == "failed" for run in runs):
                print(
                    json.dumps({"phase": "failed", "graph_states": [run["state"] for run in runs]}),
                    flush=True,
                )
                raise RuntimeError("smoke graph failed")
            minimum_elapsed = {
                "interval": 61,
                "interval_30s": 31,
            }.get(schedule_name, 0)
            if (
                len(runs) >= target_runs
                and (rows >= target_runs or body_name == "cache" or body_name == "overwrite")
                and all(run["state"] == "success" for run in runs)
                and time.monotonic() - started >= minimum_elapsed
            ):
                completed = True
                node_runs = [
                    node
                    for run in runs
                    for node in await repository.list_node_runs(str(run["id"]))
                ]
                assert len(node_runs) >= target_runs
                assert all(node["state"] == "success" for node in node_runs)
                assert all(node.get("starrocks_query_id") for node in node_runs)
                native_names = [native_attempt_name(str(node["id"])) for node in node_runs]
                placeholders = ", ".join("%s" for _ in native_names)
                native_templates = await db.execute_system(
                    "SELECT TASK_NAME FROM information_schema.tasks "
                    f"WHERE TASK_NAME IN ({placeholders})",
                    native_names,
                )
                assert not native_templates["rows"], "completed native templates remain"
                print(json.dumps({
                    "phase": "verified",
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                    "table_rows": rows,
                    "graph_states": [run["state"] for run in runs],
                    "node_states": [run["state"] for run in node_runs],
                    "native_query_ids": [
                        bool(run.get("starrocks_query_id")) for run in node_runs
                    ],
                    "native_templates_remaining": len(native_templates["rows"]),
                }), flush=True)
                break
            await asyncio.sleep(1)

        if not completed:
            runs = await repository.list_graph_runs(graph_id)
            print(json.dumps({"phase": "failed", "table_rows": await _count_rows(database),
                              "graph_states": [run["state"] for run in runs]}), flush=True)
            raise RuntimeError("one-minute task did not complete")
    finally:
        if completed and task_id is not None:
            for run in await repository.list_graph_runs(graph_id):
                for node in await repository.list_task_runs(str(run["id"])):
                    await repository.delete_task_run(str(node["id"]))
                await repository.delete_graph_run(str(run["id"]))
            await repository.delete_task(task_id)
            await db.execute_system(f"DROP DATABASE {database}")
            await db.execute_system(f"DROP USER '{worker_user}'")
            await db.execute_system(f"DROP USER '{owner}'")
            await db.execute_system(f"DROP ROLE {worker_role}")
            await db.execute_system(f"DROP ROLE {owner_role}")
            await client.delete(stream_key)
        await client.aclose()
        await db.close_system_pool()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        # Engine errors can echo CREATE USER statements with passwords.
        print(json.dumps({"phase": "error", "type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
