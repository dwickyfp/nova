"""Run task acceptance cases on an explicitly selected disposable StarRocks stack.

Creates a unique database, task owners, and Redis stream. Keeps sample data,
task definitions and their owners; removes the temporary worker and transport.
No persistent scheduler is started. Results distinguish real-time schedules
from controlled-clock ticks that still execute real SQL through the worker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import HTTPException

from app.common.nova_system import init_task_orchestration
from app.common.sql_guard import redact_sql_credentials
from app.core.config import settings
from app.core.database import db
from app.core.exceptions import ForbiddenSQLError
from app.core.redis import session_store
from app.core.security import encrypt_password
from app.modules.query.service import QueryService
from app.modules.task_orchestration import router
from app.modules.task_orchestration.consumer import GraphRunConsumer
from app.modules.task_orchestration.execution import DelegateExecutor
from app.modules.task_orchestration.reconciler import Reconciler
from app.modules.task_orchestration.repository import task_orchestration_repository as repo
from app.modules.task_orchestration.scheduler import SchedulerTick
from app.modules.task_orchestration.transport import RedisGraphRunTransport
from app.modules.task_orchestration.worker_service import WorkerService
from tests.integration._nova_system_ddl import AUDIT_LOG_DDL


class ScopedSchedule:
    def __init__(self, task_id: str, anchor: datetime | None = None) -> None:
        self.task_id = task_id
        self.anchor = anchor

    def __getattr__(self, name: str) -> Any:
        return getattr(repo, name)

    async def list_tasks(self) -> list[dict[str, Any]]:
        task = await repo.get_task(self.task_id)
        assert task is not None
        if self.anchor is not None:
            task = {**task, "created_at": self.anchor}
        return [task]

    async def list_all_edges(self) -> list[dict[str, Any]]:
        return []


class Matrix:
    def __init__(self, output: Path) -> None:
        self.suffix = uuid4().hex[:8]
        self.database = f"task_qa_{self.suffix}"
        self.owner = f"tqo_{self.suffix}"
        self.worker_user = f"tqw_{self.suffix}"
        self.schedule_user = f"tqs_{self.suffix}"
        self.limited = f"tql_{self.suffix}"
        self.role = f"tqr_{self.suffix}"
        self.limited_role = f"tqlr_{self.suffix}"
        self.worker_role = f"tqwr_{self.suffix}"
        self.output = output
        self.tasks: dict[str, dict[str, Any]] = {}
        self.results: list[dict[str, Any]] = []
        self.statements: list[str] = []
        self.secrets: list[str] = []
        self.client: Any = None
        self.worker: WorkerService
        self.transport: RedisGraphRunTransport
        self.sessions: dict[str, str] = {}
        self.encrypted_passwords: dict[str, str] = {}

    def user(self, limited: bool = False) -> dict[str, Any]:
        name = self.limited if limited else self.owner
        role = self.limited_role if limited else self.role
        return {
            "username": name,
            "roles": [role],
            "active_role": role,
            "session_id": self.sessions[name],
            "encrypted_password": self.encrypted_passwords[name],
        }

    def save(self) -> None:
        self.output.mkdir(parents=True, exist_ok=True)
        self.output.joinpath("results.json").write_text(
            json.dumps(
                {
                    "database": self.database,
                    "engine_port": settings.STARROCKS_FE_MYSQL_PORT,
                    "engine_version": getattr(self, "version", None),
                    "created_at": datetime.now(UTC).isoformat(),
                    "cases": self.results,
                    "tasks": list(self.tasks.values()),
                    "notes": (
                        "Dedicated test stack. Controlled clock cases do not prove "
                        "a day/week of uptime."
                    ),
                },
                indent=2,
                default=str,
            )
        )
        self.output.joinpath("samples.sql").write_text(
            "-- Acceptance fixtures; run CREATE TASK through Nova, not directly in StarRocks.\n"
            + "\n\n".join(statement + ";" for statement in self.statements)
            + "\n"
        )

    def record(self, name: str, ok: bool, **evidence: Any) -> None:
        self.results.append({"case": name, "passed": ok, **evidence})
        self.save()
        print(json.dumps({"case": name, "passed": ok}), flush=True)

    async def sql(self, sql: str, *, sample: bool = True) -> dict[str, Any]:
        if sample:
            self.statements.append(sql)
        return await db.execute_system(sql)

    async def table(self, name: str, *, duplicate: bool = False) -> None:
        await self.sql(
            f"CREATE TABLE {self.database}.{name} "
            "(id INT NOT NULL, amount BIGINT, label VARCHAR(200)) "
            f"{'DUPLICATE' if duplicate else 'PRIMARY'} KEY(id) "
            'DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1")'
        )

    async def task(self, name: str, body: str, clauses: str = "", *, limited: bool = False) -> str:
        statement = f"CREATE TASK {self.database}.default.{name} {clauses} AS {body}"
        self.statements.append(statement)
        result = await QueryService().execute(
            statement,
            username=self.limited if limited else self.owner,
            encrypted_password="",
            database=self.database,
            schema="default",
            role=self.limited_role if limited else self.role,
        )
        if result.error:
            raise RuntimeError(result.error)
        task_id = str(result.rows[0][0])
        self.tasks[name] = {"id": task_id, "name": name, "sql": body, "ddl": statement}
        return task_id

    async def run(self, root: str, *, limited: bool = False) -> dict[str, Any]:
        graph_id = f"{self.database}.default.{root}"
        run = await router.run_graph(
            graph_id,
            user=self.user(limited),
        )
        return await self.drain(run.id)

    async def drain(self, run_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            await self.worker.run_once(block_ms=100)
            run = await repo.get_graph_run(run_id)
            if run and run["state"] in {"success", "failed", "cancelled"}:
                nodes = await repo.list_node_runs(run_id)
                names = {task["id"]: name for name, task in self.tasks.items()}
                return {
                    "run_id": run_id,
                    "state": run["state"],
                    "nodes": [
                        {
                            "name": names.get(node["task_id"], node["task_id"]),
                            "state": node["state"],
                            "error": node.get("error_message"),
                            "query_id": node.get("starrocks_query_id"),
                            "started_at": node.get("started_at"),
                            "finished_at": node.get("finished_at"),
                        }
                        for node in nodes
                    ],
                }
            await asyncio.sleep(0.25)
        raise TimeoutError(f"run {run_id} did not settle")

    async def rows(self, table: str) -> list[list[Any]]:
        result = await self.sql(f"SELECT * FROM {self.database}.{table} ORDER BY 1", sample=False)
        return [list(row) for row in result["rows"]]

    async def check(
        self,
        name: str,
        root: str,
        table: str,
        expected: list[list[Any]],
        *,
        states: dict[str, str] | None = None,
        limited: bool = False,
    ) -> dict[str, Any]:
        run = await self.run(root, limited=limited)
        actual = await self.rows(table)
        expected_states = states or {root: "success"}
        observed = {node["name"]: node["state"] for node in run["nodes"]}
        expected_graph = "failed" if "failed" in expected_states.values() else "success"
        ok = actual == expected and observed == expected_states and run["state"] == expected_graph
        self.record(
            name,
            ok,
            expected_rows=expected,
            actual_rows=actual,
            expected_states=expected_states,
            **run,
        )
        return run

    async def setup(self) -> None:
        await db.init_system_pool()
        self.version = (await self.sql("SELECT current_version()", sample=False))["rows"][0][0]
        await self.sql("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM", sample=False)
        await self.sql(AUDIT_LOG_DDL, sample=False)
        await init_task_orchestration()
        await self.sql(f"CREATE DATABASE {self.database}")
        for account in (self.owner, self.worker_user, self.limited, self.schedule_user):
            password = secrets.token_hex(20)
            self.secrets.append(password)
            self.encrypted_passwords[account] = encrypt_password(password)
            await self.sql(f"CREATE USER '{account}' IDENTIFIED BY '{password}'", sample=False)
        await self.sql(f"CREATE ROLE {self.role}", sample=False)
        await self.sql(f"CREATE ROLE {self.limited_role}", sample=False)
        await self.sql(f"GRANT {self.limited_role} TO USER '{self.limited}'", sample=False)
        await self.sql(f"CREATE ROLE {self.worker_role}", sample=False)
        await self.sql(f"GRANT {self.role} TO USER '{self.owner}'", sample=False)
        await self.sql(f"GRANT {self.role} TO USER '{self.schedule_user}'", sample=False)
        await repo.bind_role_execution_user(self.role, self.schedule_user, self.owner)
        await self.sql(f"GRANT {self.worker_role} TO USER '{self.worker_user}'", sample=False)
        await self.sql(f"GRANT ALL ON {self.database}.* TO ROLE {self.role}", sample=False)
        await self.sql(
            f"GRANT CREATE TABLE ON DATABASE {self.database} TO ROLE {self.role}", sample=False
        )
        for account in (self.owner, self.limited, self.schedule_user):
            await self.sql(
                f"GRANT IMPERSONATE ON USER '{account}'@'%' TO ROLE {self.worker_role}",
                sample=False,
            )
        self.client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await session_store.init()
        for name, role in [(self.owner, self.role), (self.limited, self.limited_role)]:
            self.sessions[name] = await session_store.create(
                name, self.encrypted_passwords[name], [role], active_role=role
            )
        await self.client.ping()
        consumer = GraphRunConsumer(self.client)
        await consumer.ensure_group()
        self.transport = RedisGraphRunTransport(self.client)
        self.worker = WorkerService(
            repo,
            DelegateExecutor(
                None,
                impersonation_user=self.worker_user,
                impersonation_password=self.secrets[1],
                impersonation_role=self.worker_role,
                poll_interval=0.25,
                poll_timeout=60,
            ),
            consumer,
            Reconciler(repo),
        )
        await self.table("source")
        await self.sql(
            f"INSERT INTO {self.database}.source VALUES (1,10,'alpha'),(2,20,'beta'),(3,30,'gamma')"
        )
        print(
            json.dumps({"phase": "ready", "database": self.database, "version": self.version}),
            flush=True,
        )

    async def bodies(self) -> None:
        d = self.database
        cases = [
            ("values", "VALUES (1,42,'standalone')", [[1, 42, "standalone"]]),
            (
                "copy",
                f"SELECT * FROM {d}.source",
                [[1, 10, "alpha"], [2, 20, "beta"], [3, 30, "gamma"]],
            ),
            (
                "filter",
                f"SELECT id, amount * 2, UPPER(label) FROM {d}.source WHERE amount >= 20",
                [[2, 40, "BETA"], [3, 60, "GAMMA"]],
            ),
            ("empty", f"SELECT * FROM {d}.source WHERE amount > 100", []),
            (
                "unicode_null",
                "VALUES (1,NULL,'café_日本'),(2,-5,'negative')",
                [[1, None, "café_日本"], [2, -5, "negative"]],
            ),
            ("aggregate", f"SELECT 1, SUM(amount), 'total' FROM {d}.source", [[1, 60, "total"]]),
            (
                "join",
                f"SELECT a.id, a.amount+b.amount, a.label FROM {d}.source a "
                f"JOIN {d}.source b ON a.id=b.id WHERE a.id=2",
                [[2, 40, "beta"]],
            ),
        ]
        for name, select, expected in cases:
            name = "sample_" + name
            await self.table(name)
            await self.task(name, f"INSERT INTO {d}.{name} {select}")
            await self.check(f"standalone_{name}", name, name, expected)

        await self.table("append", duplicate=True)
        await self.task("append", f"INSERT INTO {d}.append VALUES (1,1,'event')")
        first = await self.check("append_first", "append", "append", [[1, 1, "event"]])
        stored = await repo.get_graph_run(first["run_id"])
        await self.transport.publish_graph_run(stored, [self.tasks["append"]["id"]])
        await self.drain(first["run_id"])
        self.record(
            "redelivery_does_not_duplicate",
            await self.rows("append") == [[1, 1, "event"]],
            actual_rows=await self.rows("append"),
            run_id=first["run_id"],
        )
        await self.check(
            "append_second_new_run", "append", "append", [[1, 1, "event"], [1, 1, "event"]]
        )

        await self.table("upsert")
        await self.sql(f"INSERT INTO {d}.upsert VALUES (1,1,'old'),(9,90,'keep')")
        await self.task("upsert", f"INSERT INTO {d}.upsert SELECT * FROM {d}.source")
        expected = [[1, 10, "alpha"], [2, 20, "beta"], [3, 30, "gamma"], [9, 90, "keep"]]
        await self.check("upsert_updates_inserts_preserves_unmatched", "upsert", "upsert", expected)
        await self.check("upsert_repeat_is_idempotent", "upsert", "upsert", expected)
        await self.task("partial", f"INSERT INTO {d}.upsert (id, amount) SELECT 1, 99")
        await self.check(
            "partial_update_preserves_label", "partial", "upsert", [[1, 99, "alpha"], *expected[1:]]
        )

        await self.table("snapshot")
        await self.sql(f"INSERT INTO {d}.snapshot VALUES (9,90,'obsolete')")
        await self.task(
            "snapshot", f"INSERT OVERWRITE {d}.snapshot SELECT * FROM {d}.source WHERE id=1"
        )
        await self.check("overwrite_removes_old_rows", "snapshot", "snapshot", [[1, 10, "alpha"]])
        await self.check("overwrite_repeat", "snapshot", "snapshot", [[1, 10, "alpha"]])
        await self.task(
            "ctas",
            f'CREATE TABLE {d}.generated PROPERTIES("replication_num"="1") '
            f"AS SELECT * FROM {d}.source",
        )
        await self.check(
            "ctas", "ctas", "generated", [[1, 10, "alpha"], [2, 20, "beta"], [3, 30, "gamma"]]
        )

    async def graphs(self) -> None:
        d = self.database
        for name in (
            "chain_a",
            "chain_b",
            "chain_c",
            "diamond_a",
            "diamond_b",
            "diamond_c",
            "diamond_d",
            "condition",
            "failure",
            "finalized",
        ):
            await self.table(name)
        await self.task("chain_a", f"INSERT INTO {d}.chain_a SELECT * FROM {d}.source")
        await self.task(
            "chain_b",
            f"INSERT INTO {d}.chain_b SELECT id,amount+1,label FROM {d}.chain_a",
            "AFTER chain_a",
        )
        await self.task(
            "chain_c",
            f"INSERT INTO {d}.chain_c SELECT id,amount*2,label FROM {d}.chain_b",
            "AFTER chain_b",
        )
        await self.check(
            "chain_three_levels",
            "chain_a",
            "chain_c",
            [[1, 22, "alpha"], [2, 42, "beta"], [3, 62, "gamma"]],
            states={n: "success" for n in ("chain_a", "chain_b", "chain_c")},
        )
        await self.task("diamond_a", f"INSERT INTO {d}.diamond_a SELECT * FROM {d}.source")
        await self.task(
            "diamond_b",
            f"INSERT INTO {d}.diamond_b SELECT * FROM {d}.diamond_a WHERE id<=2",
            "AFTER diamond_a",
        )
        await self.task(
            "diamond_c",
            f"INSERT INTO {d}.diamond_c SELECT * FROM {d}.diamond_a WHERE id=3",
            "AFTER diamond_a",
        )
        await self.task(
            "diamond_d",
            f"INSERT INTO {d}.diamond_d SELECT * FROM {d}.diamond_b "
            f"UNION ALL SELECT * FROM {d}.diamond_c",
            "AFTER diamond_b, diamond_c",
        )
        await self.check(
            "diamond_fanout_fanin",
            "diamond_a",
            "diamond_d",
            [[1, 10, "alpha"], [2, 20, "beta"], [3, 30, "gamma"]],
            states={n: "success" for n in ("diamond_a", "diamond_b", "diamond_c", "diamond_d")},
        )
        await self.task("when_true", f"INSERT INTO {d}.condition VALUES (1,1,'true')", "WHEN 1=1")
        await self.check("when_true", "when_true", "condition", [[1, 1, "true"]])
        await self.task("when_false", f"INSERT INTO {d}.condition VALUES (2,2,'false')", "WHEN 1=0")
        await self.task(
            "after_false", f"INSERT INTO {d}.condition VALUES (3,3,'child')", "AFTER when_false"
        )
        await self.check(
            "when_false_skips_child",
            "when_false",
            "condition",
            [[1, 1, "true"]],
            states={"when_false": "skipped", "after_false": "skipped"},
        )
        await self.task(
            "bad_when", f"INSERT INTO {d}.condition VALUES (4,4,'invalid')", "WHEN missing_column=1"
        )
        await self.check(
            "invalid_when_records_error",
            "bad_when",
            "condition",
            [[1, 1, "true"]],
            states={"bad_when": "failed"},
        )
        await self.task("bad_parent", f"INSERT INTO {d}.failure SELECT * FROM {d}.missing_source")
        await self.task(
            "blocked_child",
            f"INSERT INTO {d}.failure VALUES (1,1,'must not run')",
            "AFTER bad_parent",
        )
        await self.task(
            "failure_finalizer",
            f"INSERT INTO {d}.finalized VALUES (1,1,'failure finalized')",
            "FINALIZE bad_parent",
        )
        run = await self.check(
            "failed_parent_skips_child_and_finalizer",
            "bad_parent",
            "finalized",
            [],
            states={
                "bad_parent": "failed",
                "blocked_child": "skipped",
                "failure_finalizer": "skipped",
            },
        )
        self.record(
            "failed_graph_has_readable_error",
            any(n["error"] for n in run["nodes"] if n["state"] == "failed"),
            errors=[n["error"] for n in run["nodes"]],
        )
        self.record(
            "failed_child_wrote_nothing",
            await self.rows("failure") == [],
            actual_rows=await self.rows("failure"),
        )
        await self.task("success_parent", f"INSERT INTO {d}.finalized VALUES (2,2,'parent')")
        await self.task(
            "success_finalizer",
            f"INSERT INTO {d}.finalized VALUES (3,3,'success finalized')",
            "FINALIZE success_parent",
        )
        await self.check(
            "success_finalizer",
            "success_parent",
            "finalized",
            [[2, 2, "parent"], [3, 3, "success finalized"]],
            states={"success_parent": "success", "success_finalizer": "success"},
        )
        await self.task("denied", f"INSERT INTO {d}.failure VALUES (5,5,'forbidden')", limited=True)
        await self.check(
            "owner_privileges_enforced",
            "denied",
            "failure",
            [],
            states={"denied": "failed"},
            limited=True,
        )

    async def schedules(self) -> None:
        d = self.database
        variants = [
            (
                "minute",
                "SCHEDULE = '* * * * * UTC'",
                "2026-09-27T00:00:01+00:00",
                "2026-09-27T00:01:00+00:00",
            ),
            (
                "ten_minutes",
                "SCHEDULE = '*/10 * * * * UTC'",
                "2026-09-27T00:01:00+00:00",
                "2026-09-27T00:10:00+00:00",
            ),
            (
                "daily",
                "SCHEDULE = '0 2 * * * Asia/Jakarta'",
                "2026-09-27T18:59:00+00:00",
                "2026-09-27T19:00:00+00:00",
            ),
            (
                "weekly",
                "SCHEDULE = '0 3 * * 1 Asia/Jakarta'",
                "2026-09-27T19:59:00+00:00",
                "2026-09-27T20:00:00+00:00",
            ),
            (
                "interval_minute",
                "SCHEDULE EVERY(INTERVAL 1 MINUTE)",
                "2026-09-27T00:00:00+00:00",
                "2026-09-27T00:01:00+00:00",
            ),
            (
                "interval_ten_minutes",
                "SCHEDULE EVERY(INTERVAL 10 MINUTE)",
                "2026-09-27T00:00:00+00:00",
                "2026-09-27T00:10:00+00:00",
            ),
        ]
        for name, clause, anchor_iso, due_iso in variants:
            name = "sched_" + name
            await self.table(name, duplicate=True)
            task_id = await self.task(
                name, f"INSERT INTO {d}.{name} VALUES (1,1,'scheduled')", clause
            )
            due = datetime.fromisoformat(due_iso)
            tick = SchedulerTick(
                ScopedSchedule(task_id, datetime.fromisoformat(anchor_iso)), self.transport
            )
            before = await tick.tick(due - timedelta(seconds=1))
            plan = await tick.tick(due)
            graph_id = f"{d}.default.{name}"
            runs = await repo.list_graph_runs(graph_id)
            run = await self.drain(str(runs[0]["id"])) if runs else {}
            await tick.tick(due)
            actual = await self.rows(name)
            ok = (
                not before.due
                and len(plan.due) == 1
                and len(await repo.list_graph_runs(graph_id)) == 1
                and actual == [[1, 1, "scheduled"]]
                and run.get("state") == "success"
            )
            self.record(
                name,
                ok,
                clock="controlled",
                anchor=anchor_iso,
                due=due_iso,
                schedule=clause,
                actual_rows=actual,
                **run,
            )

        await self.table("wall_minute", duplicate=True)
        task_id = await self.task(
            "wall_minute",
            f"INSERT INTO {d}.wall_minute VALUES (1,1,'wall clock')",
            "SCHEDULE = '* * * * * UTC'",
        )
        tick = SchedulerTick(ScopedSchedule(task_id), self.transport)
        started = time.monotonic()
        while time.monotonic() - started < 100:
            plan = await tick.tick()
            if plan.due:
                runs = await repo.list_graph_runs(f"{d}.default.wall_minute")
                if runs:
                    run = await self.drain(str(runs[0]["id"]))
                    actual = await self.rows("wall_minute")
                    self.record(
                        "cron_minute_wall_clock",
                        run["state"] == "success" and actual == [[1, 1, "wall clock"]],
                        clock="real",
                        elapsed_seconds=round(time.monotonic() - started, 2),
                        actual_rows=actual,
                        **run,
                    )
                    break
            await asyncio.sleep(1)
        else:
            self.record(
                "cron_minute_wall_clock", False, clock="real", reason="No run within 100 seconds"
            )

    async def rejections(self) -> None:
        d = self.database
        cases = {
            "merge_into": (
                f"AS MERGE INTO {d}.upsert t USING {d}.source s ON t.id=s.id "
                "WHEN MATCHED THEN UPDATE SET t.amount=s.amount "
                "WHEN NOT MATCHED THEN INSERT (id,amount,label) VALUES(s.id,s.amount,s.label)"
            ),
            "update_body": f"AS UPDATE {d}.upsert SET amount=1 WHERE id=1",
            "delete_body": f"AS DELETE FROM {d}.upsert WHERE id=1",
            "invalid_cron": "SCHEDULE = '99 * * * * UTC' "
            f"AS INSERT INTO {d}.condition VALUES(10,10,'invalid')",
            "invalid_zone": "SCHEDULE = '* * * * * Mars/Olympus' "
            f"AS INSERT INTO {d}.condition VALUES(10,10,'invalid')",
            "self_cycle": "AFTER reject_self_cycle "
            f"AS INSERT INTO {d}.condition VALUES(10,10,'invalid')",
            "multi_statement": f"AS INSERT INTO {d}.condition VALUES(10,10,'invalid'); "
            f"DELETE FROM {d}.condition",
        }
        for name, sql in cases.items():
            statement = f"CREATE TASK {d}.default.reject_{name} {sql}"
            try:
                result = await QueryService().execute(
                    statement,
                    username=self.owner,
                    encrypted_password="",
                    database=d,
                    schema="default",
                    role=self.role,
                )
                error = result.error
            except ForbiddenSQLError as exc:
                error = str(exc)
            found = [
                t
                for t in await repo.list_tasks()
                if t["database_name"] == d and t["name"] == "reject_" + name
            ]
            self.record(
                "reject_" + name,
                bool(error) and not found,
                sql=statement,
                error=error,
                persisted=len(found),
            )

    async def overlap(self) -> None:
        d = self.database
        for policy in ("skip", "queue", "allow"):
            name = "overlap_" + policy
            await self.table(name, duplicate=True)
            await self.task(
                name, f"INSERT INTO {d}.{name} VALUES(1,1,'{policy}')", f"OVERLAP_POLICY = {policy}"
            )
            graph_id = f"{d}.default.{name}"
            user = self.user()
            first = await router.run_graph(graph_id, user=user)
            second = None
            rejected = False
            try:
                second = await router.run_graph(graph_id, user=user)
            except HTTPException as exc:
                rejected = exc.status_code == 409
            await self.drain(first.id)
            if second:
                await self.drain(second.id)
            rows = await self.rows(name)
            expected_count = 1 if policy == "skip" else 2
            self.record(
                name,
                len(rows) == expected_count and rejected == (policy == "skip"),
                expected_count=expected_count,
                actual_rows=rows,
                second_rejected=rejected,
            )

    async def close(self) -> None:
        for session in self.sessions.values():
            await session_store.delete(session)
        await session_store.close()
        if self.client:
            await self.client.delete(settings.TASK_STREAM_KEY)
            await self.client.aclose()
        for account in (self.worker_user,):
            await self.sql(f"DROP USER IF EXISTS '{account}'", sample=False)
        for role in (self.worker_role,):
            await self.sql(f"DROP ROLE IF EXISTS {role}", sample=False)
        await db.close_system_pool()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings.STARROCKS_HOST = "127.0.0.1"
    settings.STARROCKS_FE_MYSQL_PORT = args.port
    settings.STARROCKS_ROOT_USER = "root"
    settings.STARROCKS_ROOT_PASSWORD = os.environ.get("NOVA_TEST_SR_PASSWORD", "")
    settings.REDIS_URL = args.redis_url
    settings.RANGER_ENABLED = False
    settings.NOVA_TIMEZONE = "UTC"
    matrix = Matrix(args.output)
    settings.TASK_STREAM_KEY = f"nova:acceptance:{matrix.suffix}"
    settings.TASK_STREAM_GROUP = f"acceptance-{matrix.suffix}"
    try:
        await matrix.setup()
        for phase in (
            matrix.bodies,
            matrix.graphs,
            matrix.schedules,
            matrix.rejections,
            matrix.overlap,
        ):
            print(json.dumps({"phase": phase.__name__}), flush=True)
            await phase()
    except Exception as exc:
        message = redact_sql_credentials(str(exc))
        for secret in matrix.secrets:
            message = message.replace(secret, "***")
        matrix.record("suite_execution", False, error=message, error_type=type(exc).__name__)
        raise SystemExit(1) from None
    finally:
        matrix.save()
        await matrix.close()
    print(
        json.dumps(
            {
                "phase": "complete",
                "database": matrix.database,
                "passed": sum(r["passed"] for r in matrix.results),
                "total": len(matrix.results),
            }
        ),
        flush=True,
    )
    if not all(result["passed"] for result in matrix.results):
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(main())
