"""Unit tests for the UDF listing against the pinned engine's result shape.

The pinned StarRocks has no all-database function listing: an unqualified
``SHOW FULL FUNCTIONS`` fails without a current database. Global functions come
from ``SHOW FULL GLOBAL FUNCTIONS`` and database functions from
``SHOW FULL FUNCTIONS FROM db``, each with the columns ``Signature``,
``Return Type``, ``Function Type``, ``Intermediate Type`` and ``Properties``
(verified on the local engine on 2026-11-12).

The frontend consumes ``databases: string[]`` to populate its database filter
(``frontend/src/features/functions/api.ts``).
"""

import asyncmy.errors
import pytest

from app.modules.functions.service import FunctionService

COLUMNS = ["Signature", "Return Type", "Function Type", "Intermediate Type", "Properties"]
GLOBAL = [("ai_complete(VARCHAR)", "VARCHAR", "SQL", "NULL", "concat(...)")]
BY_DATABASE = {
    "analytics": [
        ("score_it(INT)", "DOUBLE", "Scalar", "NULL", "{}"),
        ("bucket_it(INT,INT)", "INT", "Scalar", "NULL", "{}"),
    ],
    "reporting": [("fmt_it(VARCHAR)", "VARCHAR", "SQL", "NULL", "concat(s)")],
    "empty_db": [],
    "we`ird": [("odd()", "INT", "SQL", "NULL", "1")],
}


class FakeCursor:
    def __init__(self, script, executed, fail):
        self._script = script
        self._executed = executed
        self._fail = fail
        self._rows: list = []
        self.description = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self._executed.append(sql)
        if sql in self._fail:
            raise self._fail[sql]
        if sql == "SHOW DATABASES":
            self.description = [("Database",)]
            self._rows = [(name,) for name in self._script]
        elif sql == "SHOW FULL GLOBAL FUNCTIONS":
            self.description = [(c,) for c in COLUMNS]
            self._rows = GLOBAL
        elif sql.startswith("SHOW FULL FUNCTIONS FROM `") and sql.endswith("`"):
            name = sql[len("SHOW FULL FUNCTIONS FROM `"):-1].replace("``", "`")
            self.description = [(c,) for c in COLUMNS]
            self._rows = self._script[name]
        else:
            raise asyncmy.errors.ProgrammingError(1046, "No database selected.")

    async def fetchall(self):
        return tuple(self._rows)


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self, *args, **kwargs):
        return self._cursor


@pytest.fixture
def engine(monkeypatch):
    executed: list[str] = []
    fail: dict[str, Exception] = {}
    monkeypatch.setattr(
        "app.modules.functions.service.db.system_conn",
        lambda: FakeConn(FakeCursor(BY_DATABASE, executed, fail)),
    )
    return executed, fail


async def test_lists_global_and_database_functions_with_parsed_signatures(engine):
    functions, databases = await FunctionService().list_udfs_with_databases()
    assert databases == ["analytics", "reporting", "we`ird"]
    shaped = {(f.database, f.name, f.args, f.scope, f.function_type, f.return_type)
              for f in functions}
    assert ("", "ai_complete", "VARCHAR", "global", "SQL", "VARCHAR") in shaped
    assert ("analytics", "bucket_it", "INT,INT", "database", "Scalar", "INT") in shaped
    assert ("we`ird", "odd", "", "database", "SQL", "INT") in shaped
    assert len(functions) == 5


async def test_never_issues_the_unqualified_listing(engine):
    executed, _ = engine
    await FunctionService().list_udfs_with_databases()
    assert "SHOW FULL FUNCTIONS" not in executed
    assert "SHOW FULL FUNCTIONS FROM `we``ird`" in executed


async def test_filter_narrows_functions_but_keeps_every_choice(engine):
    functions, databases = await FunctionService().list_udfs_with_databases(
        database="reporting"
    )
    assert [f.name for f in functions] == ["fmt_it"]
    assert databases == ["analytics", "reporting", "we`ird"]


async def test_database_without_functions_is_not_a_choice(engine):
    _, databases = await FunctionService().list_udfs_with_databases()
    assert "empty_db" not in databases and "" not in databases


async def test_database_dropped_during_scan_is_skipped(engine):
    _, fail = engine
    fail["SHOW FULL FUNCTIONS FROM `reporting`"] = asyncmy.errors.OperationalError(
        5501, "Getting analyzing error. Detail message: Unknown database 'reporting'."
    )
    functions, databases = await FunctionService().list_udfs_with_databases()
    assert "reporting" not in databases
    assert "fmt_it" not in {f.name for f in functions}


async def test_other_engine_failures_propagate(engine):
    _, fail = engine
    fail["SHOW FULL FUNCTIONS FROM `analytics`"] = asyncmy.errors.OperationalError(
        2013, "Lost connection"
    )
    with pytest.raises(asyncmy.errors.OperationalError):
        await FunctionService().list_udfs_with_databases()


async def test_list_udfs_returns_only_the_functions(engine):
    functions = await FunctionService().list_udfs(database="analytics")
    assert [f.name for f in functions] == ["score_it", "bucket_it"]
