"""Streams runtime with fakes: capture, verified reads, offsets, triggers, lifecycle."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.access_control.security_context import SecurityContext
from app.modules.streams import capture as capture_module
from app.modules.streams import service as service_module
from app.modules.streams.capture import Caller, CaptureCoordinator
from app.modules.streams.codec import Column
from app.modules.streams.journal import ConsumptionJournal
from app.modules.streams.metadata_repository import StreamMetadataRepository
from app.modules.streams.namespace import StreamName
from app.modules.streams.schemas import ChangeCursor, StreamError, StreamMode
from app.modules.streams.sources import READY, REGISTERING, SourceHead, SourceRecord, journal_digest
from app.sql_frontend.parser import parse_statement
from tests.unit.stream_metadata_fakes import metadata_scope

SOURCE = SourceRecord(
    "s" * 32, 1, "shop", "orders", 42, ("id",),
    (Column("id", "bigint"), Column("v", "varchar(10)")), "f" * 64,
    "__nova_stream_changes_x", "__nova_stream_lineage_x", READY,
)


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.conn.statements.append(sql)
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise RuntimeError("engine refused")


class Writer:
    def __init__(self, fail_on: str | None = None):
        self.statements: list[str] = []
        self.fail_on = fail_on

    def cursor(self):
        return Cursor(self)


@pytest.fixture
def capture_env(monkeypatch):
    writer = Writer()
    registry = SimpleNamespace(
        get=AsyncMock(return_value=SOURCE),
        verify=AsyncMock(return_value=SourceHead(4, 10)),
        version=AsyncMock(return_value=11),
        record_commit=AsyncMock(),
        set_status=AsyncMock(),
    )
    existing: dict[str, list[tuple]] = {"current": [], "lineage": []}

    async def writer_query(conn, sql, params=None):
        if "__nova_stream_lineage" in sql:
            return existing["lineage"]
        return existing["current"]

    @contextlib.asynccontextmanager
    async def writer_conn():
        yield writer

    @contextlib.asynccontextmanager
    async def lock(_source_id):
        yield

    monkeypatch.setattr(capture_module, "source_registry", registry)
    monkeypatch.setattr(capture_module, "writer_conn", writer_conn)
    monkeypatch.setattr(capture_module, "writer_query", writer_query)
    monkeypatch.setattr(capture_module, "source_lock", lock)
    from app.modules.access_control.service import access_control_service

    allow = AsyncMock(return_value=[True])
    monkeypatch.setattr(access_control_service, "role_allows", allow)
    return SimpleNamespace(writer=writer, registry=registry, existing=existing, allow=allow)


QUERIES: list[str] = []


def caller(rows: list[list[Any]], native=None) -> Caller:
    QUERIES.clear()

    async def query(sql):
        QUERIES.append(sql)
        return [], rows

    return Caller("alice", "analyst", query, AsyncMock(return_value=native or "native"))


def statement(sql: str):
    parsed = parse_statement(sql)
    return parsed.statement_context, parsed.normalized_sql


async def test_insert_commits_source_journal_and_lineage_atomically(capture_env):
    node, sql = statement("INSERT INTO shop.orders VALUES (1, 'a'), (2, 'b')")
    who = caller([[1, "a"], [2, "b"]])
    outcome = await CaptureCoordinator().write(SOURCE, node, sql, "insert", who)
    assert outcome.affected_rows == 2 and outcome.commit_sequence == 5
    statements = capture_env.writer.statements
    assert statements[0] == "BEGIN" and statements[-1] == "COMMIT"
    assert statements[1].startswith("INSERT INTO `shop`.`__nova_stream_changes_x` VALUES (5, 1")
    assert statements[2].startswith("INSERT INTO `shop`.`__nova_stream_lineage_x`")
    assert statements[3].startswith("INSERT INTO `shop`.`orders` (`id`, `v`) VALUES")
    assert "CAST(__c0 AS bigint)" in QUERIES[0] and "(VALUES (1, 'a'), (2, 'b'))" in QUERIES[0]
    capture_env.registry.record_commit.assert_awaited_once()
    args = capture_env.registry.record_commit.await_args.args
    assert args[1:2] == (5,) and args[3:5] == (10, 11)


async def test_journal_rows_carry_a_verifiable_digest(capture_env):
    node, sql = statement("DELETE FROM shop.orders WHERE id = 1")
    capture_env.existing["lineage"] = [("k", "lineage-1", True)]
    await CaptureCoordinator().write(SOURCE, node, sql, "delete", caller([[1, "a"]]))
    journal = capture_env.writer.statements[1]
    assert "'DELETE'" in journal and journal.count("(5, 1, ") == 1
    assert capture_env.writer.statements[3].startswith(
        "DELETE FROM `shop`.`orders` WHERE (`id` = CAST(1 AS bigint))"
    )


async def test_failure_rolls_back_and_records_nothing(capture_env):
    capture_env.writer.fail_on = "INSERT INTO `shop`.`orders`"
    node, sql = statement("INSERT INTO shop.orders VALUES (1, 'a')")
    with pytest.raises(StreamError) as caught:
        await CaptureCoordinator().write(SOURCE, node, sql, "insert", caller([[1, "a"]]))
    assert caught.value.code == "STREAM_WRITE_FAILED"
    assert capture_env.writer.statements[-1] == "ROLLBACK"
    capture_env.registry.record_commit.assert_not_awaited()


async def test_unexpected_engine_version_blocks_the_source(capture_env):
    capture_env.registry.version.return_value = 13
    node, sql = statement("INSERT INTO shop.orders VALUES (1, 'a')")
    await CaptureCoordinator().write(SOURCE, node, sql, "insert", caller([[1, "a"]]))
    status = capture_env.registry.set_status.await_args.args
    assert status[1] == "BLOCKED"
    capture_env.registry.record_commit.assert_not_awaited()


async def test_a_no_op_write_does_not_touch_the_source(capture_env):
    capture_env.existing["current"] = [(1, "a")]
    node, sql = statement("INSERT INTO shop.orders VALUES (1, 'a')")
    outcome = await CaptureCoordinator().write(SOURCE, node, sql, "insert", caller([[1, "a"]]))
    assert outcome.commit_sequence is None and capture_env.writer.statements == []


async def test_upsert_of_an_existing_key_is_an_update(capture_env):
    capture_env.existing["current"] = [(1, "a")]
    node, sql = statement("INSERT INTO shop.orders VALUES (1, 'b')")
    await CaptureCoordinator().write(SOURCE, node, sql, "insert", caller([[1, "b"]]))
    assert "'UPDATE'" in capture_env.writer.statements[1]


@pytest.mark.parametrize("status", [REGISTERING, "BLOCKED", "SOURCE_INVALID"])
async def test_sources_not_ready_write_natively(capture_env, status):
    from dataclasses import replace

    capture_env.registry.get.return_value = replace(SOURCE, status=status)
    node, sql = statement("INSERT INTO shop.orders VALUES (1, 'a')")
    outcome = await CaptureCoordinator().write(
        SOURCE, node, sql, "insert", caller([], native="native result")
    )
    assert outcome.native == "native result" and capture_env.writer.statements == []


async def test_unauthorized_writes_are_refused(capture_env):
    capture_env.allow.return_value = [False]
    node, sql = statement("DELETE FROM shop.orders WHERE id = 1")
    with pytest.raises(StreamError) as caught:
        await CaptureCoordinator().write(SOURCE, node, sql, "delete", caller([]))
    assert caught.value.code == "STREAM_ACCESS_DENIED"


@pytest.mark.parametrize(("sql", "kind"), [
    ("INSERT OVERWRITE shop.orders SELECT * FROM shop.orders", "overwrite"),
    ("INSERT INTO shop.orders (id) VALUES (1)", "insert"),
    ("INSERT INTO shop.orders WITH LABEL x VALUES (1, 'a')", "insert"),
    ("INSERT INTO shop.orders VALUES (1, DEFAULT)", "insert"),
    ("UPDATE shop.orders SET id = 2 WHERE id = 1", "update"),
    ("UPDATE shop.orders SET v = 'x' FROM shop.other WHERE orders.id = other.id", "update"),
    ("DELETE FROM shop.orders USING shop.other WHERE orders.id = other.id", "delete"),
])
async def test_unsupported_writes_are_refused(capture_env, sql, kind):
    node, normalized = statement(sql)
    with pytest.raises(StreamError) as caught:
        await CaptureCoordinator().write(SOURCE, node, normalized, kind, caller([]))
    assert caught.value.code == "STREAM_SOURCE_WRITE_UNSUPPORTED"


async def test_update_plans_the_new_values_with_the_engine(capture_env):
    node, sql = statement("UPDATE shop.orders SET v = upper(v) WHERE id > 0")
    who = caller([[1, "a", "A"]])
    await CaptureCoordinator().write(SOURCE, node, sql, "update", who)
    assert QUERIES[0] == (
        "SELECT `id`, `v`, CAST((upper(v)) AS varchar(10)) AS __new0 FROM `shop`.`orders` "
        "WHERE id > 0"
    )
    assert "'UPDATE'" in capture_env.writer.statements[1]


# ── verified reads ──────────────────────────────────────────────────────


def _journal_row(seq, ordinal, commit_id, row_id, op, before, after, digest=None):
    fields = [seq, ordinal, commit_id, 1, row_id, op, "k", before, after, 9]
    return (seq, ordinal, commit_id, 1, row_id, op, "k", before, after, 9,
            digest or journal_digest(fields))


@pytest.fixture
def read_env(monkeypatch):
    commits = {"rows": [[1, "c1", 1], [2, "c2", 1]]}
    journal = [
        _journal_row(1, 1, "c1", "r1", "INSERT", None, '{"id":1,"v":"a"}'),
        _journal_row(2, 1, "c2", "r1", "UPDATE", '{"id":1,"v":"a"}', '{"id":1,"v":"b"}'),
    ]

    async def read_commits(source, after, through):
        return [dict(zip(("commit_seq", "commit_id", "row_count"), row, strict=True))
                for row in commits["rows"]]

    async def writer_query(conn, sql, params=None):
        return journal

    @contextlib.asynccontextmanager
    async def writer_conn():
        yield object()

    set_status = AsyncMock()
    monkeypatch.setattr(service_module, "writer_query", writer_query)
    monkeypatch.setattr(service_module, "writer_conn", writer_conn)
    monkeypatch.setattr(service_module.source_registry, "set_status", set_status)
    service = service_module.StreamService()
    monkeypatch.setattr(service_module.source_registry, "commits", read_commits)
    resolved = service_module.ResolvedStream(
        SimpleNamespace(), SOURCE, StreamMode.STANDARD, 0, SourceHead(2, 11)
    )
    return SimpleNamespace(service=service, commits=commits, journal=journal,
                           resolved=resolved, set_status=set_status)


async def test_an_interval_is_reduced(read_env):
    rows = await read_env.service.rows(read_env.resolved)
    assert [(r.action, r.is_update, r.values["v"]) for r in rows] == [("INSERT", False, "b")]


async def test_a_tampered_row_blocks_the_source(read_env):
    row = list(read_env.journal[1])
    row[8] = '{"id":1,"v":"evil"}'
    read_env.journal[1] = tuple(row)
    with pytest.raises(StreamError) as caught:
        await read_env.service.rows(read_env.resolved)
    assert caught.value.code == "STREAM_SOURCE_BLOCKED"
    assert read_env.set_status.await_args.args[1] == "BLOCKED"


async def test_a_gap_in_the_commit_index_is_an_error(read_env):
    read_env.commits["rows"] = [[2, "c2", 1]]
    with pytest.raises(StreamError, match="gap"):
        await read_env.service.rows(read_env.resolved)


async def test_a_missing_journal_row_is_an_error(read_env):
    read_env.journal.pop()
    with pytest.raises(StreamError, match="missing"):
        await read_env.service.rows(read_env.resolved)


async def test_reads_are_bounded(read_env, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STREAM_READ_MAX_ROWS", 1)
    with pytest.raises(StreamError) as caught:
        await read_env.service.rows(read_env.resolved)
    assert caught.value.code == "STREAM_READ_TOO_LARGE"


def test_the_relation_checks_select_in_the_same_statement(read_env):
    from app.modules.streams.reducer import StreamRow

    sql = read_env.service.relation_sql(
        read_env.resolved, [StreamRow({"id": 1, "v": "a"}, "INSERT", False, "r1")]
    )
    assert "FROM `shop`.`orders` WHERE 1 = 0" in sql and "UNION ALL" in sql
    assert "AS `METADATA$ACTION`" in sql
    empty = read_env.service.relation_sql(read_env.resolved, [])
    assert empty.startswith("(SELECT `id` AS `id`") and "AS `METADATA$ROW_ID`" in empty


# ── offsets ─────────────────────────────────────────────────────────────


def receipt_scope(receipts):
    repo = StreamMetadataRepository(None, SecurityContext("alice", "ANALYST"))
    repo._bound = AsyncMock()
    repo._all = AsyncMock(return_value=[
        {"sequence_from": start, "sequence_to": end} for start, end in receipts
    ])
    return metadata_scope(repo)[0]


@pytest.mark.parametrize(("receipts", "expected"), [
    ([], 3),
    ([(3, 5), (5, 9)], 9),
    ([(5, 9), (3, 5)], 9),
    ([(1, 3), (3, 5)], 5),
])
async def test_offset_is_an_unbroken_chain(receipts, expected):
    cursor = await ConsumptionJournal(receipt_scope(receipts)).committed_cursor(
        "stream", ChangeCursor(1, 3)
    )
    assert cursor == ChangeCursor(1, expected)


@pytest.mark.parametrize("receipts", [[(3, 5), (6, 9)], [(3, 5), (3, 6)], [(2, 4)]])
async def test_broken_offset_chains_block(receipts):
    with pytest.raises(StreamError):
        await ConsumptionJournal(receipt_scope(receipts)).committed_cursor(
            "stream", ChangeCursor(1, 3)
        )


def test_dispatch_slots_are_per_offset_and_attempt():
    from app.modules.streams.consumption import Consumption
    from app.modules.streams.schemas import ChangeSnapshot, operation_id
    from app.modules.streams.verification import TargetIdentity

    def consumption(after: int, attempt: int = 0) -> Consumption:
        return Consumption(
            operation_id("c", after, attempt), "stream", 2,
            ChangeSnapshot("src", ChangeCursor(1, after), ChangeCursor(1, after + 3), 1),
            TargetIdentity("db", "t", "label", "alice"), operation_id("sql"), attempt=attempt,
        )

    first = consumption(3).decision_claim("a" * 64, "submit")
    assert first.key == consumption(3).decision_claim("b" * 64, "submit").key
    assert first.key != consumption(6).decision_claim("a" * 64, "submit").key
    assert first.key != consumption(3, attempt=1).decision_claim("a" * 64, "submit").key


# ── triggers ────────────────────────────────────────────────────────────

NOW = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)


def stream_task(**overrides):
    from app.modules.task_orchestration.stream_when import dump

    return {
        "id": "t1", "name": "apply", "database_name": "shop", "schema_name": "public",
        "schedule_kind": "stream", "lifecycle_state": "STARTED", "owner_role": "analyst",
        "overlap_policy": "skip",
        "when_ast": dump({"op": "has_data", "stream": "shop.orders_changes"},
                         {"shop.orders_changes": "sid"}),
        **overrides,
    }


@pytest.fixture
def trigger_env(monkeypatch):
    state = {"pending": True, "offset": 4, "head": 6}

    async def trigger_state(stream_id, name):
        assert stream_id == "sid" and name == StreamName("shop", "orders_changes")
        return state["pending"], [state["offset"], state["head"]]

    monkeypatch.setattr(service_module.stream_service, "trigger_state", trigger_state)
    repo = SimpleNamespace(latest_stream_runs=AsyncMock(return_value=[]))
    return SimpleNamespace(state=state, repo=repo)


async def plan(trigger_env, task=None, now=NOW):
    from app.modules.task_orchestration.stream_triggers import plan_stream_triggers

    return await plan_stream_triggers([task or stream_task()], [], now, trigger_env.repo)


async def test_a_started_stream_task_with_data_is_due(trigger_env):
    due = await plan(trigger_env)
    assert len(due) == 1 and due[0].trigger_type == "stream"
    assert due[0].wal_marks["streams"] == {"shop.orders_changes": ["sid", 4, 6]}
    assert due[0].run_id == (await plan(trigger_env))[0].run_id  # duplicate ticks: one run


@pytest.mark.parametrize("task", [
    stream_task(lifecycle_state="SUSPENDED"), stream_task(schedule_kind="manual"),
    stream_task(when_ast=None),
])
async def test_not_due_when_suspended_or_not_a_stream_task(trigger_env, task):
    assert await plan(trigger_env, task) == []


async def test_not_due_without_data(trigger_env):
    trigger_env.state["pending"] = False
    assert await plan(trigger_env) == []


async def test_new_data_changes_the_run(trigger_env):
    first = (await plan(trigger_env))[0].run_id
    trigger_env.state["head"] = 7
    assert (await plan(trigger_env))[0].run_id != first


def _run(digest: str, state: str, started: datetime, finished: datetime | None = None):
    return {"state": state, "started_at": started.replace(tzinfo=None),
            "finished_at": (finished or started).replace(tzinfo=None),
            "wal_marks": json.dumps({"stream_trigger": digest})}


async def test_min_interval_and_acknowledged_observations(trigger_env, monkeypatch):
    due = (await plan(trigger_env))[0]
    digest = due.wal_marks["stream_trigger"]
    trigger_env.repo.latest_stream_runs.return_value = [
        _run(digest, "success", NOW - timedelta(seconds=5))
    ]
    assert await plan(trigger_env) == []  # minimum interval
    trigger_env.repo.latest_stream_runs.return_value = [
        _run(digest, "success", NOW - timedelta(minutes=5))
    ]
    assert await plan(trigger_env) == []  # same observation already handled: no hot loop


async def test_a_failed_run_is_retried_after_back_off(trigger_env):
    digest = (await plan(trigger_env))[0].wal_marks["stream_trigger"]
    failed = _run(digest, "failed", NOW - timedelta(minutes=5), NOW - timedelta(seconds=10))
    trigger_env.repo.latest_stream_runs.return_value = [failed]
    assert await plan(trigger_env) == []  # still backing off
    failed = _run(digest, "failed", NOW - timedelta(minutes=5), NOW - timedelta(minutes=2))
    trigger_env.repo.latest_stream_runs.return_value = [failed]
    retry = await plan(trigger_env)
    assert len(retry) == 1 and retry[0].run_key.endswith(":1")


async def test_a_blocked_stream_does_not_trigger(trigger_env, monkeypatch):
    async def blocked(stream_id, name):
        raise StreamError("STREAM_SOURCE_BLOCKED", "blocked")

    monkeypatch.setattr(service_module.stream_service, "trigger_state", blocked)
    assert await plan(trigger_env) == []


def test_suspended_scheduled_roots_do_not_fire():
    from app.modules.task_orchestration.scheduler import plan_tick

    task = {"id": "t", "name": "nightly", "database_name": "shop", "schema_name": "public",
            "schedule_kind": "interval", "schedule_expr": "INTERVAL 1 MINUTE",
            "timezone": "UTC", "created_at": datetime(2026, 1, 1), "owner_role": "analyst"}
    later = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    assert plan_tick([task], [], later).due
    assert plan_tick([{**task, "lifecycle_state": "SUSPENDED"}], [], later).due == []


# ── lifecycle ───────────────────────────────────────────────────────────


@pytest.fixture
def lifecycle_repo(monkeypatch):
    from app.modules.task_orchestration import lowering

    tasks = {
        "root": {"id": "id_root", "name": "root", "database_name": "shop",
                 "schema_name": "public", "owner_role": "analyst", "handler": "sql"},
        "child": {"id": "id_child", "name": "child", "database_name": "shop",
                  "schema_name": "public", "owner_role": "analyst", "handler": "sql"},
    }
    repo = SimpleNamespace(
        find_task=AsyncMock(side_effect=lambda name, db, schema: tasks.get(name)),
        list_all_edges=AsyncMock(return_value=[{"graph_id": "shop.public.root",
                                                "parent_task": "root", "child_task": "child"}]),
        set_lifecycle=AsyncMock(),
    )
    monkeypatch.setattr(lowering, "task_orchestration_repository", repo)
    return repo


async def test_resume_and_suspend_a_root(lifecycle_repo):
    from app.modules.task_orchestration.lowering import set_task_lifecycle

    user = {"username": "alice", "active_role": "analyst"}
    changed = await set_task_lifecycle("root", "shop", "public", "resume", user)
    assert changed["lifecycle_state"] == "STARTED"
    lifecycle_repo.set_lifecycle.assert_awaited_with("id_root", "STARTED")
    assert await set_task_lifecycle("nope", "shop", "public", "resume", user) is None


@pytest.mark.parametrize(("name", "action", "role", "message"), [
    ("child", "resume", "analyst", "root task"),
    ("root", "resume", "other", "activate that role"),
    ("root", "set", "analyst", "RESUME and SUSPEND"),
])
async def test_lifecycle_refusals(lifecycle_repo, name, action, role, message):
    from app.modules.task_orchestration.lowering import TaskLoweringError, set_task_lifecycle

    with pytest.raises(TaskLoweringError, match=message):
        await set_task_lifecycle(name, "shop", "public", action,
                                 {"username": "alice", "active_role": role})
    lifecycle_repo.set_lifecycle.assert_not_awaited()
