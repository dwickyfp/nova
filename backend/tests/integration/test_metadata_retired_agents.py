"""Retain retired Agent rows without restoring runtime access or delegation."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.types.json import set_json_loads

from app.core.metadata_bootstrap import WORKLOAD_ROLES
from app.core.metadata_db import METADATA_DEPLOYMENT_LOCK, MetadataSchemaMismatch
from app.core.metadata_import import ImportColumn, target_type
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_retained_legacy import (
    SOURCE,
    importer,
    insert,
)

pytestmark = pytest.mark.metadata_pg
TABLES = (
    "CONFIG_AGENT_CAPABILITIES",
    "CONFIG_AGENT_MESSAGES",
    "CONFIG_AGENT_SESSION_EVENTS",
)
JSON_DOCUMENT = (
    '{"precise":12345678901234567890.1234567890123456789,'
    '"ordered":[3,1,3,null,{"text":"界"}],"empty":null,"truth":true}'
)


@pytest_asyncio.fixture
async def history(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="c" * 40)
    return environment


def history_row(table, *, suffix="a", optional_nulls=False):
    values, expected = {}, {}
    for column in SOURCE[table]["columns"]:
        name, kind, nullable = column["name"], column["type"], column["nullable"] == "YES"
        if optional_nulls and nullable:
            raw = None
        elif kind == "json":
            raw = JSON_DOCUMENT
        elif kind == "datetime":
            raw = datetime(2025, 2, 1, 7, 30)
        elif kind == "bigint(20)":
            raw = -(2**63)
        else:
            raw = "value-界-" + suffix
        values[name] = ImportColumn(
            name, kind, nullable, "UTC" if kind == "datetime" else None
        ).convert(raw)
        expected[name] = (
            json.loads(raw, parse_float=Decimal)
            if kind == "json" and raw is not None
            else values[name]
        )
    return values, expected


async def records(env, table):
    async with importer(env) as connection:
        set_json_loads(lambda data: json.loads(data, parse_float=Decimal), connection)
        cursor = await connection.execute(
            sql.SQL("SELECT * FROM nova_meta.{}").format(sql.Identifier(table.lower()))
        )
        return [
            dict(zip([d.name for d in cursor.description], row, strict=True))
            for row in await cursor.fetchall()
        ]


@pytest.mark.parametrize("table", TABLES)
async def test_historical_columns_owners_ids_json_precision_and_order_survive(history, table):
    cursor = await history.admin.execute(
        "SELECT column_name,data_type,character_maximum_length,is_nullable "
        "FROM information_schema.columns WHERE table_schema='nova_meta' AND table_name=%s",
        (table.lower(),),
    )
    actual = {row["column_name"]: row for row in await cursor.fetchall()}
    assert set(actual) == {column["name"] for column in SOURCE[table]["columns"]}
    for column in SOURCE[table]["columns"]:
        target = target_type(column["type"])
        value = actual[column["name"]]
        assert value["is_nullable"] == column["nullable"]
        if target.startswith("varchar("):
            assert value["data_type"] == "character varying"
            assert value["character_maximum_length"] == int(target[8:-1])
        else:
            assert value["data_type"] == (
                "timestamp with time zone" if target == "timestamptz" else target
            )
    first, first_expected = history_row(table)
    second, second_expected = history_row(table, suffix="b", optional_nulls=True)
    async with importer(history) as connection:
        await insert(connection, table, first)
        await insert(connection, table, second)
    result = await records(history, table)
    assert len(result) == 2 and first_expected in result and second_expected in result


@pytest.mark.parametrize("table", TABLES)
async def test_import_does_not_overwrite_an_existing_historical_identity(history, table):
    values, expected = history_row(table)
    async with importer(history) as connection:
        await insert(connection, table, values)
    with pytest.raises(psycopg.errors.UniqueViolation):
        async with importer(history) as connection:
            await insert(connection, table, values)
    assert await records(history, table) == [expected]


@pytest.mark.parametrize("role", (*WORKLOAD_ROLES, "nova_meta_readonly", "nova_meta_backup"))
async def test_historical_owner_context_does_not_grant_any_runtime_sql_access(history, role):
    async with await psycopg.AsyncConnection.connect(
        history.role_config(role).dsn, autocommit=True
    ) as connection:
        await connection.execute(
            "SELECT set_config('nova.principal','value-界-a',false), "
            "set_config('nova.active_role','ACCOUNTADMIN',false), "
            "set_config('nova.security_context_version','1',false), "
            "set_config('nova.principal_type','USER',false)"
        )
        for table in TABLES:
            for statement in (
                "SELECT * FROM nova_meta.{}",
                "INSERT INTO nova_meta.{} DEFAULT VALUES",
                "UPDATE nova_meta.{} SET {key}={key}",
                "DELETE FROM nova_meta.{}",
                "TRUNCATE nova_meta.{}",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    await connection.execute(
                        sql.SQL(statement).format(
                            sql.Identifier(table.lower()),
                            key=sql.Identifier(SOURCE[table]["columns"][0]["name"]),
                        )
                    )


@pytest.mark.parametrize("table", TABLES)
async def test_historical_import_and_readback_require_maintenance(history, table):
    values, _ = history_row(table)
    async with importer(history) as connection:
        await insert(connection, table, values)
    await history.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (uuid4(),)
    )
    assert await records(history, table) == []
    for statement in (
        "INSERT INTO nova_meta.{} DEFAULT VALUES",
        "UPDATE nova_meta.{} SET {key}={key}",
        "DELETE FROM nova_meta.{}",
        "TRUNCATE nova_meta.{}",
    ):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            async with importer(history) as connection:
                await connection.execute(
                    sql.SQL(statement).format(
                        sql.Identifier(table.lower()),
                        key=sql.Identifier(SOURCE[table]["columns"][0]["name"]),
                    )
                )


@pytest.mark.parametrize(
    "table,column",
    [
        ("CONFIG_AGENT_CAPABILITIES", "manifest"),
        ("CONFIG_AGENT_SESSION_EVENTS", "payload"),
    ],
)
async def test_json_null_is_preserved_separately_from_sql_null(history, table, column):
    values, _ = history_row(table)
    values[column] = ImportColumn(column, "json", True).convert("null")
    async with importer(history) as connection:
        await insert(connection, table, values)
        cursor = await connection.execute(
            sql.SQL("SELECT {column} IS NULL,jsonb_typeof({column}) FROM nova_meta.{}").format(
                sql.Identifier(table.lower()), column=sql.Identifier(column)
            )
        )
        assert await cursor.fetchone() == (False, "null")
    second, _ = history_row(table, suffix="b")
    second[column] = None
    if table == "CONFIG_AGENT_CAPABILITIES":
        with pytest.raises(psycopg.errors.NotNullViolation):
            async with importer(history) as connection:
                await insert(connection, table, second)
    else:
        async with importer(history) as connection:
            await insert(connection, table, second)
            cursor = await connection.execute(
                "SELECT payload IS NULL,jsonb_typeof(payload) "
                "FROM nova_meta.config_agent_session_events WHERE event_id=%s",
                (second["event_id"],),
            )
            assert await cursor.fetchone() == (True, None)


async def test_invalid_history_row_rolls_back_the_whole_import_transaction(history):
    capability, _ = history_row("CONFIG_AGENT_CAPABILITIES")
    message, _ = history_row("CONFIG_AGENT_MESSAGES")
    with pytest.raises(psycopg.errors.NotNullViolation):
        async with importer(history) as connection:
            await insert(connection, "CONFIG_AGENT_CAPABILITIES", capability)
            await insert(connection, "CONFIG_AGENT_MESSAGES", {**message, "content": None})
    assert await records(history, "CONFIG_AGENT_CAPABILITIES") == []
    assert await records(history, "CONFIG_AGENT_MESSAGES") == []


async def test_historical_import_holds_the_deployment_barrier(history):
    async with importer(history) as connection:
        for table in TABLES:
            values, _ = history_row(table)
            await insert(connection, table, values)
        cursor = await history.admin.execute(
            "SELECT pg_try_advisory_xact_lock_shared(%s) AS available", (METADATA_DEPLOYMENT_LOCK,)
        )
        assert (await cursor.fetchone())["available"] is False
    cursor = await history.admin.execute(
        "SELECT pg_try_advisory_xact_lock_shared(%s) AS available", (METADATA_DEPLOYMENT_LOCK,)
    )
    assert (await cursor.fetchone())["available"] is True


@pytest.mark.parametrize("table", TABLES)
async def test_explicit_history_import_reset_remains_available_in_maintenance(history, table):
    values, _ = history_row(table)
    async with importer(history) as connection:
        await insert(connection, table, values)
    assert len(await records(history, table)) == 1
    async with importer(history) as connection:
        await connection.execute(
            sql.SQL("TRUNCATE nova_meta.{}").format(sql.Identifier(table.lower()))
        )
    assert await records(history, table) == []


async def test_revision_ten_upgrade_preserves_rows_ledger_and_existing_epoch(environment, tmp_path):  # noqa: F811
    await environment.bootstrap()
    for migration in load_migrations()[:10]:
        (tmp_path / migration.name).write_text(migration.statement)
    configuration = environment.role_config("nova_meta_migrator")
    await migrate(configuration, app_version="a" * 40, directory=tmp_path)
    async with importer(environment) as connection:
        await insert(connection, "CONFIG_DASHBOARDS", {"id": "original", "name": "Original 界"})
    epoch = uuid4()
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,)
    )
    with pytest.raises(MetadataSchemaMismatch):
        await migrate(configuration, app_version="b" * 40)
    await environment.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
    result = await migrate(configuration, app_version="b" * 40)
    assert result.applied_versions == tuple(m.version for m in load_migrations()[10:])
    cursor = await environment.admin.execute(
        "SELECT name FROM nova_meta.config_dashboards WHERE id='original'"
    )
    assert (await cursor.fetchone())["name"] == "Original 界"
    cursor = await environment.admin.execute(
        "SELECT metadata_epoch,maintenance FROM nova_meta.deployment_state"
    )
    state = await cursor.fetchone()
    assert state["metadata_epoch"] == epoch and state["maintenance"]
    cursor = await environment.admin.execute(
        "SELECT version,checksum FROM nova_meta.schema_migrations ORDER BY version"
    )
    assert [(r["version"], r["checksum"]) for r in await cursor.fetchall()] == [
        (m.version, m.checksum) for m in load_migrations()
    ]
    assert (await migrate(configuration, app_version="b" * 40)).applied_versions == ()
