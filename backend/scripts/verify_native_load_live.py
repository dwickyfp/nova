"""Release QA on the live engine: paged tables over a large source, and query bursts.

Run against the development stack as a QA user (no administrator):

- Pages (NDV-CHART-016): a SQL dataset with about 262,000 distinct keys is read
  page by page. Pages are disjoint, in order, equal to the same SQL run through
  Nova's SQL endpoint with LIMIT/OFFSET, and stop at the 10,000-row offset cap.
  The API process's resident memory is sampled before and after.
- Bursts (NDV-CACHE-011): many chart queries at once from one person. Each one
  either answers or is refused with 429; none fails with a server error, the
  engine stays up, and a query afterwards answers normally.

Credentials come from NOVA_QA_OWNER_PASSWORD (alice). Everything created is
removed afterwards. Server-side process figures are read with ``ps`` on this host.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from scripts.verify_native_qa_live import Probe, dataset, login, number, use_role

#: Nine copies of the four-row sales table: 4**9 = 262,144 distinct keys.
COPIES = 9
PAGE = 1000
BURST = 40


def big_source() -> str:
    names = [f"t{i}" for i in range(COPIES)]
    key = ", '-', ".join(f"CAST({name}.id AS VARCHAR)" for name in names)
    joins = " CROSS JOIN ".join(f"analytics.sales {name}" for name in names)
    return f"SELECT CONCAT({key}) AS k, t0.amount AS amount FROM {joins}"


def api_rss_kib(port: int) -> int | None:
    """Resident memory of the process answering on ``port`` (the API worker)."""
    try:
        listening = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                                   capture_output=True, text=True, timeout=10).stdout.split()
        sizes = [int(subprocess.run(["ps", "-o", "rss=", "-p", pid], capture_output=True,
                                    text=True, timeout=10).stdout.strip() or 0)
                 for pid in listening]
        # The reloader and the worker both hold the socket; the worker is the larger.
        return max(sizes) if sizes else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def engine_running() -> bool:
    state = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", "nova-starrocks-fe"],
                           capture_output=True, text=True, timeout=20).stdout.strip()
    return state == "true"


async def run(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "result": "FAIL",
                              "cleanup": "NOT_RUN", "scenarios": {}, "copies": COPIES}
    created: list[str] = []
    space: str | None = None
    token: str | None = None
    async with httpx.AsyncClient(base_url=args.base_url, timeout=180) as client:
        probe = Probe(client)
        try:
            token = await login(probe, "alice", "NOVA_QA_OWNER_PASSWORD")
            await use_role(probe, token, "regional_manager", "author")
            space = (await probe.call("workspace", "POST", "/api/v1/workspaces/spaces", token,
                                      status=201, body={"name": f"QA load {uuid4().hex[:8]}"}
                                      )).json()["space"]["id"]
            big = await dataset(probe, token, space, big_source(), "Large QA")
            created.append(big["dataset"])
            path = f"/api/v1/viz/datasets/{big['dataset']}/query"
            base = {"revision": 1, "draft": False, "dimensions": [{"field_id": big["k"]}],
                    "measures": [{"field_id": big["amount"], "aggregation": "sum"}],
                    "sort": [{"column": "d0", "direction": "asc"}], "limit": PAGE}

            # ── Pages ──
            rss_before = api_rss_kib(args.api_port)
            seen: list[str] = []
            timings: list[float] = []
            offset: int | None = 0
            pages = 0
            while offset is not None and pages < args.pages:
                started = time.perf_counter()
                page = (await probe.call(f"page at {offset}", "POST", path, token,
                                         body={**base, "offset": offset})).json()
                timings.append(round(time.perf_counter() - started, 3))
                keys = [row[0]["value"] for row in page["rows"]]
                probe.check(f"page at {offset} has a full page in order",
                            len(keys) == PAGE and keys == sorted(keys))
                seen.extend(keys)
                offset = page.get("next_offset")
                pages += 1
            probe.check("pages never overlap", len(seen) == len(set(seen)), keys=len(seen))
            probe.check("each page follows the last", seen == sorted(seen))
            last = (await probe.call("page at the cap", "POST", path, token,
                                     body={**base, "offset": 10_000})).json()
            probe.check("the page at the offset cap offers no further page",
                        last.get("next_offset") is None and len(last["rows"]) == PAGE
                        and last.get("truncated") is True)
            beyond = await client.post(path, headers={"Authorization": f"Bearer {token}"},
                                       json={**base, "offset": 10_001})
            probe.steps.append({"request": "page past the cap", "status": beyond.status_code})
            probe.check("a page past the cap is refused", beyond.status_code == 422)
            # Nova's SQL endpoint returns at most 500 rows, so the oracle reads twice.
            want = []
            for start in (2 * PAGE, 2 * PAGE + PAGE // 2):
                oracle = (await probe.call(f"oracle rows at {start}", "POST",
                                           "/api/v1/query/execute", token, body={"sql": (
                                               f"SELECT k, SUM(amount) FROM ({big_source()}) q "
                                               f"GROUP BY k ORDER BY k LIMIT {PAGE // 2} "
                                               f"OFFSET {start}")})).json()[0]
                want.extend([row[0], number(row[1])] for row in oracle["rows"])
            third = (await probe.call("third page", "POST", path, token,
                                      body={**base, "offset": 2 * PAGE})).json()["rows"]
            got = [[row[0]["value"], number(row[1])] for row in third]
            probe.check("a page equals the same SQL with LIMIT and OFFSET", got == want,
                        rows=[len(got), len(want)],
                        first=json.loads(json.dumps([got[:2], want[:2]], default=str)),
                        differ=sum(1 for a, b in zip(got, want, strict=False) if a != b))
            rss_after = api_rss_kib(args.api_port)
            report["pages"] = {"read": pages + 2, "rows": len(seen), "seconds": timings,
                               "api_rss_kib_before": rss_before, "api_rss_kib_after": rss_after}
            if rss_before and rss_after:
                # Bounded: paging must not grow the API process by more than ~200 MB.
                probe.check("API memory stays bounded while paging",
                            rss_after - rss_before < 200_000,
                            growth_kib=rss_after - rss_before)
            report["scenarios"]["NDV-CHART-016"] = "PASS"

            # ── A burst of chart queries from one person ──
            async def one(index: int) -> int:
                response = await client.post(
                    path, headers={"Authorization": f"Bearer {token}"},
                    json={**base, "offset": (index * 97) % 9000, "limit": 100})
                return response.status_code

            started = time.perf_counter()
            statuses = await asyncio.gather(*(one(i) for i in range(args.burst)))
            counts = Counter(statuses)
            report["burst"] = {"requests": args.burst, "statuses": dict(counts),
                               "seconds": round(time.perf_counter() - started, 2)}
            probe.check("every burst query answers or is refused for capacity",
                        set(counts) <= {200, 429}, statuses=dict(counts))
            probe.check("the engine is still running after the burst", engine_running())
            after = (await probe.call("query after the burst", "POST", path, token,
                                      body={**base, "limit": 10})).json()
            probe.check("a query after the burst answers", len(after["rows"]) == 10)
            report["scenarios"]["NDV-CACHE-011"] = "PASS"
            report["result"] = "PASS"
        except Exception as error:  # noqa: BLE001 - recorded, never echoed with payloads
            report["error_type"] = type(error).__name__
            report["error"] = str(error)[:200]
        finally:
            if token:
                try:
                    for identity in created:
                        head = await client.get(f"/api/v1/viz/datasets/{identity}?draft=true",
                                                headers={"Authorization": f"Bearer {token}"})
                        if head.status_code == 200:
                            key, revision = str(uuid4()), head.json()["revision"]
                            await probe.call(
                                "delete dataset", "DELETE", f"/api/v1/viz/datasets/{identity}",
                                token, key=key, revision=revision,
                                body={"operation_id": key, "expected_revision": revision})
                    if space:
                        await probe.call("delete workspace", "DELETE",
                                         f"/api/v1/workspaces/spaces/{space}", token)
                    report["cleanup"] = "REMOVED"
                except Exception as error:  # noqa: BLE001
                    report.update(cleanup="UNVERIFIED", result="FAIL",
                                  cleanup_error_type=type(error).__name__)
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
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--pages", type=int, default=11)
    parser.add_argument("--burst", type=int, default=BURST)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({"result": report["result"], "cleanup": report["cleanup"],
                      "scenarios": report["scenarios"], "pages": report.get("pages"),
                      "burst": report.get("burst"), "error": report.get("error")}))
    raise SystemExit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
