"""Live check of Broker Load from a stage through Nova's SQL API (StarRocks compat W4).

Uploads small files to a stage with the stage upload API, runs the stage-adapted
version of every documented Broker Load example (BrokerLoad.md, BROKER_LOAD.md,
s3.md, minio.md), waits for each job, and compares the table with the expected
rows. Also records SHOW LOAD / information_schema.loads redaction, CANCEL LOAD,
ALTER LOAD, SUBMIT TASK and the refusals (storage URLs, inline credentials,
alice without stage access).

Usage (from backend/; passwords are read in-script and never printed):
    UV_NO_SYNC=1 uv run python scripts/starrocks_compat/broker_load_live.py \
        --stage compat_w4_stage --database compat_w4_load \
        --out ../docs/reports/starrocks-compat/w4-broker-load-live.json

The database and the stage must exist and ``nova_admin`` must read the stage.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from unload_live import Nova, _leak_check, _rows  # noqa: E402


def _parquet(rows: list[tuple], names: list[str], orc: bool = False) -> bytes:
    import pyarrow as pa
    import pyarrow.orc as po
    import pyarrow.parquet as pq

    table = pa.table({name: [row[i] for row in rows] for i, name in enumerate(names)})
    buffer = io.BytesIO()
    if orc:
        po.write_table(table, buffer)
    else:
        pq.write_table(table, buffer)
    return buffer.getvalue()


def cases(run: str) -> list[dict[str, Any]]:
    """Each documented example, adapted: the file path is a stage path."""
    p = "w4"
    rows3 = "1,Lily,23\n2,Rose,23\n3,Alice,24\n4,Julia,25\n"
    tab3 = "1\tLily\t23\n2\tRose\t23\n"
    plain = "CREATE TABLE {t} (col1 INT, col2 STRING, col3 INT) DUPLICATE KEY(col1) " \
            "DISTRIBUTED BY HASH(col1)"
    return [
        {
            "case": "BrokerLoad.md: two files into two tables, timeout",
            "tables": {
                "table1": "CREATE TABLE table1 (id INT NOT NULL, name VARCHAR(65533) NULL "
                "DEFAULT '', score INT NOT NULL DEFAULT '0') PRIMARY KEY(id) "
                "DISTRIBUTED BY HASH(id)",
                "table2": "CREATE TABLE table2 (id INT NOT NULL, city VARCHAR(65533) NULL "
                "DEFAULT '') PRIMARY KEY(id) DISTRIBUTED BY HASH(id)",
            },
            "files": {f"{p}/input/file1.csv": rows3, f"{p}/input/file2.csv": "200,'Beijing'\n"},
            "sql": f'LOAD LABEL {{db}}.label1_{run} (DATA INFILE("@{{stage}}/{p}/input/file1.csv") '
            'INTO TABLE table1 COLUMNS TERMINATED BY "," (id, name, score), '
            f'DATA INFILE("@{{stage}}/{p}/input/file2.csv") INTO TABLE table2 '
            'COLUMNS TERMINATED BY "," (id, city)) WITH BROKER PROPERTIES ("timeout" = "3600")',
            "check": "SELECT * FROM table1 UNION ALL SELECT id, city, NULL FROM table2",
            "expect": [[1, "Lily", 23], [2, "Rose", 23], [3, "Alice", 24], [4, "Julia", 25],
                       [200, "'Beijing'", None]],
        },
        {
            "case": "BrokerLoad.md: one file, FORMAT AS CSV",
            "tables": {"table1": "CREATE TABLE table1 (id INT, name STRING, score INT) "
                       "PRIMARY KEY(id) DISTRIBUTED BY HASH(id)"},
            "files": {f"{p}/one/file1.csv": rows3},
            "sql": f'LOAD LABEL label_7_{run} (DATA INFILE("@{{stage}}/{p}/one/file1.csv") '
            'INTO TABLE table1 COLUMNS TERMINATED BY "," FORMAT AS "CSV") WITH BROKER',
            "check": "SELECT * FROM table1",
            "expect": [[1, "Lily", 23], [2, "Rose", 23], [3, "Alice", 24], [4, "Julia", 25]],
        },
        {
            "case": "BrokerLoad.md: every file under a folder (*)",
            "tables": {"table1": "CREATE TABLE table1 (id INT, name STRING, score INT) "
                       "PRIMARY KEY(id) DISTRIBUTED BY HASH(id)"},
            "files": {f"{p}/all/a.csv": "1,Lily,23\n", f"{p}/all/b.csv": "2,Rose,23\n"},
            "sql": f'LOAD LABEL label_8_{run} (DATA INFILE("@{{stage}}/{p}/all/*") '
            'INTO TABLE table1 COLUMNS TERMINATED BY "," FORMAT AS "CSV")',
            "check": "SELECT * FROM table1",
            "expect": [[1, "Lily", 23], [2, "Rose", 23]],
        },
        {
            "case": "BROKER_LOAD.md: timeout",
            "tables": {"table1": plain.format(t="table1")},
            "files": {f"{p}/data/input/example1.csv": tab3},
            "sql": f'LOAD LABEL label1t_{run} (DATA INFILE('
            f'"@{{stage}}/{p}/data/input/example1.csv")'
            ' INTO TABLE table1) WITH BROKER PROPERTIES ("timeout" = "3600")',
            "check": "SELECT * FROM table1",
            "expect": [[1, "Lily", 23], [2, "Rose", 23]],
        },
        {
            "case": "BROKER_LOAD.md: max_filter_ratio",
            "tables": {"table2": plain.format(t="table2")},
            "files": {f"{p}/data/input/example2.csv":
                      "".join(f"{i}\tn{i}\t{i}\n" for i in range(1, 11)) + "only_one_column\n"},
            "sql": f'LOAD LABEL label2_{run} (DATA INFILE("@{{stage}}/{p}/data/input/example2.csv")'
            ' INTO TABLE table2) PROPERTIES ("max_filter_ratio" = "0.1")',
            "check": "SELECT count(*) FROM table2",
            "expect": [[10]],
        },
        {
            "case": "BROKER_LOAD.md: all files from a path, \\x01 separator",
            "tables": {"table3": plain.format(t="table3")},
            "files": {f"{p}/data/input3/f1": "1\x01a\x011\n",
                      f"{p}/data/input3/f2": "2\x01b\x012\n"},
            "sql": f'LOAD LABEL label3_{run} (DATA INFILE("@{{stage}}/{p}/data/input3/*") '
            'INTO TABLE table3 COLUMNS TERMINATED BY "\\\\x01") WITH BROKER',
            "check": "SELECT * FROM table3",
            "expect": [[1, "a", 1], [2, "b", 2]],
        },
        {
            "case": "BROKER_LOAD.md: Kerberos example adapted (tab separator, stage credentials)",
            "tables": {"table5": plain.format(t="table5")},
            "files": {f"{p}/data/input/example5.csv": tab3},
            "sql": f'LOAD LABEL label5_{run} (DATA INFILE("@{{stage}}/{p}/data/input/example5.csv")'
            ' INTO TABLE table5 COLUMNS TERMINATED BY "\\t") WITH BROKER',
            "check": "SELECT * FROM table5",
            "expect": [[1, "Lily", 23], [2, "Rose", 23]],
        },
        {
            "case": "BROKER_LOAD.md: revoke data loading (NEGATIVE)",
            "tables": {"table6": "CREATE TABLE table6 (col1 INT, col2 STRING, col3 INT SUM) "
                       "AGGREGATE KEY(col1, col2) DISTRIBUTED BY HASH(col1)"},
            "files": {f"{p}/data/input/example6.csv": "1\ta\t10\n2\tb\t20\n"},
            "before": [f'LOAD LABEL label6a_{run} (DATA INFILE("@{{stage}}/{p}/data/input/'
                       'example6.csv") INTO TABLE table6 COLUMNS TERMINATED BY "\\t")'],
            "sql": f'LOAD LABEL label6_{run} (DATA INFILE("@{{stage}}/{p}/data/input/example6.csv")'
            ' NEGATIVE INTO TABLE table6 COLUMNS TERMINATED BY "\\t") WITH BROKER',
            "check": "SELECT * FROM table6",
            "expect": [[1, "a", 0], [2, "b", 0]],
        },
        {
            "case": "BROKER_LOAD.md: destination partitions",
            "tables": {"table7": "CREATE TABLE table7 (col1 INT, col2 STRING, col3 INT) "
                       "DUPLICATE KEY(col1) PARTITION BY RANGE(col1) (PARTITION p1 VALUES LESS "
                       "THAN ('10'), PARTITION p2 VALUES LESS THAN ('20'), PARTITION p3 VALUES "
                       "LESS THAN ('30')) DISTRIBUTED BY HASH(col1)"},
            "files": {f"{p}/data/input/example7.csv": "1,a,1\n15,b,2\n"},
            "sql": f'LOAD LABEL label7_{run} (DATA INFILE("@{{stage}}/{p}/data/input/example7.csv")'
            ' INTO TABLE table7 PARTITION (p1, p2) COLUMNS TERMINATED BY ",") WITH BROKER',
            "check": "SELECT * FROM table7",
            "expect": [[1, "a", 1], [15, "b", 2]],
        },
        {
            "case": "BROKER_LOAD.md: temporary partition",
            "tables": {"table7t": "CREATE TABLE table7t (col1 INT, col2 STRING, col3 INT) "
                       "DUPLICATE KEY(col1) PARTITION BY RANGE(col1) (PARTITION p1 VALUES LESS "
                       "THAN ('10')) DISTRIBUTED BY HASH(col1)"},
            "before": ["ALTER TABLE table7t ADD TEMPORARY PARTITION tp1 VALUES LESS THAN ('10')"],
            "files": {f"{p}/data/input/example7t.csv": "3,c,3\n"},
            "sql": f'LOAD LABEL label7t_{run} (DATA INFILE("@{{stage}}/{p}/data/input/'
            'example7t.csv") INTO TABLE table7t TEMPORARY PARTITION (tp1) '
            'COLUMNS TERMINATED BY ",")',
            "check": "SELECT * FROM table7t TEMPORARY PARTITION (tp1)",
            "expect": [[3, "c", 3]],
        },
        {
            "case": "BROKER_LOAD.md: column mapping",
            "tables": {"table8": "CREATE TABLE table8 (col1 STRING, col2 INT, col3 INT) "
                       "DUPLICATE KEY(col1) DISTRIBUTED BY HASH(col1)"},
            "files": {f"{p}/data/input/example8.csv": "1,a,9\n2,b,8\n"},
            "sql": f'LOAD LABEL label8_{run} (DATA INFILE("@{{stage}}/{p}/data/input/example8.csv")'
            ' INTO TABLE table8 COLUMNS TERMINATED BY "," (col2, col1, col3)) WITH BROKER',
            "check": "SELECT * FROM table8",
            "expect": [["a", 1, 9], ["b", 2, 8]],
        },
        {
            "case": "BROKER_LOAD.md: filter conditions (WHERE)",
            "tables": {"table9": "CREATE TABLE table9 (col1 INT, col2 STRING, col3 INT) "
                       "DUPLICATE KEY(col1) DISTRIBUTED BY HASH(col1)"},
            "files": {f"{p}/data/input/example9.csv": "20180501\ta\t1\n20180701\tb\t2\n"},
            "sql": f'LOAD LABEL label9_{run} (DATA INFILE("@{{stage}}/{p}/data/input/example9.csv")'
            ' INTO TABLE table9 (col1, col2, col3) where col1 > 20180601) WITH BROKER',
            "check": "SELECT * FROM table9",
            "expect": [[20180701, "b", 2]],
        },
        {
            "case": "BROKER_LOAD.md: HLL columns (SET; the docs' empty_hll() is hll_empty())",
            "tables": {"table10": "CREATE TABLE table10 (id INT, col1 HLL HLL_UNION, col2 HLL "
                       "HLL_UNION, col3 HLL HLL_UNION) AGGREGATE KEY(id) DISTRIBUTED BY HASH(id)"},
            "files": {f"{p}/data/input/example10.csv": "1,a,x\n1,b,y\n2,c,z\n"},
            "sql": f'LOAD LABEL label10_{run} (DATA INFILE("@{{stage}}/{p}/data/input/'
            'example10.csv") INTO TABLE table10 COLUMNS TERMINATED BY "," (id, temp1, temp2) '
            "SET (col1 = hll_hash(temp1), col2 = hll_hash(temp2), col3 = hll_empty())) "
            "WITH BROKER",
            "check": "SELECT id, hll_cardinality(col1), hll_cardinality(col2), "
            "hll_cardinality(col3) FROM table10",
            "expect": [[1, 2, 2, 0], [2, 1, 1, 0]],
        },
        {
            "case": "BROKER_LOAD.md: partition fields from the file path (COLUMNS FROM PATH)",
            "tables": {"table11": "CREATE TABLE table11 (col1 INT, col2 STRING, col3 INT, city "
                       "STRING, utc_date DATE, uniq_id STRING) DUPLICATE KEY(col1) "
                       "DISTRIBUTED BY HASH(col1)"},
            "files": {
                f"{p}/dir/city=beijing/utc_date=2019-06-26/0000.csv": "1,a,1\n",
                f"{p}/dir/city=beijing/utc_date=2019-06-26/0001.csv": "2,b,2\n",
            },
            "sql": f'LOAD LABEL label11_{run} (DATA INFILE("@{{stage}}/{p}/dir/city=beijing/*/*")'
            ' INTO TABLE table11 COLUMNS TERMINATED BY "," FORMAT AS "csv" (col1, col2, col3) '
            "COLUMNS FROM PATH AS (city, utc_date) SET (uniq_id = md5sum(col1, city))) "
            "WITH BROKER",
            "check": "SELECT col1, col2, col3, city, utc_date, uniq_id = md5sum(col1, city) "
            "FROM table11",
            "expect": [[1, "a", 1, "beijing", "2019-06-26", 1],
                       [2, "b", 2, "beijing", "2019-06-26", 1]],
        },
        {
            "case": "BROKER_LOAD.md: %3A in the file path",
            "tables": {"table12": "CREATE TABLE table12 (data_time DATETIME, col1 INT, col2 INT)"
                       " DUPLICATE KEY(data_time) DISTRIBUTED BY HASH(col1)"},
            "files": {
                f"{p}/data12/data_time=2020-02-17 00%3A00%3A00/example12.csv": "1,2\n",
                f"{p}/data12/data_time=2020-02-18 00%3A00%3A00/example12.csv": "3,4\n",
            },
            "sql": f'LOAD LABEL label12_{run} (DATA INFILE("@{{stage}}/{p}/data12/*/example12.csv")'
            ' INTO TABLE table12 COLUMNS TERMINATED BY "," FORMAT AS "csv" (col1, col2) '
            "COLUMNS FROM PATH AS (data_time) "
            "SET (data_time = str_to_date(data_time, '%Y-%m-%d %H%%3A%i%%3A%s'))) WITH BROKER",
            "check": "SELECT * FROM table12",
            "expect": [["2020-02-17 00:00:00", 1, 2], ["2020-02-18 00:00:00", 3, 4]],
        },
        {
            "case": "BROKER_LOAD.md: format_type_options (skip_header, trim_space, enclose, "
                    "escape)",
            "tables": {"table13": "CREATE TABLE table13 (col1 INT, col2 STRING, col3 STRING) "
                       "DUPLICATE KEY(col1) DISTRIBUTED BY HASH(col1)"},
            "files": {f"{p}/d13/x/example13.csv":
                      'col2,col1,col3\nheader two\n "a, b" , 1 , "say \\"hi\\""\n'},
            "sql": f'LOAD LABEL label13_{run} (DATA INFILE("@{{stage}}/{p}/d13/*/example13.csv")'
            ' INTO TABLE table13 COLUMNS TERMINATED BY "," FORMAT AS "CSV" (skip_header = 2 '
            'trim_space = TRUE enclose = "\\"" escape = "\\\\") (col2, col1, col3)) WITH BROKER '
            'PROPERTIES ("timeout" = "3600")',
            "check": "SELECT * FROM table13",
            "expect": [[1, "a, b", 'say "hi"']],
        },
        {
            "case": "BROKER_LOAD.md: Parquet",
            "tables": {"table13p": "CREATE TABLE table13p (col1 INT, col2 STRING, col3 INT) "
                       "DUPLICATE KEY(col1) DISTRIBUTED BY HASH(col1)"},
            "files": {f"{p}/data/input/example13.parquet":
                      _parquet([(1, "a", 1), (2, "b", 2)], ["col1", "col2", "col3"])},
            "sql": f'LOAD LABEL label13p_{run} (DATA INFILE("@{{stage}}/{p}/data/input/'
            'example13.parquet") INTO TABLE table13p FORMAT AS "parquet" (col1, col2, col3)) '
            "WITH BROKER",
            "check": "SELECT * FROM table13p",
            "expect": [[1, "a", 1], [2, "b", 2]],
        },
        {
            "case": "BROKER_LOAD.md: ORC",
            "tables": {"table14": "CREATE TABLE table14 (col1 INT, col2 STRING, col3 INT) "
                       "DUPLICATE KEY(col1) DISTRIBUTED BY HASH(col1)"},
            "files": {f"{p}/data/input/example14.orc":
                      _parquet([(1, "a", 1), (2, "b", 2)], ["col1", "col2", "col3"], orc=True)},
            "sql": f'LOAD LABEL label14_{run} (DATA INFILE("@{{stage}}/{p}/data/input/'
            'example14.orc") INTO TABLE table14 FORMAT AS "orc" (col1, col2, col3)) WITH BROKER',
            "check": "SELECT * FROM table14",
            "expect": [[1, "a", 1], [2, "b", 2]],
        },
        {
            "case": "BROKER_LOAD.md: JSON simple mode",
            "tables": {"tbl1": "CREATE TABLE tbl1 (category VARCHAR(512), author VARCHAR(512), "
                       "title VARCHAR(512), price DOUBLE) DUPLICATE KEY(category) "
                       "DISTRIBUTED BY HASH(category)"},
            "files": {f"{p}/data/input/example1.json":
                      '{"category":"C++","author":"avc","title":"C++ primer","price":895}\n'},
            "sql": f'LOAD LABEL label15_{run} (DATA INFILE("@{{stage}}/{p}/data/input/'
            'example1.json") INTO TABLE tbl1 FORMAT AS "json") WITH BROKER',
            "check": "SELECT * FROM tbl1",
            "expect": [["C++", "avc", "C++ primer", 895]],
        },
        {
            "case": "BROKER_LOAD.md: JSON matched mode (jsonpaths, strip_outer_array)",
            "tables": {"tbl1": "CREATE TABLE tbl1 (category VARCHAR(512), author VARCHAR(512), "
                       "title VARCHAR(512), price DOUBLE) DUPLICATE KEY(category) "
                       "DISTRIBUTED BY HASH(category)"},
            "files": {f"{p}/data/input/example2.json": json.dumps([
                {"category": f"xuxb{i}", "author": f"{i}avc", "title": "SayingsoftheCentury",
                 "price": 895} for i in (111, 222, 333)])},
            "sql": f'LOAD LABEL label16_{run} (DATA INFILE("@{{stage}}/{p}/data/input/'
            'example2.json") INTO TABLE tbl1 FORMAT AS "json" (category, price, author)) '
            'WITH BROKER PROPERTIES ("strip_outer_array" = "true", '
            '"jsonpaths" = "[\\"$.category\\",\\"$.price\\",\\"$.author\\"]")',
            "check": "SELECT * FROM tbl1",
            "expect": [[f"xuxb{i}", f"{i}avc", None, 895] for i in (111, 222, 333)],
        },
        {
            "case": "BROKER_LOAD.md: JSON matched mode with json_root",
            "tables": {"tbl1": "CREATE TABLE tbl1 (category VARCHAR(512), author VARCHAR(512), "
                       "title VARCHAR(512), price DOUBLE) DUPLICATE KEY(category) "
                       "DISTRIBUTED BY HASH(category)"},
            "files": {f"{p}/data/input/example3.json": json.dumps({
                "id": 10001,
                "RECORDS": [
                    {"category": "11", "title": "SayingsoftheCentury", "price": 895},
                    {"category": "22", "author": "2avc", "price": 895},
                    {"category": "33", "author": "3avc", "title": "SayingsoftheCentury"},
                ],
                "comments": ["3 records", "there will be 3 rows"]})},
            "sql": f'LOAD LABEL label17_{run} (DATA INFILE("@{{stage}}/{p}/data/input/'
            'example3.json") INTO TABLE tbl1 FORMAT AS "json" (category, price, author)) '
            'WITH BROKER PROPERTIES ("json_root" = "$.RECORDS", "strip_outer_array" = "true", '
            '"jsonpaths" = "[\\"$.category\\",\\"$.price\\",\\"$.author\\"]")',
            "check": "SELECT * FROM tbl1",
            "expect": [["11", None, None, 895], ["22", "2avc", None, 895],
                       ["33", "3avc", None, None]],
        },
        {
            "case": "s3.md / minio.md: Parquet into user_behavior, timeout",
            "tables": {"user_behavior": "CREATE TABLE user_behavior (UserID INT NOT NULL, "
                       "ItemID INT NOT NULL, CategoryID INT NOT NULL, BehaviorType "
                       "VARCHAR(65533) NOT NULL, Timestamp DATETIME NOT NULL) DUPLICATE "
                       "KEY(UserID) DISTRIBUTED BY HASH(UserID) "
                       "PROPERTIES ('replication_num' = '1')"},
            "files": {f"{p}/user_behavior_sample.parquet": _parquet(
                [(142, 2869980, 2939262, "pv", "2017-11-25 03:43:22"),
                 (142, 2522236, 1669167, "pv", "2017-11-25 15:14:12")],
                ["UserID", "ItemID", "CategoryID", "BehaviorType", "Timestamp"])},
            "sql": f'LOAD LABEL UserBehavior_{run} (DATA INFILE("@{{stage}}/{p}/'
            'user_behavior_sample.parquet") INTO TABLE user_behavior) WITH BROKER '
            'PROPERTIES ("timeout" = "72000")',
            "check": "SELECT * FROM user_behavior",
            "expect": [[142, 2869980, 2939262, "pv", "2017-11-25 03:43:22"],
                       [142, 2522236, 1669167, "pv", "2017-11-25 15:14:12"]],
        },
        {
            "case": "Job properties: strict_mode, timezone, priority, load_mem_limit, "
                    "log_rejected_record_num, partial_update, merge_condition",
            "tables": {"pk": "CREATE TABLE pk (id INT, v INT, ts INT) PRIMARY KEY(id) "
                       "DISTRIBUTED BY HASH(id)"},
            "before": ["INSERT INTO pk VALUES (1, 10, 5), (2, 20, 5)"],
            "files": {f"{p}/props/pk.csv": "1,11,6\n2,21,4\n"},
            "sql": f'LOAD LABEL props_{run} (DATA INFILE("@{{stage}}/{p}/props/pk.csv") INTO '
            'TABLE pk COLUMNS TERMINATED BY "," (id, v, ts)) PROPERTIES ("strict_mode" = "true",'
            ' "timezone" = "UTC", "priority" = "HIGH", "load_mem_limit" = "1073741824", '
            '"log_rejected_record_num" = "10", "merge_condition" = "ts")',
            "check": "SELECT * FROM pk",
            "expect": [[1, 11, 6], [2, 20, 5]],
        },
        {
            "case": "Job properties: partial_update, partial_update_mode",
            "tables": {"pk2": "CREATE TABLE pk2 (id INT, a INT, b INT) PRIMARY KEY(id) "
                       "DISTRIBUTED BY HASH(id)"},
            "before": ["INSERT INTO pk2 VALUES (1, 10, 100)"],
            "files": {f"{p}/props/pk2.csv": "1,11\n"},
            "sql": f'LOAD LABEL partial_{run} (DATA INFILE("@{{stage}}/{p}/props/pk2.csv") INTO '
            'TABLE pk2 COLUMNS TERMINATED BY "," (id, a)) PROPERTIES ("partial_update" = "true",'
            ' "partial_update_mode" = "row")',
            "check": "SELECT * FROM pk2",
            "expect": [[1, 11, 100]],
        },
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    stage, db = args.stage, args.database
    run = time.strftime("%H%M%S")
    nova = Nova("nova_admin", db)
    stages = nova.client.get("/api/v1/stages", headers=nova.headers).json()["stages"]
    stage_id = next(row["id"] for row in stages if row["name"] == stage)
    records: list[dict[str, Any]] = []

    def upload(path: str, body: str | bytes) -> None:
        folder, name = path.rsplit("/", 1)
        data = body.encode() if isinstance(body, str) else body
        response = nova.client.post(
            f"/api/v1/stages/{stage_id}/files", headers=nova.headers,
            files={"file": (name, data)}, data={"path": folder},
        )
        response.raise_for_status()

    def step(case: str, sql: str, client: Nova = nova, **kwargs: Any) -> dict[str, Any]:
        item = client.run(sql, **kwargs)
        record = {
            "case": case, "sql": sql, "success": item.get("success"),
            "error": item.get("error"), "needs_confirmation": item.get("needs_confirmation"),
            "executed_sql": item.get("executed_sql"), "leaks": _leak_check(item),
        }
        records.append(record)
        return item

    def wait(label: str) -> dict[str, Any]:
        for _ in range(60):
            item = nova.run(f'SHOW LOAD WHERE LABEL = "{label}"')
            rows = item.get("rows") or []
            if rows and rows[-1][2] in {"FINISHED", "CANCELLED"}:
                cols = item["columns"]
                return dict(zip(cols, rows[-1], strict=False)) | {"_leaks": _leak_check(item)}
            time.sleep(2)
        return {"State": "TIMEOUT"}

    results: list[dict[str, Any]] = []
    for spec in cases(run):
        for table, ddl in spec["tables"].items():
            nova.run(f"DROP TABLE IF EXISTS {table} FORCE")
            nova.run(ddl)
        for path, body in spec["files"].items():
            upload(path, body)
        for sql in spec.get("before", []):
            sql = sql.format(db=db, stage=stage)
            item = nova.run(sql)
            if sql.startswith("LOAD"):
                wait(sql.split()[2].split(".")[-1])
        sql = spec["sql"].format(db=db, stage=stage)
        item = step(spec["case"], sql)
        label = sql.split()[2].split(".")[-1]
        job = wait(label) if item.get("success") else {"State": "NOT SUBMITTED"}
        check = nova.run(spec["check"])
        got = _rows(check.get("rows") or [])
        want = _rows(spec["expect"])
        passed = bool(item.get("success")) and job.get("State") == "FINISHED" and got == want
        results.append({
            "case": spec["case"], "label": label, "passed": passed,
            "state": job.get("State"), "error": job.get("ErrorMsg") or item.get("error"),
            "rows": check.get("rows"), "expected": spec["expect"],
            "executed_sql": item.get("executed_sql"),
            "leaks": sorted(set(_leak_check(item) + job.get("_leaks", []))),
        })
        print(("PASS " if passed else "FAIL ") + spec["case"], flush=True)

    # Monitoring and management: redaction of failed-job paths, CANCEL and ALTER LOAD.
    missing = f"missing_{run}"
    step("error path redaction: submit", f'LOAD LABEL {missing} (DATA INFILE("@{stage}/w4/'
         'nope/*.csv") INTO TABLE table1)')
    job = wait(missing)
    loads = nova.run(f"SELECT LABEL, STATE, ERROR_MSG FROM information_schema.loads WHERE "
                     f"LABEL = '{missing}'")
    error_text = str(job.get("ErrorMsg")) + json.dumps(loads.get("rows"))
    results.append({
        "case": "SHOW LOAD and information_schema.loads name the stage, not the bucket",
        "passed": f"@{stage}/w4/nope/*.csv" in error_text and "s3://" not in error_text,
        "state": job.get("State"), "error": job.get("ErrorMsg"), "rows": loads.get("rows"),
        "leaks": job.get("_leaks", []) + _leak_check(loads),
    })
    nova.run("DROP TABLE IF EXISTS slow_t FORCE")
    nova.run("CREATE TABLE slow_t (col1 INT, col2 STRING, col3 INT) DUPLICATE KEY(col1) "
             "DISTRIBUTED BY HASH(col1)")
    upload("w4/slow/a.csv", "1,a,1\n")
    managed = f"managed_{run}"
    submitted = step("CANCEL/ALTER LOAD: submit", f'LOAD LABEL {managed} (DATA INFILE('
                     f'"@{stage}/w4/slow/a.csv") INTO TABLE slow_t COLUMNS TERMINATED BY '
                     '",") PROPERTIES ("priority" = "LOWEST")')
    altered = step("ALTER LOAD priority", f'ALTER LOAD FOR {db}.{managed} PROPERTIES '
                   '("priority" = "HIGHEST")')
    cancelled = step("CANCEL LOAD", f'CANCEL LOAD FROM {db} WHERE LABEL = "{managed}"')
    job = wait(managed)
    # The job may finish before CANCEL arrives; the engine then answers that it
    # cannot cancel a finished job, which Nova passes through unchanged.
    results.append({
        "case": "ALTER LOAD and CANCEL LOAD reach the engine as the caller",
        "passed": bool(submitted.get("success")) and (
            altered.get("success") or "state" in str(altered.get("error")).lower()
        ) and (cancelled.get("success") or "finished" in str(cancelled.get("error")).lower()),
        "state": job.get("State"), "alter": altered.get("error") or "ok",
        "cancel": cancelled.get("error") or "ok", "leaks": [],
    })

    # Refusals.
    refusals = [
        ("raw S3 path", 'LOAD LABEL r1 (DATA INFILE("s3://bucket/input/file1.csv") INTO TABLE '
         'table1)', "reads files only from a stage"),
        ("inline credentials", 'LOAD LABEL r2 (DATA INFILE("@{s}/x.csv") INTO TABLE table1) WITH'
         ' BROKER ("aws.s3.access_key" = "AKIAREFUSED00000000", "aws.s3.secret_key" = '
         '"refused-secret")', "no broker name or broker properties"),
        ("broker name", 'LOAD LABEL r3 (DATA INFILE("@{s}/x.csv") INTO TABLE table1) WITH BROKER'
         ' "hdfs_broker"', "no broker name or broker properties"),
        ("credential property", 'LOAD LABEL r4 (DATA INFILE("@{s}/x.csv") INTO TABLE table1) '
         'PROPERTIES ("aws.s3.secret_key" = "refused-secret")', "no storage location"),
        ("Spark Load", 'LOAD LABEL r5 (DATA INFILE("@{s}/x.csv") INTO TABLE table1) WITH '
         'RESOURCE "spark0"', "Spark Load"),
        ("path outside the stage", 'LOAD LABEL r6 (DATA INFILE("@{s}/../x.csv") INTO TABLE '
         'table1)', "no empty"),
    ]
    for name, sql, expected in refusals:
        item = step(f"refused: {name}", sql.format(s=stage))
        text = json.dumps(item)
        results.append({
            "case": f"refused: {name}",
            "passed": not item.get("success") and expected in str(item.get("error"))
            and "refused-secret" not in text.replace(sql.format(s=stage), ""),
            "error": item.get("error"), "leaks": _leak_check(item),
        })
    alice = Nova("alice", db)
    item = step("alice without stage read access", f'LOAD LABEL a1_{run} (DATA INFILE('
                f'"@{stage}/w4/input/file1.csv") INTO TABLE table1)', client=alice)
    results.append({
        "case": "alice cannot load from a stage she cannot read",
        "passed": not item.get("success"), "error": item.get("error"),
        "leaks": _leak_check(item),
    })

    # SUBMIT TASK: a Nova task run once as the caller.
    nova.run("DROP TABLE IF EXISTS src_tbl FORCE")
    nova.run("DROP TABLE IF EXISTS tbl2 FORCE")
    nova.run("CREATE TABLE src_tbl (id INT, v STRING) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id)")
    nova.run("INSERT INTO src_tbl VALUES (1, 'a'), (2, 'b')")
    task = f"etl1_{run}"
    item = step("SUBMIT TASK", f'SUBMIT TASK {task} PROPERTIES ("session.insert_timeout" = '
                '"10000") AS CREATE TABLE tbl2 AS SELECT * FROM src_tbl')
    state = None
    for _ in range(90):
        described = nova.run(f"DESCRIBE TASK {task}")
        facts = dict((row[0], row[1]) for row in described.get("rows") or [])
        state = facts.get("last_run_state")
        if state in {"success", "failed"}:
            break
        time.sleep(2)
    check = nova.run("SELECT * FROM tbl2")
    results.append({
        "case": "SUBMIT TASK runs once as a Nova task of the caller's role",
        "passed": bool(item.get("success")) and state == "success"
        and _rows(check.get("rows") or []) == _rows([[1, "a"], [2, "b"]]),
        "result": item.get("rows"), "last_run_state": state, "rows": check.get("rows"),
        "leaks": _leak_check(item),
    })
    nova.run(f"DROP TASK {task}")

    failed = [r["case"] for r in results if not r["passed"]]
    leaks = [r["case"] for r in results if r.get("leaks")]
    report = {
        "workstream": "W4 Broker Load from stages, SUBMIT TASK",
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "nova": "http://127.0.0.1:5173",
        "engine": "StarRocks 4.1.4 (patched FE), dev stack, MinIO-backed stage",
        "stage": stage, "database": db, "run_folder": f"@{stage}/w4/",
        "summary": {"cases": len(results), "passed": len(results) - len(failed),
                    "failed": failed, "leaks": leaks},
        "results": results,
        "statements": records,
    }
    Path(args.out).write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
