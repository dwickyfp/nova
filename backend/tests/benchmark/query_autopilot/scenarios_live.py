"""Measured native scenarios, with explicit unavailable cases and fixture provenance."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from app.common.identifiers import check_identifier
from app.core.database import db
from app.modules.query.service import QueryService
from app.modules.query_autopilot.correctness import prove_result
from app.modules.query_autopilot.detection import (
    Facts,
    detect,
    diagnose,
    priority,
    serialize_findings,
)
from app.modules.query_autopilot.experiments import Experiment, Measurement
from app.modules.query_autopilot.log_evidence import log_summary
from app.modules.query_autopilot.models import Budget, Policy, utcnow
from app.modules.query_autopilot.statistics import Distribution, Window, compare_baseline
from app.modules.query_autopilot.telemetry import purpose
from app.sql_frontend.autopilot import materialized_view_shape
from app.sql_frontend.fingerprint import fingerprint


def facts_for(samples: list[float], **kwargs) -> Facts:
    distribution = Distribution()
    for sample in samples:
        distribution.add(sample)
    return Facts(distribution, compare_baseline(Window(utcnow(), distribution), []), **kwargs)


async def run(
    database: str, output: Path, *, wall_clock_history: bool = False,
    contention_database: str | None = None,
) -> dict:
    if os.getenv("NOVA_AUTOPILOT_FIXTURE_STACK") != "1" or not database.startswith("autopilot_"):
        raise ValueError("Explicit isolated retail fixture required")
    check_identifier(database, field="fixture database")
    contention_database = contention_database or database
    if not contention_database.startswith("autopilot_"):
        raise ValueError("Explicit isolated contention snapshot required")
    check_identifier(contention_database, field="contention snapshot")
    await db.init_system_pool()
    cases = []
    try:
        async with db.user_conn(
            "autopilot_executive", os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
        ) as conn:
            service = QueryService()

            async def execute(statement):
                with purpose("experiment"):
                    result = await service.execute(
                        statement,
                        "autopilot_executive",
                        "",
                        database=database,
                        role="autopilot_executive",
                        connection=conn,
                        max_rows=200000,
                    )
                if not result.success:
                    raise ValueError("fixture_execution_failed")
                return result

            times, parameters, families, queries = [], [], set(), []
            for index in range(123):
                parameter = 1 + index % 10
                statement = (
                    f"SELECT store_id,SUM(total) AS revenue FROM orders WHERE "
                    f"store_id={parameter} GROUP BY store_id"
                )
                result = await execute(statement)
                if index >= 3:
                    times.append(result.engine_roundtrip_ms)
                    parameters.append(parameter)
                    families.add(fingerprint(statement).family_id)
                    queries.extend(result.engine_query_ids)
            frequent = facts_for(times)
            rare_result = await execute("SELECT sleep(6)")
            rare = facts_for([rare_result.engine_roundtrip_ms])
            frequent_findings = detect(frequent, Policy())
            rare_findings = detect(rare, Policy())
            a, b = priority(frequent, 0), priority(rare, 0)
            cases.extend(
                [
                    {
                        "case": "A",
                        "ground_truth": "high_frequency",
                        "status": "PASS"
                        if any(f.detector == "high_frequency" for f in frequent_findings)
                        and a.score > b.score
                        else "FAIL",
                        "count": len(times),
                        "latency": frequent.latency.as_dict(),
                        "priority": asdict(a),
                        "query_ids": queries,
                    },
                    {
                        "case": "B",
                        "ground_truth": "rare_slow_lower_priority",
                        "status": "PASS"
                        if any(f.detector == "rare_slow" for f in rare_findings)
                        and b.score < a.score
                        else "FAIL",
                        "latency": rare.latency.as_dict(),
                        "priority": asdict(b),
                        "query_ids": rare_result.engine_query_ids,
                        "replay_eligible": fingerprint("SELECT sleep(6)").replay_eligible,
                    },
                    {
                        "case": "C",
                        "ground_truth": "one_parameterized_family",
                        "status": "PASS"
                        if len(families) == 1 and len(set(parameters)) == 10
                        else "FAIL",
                        "families": len(families),
                        "distinct_parameters": len(set(parameters)),
                    },
                ]
            )
            aggregate_shape = materialized_view_shape(statement)
            aggregate = facts_for(
                times,
                evidence_ids=("measured-repeated-aggregate",),
                compatible_aggregates=len(set(parameters)),
                mv_eligible=aggregate_shape.eligible,
            )
            aggregate_findings = detect(aggregate, Policy())
            cases.append(
                {
                    "case": "F-detection",
                    "ground_truth": "mv_opportunity",
                    "status": "PASS"
                    if any(f.detector == "materialized_view" for f in aggregate_findings)
                    else "FAIL",
                    "findings": serialize_findings(aggregate_findings),
                    "diagnosis": [asdict(d) for d in diagnose(aggregate_findings, aggregate)],
                    "baseline": asdict(aggregate.baseline),
                    "count": len(times),
                    "latency": aggregate.latency.as_dict(),
                    "query_ids": queries,
                    "facts": {
                        "compatible_aggregates": len(set(parameters)),
                        "mv_eligible": aggregate_shape.eligible,
                        "grouping_key_count": len(aggregate_shape.grouping),
                        "aggregate_expression_count": len(aggregate_shape.aggregates),
                        "replay_eligible": fingerprint(statement).replay_eligible,
                    },
                    "native_trial_report": "experiment-native.json",
                }
            )

            # A deterministic result-changing candidate is exercised against the
            # real engine. It must fail even if latency appears to improve.
            async def revalidate():
                await execute("EXPLAIN SELECT COUNT(*) FROM orders")
                return True

            async def snapshot():
                result = await execute("SHOW PARTITIONS FROM orders")
                from app.modules.query_autopilot.models import digest

                fields = [
                    i
                    for i, name in enumerate(result.columns)
                    if name in {"PartitionId", "VisibleVersion"}
                ]
                return (
                    digest([[row[i] for i in fields] for row in result.rows])
                    if len(fields) == 2
                    else None
                )

            async def no_change():
                pass

            async def measure(phase, control):
                statement = (
                    "SELECT COUNT(*) FROM customers"
                    if control
                    else (
                        "SELECT COUNT(*) FROM orders"
                        if phase == "before"
                        else "SELECT COUNT(*)+1 FROM orders"
                    )
                )
                result = await execute(statement)
                return Measurement(
                    result.engine_roundtrip_ms,
                    prove_result(
                        result.rows,
                        result.column_types,
                        ordered=False,
                        max_rows=100,
                        max_bytes=10000,
                    ),
                    query_id=result.engine_query_ids[0],
                )

            result = await Experiment(Budget(resource_group="autopilot_sandbox")).run(
                measure=measure, snapshot=snapshot, apply=no_change, revalidate=revalidate
            )
            cases.append(
                {
                    "case": "H",
                    "ground_truth": "result_changing_candidate_rejected",
                    "status": "PASS"
                    if result.status == "FAILED" and result.reason == "result_changed"
                    else "FAIL",
                    "experiment": asdict(result),
                }
            )
            query_id = str(uuid4())
            log = log_summary(f"ERROR query_id={query_id} Memory limit exceeded", query_id)
            log_facts = facts_for(
                times, evidence_ids=("controlled-correlated-log",), **log["facts"]
            )
            diagnosis = diagnose(detect(log_facts, Policy()), log_facts)
            unrelated = log_summary(f"ERROR query_id={uuid4()} Memory limit exceeded", query_id)
            cases.append(
                {
                    "case": "I",
                    "ground_truth": "explicit_memory_limit_only_for_exact_execution",
                    "evidence_kind": "controlled_log_fixture",
                    "status": "PASS"
                    if diagnosis[0].category == "ENGINE_MEMORY_LIMIT" and not unrelated["facts"]
                    else "FAIL",
                    "diagnosis": [asdict(d) for d in diagnosis],
                }
            )
        from tests.benchmark.query_autopilot.contention_live import contention_case
        from tests.benchmark.query_autopilot.regression_live import regression_case
        from tests.benchmark.query_autopilot.statistics_live import statistics_case

        cases.append(await statistics_case(database))
        try:
            contention = await contention_case(
                contention_database, wall_clock_history=wall_clock_history,
                checkpoint=output / "contention-progress.json",
            )
        except Exception as exc:
            cases.extend([
                {"case": "G", "status": "UNAVAILABLE",
                 "reason": "fixture_contention_execution_unavailable",
                 "error_type": type(exc).__name__},
                {"case": "D", "status": "UNAVAILABLE",
                 "reason": "contention_measurements_unavailable"},
            ])
        else:
            cases.extend([contention, regression_case(contention)])
        result = {
            "evidence_kind": "real_native_engine_and_declared_controlled_fixtures",
            "contention_database": contention_database,
            "cases": cases,
            "counts": {
                status: sum(c["status"] == status for c in cases)
                for status in ("PASS", "FAIL", "UNAVAILABLE")
            },
            "complete_acceptance": all(c["status"] == "PASS" for c in cases) and wall_clock_history,
            "production_actions": 0,
            "accepted_result_changing_candidates": 0 if result.status == "FAILED" else 1,
        }
        output.mkdir(parents=True, exist_ok=True)
        (output / "scenarios-native.json").write_text(json.dumps(result, indent=2) + "\n")
        text = "# Native scenario coverage\n\n" + "\n".join(
            f"- {case['case']}: {case['status']}" for case in cases
        )
        text += "\n\nUNAVAILABLE cases do not count as passed acceptance or accuracy evidence.\n"
        if not wall_clock_history:
            text += (
                "Regression uses a controlled detector clock; "
                "wall-clock history acceptance is NOT_RUN.\n"
            )
        (output / "scenarios-native.md").write_text(text)
        print(text)
        return result
    finally:
        await db.close_system_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--contention-database",
        help="Registered snapshot on which the dedicated replay identity already has access",
    )
    parser.add_argument(
        "--wall-clock-history",
        action="store_true",
        help="Collect three complete 30-minute historical windows; takes up to two hours",
    )
    args = parser.parse_args()
    result = asyncio.run(
        run(
            args.database, args.output, wall_clock_history=args.wall_clock_history,
            contention_database=args.contention_database,
        )
    )
    if not result["complete_acceptance"]:
        raise SystemExit(1)
