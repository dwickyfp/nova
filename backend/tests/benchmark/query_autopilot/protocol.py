"""Measured MySQL-protocol traffic, collection overhead and logical storage growth."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from contextlib import suppress
from pathlib import Path

import asyncmy

from app.common.identifiers import check_identifier
from app.core.database import db
from app.modules.query_autopilot.aggregation import aggregate
from app.modules.query_autopilot.repository import repository
from app.modules.query_autopilot.schema import ensure_schema
from app.modules.query_autopilot.telemetry import collector
from app.proxy.server import MySQLProxyServer
from tests.benchmark.query_autopilot.personas import workloads


async def run(database: str, output: Path, *, repeats: int = 30) -> dict:
    if os.getenv("NOVA_AUTOPILOT_FIXTURE_STACK") != "1" or not database.startswith("autopilot_"):
        raise ValueError("Explicit isolated fixture stack required")
    check_identifier(database, field="benchmark database")
    if repeats < 30:
        raise ValueError("At least 30 measured repetitions required")
    password = os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
    await db.init_system_pool()
    await ensure_schema()
    server = MySQLProxyServer(host="0.0.0.0", port=0)
    await server.start()
    stop = asyncio.Event()

    async def flush():
        while not stop.is_set():
            await collector.flush(repository)
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=0.25)
        while not collector.queue.empty():
            await collector.flush(repository)

    async def storage():
        result = await db.execute_system(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(CAST(payload AS VARCHAR))),0) "
            "FROM NOVA_SYSTEM.QUERY_AUTOPILOT_OBSERVATIONS"
        )
        return {"rows": int(result["rows"][0][0]), "json_bytes": int(result["rows"][0][1])}

    before = await storage()
    task = asyncio.create_task(flush())
    original = collector.enabled
    measurements, cli_results = [], []
    try:
        for persona in workloads(0):
            connection = await asyncmy.connect(
                host="127.0.0.1",
                port=server.bound_port,
                user="autopilot_" + persona.persona,
                password=password,
                autocommit=True,
            )
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(f"SET ROLE {persona.role}")
                    await cursor.execute(f"USE {database}")
                    for cycle, enabled in enumerate((False, True, False, True)):
                        collector.enabled = enabled
                        times = []
                        cpu_times = []
                        for index in range(3 + repeats):
                            workload = next(
                                w for w in workloads(index % 10) if w.persona == persona.persona
                            )
                            start = time.perf_counter()
                            cpu_start = time.process_time()
                            row_counts = []
                            for statement in workload.queries:
                                await cursor.execute(statement)
                                row_counts.append(len(await cursor.fetchall()))
                            elapsed = (time.perf_counter() - start) * 1000
                            cpu_elapsed = (time.process_time() - cpu_start) * 1000
                            if index >= 3:
                                times.append(elapsed)
                                cpu_times.append(cpu_elapsed)
                        measurements.append(
                            {
                                "persona": persona.persona,
                                "collection_enabled": enabled,
                                "cycle": cycle,
                                "count": len(times),
                                "mean_ms": statistics.mean(times),
                                "nova_process_cpu_mean_ms": statistics.mean(cpu_times),
                                "stddev_ms": statistics.stdev(times),
                                "p95_ms": sorted(times)[int(0.95 * len(times))],
                                "parameters": "paired index modulo 10",
                                "last_result_rows": row_counts,
                            }
                        )
            finally:
                connection.close()
            statement = f"SET ROLE {persona.role}; USE {database}; " + "; ".join(persona.queries)
            process = await asyncio.create_subprocess_exec(
                "docker",
                "run",
                "--rm",
                "-e",
                "MYSQL_PWD",
                "mysql:8.0",
                "mysql",
                "--protocol=TCP",
                "--ssl-mode=DISABLED",
                "--batch",
                "--skip-column-names",
                "-h",
                os.getenv("NOVA_PROXY_E2E_HOST", "host.docker.internal"),
                "-P",
                str(server.bound_port),
                "-u",
                "autopilot_" + persona.persona,
                "-e",
                statement,
                env={**os.environ, "MYSQL_PWD": password},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=60)
            cli_results.append(
                {
                    "persona": persona.persona,
                    "status": "PASS" if process.returncode == 0 else "FAIL",
                    "rows": len(stdout.splitlines()),
                }
            )
            if process.returncode:
                raise ValueError("containerized_mysql_cli_failed")
        stop.set()
        await task
        after = await storage()
        await aggregate(repository)
        disabled = statistics.mean(
            m["mean_ms"] for m in measurements if not m["collection_enabled"]
        )
        enabled = statistics.mean(m["mean_ms"] for m in measurements if m["collection_enabled"])
        report = {
            "kind": "measured_native_engine_protocol",
            "database": database,
            "measurements": measurements,
            "mysql_cli": cli_results,
            "collection": collector.health(),
            "logical_storage_before": before,
            "logical_storage_after": after,
            "overhead_mean_ms": enabled - disabled,
            "overhead_ratio": enabled / disabled - 1,
            "cpu_delta_mean_ms": statistics.mean(
                m["nova_process_cpu_mean_ms"] for m in measurements if m["collection_enabled"]
            )
            - statistics.mean(
                m["nova_process_cpu_mean_ms"] for m in measurements if not m["collection_enabled"]
            ),
            "ranger_acceptance": "NOT_RUN",
            "note": (
                "Includes paired query and drilldown round trips through "
                "QueryService; JSON bytes exclude indexes and replicas."
            ),
        }
        output.mkdir(parents=True, exist_ok=True)
        (output / "protocol.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "protocol.md").write_text(
            "# Native protocol measurements\n\n"
            f"Four personas; {repeats} measurements per cycle and three warmups.\n\n"
            f"Collection overhead: {enabled - disabled:.2f} ms "
            f"({100 * (enabled / disabled - 1):.1f}%). "
            f"Observation JSON growth: {after['json_bytes'] - before['json_bytes']} bytes.\n\n"
            "Containerized MySQL CLI: 4/4 passed. Ranger acceptance is a separate required check.\n"
        )
        return report
    finally:
        stop.set()
        await task
        collector.enabled = original
        await server.stop()
        await db.close_system_pool()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.database, args.output))


if __name__ == "__main__":
    main()
