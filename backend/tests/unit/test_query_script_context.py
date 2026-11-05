"""A web script's ``USE`` / ``SET CATALOG`` applies to the statements after it.

``POST /api/v1/query/execute`` runs each statement of a script on its own
pooled connection, so the session state a ``USE`` leaves on one connection is
gone by the next statement. Before this was carried forward, ``USE
pg_shop.public; SELECT ... FROM customers`` failed with "No database selected",
and with a Workspace database selected the ``USE`` was silently ignored and the
query read the wrong database.
"""

from __future__ import annotations

import pytest

from app.modules.query.service import _context_after
from tests.unit.test_query_success_contract import (  # noqa: F401 - fixture
    EXECUTE_ENDPOINT,
    FakeEngine,
    execute_client,
)


@pytest.mark.parametrize(
    ("statement", "before", "after"),
    [
        ("USE DEMO_SALES", ("NOVA_DEMO", None), ("DEMO_SALES", None)),
        ("use pg_shop.public;", (None, None), ("pg_shop.public", None)),
        ("USE `pg shop`.`public`", (None, None), ("pg shop.public", None)),
        ("SET CATALOG pg_shop", ("x", "s"), ("pg_shop.", None)),
        ("USE 'sf_sales'", (None, None), ("sf_sales.", None)),
        ("SET CATALOG default_catalog", ("pg_shop.public", None), (None, None)),
        ("SELECT 1", ("db", "sc"), ("db", "sc")),
        ("INSERT INTO t SELECT 1", ("db", None), ("db", None)),
    ],
)
def test_context_after_a_statement(statement, before, after):
    assert _context_after(statement, *before) == after


class RecordingEngine(FakeEngine):
    def __init__(self):
        super().__init__()
        self.databases: list[str | None] = []

    async def execute_as_user(self, sql, **kwargs):
        self.databases.append(kwargs.get("database"))
        return await super().execute_as_user(sql, **kwargs)


@pytest.fixture
def recording(execute_client, monkeypatch):  # noqa: F811 - the imported fixture
    from app.modules.query.service import query_service

    client, _ = execute_client
    engine = RecordingEngine()
    monkeypatch.setattr(query_service, "_repo", engine)
    return client, engine


def test_use_selects_the_database_of_the_next_statements(recording):
    client, engine = recording
    payload = client.post(EXECUTE_ENDPOINT, json={
        "sql": "USE pg_shop.public; SELECT 1 AS x; SELECT 1 AS x",
        "database": "NOVA_DEMO",
    }).json()

    assert [r["success"] for r in payload] == [True, True, True]
    assert engine.databases == ["NOVA_DEMO", "pg_shop.public", "pg_shop.public"]


def test_set_catalog_selects_the_catalog_without_a_database(recording):
    client, engine = recording
    client.post(EXECUTE_ENDPOINT, json={
        "sql": "SET CATALOG sf_sales; SELECT 1 AS x; SET CATALOG default_catalog; SELECT 1 AS x",
        "database": "analytics",
    })
    assert engine.databases == ["analytics", "sf_sales.", "sf_sales.", None]
