"""The worksheet refuses statements whose effect a pooled run would lose.

``QueryService.execute_statements`` without a held connection runs each
statement on its own pooled connection. ``BEGIN; INSERT ...; ROLLBACK`` used
to report three successes while the INSERT stayed committed, and a session
``SET`` reported success but never reached the next statement. Callers that
hold one connection (the MySQL proxy, task runs) keep their own session and
keep these statements.
"""

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService, delegated_connection
from app.sql_frontend.analysis.validation import (
    SESSION_SETTINGS_UNAVAILABLE,
    TRANSACTIONS_UNAVAILABLE,
    refuse_per_statement_session,
)
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.errors import CapabilityUnsupportedError
from app.sql_frontend.parser import parse_statement


def _statement(sql):
    return ast_builders.build(parse_statement(sql))


@pytest.mark.parametrize(
    "sql",
    [
        "BEGIN",
        "BEGIN WORK",
        "START TRANSACTION",
        "START TRANSACTION WITH CONSISTENT SNAPSHOT",
        "COMMIT",
        "COMMIT WORK",
        "ROLLBACK",
    ],
)
def test_transaction_control_is_refused(sql):
    with pytest.raises(CapabilityUnsupportedError) as error:
        refuse_per_statement_session(_statement(sql))
    assert str(error.value) == TRANSACTIONS_UNAVAILABLE
    assert error.value.code == "capability_unsupported"


@pytest.mark.parametrize(
    "sql",
    [
        "SET query_timeout = 600",
        "SET SESSION query_timeout = 600",
        "SET LOCAL query_timeout = 600",
        "SET @@query_timeout = 600",
        "SET @@session.query_timeout = 600",
        "SET @threshold = 5",
        "SET NAMES utf8mb4",
        "SET TRANSACTION READ ONLY",
        "SET GLOBAL query_timeout = 600, query_mem_limit = 0",
    ],
)
def test_session_settings_are_refused(sql):
    with pytest.raises(CapabilityUnsupportedError) as error:
        refuse_per_statement_session(_statement(sql))
    assert str(error.value) == SESSION_SETTINGS_UNAVAILABLE


@pytest.mark.parametrize(
    "sql",
    [
        "SET GLOBAL query_timeout = 600",
        "SET @@global.query_timeout = 600",
        "SELECT 1",
        "INSERT INTO t VALUES (1)",
        "USE analytics",
        "SET CATALOG default_catalog",
        "SELECT /*+ SET_VAR(query_timeout = 600) */ 1",
    ],
)
def test_lasting_or_ordinary_statements_pass(sql):
    refuse_per_statement_session(_statement(sql))


class RecordingRepo:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_as_user(self, sql, **kwargs):
        self.calls.append(sql)
        return QueryResult(executed_sql=sql, columns=["v"], rows=[[1]], row_count=1)


@pytest.fixture
def service(monkeypatch):
    async def audit(**kwargs):
        return "query-id"

    service = QueryService()
    service._repo = RecordingRepo()
    monkeypatch.setattr("app.modules.query.service.write_audit_log", audit)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    return service


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("BEGIN; INSERT INTO t VALUES (1); ROLLBACK", TRANSACTIONS_UNAVAILABLE),
        ("INSERT INTO t VALUES (1); COMMIT", TRANSACTIONS_UNAVAILABLE),
        ("SET query_timeout = 5; SELECT 1", SESSION_SETTINGS_UNAVAILABLE),
    ],
)
async def test_worksheet_script_runs_nothing(service, sql, message):
    results = await service.execute_statements(
        sql=sql, username="alice", encrypted_password="enc", role="analyst", database="db",
    )
    assert len(results) == 1
    assert results[0].error == message and results[0].error_code == "capability_unsupported"
    assert service._repo.calls == [], "nothing may run before the refusal"


async def test_delegated_run_keeps_its_session(service, monkeypatch):
    """A run that holds one connection is not the worksheet; it is not refused here."""
    seen = []

    def check(statement):
        seen.append(statement)

    monkeypatch.setattr(
        "app.sql_frontend.analysis.validation.refuse_per_statement_session", check
    )
    with delegated_connection("alice", object()):
        await service.execute_statements(
            sql="SET query_timeout = 5; SELECT 1",
            username="alice", encrypted_password="", role="analyst", database="db",
        )
    assert seen == []
    await service.execute_statements(
        sql="SELECT 1", username="alice", encrypted_password="", role="analyst", database="db",
        connection=object(),
    )
    assert seen == []
    await service.execute_statements(
        sql="SELECT 1", username="alice", encrypted_password="enc", role="analyst", database="db",
    )
    assert len(seen) == 1, "the pooled worksheet path does check"
