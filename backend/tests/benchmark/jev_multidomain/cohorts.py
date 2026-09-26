"""Supplemental cohort joins, frozen separately before their first inference."""

from __future__ import annotations

import hashlib
import json

from tests.benchmark.jev_multidomain.data import DATABASE
from tests.benchmark.jev_multidomain.environment import ARTIFACTS, authenticated_user


def document() -> dict:
    prefix = f"""WITH earned AS (
SELECT customer_id, SUM(recognized_revenue) AS earned_revenue,
SUM(recognized_revenue)-SUM(cogs) AS gross_profit
FROM {DATABASE}.revenue
WHERE transaction_date >= '2025-07-01' AND transaction_date < '2025-10-01'
GROUP BY customer_id), acquired AS (
SELECT customer_id, campaign_id, acquisition_channel, cac
FROM {DATABASE}.acquisition
WHERE first_purchase_date >= '2025-07-01' AND first_purchase_date < '2025-10-01')
"""
    definitions = [
        (
            "Berapa recognized revenue Q3 2025 dari pelanggan yang first "
            "purchase-nya juga di Q3 2025, per campaign asal akuisisinya?",
            "cohort_recognized_revenue",
            prefix + "SELECT a.campaign_id, SUM(e.earned_revenue) AS "
            "cohort_recognized_revenue FROM acquired a JOIN earned e ON "
            "a.customer_id=e.customer_id GROUP BY a.campaign_id ORDER BY "
            "a.campaign_id",
        ),
        (
            "Untuk pelanggan yang pertama membeli pada Q3 2025, hitung gross "
            "profit transaksi Q3 2025 per campaign yang mengakuisisinya.",
            "cohort_gross_profit",
            prefix + "SELECT a.campaign_id, SUM(e.gross_profit) AS cohort_gross_profit "
            "FROM acquired a JOIN earned e ON a.customer_id=e.customer_id "
            "GROUP BY a.campaign_id ORDER BY a.campaign_id",
        ),
        (
            "Untuk cohort pelanggan baru Q3 2025, hitung gross profit Q3 "
            "dikurangi CAC masing-masing customer satu kali, per campaign "
            "akuisisi. Jangan sebut hasilnya net profit perusahaan.",
            "cohort_contribution_after_cac",
            prefix + "SELECT a.campaign_id, SUM(COALESCE(e.gross_profit,0))-SUM(a.cac) "
            "AS cohort_contribution_after_cac FROM acquired a LEFT JOIN "
            "earned e ON a.customer_id=e.customer_id GROUP BY a.campaign_id "
            "ORDER BY a.campaign_id",
        ),
        (
            "Berapa recognized lifetime customer revenue sampai akhir 2025 "
            "untuk pelanggan yang first purchase pada Q3 2025, dikelompokkan "
            "menurut acquisition channel?",
            "cohort_lifetime_revenue",
            prefix + "SELECT a.acquisition_channel, SUM(f.lifetime_revenue) AS "
            "cohort_lifetime_revenue FROM acquired a JOIN "
            f"{DATABASE}.customer_finance f ON a.customer_id=f.customer_id "
            "GROUP BY a.acquisition_channel ORDER BY a.acquisition_channel",
        ),
        (
            "Kelompokkan total piutang lewat jatuh tempo snapshot akhir 2025 "
            "menurut acquisition channel pelanggan, dengan cohort first "
            "purchase Q3 2025.",
            "cohort_overdue_amount",
            prefix + "SELECT a.acquisition_channel, SUM(f.overdue_amount) AS cohort_overdue_amount "
            f"FROM acquired a JOIN {DATABASE}.customer_finance f ON a.customer_id=f.customer_id "
            "GROUP BY a.acquisition_channel ORDER BY a.acquisition_channel",
        ),
        (
            "Sandingkan attributed revenue iklan Q3 2025 dengan recognized "
            "revenue Q3 dari customer yang first purchase Q3, per campaign "
            "akuisisi. Jangan gandakan nilai akibat join dan jelaskan kenapa "
            "dua angka tidak harus sama.",
            "cohort_revenue_comparison",
            prefix + ", media AS (SELECT campaign_id,SUM(attributed_revenue) AS attributed_revenue "
            f"FROM {DATABASE}.ad_performance WHERE event_date >= '2025-07-01' "
            "AND event_date < '2025-10-01' GROUP BY campaign_id), accounting AS "
            "(SELECT a.campaign_id,SUM(COALESCE(e.earned_revenue,0)) AS cohort_recognized_revenue "
            "FROM acquired a LEFT JOIN earned e ON a.customer_id=e.customer_id "
            "GROUP BY a.campaign_id) SELECT m.campaign_id,m.attributed_revenue,"
            "COALESCE(a.cohort_recognized_revenue,0) AS cohort_recognized_revenue "
            "FROM media m LEFT JOIN accounting a ON m.campaign_id=a.campaign_id "
            "ORDER BY m.campaign_id",
        ),
    ]
    cases = [
        {
            "id": f"cohort-{i + 1:03}",
            "question": question,
            "category": "cohort_join",
            "expected": "FINANCE + MARKETING",
            "intent": metric,
            "difficulty": "hard",
            "metrics": [metric],
            "period": "2025-Q3",
            "group_by": None,
            "region": None,
            "history": [],
            "allowed_alternatives": [],
            "group": f"cohort-{i + 1:03}",
            "split": "supplemental",
            "oracle_sql": sql,
        }
        for i, (question, metric, sql) in enumerate(definitions)
    ]
    digest = hashlib.sha256(
        json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return {"version": "1.0", "sha256": digest, "cases": cases}


async def prepare_cohorts() -> dict:
    from app.modules.query.service import query_service

    frozen = document()
    path = ARTIFACTS / "cohort-ground-truth.json"
    if path.exists() and json.loads(path.read_text()) != frozen:
        raise RuntimeError("Supplemental cohort questions have changed after freezing")
    path.write_text(json.dumps(frozen, ensure_ascii=False, indent=2))
    user = await authenticated_user()
    answers = {}
    for case in frozen["cases"]:
        result = await query_service.execute(
            case["oracle_sql"],
            username=user["username"],
            encrypted_password=user["encrypted_password"],
            role=user["active_role"],
            session_id=user["session_id"],
            database=DATABASE,
            max_rows=100,
        )
        if result.error:
            raise RuntimeError("Supplemental oracle query failed")
        answers[case["id"]] = [
            {
                "metric": case["metrics"][0],
                "sql": case["oracle_sql"],
                "columns": result.columns,
                "rows": result.rows,
            }
        ]
    (ARTIFACTS / "cohort-oracle.json").write_text(
        json.dumps({"cases": answers}, default=str, indent=2)
    )
    return {"cohort_cases": len(answers), "sha256": frozen["sha256"]}
