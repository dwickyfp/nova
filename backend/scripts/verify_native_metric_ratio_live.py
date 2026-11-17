"""NDV-METRIC-002 and NDV-METRIC-008: ratio and weighted-average metrics at every grain.

A disposable table holds sales with a leap day, a refund, a missing profit and
a missing revenue. ``alice`` (``marketing``) publishes a semantic view whose
``margin`` is ``SUM(profit) / SUM(revenue)`` and builds a native dataset at the
region-and-day grain. Native chart queries must then match the engine's own
ratio of sums computed directly on the table: per region, in total, and never
the average of row margins or the sum of daily margins. A coarser date grain
for the ratio is refused, while a plain sum metric still totals per month.

NDV-METRIC-008: ``weighted_price`` is ``SUM(price * units) / SUM(units)``. Per
region and in total it must equal the engine's weighted average (a row with no
units, a row with zero units and a row with no price included) and differ from
the plain average price.

Usage (from backend/; passwords are read here and never printed):
    UV_NO_SYNC=1 STARROCKS_FE_MYSQL_PORT=29030 RANGER_USERNAME=admin \
    RANGER_TLS_VERIFY=false RANGER_PASSWORD=... \
        uv run python scripts/verify_native_metric_ratio_live.py \
        --output ../docs/reports/native-dataviz/metric-ratio-live-attempt-1.json

Every table, policy, semantic view and dataset it creates is removed afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "starrocks_compat"))

from unload_live import Nova  # noqa: E402

from app.integrations.ranger.client import ranger_client  # noqa: E402
from app.integrations.ranger.compiler import compile_access_policy  # noqa: E402

RUN = uuid.uuid4().hex[:6]
DB = f"ndv_metric_{RUN}"
ROLE = "marketing"
ROWS = [  # id, region, sold_on, revenue, profit, price, units
    (1, "West", "2024-02-28", "100.00", "30.00", "10.00", "1"),
    (2, "West", "2024-02-29", "300.00", "30.00", "30.00", "9"),  # leap day
    (3, "West", "2024-02-29", "-50.00", "-20.00", "50.00", "0"),  # refund, no units
    (4, "West", "2024-03-01", "200.00", None, None, "4"),  # profit and price missing
    (5, "East", "2024-02-29", "400.00", "100.00", "8.00", "10"),
    (6, "East", "2024-03-01", "100.00", "50.00", "20.00", "2"),
    (7, "East", "2024-03-01", None, "10.00", "100.00", None),  # revenue and units missing
]


def expression(sql: str) -> dict[str, Any]:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}


DEFINITION = json.dumps({
    "version": "0.1.1", "name": f"ndv_margin_{RUN}",
    "datasets": [{"name": "sales", "source": f"{DB}.sales", "primary_key": ["id"], "fields": [
        {"name": "id", "expression": expression("id"), "kind": "dimension"},
        {"name": "region", "expression": expression("region"), "kind": "dimension"},
        {"name": "sold_on", "expression": expression("sold_on"), "kind": "dimension",
         "datatype": "Date"},
        {"name": "revenue", "expression": expression("revenue"), "datatype": "Decimal"},
        {"name": "profit", "expression": expression("profit"), "datatype": "Decimal"},
        {"name": "price", "expression": expression("price"), "datatype": "Decimal"},
        {"name": "units", "expression": expression("units"), "datatype": "Decimal"},
    ]}],
    "metrics": [
        {"name": "margin", "datatype": "Decimal",
         "expression": expression("SUM(sales.profit) / SUM(sales.revenue)")},
        {"name": "revenue_total", "datatype": "Decimal",
         "expression": expression("SUM(sales.revenue)")},
        {"name": "weighted_price", "datatype": "Decimal",
         "expression": expression("SUM(sales.price * sales.units) / SUM(sales.units)")},
    ],
})


class Probe:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.failed = False

    def check(self, step: str, passed: bool, **detail: Any) -> bool:
        self.steps.append({"step": step, "passed": bool(passed), **detail})
        self.failed |= not passed
        print(f"[{'PASS' if passed else 'FAIL'}] {step}", flush=True)
        return passed


def close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    return abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal("0.000001")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    probe = Probe()
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(), "run": RUN,
        "scope": "semantic ratio metric through native chart queries vs engine oracle",
        "result": "FAIL", "cleanup": "NOT_RUN",
    }
    admin = Nova("nova_admin", "information_schema")
    alice = Nova("alice", DB)
    policy = compile_access_policy(role=ROLE, catalog="default_catalog", database=DB,
                                   table="*", accesses=["select"])
    view_id: str | None = None
    dataset = str(uuid.uuid4())
    path = f"/api/v1/viz/datasets/{dataset}"

    def call(method: str, url: str, **kw: Any):
        return alice.client.request(method, url, headers={**alice.headers,
                                                          **kw.pop("headers", {})}, **kw)

    try:
        values = ",".join(
            "(" + ",".join([str(i), f"'{r}'", f"'{d}'", rev or "NULL", pro or "NULL",
                            price or "NULL", units or "NULL"]) + ")"
            for i, r, d, rev, pro, price, units in ROWS)
        for sql in (f"CREATE DATABASE {DB}",
                    f"CREATE TABLE {DB}.sales (id INT, region STRING, sold_on DATE, "
                    "revenue DECIMAL(12,2), profit DECIMAL(12,2), price DECIMAL(12,2), units INT) "
                    "DUPLICATE KEY(id) "
                    "DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ('replication_num' = '1')",
                    f"INSERT INTO {DB}.sales VALUES {values}"):
            probe.check(f"fixture: {sql[:40]}", bool(admin.run(sql).get("success")))
        oracle = admin.run(
            "SELECT region, SUM(profit) / SUM(revenue), AVG(profit / revenue), SUM(revenue) "
            f"FROM {DB}.sales GROUP BY region ORDER BY region")["rows"]
        total = admin.run(f"SELECT SUM(profit) / SUM(revenue) FROM {DB}.sales")["rows"][0]
        daily = admin.run(
            "SELECT region, SUM(m) FROM (SELECT region, sold_on, SUM(profit) / SUM(revenue) m "
            f"FROM {DB}.sales GROUP BY region, sold_on) t GROUP BY region ORDER BY region")["rows"]
        monthly = admin.run(
            "SELECT DATE_TRUNC('month', sold_on), SUM(revenue) "
            f"FROM {DB}.sales GROUP BY 1 ORDER BY 1")["rows"]
        weighted = admin.run(
            "SELECT region, SUM(price * units) / SUM(units), AVG(price) "
            f"FROM {DB}.sales GROUP BY region ORDER BY region")["rows"]
        weighted_total = admin.run(
            f"SELECT SUM(price * units) / SUM(units), AVG(price) FROM {DB}.sales")["rows"][0]
        report["oracle"] = {"by_region": oracle, "total": total, "sum_of_daily": daily,
                            "monthly_revenue": monthly, "weighted_by_region": weighted,
                            "weighted_total": weighted_total}
        probe.check("fixture: the weighted price differs from the plain average price",
                    all(not close(w[1], w[2]) for w in weighted)
                    and not close(weighted_total[0], weighted_total[1]))
        probe.check("fixture: ratio of sums differs from averaged and daily-summed margins",
                    all(not close(o[1], o[2]) and not close(o[1], d[1])
                        for o, d in zip(oracle, daily, strict=True)))

        asyncio.run(ranger_client.put_policy(policy))
        switched = call("POST", "/api/v1/auth/switch-role", json={"role": ROLE})
        probe.check("alice uses marketing", switched.status_code == 200)
        began = time.monotonic()
        while True:  # The Ranger grant reaches the FE within its poll interval.
            created = call("POST", "/api/v1/semantic-views",
                           json={"name": f"ndv_margin_{RUN}", "database": DB,
                                 "definition": DEFINITION})
            if created.status_code == 201 or time.monotonic() - began > 60:
                break
            time.sleep(2)
        probe.check("semantic view created", created.status_code == 201,
                    status=created.status_code)
        view_id = created.json()["id"]
        fingerprint = created.json()["versions"][0]["fingerprint"]
        valid = call("POST", f"/api/v1/semantic-views/{view_id}/versions/1/validate")
        probe.check("semantic view valid", valid.status_code == 200 and valid.json()["valid"])
        published = call("POST", f"/api/v1/semantic-views/{view_id}/versions/1/publish")
        probe.check("semantic view published", published.status_code == 200)
        source = {"kind": "semantic_model", "model_id": view_id, "model_revision": 1,
                  "model_fingerprint": fingerprint, "dimensions": ["region", "sold_on"],
                  "metrics": ["margin", "revenue_total", "weighted_price"]}
        inspected = call("POST", "/api/v1/viz/datasets/inspect",
                         json={"dataset_id": dataset, "source": source})
        probe.check("inspect at region and day", inspected.status_code == 200,
                    status=inspected.status_code)
        schema = inspected.json()
        fields = {f["source_column"]: f["field_id"] for f in schema["fields"]}
        made = call("POST", "/api/v1/viz/datasets", headers={"Idempotency-Key": str(uuid.uuid4())},
                    json={"dataset_id": dataset, "source": source, "workspace_id": "personal",
                          "title": "Margins",
                          "expected_schema_fingerprint": schema["schema_fingerprint"]})
        probe.check("dataset created", made.status_code == 201, status=made.status_code)

        def query(dimensions: list[dict[str, Any]], measures: list[tuple[str, str]]):
            body = {"revision": 1, "draft": True, "dimensions": dimensions,
                    "measures": [{"field_id": fields[name], "aggregation": agg}
                                 for name, agg in measures],
                    "sort": [{"column": "d0", "direction": "asc"}] if dimensions else []}
            response = call("POST", path + "/query", json=body)
            rows = ([[cell.get("value") if cell else None for cell in row]
                     for row in response.json()["rows"]]
                    if response.status_code == 200 else [])
            return response.status_code, rows

        status, rows = query([{"field_id": fields["region"]}], [("margin", "sum")])
        probe.check("per region: margin equals SUM(profit)/SUM(revenue)",
                    status == 200 and len(rows) == len(oracle)
                    and all(r[0] == o[0] and close(r[1], o[1])
                            for r, o in zip(rows, oracle, strict=True)),
                    rows=rows)
        probe.check("per region: not the average of row margins nor the sum of daily margins",
                    status == 200 and all(not close(r[1], o[2]) and not close(r[1], d[1])
                                          for r, o, d in zip(rows, oracle, daily, strict=True)))
        status, rows = query([], [("margin", "sum"), ("revenue_total", "sum")])
        probe.check("total: one ratio of sums over every row, nulls and refund included",
                    status == 200 and len(rows) == 1 and close(rows[0][0], total[0])
                    and close(rows[0][1], sum(Decimal(o[3]) for o in oracle)), rows=rows)
        status, rows = query([{"field_id": fields["sold_on"], "date_grain": "month"}],
                             [("margin", "sum")])
        probe.check("month grain: the ratio is refused rather than added up",
                    status == 422 and rows == [], status=status)
        status, rows = query([{"field_id": fields["sold_on"], "date_grain": "month"}],
                             [("revenue_total", "sum")])
        probe.check("month grain: a plain sum still totals per month (leap day in February)",
                    status == 200 and len(rows) == len(monthly)
                    and all(close(r[1], m[1]) for r, m in zip(rows, monthly, strict=True)),
                    rows=rows)
        status, rows = query([{"field_id": fields["region"]}], [("weighted_price", "sum")])
        probe.check("per region: weighted price equals SUM(price*units)/SUM(units)",
                    status == 200 and len(rows) == len(weighted)
                    and all(r[0] == w[0] and close(r[1], w[1])
                            for r, w in zip(rows, weighted, strict=True)), rows=rows)
        probe.check("per region: the weighted price is not the plain average price",
                    status == 200 and all(not close(r[1], w[2])
                                          for r, w in zip(rows, weighted, strict=True)))
        status, rows = query([], [("weighted_price", "sum")])
        probe.check("total: one weighted price over every row with units",
                    status == 200 and len(rows) == 1 and close(rows[0][0], weighted_total[0]),
                    rows=rows)
        status, rows = query([{"field_id": fields["region"]}, {"field_id": fields["sold_on"]}],
                             [("margin", "sum")])
        probe.check("dataset grain: one row per region and day", status == 200
                    and len(rows) == 5, rows=rows)
        report["result"] = "FAIL" if probe.failed else "PASS"
    except Exception as error:  # noqa: BLE001 - recorded without payloads
        report["error_type"] = type(error).__name__
        report["error"] = str(error)[:200]
    finally:
        cleanup: list[bool] = []
        try:
            draft = call("GET", path + "?draft=true")
            if draft.status_code == 200:
                key, revision = str(uuid.uuid4()), draft.json()["revision"]
                gone = call("DELETE", path, json={"operation_id": key,
                                                  "expected_revision": revision},
                            headers={"Idempotency-Key": key, "If-Match": f'"{revision}"'})
                cleanup.append(gone.status_code in (200, 204))
            if view_id:
                cleanup.append(call("DELETE", f"/api/v1/semantic-views/{view_id}").status_code
                               == 204)
            found = asyncio.run(ranger_client.get_policy(policy.name))
            if found is not None:
                asyncio.run(ranger_client.delete_policy(int(found["id"])))
            cleanup.append(bool(admin.run(f"DROP DATABASE IF EXISTS {DB} FORCE").get("success")))
            report["cleanup"] = "REMOVED" if all(cleanup) else "PARTIAL"
        except Exception as error:  # noqa: BLE001
            report["cleanup"] = "UNVERIFIED"
            report["cleanup_error_type"] = type(error).__name__
        if report["cleanup"] != "REMOVED":
            report["result"] = "FAIL"
        report["steps"] = probe.steps
        report["finished_at"] = datetime.now(UTC).isoformat()
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({"result": report["result"], "cleanup": report["cleanup"],
                      "error": report.get("error")}))
    raise SystemExit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
