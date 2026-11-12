"""Stream control metadata on the real PG fixture: claims, facts, receipts, chains."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb

from app.core.metadata_bootstrap import WORKLOAD_ROLES
from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
    MetadataReadOnlyMaintenance,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.streams.metadata_repository import (
    NAMESPACE_FIELDS,
    StreamMetadataRepository,
    claim_key,
    event_id,
)
from app.modules.streams.schemas import StreamError, operation_id
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg
SQL_PATHS = {"web", "query", "proxy", "agent", "task", "notebook", "automation"}
READERS = SQL_PATHS | {"scheduler"}
COMMITTED = {"outcome": "COMMITTED", "reason": "verified", "load_id": 7, "sink_rows": 3,
             "transaction_id": 11}


def digest(*parts) -> str:
    return hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()


class Streams:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, workload="web", *, name="stream-owner", read_only=False):
        user = session(name, active="ANALYST")
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            actor = SecurityContext.from_session(user)
            async with db.transaction(actor, read_only=read_only) as conn:
                yield StreamMetadataRepository(conn, actor), conn
        finally:
            await db.close()

    async def admin(self, query, params=()):
        cursor = await self.environment.admin.execute(query, params)
        return await cursor.fetchall() if cursor.description else None

    @asynccontextmanager
    async def importer(self):
        """The migrator acting as the owner, the only import path."""
        dsn = self.environment.role_config("nova_meta_migrator").dsn
        async with (
            await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn,
            conn.transaction(),
        ):
            await conn.execute("SET LOCAL ROLE nova_meta_owner")
            yield conn


@pytest_asyncio.fixture
async def streams(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="d" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return Streams(environment, epoch)


# ── builders ─────────────────────────────────────────────────────────────


def source_values(table="orders", database="sales"):
    source_id = uuid4().hex
    return {
        "source_id": source_id, "epoch": 1, "database_name": database, "table_name": table,
        "table_id": 42, "key_columns": ["id"],
        "columns_json": [{"name": "id", "type": "bigint"}, {"name": "v", "type": "int"}],
        "schema_fingerprint": digest("fingerprint", source_id),
        "journal_table": f"__nova_stream_changes_{source_id[:16]}",
        "lineage_table": f"__nova_stream_lineage_{source_id[:16]}",
    }


def commit(version_before, version_after=None, rows=1, commit_id=None):
    return {"commit_id": commit_id or uuid4().hex, "version_before": version_before,
            "version_after": version_before + 1 if version_after is None else version_after,
            "row_count": rows, "principal": "stream-owner", "active_role": "ANALYST"}


BASELINE = {"commit_id": "baseline", "version_before": 5, "version_after": 5, "row_count": 0,
            "principal": "stream-owner", "active_role": None}


async def ready_source(streams, *, commits=3, table="orders"):
    """A READY source with ``commits`` captured commits after the baseline."""
    async with streams.transaction() as (repo, _):
        source = await repo.register_source(source_values(table))
        await repo.record_commit(source["source_id"], 0, BASELINE)
        version = 5
        for sequence in range(1, commits + 1):
            await repo.record_commit(source["source_id"], sequence, commit(version))
            version += 1
        return await repo.set_source_status(source["source_id"], "READY", None,
                                            expected_revision=1)


def namespace(source, *, generation=0, status="READY", stream_id=None, name="orders_changes",
              cursor=0):
    return {"catalog_name": "default_catalog", "database_name": "sales",
            "schema_name": "default", "name": name, "generation": generation,
            "stream_id": stream_id or digest("stream", name), "source_database": "sales",
            "source_name": source["table_name"], "source_id": source["source_id"],
            "owner_role": "ANALYST", "cursor_epoch": source["epoch"], "cursor_sequence": cursor,
            "status": status}


def namespace_digest(record):
    return operation_id(*(record[field] for field in NAMESPACE_FIELDS))


def namespace_claim(record, attempt=None):
    scope = (record[field] for field in NAMESPACE_FIELDS[:4])
    return {"resource_id": operation_id("stream_namespace", *scope),
            "epoch": 0, "generation": record["generation"],
            "attempt_id": attempt or digest("attempt", uuid4()),
            "operation_digest": namespace_digest(record)}


async def publish(repo, record):
    await repo.prepare_namespace_operation(namespace_digest(record), record)
    winner, acquired = await repo.acquire_claim(namespace_claim(record))
    assert acquired, winner
    return await repo.publish_generation(record)


async def stream(streams, source, **changes):
    async with streams.transaction() as (repo, _):
        return await publish(repo, namespace(source, **changes))


def operation(record, start, end, *, attempt=0, target="sales.orders_copy"):
    consume_id = digest("consume", record["stream_id"], start, end, attempt, target)
    return {
        "consume_id": consume_id, "stream_id": record["stream_id"],
        "generation": record["generation"], "source_id": record["source_id"],
        "epoch": record["cursor_epoch"], "sequence_from": start, "sequence_to": end,
        "schema_version": 1, "operation_digest": digest("statement", target),
        "target_database": "sales", "target_object": target.split(".")[1],
        "engine_label": "nova_stream_" + consume_id[:48], "principal": "stream-owner",
        "target_load_id": None, "target_transaction_id": None, "active_role": "ANALYST",
        "security_context_version": 5, "dispatch_attempt": attempt,
        "submission_digest": digest("submit", consume_id),
        "cancellation_digest": digest("cancel", consume_id),
    }


DECISION_DIGESTS = {"submit": "submission_digest", "cancel": "cancellation_digest"}


def dispatch_claim(op, decision="submit"):
    return {"resource_id": digest("dispatch", op["stream_id"], op["sequence_from"]),
            "epoch": op["epoch"], "generation": op["dispatch_attempt"],
            "attempt_id": digest("attempt", uuid4()),
            "operation_digest": op[DECISION_DIGESTS[decision]]}


async def committed(streams, op, receipt=COMMITTED, *, offset=True):
    """Prepare, win the submit claim, and record the verified receipts."""
    async with streams.transaction("task") as (repo, _):
        await repo.prepare_consumption(op)
        _, acquired = await repo.acquire_claim(dispatch_claim(op))
        assert acquired
        await repo.record_event(op, "TARGET_SUBMITTED")
        await repo.record_event(op, "TARGET_COMMITTED", receipt)
        if offset:
            await repo.record_event(op, "OFFSET_COMMITTED", receipt)


# ── claims and namespace generations ─────────────────────────────────────


async def test_a_claim_has_exactly_one_winner(streams):
    source = await ready_source(streams)
    record = namespace(source)
    ready = asyncio.Barrier(5)

    async def claim():
        async with streams.transaction() as (repo, _):
            await ready.wait()
            return await repo.acquire_claim(namespace_claim(record))

    results = await asyncio.gather(*(claim() for _ in range(5)))
    assert sum(acquired for _, acquired in results) == 1
    assert len({winner["attempt_id"] for winner, _ in results}) == 1
    winner = results[0][0]
    assert winner["claim_key"] == claim_key(winner["resource_id"], 0, 0)
    assert winner["recorded_by"] == "stream-owner" and winner["provenance"] == "runtime"
    with pytest.raises(MetadataIntegrityViolation):  # the key must be the derived digest
        async with streams.transaction() as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.audit_stream_claims (claim_key,resource_id,epoch,"
                "generation,attempt_id,operation_digest) VALUES (%s,%s,0,1,%s,%s)",
                ("f" * 64, "a" * 64, "b" * 64, "c" * 64))


async def test_only_the_claim_winner_publishes_its_prepared_facts(streams):
    source = await ready_source(streams)
    record = namespace(source)
    rival = namespace(source, stream_id=digest("rival"))
    async with streams.transaction() as (repo, _):
        await repo.prepare_namespace_operation(namespace_digest(record), record)
        await repo.prepare_namespace_operation(namespace_digest(rival), rival)
        _, acquired = await repo.acquire_claim(namespace_claim(record))
        assert acquired
        _, rival_acquired = await repo.acquire_claim(namespace_claim(rival))
        assert rival_acquired is False
    with pytest.raises(MetadataIntegrityViolation):  # the loser cannot publish
        async with streams.transaction() as (repo, _):
            await repo.publish_generation(rival)
    with pytest.raises(MetadataIntegrityViolation):  # unprepared facts cannot publish
        async with streams.transaction() as (repo, _):
            await repo.publish_generation({**record, "owner_role": "OTHER"})
    async with streams.transaction() as (repo, _):
        published = await repo.publish_generation(record)
        again = await repo.publish_generation(record)  # lost-ACK retry
        assert again == published and published["provenance"] == "runtime"
        assert (await repo.current(("default_catalog", "sales", "default",
                                    "orders_changes")))["stream_id"] == record["stream_id"]


async def test_generations_publish_in_order_and_never_change(streams):
    source = await ready_source(streams)
    first = await stream(streams, source)
    with pytest.raises(MetadataIntegrityViolation):  # generation 2 before 1
        await stream(streams, source, generation=2)
    dropped = await stream(streams, source, generation=1, status="DROPPED")
    assert dropped["status"] == "DROPPED"
    async with streams.transaction() as (repo, _):
        assert (await repo.current(("default_catalog", "sales", "default",
                                    "orders_changes")))["generation"] == 1
        assert await repo.current_generations(statuses=("READY",)) == []
    for statement in ("UPDATE nova_meta.config_streams SET status='READY'",
                      "DELETE FROM nova_meta.config_streams"):
        with pytest.raises(MetadataPermissionDenied):
            async with streams.transaction() as (_, conn):
                await conn.execute(statement)
    audit = await streams.admin(
        "SELECT action,metadata_revision,object_name FROM nova_meta.audit_log "
        "WHERE metadata_table='config_streams' ORDER BY metadata_revision")
    assert [(a["action"], a["metadata_revision"]) for a in audit] == [("CREATE", 1), ("DELETE", 2)]
    assert first["created_at"] is not None


async def test_namespace_operation_facts_are_immutable(streams):
    source = await ready_source(streams)
    record = namespace(source)
    key = namespace_digest(record)
    async with streams.transaction() as (repo, _):
        row = await repo.prepare_namespace_operation(key, record)
        assert await repo.prepare_namespace_operation(key, record) == row
    with pytest.raises(MetadataConflict):
        async with streams.transaction() as (repo, _):
            await repo.prepare_namespace_operation(key, {**record, "cursor_sequence": 2})
    with pytest.raises(MetadataIntegrityViolation):
        async with streams.transaction() as (repo, _):
            await repo.prepare_namespace_operation(digest("x"), {**record, "schema_name": "s"})


# ── definitions, sources and the commit index ───────────────────────────


async def test_definitions_name_a_recorded_source_and_snapshot(streams):
    source = await ready_source(streams, commits=2)
    stream_id = digest("definition")
    async with streams.transaction(name="creator") as (repo, _):
        row = await repo.create_definition(stream_id, "STANDARD", source["source_id"],
                                           show_initial_rows=True, initial_commit_seq=2)
        assert row["created_by"] == "creator"
        assert await repo.create_definition(stream_id, "STANDARD", source["source_id"],
                                            show_initial_rows=True, initial_commit_seq=2) == row
    with pytest.raises(MetadataConflict):
        async with streams.transaction(name="creator") as (repo, _):
            await repo.create_definition(stream_id, "APPEND_ONLY", source["source_id"])
    for bad in ({"stream_id": digest("d2"), "mode": "STANDARD", "source_id": uuid4().hex},
                {"stream_id": digest("d3"), "mode": "STANDARD", "source_id": source["source_id"],
                 "show_initial_rows": True, "initial_commit_seq": 9},
                {"stream_id": digest("d4"), "mode": "DELTA", "source_id": source["source_id"]}):
        with pytest.raises(MetadataIntegrityViolation):
            async with streams.transaction() as (repo, _):
                await repo.create_definition(**bad)
    audit = await streams.admin("SELECT object_name FROM nova_meta.audit_log "
                                "WHERE metadata_table='config_stream_definitions'")
    assert [a["object_name"] for a in audit] == [stream_id]


async def test_the_commit_index_is_one_unbroken_immutable_chain(streams):
    async with streams.transaction() as (repo, _):
        source_id = (await repo.register_source(source_values()))["source_id"]
    with pytest.raises(MetadataIntegrityViolation):  # a baseline does not move the version
        async with streams.transaction() as (repo, _):
            await repo.record_commit(source_id, 0, {**BASELINE, "version_after": 6})
    async with streams.transaction() as (repo, _):
        await repo.record_commit(source_id, 0, BASELINE)
        first = commit(5)
        row = await repo.record_commit(source_id, 1, first)
        assert await repo.record_commit(source_id, 1, first) == row  # identical retry
        # A snapshot commit leaves the engine version unchanged.
        await repo.record_commit(source_id, 2, commit(6, 6, rows=4))
    for sequence, bad in ((4, commit(6)), (3, commit(7)), (3, commit(6, 8))):
        with pytest.raises(MetadataIntegrityViolation):  # gap, version break, jump
            async with streams.transaction() as (repo, _):
                await repo.record_commit(source_id, sequence, bad)
    with pytest.raises(MetadataConflict):  # a different fact for a recorded sequence
        async with streams.transaction() as (repo, _):
            await repo.record_commit(source_id, 1, {**first, "row_count": 2})
    ready = asyncio.Barrier(2)

    async def capture(fact):
        async with streams.transaction("task") as (repo, _):
            await ready.wait()
            return await repo.record_commit(source_id, 3, fact)

    results = await asyncio.gather(capture(commit(6)), capture(commit(6)),
                                   return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    async with streams.transaction(read_only=True) as (repo, _):
        assert (await repo.head_commit(source_id))["commit_seq"] == 3
        assert [c["commit_seq"] for c in await repo.commits_between(source_id, 0, 3)] == [1, 2, 3]
    with pytest.raises(MetadataPermissionDenied):
        async with streams.transaction() as (_, conn):
            await conn.execute("UPDATE nova_meta.audit_stream_source_commits SET row_count=0")


async def test_source_status_moves_forward_under_a_revision(streams):
    async with streams.transaction(name="registrar") as (repo, _):
        source = await repo.register_source(source_values())
        assert (source["status"], source["metadata_revision"]) == ("REGISTERING", 1)
        assert source["registered_by"] == "registrar"
    source_id = source["source_id"]
    with pytest.raises(MetadataIntegrityViolation):  # READY needs its baseline
        async with streams.transaction() as (repo, _):
            await repo.set_source_status(source_id, "READY", None, expected_revision=1)
    with pytest.raises(MetadataConflict):  # one live registration per table
        async with streams.transaction() as (repo, _):
            await repo.register_source(source_values(table="ORDERS"))
    async with streams.transaction() as (repo, _):
        await repo.record_commit(source_id, 0, BASELINE)
        ready = await repo.set_source_status(source_id, "READY", "ignored", expected_revision=1)
    assert ready["status_reason"] is None and ready["metadata_revision"] == 2
    barrier = asyncio.Barrier(2)

    async def move(status):
        async with streams.transaction("scheduler") as (repo, _):
            await barrier.wait()
            return await repo.set_source_status(source_id, status, "é" * 400,
                                                expected_revision=2)

    results = await asyncio.gather(move("BLOCKED"), move("SOURCE_INVALID"),
                                   return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    winner = next(r for r in results if isinstance(r, dict))
    assert len(winner["status_reason"].encode()) <= 500
    async with streams.transaction() as (repo, _):
        dropped = await repo.set_source_status(source_id, "DROPPED", None, expected_revision=3)
    for status in ("READY", "BLOCKED", "SOURCE_INVALID"):
        with pytest.raises(MetadataIntegrityViolation):  # DROPPED is final
            async with streams.transaction() as (repo, _):
                await repo.set_source_status(source_id, status, None, expected_revision=4)
    with pytest.raises(MetadataIntegrityViolation):
        async with streams.transaction() as (_, conn):
            await conn.execute("UPDATE nova_meta.config_stream_sources SET status_reason='x' "
                               "WHERE source_id=%s", (source_id,))
    assert dropped["metadata_revision"] == 4
    async with streams.transaction() as (repo, _):  # the table can register again
        await repo.register_source(source_values())


@pytest.mark.parametrize("bad", [
    {"source_id": "not-hex"}, {"journal_table": "__nova_stream_changes_x"},
    {"key_columns": []}, {"epoch": 0},
])
async def test_invalid_registrations_are_refused(streams, bad):
    with pytest.raises(MetadataIntegrityViolation):
        async with streams.transaction() as (repo, _):
            await repo.register_source({**source_values(), **bad})


# ── consumption operations and receipts ─────────────────────────────────


async def test_a_consumption_receipt_advances_the_offset(streams):
    source = await ready_source(streams)
    record = await stream(streams, source)
    op = operation(record, 0, 2)
    await committed(streams, op)
    async with streams.transaction("scheduler", read_only=True) as (repo, _):
        assert await repo.committed_cursor(record["stream_id"], 1, 0) == 2
        assert await repo.terminal_receipt(op["consume_id"]) == COMMITTED
        assert (await repo.winning_operation(op["submission_digest"]))["consume_id"] == \
            op["consume_id"]
        events = await repo.recent_events(record["stream_id"], 10)
    assert {e["state"] for e in events} == {"PREPARED", "TARGET_SUBMITTED", "TARGET_COMMITTED",
                                            "OFFSET_COMMITTED"}
    stored = await streams.admin("SELECT event_id FROM nova_meta.audit_stream_consumptions "
                                 "WHERE state='OFFSET_COMMITTED'")
    assert stored[0]["event_id"] == event_id(op["consume_id"], "OFFSET_COMMITTED")
    assert await streams.admin("SELECT 1 FROM nova_meta.audit_log WHERE metadata_table "
                               "LIKE 'audit_stream%%'") == []


async def test_duplicate_stale_and_conflicting_receipts(streams):
    source = await ready_source(streams)
    record = await stream(streams, source)
    op = operation(record, 0, 2)
    await committed(streams, op, offset=False)
    async with streams.transaction("task") as (repo, _):  # duplicate delivery
        await repo.record_event(op, "TARGET_COMMITTED", COMMITTED)
        # Repeated non-terminal transitions are idempotent, receipts or not.
        await repo.record_event(op, "VERIFICATION_REQUIRED")
        await repo.record_event(op, "VERIFICATION_REQUIRED",
                                {"outcome": "VERIFICATION_REQUIRED", "reason": "late"})
    for state, receipt in (
        ("TARGET_COMMITTED", {**COMMITTED, "sink_rows": 4}),   # a different receipt
        ("FAILED", {"outcome": "FAILED", "reason": "cancelled"}),  # after a commit
        ("OFFSET_COMMITTED", {**COMMITTED, "load_id": 8}),
    ):
        with pytest.raises(MetadataConflict):
            async with streams.transaction("task") as (repo, _):
                await repo.record_event(op, state, receipt)
    for state, receipt in (
        ("TARGET_COMMITTED", {"outcome": "FAILED", "reason": "x"}),
        ("TARGET_COMMITTED", {"outcome": "COMMITTED"}),
        ("TARGET_COMMITTED", {**COMMITTED, "load_id": -1}),
        ("TARGET_COMMITTED", {**COMMITTED, "error_message": "secret"}),
        ("CANCELLED", COMMITTED),
    ):
        with pytest.raises(MetadataIntegrityViolation):
            async with streams.transaction("task") as (_, conn):
                await conn.execute(
                    "INSERT INTO nova_meta.audit_stream_consumptions (event_id,consume_id,"
                    "stream_id,epoch,generation,sequence_from,sequence_to,operation_digest,"
                    "target_database,target_object,engine_label,principal,state,receipt) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (event_id(digest("other", state, str(receipt)), state),
                     digest("other", state, str(receipt)), op["stream_id"], 1, 0, 0, 2,
                     op["operation_digest"], "sales", "t", "l", "p", state,
                     Jsonb(receipt)))
    async with streams.transaction("task") as (repo, _):
        await repo.record_event(op, "OFFSET_COMMITTED", COMMITTED)
    for statement in ("UPDATE nova_meta.audit_stream_consumptions SET state='FAILED'",
                      "DELETE FROM nova_meta.audit_stream_consumptions"):
        with pytest.raises(MetadataPermissionDenied):
            async with streams.transaction("task") as (_, conn):
                await conn.execute(statement)


async def test_states_need_the_operation_and_its_winning_claim(streams):
    source = await ready_source(streams)
    record = await stream(streams, source)
    op = operation(record, 0, 2)
    with pytest.raises(MetadataIntegrityViolation):  # no prepared operation
        async with streams.transaction("task") as (repo, _):
            await repo.record_event(op, "PREPARED")
    async with streams.transaction("task") as (repo, _):
        await repo.prepare_consumption(op)
    for state, receipt in (("TARGET_SUBMITTED", None), ("TARGET_COMMITTED", COMMITTED),
                           ("CANCELLED", None)):
        with pytest.raises(MetadataIntegrityViolation):  # no winning claim
            async with streams.transaction("task") as (repo, _):
                await repo.record_event(op, state, receipt)
    with pytest.raises(MetadataIntegrityViolation):  # facts must repeat the operation
        async with streams.transaction("task") as (repo, _):
            await repo.record_event({**op, "sequence_to": 3}, "VERIFICATION_REQUIRED")
    async with streams.transaction("task") as (repo, _):
        _, acquired = await repo.acquire_claim(dispatch_claim(op, "cancel"))
        assert acquired
        await repo.record_event(op, "CANCELLED")
        # The cancel winner holds the slot: submission can never be recorded.
        _, submit = await repo.acquire_claim({**dispatch_claim(op), "attempt_id": "e" * 64})
        assert submit is False
    with pytest.raises(MetadataIntegrityViolation):
        async with streams.transaction("task") as (repo, _):
            await repo.record_event(op, "TARGET_SUBMITTED")
    with pytest.raises(MetadataConflict):  # operation facts are immutable
        async with streams.transaction("task") as (repo, _):
            await repo.prepare_consumption({**op, "dispatch_attempt": 1})
    with pytest.raises(MetadataIntegrityViolation):  # only a published READY generation
        async with streams.transaction("task") as (repo, _):
            await repo.prepare_consumption(operation({**record, "generation": 5}, 0, 3))


async def test_offset_receipts_form_one_unbroken_chain(streams):
    source = await ready_source(streams, commits=8)
    record = await stream(streams, source)
    await committed(streams, operation(record, 0, 2))
    await committed(streams, operation(record, 2, 5))
    with pytest.raises(MetadataIntegrityViolation):  # a gap after 5
        await committed(streams, operation(record, 6, 8))
    for start, end in ((2, 4), (4, 7), (1, 3)):  # same start, overlap
        with pytest.raises(MetadataConflict):
            await committed(streams, operation(record, start, end, attempt=1))
    async with streams.transaction(read_only=True) as (repo, _):
        assert await repo.committed_cursor(record["stream_id"], 1, 0) == 5
        # Commit evidence without an offset receipt does not move the offset.
    await committed(streams, operation(record, 5, 8), offset=False)
    async with streams.transaction(read_only=True) as (repo, _):
        assert await repo.committed_cursor(record["stream_id"], 1, 0) == 5


async def test_concurrent_offset_receipts_have_one_winner(streams):
    source = await ready_source(streams, commits=4)
    record = await stream(streams, source)
    first, second = operation(record, 0, 2), operation(record, 0, 3, attempt=1)
    await committed(streams, first, offset=False)
    await committed(streams, second, offset=False)
    ready = asyncio.Barrier(2)

    async def advance(op):
        async with streams.transaction("task") as (repo, _):
            await ready.wait()
            return await repo.record_event(op, "OFFSET_COMMITTED", COMMITTED)

    results = await asyncio.gather(advance(first), advance(second), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    async with streams.transaction(read_only=True) as (repo, _):
        assert await repo.committed_cursor(record["stream_id"], 1, 0) in {2, 3}


# ── access, maintenance and import ──────────────────────────────────────


@pytest.mark.parametrize("workload", [role.removeprefix("nova_meta_") for role in WORKLOAD_ROLES])
async def test_workload_privileges(streams, workload):
    source = await ready_source(streams)
    if workload not in READERS:
        with pytest.raises(MetadataPermissionDenied):
            async with streams.transaction(workload, read_only=True) as (repo, _):
                await repo.get_source(source["source_id"])
        with pytest.raises(MetadataPermissionDenied):
            async with streams.transaction(workload) as (repo, _):
                await repo.acquire_claim(namespace_claim(namespace(source)))
        return
    async with streams.transaction(workload, read_only=True) as (repo, _):
        assert (await repo.get_source(source["source_id"]))["status"] == "READY"
    # Every reader may publish a generation (maintenance marks streams stale).
    record = await stream(streams, source) if workload == "web" else None
    async with streams.transaction(workload) as (repo, _):
        if record is None:
            await publish(repo, namespace(source, name="by_" + workload))
        updated = await repo.set_source_status(source["source_id"], "BLOCKED", "test",
                                               expected_revision=2)
        assert updated["status"] == "BLOCKED"
    if workload == "scheduler":
        for write in (
            lambda repo: repo.register_source(source_values("other")),
            lambda repo: repo.record_commit(source["source_id"], 4, commit(8)),
            lambda repo: repo.create_definition(digest("s"), "STANDARD", source["source_id"]),
        ):
            with pytest.raises(MetadataPermissionDenied):
                async with streams.transaction(workload) as (repo, _):
                    await write(repo)
    for table in ("audit_stream_claims", "config_stream_sources"):
        with pytest.raises(MetadataPermissionDenied):
            async with streams.transaction(workload) as (_, conn):
                await conn.execute(f"DELETE FROM nova_meta.{table}")


async def test_unbound_actors_read_nothing(streams):
    source = await ready_source(streams)
    async with await psycopg.AsyncConnection.connect(
        streams.environment.role_config("nova_meta_web").dsn, autocommit=True,
    ) as conn:
        cursor = await conn.execute("SELECT count(*) FROM nova_meta.config_stream_sources")
        assert (await cursor.fetchone())[0] == 0
    assert source


async def test_maintenance_blocks_writes_but_not_reads(streams):
    source = await ready_source(streams)
    await streams.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(MetadataReadOnlyMaintenance):
        async with streams.transaction() as (repo, _):
            await repo.set_source_status(source["source_id"], "BLOCKED", None,
                                         expected_revision=2)
    async with streams.transaction(read_only=True) as (repo, _):
        assert (await repo.get_source(source["source_id"]))["status"] == "READY"


async def test_import_keeps_legacy_rows_only_under_maintenance(streams):
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        async with streams.importer() as conn:
            await conn.execute(
                "INSERT INTO nova_meta.audit_stream_claims (claim_key,resource_id,epoch,"
                "generation,attempt_id,operation_digest) VALUES ('k','r',0,0,'a','d')")
    await streams.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    stream_id = digest("legacy-stream")
    async with streams.importer() as conn:
        # Legacy rows today's guards would refuse: a non-hex source id, a READY
        # source without a baseline, a generation gap and overlapping receipts.
        await conn.execute(
            "INSERT INTO nova_meta.config_stream_sources (source_id,epoch,database_name,"
            "table_name,table_id,key_columns,columns_json,schema_fingerprint,journal_table,"
            "lineage_table,status,registered_by,created_at,updated_at) VALUES ('legacy-src',3,"
            "'sales','orders',9,'[\"id\"]','[{\"name\":\"id\",\"type\":\"bigint\"}]','fp',"
            "'j','l','READY','old-admin','2026-10-01','2026-10-02')")
        await conn.execute(
            "INSERT INTO nova_meta.config_streams (catalog_name,database_name,schema_name,name,"
            "generation,stream_id,source_database,source_name,source_id,owner_role,cursor_epoch,"
            "cursor_sequence,status,created_at) VALUES ('default_catalog','sales','default',"
            "'legacy',3,%s,'sales','orders','legacy-src','ANALYST',3,0,'READY','2026-10-01')",
            (stream_id,))
        for event, (start, end) in (("e1", (0, 4)), ("e2", (2, 6))):
            await conn.execute(
                "INSERT INTO nova_meta.audit_stream_consumptions (event_id,consume_id,stream_id,"
                "epoch,generation,sequence_from,sequence_to,operation_digest,target_database,"
                "target_object,engine_label,principal,state,receipt,created_at) VALUES "
                "(%s,%s,%s,3,3,%s,%s,'d','sales','t','l','p','OFFSET_COMMITTED',NULL,"
                "'2026-10-03')", (event, event, stream_id, start, end))
    await streams.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    rows = await streams.admin("SELECT provenance,created_at FROM nova_meta.config_streams")
    assert rows[0]["provenance"] == "imported" and rows[0]["created_at"].year == 2026
    async with streams.transaction() as (repo, _):
        legacy = await repo.get_source("legacy-src")
        assert legacy["registered_by"] == "old-admin" and legacy["key_columns"] == ["id"]
        assert (await repo.current(("default_catalog", "sales", "default", "legacy")))[
            "generation"] == 3
        # Imported evidence is kept, and the read-side chain check still blocks it.
        with pytest.raises(StreamError):
            await repo.committed_cursor(stream_id, 3, 0)
        # A runtime change to an imported row is still a valid, revisioned transition.
        blocked = await repo.set_source_status("legacy-src", "BLOCKED", "reason",
                                               expected_revision=1)
        assert blocked["metadata_revision"] == 2 and blocked["provenance"] == "imported"
    with pytest.raises(MetadataIntegrityViolation):  # runtime must continue in order
        async with streams.transaction() as (repo, _):
            await repo.publish_generation({
                "catalog_name": "default_catalog", "database_name": "sales",
                "schema_name": "default", "name": "legacy", "generation": 5,
                "stream_id": stream_id, "source_database": "sales", "source_name": "orders",
                "source_id": "legacy-src", "owner_role": "ANALYST", "cursor_epoch": 3,
                "cursor_sequence": 0, "status": "DROPPED"})
