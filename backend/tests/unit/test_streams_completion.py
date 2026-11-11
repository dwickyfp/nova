"""Streams, second round: MERGE and TRUNCATE capture, SHOW_INITIAL_ROWS,
CREATE OR REPLACE, retention (STALE), maintenance and the trigger view."""

from __future__ import annotations

import contextlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.streams import capture as capture_module
from app.modules.streams import service as service_module
from app.modules.streams.capture import CaptureCoordinator
from app.modules.streams.namespace import StreamName
from app.modules.streams.repository import StreamRecord
from app.modules.streams.schemas import ChangeCursor, StreamError, StreamMode
from app.modules.streams.sources import SourceHead, journal_digest
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.binding.catalog import Binder
from app.sql_frontend.binding.models import BoundColumn, BoundTable, TableName
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import CompositePlan, EngineSqlPlan
from app.sql_frontend.planning.planner import SQLPlanner
from tests.unit import test_streams_runtime as runtime
from tests.unit.test_streams_runtime import QUERIES, SOURCE, caller

#: The capture fakes of the first runtime suite, shared as a fixture.
capture_env = runtime.capture_env

# ── MERGE planning ──────────────────────────────────────────────────────

ORDERS = BoundTable(
    TableName("orders", "shop"),
    "BASE TABLE",
    columns=(
        BoundColumn("id", "bigint", 1, False),
        BoundColumn("v", "varchar(10)", 2, True, "new", default_sql="'new'"),
    ),
    key_type="PRIMARY",
    primary_key_columns=("id",),
)
GENERATED = BoundTable(
    TableName("orders", "shop"),
    "BASE TABLE",
    columns=(
        BoundColumn("id", "bigint", 1, False),
        BoundColumn("v", "varchar(10)", 2, True),
        BoundColumn("w", "varchar(20)", 3, True, generated_expression="concat(v, v)"),
    ),
    key_type="PRIMARY",
    primary_key_columns=("id",),
)
MERGE = (
    "MERGE INTO shop.orders AS t USING shop.incoming AS s ON t.id = s.id "
    "WHEN MATCHED AND s.gone THEN DELETE "
    "WHEN MATCHED THEN UPDATE SET v = s.v"
)
UPSERT = (
    "MERGE INTO shop.orders AS t USING shop.incoming AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET v = s.v "
    "WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id)"
)


class Catalog:
    def __init__(self, table: BoundTable) -> None:
        self.table = table

    async def resolve_table(self, name):
        return BoundTable(name, "BASE TABLE")

    async def get_columns(self, name):
        return self.table.columns

    async def get_details(self, name):
        return self.table


async def plan_merge(monkeypatch, table: BoundTable, captured: bool, sql: str = MERGE):
    from app.modules.streams.sources import source_registry

    monkeypatch.setattr(
        source_registry,
        "captured_tables",
        AsyncMock(return_value={("shop", "orders"): SOURCE} if captured else {}),
    )
    statement = ast_builders.build(parse_statement(sql))
    return await SQLPlanner().plan(
        statement, PlanningContext(database="shop", binder=Binder(Catalog(table)))
    )


async def test_a_merge_into_a_source_is_one_captured_plan(monkeypatch):
    plan = await plan_merge(monkeypatch, ORDERS, captured=True)
    assert isinstance(plan, EngineSqlPlan) and plan.capture is not None
    capture = plan.capture
    assert capture.columns == ("id", "v")
    assert capture.clause_kinds == ("delete", "update")
    assert "AS `__c1`" in capture.rows_sql
    assert capture.deleted_sql.startswith("SELECT `id`, `v` FROM shop.orders WHERE `id` IN")
    assert capture.check_sql.startswith("SELECT assert_true(")
    assert len(capture.native_steps) == 3
    # Sent to the engine as written, it fails rather than writing uncaptured.
    assert "assert_true(FALSE" in plan.engine_sql
    assert plan.requires_confirmation and plan.effects.deletes_rows
    assert plan.transaction_intent.kind == "merge"


async def test_a_captured_upsert_writes_defaults_for_new_rows(monkeypatch):
    plan = await plan_merge(monkeypatch, ORDERS, captured=True, sql=UPSERT)
    capture = plan.capture
    assert capture.clause_kinds == ("insert", "update") and not capture.deleted_sql
    # Whole rows: a new row gets the default of a column no clause writes.
    assert "'new'" in capture.rows_sql
    assert len(capture.native_steps) == 1


async def test_an_ordinary_merge_names_its_target_on_every_step(monkeypatch):
    plan = await plan_merge(monkeypatch, ORDERS, captured=False)
    assert isinstance(plan, CompositePlan)
    assert all(step.capture is None for step in plan.steps)
    assert {step.transaction_intent.target for step in plan.steps} == {
        ("default_catalog", "shop", "orders")
    }


async def test_a_captured_merge_refuses_generated_columns(monkeypatch):
    with pytest.raises(SemanticError, match="generated"):
        await plan_merge(monkeypatch, GENERATED, captured=True, sql=UPSERT)


# ── MERGE and TRUNCATE capture ──────────────────────────────────────────


def merge_capture(**overrides):
    from app.sql_frontend.planning.execution import MergeCapture

    values = {
        "rows_sql": "SELECT 1 AS `__c0`, 'b' AS `__c1`",
        "columns": ("id", "v"),
        "deleted_sql": "SELECT `id`, `v` FROM shop.orders WHERE `id` IN (2)",
        "check_sql": "SELECT assert_true(TRUE, 'x')",
        "clause_kinds": ("delete", "insert", "update"),
        "native_steps": ("check", "delete", "upsert"),
    }
    return MergeCapture(**{**values, **overrides})


async def test_a_merge_is_a_delete_commit_then_an_upsert_commit(capture_env):
    capture_env.registry.version = AsyncMock(side_effect=[11, 12])
    answers = iter([[[1]], [[2, "gone"]], [[1, "b"]]])

    async def query(sql):
        QUERIES.append(sql)
        return [], next(answers)

    who = caller([])
    who = replace(who, query=query)
    node = parse_statement(MERGE).statement_context
    outcome = await CaptureCoordinator().write(
        SOURCE, node, MERGE, "merge", who, merge_capture()
    )
    assert outcome.affected_rows == 2 and outcome.commit_sequence == 6
    # Every query ran before the first commit, as the caller.
    assert QUERIES[0].startswith("SELECT assert_true") and "IN (2)" in QUERIES[1]
    assert "CAST(`__c0` AS bigint)" in QUERIES[2]
    statements = capture_env.writer.statements
    commits = [i for i, sql in enumerate(statements) if sql == "COMMIT"]
    assert len(commits) == 2
    first, second = statements[: commits[0] + 1], statements[commits[0] + 1 :]
    assert first[3].startswith("DELETE FROM `shop`.`orders`")
    assert second[3].startswith("INSERT INTO `shop`.`orders`")
    sequences = [call.args[1] for call in capture_env.registry.record_commit.await_args_list]
    assert sequences == [5, 6]
    requested = capture_env.allow.await_args.kwargs["requests"]
    assert sorted(request.access for request in requested) == ["delete", "insert", "update"]


async def test_a_merge_planned_before_streams_existed_is_refused(capture_env):
    node = parse_statement(MERGE).statement_context
    with pytest.raises(StreamError) as caught:
        await CaptureCoordinator().write(SOURCE, node, MERGE, "merge", caller([]), None)
    assert caught.value.code == "STREAM_SOURCE_WRITE_UNSUPPORTED"


async def test_a_merge_whose_columns_drifted_needs_review(capture_env):
    node = parse_statement(MERGE).statement_context
    with pytest.raises(StreamError) as caught:
        await CaptureCoordinator().write(
            SOURCE, node, MERGE, "merge", caller([]), merge_capture(columns=("id",))
        )
    assert caught.value.code == "STREAM_SCHEMA_REVIEW_REQUIRED"


async def test_truncate_journals_every_row_as_a_delete(capture_env):
    node = parse_statement("TRUNCATE TABLE shop.orders").statement_context
    outcome = await CaptureCoordinator().write(
        SOURCE, node, "TRUNCATE TABLE shop.orders", "truncate", caller([[1, "a"], [2, "b"]])
    )
    assert outcome.affected_rows == 2
    assert QUERIES[0] == "SELECT `id`, `v` FROM `shop`.`orders`"
    journal = capture_env.writer.statements[1]
    assert journal.count("'DELETE'") == 2
    assert capture_env.writer.statements[3].startswith("DELETE FROM `shop`.`orders` WHERE")
    assert capture_env.allow.await_args.kwargs["requests"][0].access == "delete"


async def test_truncating_a_partition_is_refused(capture_env):
    sql = "TRUNCATE TABLE shop.orders PARTITION (p1)"
    node = parse_statement(sql).statement_context
    with pytest.raises(StreamError) as caught:
        await CaptureCoordinator().write(SOURCE, node, sql, "truncate", caller([]))
    assert caught.value.code == "STREAM_SOURCE_WRITE_UNSUPPORTED"


async def test_orphan_journal_rows_are_cleared_before_a_commit(capture_env, monkeypatch):
    seen: list[str] = []
    original = capture_module.writer_query

    async def writer_query(conn, sql, params=None):
        seen.append(sql)
        return await original(conn, sql, params)

    monkeypatch.setattr(capture_module, "writer_query", writer_query)
    node = parse_statement("DELETE FROM shop.orders WHERE id = 1").statement_context
    await CaptureCoordinator().write(
        SOURCE, node, "DELETE FROM shop.orders WHERE id = 1", "delete", caller([[1, "a"]])
    )
    assert "DELETE FROM `shop`.`__nova_stream_changes_x` WHERE commit_seq > %s" in seen


# ── grammar ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("sql", "append_only", "replace_", "initial"), [
    ("CREATE STREAM d.s ON TABLE d.t", False, False, False),
    ("CREATE OR REPLACE STREAM d.s ON TABLE d.t", False, True, False),
    ("CREATE STREAM d.s ON TABLE d.t SHOW_INITIAL_ROWS = TRUE", False, False, True),
    ("CREATE STREAM d.s ON TABLE d.t APPEND_ONLY = TRUE SHOW_INITIAL_ROWS = TRUE",
     True, False, True),
    ("CREATE STREAM d.s ON TABLE d.t APPEND_ONLY = FALSE SHOW_INITIAL_ROWS = FALSE",
     False, False, False),
])
def test_create_stream_options(sql, append_only, replace_, initial):
    statement = ast_builders.build(parse_statement(sql))
    assert (statement.append_only, statement.or_replace, statement.show_initial_rows) == (
        append_only, replace_, initial,
    )


def test_or_replace_and_if_not_exists_conflict():
    with pytest.raises(SemanticError, match="OR REPLACE or IF NOT EXISTS"):
        ast_builders.build(parse_statement("CREATE OR REPLACE STREAM IF NOT EXISTS s ON TABLE t"))


def test_show_initial_rows_stays_an_identifier():
    assert type(ast_builders.build(parse_statement("SELECT show_initial_rows FROM t"))).__name__ \
        == "NativeStatement"


# ── reads: SHOW_INITIAL_ROWS snapshots ──────────────────────────────────


def _row(seq, ordinal, commit_id, row_id, op, before, after):
    digest = journal_digest([seq, ordinal, commit_id, 1, row_id, op, "k", before, after, 10])
    return (seq, ordinal, commit_id, 1, row_id, op, "k", before, after, 10, digest)


@pytest.fixture
def snapshot_env(monkeypatch):
    commits = {"rows": [[1, "snap", 1], [2, "c2", 1]]}
    journal = [
        _row(1, 1, "snap", "r1", "INITIAL", None, '{"id":1,"v":"a"}'),
        _row(2, 1, "c2", "r1", "UPDATE", '{"id":1,"v":"a"}', '{"id":1,"v":"b"}'),
    ]

    async def execute(sql, params=None):
        return commits

    async def writer_query(conn, sql, params=None):
        return journal

    @contextlib.asynccontextmanager
    async def writer_conn():
        yield object()

    monkeypatch.setattr(service_module, "writer_query", writer_query)
    monkeypatch.setattr(service_module, "writer_conn", writer_conn)
    service = service_module.StreamService()
    monkeypatch.setattr(service, "_execute", execute)
    return service


async def test_the_snapshot_owner_sees_initial_rows_netted_with_later_changes(snapshot_env):
    resolved = service_module.ResolvedStream(
        SimpleNamespace(), SOURCE, StreamMode.STANDARD, 0, SourceHead(2, 11), initial_commit=1
    )
    rows = await snapshot_env.rows(resolved)
    assert [(r.action, r.is_update, r.values["v"]) for r in rows] == [("INSERT", False, "b")]


async def test_other_streams_skip_a_snapshot_but_still_count_its_rows(snapshot_env):
    resolved = service_module.ResolvedStream(
        SimpleNamespace(), SOURCE, StreamMode.STANDARD, 0, SourceHead(2, 11)
    )
    rows = await snapshot_env.rows(resolved)
    assert [(r.action, r.is_update, r.values["v"]) for r in rows] == [
        ("DELETE", True, "a"), ("INSERT", True, "b"),
    ]


async def test_a_snapshot_journals_current_rows_without_moving_the_engine(monkeypatch):
    registry = SimpleNamespace(
        get=AsyncMock(return_value=SOURCE),
        verify=AsyncMock(return_value=SourceHead(4, 10)),
        record_commit=AsyncMock(),
    )
    written: list[str] = []

    async def writer_query(conn, sql, params=None):
        if sql.startswith("SELECT `id`, `v` FROM"):
            return [(1, "a"), (2, "b")]
        if "__nova_stream_lineage" in sql:
            return []
        written.append(sql)
        return []

    @contextlib.asynccontextmanager
    async def writer_conn():
        yield object()

    @contextlib.asynccontextmanager
    async def lock(_source_id):
        yield

    for module in (service_module, capture_module):
        monkeypatch.setattr(module, "writer_query", writer_query)
    monkeypatch.setattr(service_module, "writer_conn", writer_conn)
    monkeypatch.setattr(service_module, "source_registry", registry)
    monkeypatch.setattr(service_module, "source_lock", lock)
    start, snapshot = await service_module.StreamService()._snapshot(
        SOURCE, service_module.StreamCaller("alice", "analyst")
    )
    assert (start, snapshot) == (4, 5)
    assert written[0].startswith("DELETE FROM `shop`.`__nova_stream_changes_x`")
    assert written[1].count("'INITIAL'") == 2
    args = registry.record_commit.await_args.args
    assert args[1] == 5 and args[3:6] == (10, 10, 2)


# ── CREATE OR REPLACE, retention ────────────────────────────────────────

NAME = StreamName("shop", "orders_changes")


def record(status="READY", owner="analyst"):
    return StreamRecord(
        NAME, 3, "sid", StreamName("shop", "orders"), SOURCE.source_id, owner,
        ChangeCursor(1, 4), status,
    )


async def test_replacing_needs_the_owner_role(monkeypatch):
    service = service_module.StreamService()
    monkeypatch.setattr(service.repository, "current", AsyncMock(return_value=record()))
    monkeypatch.setattr(service, "authorize_read", AsyncMock())
    with pytest.raises(StreamError) as caught:
        await service.create(
            NAME, StreamName("shop", "orders"), StreamMode.STANDARD,
            service_module.StreamCaller("bob", "other"), or_replace=True,
        )
    assert caught.value.code == "STREAM_ACCESS_DENIED"


async def test_a_stale_stream_must_be_replaced_or_dropped(monkeypatch):
    service = service_module.StreamService()
    monkeypatch.setattr(service.repository, "current", AsyncMock(return_value=record("STALE")))
    monkeypatch.setattr(service, "authorize_read", AsyncMock())
    with pytest.raises(StreamError) as caught:
        await service.current(NAME)
    assert caught.value.code == "STREAM_STALE"
    with pytest.raises(StreamError) as caught:
        await service.create(
            NAME, StreamName("shop", "orders"), StreamMode.STANDARD,
            service_module.StreamCaller("alice", "analyst"),
        )
    assert caught.value.code == "STREAM_ALREADY_EXISTS"


async def test_an_expired_backlog_marks_the_stream_stale(monkeypatch):
    service = service_module.StreamService()
    monkeypatch.setattr(service, "stale_after", AsyncMock(return_value=("2026-01-01", True)))
    monkeypatch.setattr(service.repository, "current", AsyncMock(return_value=record()))
    publish = AsyncMock()
    monkeypatch.setattr(service.repository, "publish", publish)
    with pytest.raises(StreamError) as caught:
        await service._check_retention(record(), SOURCE, 4, 9)
    assert caught.value.code == "STREAM_STALE"
    published = publish.await_args.args[0]
    assert published.status == "STALE" and published.generation == 4


async def test_nothing_pending_is_never_stale(monkeypatch):
    service = service_module.StreamService()
    execute = AsyncMock()
    monkeypatch.setattr(service, "_execute", execute)
    assert await service.stale_after(SOURCE, 4, 4) == (None, False)
    execute.assert_not_awaited()


async def test_retention_is_measured_on_the_engine_clock(monkeypatch):
    service = service_module.StreamService()
    execute = AsyncMock(return_value={"rows": [["2026-11-25 10:00:00", 0, 3600]]})
    monkeypatch.setattr(service, "_execute", execute)
    assert await service._retention(SOURCE, 4, 6) == ("2026-11-25 10:00:00", False, 3600)
    sql = execute.await_args.args[0]
    assert "INTERVAL 14 DAY" in sql and "TIMESTAMPDIFF(SECOND, NOW()" in sql


async def test_maintenance_marks_stale_streams_and_collects_journals(monkeypatch):
    service = service_module.StreamService()

    async def execute(sql, params=None):
        if "CONFIG_STREAM_SOURCES" in sql:
            return {"rows": [[SOURCE.source_id, "READY"]]}
        return {"rows": [["default_catalog", "shop", "default", "orders_changes",
                          SOURCE.source_id, "READY"]]}

    monkeypatch.setattr(service, "_execute", execute)
    monkeypatch.setattr(service.repository, "current", AsyncMock(return_value=record()))
    monkeypatch.setattr(service.journal, "committed_cursor",
                        AsyncMock(return_value=ChangeCursor(1, 4)))
    monkeypatch.setattr(service, "_retention", AsyncMock(return_value=(None, True, None)))
    mark = AsyncMock()
    monkeypatch.setattr(service, "_mark_stale", mark)
    collect = AsyncMock()
    monkeypatch.setattr(service, "collect_source", collect)
    registry = SimpleNamespace(
        get=AsyncMock(return_value=SOURCE), head_commit=AsyncMock(return_value=(9, 20))
    )
    monkeypatch.setattr(service_module, "source_registry", registry)
    summary = await service.maintain()
    assert summary["streams"] == {"READY": 0, "STALE": 1}
    assert summary["backlog_commits_max"] == 5
    mark.assert_awaited_once()
    collect.assert_awaited_once()


# ── trigger view ────────────────────────────────────────────────────────


async def test_trigger_facts_show_streams_runs_and_the_not_consumed_warning(monkeypatch):
    from app.modules.task_orchestration import views
    from app.modules.task_orchestration.stream_when import dump

    async def trigger_state(stream_id, name):
        return True, [4, 6]

    monkeypatch.setattr(service_module.stream_service, "trigger_state", trigger_state)
    run = {
        "id": "run1", "state": "success", "started_at": None, "finished_at": None,
        "wal_marks": json.dumps({"stream_trigger": "d",
                                 "streams": {"shop.orders_changes": ["sid", 4, 6]}}),
    }
    monkeypatch.setattr(views.task_orchestration_repository, "latest_stream_runs",
                        AsyncMock(return_value=[run]))
    task = {
        "name": "apply", "lifecycle_state": "STARTED", "schedule_kind": "stream",
        "when_expr": "SYSTEM$STREAM_HAS_DATA('shop.orders_changes')",
        "when_ast": dump({"op": "has_data", "stream": "shop.orders_changes"},
                         {"shop.orders_changes": "sid"}),
    }
    facts = await views.trigger_facts(task, "shop.public.apply")
    assert facts["streams"] == [
        {"stream": "shop.orders_changes", "offset": 4, "head": 6, "has_data": True}
    ]
    assert facts["recent_triggered_runs"][0]["observed"] == {
        "shop.orders_changes": {"offset": 4, "head": 6}
    }
    assert facts["warning"].startswith("STREAM_NOT_CONSUMED")
