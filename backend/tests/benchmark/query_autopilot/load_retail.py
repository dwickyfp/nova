"""Explicit fixture creation for an isolated native-RBAC acceptance stack."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from app.common.identifiers import check_identifier
from app.core.config import settings
from app.core.database import db
from app.core.security import encrypt_password
from app.modules.query.service import QueryService
from app.modules.query_autopilot.telemetry import purpose
from tests.benchmark.query_autopilot.personas import workloads
from tests.benchmark.query_autopilot.retail import SCALES, SCHEMA, generate


async def load(directory: Path, *, database: str, sandbox: str, scale: str) -> dict:
    if os.getenv("NOVA_AUTOPILOT_FIXTURE_STACK") != "1":
        raise ValueError(
            "Set NOVA_AUTOPILOT_FIXTURE_STACK=1 only for an isolated seeded test stack"
        )
    for name in (database, sandbox):
        check_identifier(name, field="fixture database")
        if not name.startswith("autopilot_"):
            raise ValueError("Fixture databases must use the autopilot_ prefix")
    if database == sandbox or settings.RANGER_ENABLED:
        raise ValueError("Use the separate patched-FE fixture setup for Ranger acceptance")
    manifest = generate(directory, SCALES[scale])
    await db.init_system_pool()
    password = os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
    role = "autopilot_fixture_admin"
    try:
        # Fixture provisioning is the only system-principal operation. All
        # subsequent table/data SQL uses the seeded authenticated test account.
        for name in (database, sandbox):
            existing = await db.execute_system("SHOW DATABASES")
            if any(row[0] == name for row in existing["rows"]):
                raise ValueError("fixture_database_exists_choose_a_fresh_name")
            await db.execute_system(f"CREATE DATABASE `{name}`")
        await db.execute_system(f"CREATE ROLE IF NOT EXISTS {role}")
        for name in (database, sandbox):
            await db.execute_system(f"GRANT ALL ON {name}.* TO ROLE {role}")
            await db.execute_system(f"GRANT ALL ON DATABASE {name} TO ROLE {role}")
        await db.execute_system(f"GRANT {role} TO USER nova_admin")
        service = QueryService()
        async with db.user_conn("nova_admin", password) as connection:

            async def execute(statement: str, target: str):
                with purpose("experiment"):
                    result = await service.execute(
                        statement,
                        "nova_admin",
                        encrypt_password(password),
                        database=target,
                        role=role,
                        connection=connection,
                        confirm_destructive=True,
                    )
                if not result.success or result.needs_confirmation:
                    raise ValueError(
                        "fixture_sql_failed: " + (result.error_code or "engine_rejected")
                    )

            for table, schema in SCHEMA.items():
                key = schema.split()[0]
                for target in (database, sandbox):
                    await execute(
                        f"CREATE TABLE {table} ({schema}) DUPLICATE KEY({key}) "
                        f"DISTRIBUTED BY HASH({key}) BUCKETS 1 PROPERTIES('replication_num'='1')",
                        target,
                    )
                with (directory / f"{table}.csv").open() as stream:
                    batch = []
                    for row in csv.reader(stream):
                        batch.append(
                            "(" + ",".join(connection.escape(value) for value in row) + ")"
                        )
                        if len(batch) == 500:
                            await execute(
                                f"INSERT INTO {table} VALUES " + ",".join(batch), database
                            )
                            batch.clear()
                    if batch:
                        await execute(f"INSERT INTO {table} VALUES " + ",".join(batch), database)
                await execute(
                    f"INSERT INTO {sandbox}.{table} SELECT * FROM {database}.{table}", sandbox
                )
                print(json.dumps({"table": table, "rows": manifest["tables"][table]}), flush=True)
        for workload in workloads(0):
            await db.execute_system(f"CREATE ROLE IF NOT EXISTS {workload.role}")
            await db.execute_system(
                f"CREATE USER IF NOT EXISTS 'autopilot_{workload.persona}' IDENTIFIED BY %s",
                (password,),
            )
            await db.execute_system(f"GRANT SELECT ON {database}.* TO ROLE {workload.role}")
            await db.execute_system(f"GRANT {workload.role} TO USER autopilot_{workload.persona}")
        await db.execute_system("CREATE ROLE IF NOT EXISTS autopilot_replay_role")
        await db.execute_system(
            "CREATE USER IF NOT EXISTS 'autopilot_replay' IDENTIFIED BY %s", (password,)
        )
        await db.execute_system(f"GRANT ALL ON {sandbox}.* TO ROLE autopilot_replay_role")
        await db.execute_system(f"GRANT ALL ON DATABASE {sandbox} TO ROLE autopilot_replay_role")
        await db.execute_system(
            f"GRANT ALL ON ALL MATERIALIZED VIEWS IN DATABASE {sandbox} "
            "TO ROLE autopilot_replay_role"
        )
        await db.execute_system("GRANT autopilot_replay_role TO USER autopilot_replay")
        await db.execute_system(
            "CREATE RESOURCE GROUP IF NOT EXISTS autopilot_sandbox TO (user='autopilot_replay') "
            "WITH ('cpu_weight'='1', 'mem_limit'='10%', 'concurrency_limit'='1')"
        )
        manifest.update(
            database=database, sandbox=sandbox, role=role, resource_group="autopilot_sandbox"
        )
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))
        return manifest
    finally:
        await db.close_system_pool()


def main():
    import asyncio

    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--sandbox", required=True)
    parser.add_argument("--scale", choices=SCALES, default="local")
    args = parser.parse_args()
    asyncio.run(
        load(args.directory, database=args.database, sandbox=args.sandbox, scale=args.scale)
    )


if __name__ == "__main__":
    main()
