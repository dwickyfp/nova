"""Explorer lists only what the caller's engine session (Ranger) shows."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.modules.explorer.repository as repo_module
import app.modules.explorer.service as module
from app.core.exceptions import InsufficientRoleError

USER = {"username": "analyst", "encrypted_password": "encrypted", "active_role": "marketing"}


class FakeCaller:
    def __init__(self, *, catalogs=None, databases=None, relations=None, functions=None):
        self._catalogs = catalogs or [
            {"name": "default_catalog", "type": "Internal", "comment": None}
        ]
        self._databases = databases or {}
        self._relations = relations or {}
        self._functions = functions or {}

    async def catalogs(self):
        return self._catalogs

    async def databases(self, catalog):
        return self._databases.get(catalog, [])

    async def relations(self, catalog, database):
        return self._relations.get((catalog, database), {})

    async def function_names(self, database):
        return self._functions.get(database, set())


def install_caller(monkeypatch, caller: FakeCaller) -> list[dict]:
    opened: list[dict] = []

    @contextlib.asynccontextmanager
    async def fake(user):
        opened.append(user)
        yield caller

    monkeypatch.setattr(module, "caller_metadata", fake)
    return opened


def install_store(monkeypatch, catalogs=None, objects=None, columns=None):
    """Fake the Nova Catalog store the explorer reads descriptions from."""
    store = {
        "list_catalogs": AsyncMock(return_value=catalogs or []),
        "list_objects": AsyncMock(return_value=objects or []),
        "list_columns": AsyncMock(return_value=columns or []),
    }
    for name, mock in store.items():
        monkeypatch.setattr(module.catalog_repo, name, mock)
    return store


def install_repo(monkeypatch, **results):
    names = (
        "list_tables", "list_views", "list_materialized_views", "list_functions",
        "list_pipes", "list_stages", "list_tasks", "get_table_detail", "get_view_detail",
        "get_mv_detail", "get_function_detail", "get_pipe_detail",
    )
    mocks = {}
    for name in names:
        default = [] if name.startswith("list") else None
        mocks[name] = AsyncMock(return_value=results.get(name, default))
        monkeypatch.setattr(module.explorer_repo, name, mocks[name])
    install_store(monkeypatch)
    monkeypatch.setattr(module, "decrypt_password", lambda value: "pw")
    monkeypatch.setattr(module.entity_registry, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.semantic_view_service, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.feature_store, "list_views", AsyncMock(return_value=[]))
    return mocks


@pytest.mark.asyncio
async def test_catalog_tree_comes_from_the_callers_session(monkeypatch):
    caller = FakeCaller(
        catalogs=[
            {"name": "default_catalog", "type": "Internal", "comment": None},
            {"name": "pg_sales", "type": "Jdbc", "comment": None},
        ],
        databases={"default_catalog": ["analytics"], "pg_sales": ["public"]},
    )
    opened = install_caller(monkeypatch, caller)
    execute_system = AsyncMock()
    monkeypatch.setattr(repo_module.db, "execute_system", execute_system)
    install_store(monkeypatch, catalogs=[
        {"name": "pg_sales", "managed": False, "source": "engine", "status": "ok",
         "last_sync_at": None},
        {"name": "hidden_lake", "managed": True, "source": "nova", "status": "ok"},
    ])

    result = await module.ExplorerService().get_catalogs(USER)

    assert [(c.name, c.databases) for c in result.catalogs] == [
        ("default_catalog", ["analytics"]),
        ("pg_sales", ["public"]),
    ]
    # The store describes catalogs; it never adds one the caller cannot list.
    assert result.catalogs[1].source == "engine"
    assert result.catalogs[1].sync_status == "ok"
    assert opened == [USER]
    execute_system.assert_not_awaited()


@pytest.mark.asyncio
async def test_hidden_database_returns_nothing_and_reads_no_system_metadata(monkeypatch):
    install_caller(monkeypatch, FakeCaller(databases={"default_catalog": ["analytics"]}))
    mocks = install_repo(monkeypatch, list_stages=[{"name": "s", "schema_name": "public"}])

    result = await module.ExplorerService().get_database_objects("finance", user=USER)

    assert result is None
    for name in ("list_tables", "list_views", "list_stages", "list_tasks", "list_pipes"):
        mocks[name].assert_not_awaited()


@pytest.mark.asyncio
async def test_database_objects_keep_only_relations_the_caller_lists(monkeypatch):
    install_caller(monkeypatch, FakeCaller(
        databases={"default_catalog": ["analytics"]},
        relations={("default_catalog", "analytics"): {
            "sales": "BASE TABLE", "sales_view": "VIEW", "sales_mv": "VIEW",
        }},
        functions={"analytics": {"allowed_fn"}},
    ))
    install_repo(
        monkeypatch,
        list_tables=[{"name": "sales"}, {"name": "salaries"}],
        list_views=[{"name": "sales_view"}, {"name": "salary_view"}],
        list_materialized_views=[{"name": "sales_mv"}, {"name": "salary_mv"}],
        list_functions=[{"name": "allowed_fn"}, {"name": "secret_fn"}],
        list_pipes=[
            {"name": "load_sales", "target_table": "analytics.sales"},
            {"name": "load_salaries", "target_table": "salaries"},
        ],
    )

    result = await module.ExplorerService().get_database_objects("analytics", user=USER)

    assert [t.name for t in result.tables] == ["sales"]
    assert [v.name for v in result.views] == ["sales_view"]
    assert [m.name for m in result.materialized_views] == ["sales_mv"]
    assert [f.name for f in result.functions] == ["allowed_fn"]
    assert [p.name for p in result.pipes] == ["load_sales"]
    assert result.summary["tables"] == 1


@pytest.mark.asyncio
async def test_external_catalog_lists_its_own_tables_without_internal_objects(monkeypatch):
    install_caller(monkeypatch, FakeCaller(
        databases={"pg_sales": ["public"]},
        relations={("pg_sales", "public"): {"orders": "BASE TABLE"}},
    ))
    mocks = install_repo(monkeypatch, list_views=[{"name": "internal_view"}])

    result = await module.ExplorerService().get_database_objects(
        "public", catalog="pg_sales", user=USER
    )

    assert [t.name for t in result.tables] == ["orders"]
    assert result.views == []
    mocks["list_views"].assert_not_awaited()
    mocks["list_stages"].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "name", "repo_method"),
    [
        ("get_table_detail", "salaries", "get_table_detail"),
        ("get_view_detail", "salary_view", "get_view_detail"),
        ("get_mv_detail", "salary_mv", "get_mv_detail"),
        ("get_function_detail", "secret_fn", "get_function_detail"),
    ],
)
async def test_detail_of_an_object_the_caller_cannot_list_is_not_read(
    monkeypatch, method, name, repo_method
):
    install_caller(monkeypatch, FakeCaller(
        relations={("default_catalog", "analytics"): {"sales": "BASE TABLE"}},
    ))
    mocks = install_repo(monkeypatch)

    result = await getattr(module.ExplorerService(), method)("analytics", name, user=USER)

    assert result is None
    mocks[repo_method].assert_not_awaited()


@pytest.mark.asyncio
async def test_pipe_detail_requires_a_visible_target_table(monkeypatch):
    install_caller(monkeypatch, FakeCaller(
        relations={("default_catalog", "analytics"): {"sales": "BASE TABLE"}},
    ))
    pipe = {"name": "load_salaries", "database": "analytics", "pipe_id": 1,
            "state": "RUNNING", "target_table": "salaries", "properties": {}}
    install_repo(monkeypatch, get_pipe_detail=pipe)

    assert await module.ExplorerService().get_pipe_detail(
        "analytics", "load_salaries", user=USER
    ) is None


@pytest.mark.asyncio
async def test_router_answers_404_for_a_database_the_caller_cannot_see(monkeypatch):
    from app.modules.explorer import router

    monkeypatch.setattr(
        router.explorer_service, "get_database_objects", AsyncMock(return_value=None)
    )
    with pytest.raises(HTTPException) as missing:
        await router.list_database_objects("finance", USER)
    assert missing.value.status_code == 404


class FakeCursor:
    def __init__(self, refuse: set[str] | None = None):
        self.executed: list[str] = []
        self._refuse = refuse or set()
        self._last = ""

    async def execute(self, sql):
        self.executed.append(sql)
        self._last = sql
        if any(sql.startswith(prefix) for prefix in self._refuse):
            raise RuntimeError("Access denied")

    async def fetchall(self):
        if self._last.startswith("SHOW DATABASES"):
            return [("analytics",), ("information_schema",)]
        if self._last.startswith("SHOW FULL TABLES"):
            return [("sales", "BASE TABLE"), ("sales_mv", "VIEW")]
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


@pytest.mark.asyncio
async def test_caller_session_activates_the_role_and_hides_system_databases(monkeypatch):
    cursor = FakeCursor()
    connections: list[tuple[str, str]] = []

    @contextlib.asynccontextmanager
    async def user_conn(username, password, database=None):
        connections.append((username, password))
        yield FakeConnection(cursor)

    monkeypatch.setattr(repo_module.db, "user_conn", user_conn)
    monkeypatch.setattr(repo_module, "decrypt_password", lambda value: "pw")

    async with repo_module.caller_metadata(USER) as caller:
        databases = await caller.databases("default_catalog")
        relations = await caller.relations("default_catalog", "analytics")

    assert connections == [("analyst", "pw")]
    assert cursor.executed[0] == "SET ROLE marketing"
    assert databases == ["analytics"]
    assert relations == {"sales": "BASE TABLE", "sales_mv": "VIEW"}


@pytest.mark.asyncio
async def test_refused_metadata_statement_hides_the_object():
    caller = repo_module.CallerMetadata(FakeCursor(refuse={"SHOW FULL TABLES"}))
    assert await caller.relations("default_catalog", "analytics") == {}


@pytest.mark.asyncio
async def test_caller_session_requires_an_active_role(monkeypatch):
    user_conn = AsyncMock()
    monkeypatch.setattr(repo_module.db, "user_conn", user_conn)
    with pytest.raises(InsufficientRoleError):
        async with repo_module.caller_metadata({**USER, "active_role": None}):
            pass
    user_conn.assert_not_called()


@pytest.mark.asyncio
async def test_external_tables_are_described_by_the_store(monkeypatch):
    install_caller(monkeypatch, FakeCaller(
        databases={"lake": ["sales"]},
        relations={("lake", "sales"): {"orders": "BASE TABLE", "stale": "BASE TABLE"}},
    ))
    install_repo(monkeypatch)
    install_store(monkeypatch, objects=[
        {"object_name": "orders", "object_type": "table", "comment": "Orders",
         "row_count": 12},
        {"object_name": "secret", "object_type": "table", "comment": "Not listed"},
    ])

    result = await module.ExplorerService().get_database_objects(
        "sales", catalog="lake", user=USER
    )

    assert [(t.name, t.comment, t.row_count) for t in result.tables] == [
        ("orders", "Orders", 12),
        ("stale", None, None),
    ]


@pytest.mark.asyncio
async def test_external_table_detail_uses_stored_columns_after_the_gate(monkeypatch):
    install_caller(monkeypatch, FakeCaller(
        databases={"lake": ["sales"]},
        relations={("lake", "sales"): {"orders": "BASE TABLE"}},
    ))
    mocks = install_repo(monkeypatch)
    store = install_store(monkeypatch, columns=[
        {"ordinal": 1, "column_name": "id", "data_type": "bigint", "is_nullable": False,
         "column_comment": None},
    ])

    detail = await module.ExplorerService().get_table_detail(
        "sales", "orders", user=USER, catalog="lake"
    )
    hidden = await module.ExplorerService().get_table_detail(
        "sales", "salaries", user=USER, catalog="lake"
    )

    assert detail.catalog == "lake"
    assert [(c.name, c.data_type, c.is_nullable) for c in detail.columns] == [
        ("id", "bigint", "NO"),
    ]
    assert hidden is None
    store["list_columns"].assert_awaited_once_with("lake", "sales", "orders")
    mocks["get_table_detail"].assert_not_awaited()
