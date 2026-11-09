"""Dashboard load benchmark: 4, 12 and 24-chart dashboards, 1/5/20 viewers.

Usage (from ``backend/``, so Nova's chart translator imports)::

    UV_NO_SYNC=1 uv run python ../superset/bench/load.py setup
    UV_NO_SYNC=1 uv run python ../superset/bench/load.py run --seconds 70 \
        --out ../docs/benchmarks/superset-lean/raw/baseline-load.json

``setup`` creates (or finds) one virtual dataset and three dashboards titled
``Nova lean bench <n>`` owned by the signed-in benchmark principal, built
through Superset's REST API as that principal. Rerunning finds them by title.

``run`` simulates viewers. One dashboard load is what Superset's own page does
after its bundle is cached: read the dashboard, its charts and its datasets,
then ask the chart data API for every chart with at most six requests in
flight (a browser's per-host limit). The load time is from the first request
to the last chart's data. Each viewer repeats loads until the cell's time is
up. Chart data runs through Nova's proxy as the viewer, so StarRocks, Ranger,
the proxy and Nova's identity exchange are all on the path, as in production.
The first load of each cell is reported separately: it is the one most likely
to miss Superset's data cache.

An ``edit`` cell has authors read a dashboard and save its unchanged layout
back (``PUT /api/v1/dashboard/<id>``), as the editor's Save does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nova_session import BiSession, csrf, principal, sign_in  # noqa: E402

from app.modules.superset.charts import ChartPlan, Measure, to_superset  # noqa: E402

DATASET = "nova_lean_bench_sales"
SQL = """SELECT o.order_date, o.quantity, o.gross_amount, o.discount_amount, o.net_amount,
       o.cost_amount, o.returned_qty, o.payment_method, o.status,
       t.region, t.channel, t.tier, t.city, p.category, p.brand
FROM NOVA_RETAIL_FIELD.sales_orders AS o
JOIN NOVA_RETAIL_FIELD.outlets AS t ON t.outlet_id = o.outlet_id
JOIN NOVA_RETAIL_FIELD.products AS p ON p.product_id = o.product_id"""
MEASURES = ("net_amount", "gross_amount", "quantity", "discount_amount", "cost_amount", "returned_qty")
DIMENSIONS = ("region", "channel", "category", "brand", "tier", "payment_method")
SIZES = (4, 12, 24)
VIEWERS = (1, 5, 20)
BROWSER_PARALLEL = 6


def plans(count: int) -> list[tuple[str, ChartPlan]]:
    """``count`` distinct charts: varied kinds, measures and dimensions."""
    out: list[tuple[str, ChartPlan]] = []
    kinds = ("line", "bar", "pie", "big_number", "bar_stacked", "table", "area", "heatmap")
    i = 0
    while len(out) < count:
        kind = kinds[i % len(kinds)]
        measure = MEASURES[i % len(MEASURES)]
        dim = DIMENSIONS[(i // len(kinds)) % len(DIMENSIONS)]
        other = DIMENSIONS[(i // len(kinds) + 1) % len(DIMENSIONS)]
        m = [Measure(column=measure)]
        if kind in ("line", "area"):
            plan = ChartPlan(kind=kind, x="order_date", time_grain="P1M", measures=m)
        elif kind == "bar":
            plan = ChartPlan(kind="bar", x=dim, measures=m, order="value_desc")
        elif kind == "bar_stacked":
            plan = ChartPlan(kind="bar_stacked", x=dim, series=other, measures=m)
        elif kind == "pie":
            plan = ChartPlan(kind="pie", x=dim, measures=m)
        elif kind == "big_number":
            plan = ChartPlan(kind="big_number", measures=m)
        elif kind == "heatmap":
            plan = ChartPlan(kind="heatmap", x=dim, series=other, measures=m)
        else:
            plan = ChartPlan(kind="table", columns=[dim, other], measures=m, limit=100)
        out.append((f"{kind} {measure} by {dim}", plan))
        i += 1
    return out


async def _request(bi: httpx.AsyncClient, method: str, path: str, token: str, **kw: Any) -> dict:
    response = await bi.request(method, path, headers={"X-CSRFToken": token}, **kw)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:300]}")
    return response.json() if response.content else {}


async def setup(session: BiSession) -> dict[str, int]:
    async with session.bi() as bi:
        token = await csrf(bi)
        found = (await _request(
            bi, "GET", f"/api/v1/dataset/?q=(filters:!((col:table_name,opr:eq,value:'{DATASET}')))", token,
        ))["result"]
        if found:
            dataset_id = int(found[0]["id"])
        else:
            dbs = (await _request(bi, "GET", "/api/v1/database/", token))["result"]
            nova = next(row["id"] for row in dbs if row["database_name"] == "Nova")
            created = await _request(
                bi, "POST", "/api/v1/dataset/", token,
                json={"database": nova, "catalog": "default_catalog", "schema": "NOVA_RETAIL_FIELD",
                      "table_name": DATASET, "sql": SQL},
            )
            dataset_id = int(created["id"])
        detail = (await _request(bi, "GET", f"/api/v1/dataset/{dataset_id}", token))["result"]
        columns = [c["column_name"] for c in detail["columns"]]
        temporal = {c["column_name"] for c in detail["columns"] if c.get("is_dttm")}
        if "order_date" not in temporal:
            date_col = next(c for c in detail["columns"] if c["column_name"] == "order_date")
            await _request(bi, "PUT", f"/api/v1/dataset/{dataset_id}", token,
                           json={"columns": [{"id": date_col["id"], "column_name": "order_date", "is_dttm": True}]})
            temporal.add("order_date")
        dashboards: dict[str, int] = {}
        for size in SIZES:
            title = f"Nova lean bench {size}"
            existing = (await _request(
                bi, "GET",
                f"/api/v1/dashboard/?q=(filters:!((col:dashboard_title,opr:eq,value:'{title}')))", token,
            ))["result"]
            if existing:
                dashboards[str(size)] = int(existing[0]["id"])
                continue
            chart_ids: list[tuple[int, str]] = []
            for name, plan in plans(size):
                viz, form, query_context = to_superset(plan, dataset_id=dataset_id, columns=columns, temporal=temporal)
                chart = await _request(bi, "POST", "/api/v1/chart/", token, json={
                    "slice_name": f"[bench {size}] {name}", "viz_type": viz, "datasource_id": dataset_id,
                    "datasource_type": "table", "params": json.dumps(form), "query_context": json.dumps(query_context),
                })
                chart_ids.append((int(chart["id"]), name))
            layout: dict[str, Any] = {
                "DASHBOARD_VERSION_KEY": "v2",
                "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
                "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": title}},
                "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
            }
            for start in range(0, len(chart_ids), 3):
                row_id = f"ROW-{start // 3}"
                layout["GRID_ID"]["children"].append(row_id)
                layout[row_id] = {"type": "ROW", "id": row_id, "children": [], "parents": ["ROOT_ID", "GRID_ID"],
                                  "meta": {"background": "BACKGROUND_TRANSPARENT"}}
                for chart_id, name in chart_ids[start:start + 3]:
                    node = f"CHART-{chart_id}"
                    layout[row_id]["children"].append(node)
                    layout[node] = {"type": "CHART", "id": node, "children": [],
                                    "parents": ["ROOT_ID", "GRID_ID", row_id],
                                    "meta": {"chartId": chart_id, "width": 4, "height": 50, "sliceName": name}}
            dashboard = await _request(bi, "POST", "/api/v1/dashboard/", token, json={
                "dashboard_title": title, "position_json": json.dumps(layout), "published": False,
            })
            dashboard_id = int(dashboard["id"])
            for chart_id, _ in chart_ids:
                await _request(bi, "PUT", f"/api/v1/chart/{chart_id}", token, json={"dashboards": [dashboard_id]})
            dashboards[str(size)] = dashboard_id
    return dashboards


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))], 1)


def _stats(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50_ms": _percentile(values, 0.5),
        "p95_ms": _percentile(values, 0.95),
        "p99_ms": _percentile(values, 0.99) if len(values) >= 100 else None,
        "mean_ms": round(statistics.fmean(values), 1) if values else None,
    }


async def load_once(bi: httpx.AsyncClient, token: str, dashboard_id: int, charts: list[dict]) -> tuple[float, list[float], int]:
    errors = 0
    started = time.perf_counter()
    for path in (f"/api/v1/dashboard/{dashboard_id}", f"/api/v1/dashboard/{dashboard_id}/charts",
                 f"/api/v1/dashboard/{dashboard_id}/datasets"):
        response = await bi.get(path)
        errors += response.status_code >= 400
    gate = asyncio.Semaphore(BROWSER_PARALLEL)
    chart_ms: list[float] = []

    async def one(chart: dict) -> None:
        nonlocal errors
        async with gate:
            t0 = time.perf_counter()
            response = await bi.post("/api/v1/chart/data", json=chart["query_context"],
                                     headers={"X-CSRFToken": token})
            chart_ms.append((time.perf_counter() - t0) * 1000)
            if response.status_code >= 400:
                errors += 1

    await asyncio.gather(*(one(chart) for chart in charts))
    return (time.perf_counter() - started) * 1000, chart_ms, errors


async def charts_of(bi: httpx.AsyncClient, dashboard_id: int) -> list[dict]:
    rows = (await bi.get(f"/api/v1/dashboard/{dashboard_id}/charts")).json()["result"]
    out = []
    for row in rows:
        detail = (await bi.get(f"/api/v1/chart/{row['id']}")).json()["result"]
        out.append({"id": row["id"], "query_context": json.loads(detail["query_context"])})
    return out


async def view_cell(session: BiSession, dashboard_id: int, viewers: int, seconds: float) -> dict[str, Any]:
    async with session.bi() as setup_client:
        charts = await charts_of(setup_client, dashboard_id)
    loads: list[float] = []
    chart_ms: list[float] = []
    errors = 0
    requests = 0
    first: float | None = None
    deadline = time.perf_counter() + seconds

    async def viewer() -> None:
        nonlocal errors, requests, first
        async with session.bi() as bi:
            token = await csrf(bi)
            while time.perf_counter() < deadline:
                try:
                    elapsed, per_chart, failed = await load_once(bi, token, dashboard_id, charts)
                except httpx.HTTPError:
                    errors += 1
                    requests += 1
                    continue
                if first is None:
                    first = elapsed
                loads.append(elapsed)
                chart_ms.extend(per_chart)
                errors += failed
                requests += 3 + len(charts)

    await asyncio.gather(*(viewer() for _ in range(viewers)))
    return {
        "dashboard_load": _stats(loads),
        "first_load_ms": round(first, 1) if first else None,
        "chart_data": _stats(chart_ms),
        "requests": requests,
        "errors": errors,
        "error_rate": round(errors / requests, 4) if requests else None,
        "throughput_loads_per_s": round(len(loads) / seconds, 2),
    }


async def edit_cell(session: BiSession, dashboard_id: int, authors: int, seconds: float) -> dict[str, Any]:
    saves: list[float] = []
    errors = 0
    deadline = time.perf_counter() + seconds

    async def author() -> None:
        nonlocal errors
        async with session.bi() as bi:
            token = await csrf(bi)
            while time.perf_counter() < deadline:
                t0 = time.perf_counter()
                current = (await bi.get(f"/api/v1/dashboard/{dashboard_id}")).json()["result"]
                response = await bi.put(
                    f"/api/v1/dashboard/{dashboard_id}",
                    json={"position_json": current["position_json"], "json_metadata": current["json_metadata"]},
                    headers={"X-CSRFToken": token},
                )
                await bi.get(f"/api/v1/dashboard/{dashboard_id}")
                saves.append((time.perf_counter() - t0) * 1000)
                errors += response.status_code >= 400

    await asyncio.gather(*(author() for _ in range(authors)))
    return {"save_reopen": _stats(saves), "errors": errors,
            "error_rate": round(errors / len(saves), 4) if saves else None}


async def run(seconds: float, out: Path, label: str) -> None:
    session = await sign_in(principal("admin"))
    dashboards = await setup(session)
    cells: list[dict[str, Any]] = []
    for size in SIZES:
        for viewers in VIEWERS:
            started = datetime.now(UTC).isoformat()
            result = await view_cell(session, dashboards[str(size)], viewers, seconds)
            cells.append({"scenario": "view", "charts": size, "viewers": viewers, "started_at": started, **result})
            print(json.dumps(cells[-1]))
    for authors in (1, 5):
        result = await edit_cell(session, dashboards["12"], authors, min(seconds, 40))
        cells.append({"scenario": "edit", "charts": 12, "authors": authors, **result})
        print(json.dumps(cells[-1]))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "label": label, "seconds_per_cell": seconds, "browser_parallel": BROWSER_PARALLEL,
        "dashboards": dashboards, "dataset_sql": SQL, "cells": cells,
        "finished_at": datetime.now(UTC).isoformat(),
    }, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup")
    runner = sub.add_parser("run")
    runner.add_argument("--seconds", type=float, default=70)
    runner.add_argument("--label", default="baseline")
    runner.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.command == "setup":
        print(asyncio.run(_setup()))
    else:
        asyncio.run(run(args.seconds, Path(args.out), args.label))


async def _setup() -> dict[str, int]:
    return await setup(await sign_in(principal("admin")))


if __name__ == "__main__":
    main()
