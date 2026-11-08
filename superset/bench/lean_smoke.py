"""Smoke check of the lean BI deployment, as structured JSON.

Usage (from ``backend/``)::

    UV_NO_SYNC=1 uv run python ../superset/bench/lean_smoke.py > lean-smoke.json

Each check records ``ok`` and what it observed. It exits 1 when any check
fails. It needs the bench dashboards (``load.py setup``) and changes nothing
but a no-op dashboard save on the bench dashboard it owns.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from load import charts_of, setup  # noqa: E402
from nova_session import csrf, principal, sign_in  # noqa: E402

EXPECTED = {
    "nova-superset": "running",
    "nova-superset-worker": "running",
    "nova-superset-beat": "running",
    "nova-superset-db": "running",
}


def _sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


async def main() -> int:
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, **observed: Any) -> None:
        checks.append({"check": name, "ok": bool(ok), **observed})

    for name, state in EXPECTED.items():
        status = _sh("docker", "inspect", name, "--format", "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}")
        check(f"container {name}", status.startswith(state) and "unhealthy" not in status, status=status)
    init = _sh("docker", "inspect", "nova-superset-init", "--format", "{{.State.Status}} {{.State.ExitCode}}")
    check("superset-init ran once and exited 0", init == "exited 0", status=init)
    web_browser = _sh("docker", "exec", "nova-superset", "sh", "-c", "test -d /app/.playwright && echo yes || echo no")
    check("web image carries no browser", web_browser == "no", playwright_dir=web_browser)
    worker_browser = _sh("docker", "exec", "nova-superset-worker", "sh", "-c", "test -d /app/.playwright && echo yes || echo no")
    check("worker image carries the browser", worker_browser == "yes", playwright_dir=worker_browser)
    ping = _sh("docker", "exec", "nova-superset-worker", "sh", "-c",
               'celery --broker="$SUPERSET_REDIS_URL/6" inspect ping -d celery@$HOSTNAME -t 10')
    check("worker answers on its broker", "pong" in ping, reply=ping[-60:])
    queue = _sh("docker", "exec", "nova-superset-worker", "/app/.venv/bin/python", "-c",
                "import os, redis; print(redis.Redis.from_url(os.environ['SUPERSET_REDIS_URL'] + '/6').llen('celery'))")
    check("no backlog in the Celery queue", queue in ("0", "1", ""), llen=queue)

    admin = await sign_in(principal("admin"))
    dashboards = await setup(admin)
    async with admin.bi() as bi:
        health = await bi.get("/health")
        check("GET /bi/health", health.status_code == 200, status=health.status_code)
        token = await csrf(bi)
        dashboard_id = dashboards["4"]
        read = await bi.get(f"/api/v1/dashboard/{dashboard_id}")
        check("signed-in dashboard read", read.status_code == 200, status=read.status_code)
        charts = await charts_of(bi, dashboard_id)
        data = await bi.post("/api/v1/chart/data", json=charts[0]["query_context"], headers={"X-CSRFToken": token})
        check("governed chart data through Nova's proxy", data.status_code == 200, status=data.status_code)
        current = read.json()["result"]
        save = await bi.put(f"/api/v1/dashboard/{dashboard_id}", json={"position_json": current["position_json"]},
                            headers={"X-CSRFToken": token})
        check("dashboard edit save", save.status_code == 200, status=save.status_code)
        state = await bi.post(f"/api/v1/dashboard/{dashboard_id}/filter_state?tab_id=1",
                              json={"value": json.dumps({})}, headers={"X-CSRFToken": token})
        key = state.json().get("key") if state.status_code == 201 else None
        back = await bi.get(f"/api/v1/dashboard/{dashboard_id}/filter_state/{key}") if key else None
        check("native filter state round trip", bool(back and back.status_code == 200), status=state.status_code)
        thumb = await bi.get(f"/api/v1/dashboard/{dashboard_id}/thumbnail/{current.get('digest', 'x')}/")
        check("thumbnail endpoint answers", thumb.status_code in (200, 202, 302), status=thumb.status_code)
    async with admin.nova() as nova:
        listing = await nova.get("/agents/studio/dataviz/dashboards")
        check("Nova Dataviz dashboard listing", listing.status_code == 200, status=listing.status_code)
        context = await nova.get("/agents/studio/dataviz/context", params={"dashboard_id": dashboard_id})
        check("Nova Dataviz context", context.status_code == 200 and context.json().get("available"),
              status=context.status_code)

    failed = [item for item in checks if not item["ok"]]
    print(json.dumps({"ok": not failed, "failed": len(failed), "checks": checks}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
