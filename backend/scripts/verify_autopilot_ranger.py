"""MV rewrite acceptance on the explicitly selected isolated Ranger fixture."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_ranger_e2e import _inherit_pid_one_environment  # noqa: E402


async def run(output: Path) -> dict:
    _inherit_pid_one_environment()
    from app.core.config import settings
    from app.core.database import db
    from app.modules.query.service import QueryService
    from app.modules.query_autopilot.correctness import equivalent, prove_result
    from app.modules.query_autopilot.models import Scope
    from app.modules.query_autopilot.runtime import (
        AuthorizationUnavailable,
        AuthorizedSQL,
        current_policy_revision,
        validate_policy_revision,
    )
    from app.modules.query_autopilot.telemetry import purpose
    from app.sql_frontend.autopilot import materialized_view_definition

    if os.getenv("NOVA_AUTOPILOT_FIXTURE_STACK") != "1" or not settings.RANGER_ENABLED:
        raise ValueError("Explicit isolated patched-FE Ranger fixture required")
    await db.init_system_pool()
    service = QueryService()
    cases = []
    password = os.environ["NOVA_ADMIN_TEST_PASSWORD"]
    revision = await current_policy_revision()
    if revision is None:
        raise ValueError("fixture_policy_revision_unavailable")
    revision_scope = Scope(
        principal="alice", active_role="marketing", security_context_version=1,
        database="analytics", policy_revision=revision,
    )
    await validate_policy_revision(revision_scope)
    async with db.user_conn("alice", "NovaAlice2026!") as connection:
        try:
            await AuthorizedSQL().execute(
                "SELECT COUNT(*) FROM sales",
                revision_scope.model_copy(update={"policy_revision": revision + ":stale"}),
                connection=connection, category="experiment",
            )
        except AuthorizationUnavailable as exc:
            if str(exc) != "ranger_policy_revision_changed":
                raise
        else:
            raise ValueError("stale_policy_revision_was_not_blocked")

    async def execute(connection, principal, role, statement, *, confirmed=False):
        with purpose("experiment"):
            result = await service.execute(
                statement,
                principal,
                "",
                database="analytics",
                role=role,
                connection=connection,
                max_rows=10000,
                confirm_destructive=confirmed,
            )
        if not result.success or result.truncated:
            raise ValueError("fixture_execution_failed_or_truncated")
        return result

    def proof(result):
        return prove_result(
            result.rows,
            result.column_types,
            ordered=False,
            max_rows=10000,
            max_bytes=1000000,
            truncated=result.truncated,
        )

    specifications = (
        ("row_filter", "SELECT SUM(amount) AS total FROM sales GROUP BY city", True, True),
        ("mask", "SELECT phone,COUNT(*) AS n FROM customers GROUP BY phone", True, True),
        (
            "inner_join_missing_filter_column_negative",
            "SELECT c.phone,SUM(s.amount) AS total FROM sales s "
            "INNER JOIN customers c ON s.city=c.city GROUP BY c.phone",
            False,
            True,
        ),
        (
            "inner_join_varchar_metadata_change_rejected",
            "SELECT s.city,c.phone,SUM(s.amount) AS total FROM sales s "
            "INNER JOIN customers c ON s.city=c.city GROUP BY s.city,c.phone",
            True,
            False,
        ),
        (
            "inner_join_retained_grouping_filter_and_mask",
            "SELECT c.phone,SUM(s.amount) AS total "
            "FROM sales s INNER JOIN customers c ON s.city=c.city "
            "GROUP BY s.city,c.phone",
            True,
            True,
        ),
    )
    try:
        async with db.user_conn("nova_admin", password) as admin:
            for label, statement, expected_rewrite, expected_equivalence in specifications:
                name = "nova_ap_accept_" + uuid4().hex[:16]
                before = {}
                before_rows = {}
                for principal, credential in (("alice", "NovaAlice2026!"), ("bob", "NovaBob2026!")):
                    async with db.user_conn(principal, credential) as conn:
                        await execute(
                            conn,
                            principal,
                            "marketing",
                            "SET enable_materialized_view_rewrite=false",
                        )
                        initial = await execute(conn, principal, "marketing", statement)
                        before[principal] = proof(initial)
                        before_rows[principal] = initial.rows
                created = False
                record = {
                    "case": label,
                    "status": "FAIL",
                    "principals": [],
                    "expected_rewrite": expected_rewrite,
                    "expected_typed_equivalence": expected_equivalence,
                }
                try:
                    await execute(
                        admin,
                        "nova_admin",
                        "ACCOUNTADMIN",
                        f"CREATE MATERIALIZED VIEW `{name}` DISTRIBUTED BY RANDOM "
                        "REFRESH MANUAL PROPERTIES('replication_num'='1') AS "
                        + materialized_view_definition(statement),
                    )
                    created = True
                    await execute(
                        admin,
                        "nova_admin",
                        "ACCOUNTADMIN",
                        f"REFRESH MATERIALIZED VIEW `{name}` WITH SYNC MODE",
                    )
                    for principal, credential in (
                        ("alice", "NovaAlice2026!"),
                        ("bob", "NovaBob2026!"),
                    ):
                        async with db.user_conn(principal, credential) as conn:
                            await execute(
                                conn,
                                principal,
                                "marketing",
                                "SET enable_materialized_view_rewrite=true",
                            )
                            plan = await execute(
                                conn, principal, "marketing", "EXPLAIN " + statement
                            )
                            result = await execute(conn, principal, "marketing", statement)
                            matches = equivalent(before[principal], proof(result))
                            rewrite = name in "\n".join(str(v) for row in plan.rows for v in row)
                            record["principals"].append(
                                {
                                    "principal": principal,
                                    "active_role": "marketing",
                                    "result_equivalent": matches,
                                    "candidate_accepted": matches is True and rewrite,
                                    "values_equal": before_rows[principal] == result.rows,
                                    "before_types": before[principal].types,
                                    "after_types": result.column_types,
                                    "actual_rewrite": rewrite,
                                    "query_ids": result.engine_query_ids,
                                    "row_count": result.row_count,
                                }
                            )
                    record["status"] = (
                        "FAIL"
                        if any(
                            p["result_equivalent"] is not expected_equivalence
                            or p["values_equal"] is not True
                            for p in record["principals"]
                        )
                        else "PASS"
                        if all(
                            p["actual_rewrite"] == expected_rewrite for p in record["principals"]
                        )
                        else "INCONCLUSIVE"
                    )
                    if record["status"] == "INCONCLUSIVE":
                        record["reason"] = "ranger_safe_rewrite_not_observed"
                except Exception as exc:
                    record["error_type"] = type(exc).__name__
                finally:
                    if created:
                        await execute(
                            admin,
                            "nova_admin",
                            "ACCOUNTADMIN",
                            f"DROP MATERIALIZED VIEW `{name}`",
                            confirmed=True,
                        )
                        record["fixture_object_removed"] = True
                cases.append(record)
    finally:
        await db.close_system_pool()
    report = {
        "evidence_kind": "isolated_patched_fe_measurements",
        "cases": cases,
        "status": "PASS" if all(c["status"] == "PASS" for c in cases) else "FAIL",
        "production_actions": 0,
        "policy_revision": {
            "control_plane_revision": revision,
            "matching_scope": "PASS", "stale_scope_blocked_before_sql": "PASS",
            "fe_propagation": "verified_separately_by_filter_and_mask_cases",
        },
        "enrollment_acceptance": "not_issued_unrelated_to_production_snapshot",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "ranger-mv.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "ranger-mv.md").write_text(
        "# Patched-FE MV acceptance\n\n"
        + "\n".join(f"- {c['case']}: {c['status']}" for c in cases)
        + "\n\nNo production action or production-snapshot proof was issued.\n"
    )
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.output))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
