from __future__ import annotations

import argparse
import asyncio
import fnmatch
import getpass
import json
import os
import sys
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .ai import AIBudget, ai_cases, configure_aliases, verify_redaction
from .classification import public_exclusion
from .contracts import Case, Outcome, UnverifiedBinding, normalize
from .engine_overloads import engine_cases
from .functions import function_cases
from .inventory import (
    BASELINE_PATH,
    baseline_differences,
    inventory_document,
    read_functions,
)
from .logical_overloads import logical_cases
from .manifest import capability_manifest
from .ml import MLFixtures
from .mysql_client import compatibility_cases
from .provenance import source_snapshot
from .reporting import write_reports
from .runtime import NovaAPI, Target
from .scenarios import core_cases
from .stages import StageFixtures
from .tasks import TaskFixtures


async def execute_case(case: Case, target: Target, database: str, observer=None) -> Outcome:
    started = time.perf_counter()
    result = Outcome(
        case.id,
        case.group,
        "FAIL",
        target.safe(case.sql),
        signatures=list(case.signatures),
        statement_rules=list(case.statement_rules),
        verification_kind="refusal" if case.error_code is not None else "positive",
        logical_overloads=list(case.logical_overloads),
    )
    connection = None
    try:
        if case.prerequisite:
            result.status = "BLOCKED"
            result.detail = case.prerequisite
            return result
        connection = await target.connect()
        async with connection.cursor() as cursor:
            await asyncio.wait_for(cursor.execute(f"USE `{database}`"), timeout=20)
            for sql in case.setup:
                await asyncio.wait_for(cursor.execute(sql), timeout=60)
            try:
                await asyncio.wait_for(cursor.execute(case.sql, case.parameters), timeout=60)
                rows = list(await cursor.fetchall())
                result.observed_rows = normalize(rows)
                result.observed_columns = normalize(list(cursor.description or []))
            except Exception as exc:
                code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
                result.observed_error = {"code": code, "message": target.safe(str(exc))}
                if case.error_code is None:
                    raise
                assert code == case.error_code, f"expected error {case.error_code}; got {code}"
                if case.error_contains:
                    assert case.error_contains.lower() in str(exc).lower(), (
                        f"Unexpected refusal: {exc}"
                    )
            else:
                assert case.error_code is None, "Expected refusal, but Nova returned success"
                case.check(rows, list(cursor.description or []))
            if observer is not None:
                result.observed_effects.append(await observer())
            for effect in case.effects:
                deadline = time.monotonic() + effect.timeout_seconds
                evidence = {"sql": target.safe(effect.sql), "status": "FAIL", "rows": None}
                result.observed_effects.append(evidence)
                while True:
                    try:
                        await asyncio.wait_for(cursor.execute(effect.sql), timeout=20)
                        observed = list(await cursor.fetchall())
                        evidence["rows"] = normalize(observed)
                        Case(case.id, case.group, effect.sql, effect.expected, effect.oracle).check(
                            observed, list(cursor.description or [])
                        )
                    except Exception as exc:
                        evidence["detail"] = target.safe(f"{type(exc).__name__}: {exc}")
                        if (
                            isinstance(exc, (TimeoutError, ConnectionError))
                            or (type(exc).__name__ == "OperationalError")
                            or time.monotonic() >= deadline
                        ):
                            raise
                        await asyncio.sleep(0.5)
                    else:
                        evidence["status"] = "PASS"
                        evidence.pop("detail", None)
                        break
        result.status = "PASS"
    except UnverifiedBinding as exc:
        result.status = "BLOCKED"
        result.detail = target.failure(exc)
    except Exception as exc:
        result.detail = target.safe(f"{type(exc).__name__}: {exc}")
    finally:
        if connection is not None:
            connection.close()
        result.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    return result


async def check_engine_health(target: Target, database: str) -> None:
    connection = await target.connect()
    try:
        async with connection.cursor() as cursor:
            await asyncio.wait_for(cursor.execute(f"USE `{database}`"), timeout=15)
            await asyncio.wait_for(cursor.execute("SELECT COUNT(*) FROM numbers"), timeout=20)
            assert await cursor.fetchone() == (6,), "Fixture data or engine availability changed"
    finally:
        connection.close()


def exclude_case_groups(cases: list[Case], groups: list[str], reason: str | None):
    if groups and not reason:
        raise ValueError("An explicit reason is required for excluded groups")
    excluded = [case for case in cases if case.group in groups]
    return [case for case in cases if case.group not in groups], [
        {**asdict(case), "verification": "USER_EXCLUDED", "reason": reason}
        for case in excluded
    ]


async def execute_cases(
    cases, target, database, outcomes, *, workers=1, checkpoint=None, observers=None
):
    semaphore = asyncio.Semaphore(workers)
    stopped = asyncio.Event()
    completed = 0
    reason = ""

    async def execute(case):
        nonlocal completed, reason
        async with semaphore:
            if stopped.is_set():
                result = Outcome(
                    case.id,
                    case.group,
                    "BLOCKED",
                    target.safe(case.sql),
                    detail=reason,
                    signatures=list(case.signatures),
                    statement_rules=list(case.statement_rules),
                    logical_overloads=list(case.logical_overloads),
                )
            else:
                observer = (observers or {}).get(case.id)
                result = await execute_case(case, target, database, observer)
                if result.status == "FAIL" and (
                    result.observed_error is not None
                    or "TimeoutError" in result.detail or "OperationalError" in result.detail
                ):
                    try:
                        await check_engine_health(target, database)
                    except Exception as exc:
                        reason = target.safe(f"Engine health probe failed: {type(exc).__name__}")
                        stopped.set()
            outcomes.append(result)
            completed += 1
            if checkpoint is not None:
                checkpoint(completed, result)

    await asyncio.gather(*(execute(case) for case in cases))
    return reason or None


async def run(args) -> int:
    if not __debug__:
        raise RuntimeError("Functional SQL verification requires Python assertions enabled")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    directory = Path(args.report_dir or f"/tmp/nova-functional-sql/{run_id}")
    password = os.getenv("NOVA_SQL_TEST_PASSWORD", "")
    if not password and sys.stdin.isatty():
        password = getpass.getpass("Nova test account password: ")
    target = Target(
        args.host,
        args.port,
        args.user,
        password,
        args.api_url,
        args.planner_timeout_ms,
        args.role or None,
    )
    if args.cleanup_fixtures:
        from .recovery import recover

        return await recover(Path(args.cleanup_fixtures), target)
    outcomes: list[Outcome] = []
    document: dict = {}
    database = "nova_sql_test_" + uuid4().hex[:12]
    api = NovaAPI(target)
    created = False
    metadata = {
        "run_id": run_id,
        "target": f"{target.host}:{target.port}",
        "suite": args.suite,
        "database": database,
        "full_matrix_requested": args.suite == "all",
        "ai_spend_upper_bound_usd": 0,
        "cleanup_verified": False,
        "planner_timeout_ms": args.planner_timeout_ms,
        "active_role": target.role,
        "ml_mode": args.ml_mode,
        "source_snapshot": source_snapshot(),
        "excluded_groups": args.exclude_group,
        "exclusion_reason": args.exclusion_reason,
    }
    directory.mkdir(parents=True, exist_ok=True)

    def fixture_checkpoint():
        journal = {
            "run_id": run_id,
            "target": metadata["target"],
            "api_url": target.api_url,
            "user": target.user,
            "role": target.role,
            "database": database,
            "database_created": created,
            "stages": [
                {key: stage[key] for key in ("id", "name", "database_name")}
                for stage in stages.stages
            ],
            "models": models.models,
            "tasks": tasks.names if tasks.selected else [],
            "native_task": tasks.native_name if tasks.selected else None,
            "cleanup_verified": metadata["cleanup_verified"],
        }
        temporary = directory / "fixtures.json.tmp"
        temporary.write_text(json.dumps(journal, indent=2))
        temporary.chmod(0o600)
        temporary.replace(directory / "fixtures.json")

    stages = StageFixtures(api, database, args.storage_connection, fixture_checkpoint)
    models = MLFixtures(api, database, fixture_checkpoint, mode=args.ml_mode)
    tasks = TaskFixtures(api, database)
    fixture_checkpoint()
    try:
        if not password:
            raise RuntimeError("Set NOVA_SQL_TEST_PASSWORD or use an interactive password prompt")
        connection = await target.connect()
        try:
            async with connection.cursor() as cursor:
                await asyncio.wait_for(
                    cursor.execute("SELECT CURRENT_VERSION(), CURRENT_USER(), CURRENT_ROLE()"),
                    timeout=30,
                )
                metadata["engine_identity"] = list(await cursor.fetchone())
                if not str(metadata["engine_identity"][0]).startswith("4.1.4-4a9848e"):
                    raise RuntimeError("Runtime engine does not match the researched source pin")
            functions = await read_functions(connection)
            document = inventory_document(functions)
        finally:
            connection.close()
        if args.record_baseline:
            BASELINE_PATH.write_text(json.dumps(document, indent=2) + "\n")
        differences = baseline_differences(document)
        outcomes.append(
            Outcome(
                "inventory.baseline",
                "inventory",
                "BLOCKED" if any(differences.values()) else "PASS",
                detail=json.dumps(differences),
            )
        )
        if args.inventory:
            metadata["cleanup_verified"] = True
        else:
            await api.login()
            connection = await target.connect()
            try:
                async with connection.cursor() as cursor:
                    created = True
                    fixture_checkpoint()
                    await asyncio.wait_for(cursor.execute(f"CREATE DATABASE `{database}`"), 60)
                    await asyncio.wait_for(cursor.execute(f"USE `{database}`"), 20)
                    await asyncio.wait_for(
                        cursor.execute(
                            "CREATE TABLE numbers (n INT NOT NULL) DUPLICATE KEY(n) "
                            "DISTRIBUTED BY HASH(n) BUCKETS 1 PROPERTIES('replication_num'='1')"
                        ),
                        60,
                    )
                    await asyncio.wait_for(
                        cursor.execute("INSERT INTO numbers VALUES (0),(1),(2),(3),(4),(5)"), 60
                    )
            finally:
                connection.close()
            cases = function_cases(functions) + core_cases(database)
            cases.extend(logical_cases(document["scalar_logical_overloads"]))
            cases.extend(engine_cases(document["engine_logical_overloads"]))
            if args.suite == "all" or "task" in args.suite.split(","):
                cases.extend(tasks.cases())
                fixture_checkpoint()
            if args.suite == "all" or "ml" in args.suite.split(","):
                cases.extend(models.relay_cases())
                try:
                    await models.provision()
                except Exception as exc:
                    outcomes.append(
                        Outcome(
                            "fixture.ml",
                            "ml",
                            "BLOCKED",
                            target.safe(models.last_sql),
                            detail=target.failure(exc),
                            observed_rows=models.failed_result,
                        )
                    )
                finally:
                    cases.extend(models.cases)
                    for item in models.evidence:
                        outcomes.append(
                            Outcome(
                                "ml.api_training_" + item["kind"],
                                "ml",
                                "PASS",
                                target.safe(item["sql"]),
                                detail=(
                                    "Authenticated API counterpart; "
                                    "SQL inference is tested separately"
                                ),
                                observed_rows=normalize(item["result"]),
                            )
                        )
            if args.suite == "all" or "ai" in args.suite.split(","):
                ai_ready = False
                try:
                    await verify_redaction(api)
                    outcomes.append(Outcome("ai.metadata_redaction", "security", "PASS"))
                    if args.configure_ai:
                        await configure_aliases(api)
                    aliases = (await api.request("GET", "/api/v1/ai/aliases"))["aliases"]
                    kinds = {
                        item["function_type"]
                        for item in aliases
                        if item["is_default"] and item["is_active"]
                    }
                    assert (
                        len(
                            kinds
                            & {
                                "complete",
                                "sentiment",
                                "classify",
                                "summarize",
                                "extract",
                                "translate",
                                "filter",
                            }
                        )
                        == 7
                    ), "Seven live AI aliases required"
                    for item in aliases:
                        if item["function_type"] in kinds and item["is_default"]:
                            assert item["model_name"] == "deepseek-v4-1-flash"
                            assert 0 < item["default_params"]["max_tokens"] <= 1024
                    budget = AIBudget(Path(args.ai_ledger))
                    metadata.update(await budget.reserve(7))
                    ai_ready = True
                except Exception as exc:
                    outcomes.append(
                        Outcome("fixture.ai", "ai", "BLOCKED", detail=target.failure(exc))
                    )
                cases.extend(ai_cases(functions, ready=ai_ready))
            if args.suite == "all" or "stage" in args.suite.split(","):
                try:
                    cases.extend(await stages.provision())
                except Exception as exc:
                    outcomes.append(
                        Outcome("fixture.stages", "stage", "BLOCKED", detail=target.failure(exc))
                    )
            cases, exclusions = exclude_case_groups(
                cases, args.exclude_group, args.exclusion_reason
            )
            metadata["user_excluded_cases"] = len(exclusions)
            (directory / "excluded_cases.json").write_text(
                json.dumps(exclusions, indent=2, ensure_ascii=False)
            )
            selected_groups = set(args.suite.split(","))
            if "core" in selected_groups:
                selected_groups.remove("core")
                selected_groups.update(case.group for case in core_cases(database))
            if "functions" in selected_groups:
                selected_groups.remove("functions")
                selected_groups.update(
                    {
                        "scalar", "aggregate", "complex", "conditional",
                        "table", "analytic", "geospatial",
                    }
                )
            if "all" not in selected_groups:
                unknown = selected_groups - {case.group for case in cases} - {"stage", "ai"}
                if unknown:
                    raise RuntimeError(f"Unknown suite groups: {sorted(unknown)}")
                cases = [case for case in cases if case.group in selected_groups]
            if args.isolated_stack:
                if args.port == 4406 or args.api_url.rstrip("/") == "http://127.0.0.1:8000":
                    raise RuntimeError("Isolated trials must use separate proxy and API endpoints")
                cases = [
                    replace(case, prerequisite=None)
                    if case.prerequisite
                    in {
                        "Aggregate-state trials require --isolated-stack",
                        (
                            "Nova orchestration execution requires an isolated stack "
                            "with a running worker"
                        ),
                    }
                    else case
                    for case in cases
                ]
            if args.case:
                cases = [
                    case for case in cases
                    if any(fnmatch.fnmatchcase(case.id, pattern) for pattern in args.case)
                ]
            if args.rerun:
                previous = json.loads(Path(args.rerun).read_text())
                failed = {item["id"] for item in previous["outcomes"] if item["status"] != "PASS"}
                cases = [case for case in cases if case.id in failed]
            if not cases:
                raise RuntimeError("No executable cases selected")
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "cases.json").write_text(
                json.dumps([asdict(case) for case in cases], indent=2, ensure_ascii=False)
            )
            (directory / "manifest.json").write_text(
                json.dumps(
                    capability_manifest(document, cases, exclusions), indent=2, ensure_ascii=False
                )
            )

            def checkpoint(completed, result):
                if completed % 50 == 0 or result.status == "FAIL":
                    print(f"Executed {completed}/{len(cases)} cases", flush=True)
                    write_reports(directory, outcomes, document, metadata)

            engine_failure = await execute_cases(
                cases,
                target,
                database,
                outcomes,
                workers=args.workers,
                checkpoint=checkpoint,
                observers={"task.orchestration_execution": tasks.execute_graph},
            )
            if ({"all", "protocol"} & selected_groups) and not args.case and not args.rerun:
                if engine_failure:
                    outcomes.append(
                        Outcome(
                            "mysql_cli.engine_health", "protocol", "BLOCKED", detail=engine_failure
                        )
                    )
                else:
                    outcomes.extend(await compatibility_cases(target, database, stages.stages))
            if args.suite == "all" and not args.case and not args.rerun:
                mapped_logical = {key for case in cases for key in case.logical_overloads} | {
                    key for case in exclusions for key in case["logical_overloads"]
                }
                for entry in document["engine_logical_overloads"]:
                    if entry["key"] not in mapped_logical and not entry.get("exclusion"):
                        outcomes.append(
                            Outcome(
                                "uncovered.engine." + entry["key"],
                                "coverage",
                                "BLOCKED",
                                detail=entry["signature"]
                                + " requires an executable user-path oracle",
                                logical_overloads=[entry["key"]],
                            )
                        )
                for entry in document["scalar_logical_overloads"]:
                    if (
                        entry["advertised_name"] and entry["key"] not in mapped_logical
                        and not entry.get("exclusion")
                    ):
                        outcomes.append(
                            Outcome(
                                "uncovered.logical." + entry["key"],
                                "coverage",
                                "BLOCKED",
                                detail=entry["signature"],
                                logical_overloads=[entry["key"]],
                            )
                        )
                mapped = {key for case in cases for key in case.signatures} | {
                    key for case in exclusions for key in case["signatures"]
                }
                for function in functions:
                    if function.key not in mapped and not public_exclusion(function):
                        outcomes.append(
                            Outcome(
                                "uncovered.function." + function.key,
                                "coverage",
                                "BLOCKED",
                                detail=function.signature,
                                signatures=[function.key],
                            )
                        )
                mapped_rules = {rule for case in cases for rule in case.statement_rules} | {
                    rule for case in exclusions for rule in case["statement_rules"]
                }
                for rule in document["statement_rules"]:
                    if rule not in mapped_rules:
                        outcomes.append(
                            Outcome(
                                "uncovered.statement." + rule,
                                "coverage",
                                "BLOCKED",
                                detail="No executable contract case",
                                statement_rules=[rule],
                            )
                        )
    except asyncio.CancelledError:
        outcomes.append(
            Outcome(
                "run.interrupted",
                "environment",
                "BLOCKED",
                detail="Run interrupted; completed results preserved",
            )
        )
    except Exception as exc:
        outcomes.append(
            Outcome(
                "run.preflight",
                "environment",
                "BLOCKED",
                detail=target.safe(f"{type(exc).__name__}: {exc}"),
            )
        )
    finally:
        for fixture_name, fixtures in (("tasks", tasks), ("models", models)):
            if (fixture_name == "tasks" and not tasks.selected) or (
                fixture_name == "models" and not models.models
            ):
                continue
            try:
                await fixtures.close()
                outcomes.append(Outcome("cleanup." + fixture_name, "cleanup", "PASS"))
            except Exception as exc:
                outcomes.append(
                    Outcome(
                        "cleanup." + fixture_name, "cleanup", "FAIL", detail=target.failure(exc)
                    )
                )
        metadata["stage_fixtures"] = [
            {key: stage[key] for key in ("id", "name", "database_name")} for stage in stages.stages
        ]
        if stages.stages:
            try:
                await stages.close()
                outcomes.append(Outcome("cleanup.stages", "cleanup", "PASS"))
            except Exception as exc:
                outcomes.append(
                    Outcome("cleanup.stages", "cleanup", "FAIL", detail=target.failure(exc))
                )
        if created:
            try:
                await api.sql(f"DROP DATABASE IF EXISTS `{database}` FORCE", confirm=True)
                connection = await target.connect()
                try:
                    async with connection.cursor() as cursor:
                        await asyncio.wait_for(
                            cursor.execute(f"SHOW DATABASES LIKE '{database}'"), 20
                        )
                        assert not await cursor.fetchall(), "Fixture database remains"
                finally:
                    connection.close()
                metadata["cleanup_verified"] = not any(
                    item.group == "cleanup" and item.status != "PASS" for item in outcomes
                )
                outcomes.append(Outcome("cleanup.database", "cleanup", "PASS"))
            except Exception as exc:
                outcomes.append(
                    Outcome("cleanup.database", "cleanup", "FAIL", detail=target.failure(exc))
                )
        try:
            await api.close()
        except Exception as exc:
            metadata["cleanup_verified"] = False
            outcomes.append(
                Outcome("cleanup.api_session", "cleanup", "FAIL", detail=target.failure(exc))
            )
        metadata["final_source_snapshot"] = source_snapshot()
        if metadata["final_source_snapshot"] != metadata["source_snapshot"]:
            outcomes.append(
                Outcome(
                    "run.source_changed",
                    "environment",
                    "BLOCKED",
                    detail="Source or configuration files changed during this run; rerun required",
                )
            )
        summary = write_reports(directory, outcomes, document, metadata)
        fixture_checkpoint()
    print(json.dumps(summary, indent=2))
    print(f"Reports: {directory}")
    return 0 if summary["status"] == "PASS" else 1


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify Nova SQL over the public MySQL protocol")
    parser.add_argument("--target", choices=["local"], default="local")
    parser.add_argument("--host", default=os.getenv("NOVA_SQL_TEST_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("NOVA_SQL_TEST_PORT", "4406")))
    parser.add_argument("--user", default=os.getenv("NOVA_SQL_TEST_USER", "nova_admin"))
    parser.add_argument("--role", default=os.getenv("NOVA_SQL_TEST_ROLE", "ACCOUNTADMIN"))
    parser.add_argument(
        "--api-url", default=os.getenv("NOVA_SQL_TEST_API_URL", "http://127.0.0.1:8000")
    )
    parser.add_argument("--suite", default="all")
    parser.add_argument(
        "--ml-mode", choices=["interactive", "balanced", "best"], default="balanced"
    )
    parser.add_argument("--storage-connection", default="production")
    parser.add_argument(
        "--configure-ai", action="store_true", help="Persist the approved Kenari aliases"
    )
    parser.add_argument("--ai-ledger", default="/tmp/nova-functional-sql/ai-budget.json")
    parser.add_argument(
        "--strict", action="store_true", help="Failures and blockers always exit nonzero"
    )
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--record-baseline", action="store_true")
    parser.add_argument("--case", action="append", help="Case ID glob; repeat to select more cases")
    parser.add_argument("--exclude-group", action="append", default=[])
    parser.add_argument("--exclusion-reason", help="User-authorized reason recorded in the report")
    parser.add_argument("--rerun", help="Previous report.json; rerun its failed executable cases")
    parser.add_argument("--cleanup-fixtures", help="Recover only fixtures owned by a run journal")
    parser.add_argument("--report-dir")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--planner-timeout-ms", type=int)
    parser.add_argument(
        "--isolated-stack",
        action="store_true",
        help="Enable aggregate-state trials only on a disposable engine",
    )
    return parser


def main() -> int:
    return asyncio.run(run(argument_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
