"""Ontology persistence SQL, HTTP routes, and the Semantic View publish hook."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.ontology import repository as repository_module
from app.modules.ontology import representations
from app.modules.ontology import router as router_module
from app.modules.ontology.contracts import Actor, Domain, Release, ReleaseMember, manifest_hash
from app.modules.ontology.metadata_repository import (
    BATCH_ROWS,
    MAX_CURRENT,
    MetadataBranches,
    OntologyMetadataRepository,
    OntologyReadBoundExceeded,
)
from app.modules.ontology.releases import ReleaseService
from app.modules.ontology.repository import OntologyRepository
from app.modules.ontology.representations import PublishedView, SemanticViewCatalog
from app.modules.ontology.schema import ONTOLOGY_TABLES, ensure_ontology_schema
from app.modules.ontology.service import OntologyService
from app.modules.ontology.stewardship import StewardshipService
from tests.unit._ontology import ADMIN, ANALYST, FakeCatalog, FakeRepository, definition


def _domain(name="Finance", revision=1) -> Domain:
    return Domain(
        id="dom_1",
        revision=revision,
        actor=Actor.from_user(ADMIN),
        name=name,
        steward_roles=["finance_steward"],
    )


@pytest.fixture
def sql(monkeypatch):
    def install(responses):
        connection = Mock()
        connection.execute = AsyncMock()
        repo = OntologyMetadataRepository(connection, Actor.from_user(ADMIN))
        repo._bound = AsyncMock()
        repo._rows = AsyncMock(side_effect=responses)

        @asynccontextmanager
        async def repository(self, *, read_only):
            yield repo

        monkeypatch.setattr(OntologyRepository, "_repository", repository)
        return repo

    return install


def row(record, branches=1):
    return {"payload": record.model_dump(mode="json"), "branches": branches}


async def test_runtime_does_not_create_ontology_tables(monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("runtime DDL"))
    monkeypatch.setattr(repository_module.db, "execute_system", forbidden)
    await ensure_ontology_schema()
    forbidden.assert_not_awaited()
    from app.core.metadata_schema import load_migrations

    migration = next(item for item in load_migrations() if item.version == 20)
    assert all(name.lower() in migration.statement for name in ONTOLOGY_TABLES.values())


async def test_save_inserts_the_next_revision(sql):
    repo = sql([[]])
    saved = await OntologyRepository().save("domains", _domain())
    assert saved.revision == 1
    statement, params = repo.connection.execute.call_args.args
    assert statement.startswith("INSERT INTO nova_meta.config_ontology_domains")
    assert params[:2] == ("dom_1", 1)
    assert params[3].obj["actor"] == _domain().actor.model_dump(mode="json")
    assert params[3].obj["name"] == "Finance"


async def test_save_many_writes_changed_records_in_one_insert(sql):
    stored = _domain()
    other = _domain("Treasury").model_copy(update={"id": "dom_2"})
    repo = sql([[row(stored)]])
    third = _domain("Ops").model_copy(update={"id": "dom_3"})
    written = await OntologyRepository().save_many("domains", [(stored, 1), (other, 0), (third, 0)])
    assert written == 2
    repo.connection.execute.assert_awaited_once()
    statement, params = repo.connection.execute.call_args.args
    assert statement.count("(%s,%s,%s,%s)") == 2
    assert params[0] == "dom_2" and params[4] == "dom_3"


async def test_save_many_writes_a_large_batch_in_few_statements(sql):
    repo = sql([[], [], []])
    records = [
        (_domain().model_copy(update={"id": f"dom_{i}"}), 0) for i in range(2 * BATCH_ROWS + 1)
    ]
    assert await OntologyRepository().save_many("domains", records) == len(records)
    inserts = repo.connection.execute.call_args_list
    assert [call.args[0].count("(%s,%s,%s,%s)") for call in inserts] == [BATCH_ROWS, BATCH_ROWS, 1]


async def test_save_many_aborts_the_whole_batch_on_a_conflict(sql):
    repo = sql([[row(_domain())]])
    with pytest.raises(MetadataConflict):
        await OntologyRepository().save_many(
            "domains",
            [(_domain("Treasury"), 0), (_domain("Ops").model_copy(update={"id": "x"}), 0)],
        )
    repo.connection.execute.assert_not_awaited()


async def test_saving_identical_content_is_a_no_op(sql):
    stored = _domain()
    repo = sql([[row(stored)]])
    again = await OntologyRepository().save(
        "domains", stored.model_copy(update={"revision": 9}), expected_revision=0
    )
    assert again.revision == 1
    repo.connection.execute.assert_not_awaited()


async def test_save_checks_the_expected_revision(sql):
    stored = _domain()
    sql([[row(stored)]])
    with pytest.raises(MetadataConflict):
        await OntologyRepository().save("domains", _domain("Treasury"), expected_revision=0)
    sql([[]])
    with pytest.raises(MetadataConflict):
        await OntologyRepository().save("domains", _domain(), expected_revision=3)
    sql([[row(stored)]])
    changed = await OntologyRepository().save("domains", _domain("Treasury"), expected_revision=1)
    assert changed.revision == 2 and changed.created_at == stored.created_at


async def test_an_unbound_actor_never_reads_or_writes(sql):
    repo = sql([[]])
    repo._bound.side_effect = MetadataPermissionDenied()
    with pytest.raises(MetadataPermissionDenied):
        await OntologyRepository().save("domains", _domain())
    repo._rows.assert_not_awaited()
    repo.connection.execute.assert_not_awaited()


async def test_forked_revisions_require_reconciliation(sql):
    for method, args in (
        ("latest", ("domains", "dom_1", Domain)),
        ("revision", ("domains", "dom_1", 2, Domain)),
        ("current", ("domains", Domain)),
    ):
        sql([[row(_domain(), 2), row(_domain(), 2)]])
        with pytest.raises(MetadataBranches):
            await getattr(OntologyRepository(), method)(*args)


async def test_reads_decode_payloads_and_bound_the_current_set(sql):
    sql([[row(_domain())]])
    assert (await OntologyRepository().latest("domains", "dom_1", Domain)).name == "Finance"
    sql([[]])
    assert await OntologyRepository().latest("domains", "dom_1", Domain) is None
    sql([[row(_domain())]])
    assert (await OntologyRepository().revision("domains", "dom_1", 1, Domain)).revision == 1
    sql([[]])
    assert await OntologyRepository().revision("domains", "dom_1", 1, Domain) is None
    sql([[row(_domain())]])
    assert len(await OntologyRepository().history("domains", "dom_1", Domain)) == 1
    sql([[row(_domain())]])
    assert len(await OntologyRepository().current("domains", Domain)) == 1
    sql([[row(_domain())] * (MAX_CURRENT + 1)])
    with pytest.raises(OntologyReadBoundExceeded):
        await OntologyRepository().current("domains", Domain)


# --- Semantic View adapter and publish hook ------------------------------------------


async def test_the_semantic_view_adapter_reads_only_published_versions(monkeypatch):
    from app.modules.intelligence import semantic_views

    views = {
        "live": {"name": "Sales", "status": "ACTIVE", "active_version": 2},
        "draft": {"name": "Draft", "status": "DRAFT", "active_version": None},
        "old": {"name": "Old", "status": "ACTIVE", "active_version": 1},
    }
    versions = {
        ("live", 2): {
            "version": 2,
            "status": "ACTIVE",
            "fingerprint": "f" * 64,
            "definition": definition(),
        },
        ("old", 1): {
            "version": 1,
            "status": "DEPRECATED",
            "fingerprint": "e" * 64,
            "definition": definition(),
        },
    }

    async def get(view_id):
        return views.get(view_id)

    async def version(view_id, number):
        return versions.get((view_id, number))

    async def visible(view_id, user):
        return view_id == "live"

    service = semantic_views.semantic_view_service
    monkeypatch.setattr(service, "_get", get)
    monkeypatch.setattr(service, "_version", version)
    monkeypatch.setattr(service, "is_visible", visible)
    catalog = SemanticViewCatalog()
    published = await catalog.published("live")
    assert published.version == 2 and published.name == "Sales"
    assert await catalog.published("draft") is None
    assert await catalog.published("old") is None
    assert await catalog.published("missing") is None
    assert await catalog.visible("live", ANALYST) is True


async def test_publish_hook_never_undoes_a_publication(monkeypatch, caplog):
    async def boom(view_id, actor):
        raise RuntimeError("redis down")

    monkeypatch.setattr(representations, "refresh_view", boom)
    await representations.refresh_after_publish("sales_sv", ADMIN)
    assert "drift check failed" in caplog.text

    async def counted(view_id, actor):
        return {"repinned": 1, "stale": 0, "cosmetic": 0}

    monkeypatch.setattr(representations, "refresh_view", counted)
    await representations.refresh_after_publish("sales_sv", ADMIN)


async def test_publishing_a_semantic_view_runs_the_drift_check():
    import inspect

    from app.modules.intelligence.semantic_views import SemanticViewService

    source = inspect.getsource(SemanticViewService._commit_publication)
    assert source.index("_relink(view_id)") < source.index("refresh_after_publish(view_id, user)")


# --- HTTP routes -------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, fake_metadata_db):
    repository = FakeRepository()
    catalog = FakeCatalog()
    reads = OntologyService(repository, catalog)
    monkeypatch.setattr(router_module, "ontology_service", reads)
    monkeypatch.setattr(
        router_module, "stewardship_service", StewardshipService(repository, catalog)
    )
    monkeypatch.setattr(router_module, "release_service", ReleaseService(repository, catalog))

    async def audit(**_):
        return "audit"

    monkeypatch.setattr("app.modules.ontology.access.write_audit_log", audit)
    app = FastAPI()
    app.include_router(router_module.router, prefix="/ontology")
    state = {"user": ADMIN}
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    test_client = TestClient(app)
    test_client.state = state  # type: ignore[attr-defined]
    return test_client


def test_routes_cover_the_steward_workflow(client):
    assert client.get("/ontology/releases/current").json()["release"] is None
    domain = client.post(
        "/ontology/domains",
        json={"name": "Finance", "steward_roles": ["finance_steward"]},
    ).json()
    assert (
        client.put(
            f"/ontology/domains/{domain['id']}",
            json={
                "steward_roles": ["finance_steward"],
                "description": "Money",
                "expected_revision": 1,
            },
        ).status_code
        == 200
    )
    assert [item["name"] for item in client.get("/ontology/domains").json()] == ["Finance"]

    client.state["user"] = ANALYST
    proposal = client.post(
        "/ontology/bootstrap",
        json={"view_id": "sales_sv", "domain_id": domain["id"], "operation_id": "boot-0001"},
    ).json()
    assert "request_digest" not in proposal
    assert client.get(f"/ontology/proposals/{proposal['id']}").json()["status"] == "PENDING"
    assert [item["id"] for item in client.get("/ontology/proposals").json()] == [proposal["id"]]

    from tests.unit._ontology import FINANCE_STEWARD

    client.state["user"] = FINANCE_STEWARD
    reviewed = client.post(
        f"/ontology/proposals/{proposal['id']}/review",
        json={"decision": "APPROVE", "rationale": "ok", "operation_id": "review-0001"},
    ).json()
    assert reviewed["status"] == "APPLIED"
    assert client.post(f"/ontology/proposals/{proposal['id']}/apply").json()["status"] == "APPLIED"

    client.state["user"] = ADMIN
    release = client.post("/ontology/releases", json={"note": "R1"}).json()
    assert release["member_counts"]["concept"] == 2 and "members" in release
    published = client.post(f"/ontology/releases/{release['id']}/publish").json()
    assert published["status"] == "PUBLISHED"
    assert client.get(f"/ontology/releases/{release['id']}").json()["id"] == release["id"]

    client.state["user"] = ANALYST
    listed = client.get("/ontology/releases").json()
    assert "members" not in listed[0] and "issues" not in listed[0]
    current = client.get("/ontology/releases/current").json()
    assert current["release"]["id"] == release["id"]
    concepts = client.get("/ontology/concepts", params={"q": "net sales"}).json()["concepts"]
    assert [item["qualified_name"] for item in concepts] == ["Finance.Revenue"]
    concept_id = concepts[0]["id"]
    assert client.get(f"/ontology/concepts/{concept_id}").json()["concept"]["id"] == concept_id
    assert client.get(f"/ontology/concepts/{concept_id}/related").json()["concepts"] == []
    bindings = client.get(f"/ontology/concepts/{concept_id}/representations").json()
    assert bindings["representations"][0]["usable"] is True

    other = client.post(
        "/ontology/proposals",
        json={
            "title": "Churn",
            "rationale": "pilot",
            "operation_id": "churn-0001",
            "changes": [
                {
                    "target": "concept",
                    "op": "create",
                    "body": {
                        "domain_id": domain["id"],
                        "kind": "METRIC",
                        "slug": "Churn",
                        "label": "Churn",
                        "definition": "Customers lost.",
                    },
                }
            ],
        },
    )
    assert other.status_code == 201
    assert client.post(f"/ontology/proposals/{other.json()['id']}/withdraw").json()["status"] == (
        "WITHDRAWN"
    )

    client.state["user"] = ADMIN
    second = client.post("/ontology/releases", json={}).json()
    client.post(f"/ontology/releases/{second['id']}/publish")
    rolled = client.post(
        f"/ontology/releases/{release['id']}/rollback", json={"reason": "check"}
    ).json()
    assert rolled == {"release_id": release["id"], "previous_release_id": second["id"]}


def test_manifest_hash_ignores_member_order():
    members = [
        ReleaseMember(kind="concept", id="b", revision=1, fingerprint="a" * 64),
        ReleaseMember(kind="domain", id="a", revision=2, fingerprint="b" * 64),
    ]
    assert manifest_hash(members) == manifest_hash(list(reversed(members)))
    release = Release(
        id="ont_rel_0001",
        actor=Actor.from_user(ADMIN),
        sequence=1,
        members=members,
        manifest_hash=manifest_hash(members),
    )
    renamed = release.model_copy(update={"note": "x"})
    assert release.content_fingerprint() != renamed.content_fingerprint()
    assert PublishedView("v", "n", 1, "f", {}).version == 1
