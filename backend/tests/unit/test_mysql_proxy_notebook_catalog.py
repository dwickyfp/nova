"""DuckDB's catalog queries on a notebook connection follow Nova's catalog rule.

The scanner reads ``information_schema`` before any Nova query. A role Ranger
refuses ``information_schema`` must still see the databases ``SHOW DATABASES``
gives it, and the columns ``SHOW FULL COLUMNS`` lets it describe — no more.
"""

from __future__ import annotations

import pytest

from app.modules.query.repository import QueryResult
from app.proxy.executor import CollectingSink, ProxyQueryExecutor
from app.proxy.notebook_metadata import ScannerQuery, column_row, scanner_query
from app.proxy.session import SessionState

SCHEMATA = "\nSELECT schema_name\nFROM information_schema.schemata\n"
COLUMNS = (
    "\nSELECT c.table_name, c.column_name, c.data_type, c.column_type, c.column_default, "
    "c.is_nullable, c.numeric_precision, c.numeric_scale, t.table_comment, c.column_comment\n"
    "FROM information_schema.columns c\nLEFT JOIN information_schema.tables t\n"
    "\tON t.TABLE_CATALOG = c.TABLE_CATALOG\n\tAND t.TABLE_SCHEMA = c.TABLE_SCHEMA\n"
    "\tAND t.TABLE_NAME = c.TABLE_NAME\nWHERE c.table_schema={schema}\n"
    "ORDER BY c.table_name, c.ordinal_position;\n"
)
TABLE_COLUMNS = COLUMNS.replace("SELECT c.table_name, ", "SELECT ").replace(
    "WHERE c.table_schema={schema}", "WHERE c.table_schema={schema} AND c.table_name={table}"
)
INDEXES = (
    "\nSELECT DISTINCT TABLE_NAME, INDEX_NAME\nFROM INFORMATION_SCHEMA.STATISTICS\n"
    "WHERE TABLE_SCHEMA = 'sales';\n"
)
SIZE = (
    "\nSELECT SUM(data_length + index_length)\nFROM information_schema.tables\n"
    "WHERE table_schema = 'sales';\n"
)


def test_the_scanner_queries_are_recognised_with_their_literals():
    assert scanner_query(SCHEMATA) == ScannerQuery("schemas")
    assert scanner_query(COLUMNS.format(schema="'sales'")) == ScannerQuery("columns", "sales")
    assert scanner_query(
        TABLE_COLUMNS.format(schema="'o''brien'", table="'orders'")
    ) == ScannerQuery("columns", "o'brien", "orders")
    assert scanner_query(INDEXES) == ScannerQuery("indexes", "sales")
    assert scanner_query(SIZE) == ScannerQuery("size", "sales")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT schema_name FROM information_schema.schemata WHERE 1=1",
        "SELECT * FROM information_schema.columns",
        COLUMNS.format(schema="'a' OR 1=1 --'"),
        "SELECT 1",
    ],
)
def test_anything_else_runs_unchanged(sql):
    assert scanner_query(sql) is None


def test_show_full_columns_maps_to_the_scanner_row():
    assert column_row(
        {"Field": "amount", "Type": "decimal(18,2)", "Null": "NO", "Default": None,
         "Comment": ""}
    ) == ["amount", "decimal", "decimal(18,2)", None, "NO", 18, 2, None, None]
    assert column_row({"Field": "region", "Type": "varchar(65533)", "Null": "YES"})[:5] == [
        "region", "varchar", "varchar(65533)", None, "YES"
    ]


class CatalogService:
    """Ranger refuses ``information_schema``; ``SHOW`` lists what the role may see."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def preflight_script(self, statements, **kwargs):
        return None

    async def execute_statements(self, **kwargs):
        sql = kwargs["sql"]
        self.calls.append(sql)
        if "information_schema" in sql.lower():
            return [QueryResult(error="Access denied; you need the SELECT privilege")]
        if sql == "SHOW DATABASES":
            return [QueryResult(columns=["Database"],
                                rows=[["sales"], ["NOVA_SYSTEM"], ["information_schema"]])]
        if sql == "SHOW TABLES FROM `sales`":
            return [QueryResult(columns=["Tables_in_sales"], rows=[["orders"], ["secret"]])]
        if sql == "SHOW FULL COLUMNS FROM `sales`.`orders`":
            return [QueryResult(
                columns=["Field", "Type", "Collation", "Null", "Key", "Default", "Extra",
                         "Privileges", "Comment"],
                rows=[["id", "bigint(20)", None, "NO", "true", None, "", "", ""],
                      ["region", "varchar(64)", None, "YES", "false", None, "", "", "area"]],
            )]
        return [QueryResult(error="Access denied")]


@pytest.fixture
def catalog(monkeypatch):
    service = CatalogService()
    monkeypatch.setattr("app.proxy.executor.query_service", service)
    return service


async def _run(sql: str, *, notebook: bool = True):
    session = SessionState(active_role="retail_steward")
    session.notebook_connection = notebook
    sink = CollectingSink()
    await ProxyQueryExecutor(session).execute_to(
        sql, sink, username="steward", connection=object(), session_id="s"
    )
    return sink.result()


async def test_schemas_come_from_show_databases_without_hidden_ones(catalog):
    result = await _run(SCHEMATA)
    assert [c.name for c in result.columns] == ["schema_name"]
    assert result.rows == [["sales"]]
    assert catalog.calls == ["SHOW DATABASES"]


async def test_refused_columns_are_described_table_by_table(catalog):
    result = await _run(COLUMNS.format(schema="'sales'"))
    assert result.error is None
    assert [c.name for c in result.columns][:3] == ["table_name", "column_name", "data_type"]
    assert result.rows == [
        ["orders", "id", "bigint", "bigint(20)", None, "NO", None, None, None, None],
        ["orders", "region", "varchar", "varchar(64)", None, "YES", None, None, None, "area"],
    ]
    # The table the role may not describe is left out, not guessed.
    assert "SHOW FULL COLUMNS FROM `sales`.`secret`" in catalog.calls


async def test_a_table_the_role_cannot_describe_keeps_the_refusal(catalog):
    result = await _run(TABLE_COLUMNS.format(schema="'sales'", table="'secret'"))
    assert result.error is not None and not result.rows


async def test_indexes_and_size_are_answered_without_the_engine(catalog):
    indexes = await _run(INDEXES)
    size = await _run(SIZE)
    assert indexes.is_resultset and indexes.rows == []
    assert size.rows == [[None]]
    assert catalog.calls == []


async def test_other_clients_keep_the_engine_answer(catalog):
    result = await _run(SCHEMATA, notebook=False)
    assert result.error is not None
    assert len(catalog.calls) == 1 and "information_schema" in catalog.calls[0]
