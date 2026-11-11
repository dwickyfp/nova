"""Preserve inactive source stores without granting runtime access to them."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql

from app.core.metadata_bootstrap import WORKLOAD_ROLES
from app.core.metadata_db import METADATA_DEPLOYMENT_LOCK
from app.core.metadata_import import ImportColumn, import_row_id, target_type
from app.core.metadata_migrations import migrate
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401

pytestmark = pytest.mark.metadata_pg
SOURCE = json.loads(
    (Path(__file__).parents[3] / "docs/architecture/production-refactor/inventory.json").read_text()
)["live"]["tables"]
TABLES = (
    "CONFIG_DASHBOARDS",
    "CONFIG_DASHBOARD_WIDGETS",
    "CONFIG_PINNED_QUERIES",
    "LINEAGE_LOAD_HISTORY",
    "QUALITY_TABLE_STATS",
    "STAGE_FILE_MANIFEST",
    "USAGE_QUERY_STATS",
)
PARTITIONS = (
    "lineage_load_history_unpartitioned",
    "quality_table_stats_unpartitioned",
    "usage_query_stats_unpartitioned",
)


@pytest_asyncio.fixture
async def retained(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="c" * 40)
    return environment


@asynccontextmanager
async def importer(env):
    async with (
        await psycopg.AsyncConnection.connect(
            env.role_config("nova_meta_migrator").dsn,
            autocommit=True,
        ) as connection,
        connection.transaction(),
    ):
        await connection.execute("SET LOCAL ROLE nova_meta_owner")
        yield connection


def sample(table, *, suffix="a", optional_nulls=False):
    values = {}
    for column in SOURCE[table]["columns"]:
        name, kind = column["name"], column["type"]
        nullable = column["nullable"] == "YES"
        if nullable and optional_nulls:
            value = None
        elif kind == "datetime":
            value = datetime(2025, 1, 2, 7, 30)
        elif kind == "bigint(20)":
            value = 2**63 - 1
        elif kind == "int(11)":
            value = -(2**31)
        elif kind == "tinyint(1)":
            value = 1
        else:
            value = "value-界-" + suffix
        values[name] = ImportColumn(
            name,
            kind,
            nullable,
            "Asia/Jakarta" if kind == "datetime" else None,
        ).convert(value)
    if SOURCE[table]["key_model"] == "DUPLICATE":
        values["record_id"] = import_row_id(uuid4(), table, "c" * 64, 0)
    return values


async def insert(connection, table, values):
    await connection.execute(
        sql.SQL("INSERT INTO nova_meta.{} ({}) VALUES ({})").format(
            sql.Identifier(table.lower()),
            sql.SQL(",").join(map(sql.Identifier, values)),
            sql.SQL(",").join(sql.Placeholder() for _ in values),
        ),
        tuple(values.values()),
    )


async def rows(env, table):
    async with importer(env) as connection:
        cursor = await connection.execute(
            sql.SQL("SELECT * FROM nova_meta.{}").format(
                sql.Identifier(table.lower()),
            )
        )
        return [
            dict(zip([d.name for d in cursor.description], row, strict=True))
            for row in await cursor.fetchall()
        ]


@pytest.mark.parametrize("table", TABLES)
async def test_every_source_column_type_nullability_and_value_survives(retained, table):
    cursor = await retained.admin.execute(
        "SELECT column_name,data_type,character_maximum_length,is_nullable "
        "FROM information_schema.columns WHERE table_schema='nova_meta' AND table_name=%s",
        (table.lower(),),
    )
    actual = {r["column_name"]: r for r in await cursor.fetchall()}
    expected_names = {c["name"] for c in SOURCE[table]["columns"]}
    if SOURCE[table]["key_model"] == "DUPLICATE":
        expected_names.add("record_id")
    assert set(actual) == expected_names
    for column in SOURCE[table]["columns"]:
        destination = target_type(column["type"])
        if destination.startswith("varchar"):
            assert actual[column["name"]]["data_type"] == "character varying"
            assert actual[column["name"]]["character_maximum_length"] == int(
                destination.removeprefix("varchar(").removesuffix(")"),
            )
        else:
            expected = "timestamp with time zone" if destination == "timestamptz" else destination
            assert actual[column["name"]]["data_type"] == expected
        assert actual[column["name"]]["is_nullable"] == column["nullable"]
    first = sample(table)
    second = sample(table, suffix="b", optional_nulls=True)
    async with importer(retained) as connection:
        await insert(connection, table, first)
        await insert(connection, table, second)
    actual_rows = await rows(retained, table)
    assert len(actual_rows) == 2 and first in actual_rows and second in actual_rows
    for value in first.values():
        if isinstance(value, datetime):
            assert value == datetime(2025, 1, 2, 0, 30, tzinfo=UTC)


@pytest.mark.parametrize("table", [t for t in TABLES if SOURCE[t]["key_model"] == "DUPLICATE"])
async def test_identical_source_occurrences_remain_distinct_and_exact_replay_conflicts(
    retained, table
):
    first = sample(table)
    snapshot = uuid4()
    first["record_id"] = import_row_id(snapshot, table, "e" * 64, 0)
    second = {**first, "record_id": import_row_id(snapshot, table, "e" * 64, 1)}
    async with importer(retained) as connection:
        await insert(connection, table, first)
        await insert(connection, table, second)
    with pytest.raises(psycopg.errors.UniqueViolation):
        async with importer(retained) as connection:
            await insert(connection, table, first)
    assert len(await rows(retained, table)) == 2


@pytest.mark.parametrize("role", (*WORKLOAD_ROLES, "nova_meta_readonly", "nova_meta_backup"))
async def test_every_runtime_and_readonly_identity_is_refused(retained, role):
    async with await psycopg.AsyncConnection.connect(
        retained.role_config(role).dsn,
        autocommit=True,
    ) as connection:
        await connection.execute(
            "SELECT set_config('nova.principal','retained-reader',false), "
            "set_config('nova.active_role','ACCOUNTADMIN',false), "
            "set_config('nova.security_context_version','1',false), "
            "set_config('nova.principal_type','USER',false)"
        )
        for table in (*[t.lower() for t in TABLES], *PARTITIONS):
            source = table.removesuffix("_unpartitioned").upper()
            key = sql.Identifier(SOURCE[source]["columns"][0]["name"])
            for statement in (
                "SELECT * FROM nova_meta.{}",
                "INSERT INTO nova_meta.{} DEFAULT VALUES",
                "UPDATE nova_meta.{} SET {key}={key}",
                "DELETE FROM nova_meta.{}",
                "TRUNCATE nova_meta.{}",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    await connection.execute(
                        sql.SQL(statement).format(sql.Identifier(table), key=key)
                    )


async def test_migrator_requires_explicit_owner_role(retained):
    async with await psycopg.AsyncConnection.connect(
        retained.role_config("nova_meta_migrator").dsn,
        autocommit=True,
    ) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await insert(connection, "CONFIG_DASHBOARDS", sample("CONFIG_DASHBOARDS"))


@pytest.mark.parametrize("table", TABLES)
async def test_owner_cannot_mutate_or_read_retained_rows_after_maintenance(retained, table):
    async with importer(retained) as connection:
        await insert(connection, table, sample(table))
    await retained.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false",
        (uuid4(),),
    )
    assert await rows(retained, table) == []
    for statement in (
        "INSERT INTO nova_meta.{} DEFAULT VALUES",
        "DELETE FROM nova_meta.{}",
        "UPDATE nova_meta.{} SET {key}={key}",
        "TRUNCATE nova_meta.{}",
    ):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            async with importer(retained) as connection:
                await connection.execute(
                    sql.SQL(statement).format(
                        sql.Identifier(table.lower()),
                        key=sql.Identifier(SOURCE[table]["columns"][0]["name"]),
                    )
                )


@pytest.mark.parametrize("table", PARTITIONS)
async def test_direct_partition_truncate_is_also_fenced_by_maintenance(retained, table):
    await retained.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false",
        (uuid4(),),
    )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        async with importer(retained) as connection:
            await connection.execute(sql.SQL("TRUNCATE nova_meta.{}").format(sql.Identifier(table)))


async def test_import_holds_the_shared_deployment_barrier_until_commit(retained):
    async with importer(retained) as connection:
        await insert(connection, "CONFIG_DASHBOARDS", sample("CONFIG_DASHBOARDS"))
        cursor = await retained.admin.execute(
            "SELECT pg_try_advisory_xact_lock_shared(%s) AS available",
            (METADATA_DEPLOYMENT_LOCK,),
        )
        assert (await cursor.fetchone())["available"] is False
    cursor = await retained.admin.execute(
        "SELECT pg_try_advisory_xact_lock_shared(%s) AS available",
        (METADATA_DEPLOYMENT_LOCK,),
    )
    assert (await cursor.fetchone())["available"] is True


async def test_invalid_import_rolls_back_prior_rows_and_copy_cannot_bypass_rls(retained):
    valid = sample("CONFIG_DASHBOARDS")
    with pytest.raises(psycopg.errors.StringDataRightTruncation):
        async with importer(retained) as connection:
            await insert(connection, "CONFIG_DASHBOARDS", valid)
            await insert(connection, "CONFIG_DASHBOARDS", {**valid, "id": "synthetic-secret" * 8})
    assert await rows(retained, "CONFIG_DASHBOARDS") == []
    with pytest.raises(psycopg.errors.FeatureNotSupported):
        async with (
            importer(retained) as connection,
            connection.cursor() as cursor,
            cursor.copy("COPY nova_meta.config_dashboards (id,name) FROM STDIN") as copy,
        ):
            await copy.write_row(("refused", "Refused"))
    await retained.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false",
        (uuid4(),),
    )
    with pytest.raises(psycopg.errors.FeatureNotSupported):
        async with (
            importer(retained) as connection,
            connection.cursor() as cursor,
            cursor.copy("COPY nova_meta.config_dashboards (id,name) FROM STDIN") as copy,
        ):
            await copy.write_row(("refused", "Refused"))


@pytest.mark.parametrize("table", TABLES)
async def test_explicit_import_reset_is_allowed_only_inside_maintenance(retained, table):
    async with importer(retained) as connection:
        await insert(connection, table, sample(table))
    assert len(await rows(retained, table)) == 1
    async with importer(retained) as connection:
        await connection.execute(
            sql.SQL("TRUNCATE nova_meta.{}").format(
                sql.Identifier(table.lower()),
            )
        )
    assert await rows(retained, table) == []
