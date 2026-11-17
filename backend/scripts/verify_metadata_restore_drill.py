"""Restore drill for Nova control metadata and Workspace objects (NDV-STORE-012, NDV-OPS-007).

Recovers a verified ``pg_dump`` backup of ``nova_control`` onto a *disposable*
PostgreSQL 17 server the way an operator would: provision the deployment's
roles with the normal bootstrap (new passwords; roles are cluster objects that a
database dump does not carry), restore the dump with its owners, grants and
row-level security policies in one transaction, and prove the restored copy is
usable together with the Workspace object storage it points at:

1. the backup file matches the digest recorded when it was taken;
2. schema version and metadata epoch match the backup receipt, and every
   applied migration's checksum matches the migration file in this checkout;
3. every table holds exactly the row count recorded at freeze time;
4. every foreign key was restored and is validated;
5. every Dataviz revision's definition matches its stored digest, every
   resource's revisions are complete from 1 to its draft revision, and every
   publication, publication reference and revision dependency points at a
   revision that exists;
6. every live Workspace file and published version that records a content
   digest has its object in Workspace storage with exactly that digest;
7. the normal migrator brings the copy to this code's schema with every row
   count unchanged, and the frozen copy is thawed;
8. a workload login works through the restored grants and policies: the
   Workspace reconciler, on the maintenance login, sees exactly the referenced
   keys, and another identity on that login sees none.

The disposable server's ``nova_control`` must be empty (the PG17 test fixture's
state); the schema and roles are removed afterwards. Run it with
``NOVA_TEST_METADATA_ADMIN_DSN`` and ``NOVA_TEST_METADATA_DISPOSABLE=acknowledged``
under the PG17 fixture lock. Nothing is written to the source database or to
object storage. Credentials are read from the operator's
private connection files and are never printed.

    python scripts/verify_metadata_restore_drill.py \\
        --backup-dir ~/.local/share/nova/<upgrade receipt dir> \\
        --dump nova-control-before-60.dump \\
        --target-container <disposable pg container> \\
        --output ../docs/reports/native-dataviz/restore-drill-attempt-2.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.core.exceptions import StorageError
from app.modules.viz.documents import canonical

MIGRATIONS = Path(__file__).resolve().parents[1] / "metadata_migrations"


class Drill:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.failed = False

    def check(self, step: str, passed: bool, **detail: Any) -> bool:
        self.steps.append({"step": step, "passed": bool(passed), **detail})
        self.failed |= not passed
        print(f"[{'PASS' if passed else 'FAIL'}] {step}", flush=True)
        return passed


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def restore(dump: Path, container: str, user: str, database: str) -> tuple[int, str]:
    """pg_restore inside the disposable server's container, reading the dump on stdin.

    Owners, grants and policies are restored as dumped: the bootstrap has just
    provisioned every role they name."""
    with dump.open("rb") as stream:
        result = subprocess.run(
            ["docker", "exec", "-i", container, "pg_restore", "-U", user, "-d", database,
             "--exit-on-error", "--single-transaction"],
            stdin=stream, capture_output=True, text=True, timeout=3600)
    # Errors name objects, never row values; keep only the first lines.
    return result.returncode, "\n".join(result.stderr.splitlines()[:5])


async def bring_to_current(environment: Any, receipt: dict[str, Any], drill: Drill) -> None:
    """A backup taken before the running code's schema is migrated forward with
    the normal migrator, then thawed: the backup was taken frozen."""
    from app.core.metadata_migrations import migrate
    from app.core.metadata_schema import load_migrations

    await migrate(environment.role_config("nova_meta_migrator"), app_version="d" * 40)
    async with await psycopg.AsyncConnection.connect(
        environment.config.dsn, autocommit=True, row_factory=dict_row) as admin:
        state = await (await admin.execute(
            "SELECT schema_version, metadata_epoch, maintenance "
            "FROM nova_meta.deployment_state")).fetchone()
        assert state is not None
        changed = {}
        for table, count in receipt["counts"].items():
            row = await (await admin.execute(sql.SQL(
                "SELECT count(*) AS n FROM nova_meta.{}").format(sql.Identifier(table)))).fetchone()
            if row is None or row["n"] != count:
                changed[table] = None if row is None else row["n"]
        drill.check("the normal migrator brings the restored copy to this code's schema "
                    "without changing data",
                    state["schema_version"] == len(load_migrations())
                    and str(state["metadata_epoch"]) == receipt["state"]["metadata_epoch"]
                    and not changed,
                    schema_version=state["schema_version"], changed=changed)
        # The backup was taken frozen; the recovered deployment is thawed for use.
        await admin.execute("UPDATE nova_meta.deployment_state SET maintenance=false")


async def verify_workload_access(environment: Any, rows: list[dict], drill: Drill) -> None:
    """The restored grants and RLS serve a workload login exactly as before."""
    from app.core.metadata_db import MetadataDatabase
    from app.modules.access_control.security_context import SecurityContext
    from app.modules.workspaces.object_reconciler import RECONCILER, referenced_in

    keys = sorted({row["object_key"] for row in rows})[:500]
    async with await psycopg.AsyncConnection.connect(
        environment.config.dsn, row_factory=dict_row) as admin:
        state = await (await admin.execute(
            "SELECT schema_version, metadata_epoch FROM nova_meta.deployment_state")).fetchone()
        assert state is not None
    database = MetadataDatabase(environment.role_config("nova_meta_maintenance"))
    await database.open(schema_version=state["schema_version"],
                        metadata_epoch=str(state["metadata_epoch"]))
    try:
        async with database.transaction(RECONCILER, read_only=True) as connection:
            seen = await referenced_in(connection, keys)
        other = SecurityContext("catalog-refresher", "SECURITYADMIN", principal_type="SERVICE")
        async with database.transaction(other, read_only=True) as connection:
            hidden = await referenced_in(connection, keys)
    finally:
        await database.close()
    drill.check("a workload login works through the restored grants and policies",
                bool(keys) and seen == set(keys) and hidden == set(),
                keys=len(keys), seen=len(seen), other_identity_saw=len(hidden))


Row = dict[str, Any]


def one(conn: psycopg.Connection[Row], query: Any) -> Row:
    row = conn.execute(query).fetchone()
    if row is None:
        raise RuntimeError("expected one row")
    return row


def verify_metadata(conn: psycopg.Connection[Row], drill: Drill, receipt: Row) -> list[Row]:
    state = one(conn, 
        "SELECT schema_version,metadata_epoch FROM nova_meta.deployment_state")
    drill.check("schema version and epoch match the backup receipt",
                state["schema_version"] == receipt["state"]["schema_version"]
                and str(state["metadata_epoch"]) == receipt["state"]["metadata_epoch"],
                schema_version=state["schema_version"])

    ledger = conn.execute(
        "SELECT version,checksum FROM nova_meta.schema_migrations ORDER BY version").fetchall()
    files = sorted(MIGRATIONS.glob("*.sql"))[: state["schema_version"]]
    recorded = [row["checksum"] for row in ledger]
    expected = [sha256_file(path) for path in files]
    drill.check("every applied migration matches this checkout's migration file",
                len(ledger) == state["schema_version"] and recorded == expected,
                applied=len(ledger))

    mismatched = {}
    for table, count in receipt["counts"].items():
        actual = one(conn, sql.SQL("SELECT count(*) AS n FROM nova_meta.{}").format(
            sql.Identifier(table)))["n"]
        if actual != count:
            mismatched[table] = {"expected": count, "restored": actual}
    drill.check("every table holds the row count recorded at freeze time", not mismatched,
                tables=len(receipt["counts"]), rows=sum(receipt["counts"].values()),
                mismatched=mismatched)

    keys = one(conn, 
        "SELECT count(*) AS n, count(*) FILTER (WHERE NOT convalidated) AS unvalidated "
        "FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace "
        "WHERE n.nspname='nova_meta' AND c.contype='f'")
    drill.check("every foreign key is restored and validated",
                keys["n"] > 0 and keys["unvalidated"] == 0, foreign_keys=keys["n"])

    bad_digest = 0
    for row in conn.execute("SELECT definition,digest FROM nova_meta.viz_revisions"):
        if hashlib.sha256(canonical(row["definition"])).hexdigest() != row["digest"]:
            bad_digest += 1
    revisions = one(conn, "SELECT count(*) AS n FROM nova_meta.viz_revisions")["n"]
    drill.check("every Dataviz revision matches its stored digest", bad_digest == 0,
                revisions=revisions, mismatched=bad_digest)

    gaps = one(conn, 
        "SELECT count(*) AS n FROM nova_meta.viz_resources r WHERE r.draft_revision IS NOT NULL "
        "AND (SELECT count(*) FROM nova_meta.viz_revisions v WHERE v.resource_id=r.resource_id "
        "AND v.revision BETWEEN 1 AND r.draft_revision) <> r.draft_revision")["n"]
    drill.check("every resource's revisions are complete up to its draft", gaps == 0,
                incomplete=gaps)
    dangling = {
        "publications": "SELECT count(*) AS n FROM nova_meta.viz_publications p WHERE NOT EXISTS "
        "(SELECT 1 FROM nova_meta.viz_revisions v WHERE v.resource_id=p.resource_id "
        "AND v.revision=p.revision)",
        "publication_references": "SELECT count(*) AS n FROM nova_meta.viz_publication_references "
        "p WHERE NOT EXISTS (SELECT 1 FROM nova_meta.viz_revisions v "
        "WHERE v.resource_id=p.resource_id AND v.revision=p.revision)",
        "revision_dependencies": "SELECT count(*) AS n FROM nova_meta.viz_revision_dependencies d "
        "WHERE NOT EXISTS (SELECT 1 FROM nova_meta.viz_revisions v "
        "WHERE v.resource_id=d.depends_on AND v.revision=d.depends_revision)",
        "current_publications": "SELECT count(*) AS n FROM nova_meta.viz_resources r "
        "WHERE r.current_publication_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "
        "nova_meta.viz_publications p WHERE p.publication_id=r.current_publication_id)",
    }
    found = {name: one(conn, query)["n"] for name, query in dangling.items()}
    drill.check("every publication, reference and dependency resolves",
                not any(found.values()), dangling=found)

    return conn.execute(
        "SELECT object_key, content_sha256 FROM nova_meta.config_workspace_entries "
        "WHERE entry_type='file' AND is_deleted IS NOT TRUE AND content_sha256 IS NOT NULL "
        "UNION ALL SELECT object_key, content_sha256 FROM "
        "nova_meta.config_workspace_file_versions WHERE NOT is_deleted "
        "AND state='published' AND content_sha256 IS NOT NULL").fetchall()


async def verify_objects(rows: list[dict], drill: Drill) -> None:
    from app.modules.workspaces.service import workspace_service

    verified, missing, changed = 0, 0, 0
    for row in rows:
        try:
            data = await asyncio.to_thread(workspace_service._read_object, row["object_key"])
        except StorageError as error:
            if error.status_code != 404:
                raise
            missing += 1
            continue
        if hashlib.sha256(data).hexdigest() == row["content_sha256"]:
            verified += 1
        else:
            changed += 1
    drill.check("every restored file reference has its object with the recorded digest",
                missing == 0 and changed == 0, objects=len(rows), verified=verified,
                missing=missing, changed=changed)


async def drill_once(args: argparse.Namespace, drill: Drill, report: dict[str, Any]) -> None:
    from tests.integration.test_metadata_bootstrap import disposable_environment

    receipt = json.loads((args.backup_dir / "before.json").read_text())
    backup = json.loads((args.backup_dir / "backup.json").read_text())
    dump = args.backup_dir / args.dump
    report["backup"] = {"bytes": dump.stat().st_size, "receipt_sha256": backup["sha256"]}
    if not drill.check("the backup file matches the digest recorded when it was taken",
                       sha256_file(dump) == backup["sha256"]):
        raise RuntimeError("backup digest mismatch")
    async with disposable_environment() as environment:
        result = await environment.bootstrap()
        drill.check("the bootstrap provisions the deployment's roles on the new server",
                    result.created and result.login_count == 15, logins=result.login_count)
        parts = conninfo_to_dict(environment.config.dsn)
        started = time.monotonic()
        code, errors = await asyncio.to_thread(
            restore, dump, args.target_container, str(parts["user"]), str(parts["dbname"]))
        report["restore_seconds"] = round(time.monotonic() - started, 1)
        if not drill.check("the backup restores in one transaction with its grants and "
                           "policies", code == 0, errors=errors):
            raise RuntimeError("restore failed")
        with psycopg.connect(environment.config.dsn, row_factory=dict_row) as conn:
            rows = verify_metadata(conn, drill, receipt)
        await verify_objects(rows, drill)
        await bring_to_current(environment, receipt, drill)
        await verify_workload_access(environment, rows, drill)
    report["disposable_state"] = "schema and roles removed by the fixture teardown"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--dump", required=True)
    parser.add_argument("--target-container", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    drill = Drill()
    report: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "result": "FAIL"}
    try:
        asyncio.run(drill_once(args, drill, report))
        report["result"] = "FAIL" if drill.failed else "PASS"
    except BaseException as error:  # noqa: BLE001 - recorded without payloads
        report["error"] = f"{type(error).__name__}: {str(error)[:160]}"
        # Where it failed, as file:line only; no values.
        report["error_at"] = [f"{Path(frame.filename).name}:{frame.lineno}"
                              for frame in traceback.extract_tb(error.__traceback__)][-4:]
    finally:
        report["steps"] = drill.steps
        report["finished_at"] = datetime.now(UTC).isoformat()
        args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({k: report.get(k) for k in ("result", "error", "restore_seconds")}))
    raise SystemExit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
