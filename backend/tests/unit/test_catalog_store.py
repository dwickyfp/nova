"""Catalog store, sync, visibility cache, refresh gate, and API.

The engine and ``NOVA_SYSTEM`` are faked: the repository tests record the SQL
sent to the system pool, and the sync tests run against an in-memory store.
"""

from __future__ import annotations

import contextlib
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.modules.catalog import refresher, service, sync, visibility
from app.modules.catalog import repository as repo_module
from app.modules.catalog.repository import CatalogRepository, row_id
from app.modules.catalog.router import router
from app.modules.catalog.sync import EngineHarvester, Harvest, Relation

ADMIN = {
    "username": "nova_admin",
    "encrypted_password": "enc",
    "session_id": "s1",
    "active_role": "ACCOUNTADMIN",
    "assigned_roles": ["ACCOUNTADMIN", "marketing"],
    "security_context_version": 1,
}
ANALYST = {**ADMIN, "username": "alice", "active_role": "marketing"}


@pytest.fixture(autouse=True)
def no_securable_registry(monkeypatch):
    """The securable registry has its own tests (``test_catalog_metadata``)."""
    monkeypatch.setattr(sync.securables, "record_harvest", AsyncMock(return_value={}))
    monkeypatch.setattr(sync.securables, "record_catalogs", AsyncMock())
    monkeypatch.setattr(refresher, "metadata_pass", AsyncMock())


# ── Repository: SQL sent to the system pool ───────────────────────────────


class RecordingDb:
    def __init__(self, results=None):
        self.calls: list[tuple[str, list | None]] = []
        self._results = list(results or [])

    async def execute_system(self, sql, params=None):
        self.calls.append((sql, params))
        if self._results:
            result = self._results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return {"rows": []}


@pytest.fixture
def recording_db(monkeypatch):
    fake = RecordingDb()
    monkeypatch.setattr(repo_module.db, "execute_system", fake.execute_system)
    return fake


async def test_schema_creates_the_four_flat_tables(recording_db):
    await CatalogRepository().ensure_schema()
    created = [sql for sql, _ in recording_db.calls]
    for table in ("CATALOG_CATALOGS", "CATALOG_OBJECTS", "CATALOG_COLUMNS",
                  "CATALOG_SYNC_RUNS"):
        assert any(f"NOVA_SYSTEM.{table} (" in sql for sql in created), table
    assert all("CREATE TABLE IF NOT EXISTS" in sql for sql in created)


async def test_upsert_drops_credential_properties(recording_db):
    await CatalogRepository().upsert_catalog(
        {
            "name": "lake",
            "kind": "external",
            "catalog_type": "iceberg",
            "managed": True,
            "source": "nova",
            "properties": {
                "aws.s3.secret_key": "LEAK_SECRET",
                "hive.metastore.password": "LEAK_PW",
                "iceberg.catalog.warehouse": "s3://wh",
            },
        }
    )
    sql, params = recording_db.calls[-1]
    assert sql.startswith("INSERT INTO NOVA_SYSTEM.CATALOG_CATALOGS")
    assert "LEAK_SECRET" not in str(params) and "LEAK_PW" not in str(params)
    assert json.dumps({"iceberg.catalog.warehouse": "s3://wh"}) in params


async def test_read_drops_credential_properties_already_stored(monkeypatch):
    columns = repo_module._CATALOG_COLUMNS
    row = [None] * len(columns)
    row[columns.index("name")] = "lake"
    row[columns.index("properties_json")] = json.dumps(
        {"azure.account.key": "LEAK", "warehouse": "s3://wh"}
    )
    fake = RecordingDb([{"rows": [row]}])
    monkeypatch.setattr(repo_module.db, "execute_system", fake.execute_system)
    catalog = await CatalogRepository().get_catalog("lake")
    assert catalog["properties"] == {"warehouse": "s3://wh"}


async def test_insert_many_batches(recording_db):
    rows = [{"id": str(i), "catalog_name": "c"} for i in range(450)]
    await CatalogRepository().upsert_objects(rows)
    inserts = [params for sql, params in recording_db.calls if "INSERT" in sql]
    width = len(repo_module._OBJECT_COLUMNS)
    assert [len(params) // width for params in inserts] == [200, 200, 50]


async def test_legacy_rows_are_copied_once(monkeypatch):
    legacy = {
        "rows": [
            ["old_lake", "iceberg", "hms", "thrift://hms", "production", None,
             json.dumps({"aws.s3.access_key": "LEAK", "warehouse": "s3://wh"}), None,
             "nova_admin"],
            ["known", "hive", "hms", "thrift://hms", None, None, "{}", None, "x"],
        ]
    }
    known_columns = repo_module._CATALOG_COLUMNS
    known_row = [None] * len(known_columns)
    known_row[known_columns.index("name")] = "known"
    fake = RecordingDb([legacy, {"rows": [known_row]}])
    monkeypatch.setattr(repo_module.db, "execute_system", fake.execute_system)

    copied = await CatalogRepository().migrate_legacy_catalogs(sync.nova_now())

    assert copied == 1
    inserts = [params for sql, params in fake.calls if sql.startswith("INSERT")]
    assert len(inserts) == 1 and inserts[0][0] == "old_lake"
    assert "LEAK" not in str(inserts[0])


async def test_missing_legacy_table_copies_nothing(monkeypatch):
    fake = RecordingDb([RuntimeError("Unknown table")])
    monkeypatch.setattr(repo_module.db, "execute_system", fake.execute_system)
    assert await CatalogRepository().migrate_legacy_catalogs(sync.nova_now()) == 0


# ── Sync against an in-memory store ──────────────────────────────────────


class MemoryRepo:
    def __init__(self, catalogs=None):
        self.catalogs = {row["name"]: dict(row) for row in catalogs or []}
        self.objects: dict[str, dict] = {}
        self.columns: dict[tuple, list] = {}
        self.runs: list[dict] = []
        self.column_writes = 0

    async def get_catalog(self, name):
        return dict(self.catalogs[name]) if name in self.catalogs else None

    async def list_catalogs(self, *, include_dropped=False):
        return [
            dict(row) for row in self.catalogs.values()
            if include_dropped or row.get("dropped_at") is None
        ]

    async def upsert_catalog(self, row):
        self.catalogs[row["name"]] = dict(row)

    async def list_objects(self, catalog, database=None, *, include_dropped=False):
        return [
            dict(row) for row in self.objects.values()
            if row["catalog_name"] == catalog
            and (database is None or row["database_name"] == database)
            and (include_dropped or row.get("dropped_at") is None)
        ]

    async def upsert_objects(self, rows):
        for row in rows:
            self.objects[row["id"]] = dict(row)

    async def mark_objects_dropped(self, ids, at):
        for key in ids:
            self.objects[key]["dropped_at"] = at

    async def catalogs_with_live_objects(self):
        return {row["catalog_name"] for row in self.objects.values()
                if row.get("dropped_at") is None}

    async def retire_catalog_objects(self, catalog, at):
        for row in self.objects.values():
            if row["catalog_name"] == catalog and row.get("dropped_at") is None:
                row["dropped_at"] = at
        self.columns = {key: cols for key, cols in self.columns.items() if key[0] != catalog}

    async def replace_columns(self, catalog, database, obj, columns):
        self.column_writes += 1
        self.columns[(catalog, database, obj)] = list(columns)

    async def insert_columns(self, rows):
        for row in rows:
            key = (row["catalog_name"], row["database_name"], row["object_name"])
            self.columns.setdefault(key, []).append(row)
        self.column_writes += 1 if rows else 0

    async def delete_columns(self, catalog, database, obj):
        self.columns.pop((catalog, database, obj), None)

    async def write_sync_run(self, run):
        self.runs.append(dict(run))


class FakeHarvester:
    def __init__(self, catalogs=None, harvests=None, error=None):
        self._catalogs = catalogs or []
        self.harvests = harvests or {}
        self.error = error

    async def catalogs(self):
        return self._catalogs

    async def harvest(self, name):
        if self.error:
            raise self.error
        return self.harvests[name]


def _orders(columns=(("id", "bigint"),)):
    return Relation(
        database="sales",
        name="orders",
        object_type="table",
        columns=[
            {"ordinal": i, "column_name": n, "data_type": t, "is_nullable": True,
             "column_comment": None}
            for i, (n, t) in enumerate(columns, start=1)
        ],
    )


async def test_sync_records_objects_columns_and_the_run():
    repo = MemoryRepo([{"name": "lake", "catalog_type": "iceberg"}])
    harvester = FakeHarvester(harvests={"lake": Harvest(["sales"], [_orders()])})

    run = await sync.sync_catalog("lake", repo=repo, harvester=harvester)

    assert run["status"] == "ok"
    assert run["counts"] == {"databases": 1, "relations": 1, "columns_rewritten": 1,
                             "dropped": 0}
    assert {(o["database_name"], o["object_name"], o["object_type"])
            for o in repo.objects.values()} == {("sales", "", "database"),
                                                ("sales", "orders", "table")}
    assert repo.columns[("lake", "sales", "orders")][0]["column_name"] == "id"
    assert repo.catalogs["lake"]["status"] == "ok"
    assert repo.catalogs["lake"]["last_sync_at"] is not None
    assert repo.runs[-1]["id"] == run["id"]


async def test_unchanged_columns_are_not_rewritten_and_changes_are():
    repo = MemoryRepo([{"name": "lake", "catalog_type": "iceberg"}])
    harvester = FakeHarvester(harvests={"lake": Harvest(["sales"], [_orders()])})
    await sync.sync_catalog("lake", repo=repo, harvester=harvester)
    await sync.sync_catalog("lake", repo=repo, harvester=harvester)
    assert repo.column_writes == 1

    harvester.harvests["lake"] = Harvest(["sales"], [_orders((("id", "bigint"),
                                                               ("total", "double")))])
    await sync.sync_catalog("lake", repo=repo, harvester=harvester)
    assert repo.column_writes == 2
    assert len(repo.columns[("lake", "sales", "orders")]) == 2


async def test_objects_no_longer_seen_are_marked_dropped_and_return():
    repo = MemoryRepo([{"name": "lake", "catalog_type": "iceberg"}])
    harvester = FakeHarvester(harvests={"lake": Harvest(["sales"], [_orders()])})
    await sync.sync_catalog("lake", repo=repo, harvester=harvester)

    harvester.harvests["lake"] = Harvest(["sales"], [])
    run = await sync.sync_catalog("lake", repo=repo, harvester=harvester)
    orders = repo.objects[row_id("lake", "sales", "orders", "relation")]
    assert run["counts"]["dropped"] == 1
    assert orders["dropped_at"] is not None
    assert ("lake", "sales", "orders") not in repo.columns

    harvester.harvests["lake"] = Harvest(["sales"], [_orders()])
    await sync.sync_catalog("lake", repo=repo, harvester=harvester)
    orders = repo.objects[row_id("lake", "sales", "orders", "relation")]
    assert orders["dropped_at"] is None
    assert ("lake", "sales", "orders") in repo.columns


async def test_sync_error_is_redacted_on_the_run_and_the_catalog():
    repo = MemoryRepo([{"name": "lake", "catalog_type": "iceberg"}])
    harvester = FakeHarvester(
        error=RuntimeError("failed: 'aws.s3.secret_key' = 'SECRET_SENTINEL'")
    )

    run = await sync.sync_catalog("lake", repo=repo, harvester=harvester)

    assert run["status"] == "error"
    assert "SECRET_SENTINEL" not in run["error"]
    assert "SECRET_SENTINEL" not in repo.catalogs["lake"]["last_sync_error"]
    assert repo.catalogs["lake"]["status"] == "error"


async def test_reconcile_records_raw_sql_catalogs_and_marks_missing_ones():
    repo = MemoryRepo([
        {"name": "default_catalog", "catalog_type": "internal", "kind": "native"},
        {"name": "gone", "catalog_type": "hive", "kind": "external"},
    ])
    harvester = FakeHarvester(
        catalogs=[
            {"name": "default_catalog", "type": "Internal", "comment": None},
            {"name": "raw_lake", "type": "Iceberg", "comment": "made in SQL"},
        ],
        harvests={
            "default_catalog": Harvest([], []),
            "raw_lake": Harvest(["sales"], [_orders()]),
        },
    )

    runs = await sync.reconcile_catalogs(repo=repo, harvester=harvester)

    raw = repo.catalogs["raw_lake"]
    assert (raw["source"], raw["managed"], raw["kind"], raw["catalog_type"]) == (
        "engine", False, "external", "iceberg"
    )
    assert repo.catalogs["gone"]["dropped_at"] is not None
    assert {run["catalog_name"] for run in runs} == {"default_catalog", "raw_lake"}


async def test_reconcile_retires_the_objects_and_columns_of_a_vanished_catalog():
    repo = MemoryRepo([{"name": "lake", "catalog_type": "iceberg", "kind": "external"}])
    harvester = FakeHarvester(
        catalogs=[{"name": "lake", "type": "Iceberg", "comment": None}],
        harvests={"lake": Harvest(["sales"], [_orders()])},
    )
    await sync.reconcile_catalogs(repo=repo, harvester=harvester)
    assert ("lake", "sales", "orders") in repo.columns

    harvester._catalogs = []
    await sync.reconcile_catalogs(repo=repo, harvester=harvester)

    assert repo.catalogs["lake"]["dropped_at"] is not None
    assert repo.objects and all(o["dropped_at"] is not None for o in repo.objects.values())
    assert not repo.columns


async def test_reconcile_retires_objects_of_a_catalog_already_marked_dropped():
    # A drop through Nova marks the catalog row first; the next pass must
    # still retire the objects it left live.
    repo = MemoryRepo([{"name": "lake", "catalog_type": "iceberg", "kind": "external",
                        "dropped_at": sync.nova_now()}])
    key = row_id("lake", "sales", "orders", "relation")
    repo.objects[key] = {"id": key, "catalog_name": "lake", "database_name": "sales",
                         "object_name": "orders", "object_type": "table", "dropped_at": None}
    repo.columns[("lake", "sales", "orders")] = [{"column_name": "id"}]
    live = row_id("default_catalog", "d", "", "database")
    repo.objects[live] = {"id": live, "catalog_name": "default_catalog", "database_name": "d",
                          "object_name": "", "object_type": "database", "dropped_at": None}
    harvester = FakeHarvester(
        catalogs=[{"name": "default_catalog", "type": "Internal", "comment": None}],
        harvests={"default_catalog": Harvest(["d"], [])},
    )

    await sync.reconcile_catalogs(repo=repo, harvester=harvester)

    assert repo.objects[key]["dropped_at"] is not None
    assert ("lake", "sales", "orders") not in repo.columns
    assert repo.objects[live]["dropped_at"] is None


async def test_retire_catalog_objects_marks_live_rows_and_deletes_columns(recording_db):
    at = sync.nova_now()
    await CatalogRepository().retire_catalog_objects("lake", at)
    (update, update_params), (delete, delete_params) = recording_db.calls
    assert update.startswith("UPDATE NOVA_SYSTEM.CATALOG_OBJECTS SET dropped_at = %s")
    assert "catalog_name = %s AND dropped_at IS NULL" in update
    assert update_params == [at, "lake"]
    assert delete.startswith("DELETE FROM NOVA_SYSTEM.CATALOG_COLUMNS")
    assert delete_params == ["lake"]


async def test_reconcile_revives_a_recreated_catalog():
    repo = MemoryRepo([{"name": "lake", "catalog_type": "iceberg", "kind": "external",
                        "dropped_at": sync.nova_now()}])
    harvester = FakeHarvester(
        catalogs=[{"name": "lake", "type": "Iceberg", "comment": None}],
        harvests={"lake": Harvest([], [])},
    )
    await sync.reconcile_catalogs(repo=repo, harvester=harvester)
    assert repo.catalogs["lake"]["dropped_at"] is None


# ── Harvester: information_schema first, SHOW/DESC fallback ─────────────


async def test_internal_catalog_is_harvested_from_information_schema():
    responses = {
        "SHOW DATABASES": {"rows": [["analytics"], ["information_schema"], ["sys"]]},
        "information_schema.tables": {"rows": [
            ["analytics", "sales", "BASE TABLE", "StarRocks", 10, 100, ""],
            ["analytics", "sales_mv", "VIEW", None, None, None, ""],
            ["analytics", "v", "VIEW", None, None, None, "a view"],
        ]},
        "information_schema.columns": {"rows": [
            ["analytics", "sales", 1, "id", "bigint", "NO", ""],
            ["analytics", "sales", 2, "city", "varchar(32)", "YES", "City"],
        ]},
        "materialized_views": {"rows": [["analytics", "sales_mv"]]},
    }
    calls = []

    async def execute(sql, params=None):
        calls.append((sql, params))
        for marker, result in responses.items():
            if marker in sql:
                return result
        raise AssertionError(sql)

    harvest = await EngineHarvester(execute).harvest("default_catalog")

    assert harvest.databases == ["analytics"]
    kinds = {r.name: r.object_type for r in harvest.relations}
    assert kinds == {"sales": "table", "sales_mv": "materialized_view", "v": "view"}
    sales = next(r for r in harvest.relations if r.name == "sales")
    assert [(c["column_name"], c["is_nullable"]) for c in sales.columns] == [
        ("id", False), ("city", True)
    ]
    assert all(params in (None, ["analytics"]) for _, params in calls)


async def test_external_catalog_falls_back_to_show_and_desc():
    async def execute(sql, params=None):
        if sql.startswith("SHOW DATABASES"):
            return {"rows": [["sales"]]}
        if "information_schema" in sql:
            raise RuntimeError("information_schema not supported")
        if sql.startswith("SHOW FULL TABLES"):
            return {"rows": [["orders", "BASE TABLE"]]}
        if sql.startswith("DESC"):
            assert sql == "DESC `lake`.`sales`.`orders`"
            return {"rows": [["id", "bigint", "Yes", "true", None, ""]]}
        raise AssertionError(sql)

    harvest = await EngineHarvester(execute).harvest("lake")

    assert [(r.database, r.name, r.object_type) for r in harvest.relations] == [
        ("sales", "orders", "table")
    ]
    assert harvest.relations[0].columns[0]["column_name"] == "id"


async def test_harvest_refuses_an_unsafe_catalog_name():
    from app.common.identifiers import InvalidIdentifierError

    with pytest.raises(InvalidIdentifierError):
        await EngineHarvester(AsyncMock()).harvest("x`; DROP TABLE t; --")


# ── Visibility cache ─────────────────────────────────────────────────────


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def incr(self, key):
        self.data[key] = str(int(self.data.get(key, "0")) + 1)


def _opener(counter):
    class Caller:
        async def catalogs(self):
            return [{"name": "default_catalog", "type": "Internal", "comment": None}]

        async def databases(self, catalog):
            return ["analytics"]

    @contextlib.asynccontextmanager
    async def opener(user):
        counter.append(user["active_role"])
        yield Caller()

    return opener


async def test_visibility_is_cached_per_security_context(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(visibility.session_store, "_redis", redis)
    opened: list[str] = []
    opener = _opener(opened)

    await visibility.visible_catalogs(ADMIN, opener)
    await visibility.visible_catalogs(ADMIN, opener)
    assert opened == ["ACCOUNTADMIN"]

    # Another role, or a newer security context, never reads that answer.
    await visibility.visible_catalogs(ANALYST, opener)
    await visibility.visible_catalogs({**ADMIN, "security_context_version": 2}, opener)
    assert opened == ["ACCOUNTADMIN", "marketing", "ACCOUNTADMIN"]

    await visibility.bump()
    await visibility.visible_catalogs(ADMIN, opener)
    assert opened[-1] == "ACCOUNTADMIN" and len(opened) == 4
    assert all("SECRET" not in value for value in redis.data.values())


async def test_without_redis_every_call_asks_the_engine(monkeypatch):
    monkeypatch.setattr(visibility.session_store, "_redis", None)
    opened: list[str] = []
    await visibility.visible_catalogs(ADMIN, _opener(opened))
    await visibility.visible_catalogs(ADMIN, _opener(opened))
    assert len(opened) == 2


async def test_a_session_without_an_active_role_is_refused(monkeypatch):
    from app.core.exceptions import InsufficientRoleError

    monkeypatch.setattr(visibility.session_store, "_redis", FakeRedis())
    with pytest.raises(InsufficientRoleError):
        await visibility.visible_catalogs({**ADMIN, "active_role": None})


# ── Refresh gate ─────────────────────────────────────────────────────────


@pytest.fixture
def audits(monkeypatch):
    records: list[dict] = []

    async def write(**kwargs):
        records.append(kwargs)

    monkeypatch.setattr(service, "write_audit_log", write)
    monkeypatch.setattr(service.visibility, "bump", AsyncMock())
    return records


async def test_refresh_requires_a_security_admin_role_and_audits_the_refusal(
    monkeypatch, audits
):
    reconcile = AsyncMock()
    monkeypatch.setattr(service, "reconcile_catalogs", reconcile)

    with pytest.raises(service.CatalogAccessDenied):
        await service.CatalogService(MemoryRepo()).refresh(None, ANALYST)

    reconcile.assert_not_awaited()
    assert audits[-1]["status"] == "DENIED"
    assert audits[-1]["action"] == "REFRESH_METADATA"


async def test_admin_refresh_syncs_and_audits(monkeypatch, audits):
    repo = MemoryRepo([{"name": "lake", "catalog_type": "iceberg"}])
    run = {"id": "r1", "catalog_name": "lake", "trigger": "manual", "status": "ok"}
    sync_one = AsyncMock(return_value=run)
    monkeypatch.setattr(service, "sync_catalog", sync_one)

    runs = await service.CatalogService(repo).refresh("lake", ADMIN)

    assert [r.id for r in runs] == ["r1"]
    sync_one.assert_awaited_once()
    assert audits[-1]["status"] == "SUCCESS"
    assert audits[-1]["object_name"] == "lake"


async def test_refresh_of_an_unknown_catalog_is_an_error(monkeypatch, audits):
    monkeypatch.setattr(service, "reconcile_catalogs", AsyncMock(return_value=[]))
    with pytest.raises(service.CatalogError):
        await service.CatalogService(MemoryRepo()).refresh("nowhere", ADMIN)
    assert audits[-1]["status"] == "ERROR"


# ── Refresher loop ───────────────────────────────────────────────────────


async def test_refresher_skips_without_redis_or_the_lock(monkeypatch):
    reconcile = AsyncMock()
    monkeypatch.setattr(refresher, "reconcile_catalogs", reconcile)

    monkeypatch.setattr(refresher.session_store, "_redis", None)
    assert await refresher.refresh_once() is False

    class Lost:
        async def set(self, *args, **kwargs):
            return False

    monkeypatch.setattr(refresher.session_store, "_redis", Lost())
    assert await refresher.refresh_once() is False
    reconcile.assert_not_awaited()


async def test_refresher_runs_one_pass_under_the_lock(monkeypatch):
    reconcile = AsyncMock()
    monkeypatch.setattr(refresher, "reconcile_catalogs", reconcile)

    class Won:
        released = False

        async def set(self, *args, **kwargs):
            return True

        async def eval(self, *args):
            Won.released = True
            return 1

    monkeypatch.setattr(refresher.session_store, "_redis", Won())
    assert await refresher.refresh_once() is True
    reconcile.assert_awaited_once_with(trigger="schedule")
    assert Won.released


# ── API ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/catalog")
    app.dependency_overrides[get_current_user] = lambda: ANALYST
    return TestClient(app)


def test_listing_returns_only_what_the_service_lists(client, monkeypatch):
    from app.modules.catalog.schemas import CatalogListResponse, CatalogSummary

    listing = CatalogListResponse(
        catalogs=[CatalogSummary(name="default_catalog", kind="native",
                                 catalog_type="internal")],
        count=1,
    )
    monkeypatch.setattr(service.catalog_service, "list_catalogs",
                        AsyncMock(return_value=listing))
    response = client.get("/api/v1/catalog/catalogs")
    assert response.status_code == 200
    assert [c["name"] for c in response.json()["catalogs"]] == ["default_catalog"]


def test_unknown_catalog_is_404_and_bad_name_is_400(client, monkeypatch):
    monkeypatch.setattr(service.catalog_service, "get_catalog",
                        AsyncMock(return_value=None))
    assert client.get("/api/v1/catalog/catalogs/hidden").status_code == 404
    monkeypatch.undo()
    monkeypatch.setattr(service.visibility, "visible_catalogs", AsyncMock(return_value=[]))
    assert client.get("/api/v1/catalog/catalogs/bad-name").status_code == 400


def test_refresh_by_a_non_admin_is_403(client, monkeypatch):
    monkeypatch.setattr(
        service.catalog_service,
        "refresh",
        AsyncMock(side_effect=service.CatalogAccessDenied("not an admin")),
    )
    assert client.post("/api/v1/catalog/catalogs/refresh").status_code == 403
    assert client.post("/api/v1/catalog/catalogs/lake/refresh").status_code == 403


# ── Service read paths and remaining routes ──────────────────────────────


VISIBLE = [{"name": "lake", "type": "Iceberg", "comment": None, "databases": ["sales"]}]


@pytest.fixture
def lake_visible(monkeypatch):
    monkeypatch.setattr(service.visibility, "visible_catalogs", AsyncMock(return_value=VISIBLE))
    monkeypatch.setattr(
        service.visibility, "visible_relations",
        AsyncMock(return_value={"orders": "BASE TABLE", "orders_v": "VIEW"}),
    )
    monkeypatch.setattr(
        service.visibility, "visible_databases", AsyncMock(return_value=["sales"])
    )


async def test_listing_merges_store_rows_into_visible_catalogs(lake_visible):
    repo = MemoryRepo([
        {"name": "lake", "kind": "external", "catalog_type": "iceberg", "managed": True,
         "source": "nova", "status": "ok"},
        {"name": "hidden", "kind": "external", "catalog_type": "hive"},
    ])
    listing = await service.CatalogService(repo).list_catalogs(ANALYST)
    assert [(c.name, c.managed, c.source, c.databases) for c in listing.catalogs] == [
        ("lake", True, "nova", ["sales"])
    ]


async def test_objects_and_columns_come_only_for_listed_relations(lake_visible):
    repo = MemoryRepo()
    repo.objects["x"] = {"id": "x", "catalog_name": "lake", "database_name": "sales",
                         "object_name": "orders", "object_type": "table", "comment": "Orders"}
    repo.list_columns = AsyncMock(return_value=[
        {"ordinal": 1, "column_name": "id", "data_type": "bigint", "is_nullable": False,
         "column_comment": None}
    ])
    svc = service.CatalogService(repo)

    objects = await svc.list_objects("lake", "sales", ANALYST)
    assert [(o.object_name, o.object_type, o.comment) for o in objects] == [
        ("orders", "table", "Orders"), ("orders_v", "view", None)
    ]
    assert await svc.list_objects("lake", "finance", ANALYST) is None
    assert [c.name for c in await svc.columns("lake", "sales", "orders", ANALYST)] == ["id"]
    assert await svc.columns("lake", "sales", "salaries", ANALYST) is None


async def test_a_database_made_after_the_cached_listing_is_rechecked(
    lake_visible, monkeypatch
):
    """A database created in raw SQL after the cached listing is found by asking
    the engine again as the caller, not refused until the cache expires."""
    fresh = AsyncMock(return_value=["sales", "events"])
    monkeypatch.setattr(service.visibility, "visible_databases", fresh)

    objects = await service.CatalogService(MemoryRepo()).list_objects("lake", "events", ANALYST)

    assert objects is not None
    fresh.assert_awaited_once_with(ANALYST, "lake", fresh=True)
    fresh.return_value = ["sales"]
    assert await service.CatalogService(MemoryRepo()).list_objects(
        "lake", "missing", ANALYST
    ) is None


async def test_fresh_visibility_skips_the_cached_answer(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.store = {}

        async def get(self, key):
            return self.store.get(key)

        async def set(self, key, value, ex=None):
            self.store[key] = value

    redis = FakeRedis()
    monkeypatch.setattr(visibility.session_store, "_redis", redis)
    monkeypatch.setattr(visibility.settings, "CATALOG_VISIBILITY_CACHE_SECONDS", 60)
    answers = [["sales"], ["sales", "events"]]

    @contextlib.asynccontextmanager
    async def opener(user):
        class Caller:
            async def databases(self, catalog):
                return answers.pop(0)

        yield Caller()

    assert await visibility.visible_databases(ADMIN, "lake", opener) == ["sales"]
    assert await visibility.visible_databases(ADMIN, "lake", opener, fresh=True) == [
        "sales", "events"
    ]
    # The fresh answer replaces the cached one for later readers.
    assert await visibility.visible_databases(ADMIN, "lake", opener) == ["sales", "events"]


async def test_sync_runs_need_a_visible_catalog(lake_visible):
    repo = MemoryRepo()
    repo.list_sync_runs = AsyncMock(return_value=[
        {"id": "r1", "catalog_name": "lake", "trigger": "manual", "status": "ok",
         "counts": {"relations": 1}}
    ])
    svc = service.CatalogService(repo)
    assert [r.id for r in await svc.sync_runs("lake", ANALYST)] == ["r1"]
    assert await svc.sync_runs("hidden", ANALYST) is None


async def test_alter_runs_as_the_caller_and_keeps_the_definition(lake_visible, monkeypatch):
    from app.modules.catalog.schemas import ExternalCatalogAlter
    from app.modules.query.repository import QueryResult

    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        return QueryResult(executed_sql=kwargs["sql"], error=None)

    monkeypatch.setattr(service.query_service, "execute", execute)
    monkeypatch.setattr(service, "sync_catalog", AsyncMock())
    monkeypatch.setattr(service.visibility, "bump", AsyncMock())
    monkeypatch.setattr(service, "redacted_create_statement", AsyncMock(return_value=None))
    repo = MemoryRepo([{"name": "lake", "catalog_type": "iceberg", "comment": "old",
                        "properties": {"a": "1"}, "storage_connection": "production"}])

    detail = await service.CatalogService(repo).alter(
        "lake", ExternalCatalogAlter(properties={"b": "2"}), ANALYST
    )

    assert calls[0]["role"] == "marketing" and calls[0]["username"] == "alice"
    assert calls[0]["sql"] == "ALTER CATALOG lake SET ('b' = '2')"
    assert repo.catalogs["lake"]["properties"] == {"a": "1", "b": "2"}
    assert repo.catalogs["lake"]["comment"] == "old"
    assert detail is not None and detail.storage_connection == "production"


async def test_create_statement_is_redacted_or_absent(monkeypatch):
    monkeypatch.setattr(service.db, "execute_system", AsyncMock(return_value={
        "rows": [["lake", "CREATE EXTERNAL CATALOG lake PROPERTIES "
                          "('aws.s3.secret_key' = 'LEAK')"]]
    }))
    assert "LEAK" not in await service.redacted_create_statement("lake")
    monkeypatch.setattr(service.db, "execute_system", AsyncMock(side_effect=RuntimeError()))
    assert await service.redacted_create_statement("lake") is None


def _detail():
    from app.modules.catalog.schemas import CatalogDetail

    return CatalogDetail(name="lake", kind="external", catalog_type="iceberg")


def test_catalog_routes_map_outcomes_to_status_codes(client, monkeypatch):
    from app.modules.catalog.schemas import CatalogObject, SyncRun

    svc = service.catalog_service
    monkeypatch.setattr(svc, "get_catalog", AsyncMock(return_value=_detail()))
    monkeypatch.setattr(svc, "alter", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "drop", AsyncMock(side_effect=service.CatalogError("denied")))
    monkeypatch.setattr(svc, "sync_runs", AsyncMock(return_value=[
        SyncRun(id="r1", catalog_name="lake", trigger="manual", status="ok")
    ]))
    monkeypatch.setattr(svc, "list_objects", AsyncMock(return_value=[
        CatalogObject(catalog_name="lake", database_name="sales", object_name="orders",
                      object_type="table")
    ]))
    monkeypatch.setattr(svc, "columns", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "refresh", AsyncMock(return_value=[]))
    body = {"name": "lake", "type": "iceberg", "metastore_uri": "thrift://hms:9083"}

    assert client.get("/api/v1/catalog/catalogs/lake").json()["name"] == "lake"
    # Catalogs are created from a connection (/catalog/foreign-catalogs) only.
    assert client.post("/api/v1/catalog/catalogs", json=body).status_code == 405
    assert client.patch("/api/v1/catalog/catalogs/lake", json={"comment": "x"}).status_code == 404
    assert client.delete("/api/v1/catalog/catalogs/lake").status_code == 400
    assert client.get("/api/v1/catalog/catalogs/lake/sync-runs").json()["runs"][0]["id"] == "r1"
    objects = client.get("/api/v1/catalog/catalogs/lake/databases/sales/objects")
    assert objects.json()[0]["object_name"] == "orders"
    columns = client.get("/api/v1/catalog/catalogs/lake/databases/sales/objects/x/columns")
    assert columns.status_code == 404
    assert client.post("/api/v1/catalog/catalogs/lake/refresh").json() == {"runs": []}
    # A request can never carry a credential property.
    bad = {"properties": {"aws.s3.secret_key": "LEAK"}}
    assert client.patch("/api/v1/catalog/catalogs/lake", json=bad).status_code == 422


def test_unlisted_objects_and_unknown_runs_are_404(client, monkeypatch):
    svc = service.catalog_service
    monkeypatch.setattr(svc, "sync_runs", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "list_objects", AsyncMock(return_value=None))
    assert client.get("/api/v1/catalog/catalogs/x/sync-runs").status_code == 404
    assert client.get("/api/v1/catalog/catalogs/x/databases/d/objects").status_code == 404


# ── Source classification of foreign catalogs ───────────────────────────


def _source_env(monkeypatch, *, kinds=None, error=None):
    """A Postgres connection whose source reports ``kinds`` (or raises)."""
    from app.modules.catalog import connections
    from app.modules.catalog.sources import SOURCES, Credentials

    calls: dict = {}

    async def get(name):
        calls["connection"] = name
        return {"name": name, "connector_type": "postgres", "host": "pg", "port": 5432,
                "database_name": "shop", "options": {}, "secret_ref": f"nova://{name}"}

    async def resolve(connection):
        return Credentials("reader", "pw")

    async def read_objects(endpoint, credentials, databases):
        calls["read"] = (endpoint.host, credentials.username, list(databases))
        if error:
            raise error
        return kinds or {}

    monkeypatch.setattr(connections.connection_repo, "get", get)
    monkeypatch.setattr(connections.connection_service, "resolve_credentials", resolve)
    monkeypatch.setattr(SOURCES["postgres"], "read_objects", read_objects)
    return calls


def _pg_catalog():
    return {"name": "pg_shop", "catalog_type": "postgres", "kind": "foreign",
            "properties": {"nova.connection": "shop_pg"}}


def _pg_harvest():
    return Harvest(["public"], [
        Relation(database="public", name="orders", object_type="table", engine="JDBC"),
        Relation(database="public", name="jakarta_orders", object_type="table", engine="JDBC"),
    ])


async def test_foreign_catalog_relations_get_their_kinds_from_the_source(monkeypatch):
    from app.modules.catalog.sources import ObjectKind

    calls = _source_env(monkeypatch, kinds={
        ("public", "orders"): ObjectKind("table", "Partitioned table"),
        ("public", "jakarta_orders"): ObjectKind("view", "View"),
    })
    repo = MemoryRepo([_pg_catalog()])
    harvester = FakeHarvester(harvests={"pg_shop": _pg_harvest()})

    await sync.sync_catalog("pg_shop", repo=repo, harvester=harvester)

    stored = {o["object_name"]: o for o in repo.objects.values() if o["object_name"]}
    assert (stored["orders"]["object_type"], stored["orders"]["engine"]) == (
        "table", "Partitioned table"
    )
    assert (stored["jakarta_orders"]["object_type"], stored["jakarta_orders"]["engine"]) == (
        "view", "View"
    )
    assert calls == {"connection": "shop_pg", "read": ("pg", "reader", ["public"])}


async def test_an_unreachable_source_keeps_the_kinds_of_the_last_sync(monkeypatch):
    from app.modules.catalog.sources import ObjectKind

    _source_env(monkeypatch, kinds={("public", "jakarta_orders"): ObjectKind("view", "View")})
    repo = MemoryRepo([_pg_catalog()])
    harvester = FakeHarvester(harvests={"pg_shop": _pg_harvest()})
    await sync.sync_catalog("pg_shop", repo=repo, harvester=harvester)

    _source_env(monkeypatch, error=RuntimeError("password authentication failed: pw=SENTINEL"))
    harvester.harvests["pg_shop"] = _pg_harvest()
    run = await sync.sync_catalog("pg_shop", repo=repo, harvester=harvester)

    assert run["status"] == "ok"
    view = repo.objects[row_id("pg_shop", "public", "jakarta_orders", "relation")]
    assert (view["object_type"], view["engine"]) == ("view", "View")


async def test_catalogs_without_a_connection_are_not_classified(monkeypatch):
    calls = _source_env(monkeypatch)
    repo = MemoryRepo([{"name": "lake", "catalog_type": "iceberg"}])
    harvester = FakeHarvester(harvests={"lake": Harvest(["sales"], [_orders()])})

    await sync.sync_catalog("lake", repo=repo, harvester=harvester)

    assert calls == {}
    assert repo.objects[row_id("lake", "sales", "orders", "relation")]["object_type"] == "table"


@pytest.mark.parametrize("stored", [
    {},
    # A row an earlier sync rewrote with the engine's type is healed.
    {"catalog_type": "jdbc", "kind": "external"},
])
async def test_reconcile_keeps_a_foreign_catalogs_source_type(monkeypatch, stored):
    """The engine reports every foreign catalog as ``jdbc``; the store keeps
    ``postgres`` so the catalog is still presented and altered as its source."""
    _source_env(monkeypatch)
    repo = MemoryRepo([{**_pg_catalog(), **stored}])
    harvester = FakeHarvester(
        catalogs=[{"name": "pg_shop", "type": "Jdbc", "comment": None}],
        harvests={"pg_shop": Harvest([], [])},
    )

    await sync.reconcile_catalogs(repo=repo, harvester=harvester)

    stored = repo.catalogs["pg_shop"]
    assert (stored["catalog_type"], stored["kind"]) == ("postgres", "foreign")


async def test_reconcile_retypes_an_external_catalog_the_engine_changed():
    repo = MemoryRepo([{"name": "lake", "catalog_type": "hive", "kind": "external"}])
    harvester = FakeHarvester(
        catalogs=[{"name": "lake", "type": "Iceberg", "comment": None}],
        harvests={"lake": Harvest([], [])},
    )
    await sync.reconcile_catalogs(repo=repo, harvester=harvester)
    assert repo.catalogs["lake"]["catalog_type"] == "iceberg"


async def test_a_sources_own_system_schemas_are_not_synced(monkeypatch):
    _source_env(monkeypatch)
    repo = MemoryRepo([_pg_catalog()])
    harvester = FakeHarvester(harvests={"pg_shop": Harvest(
        ["public", "pg_catalog", "information_schema"],
        [Relation(database="public", name="orders", object_type="table"),
         Relation(database="pg_catalog", name="pg_class", object_type="table")],
    )})

    await sync.sync_catalog("pg_shop", repo=repo, harvester=harvester)

    assert {o["database_name"] for o in repo.objects.values()} == {"public"}
