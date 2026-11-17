"""Live process drills for Native Dataviz (NDV-OPS-003, NDV-OPS-008).

The development stack keeps running untouched. The drill starts its own extra
web and query processes on spare ports, with the same configuration, shared
session store and metadata as the normal ones, and stops them afterwards.

NDV-OPS-008, two web replicas: the QA author signs in through the normal web
process; the same session then reads the same dataset document through a second
web process and gets an identical document. After the author switches to a role
without access on one replica, both replicas refuse the dataset query with the
same status and body.

NDV-OPS-003, a query process killed mid-load: twelve widget queries run against
the extra query process, which is killed with SIGKILL while they are in flight.
Every widget ends on its own (an answer or an error, never a hang), the
replacement process answers all twelve, and the author's engine sessions return
to the baseline count.

Credentials come from NOVA_QA_OWNER_PASSWORD, the Ranger settings in the
environment and the administrator's test password used by the StarRocks
compatibility scripts. None is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent / "starrocks_compat"))

from unload_live import Nova  # noqa: E402

from app.integrations.ranger.client import ranger_client  # noqa: E402
from app.integrations.ranger.compiler import compile_access_policy  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
RUN = uuid.uuid4().hex[:6]
DB = f"ndv_ops_{RUN}"
ROLE, OTHER_ROLE = "marketing", "finance"
WIDGETS = 12


class Probe:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.failed = False

    def check(self, step: str, passed: bool, **detail: Any) -> bool:
        self.steps.append({"step": step, "passed": bool(passed), **detail})
        self.failed |= not passed
        print(f"[{'PASS' if passed else 'FAIL'}] {step}", flush=True)
        return passed


def start(role: str, port: int, log: Path) -> subprocess.Popen[bytes]:
    env = {**os.environ, "NOVA_PROCESS_ROLE": role, "PROXY_ENABLED": "false",
           "ML_TRAINING_EXECUTOR": "worker", "NOVA_METRICS_HOST": "127.0.0.1"}
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port)],
        cwd=BACKEND, env=env, stdout=log.open("ab"), stderr=subprocess.STDOUT,
        start_new_session=True)


def ready(port: int, limit: float = 180) -> bool:
    began = time.monotonic()
    while time.monotonic() - began < limit:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/metrics", timeout=5).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


def stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)


class Session:
    def __init__(self, token: str) -> None:
        self.token = token

    def headers(self, key: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}",
                "Idempotency-Key": key or str(uuid.uuid4())}


def sign_in(base: str) -> Session:
    response = httpx.post(f"{base}/api/v1/auth/login", timeout=60, json={
        "username": "alice", "password": os.environ["NOVA_QA_OWNER_PASSWORD"]})
    response.raise_for_status()
    session = Session(response.json()["access_token"])
    switch(base, session, ROLE)
    return session


def switch(base: str, session: Session, role: str) -> None:
    httpx.post(f"{base}/api/v1/auth/switch-role", headers=session.headers(),
               json={"role": role}, timeout=60).raise_for_status()


def engine_sessions(admin: Nova) -> int:
    result = admin.run("SHOW FULL PROCESSLIST")
    user = result["columns"].index("User")
    return sum(1 for row in result.get("rows") or [] if row[user] == "alice")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-url", default="http://127.0.0.1:8000")
    parser.add_argument("--web-port", type=int, default=8010)
    parser.add_argument("--query-port", type=int, default=8011)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    probe = Probe()
    report: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "run": RUN,
                              "result": "FAIL", "cleanup": "NOT_RUN"}
    admin = Nova("nova_admin", "information_schema")
    allow = compile_access_policy(role=ROLE, catalog="default_catalog", database=DB,
                                  table="*", accesses=["select"])
    web_b = query_b = None
    session: Session | None = None
    dataset: str | None = None
    replica = f"http://127.0.0.1:{args.web_port}"
    query_url = f"http://127.0.0.1:{args.query_port}"
    try:
        for sql in (
            f"CREATE DATABASE {DB}",
            f"CREATE TABLE {DB}.orders (id BIGINT, region STRING, amount DECIMAL(12,2)) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            "PROPERTIES ('replication_num' = '1')",
            f"INSERT INTO {DB}.orders SELECT generate_series, "
            "CASE generate_series % 4 WHEN 0 THEN 'North' WHEN 1 THEN 'South' "
            "WHEN 2 THEN 'East' ELSE 'West' END, generate_series % 97 "
            "FROM TABLE(generate_series(1, 20000))",
        ):
            if not probe.check(f"fixture: {sql[:40]}", bool(admin.run(sql).get("success"))):
                raise AssertionError("fixture failed")
        asyncio.run(ranger_client.put_policy(allow))
        web_b = start("web", args.web_port, args.log)
        query_b = start("query", args.query_port, args.log)
        if not probe.check("the extra web and query processes start",
                           ready(args.web_port) and ready(args.query_port)):
            raise AssertionError("extra processes did not start")

        session = sign_in(args.web_url)
        identity = str(uuid.uuid4())
        source = {"kind": "sql", "database": DB, "sql": "SELECT id, region, amount FROM orders"}
        began = time.monotonic()
        while True:  # the grant reaches the engine within its poll interval
            inspected = httpx.post(f"{query_url}/api/v1/viz/datasets/inspect", timeout=120,
                                   headers=session.headers(),
                                   json={"dataset_id": identity, "source": source})
            if inspected.status_code == 200 or time.monotonic() - began > 180:
                break
            time.sleep(3)
        inspection = inspected.json()
        operation = str(uuid.uuid4())
        began = time.monotonic()
        while True:  # a busy engine answers 503/504; the same operation is retried
            created = httpx.post(f"{args.web_url}/api/v1/viz/datasets", timeout=120,
                                 headers=session.headers(operation), json={
                                     "dataset_id": identity, "source": source,
                                     "workspace_id": "personal", "title": f"Process drill {RUN}",
                                     "expected_schema_fingerprint":
                                         inspection["schema_fingerprint"]})
            if created.status_code not in (429, 503, 504) or time.monotonic() - began > 180:
                break
            time.sleep(5)
        probe.check("the author builds the dataset", created.status_code == 201,
                    status=created.status_code)
        dataset = identity
        fields = {item["source_column"]: item["field_id"] for item in inspection["fields"]}
        body = {"revision": 1, "draft": True, "limit": 1000,
                "dimensions": [{"field_id": fields["region"]}],
                "measures": [{"field_id": fields["amount"], "aggregation": "sum"}]}

        # ── NDV-OPS-008: one session, two web replicas ──
        path = f"/api/v1/viz/datasets/{identity}?draft=true"
        first = httpx.get(args.web_url + path, headers=session.headers(), timeout=60)
        second = httpx.get(replica + path, headers=session.headers(), timeout=60)
        probe.check("both web replicas return the identical document",
                    first.status_code == second.status_code == 200
                    and first.json() == second.json())
        missing = f"/api/v1/viz/datasets/{uuid.uuid4()}?draft=true"
        absent = [httpx.get(base + missing, headers=session.headers(), timeout=60)
                  for base in (args.web_url, replica)]
        probe.check("both replicas refuse an unknown document identically",
                    absent[0].status_code == absent[1].status_code
                    and absent[0].json() == absent[1].json(),
                    status=absent[0].status_code)
        switch(args.web_url, session, OTHER_ROLE)
        refused = [httpx.post(f"{base}/api/v1/viz/datasets/{identity}/query", timeout=120,
                              headers=session.headers(), json=body)
                   for base in (f"http://127.0.0.1:{8001}", query_url)]
        probe.check("after a role switch on one replica, both query processes refuse alike",
                    refused[0].status_code == refused[1].status_code
                    and refused[0].status_code in (403, 404)
                    and refused[0].json() == refused[1].json(),
                    statuses=[item.status_code for item in refused])
        switch(replica, session, ROLE)
        back = httpx.post(f"{args.web_url.replace('8000', '8001')}/api/v1/viz/datasets/"
                          f"{identity}/query", headers=session.headers(), json=body, timeout=120)
        probe.check("a role switch on the second replica is seen by the first",
                    back.status_code == 200 and len(back.json().get("rows", [])) == 4,
                    status=back.status_code)

        # ── NDV-OPS-003: a query process killed mid-load ──
        baseline = engine_sessions(admin)
        report["engine_sessions_baseline"] = baseline

        async def load(kill_after: float | None, *, retry_for: float = 0) -> list[str]:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90)) as client:
                async def widget() -> str:
                    # As a dashboard does: a busy answer (429, 503, 504) is
                    # retried with backoff within the budget; anything else
                    # is the widget's own outcome.
                    began, attempt = time.monotonic(), 0
                    while True:
                        try:
                            response = await client.post(
                                f"{query_url}/api/v1/viz/datasets/{identity}/query",
                                headers=session.headers(), json=body)
                        except httpx.HTTPError as error:
                            return type(error).__name__
                        if (response.status_code not in (429, 503, 504)
                                or time.monotonic() - began > retry_for):
                            return str(response.status_code)
                        attempt += 1
                        await asyncio.sleep(min(0.5 * 2 ** attempt, 15))

                tasks = [asyncio.create_task(widget()) for _ in range(WIDGETS)]
                if kill_after is not None:
                    await asyncio.sleep(kill_after)
                    report["engine_sessions_in_flight"] = await asyncio.to_thread(
                        engine_sessions, admin)
                    os.killpg(query_b.pid, signal.SIGKILL)
                return list(await asyncio.gather(*tasks))

        began = time.monotonic()
        outcomes = asyncio.run(load(kill_after=0.5))
        report["killed_load"] = {"outcomes": outcomes,
                                 "seconds": round(time.monotonic() - began, 1)}
        probe.check("every widget ends on its own when the query process dies",
                    len(outcomes) == WIDGETS and time.monotonic() - began < 95,
                    outcomes=sorted(set(outcomes)))
        probe.check("the killed process's in-flight widgets fail rather than hang",
                    any(item != "200" for item in outcomes))
        query_b.wait(30)
        query_b = start("query", args.query_port, args.log)
        probe.check("a replacement query process starts", ready(args.query_port))
        began = time.monotonic()
        outcomes = asyncio.run(load(kill_after=None, retry_for=300))
        report["recovered_load"] = {"outcomes": outcomes,
                                    "seconds": round(time.monotonic() - began, 1),
                                    "host_load": os.getloadavg()}
        probe.check("the replacement answers every widget",
                    outcomes == ["200"] * WIDGETS, outcomes=sorted(set(outcomes)))
        began = time.monotonic()
        current = engine_sessions(admin)
        while current > baseline and time.monotonic() - began < 120:
            time.sleep(5)
            current = engine_sessions(admin)
        report["engine_sessions_after"] = current
        probe.check("the author's engine sessions return to the baseline",
                    current <= baseline, baseline=baseline, after=current)
        report["result"] = "FAIL" if probe.failed else "PASS"
    except Exception as error:  # noqa: BLE001 - recorded without payloads
        report["error"] = f"{type(error).__name__}: {str(error)[:160]}"
    finally:
        cleaned = []
        try:
            if session is not None and dataset is not None:
                switch(args.web_url, session, ROLE)
                head = httpx.get(f"{args.web_url}/api/v1/viz/datasets/{dataset}?draft=true",
                                 headers=session.headers(), timeout=60)
                if head.status_code == 200:
                    revision = head.json()["revision"]
                    operation = str(uuid.uuid4())
                    gone = httpx.request(
                        "DELETE", f"{args.web_url}/api/v1/viz/datasets/{dataset}",
                        headers={**session.headers(operation), "If-Match": f'"{revision}"'},
                        json={"operation_id": operation, "expected_revision": revision},
                        timeout=60)
                    cleaned.append(gone.status_code in (200, 204))
            if session is not None:
                httpx.post(f"{args.web_url}/api/v1/auth/logout", headers=session.headers(),
                           timeout=30)
            found = asyncio.run(ranger_client.get_policy(allow.name))
            if found is not None:
                asyncio.run(ranger_client.delete_policy(int(found["id"])))
            cleaned.append(bool(admin.run(f"DROP DATABASE IF EXISTS {DB} FORCE").get("success")))
            report["cleanup"] = "REMOVED" if all(cleaned) else "PARTIAL"
        except Exception as error:  # noqa: BLE001
            report["cleanup"] = f"UNVERIFIED: {type(error).__name__}"
        stop(web_b)
        stop(query_b)
        if report["cleanup"] != "REMOVED":
            report["result"] = "FAIL"
        report["steps"] = probe.steps
        report["finished_at"] = datetime.now(UTC).isoformat()
        args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({k: report.get(k) for k in ("result", "cleanup", "error")}))
    raise SystemExit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
