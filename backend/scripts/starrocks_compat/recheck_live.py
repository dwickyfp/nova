"""Repeatable live re-check of pipes, Kafka routine loads and Iceberg catalogs.

Runs the core documented flows of StarRocks compat W3 (pipes over a stage),
W8 (routine load from Kafka) and W9 (Iceberg REST catalog) through Nova's HTTP
API on the shared development stack, including the governance checks with the
QA user ``alice`` (role ``marketing``). Every object it creates carries the
run suffix and is removed at the end, also after a failure. Ranger grants are
written and deleted by name.

Usage (from backend/; passwords and keys are read here and never printed):
    UV_NO_SYNC=1 STARROCKS_FE_MYSQL_PORT=29030 RANGER_USERNAME=admin \
    RANGER_TLS_VERIFY=false RANGER_PASSWORD=... \
        uv run python scripts/starrocks_compat/recheck_live.py \
        --out ../docs/reports/starrocks-compat/recheck-live-attempt-1.json

Needs the ``starrocks-compat`` services (Kafka, Iceberg REST) running and the
``marketing`` role bound to a scheduled-task account (pipes run as it).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from unload_live import ROOT, Nova  # noqa: E402

from app.integrations.ranger.client import ranger_client  # noqa: E402
from app.integrations.ranger.compiler import compile_access_policy  # noqa: E402

RUN = uuid.uuid4().hex[:6]
SECRETS: list[str] = []


def _env(name: str) -> str:
    for line in (ROOT / "docker/.env").read_text().splitlines():
        if line.startswith(f"{name}="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            SECRETS.append(value)
            return value
    raise SystemExit(f"{name} is not set in docker/.env")


class Recorder:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.started = time.monotonic()

    def check(self, section: str, step: str, passed: bool, **detail: Any) -> bool:
        text = json.dumps(detail, default=str)
        for secret in SECRETS:
            if secret and secret in text:
                passed, detail = False, {"error": "a secret appeared in a response"}
        self.steps.append(
            {
                "section": section,
                "step": step,
                "passed": bool(passed),
                "at_seconds": round(time.monotonic() - self.started, 1),
                **detail,
            }
        )
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {section}: {step}", flush=True)
        return bool(passed)


def _ok(item: dict[str, Any]) -> bool:
    return bool(item.get("success", item.get("error") is None))


def _brief(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "error": item.get("error"),
        "rows": (item.get("rows") or [])[:10],
        "warnings": item.get("warnings"),
    }


def wait_for(fn: Callable[[], Any], until: Callable[[Any], bool], seconds: float) -> Any:
    deadline = time.monotonic() + seconds
    value = fn()
    while not until(value) and time.monotonic() < deadline:
        time.sleep(3)
        value = fn()
    return value


def ranger(
    policy_action: str,
    *,
    role: str,
    catalog: str,
    database: str,
    accesses: list[str],
    names: list[str],
) -> None:
    async def go() -> None:
        policy = compile_access_policy(
            role=role,
            catalog=catalog,
            database=database,
            table="*",
            accesses=accesses,
        )
        if policy_action == "grant":
            await ranger_client.put_policy(policy)
            names.append(policy.name)
        else:
            found = await ranger_client.get_policy(policy.name)
            if found is not None:
                await ranger_client.delete_policy(int(found["id"]))

    asyncio.run(go())


def drop_policies(names: list[str]) -> None:
    async def go() -> None:
        for name in names:
            found = await ranger_client.get_policy(name)
            if found is not None:
                await ranger_client.delete_policy(int(found["id"]))

    asyncio.run(go())


# ── W3 pipes ────────────────────────────────────────────────────────────────


def pipes(rec: Recorder, admin: Nova, policies: list[str]) -> None:
    section = "W3 pipes"
    db = f"compat_recheck_pipe_{RUN}"
    stage = f"pipe_landing_{RUN}"
    rec.check(section, "create database", _ok(admin.run(f"CREATE DATABASE {db}")))
    admin.database = db
    rec.check(
        section,
        "create table",
        _ok(
            admin.run(
                "CREATE TABLE orders (id INT, name VARCHAR(32), amount DECIMAL(10,2)) "
                "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1"
            )
        ),
    )
    created = admin.client.post(
        "/api/v1/stages",
        headers=admin.headers,
        json={
            "name": stage,
            "database_name": db,
            "schema_name": db,
            "storage_connection": "production",
        },
    )
    rec.check(section, "create stage", created.status_code == 201, status=created.status_code)
    stage_id = created.json().get("id")

    def upload(name: str, body: str) -> int:
        response = admin.client.post(
            f"/api/v1/stages/{stage_id}/files",
            headers=admin.headers,
            files={"file": (name, body.encode(), "text/csv")},
            data={"path": "orders"},
        )
        return response.status_code

    rec.check(
        section,
        "upload first file",
        upload("a1.csv", "id,name,amount\n1,ana,10.50\n2,budi,20\n") in (200, 201),
    )
    ranger(
        "grant",
        role="marketing",
        catalog="default_catalog",
        database=db,
        accesses=["select", "insert", "create table"],
        names=policies,
    )
    time.sleep(8)
    alice = Nova("alice", db)
    refused = alice.run(
        "CREATE PIPE p_url AS INSERT INTO orders SELECT * FROM FILES("
        '"path" = "s3://lake/x/*.csv", "format" = "csv")'
    )
    rec.check(section, "storage URL refused", not _ok(refused), **_brief(refused))
    created_pipe = wait_for(
        lambda: alice.run(
            f'CREATE PIPE orders_pipe PROPERTIES ("POLL_INTERVAL" = "30") '
            f"AS INSERT INTO orders (id, name, amount) SELECT id, name, amount "
            f"FROM @{stage}/orders/*.csv"
        ),
        _ok,
        30,
    )
    rec.check(
        section,
        "alice (marketing) creates a shorthand CSV pipe",
        _ok(created_pipe),
        **_brief(created_pipe),
    )

    def count() -> int:
        item = admin.run("SELECT count(*) FROM orders")
        return int(item["rows"][0][0]) if _ok(item) and item.get("rows") else -1

    rec.check(
        section,
        "first file loaded at creation",
        wait_for(count, lambda n: n == 2, 120) == 2,
        rows=count(),
    )
    rec.check(
        section,
        "upload second file",
        upload("a2.csv", "id,name,amount\n3,citra,7.25\n") in (200, 201),
    )
    rec.check(
        section,
        "next poll loads the new file",
        wait_for(count, lambda n: n == 3, 150) == 3,
        rows=count(),
    )
    ids = admin.run("SELECT id, count(*) FROM orders GROUP BY id HAVING count(*) > 1")
    rec.check(section, "each row loaded once", _ok(ids) and not ids.get("rows"), **_brief(ids))
    suspended = alice.run("ALTER PIPE orders_pipe SUSPEND")
    rec.check(section, "suspend", _ok(suspended), **_brief(suspended))
    upload("a3.csv", "id,name,amount\n4,dewi,1\n")
    time.sleep(45)
    rec.check(section, "suspended pipe loads nothing", count() == 3, rows=count())
    resumed = alice.run("ALTER PIPE orders_pipe RESUME")
    rec.check(
        section,
        "resume loads the waiting file",
        _ok(resumed) and wait_for(count, lambda n: n == 4, 150) == 4,
        rows=count(),
    )
    show = alice.run("SHOW PIPES")
    rec.check(
        section,
        "SHOW PIPES lists it with the owner role",
        _ok(show) and any("marketing" in map(str, r) for r in show.get("rows") or []),
        **_brief(show),
    )
    files = alice.run(
        "SELECT LOAD_STATE, count(*) FROM information_schema.pipe_files GROUP BY LOAD_STATE"
    )
    rec.check(section, "pipe_files ledger", _ok(files), **_brief(files))
    text = json.dumps([show, files])
    rec.check(
        section, "no storage address in results", "s3://" not in text and "s3a://" not in text
    )
    # alice with her other role, finance, which has no grant on this database.
    other = Nova("alice", db)
    switched = other.client.post(
        "/api/v1/auth/switch-role", headers=other.headers, json={"role": "finance"}
    )
    peek = other.run("SHOW PIPES")
    rec.check(
        section,
        "another role of the same person sees no pipe",
        switched.status_code == 200 and (not _ok(peek) or not peek.get("rows")),
        switch_status=switched.status_code,
        **_brief(peek),
    )
    denied = other.run("ALTER PIPE orders_pipe SUSPEND")
    rec.check(section, "another role cannot control the pipe", not _ok(denied), **_brief(denied))
    dropped = alice.run("DROP PIPE orders_pipe")
    rec.check(section, "drop pipe", _ok(dropped), **_brief(dropped))
    again = alice.run("DROP PIPE IF EXISTS orders_pipe")
    rec.check(section, "DROP PIPE IF EXISTS on a missing pipe", _ok(again), **_brief(again))
    for name in ("a1.csv", "a2.csv", "a3.csv"):
        admin.client.delete(f"/api/v1/stages/{stage_id}/files/orders/{name}", headers=admin.headers)
    admin.client.delete(f"/api/v1/stages/{stage_id}", headers=admin.headers)
    admin.database = None  # type: ignore[assignment]
    rec.check(section, "cleanup: drop database", _ok(admin.run(f"DROP DATABASE {db} FORCE")))


# ── W8 routine load ─────────────────────────────────────────────────────────


def kafka_produce(topic: str, lines: list[str]) -> int:
    process = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "nova-kafka",
            "/opt/kafka/bin/kafka-console-producer.sh",
            "--bootstrap-server",
            "kafka:9092",
            "--topic",
            topic,
        ],
        input="\n".join(lines) + "\n",
        text=True,
        capture_output=True,
        timeout=120,
    )
    return process.returncode


def kafka_delete_topic(topic: str) -> None:
    subprocess.run(
        [
            "docker",
            "exec",
            "nova-kafka",
            "/opt/kafka/bin/kafka-topics.sh",
            "--bootstrap-server",
            "kafka:9092",
            "--delete",
            "--topic",
            topic,
        ],
        capture_output=True,
        timeout=120,
    )


def routine_load(rec: Recorder, admin: Nova, policies: list[str]) -> None:
    section = "W8 routine load"
    db = f"compat_recheck_rl_{RUN}"
    conn = f"recheck_kafka_{RUN}"
    topic = f"compat_recheck_{RUN}"
    admin.database = None  # type: ignore[assignment]
    created = admin.run(f"CREATE CONNECTION {conn} TYPE = kafka ENDPOINT = 'kafka:9092'")
    rec.check(section, "create Kafka connection", _ok(created), **_brief(created))
    roles = admin.client.put(
        f"/api/v1/catalog/connections/{conn}/roles",
        headers=admin.headers,
        json={"roles": ["marketing", "ACCOUNTADMIN"]},
    )
    rec.check(
        section, "choose roles that may load", roles.status_code == 200, status=roles.status_code
    )
    rec.check(
        section,
        "produce 3 CSV messages",
        kafka_produce(topic, ["1,alice,10.50", "2,bob,20.00", "3,carol,7.25"]) == 0,
    )
    admin.run(f"CREATE DATABASE {db}")
    admin.database = db
    admin.run(
        "CREATE TABLE orders (order_id INT, customer VARCHAR(32), amount DECIMAL(10,2)) "
        "DUPLICATE KEY(order_id) DISTRIBUTED BY HASH(order_id) BUCKETS 1"
    )
    job = admin.run(
        f'CREATE ROUTINE LOAD {db}.orders_csv ON orders COLUMNS TERMINATED BY ",", '
        f'COLUMNS (order_id, customer, amount) PROPERTIES ("format" = "csv", '
        f'"max_batch_interval" = "5") FROM KAFKA ("kafka_connection" = "{conn}", '
        f'"kafka_topic" = "{topic}", "property.kafka_default_offsets" = "OFFSET_BEGINNING")'
    )
    rec.check(section, "create CSV routine load", _ok(job), **_brief(job))

    def rows() -> list[Any]:
        item = admin.run("SELECT order_id, customer, amount FROM orders ORDER BY order_id")
        return item.get("rows") or [] if _ok(item) else []

    loaded = wait_for(rows, lambda r: len(r) == 3, 120)
    rec.check(section, "3 rows loaded exactly", len(loaded) == 3, rows=loaded)
    show = admin.run(f"SHOW ROUTINE LOAD FOR {db}.orders_csv")
    rec.check(
        section, "SHOW ROUTINE LOAD", _ok(show) and bool(show.get("rows")), error=show.get("error")
    )
    columns = show.get("columns") or []
    leaking = sorted(
        {
            str(column)
            for row in show.get("rows") or []
            for column, value in zip(columns, row, strict=False)
            if "kafka:9092" in str(value)
        }
    )
    rec.check(section, "no broker address or secret in SHOW", not leaking, columns=leaking)
    alice_show = Nova("alice", db).run(f"SHOW ROUTINE LOAD FOR {db}.orders_csv")
    rec.check(
        section,
        "a role without access cannot read the job",
        not _ok(alice_show) or not alice_show.get("rows"),
        **_brief(alice_show),
    )
    for sql in (
        f"PAUSE ROUTINE LOAD FOR {db}.orders_csv",
        f'ALTER ROUTINE LOAD FOR {db}.orders_csv PROPERTIES ("max_batch_interval" = "10")',
        f"RESUME ROUTINE LOAD FOR {db}.orders_csv",
    ):
        item = admin.run(sql)
        rec.check(section, sql.split(" FOR")[0], _ok(item), **_brief(item))
    topic_change = admin.run(
        f'ALTER ROUTINE LOAD FOR {db}.orders_csv FROM KAFKA ("kafka_topic" = "other")'
    )
    rec.check(
        section, "changing the topic is refused", not _ok(topic_change), **_brief(topic_change)
    )
    pulsar = admin.run(
        f'CREATE ROUTINE LOAD {db}.p ON orders FROM PULSAR ("pulsar_service_url" = "pulsar://x:6650")'
    )
    rec.check(section, "Pulsar refused", not _ok(pulsar), **_brief(pulsar))
    raw = admin.run(
        f'CREATE ROUTINE LOAD {db}.raw ON orders FROM KAFKA ("kafka_broker_list" = "kafka:9092", '
        f'"kafka_topic" = "{topic}")'
    )
    rec.check(section, "native broker list refused", not _ok(raw), **_brief(raw))
    alice = Nova("alice", db)
    denied = alice.run(
        f'CREATE ROUTINE LOAD {db}.alice_job ON orders COLUMNS TERMINATED BY "," '
        f'FROM KAFKA ("kafka_connection" = "{conn}", "kafka_topic" = "{topic}")'
    )
    rec.check(section, "alice without INSERT is refused", not _ok(denied), **_brief(denied))
    stopped = admin.run(f"STOP ROUTINE LOAD FOR {db}.orders_csv")
    rec.check(section, "STOP", _ok(stopped), **_brief(stopped))
    resumed = admin.run(f"RESUME ROUTINE LOAD FOR {db}.orders_csv")
    rec.check(section, "a stopped job cannot resume", not _ok(resumed), **_brief(resumed))
    admin.database = None  # type: ignore[assignment]
    admin.run(f"DROP DATABASE {db} FORCE")
    dropped = admin.run(f"DROP CONNECTION {conn}")
    rec.check(section, "cleanup: drop connection and database", _ok(dropped), **_brief(dropped))
    kafka_delete_topic(topic)


# ── W9 Iceberg ──────────────────────────────────────────────────────────────


def iceberg(rec: Recorder, admin: Nova, policies: list[str]) -> None:
    section = "W9 iceberg"
    conn, catalog, db = f"recheck_ice_{RUN}", f"recheck_lake_{RUN}", f"recheck_{RUN}"
    admin.database = None  # type: ignore[assignment]
    created = admin.run(
        f"CREATE CONNECTION {conn} TYPE = iceberg ENDPOINT = "
        f"'http://iceberg-rest:8181?s3_endpoint=http://minio:9000&s3_region=us-east-1' "
        f"SECRET = 'nova://{conn}'"
    )
    rec.check(section, "create Iceberg connection", _ok(created), **_brief(created))
    creds = admin.client.put(
        f"/api/v1/catalog/connections/{conn}/credentials",
        headers=admin.headers,
        json={"username": _env("ICEBERG_S3_ACCESS_KEY"), "password": _env("ICEBERG_S3_SECRET_KEY")},
    )
    rec.check(
        section, "enter storage credentials", creds.status_code == 200, status=creds.status_code
    )
    rec.check(
        section, "credential response holds no key", not any(s in creds.text for s in SECRETS)
    )
    cat = admin.run(f"CREATE FOREIGN CATALOG {catalog} USING CONNECTION {conn}")
    rec.check(section, "create foreign catalog", _ok(cat), **_brief(cat))
    shown = admin.run(f"SHOW CREATE CATALOG {catalog}")
    rec.check(
        section,
        "SHOW CREATE CATALOG hides keys",
        not any(s in json.dumps(shown) for s in SECRETS),
        error=shown.get("error"),
    )
    steps = [
        f"CREATE DATABASE {catalog}.{db}",
        f"CREATE TABLE {catalog}.{db}.orders "
        "(id INT, customer STRING, amount DECIMAL(10,2), dt DATE) PARTITION BY (dt)",
        f"INSERT INTO {catalog}.{db}.orders VALUES "
        "(1,'ana',10.50,'2026-11-16'),(2,'budi',20.00,'2026-11-17')",
        f"INSERT INTO {catalog}.{db}.orders VALUES (3,'citra',7.25,'2026-11-17')",
    ]
    for sql in steps:
        item = admin.run(sql)
        rec.check(section, sql[:60], _ok(item), **_brief(item))
    rows = admin.run(f"SELECT id, customer, amount FROM {catalog}.{db}.orders ORDER BY id")
    rec.check(
        section,
        "select returns the 3 rows",
        _ok(rows) and len(rows.get("rows") or []) == 3,
        **_brief(rows),
    )
    snaps = admin.run(
        f"SELECT snapshot_id FROM {catalog}.{db}.`orders$snapshots` ORDER BY committed_at"
    )
    ok_snaps = _ok(snaps) and len(snaps.get("rows") or []) >= 2
    rec.check(section, "snapshots metadata table", ok_snaps, **_brief(snaps))
    if ok_snaps:
        first = snaps["rows"][0][0]
        travel = admin.run(f"SELECT count(*) FROM {catalog}.{db}.orders FOR VERSION AS OF {first}")
        rec.check(
            section,
            "time travel to the first snapshot has 2 rows",
            _ok(travel) and str(travel["rows"][0][0]) == "2",
            **_brief(travel),
        )
    alice = Nova("alice", None)  # type: ignore[arg-type]
    before = alice.run(f"SELECT * FROM {catalog}.{db}.orders")
    rec.check(section, "alice denied before a grant", not _ok(before), **_brief(before))
    ranger(
        "grant", role="marketing", catalog=catalog, database=db, accesses=["select"], names=policies
    )
    after = wait_for(lambda: alice.run(f"SELECT count(*) FROM {catalog}.{db}.orders"), _ok, 60)
    rec.check(section, "alice reads after SELECT grant", _ok(after), **_brief(after))
    insert = alice.run(f"INSERT INTO {catalog}.{db}.orders VALUES (9,'eko',1.00,'2026-11-18')")
    rec.check(section, "alice INSERT still denied", not _ok(insert), **_brief(insert))
    listing = alice.client.get("/api/v1/catalog/connections", headers=alice.headers)
    rec.check(
        section,
        "connections page is security-admin only",
        listing.status_code == 403,
        status=listing.status_code,
    )
    ranger(
        "revoke",
        role="marketing",
        catalog=catalog,
        database=db,
        accesses=["select"],
        names=policies,
    )
    revoked = wait_for(
        lambda: alice.run(f"SELECT count(*) FROM {catalog}.{db}.orders"), lambda i: not _ok(i), 60
    )
    rec.check(section, "alice denied after revoke", not _ok(revoked), **_brief(revoked))
    admin.run(f"DROP TABLE {catalog}.{db}.orders")
    admin.run(f"DROP DATABASE {catalog}.{db}")
    dropped = admin.client.delete(f"/api/v1/catalog/catalogs/{catalog}", headers=admin.headers)
    rec.check(
        section,
        "cleanup: remove catalog",
        dropped.status_code in (200, 204),
        status=dropped.status_code,
    )
    gone = admin.run(f"DROP CONNECTION {conn}")
    rec.check(section, "cleanup: drop connection", _ok(gone), **_brief(gone))


# ── W5 backup API (repositories on a stage) ────────────────────────────────


def backup_api(rec: Recorder, admin: Nova, policies: list[str]) -> None:
    section = "W5 backup API"
    db, stage, repo = f"compat_recheck_bk_{RUN}", f"bk_stage_{RUN}", f"recheck_repo_{RUN}"
    admin.database = None  # type: ignore[assignment]
    admin.run(f"CREATE DATABASE {db}")
    created = admin.client.post(
        "/api/v1/stages",
        headers=admin.headers,
        json={
            "name": stage,
            "database_name": db,
            "schema_name": db,
            "storage_connection": "production",
        },
    )
    stage_id = created.json().get("id")
    url = admin.client.post(
        "/api/v1/backup/repositories",
        headers=admin.headers,
        json={"name": repo, "location": "s3://lake/x/"},
    )
    rec.check(
        section,
        "storage URL refused",
        url.status_code == 400,
        status=url.status_code,
        detail=url.json().get("detail"),
    )
    made = admin.client.post(
        "/api/v1/backup/repositories",
        headers=admin.headers,
        json={
            "name": repo,
            "location": f"@{db}.{db}.{stage}/backups/",
            "storage_connection": "production",
        },
    )
    rec.check(
        section,
        "create repository on a stage through the API",
        made.status_code == 201,
        status=made.status_code,
        body=made.json(),
    )
    listed = admin.client.get("/api/v1/backup/repositories", headers=admin.headers)
    text = listed.text
    rec.check(
        section,
        "repository listed without bucket or key",
        listed.status_code == 200
        and repo in text
        and "s3a://" not in text
        and not any(s in text for s in SECRETS),
        status=listed.status_code,
    )
    alice = Nova("alice", None)  # type: ignore[arg-type]
    denied = alice.client.post(
        "/api/v1/backup/repositories",
        headers=alice.headers,
        json={"name": f"{repo}_a", "location": f"@{db}.{db}.{stage}/x/"},
    )
    rec.check(
        section, "non-admin refused", denied.status_code in (400, 403), status=denied.status_code
    )
    dropped = admin.run(f"DROP REPOSITORY `{repo}`")
    rec.check(section, "drop repository", _ok(dropped), **_brief(dropped))
    admin.client.delete(f"/api/v1/stages/{stage_id}", headers=admin.headers)
    rec.check(section, "cleanup: drop database", _ok(admin.run(f"DROP DATABASE {db} FORCE")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--only", choices=["pipes", "routine_load", "iceberg", "backup_api"], action="append"
    )
    args = parser.parse_args()
    rec = Recorder()
    policies: list[str] = []
    admin = Nova("nova_admin", None)  # type: ignore[arg-type]
    SECRETS.append(admin._secret)
    sections = {
        "pipes": pipes,
        "routine_load": routine_load,
        "iceberg": iceberg,
        "backup_api": backup_api,
    }
    try:
        for name, fn in sections.items():
            if args.only and name not in args.only:
                continue
            try:
                fn(rec, admin, policies)
            except Exception as exc:  # noqa: BLE001 - recorded, next section still runs
                rec.check(
                    name,
                    "section raised",
                    False,
                    error=repr(exc),
                    trace=traceback.format_exc()[-1500:],
                )
    finally:
        drop_policies(policies)
    failed = [s for s in rec.steps if not s["passed"]]
    report = {
        "run": RUN,
        "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "through": "Nova HTTP API at 127.0.0.1:5173 as nova_admin, alice (marketing), bob",
        "summary": {
            "steps": len(rec.steps),
            "passed": len(rec.steps) - len(failed),
            "failed": [f"{s['section']}: {s['step']}" for s in failed],
        },
        "ranger_policies_removed": policies,
        "steps": rec.steps,
    }
    Path(args.out).write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(json.dumps(report["summary"], indent=1))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
