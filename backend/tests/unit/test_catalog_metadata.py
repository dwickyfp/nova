"""Nova Catalog phase 3: securable ids, business metadata, search, lineage, links.

``NOVA_SYSTEM`` and the engine are faked: repositories are in memory, and the
caller's engine view is a fixed map of catalogs, databases, and relations.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.modules.catalog import access, governance, lineage, links, metadata, securables
from app.modules.catalog.governance import (
    GovernanceService,
    MetadataDenied,
    MetadataInvalid,
    MetadataNotFound,
)
from app.modules.catalog.metadata_schemas import AnnotationWrite, TagWrite
from app.modules.catalog.securables import (
    Securable,
    catalog_id,
    column_id,
    database_id,
    relation_id,
)
from app.modules.catalog.sync import Harvest, Relation

T0 = datetime(2026, 11, 5, 10, 0, 0)
T1 = datetime(2026, 11, 5, 11, 0, 0)

ADMIN = {
    "username": "nova_admin",
    "encrypted_password": "enc",
    "session_id": "s1",
    "active_role": "ACCOUNTADMIN",
    "assigned_roles": ["ACCOUNTADMIN", "marketing"],
    "security_context_version": 1,
}
ANALYST = {**ADMIN, "username": "alice", "active_role": "marketing",
           "assigned_roles": ["marketing"]}


# ── In-memory stores ─────────────────────────────────────────────────────


class MemorySecurables:
    def __init__(self, rows=()):
        self.rows: dict[str, Securable] = {row.id: row for row in rows}

    async def get(self, key):
        return self.rows.get(key)

    async def get_many(self, ids):
        return [self.rows[key] for key in dict.fromkeys(ids) if key in self.rows]

    async def children(self, parent_id):
        return sorted(
            (row for row in self.rows.values() if row.parent_id == parent_id),
            key=lambda row: (row.ordinal or 0, row.column_name),
        )

    async def in_catalog(self, catalog):
        return [row for row in self.rows.values() if row.catalog_name == catalog]

    async def of_type(self, kind):
        return [row for row in self.rows.values() if row.securable_type == kind]

    async def search_names(self, term, limit):
        needle = term.casefold()
        return [
            row for row in self.rows.values()
            if any(needle in part.casefold() for part in (
                row.catalog_name, row.database_name, row.object_name, row.column_name))
        ][:limit]

    async def write(self, rows):
        for row in rows:
            self.rows[row.id] = row

    async def mark_dropped(self, ids, at):
        for key in ids:
            self.rows[key] = Securable(**{**self.rows[key].as_row(), "dropped_at": at})

    async def mark_catalog_dropped(self, catalog, at):
        await self.mark_dropped(
            [row.id for row in self.rows.values()
             if row.catalog_name == catalog and row.dropped_at is None],
            at,
        )

    async def securables_in_catalog(self, catalog):
        return [row.as_row() for row in await self.in_catalog(catalog)]

    async def securables_of_type(self, kind):
        return [row.as_row() for row in await self.of_type(kind)]

    async def write_securables(self, catalog, rows, *, generation):
        assert generation == 1
        for row in rows:
            previous = self.rows.get(row["id"])
            self.rows[row["id"]] = Securable.from_row({
                **row, "first_seen_at": previous.first_seen_at if previous else self.now,
                "updated_at": self.now, "dropped_at": None,
            })

    async def mark_securables_dropped(self, catalog, ids, *, generation):
        assert generation == 1
        if ids is None:
            await self.mark_catalog_dropped(catalog, self.now)
        else:
            await self.mark_dropped(ids, self.now)


async def record_harvest(catalog, harvest, now, store):
    store.now = now
    return await securables.record_harvest(store, catalog, harvest, generation=1)


class MemoryMetadata:
    def __init__(self):
        self.tags: dict[str, dict[str, dict]] = {}
        self.annotations: dict[str, dict] = {}
        self.engine: dict[str, str] = {}

    async def tags_for(self, ids):
        return {
            key: [self.tags[key][tag] for tag in sorted(self.tags[key])]
            for key in ids if self.tags.get(key)
        }

    async def put_tag(self, securable_id, securable_type, key, value, user, now):
        self.tags.setdefault(securable_id, {})[key] = {
            "securable_id": securable_id, "tag_key": key, "tag_value": value,
            "updated_by": user, "updated_at": now,
        }

    async def delete_tag(self, securable_id, key):
        return self.tags.get(securable_id, {}).pop(key, None) is not None

    async def search_tags(self, *, term=None, key=None, value=None, limit=500):
        found = []
        for rows in self.tags.values():
            for row in rows.values():
                text = f"{row['tag_key']} {row['tag_value'] or ''}".casefold()
                if term and term.casefold() not in text:
                    continue
                if key and row["tag_key"].casefold() != key.casefold():
                    continue
                if value is not None and (row["tag_value"] or "").casefold() != value.casefold():
                    continue
                found.append(row)
        return found[:limit]

    async def annotations_for(self, ids):
        return {key: self.annotations[key] for key in ids if key in self.annotations}

    async def put_annotation(self, securable_id, securable_type, changes, user, now):
        row = {**self.annotations.get(securable_id, {}), **changes,
               "securable_id": securable_id, "updated_by": user}
        self.annotations[securable_id] = row
        return row

    async def search_annotations(self, term, limit=500):
        needle = term.casefold()
        return [
            row for row in self.annotations.values()
            if any(needle in str(row.get(f) or "").casefold()
                   for f in ("comment", "owner_name", "classification"))
        ]

    async def search_engine_comments(self, term, limit=500):
        return [key for key, text in self.engine.items() if term.casefold() in text.casefold()]

    async def engine_comments(self, ids):
        return {key: self.engine[key] for key in ids if key in self.engine}

    async def engine_column_comments(self, catalog, database, relation):
        return {}


class MemoryEdges:
    def __init__(self, edges=()):
        self.edges = list(edges)

    async def edges_of(self, securable_id, limit=200):
        return [e for e in self.edges if securable_id in (e["source_id"], e["target_id"])]


class MemoryLinks:
    def __init__(self, links=()):
        self.links = list(links)

    async def for_securable(self, securable_id):
        return [link for link in self.links if link["securable_id"] == securable_id]


def _relation(catalog, database, name, kind="table", dropped=None):
    return Securable(
        id=relation_id(catalog, database, name), securable_type=kind, catalog_name=catalog,
        database_name=database, object_name=name, parent_id=database_id(catalog, database),
        dropped_at=dropped,
    )


def _column(catalog, database, relation, name, ordinal=1, dropped=None):
    return Securable(
        id=column_id(catalog, database, relation, name), securable_type="column",
        catalog_name=catalog, database_name=database, object_name=relation, column_name=name,
        parent_id=relation_id(catalog, database, relation), ordinal=ordinal,
        data_type="varchar(64)", dropped_at=dropped,
    )


ORDERS = _relation("default_catalog", "sales", "orders")
EMAIL = _column("default_catalog", "sales", "orders", "email", 2)
ORDER_ID = _column("default_catalog", "sales", "orders", "id", 1)
PAYROLL = _relation("default_catalog", "hr", "payroll")
SALARY = _column("default_catalog", "hr", "payroll", "salary")
GONE = _relation("default_catalog", "sales", "legacy_orders", dropped=T1)
GONE_EMAIL = _column("default_catalog", "sales", "legacy_orders", "email", dropped=T1)
LAKE_EVENTS = _relation("lake", "web", "events")

#: What each role's engine session lists.
ENGINE = {
    "ACCOUNTADMIN": {
        "default_catalog": {"sales": {"orders"}, "hr": {"payroll"}},
        "lake": {"web": {"events"}},
    },
    "marketing": {"default_catalog": {"sales": {"orders"}}},
}


@pytest.fixture
def engine(monkeypatch):
    calls = {"catalogs": 0, "relations": 0}

    async def catalogs(user):
        calls["catalogs"] += 1
        view = ENGINE.get(user["active_role"], {})
        return [{"name": name, "databases": sorted(dbs)} for name, dbs in view.items()]

    async def relations(user, catalog, database):
        calls["relations"] += 1
        names = ENGINE.get(user["active_role"], {}).get(catalog, {}).get(database, set())
        return {name: "BASE TABLE" for name in names}

    monkeypatch.setattr(access.visibility, "visible_catalogs", catalogs)
    monkeypatch.setattr(access.visibility, "visible_relations", relations)
    return calls


@pytest.fixture
def audits(monkeypatch):
    write = AsyncMock()
    monkeypatch.setattr(governance, "write_audit_log", write)
    return write


@pytest.fixture
def ranger(monkeypatch):
    from app.modules.access_control import service as ac_service

    grants: dict[tuple[str, str], list[str]] = {}

    async def effective_access(*, principal, active_role, resource):
        return {
            "object_access": grants.get((active_role, resource), []),
            "object_access_comparison_supported": True,
        }

    monkeypatch.setattr(ac_service.access_control_service, "effective_access", effective_access)
    return grants


def _service(rows=None, edges=(), link_rows=()):
    store = MemorySecurables(
        rows if rows is not None
        else [ORDERS, EMAIL, ORDER_ID, PAYROLL, SALARY, GONE, GONE_EMAIL, LAKE_EVENTS]
    )
    return GovernanceService(store, MemoryMetadata(), MemoryEdges(edges), MemoryLinks(link_rows))


# ── Securable ids and the registry ───────────────────────────────────────


def test_ids_are_stable_and_distinct_per_level():
    assert relation_id("c", "d", "t") == relation_id("c", "d", "t")
    assert len({catalog_id("c"), database_id("c", "d"), relation_id("c", "d", "t"),
                column_id("c", "d", "t", "x")}) == 4
    # Column names are case-insensitive in the engine; the id follows.
    assert column_id("c", "d", "t", "Email") == column_id("c", "d", "t", "email")


def test_database_and_relation_ids_match_the_phase_one_object_ids():
    from app.modules.catalog.repository import row_id

    assert database_id("c", "d") == row_id("c", "d", "", "database")
    assert relation_id("c", "d", "t") == row_id("c", "d", "t", "relation")


def test_securable_names():
    assert EMAIL.full_name == "default_catalog.sales.orders.email"
    assert EMAIL.relation_id == ORDERS.id and ORDERS.relation_id == ORDERS.id
    assert Securable(id="x", securable_type="database", catalog_name="c",
                     database_name="d").relation_id is None


def _harvest(*relations, databases=("sales",)):
    return Harvest(databases=list(databases), relations=list(relations))


def _orders(*columns):
    return Relation(
        database="sales", name="orders", object_type="table",
        columns=[{"ordinal": i, "column_name": c, "data_type": "int"}
                 for i, c in enumerate(columns, start=1)],
    )


async def test_record_harvest_registers_every_level_once():
    store = MemorySecurables()
    counts = await record_harvest("default_catalog", _harvest(_orders("id", "email")), T0, store)
    kinds = sorted(row.securable_type for row in store.rows.values())
    assert kinds == ["catalog", "column", "column", "database", "table"]
    assert counts == {"securables_written": 5, "securables_dropped": 0}
    # Unchanged on the next pass: nothing is rewritten.
    again = await record_harvest("default_catalog", _harvest(_orders("id", "email")), T1, store)
    assert again == {"securables_written": 0, "securables_dropped": 0}
    assert store.rows[column_id("default_catalog", "sales", "orders", "email")].first_seen_at == T0


async def test_a_vanished_relation_and_its_columns_are_dropped_and_come_back_with_the_same_id():
    store = MemorySecurables()
    await record_harvest("default_catalog", _harvest(_orders("id", "email")), T0, store)
    email = column_id("default_catalog", "sales", "orders", "email")
    counts = await record_harvest("default_catalog", _harvest(), T1, store)
    assert counts["securables_dropped"] == 3
    assert store.rows[email].dropped_at == T1
    assert store.rows[relation_id("default_catalog", "sales", "orders")].dropped_at == T1
    await record_harvest("default_catalog", _harvest(_orders("id", "email")), T1, store)
    assert store.rows[email].dropped_at is None and store.rows[email].first_seen_at == T0


async def test_columns_are_kept_when_the_harvest_could_not_describe_the_relation():
    store = MemorySecurables()
    await record_harvest("default_catalog", _harvest(_orders("id", "email")), T0, store)
    await record_harvest("default_catalog", _harvest(_orders()), T1, store)
    email = column_id("default_catalog", "sales", "orders", "email")
    assert store.rows[email].dropped_at is None
    # Described again without the column: now it is gone.
    await record_harvest("default_catalog", _harvest(_orders("id")), T1, store)
    assert store.rows[email].dropped_at == T1


async def test_record_catalogs_drops_a_missing_catalog_and_everything_under_it(monkeypatch):
    from app.modules.access_control.security_context import SecurityContext
    from app.modules.catalog import sync

    store = MemorySecurables()
    store.now = T0
    store.list_catalogs = AsyncMock(return_value=[])
    store.create_catalog = AsyncMock()
    store.claim_sync = AsyncMock(return_value=1)
    store.catalogs_with_live_objects = AsyncMock(return_value=set())
    store.retire_catalog_objects = AsyncMock()
    engine = AsyncMock()
    actor = SecurityContext("catalog-sync", "PUBLIC", principal_type="SERVICE")

    @asynccontextmanager
    async def transaction(bound):
        assert bound == actor
        yield store

    monkeypatch.setattr(sync, "catalog_transaction", transaction)
    monkeypatch.setattr(sync, "sync_catalog", AsyncMock())

    async def reconcile(names, now):
        store.now = now
        engine.catalogs.return_value = [{"name": name, "type": "internal"} for name in names]
        await sync.reconcile_catalogs(actor=actor, harvester=engine)

    await reconcile(["default_catalog", "lake"], T0)
    await record_harvest("lake", _harvest(databases=("web",)), T0, store)
    await reconcile(["default_catalog"], T1)
    assert store.rows[catalog_id("lake")].dropped_at == T1
    assert store.rows[database_id("lake", "web")].dropped_at == T1
    assert store.rows[catalog_id("default_catalog")].dropped_at is None
    await reconcile(["default_catalog", "lake"], T1)
    assert store.rows[catalog_id("lake")].dropped_at is None


def test_like_pattern_escapes_wildcards():
    assert securables.like_pattern("Sales_%") == "%sales\\_\\%%"


# ── Business metadata store ──────────────────────────────────────────────


def test_legacy_tags_map_to_securable_ids():
    legacy = metadata.legacy_securable
    assert legacy("TABLE", "sales.orders") == (relation_id("default_catalog", "sales", "orders"),
                                               "table")
    assert legacy("column", "lake.web.events.ip") == (column_id("lake", "web", "events", "ip"),
                                                      "column")
    assert legacy("database", "sales") == (database_id("default_catalog", "sales"), "database")
    assert legacy("function", "sales.f") is None


async def test_runtime_never_rewrites_legacy_tags(monkeypatch):
    from app.common import nova_system
    from app.core import metadata_runtime

    probe = AsyncMock(return_value=False)
    metadata_db = type("Metadata", (), {"probe": probe})()
    monkeypatch.setattr(metadata_runtime, "metadata_db", lambda: metadata_db)
    forbidden = AsyncMock(side_effect=AssertionError("runtime cannot rewrite source tags"))
    monkeypatch.setattr(nova_system.db, "execute_system", forbidden)
    await nova_system.init_nova_system()
    probe.assert_awaited_once()
    forbidden.assert_not_awaited()


@pytest.mark.parametrize(("kind", "name"), [("column", "db.table"), ("database", "a.b.c"),
                                            ("pipe", "db.pipe")])
def test_unsupported_legacy_tags_are_not_attached_to_an_unrelated_object(kind, name):
    assert metadata.legacy_securable(kind, name) is None


# ── Visibility ───────────────────────────────────────────────────────────


async def test_caller_view_follows_the_engine_and_hides_dropped_from_non_admins(engine):
    analyst = access.CallerView(ANALYST)
    assert await analyst.can_see(EMAIL) and await analyst.can_see(ORDERS)
    assert not await analyst.can_see(SALARY)
    assert not await analyst.can_see(LAKE_EVENTS)
    assert not await analyst.can_see(GONE)
    admin = access.CallerView(ADMIN)
    assert admin.admin and await admin.can_see(GONE) and await admin.can_see(SALARY)


async def test_describe_hides_what_the_engine_hides(engine, audits):
    service = _service()
    with pytest.raises(MetadataNotFound):
        await service.describe(PAYROLL.id, ANALYST)
    with pytest.raises(MetadataNotFound):
        await service.describe("0" * 64, ANALYST)
    detail = await service.describe(ORDERS.id, ANALYST)
    assert [column.column_name for column in detail.columns] == ["id", "email"]
    assert detail.status == "live" and not detail.can_edit


# ── Writes: authorization and audit ──────────────────────────────────────


async def test_security_admin_tags_a_column_and_the_change_is_audited(engine, audits):
    service = _service()
    detail = await service.put_tags(EMAIL.id, [TagWrite(key="pii", value="email")], ADMIN)
    assert [(tag.key, tag.value) for tag in detail.tags] == [("pii", "email")]
    audit = audits.await_args.kwargs
    assert (audit["action"], audit["status"], audit["object_type"]) == (
        "SET_TAG", "SUCCESS", "COLUMN")
    assert audit["object_name"] == "default_catalog.sales.orders.email"
    assert "email" not in audit["sql_text"].replace("pii", "")  # the value is never audited


async def test_a_role_without_alter_is_refused_and_the_refusal_is_audited(engine, audits, ranger):
    service = _service()
    with pytest.raises(MetadataDenied):
        await service.put_tags(EMAIL.id, [TagWrite(key="pii")], ANALYST)
    assert audits.await_args.kwargs["status"] == "DENIED"
    # A securable the role cannot see is refused as not found, also audited.
    with pytest.raises(MetadataNotFound):
        await service.put_tags(SALARY.id, [TagWrite(key="pii")], ANALYST)
    assert audits.await_args.kwargs["status"] == "DENIED"
    assert audits.await_args.kwargs["object_name"] == SALARY.id


async def test_ranger_alter_on_the_relation_lets_a_role_edit_it_and_its_columns(
    engine, audits, ranger
):
    ranger[("marketing", "default_catalog.sales.orders")] = ["SELECT", "ALTER"]
    service = _service()
    detail = await service.annotate(EMAIL.id, AnnotationWrite(comment="Contact email"), ANALYST)
    assert detail.comment == "Contact email" and detail.can_edit and not detail.can_set_owner
    # Owners are a governance assignment: security admins only.
    with pytest.raises(MetadataDenied):
        await service.annotate(ORDERS.id, AnnotationWrite(owner_name="sales_ops"), ANALYST)


async def test_an_unknown_ranger_answer_refuses(engine, audits, monkeypatch):
    from app.modules.access_control import service as ac_service

    monkeypatch.setattr(ac_service.access_control_service, "effective_access",
                        AsyncMock(side_effect=RuntimeError("ranger down")))
    with pytest.raises(MetadataDenied):
        await _service().put_tags(EMAIL.id, [TagWrite(key="pii")], ANALYST)


async def test_dropped_securables_are_edited_by_security_admins_only(engine, audits, ranger):
    ranger[("marketing", "default_catalog.sales.legacy_orders")] = ["ALL"]
    service = _service()
    with pytest.raises(MetadataNotFound):
        await service.put_tags(GONE.id, [TagWrite(key="retired")], ANALYST)
    detail = await service.put_tags(GONE.id, [TagWrite(key="retired")], ADMIN)
    assert detail.status == "dropped"


async def test_annotation_rules(engine, audits):
    service = _service()
    with pytest.raises(MetadataInvalid):
        await service.annotate(ORDERS.id, AnnotationWrite(classification="RESTRICTED"), ADMIN)
    assert audits.await_args.kwargs["status"] == "DENIED"
    with pytest.raises(MetadataInvalid):
        await service.annotate(ORDERS.id, AnnotationWrite(), ADMIN)
    detail = await service.annotate(ORDERS.id, AnnotationWrite(owner_name="sales_ops"), ADMIN)
    assert (detail.owner_name, detail.owner_type) == ("sales_ops", "role")
    detail = await service.annotate(
        ORDERS.id, AnnotationWrite.model_validate({"owner_name": None}), ADMIN
    )
    assert detail.owner_name is None and detail.owner_type is None
    detail = await service.annotate(EMAIL.id, AnnotationWrite(classification="RESTRICTED",
                                                               comment="x" * 10), ADMIN)
    assert detail.classification == "RESTRICTED"
    assert audits.await_args.kwargs["sql_text"] == (
        "SET COMMENT (10 chars); SET CLASSIFICATION RESTRICTED"
    )


async def test_removing_a_tag(engine, audits):
    service = _service()
    await service.put_tags(ORDERS.id, [TagWrite(key="tier", value="gold")], ADMIN)
    detail = await service.delete_tag(ORDERS.id, "tier", ADMIN)
    assert detail.tags == [] and audits.await_args.kwargs["status"] == "SUCCESS"
    await service.delete_tag(ORDERS.id, "tier", ADMIN)
    assert audits.await_args.kwargs["status"] == "NOOP"


async def test_a_failed_write_is_audited_as_an_error(engine, audits):
    service = _service()
    service.metadata.put_tag = AsyncMock(side_effect=RuntimeError("password=hunter2 boom"))
    with pytest.raises(RuntimeError):
        await service.put_tags(ORDERS.id, [TagWrite(key="tier")], ADMIN)
    audit = audits.await_args.kwargs
    assert audit["status"] == "ERROR" and "hunter2" not in audit["error_message"]


async def test_an_audit_outage_does_not_replace_the_answer(engine, monkeypatch):
    monkeypatch.setattr(governance, "write_audit_log", AsyncMock(side_effect=RuntimeError))
    detail = await _service().put_tags(ORDERS.id, [TagWrite(key="tier")], ADMIN)
    assert detail.tags[0].key == "tier"


async def test_resolve_finds_each_level_by_name(engine, audits):
    service = _service([*_service().securables.rows.values(),
                        Securable(id=catalog_id("default_catalog"), securable_type="catalog",
                                  catalog_name="default_catalog"),
                        Securable(id=database_id("default_catalog", "sales"),
                                  securable_type="database", catalog_name="default_catalog",
                                  database_name="sales")])
    assert (await service.resolve(ANALYST, catalog="default_catalog")).securable_type == "catalog"
    assert (await service.resolve(ANALYST, catalog="default_catalog", database="sales")
            ).securable_type == "database"
    assert (await service.resolve(ANALYST, catalog="default_catalog", database="sales",
                                  object_name="orders", column="EMAIL")).id == EMAIL.id
    with pytest.raises(MetadataInvalid):
        await service.resolve(ANALYST, catalog="default_catalog", column="email")
    with pytest.raises(MetadataInvalid):
        await service.resolve(ANALYST, catalog="default_catalog", object_name="orders")


# ── Search ───────────────────────────────────────────────────────────────


async def test_search_by_tag_is_cut_by_what_the_caller_can_list(engine, audits):
    service = _service()
    for row in (EMAIL, SALARY, GONE_EMAIL, LAKE_EVENTS):
        await service.metadata.put_tag(row.id, row.securable_type, "pii", "yes", "a", T0)
    analyst = await service.search(ANALYST, tag="pii")
    assert [hit.full_name for hit in analyst.results] == ["default_catalog.sales.orders.email"]
    admin = await service.search(ADMIN, tag="PII=yes")
    assert {hit.full_name for hit in admin.results} == {
        EMAIL.full_name, SALARY.full_name, LAKE_EVENTS.full_name}
    with_dropped = await service.search(ADMIN, tag="pii", include_dropped=True)
    assert GONE_EMAIL.full_name in {hit.full_name for hit in with_dropped.results}
    dropped = next(hit for hit in with_dropped.results if hit.full_name == GONE_EMAIL.full_name)
    assert dropped.status == "dropped" and dropped.tags[0].key == "pii"
    # A non-admin asking for dropped objects still gets none.
    assert all(hit.status == "live" for hit in
               (await service.search(ANALYST, tag="pii", include_dropped=True)).results)


async def test_search_matches_names_comments_and_owners_and_ranks_exact_names_first(
    engine, audits
):
    service = _service()
    await service.metadata.put_annotation(ORDERS.id, "table", {"comment": "Customer orders"},
                                          "a", T0)
    service.metadata.engine[ORDER_ID.id] = "order key"
    result = await service.search(ANALYST, query="orders")
    names = [hit.full_name for hit in result.results]
    assert names[0] == ORDERS.full_name
    assert set(result.results[0].matched) == {"comment", "name"}
    assert "default_catalog.hr.payroll" not in names
    by_comment = await service.search(ANALYST, query="order key")
    assert [hit.full_name for hit in by_comment.results] == [ORDER_ID.full_name]
    assert by_comment.results[0].matched == ["comment"]


async def test_search_combines_term_and_tag_and_filters_types(engine, audits):
    service = _service()
    await service.metadata.put_tag(EMAIL.id, "column", "pii", None, "a", T0)
    result = await service.search(ADMIN, query="orders", tag="pii")
    assert [hit.full_name for hit in result.results] == [EMAIL.full_name]
    only_tables = await service.search(ADMIN, query="orders", types=["table"])
    assert {hit.securable_type for hit in only_tables.results} == {"table"}


async def test_search_needs_a_term_and_honours_the_limit(engine, audits):
    service = _service()
    with pytest.raises(MetadataInvalid):
        await service.search(ADMIN, query="  ")
    limited = await service.search(ADMIN, query="o", limit=1)
    assert limited.count == 1 and limited.truncated


# ── Lineage ──────────────────────────────────────────────────────────────


def _edge(source, target, kind="insert"):
    return {"source_id": source.id, "target_id": target.id, "source_name": source.full_name,
            "target_name": target.full_name, "statement_kind": kind, "last_seen_at": T0}


async def test_lineage_shows_only_endpoints_the_caller_can_see(engine, audits):
    rollup = _relation("default_catalog", "sales", "rollup")
    for role in ("marketing", "ACCOUNTADMIN"):
        ENGINE[role]["default_catalog"]["sales"].add("rollup")
    try:
        service = _service(
            [ORDERS, PAYROLL, GONE, rollup],
            edges=[_edge(ORDERS, rollup), _edge(PAYROLL, rollup), _edge(GONE, rollup)],
        )
        analyst = await service.lineage(rollup.id, ANALYST)
        assert [node.full_name for node in analyst.upstream] == [ORDERS.full_name]
        admin = await service.lineage(rollup.id, ADMIN)
        assert {node.status for node in admin.upstream} == {"live", "dropped"}
        downstream = await service.lineage(ORDERS.id, ANALYST)
        assert [node.full_name for node in downstream.downstream] == [rollup.full_name]
        with pytest.raises(MetadataInvalid):
            await _service([EMAIL, ORDERS]).lineage(EMAIL.id, ANALYST)
    finally:
        for role in ("marketing", "ACCOUNTADMIN"):
            ENGINE[role]["default_catalog"]["sales"].discard("rollup")


async def test_lineage_to_an_unsynced_relation_uses_its_recorded_name(engine, audits):
    unsynced = _relation("default_catalog", "sales", "orders_copy")
    ENGINE["marketing"]["default_catalog"]["sales"].add("orders_copy")
    try:
        service = _service([ORDERS], edges=[_edge(ORDERS, unsynced, "create_table_as_select")])
        result = await service.lineage(ORDERS.id, ANALYST)
        assert [node.full_name for node in result.downstream] == [unsynced.full_name]
    finally:
        ENGINE["marketing"]["default_catalog"]["sales"].discard("orders_copy")
    assert governance._unregistered("x", "no-dots") is None


def _audit_rows(*statements):
    return {"rows": [[f"q{i}", T0, database, sql] for i, (database, sql) in
                     enumerate(statements)]}


async def test_lineage_harvest_records_edges_and_a_watermark(monkeypatch):
    stored = {}
    runs = []

    class Repo:
        async def watermark(self):
            return None

        async def existing(self, ids):
            return {}

        async def upsert(self, edges):
            stored.update({edge["id"]: edge for edge in edges})

    async def statements(since):
        return _audit_rows(
            ("sales", "INSERT INTO rollup SELECT region, count(*) FROM orders GROUP BY region"),
            ("sales", "INSERT INTO rollup SELECT * FROM orders"),
            (None, "INSERT INTO sales.t VALUES (1)"),
            ("sales", "CREATE VIEW v AS WITH x AS (SELECT * FROM lake.web.events) "
                      "SELECT * FROM x"),
        )["rows"]

    Repo.statements = staticmethod(statements)
    from app.modules.catalog import repository

    monkeypatch.setattr(repository.catalog_repo, "write_sync_run",
                        AsyncMock(side_effect=lambda run: runs.append(run)))
    run = await lineage.harvest_lineage(Repo())
    assert run["status"] == "ok" and run["counts"]["edges"] == 2
    assert run["catalog_name"] == lineage.LINEAGE_RUN and run["counts"]["watermark"]
    targets = {(e["source_name"], e["target_name"], e["statement_kind"]) for e in stored.values()}
    assert targets == {
        ("default_catalog.sales.orders", "default_catalog.sales.rollup", "insert"),
        ("lake.web.events", "default_catalog.sales.v", "create_view"),
    }
    edge = next(iter(stored.values()))
    assert set(edge) == set(lineage._COLUMNS)


async def test_a_failed_lineage_pass_is_recorded_and_redacted(monkeypatch):
    class Repo:
        async def watermark(self):
            return T0

    Repo.statements = AsyncMock(side_effect=RuntimeError("password='x1' broke"))
    from app.modules.catalog import repository

    monkeypatch.setattr(repository.catalog_repo, "write_sync_run", AsyncMock())
    run = await lineage.harvest_lineage(Repo())
    assert run["status"] == "error" and "x1" not in run["error"]


# ── Links from Semantic Views and entities ───────────────────────────────


def test_relation_strings_resolve_to_securable_ids():
    assert links.relation_securable("sales.orders") == (
        ORDERS.id, "default_catalog.sales.orders")
    assert links.relation_securable("`lake`.`web`.`events`")[0] == LAKE_EVENTS.id
    assert links.relation_securable("orders") is None
    assert links._sources('{"datasets": [{"source": "sales.orders"}, "bad"]}') == ["sales.orders"]
    assert links._sources("not json") == [] and links._sources(None) == []


async def test_reconcile_links_follows_the_active_or_newest_version(monkeypatch):
    written, deleted = [], []

    owner_store = AsyncMock()
    owner_store.view_link_sources.return_value = [{
        "id": "v1", "catalog_name": "default_catalog", "database_name": "sales",
        "schema_name": "public", "name": "revenue",
        "definition": {"datasets": [{"source": "sales.orders"}]},
    }]
    owner_store.list_entities.return_value = [{
        "id": "e1", "catalog_name": "default_catalog", "database_name": "sales",
        "schema_name": "public", "name": "customer", "relation_name": "lake.web.events",
    }]

    @asynccontextmanager
    async def reader():
        yield owner_store

    monkeypatch.setattr(links, "_owner_reader", reader)

    class Repo:
        async def all(self):
            return [{"id": "stale", "owner_label": "x"}]

        async def write(self, rows):
            written.extend(rows)

        async def delete(self, ids):
            deleted.extend(ids)

    counts = await links.reconcile_links(Repo())
    assert counts["links"] == 2 and deleted == ["stale"]
    assert {(row["owner_type"], row["securable_id"]) for row in written} == {
        ("semantic_view", ORDERS.id), ("entity", LAKE_EVENTS.id)}


async def test_refresh_owner_links_never_raises(monkeypatch):
    monkeypatch.setattr(links, "_owner_reader", lambda: (_ for _ in ()).throw(RuntimeError()))
    await links.refresh_owner_links(links.SEMANTIC_VIEW, "v1")


async def test_used_by_shows_owners_the_caller_may_see(engine, audits, monkeypatch):
    rows = [
        {"owner_type": "semantic_view", "owner_id": "v1", "owner_label": "s.revenue",
         "securable_id": ORDERS.id},
        {"owner_type": "entity", "owner_id": "e1", "owner_label": "s.customer",
         "securable_id": ORDERS.id},
    ]
    visible = {"v1"}

    async def owner_visible(owner_type, owner_id, user):
        return owner_id in visible

    monkeypatch.setattr(governance, "_owner_visible", owner_visible)
    detail = await _service(link_rows=rows).describe(ORDERS.id, ANALYST)
    assert [link.label for link in detail.used_by] == ["s.revenue"]


async def test_owner_visibility_uses_the_owners_rules(monkeypatch):
    from app.modules.intelligence import entities, semantic_views

    monkeypatch.setattr(semantic_views.semantic_view_service, "is_visible",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(entities.entity_registry, "get", AsyncMock(return_value=None))
    assert await governance._owner_visible("semantic_view", "v", ANALYST)
    assert not await governance._owner_visible("entity", "e", ANALYST)
    assert not await governance._owner_visible("other", "x", ANALYST)


# ── API ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(engine, audits, monkeypatch):
    from app.modules.catalog import metadata_router

    service = _service()
    monkeypatch.setattr(metadata_router, "governance_service", service)
    app = FastAPI()
    app.include_router(metadata_router.router, prefix="/api/v1/catalog")
    user = {"value": ANALYST}
    app.dependency_overrides[get_current_user] = lambda: user["value"]
    with TestClient(app) as test_client:
        yield test_client, user, service


def test_api_maps_visibility_and_authorization(client, ranger):
    test_client, user, _service_ = client
    assert test_client.get(f"/api/v1/catalog/objects/{PAYROLL.id}").status_code == 404
    assert test_client.get("/api/v1/catalog/objects/not-an-id").status_code == 422
    response = test_client.put(f"/api/v1/catalog/objects/{EMAIL.id}/tags",
                               json={"tags": [{"key": "pii", "value": "email"}]})
    assert response.status_code == 403
    user["value"] = ADMIN
    response = test_client.put(f"/api/v1/catalog/objects/{EMAIL.id}/tags",
                               json={"tags": [{"key": "pii", "value": "email"}]})
    assert response.status_code == 200 and response.json()["tags"][0]["key"] == "pii"
    assert test_client.get(f"/api/v1/catalog/objects/{EMAIL.id}/tags").json()[0]["value"] == (
        "email")
    bad = test_client.put(f"/api/v1/catalog/objects/{EMAIL.id}/tags",
                          json={"tags": [{"key": "bad key"}]})
    assert bad.status_code == 422
    found = test_client.get("/api/v1/catalog/search", params={"tag": "pii"}).json()
    assert found["count"] == 1 and found["results"][0]["matched"] == ["tag"]
    assert test_client.get("/api/v1/catalog/search").status_code == 400
    removed = test_client.delete(f"/api/v1/catalog/objects/{EMAIL.id}/tags/pii")
    assert removed.status_code == 200 and removed.json()["tags"] == []
    patched = test_client.patch(f"/api/v1/catalog/objects/{ORDERS.id}/annotations",
                                json={"classification": "PUBLIC"})
    assert patched.status_code == 400
    resolved = test_client.get("/api/v1/catalog/objects/resolve", params={
        "catalog": "default_catalog", "database": "sales", "object": "orders"})
    assert resolved.json()["id"] == ORDERS.id
    assert test_client.get(f"/api/v1/catalog/objects/{ORDERS.id}/lineage").json() == {
        "id": ORDERS.id, "full_name": ORDERS.full_name, "upstream": [], "downstream": []}


def test_control_characters_are_rejected_in_comments_and_tag_values():
    with pytest.raises(ValueError):
        AnnotationWrite(comment="a\x00b")
    with pytest.raises(ValueError):
        TagWrite(key="k", value="\x1b[31m")
    assert AnnotationWrite(comment="  ").comment is None



def test_catalog_schema_is_owned_by_the_pg_migration():
    from app.core.metadata_schema import load_migrations

    statement = "\n".join(item.statement for item in load_migrations())
    for table in ("catalog_securables", "config_object_tags", "catalog_annotations",
                  "catalog_lineage", "catalog_securable_links"):
        assert f"CREATE TABLE nova_meta.{table}" in statement


async def test_semantic_views_and_entities_relink_through_the_catalog(monkeypatch):
    from app.modules.intelligence import entities, semantic_views

    relink = AsyncMock()
    monkeypatch.setattr(links, "refresh_owner_links", relink)
    await semantic_views._relink("v1")
    await entities._relink("e1")
    assert [call.args for call in relink.await_args_list] == [
        ("semantic_view", "v1"), ("entity", "e1")]
