"""Measured Native Dataviz performance on the live development stack (NDV-PERF-001/002/004/006).

Two synthetic tables are created in a run-owned database: ``orders`` with
``--rows`` orders (100,000 by default) behind the dashboard, and ``orders_1m``
with 1,000,000 orders behind the scrolled table. A QA author (alice, as
``marketing``) builds a SQL dataset over each, twelve charts and a published
dashboard with those twelve widgets. Before anything is timed, one widget query
must succeed, so the Ranger grant has reached the engine. Then, timed from the
client through the normal gateway (the browser's own path):

- NDV-PERF-002, shell: the published dashboard and its datasets are read
  ``--runs`` times; p95 must be under 1 s.
- NDV-PERF-001, warm dashboard: the shell plus all twelve widget queries,
  ``--runs`` times, sent the way the page sends them (three at a time per page,
  a 429 retried with the page's jittered backoff); p95 must be under 3 s with
  no failed widget.
- NDV-PERF-004, simultaneous viewers: ``--sessions`` separate sign-ins refresh
  the dashboard at the same moment, each with its own three-query queue. The
  query process's admission gauge is sampled throughout and must never exceed
  its cap; no request may fail with a server error, and at most 1% of widgets
  may stay unanswered after the page's retries (the error budget).
- NDV-PERF-006, a 1,000,000-row table scrolled: a table over every order is
  paged to the 10,000-row offset cap. Every response holds one page only, its
  size is bounded, the page past the cap is refused, and the query process's
  resident memory stays bounded.

This is development hardware (one host, Docker StarRocks, dev servers with
reload), recorded in the report; it is not a production capacity claim.
Credentials come from NOVA_QA_OWNER_PASSWORD (alice) and the administrator's
test password used by the StarRocks compatibility scripts; none is printed.
Everything created is removed afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import platform
import random
import re
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent / "starrocks_compat"))

from unload_live import Nova  # noqa: E402

from app.integrations.ranger.client import ranger_client  # noqa: E402
from app.integrations.ranger.compiler import compile_access_policy  # noqa: E402

RUN = uuid.uuid4().hex[:6]
DB = f"ndv_perf_{RUN}"
ROLE = "marketing"
BIG_ROWS = 1_000_000
PAGE_QUEUE = 3  # MAX_CONCURRENT_QUERIES in charts/query-queue.ts
RETRIES = 5  # capacityRetry in charts/query-queue.ts
TABLE_PAGE = 100
OFFSET_CAP = 10_000


def capacity_delay(attempt: int) -> float:
    """capacityDelay in charts/query-queue.ts, in seconds."""
    return (min(4000, 300 * 2 ** attempt) + random.randint(0, 199)) / 1000


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * len(ordered) + 0.5)) - 1)]


def summary(values: list[float]) -> dict[str, float]:
    return {"runs": len(values), "p50": round(statistics.median(values), 3),
            "p95": round(p95(values), 3), "max": round(max(values), 3),
            "mean": round(statistics.fmean(values), 3)}


def rss_kib(port: int) -> int | None:
    try:
        pids = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                              capture_output=True, text=True, timeout=10).stdout.split()
        sizes = [int(subprocess.run(["ps", "-o", "rss=", "-p", pid], capture_output=True,
                                    text=True, timeout=10).stdout.strip() or 0) for pid in pids]
        return max(sizes) if sizes else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def hardware() -> dict[str, Any]:
    def sysctl(name: str) -> str:
        try:
            return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    return {"os": platform.platform(), "cpu": sysctl("machdep.cpu.brand_string"),
            "cores": os.cpu_count(), "memory_bytes": sysctl("hw.memsize"),
            "engine": "StarRocks in Docker on this host", "servers": "dev.sh (uvicorn --reload)"}


class Probe:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.failed = False

    def check(self, step: str, passed: bool, **detail: Any) -> bool:
        self.steps.append({"step": step, "passed": bool(passed), **detail})
        self.failed |= not passed
        print(f"[{'PASS' if passed else 'FAIL'}] {step}", flush=True)
        return passed


class Session:
    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self.client, self.token = client, token

    async def call(self, method: str, path: str, *, body: Any = None, status: int = 200,
                   revision: int | None = None) -> Any:
        # A command's idempotency key is its operation id.
        key = (body or {}).get("operation_id") if isinstance(body, dict) else None
        headers = {"Authorization": f"Bearer {self.token}",
                   "Idempotency-Key": str(key or uuid.uuid4())}
        if revision is not None:
            headers["If-Match"] = f'"{revision}"'
        response = await self.client.request(method, path, headers=headers, json=body)
        if response.status_code != status:
            detail = response.json().get("detail") if response.content else None
            raise AssertionError(f"{method} {path.split('?')[0].rsplit('/', 1)[-1]} "
                                 f"returned {response.status_code}: {str(detail)[:160]}")
        return response.json() if response.content else None

    async def query(self, dataset: str, body: dict[str, Any]) -> tuple[int, int, int]:
        """One widget query as the page sends it: (final status, attempts, bytes)."""
        for attempt in range(RETRIES + 1):
            response = await self.client.post(
                f"/api/v1/viz/datasets/{dataset}/query", json=body,
                headers={"Authorization": f"Bearer {self.token}"})
            if response.status_code != 429 or attempt == RETRIES:
                return response.status_code, attempt + 1, len(response.content)
            await asyncio.sleep(capacity_delay(attempt))
        raise AssertionError("unreachable")


async def sign_in(client: httpx.AsyncClient) -> Session:
    response = await client.post("/api/v1/auth/login", json={
        "username": "alice", "password": os.environ["NOVA_QA_OWNER_PASSWORD"]})
    response.raise_for_status()
    session = Session(client, str(response.json()["access_token"]))
    await session.call("POST", "/api/v1/auth/switch-role", body={"role": ROLE})
    return session


def widget_queries(dataset: str, fields: dict[str, str]) -> list[tuple[str, dict[str, Any]]]:
    """The twelve charts' queries, exactly as each chart asks for its data."""
    def q(dimensions: list[dict[str, Any]], measures: list[tuple[str, str]],
          **extra: Any) -> dict[str, Any]:
        return {"revision": 1, "draft": False, "dimensions": dimensions,
                "measures": [{"field_id": fields[name], "aggregation": agg}
                             for name, agg in measures], "limit": 1000, **extra}

    month = {"field_id": fields["ordered_on"], "date_grain": "month"}
    return [
        ("Revenue by region", q([{"field_id": fields["region"]}], [("amount", "sum")])),
        ("Revenue by month", q([month], [("amount", "sum")])),
        ("Total revenue", q([], [("amount", "sum")])),
        ("Revenue share by channel", q([{"field_id": fields["channel"]}], [("amount", "sum")])),
        ("Revenue by product", q([{"field_id": fields["product"]}], [("amount", "sum")])),
        ("Units by product", q([{"field_id": fields["product"]}], [("units", "sum")])),
        ("Average order by month", q([month], [("amount", "avg")])),
        ("Orders", q([], [("id", "count")])),
        ("Revenue by region and channel",
         q([{"field_id": fields["region"]}, {"field_id": fields["channel"]}],
           [("amount", "sum")])),
        ("Units by region", q([{"field_id": fields["region"]}], [("units", "sum")])),
        ("Largest order by channel", q([{"field_id": fields["channel"]}], [("amount", "max")])),
        ("Products sold", q([], [("product", "count_distinct")])),
    ]


async def load_dashboard(session: Session, dashboard: str, dataset: str,
                         queries: list[tuple[str, dict[str, Any]]], *, shell_only: bool = False
                         ) -> tuple[float, list[tuple[int, int, int]]]:
    started = time.perf_counter()
    await session.call("GET", f"/api/v1/viz/dashboards/{dashboard}")
    await session.call("GET", f"/api/v1/viz/dashboards/{dashboard}/datasets?revision=1")
    if shell_only:
        return time.perf_counter() - started, []
    queue = asyncio.Semaphore(PAGE_QUEUE)

    async def one(body: dict[str, Any]) -> tuple[int, int, int]:
        async with queue:
            return await session.query(dataset, body)

    results = await asyncio.gather(*(one(body) for _, body in queries))
    return time.perf_counter() - started, list(results)


async def admission_peak(client: httpx.AsyncClient, port: int, stop: asyncio.Event,
                         seen: list[float]) -> None:
    url = f"http://127.0.0.1:{port}/metrics"
    while not stop.is_set():
        try:
            text = (await client.get(url)).text
            found = re.search(r"^nova_query_admission_active (\S+)$", text, re.M)
            if found:
                seen.append(float(found.group(1)))
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.05)


async def publish_dataset(author: Session, client: httpx.AsyncClient, table: str,
                          title: str) -> tuple[str, dict[str, str]]:
    identity = str(uuid.uuid4())
    source = {"kind": "sql", "database": DB, "sql":
              f"SELECT id, region, channel, product, ordered_on, amount, units FROM {table}"}
    inspected = None
    began = time.monotonic()
    while inspected is None:  # The Ranger grant reaches the FE within its poll interval.
        response = await client.post(
            "/api/v1/viz/datasets/inspect", json={"dataset_id": identity, "source": source},
            headers={"Authorization": f"Bearer {author.token}"})
        if response.status_code == 200:
            inspected = response.json()
        elif time.monotonic() - began > 90:
            raise AssertionError(f"inspect returned {response.status_code}")
        else:
            await asyncio.sleep(2)
    body = {"dataset_id": identity, "source": source, "workspace_id": "personal",
            "title": title, "expected_schema_fingerprint": inspected["schema_fingerprint"]}
    while True:  # Each process sees the new grant on its own engine sessions.
        try:
            await author.call("POST", "/api/v1/viz/datasets", status=201, body=body)
            break
        except AssertionError:
            if time.monotonic() - began > 180:
                raise
            await asyncio.sleep(3)
    await author.call("POST", f"/api/v1/viz/datasets/{identity}/publish", revision=1,
                      body={"operation_id": str(uuid.uuid4()), "expected_revision": 1})
    return identity, {item["source_column"]: item["field_id"] for item in inspected["fields"]}


async def until_readable(author: Session, dataset: str, body: dict[str, Any]) -> float:
    """Wait until a real query answers: inspection alone does not prove the grant."""
    began = time.monotonic()
    while True:
        status, _, _ = await author.query(dataset, body)
        if status == 200:
            return time.monotonic() - began
        if time.monotonic() - began > 180:
            raise AssertionError(f"the dataset query still returned {status}")
        await asyncio.sleep(3)


async def measure(args: argparse.Namespace, probe: Probe, report: dict[str, Any],
                  created: dict[str, list[str]]) -> None:
    limits = httpx.Limits(max_connections=max(64, args.sessions * PAGE_QUEUE))
    async with httpx.AsyncClient(base_url=args.base_url, timeout=120, limits=limits) as client:
        author = await sign_in(client)
        identity, fields = await publish_dataset(author, client, "orders", f"Perf orders {RUN}")
        created["datasets"].append(identity)
        big, big_fields = await publish_dataset(author, client, "orders_1m",
                                                f"Perf 1M orders {RUN}")
        created["datasets"].append(big)
        queries = widget_queries(identity, fields)
        report["grant_wait_seconds"] = round(await until_readable(author, identity,
                                                                  queries[2][1]), 1)
        widgets = []
        for index, (title, body) in enumerate(queries):
            chart = str(uuid.uuid4())
            dims, measures = body["dimensions"], body["measures"]
            if not dims:
                encoding: dict[str, Any] = {"kind": "single_measure", "measure": measures[0]}
                visual = "kpi"
            else:
                encoding = {"kind": "table", "dimensions": dims, "measures": measures,
                            "page_size": 100} if len(dims) > 1 else {
                    "kind": "cartesian", "dimensions": dims, "measures": measures}
                visual = "table" if len(dims) > 1 else "bar"
            await author.call("POST", "/api/v1/viz/charts", status=201, body={
                "workspace_id": "personal", "definition": {
                    "schema_version": 2, "chart_id": chart, "title": title,
                    "dataset_id": identity, "dataset_revision": 1, "visual": visual,
                    "encoding": encoding}})
            created["charts"].append(chart)
            await author.call("POST", f"/api/v1/viz/charts/{chart}/publish", revision=1,
                              body={"operation_id": str(uuid.uuid4()), "expected_revision": 1})
            widgets.append({"kind": "chart", "widget_id": str(uuid.uuid4()),
                            "layout": {"x": (index % 3) * 4, "y": (index // 3) * 4,
                                       "w": 4, "h": 4},
                            "chart_ref": {"chart_id": chart, "revision": 1}})
        dashboard = str(uuid.uuid4())
        await author.call("POST", "/api/v1/viz/dashboards", status=201, body={
            "workspace_id": "personal", "definition": {
                "dashboard_id": dashboard, "title": f"Perf 12 widgets {RUN}",
                "pages": [{"page_id": str(uuid.uuid4()), "title": "Overview",
                           "widgets": widgets}],
                "datasets": [{"dataset_id": identity, "revision": 1}]}})
        created["dashboards"].append(dashboard)
        await author.call("POST", f"/api/v1/viz/dashboards/{dashboard}/publish", revision=1,
                          body={"operation_id": str(uuid.uuid4()), "expected_revision": 1})

        # Warm-up: one full load, so "warm" means the engine and processes are warm.
        _, results = await load_dashboard(author, dashboard, identity, queries)
        probe.check("warm-up load answers every widget", all(s == 200 for s, _, _ in results),
                    statuses=[s for s, _, _ in results])

        # ── NDV-PERF-002: the shell with cached metadata ──
        shell = [(await load_dashboard(author, dashboard, identity, queries,
                                       shell_only=True))[0] for _ in range(args.runs)]
        report["NDV-PERF-002"] = summary(shell)
        probe.check("NDV-PERF-002 shell p95 under 1 s", p95(shell) < 1.0, **summary(shell))

        # ── NDV-PERF-001: twelve warm widgets ──
        timings, statuses, attempts = [], Counter(), Counter()
        for _ in range(args.runs):
            seconds, results = await load_dashboard(author, dashboard, identity, queries)
            timings.append(seconds)
            statuses.update(status for status, _, _ in results)
            attempts.update(tries for _, tries, _ in results)
        report["NDV-PERF-001"] = {**summary(timings), "widget_statuses": dict(statuses),
                                  "attempts_per_widget": dict(attempts)}
        probe.check("NDV-PERF-001 twelve-widget load p95 under 3 s", p95(timings) < 3.0,
                    **summary(timings))
        probe.check("NDV-PERF-001 no widget failed", set(statuses) == {200},
                    statuses=dict(statuses))

        # ── NDV-PERF-004: simultaneous viewers ──
        viewers = []
        for _ in range(args.sessions):
            viewers.append(await sign_in(client))
        stop, gauge = asyncio.Event(), []
        sampler = asyncio.create_task(admission_peak(client, args.query_port, stop, gauge))
        started = time.perf_counter()
        loads = await asyncio.gather(*(load_dashboard(viewer, dashboard, identity, queries)
                                       for viewer in viewers), return_exceptions=True)
        wall = time.perf_counter() - started
        stop.set()
        await sampler
        failures = [type(item).__name__ for item in loads if isinstance(item, BaseException)]
        finals = Counter(status for item in loads if not isinstance(item, BaseException)
                         for status, _, _ in item[1])
        tries = Counter(t for item in loads if not isinstance(item, BaseException)
                        for _, t, _ in item[1])
        widgets_total = args.sessions * len(queries)
        unanswered = widgets_total - finals.get(200, 0)
        load_times = [item[0] for item in loads if not isinstance(item, BaseException)]
        cap = int(args.admission_cap)
        report["NDV-PERF-004"] = {
            "sessions": args.sessions, "widgets": widgets_total, "wall_seconds": round(wall, 2),
            "final_statuses": dict(finals), "attempts_per_widget": dict(tries),
            "unanswered": unanswered, "unanswered_share": round(unanswered / widgets_total, 4),
            "load_seconds": summary(load_times) if load_times else None,
            "admission_samples": len(gauge), "admission_peak": max(gauge) if gauge else None,
            "session_errors": failures}
        probe.check("NDV-PERF-004 no request failed with a server error",
                    not failures and not any(status >= 500 for status in finals),
                    statuses=dict(finals), errors=failures[:5])
        probe.check("NDV-PERF-004 admission stays at or under its cap",
                    bool(gauge) and max(gauge) <= cap, peak=max(gauge) if gauge else None,
                    cap=cap)
        probe.check("NDV-PERF-004 unanswered widgets within the 1% error budget",
                    unanswered <= widgets_total * 0.01, unanswered=unanswered,
                    widgets=widgets_total)
        for viewer in viewers:
            with contextlib.suppress(httpx.HTTPError):
                await client.post("/api/v1/auth/logout",
                                  headers={"Authorization": f"Bearer {viewer.token}"})

        # ── NDV-PERF-006: a 1,000,000-row table scrolled ──
        body = {"revision": 1, "draft": False, "dimensions": [{"field_id": big_fields["id"]}],
                "measures": [{"field_id": big_fields["amount"], "aggregation": "sum"}],
                "sort": [{"column": "d0", "direction": "asc"}], "limit": TABLE_PAGE}
        rss_before = rss_kib(args.query_port)
        offset: int | None = 0
        pages, largest, seen = 0, 0, 0
        while offset is not None and offset <= OFFSET_CAP - TABLE_PAGE:
            response = await client.post(
                f"/api/v1/viz/datasets/{big}/query", json={**body, "offset": offset},
                headers={"Authorization": f"Bearer {author.token}"})
            if response.status_code == 429:
                await asyncio.sleep(0.3)
                continue
            page = response.json()
            if not probe.check(f"page at {offset} holds one page",
                               response.status_code == 200 and len(page["rows"]) == TABLE_PAGE,
                               status=response.status_code) or pages > 200:
                break
            largest = max(largest, len(response.content))
            seen += len(page["rows"])
            offset = page.get("next_offset")
            pages += 1
            if offset is not None and offset % 2000 != 0:
                offset = (offset // 2000 + 1) * 2000  # scroll in jumps, as a fast flick does
        beyond = await client.post(
            f"/api/v1/viz/datasets/{big}/query", json={**body, "offset": OFFSET_CAP + 1},
            headers={"Authorization": f"Bearer {author.token}"})
        rss_after = rss_kib(args.query_port)
        report["NDV-PERF-006"] = {
            "source_rows": BIG_ROWS, "pages_read": pages, "rows_received": seen,
            "largest_response_bytes": largest, "page_past_cap_status": beyond.status_code,
            "query_rss_kib_before": rss_before, "query_rss_kib_after": rss_after}
        probe.check("NDV-PERF-006 the browser receives pages, never the table",
                    seen <= OFFSET_CAP and largest < 64_000, rows=seen, largest_bytes=largest)
        probe.check("NDV-PERF-006 a page past the offset cap is refused",
                    beyond.status_code == 422)
        if rss_before and rss_after:
            probe.check("NDV-PERF-006 query process memory stays bounded",
                        rss_after - rss_before < 200_000, growth_kib=rss_after - rss_before)
        # The operator metrics (NDV-OPS-006) saw these reads with bounded labels only.
        text = (await client.get(f"http://127.0.0.1:{args.query_port}/metrics")).text
        labels = re.findall(r'^nova_dataviz_reads_total\{([^}]*)\}', text, re.M)
        report["operator_metrics"] = sorted(set(labels))
        probe.check("operator metrics count these reads with bounded labels",
                    bool(labels) and all(
                        set(re.findall(r'(\w+)="', item)) == {"operation", "outcome"}
                        for item in labels) and identity not in text and "alice" not in text,
                    labels=sorted(set(labels)))
        await client.post("/api/v1/auth/logout",
                          headers={"Authorization": f"Bearer {author.token}"})


async def cleanup(args: argparse.Namespace, created: dict[str, list[str]]) -> bool:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=120) as client:
        session = await sign_in(client)
        ok = True
        for collection in ("dashboards", "charts", "datasets"):
            for identity in created[collection]:
                head = await client.get(f"/api/v1/viz/{collection}/{identity}?draft=true",
                                        headers={"Authorization": f"Bearer {session.token}"})
                if head.status_code != 200:
                    continue
                revision = head.json()["revision"]
                try:
                    await session.call("DELETE", f"/api/v1/viz/{collection}/{identity}",
                                       revision=revision, body={
                                           "operation_id": str(uuid.uuid4()),
                                           "expected_revision": revision})
                except AssertionError:
                    ok = False
        await client.post("/api/v1/auth/logout",
                          headers={"Authorization": f"Bearer {session.token}"})
        return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--query-port", type=int, default=8001)
    parser.add_argument("--admission-cap", type=int, default=4)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--sessions", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    probe = Probe()
    report: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "run": RUN,
                              "dashboard_rows": args.rows, "hardware": hardware(),
                              "result": "FAIL", "cleanup": "NOT_RUN"}
    admin = Nova("nova_admin", "information_schema")
    policy = compile_access_policy(role=ROLE, catalog="default_catalog", database=DB,
                                   table="*", accesses=["select"])
    created: dict[str, list[str]] = {"dashboards": [], "charts": [], "datasets": []}
    try:
        started = time.perf_counter()
        def fill(table: str, rows: int) -> list[str]:
            return [
                f"CREATE TABLE {DB}.{table} (id BIGINT, region STRING, channel STRING, "
                "product STRING, ordered_on DATE, amount DECIMAL(12,2), units INT) "
                "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 4 "
                "PROPERTIES ('replication_num' = '1')",
                f"INSERT INTO {DB}.{table} SELECT generate_series, "
                "CASE generate_series % 4 WHEN 0 THEN 'North' WHEN 1 THEN 'South' "
                "WHEN 2 THEN 'East' ELSE 'West' END, "
                "CASE generate_series % 3 WHEN 0 THEN 'Online' WHEN 1 THEN 'Store' "
                "ELSE 'Partner' END, "
                "CONCAT('P', generate_series % 50), "
                "DATE_ADD('2024-01-01', generate_series % 730), "
                "CAST((generate_series % 1000) / 10 AS DECIMAL(12,2)), generate_series % 7 + 1 "
                f"FROM TABLE(generate_series(1, {rows}))"]

        for sql in [f"CREATE DATABASE {DB}", *fill("orders", args.rows),
                    *fill("orders_1m", BIG_ROWS)]:
            if not probe.check(f"fixture: {sql[:56]}", bool(admin.run(sql).get("success"))):
                raise AssertionError("fixture failed")
        for table, rows in (("orders", args.rows), ("orders_1m", BIG_ROWS)):
            count = admin.run(f"SELECT COUNT(*) FROM {DB}.{table}")["rows"][0][0]
            probe.check(f"fixture: {table} holds {rows} orders", int(count) == rows, rows=count)
        report["fixture_seconds"] = round(time.perf_counter() - started, 1)
        asyncio.run(ranger_client.put_policy(policy))
        asyncio.run(measure(args, probe, report, created))
        report["result"] = "FAIL" if probe.failed else "PASS"
    except Exception as error:  # noqa: BLE001 - recorded without payloads
        report["error_type"] = type(error).__name__
        report["error"] = str(error)[:200]
    finally:
        results = []
        try:
            results.append(asyncio.run(cleanup(args, created)))
            found = asyncio.run(ranger_client.get_policy(policy.name))
            if found is not None:
                asyncio.run(ranger_client.delete_policy(int(found["id"])))
            results.append(bool(admin.run(f"DROP DATABASE IF EXISTS {DB} FORCE").get("success")))
            report["cleanup"] = "REMOVED" if all(results) else "PARTIAL"
        except Exception as error:  # noqa: BLE001
            report["cleanup"] = "UNVERIFIED"
            report["cleanup_error_type"] = type(error).__name__
        if report["cleanup"] != "REMOVED":
            report["result"] = "FAIL"
        report["steps"] = probe.steps
        report["finished_at"] = datetime.now(UTC).isoformat()
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({key: report.get(key) for key in (
        "result", "cleanup", "error", "NDV-PERF-002", "NDV-PERF-001", "NDV-PERF-004",
        "NDV-PERF-006")}, default=str))
    raise SystemExit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
