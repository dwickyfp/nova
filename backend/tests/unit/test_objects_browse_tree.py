"""The object browser tree types relations in two statements per database."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import asyncmy.errors
import pytest

from app.modules.objects import repository as repo_module
from app.modules.objects.repository import ObjectRepository

RELATIONS = {
    "sales": [("orders", "BASE TABLE"), ("v_orders", "VIEW"), ("mv_daily", "VIEW")],
    "empty": [],
}
MVS = {"sales": [(1, "sales", "mv_daily")]}


class Cursor:
    def __init__(self, log, opened):
        self.log = log
        self.description = None
        self._rows = []
        opened.append(self)

    async def execute(self, sql):
        self.log.append(sql)
        await asyncio.sleep(0)
        if sql.startswith("SET ROLE"):
            return
        name = sql.split("`")[1]
        if name not in RELATIONS:
            raise asyncmy.errors.OperationalError(5501, f"Unknown database '{name}'.")
        if sql.startswith("SHOW FULL TABLES"):
            self.description = [("Tables_in",), ("Table_type",)]
            self._rows = RELATIONS[name]
        else:
            self.description = [("id",), ("database_name",), ("name",)]
            self._rows = MVS.get(name, [])

    async def fetchall(self):
        return self._rows


@pytest.fixture
def engine(monkeypatch):
    log: list[str] = []
    opened: list[Cursor] = []

    @asynccontextmanager
    async def cursor(self, *, username, encrypted_password, role=None):
        cur = Cursor(log, opened)
        if role:
            await cur.execute(f"SET ROLE {role}")
        yield cur

    monkeypatch.setattr(ObjectRepository, "_user_cursor", cursor)
    return log, opened


async def test_relations_are_typed_without_a_statement_per_table(engine):
    log, _ = engine
    types = await ObjectRepository().list_table_types(
        "sales", username="u", encrypted_password="e", role="ANALYST",
    )
    assert [t["name"] for t in types["tables"]] == ["orders"]
    assert [v["name"] for v in types["views"]] == ["v_orders"]
    assert [m["name"] for m in types["materialized_views"]] == ["mv_daily"]
    assert log == ["SET ROLE ANALYST", "SHOW FULL TABLES FROM `sales`",
                   "SHOW MATERIALIZED VIEWS FROM `sales`"]


async def test_many_databases_share_a_few_connections_with_the_role(engine):
    log, opened = engine
    names = ["sales", "empty", "dropped_meanwhile"] * 4
    found = await ObjectRepository().table_types_by_database(
        names, username="u", encrypted_password="e", role="ANALYST", connections=3,
    )
    assert len(opened) == 3
    assert log.count("SET ROLE ANALYST") == 3
    assert found["dropped_meanwhile"] == {"tables": [], "views": [], "materialized_views": []}
    assert len(found["sales"]["tables"]) == 1


async def test_other_engine_failures_propagate(engine, monkeypatch):
    async def broken(cur, database):
        raise asyncmy.errors.OperationalError(2013, "Lost connection")

    monkeypatch.setattr(ObjectRepository, "_table_types", staticmethod(broken))
    with pytest.raises(asyncmy.errors.OperationalError):
        await ObjectRepository().table_types_by_database(
            ["sales"], username="u", encrypted_password="e",
        )


async def test_an_unsafe_role_is_refused_before_any_statement(monkeypatch):
    from app.common.identifiers import InvalidIdentifierError

    statements: list[str] = []

    class Conn:
        def cursor(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql):
            statements.append(sql)

    monkeypatch.setattr(repo_module, "decrypt_password", lambda value: "pw")
    monkeypatch.setattr(repo_module.db, "user_conn", lambda username, password: Conn())
    with pytest.raises(InvalidIdentifierError):
        await ObjectRepository().list_table_types(
            "sales", username="u", encrypted_password="e", role="ANALYST; DROP",
        )
    assert statements == []
