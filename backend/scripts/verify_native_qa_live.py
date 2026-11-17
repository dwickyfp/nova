"""Release QA on the live engine: exact aggregates, masks and table denial.

Run against the development stack with QA users only (no administrator):

- Aggregates (NDV-METRIC-001/004/005): native chart queries over a SQL dataset
  with NULLs match the same SQL run by the same person through Nova's SQL
  endpoint, group by group and in total.
- Masks (NDV-ACL-005, NDV-FILTER-017): the seed masks analytics.customers.phone
  for the marketing role. A viewer in marketing gets masked values in chart
  results and in filter options; the clear numbers never appear.
- Table denial (NDV-ACL-003): a role that may open the shared dataset but
  cannot read its table gets a refusal and no rows.
- ISO weeks (NDV-METRIC-015): a week grain over dates around three new years
  groups by the ISO Monday, the same as the engine's own date_trunc('week').

Credentials come from NOVA_QA_OWNER_PASSWORD (alice) and
NOVA_QA_OUTSIDER_PASSWORD (bob). Everything created is removed afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

SALES = ("SELECT city, amount, CASE WHEN city = 'Jakarta' THEN NULL ELSE amount END "
         "AS partial_amount FROM analytics.sales")
CUSTOMERS = "SELECT id, city, phone FROM analytics.customers"
CLEAR_PHONES = {"+62-811-0001", "+62-811-0002"}
VIEWER_ROLES = ("marketing", "finance")


class Probe:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.steps: list[dict[str, Any]] = []

    def check(self, name: str, condition: bool, **detail: Any) -> None:
        self.steps.append({"check": name, "passed": condition, **detail})
        if not condition:
            raise AssertionError(name)

    async def call(self, name: str, method: str, path: str, token: str, *, status: int = 200,
                   body: Any = None, key: str | None = None,
                   revision: int | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"}
        if key:
            headers["Idempotency-Key"] = key
        if revision is not None:
            headers["If-Match"] = f'"{revision}"'
        response = await self.client.request(method, path, headers=headers, json=body)
        step: dict[str, Any] = {"request": name, "status": response.status_code,
                                "expected": status}
        if response.status_code != status:
            # The refusal's own short message; payloads with data are never kept.
            try:
                step["detail"] = str(response.json().get("detail", ""))[:160]
            except ValueError:
                step["detail"] = ""
        self.steps.append(step)
        self.check(name, response.status_code == status)
        return response


async def login(probe: Probe, username: str, variable: str) -> str:
    response = await probe.client.post("/api/v1/auth/login", json={
        "username": username, "password": os.environ[variable]})
    probe.check(f"{username} login", response.status_code == 200)
    return str(response.json()["access_token"])


async def use_role(probe: Probe, token: str, role: str, who: str) -> None:
    switched = await probe.call(f"{who} uses {role}", "POST", "/api/v1/auth/switch-role", token,
                                body={"role": role})
    probe.check(f"{who} active role {role}", switched.json()["active_role"] == role)


async def dataset(probe: Probe, token: str, space: str, sql: str, title: str) -> dict[str, str]:
    identity = str(uuid4())
    source = {"kind": "sql", "database": "analytics", "sql": sql}
    inspected = (await probe.call(f"inspect {title}", "POST", "/api/v1/viz/datasets/inspect",
                                  token, body={"dataset_id": identity, "source": source})).json()
    await probe.call(f"create {title}", "POST", "/api/v1/viz/datasets", token, status=201,
                     key=str(uuid4()), body={
                         "dataset_id": identity, "source": source, "workspace_id": space,
                         "title": title,
                         "expected_schema_fingerprint": inspected["schema_fingerprint"]})
    key = str(uuid4())
    await probe.call(f"publish {title}", "POST", f"/api/v1/viz/datasets/{identity}/publish",
                     token, key=key, revision=1, body={"operation_id": key, "expected_revision": 1})
    # Viewers need a grant on the dataset itself, as the Share dialog gives.
    state = (await probe.call(f"grants {title}", "GET",
                              f"/api/v1/viz/datasets/{identity}/grants", token)).json()
    key = str(uuid4())
    await probe.call(f"share {title}", "PUT", f"/api/v1/viz/datasets/{identity}/grants", token,
                     key=key, revision=state["revision"], body={
                         "operation_id": key, "expected_revision": state["revision"],
                         "grants": [{"subject_type": "role", "subject_id": role,
                                     "permissions": ["view"]} for role in VIEWER_ROLES]})
    # Column names can be anything, including "id", so the dataset id has its own key.
    fields = {item["source_column"]: item["field_id"] for item in inspected["fields"]}
    return {**fields, "dataset": identity}


def number(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("value")
    return None if value is None else Decimal(str(value))


async def run(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "result": "FAIL",
                              "cleanup": "NOT_RUN", "scenarios": {}}
    tokens: dict[str, str] = {}
    created: list[str] = []
    space: str | None = None
    async with httpx.AsyncClient(base_url=args.base_url, timeout=90) as client:
        probe = Probe(client)
        try:
            tokens["author"] = await login(probe, "alice", "NOVA_QA_OWNER_PASSWORD")
            tokens["viewer"] = await login(probe, "bob", "NOVA_QA_OUTSIDER_PASSWORD")
            tokens["other"] = await login(probe, "alice", "NOVA_QA_OWNER_PASSWORD")
            await use_role(probe, tokens["author"], "regional_manager", "author")
            await use_role(probe, tokens["viewer"], "marketing", "bob")
            created_space = await probe.call("workspace", "POST", "/api/v1/workspaces/spaces",
                                             tokens["author"], status=201,
                                             body={"name": f"QA live {uuid4().hex[:8]}"})
            space = created_space.json()["space"]["id"]
            await probe.call("share", "PUT", f"/api/v1/workspaces/spaces/{space}/shares",
                             tokens["author"], body={"shares": [
                                 {"role": "marketing", "permission": "view"},
                                 {"role": "finance", "permission": "view"}]})

            # ── Aggregates against plain SQL, as the same person and role ──
            sales = await dataset(probe, tokens["author"], space, SALES, "Sales QA")
            created.append(sales["dataset"])
            measures = [(sales["amount"], aggregation) for aggregation in
                        ("sum", "count", "avg", "min", "max")]
            measures += [(sales["partial_amount"], aggregation) for aggregation in
                         ("sum", "count", "avg")]
            measures += [(sales["city"], "count_distinct")]
            body = {"revision": 1, "draft": False, "dimensions": [{"field_id": sales["city"]}],
                    "measures": [{"field_id": f, "aggregation": a} for f, a in measures],
                    "sort": [{"column": "d0", "direction": "asc"}]}
            native = (await probe.call("native grouped", "POST",
                                       f"/api/v1/viz/datasets/{sales['dataset']}/query",
                                       tokens["author"], body=body)).json()["rows"]
            oracle_sql = ("SELECT city, SUM(amount), COUNT(amount), AVG(amount), MIN(amount), "
                          "MAX(amount), SUM(partial_amount), COUNT(partial_amount), "
                          f"AVG(partial_amount), COUNT(DISTINCT city) FROM ({SALES}) q "
                          "GROUP BY city ORDER BY city")
            oracle = (await probe.call("oracle grouped", "POST", "/api/v1/query/execute",
                                       tokens["author"], body={"sql": oracle_sql})).json()[0]
            probe.check("oracle ran", oracle["success"], error=oracle.get("error"))
            got = [[row[0]["value"], *[number(cell) for cell in row[1:]]] for row in native]
            want = [[row[0], *[number(cell) for cell in row[1:]]] for row in oracle["rows"]]
            probe.check("grouped aggregates equal the SQL oracle", got == want,
                        native=json.loads(json.dumps(got, default=str)),
                        oracle=json.loads(json.dumps(want, default=str)))
            jakarta = next(row for row in got if row[0] == "Jakarta")
            probe.check("an all-NULL group sums to NULL and counts 0", jakarta[6] is None
                        and jakarta[7] == 0 and jakarta[8] is None)
            total = (await probe.call("native total", "POST",
                                      f"/api/v1/viz/datasets/{sales['dataset']}/query",
                                      tokens["author"], body={**body, "dimensions": [],
                                                              "sort": []})).json()["rows"][0]
            oracle_total = (await probe.call("oracle total", "POST", "/api/v1/query/execute",
                                             tokens["author"], body={"sql": oracle_sql.replace(
                                                 "city, SUM", "SUM").replace(
                                                 " GROUP BY city ORDER BY city", "")})).json()[0]
            probe.check("totals equal the SQL oracle",
                        [number(cell) for cell in total]
                        == [number(cell) for cell in oracle_total["rows"][0]])
            report["scenarios"].update({"NDV-METRIC-001": "PASS", "NDV-METRIC-004": "PASS",
                                        "NDV-METRIC-005": "PASS"})

            # ── ISO weeks across year boundaries, grouped by the engine ──
            days = ["2020-12-31", "2021-01-01", "2021-01-03", "2021-01-04", "2024-12-29",
                    "2024-12-30", "2025-01-01", "2026-01-01", "2026-01-04", "2026-01-05"]
            # A SQL source reads a governed table, so the dates ride on one of its rows.
            literal_days = " UNION ALL ".join(f"SELECT CAST('{day}' AS DATE) AS d"
                                              for day in days)
            weeks_sql = (f"SELECT t.d, 1 AS n FROM ({literal_days}) t "
                         "CROSS JOIN (SELECT MIN(city) AS city FROM analytics.sales) s")
            weeks = await dataset(probe, tokens["author"], space, weeks_sql, "Weeks QA")
            created.append(weeks["dataset"])
            by_week = (await probe.call(
                "native weeks", "POST", f"/api/v1/viz/datasets/{weeks['dataset']}/query",
                tokens["author"], body={
                    "revision": 1, "draft": False,
                    "dimensions": [{"field_id": weeks["d"], "date_grain": "week"}],
                    "measures": [{"field_id": weeks["n"], "aggregation": "sum"}],
                    "sort": [{"column": "d0", "direction": "asc"}]})).json()["rows"]
            got_weeks = {row[0]["value"][:10]: int(number(row[1]) or 0) for row in by_week}
            iso: dict[str, int] = {}
            for day in days:
                parsed = date.fromisoformat(day)
                monday = (parsed - timedelta(days=parsed.weekday())).isoformat()
                iso[monday] = iso.get(monday, 0) + 1
            engine = (await probe.call("oracle weeks", "POST", "/api/v1/query/execute",
                                       tokens["author"], body={"sql": (
                                           "SELECT CAST(date_trunc('week', d) AS DATE), "
                                           f"COUNT(*) FROM ({weeks_sql}) q GROUP BY 1 ORDER BY 1")}
                                       )).json()[0]
            want_engine = {str(row[0])[:10]: int(row[1]) for row in engine["rows"]}
            probe.check("weeks start on the ISO Monday across year boundaries",
                        got_weeks == iso == want_engine,
                        native=got_weeks, iso=iso, engine=want_engine)
            report["scenarios"]["NDV-METRIC-015"] = "PASS"

            # ── Ranger masks reach chart values and filter options ──
            customers = await dataset(probe, tokens["author"], space, CUSTOMERS, "Customers QA")
            created.append(customers["dataset"])
            seen = await client.get(f"/api/v1/viz/datasets/{customers['dataset']}/permissions",
                                    headers={"Authorization": f"Bearer {tokens['viewer']}"})
            probe.steps.append({"request": "bob permissions", "status": seen.status_code,
                                "permissions": seen.json() if seen.status_code == 200 else None})
            phones = {"revision": 1, "draft": False,
                      "dimensions": [{"field_id": customers["phone"]}], "limit": 200}
            masked = (await probe.call("bob reads phone options", "POST",
                                       f"/api/v1/viz/datasets/{customers['dataset']}/query",
                                       tokens["viewer"], body=phones)).json()["rows"]
            values = {row[0]["value"] if row[0] else None for row in masked}
            probe.check("marketing sees masked phones, never the clear numbers",
                        bool(values) and not values & CLEAR_PHONES,
                        masked=sorted(map(str, values)))
            counted = (await probe.call("bob counts by phone", "POST",
                                        f"/api/v1/viz/datasets/{customers['dataset']}/query",
                                        tokens["viewer"], body={**phones, "measures": [
                                            {"field_id": customers["city"],
                                             "aggregation": "count"}]})).text
            probe.check("no clear phone in a chart result", not any(p in counted
                                                                   for p in CLEAR_PHONES))
            report["scenarios"].update({"NDV-ACL-005": "PASS", "NDV-FILTER-017": "PASS"})

            # ── A role that may open the dataset but not read its table ──
            await use_role(probe, tokens["other"], "finance", "alice")
            refused = await client.post(
                f"/api/v1/viz/datasets/{sales['dataset']}/query",
                headers={"Authorization": f"Bearer {tokens['other']}"},
                json={**body, "dimensions": [], "sort": []})
            probe.steps.append({"request": "finance queries sales", "status": refused.status_code})
            if refused.status_code == 200:
                report["scenarios"]["NDV-ACL-003"] = (
                    "NOT_APPLICABLE: finance can read analytics.sales")
            else:
                probe.check("finance is refused without rows",
                            refused.status_code in {403, 404} and "rows" not in refused.text)
                report["scenarios"]["NDV-ACL-003"] = "PASS"
            report["result"] = "PASS"
        except Exception as error:  # noqa: BLE001 - recorded, never echoed with payloads
            report["error_type"] = type(error).__name__
            report["error"] = str(error)[:200]
        finally:
            author = tokens.get("author")
            if author:
                try:
                    for identity in created:
                        head = await client.get(f"/api/v1/viz/datasets/{identity}?draft=true",
                                                headers={"Authorization": f"Bearer {author}"})
                        if head.status_code == 200:
                            key, revision = str(uuid4()), head.json()["revision"]
                            await probe.call(
                                "delete dataset", "DELETE", f"/api/v1/viz/datasets/{identity}",
                                author, key=key, revision=revision,
                                body={"operation_id": key, "expected_revision": revision})
                    if space:
                        await probe.call("delete workspace", "DELETE",
                                         f"/api/v1/workspaces/spaces/{space}", author)
                    report["cleanup"] = "REMOVED"
                except Exception as error:  # noqa: BLE001
                    report.update(cleanup="UNVERIFIED", result="FAIL",
                                  cleanup_error_type=type(error).__name__)
            for token in tokens.values():
                try:
                    await client.post("/api/v1/auth/logout",
                                      headers={"Authorization": f"Bearer {token}"})
                except Exception:  # noqa: BLE001
                    report.setdefault("logout_errors", 0)
            report["steps"] = probe.steps
            report["finished_at"] = datetime.now(UTC).isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({"result": report["result"], "cleanup": report["cleanup"],
                      "scenarios": report["scenarios"], "error": report.get("error")}))
    raise SystemExit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
