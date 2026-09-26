"""Seeded synthetic operating data. Amounts are IDR; dates cover 2024 and 2025."""

from __future__ import annotations

import calendar
import math
import random
from datetime import date, timedelta

SEED = 20260925
DATABASE = "NOVA_JEV_BENCH_20260925"
VERSION = "1.0"

SCHEMAS = {
    "customers": "customer_id INT, customer_name VARCHAR(80), region VARCHAR(30), "
    "city VARCHAR(40), segment VARCHAR(30), business_unit VARCHAR(30)",
    "products": "product_id INT, product_name VARCHAR(80), category VARCHAR(30), "
    "product_family VARCHAR(30)",
    "revenue": "invoice_id INT, customer_id INT, product_id INT, region "
    "VARCHAR(30), channel VARCHAR(30), gross_revenue DOUBLE, discount "
    "DOUBLE, net_revenue DOUBLE, tax DOUBLE, recognized_revenue "
    "DOUBLE, deferred_revenue DOUBLE, cogs DOUBLE, transaction_date "
    "DATE, posting_date DATE, currency VARCHAR(8), status VARCHAR(20)",
    "ledger": "transaction_id INT, transaction_date DATE, posting_date DATE, "
    "account_id INT, account_name VARCHAR(80), account_type "
    "VARCHAR(30), cost_center VARCHAR(30), business_unit VARCHAR(30), "
    "department VARCHAR(30), currency VARCHAR(8), debit DOUBLE, "
    "credit DOUBLE, amount DOUBLE, fiscal_year INT, fiscal_month INT",
    "expenses": "expense_id INT, expense_category VARCHAR(40), department "
    "VARCHAR(30), cost_center VARCHAR(30), business_unit VARCHAR(30), "
    "region VARCHAR(30), vendor VARCHAR(50), amount DOUBLE, "
    "budget_category VARCHAR(30), recurring_flag BOOLEAN, "
    "transaction_date DATE",
    "budget_forecast": "budget_id INT, fiscal_period DATE, business_unit VARCHAR(30), "
    "department VARCHAR(30), account VARCHAR(40), budget DOUBLE, "
    "forecast DOUBLE, actual DOUBLE, variance DOUBLE, "
    "variance_percent DOUBLE",
    "cash_flow": "cash_id INT, transaction_date DATE, region VARCHAR(30), "
    "cash_inflow DOUBLE, cash_outflow DOUBLE, operating_cashflow "
    "DOUBLE, investing_cashflow DOUBLE, financing_cashflow DOUBLE, "
    "cash_balance DOUBLE",
    "customer_finance": "customer_id INT, lifetime_revenue DOUBLE, outstanding_invoice "
    "DOUBLE, overdue_amount DOUBLE, payment_term INT, payment_delay "
    "INT, credit_limit DOUBLE",
    "finance_monthly": "month_id INT, fiscal_period DATE, region VARCHAR(30), "
    "recognized_revenue DOUBLE, cogs DOUBLE, opex DOUBLE, "
    "operating_profit DOUBLE",
    "campaigns": "campaign_id INT, campaign_name VARCHAR(80), campaign_type "
    "VARCHAR(30), campaign_objective VARCHAR(40), start_date DATE, "
    "end_date DATE, budget DOUBLE, channel VARCHAR(30), "
    "audience_segment VARCHAR(30), attribution_model VARCHAR(40)",
    "ad_performance": "performance_id INT, campaign_id INT, ad_id INT, event_date DATE, "
    "region VARCHAR(30), product_id INT, impressions INT, clicks INT, "
    "ctr DOUBLE, cpc DOUBLE, cpm DOUBLE, spend DOUBLE, conversions "
    "INT, conversion_rate DOUBLE, attributed_revenue DOUBLE, roas "
    "DOUBLE, attribution_ambiguous BOOLEAN",
    "leads": "lead_id INT, source VARCHAR(30), campaign_id INT, segment "
    "VARCHAR(30), geography VARCHAR(30), acquisition_date DATE, mql "
    "BOOLEAN, sales_qualified BOOLEAN, converted BOOLEAN, "
    "conversion_date DATE",
    "acquisition": "customer_id INT, acquisition_channel VARCHAR(30), campaign_id "
    "INT, cac DOUBLE, first_purchase_date DATE, first_purchase_value "
    "DOUBLE",
    "funnel": "funnel_id INT, campaign_id INT, event_date DATE, region "
    "VARCHAR(30), visitors INT, signups INT, leads INT, mql INT, "
    "sales_qualified INT, opportunities INT, customers INT",
    "engagement": "engagement_id INT, campaign_id INT, event_date DATE, sessions "
    "INT, page_views INT, bounce_rate DOUBLE, email_open_rate DOUBLE, "
    "email_click_rate DOUBLE, social_engagement INT",
}


def generate() -> dict[str, list[tuple]]:
    rng = random.Random(SEED)
    rows: dict[str, list[tuple]] = {name: [] for name in SCHEMAS}
    regions = ["West", "Central", "East", "North"]
    channels = ["Paid Search", "Paid Social", "Email", "Affiliate"]
    departments = ["Operations", "Engineering", "Sales", "Marketing"]
    for customer in range(1, 601):
        region = regions[customer % 4]
        rows["customers"].append(
            (
                customer,
                f"Synthetic account {customer:04}",
                region,
                f"{region} City {customer % 3 + 1}",
                "Enterprise" if customer <= 30 else "SMB",
                "Cloud" if customer % 2 else "Services",
            )
        )
        campaign = (customer % 24) + 1
        acquired = date(2024, 1, 1) + timedelta(days=rng.randrange(630))
        rows["acquisition"].append(
            (
                customer,
                channels[(campaign - 1) % 4],
                campaign,
                round(rng.uniform(250000, 3400000), 2),
                acquired,
                round(rng.uniform(200000, 8000000), 2),
            )
        )
    for product in range(1, 13):
        rows["products"].append(
            (
                product,
                f"Synthetic product {product}",
                ["Platform", "Support", "Training"][product % 3],
                "Recurring" if product % 3 else "One-off",
            )
        )
    for campaign in range(1, 25):
        rows["campaigns"].append(
            (
                campaign,
                f"Program {campaign:02}",
                "Seasonal" if campaign % 6 == 0 else "Always-on",
                "Acquisition" if campaign % 2 else "Retention",
                date(2024, 1, 1),
                date(2025, 12, 31),
                float(8e7 + campaign * 3e6),
                channels[(campaign - 1) % 4],
                "Enterprise" if campaign % 3 else "SMB",
                "Multi-touch" if campaign % 5 == 0 else "Last-touch",
            )
        )
    lifetime = {customer: 0.0 for customer in range(1, 601)}
    monthly_expenses: dict[tuple[int, int, str], float] = {}
    invoice = expense = posting = 0
    for year in [2024, 2025]:
        for month in range(1, 13):
            seasonal = 1 + 0.2 * math.sin(month * math.pi / 6) + (0.4 if month == 11 else 0)
            for _ in range(650):
                invoice += 1
                customer = rng.randint(1, 30) if rng.random() < 0.38 else rng.randint(31, 600)
                when = date(year, month, rng.randint(1, calendar.monthrange(year, month)[1]))
                region = regions[customer % 4]
                gross = rng.uniform(2e5, 8e6) * seasonal * (1.12 if year == 2025 else 1)
                gross *= 1.8 if customer <= 30 else 1
                if region == "East" and year == 2025 and month >= 7:
                    gross *= 0.7
                discount = gross * rng.uniform(0, 0.22)
                net = gross - discount
                if invoice % 71 == 0:
                    net *= -0.4
                recognized = net * (0.72 if invoice % 5 == 0 else 1)
                cogs = net * rng.uniform(0.35, 0.75)
                values = [
                    round(v, 2)
                    for v in [gross, discount, net, net * 0.11, recognized, net - recognized, cogs]
                ]
                rows["revenue"].append(
                    (
                        invoice,
                        customer,
                        rng.randint(1, 12),
                        region,
                        channels[customer % 4],
                        *values,
                        when,
                        when + timedelta(days=12 if invoice % 53 == 0 else 2),
                        "IDR",
                        "Credit note" if net < 0 else "Posted",
                    )
                )
                lifetime[customer] += recognized
                for account, name, debit, credit in [
                    (1100, "Accounts receivable", net, 0),
                    (4100, "Recognized revenue", 0, recognized),
                    (2100, "Deferred revenue", 0, net - recognized),
                ]:
                    posting += 1
                    rows["ledger"].append(
                        (
                            posting,
                            when,
                            when + timedelta(days=2),
                            account,
                            name,
                            "Asset"
                            if account == 1100
                            else "Revenue"
                            if account == 4100
                            else "Liability",
                            "CC-Sales",
                            "Cloud",
                            "Sales",
                            "IDR",
                            round(debit, 2),
                            round(credit, 2),
                            round(credit - debit, 2),
                            year,
                            month,
                        )
                    )
            for department in departments:
                amounts = []
                for index in range(35):
                    expense += 1
                    amount = rng.uniform(2e6, 9e6) * (1.1 if year == 2025 else 1)
                    if department == "Engineering" and year == 2025 and month in [8, 9]:
                        amount *= 1.65
                    if department == "Marketing" and month == 11:
                        amount *= 1.7
                    if expense % 197 == 0:
                        amount *= 5
                    amount = round(amount, 2)
                    amounts.append(amount)
                    rows["expenses"].append(
                        (
                            expense,
                            ["Payroll", "Services", "Media", "Travel"][index % 4],
                            department,
                            f"CC-{department}",
                            "Cloud",
                            regions[index % 4],
                            f"Synthetic vendor {index % 9}",
                            amount,
                            "OPEX",
                            index % 3 != 0,
                            date(year, month, min(index % 28 + 1, 28)),
                        )
                    )
                actual = round(sum(amounts), 2)
                budget = 2.1e8 * (1.08 if year == 2025 else 1)
                forecast = budget * (1.12 if department == "Engineering" else 1.02)
                key = len(rows["budget_forecast"]) + 1
                rows["budget_forecast"].append(
                    (
                        key,
                        date(year, month, 1),
                        "Cloud",
                        department,
                        "OPEX",
                        budget,
                        forecast,
                        actual,
                        actual - budget,
                        (actual - budget) / budget * 100,
                    )
                )
                monthly_expenses[(year, month, department)] = actual
            for region in regions:
                total = sum(
                    r[9]
                    for r in rows["revenue"]
                    if r[3] == region and r[12].year == year and r[12].month == month
                )
                cogs = sum(
                    r[11]
                    for r in rows["revenue"]
                    if r[3] == region and r[12].year == year and r[12].month == month
                )
                opex = sum(
                    r[7]
                    for r in rows["expenses"]
                    if r[5] == region and r[10].year == year and r[10].month == month
                )
                rows["finance_monthly"].append(
                    (
                        len(rows["finance_monthly"]) + 1,
                        date(year, month, 1),
                        region,
                        round(total, 2),
                        round(cogs, 2),
                        round(opex, 2),
                        round(total - cogs - opex, 2),
                    )
                )
                inflow = total * rng.uniform(0.75, 0.99)
                outflow = sum(monthly_expenses[(year, month, d)] for d in departments) / 4
                outflow += total * 0.35
                cash = [inflow, outflow, inflow - outflow, -1.8e7, 5e6, 4e8 + inflow - outflow]
                rows["cash_flow"].append(
                    (
                        len(rows["cash_flow"]) + 1,
                        date(year, month, 1),
                        region,
                        *[round(v, 2) for v in cash],
                    )
                )
    for customer, total in lifetime.items():
        overdue = total * (0.18 if customer % 17 == 0 else 0.01)
        rows["customer_finance"].append(
            (
                customer,
                round(total, 2),
                round(total * 0.2, 2),
                round(overdue, 2),
                30 if customer % 4 else 60,
                48 if customer % 17 == 0 else rng.randrange(12),
                1.5e8,
            )
        )
    when = date(2024, 1, 1)
    while when <= date(2025, 12, 31):
        for campaign in range(1, 25):
            seasonal = 1.65 if when.month in [6, 11] and campaign % 6 == 0 else 1
            impressions = int(rng.uniform(3000, 20000) * seasonal)
            clicks = int(impressions * rng.uniform(0.005, 0.06))
            conversion = 0.006 if campaign % 7 == 0 else rng.uniform(0.025, 0.12)
            converted = int(clicks * conversion)
            spend = round(clicks * rng.uniform(1500, 16000) * (1.8 if campaign % 5 == 0 else 1), 2)
            attributed = round(converted * rng.uniform(2e5, 2e6), 2)
            if campaign % 5 == 0:
                attributed *= 1.6
            if when.year == 2025 and when.month >= 7 and campaign in [3, 7]:
                attributed *= 0.6
            ident = len(rows["ad_performance"]) + 1
            rows["ad_performance"].append(
                (
                    ident,
                    campaign,
                    campaign * 10 + when.day % 3,
                    when,
                    regions[campaign % 4],
                    campaign % 12 + 1,
                    impressions,
                    clicks,
                    clicks / impressions,
                    spend / max(clicks, 1),
                    spend / impressions * 1000,
                    spend,
                    converted,
                    converted / max(clicks, 1),
                    attributed,
                    attributed / max(spend, 1),
                    campaign % 5 == 0,
                )
            )
            mql = int(converted * (0.2 if campaign % 7 == 0 else 0.8))
            sql = int(mql * 0.55)
            rows["funnel"].append(
                (
                    ident,
                    campaign,
                    when,
                    regions[campaign % 4],
                    clicks,
                    int(clicks * 0.2),
                    converted,
                    mql,
                    sql,
                    int(sql * 0.7),
                    int(sql * 0.45),
                )
            )
            rows["engagement"].append(
                (
                    ident,
                    campaign,
                    when,
                    clicks,
                    int(clicks * 2.4),
                    0.85 if campaign % 7 == 0 else rng.uniform(0.2, 0.55),
                    rng.uniform(0.1, 0.5),
                    rng.uniform(0.01, 0.12),
                    int(clicks * 0.14),
                )
            )
            for _ in range(2):
                is_mql = rng.random() < (0.2 if campaign % 7 == 0 else 0.75)
                is_sql = is_mql and rng.random() < 0.5
                won = is_sql and rng.random() < 0.4
                rows["leads"].append(
                    (
                        len(rows["leads"]) + 1,
                        channels[(campaign - 1) % 4],
                        campaign,
                        "Enterprise" if campaign % 3 else "SMB",
                        regions[campaign % 4],
                        when,
                        is_mql,
                        is_sql,
                        won,
                        when + timedelta(days=7) if won else None,
                    )
                )
        when += timedelta(days=1)
    return rows
