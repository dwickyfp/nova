"""Planted statistics/cardinality error measured on an isolated native fixture."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from uuid import uuid4

from app.common.identifiers import check_identifier
from app.core.database import db
from app.modules.query.service import QueryService
from app.modules.query_autopilot.correctness import prove_result
from app.modules.query_autopilot.detection import Facts, detect, diagnose, serialize_findings
from app.modules.query_autopilot.experiments import Experiment, Measurement
from app.modules.query_autopilot.models import Budget, Policy, digest, utcnow
from app.modules.query_autopilot.profile import analyzed_profile_summary, profile_summary
from app.modules.query_autopilot.statistics import Distribution, Window, compare_baseline
from app.modules.query_autopilot.statistics_evidence import statistics_summary
from app.modules.query_autopilot.telemetry import purpose


async def statistics_case(database: str) -> dict:
    if os.getenv("NOVA_AUTOPILOT_FIXTURE_STACK") != "1" or not database.startswith("autopilot_"):
        raise ValueError("Explicit isolated retail fixture required")
    check_identifier(database, field="fixture database")
    table = "autopilot_stats_" + uuid4().hex[:10]
    statement = f"SELECT SUM(id) FROM {table} WHERE k=1"
    phases = {}
    durations = {"before": [], "after": []}
    async with db.user_conn(
        "nova_admin", os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
    ) as conn:

        async def execute(sql, *, confirmed=False):
            with purpose("experiment"):
                result = await QueryService().execute(
                    sql,
                    "nova_admin",
                    "",
                    database=database,
                    role="autopilot_fixture_admin",
                    connection=conn,
                    max_rows=100000,
                    confirm_destructive=confirmed,
                )
            if not result.success or result.truncated:
                raise ValueError("fixture_execution_failed_or_truncated")
            return result

        await execute(
            f"CREATE TABLE {table}(id BIGINT,k INT) DUPLICATE KEY(id) "
            'DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1")'
        )
        try:
            await execute(f"INSERT INTO {table} SELECT order_id,0 FROM orders LIMIT 100")
            await execute(f"ANALYZE TABLE {table} WITH SYNC MODE")
            # CI has 500 orders but 1,500 items. The planted positive must meet
            # the detector's 1,000-row support floor without lowering it.
            growth = await execute("SELECT COUNT(*) FROM order_items")
            growth_rows = int(growth.rows[0][0])
            if growth_rows < 1000:
                raise ValueError("cardinality_fixture_requires_at_least_1000_items")
            await execute(f"INSERT INTO {table} SELECT item_id,1 FROM order_items")
            await execute("SET enable_profile=true")
            await execute("SET query_mem_limit=536870912")
            await execute("SET query_timeout=30")

            async def revalidate():
                await execute("EXPLAIN " + statement)
                return True

            async def snapshot():
                result = await execute("SHOW PARTITIONS FROM " + table)
                indexes = [
                    i
                    for i, name in enumerate(result.columns)
                    if name in {"PartitionId", "VisibleVersion"}
                ]
                return (
                    digest([[row[i] for i in indexes] for row in result.rows])
                    if len(indexes) == 2
                    else None
                )

            async def measure(phase, control):
                result = await execute("SELECT COUNT(*) FROM customers" if control else statement)
                resource = None
                if not control:
                    durations[phase].append(result.engine_roundtrip_ms)
                if not control and len(durations[phase]) == 4:
                    await asyncio.sleep(0.3)
                    query_id = result.engine_query_ids[0]
                    profile = await execute("SELECT get_query_profile('" + query_id + "')")
                    resource = profile_summary(
                        "\n".join(str(v) for row in profile.rows for v in row)
                    )["facts"]
                    analyzed = await execute("ANALYZE PROFILE FROM '" + query_id + "'")
                    operators = analyzed_profile_summary(
                        "\n".join(str(v) for row in analyzed.rows for v in row), query_id=query_id
                    )
                    stats = await execute(
                        f"SHOW STATS META WHERE `Table`='{table}' AND `Database`='{database}'"
                    )
                    phases[phase] = {
                        "query_id": query_id,
                        "operators": operators,
                        "statistics": statistics_summary(stats.columns, stats.rows),
                    }
                return Measurement(
                    result.engine_roundtrip_ms,
                    prove_result(
                        result.rows,
                        result.column_types,
                        ordered=False,
                        max_rows=100,
                        max_bytes=10000,
                        truncated=result.truncated,
                    ),
                    resource=resource,
                    query_id=result.engine_query_ids[0],
                )

            async def refresh():
                await execute(f"ANALYZE TABLE {table} WITH SYNC MODE")

            trial = await Experiment(Budget(resource_group="autopilot_sandbox")).run(
                measure=measure,
                snapshot=snapshot,
                apply=refresh,
                revalidate=revalidate,
            )
            for phase, evidence in phases.items():
                latency = Distribution()
                for duration in durations[phase][3:]:
                    latency.add(duration)
                facts = Facts(
                    latency,
                    compare_baseline(Window(utcnow(), latency), []),
                    evidence_ids=(evidence["query_id"],),
                    **evidence["operators"]["facts"],
                    **evidence["statistics"]["facts"],
                )
                findings = detect(facts, Policy())
                evidence["findings"] = serialize_findings(findings)
                evidence["diagnosis"] = [asdict(d) for d in diagnose(findings, facts)]
            before = {f["detector"] for f in phases.get("before", {}).get("findings", [])}
            after = {f["detector"] for f in phases.get("after", {}).get("findings", [])}
            return {
                "case": "E",
                "ground_truth": "planted_growth_after_statistics_collection",
                "status": "PASS"
                if {"statistics", "cardinality_error"} <= before
                and not {"statistics", "cardinality_error"} & after
                and trial.correctness == "EQUIVALENT"
                else "FAIL",
                "evidence_kind": "real_native_engine_controlled_fixture",
                "planting": {"growth_source": "order_items", "inserted_rows": growth_rows,
                             "cardinality_support_floor": 1000},
                "phases": phases,
                "experiment": asdict(trial),
                "production_actions": 0,
                "causality": "refresh_reduced_estimate_error; latency_benefit_reported_separately",
            }
        finally:
            await execute("DROP TABLE " + table, confirmed=True)
