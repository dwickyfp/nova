"""Live 5W+1H lab for Nova's Dataviz assistant.

Usage (from ``backend/``, against the running development stack)::

    UV_NO_SYNC=1 uv run python -m tests.benchmark.dataviz_assistant.run setup
    UV_NO_SYNC=1 uv run python -m tests.benchmark.dataviz_assistant.run gold
    UV_NO_SYNC=1 uv run python -m tests.benchmark.dataviz_assistant.run ask --out results.json

``setup`` builds (or finds) the dashboard "Dataviz 5W1H eval" over the Semantic
View dataset of ``retail_sales`` with a Region native filter. ``gold`` computes
the expected facts directly from that dataset through Superset's chart data
API as the same principal, so they are governed exactly like the assistant's
numbers. ``ask`` sends every case through ``POST /agents/nova-dataviz-assistant/
threads/{id}/messages`` with the Dataviz context the panel sends, waits for the
turn, and stores the answer with its tool steps for judging.

Credentials come from ``docker/.env`` through ``superset/bench/nova_session``
and are never printed. The model is the deployment's configured default.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "superset" / "bench"))

from nova_session import BiSession, csrf, principal, sign_in  # noqa: E402

TITLE = "Dataviz 5W1H eval"
VIEW_DATASET = "semantic_retail_sales_sales_orders"
ASSISTANT = "nova-dataviz-assistant"
OUT = ROOT / "docs" / "benchmarks" / "dataviz-assistant"
OCT = "2026-10-01 : 2026-11-01"
SEP = "2026-09-01 : 2026-10-01"

CASES: list[dict[str, Any]] = [
    {
        "id": "what_how",
        "w": ["what", "how", "when"],
        "filters": None,
        "question": (
            "Apa arti net revenue di dashboard ini, bagaimana dihitung, "
            "dan berapa totalnya di Oktober 2026?"
        ),
    },
    {
        "id": "who",
        "w": ["who", "when"],
        "filters": None,
        "question": "Salesman siapa yang menyumbang net revenue terbesar di Oktober 2026?",
    },
    {
        "id": "when",
        "w": ["when"],
        "filters": None,
        "question": "Bulan apa net revenue tertinggi antara Mei dan Oktober 2026?",
    },
    {
        "id": "where",
        "w": ["where", "when"],
        "filters": None,
        "question": "Region mana yang net revenue-nya paling besar di Oktober 2026?",
    },
    {
        "id": "why",
        "w": ["why", "where", "who", "when"],
        "filters": None,
        "question": "Kenapa net revenue Oktober 2026 berubah dibanding September 2026?",
    },
    {
        "id": "lineage",
        "w": ["how"],
        "filters": None,
        "question": "Data di dashboard ini dari mana?",
    },
    {
        "id": "filtered",
        "w": ["what", "when", "where"],
        "filters": "first_region",
        "question": "Berapa net revenue Oktober 2026 dengan filter yang sedang aktif?",
    },
    {
        "id": "full_5w1h",
        "w": ["what", "who", "when", "where", "why", "how"],
        "filters": None,
        "question": (
            "Jelaskan perubahan units sold Oktober 2026 dibanding September 2026: apa, "
            "siapa, kapan, di mana, kenapa, dan bagaimana dihitung."
        ),
    },
]


async def _json(response: httpx.Response) -> Any:
    response.raise_for_status()
    return response.json()


async def dataset_id(bi: httpx.AsyncClient) -> int:
    rows = (
        await bi.get(
            f"/api/v1/dataset/?q=(filters:!((col:table_name,opr:eq,value:'{VIEW_DATASET}')))"
        )
    ).json()["result"]
    if not rows:
        raise SystemExit("Sync the retail_sales Semantic View to BI first (POST .../bi-sync).")
    return int(rows[0]["id"])


def _chart(name: str, viz: str, form: dict[str, Any], dataset: int) -> dict[str, Any]:
    form = {"viz_type": viz, "datasource": f"{dataset}__table", **form}
    return {
        "slice_name": name,
        "viz_type": viz,
        "datasource_id": dataset,
        "datasource_type": "table",
        "params": json.dumps(form),
    }


async def setup(session: BiSession) -> dict[str, Any]:
    async with session.bi() as bi:
        token = await csrf(bi)
        headers = {"X-CSRFToken": token}
        found = (
            await bi.get(
                f"/api/v1/dashboard/?q=(filters:!((col:dashboard_title,opr:eq,value:'{TITLE}')))"
            )
        ).json()["result"]
        dataset = await dataset_id(bi)
        if found:
            return {"dashboard_id": int(found[0]["id"]), "dataset_id": dataset}
        specs = [
            _chart(
                "Net revenue per bulan",
                "echarts_timeseries_line",
                {
                    "x_axis": "order_date",
                    "time_grain_sqla": "P1M",
                    "metrics": ["net_revenue"],
                    "groupby": [],
                    "row_limit": 1000,
                },
                dataset,
            ),
            _chart(
                "Net revenue per region",
                "echarts_timeseries_bar",
                {"x_axis": "region", "metrics": ["net_revenue"], "groupby": [], "row_limit": 100},
                dataset,
            ),
            _chart(
                "Net revenue per merek",
                "echarts_timeseries_bar",
                {"x_axis": "brand", "metrics": ["net_revenue"], "groupby": [], "row_limit": 100},
                dataset,
            ),
            _chart("Units sold", "big_number_total", {"metric": "units_sold"}, dataset),
        ]
        ids = []
        for spec in specs:
            ids.append(
                int(
                    (await _json(await bi.post("/api/v1/chart/", json=spec, headers=headers)))["id"]
                )
            )
        layout: dict[str, Any] = {
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
            "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": TITLE}},
            "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
        }
        for index, chart_id in enumerate(ids):
            row, node = f"ROW-{index}", f"CHART-{chart_id}"
            layout["GRID_ID"]["children"].append(row)
            layout[row] = {
                "type": "ROW",
                "id": row,
                "children": [node],
                "parents": ["ROOT_ID", "GRID_ID"],
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
            }
            layout[node] = {
                "type": "CHART",
                "id": node,
                "children": [],
                "parents": ["ROOT_ID", "GRID_ID", row],
                "meta": {"chartId": chart_id, "width": 12, "height": 50},
            }
        metadata = {
            "native_filter_configuration": [
                {
                    "id": "NATIVE_FILTER-region",
                    "name": "Region",
                    "filterType": "filter_select",
                    "type": "NATIVE_FILTER",
                    "targets": [{"datasetId": dataset, "column": {"name": "region"}}],
                    "controlValues": {"multiSelect": True, "enableEmptyFilter": False},
                    "defaultDataMask": {"filterState": {}},
                    "cascadeParentIds": [],
                    "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
                }
            ]
        }
        created = await _json(
            await bi.post(
                "/api/v1/dashboard/",
                json={
                    "dashboard_title": TITLE,
                    "position_json": json.dumps(layout),
                    "json_metadata": json.dumps(metadata),
                    "published": False,
                },
                headers=headers,
            )
        )
        dashboard = int(created["id"])
        for chart_id in ids:
            await bi.put(
                f"/api/v1/chart/{chart_id}", json={"dashboards": [dashboard]}, headers=headers
            )
        return {"dashboard_id": dashboard, "dataset_id": dataset}


async def _data(
    bi: httpx.AsyncClient, token: str, dataset: int, query: dict[str, Any]
) -> list[dict]:
    body = {
        "datasource": {"id": dataset, "type": "table"},
        "force": False,
        "queries": [query],
        "result_format": "json",
        "result_type": "full",
    }
    result = await _json(
        await bi.post("/api/v1/chart/data", json=body, headers={"X-CSRFToken": token})
    )
    return result["result"][0]["data"]


def _q(metrics, columns=(), window=None, extra=(), limit=1000) -> dict[str, Any]:
    filters = list(extra)
    if window:
        filters.append({"col": "order_date", "op": "TEMPORAL_RANGE", "val": window})
    return {
        "metrics": list(metrics),
        "columns": list(columns),
        "filters": filters,
        "orderby": [[metrics[0], False]] if columns else [],
        "row_limit": limit,
        "extras": {},
    }


async def gold(session: BiSession, ids: dict[str, Any]) -> dict[str, Any]:
    dataset = ids["dataset_id"]
    async with session.bi() as bi:
        token = await csrf(bi)
        total_oct = (await _data(bi, token, dataset, _q(["net_revenue"], window=OCT)))[0][
            "net_revenue"
        ]
        total_sep = (await _data(bi, token, dataset, _q(["net_revenue"], window=SEP)))[0][
            "net_revenue"
        ]
        by_region_oct = await _data(bi, token, dataset, _q(["net_revenue"], ["region"], OCT))
        by_region_sep = await _data(bi, token, dataset, _q(["net_revenue"], ["region"], SEP))
        by_salesman = await _data(
            bi, token, dataset, _q(["net_revenue"], ["employee_name"], OCT, limit=5)
        )
        by_month = await _data(
            bi,
            token,
            dataset,
            {
                "metrics": ["net_revenue"],
                "columns": [
                    {
                        "columnType": "BASE_AXIS",
                        "sqlExpression": "order_date",
                        "label": "order_date",
                        "expressionType": "SQL",
                        "timeGrain": "P1M",
                    }
                ],
                "filters": [
                    {"col": "order_date", "op": "TEMPORAL_RANGE", "val": "2026-05-01 : 2026-11-01"}
                ],
                "orderby": [],
                "row_limit": 100,
                "extras": {},
            },
        )
        units = [
            (await _data(bi, token, dataset, _q(["units_sold"], window=w)))[0]["units_sold"]
            for w in (OCT, SEP)
        ]
        first_region = by_region_oct[-1]["region"] if by_region_oct else None
        filtered = (
            await _data(
                bi,
                token,
                dataset,
                _q(
                    ["net_revenue"],
                    window=OCT,
                    extra=[{"col": "region", "op": "IN", "val": [first_region]}],
                ),
            )
        )[0]["net_revenue"]
    before = {row["region"]: row["net_revenue"] for row in by_region_sep}
    drivers = sorted(
        (
            (row["region"], row["net_revenue"] - before.get(row["region"], 0))
            for row in by_region_oct
        ),
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    return {
        "net_revenue_oct": total_oct,
        "net_revenue_sep": total_sep,
        "change": total_oct - total_sep,
        "top_region_oct": by_region_oct[0],
        "top_salesman_oct": by_salesman[0],
        "best_month": max(by_month, key=lambda row: row["net_revenue"]),
        "region_drivers": drivers[:3],
        "units_oct": units[0],
        "units_sep": units[1],
        "filter_region": first_region,
        "net_revenue_oct_filtered": filtered,
    }


async def filter_key(session: BiSession, dashboard_id: int, region: str | None) -> str:
    """A filter state exactly as the dashboard stores it when a viewer selects one."""
    mask = {
        "NATIVE_FILTER-region": {
            "id": "NATIVE_FILTER-region",
            "extraFormData": {"filters": [{"col": "region", "op": "IN", "val": [region]}]}
            if region
            else {},
            "filterState": {"value": [region] if region else None},
            "ownState": {},
        }
    }
    async with session.bi() as bi:
        token = await csrf(bi)
        body = await _json(
            await bi.post(
                f"/api/v1/dashboard/{dashboard_id}/filter_state?tab_id=1",
                json={"value": json.dumps(mask)},
                headers={"X-CSRFToken": token},
            )
        )
    return str(body["key"])


def _save(out: Path, ids: dict[str, Any], facts: dict[str, Any], results: list) -> None:
    """Written after every case, so a failure keeps the cases already answered."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "ran_at": datetime.now(UTC).isoformat(),
                "dashboard": ids,
                "gold": facts,
                "results": results,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )


async def ask(
    session: BiSession,
    ids: dict[str, Any],
    facts: dict[str, Any],
    out: Path,
    only: set[str] | None = None,
) -> None:
    results = []
    async with httpx.AsyncClient(
        base_url=session.api, timeout=600, headers={"Authorization": f"Bearer {session.token}"}
    ) as nova:
        for case in CASES:
            if only and case["id"] not in only:
                continue
            region = facts["filter_region"] if case["filters"] == "first_region" else None
            key = await filter_key(session, ids["dashboard_id"], region)
            thread = await _json(
                await nova.post(f"/agents/{ASSISTANT}/threads", json={"title": case["id"]})
            )
            started = time.perf_counter()
            async with nova.stream(
                "POST",
                f"/agents/{ASSISTANT}/threads/{thread['thread_id']}/messages",
                json={
                    "content": case["question"],
                    "dataviz": {
                        "dashboard_id": ids["dashboard_id"],
                        "native_filters_key": key,
                        "edit_state": "clean",
                        "path": f"/bi/superset/dashboard/{ids['dashboard_id']}/",
                    },
                },
            ) as response:
                response.raise_for_status()
                async for _ in response.aiter_lines():
                    pass
            seconds = round(time.perf_counter() - started, 1)
            stored = await _json(
                await nova.get(f"/agents/{ASSISTANT}/threads/{thread['thread_id']}")
            )
            answer = next((m for m in reversed(stored["messages"]) if m["role"] == "assistant"), {})
            steps = answer.get("steps") or []
            plan = next(
                (s for s in steps if s.get("kind") == "runtime_decision" and "selected_tools" in s),
                {},
            )
            results.append(
                {
                    **case,
                    "seconds": seconds,
                    "answer": answer.get("content", ""),
                    "tools": [
                        {
                            "name": s.get("name"),
                            "status": s.get("status"),
                            "arguments": s.get("arguments"),
                        }
                        for s in steps
                        if s.get("kind") == "tool"
                    ],
                    "finish": next(
                        (s.get("finish_reason") for s in steps if s.get("kind") == "turn_outcome"),
                        None,
                    ),
                    "tokens": answer.get("total_tokens"),
                    "required_tools": plan.get("required_tools"),
                    "selected_tools": plan.get("selected_tools"),
                }
            )
            print(
                json.dumps(
                    {
                        "id": case["id"],
                        "seconds": seconds,
                        "finish": results[-1]["finish"],
                        "tools": [t["name"] for t in results[-1]["tools"]],
                    }
                ),
                flush=True,
            )
            _save(out, ids, facts, results)
async def main_async(command: str, out: Path, only: set[str] | None = None) -> None:
    session = await sign_in(principal("admin"))
    ids = await setup(session)
    if command == "setup":
        print(ids)
        return
    facts = await gold(session, ids)
    if command == "gold":
        print(json.dumps(facts, indent=2, default=str))
        return
    await ask(session, ids, facts, out, only)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["setup", "gold", "ask"])
    parser.add_argument("--out", type=Path, default=OUT / "raw" / "run.json")
    parser.add_argument("--only", nargs="*", default=None, help="Case ids to run.")
    args = parser.parse_args()
    asyncio.run(main_async(args.command, args.out, set(args.only) if args.only else None))


if __name__ == "__main__":
    main()
