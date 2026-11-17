"""SHOW [ALL] AUTHENTICATION [FOR user] runs as a read-only engine listing.

The grammar labels its two alternatives, so the parser produces
``ShowAllAuthenticationContext`` and ``ShowAuthenticationForUserContext``
instead of a ``...StatementContext``; without explicit builders the frontend
refused them with "No AST builder registered for SQL extension".
"""

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.ast.statements import NativeStatement
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner

FORMS = [
    "SHOW AUTHENTICATION",
    "SHOW ALL AUTHENTICATION",
    "SHOW AUTHENTICATION FOR root",
    "SHOW AUTHENTICATION FOR 'alice'@'%'",
]


@pytest.mark.parametrize("sql", FORMS)
def test_builds_a_read_only_native_statement(sql):
    statement = ast_builders.build(parse_statement(sql))
    assert type(statement) is NativeStatement
    analysis = SQLPlanner().preflight(statement)
    assert analysis.effects.reads_data
    assert not (
        analysis.effects.writes_metadata
        or analysis.effects.changes_security
        or analysis.requires_confirmation
    )


@pytest.mark.parametrize("sql", FORMS)
async def test_runs_as_the_caller_and_is_audited(monkeypatch, sql):
    calls, audits = [], []

    class Repo:
        async def execute_as_user(self, sql, **kwargs):
            calls.append((sql, kwargs["role"]))
            return QueryResult(executed_sql=sql, columns=["UserIdentity"], rows=[["x"]])

    async def audit(**kwargs):
        audits.append(kwargs)

    service = QueryService()
    service._repo = Repo()
    monkeypatch.setattr("app.modules.query.service.write_audit_log", audit)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    results = await service.execute_statements(
        sql=sql, username="alice", encrypted_password="enc", role="user_admin", database="db",
    )
    assert results[0].error is None
    assert calls == [(sql, "user_admin")]
    assert [row["sql_text"] for row in audits] == [sql]
