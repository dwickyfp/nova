"""Cold start of the BI web service, to an authenticated dashboard.

Usage (from ``backend/``)::

    UV_NO_SYNC=1 uv run python ../superset/bench/coldstart.py --runs 3 --out ../docs/benchmarks/superset-lean/raw/baseline-coldstart.json

Each run restarts the ``nova-superset`` container and records three moments
from the restart command: the health endpoint answering, a signed-in session
reading the 12-chart bench dashboard, and that dashboard's first chart
returning data through Nova's proxy. "Ready" is the last one: a user can see
data, not merely a container that reports healthy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from load import charts_of, setup  # noqa: E402
from nova_session import csrf, principal, sign_in  # noqa: E402


async def once(container: str) -> dict[str, float]:
    session = await sign_in(principal("admin"))
    dashboards = await setup(session)
    dashboard_id = dashboards["12"]
    t0 = time.perf_counter()
    subprocess.run(["docker", "restart", container], check=True, capture_output=True)
    marks: dict[str, float] = {"restart_returned_s": round(time.perf_counter() - t0, 2)}
    async with httpx.AsyncClient(timeout=5) as plain:
        while True:
            try:
                if (await plain.get(f"{session.superset}/health")).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.25)
    marks["health_s"] = round(time.perf_counter() - t0, 2)
    async with session.bi() as bi:
        while (await bi.get(f"/api/v1/dashboard/{dashboard_id}")).status_code != 200:
            await asyncio.sleep(0.25)
        marks["dashboard_metadata_s"] = round(time.perf_counter() - t0, 2)
        charts = await charts_of(bi, dashboard_id)
        token = await csrf(bi)
        response = await bi.post("/api/v1/chart/data", json=charts[0]["query_context"], headers={"X-CSRFToken": token})
        response.raise_for_status()
        marks["first_chart_data_s"] = round(time.perf_counter() - t0, 2)
    return marks


async def main_async(runs: int, container: str, out: Path, label: str) -> None:
    results = []
    for _ in range(runs):
        results.append(await once(container))
        print(json.dumps(results[-1]))
        await asyncio.sleep(10)
    ready = sorted(r["first_chart_data_s"] for r in results)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "label": label, "container": container, "runs": results,
        "ready_median_s": ready[len(ready) // 2], "ready_max_s": ready[-1],
    }, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--container", default="nova-superset")
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    asyncio.run(main_async(args.runs, args.container, Path(args.out), args.label))


if __name__ == "__main__":
    main()
