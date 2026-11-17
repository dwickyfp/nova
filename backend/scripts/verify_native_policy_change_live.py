"""A native chart query follows Ranger policy changes made while a viewer works.

``alice`` reads a native dataset over a disposable table as ``marketing``. A
steward then changes Ranger, without touching her role or session: first a row
filter, then a column mask, then the access policy is removed. After each
change the next governed chart query must show the new result, and the time
until it does is recorded (the FE polls Ranger every few seconds). The browser
side, which may show rows it read for up to 30 seconds and never reuses rows
for downloads, is covered by the frontend tests.

Usage (from backend/; passwords are read here and never printed):
    UV_NO_SYNC=1 STARROCKS_FE_MYSQL_PORT=29030 RANGER_USERNAME=admin \
    RANGER_TLS_VERIFY=false RANGER_PASSWORD=... \
        uv run python scripts/verify_native_policy_change_live.py \
        --output ../docs/reports/native-dataviz/policy-change-live-attempt-1.json

Every table, policy, dataset and workspace it creates is removed afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "starrocks_compat"))

from unload_live import Nova  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.integrations.ranger.client import ranger_client  # noqa: E402
from app.integrations.ranger.compiler import (  # noqa: E402
    POLICY_TYPE_ROWFILTER,
    compile_access_policy,
    compile_mask_policy,
    managed_name,
    table_resources,
)
from app.integrations.ranger.schemas import (  # noqa: E402
    RangerPolicy,
    RangerPolicyItemAccess,
    RangerRowFilterInfo,
    RangerRowFilterPolicyItem,
)

RUN = uuid.uuid4().hex[:6]
DB = f"ndv_policy_{RUN}"
ROLE = "marketing"
#: Longest wait for one policy change to reach the engine before failing.
PROPAGATION_LIMIT_SECONDS = 60


class Probe:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.failed = False

    def check(self, step: str, passed: bool, **detail: Any) -> bool:
        self.steps.append({"step": step, "passed": bool(passed), **detail})
        self.failed |= not passed
        print(f"[{'PASS' if passed else 'FAIL'}] {step}", flush=True)
        return passed


def row_filter(expression: str) -> RangerPolicy:
    return RangerPolicy(
        service=settings.RANGER_SERVICE_NAME,
        name=managed_name("scope", ROLE, "default_catalog", DB, "regions"),
        description="Disposable policy-change acceptance filter.",
        policyType=POLICY_TYPE_ROWFILTER,
        resources=table_resources("default_catalog", DB, "regions"),
        rowFilterPolicyItems=[RangerRowFilterPolicyItem(
            roles=[ROLE], accesses=[RangerPolicyItemAccess(type="select")],
            rowFilterInfo=RangerRowFilterInfo(filterExpr=expression))],
    )


def put(policy: RangerPolicy, names: list[str]) -> None:
    asyncio.run(ranger_client.put_policy(policy))
    if policy.name not in names:
        names.append(policy.name)


def remove(names: list[str], only: str | None = None) -> None:
    async def go() -> None:
        for name in list(names):
            if only and name != only:
                continue
            found = await ranger_client.get_policy(name)
            if found is not None:
                await ranger_client.delete_policy(int(found["id"]))
            names.remove(name)

    asyncio.run(go())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    probe = Probe()
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(), "run": RUN,
        "scope": "governed native chart query after Ranger row filter, mask and revoke",
        "result": "FAIL", "cleanup": "NOT_RUN",
    }
    policies: list[str] = []
    admin = Nova("nova_admin", "information_schema")
    alice = Nova("alice", DB)
    space_id: str | None = None
    dataset = str(uuid.uuid4())
    board = str(uuid.uuid4())
    path = f"/api/v1/viz/datasets/{dataset}"
    try:
        for sql in (f"CREATE DATABASE {DB}",
                    f"CREATE TABLE {DB}.regions (city STRING, amount DECIMAL(10,2)) "
                    "DISTRIBUTED BY HASH(city) BUCKETS 1 PROPERTIES ('replication_num' = '1')",
                    f"INSERT INTO {DB}.regions VALUES ('Jakarta', 400.00), ('Bandung', 600.00)"):
            probe.check(f"fixture: {sql.split(' (')[0][:60]}", bool(admin.run(sql).get("success")))
        access = compile_access_policy(role=ROLE, catalog="default_catalog", database=DB,
                                       table="*", accesses=["select"])
        put(access, policies)
        switched = alice.client.post("/api/v1/auth/switch-role", headers=alice.headers,
                                     json={"role": ROLE})
        probe.check("alice uses marketing", switched.status_code == 200
                    and switched.json().get("active_role") == ROLE)

        def request(method: str, url: str, **kw: Any):
            return alice.client.request(method, url, headers={**alice.headers,
                                                              **kw.pop("headers", {})}, **kw)

        space = request("POST", "/api/v1/workspaces/spaces",
                        json={"name": f"Policy change {RUN}"})
        probe.check("shared workspace", space.status_code == 201)
        space_id = str(space.json()["space"]["id"])
        source = {"kind": "sql", "database": DB, "sql": f"SELECT city, amount FROM {DB}.regions"}
        started = time.monotonic()
        while True:  # The new access policy takes a poll interval to reach the FE.
            inspected = request("POST", "/api/v1/viz/datasets/inspect",
                                json={"dataset_id": dataset, "source": source})
            if inspected.status_code == 200 or time.monotonic() - started > 60:
                break
            time.sleep(1)
        probe.check("inspect", inspected.status_code == 200, status=inspected.status_code)
        schema = inspected.json()
        fields = {f["source_column"]: f["field_id"] for f in schema["fields"]}
        created = request("POST", "/api/v1/viz/datasets",
                          headers={"Idempotency-Key": str(uuid.uuid4())},
                          json={"dataset_id": dataset, "source": source, "workspace_id": space_id,
                                "title": "Regions",
                                "expected_schema_fingerprint": schema["schema_fingerprint"]})
        probe.check("dataset created", created.status_code == 201, status=created.status_code)
        query = {"revision": 1, "draft": True,
                 "dimensions": [{"field_id": fields["city"]}],
                 "measures": [{"field_id": fields["amount"], "aggregation": "sum"}],
                 "sort": [{"column": "d0", "direction": "asc"}]}

        def read() -> tuple[int, list[list[Any]]]:
            response = request("POST", path + "/query", json=query)
            if response.status_code != 200:
                return response.status_code, []
            return 200, [[cell.get("value") for cell in row] for row in response.json()["rows"]]

        def until(step: str, expected) -> None:
            began = time.monotonic()
            while True:
                status, rows = read()
                if expected(status, rows):
                    probe.check(step, True, status=status, rows=rows,
                                seconds_until_applied=round(time.monotonic() - began, 1))
                    return
                if time.monotonic() - began > PROPAGATION_LIMIT_SECONDS:
                    probe.check(step, False, status=status, rows=rows)
                    return
                time.sleep(0.5)

        status, rows = read()
        probe.check("before: both cities", status == 200
                    and rows == [["Bandung", "600.00"], ["Jakarta", "400.00"]], rows=rows)

        put(row_filter("city = 'Jakarta'"), policies)
        until("row filter added: the next read has only Jakarta",
              lambda s, r: s == 200 and r == [["Jakarta", "400.00"]])

        mask = compile_mask_policy(role=ROLE, catalog="default_catalog", database=DB,
                                   table="regions", column="city", mask_type="MASK_HASH")
        put(mask, policies)
        until("mask added: the city value is no longer readable",
              lambda s, r: s == 200 and len(r) == 1 and r[0][0] not in ("Jakarta", None)
              and r[0][1] == "400.00")

        # A download is an audited authorization plus the same governed read, run again.
        created = request("POST", "/api/v1/viz/dashboards",
                          headers={"Idempotency-Key": str(uuid.uuid4())},
                          json={"workspace_id": space_id, "definition": {
                              "dashboard_id": board, "title": "Regions",
                              "pages": [{"page_id": str(uuid.uuid4()), "title": "Overview"}]}})
        probe.check("dashboard created", created.status_code == 201, status=created.status_code)
        export = request("POST", f"/api/v1/viz/dashboards/{board}/exports",
                         json={"format": "csv"})
        probe.check("download authorized for the owner", export.status_code == 200
                    and export.json().get("allowed") is True, status=export.status_code)
        status, rows = read()
        probe.check("downloaded rows are masked", status == 200 and len(rows) == 1
                    and rows[0][0] not in ("Jakarta", None), rows=rows)

        remove(policies, only=access.name)
        until("access removed: the next read is refused and returns no rows",
              lambda s, r: s in (403, 404) and r == [])
        report["result"] = "FAIL" if probe.failed else "PASS"
    except Exception as error:  # noqa: BLE001 - recorded, never echoed with payloads
        report["error_type"] = type(error).__name__
        report["error"] = str(error)[:200]
    finally:
        cleanup: list[bool] = []
        try:
            remove(policies)
            cleanup.append(not policies)
            for target in (f"/api/v1/viz/dashboards/{board}", path):
                draft = alice.client.get(target + "?draft=true", headers=alice.headers)
                if draft.status_code != 200:
                    continue
                key, revision = str(uuid.uuid4()), draft.json()["revision"]
                deleted = alice.client.request(
                    "DELETE", target, json={"operation_id": key, "expected_revision": revision},
                    headers={**alice.headers, "Idempotency-Key": key,
                             "If-Match": f'"{revision}"'})
                cleanup.append(deleted.status_code in (200, 204))
            if space_id:
                gone = alice.client.delete(f"/api/v1/workspaces/spaces/{space_id}",
                                           headers=alice.headers)
                cleanup.append(gone.status_code in (200, 204))
            cleanup.append(bool(admin.run(f"DROP DATABASE IF EXISTS {DB} FORCE").get("success")))
            report["cleanup"] = "REMOVED" if all(cleanup) else "PARTIAL"
        except Exception as error:  # noqa: BLE001
            report["cleanup"] = "UNVERIFIED"
            report["cleanup_error_type"] = type(error).__name__
        if report["cleanup"] != "REMOVED":
            report["result"] = "FAIL"
        report["steps"] = probe.steps
        report["finished_at"] = datetime.now(UTC).isoformat()
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"result": report["result"], "cleanup": report["cleanup"],
                      "error": report.get("error")}))
    raise SystemExit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
