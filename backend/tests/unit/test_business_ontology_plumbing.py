"""Ontology persistence SQL, HTTP routes, and the Semantic View publish hook."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.modules.ontology import repository as repository_module
from app.modules.ontology import representations
from app.modules.ontology import router as router_module
from app.modules.ontology.contracts import Actor, Domain, Release, ReleaseMember, manifest_hash
from app.modules.ontology.releases import ReleaseService
from app.modules.ontology.repository import MAX_CURRENT, OntologyRepository
from app.modules.ontology.representations import PublishedView, SemanticViewCatalog
from app.modules.ontology.schema import ONTOLOGY_DDL, ONTOLOGY_TABLES, ensure_ontology_schema
from app.modules.ontology.service import OntologyService
from app.modules.ontology.stewardship import StewardshipService
from tests.unit._ontology import ADMIN, ANALYST, FakeCatalog, FakeRepository, definition


def _domain(name="Finance", revision=1) -> Domain:
    return Domain(
        id="dom_1", revision=revision, actor=Actor.from_user(ADMIN), name=name,
        steward_roles=["finance_steward"],
    )


class FakeDb:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, list]] = []

    async def execute_system(self, sql, params=None):
        self.calls.append((sql, params))
        return self.responses.pop(0) if self.responses else {"rows": []}


class Lease:
    def __init__(self, renew=True):
        self._renew = renew

    async def renew(self):
        return self._renew


@pytest.fixture
def sql(monkeypatch):
    def install(responses, renew=True):
        fake = FakeDb(responses)
        monkeypatch.setattr(repository_module, "db", fake)

        @asynccontextmanager
        async def lock(key, **_):
            assert key.startswith("ontology:")
            yield Lease(renew)

        monkeypatch.setattr(repository_module, "metadata_lock", lock)
        return fake

    return install


async def test_schema_creates_every_table(monkeypatch):
    fake = FakeDb([])
    monkeypatch.setattr("app.modules.ontology.schema.db", fake)
    await ensure_ontology_schema()
    assert len(fake.calls) == len(ONTOLOGY_TABLES) == len(ONTOLOGY_DDL)
    assert all("PRIMARY KEY(id, revision, operation_id)" in sql for sql, _ in fake.calls)


async def test_save_inserts_the_next_revision(sql):
    fake = sql([{"rows": []}])
    saved = await OntologyRepository().save("domains", _domain())
    assert saved.revision == 1
    insert_sql, params = fake.calls[-1]
    assert insert_sql.startswith("INSERT INTO NOVA_SYSTEM.CONFIG_ONTOLOGY_DOMAINS")
    assert params[0:2] == ["dom_1", 1]
    assert params[3:6] == ["nova_admin", "ACCOUNTADMIN", 1]
    assert json.loads(params[6])["name"] == "Finance"


async def test_save_many_writes_changed_records_in_one_insert(sql):
    stored = _domain()
    other = _domain("Treasury").model_copy(update={"id": "dom_2"})
    # latest_many returns the stored domain; dom_2 and dom_3 are new.
    fake = sql([{"rows": [[stored.model_dump_json(), 1]]}])
    third = _domain("Ops").model_copy(update={"id": "dom_3"})
    written = await OntologyRepository().save_many(
        "domains", [(stored, 1), (other, 0), (third, 0)]
    )
    assert written == 2
    inserts = [call for call in fake.calls if call[0].startswith("INSERT")]
    assert len(inserts) == 1 and inserts[0][0].count("NOW()") == 2
    assert inserts[0][1][0] == "dom_2" and inserts[0][1][7] == "dom_3"


async def test_save_many_writes_a_large_batch_in_few_statements(sql):
    # A join run over many databases stores tens of thousands of results; one statement
    # per BATCH_ROWS keeps the write inside the lease's deadline.
    from app.modules.ontology.repository import BATCH_ROWS

    fake = sql([])
    records = [(_domain().model_copy(update={"id": f"dom_{i}"}), 0)
               for i in range(2 * BATCH_ROWS + 1)]
    assert await OntologyRepository().save_many("domains", records) == len(records)
    inserts = [call for call in fake.calls if call[0].startswith("INSERT")]
    assert [call[0].count("NOW()") for call in inserts] == [BATCH_ROWS, BATCH_ROWS, 1]


async def test_save_many_aborts_the_whole_batch_on_a_conflict(sql):
    stored = _domain()
    fake = sql([{"rows": [[stored.model_dump_json(), 1]]}])
    with pytest.raises(HTTPException) as conflict:
        await OntologyRepository().save_many(
            "domains",
            [(_domain("Treasury"), 0), (_domain("Ops").model_copy(update={"id": "x"}), 0)],
        )
    assert conflict.value.status_code == 409
    assert not [call for call in fake.calls if call[0].startswith("INSERT")]


async def test_saving_identical_content_is_a_no_op(sql):
    stored = _domain()
    fake = sql([{"rows": [[stored.model_dump_json(), 1]]}])
    again = await OntologyRepository().save(
        "domains", stored.model_copy(update={"revision": 9}), expected_revision=0
    )
    assert again.revision == 1
    assert len(fake.calls) == 1


async def test_save_checks_the_expected_revision(sql):
    stored = _domain()
    sql([{"rows": [[stored.model_dump_json(), 1]]}])
    with pytest.raises(HTTPException) as stale:
        await OntologyRepository().save("domains", _domain("Treasury"), expected_revision=0)
    assert stale.value.status_code == 409
    sql([{"rows": []}])
    with pytest.raises(HTTPException) as gone:
        await OntologyRepository().save("domains", _domain(), expected_revision=3)
    assert gone.value.status_code == 409
    sql([{"rows": [[stored.model_dump_json(), 1]]}])
    changed = await OntologyRepository().save("domains", _domain("Treasury"), expected_revision=1)
    assert changed.revision == 2 and changed.created_at == stored.created_at


async def test_an_expired_lease_never_writes(sql):
    fake = sql([{"rows": []}], renew=False)
    with pytest.raises(HTTPException) as expired:
        await OntologyRepository().save("domains", _domain())
    assert expired.value.status_code == 409
    assert not any(call[0].startswith("INSERT") for call in fake.calls)


async def test_forked_revisions_require_reconciliation(sql):
    row = [_domain().model_dump_json(), 2]
    for method, args in (
        ("latest", ("domains", "dom_1", Domain)),
        ("revision", ("domains", "dom_1", 2, Domain)),
    ):
        sql([{"rows": [row, row]}])
        with pytest.raises(HTTPException) as fork:
            await getattr(OntologyRepository(), method)(*args)
        assert fork.value.status_code == 409
    sql([{"rows": [[row[0], 2]]}])
    with pytest.raises(HTTPException):
        await OntologyRepository().current("domains", Domain)


async def test_reads_decode_payloads_and_bound_the_current_set(sql):
    payload = _domain().model_dump_json()
    sql([{"rows": [[payload, 1]]}])
    assert (await OntologyRepository().latest("domains", "dom_1", Domain)).name == "Finance"
    sql([{"rows": []}])
    assert await OntologyRepository().latest("domains", "dom_1", Domain) is None
    sql([{"rows": [[json.loads(payload), 1]]}])
    assert (await OntologyRepository().revision("domains", "dom_1", 1, Domain)).revision == 1
    sql([{"rows": []}])
    assert await OntologyRepository().revision("domains", "dom_1", 1, Domain) is None
    sql([{"rows": [[payload, 1]]}])
    assert len(await OntologyRepository().history("domains", "dom_1", Domain)) == 1
    sql([{"rows": [[payload, 1]]}])
    assert len(await OntologyRepository().current("domains", Domain)) == 1
    sql([{"rows": [[payload, 1]]} | {"rows": [[payload, 1]] * (MAX_CURRENT + 1)}])
    with pytest.raises(HTTPException) as bound:
        await OntologyRepository().current("domains", Domain)
    assert bound.value.status_code == 422


# --- Semantic View adapter and publish hook ------------------------------------------


async def test_the_semantic_view_adapter_reads_only_published_versions(monkeypatch):
    from app.modules.intelligence import semantic_views

    views = {
        "live": {"name": "Sales", "status": "ACTIVE", "active_version": 2},
        "draft": {"name": "Draft", "status": "DRAFT", "active_version": None},
        "old": {"name": "Old", "status": "ACTIVE", "active_version": 1},
    }
    versions = {
        ("live", 2): {"version": 2, "status": "ACTIVE", "fingerprint": "f" * 64,
                      "definition": definition()},
        ("old", 1): {"version": 1, "status": "DEPRECATED", "fingerprint": "e" * 64,
                     "definition": definition()},
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

    source = inspect.getsource(SemanticViewService._publish_locked)
    assert source.index("_relink(view_id)") < source.index("refresh_after_publish(view_id, user)")


# --- HTTP routes -------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
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
    assert client.put(
        f"/ontology/domains/{domain['id']}",
        json={"steward_roles": ["finance_steward"], "description": "Money",
              "expected_revision": 1},
    ).status_code == 200
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
            "title": "Churn", "rationale": "pilot", "operation_id": "churn-0001",
            "changes": [{"target": "concept", "op": "create", "body": {
                "domain_id": domain["id"], "kind": "METRIC", "slug": "Churn",
                "label": "Churn", "definition": "Customers lost."}}],
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
        id="ont_rel_0001", actor=Actor.from_user(ADMIN), sequence=1, members=members,
        manifest_hash=manifest_hash(members),
    )
    renamed = release.model_copy(update={"note": "x"})
    assert release.content_fingerprint() != renamed.content_fingerprint()
    assert PublishedView("v", "n", 1, "f", {}).version == 1
