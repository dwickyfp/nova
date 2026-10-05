"""Gold values for the lab, from SQL run independently of any agent.

    cd backend && uv run python -m tests.benchmark.smart_native.gold
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from tests.benchmark.smart_native.lab import DATABASE, write_state

LEDGER, STAFF = f"{DATABASE}.gl_entries", f"{DATABASE}.employees"
EXPENSE_2025 = f"FROM {LEDGER} WHERE account_type='Expense' AND YEAR(entry_date)=2025"
MONTH = (f"SELECT SUM(amount) FROM {LEDGER} WHERE account_type='Expense' "
         "AND entry_date >= '{start}' AND entry_date < '{end}'")

CHANGE = (
    "SELECT {by}, SUM(CASE WHEN entry_date >= '2026-05-01' THEN amount ELSE -amount END) "
    f"FROM {LEDGER} WHERE account_type='Expense' AND entry_date >= '2026-04-01' "
    "AND entry_date < '2026-06-01' GROUP BY {by} ORDER BY {by}"
)

QUERIES = {
    "expense_2025": f"SELECT SUM(amount) {EXPENSE_2025}",
    "net_income_2025_by_department":
        "SELECT department, SUM(CASE WHEN account_type='Revenue' THEN amount ELSE -amount END) "
        f"FROM {LEDGER} WHERE YEAR(entry_date)=2025 GROUP BY department ORDER BY department",
    "expense_2025_by_department":
        f"SELECT department, SUM(amount) {EXPENSE_2025} GROUP BY department ORDER BY department",
    "expense_2025_by_category":
        f"SELECT category, SUM(amount) {EXPENSE_2025} GROUP BY category ORDER BY category",
    "active_headcount":
        f"SELECT COUNT(DISTINCT employee_id) FROM {STAFF} WHERE employment_status='active'",
    "active_headcount_by_department":
        f"SELECT department, COUNT(DISTINCT employee_id) FROM {STAFF} "
        "WHERE employment_status='active' GROUP BY department ORDER BY department",
    "avg_salary_by_job_level":
        f"SELECT job_level, ROUND(AVG(monthly_salary), 2) FROM {STAFF} "
        "GROUP BY job_level ORDER BY job_level",
    "expense_april_2026": MONTH.format(start="2026-04-01", end="2026-05-01"),
    "expense_may_2026": MONTH.format(start="2026-05-01", end="2026-06-01"),
    "expense_change_april_to_may_2026_by_category": CHANGE.format(by="category"),
    "expense_change_april_to_may_2026_by_department": CHANGE.format(by="department"),
}


async def compute() -> dict[str, list[list[str]]]:
    from app.core.database import db

    await db.init_system_pool()
    gold = {}
    for key, sql in QUERIES.items():
        result = await db.execute_system(sql)
        gold[key] = [[str(cell) for cell in row] for row in result["rows"]]
    await db.close_system_pool()
    expense = {row[0]: Decimal(row[1]) for row in gold["expense_2025_by_department"]}
    staff = {row[0]: Decimal(row[1]) for row in gold["active_headcount_by_department"]}
    total = sum(expense.values())
    gold["expense_per_active_employee"] = [
        [name, f"{expense[name] / staff[name]:.4f}"] for name in sorted(expense)
    ]
    gold["expense_share_2025_by_department"] = [
        [name, f"{expense[name] / total * 100:.2f}"] for name in sorted(expense)
    ]
    april = Decimal(gold["expense_april_2026"][0][0])
    may = Decimal(gold["expense_may_2026"][0][0])
    gold["expense_growth_april_to_may_2026"] = [[f"{(may - april) / april * 100:.2f}"]]
    return gold


if __name__ == "__main__":
    values = asyncio.run(compute())
    write_state("gold.json", values)
    for name, table in values.items():
        print(name, table)
