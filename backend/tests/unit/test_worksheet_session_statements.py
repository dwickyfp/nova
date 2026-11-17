"""Worksheet session: settings, user variables and transactions on one held connection.

A worksheet script that contains ``SET``, ``SET @var``, ``BEGIN``, ``COMMIT`` or
``ROLLBACK`` (or a run of a worksheet that remembers settings) runs every
statement on one connection opened as the caller, through the normal
``QueryService.execute`` path. Settings and variables a run leaves behind are
remembered per sign-in session and worksheet file and applied again at the next
run of that worksheet only. Callers that already hold a connection (the MySQL
proxy, task runs) keep their own session and are not changed.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from app.core.security import decrypt_password, encrypt_password
from app.modules.query import worksheet_session
from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService, delegated_connection
from app.modules.query.worksheet_session import (
    FAILED_TRANSACTION_ROLLED_BACK,
    OPEN_TRANSACTION_ROLLED_BACK,
    WorksheetState,
    is_worksheet_connection,
    variable_literal,
)
from app.sql_frontend.analysis.validation import (
    SESSION_SETTINGS_UNAVAILABLE,
    TRANSACTIONS_UNAVAILABLE,
)
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.errors import CapabilityUnsupportedError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.session import (
    GLOBAL_AND_SESSION_MIXED,
    redact_user_variable_text,
    redact_user_variable_values,
    session_statement,
    setting_statement,
)


def _statement(sql):
    return ast_builders.build(parse_statement(sql))


# --- Classification -----------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("BEGIN", "begin"),
        ("BEGIN WORK", "begin"),
        ("START TRANSACTION", "begin"),
        ("START TRANSACTION WITH CONSISTENT SNAPSHOT", "begin"),
        ("COMMIT", "end"),
        ("COMMIT WORK", "end"),
        ("ROLLBACK", "end"),
        ("SET query_timeout = 600", "set"),
        ("SET SESSION query_timeout = 600", "set"),
        ("SET LOCAL query_timeout = 600", "set"),
        ("SET @@query_timeout = 600", "set"),
        ("SET @@session.query_timeout = 600", "set"),
        ("SET time_zone = 'Asia/Shanghai'", "set"),
        ("SET query_mem_limit = 10 * 1024 * 1024 * 1024", "set"),
        ("set sql_mode = 'PIPES_AS_CONCAT'", "set"),
        ("SET enable_profile = true", "set"),
        ("SET dynamic_overwrite = true", "set"),
        ("SET @v1=1, @v2=2", "set"),
        ("SET @threshold = 5", "set"),
        ("SET NAMES utf8mb4", "set"),
        ("SET TRANSACTION READ ONLY", "set"),
        ("SET GLOBAL query_timeout = 600", None),
        ("SET @@global.query_timeout = 600", None),
        ("SELECT 1", None),
        ("INSERT INTO t VALUES (1)", None),
        ("USE analytics", None),
        ("SET CATALOG default_catalog", None),
        ("SELECT /*+ SET_VAR(query_timeout = 600) */ 1", None),
        ("SET PASSWORD = PASSWORD('x')", None),
    ],
)
def test_session_statement_kinds(sql, kind):
    assert session_statement(_statement(sql)).kind == kind


def test_setting_changes_keep_the_written_assignment_and_default_clears():
    session = session_statement(
        _statement("SET time_zone = 'Asia/Shanghai', @@session.query_timeout = 60, x = DEFAULT")
    )
    assert [(change.name, change.assignment) for change in session.settings] == [
        ("time_zone", "time_zone = 'Asia/Shanghai'"),
        ("query_timeout", "@@session.query_timeout = 60"),
        ("x", None),
    ]


def test_user_variable_names_are_collected_once():
    session = session_statement(_statement("SET @v1 = 1, @V1 = 2, @v2 = 'x'"))
    assert session.user_variables == ("v1", "v2")


@pytest.mark.parametrize(
    "sql",
    [
        "SET GLOBAL query_timeout = 600, query_mem_limit = 0",
        "SET query_mem_limit = 0, GLOBAL query_timeout = 600",
        "SET @@global.query_timeout = 600, @v = 1",
    ],
)
def test_one_set_cannot_mix_global_and_session_items(sql):
    with pytest.raises(CapabilityUnsupportedError) as error:
        session_statement(_statement(sql))
    assert str(error.value) == GLOBAL_AND_SESSION_MIXED


# --- Redaction ----------------------------------------------------------------


def test_user_variable_values_are_redacted_for_audit():
    statement = _statement("SET @token = 'secret-value', @n = (SELECT 42), query_timeout = 5")
    redacted = redact_user_variable_values(statement)
    assert redacted == "SET @token = ?, @n = ?, query_timeout = 5"
    assert "secret" not in redacted and "42" not in redacted
    assert redact_user_variable_values(_statement("SET query_timeout = 5")) is None
    assert redact_user_variable_text("SET @t = 'secret'") == "SET @t = ?"
    assert redact_user_variable_text("SET @t = 'secret' garbage (") == "SET @... = ?"
    assert redact_user_variable_text("SELECT 'secret'") == "SELECT 'secret'"


def test_remembered_setting_is_rechecked_before_it_is_applied():
    assert setting_statement("time_zone = 'Asia/Shanghai'") is not None
    for assignment in (
        "time_zone = 'UTC'; DROP TABLE t",
        "@v = 1",
        "GLOBAL query_timeout = 5",
        "a = 1, b = 2",
        "sql_dialect = 'trino'",
    ):
        with pytest.raises(ValueError):
            setting_statement(assignment)


# --- User variable literals ---------------------------------------------------


@pytest.mark.parametrize(
    ("value", "type_name", "literal"),
    [
        (None, "varchar", None),
        (3, "tinyint", "3"),
        (1, "boolean", "TRUE"),
        (0, "boolean", "FALSE"),
        (1.5, "double", "1.5"),
        ("it's", "varchar", "'it''s'"),
        ("a\\b", "varchar", "'a\\\\b'"),
        ('{"a": 1}', "json", "parse_json('{\"a\": 1}')"),
        ("170141183460469231731687303715884105727", "largeint",
         "170141183460469231731687303715884105727"),
    ],
)
def test_variable_literals(value, type_name, literal):
    assert variable_literal(value, type_name) == literal


@pytest.mark.parametrize(
    ("value", "type_name"), [("[1,2]", "array<tinyint>"), (float("nan"), "double")]
)
def test_values_without_a_literal_are_not_kept(value, type_name):
    with pytest.raises(worksheet_session._NotKept):
        variable_literal(value, type_name)


# --- Held-connection runs -----------------------------------------------------


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self._row = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql):
        self.connection.log.append(("setup", self.connection.number, sql))
        if sql == "ROLLBACK":
            self.connection.rolled_back = True
        if sql.startswith("SELECT @"):
            row = []
            for name in self.connection.engine.read_back_names(sql):
                value, type_name = self.connection.engine.variables.get(name, (None, "varchar"))
                row += [value, type_name]
            self._row = tuple(row)

    async def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, engine, number):
        self.engine = engine
        self.number = number
        self.log = engine.log
        self.rolled_back = False
        self.closed = False

    def cursor(self, *args):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class FakeEngine:
    """Stands in for StarRocks: records every statement and the connection it ran on."""

    def __init__(self):
        self.log: list[tuple[str, int | None, str]] = []
        self.connections: list[FakeConnection] = []
        self.variables: dict[str, tuple[object, str]] = {}
        self.fail_on: str | None = None

    @staticmethod
    def read_back_names(sql):
        import re

        return [name for name in re.findall(r"@`([^`]+)` AS v\d+", sql)]

    @contextlib.asynccontextmanager
    async def user_conn(self, username, password, database=None):
        assert password == "pw", "the held connection is opened as the caller"
        connection = FakeConnection(self, len(self.connections) + 1)
        self.connections.append(connection)
        try:
            yield connection
        finally:
            connection.closed = True


class RecordingRepo:
    def __init__(self, engine):
        self.engine = engine

    async def execute_as_user(self, sql, **kwargs):
        connected = kwargs.get("connected")
        number = connected.number if connected is not None else None
        self.engine.log.append(("statement", number, sql))
        if connected is not None and number != 99:
            assert is_worksheet_connection(connected)
        if self.engine.fail_on and self.engine.fail_on in sql:
            return QueryResult(executed_sql=sql, error="engine refused")
        if sql.upper().startswith("SELECT"):
            return QueryResult(executed_sql=sql, columns=["v"], rows=[[1]], row_count=1)
        return QueryResult(executed_sql=sql)


class FakeStore:
    def __init__(self):
        self.values: dict[tuple[str, str], str] = {}

    async def get_worksheet_state(self, session_id, file_id):
        return self.values.get((session_id, file_id))

    async def set_worksheet_state(self, session_id, file_id, value):
        self.values[(session_id, file_id)] = value
        return True

    async def clear_worksheet_state(self, session_id, file_id):
        self.values.pop((session_id, file_id), None)


@pytest.fixture
def engine(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr("app.core.database.db.user_conn", engine.user_conn)
    return engine


@pytest.fixture
def store(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr("app.core.redis.session_store", store)
    return store


@pytest.fixture
def audits(monkeypatch):
    rows: list[dict] = []

    async def audit(**kwargs):
        rows.append(kwargs)
        return "query-id"

    monkeypatch.setattr("app.modules.query.service.write_audit_log", audit)
    return rows


@pytest.fixture
def service(monkeypatch, engine, store, audits):
    service = QueryService()
    service._repo = RecordingRepo(engine)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    monkeypatch.setattr(
        "app.modules.query.worksheet_session.decrypt_password",
        lambda value: "pw" if value == "enc" else decrypt_password(value),
    )
    return service


async def _run(service, sql, *, file_id="ws-1", session_id="s-1", user="alice",
               role="analyst", version=1, password="enc", database="db"):
    return await service.execute_statements(
        sql=sql, username=user, encrypted_password=password, role=role, database=database,
        session_id=session_id, file_id=file_id, security_context_version=version,
    )


def _statements(engine, number=None):
    return [sql for kind, conn, sql in engine.log if kind == "statement"
            and (number is None or conn == number)]


async def test_script_with_session_statement_runs_on_one_held_connection(service, engine):
    results = await _run(service, "SET time_zone = 'Asia/Shanghai'; SELECT now(); SELECT 2")
    assert [r.error for r in results] == [None, None, None]
    assert len(engine.connections) == 1
    assert {conn for kind, conn, _ in engine.log if kind == "statement"} == {1}
    assert _statements(engine) == ["SET time_zone = 'Asia/Shanghai'", "SELECT now()", "SELECT 2"]
    assert engine.connections[0].closed


async def test_script_without_session_statements_keeps_the_per_statement_path(service, engine):
    results = await _run(service, "SELECT 1; SELECT 2")
    assert [r.error for r in results] == [None, None]
    assert engine.connections == []
    assert {conn for kind, conn, _ in engine.log if kind == "statement"} == {None}


async def test_settings_persist_to_the_next_run_of_the_same_worksheet_in_order(
    service, engine, store
):
    await _run(service, "SET time_zone = 'Asia/Shanghai'; SET query_mem_limit = 10 * 1024; "
               "SET time_zone = '+07:00'")
    engine.log.clear()
    results = await _run(service, "SELECT now()")
    assert results[0].error is None
    assert len(engine.connections) == 2
    setup = [sql for kind, conn, sql in engine.log if kind == "setup" and conn == 2]
    # The role first, then each remembered setting in the order it was last set.
    assert setup == [
        "SET ROLE analyst",
        "SET query_mem_limit = 10 * 1024",
        "SET time_zone = '+07:00'",
    ]
    assert _statements(engine, 2) == ["SELECT now()"]
    first_setup = engine.log.index(("setup", 2, "SET time_zone = '+07:00'"))
    assert first_setup < engine.log.index(("statement", 2, "SELECT now()"))


async def test_default_clears_a_remembered_setting(service, engine, store):
    await _run(service, "SET time_zone = 'Asia/Shanghai'; SET query_timeout = 60")
    await _run(service, "SET time_zone = DEFAULT")
    engine.log.clear()
    await _run(service, "SELECT 1")
    setup = [sql for kind, _, sql in engine.log if kind == "setup"]
    assert setup == ["SET ROLE analyst", "SET query_timeout = 60"]
    await _run(service, "SET query_timeout = DEFAULT")
    assert store.values == {}, "an empty state is removed, not stored"
    engine.log.clear()
    await _run(service, "SELECT 1")
    assert [kind for kind, _, _ in engine.log] == ["statement"], "nothing to hold any more"


async def test_user_variables_are_read_back_and_applied_next_run(service, engine, store):
    engine.variables = {"v1": (1, "tinyint"), "v2": (2, "tinyint")}
    await _run(service, "SET @v1=1, @v2=2")
    read_back = [sql for kind, _, sql in engine.log if kind == "setup" and sql.startswith("SELECT")]
    assert read_back == [
        "SELECT @`v1` AS v0, typeof(@`v1`) AS t0, @`v2` AS v1, typeof(@`v2`) AS t1"
    ]
    engine.log.clear()
    await _run(service, "SELECT @v1 + @v2")
    setup = [sql for kind, _, sql in engine.log if kind == "setup"]
    assert setup == ["SET ROLE analyst", "SET @`v1` = 1", "SET @`v2` = 2"]
    assert _statements(engine) == ["SELECT @v1 + @v2"]


async def test_null_clears_a_user_variable(service, engine, store):
    engine.variables = {"v1": (1, "tinyint")}
    await _run(service, "SET @v1 = 1")
    engine.variables = {"v1": (None, "varchar")}
    await _run(service, "SET @v1 = NULL")
    assert store.values == {}


async def test_state_never_reaches_another_worksheet_user_session_or_role(service, engine, store):
    engine.variables = {"v": ("x", "varchar")}
    await _run(service, "SET time_zone = 'UTC'; SET @v = 'x'")
    assert list(store.values) == [("s-1", "ws-1")]
    for other in (
        {"file_id": "ws-2"},
        {"session_id": "s-2"},
        {"user": "bob"},
        {"role": "auditor"},
        {"version": 2},
        {"file_id": None},
    ):
        engine.log.clear()
        engine.connections.clear()
        results = await _run(service, "SELECT 1", **other)
        assert results[0].error is None
        assert engine.connections == [], f"no remembered state was applied for {other}"
        assert all("UTC" not in sql and "@`v`" not in sql for _, _, sql in engine.log)


async def test_role_switch_clears_the_remembered_state(service, engine, store):
    await _run(service, "SET time_zone = 'UTC'")
    assert ("s-1", "ws-1") in store.values
    engine.log.clear()
    results = await _run(service, "SELECT 1", role="auditor", version=2)
    assert results[0].error is None
    assert store.values == {}, "state of the previous role is deleted, not applied"
    assert results[0].warnings == [worksheet_session.STATE_CLEARED_FOR_ROLE]


async def test_session_store_role_switch_deletes_worksheet_state():
    from app.core.redis import WORKSHEET_SESSION_PREFIX, SessionStore

    class FakeRedis:
        def __init__(self):
            self.deleted = []

        async def eval(self, *args):
            return 7

        async def delete(self, *keys):
            self.deleted += keys

    sessions = SessionStore()
    sessions._redis = FakeRedis()
    assert await sessions.set_active_role("s-1", "auditor") == 7
    await sessions.delete("s-2")
    assert sessions._redis.deleted == [
        f"{WORKSHEET_SESSION_PREFIX}s-1",
        "nova:session:s-2",
        f"{WORKSHEET_SESSION_PREFIX}s-2",
    ]


async def test_stored_state_is_encrypted_and_holds_no_plain_values(service, engine, store):
    engine.variables = {"token": ("secret-value", "varchar")}
    await _run(service, "SET @token = 'secret-value'")
    stored = store.values[("s-1", "ws-1")]
    assert "secret-value" not in stored
    data = json.loads(decrypt_password(stored))
    assert data["variables"] == [["token", "'secret-value'"]]
    assert data["active_role"] == "analyst" and data["security_context_version"] == 1


async def test_variable_values_never_reach_audit(service, engine, audits):
    engine.variables = {"token": ("secret-value", "varchar")}
    await _run(service, "SET @token = 'secret-value'; SELECT @token")
    texts = [json.dumps(row, default=str) for row in audits]
    assert texts and all("secret-value" not in text for text in texts)
    assert any(row.get("sql_text") == "SET @token = ?" for row in audits)


async def test_transaction_rollback_and_commit_run_in_one_session(service, engine):
    results = await _run(service, "BEGIN; INSERT INTO t VALUES (1); ROLLBACK")
    assert [r.error for r in results] == [None, None, None]
    assert _statements(engine, 1) == ["BEGIN", "INSERT INTO t VALUES (1)", "ROLLBACK"]


async def test_open_transaction_at_end_of_run_is_rolled_back_and_reported(service, engine, audits):
    results = await _run(service, "BEGIN; INSERT INTO t VALUES (1)")
    assert len(results) == 3
    assert results[-1].error == OPEN_TRANSACTION_ROLLED_BACK
    assert results[-1].error_code == "transaction_rolled_back"
    assert engine.connections[0].rolled_back
    assert any(row.get("action") == "transaction_rolled_back" for row in audits)


async def test_failure_inside_a_transaction_rolls_back(service, engine):
    engine.fail_on = "VALUES (2)"
    results = await _run(
        service, "BEGIN; INSERT INTO t VALUES (1); INSERT INTO t VALUES (2); COMMIT"
    )
    assert len(results) == 3
    assert results[-1].error == "engine refused"
    assert FAILED_TRANSACTION_ROLLED_BACK in results[-1].warnings
    assert engine.connections[0].rolled_back
    assert "COMMIT" not in _statements(engine)


async def test_statements_inside_a_transaction_reuse_the_prepared_session(
    service, engine, monkeypatch
):
    seen = []
    original = service.execute

    async def execute(**kwargs):
        seen.append((kwargs["sql"], kwargs["client_transaction"]))
        return await original(**kwargs)

    monkeypatch.setattr(service, "execute", execute)
    await _run(service, "SELECT 0; BEGIN; INSERT INTO t VALUES (1); COMMIT; SELECT 1")
    assert seen == [
        ("SELECT 0", False),
        ("BEGIN", False),
        ("INSERT INTO t VALUES (1)", True),
        ("COMMIT", True),
        ("SELECT 1", False),
    ]


@pytest.mark.parametrize(
    "sql",
    ["SET sql_dialect = 'trino'; SELECT 1", "SELECT 1; SET GLOBAL a = 1, b = 2"],
)
async def test_refusals_still_run_nothing(service, engine, sql):
    results = await _run(service, sql)
    assert len(results) == 1 and results[0].error_code == "capability_unsupported"
    assert engine.log == [] and engine.connections == []


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("BEGIN; INSERT INTO t VALUES (1); ROLLBACK", TRANSACTIONS_UNAVAILABLE),
        ("SET query_timeout = 5; SELECT 1", SESSION_SETTINGS_UNAVAILABLE),
    ],
)
async def test_without_caller_credentials_session_statements_are_refused(
    service, engine, sql, message
):
    results = await _run(service, sql, password="")
    assert len(results) == 1
    assert results[0].error == message and results[0].error_code == "capability_unsupported"
    assert engine.log == [], "nothing may run before the refusal"


async def test_tampered_state_is_dropped_not_applied(service, engine, store):
    state = WorksheetState("alice", "analyst", 1, settings={"x": "x = 1; DROP TABLE t"},
                           variables={"v": "(SELECT secret FROM t)"})
    store.values[("s-1", "ws-1")] = worksheet_session._encode(state)
    results = await _run(service, "SELECT 1")
    setup = [sql for kind, _, sql in engine.log if kind == "setup"]
    assert setup == ["SET ROLE analyst"]
    assert len(results[0].warnings) == 2
    assert store.values == {}


async def test_undecryptable_state_is_discarded(service, engine, store):
    store.values[("s-1", "ws-1")] = encrypt_password("not json")
    results = await _run(service, "SELECT 1")
    assert results[0].error is None and engine.connections == []
    assert store.values == {}


async def test_held_callers_keep_their_own_session(service, engine):
    """The MySQL proxy and task runs hold a connection already; nothing changes for them."""

    class Held:
        number = 99

    with delegated_connection("alice", Held()):
        results = await service.execute_statements(
            sql="SET query_timeout = 5; SELECT 1",
            username="alice", encrypted_password="", role="analyst", database="db",
            file_id="ws-1", session_id="s-1",
        )
    assert [r.error for r in results] == [None, None]
    assert engine.connections == []


# --- \G ----------------------------------------------------------------------


async def test_vertical_terminator_is_removed_and_marked(service, engine):
    results = await _run(service, "SELECT 1\\G SELECT 'a\\Gb'; SHOW PROCESSLIST\\G")
    assert _statements(engine) == ["SELECT 1", "SELECT 'a\\Gb'", "SHOW PROCESSLIST"]
    assert [r.vertical for r in results] == [True, False, True]
