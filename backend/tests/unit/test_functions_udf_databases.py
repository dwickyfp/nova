"""Unit tests for the UDF listing return shape.

The frontend consumes ``databases: string[]`` on ``/functions/udf`` to populate
its database filter (``frontend/src/features/functions/api.ts``). The field is
derived from the same ``SHOW FULL FUNCTIONS`` rows as ``functions`` — no second
query — which is the property these tests pin.

Placed at L1: ``list_udfs_with_databases`` is driven with a fake connection, so
neither an engine nor a route is involved.
"""

import pytest

from app.modules.functions.service import FunctionService


class FakeCursor:
    """A cursor whose ``SHOW FULL FUNCTIONS`` returns scripted rows."""

    def __init__(self, columns: list[str], rows: list[tuple]) -> None:
        self._columns = columns
        self._rows = rows
        self.description = [(c,) for c in columns]
        self.executed: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.executed.append(sql)

    async def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self, *args, **kwargs):
        return self._cursor


@pytest.fixture
def fake_db(monkeypatch):
    """Install a fake system connection returning the given rows."""
    columns = ["Db", "Name", "Type", "Arguments", "Return_type"]

    def install(rows: list[tuple]) -> FakeCursor:
        cursor = FakeCursor(columns, rows)
        monkeypatch.setattr(
            "app.modules.functions.service.db.system_conn",
            lambda: FakeConn(cursor),
        )
        return cursor

    return install


ROWS = [
    ("analytics", "score_it", "GLOBAL", "(x INT)", "DOUBLE"),
    ("analytics", "bucket_it", "GLOBAL", "(x INT)", "INT"),
    ("reporting", "fmt_it", "JAVA", "(s STRING)", "STRING"),
    ("", "no_db_fn", "GLOBAL", "()", "INT"),
]


class TestDatabasesField:
    async def test_lists_every_database_holding_a_udf(self, fake_db):
        fake_db(ROWS)
        functions, databases = await FunctionService().list_udfs_with_databases()
        assert databases == ["analytics", "reporting"]
        assert len(functions) == 4

    async def test_databases_are_sorted_and_deduplicated(self, fake_db):
        fake_db(ROWS)
        _, databases = await FunctionService().list_udfs_with_databases()
        assert databases == sorted(databases)
        assert len(databases) == len(set(databases))

    async def test_empty_database_contributes_nothing(self, fake_db):
        """A GLOBAL function reports an empty ``Db``; it is not a choice."""
        fake_db(ROWS)
        _, databases = await FunctionService().list_udfs_with_databases()
        assert "" not in databases

    async def test_databases_is_unfiltered_when_database_narrows_functions(self, fake_db):
        """The filter selects functions, not the set of filter choices.

        This is the property the UI needs: selecting one database must not
        remove the others from the dropdown.
        """
        fake_db(ROWS)
        functions, databases = await FunctionService().list_udfs_with_databases(
            database="reporting"
        )
        assert [f.name for f in functions] == ["fmt_it"]
        assert databases == ["analytics", "reporting"]

    async def test_only_one_query_is_issued(self, fake_db):
        """Derived from the rows already fetched, not a second query."""
        cursor = fake_db(ROWS)
        await FunctionService().list_udfs_with_databases()
        assert len(cursor.executed) == 1, cursor.executed
        assert "SHOW FULL FUNCTIONS" in cursor.executed[0]

    async def test_empty_result_yields_empty_databases(self, fake_db):
        fake_db([])
        functions, databases = await FunctionService().list_udfs_with_databases()
        assert functions == []
        assert databases == []


class TestListUdfsStillWorks:
    """The original entry point is unchanged for existing callers."""

    async def test_list_udfs_returns_only_the_functions(self, fake_db):
        fake_db(ROWS)
        functions = await FunctionService().list_udfs()
        assert len(functions) == 4
        assert functions[0].database == "analytics"

    async def test_list_udfs_honours_the_filter(self, fake_db):
        fake_db(ROWS)
        functions = await FunctionService().list_udfs(database="analytics")
        assert [f.name for f in functions] == ["score_it", "bucket_it"]
