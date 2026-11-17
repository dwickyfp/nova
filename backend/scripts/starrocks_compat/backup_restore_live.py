"""Live check of backup and restore through a repository on a stage (StarRocks compat W5).

As ``nova_admin`` (active role ACCOUNTADMIN): create a repository in a stage
folder, back up a small database, restore a dropped table, restore under a new
name, and read SHOW REPOSITORIES / BACKUP / SNAPSHOT / RESTORE. As ``alice``:
every repository, backup and restore statement is refused. Storage URLs and
inline credentials are refused. The repository is dropped at the end.

Usage (from backend/; passwords are read in-script and never printed):
    UV_NO_SYNC=1 uv run python scripts/starrocks_compat/backup_restore_live.py \
        --stage compat_w5_stage --database compat_w5_db \
        --out ../docs/reports/starrocks-compat/w5-backup-restore-live.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from unload_live import Nova, _leak_check, _rows  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    stage, db = args.stage, args.database
    run = time.strftime("%H%M%S")
    repo, snap = f"compat_w5_repo_{run}", f"snap_{run}"
    nova = Nova("nova_admin", db)
    results: list[dict[str, Any]] = []

    def step(case: str, sql: str, *, client: Nova = nova, expect_ok: bool = True,
             contains: str | None = None, **kwargs: Any) -> dict[str, Any]:
        item = client.run(sql, **kwargs)
        ok = bool(item.get("success")) == expect_ok
        if contains is not None:
            ok = ok and contains in json.dumps(item)
        results.append({
            "case": case, "sql": sql, "passed": ok, "success": item.get("success"),
            "error": item.get("error"), "needs_confirmation": item.get("needs_confirmation"),
            "columns": item.get("columns"), "rows": item.get("rows"),
            "executed_sql": item.get("executed_sql"), "leaks": _leak_check(item),
        })
        print(("PASS " if ok else "FAIL ") + case, flush=True)
        return item

    def wait(sql: str, state_col: str) -> dict[str, Any]:
        for _ in range(90):
            item = nova.run(sql)
            rows = item.get("rows") or []
            if rows:
                row = dict(zip(item["columns"], rows[-1], strict=False))
                if row.get(state_col) in {"FINISHED", "CANCELLED"}:
                    return row
            time.sleep(2)
        return {state_col: "TIMEOUT"}

    data = [[1, "north", 10.5], [2, "south", 20.0], [3, "east", 7.25]]
    nova.run("DROP TABLE IF EXISTS sales FORCE")
    for table in ("regions", "regions_copy", "regions_cancel"):
        nova.run(f"DROP TABLE IF EXISTS {table} FORCE")
    nova.run("CREATE TABLE sales (id INT, region STRING, amount DOUBLE) DUPLICATE KEY(id) "
             "DISTRIBUTED BY HASH(id) PROPERTIES ('replication_num' = '1')")
    nova.run("INSERT INTO sales VALUES " + ", ".join(f"({a}, '{b}', {c})" for a, b, c in data))
    nova.run("CREATE TABLE regions (region STRING, manager STRING) DUPLICATE KEY(region) "
             "DISTRIBUTED BY HASH(region) PROPERTIES ('replication_num' = '1')")
    nova.run("INSERT INTO regions VALUES ('north', 'Ana'), ('south', 'Budi')")

    # Refusals first: nothing is created.
    step("refused: storage URL location", f'CREATE REPOSITORY r_{run} WITH BROKER ON LOCATION '
         '"s3a://bucket_s3/backup"', expect_ok=False, contains="only in a stage")
    step("refused: inline credentials", f'CREATE REPOSITORY r_{run} WITH BROKER ON LOCATION '
         f'"@{stage}/backups/" PROPERTIES ("aws.s3.access_key" = "AKIAREFUSED0000000", '
         '"aws.s3.secret_key" = "refused-secret")', expect_ok=False,
         contains="no broker name or properties")
    alice = Nova("alice", db)
    for sql in (
        f'CREATE REPOSITORY a_{run} WITH BROKER ON LOCATION "@{stage}/alice/"',
        "SHOW REPOSITORIES",
        f"BACKUP DATABASE {db} SNAPSHOT a_{run} TO {repo}",
        f"SHOW BACKUP FROM {db}",
        f"SHOW SNAPSHOT ON {repo}",
        f"RESTORE SNAPSHOT {db}.a_{run} FROM {repo} PROPERTIES ('backup_timestamp' = 'x')",
        f"CANCEL BACKUP FROM {db}",
        f"DROP REPOSITORY {repo}",
    ):
        step(f"alice refused: {sql.split(' ')[0]} {sql.split(' ')[1]}", sql, client=alice,
             expect_ok=False, contains="ACCOUNTADMIN")

    step("CREATE REPOSITORY on a stage folder", f'CREATE REPOSITORY {repo} WITH BROKER ON '
         f'LOCATION "@{stage}/backups_{run}/"', contains=f"@{stage}/backups_{run}")
    shown = step("SHOW REPOSITORIES names the stage", "SHOW REPOSITORIES",
                 contains=f"@{stage}/backups_{run}")
    step("BACKUP asks for confirmation", f"BACKUP DATABASE {db} SNAPSHOT {snap} TO {repo} "
         "ON (TABLE sales, TABLE regions)", confirm=False, expect_ok=False,
         contains='"needs_confirmation": true')
    step("BACKUP DATABASE ... ON (TABLE ...)", f"BACKUP DATABASE {db} SNAPSHOT {snap} TO {repo} "
         "ON (TABLE sales, TABLE regions) PROPERTIES ('type' = 'full', 'timeout' = '3600')")
    backup = wait(f"SHOW BACKUP FROM {db}", "State")
    results.append({"case": "backup finished", "passed": backup.get("State") == "FINISHED",
                    "job": backup, "leaks": []})
    snapshots = step("SHOW SNAPSHOT ON repository", f"SHOW SNAPSHOT ON {repo} WHERE SNAPSHOT = "
                     f"'{snap}'", contains=snap)
    timestamp = (snapshots.get("rows") or [[None, None]])[0][1]
    step("SHOW SNAPSHOT details", f"SHOW SNAPSHOT ON {repo} WHERE SNAPSHOT = '{snap}' AND "
         f"TIMESTAMP = '{timestamp}'", contains="sales")

    step("drop a table to restore", "DROP TABLE sales FORCE", confirm=True)
    step("RESTORE a table", f"RESTORE SNAPSHOT {db}.{snap} FROM {repo} ON (TABLE sales) "
         f"PROPERTIES ('backup_timestamp' = '{timestamp}', 'replication_num' = '1')")
    restore = wait(f"SHOW RESTORE FROM {db}", "State")
    rows = nova.run("SELECT * FROM sales").get("rows") or []
    results.append({"case": "restored table has the backed-up rows",
                    "passed": restore.get("State") == "FINISHED" and _rows(rows) == _rows(data),
                    "job": restore, "rows": rows, "leaks": []})
    step("RESTORE under a new name (AS)", f"RESTORE SNAPSHOT {db}.{snap} FROM {repo} ON (TABLE "
         f"regions AS regions_copy) PROPERTIES ('backup_timestamp' = '{timestamp}', "
         "'replication_num' = '1')")
    restore = wait(f"SHOW RESTORE FROM {db}", "State")
    rows = nova.run("SELECT * FROM regions_copy").get("rows") or []
    results.append({"case": "restored copy has the backed-up rows",
                    "passed": restore.get("State") == "FINISHED"
                    and _rows(rows) == _rows([["north", "Ana"], ["south", "Budi"]]),
                    "job": restore, "rows": rows, "leaks": []})
    step("SHOW RESTORE", f"SHOW RESTORE FROM {db}", contains='"regions"')

    # CANCEL: start a job and cancel it; a job that already finished cannot be.
    step("BACKUP to cancel", f"BACKUP DATABASE {db} SNAPSHOT c_{snap} TO {repo} ON (TABLE "
         "regions)")
    step("CANCEL BACKUP", f"CANCEL BACKUP FROM {db}")
    cancelled = wait(f"SHOW BACKUP FROM {db}", "State")
    results.append({"case": "backup cancelled or already finished",
                    "passed": cancelled.get("State") in {"CANCELLED", "FINISHED"},
                    "job": cancelled, "leaks": []})
    step("RESTORE to cancel", f"RESTORE SNAPSHOT {db}.{snap} FROM {repo} ON (TABLE regions AS "
         f"regions_cancel) PROPERTIES ('backup_timestamp' = '{timestamp}', "
         "'replication_num' = '1')")
    cancel = nova.run(f"CANCEL RESTORE FROM {db}")
    restore = wait(f"SHOW RESTORE FROM {db}", "State")
    results.append({
        "case": "CANCEL RESTORE stops a running restore (or reports it already finished)",
        "passed": (cancel.get("success") and restore.get("State") == "CANCELLED")
        or (not cancel.get("success") and "No restore is currently running"
            in str(cancel.get("error")) and restore.get("State") == "FINISHED"),
        "cancel": cancel.get("error") or "ok", "job": restore, "leaks": _leak_check(cancel),
    })
    nova.run("DROP TABLE IF EXISTS regions_cancel FORCE")

    # A read-only repository with the same name on the same folder (as another
    # cluster would create it) sees the snapshots and can restore from them.
    step("DROP REPOSITORY asks for confirmation", f"DROP REPOSITORY {repo}", confirm=False,
         expect_ok=False, contains='"needs_confirmation": true')
    step("DROP REPOSITORY", f"DROP REPOSITORY {repo}")
    step("CREATE READ ONLY REPOSITORY on the same folder", "CREATE READ ONLY REPOSITORY "
         f'{repo} WITH BROKER ON LOCATION "@{stage}/backups_{run}/"')
    step("read-only repository lists the snapshot", f"SHOW SNAPSHOT ON {repo}", contains=snap)
    nova.run("DROP TABLE IF EXISTS regions FORCE")
    step("RESTORE from the read-only repository", f"RESTORE SNAPSHOT {db}.{snap} FROM {repo} ON "
         f"(TABLE regions) PROPERTIES ('backup_timestamp' = '{timestamp}', "
         "'replication_num' = '1')")
    restore = wait(f"SHOW RESTORE FROM {db}", "State")
    rows = nova.run("SELECT * FROM regions").get("rows") or []
    results.append({"case": "restore from a read-only repository",
                    "passed": restore.get("State") == "FINISHED"
                    and _rows(rows) == _rows([["north", "Ana"], ["south", "Budi"]]),
                    "job": restore, "rows": rows, "leaks": []})
    step("BACKUP to a read-only repository is refused by the engine", f"BACKUP DATABASE {db} "
         f"SNAPSHOT r_{snap} TO {repo} ON (TABLE regions)", expect_ok=False)
    step("DROP READ ONLY REPOSITORY", f"DROP REPOSITORY {repo}")

    failed = [r["case"] for r in results if not r["passed"]]
    leaks = [r["case"] for r in results if r.get("leaks")]
    report = {
        "workstream": "W5 backup and restore with a repository on a stage",
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "nova": "http://127.0.0.1:5173",
        "engine": "StarRocks 4.1.4 (patched FE), dev stack, MinIO-backed stage",
        "stage": stage, "database": db, "repository_folder": f"@{stage}/backups_{run}/",
        "show_repositories": shown.get("rows"),
        "summary": {"cases": len(results), "passed": len(results) - len(failed),
                    "failed": failed, "leaks": leaks},
        "results": results,
    }
    Path(args.out).write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
