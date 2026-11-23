"""Stream journals stay with the writer: Ranger guard, SQL refusal, release on drop."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.streams import sources as sources_module
from app.modules.streams.codec import Column
from app.modules.streams.schemas import StreamError
from app.modules.streams.sources import (
    DROPPED,
    JOURNAL_GUARD_POLICY,
    READY,
    SourceRecord,
    SourceRegistry,
    ensure_journal_guard,
    journal_guard_policy,
)
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.internal_names import names_internal_relation
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner
from tests.unit.stream_metadata_fakes import metadata_scope


def _scope(live=None):
    scope, repository = metadata_scope()
    repository.current_generations = AsyncMock(return_value=live or [])
    return scope


SOURCE = SourceRecord(
    "a" * 32, 1, "shop", "orders", 42, ("id",), (Column("id", "bigint"),), "f" * 64,
    "__nova_stream_changes_aaaaaaaaaaaaaaaa", "__nova_stream_lineage_aaaaaaaaaaaaaaaa", READY,
)


# ── user SQL never names a journal ────────────────────────────────────────


@pytest.mark.parametrize("sql", [
    "SELECT * FROM shop.__nova_stream_changes_aaaaaaaaaaaaaaaa",
    "SELECT * FROM `shop`.`__nova_stream_changes_aaaaaaaaaaaaaaaa`",
    "SELECT * FROM __NOVA_STREAM_LINEAGE_x",
    "SELECT before_image FROM default_catalog.shop.__nova_stream_changes_x",
    "SELECT * FROM orders o JOIN shop.__nova_stream_changes_x j ON o.id = j.commit_seq",
    "WITH c AS (SELECT * FROM __nova_stream_changes_x) SELECT * FROM c",
    "SELECT * FROM orders WHERE id IN (SELECT commit_seq FROM __nova_stream_changes_x)",
    "INSERT INTO shop.__nova_stream_changes_x VALUES (1)",
    "DELETE FROM shop.__nova_stream_changes_x WHERE commit_seq > 0",
    "TRUNCATE TABLE shop.__nova_stream_lineage_x",
    "DROP TABLE shop.__nova_stream_changes_x",
    "SHOW CREATE TABLE shop.__nova_stream_changes_x",
    "DESC shop.__nova_stream_changes_x",
    "CREATE TABLE copy AS SELECT * FROM shop.__nova_stream_changes_x",
    "CREATE VIEW v AS SELECT * FROM shop.__nova_stream_changes_x",
    "EXPLAIN SELECT * FROM shop.__nova_stream_changes_x",
])
async def test_sql_naming_a_journal_is_refused_before_the_engine(sql):
    parsed = parse_statement(sql)
    assert names_internal_relation(parsed)
    with pytest.raises(SemanticError, match="managed by Nova Streams"):
        await SQLPlanner().plan(ast_builders.build(parsed), PlanningContext(database="shop"))


@pytest.mark.parametrize("sql", [
    "SELECT * FROM shop.orders",
    "SELECT '__nova_stream_changes_x' AS label",
    "SELECT * FROM __nova_streamline",
    "SELECT * FROM information_schema.tables WHERE table_name LIKE 'orders%'",
])
def test_other_names_and_string_literals_are_not_refused(sql):
    assert not names_internal_relation(parse_statement(sql))


# ── the Ranger guard ──────────────────────────────────────────────────────


@pytest.fixture
def ranger(monkeypatch):
    monkeypatch.setattr(settings, "RANGER_PASSWORD", "secret")
    monkeypatch.setattr(settings, "STREAM_WRITER_ROLE", "nova_stream_writer_role")
    from app.integrations.ranger.client import ranger_client

    fake = AsyncMock()
    fake.get_policy = AsyncMock(return_value=None)
    fake.list_policies = AsyncMock(return_value=[])
    for name in ("get_policy", "put_policy", "list_policies", "delete_policy"):
        monkeypatch.setattr(ranger_client, name, getattr(fake, name))
    return fake


def test_guard_denies_everyone_but_the_writer_every_journal():
    policy = journal_guard_policy().to_api()
    assert policy["name"] == JOURNAL_GUARD_POLICY
    assert policy["resources"]["database"]["values"] == ["*"]
    assert policy["resources"]["table"]["values"] == ["__nova_stream_*"]
    deny, = policy["denyPolicyItems"]
    assert deny["groups"] == ["public"]
    assert {a["type"] for a in deny["accesses"]} == {"select", "insert", "update", "delete"}
    exception, = policy["denyExceptions"]
    assert set(exception["roles"]) == {settings.STREAM_WRITER_ROLE, "ACCOUNTADMIN"}
    assert not policy.get("policyItems")


async def test_guard_is_created_when_missing(ranger):
    await ensure_journal_guard()
    ranger.put_policy.assert_awaited_once()
    assert ranger.put_policy.await_args.args[0].name == JOURNAL_GUARD_POLICY


async def test_guard_in_place_is_not_rewritten(ranger):
    stored = journal_guard_policy().to_api()
    stored.update(id=7, isEnabled=True, guid="g")
    stored["denyPolicyItems"][0].update(conditions=[], delegateAdmin=False, users=[])
    await ensure_journal_guard([stored])
    ranger.put_policy.assert_not_awaited()


@pytest.mark.parametrize("tamper", [
    lambda p: p.update(denyPolicyItems=[]),
    lambda p: p["denyExceptions"][0]["roles"].append("analyst"),
    lambda p: p.update(isEnabled=False),
    lambda p: p["resources"]["table"].update(values=["__nova_stream_changes_*"]),
    lambda p: p["denyPolicyItems"][0].update(conditions=[{"type": "ip", "values": ["1"]}]),
    lambda p: p.update(policyItems=[{"groups": ["public"], "accesses": [{"type": "select"}]}]),
])
async def test_tampered_guard_is_repaired(ranger, tamper):
    stored = journal_guard_policy().to_api()
    tamper(stored)
    await ensure_journal_guard([stored])
    ranger.put_policy.assert_awaited_once()


async def test_no_guard_without_ranger_or_writer_role(monkeypatch, ranger):
    monkeypatch.setattr(settings, "RANGER_PASSWORD", "")
    with pytest.raises(StreamError) as exc:
        await ensure_journal_guard()
    assert exc.value.code == "STREAM_GOVERNANCE_UNSUPPORTED"
    monkeypatch.setattr(settings, "RANGER_PASSWORD", "secret")
    monkeypatch.setattr(settings, "STREAM_WRITER_ROLE", "")
    with pytest.raises(StreamError) as exc:
        await ensure_journal_guard()
    assert exc.value.code == "STREAM_CHANGE_TRACKING_UNAVAILABLE"
    ranger.put_policy.assert_not_awaited()


async def test_failed_guard_write_is_an_error(ranger):
    ranger.put_policy.side_effect = RuntimeError("ranger down")
    with pytest.raises(StreamError) as exc:
        await ensure_journal_guard()
    assert exc.value.code == "STREAM_GOVERNANCE_UNSUPPORTED"


async def test_register_protects_journals_before_creating_them(monkeypatch, ranger):
    calls: list[str] = []
    registry = SourceRegistry(metadata=_scope())
    monkeypatch.setattr(registry, "engine_identity",
                        AsyncMock(return_value=(42, "PRIMARY_KEYS", "`id`")))
    monkeypatch.setattr(registry, "active_for_table", AsyncMock(return_value=None))
    monkeypatch.setattr(registry, "columns", AsyncMock(return_value=SOURCE.columns))
    monkeypatch.setattr(registry, "partition_versions", AsyncMock(return_value=[3]))
    monkeypatch.setattr(sources_module, "require_governance_supported", AsyncMock())

    async def guard(*_args):
        calls.append("guard")

    async def execute(sql, params=None):
        calls.append("ddl")
        raise RuntimeError("stop after the first DDL")

    monkeypatch.setattr(sources_module, "ensure_journal_guard", guard)
    monkeypatch.setattr(registry, "_execute", execute)
    with pytest.raises(RuntimeError, match="stop"):
        await registry.register("shop", "orders", "alice")
    assert calls == ["guard", "ddl"]


async def test_failed_guard_stops_registration(monkeypatch, ranger):
    registry = SourceRegistry(metadata=_scope())
    monkeypatch.setattr(registry, "engine_identity",
                        AsyncMock(return_value=(42, "PRIMARY_KEYS", "`id`")))
    monkeypatch.setattr(registry, "active_for_table", AsyncMock(return_value=None))
    monkeypatch.setattr(registry, "columns", AsyncMock(return_value=SOURCE.columns))
    monkeypatch.setattr(registry, "partition_versions", AsyncMock(return_value=[3]))
    monkeypatch.setattr(sources_module, "require_governance_supported", AsyncMock())
    execute = AsyncMock()
    monkeypatch.setattr(registry, "_execute", execute)
    ranger.put_policy.side_effect = RuntimeError("ranger down")
    with pytest.raises(StreamError):
        await registry.register("shop", "orders", "alice")
    execute.assert_not_awaited()


# ── release of a dropped source ───────────────────────────────────────────


async def test_release_drops_journal_tables_and_the_writer_policy(monkeypatch, ranger):
    registry = SourceRegistry(metadata=_scope())
    execute = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(registry, "_execute", execute)
    ranger.get_policy = AsyncMock(return_value={"id": 11})
    from app.integrations.ranger.client import ranger_client

    monkeypatch.setattr(ranger_client, "get_policy", ranger.get_policy)
    await registry.release(replace(SOURCE, status=DROPPED))
    statements = [call.args[0] for call in execute.await_args_list]
    assert statements == [
        "DROP TABLE IF EXISTS `shop`.`__nova_stream_changes_aaaaaaaaaaaaaaaa`",
        "DROP TABLE IF EXISTS `shop`.`__nova_stream_lineage_aaaaaaaaaaaaaaaa`",
    ]
    ranger.get_policy.assert_awaited_once_with(f"nova-managed/stream-writer/{'a' * 32}")
    ranger.delete_policy.assert_awaited_once_with(11)


async def test_release_refuses_a_live_or_unexpected_source(monkeypatch, ranger):
    registry = SourceRegistry(metadata=_scope())
    execute = AsyncMock()
    monkeypatch.setattr(registry, "_execute", execute)
    with pytest.raises(StreamError):
        await registry.release(SOURCE)
    with pytest.raises(StreamError):
        await registry.release(replace(SOURCE, status=DROPPED, journal_table="orders"))
    execute.assert_not_awaited()


async def test_last_stream_dropped_releases_the_source(monkeypatch, ranger):
    registry = SourceRegistry(metadata=_scope())
    dropped = replace(SOURCE, status=DROPPED)
    monkeypatch.setattr(registry, "set_status", AsyncMock())
    monkeypatch.setattr(registry, "get", AsyncMock(return_value=dropped))
    release = AsyncMock()
    monkeypatch.setattr(registry, "release", release)
    await registry.release_if_unused(SOURCE)
    release.assert_awaited_once_with(dropped)


async def test_source_still_read_is_not_released(monkeypatch, ranger):
    registry = SourceRegistry(metadata=_scope())
    monkeypatch.setattr(registry, "set_status", AsyncMock())
    monkeypatch.setattr(registry, "get", AsyncMock(return_value=SOURCE))
    release = AsyncMock()
    monkeypatch.setattr(registry, "release", release)
    await registry.release_if_unused(SOURCE)  # status did not move to DROPPED
    release.assert_not_awaited()


async def test_maintenance_backfills_guard_and_releases_leftovers(monkeypatch, ranger):
    registry = SourceRegistry(metadata=_scope())
    left = replace(SOURCE, status=DROPPED)
    policy_only = replace(
        SOURCE, source_id="b" * 32, status=DROPPED,
        journal_table="__nova_stream_changes_b", lineage_table="__nova_stream_lineage_b",
    )
    released = replace(
        SOURCE, source_id="c" * 32, status=DROPPED,
        journal_table="__nova_stream_changes_c", lineage_table="__nova_stream_lineage_c",
    )
    monkeypatch.setattr(registry, "sources", AsyncMock(return_value=[left, policy_only, released]))
    ranger.list_policies.return_value = [{"name": f"nova-managed/stream-writer/{'b' * 32}"}]
    monkeypatch.setattr(registry, "_execute", AsyncMock(return_value={
        "rows": [("shop", left.journal_table)],
    }))
    release = AsyncMock()
    monkeypatch.setattr(registry, "release", release)
    guard = AsyncMock()
    monkeypatch.setattr(sources_module, "ensure_journal_guard", guard)
    outcome = await registry.maintain_guards(has_sources=True)
    assert outcome == {"released": 2}
    assert [call.args[0] for call in release.await_args_list] == [left, policy_only]
    guard.assert_awaited_once()


async def test_maintenance_does_nothing_without_ranger(monkeypatch, ranger):
    monkeypatch.setattr(settings, "RANGER_PASSWORD", "")
    registry = SourceRegistry(metadata=_scope())
    sources = AsyncMock()
    monkeypatch.setattr(registry, "sources", sources)
    assert await registry.maintain_guards(has_sources=True) == {"released": 0}
    sources.assert_not_awaited()
