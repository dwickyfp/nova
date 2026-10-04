from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from .contracts import Case, EffectCheck
from .functions import quote
from .runtime import NovaAPI


class TaskFixtures:
    def __init__(self, api: NovaAPI, database: str):
        self.api = api
        self.database = database
        self.name = "nova_sql_task_" + uuid4().hex[:12]
        self.native_name = "nova_sql_native_" + uuid4().hex[:12]
        self.run_name = "nova_sql_graph_" + uuid4().hex[:12]
        self.names = [self.name, self.name + "_invalid", self.run_name]
        self.selected = False

    def cases(self) -> list[Case]:
        self.selected = True
        body = f"INSERT INTO `{self.database}`.task_sink VALUES (100)"
        sink = (
            "CREATE TABLE IF NOT EXISTS task_sink(n INT) DUPLICATE KEY(n) "
            "DISTRIBUTED BY HASH(n) BUCKETS 1 PROPERTIES('replication_num'='1')"
        )
        qualifier = f"database_name={quote(self.database)} AND name={quote(self.name)}"
        return [
            Case(
                "task.create_metadata",
                "task",
                f"CREATE TASK {self.database}.default.{self.name} AS {body}",
                [self.name, self.database, "default", "manual", None, "skip", 0],
                "task_metadata",
                statement_rules=("submitTaskStatement",),
                setup=(sink,),
                effects=(
                    EffectCheck("SELECT COUNT(*) FROM task_sink", [[0]]),
                    EffectCheck(
                        "SELECT name,database_name,schema_name,definition,schedule_kind,"
                        "owner_role,created_by FROM NOVA_SYSTEM.CONFIG_TASKS WHERE " + qualifier,
                        [
                            [
                                self.name,
                                self.database,
                                "default",
                                body,
                                "manual",
                                "ACCOUNTADMIN",
                                self.api.target.user,
                            ]
                        ],
                    ),
                ),
            ),
            Case(
                "task.invalid_overlap",
                "task",
                f"CREATE TASK {self.database}.default.{self.name}_invalid "
                f"OVERLAP_POLICY='impossible' AS {body}",
                error_code=1064,
                error_contains="overlap",
                effects=(
                    EffectCheck(
                        "SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_TASKS WHERE database_name="
                        + quote(self.database)
                        + " AND name="
                        + quote(self.name + "_invalid"),
                        [[0]],
                    ),
                ),
            ),
            Case(
                "task.native_effect",
                "task",
                f"SUBMIT TASK {self.database}.{self.native_name} AS "
                f"CREATE TABLE {self.database}.task_output "
                "PROPERTIES('replication_num'='1') AS SELECT 42 AS n",
                self.native_name,
                "task_submission",
                statement_rules=("submitTaskStatement",),
                effects=(EffectCheck("SELECT n FROM task_output", [[42]], timeout_seconds=45),),
            ),
            Case(
                "task.orchestration_execution",
                "task",
                f"CREATE TASK {self.database}.default.{self.run_name} AS {body}",
                [self.run_name, self.database, "default", "manual", None, "skip", 0],
                "task_metadata",
                setup=(sink,),
                effects=(EffectCheck("SELECT n FROM task_sink", [[100]]),),
                prerequisite=(
                    "Nova orchestration execution requires an isolated stack with a running worker"
                ),
            ),
        ]

    async def execute_graph(self) -> dict:
        graph = f"{self.database}.default.{self.run_name}"
        run = await self.api.request("POST", f"/api/v1/task-orchestration/graphs/{graph}/runs")
        deadline = time.monotonic() + 90
        while True:
            observed = await self.api.request("GET", "/api/v1/task-orchestration/runs/" + run["id"])
            state = observed["run"]["state"]
            if state in {"success", "failed", "cancelled"}:
                assert state == "success", f"Task graph ended in {state}"
                assert observed["node_runs"] and all(
                    node["state"] == "success" for node in observed["node_runs"]
                )
                return {
                    "graph_id": graph,
                    "run_id": run["id"],
                    "state": state,
                    "nodes": observed["node_runs"],
                }
            if time.monotonic() >= deadline:
                raise TimeoutError("Task worker did not complete the isolated graph")
            await asyncio.sleep(1)

    async def close(self) -> None:
        if not self.selected:
            return
        qualifier = (
            f"database_name={quote(self.database)} AND name IN "
            f"({','.join(quote(name) for name in self.names)})"
        )
        graph = quote(f"{self.database}.default.{self.run_name}")
        await self.api.sql(
            "UPDATE NOVA_SYSTEM.CONFIG_TASK_GRAPH_RUNS SET state='cancelled' WHERE graph_id="
            + graph
            + " AND state IN ('pending','running')",
            confirm=True,
        )
        runs = await self.api.sql(
            "SELECT id FROM NOVA_SYSTEM.CONFIG_TASK_GRAPH_RUNS WHERE graph_id=" + graph
        )
        for row in runs[0]["rows"]:
            await self.api.sql(
                "DELETE FROM NOVA_SYSTEM.CONFIG_TASK_RUNS WHERE graph_run_id=" + quote(row[0]),
                confirm=True,
            )
        await self.api.sql(
            "DELETE FROM NOVA_SYSTEM.CONFIG_TASK_GRAPH_RUNS WHERE graph_id=" + graph, confirm=True
        )
        await self.api.sql("DELETE FROM NOVA_SYSTEM.CONFIG_TASKS WHERE " + qualifier, confirm=True)
        rows = await self.api.sql(
            "SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_TASKS WHERE " + qualifier
        )
        assert rows[0]["rows"] == [[0]], "Nova task metadata remains"
        native = await self.api.sql(
            "SELECT TASK_NAME FROM information_schema.tasks WHERE TASK_NAME="
            + quote(self.native_name)
        )
        if native[0]["rows"]:
            await self.api.sql(f"DROP TASK {self.database}.{self.native_name}", confirm=True)
        remaining = await self.api.sql(
            "SELECT TASK_NAME FROM information_schema.tasks WHERE TASK_NAME="
            + quote(self.native_name)
        )
        assert not remaining[0]["rows"], "Native task remains"
