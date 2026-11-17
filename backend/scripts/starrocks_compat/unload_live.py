"""Live check of unloading to a stage through Nova's SQL API (StarRocks compat W2).

Runs every unload form and the stage-adapted version of every documented
unloading example, reads the written files back from the stage and compares
the rows with the source exactly. Also records the refusals: storage URLs,
inline credentials, missing confirmation and a role without stage write access.

Usage (from backend/; passwords are read here and never printed):
    UV_NO_SYNC=1 uv run python scripts/starrocks_compat/unload_live.py \
        --stage compat_unload_w2 --database compat_unload_w2 \
        --out ../docs/reports/starrocks-compat/w2-unload-live.json

The database and the stage must exist (the stage registered in that
database and schema) and the caller ``nova_admin`` must be able to write to it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[3]
NOVA = "http://127.0.0.1:5173"


def _password(user: str) -> str:
    if user == "nova_admin":
        for line in (ROOT / "docker/.env").read_text().splitlines():
            if line.startswith("NOVA_ADMIN_TEST_PASSWORD="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
        raise SystemExit("NOVA_ADMIN_TEST_PASSWORD is not set in docker/.env")
    text = (ROOT / "docker/init-nova.sql").read_text()
    match = re.search(
        rf"CREATE USER IF NOT EXISTS '{re.escape(user)}' IDENTIFIED BY '([^']*)'", text
    )
    if match is None:
        raise SystemExit(f"No password for {user} in docker/init-nova.sql")
    return match.group(1)


class Nova:
    def __init__(self, user: str, database: str) -> None:
        self.user, self.database = user, database
        self._secret = _password(user)
        self.client = httpx.Client(base_url=NOVA, timeout=300)
        # The development backend reloads when source changes; retry briefly.
        for attempt in range(10):
            try:
                login = self.client.post(
                    "/api/v1/auth/login", json={"username": user, "password": self._secret}
                )
                token = login.json()["access_token"]
                break
            except (httpx.HTTPError, ValueError, KeyError):
                if attempt == 9:
                    raise SystemExit(f"Sign-in as {user} failed") from None
                time.sleep(3)
        self.headers = {"Authorization": f"Bearer {token}"}

    def download(self, stage: str, path: str) -> bytes:
        stages = self.client.get("/api/v1/stages", headers=self.headers).json()["stages"]
        stage_id = next(row["id"] for row in stages if row["name"] == stage)
        response = self.client.get(
            f"/api/v1/stages/{stage_id}/files/{path}", headers=self.headers
        )
        response.raise_for_status()
        return response.content

    def run(self, sql: str, *, confirm: bool = True, max_rows: int = 1000) -> dict[str, Any]:
        for attempt in range(10):
            try:
                response = self.client.post(
                    "/api/v1/query/execute",
                    headers=self.headers,
                    json={"sql": sql, "database": self.database, "max_rows": max_rows,
                          "confirm_destructive": confirm},
                )
                if response.status_code >= 500 or response.status_code == 502:
                    raise httpx.HTTPError(f"status {response.status_code}")
                body = response.json()
                break
            except (httpx.HTTPError, ValueError):
                if attempt == 9:
                    raise
                time.sleep(3)
        item = body[-1] if isinstance(body, list) else body
        if not isinstance(item, dict):
            item = {"success": False, "error": str(body)}
        text = json.dumps(item)
        if self._secret in text:
            raise SystemExit("A password appeared in a response")
        return item


def _norm(value: Any) -> str:
    if value is None:
        return "NULL"
    text = str(value)
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        number = float(text)
        return repr(int(number)) if number.is_integer() else repr(round(number, 6))
    return re.sub(r"^(\d{4}-\d\d-\d\d)T", r"\1 ", text)


def _rows(rows: list[list[Any]]) -> list[tuple[str, ...]]:
    return sorted(tuple(_norm(value) for value in row) for row in rows)


def _leak_check(item: dict[str, Any]) -> list[str]:
    # ``original_sql`` echoes what the caller typed (a refused storage URL).
    text = json.dumps({k: v for k, v in item.items() if k != "original_sql"})
    found = []
    if re.search(r"s3a?://", text):
        found.append("physical path")
    if re.search(r"aws\.s3\.(access|secret)_key'='(?!\*\*\*')", text):
        found.append("credential")
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    s, db = args.stage, args.database
    run_id = time.strftime("%H%M%S")
    nova = Nova("nova_admin", db)
    records: list[dict[str, Any]] = []

    def step(name: str, sql: str, **kwargs: Any) -> dict[str, Any]:
        item = nova.run(sql, **kwargs)
        record = {
            "case": name,
            "sql": sql,
            "success": bool(item.get("success")),
            "error": item.get("error"),
            "needs_confirmation": item.get("needs_confirmation"),
            "executed_sql": item.get("executed_sql"),
            "leaks": _leak_check(item),
        }
        records.append(record)
        return item

    def folder(name: str) -> str:
        return f"w2_{run_id}/{name}"

    def readback(name: str, read_sql: str, source_sql: str, *, split: str | None = None) -> None:
        got = nova.run(read_sql)
        source = nova.run(source_sql)
        rows = got.get("rows") or []
        if split is not None:
            rows = [str(row[0]).split(split) for row in rows]
        ok = bool(got.get("success")) and _rows(rows) == _rows(source.get("rows") or [])
        records.append({
            "case": name + " / read back",
            "sql": read_sql,
            "success": ok,
            "rows": len(rows),
            "source_rows": len(source.get("rows") or []),
            "error": got.get("error") if not got.get("success") else (
                None if ok else "rows differ from the source"
            ),
            "leaks": _leak_check(got),
        })

    def file_compare(
        name: str, path: str, source_sql: str, *, sep: str, quote: str | None = None
    ) -> None:
        """Download the written files and parse them exactly (no read-side guessing)."""
        import csv
        import io

        item = nova.run(f"LIST @{s}/{path}")
        files = [row[0] for row in item.get("rows") or [] if str(row[2]) in {"0", "False"}]
        rows: list[list[Any]] = []
        for file in files:
            text = nova.download(s, file.split("/", 1)[1]).decode()
            reader = csv.reader(
                io.StringIO(text), delimiter=sep, quotechar=quote or "\x00",
                doublequote=True, quoting=csv.QUOTE_MINIMAL if quote else csv.QUOTE_NONE,
            )
            rows.extend([None if value == "\\N" else value for value in row] for row in reader)
        source = nova.run(source_sql)
        ok = bool(files) and _rows(rows) == _rows(source.get("rows") or [])
        records.append({
            "case": name + " / downloaded files parsed",
            "sql": f"LIST @{s}/{path} + download",
            "success": ok,
            "files": files,
            "rows": len(rows),
            "error": None if ok else "rows differ from the source",
        })

    def listing(name: str, path: str) -> list[str]:
        item = nova.run(f"LIST @{s}/{path}")
        files = [row[0] for row in item.get("rows") or [] if str(row[2]) in {"0", "False"}]
        records.append({
            "case": name + " / list",
            "sql": f"LIST @{s}/{path}",
            "success": bool(item.get("success")) and bool(files),
            "files": files,
            "leaks": _leak_check(item),
        })
        return files

    def wait_export(name: str, path: str) -> None:
        state, row = "", None
        for _ in range(60):
            item = nova.run("SHOW EXPORT ORDER BY CreateTime DESC LIMIT 20")
            matches = [r for r in item.get("rows") or [] if str(r[5]).startswith(f"@{s}/{path}")]
            if matches:
                row = matches[0]
                state = row[2]
                if state in {"FINISHED", "CANCELLED"}:
                    break
            time.sleep(2)
        records.append({
            "case": name + " / SHOW EXPORT",
            "sql": "SHOW EXPORT ORDER BY CreateTime DESC LIMIT 20",
            "success": state == "FINISHED",
            "state": state,
            "path_shown": row[5] if row else None,
            "query_id": row[1] if row else None,
            "error": row[10] if row and state != "FINISHED" else None,
        })

    # ── Fixtures (the docs' sales_records, a partitioned testTbl, tricky text) ──
    step("fixture", "DROP TABLE IF EXISTS sales_records")
    step("fixture", (
        "CREATE TABLE sales_records(record_id BIGINT, seller STRING, store_id INT, "
        "sales_time DATETIME, sales_amt DOUBLE) DUPLICATE KEY(record_id) "
        "PARTITION BY date_trunc('day', sales_time) DISTRIBUTED BY HASH(record_id)"
    ))
    step("fixture", (
        "INSERT INTO sales_records VALUES "
        "(220313001,'Amy',1,'2022-03-13 12:00:00',8573.25),"
        "(220314002,'Bob',2,'2022-03-14 12:00:00',6948.99),"
        "(220314003,'Amy',1,'2022-03-14 12:00:00',4319.01),"
        "(220315004,'Carl',3,'2022-03-15 12:00:00',8734.26),"
        "(220316005,'Carl',3,'2022-03-16 12:00:00',4212.69),"
        "(220317006,'Bob',2,'2022-03-17 12:00:00',9515.88)"
    ))
    step("fixture", "DROP TABLE IF EXISTS testTbl")
    step("fixture", (
        "CREATE TABLE testTbl (k1 DATE, col1 INT, col2 STRING, col3 DOUBLE) "
        "DUPLICATE KEY(k1) PARTITION BY RANGE(k1) ("
        "PARTITION p1 VALUES LESS THAN ('2022-01-01'), "
        "PARTITION p2 VALUES LESS THAN ('2023-01-01'), "
        "PARTITION p3 VALUES LESS THAN ('2024-01-01')) DISTRIBUTED BY HASH(k1) BUCKETS 1"
    ))
    step("fixture", (
        "INSERT INTO testTbl VALUES ('2021-05-01',1,'a',1.5),('2021-06-01',2,'b',2.5),"
        "('2022-05-01',3,'c',3.5),('2023-05-01',4,'d',4.5)"
    ))
    step("fixture", "DROP TABLE IF EXISTS unload_text")
    step("fixture", (
        "CREATE TABLE unload_text (id INT, note STRING) DUPLICATE KEY(id) "
        "DISTRIBUTED BY HASH(id) BUCKETS 1"
    ))
    step("fixture", (
        "INSERT INTO unload_text VALUES (1,'plain'),(2,'has, comma'),"
        "(3,'say \"hi\"'),(4,NULL)"
    ))
    sales = "SELECT * FROM sales_records"

    # ── INSERT INTO FILES: docs examples, stage-adapted ──
    f = folder("multi")
    step("FILES multiple files (docs: unload into multiple files)", (
        f"INSERT INTO FILES(\"path\" = \"@{s}/{f}/data1\", \"format\" = \"parquet\", "
        "\"compression\" = \"uncompressed\", \"target_max_file_size\" = \"1024\") " + sales
    ))
    listing("FILES multiple files", f)
    readback(
        "FILES multiple files", f"SELECT * FROM @{s}.w2_{run_id}.multi.data1.*.parquet", sales
    )

    f = folder("partitioned")
    step("FILES partition_by (docs: different paths)", (
        f"INSERT INTO FILES(\"path\" = \"@{s}/{f}/\", \"format\" = \"parquet\", "
        "\"compression\" = \"lz4\", \"partition_by\" = \"store_id\") " + sales
    ))
    listing("FILES partition_by", f)
    readback("FILES partition_by", f"SELECT * FROM @{s}.w2_{run_id}.partitioned.*.*.parquet", sales)

    f = folder("single")
    step("FILES single file (docs: unload into a single file)", (
        f"INSERT INTO FILES(\"path\" = \"@{s}/{f}/data2\", \"format\" = \"parquet\", "
        "\"compression\" = \"lz4\", \"single\" = \"true\") " + sales
    ))
    listing("FILES single file", f)
    readback(
        "FILES single file", f"SELECT * FROM @{s}.w2_{run_id}.single.data2.*.parquet", sales
    )

    f = folder("zstd")
    step("FILES zstd single (docs: unload to MinIO)", (
        f"INSERT INTO FILES(\"path\" = \"@{s}/{f}/data3\", \"format\" = \"parquet\", "
        "\"compression\" = \"zstd\", \"single\" = \"true\") " + sales
    ))
    readback("FILES zstd single", f"SELECT * FROM @{s}.w2_{run_id}.zstd.data3.*.parquet", sales)

    f = folder("csv")
    step("FILES CSV (docs: NFS CSV, corrected csv.row_delimiter)", (
        f"INSERT INTO FILES('path' = '@{s}/{f}/', 'format' = 'csv', "
        "'compression' = 'uncompressed', 'csv.column_separator' = ',', "
        "'csv.row_delimiter' = '\\n') " + sales
    ))
    readback("FILES CSV", f"SELECT * FROM @{s}.w2_{run_id}.csv.*.csv", sales)

    step("FILES CSV docs typo csv.row_delimitor is refused", (
        f"INSERT INTO FILES('path' = '@{s}/{folder('typo')}/', 'format' = 'csv', "
        "'csv.column_separator' = ',', 'csv.row_delimitor' = '\\n') " + sales
    ))
    records[-1]["expected_refusal"] = True
    records[-1]["success"] = "csv.row_delimitor" in str(records[-1]["error"])

    f = folder("csv_enclosed")
    step("FILES CSV enclosed with header (docs: RFC 4180)", (
        f"INSERT INTO FILES('path' = '@{s}/{f}/', 'format' = 'csv', "
        "'compression' = 'uncompressed', 'csv.column_separator' = ',', "
        "'csv.row_delimiter' = '\\n', 'csv.enclose' = '\"', 'csv.escape' = '\"', "
        "'csv.include_header' = 'true') " + sales
    ))
    readback(
        "FILES CSV enclosed with header",
        f"SELECT * FROM @{s}.w2_{run_id}.csv_enclosed.*.csv",
        sales,
    )

    f = folder("text_enclosed")
    step("FILES CSV enclosed, commas, quotes and NULL", (
        f"INSERT INTO FILES('path' = '@{s}/{f}/', 'format' = 'csv', "
        "'compression' = 'uncompressed', 'csv.column_separator' = '|', "
        "'csv.enclose' = '\"', 'csv.escape' = '\"') SELECT * FROM unload_text"
    ))
    file_compare(
        "FILES CSV enclosed, commas, quotes and NULL", f, "SELECT * FROM unload_text",
        sep="|", quote='"',
    )
    # Reading these files back in SQL goes through Nova's CSV detection, which
    # takes a header-less first line as the header and reads \N as "N" when it
    # sets a backslash escape: recorded, not counted as an unload failure.
    readback(
        "FILES CSV enclosed, commas, quotes and NULL (SQL read, detection limit)",
        f"SELECT * FROM @{s}.w2_{run_id}.text_enclosed.*.csv",
        "SELECT * FROM unload_text",
    )
    records[-1]["read_side_limit"] = True

    f = folder("parquet_v1")
    step("FILES Parquet version 1.0 (docs: NFS Parquet)", (
        f"INSERT INTO FILES('path' = '@{s}/{f}/', 'format' = 'parquet', "
        "'compression' = 'snappy', 'parquet.version' = '1.0', "
        "'parquet.use_legacy_encoding' = 'true') " + sales
    ))
    readback("FILES Parquet 1.0", f"SELECT * FROM @{s}.w2_{run_id}.parquet_v1.*.parquet", sales)

    f = folder("parquet_default")
    step("FILES Parquet without compression option (docs Example 7)", (
        f"INSERT INTO FILES('path' = '@{s}/{f}/', 'format' = 'parquet') " + sales
    ))
    readback(
        "FILES Parquet default",
        f"SELECT * FROM @{s}.w2_{run_id}.parquet_default.*.parquet",
        sales,
    )

    f = folder("orc")
    step("FILES ORC", (
        f"INSERT INTO FILES('path' = '@{s}/{f}/', 'format' = 'orc', "
        "'compression' = 'zstd') " + sales
    ))
    readback("FILES ORC", f"SELECT * FROM @{s}.w2_{run_id}.orc.*.orc", sales)

    # ── SELECT ... INTO OUTFILE ──
    f = folder("outfile_csv")
    step("OUTFILE CSV", (
        f"SELECT * FROM sales_records INTO OUTFILE '@{s}/{f}/sales_' FORMAT AS CSV "
        "PROPERTIES('column_separator' = ',', 'line_delimiter' = '\\n')"
    ))
    listing("OUTFILE CSV", f)
    readback("OUTFILE CSV", f"SELECT * FROM @{s}.w2_{run_id}.outfile_csv.*.csv", sales)

    f = folder("outfile_parquet")
    step("OUTFILE Parquet", (
        f"SELECT * FROM sales_records INTO OUTFILE '@{s}/{f}/sales_' FORMAT AS PARQUET "
        "PROPERTIES('compression_type' = 'zstd', 'use_dictionary' = 'false', "
        "'max_file_size' = '104857600', 'max_row_group_bytes' = '134217728')"
    ))
    readback("OUTFILE Parquet", f"SELECT * FROM @{s}.w2_{run_id}.outfile_parquet.*.parquet", sales)

    step("OUTFILE ORC is refused by the engine", (
        f"SELECT * FROM sales_records INTO OUTFILE '@{s}/{folder('outfile_orc')}/x_' FORMAT AS ORC"
    ))
    records[-1]["expected_refusal"] = True
    records[-1]["success"] = "Only support CSV and PARQUET" in str(records[-1]["error"])

    # ── EXPORT TABLE: docs examples, stage-adapted ──
    f = folder("export_all")
    step("EXPORT all data (docs: to HDFS)", f"EXPORT TABLE testTbl TO \"@{s}/{f}/\" WITH BROKER")
    wait_export("EXPORT all data", f)
    readback("EXPORT all data", f"SELECT * FROM @{s}.w2_{run_id}.export_all.*.csv",
             "SELECT * FROM testTbl")

    f = folder("export_parts")
    step("EXPORT partitions p1,p2 (docs)", (
        f"EXPORT TABLE testTbl PARTITION (p1,p2) TO \"@{s}/{f}/\" WITH BROKER"
    ))
    wait_export("EXPORT partitions", f)
    readback("EXPORT partitions", f"SELECT * FROM @{s}.w2_{run_id}.export_parts.*.csv",
             "SELECT * FROM testTbl PARTITION (p1, p2)")

    f = folder("export_comma")
    step("EXPORT column_separator ',' (docs)", (
        f"EXPORT TABLE testTbl TO \"@{s}/{f}/\" PROPERTIES (\"column_separator\"=\",\") "
        "WITH BROKER"
    ))
    wait_export("EXPORT comma", f)
    readback("EXPORT comma", f"SELECT * FROM @{s}.w2_{run_id}.export_comma.*.csv",
             "SELECT * FROM testTbl")

    f = folder("export_x01")
    step("EXPORT column_separator \\x01 (docs)", (
        f"EXPORT TABLE testTbl TO \"@{s}/{f}/\" PROPERTIES (\"column_separator\"=\"\\\\x01\") "
        "WITH BROKER"
    ))
    wait_export("EXPORT \\x01", f)
    file_compare("EXPORT \\x01", f, "SELECT * FROM testTbl", sep="\x01")

    f = folder("export_prefix")
    step("EXPORT file name prefix (docs)", (
        f"EXPORT TABLE testTbl TO \"@{s}/{f}/testTbl_\" WITH BROKER"
    ))
    wait_export("EXPORT prefix", f)
    listing("EXPORT prefix", f)

    f = folder("export_s3")
    step("EXPORT (docs: to AWS S3; WITH BROKER and no properties)", (
        f"EXPORT TABLE testTbl TO \"@{s}/{f}/\" WITH BROKER"
    ))
    wait_export("EXPORT S3 form", f)

    f = folder("export_sync")
    item = step("EXPORT WITH SYNC MODE returns the job row", (
        f"EXPORT TABLE testTbl TO '@{s}/{f}/' WITH SYNC MODE"
    ))
    records[-1]["rows"] = item.get("rows")
    records[-1]["success"] = bool(item.get("success")) and bool(item.get("rows")) and all(
        str(row[5]).startswith(f"@{s}/{f}/") for row in item.get("rows") or []
    )
    readback("EXPORT WITH SYNC MODE", f"SELECT * FROM @{s}.w2_{run_id}.export_sync.*.csv",
             "SELECT * FROM testTbl")

    f = folder("export_noqid")
    step("EXPORT include_query_id false", (
        f"EXPORT TABLE testTbl TO '@{s}/{f}/' PROPERTIES('include_query_id' = 'false')"
    ))
    wait_export("EXPORT include_query_id false", f)
    listing("EXPORT include_query_id false", f)

    f = folder("export_cols")
    step("EXPORT partitions, columns, properties (docs: Export.md)", (
        f"EXPORT TABLE {db}.testTbl PARTITION (p1,p2) (col1, col3) "
        f"TO \"@{s}/{f}/lineorder_\" PROPERTIES (\"column_separator\"=\",\", "
        "\"load_mem_limit\"=\"2147483648\", \"timeout\" = \"3600\") WITH BROKER"
    ))
    wait_export("EXPORT columns", f)
    readback("EXPORT columns", f"SELECT * FROM @{s}.w2_{run_id}.export_cols.*.csv",
             "SELECT col1, col3 FROM testTbl PARTITION (p1, p2)")

    step("fixture", "DROP TABLE IF EXISTS unload_big")
    step("fixture", (
        "CREATE TABLE unload_big (id BIGINT, label STRING) DUPLICATE KEY(id) "
        "DISTRIBUTED BY HASH(id) BUCKETS 8"
    ))
    step("fixture", (
        "INSERT INTO unload_big SELECT generate_series, repeat('x', 100) "
        "FROM TABLE(generate_series(1, 1000000))"
    ))
    f = folder("export_cancel")
    step("EXPORT then CANCEL EXPORT", f"EXPORT TABLE unload_big TO '@{s}/{f}/' WITH BROKER")
    jobs = nova.run("SHOW EXPORT ORDER BY CreateTime DESC LIMIT 5")
    target = next((r for r in jobs.get("rows") or [] if str(r[5]).startswith(f"@{s}/{f}")), None)
    if target is not None:
        step("CANCEL EXPORT WHERE queryid", f"CANCEL EXPORT WHERE queryid = \"{target[1]}\"")
        step("SHOW EXPORT WHERE QUERYID", f"SHOW EXPORT WHERE QUERYID = \"{target[1]}\"")
    step("SHOW EXPORT FROM db WHERE STATE ORDER BY", (
        f"SHOW EXPORT FROM {db} WHERE STATE = \"FINISHED\" ORDER BY StartTime ASC"
    ))

    # ── Nova's own forms ──
    f = folder("copy_into")
    step("COPY INTO @stage FROM (query)", f"COPY INTO @{s}/{f}/ FROM (SELECT * FROM sales_records)")
    readback("COPY INTO @stage", f"SELECT * FROM @{s}.w2_{run_id}.copy_into.*.csv", sales)
    f = folder("insert_stage")
    step("INSERT INTO @stage SELECT", f"INSERT INTO @{s}/{f}/ SELECT * FROM sales_records")
    readback("INSERT INTO @stage", f"SELECT * FROM @{s}.w2_{run_id}.insert_stage.*.csv", sales)

    # ── Refusals ──
    refusals = [
        ("raw s3 path refused", "INSERT INTO FILES('path'='s3://stages/x/', 'format'='csv') "
         "SELECT 1"),
        ("raw hdfs path refused", "EXPORT TABLE testTbl TO 'hdfs://10.0.0.1:9000/a/' WITH BROKER"),
        ("raw file path refused", "SELECT 1 INTO OUTFILE 'file:///tmp/x_' FORMAT AS CSV"),
        ("inline credentials refused", (
            f"INSERT INTO FILES('path'='@{s}/{folder('x')}/', 'format'='csv', "
            "'aws.s3.access_key'='AKIAEXAMPLE', 'aws.s3.secret_key'='example') SELECT 1"
        )),
        ("broker properties refused", (
            f"EXPORT TABLE testTbl TO '@{s}/{folder('x')}/' WITH BROKER "
            "('username'='u', 'password'='p')"
        )),
        ("stage root without folder refused",
         f"INSERT INTO FILES('path'='@{s}', 'format'='csv') SELECT 1"),
    ]
    for name, sql in refusals:
        item = step(name, sql)
        records[-1]["expected_refusal"] = True
        records[-1]["success"] = not item.get("success")
    item = step(
        "unload without confirmation asks for it",
        f"INSERT INTO FILES('path'='@{s}/{folder('noconfirm')}/', 'format'='csv') SELECT 1",
        confirm=False,
    )
    records[-1]["expected_refusal"] = True
    records[-1]["success"] = bool(item.get("needs_confirmation"))

    alice = Nova("alice", "analytics")
    for name, sql in [
        ("alice (marketing) INSERT INTO FILES refused",
         f"INSERT INTO FILES('path'='@{db}.{db}.{s}/alice/', 'format'='parquet') "
         "SELECT * FROM customers"),
        ("alice (marketing) INTO OUTFILE refused",
         f"SELECT * FROM customers INTO OUTFILE '@{db}.{db}.{s}/alice/c_' FORMAT AS PARQUET"),
        ("alice (marketing) EXPORT refused",
         f"EXPORT TABLE customers TO '@{db}.{db}.{s}/alice/' WITH BROKER"),
    ]:
        item = alice.run(sql)
        records.append({
            "case": name, "sql": sql, "user": "alice", "role": "marketing",
            "success": not item.get("success") and item.get("error") == "Stage access denied",
            "error": item.get("error"), "expected_refusal": True,
            "leaks": _leak_check(item),
        })

    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    failures = [
        r for r in records
        if r["case"] != "fixture" and not r["success"] and not r.get("read_side_limit")
    ]
    leaks = [r for r in records if r.get("leaks")]
    report = {
        "workstream": "W2 unload to stage",
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_commit": commit,
        "working_tree": "uncommitted W2 changes on top of the base commit",
        "nova": NOVA,
        "engine": "StarRocks 4.1.4 (patched FE), dev stack, MinIO-backed stage",
        "stage": s,
        "database": db,
        "run_folder": f"@{s}/w2_{run_id}/",
        "summary": {
            "cases": len([r for r in records if r["case"] != "fixture"]),
            "passed": len([r for r in records if r["case"] != "fixture" and r["success"]]),
            "failed": [r["case"] for r in failures],
            "read_side_limits": [r["case"] for r in records if r.get("read_side_limit")],
            "leaks": [r["case"] for r in leaks],
        },
        "records": records,
    }
    Path(args.out).write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
