"""Seed, or remove, a 50-widget native dashboard for the live browser check (NDV-PERF-003).

``seed`` creates, in the QA owner's personal space, one SQL dataset over
analytics.sales, five charts (bar, line, KPI, table, pie) and a dashboard of
three pages holding 17, 17 and 16 chart widgets. It prints the dashboard id
and writes the created ids to ``--state``. ``remove`` deletes them again.

Credentials come from NOVA_QA_OWNER_PASSWORD (alice); nothing is printed but ids.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

SALES = "SELECT city, amount FROM analytics.sales"
PAGES = (17, 17, 16)


async def call(client: httpx.AsyncClient, token: str, method: str, path: str, *,
               body: Any = None, revision: int | None = None, status: int = 200,
               key: str | None = None) -> Any:
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": key or str(uuid4())}
    if revision is not None:
        headers["If-Match"] = f'"{revision}"'
    response = await client.request(method, path, headers=headers, json=body)
    if response.status_code != status:
        try:
            detail = str(response.json().get("detail", ""))[:200]
        except ValueError:
            detail = ""
        raise SystemExit(f"{method} {path.split('/')[4]} returned {response.status_code} {detail}")
    return response.json() if response.content else None


async def login(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/v1/auth/login", json={
        "username": "alice", "password": os.environ["NOVA_QA_OWNER_PASSWORD"]})
    response.raise_for_status()
    token = str(response.json()["access_token"])
    await call(client, token, "POST", "/api/v1/auth/switch-role",
               body={"role": "regional_manager"})
    return token


def charts(dataset: str, fields: dict[str, str]) -> list[dict[str, Any]]:
    city, amount = fields["city"], fields["amount"]
    measure = [{"field_id": amount, "aggregation": "sum"}]
    base = {"schema_version": 2, "dataset_id": dataset, "dataset_revision": 1}
    return [
        {**base, "title": "Revenue by city", "visual": "bar", "encoding": {
            "kind": "cartesian", "dimensions": [{"field_id": city}], "measures": measure}},
        {**base, "title": "Revenue trend by city", "visual": "line", "encoding": {
            "kind": "cartesian", "dimensions": [{"field_id": city}], "measures": measure}},
        {**base, "title": "Total revenue", "visual": "kpi", "encoding": {
            "kind": "single_measure", "measure": measure[0]}},
        {**base, "title": "Revenue table", "visual": "table", "encoding": {
            "kind": "table", "dimensions": [{"field_id": city}], "measures": measure}},
        {**base, "title": "Revenue share", "visual": "pie", "encoding": {
            "kind": "category", "category": {"field_id": city}, "measure": measure[0]}},
    ]


async def seed(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=90) as client:
        token = await login(client)
        dataset = str(uuid4())
        source = {"kind": "sql", "database": "analytics", "sql": SALES}
        inspected = await call(client, token, "POST", "/api/v1/viz/datasets/inspect",
                               body={"dataset_id": dataset, "source": source})
        await call(client, token, "POST", "/api/v1/viz/datasets", status=201, body={
            "dataset_id": dataset, "source": source, "workspace_id": "personal",
            "title": "Perf QA sales",
            "expected_schema_fingerprint": inspected["schema_fingerprint"]})
        fields = {item["source_column"]: item["field_id"] for item in inspected["fields"]}
        chart_ids = []
        for definition in charts(dataset, fields):
            definition["chart_id"] = str(uuid4())
            await call(client, token, "POST", "/api/v1/viz/charts", status=201,
                       body={"workspace_id": "personal", "definition": definition})
            chart_ids.append(definition["chart_id"])
        pages = []
        for number, count in enumerate(PAGES, 1):
            widgets = []
            for index in range(count):
                widgets.append({"kind": "chart", "widget_id": str(uuid4()),
                                "layout": {"x": (index % 3) * 4, "y": (index // 3) * 4,
                                           "w": 4, "h": 4},
                                "chart_ref": {"chart_id": chart_ids[index % len(chart_ids)],
                                              "revision": 1}})
            pages.append({"page_id": str(uuid4()), "title": f"Page {number}",
                          "widgets": widgets})
        dashboard = str(uuid4())
        await call(client, token, "POST", "/api/v1/viz/dashboards", status=201, body={
            "workspace_id": "personal", "definition": {
                "dashboard_id": dashboard, "title": "Perf QA 50 widgets", "pages": pages,
                "datasets": [{"dataset_id": dataset, "revision": 1}]}})
        args.state.write_text(json.dumps({"dashboard": dashboard, "charts": chart_ids,
                                          "dataset": dataset}))
        print(dashboard)


async def remove(args: argparse.Namespace) -> None:
    state = json.loads(args.state.read_text())
    async with httpx.AsyncClient(base_url=args.base_url, timeout=90) as client:
        token = await login(client)
        for collection, ids in (("dashboards", [state["dashboard"]]),
                                ("charts", state["charts"]), ("datasets", [state["dataset"]])):
            for identity in filter(None, ids):
                head = await call(client, token, "GET",
                                  f"/api/v1/viz/{collection}/{identity}?draft=true")
                key = str(uuid4())
                await call(client, token, "DELETE", f"/api/v1/viz/{collection}/{identity}",
                           revision=head["revision"], key=key,
                           body={"operation_id": key, "expected_revision": head["revision"]})
        print("removed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "remove"))
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(seed(args) if args.action == "seed" else remove(args))


if __name__ == "__main__":
    main()
