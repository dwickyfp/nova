"""CREATE STREAM: owner privilege for a new source, CREATE in the stream's database, quotas."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.streams import privileges
from app.modules.streams import service as service_module
from app.modules.streams import sources as sources_module
from app.modules.streams.codec import Column
from app.modules.streams.namespace import StreamName
from app.modules.streams.schemas import StreamError, StreamMode
from app.modules.streams.service import StreamCaller, StreamService
from app.modules.streams.sources import READY, SourceRecord, SourceRegistry
from app.sql_frontend.read_only import is_read_only_statement, statement_effects
from tests.unit.stream_metadata_fakes import metadata_scope

DENIED = (
    "SQL error: (5204) Access denied; you need (at least one of) the ALTER privilege(s) "
    "on TABLE orders for this operation."
)
CREATE_DENIED = (
    "SQL error: (5204) Access denied; you need (at least one of) the CREATE TABLE "
    "privilege(s) on DATABASE shop for this operation."
)
NO_ROLLUP = (
    "SQL error: (1064) Materialized view [__nova_stream_check_x] does not exist in table [orders]"
)
NO_REPLICAS = (
    "SQL error: (1064) Getting analyzing error. Detail message: "
    "Replication num should larger than 0."
)

SOURCE = SourceRecord(
    "a" * 32, 1, "shop", "orders", 42, ("id",), (Column("id", "bigint"),), "f" * 64,
    "__nova_stream_changes_aaaaaaaaaaaaaaaa", "__nova_stream_lineage_aaaaaaaaaaaaaaaa", READY,
)


def _caller(answers: dict[str, str | None], seen: list[str] | None = None) -> StreamCaller:
    async def engine(sql: str) -> str | None:
        if seen is not None:
            seen.append(sql)
        for prefix, answer in answers.items():
            if sql.startswith(prefix):
                return answer
        raise AssertionError(f"unexpected probe {sql}")

    return StreamCaller("bob", "analyst", 1, engine)


OWNER = {"ALTER TABLE": NO_ROLLUP, "CREATE TABLE": NO_REPLICAS}


# ── engine proofs ─────────────────────────────────────────────────────────


async def test_owner_proof_runs_a_harmless_alter_as_the_caller():
    seen: list[str] = []
    await privileges.require_owner_privilege(_caller(OWNER, seen), "shop", "orders")
    sql, = seen
    assert sql.startswith("ALTER TABLE `shop`.`orders` DROP ROLLUP `__nova_stream_check_")


async def test_select_only_caller_cannot_start_capture():
    with pytest.raises(StreamError) as exc:
        await privileges.require_owner_privilege(_caller({"ALTER": DENIED}), "shop", "orders")
    assert exc.value.code == "STREAM_ACCESS_DENIED"
    assert "ALTER on shop.orders" in str(exc.value)


@pytest.mark.parametrize("answer", [
    None,  # the statement unexpectedly succeeded
    "Connection error: lost connection",
    "SQL error: (1064) Getting analyzing error. Detail message: Unknown table orders",
])
async def test_inconclusive_owner_proof_refuses(answer):
    with pytest.raises(StreamError) as exc:
        await privileges.require_owner_privilege(_caller({"ALTER": answer}), "shop", "orders")
    assert exc.value.code == "STREAM_ACCESS_DENIED"
    assert "could not be verified" in str(exc.value)


async def test_no_engine_session_refuses():
    with pytest.raises(StreamError) as exc:
        await privileges.require_owner_privilege(StreamCaller("bob", "analyst"), "shop", "t")
    assert exc.value.code == "STREAM_ACCESS_DENIED"
    with pytest.raises(StreamError):
        await privileges.require_create_privilege(StreamCaller("bob", "analyst"), "shop")


async def test_create_proof_uses_zero_replicas_and_names_the_database():
    seen: list[str] = []
    await privileges.require_create_privilege(_caller(OWNER, seen), "shop")
    sql, = seen
    assert sql.startswith("CREATE TABLE `shop`.`__nova_stream_check_")
    assert "'replication_num' = '0'" in sql
    with pytest.raises(StreamError) as exc:
        await privileges.require_create_privilege(_caller({"CREATE": CREATE_DENIED}), "shop")
    assert exc.value.code == "STREAM_ACCESS_DENIED"
    assert "CREATE TABLE privilege" in str(exc.value)


async def test_create_proof_that_created_a_table_drops_it_and_refuses(monkeypatch):
    from app.core.database import db

    execute = AsyncMock()
    monkeypatch.setattr(db, "execute_system", execute)
    with pytest.raises(StreamError) as exc:
        await privileges.require_create_privilege(_caller({"CREATE": None}), "shop")
    assert "could not be verified" in str(exc.value)
    dropped = execute.await_args.args[0]
    assert dropped.startswith("DROP TABLE IF EXISTS `shop`.`__nova_stream_check_")


# ── registration asks for ownership only for a new source ─────────────────


def _registry(current):
    scope, repository = metadata_scope()
    repository.current_generations = AsyncMock(return_value=[])
    registry = SourceRegistry(metadata=scope)
    registry.engine_identity = AsyncMock(return_value=(42, "PRIMARY_KEYS", "`id`"))
    registry.active_for_table = AsyncMock(return_value=current)
    registry.columns = AsyncMock(return_value=SOURCE.columns)
    registry.partition_versions = AsyncMock(return_value=[3])
    return registry


async def test_registered_source_does_not_ask_for_ownership():
    admit = AsyncMock()
    assert await _registry(SOURCE).register("shop", "orders", "bob", admit=admit) == SOURCE
    admit.assert_not_awaited()


async def test_new_source_is_admitted_before_any_change(monkeypatch):
    registry = _registry(None)
    governance = AsyncMock()
    monkeypatch.setattr(sources_module, "require_governance_supported", governance)
    registry._execute = AsyncMock()
    admit = AsyncMock(side_effect=StreamError("STREAM_ACCESS_DENIED", "no"))
    with pytest.raises(StreamError):
        await registry.register("shop", "orders", "bob", admit=admit)
    admit.assert_awaited_once()
    governance.assert_not_awaited()
    registry._execute.assert_not_awaited()


async def test_recreated_table_is_a_new_source_and_asks_again():
    registry = _registry(replace(SOURCE, table_id=7))
    admit = AsyncMock(side_effect=StreamError("STREAM_ACCESS_DENIED", "no"))
    with pytest.raises(StreamError):
        await registry.register("shop", "orders", "bob", admit=admit)
    admit.assert_awaited_once()


# ── the service: order of checks and quotas ───────────────────────────────


def _stream_row(name, source_id, role="analyst", stream_id=None):
    return {
        "name": name, "database_name": "shop", "source_id": source_id, "owner_role": role,
        "stream_id": stream_id or f"id-{name}", "status": READY,
    }


@pytest.fixture
def service(monkeypatch):
    svc = StreamService()
    svc.repository = SimpleNamespace(
        current=AsyncMock(return_value=None),
        current_generations=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(svc, "_table_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(svc, "authorize_read", AsyncMock())
    registry = SimpleNamespace(
        active_for_table=AsyncMock(return_value=None),
        register=AsyncMock(side_effect=RuntimeError("registered")),
    )
    monkeypatch.setattr(service_module, "source_registry", registry)
    svc.registry = registry
    return svc


async def _create(svc, caller):
    await svc.create(StreamName("shop", "s"), StreamName("shop", "orders"),
                     StreamMode.STANDARD, caller)


async def test_create_needs_create_privilege_in_the_stream_database(service):
    with pytest.raises(StreamError) as exc:
        await _create(service, _caller({"CREATE": CREATE_DENIED}))
    assert exc.value.code == "STREAM_ACCESS_DENIED"
    service.registry.register.assert_not_awaited()


async def test_create_passes_the_ownership_check_to_registration(service):
    seen: list[str] = []
    with pytest.raises(RuntimeError, match="registered"):
        await _create(service, _caller(OWNER, seen))
    admit = service.registry.register.await_args.kwargs["admit"]
    await admit()
    assert seen[-1].startswith("ALTER TABLE `shop`.`orders` DROP ROLLUP")


async def test_stream_quota(monkeypatch, service):
    monkeypatch.setattr(settings, "STREAM_MAX_STREAMS_PER_ROLE", 2)
    service.repository.current_generations.return_value = [
        _stream_row("a", "s1"), _stream_row("b", "s1"), _stream_row("c", "s2", role="other"),
    ]
    with pytest.raises(StreamError) as exc:
        await _create(service, _caller(OWNER))
    assert exc.value.code == "STREAM_QUOTA_EXCEEDED"
    assert "2 streams" in str(exc.value)
    service.registry.register.assert_not_awaited()


async def test_source_quota_counts_distinct_tables(monkeypatch, service):
    monkeypatch.setattr(settings, "STREAM_MAX_SOURCES_PER_ROLE", 2)
    service.repository.current_generations.return_value = [
        _stream_row("a", "s1"), _stream_row("b", "s2"), _stream_row("c", "s2"),
    ]
    with pytest.raises(StreamError) as exc:
        await _create(service, _caller(OWNER))
    assert exc.value.code == "STREAM_QUOTA_EXCEEDED"
    assert "2 tables" in str(exc.value)
    # Another stream on a table the role already captures is within the quota.
    service.registry.active_for_table.return_value = SimpleNamespace(
        status=READY, source_id="s2"
    )
    with pytest.raises(RuntimeError, match="registered"):
        await _create(service, _caller(OWNER))


async def test_replacing_own_stream_is_not_counted_twice(monkeypatch, service):
    monkeypatch.setattr(settings, "STREAM_MAX_STREAMS_PER_ROLE", 1)
    existing = SimpleNamespace(
        status=READY, owner_role="analyst", stream_id="id-s", source=StreamName("shop", "o"),
        generation=0, source_id="s1",
    )
    service.repository.current.return_value = existing
    service.repository.current_generations.return_value = [_stream_row("s", "s1")]
    with pytest.raises(RuntimeError, match="registered"):
        await service.create(StreamName("shop", "s"), StreamName("shop", "orders"),
                             StreamMode.STANDARD, _caller(OWNER), or_replace=True)


# ── effects: CREATE STREAM is not a metadata-only statement ───────────────


def test_create_stream_declares_schema_and_security_effects():
    effects = statement_effects("CREATE STREAM s ON TABLE orders")
    assert effects is not None
    assert effects.changes_schema and effects.changes_security and effects.writes_metadata
    assert not is_read_only_statement("CREATE STREAM s ON TABLE orders")
    assert not is_read_only_statement("CREATE OR REPLACE STREAM s ON TABLE orders")
    assert is_read_only_statement("SHOW STREAMS")
    dropped = statement_effects("DROP STREAM s")
    assert dropped is not None and dropped.drops_objects and not dropped.changes_security
