"""Business metadata independent of benchmark question wording and expected answers."""

from tests.benchmark.jev_multidomain.data import DATABASE, SCHEMAS

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

# name, owning relation, SQL, business definition, aliases
FINANCE_METRICS = [
    (
        "recognized_revenue",
        "revenue",
        "SUM(revenue.recognized_revenue)",
        "Accounting revenue earned in the service period, excluding "
        "deferred obligations and tax. Not advertising attribution.",
        ["recognized revenue", "pendapatan diakui", "revenue", "omzet perusahaan"],
    ),
    (
        "gross_revenue",
        "revenue",
        "SUM(revenue.gross_revenue)",
        "Invoice value before discounts, tax and revenue deferral.",
        ["gross revenue", "pendapatan kotor"],
    ),
    (
        "net_revenue",
        "revenue",
        "SUM(revenue.net_revenue)",
        "Invoice value after discounts and credit notes; distinct from "
        "recognition and attribution.",
        ["net revenue", "pendapatan bersih"],
    ),
    (
        "deferred_revenue",
        "revenue",
        "SUM(revenue.deferred_revenue)",
        "Invoiced value not yet earned; a liability rather than current-period income.",
        ["deferred revenue", "pendapatan ditangguhkan"],
    ),
    (
        "gross_profit",
        "revenue",
        "SUM(revenue.recognized_revenue)-SUM(revenue.cogs)",
        "Recognized revenue less direct cost of goods and service "
        "delivery, before operating expenses.",
        ["gross profit", "laba kotor"],
    ),
    (
        "gross_margin",
        "revenue",
        "100*(SUM(revenue.recognized_revenue)-SUM(revenue.cogs))/NULLIF(SUM(revenue.recognized_revenue),0)",
        "Gross profit as a percentage of recognized revenue. Ratio of "
        "sums, never average row margins.",
        ["gross margin", "margin kotor", "margin"],
    ),
    (
        "opex",
        "expenses",
        "SUM(expenses.amount)",
        "Booked operating expenditure including payroll, services, media "
        "and travel. Not platform-reported ad spend.",
        ["opex", "biaya operasional", "company expenses", "spend"],
    ),
    (
        "operating_profit",
        "finance_monthly",
        "SUM(finance_monthly.operating_profit)",
        "Recognized revenue minus COGS and booked operating expenses, "
        "reconciled monthly by region. No causal attribution to "
        "advertising.",
        ["operating profit", "laba operasional", "profit", "profitability"],
    ),
    (
        "budget",
        "budget_forecast",
        "SUM(budget_forecast.budget)",
        "Approved company operating-expense budget by fiscal month and "
        "department, not campaign budget.",
        ["budget", "anggaran perusahaan"],
    ),
    (
        "actual_expense",
        "budget_forecast",
        "SUM(budget_forecast.actual)",
        "Booked department operating expenditure in the budget comparison.",
        ["actual expense", "realisasi biaya"],
    ),
    (
        "budget_variance",
        "budget_forecast",
        "SUM(budget_forecast.actual)-SUM(budget_forecast.budget)",
        "Actual company expense less budget; positive means overspending, negative underspending.",
        ["budget variance", "selisih anggaran", "overspending"],
    ),
    (
        "budget_variance_percent",
        "budget_forecast",
        "100*(SUM(budget_forecast.actual)-SUM(budget_forecast.budget))/NULLIF(SUM(budget_forecast.budget),0)",
        "Signed expense variance as percent of approved company budget.",
        ["budget variance percent", "persentase selisih anggaran"],
    ),
    (
        "forecast",
        "budget_forecast",
        "SUM(budget_forecast.forecast)",
        "Revised expense expectation for the fiscal period; not a statistical model output.",
        ["forecast", "proyeksi biaya"],
    ),
    (
        "operating_cashflow",
        "cash_flow",
        "SUM(cash_flow.operating_cashflow)",
        "Cash collected less operating cash payments. Does not equal booked profit.",
        ["operating cashflow", "arus kas operasi", "cashflow"],
    ),
    (
        "overdue_amount",
        "customer_finance",
        "SUM(customer_finance.overdue_amount)",
        "Unpaid receivables past contractual due date at the end of 2025; "
        "snapshot is not time additive.",
        ["overdue amount", "piutang lewat jatuh tempo"],
    ),
    (
        "outstanding_invoice",
        "customer_finance",
        "SUM(customer_finance.outstanding_invoice)",
        "Open invoice balance at the end of 2025 including amounts not yet overdue.",
        ["outstanding invoice", "piutang terbuka"],
    ),
    (
        "customer_value",
        "customer_finance",
        "SUM(customer_finance.lifetime_revenue)",
        "Recognized lifetime customer revenue through 2025. Not first "
        "purchase or marketing-attributed revenue.",
        ["customer value", "lifetime revenue", "nilai pelanggan"],
    ),
    (
        "customer_count",
        "revenue",
        "COUNT(DISTINCT revenue.customer_id)",
        "Distinct invoiced customers in the accounting period, including existing customers.",
        ["customer count", "customer", "pelanggan"],
    ),
]

MARKETING_METRICS = [
    (
        "ad_spend",
        "ad_performance",
        "SUM(ad_performance.spend)",
        "Platform-reported media cost. Excludes payroll and other booked "
        "company operating expenses.",
        ["ad spend", "advertising spend", "biaya iklan", "spend"],
    ),
    (
        "attributed_revenue",
        "ad_performance",
        "SUM(ad_performance.attributed_revenue)",
        "Revenue credited to ads under each campaign's attribution model. "
        "Multi-touch can overlap; not recognized company revenue.",
        ["attributed revenue", "attribution revenue", "revenue campaign", "revenue"],
    ),
    (
        "roas",
        "ad_performance",
        "SUM(ad_performance.attributed_revenue)/NULLIF(SUM(ad_performance.spend),0)",
        "Attributed revenue per unit of media cost; excludes COGS and "
        "operating expenses, so not profit or company ROI.",
        ["ROAS", "return on ad spend"],
    ),
    (
        "ctr",
        "ad_performance",
        "100*SUM(ad_performance.clicks)/NULLIF(SUM(ad_performance.impressions),0)",
        "Percent of ad impressions clicked; ratio of totals, not average daily percentages.",
        ["CTR", "click through rate"],
    ),
    (
        "cpc",
        "ad_performance",
        "SUM(ad_performance.spend)/NULLIF(SUM(ad_performance.clicks),0)",
        "Media cost per click, regardless of lead quality or profitability.",
        ["CPC", "cost per click"],
    ),
    (
        "cpm",
        "ad_performance",
        "1000*SUM(ad_performance.spend)/NULLIF(SUM(ad_performance.impressions),0)",
        "Media cost per thousand delivered impressions.",
        ["CPM", "cost per mille"],
    ),
    (
        "conversion_rate",
        "ad_performance",
        "100*SUM(ad_performance.conversions)/NULLIF(SUM(ad_performance.clicks),0)",
        "Percent of ad clicks producing a platform conversion, not the "
        "full lead-to-customer funnel.",
        ["conversion rate", "tingkat konversi"],
    ),
    (
        "conversions",
        "ad_performance",
        "SUM(ad_performance.conversions)",
        "Count of platform conversion events, potentially including repeat customers.",
        ["conversions", "konversi iklan"],
    ),
    (
        "impressions",
        "ad_performance",
        "SUM(ad_performance.impressions)",
        "Ad exposures including repeat impressions to the same person.",
        ["impressions", "tayangan"],
    ),
    (
        "mql_count",
        "leads",
        "SUM(CASE WHEN leads.mql THEN 1 ELSE 0 END)",
        "Marketing-qualified leads meeting the documented profile and engagement criteria.",
        ["MQL", "marketing qualified leads"],
    ),
    (
        "sql_count",
        "leads",
        "SUM(CASE WHEN leads.sales_qualified THEN 1 ELSE 0 END)",
        "Sales-qualified leads handed off by marketing; SQL here is a "
        "funnel stage, not a query language.",
        ["sales qualified leads", "SQL leads"],
    ),
    (
        "cac",
        "acquisition",
        "AVG(acquisition.cac)",
        "Mean allocated acquisition cost per acquired customer; not cost "
        "per click or total company expense.",
        ["CAC", "customer acquisition cost"],
    ),
    (
        "new_customers",
        "acquisition",
        "COUNT(DISTINCT acquisition.customer_id)",
        "Customers whose first purchase occurs in the acquisition period. "
        "Distinct from all invoiced customers.",
        ["new customers", "customer", "pelanggan baru"],
    ),
    (
        "campaign_budget",
        "campaigns",
        "SUM(campaigns.budget)",
        "Whole-campaign lifetime budget; do not sum it across daily "
        "performance rows. Not the company's monthly OPEX budget.",
        ["campaign budget", "budget", "anggaran campaign"],
    ),
    (
        "funnel_conversion",
        "funnel",
        "100*SUM(funnel.customers)/NULLIF(SUM(funnel.visitors),0)",
        "Visitors becoming customers across the full funnel, not ad click conversion.",
        ["funnel conversion", "konversi funnel"],
    ),
    (
        "bounce_rate",
        "engagement",
        "100*SUM(engagement.sessions*engagement.bounce_rate)/NULLIF(SUM(engagement.sessions),0)",
        "Session-weighted bounce percentage; high traffic can coexist with weak engagement.",
        ["bounce rate", "rasio pantulan"],
    ),
    (
        "email_open_rate",
        "engagement",
        "100*AVG(engagement.email_open_rate)",
        "Mean recorded daily email-open percentage; denominator send "
        "counts are not available for weighting.",
        ["email open rate", "tingkat buka email"],
    ),
    (
        "sessions",
        "engagement",
        "SUM(engagement.sessions)",
        "Visits associated with a campaign, not distinct people.",
        ["sessions", "kunjungan"],
    ),
]

METRICS = {m[0]: m for m in FINANCE_METRICS + MARKETING_METRICS}
DOMAIN_TABLES = {
    "FINANCE": [
        "revenue",
        "ledger",
        "expenses",
        "budget_forecast",
        "cash_flow",
        "customer_finance",
        "finance_monthly",
        "customers",
        "products",
    ],
    "MARKETING": [
        "campaigns",
        "ad_performance",
        "leads",
        "acquisition",
        "funnel",
        "engagement",
        "customers",
        "products",
    ],
}
DESCRIPTIONS = {
    "FINANCE": "Accounting and corporate planning specialist. Reconciles earned "
    "revenue, receivables, direct costs, operating profit, department "
    "budgets and cash movements. Distinguishes invoice value, "
    "recognition and cash collection. Explains period, currency and "
    "margin denominators. Consult Marketing for campaign attribution, "
    "acquisition cohorts and media-efficiency evidence. Cannot infer "
    "campaign effectiveness from accounting revenue alone.",
    "MARKETING": "Demand generation and acquisition specialist. Evaluates campaign "
    "exposure, engagement, media efficiency, qualified pipeline, "
    "customer acquisition and attributed revenue. Distinguishes "
    "clicks, qualified leads, first purchases and attribution "
    "windows. Consult Finance for earned revenue, full company costs, "
    "overdue invoices and customer profitability. Advertising return "
    "is not accounting profit or causal lift.",
    "SALES": "Sales operations specialist. Owns opportunity pipeline, seller "
    "activity, quota attainment, bookings stages and deal forecasts. "
    "Consult Marketing for advertising attribution and qualified-lead "
    "generation, Finance for revenue recognition, profit and cash. "
    "Does not own campaign-efficiency or accounting measures.",
}


def expression(sql: str) -> dict:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}


def definition(domain: str) -> dict:
    datasets = []
    for table in DOMAIN_TABLES[domain]:
        fields = []
        for column in SCHEMAS[table].split(", "):
            name, datatype = column.split(" ", 1)
            is_time = datatype == "DATE"
            categorical = is_time or "VARCHAR" in datatype or name.endswith("_id")
            field = {
                "name": name,
                "expression": expression(name),
                "datatype": "Date"
                if is_time
                else "String"
                if "VARCHAR" in datatype
                else "Boolean"
                if datatype == "BOOLEAN"
                else "Decimal",
                "description": name.replace("_", " ") + ("; IDR" if datatype == "DOUBLE" else ""),
            }
            if categorical:
                field["dimension"] = {"is_time": is_time}
            if name in {"transaction_date", "event_date", "acquisition_date", "fiscal_period"}:
                field["ai_context"] = {"synonyms": ["date", "period", "tanggal", "periode"]}
            fields.append(field)
        key = SCHEMAS[table].split()[0]
        datasets.append(
            {
                "name": table,
                "source": f"{DATABASE}.{table}",
                "primary_key": [key],
                "grain": {"keys": [key]},
                "description": f"Synthetic {table.replace('_', ' ')}. "
                f"One row per {key}; dates cover 2024-2025. Monetary values are IDR.",
                "fields": fields,
            }
        )
    relationships = []
    for source, target, column in [
        ("revenue", "customers", "customer_id"),
        ("revenue", "products", "product_id"),
        ("customer_finance", "customers", "customer_id"),
        ("ad_performance", "campaigns", "campaign_id"),
        ("ad_performance", "products", "product_id"),
        ("leads", "campaigns", "campaign_id"),
        ("acquisition", "campaigns", "campaign_id"),
        ("acquisition", "customers", "customer_id"),
        ("funnel", "campaigns", "campaign_id"),
        ("engagement", "campaigns", "campaign_id"),
    ]:
        if source in DOMAIN_TABLES[domain]:
            relationships.append(
                {
                    "name": f"{source}_to_{target}",
                    "from": source,
                    "to": target,
                    "from_columns": [column],
                    "to_columns": [column],
                    "cardinality": "many_to_one",
                }
            )
    metrics = [
        {
            "name": name,
            "expression": expression(sql),
            "datatype": "Decimal",
            "description": description,
            "owner_domain": domain.lower(),
            "authority": "authoritative",
            "ai_context": {"synonyms": synonyms},
            "base_dataset": table,
            **({"default_time_dimension": f"{table}.{DATES[table]}"} if table in DATES else {}),
        }
        for name, table, sql, description, synonyms in (
            FINANCE_METRICS if domain == "FINANCE" else MARKETING_METRICS
        )
    ]
    return {
        "version": "0.1.1",
        "name": f"jev_bench_{domain.lower()}",
        "description": DESCRIPTIONS[domain],
        "datasets": datasets,
        "relationships": relationships,
        "metrics": metrics,
        "query_generation_instructions": (
            "Use the governed metric definition and its base dataset. Use qualified dimension "
            "names when several datasets have the same field name. Preserve the requested "
            "period with >= start and < next-period-start date filters on the metric's default "
            "time dimension. Calendar quarters contain three months. Do not introduce a time "
            "grouping unless requested. Customer receivable and lifetime-value measures are "
            "end-of-2025 snapshots; campaign budget is a campaign-lifetime measure. These have "
            "no transaction-date filter. Region and campaign grouping must use the metric's "
            "base fact dataset where available. Do not average precomputed ratios or join facts "
            "at incompatible grains. Preserve unresolved constraints instead of guessing."
        ),
        "ai_context": {
            "instructions": "Use the metric's business definition and actual time field. "
            "Ratios use totals. "
            "Q3 means July through September of the requested year. Company and campaign "
            "revenue/budget/customer measures have different meanings; clarify unresolved scope. "
            "Data are seeded synthetic benchmark data, not actual company records. "
            "Do not infer causal effects from correlations. Do not join fact "
            "tables at incompatible grains."
        },
    }


def manifest(domain: str) -> dict:
    metrics = (
        FINANCE_METRICS
        if domain == "FINANCE"
        else MARKETING_METRICS
        if domain == "MARKETING"
        else []
    )
    return {
        "available_to_auto": True,
        "delegation_description": DESCRIPTIONS[domain],
        "capability_tags": [domain.lower()],
        "owns": [item[0] for item in metrics],
        "good_for": [item[3] for item in metrics[:6]],
        "consult_when": [
            "Another domain's authoritative evidence is needed to complete a comparison."
        ],
        "not_primary_for": [DESCRIPTIONS["MARKETING" if domain == "FINANCE" else "FINANCE"]],
        "can_delegate": True,
        "priority": 0,
    }
