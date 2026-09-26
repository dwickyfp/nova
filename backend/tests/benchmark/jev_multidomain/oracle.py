"""Independent SQL answers; labels and results never enter participant prompts."""

from __future__ import annotations

import json
import math

from tests.benchmark.jev_multidomain.catalog import METRICS
from tests.benchmark.jev_multidomain.data import DATABASE
from tests.benchmark.jev_multidomain.environment import ARTIFACTS, authenticated_user

DATES = {
    "revenue": "transaction_date",
    "expenses": "transaction_date",
    "budget_forecast": "fiscal_period",
    "finance_monthly": "fiscal_period",
    "cash_flow": "transaction_date",
    "ad_performance": "event_date",
    "leads": "acquisition_date",
    "acquisition": "first_purchase_date",
    "funnel": "event_date",
    "engagement": "event_date",
}


def metric_sql(case: dict, metric: str) -> str:
    _, table, expression, _, _ = METRICS[metric]
    grouping = case.get("group_by")
    columns = (grouping + ", " if grouping else "") + expression + f" AS {metric}"
    sql = f"SELECT {columns} FROM {DATABASE}.{table}"
    filters = []
    if case.get("period") and table in DATES:
        if case["period"] != "2025-Q3":
            raise ValueError("Unsupported oracle period")
        filters.append(f"{DATES[table]} >= '2025-07-01' AND {DATES[table]} < '2025-10-01'")
    if case.get("region"):
        if case["region"] not in {"East", "West", "North", "Central"}:
            raise ValueError("Unsupported oracle region")
        filters.append(f"region = '{case['region']}'")
    if filters:
        sql += " WHERE " + " AND ".join(filters)
    if grouping:
        sql += f" GROUP BY {grouping} ORDER BY {grouping}"
    return sql


async def build_oracle() -> dict:
    from app.modules.query.service import query_service

    user = await authenticated_user()
    ground = json.loads((ARTIFACTS / "ground_truth.json").read_text())
    cache, cases = {}, {}
    for case in ground["cases"]:
        results = []
        for metric in case["metrics"]:
            sql = metric_sql(case, metric)
            if sql not in cache:
                result = await query_service.execute(
                    sql,
                    username=user["username"],
                    encrypted_password=user["encrypted_password"],
                    role=user["active_role"],
                    session_id=user["session_id"],
                    database=DATABASE,
                    max_rows=100,
                )
                if getattr(result, "error", None):
                    raise RuntimeError(f"Oracle query failed for {metric}")
                cache[sql] = {
                    "metric": metric,
                    "sql": sql,
                    "columns": result.columns,
                    "rows": result.rows,
                }
            results.append(cache[sql])
        cases[case["id"]] = results
    document = {
        "ground_truth_sha256": ground["sha256"],
        "unique_queries": len(cache),
        "cases": cases,
    }
    (ARTIFACTS / "oracle.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=str)
    )
    return {"oracle_cases": len(cases), "unique_queries": len(cache)}


def equivalent_rows(actual: list[list], expected: list[list]) -> bool:
    if len(actual) != len(expected):
        return False
    actual = sorted(actual, key=lambda row: str(row[0]) if len(row) > 1 else "")
    expected = sorted(expected, key=lambda row: str(row[0]) if len(row) > 1 else "")
    for left, right in zip(actual, expected, strict=True):
        if len(left) != len(right):
            return False
        for observed, reference in zip(left, right, strict=True):
            if observed == reference:
                continue
            try:
                if math.isclose(float(observed), float(reference), rel_tol=1e-9, abs_tol=1e-6):
                    continue
            except (TypeError, ValueError):
                pass
            return False
    return True


async def verify_semantic_queries() -> dict:
    from app.modules.agents.semantic.compiler import SemanticCompiler
    from app.modules.agents.semantic.ir import SemanticModelIR
    from app.modules.agents.semantic.ossie import parse_ossie
    from app.modules.agents.semantic.planning import SemanticFilter, SemanticPlan
    from app.modules.query.service import query_service

    models = [
        SemanticModelIR.from_ossie(
            parse_ossie((ARTIFACTS / f"{name}.ossie.yaml").read_text()).as_dict()
        )
        for name in ["finance", "marketing"]
    ]
    user = await authenticated_user()
    oracle = json.loads((ARTIFACTS / "oracle.json").read_text())["cases"]
    cases = json.loads((ARTIFACTS / "ground_truth.json").read_text())["cases"]
    seen, checks = set(), []
    for case in cases:
        for gold in oracle[case["id"]]:
            if gold["sql"] in seen:
                continue
            seen.add(gold["sql"])
            model = next(model for model in models if model.metric(gold["metric"]))
            metric = model.metric(gold["metric"])
            filters = []
            if case.get("period") and metric.default_time_dimension:
                filters = [
                    SemanticFilter(metric.default_time_dimension, ">=", "2025-07-01"),
                    SemanticFilter(metric.default_time_dimension, "<", "2025-10-01"),
                ]
            if case.get("region"):
                filters.append(SemanticFilter(f"{metric.base_dataset}.region", "=", case["region"]))
            dimensions = (
                (f"{metric.base_dataset}.{case['group_by']}",) if case.get("group_by") else ()
            )
            compiled = SemanticCompiler().compile(
                model,
                SemanticPlan(metrics=(metric.name,), dimensions=dimensions, filters=tuple(filters)),
            )
            result = await query_service.execute(
                compiled.sql,
                username=user["username"],
                encrypted_password=user["encrypted_password"],
                role=user["active_role"],
                session_id=user["session_id"],
                database=DATABASE,
                max_rows=100,
            )
            correct = result.columns == gold["columns"] and equivalent_rows(
                result.rows, gold["rows"]
            )
            checks.append(
                {
                    "case_id": case["id"],
                    "metric": metric.name,
                    "sql": compiled.sql,
                    "columns": result.columns,
                    "rows": result.rows,
                    "correct": correct,
                }
            )
    report = {"checks": checks, "all_correct": all(item["correct"] for item in checks)}
    (ARTIFACTS / "semantic-oracle-verification.json").write_text(
        json.dumps(report, default=str, indent=2)
    )
    return {"verified_queries": len(checks), "all_correct": report["all_correct"]}
