"""One-shot PostgreSQL schema installation, separate from application startup."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.core.metadata_db import (
    METADATA_DEPLOYMENT_LOCK,
    MetadataOperationFailed,
    MetadataOutcomeUnknown,
    MetadataPoolConfig,
    MetadataSchemaMismatch,
    MetadataTimeout,
)
from app.core.metadata_schema import (
    MIGRATION_DIRECTORY,
    SchemaManifestError,
    load_migrations,
    verify_applied_migrations,
)

METADATA_ROLES = (
    "nova_meta_owner", "nova_meta_migrator", "nova_meta_app",
    "nova_meta_readonly", "nova_meta_backup",
)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    schema_version: int
    applied_versions: tuple[int, ...]


async def migrate(
    config: MetadataPoolConfig, *, app_version: str,
    directory: Path = MIGRATION_DIRECTORY,
) -> MigrationResult:
    """Commit all pending DDL and its ledger together; never publish an epoch."""
    if not re.fullmatch(r"[0-9a-f]{40}", app_version):
        raise SchemaManifestError()
    migrations = load_migrations(directory)
    try:
        async with await psycopg.AsyncConnection.connect(
            config.dsn, autocommit=True, row_factory=dict_row, prepare_threshold=None,
            application_name="nova-metadata-migrator",
            connect_timeout=max(1, int(config.acquire_timeout)),
            options=(
                f"-c statement_timeout={config.statement_timeout_ms} "
                f"-c lock_timeout={config.lock_timeout_ms} "
                f"-c idle_in_transaction_session_timeout={config.idle_transaction_timeout_ms} "
                "-c synchronous_commit=on -c timezone=UTC"
            ),
        ) as conn:
            if conn.info.server_version // 10000 != 17:
                raise MetadataSchemaMismatch()
            async with conn.transaction():
                cursor = await conn.execute(
                    "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, "
                    "rolcreaterole, rolreplication FROM pg_catalog.pg_roles "
                    "WHERE rolname = ANY(%s)", (list(METADATA_ROLES),),
                )
                roles = {row["rolname"]: row for row in await cursor.fetchall()}
                if set(roles) != set(METADATA_ROLES):
                    raise MetadataSchemaMismatch()
                for name, role in roles.items():
                    if any(role[key] for key in (
                        "rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole",
                    )) or (name != "nova_meta_backup" and role["rolreplication"]):
                        raise MetadataSchemaMismatch()
                    if name == "nova_meta_owner" and role["rolcanlogin"]:
                        raise MetadataSchemaMismatch()
                cursor = await conn.execute(
                    "SELECT session_user = 'nova_meta_migrator' AND "
                    "pg_has_role(session_user, 'nova_meta_owner', 'SET') AS allowed"
                )
                identity = await cursor.fetchone()
                if not identity or not identity["allowed"]:
                    raise MetadataSchemaMismatch()
                await conn.execute("SET LOCAL ROLE nova_meta_owner")
                await conn.execute("SET LOCAL search_path = pg_catalog")
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(%s)", (METADATA_DEPLOYMENT_LOCK,),
                )
                cursor = await conn.execute(
                    "SELECT nspowner = 'nova_meta_owner'::regrole AS owned "
                    "FROM pg_catalog.pg_namespace WHERE nspname = 'nova_meta'"
                )
                schema = await cursor.fetchone()
                if schema is not None and not schema["owned"]:
                    raise MetadataSchemaMismatch()
                if schema is None:
                    await conn.execute("CREATE SCHEMA nova_meta AUTHORIZATION nova_meta_owner")
                await conn.execute("REVOKE ALL ON SCHEMA nova_meta FROM PUBLIC")
                for kind in ("TABLES", "SEQUENCES", "FUNCTIONS", "TYPES"):
                    await conn.execute(psycopg.sql.SQL(
                        "ALTER DEFAULT PRIVILEGES FOR ROLE nova_meta_owner IN SCHEMA nova_meta "
                        "REVOKE ALL ON {} FROM PUBLIC"
                    ).format(psycopg.sql.SQL(kind)))
                cursor = await conn.execute(
                    "SELECT to_regclass('nova_meta.schema_migrations') AS ledger"
                )
                ledger = await cursor.fetchone()
                applied = []
                if ledger and ledger["ledger"] is not None:
                    cursor = await conn.execute(
                        "SELECT version, name, checksum FROM nova_meta.schema_migrations "
                        "ORDER BY version"
                    )
                    applied = await cursor.fetchall()
                    if not applied:
                        raise SchemaManifestError()
                else:
                    cursor = await conn.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_class "
                        "WHERE relnamespace = 'nova_meta'::regnamespace) "
                        "OR EXISTS(SELECT 1 FROM pg_proc "
                        "WHERE pronamespace = 'nova_meta'::regnamespace) "
                        "OR EXISTS(SELECT 1 FROM pg_type "
                        "WHERE typnamespace = 'nova_meta'::regnamespace) AS occupied"
                    )
                    occupied = await cursor.fetchone()
                    if occupied and occupied["occupied"]:
                        raise SchemaManifestError()
                count = verify_applied_migrations(migrations, applied)
                if count:
                    cursor = await conn.execute(
                        "SELECT schema_version, maintenance FROM nova_meta.deployment_state "
                        "WHERE singleton = true"
                    )
                    state = await cursor.fetchone()
                    if not state or state["schema_version"] != count:
                        raise MetadataSchemaMismatch()
                    if count < len(migrations) and not state["maintenance"]:
                        raise MetadataSchemaMismatch()
                for migration in migrations[count:]:
                    # PostgreSQL forbids transaction control inside a DO block
                    # nested in our transaction, including dynamic COMMIT SQL.
                    await conn.execute(
                        "DO $nova_schema_revision$ BEGIN\n" + migration.statement
                        + "\nEND $nova_schema_revision$;", prepare=False,
                    )
                    await conn.execute(
                        "INSERT INTO nova_meta.schema_migrations "
                        "(version, name, checksum, app_version) VALUES (%s, %s, %s, %s)",
                        (migration.version, migration.name, migration.checksum, app_version),
                    )
                if count < len(migrations):
                    await conn.execute(
                        "UPDATE nova_meta.deployment_state SET schema_version = %s, "
                        "updated_at = clock_timestamp() WHERE singleton = true",
                        (len(migrations),),
                    )
            return MigrationResult(len(migrations), tuple(m.version for m in migrations[count:]))
    except (psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable):
        raise MetadataTimeout() from None
    except psycopg.OperationalError:
        raise MetadataOutcomeUnknown() from None
    except psycopg.Error:
        raise MetadataOperationFailed() from None


def main() -> int:
    from pydantic import ValidationError

    from app.core.config import MetadataMigratorSettings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-version", required=True)
    args = parser.parse_args()
    try:
        settings = MetadataMigratorSettings()
        result = asyncio.run(migrate(
            MetadataPoolConfig(
                settings.dsn.get_secret_value(),
                statement_timeout_ms=settings.statement_timeout_ms,
                lock_timeout_ms=settings.lock_timeout_ms,
            ), app_version=args.app_version,
        ))
    except (ValidationError, ValueError):
        print(json.dumps({"status": "REFUSED", "error": "invalid_migration_configuration"}))
        return 2
    except (
        MetadataOperationFailed, MetadataOutcomeUnknown, MetadataSchemaMismatch, MetadataTimeout,
    ) as error:
        print(json.dumps({"status": "REFUSED", "error": type(error).__name__}))
        return 1
    print(json.dumps({
        "status": "SCHEMA_INSTALLED", "schema_version": result.schema_version,
        "applied_versions": result.applied_versions, "metadata_epoch_published": False,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
