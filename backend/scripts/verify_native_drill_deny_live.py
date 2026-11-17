"""Live NDV-INTER-009: drilling into detail the role may not read.

A run-owned table of orders is readable by ``marketing``. The QA author (alice,
as ``marketing``) builds a SQL dataset over it and reads region totals. Then a
Ranger deny policy removes SELECT on the ``id`` column for ``marketing`` (the
detail a drill from region to order would show). Through the normal gateway:

- the drilled query (orders of one region) is refused with 403;
- the refusal carries no rows and names no table, column or database;
- what the region level answers once the deny is active is recorded as
  observed, since the dataset reads every one of its fields.

Everything created is removed afterwards. Credentials come from
NOVA_QA_OWNER_PASSWORD and the administrator's test password used by the
StarRocks compatibility scripts; none is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent / "starrocks_compat"))

from unload_live import Nova  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.integrations.ranger.client import ranger_client  # noqa: E402
from app.integrations.ranger.compiler import (  # noqa: E402
    compile_access_policy,
    managed_name,
    table_resources,
)
from app.integrations.ranger.schemas import (  # noqa: E402
    RangerPolicy,
    RangerPolicyItem,
    RangerPolicyItemAccess,
)

RUN = uuid.uuid4().hex[:6]
DB = f"ndv_inter9_{RUN}"
ROLE = "marketing"


class Probe:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.failed = False

    def check(self, step: str, passed: bool, **detail: Any) -> bool:
        self.steps.append({"step": step, "passed": bool(passed), **detail})
        self.failed |= not passed
        print(f"[{'PASS' if passed else 'FAIL'}] {step}", flush=True)
        return passed


def deny_policy() -> RangerPolicy:
    return RangerPolicy(
        service=settings.RANGER_SERVICE_NAME,
        name=managed_name("qa-deny", ROLE, "default_catalog", DB, "orders", "id"),
        description="Nova QA drill: detail column denied.",
        resources=table_resources("default_catalog", DB, "orders", "id"),
        denyPolicyItems=[RangerPolicyItem(
            roles=[ROLE], accesses=[RangerPolicyItemAccess(type="select")])],
    )


class Client:
    def __init__(self, base_url: str) -> None:
        self.http = httpx.Client(base_url=base_url, timeout=120)
        self.token = ""

    def sign_in(self) -> None:
        response = self.http.post("/api/v1/auth/login", json={
            "username": "alice", "password": os.environ["NOVA_QA_OWNER_PASSWORD"]})
        response.raise_for_status()
        self.token = response.json()["access_token"]
        self.call("POST", "/api/v1/auth/switch-role", {"role": ROLE})

    def call(self, method: str, path: str, body: Any = None, *,
             revision: int | None = None) -> httpx.Response:
        key = (body or {}).get("operation_id") if isinstance(body, dict) else None
        headers = {"Authorization": f"Bearer {self.token}",
                   "Idempotency-Key": str(key or uuid.uuid4())}
        if revision is not None:
            headers["If-Match"] = f'"{revision}"'
        return self.http.request(method, path, headers=headers, json=body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    probe = Probe()
    report: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "run": RUN,
                              "result": "FAIL", "cleanup": "NOT_RUN"}
    admin = Nova("nova_admin", "information_schema")
    allow = compile_access_policy(role=ROLE, catalog="default_catalog", database=DB,
                                  table="*", accesses=["select"])
    deny = deny_policy()
    client = Client(args.base_url)
    dataset: str | None = None
    try:
        for sql in (
            f"CREATE DATABASE {DB}",
            f"CREATE TABLE {DB}.orders (id BIGINT, region STRING, amount DECIMAL(12,2)) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            "PROPERTIES ('replication_num' = '1')",
            f"INSERT INTO {DB}.orders VALUES (1,'North',10.50),(2,'North',4.00),"
            "(3,'South',7.25),(4,'South',1.00)",
        ):
            if not probe.check(f"fixture: {sql[:40]}", bool(admin.run(sql).get("success"))):
                raise AssertionError("fixture failed")
        asyncio.run(ranger_client.put_policy(allow))
        client.sign_in()
        identity = str(uuid.uuid4())
        source = {"kind": "sql", "database": DB, "sql": "SELECT id, region, amount FROM orders"}
        began = time.monotonic()
        while True:  # the grant reaches the engine within its poll interval
            inspected = client.call("POST", "/api/v1/viz/datasets/inspect",
                                    {"dataset_id": identity, "source": source})
            if inspected.status_code == 200 or time.monotonic() - began > 180:
                break
            time.sleep(3)
        inspection = inspected.json()
        created = client.call("POST", "/api/v1/viz/datasets", {
            "dataset_id": identity, "source": source, "workspace_id": "personal",
            "title": f"Drill deny {RUN}",
            "expected_schema_fingerprint": inspection["schema_fingerprint"]})
        probe.check("author builds the dataset while the table is readable",
                    created.status_code == 201, status=created.status_code)
        dataset = identity
        fields = {item["source_column"]: item["field_id"] for item in inspection["fields"]}

        def query(drilled: bool) -> httpx.Response:
            body: dict[str, Any] = {
                "revision": 1, "draft": True, "limit": 1000,
                "dimensions": [{"field_id": fields["id" if drilled else "region"]}],
                "measures": [{"field_id": fields["amount"], "aggregation": "sum"}]}
            if drilled:
                body["filters"] = {"expression": {
                    "type": "predicate", "op": "eq",
                    "field": {"dataset_id": identity, "dataset_revision": 1,
                              "field_id": fields["region"]},
                    "values": [{"type": "string", "value": "North"}]}}
            return client.call("POST", f"/api/v1/viz/datasets/{identity}/query", body)

        before = query(drilled=False)
        probe.check("region totals answer before the deny", before.status_code == 200
                    and len(before.json().get("rows", [])) == 2, status=before.status_code)
        drilled_before = query(drilled=True)
        probe.check("the drilled orders answer before the deny",
                    drilled_before.status_code == 200
                    and len(drilled_before.json().get("rows", [])) == 2,
                    status=drilled_before.status_code)

        asyncio.run(ranger_client.put_policy(deny))
        began = time.monotonic()
        refused = query(drilled=True)
        while refused.status_code == 200 and time.monotonic() - began < 180:
            time.sleep(3)
            refused = query(drilled=True)
        report["deny_seconds"] = round(time.monotonic() - began, 1)
        body = refused.json()
        detail = json.dumps(body)
        probe.check("the drilled query is refused once the detail is denied",
                    refused.status_code == 403, status=refused.status_code)
        probe.check("the refusal carries no rows", "rows" not in body)
        probe.check("the refusal names no table, column or database",
                    all(word not in detail for word in ("orders", DB, '"id"', "column")),
                    detail=body.get("detail"))
        after = query(drilled=False)
        report["region_level_after_deny"] = {
            "status": after.status_code,
            "rows": len(after.json().get("rows", [])) if after.status_code == 200 else 0,
            "note": "observed; the dataset reads every field, so a denied field can make the "
                    "whole dataset unreadable for the role"}
        probe.check("the region level never shows the denied detail",
                    after.status_code in (200, 403) and (
                        after.status_code == 403
                        or all(len(row) == 2 for row in after.json().get("rows", []))),
                    status=after.status_code)
        report["result"] = "FAIL" if probe.failed else "PASS"
    except Exception as error:  # noqa: BLE001 - recorded without payloads
        report["error"] = f"{type(error).__name__}: {str(error)[:160]}"
    finally:
        cleaned = []
        try:
            if dataset is not None:
                head = client.call("GET", f"/api/v1/viz/datasets/{dataset}?draft=true")
                if head.status_code == 200:
                    revision = head.json()["revision"]
                    gone = client.call("DELETE", f"/api/v1/viz/datasets/{dataset}", {
                        "operation_id": str(uuid.uuid4()), "expected_revision": revision},
                        revision=revision)
                    cleaned.append(gone.status_code in (200, 204))
            client.call("POST", "/api/v1/auth/logout")
            for policy in (deny, allow):
                found = asyncio.run(ranger_client.get_policy(policy.name))
                if found is not None:
                    asyncio.run(ranger_client.delete_policy(int(found["id"])))
            cleaned.append(bool(admin.run(f"DROP DATABASE IF EXISTS {DB} FORCE").get("success")))
            report["cleanup"] = "REMOVED" if all(cleaned) else "PARTIAL"
        except Exception as error:  # noqa: BLE001
            report["cleanup"] = f"UNVERIFIED: {type(error).__name__}"
        if report["cleanup"] != "REMOVED":
            report["result"] = "FAIL"
        report["steps"] = probe.steps
        report["finished_at"] = datetime.now(UTC).isoformat()
        args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({k: report.get(k) for k in (
        "result", "cleanup", "error", "deny_seconds", "region_level_after_deny")}))
    raise SystemExit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
