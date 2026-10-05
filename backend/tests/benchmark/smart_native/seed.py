"""Seed the lab: tables, a non-root user, Ranger read access, two Views and two agents.

    cd backend && uv run python -m tests.benchmark.smart_native.seed

Run with the engine's administrative configuration (and ``RANGER_*`` when the
stack is governed). The data is deterministic. Re-running replaces the tables and
rotates the lab user's password; Views and agents are reused by name.
"""

from __future__ import annotations

import asyncio
import json
import random
import secrets
from datetime import date, timedelta

from tests.benchmark.smart_native.lab import DATABASE, ROLE, USER, api, login, write_state

DEPARTMENTS = ["Engineering", "Sales", "Marketing", "Finance", "Operations"]
EXPENSES = ["Payroll", "Cloud", "Travel", "Marketing Spend", "Office"]
REVENUES = ["Subscription", "Services"]
LEVELS = {"Junior": 8, "Mid": 14, "Senior": 22, "Lead": 32}


def rows() -> tuple[list[tuple], list[tuple]]:
    rng = random.Random(20261005)
    ledger: list[tuple] = []
    for month in range(1, 13):  # all of 2025, then January to September 2026
        for year in (2025, 2026):
            if year == 2026 and month > 9:
                continue
            for department in DEPARTMENTS:
                for category in EXPENSES:
                    ledger.append((len(ledger) + 1, date(year, month, rng.randrange(1, 28)),
                                   department, "Expense", category,
                                   rng.randrange(20, 400) * 1_000_000))
                if department in ("Sales", "Marketing"):
                    for category in REVENUES:
                        ledger.append((len(ledger) + 1, date(year, month, rng.randrange(1, 28)),
                                       department, "Revenue", category,
                                       rng.randrange(400, 2500) * 1_000_000))
    employees = []
    for number in range(1, 121):
        level = rng.choices(list(LEVELS), (40, 35, 18, 7))[0]
        hired = date(2019, 1, 1) + timedelta(days=rng.randrange(0, 2800))
        status = rng.choices(("active", "resigned"), (85, 15))[0]
        employees.append((number, f"Employee {number:03d}", rng.choice(DEPARTMENTS), level,
                          status, hired, (LEVELS[level] + rng.randrange(0, 5)) * 1_000_000,
                          rng.choice(("F", "M"))))
    return ledger, employees


def _values(items: list[tuple]) -> str:
    return ",".join(
        "(" + ",".join(f"'{cell}'" if isinstance(cell, (str, date)) else str(cell)
                       for cell in row) + ")"
        for row in items
    )


async def seed_engine() -> None:
    from app.core.database import db
    from app.modules.access_control.security_context import SecurityContext
    from app.modules.access_control.service import access_control_service

    await db.init_system_pool()
    ledger, employees = rows()
    password = secrets.token_urlsafe(24)
    table = 'DISTRIBUTED BY HASH({key}) BUCKETS 1 PROPERTIES("replication_num"="1")'
    for statement in (
        f"CREATE DATABASE IF NOT EXISTS {DATABASE}",
        f"DROP TABLE IF EXISTS {DATABASE}.gl_entries",
        f"DROP TABLE IF EXISTS {DATABASE}.employees",
        f"CREATE TABLE {DATABASE}.gl_entries (entry_id BIGINT, entry_date DATE, "
        "department VARCHAR(64), account_type VARCHAR(16), category VARCHAR(64), "
        f"amount DECIMAL(20,2)) DUPLICATE KEY(entry_id) {table.format(key='entry_id')}",
        f"CREATE TABLE {DATABASE}.employees (employee_id BIGINT, full_name VARCHAR(128), "
        "department VARCHAR(64), job_level VARCHAR(16), employment_status VARCHAR(16), "
        "hire_date DATE, monthly_salary DECIMAL(20,2), gender VARCHAR(4)) "
        f"DUPLICATE KEY(employee_id) {table.format(key='employee_id')}",
        f"INSERT INTO {DATABASE}.gl_entries VALUES {_values(ledger)}",
        f"INSERT INTO {DATABASE}.employees VALUES {_values(employees)}",
        f"CREATE USER IF NOT EXISTS '{USER}'@'%' IDENTIFIED BY '{password}'",
        f"ALTER USER '{USER}'@'%' IDENTIFIED BY '{password}'",
    ):
        await db.execute_system(statement)
    admin = SecurityContext(principal="nova_admin", active_role="ACCOUNTADMIN")
    try:
        await access_control_service.create_role(admin, ROLE, "Smart acceptance lab")
    except Exception as exc:  # noqa: BLE001 - the role exists on a second run
        print(f"role: {type(exc).__name__}")
        await db.execute_system(f"CREATE ROLE IF NOT EXISTS {ROLE}")
    for statement in (
        f"GRANT SELECT ON ALL TABLES IN DATABASE {DATABASE} TO ROLE {ROLE}",
        f"GRANT {ROLE} TO USER '{USER}'@'%'",
        f"SET DEFAULT ROLE {ROLE} TO '{USER}'@'%'",
    ):
        await db.execute_system(statement)
    try:
        await access_control_service.grant_access(
            admin, role=ROLE, catalog="default_catalog", database=DATABASE, table="*",
            accesses=["select"],
        )
    except Exception as exc:  # noqa: BLE001 - a stack without Ranger needs only the GRANT
        print(f"Ranger grant skipped: {type(exc).__name__}")
    await db.close_system_pool()
    write_state("user.json", {"password": password})
    write_state("session.json", {})


def _field(name: str, description: str, *, dimension: bool = False, time: bool = False,
           datatype: str | None = None, synonyms: list[str] | None = None,
           samples: list[str] | None = None) -> str:
    lines = [f"      - name: {name}", "        expression:", "          dialects:",
             "            - dialect: ANSI_SQL", f"              expression: {json.dumps(name)}",
             f"        description: {json.dumps(description)}"]
    if datatype:
        lines.append(f"        datatype: {datatype}")
    if synonyms:
        lines += ["        ai_context:", f"          synonyms: {json.dumps(synonyms)}"]
    if time:
        lines += ["        dimension:", "          is_time: true"]
    elif dimension:
        lines.append("        dimension: " + (
            f"\n          sample_values: {json.dumps(samples)}" if samples else "{}"))
    return "\n".join(lines)


def _metric(name: str, expression: str, description: str, datatype: str,
            synonyms: list[str]) -> str:
    return "\n".join([
        f"  - name: {name}", "    expression:", "      dialects:", "        - dialect: ANSI_SQL",
        f"          expression: {json.dumps(expression)}", f"    datatype: {datatype}",
        f"    description: {json.dumps(description)}", "    ai_context:",
        f"      synonyms: {json.dumps(synonyms)}",
    ])


def _case(kind: str, sign: str = "") -> str:
    return (f"SUM(CASE WHEN gl_entries.account_type = '{kind}' THEN gl_entries.amount "
            f"ELSE {sign}0 END)")


FINANCE = "\n".join([
    "version: 0.1.1", "name: lab_finance_ledger",
    "description: General ledger of company revenue and expenses by department and "
    "category, in IDR.",
    "ai_context:",
    "  instructions: Use for revenue, expense, cost, budget spend and net income questions.",
    "datasets:", "  - name: gl_entries", f"    source: {DATABASE}.gl_entries",
    "    primary_key: [entry_id]", "    description: One row per general ledger entry.",
    "    fields:",
    _field("entry_id", "Ledger entry identifier"),
    _field("entry_date", "Posting date of the entry", time=True, datatype="DateTime",
           synonyms=["date", "posting date", "tanggal"]),
    _field("department", "Department that owns the entry", dimension=True,
           synonyms=["departemen", "divisi", "team"], samples=DEPARTMENTS),
    _field("account_type", "Revenue or Expense", dimension=True,
           synonyms=["type", "jenis akun"], samples=["Revenue", "Expense"]),
    _field("category", "Ledger category such as Payroll, Cloud, Travel, Subscription",
           dimension=True, synonyms=["kategori", "cost category"], samples=EXPENSES + REVENUES),
    _field("amount", "Entry amount in IDR", datatype="Decimal"),
    "metrics:",
    _metric("total_expense", _case("Expense"), "Total expenses in IDR", "Decimal",
            ["expense", "expenses", "cost", "spend", "biaya", "pengeluaran", "beban"]),
    _metric("total_revenue", _case("Revenue"), "Total revenue in IDR", "Decimal",
            ["revenue", "income", "pendapatan", "omzet"]),
    _metric("net_income",
            "SUM(CASE WHEN gl_entries.account_type = 'Revenue' THEN gl_entries.amount "
            "ELSE -gl_entries.amount END)",
            "Revenue minus expenses in IDR", "Decimal",
            ["profit", "laba", "laba bersih", "net profit"]),
]) + "\n"

HR = "\n".join([
    "version: 0.1.1", "name: lab_hr_workforce",
    "description: Employee master data for headcount, salary and attrition by department "
    "and job level.",
    "ai_context:",
    "  instructions: Use for headcount, employees, salary, payroll and resignation questions.",
    "datasets:", "  - name: employees", f"    source: {DATABASE}.employees",
    "    primary_key: [employee_id]",
    "    description: One row per employee, current and former.", "    fields:",
    _field("employee_id", "Employee identifier"),
    _field("department", "Department of the employee", dimension=True,
           synonyms=["departemen", "divisi", "team"], samples=DEPARTMENTS),
    _field("job_level", "Seniority level", dimension=True,
           synonyms=["level", "jabatan", "seniority"], samples=list(LEVELS)),
    _field("employment_status", "active or resigned", dimension=True,
           synonyms=["status", "status karyawan"], samples=["active", "resigned"]),
    _field("gender", "F or M", dimension=True, synonyms=["jenis kelamin"], samples=["F", "M"]),
    _field("hire_date", "Date the employee joined", time=True, datatype="DateTime",
           synonyms=["join date", "tanggal masuk"]),
    _field("monthly_salary", "Monthly salary in IDR", datatype="Decimal"),
    "metrics:",
    _metric("headcount", "COUNT(DISTINCT employees.employee_id)",
            "Number of employees, current and former", "Integer",
            ["employees", "jumlah karyawan", "karyawan", "pegawai", "staff"]),
    _metric("active_headcount",
            "COUNT(DISTINCT CASE WHEN employees.employment_status = 'active' "
            "THEN employees.employee_id END)",
            "Number of currently active employees", "Integer",
            ["active employees", "karyawan aktif"]),
    _metric("avg_monthly_salary", "AVG(employees.monthly_salary)",
            "Average monthly salary in IDR", "Decimal",
            ["average salary", "rata-rata gaji", "gaji rata-rata"]),
    _metric("monthly_payroll", "SUM(employees.monthly_salary)", "Total monthly salary in IDR",
            "Decimal", ["payroll", "total gaji", "salary cost"]),
]) + "\n"


def _must(status: int, data, expected=(200, 201)):
    if status not in expected:
        raise SystemExit(f"HTTP {status}: {json.dumps(data, default=str)[:600]}")
    return data


def _view(name: str, definition: str) -> str:
    _, existing = api("GET", "/semantic-views")
    found = next((view for view in existing if view.get("name") == name), None)
    if found and found.get("status") == "ACTIVE":
        return found["id"]
    view = _must(*api("POST", "/semantic-views", {
        "name": name, "database": DATABASE, "definition": definition}))
    for step in ("validate", "publish"):
        _must(*api("POST", f"/semantic-views/{view['id']}/versions/1/{step}"))
    return view["id"]


def _agent(name: str, description: str, response: str, view_id: str, tools: list[str]) -> str:
    _, listing = api("GET", "/agents")
    agents = listing if isinstance(listing, list) else listing.get("agents", [])
    found = next((agent for agent in agents if agent.get("name") == name), None)
    body = {
        "name": name, "description": description, "database_name": DATABASE,
        "instructions_response": response, "response_style": "concise",
        "default_tools": tools, "policy": "auto_read_only", "semantic_view_ids": [view_id],
        "visibility": "private",
    }
    if found:
        agent_id = found["agent_id"]
        _must(*api("PUT", f"/agents/{agent_id}", {"default_tools": tools}))
    else:
        agent_id = _must(*api("POST", "/agents", body))["agent_id"]
        _must(*api("POST", f"/agents/{agent_id}/access", {"role_name": ROLE}))
    _must(*api("POST", f"/agents/{agent_id}/access/verify", {"role_name": ROLE}))
    return agent_id


def seed_studio() -> dict[str, str]:
    login()
    finance, hr = _view("lab_finance_ledger", FINANCE), _view("lab_hr_workforce", HR)
    ids = {
        "finance_view": finance, "hr_view": hr,
        # Finance may forecast and explain a change; HR may not, on purpose.
        "finance_agent": _agent(
            "Finance Analyst",
            "Answers revenue, expense and net income questions from the company ledger.",
            "You are a finance analyst. Amounts are in IDR. State the period you applied.",
            finance, ["load_skill", "semantic_query", "data_to_chart", "ml_execute",
                      "diagnose_change"]),
        "hr_agent": _agent(
            "HR Analyst",
            "Answers headcount, salary and attrition questions from employee master data.",
            "You are an HR analyst. Salaries are monthly, in IDR.",
            hr, ["load_skill", "semantic_query", "data_to_chart"]),
    }
    write_state("ids.json", ids)
    return ids


if __name__ == "__main__":
    asyncio.run(seed_engine())
    print(json.dumps(seed_studio(), indent=1))
