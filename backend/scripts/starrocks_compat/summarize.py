"""Give every StarRocks docs statement that still fails in a probe run a reason,
and write the per-page CSV and the counts the probe report needs.

Usage (from backend/; files in ~/.local/share/nova/starrocks-compat):
    uv run python scripts/starrocks_compat/summarize.py \
        --before results-2.jsonl --after results-3.jsonl \
        --strict strict-196.jsonl --csv ../docs/reports/starrocks-compat/probe-2-pages.csv \
        --json probe-2-summary.json

Rows of --after replace rows of --before with the same key; pages not re-run keep
their --before rows. Reasons, first match wins:
  w4_broker_load_task / w5_backup_restore / w8_routine_load / w9_iceberg
                     Owned by another workstream (tagged, not fixed here).
  external_system    S3, HDFS, Hive, Hudi, Delta, JDBC, Elasticsearch, Kafka, Azure,
                     GCS or other systems that are not part of the stack.
  cluster_admin      Shared-data, nodes, warehouses, storage volumes, cluster ids,
                     account statements: not run or not meaningful on one dev BE.
  engine_crash       Not run: crashes the pinned backend.
  docs_error         Syntax the engine rejects, placeholders, objects created twice,
                     reused labels, statements merged in the docs, printed output.
  engine_differs     The pinned 4.1.4 engine rejects what the docs describe (newer or
                     different behaviour); the same error comes back without Nova.
  timing             An asynchronous job (schema change, rollup, MV) was still running.
  missing_object     An object the docs assume is still missing (no fixture possible:
                     it comes from a load, an external system, or another session).
  nova_gap           Nova fails, the engine runs it from the same starting state.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import re
from pathlib import Path

HOME = Path.home() / ".local/share/nova/starrocks-compat"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

FAILED = {"both_fail", "nova_gap", "timing", "fixture_error"}

PAGE_RULES = [
    ("w5_backup_restore", re.compile(r"backup_restore/|Backup_and_restore|Exporting_faq|"
                                     r"cluster_snapshot|SNAPSHOT", re.I)),
    ("w8_routine_load", re.compile(r"routine_load|RoutineLoad|routine-load|Kafka|Flink|"
                                   r"Json_loading", re.I)),
    ("w9_iceberg", re.compile(r"iceberg", re.I)),
    ("w4_broker_load_task", re.compile(r"BrokerLoad|BROKER_LOAD|broker_load|SUBMIT_TASK|"
                                       r"DROP_TASK|ALTER_TASK|SparkLoad|hdfs_load|loading/s3|"
                                       r"loading/gcs|loading/azure|loading/minio|"
                                       r"loading/tencent|loading/huawei|"
                                       r"Loading_data_template|SHOW_LOAD|CANCEL_LOAD|"
                                       r"ALTER_LOAD", re.I)),
]
SQL_RULES = [
    ("w5_backup_restore", re.compile(r"^\s*(BACKUP|RESTORE|CANCEL\s+(BACKUP|RESTORE)|"
                                     r"(CREATE|DROP)\s+REPOSITORY|SHOW\s+(SNAPSHOT|BACKUP|"
                                     r"RESTORE|REPOSITORIES))\b", re.I)),
    ("w8_routine_load", re.compile(r"ROUTINE\s+LOAD", re.I)),
    ("w4_broker_load_task", re.compile(r"^\s*(LOAD\s+LABEL|SUBMIT\s+TASK)\b|WITH\s+BROKER|"
                                       r"\bSUBMIT\s+TASK\b", re.I)),
    ("w9_iceberg", re.compile(r"iceberg", re.I)),
]
EXTERNAL = re.compile(
    r"Access storage error|UnknownHost|Connection refused|cannot compute object|"
    r"Unknown catalog|catalog .* (does not|doesn't) exist|resource .* not exist|"
    r"Resource\(.*\) does not exist|hive|hudi|delta|paimon|jdbc|elasticsearch|kudu|"
    r"maxcompute|odps|hdfs://|s3a?://|oss://|cos://|gs://|abfss?://|wasbs?://|"
    r"ENGINE\s*=\s*(MYSQL|HIVE|ES|ELASTICSEARCH|JDBC|BROKER|HUDI|ICEBERG)|"
    r"connector|metastore|thrift|kafka|pulsar|EXTERNAL\s+(CATALOG|TABLE|RESOURCE)|"
    r"CREATE\s+RESOURCE|get TableMeta failed|Failed to get file status|No files were found|"
    r"doesn't get file|FILES\s*\(|external table",
    re.I,
)
CLUSTER = re.compile(
    r"storage volume|warehouse|compute node|cloud native|shared.data|shared-data|"
    r"run_mode|Not found compute node|backend\b.*id|be_label|DECOMMISSION|"
    r"SHOW\s+PROC\s+'/(jobs|statistic|backends|frontends|cluster)|"
    r"cannot find user|Unknown user|max_user_connections|resource group|"
    r"set_config|ADMIN|SYSTEM\b|BLACKLIST",
    re.I,
)
DOCS = re.compile(
    r"Getting syntax error|No viable statement|Unexpected input|already exists|"
    r"already been used|already exist|Duplicate column|is a reserved|\\G|"
    r"invalid data volume expression|Cooldown time should be later|"
    r"must be an aggregate expression|No tables used|Can't create database|"
    r"database exists",
    re.I,
)
TIMING = re.compile(r"being created on the table|schema change operation is in progress|"
                    r"is being|in progress|not finished|wait until", re.I)
MISSING = re.compile(r"Unknown table|Unknown database|not found|not exist|does not exist|"
                     r"cannot be resolved|Unknown column|Unknown partition|No database selected",
                     re.I)
PLACEHOLDER = re.compile(
    r"\b(tbl_name|table_name|db_name|database_name|tbl|label_name|job_name|mv_name|"
    r"partition_name|index_name|view_name|catalog_name|xxx|your_|<|\[)", re.I
)


def error_of(row: dict) -> str:
    strict = row.get("strict") or {}
    if row["outcome"] == "nova_gap":
        return str(strict.get("nova") or row.get("nova_error") or "")
    return str(row.get("engine_error") or strict.get("engine") or row.get("nova_error") or "")


def reason(row: dict, overrides: dict[str, str]) -> str:
    if row["key"] in overrides:
        return overrides[row["key"]]
    sql, page, error = row["sql"], row["page"], error_of(row)
    if row["outcome"] == "nova_gap":
        return "nova_gap"
    if row["outcome"] == "timing" or TIMING.search(error):
        return "timing"
    for name, pattern in SQL_RULES:
        if pattern.search(sql):
            return name
    for name, pattern in PAGE_RULES:
        if pattern.search(page):
            return name
    if EXTERNAL.search(error) or EXTERNAL.search(sql):
        return "external_system"
    if CLUSTER.search(error) or CLUSTER.search(sql):
        return "cluster_admin"
    if DOCS.search(error):
        return "docs_error"
    if MISSING.search(error):
        if PLACEHOLDER.search(sql) or re.search(r"'(tbl_name|db_name|table_name)'", error):
            return "docs_error"
        return "missing_object"
    return "engine_differs"


def area(page: str) -> str:
    parts = page.split("/")
    if parts[0] == "sql-reference" and len(parts) > 2:
        if parts[1] == "sql-statements" and len(parts) > 3:
            return "/".join(parts[:3])
        return "/".join(parts[:2])
    return parts[0]


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (HOME / name).read_text().splitlines() if line]


def bucket(outcome: str) -> str:
    if outcome.startswith("skipped"):
        return "skipped"
    if outcome in ("template", "signature"):
        return "template"
    if outcome in ("order_artifact",):
        return "ok"
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--strict")
    parser.add_argument("--overrides", default=str(Path(__file__).with_name("reasons.json")))
    parser.add_argument("--csv", required=True)
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    overrides_path = Path(args.overrides)
    overrides = (
        {k: v["reason"] for k, v in json.loads(overrides_path.read_text()).items()}
        if overrides_path.exists() else {}
    )
    before = {row["key"]: row for row in load(args.before)}
    after_rows = [row for row in load(args.after) if ":fixture:" not in row["key"]]
    fixture_rows = [row for row in load(args.after) if ":fixture:" in row["key"]]
    rerun_pages = {row["page"] for row in after_rows}
    merged = {k: v for k, v in before.items() if v["page"] not in rerun_pages}
    merged.update({row["key"]: row for row in after_rows})

    areas: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in before.values():
        areas[area(row["page"])]["before_" + bucket(row["outcome"])] += 1
    for row in merged.values():
        areas[area(row["page"])]["after_" + bucket(row["outcome"])] += 1

    reasons: collections.Counter = collections.Counter()
    reasons_by_area: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    failing = []
    for row in merged.values():
        if row["outcome"] in FAILED:
            why = reason(row, overrides)
            row["reason"] = why
            reasons[why] += 1
            reasons_by_area[area(row["page"])][why] += 1
            failing.append(row)

    pages: dict[str, dict] = {}
    for row in merged.values():
        entry = pages.setdefault(row["page"], collections.Counter())
        entry[bucket(row["outcome"])] += 1
        if "reason" in row:
            entry["reason:" + row["reason"]] += 1
    before_pages: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in before.values():
        before_pages[row["page"]][bucket(row["outcome"])] += 1
    fixture_errors = collections.Counter(
        row["page"] for row in fixture_rows if row["outcome"] != "fixture_ok"
    )
    reason_names = sorted(reasons)
    columns = ["page", "area", "fixture", "fixture_errors", "ok_before", "ok", "nova_gap",
               "timing", "both_fail", "template", "skipped", *reason_names]
    with Path(args.csv).open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for page in sorted(pages):
            entry = pages[page]
            fixture = (FIXTURES / Path(page).with_suffix(".sql")).exists()
            writer.writerow([
                page, area(page), "yes" if fixture else "", fixture_errors.get(page, 0),
                before_pages[page]["ok"], entry["ok"], entry["nova_gap"], entry["timing"],
                entry["both_fail"], entry["template"], entry["skipped"],
                *[entry["reason:" + name] for name in reason_names],
            ])

    strict_summary = {}
    if args.strict:
        strict_rows = load(args.strict)
        strict_summary = dict(collections.Counter(row["strict"]["verdict"] for row in strict_rows))
    totals_before = collections.Counter(bucket(r["outcome"]) for r in before.values())
    totals_after = collections.Counter(bucket(r["outcome"]) for r in merged.values())
    summary = {
        "statements": len(merged),
        "rerun_pages": len(rerun_pages),
        "fixture_files": sum(1 for _ in FIXTURES.rglob("*.sql")),
        "fixture_statements": len(fixture_rows),
        "fixture_failures": [
            {"key": r["key"], "error": r.get("nova_error", r["outcome"])[:300]}
            for r in fixture_rows if r["outcome"] != "fixture_ok"
        ],
        "totals_before": dict(totals_before),
        "totals_after": dict(totals_after),
        "areas": {name: dict(counts) for name, counts in sorted(areas.items())},
        "reasons": dict(reasons.most_common()),
        "reasons_by_area": {k: dict(v) for k, v in sorted(reasons_by_area.items())},
        "strict_196": strict_summary,
        "nova_gaps": [
            {"key": r["key"], "sql": r["sql"][:600], "nova": error_of(r)[:400],
             "engine": str((r.get("strict") or {}).get("engine", "ok"))[:200]}
            for r in failing if r["reason"] == "nova_gap"
        ],
        "unexplained": [
            {"key": r["key"], "reason": r["reason"], "sql": r["sql"][:300],
             "error": error_of(r)[:300]}
            for r in failing if r["reason"] in ("engine_differs", "missing_object")
        ],
    }
    (HOME / args.json).write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: summary[k] for k in (
        "statements", "rerun_pages", "fixture_files", "totals_before", "totals_after",
        "reasons", "strict_196")}, indent=1))


if __name__ == "__main__":
    main()
