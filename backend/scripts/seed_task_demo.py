"""Install the acceptance SQL examples on an explicitly selected Nova instance."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from app.common.audit import write_audit_log
from app.common.identifiers import check_identifier
from app.common.sql_guard import split_sql_statements, strip_sql_comments
from app.core.config import settings
from app.core.database import db
from app.modules.query.service import QueryService
from app.modules.task_orchestration.repository import task_orchestration_repository as repo


async def seed(port: int, database: str, artifacts: Path, owner: str, role: str) -> None:
    check_identifier(database, field="demo database")
    source = json.loads((artifacts / "results.json").read_text())
    source_database = source["database"]
    check_identifier(source_database, field="source database")
    settings.STARROCKS_HOST = "127.0.0.1"
    settings.STARROCKS_FE_MYSQL_PORT = port
    settings.RANGER_ENABLED = True
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", owner):
        raise ValueError("Invalid demo owner")
    check_identifier(role, field="execution role")
    await db.init_system_pool()
    try:
        # Only fixture DDL/data is copied. Run records always belong to their instance.
        for raw in split_sql_statements((artifacts / "samples.sql").read_text()):
            statement = strip_sql_comments(raw).strip().replace(source_database, database)
            if statement.startswith("CREATE TASK "):
                continue
            if statement == f"CREATE DATABASE {database}":
                statement = f"CREATE DATABASE IF NOT EXISTS {database}"
            elif statement.startswith(f"CREATE TABLE {database}."):
                statement = statement.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
            elif statement.startswith(f"INSERT INTO {database}."):
                table = re.match(r"INSERT INTO ([A-Za-z0-9_]+\.[A-Za-z0-9_]+) ", statement)
                if table is None:
                    raise ValueError("Unsupported fixture INSERT")
                count = await db.execute_system(f"SELECT COUNT(*) FROM {table[1]}")
                if count["rows"][0][0]:
                    continue
            else:
                raise ValueError("Unexpected statement in fixture setup")
            await db.execute_system(statement)
            await write_audit_log(
                event_type="TASK_DEMO",
                user_name=owner,
                active_role=role,
                action="SEED",
                object_type="DATABASE",
                object_name=database,
                database_name=database,
                sql_text=statement,
                status="SUCCESS",
            )

        existing = {
            row["name"]: row
            for row in await repo.list_tasks()
            if row["database_name"] == database and row.get("schema_name") == "default"
        }
        names: list[str] = []
        for task in source["tasks"]:
            # The denied-owner fixture needs its disposable, unprivileged test account.
            if task["name"] == "denied":
                continue
            name = task["name"]
            body = task["sql"].replace(source_database, database)
            if name in existing:
                row = existing[name]
                if row["definition"] != body or row["created_by"] != owner:
                    raise ValueError(f"Existing demo task differs: {name}")
            else:
                result = await QueryService().execute(
                    task["ddl"].replace(source_database, database),
                    username=owner,
                    encrypted_password="",
                    database=database,
                    schema="default",
                    role=role,
                )
                if result.error:
                    raise RuntimeError(result.error)
            names.append(name)

        all_graphs = await repo.list_graph_ids()
        graphs = [graph for graph in all_graphs if graph.startswith(database + ".")]
        report = {
            "database": database,
            "engine_port": port,
            "owner": owner,
            "role": role,
            "task_count": len(names),
            "task_names": names,
            "demo_graph_count": len(graphs),
            "accountadmin_total_graph_count": len(all_graphs),
            "graph_ids": graphs,
            "copied_run_history": False,
        }
        (artifacts / "main-instance.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
    finally:
        await db.close_system_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", default="NOVA_TASK_DEMO")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "docs/benchmarks/task-acceptance-2026-09-25",
    )
    args = parser.parse_args()
    asyncio.run(seed(args.port, args.database, args.artifacts, args.owner, args.role))
