"""Regression tests for guard placement in QueryService.execute.

Acceptance (c): ``SELECT 1; DROP TABLE t`` must require confirmation and must
not reach StarRocks without ``confirm_destructive``.

The previous implementation called ``guard_sql`` / ``is_destructive_sql`` on the
raw blob *before* splitting, with a pattern anchored to ``^\\s*``. The second
statement was therefore invisible to the guard and executed for real.

These tests stub the repository so the assertion is on what would have been sent
to the engine — no database required.
"""

import pytest

from app.core.exceptions import ForbiddenSQLError
from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService


class RecordingRepo:
    """Stub repository that records the SQL handed to the engine."""

    def __init__(self):
        self.calls: list[str] = []

    async def execute_as_user(self, sql, **kwargs):
        self.calls.append(sql)
        return QueryResult(executed_sql=sql, columns=["v"], rows=[[1]], row_count=1)


@pytest.fixture
def patched(monkeypatch):
    """Return a factory that installs a recording repo on a QueryService."""
    created = {}

    def make():
        svc = QueryService()
        repo = RecordingRepo()
        svc._repo = repo
        created["svc"] = svc
        created["repo"] = repo
        return svc

    async def no_audit(**kwargs):
        return None

    monkeypatch.setattr("app.modules.query.service.write_audit_log", no_audit)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    return make, created


async def _execute(svc, sql, confirm_destructive=False):
    return await svc.execute(
        sql=sql,
        username="analyst",
        encrypted_password="enc",
        confirm_destructive=confirm_destructive,
    )


class TestMultiStatementDestructiveIsBlocked:
    """The guard must see every statement, not just the first."""

    async def test_second_statement_drop_requires_confirmation(self, patched):
        make, created = patched
        svc = make()
        with pytest.raises(ForbiddenSQLError, match="confirmation"):
            await _execute(svc, "SELECT 1; DROP TABLE t")
        assert created["repo"].calls == [], "destructive SQL reached the engine"

    async def test_second_statement_drop_not_executed_without_confirmation(self, patched):
        make, created = patched
        svc = make()
        with pytest.raises(ForbiddenSQLError):
            await _execute(svc, "SELECT 1; DROP TABLE t", confirm_destructive=False)
        assert created["repo"].calls == []

    async def test_truncate_in_second_statement_requires_confirmation(self, patched):
        make, created = patched
        svc = make()
        with pytest.raises(ForbiddenSQLError, match="confirmation"):
            await _execute(svc, "SELECT 1; TRUNCATE TABLE t")
        assert created["repo"].calls == []

    async def test_delete_in_second_statement_requires_confirmation(self, patched):
        make, created = patched
        svc = make()
        with pytest.raises(ForbiddenSQLError, match="confirmation"):
            await _execute(svc, "SELECT 1; DELETE FROM t")
        assert created["repo"].calls == []

    async def test_accountadmin_drop_in_second_statement_blocked(self, patched):
        make, created = patched
        svc = make()
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            await _execute(svc, "SELECT 1; DROP ROLE ACCOUNTADMIN", confirm_destructive=True)
        assert created["repo"].calls == []

    async def test_commented_accountadmin_drop_in_second_statement_blocked(self, patched):
        make, created = patched
        svc = make()
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            await _execute(
                svc,
                "SELECT 1; /*x*/ DROP ROLE `ACCOUNTADMIN`",
                confirm_destructive=True,
            )
        assert created["repo"].calls == []


class TestSingleStatementBehaviourUnchanged:
    async def test_plain_select_executes(self, patched):
        make, created = patched
        svc = make()
        await _execute(svc, "SELECT 1")
        assert created["repo"].calls == ["SELECT 1"]

    async def test_leading_drop_still_requires_confirmation(self, patched):
        make, created = patched
        svc = make()
        with pytest.raises(ForbiddenSQLError, match="confirmation"):
            await _execute(svc, "DROP TABLE t")
        assert created["repo"].calls == []

    async def test_confirmed_single_drop_executes(self, patched):
        make, created = patched
        svc = make()
        await _execute(svc, "DROP TABLE t", confirm_destructive=True)
        assert created["repo"].calls == ["DROP TABLE t"]

    async def test_semicolon_inside_literal_is_one_statement(self, patched):
        make, created = patched
        svc = make()
        await _execute(svc, "SELECT 'a;b'")
        assert created["repo"].calls == ["SELECT 'a;b'"]


class TestExecuteStatementsPath:
    """execute_statements must apply the same per-statement guard."""

    async def test_destructive_statement_in_script_requires_confirmation(self, patched):
        make, created = patched
        svc = make()
        results = await svc.execute_statements(
            sql="SELECT 1; DROP TABLE t",
            username="analyst",
            encrypted_password="enc",
        )
        # SELECT 1 runs; the DROP is refused and reported as an error result.
        assert created["repo"].calls == ["SELECT 1"]
        assert results[-1].warnings, "the refused statement must be reported"
        assert "confirmation" in results[-1].warnings[0].lower()

    async def test_clean_script_executes_every_statement(self, patched):
        make, created = patched
        svc = make()
        await svc.execute_statements(
            sql="SELECT 1; SELECT 2",
            username="analyst",
            encrypted_password="enc",
        )
        assert created["repo"].calls == ["SELECT 1", "SELECT 2"]
