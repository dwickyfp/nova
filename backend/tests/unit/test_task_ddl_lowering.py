"""Unit tests for the Nova ``CREATE TASK`` -> metadata lowering (NOVA-54 / PR 3a).

Pure tests: no engine, no Redis, no database. They pin the acceptance criteria
the grammar cannot express — clause order and duplication, the overlap enum, cron
validation, ``[=]`` normalisation, self/merged cycle rejection, and the fact that
``CREATE TASK`` lowers to metadata rather than an engine statement.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.task_orchestration.ddl import (
    TaskDDLError,
    is_create_task,
    parse_create_task,
    validate_merged_graph,
)
from app.modules.task_orchestration.graph import Edge, GraphValidationError
from app.modules.task_orchestration.lowering import TaskLoweringError, persist_lowered_task


def lower(sql: str, **kwargs: Any):
    return parse_create_task(sql, database="db1", timezone="Asia/Jakarta", **kwargs)


class TestIsCreateTask:
    @pytest.mark.parametrize(
        "sql",
        [
            "CREATE TASK t1 AS INSERT INTO t SELECT 1",
            "  create task t1 AS INSERT INTO t SELECT 1",
            "\nCREATE   TASK t1 AS INSERT INTO t SELECT 1",
        ],
    )
    def test_detects_the_surface(self, sql: str) -> None:
        assert is_create_task(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "CREATE TABLE t AS SELECT 1",
            "CREATE ML_MODEL m AS SELECT 1",
            "SELECT 1",
            "SUBMIT TASK t AS INSERT INTO x SELECT 1",
        ],
    )
    def test_does_not_match_other_statements(self, sql: str) -> None:
        assert not is_create_task(sql)


class TestClauseLowering:
    def test_after_single_parent(self) -> None:
        task = lower("CREATE TASK t1 AFTER a AS INSERT INTO t SELECT 1")
        assert task.after == ("a",)
        assert task.finalize is None


class TestTaskScope:
    """A task name is qualified (``database.schema.task``), like a stage."""

    def test_three_part_name_sets_database_and_schema(self) -> None:
        task = lower("CREATE TASK analytics.etl.daily AS INSERT INTO t SELECT 1")
        assert (task.database_name, task.schema_name, task.name) == (
            "analytics",
            "etl",
            "daily",
        )
        assert task.qualified_name == "analytics.etl.daily"

    def test_two_part_name_sets_schema_from_the_session_database(self) -> None:
        task = lower("CREATE TASK etl.daily AS INSERT INTO t SELECT 1")
        # database comes from the session default ("db1" in the test helper).
        assert (task.database_name, task.schema_name, task.name) == (
            "db1",
            "etl",
            "daily",
        )

    def test_bare_name_falls_back_to_session_scope(self) -> None:
        task = lower("CREATE TASK daily AS INSERT INTO t SELECT 1", schema="silver")
        assert (task.database_name, task.schema_name, task.name) == (
            "db1",
            "silver",
            "daily",
        )

    def test_backticked_segments_are_split_and_unquoted(self) -> None:
        task = lower("CREATE TASK `my db`.`sch`.`my task` AS INSERT INTO t SELECT 1")
        assert (task.database_name, task.schema_name, task.name) == (
            "my db",
            "sch",
            "my task",
        )

    def test_four_part_name_is_rejected(self) -> None:
        with pytest.raises(TaskDDLError, match="database.schema.task"):
            lower("CREATE TASK cat.db.sch.t AS INSERT INTO t SELECT 1")

    def test_after_single_parent(self) -> None:
        task = lower("CREATE TASK t1 AFTER a AS INSERT INTO t SELECT 1")
        assert task.after == ("a",)
        assert task.finalize is None

    def test_after_multiple_parents(self) -> None:
        task = lower("CREATE TASK t1 AFTER a, b, c AS INSERT INTO t SELECT 1")
        assert task.after == ("a", "b", "c")

    def test_after_qualified_parent_is_rejected(self) -> None:
        """A graph is single-schema, so a parent is a bare name, not qualified.

        Stripping the scope would silently resolve `other.t` to a same-named
        task in this schema, so the qualified form is rejected outright.
        """
        with pytest.raises(TaskDDLError, match="bare task name"):
            lower("CREATE TASK db1.sch.t1 AFTER db1.sch.a AS INSERT INTO t SELECT 1")

    def test_finalize_normalises_both_spellings(self) -> None:
        with_eq = lower("CREATE TASK t1 FINALIZE = b AS INSERT INTO t SELECT 1")
        without_eq = lower("CREATE TASK t1 FINALIZE b AS INSERT INTO t SELECT 1")
        assert with_eq.finalize == without_eq.finalize == "b"

    def test_when_preserves_and_structure(self) -> None:
        task = lower("CREATE TASK t1 WHEN a > 1 AND b < 2 AS INSERT INTO t SELECT 1")
        assert task.when_expr == "a > 1 AND b < 2"

    def test_when_preserves_or_precedence(self) -> None:
        task = lower(
            "CREATE TASK t1 WHEN a > 1 OR b < 2 AND c > 3 AS INSERT INTO t SELECT 1"
        )
        assert task.when_expr is not None
        assert "OR" in task.when_expr and "AND" in task.when_expr

    @pytest.mark.parametrize(
        ("written", "expected"),
        [("SKIP", "skip"), ("skip", "skip"), ("QUEUE", "queue"), ("allow", "allow")],
    )
    def test_overlap_policy_is_normalised(self, written: str, expected: str) -> None:
        task = lower(f"CREATE TASK t1 OVERLAP_POLICY = '{written}' AS INSERT INTO t SELECT 1")
        assert task.overlap_policy == expected

    def test_overlap_policy_defaults_to_skip(self) -> None:
        assert lower("CREATE TASK t1 AS INSERT INTO t SELECT 1").overlap_policy == "skip"

    def test_all_clauses_together(self) -> None:
        task = lower(
            "CREATE TASK t1 AFTER a FINALIZE b WHEN c > 0 "
            "OVERLAP_POLICY = 'QUEUE' SCHEDULE = '0 2 * * * UTC' "
            "AS INSERT INTO t SELECT 1"
        )
        assert task.after == ("a",)
        assert task.finalize == "b"
        assert task.when_expr == "c > 0"
        assert task.overlap_policy == "queue"
        assert task.schedule_kind == "cron"
        assert task.schedule_expr == "0 2 * * *"


class TestScheduleLowering:
    def test_cron_with_embedded_timezone(self) -> None:
        task = lower("CREATE TASK t1 SCHEDULE = '0 2 * * * Asia/Jakarta' AS INSERT INTO t SELECT 1")
        assert task.schedule_kind == "cron"
        assert task.schedule_expr == "0 2 * * *"
        assert task.timezone == "Asia/Jakarta"

    def test_using_cron_prefix_is_accepted(self) -> None:
        task = lower(
            "CREATE TASK t1 SCHEDULE = 'USING CRON 0 2 * * * Asia/Jakarta' "
            "AS INSERT INTO t SELECT 1"
        )
        assert task.schedule_kind == "cron"
        assert task.schedule_expr == "0 2 * * *"
        assert task.timezone == "Asia/Jakarta"

    def test_embedded_timezone_overrides_the_session_default(self) -> None:
        task = parse_create_task(
            "CREATE TASK t1 SCHEDULE = '0 * * * * UTC' AS INSERT INTO t SELECT 1",
            database="db1",
            timezone="Asia/Jakarta",
        )
        assert task.timezone == "UTC"

    def test_missing_schedule_is_manual(self) -> None:
        task = lower("CREATE TASK t1 AS INSERT INTO t SELECT 1")
        assert task.schedule_kind == "manual"
        assert task.schedule_expr is None

    @pytest.mark.parametrize(
        "cron", ["bad cron", "not-a-cron", "* * * *", "99 99 99 99 99"]
    )
    def test_invalid_cron_is_rejected(self, cron: str) -> None:
        with pytest.raises(TaskDDLError):
            lower(f"CREATE TASK t1 SCHEDULE = '{cron}' AS INSERT INTO t SELECT 1")


class TestBodyRestriction:
    @pytest.mark.parametrize(
        "body",
        [
            "INSERT INTO t SELECT 1",
            "INSERT OVERWRITE t SELECT 1",
            "CREATE TABLE x AS SELECT 1",
            "CACHE SELECT a FROM t",
        ],
    )
    def test_accepted_bodies(self, body: str) -> None:
        task = lower(f"CREATE TASK t1 AS {body}")
        assert task.body == body

    def test_bare_select_body_is_rejected_by_the_grammar(self) -> None:
        with pytest.raises(TaskDDLError):
            lower("CREATE TASK t1 AS SELECT 1")

    def test_stage_reference_body_is_accepted_since_109a(self) -> None:
        # NOVA-125 (109-A) adds the `@stage` rule to the vendored grammar, so a
        # task body that reads from a stage now parses. Before 109-A this failed
        # at parse time ("no viable alternative at input 'FROM @'"). The task
        # lowering preserves the body verbatim; `@stage` translation stays in the
        # dialect pipeline (109-B, NOVA-126).
        task = lower("CREATE TASK t1 AS INSERT INTO t SELECT a FROM @stage1.data.csv")
        assert task.body == "INSERT INTO t SELECT a FROM @stage1.data.csv"


class TestValidation:
    def test_schedule_before_after_is_rejected(self) -> None:
        with pytest.raises(TaskDDLError, match="order"):
            lower(
                "CREATE TASK t1 SCHEDULE = '0 2 * * *' AFTER a AS INSERT INTO t SELECT 1"
            )

    def test_duplicate_clause_is_rejected(self) -> None:
        with pytest.raises(TaskDDLError, match="duplicate"):
            lower("CREATE TASK t1 AFTER a AFTER b AS INSERT INTO t SELECT 1")

    def test_unknown_overlap_policy_is_rejected(self) -> None:
        with pytest.raises(TaskDDLError, match="OVERLAP_POLICY"):
            lower("CREATE TASK t1 OVERLAP_POLICY = 'MAYBE' AS INSERT INTO t SELECT 1")

    def test_self_after_is_rejected(self) -> None:
        with pytest.raises(TaskDDLError, match="itself"):
            lower("CREATE TASK t1 AFTER t1 AS INSERT INTO t SELECT 1")

    def test_self_finalize_is_rejected(self) -> None:
        with pytest.raises(TaskDDLError, match="itself"):
            lower("CREATE TASK t1 FINALIZE t1 AS INSERT INTO t SELECT 1")

    def test_duplicate_parent_is_rejected(self) -> None:
        with pytest.raises(TaskDDLError, match="more than once"):
            lower("CREATE TASK t1 AFTER a, a AS INSERT INTO t SELECT 1")

    def test_not_a_create_task(self) -> None:
        with pytest.raises(TaskDDLError):
            parse_create_task("SELECT 1", database="db1", timezone="UTC")


class TestMergedGraphCycle:
    def test_cross_task_cycle_is_rejected(self) -> None:
        # a -> b already stored; adding `b AFTER a` would close the loop.
        with pytest.raises(GraphValidationError):
            validate_merged_graph(["a", "b"], [Edge("a", "b"), Edge("b", "a")])

    def test_diamond_is_accepted(self) -> None:
        validate_merged_graph(
            ["a", "b", "c", "d"],
            [Edge("a", "b"), Edge("a", "c"), Edge("b", "d"), Edge("c", "d")],
        )


class FakeLoweringRepository:
    """In-memory stand-in for ``TaskOrchestrationRepository``.

    Implements only what ``persist_lowered_task`` calls, with the same
    name-keyed graph semantics as the real repository.
    """

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq}"

    async def create_task(self, data: dict[str, Any], created_by: str | None):
        task_id = self._next_id("task")
        row = {"id": task_id, "created_by": created_by, **data}
        self.tasks[task_id] = row
        return row

    async def delete_task(self, task_id: str) -> bool:
        return self.tasks.pop(task_id, None) is not None

    async def create_edge(self, graph_id: str, data: dict[str, Any]):
        edge_id = self._next_id("edge")
        row = {"id": edge_id, "graph_id": graph_id, **data}
        self.edges.append(row)
        return row

    async def delete_edge(self, edge_id: str) -> bool:
        before = len(self.edges)
        self.edges = [e for e in self.edges if e["id"] != edge_id]
        return len(self.edges) != before

    async def list_tasks(self, graph_id: str | None = None):
        return list(self.tasks.values())

    async def list_edges(self, graph_id: str):
        return [e for e in self.edges if e["graph_id"] == graph_id]

    async def list_all_edges(self):
        return list(self.edges)


class TestPersistLoweredTask:
    @pytest.fixture
    def repo(self, monkeypatch):
        fake = FakeLoweringRepository()
        monkeypatch.setattr(
            "app.modules.task_orchestration.lowering.task_orchestration_repository", fake
        )
        return fake

    async def test_single_after_writes_one_task_and_one_edge(self, repo) -> None:
        task = lower("CREATE TASK t1 AFTER a AS INSERT INTO t SELECT 1")
        result = await persist_lowered_task(task, created_by="alice")

        assert result.task["name"] == "t1"
        assert result.task["created_by"] == "alice"
        assert result.task["definition"] == "INSERT INTO t SELECT 1"
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert (edge["parent_task"], edge["child_task"], edge["edge_kind"]) == (
            "a",
            "t1",
            "after",
        )

    async def test_multiple_after_writes_two_edges(self, repo) -> None:
        task = lower("CREATE TASK t1 AFTER a, b AS INSERT INTO t SELECT 1")
        result = await persist_lowered_task(task, created_by="alice")
        assert {(e["parent_task"], e["child_task"]) for e in result.edges} == {
            ("a", "t1"),
            ("b", "t1"),
        }

    async def test_finalize_is_stored_with_its_own_kind(self, repo) -> None:
        task = lower("CREATE TASK t1 FINALIZE b AS INSERT INTO t SELECT 1")
        result = await persist_lowered_task(task, created_by="alice")
        assert len(result.edges) == 1
        assert result.edges[0]["edge_kind"] == "finalize"

    async def test_cycle_against_stored_rows_rolls_back(self, repo) -> None:
        # `a AFTER t1` already exists, so adding `t1 AFTER a` closes a loop.
        # Graph ids are qualified (database.schema.root), matching the lowering.
        await repo.create_task(
            {"name": "a", "database_name": "db1", "schema_name": "default", "timezone": "UTC"},
            "alice",
        )
        await repo.create_edge(
            "db1.default.t1",
            {"parent_task": "t1", "child_task": "a", "edge_kind": "after"},
        )

        task = lower("CREATE TASK db1.default.t1 AFTER a AS INSERT INTO t SELECT 1")
        with pytest.raises(TaskLoweringError, match="cycle"):
            await persist_lowered_task(task, created_by="alice")

        # No half-created task survives the rejection.
        assert all(row["name"] != "t1" for row in repo.tasks.values())
        assert all(e["child_task"] != "t1" for e in repo.edges)

    async def test_cycle_across_two_graph_keys_is_still_caught(self, repo) -> None:
        """Edges are keyed per creating graph, so the check must span components.

        `b AFTER a` is stored under graph `b`; `a AFTER b` would be stored under
        graph `a`. A per-graph-key check would see neither, so this proves the
        component-based validation is what actually catches it.
        """
        await repo.create_task(
            {"name": "a", "database_name": "db1", "schema_name": "default", "timezone": "UTC"},
            "alice",
        )
        await repo.create_task(
            {"name": "b", "database_name": "db1", "schema_name": "default", "timezone": "UTC"},
            "alice",
        )
        # graph_id differs from the new task's name on purpose: this is the
        # `CREATE TASK b AFTER a` row.
        await repo.create_edge(
            "db1.default.b",
            {"parent_task": "a", "child_task": "b", "edge_kind": "after"},
        )

        tasks_before = len(repo.tasks)
        task = lower("CREATE TASK db1.default.a AFTER b AS INSERT INTO t SELECT 1")
        with pytest.raises(TaskLoweringError, match="cycle"):
            await persist_lowered_task(task, created_by="alice")

        # The rejected task row was rolled back: only the two pre-seeded rows
        # remain, and no edge for the new statement survived.
        assert len(repo.tasks) == tasks_before
        assert not any(e["graph_id"] == "db1.default.a" for e in repo.edges)
