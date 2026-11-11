"""EXECUTE TASK, script tasks (BEGIN ... END / $$) and LANGUAGE PYTHON tasks.

Covers the grammar, lowering and validation, statement splitting, SQL
frontend analysis and planning, the EXECUTE TASK path, the worker's dispatch,
the script runner and the Python runner against a fake sandbox.
"""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import fakeredis
import pytest
from fastapi import HTTPException

from app.common.sql_guard import split_sql_statements
from app.core.config import settings
from app.core.redis import session_store
from app.core.security import encrypt_password
from app.modules.access_control import data_tokens
from app.modules.notebooks.runtime.provider import (
    TASK_RUNTIME_PREFIX,
    KernelEvent,
    SandboxHandle,
    SandboxSpec,
)
from app.modules.query.repository import QueryResult
from app.modules.task_orchestration import python_task, script_task
from app.modules.task_orchestration import router as orch_router
from app.modules.task_orchestration.access import owns_task
from app.modules.task_orchestration.ddl import (
    TaskDDLError,
    parse_create_task,
    parse_execute_task,
)
from app.modules.task_orchestration.execution import NodeExecutionError, TaskSpec
from app.sql_frontend.analysis.semantics import semantics_registry
from app.sql_frontend.ast import statements as ast
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import ActionKind, ExecuteTaskPayload
from app.sql_frontend.planning.payloads import action_payload

PAYLOAD = "application/vnd.nova.payload+json"
SCRIPT = (
    "CREATE TASK sales.etl.refresh AS BEGIN\n"
    "  UPDATE stock SET level = CASE WHEN qty > 0 THEN 'ok' ELSE 'empty' END WHERE id > 0;\n"
    "  INSERT INTO stock_log SELECT * FROM stock;\n"
    "END"
)
PYTHON = (
    "CREATE TASK sales.etl.score\n"
    "  SCHEDULE = '0 2 * * * Asia/Jakarta'\n"
    "  LANGUAGE PYTHON RUNTIME_VERSION = '3.12'\n"
    "  PACKAGES = ('scikit-learn==1.5.2', 'tabulate')\n"
    "AS $$\n"
    "import novaduck\n"
    "con = novaduck.connect()\n"
    "# from a.default.b import c stays as written\n"
    "print(con.sql('SELECT 1').fetchall())\n"
    "$$"
)


@pytest.fixture(autouse=True)
def allowlist(monkeypatch):
    monkeypatch.setattr(settings, "NOTEBOOK_PACKAGE_ALLOWLIST", "scikit-learn,tabulate,polars")


# ── Grammar ─────────────────────────────────────────────────────────────


class TestGrammar:
    @pytest.mark.parametrize(
        ("sql", "context"),
        [
            ("EXECUTE TASK sales.etl.refresh", "NovaExecuteTaskStatementContext"),
            ("EXECUTE TASK refresh", "NovaExecuteTaskStatementContext"),
            ("EXECUTE stmt1", "ExecuteStatementContext"),
            (SCRIPT, "SubmitTaskStatementContext"),
            ("CREATE TASK t AS $$ INSERT INTO b SELECT 1; $$", "SubmitTaskStatementContext"),
            (PYTHON, "SubmitTaskStatementContext"),
            ("CREATE TASK t AS INSERT INTO b SELECT 1", "SubmitTaskStatementContext"),
        ],
    )
    def test_new_forms_parse(self, sql, context):
        assert type(parse_statement(sql).statement_context).__name__ == context

    def test_new_keywords_stay_identifiers(self):
        parsed = parse_statement("SELECT language, packages, runtime_version FROM t")
        assert type(parsed.statement_context).__name__ == "QueryStatementContext"

    def test_block_statements_end_with_a_semicolon(self):
        with pytest.raises(ValueError):
            parse_statement("CREATE TASK t AS BEGIN INSERT INTO b SELECT * FROM a END")


# ── Lowering ────────────────────────────────────────────────────────────


class TestLowering:
    def test_begin_end_block_lowers_to_a_script(self):
        task = parse_create_task(SCRIPT)
        assert task.handler == "sql_script"
        assert task.language == "sql"
        assert task.statements == (
            "UPDATE stock SET level = CASE WHEN qty > 0 THEN 'ok' ELSE 'empty' END WHERE id > 0",
            "INSERT INTO stock_log SELECT * FROM stock",
        )
        assert task.handler_config == {
            "version": 1,
            "language": "sql",
            "statements": list(task.statements),
        }
        assert task.body.endswith("INSERT INTO stock_log SELECT * FROM stock;")
        assert (task.database_name, task.schema_name, task.name) == ("sales", "etl", "refresh")

    def test_dollar_body_lowers_to_a_script(self):
        task = parse_create_task(
            "CREATE TASK d.s.t AS $$\nUPDATE a SET x = 1 WHERE id = 1;\n"
            "INSERT INTO b SELECT ';' FROM a;\n$$"
        )
        assert task.handler == "sql_script"
        assert task.statements == (
            "UPDATE a SET x = 1 WHERE id = 1",
            "INSERT INTO b SELECT ';' FROM a",
        )

    def test_language_sql_is_the_default(self):
        task = parse_create_task("CREATE TASK d.s.t LANGUAGE SQL AS $$ INSERT INTO b SELECT 1 $$")
        assert task.handler == "sql_script"

    def test_single_statement_stays_a_native_task(self):
        task = parse_create_task("CREATE TASK d.s.t AS INSERT INTO b SELECT 1")
        assert task.handler == "sql"
        assert task.handler_config is None
        assert task.body == "INSERT INTO b SELECT 1"

    def test_python_task(self):
        task = parse_create_task(PYTHON)
        assert task.handler == "python"
        assert task.language == "python"
        assert task.schedule_kind == "cron"
        assert task.timezone == "Asia/Jakarta"
        assert task.packages == ("scikit-learn==1.5.2", "tabulate")
        assert task.handler_config == {
            "version": 1,
            "language": "python",
            "runtime_version": "3.12",
            "packages": ["scikit-learn==1.5.2", "tabulate"],
        }
        assert task.body.startswith("import novaduck\n")
        assert "from a.default.b import c" in task.body

    def test_python_runtime_defaults(self):
        task = parse_create_task("CREATE TASK d.s.t LANGUAGE PYTHON AS $$\n  x = 1\n  print(x)\n$$")
        assert task.runtime_version == "3.12"
        assert task.body == "x = 1\nprint(x)"  # dedented

    def test_stage_reads_are_allowed_in_a_script(self):
        task = parse_create_task(
            "CREATE TASK d.s.t AS BEGIN INSERT INTO b SELECT * FROM @st/x.csv; END"
        )
        assert task.statements == ("INSERT INTO b SELECT * FROM @st/x.csv",)

    def test_stage_in_a_single_statement_points_to_a_block(self):
        with pytest.raises(TaskDDLError, match="BEGIN ... END"):
            parse_create_task("CREATE TASK d.s.t AS INSERT INTO b SELECT * FROM @st/x.csv")

    @pytest.mark.parametrize(
        ("sql", "message"),
        [
            ("CREATE TASK d.s.t AS BEGIN SET ROLE r; END", "cannot switch roles"),
            ("CREATE TASK d.s.t AS BEGIN EXECUTE TASK other; END", "cannot run other tasks"),
            ("CREATE TASK d.s.t AS BEGIN DROP TASK other; END", "cannot drop tasks"),
            (
                "CREATE TASK d.s.t AS BEGIN CREATE TASK x AS INSERT INTO b SELECT 1; END",
                "cannot create tasks",
            ),
            ("CREATE TASK d.s.t AS BEGIN COMMIT; END", "cannot commit"),
            ("CREATE TASK d.s.t AS BEGIN DROP ROLE ACCOUNTADMIN; END", "ACCOUNTADMIN"),
            (
                "CREATE TASK d.s.t AS $$ INSERT INTO b SELECT * FROM FILES("
                "'aws.s3.access_key' = 'AKIA', 'aws.s3.secret_key' = 'x') $$",
                "credentials",
            ),
            ("CREATE TASK d.s.t AS $$   $$", "no statements"),
            ("CREATE TASK d.s.t AS $$ SELEC 1 $$", "not valid SQL"),
            ("CREATE TASK d.s.t LANGUAGE PYTHON AS $$\nfor x in\n$$", "syntax error on line 1"),
            ("CREATE TASK d.s.t LANGUAGE PYTHON AS $$\n\n$$", "no code"),
            (
                "CREATE TASK d.s.t LANGUAGE PYTHON PACKAGES = ('requests') AS $$\nx = 1\n$$",
                "approved package list",
            ),
            (
                "CREATE TASK d.s.t LANGUAGE PYTHON PACKAGES = ('tabulate', 'Tabulate') "
                "AS $$\nx = 1\n$$",
                "more than once",
            ),
            (
                "CREATE TASK d.s.t LANGUAGE PYTHON PACKAGES = ('tabulate>=1') AS $$\nx = 1\n$$",
                "name==version",
            ),
            (
                "CREATE TASK d.s.t LANGUAGE PYTHON RUNTIME_VERSION = '3.9' AS $$\nx = 1\n$$",
                "not available",
            ),
            (
                "CREATE TASK d.s.t PACKAGES = ('tabulate') AS INSERT INTO b SELECT 1",
                "need LANGUAGE",
            ),
            ("CREATE TASK d.s.t LANGUAGE PYTHON AS INSERT INTO b SELECT 1", "between \\$\\$"),
            ("CREATE TASK d.s.t LANGUAGE RUBY AS $$ x $$", "unknown task LANGUAGE"),
            (
                "CREATE TASK d.s.t PACKAGES = ('tabulate') LANGUAGE PYTHON AS $$\nx = 1\n$$",
                "order",
            ),
        ],
    )
    def test_rejections(self, sql, message):
        with pytest.raises(TaskDDLError, match=message):
            parse_create_task(sql)

    def test_script_size_is_bounded(self):
        body = " ".join("INSERT INTO b SELECT 1;" for _ in range(101))
        with pytest.raises(TaskDDLError, match="at most 100"):
            parse_create_task(f"CREATE TASK d.s.t AS BEGIN {body} END")

    def test_execute_task_target(self):
        target = parse_execute_task("EXECUTE TASK refresh", database="sales", schema="etl")
        assert (target.database_name, target.schema_name, target.name) == (
            "sales",
            "etl",
            "refresh",
        )
        assert target.qualified_name == "sales.etl.refresh"
        qualified = parse_execute_task("EXECUTE TASK a.b.c", database="x")
        assert qualified.qualified_name == "a.b.c"
        assert parse_execute_task("EXECUTE TASK t").schema_name == "default"
        with pytest.raises(TaskDDLError):
            parse_execute_task("SELECT 1")


# ── Statement splitting ─────────────────────────────────────────────────


class TestSplitting:
    def test_task_block_is_one_statement(self):
        parts = split_sql_statements(f"SELECT 1; {SCRIPT}; EXECUTE TASK sales.etl.refresh")
        assert parts == ["SELECT 1", SCRIPT, "EXECUTE TASK sales.etl.refresh"]

    def test_submit_task_block_stays_whole_for_a_clear_refusal(self):
        sql = "SUBMIT TASK x AS BEGIN INSERT INTO b SELECT 1; END; SELECT 2"
        assert split_sql_statements(sql) == [
            "SUBMIT TASK x AS BEGIN INSERT INTO b SELECT 1; END",
            "SELECT 2",
        ]

    def test_dollar_body_is_one_statement(self):
        parts = split_sql_statements(f"{PYTHON};\nSELECT a$$b; SELECT 3")
        assert parts == [PYTHON, "SELECT a$$b", "SELECT 3"]

    def test_unterminated_dollar_body_swallows_the_rest(self):
        assert split_sql_statements("CREATE FUNCTION f() AS $$ a; b") == [
            "CREATE FUNCTION f() AS $$ a; b"
        ]

    @pytest.mark.parametrize(
        ("sql", "count"),
        [
            ("BEGIN; INSERT INTO t VALUES (1); COMMIT", 3),
            ("SELECT 1 AS begin; SELECT 2", 2),
            ("SELECT CASE WHEN a THEN 1 END; SELECT 2", 2),
            ("CREATE TASK t AS INSERT INTO b SELECT 1; SELECT 9", 2),
            ("/* CREATE TASK t AS BEGIN */ SELECT 1; SELECT 2", 2),
        ],
    )
    def test_other_statements_split_as_before(self, sql, count):
        assert len(split_sql_statements(sql)) == count

    def test_default_schema_rewrite_leaves_dollar_bodies(self):
        from app.modules.query.service import QueryService

        normalize = QueryService._normalize_default_schema_qualification
        assert normalize(PYTHON) == PYTHON
        assert normalize("SELECT * FROM a.default.b") == "SELECT * FROM a.b"


# ── SQL frontend ────────────────────────────────────────────────────────


def _analyze(sql: str):
    statement = ast_builders.build(parse_statement(sql))
    return statement, semantics_registry.analyze(statement)


class TestFrontend:
    def test_execute_task_is_a_nova_action(self):
        statement, analysis = _analyze("EXECUTE TASK sales.etl.refresh")
        assert isinstance(statement, ast.ExecuteTaskStatement)
        assert analysis.effects.writes_data and analysis.effects.writes_metadata
        assert not analysis.requires_confirmation
        target = semantics_registry.validate(statement, None)
        payload = action_payload(ActionKind.EXECUTE_TASK, 0, target)
        assert payload == ExecuteTaskPayload(0, "refresh", "sales", "etl")

    def test_script_that_updates_is_confirmed_at_creation(self):
        _statement, analysis = _analyze(SCRIPT)
        assert analysis.requires_confirmation
        assert analysis.effects.updates_rows and analysis.effects.writes_metadata

    def test_insert_only_script_needs_no_confirmation(self):
        _statement, analysis = _analyze("CREATE TASK d.s.t AS BEGIN INSERT INTO b SELECT 1; END")
        assert not analysis.requires_confirmation
        assert analysis.effects.writes_data

    def test_single_statement_task_keeps_its_effects(self):
        _statement, analysis = _analyze("CREATE TASK d.s.t AS INSERT INTO b SELECT 1")
        assert analysis.effects.writes_metadata and not analysis.effects.writes_data

    def test_submit_task_refuses_nova_bodies(self):
        with pytest.raises(SemanticError, match="CREATE TASK"):
            ast_builders.build(
                parse_statement("SUBMIT TASK x AS BEGIN INSERT INTO b SELECT 1; END")
            )
        with pytest.raises(SemanticError, match="CREATE TASK"):
            ast_builders.build(parse_statement("SUBMIT TASK x AS $$ INSERT INTO b SELECT 1 $$"))
        native = ast_builders.build(parse_statement("SUBMIT TASK x AS INSERT INTO b SELECT 1"))
        assert isinstance(native, ast.NativeStatement)


# ── EXECUTE TASK resolution and enqueue ────────────────────────────────


class _Repo:
    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self.create_graph_run = AsyncMock(
            side_effect=lambda row: {**row, "id": "run-1", "started_at": None}
        )
        self.list_active_graph_runs = AsyncMock(return_value=[])

    def task(self, name: str, handler: str = "sql", role: str = "analyst") -> None:
        self.tasks.append(
            {
                "id": f"id_{name}",
                "name": name,
                "database_name": "sales",
                "schema_name": "etl",
                "created_by": "alice",
                "owner_role": role,
                "handler": handler,
                "schedule_kind": "manual",
                "overlap_policy": "skip",
            }
        )

    def edge(self, graph: str, parent: str, child: str) -> None:
        self.edges.append(
            {
                "id": f"{parent}-{child}",
                "graph_id": graph,
                "parent_task": parent,
                "child_task": child,
                "edge_kind": "after",
            }
        )

    async def list_all_edges(self):
        return list(self.edges)

    async def list_tasks(self):
        return list(self.tasks)


USER = {
    "username": "alice",
    "active_role": "analyst",
    "assigned_roles": ["analyst"],
    "session_id": "sess-1",
    "encrypted_password": "e",
}


@pytest.fixture
def graph_repo(monkeypatch):
    sessions = {"sess-1": {"username": "alice", "active_role": "analyst"}}

    async def get_session(session_id):
        return sessions.get(session_id)

    monkeypatch.setattr(session_store, "get", get_session)
    repo = _Repo()
    repo.task("extract")
    repo.task("load", handler="sql_script")
    repo.task("score", handler="python")
    repo.task("solo", handler="python")
    repo.edge("sales.etl.extract", "extract", "load")
    repo.edge("sales.etl.extract", "load", "score")
    monkeypatch.setattr(orch_router, "_repository", repo)
    monkeypatch.setattr(orch_router, "verify_active_role", AsyncMock())
    monkeypatch.setattr(orch_router, "write_audit_log", AsyncMock())
    monkeypatch.setattr(orch_router, "_publish_manual_run", AsyncMock())
    return repo


class TestExecuteTask:
    async def test_root_runs_its_graph(self, graph_repo):
        graph = await orch_router.resolve_execute_target("extract", "sales", "etl", USER)
        assert graph == "sales.etl.extract"
        run = await orch_router.enqueue_manual_run(graph, USER)
        saved = graph_repo.create_graph_run.call_args.args[0]
        assert saved["trigger_type"] == "manual"
        assert saved["execution_session_id"] == "sess-1"
        assert run["id"] == "run-1"
        published = orch_router._publish_manual_run.call_args.args[1]
        assert sorted(published) == ["id_extract", "id_load", "id_score"]

    async def test_standalone_task_runs(self, graph_repo):
        assert await orch_router.resolve_execute_target("solo", "sales", "etl", USER) == (
            "sales.etl.solo"
        )

    async def test_child_task_points_to_the_root(self, graph_repo):
        with pytest.raises(HTTPException) as caught:
            await orch_router.resolve_execute_target("score", "sales", "etl", USER)
        assert caught.value.status_code == 409
        assert "runs after load" in caught.value.detail
        assert "EXECUTE TASK sales.etl.extract" in caught.value.detail

    async def test_unknown_and_unowned_tasks_look_the_same(self, graph_repo):
        for name, user in (
            ("nope", USER),
            ("solo", {**USER, "active_role": "other", "assigned_roles": ["other"]}),
        ):
            with pytest.raises(HTTPException) as caught:
                await orch_router.resolve_execute_target(name, "sales", "etl", user)
            assert caught.value.status_code == 404

    async def test_run_needs_the_owner_role_and_a_session(self, graph_repo):
        with pytest.raises(HTTPException) as caught:
            await orch_router.enqueue_manual_run("sales.etl.solo", {**USER, "session_id": None})
        assert caught.value.status_code == 403

    async def test_skip_policy_refuses_an_overlapping_run(self, graph_repo):
        graph_repo.list_active_graph_runs.return_value = [{"id": "busy"}]
        with pytest.raises(HTTPException) as caught:
            await orch_router.enqueue_manual_run("sales.etl.solo", USER)
        assert caught.value.status_code == 409

    async def test_adapter_returns_the_queued_run(self, graph_repo):
        from app.sql_frontend.execution.adapters import FeatureAdapters

        adapters = object.__new__(FeatureAdapters)
        result = await adapters._execute_execute_task(
            sql="EXECUTE TASK refresh",
            normalized_sql="EXECUTE TASK refresh",
            username="alice",
            encrypted_password="e",
            role="analyst",
            database="sales",
            session_id="sess-1",
            file_id=None,
            security_context_version=1,
            validated_frontend=ExecuteTaskPayload(0, "extract", "sales", "etl"),
        )
        assert result.error is None
        assert result.columns == ["task", "run_id", "state", "trigger"]
        assert result.rows == [["sales.etl.extract", "run-1", "pending", "manual"]]

    async def test_adapter_reports_refusals_as_errors(self, graph_repo):
        from app.sql_frontend.execution.adapters import FeatureAdapters

        adapters = object.__new__(FeatureAdapters)
        common = dict(
            sql="EXECUTE TASK x",
            normalized_sql="EXECUTE TASK x",
            username="alice",
            encrypted_password="e",
            role="analyst",
            session_id="sess-1",
            file_id=None,
            security_context_version=1,
        )
        child = await adapters._execute_execute_task(
            **common,
            database="sales",
            validated_frontend=ExecuteTaskPayload(0, "load", "sales", "etl"),
        )
        assert "starts a graph at its root" in (child.error or "")
        no_db = await adapters._execute_execute_task(
            **common,
            database=None,
            validated_frontend=ExecuteTaskPayload(0, "x", None, None),
        )
        assert "needs a database" in (no_db.error or "")
        proxy = await adapters._execute_execute_task(
            **{**common, "session_id": "random-proxy-connection-id"},
            database="sales",
            validated_frontend=ExecuteTaskPayload(0, "extract", "sales", "etl"),
        )
        assert "Nova sign-in" in (proxy.error or "")
        graph_repo.create_graph_run.assert_not_awaited()

    def test_script_and_python_tasks_follow_the_owner_role(self):
        for handler in ("sql", "sql_script", "python"):
            task = {"handler": handler, "owner_role": "analyst", "created_by": "bob"}
            assert owns_task(task, {"username": "alice", "active_role": "analyst"})
        internal = {"handler": "intelligence.monitor", "owner_role": "analyst", "created_by": "bob"}
        assert not owns_task(internal, {"username": "alice", "active_role": "analyst"})

    def test_body_shape(self):
        shape = orch_router._body_shape
        assert shape({"handler": "sql"})["statement_count"] == 1
        assert (
            shape(
                {"handler": "sql_script", "handler_config": json.dumps({"statements": ["a", "b"]})}
            )["statement_count"]
            == 2
        )
        python = shape(
            {
                "handler": "python",
                "handler_config": {"packages": ["tabulate"], "runtime_version": "3.12"},
            }
        )
        assert python["language"] == "python"
        assert python["packages"] == ["tabulate"]
        assert shape({"handler": "python", "handler_config": "not json"})["packages"] == []


# ── Script runner ───────────────────────────────────────────────────────


class _Cursor:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.log.append(sql)

    async def fetchone(self):
        return {"nova_active_role": "analyst"}

    async def fetchall(self):
        return []


class _Conn:
    def __init__(self):
        self.log: list[str] = []

    def cursor(self, cursor_class=None):
        return _Cursor(self.log)


class _Executor:
    def __init__(self):
        self.conn = _Conn()
        self.owners: list[tuple[str, str | None]] = []

    def _owner_conn(self, owner, session_id=None):
        executor = self

        class _Ctx:
            async def __aenter__(self_inner):
                executor.owners.append((owner, session_id))
                return executor.conn

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    async def _prepare_task_session(self, cur, spec):
        await cur.execute(f"SET ROLE {spec.active_role}")
        await cur.execute(f"USE `{spec.database}`")


def _script_task(statements):
    return {
        "id": "t1",
        "name": "refresh",
        "handler": "sql_script",
        "handler_config": json.dumps({"statements": statements}),
        "definition": "",
    }


SPEC = TaskSpec(name="refresh", body="", database="sales", active_role="analyst", schema="etl")


class TestScriptRunner:
    async def test_runs_every_statement_in_order(self, monkeypatch):
        from app.modules.query import service as query_module

        calls = []

        async def execute_statements(**kwargs):
            calls.append(kwargs)
            return [QueryResult(affected_rows=2), QueryResult(affected_rows=3)]

        monkeypatch.setattr(query_module.query_service, "execute_statements", execute_statements)
        executor = _Executor()
        summary = await script_task.run_sql_script(
            _script_task(["UPDATE a SET x = 1 WHERE id > 0", "INSERT INTO b SELECT 1"]),
            SPEC,
            "svc_etl",
            executor,
        )
        assert summary == "2 statements ran; 5 rows affected"
        call = calls[0]
        assert call["sql"] == "UPDATE a SET x = 1 WHERE id > 0;\nINSERT INTO b SELECT 1"
        assert call["connection"] is executor.conn
        assert call["confirm_destructive"] is True
        assert call["role"] == "analyst" and call["database"] == "sales"
        assert executor.conn.log == ["SET ROLE analyst", "USE `sales`"]
        assert executor.owners == [("svc_etl", None)]

    async def test_stops_at_the_first_failure(self, monkeypatch):
        from app.modules.query import service as query_module

        async def execute_statements(**kwargs):
            return [QueryResult(affected_rows=1), QueryResult(error="Access denied on b")]

        monkeypatch.setattr(query_module.query_service, "execute_statements", execute_statements)
        with pytest.raises(NodeExecutionError) as caught:
            await script_task.run_sql_script(
                _script_task(
                    [
                        "UPDATE a SET x = 1 WHERE id > 0",
                        "INSERT INTO b SELECT 1",
                        "INSERT INTO c SELECT 1",
                    ]
                ),
                SPEC,
                "alice",
                _Executor(),
            )
        assert str(caught.value) == (
            "Statement 2 of 3 failed: Access denied on b (statements 1-1 ran and are kept)"
        )

    async def test_manual_run_uses_the_session(self, monkeypatch):
        from app.modules.query import service as query_module

        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr(session_store, "_redis", redis)
        session_id = await session_store.create(
            "alice",
            encrypt_password("pw"),
            ["analyst"],
            default_role="analyst",
            active_role="analyst",
        )
        seen = {}

        async def execute_statements(**kwargs):
            seen.update(kwargs)
            return [QueryResult()]

        monkeypatch.setattr(query_module.query_service, "execute_statements", execute_statements)
        executor = _Executor()
        spec = TaskSpec(
            name="refresh",
            body="",
            database="sales",
            active_role="analyst",
            execution_session_id=session_id,
        )
        await script_task.run_sql_script(
            _script_task(["INSERT INTO b SELECT 1"]), spec, "alice", executor
        )
        assert executor.owners == [("alice", session_id)]
        assert seen["session_id"] == session_id and seen["encrypted_password"]
        await redis.aclose()

    async def test_heartbeat_while_a_script_runs(self, monkeypatch):
        from app.modules.query import service as query_module

        monkeypatch.setattr(script_task, "HEARTBEAT_SECONDS", 0.01)

        async def execute_statements(**kwargs):
            await asyncio.sleep(0.05)
            return [QueryResult()]

        monkeypatch.setattr(query_module.query_service, "execute_statements", execute_statements)
        beats = AsyncMock()
        await script_task.run_sql_script(
            _script_task(["INSERT INTO b SELECT 1"]), SPEC, "alice", _Executor(), heartbeat=beats
        )
        assert beats.await_count >= 1

    async def test_falls_back_to_the_definition(self):
        task = {"definition": "INSERT INTO a SELECT 1;\nINSERT INTO b SELECT 2;"}
        assert script_task.script_statements(task) == [
            "INSERT INTO a SELECT 1",
            "INSERT INTO b SELECT 2",
        ]

    async def test_needs_a_database(self):
        with pytest.raises(NodeExecutionError, match="no database"):
            await script_task.run_sql_script(
                _script_task(["INSERT INTO b SELECT 1"]),
                TaskSpec(name="x", body="", active_role="analyst"),
                "alice",
                _Executor(),
            )


# ── Python runner ───────────────────────────────────────────────────────


class FakeSandbox:
    """execd + nova_runtime, enough for a task run."""

    def __init__(self) -> None:
        self.created: list[SandboxSpec] = []
        self.killed: list[str] = []
        self.ran: list[str] = []
        self.files: dict[str, bytes] = {}
        self.allowed: list[list[str]] = []
        self.denied: list[list[str]] = []
        self.interrupted: list[str] = []
        self.install_result: dict[str, Any] = {"ok": True}
        self.nova_attached = True

    async def create(self, spec):
        self.created.append(spec)
        return SandboxHandle(sandbox_id=f"sb-{len(self.created)}", context_id="ctx")

    async def run(self, handle, code, sink):
        self.ran.append(code)
        await sink(KernelEvent(type="init", text=f"k-{len(self.ran)}"))
        call = re.search(r"__nova__\.(\w+)\((.*)\)$", code, re.S)
        if call:
            name = call.group(1)
            arg = json.loads(eval(call.group(2))) if call.group(2) else {}  # noqa: S307
            if name == "bootstrap":
                payload = {"ready": True, "nova": {"attached": self.nova_attached}}
            elif name == "install":
                await sink(KernelEvent(type="stdout", text="Collecting tabulate\n"))
                payload = dict(self.install_result)
                if payload.get("ok"):
                    payload["versions"] = {p["name"]: "1.0" for p in arg["packages"]}
            else:
                payload = {}
            await sink(KernelEvent(type="result", bundle={PAYLOAD: json.dumps(payload)}))
            return
        for line in code.splitlines():
            if line.startswith("print "):
                await sink(KernelEvent(type="stdout", text=line[6:] + "\n"))
            elif line.startswith("rpc "):
                request_id = line[4:]
                await sink(KernelEvent(type="stderr", text=f"\x1enova-rpc {request_id}\x1e\n"))
            elif line == "fail":
                await sink(
                    KernelEvent(
                        type="error",
                        error={
                            "name": "ValueError",
                            "value": "bad rows",
                            "traceback": [
                                "\x1b[31mTraceback\x1b[0m",
                                "  line 3",
                                "ValueError: bad rows",
                            ],
                        },
                    )
                )
            elif line == "hang":
                await asyncio.Event().wait()
        await sink(KernelEvent(type="complete"))

    async def interrupt(self, handle, kernel_execution_id):
        self.interrupted.append(kernel_execution_id)

    async def write_file(self, handle, path, content):
        self.files[path] = content

    async def read_file(self, handle, path):
        return self.files[path]

    async def allow_hosts(self, handle, hosts):
        self.allowed.append(hosts)

    async def deny_hosts(self, handle, hosts):
        self.denied.append(hosts)

    async def kill(self, sandbox_id):
        self.killed.append(sandbox_id)


@pytest.fixture
async def py_env(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(session_store, "_redis", redis)
    monkeypatch.setattr(settings, "OPENSANDBOX_API_KEY", "k")
    monkeypatch.setattr(settings, "NOTEBOOK_PROXY_ADDRESS", "0.250.250.254")
    from app.modules.notebooks.runtime import worker as notebook_worker

    async def catalogs(session):
        return [{"name": "default_catalog", "type": "Internal"}]

    monkeypatch.setattr(notebook_worker, "visible_catalogs", catalogs)
    session_id = await session_store.create(
        "alice",
        encrypt_password("pw"),
        ["analyst", "finance"],
        default_role="analyst",
        active_role="analyst",
    )
    yield SimpleNamespace(redis=redis, session_id=session_id, sandbox=FakeSandbox())
    await redis.aclose()


def _py_task(code: str, packages=()):
    return {
        "id": "t-py",
        "name": "score",
        "handler": "python",
        "definition": code,
        "handler_config": {
            "version": 1,
            "language": "python",
            "runtime_version": "3.12",
            "packages": list(packages),
        },
    }


def _manual(env) -> TaskSpec:
    return TaskSpec(
        name="score",
        body="",
        database="sales",
        active_role="analyst",
        execution_session_id=env.session_id,
    )


class TestPythonRunner:
    async def test_manual_run_prints_and_cleans_up(self, py_env):
        sandbox = py_env.sandbox
        output = await python_task.run_python_task(
            _py_task("print hello\nprint done", ["tabulate==0.9.0"]),
            _manual(py_env),
            "alice",
            _Executor(),
            provider=sandbox,
        )
        assert output == "[nova] installed tabulate 1.0\nhello\ndone\n"
        spec = sandbox.created[0]
        assert spec.runtime_id.startswith(TASK_RUNTIME_PREFIX)
        label = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?$")
        assert all(
            label.match(value) for value in (spec.runtime_id, spec.notebook_id, spec.owner_hash)
        )
        assert spec.egress == [("0.250.250.254", spec.egress[0][1])]
        assert sandbox.allowed == sandbox.denied == [["pypi.org", "files.pythonhosted.org"]]
        assert "tabulate" in sandbox.ran[1] and "==0.9.0" in sandbox.ran[1]
        assert sandbox.killed == ["sb-1"]
        credential = json.loads(next(iter(sandbox.files.values())))
        assert credential["user"] == "alice" and credential["token"]
        # The token is bound to the run's sandbox: gone with it.
        assert not await py_env.redis.keys(f"{data_tokens.TASK.subject_key_prefix}*")
        assert not await py_env.redis.keys(f"{data_tokens.TASK.bind_prefix}*")
        assert not await py_env.redis.keys(f"{data_tokens.TASK.token_prefix}*")

    async def test_token_verifies_only_while_the_run_lives(self, py_env, monkeypatch):
        seen = {}
        original = python_task._run_code

        async def checking(sandboxes, handle, identity, source, output, deadline, heartbeat):
            fields = await py_env.redis.hgetall(
                data_tokens.TASK.token_prefix
                + await py_env.redis.get(
                    data_tokens.TASK.bind_prefix + sandboxes.created[0].runtime_id
                )
            )
            binding = data_tokens.TokenBinding(
                token_id="x",
                session_id=fields["session_id"],
                username=fields["username"],
                active_role=fields["active_role"],
                security_context_version=int(fields["version"]),
                purpose="task",
                subject=fields["subject"],
            )
            seen["binding"] = binding
            seen["session"] = await data_tokens.verify_binding(binding)
            await original(sandboxes, handle, identity, source, output, deadline, heartbeat)

        monkeypatch.setattr(python_task, "_run_code", checking)
        await python_task.run_python_task(
            _py_task("print x"), _manual(py_env), "alice", _Executor(), provider=py_env.sandbox
        )
        assert seen["session"].active_role == "analyst"
        assert seen["binding"].notebook  # the proxy treats it as a sandbox connection
        with pytest.raises(data_tokens.BiCredentialError):
            await data_tokens.verify_binding(seen["binding"])

    async def test_python_error_fails_with_traceback_and_output(self, py_env):
        with pytest.raises(NodeExecutionError) as caught:
            await python_task.run_python_task(
                _py_task("print loading\nfail"),
                _manual(py_env),
                "alice",
                _Executor(),
                provider=py_env.sandbox,
            )
        message = str(caught.value)
        assert message.startswith("ValueError: bad rows\nTraceback")
        assert "--- output ---\nloading" in message
        assert "\x1b[" not in message
        assert py_env.sandbox.killed == ["sb-1"]

    async def test_role_changed_since_the_run_started(self, py_env):
        await session_store.set_active_role(py_env.session_id, "finance")
        with pytest.raises(NodeExecutionError, match="activate 'analyst'"):
            await python_task.run_python_task(
                _py_task("print x"), _manual(py_env), "alice", _Executor(), provider=py_env.sandbox
            )
        assert py_env.sandbox.created == []

    async def test_ended_session(self, py_env):
        spec = TaskSpec(
            name="score",
            body="",
            database="sales",
            active_role="analyst",
            execution_session_id="gone",
        )
        with pytest.raises(NodeExecutionError, match="sign in"):
            await python_task.run_python_task(
                _py_task("print x"), spec, "alice", _Executor(), provider=py_env.sandbox
            )

    async def test_package_removed_from_the_allowlist(self, py_env, monkeypatch):
        monkeypatch.setattr(settings, "NOTEBOOK_PACKAGE_ALLOWLIST", "polars")
        with pytest.raises(NodeExecutionError, match="cannot be installed"):
            await python_task.run_python_task(
                _py_task("print x", ["tabulate"]),
                _manual(py_env),
                "alice",
                _Executor(),
                provider=py_env.sandbox,
            )
        assert py_env.sandbox.created == []

    async def test_failed_install(self, py_env):
        py_env.sandbox.install_result = {
            "ok": False,
            "error": "pip could not install",
            "failed_imports": [{"module": "tabulate"}],
        }
        with pytest.raises(NodeExecutionError, match="could not import tabulate"):
            await python_task.run_python_task(
                _py_task("print x", ["tabulate"]),
                _manual(py_env),
                "alice",
                _Executor(),
                provider=py_env.sandbox,
            )
        assert py_env.sandbox.denied == [["pypi.org", "files.pythonhosted.org"]]
        assert py_env.sandbox.killed == ["sb-1"]

    async def test_nova_unreachable(self, py_env):
        py_env.sandbox.nova_attached = False
        with pytest.raises(NodeExecutionError, match="could not reach Nova data"):
            await python_task.run_python_task(
                _py_task("print x"), _manual(py_env), "alice", _Executor(), provider=py_env.sandbox
            )

    @pytest.mark.parametrize(
        ("setting", "message"),
        [("OPENSANDBOX_API_KEY", "not set up"), ("NOTEBOOK_PROXY_ADDRESS", "NOTEBOOK_PROXY")],
    )
    async def test_deployment_prerequisites(self, py_env, monkeypatch, setting, message):
        monkeypatch.setattr(settings, setting, "")
        with pytest.raises(NodeExecutionError, match=message):
            await python_task.run_python_task(
                _py_task("print x"), _manual(py_env), "alice", _Executor(), provider=py_env.sandbox
            )

    async def test_timeout_interrupts(self, py_env, monkeypatch):
        monkeypatch.setattr(settings, "TASK_PYTHON_TIMEOUT_SECONDS", 0)
        monkeypatch.setattr(python_task, "HEARTBEAT_SECONDS", 0.01)
        with pytest.raises(NodeExecutionError, match="did not finish"):
            await python_task.run_python_task(
                _py_task("hang"), _manual(py_env), "alice", _Executor(), provider=py_env.sandbox
            )
        assert py_env.sandbox.interrupted
        assert py_env.sandbox.killed == ["sb-1"]

    async def test_write_requests_reach_nova(self, py_env, monkeypatch):
        handled = []

        async def handle(provider, sandbox, session_id, request_id):
            handled.append((session_id, request_id))

        monkeypatch.setattr(python_task.rpc, "handle", handle)
        request = "a" * 32
        output = await python_task.run_python_task(
            _py_task(f"print before\nrpc {request}\nprint after"),
            _manual(py_env),
            "alice",
            _Executor(),
            provider=py_env.sandbox,
        )
        assert handled == [(py_env.session_id, request)]
        assert output == "before\nafter\n"

    async def test_scheduled_run_uses_a_delegated_session(self, py_env, monkeypatch):
        handled = []

        async def handle(provider, sandbox, session_id, request_id):
            from app.modules.query.service import delegated_connection_for

            handled.append((session_id, delegated_connection_for("svc_etl")))

        monkeypatch.setattr(python_task.rpc, "handle", handle)
        executor = _Executor()
        spec = TaskSpec(name="score", body="", database="sales", active_role="analyst")
        await python_task.run_python_task(
            _py_task("rpc " + "b" * 32), spec, "svc_etl", executor, provider=py_env.sandbox
        )
        assert executor.owners == [("svc_etl", None)]
        assert executor.conn.log[:1] == ["SET ROLE analyst"]
        session_id, connection = handled[0]
        assert session_id.startswith(data_tokens.DELEGATED)
        assert connection is executor.conn
        # The delegated session ends with the run.
        assert not await py_env.redis.keys(f"{data_tokens.DELEGATED_PREFIX}*")

    async def test_catalogs_on_a_delegated_connection(self):
        class Cur(_Cursor):
            async def fetchall(self):
                if self.log and self.log[-1] == "SHOW CATALOGS":
                    return [
                        {"Catalog": "default_catalog", "Type": "Internal"},
                        {"Catalog": "pg_shop", "Type": "Jdbc"},
                    ]
                return []

        class Conn(_Conn):
            def cursor(self, cursor_class=None):
                return Cur(self.log)

        identity = python_task._Identity(session=None, connection=Conn())  # type: ignore[arg-type]
        catalogs = await python_task._catalogs(identity)
        assert catalogs == [
            {"name": "default_catalog", "type": "Internal"},
            {"name": "pg_shop", "type": "Jdbc"},
        ]

    def test_output_keeps_the_tail(self, monkeypatch):
        monkeypatch.setattr(python_task, "OUTPUT_TAIL_CHARS", 10)
        out = python_task._Output()
        for _ in range(10):
            out.add("0123456789")
        out.add("\x1b[31mEND\x1b[0m")
        assert out.text() == "…3456789END"


# ── Worker dispatch ─────────────────────────────────────────────────────


class TestWorkerDispatch:
    @pytest.mark.parametrize("handler", ["sql_script", "python"])
    async def test_script_nodes_run_in_nova(self, monkeypatch, handler):
        from app.modules.task_orchestration import worker as worker_module

        calls = []

        async def runner(task, spec, owner, executor, heartbeat=None):
            calls.append((task["name"], spec.execution_session_id, owner))
            await heartbeat()
            return "2 statements ran"

        target = (
            (python_task, "run_python_task")
            if handler == "python"
            else (script_task, "run_sql_script")
        )
        monkeypatch.setattr(*target, runner)
        repo = SimpleNamespace(
            transition_task_run=AsyncMock(return_value=True),
            mark_task_run_heartbeat=AsyncMock(),
            mark_graph_run_heartbeat=AsyncMock(),
        )
        worker = worker_module.GraphRunWorker(repo, executor=object())
        monkeypatch.setattr(worker, "_audit", AsyncMock())
        job = worker_module.GraphRunJob(
            graph_run_id="g1",
            graph_id="sales.etl.t",
            trigger_type="manual",
            execution_session_id="s1",
            execution_user="alice",
            execution_role="analyst",
        )
        task = {
            "id": "t1",
            "name": "t",
            "handler": handler,
            "created_by": "alice",
            "owner_role": "analyst",
            "database_name": "sales",
            "schema_name": "etl",
        }
        await worker._execute_one(job, task, "run-1")
        assert calls == [("t", "s1", "alice")]
        kwargs = repo.transition_task_run.call_args.kwargs
        assert repo.transition_task_run.call_args.args[2] == "success"
        assert kwargs["output"] == "2 statements ran"
        repo.mark_task_run_heartbeat.assert_awaited_with("run-1")

    async def test_script_failure_fails_the_node(self, monkeypatch):
        from app.modules.task_orchestration import worker as worker_module

        async def runner(*args, **kwargs):
            raise NodeExecutionError("Statement 1 of 1 failed: denied")

        monkeypatch.setattr(script_task, "run_sql_script", runner)
        repo = SimpleNamespace(transition_task_run=AsyncMock(return_value=True))
        worker = worker_module.GraphRunWorker(repo, executor=object())
        monkeypatch.setattr(worker, "_audit", AsyncMock())
        job = worker_module.GraphRunJob(graph_run_id="g1", graph_id="x", trigger_type="schedule")
        task = {
            "id": "t1",
            "name": "t",
            "handler": "sql_script",
            "created_by": "svc",
            "owner_role": "analyst",
            "database_name": "sales",
        }
        await worker._execute_one(job, task, "run-1")
        assert repo.transition_task_run.call_args.args[2] == "failed"
        assert repo.transition_task_run.call_args.kwargs["error_message"] == (
            "Statement 1 of 1 failed: denied"
        )
        assert "output" not in repo.transition_task_run.call_args.kwargs


# ── Tokens, delegation, sandbox ownership ──────────────────────────────


class TestTokensAndSandboxes:
    async def test_delegated_session_round_trip(self, monkeypatch):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr(session_store, "_redis", redis)
        session_id = await data_tokens.create_delegated_session("svc", "analyst", 60)
        session = await data_tokens.resolve_session(session_id)
        assert (session.username, session.active_role, session.delegated) == (
            "svc",
            "analyst",
            True,
        )
        assert 0 < await redis.ttl(data_tokens.DELEGATED_PREFIX + session_id.split(":")[1]) <= 60
        await data_tokens.end_delegated_session(session_id)
        with pytest.raises(data_tokens.BiCredentialError):
            await data_tokens.resolve_session(session_id)
        await redis.aclose()

    async def test_superset_delegation_still_uses_its_ttl(self, monkeypatch):
        from app.modules.superset import tokens as superset_tokens

        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr(session_store, "_redis", redis)
        monkeypatch.setattr(settings, "SUPERSET_DELEGATION_TTL_SECONDS", 120)
        session_id = await superset_tokens.create_delegated_session("ana", "bi_role")
        key = data_tokens.DELEGATED_PREFIX + session_id.split(":")[1]
        assert 60 < await redis.ttl(key) <= 120
        await redis.aclose()

    def test_task_purpose_is_a_sandbox_purpose(self):
        assert data_tokens.PURPOSES["task"] is data_tokens.TASK
        binding = data_tokens.TokenBinding("i", "s", "u", "r", 1, purpose="task")
        assert binding.notebook
        assert not data_tokens.TokenBinding("i", "s", "u", "r", 1, purpose="bi").notebook

    async def test_notebook_reconciler_leaves_task_sandboxes(self, monkeypatch):
        from app.modules.notebooks.runtime import state
        from app.modules.notebooks.runtime.worker import NotebookRuntimeWorker

        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr(session_store, "_redis", redis)

        class Provider:
            killed: list[str] = []

            async def list_runtime_sandboxes(self):
                return {"sb-task": f"{TASK_RUNTIME_PREFIX}abc", "sb-orphan": "rt-gone"}

            async def kill(self, sandbox_id):
                self.killed.append(sandbox_id)

        monkeypatch.setattr(state, "live_runtimes", AsyncMock(return_value=[]))
        provider = Provider()
        await NotebookRuntimeWorker(provider, worker_id="w")._reconcile(startup=True)
        assert provider.killed == ["sb-orphan"]
        await redis.aclose()


# ── DROP TASK ───────────────────────────────────────────────────────────


class _DropRepo:
    def __init__(self) -> None:
        def task(name, role="analyst"):
            return {
                "id": f"id_{name}",
                "name": name,
                "database_name": "sales",
                "schema_name": "etl",
                "owner_role": role,
                "created_by": "bob",
                "handler": "sql_script",
            }

        self.tasks = {name: task(name) for name in ("root", "child", "solo")}
        self.tasks["theirs"] = task("theirs", role="finance")
        self.edges = [
            {
                "id": "e1",
                "graph_id": "sales.etl.root",
                "parent_task": "root",
                "child_task": "child",
                "edge_kind": "after",
            }
        ]
        self.active: dict[str, list] = {}
        self.deleted_edges: list[str] = []
        self.deleted_tasks: list[str] = []

    async def find_task(self, name, database_name, schema_name):
        task = self.tasks.get(name)
        if task and (task["database_name"], task["schema_name"]) == (database_name, schema_name):
            return task
        return None

    async def list_all_edges(self):
        return list(self.edges)

    async def list_active_graph_runs(self, graph_id):
        return self.active.get(graph_id, [])

    async def delete_edge(self, edge_id):
        self.deleted_edges.append(edge_id)

    async def delete_task(self, task_id):
        self.deleted_tasks.append(task_id)


@pytest.fixture
def drop_repo(monkeypatch):
    from app.modules.task_orchestration import lowering

    repo = _DropRepo()
    monkeypatch.setattr(lowering, "task_orchestration_repository", repo)
    return repo


ANALYST = {"username": "alice", "active_role": "analyst"}


class TestDropTask:
    def test_target_is_scoped_like_create_task(self):
        from app.modules.task_orchestration.ddl import parse_drop_task

        target = parse_drop_task("DROP TASK IF EXISTS t FORCE", database="sales", schema="etl")
        assert (target.qualified_name, target.if_exists, target.force) == (
            "sales.etl.t",
            True,
            True,
        )
        assert parse_drop_task("DROP TASK a.b.c").qualified_name == "a.b.c"

    def test_keeps_the_engine_statement_effects(self):
        statement, analysis = _analyze("DROP TASK sales.etl.t")
        assert isinstance(statement, ast.DropTaskStatement)
        assert analysis.requires_confirmation and analysis.effects.drops_objects

    async def test_drops_a_child_and_its_edges(self, drop_repo):
        from app.modules.task_orchestration.lowering import drop_nova_task

        dropped = await drop_nova_task("child", "sales", "etl", ANALYST)
        assert dropped["id"] == "id_child"
        assert drop_repo.deleted_edges == ["e1"] and drop_repo.deleted_tasks == ["id_child"]

    async def test_refuses_a_task_others_depend_on(self, drop_repo):
        from app.modules.task_orchestration.lowering import TaskLoweringError, drop_nova_task

        with pytest.raises(TaskLoweringError, match="child run after or finalize root"):
            await drop_nova_task("root", "sales", "etl", ANALYST)
        assert drop_repo.deleted_tasks == []

    async def test_refuses_another_roles_task(self, drop_repo):
        from app.modules.task_orchestration.lowering import TaskLoweringError, drop_nova_task

        with pytest.raises(TaskLoweringError, match="activate that role"):
            await drop_nova_task("theirs", "sales", "etl", ANALYST)

    async def test_refuses_while_a_run_is_active(self, drop_repo):
        from app.modules.task_orchestration.lowering import TaskLoweringError, drop_nova_task

        drop_repo.active["sales.etl.solo"] = [{"id": "r1"}]
        with pytest.raises(TaskLoweringError, match="run in progress"):
            await drop_nova_task("solo", "sales", "etl", ANALYST)

    async def test_unknown_name_is_not_a_nova_task(self, drop_repo):
        from app.modules.task_orchestration.lowering import drop_nova_task

        assert await drop_nova_task("native_tpl", "sales", "etl", ANALYST) is None

    async def _run(self, sql, monkeypatch):
        from app.sql_frontend.execution.adapters import FeatureAdapters
        from app.sql_frontend.planning.execution import DropTaskPayload, NovaActionPlan

        adapters = object.__new__(FeatureAdapters)
        audits: list[dict] = []

        async def audit(**kwargs):
            audits.append(kwargs)

        adapters._audit = audit  # type: ignore[method-assign]
        engine = AsyncMock(return_value=QueryResult(columns=["Status"], rows=[["OK"]]))
        adapters.execute_engine = engine  # type: ignore[method-assign]
        statement, analysis = _analyze(sql)
        target = semantics_registry.validate(
            statement, SimpleNamespace(database="sales", schema="etl")
        )
        plan = NovaActionPlan(
            ActionKind.DROP_TASK,
            action_payload(ActionKind.DROP_TASK, 0, target),
            analysis.effects,
            True,
        )
        assert isinstance(plan.payload, DropTaskPayload)
        context = SimpleNamespace(
            statements={0: statement},
            username="alice",
            role="analyst",
            session_id="s",
            file_id=None,
        )
        return await adapters.drop_task(plan, context), engine, audits

    async def test_adapter_drops_a_nova_task(self, drop_repo, monkeypatch):
        result, engine, audits = await self._run("DROP TASK solo", monkeypatch)
        assert result.error is None and result.rows[0][:2] == ["id_solo", "solo"]
        engine.assert_not_awaited()
        assert audits[0]["status"] == "SUCCESS" and audits[0]["object_name"] == "sales.etl.solo"

    async def test_adapter_reports_a_refusal(self, drop_repo, monkeypatch):
        result, engine, audits = await self._run("DROP TASK root", monkeypatch)
        assert "drop them first" in (result.error or "")
        assert audits[0]["status"] == "ERROR"
        engine.assert_not_awaited()

    async def test_adapter_forwards_engine_tasks(self, drop_repo, monkeypatch):
        result, engine, _audits = await self._run(
            "DROP TASK IF EXISTS native_tpl FORCE", monkeypatch
        )
        sent = engine.await_args.args[0]
        assert sent.engine_sql == "DROP TASK IF EXISTS native_tpl FORCE"
        assert sent.requires_confirmation and result.rows == [["OK"]]
