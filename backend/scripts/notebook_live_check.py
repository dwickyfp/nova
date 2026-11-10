"""Live check of Nova Notebook through the HTTP API, as a signed-in user.

Run against a local dev stack (backend on :8000, worker and OpenSandbox up)::

    NOVA_USER=nova_admin NOVA_PASSWORD=... uv run --no-sync python scripts/notebook_live_check.py

It creates a notebook in the user's Workspace, starts its runtime, runs SQL and
Python cells (local DuckDB and governed Nova data), follows the event stream,
and stops the runtime. The password is read from the environment and never
printed. Exit code 0 means every check passed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

import httpx

BASE = os.environ.get("NOVA_API", "http://127.0.0.1:8000/api/v1")


class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
        if not ok:
            self.failed += 1


async def login(client: httpx.AsyncClient) -> None:
    password = os.environ["NOVA_PASSWORD"].strip().strip("'\"")
    response = await client.post(
        f"{BASE}/auth/login",
        json={"username": os.environ.get("NOVA_USER", "nova_admin"), "password": password},
    )
    response.raise_for_status()
    body = response.json()
    client.headers["Authorization"] = f"Bearer {body['access_token']}"
    role = os.environ.get("NOVA_ROLE")
    if role:
        (await client.post(f"{BASE}/auth/switch-role", json={"role": role})).raise_for_status()


async def wait_runtime(client, notebook_id: str, want: set[str], timeout: float = 240) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = (await client.get(f"{BASE}/notebooks/{notebook_id}/runtime")).json()["runtime"]
        if last.get("status") in want:
            return last
        if last.get("status") in {"FAILED", "LOST"}:
            return last
        await asyncio.sleep(1.5)
    return last


async def run_and_wait(client, notebook_id: str, revision: int, cell_id: str,
                       timeout: float = 120) -> dict:
    response = await client.post(
        f"{BASE}/notebooks/{notebook_id}/executions",
        json={"mode": "cell", "cell_id": cell_id, "base_revision": revision},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    response.raise_for_status()
    execution_id = response.json()["executions"][0]["execution_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        latest = (await client.get(f"{BASE}/notebooks/{notebook_id}/executions/latest")).json()
        for item in latest["executions"]:
            if item["execution_id"] == execution_id:
                return item
        await asyncio.sleep(1)
    return {"status": "timeout"}


async def main() -> int:
    checks = Checks()
    async with httpx.AsyncClient(timeout=60) as client:
        await login(client)
        name = f"live-check-{uuid.uuid4().hex[:6]}.ipynb"
        created = await client.post(f"{BASE}/notebooks", json={"parent_path": "", "name": name})
        checks.check("create notebook", created.status_code == 201, str(created.status_code))
        notebook = created.json()
        notebook_id = notebook["entry"]["id"]
        revision = notebook["notebook"]["revision"]
        cells = [
            {"kind": "python", "source": "db.sql('SELECT 1 AS ready').fetchall()", "id": "c-ready"},
            {"kind": "python", "source": "import duckdb\nduckdb.sql('SELECT 41 + 1').fetchall()",
             "id": "c-default"},
            {"kind": "sql", "name": "regions", "id": "c-sql",
             "source": "SELECT * FROM (VALUES ('Jakarta', 100), ('Bandung', 200)) "
                       "t(region, amount)"},
            {"kind": "python", "id": "c-py",
             "source": "print('rows', regions.df().shape[0])\nregions.df()"},
            {"kind": "sql", "id": "c-ref", "name": "big",
             "source": "SELECT region FROM {{regions}} WHERE amount > 150"},
            {"kind": "sql", "id": "c-nova", "name": "roles",
             "source": "SELECT CURRENT_ROLE() AS role, 1 + 1 AS two", "engine": "nova"},
            {"kind": "sql", "id": "c-nova-scan",
             "source": "SELECT count(*) AS schemas FROM information_schema.schemata "
                       "WHERE catalog_name = 'nova'"},
            {"kind": "sql", "id": "c-write", "engine": "nova",
             "source": "CREATE TABLE should_not_exist (x INT)"},
            {"kind": "python", "id": "c-error", "source": "1/0"},
            {"kind": "python", "id": "c-plot",
             "source": "import matplotlib.pyplot as plt\nplt.plot([1, 3, 2]); plt.show()"},
        ]
        edit = await client.patch(
            f"{BASE}/notebooks/{notebook_id}",
            json={"base_revision": revision, "edits": [
                {"op": "insert", "index": 99, "cell": cell} for cell in cells
            ]},
        )
        checks.check("add cells", edit.status_code == 200, edit.text[:200])
        revision = edit.json()["notebook"]["revision"]

        started = time.monotonic()
        start = await client.post(f"{BASE}/notebooks/{notebook_id}/runtime")
        checks.check("start runtime accepted", start.status_code == 202, start.text[:200])
        again = await client.post(f"{BASE}/notebooks/{notebook_id}/runtime")
        checks.check(
            "second start reuses the runtime",
            again.json()["runtime"]["runtime_id"] == start.json()["runtime"]["runtime_id"],
        )
        runtime = await wait_runtime(client, notebook_id, {"READY"})
        checks.check("runtime ready", runtime.get("status") == "READY",
                     f"{runtime.get('status')} in {time.monotonic() - started:.1f}s "
                     f"{runtime.get('error') or ''}")
        checks.check("nova attached", bool(runtime.get("nova_attached")),
                     str(runtime.get("nova_error")))
        if runtime.get("status") != "READY":
            return 1

        results = {}
        for cell in cells:
            results[cell["id"]] = await run_and_wait(client, notebook_id, revision, cell["id"])

        def first(outputs, kind):
            return next((o for o in outputs or [] if o.get("type") == kind), None)

        r = results["c-ready"]
        checks.check("db.sql works with no setup", r["status"] == "succeeded"
                     and "(1,)" in json.dumps(r["outputs"]), json.dumps(r["outputs"])[:200])
        r = results["c-default"]
        checks.check("duckdb.sql uses the same default", r["status"] == "succeeded"
                     and "42" in json.dumps(r["outputs"]), json.dumps(r["outputs"])[:200])
        r = results["c-sql"]
        table = first(r["outputs"], "table")
        checks.check("SQL cell returns a named table", r["status"] == "succeeded" and table
                     and table["table"]["rows"] == [["Jakarta", 100], ["Bandung", 200]],
                     json.dumps(r["outputs"])[:300])
        r = results["c-py"]
        checks.check("Python reads the SQL result", r["status"] == "succeeded"
                     and first(r["outputs"], "table") is not None
                     and "rows 2" in json.dumps(r["outputs"]), json.dumps(r["outputs"])[:300])
        r = results["c-ref"]
        table = first(r["outputs"], "table")
        checks.check("{{ref}} chains SQL to SQL", table is not None
                     and table["table"]["rows"] == [["Bandung"]],
                     f"{r['status']} {r.get('error_message')} {json.dumps(r['outputs'])[:300]}")
        r = results["c-nova"]
        table = first(r["outputs"], "table")
        checks.check("Nova engine runs as the active role", table is not None
                     and table["table"]["origin"] == "nova", json.dumps(r["outputs"])[:300])
        print("      role seen by Nova:", table and table["table"]["rows"])
        r = results["c-nova-scan"]
        table = first(r["outputs"], "table")
        checks.check("DuckDB lists governed nova schemas", table is not None
                     and (table["table"]["rows"][0][0] or 0) > 0, json.dumps(r["outputs"])[:300])
        r = results["c-write"]
        checks.check("writes to Nova are refused", r["status"] == "failed",
                     (r.get("error_message") or "")[:200])
        r = results["c-error"]
        error = first(r["outputs"], "error")
        checks.check("traceback reported", r["status"] == "failed" and error
                     and error["ename"] == "ZeroDivisionError")
        r = results["c-plot"]
        checks.check("matplotlib image captured", first(r["outputs"], "image") is not None,
                     json.dumps([o.get("type") for o in r["outputs"] or []]))

        metrics = (await client.get(f"{BASE}/notebooks/{notebook_id}/runtime/metrics")).json()
        checks.check("metrics", bool(metrics.get("metrics")), json.dumps(metrics))

        stop = await client.post(f"{BASE}/notebooks/{notebook_id}/runtime/stop")
        checks.check("stop accepted", stop.status_code == 202)
        runtime = await wait_runtime(client, notebook_id, {"STOPPED", "ABSENT"}, timeout=60)
        checks.check("runtime stopped", runtime.get("status") == "STOPPED", str(runtime))
        if os.environ.get("KEEP_NOTEBOOK") != "1":
            await client.delete(f"{BASE}/workspaces/files/{notebook_id}")
    print(f"\n{'ALL PASSED' if not checks.failed else f'{checks.failed} FAILED'}")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
