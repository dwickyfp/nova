"""Real StarRocks/Redis acceptance for ontology revisions and the release alias."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.modules.ontology.contracts import Actor, Alias, Domain
from app.modules.ontology.repository import OntologyRepository
from app.modules.ontology.schema import ONTOLOGY_TABLES, ensure_ontology_schema
from tests.conftest import redis_url, root_password
from tests.integration._stack import require_shared_stack, shared_stack_host_port

pytestmark = pytest.mark.engine

ACTOR = Actor(principal="ontology-test", active_role="ACCOUNTADMIN", security_context_version=1)


@pytest.fixture
async def ontology_db(request, monkeypatch, docker_services):
    require_shared_stack(request)
    monkeypatch.setattr(settings, "STARROCKS_HOST", "127.0.0.1")
    monkeypatch.setattr(
        settings,
        "STARROCKS_FE_MYSQL_PORT",
        shared_stack_host_port("NOVA_TEST_FE_MYSQL_PORT", 29030),
    )
    monkeypatch.setattr(settings, "STARROCKS_ROOT_USER", "root")
    monkeypatch.setattr(settings, "STARROCKS_ROOT_PASSWORD", root_password())
    monkeypatch.setattr(
        settings, "REDIS_URL", redis_url(shared_stack_host_port("NOVA_TEST_REDIS_PORT", 26379))
    )
    await db.init_system_pool()
    await session_store.init()
    await db.execute_system("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    await ensure_ontology_schema()
    prefix = "it_" + uuid4().hex[:16]
    try:
        yield prefix
    finally:
        for table in ONTOLOGY_TABLES.values():
            await db.execute_system(
                f"DELETE FROM NOVA_SYSTEM.{table} WHERE id LIKE %s", [prefix + "%"]
            )
        await session_store.close()
        await db.close_system_pool()


async def test_bootstrap_ddl_matches_the_migration_and_is_idempotent(ontology_db):
    await ensure_ontology_schema()
    migration = Path("migrations/20261106_business_ontology.sql").read_text()
    for statement in migration.split(";"):
        sql = "\n".join(
            line for line in statement.splitlines() if not line.strip().startswith("--")
        ).strip()
        if sql:
            await db.execute_system(sql)
    header = "-- Supernova Business Ontology: append-only revisions of shared business meaning"
    bootstrap = (
        Path("../docker/init-nova.sql")
        .read_text()
        .split(header, 1)[1]
        .split("-- End Supernova Business Ontology schema", 1)[0]
        .strip()
    )
    assert bootstrap == migration.split("\n", 1)[1].strip()


async def test_derived_layer_ddl_matches_its_migration(ontology_db):
    migration = Path("migrations/20261107_ontology_derived_layer.sql").read_text()
    for statement in migration.split(";"):
        sql = "\n".join(
            line for line in statement.splitlines() if not line.strip().startswith("--")
        ).strip()
        if sql:
            await db.execute_system(sql)
    bootstrap = (
        Path("../docker/init-nova.sql")
        .read_text()
        .split("-- Supernova derived ontology layer:", 1)[1]
        .split("\n", 1)[1]
        .split("-- End Supernova derived ontology layer", 1)[0]
        .strip()
    )
    assert bootstrap == migration.split("\n", 1)[1].strip()


async def test_evaluation_ddl_matches_its_migration(ontology_db):
    migration = Path("migrations/20261108_ontology_evaluation.sql").read_text()
    for statement in migration.split(";"):
        sql = "\n".join(
            line for line in statement.splitlines() if not line.strip().startswith("--")
        ).strip()
        if sql:
            await db.execute_system(sql)
    bootstrap = (
        Path("../docker/init-nova.sql")
        .read_text()
        .split("-- Supernova ontology evaluation:", 1)[1]
        .split("\n", 1)[1]
        .split("-- End Supernova ontology evaluation", 1)[0]
        .strip()
    )
    assert bootstrap == migration.split("\n", 1)[1].strip()


async def test_a_batch_write_appends_only_changed_records(ontology_db):
    repository = OntologyRepository()
    domains = [
        Domain(id=f"{ontology_db}_{index}", actor=ACTOR, name=f"D{index}",
               steward_roles=["finance_steward"])
        for index in range(60)
    ]
    assert await repository.save_many("domains", [(item, 0) for item in domains]) == 60
    assert await repository.save_many("domains", [(item, 1) for item in domains]) == 0
    changed = domains[0].model_copy(update={"description": "Money"})
    assert await repository.save_many("domains", [(changed, 1), (domains[1], 1)]) == 1
    latest = await repository.latest_many("domains", [item.id for item in domains[:2]], Domain)
    assert latest[changed.id].revision == 2 and latest[changed.id].description == "Money"
    assert latest[domains[1].id].revision == 1
    with pytest.raises(HTTPException) as stale:
        await repository.save_many("domains", [(domains[2].model_copy(update={"name": "X"}), 0)])
    assert stale.value.status_code == 409


async def test_revisions_are_appended_checked_and_read_back(ontology_db):
    repository = OntologyRepository()
    domain = Domain(
        id=ontology_db + "_finance", actor=ACTOR, name="Finance",
        steward_roles=["finance_steward"],
    )
    first = await repository.save("domains", domain)
    assert first.revision == 1
    assert (await repository.save("domains", domain)).revision == 1  # identical: no-op
    second = await repository.save(
        "domains", domain.model_copy(update={"description": "Money"}), expected_revision=1
    )
    assert second.revision == 2
    with pytest.raises(HTTPException) as stale:
        await repository.save(
            "domains", domain.model_copy(update={"description": "Stale"}), expected_revision=1
        )
    assert stale.value.status_code == 409
    assert (await repository.latest("domains", domain.id, Domain)).description == "Money"
    assert (await repository.revision("domains", domain.id, 1, Domain)).description == ""
    assert [item.revision for item in await repository.history("domains", domain.id, Domain)] == [
        2, 1,
    ]
    current = {item.id: item for item in await repository.current("domains", Domain)}
    assert current[domain.id].revision == 2


async def _empty_ontology() -> bool:
    for table in ONTOLOGY_TABLES.values():
        result = await db.execute_system(f"SELECT COUNT(*) FROM NOVA_SYSTEM.{table}")
        if result["rows"][0][0]:
            return False
    return True


async def test_steward_release_and_grounding_round_trip_on_the_engine(ontology_db, monkeypatch):
    """Review, release, resolve and roll back through real StarRocks reads.

    Releases and the ``current`` alias are deployment-wide, so this runs only
    against an ontology nobody has started, and removes everything it wrote.
    """
    if not await _empty_ontology():
        pytest.skip("the engine already holds ontology records; not touching them")
    from app.modules.ontology import access
    from app.modules.ontology.contracts import RepresentationChange
    from app.modules.ontology.grounding import ground
    from app.modules.ontology.releases import AliasMove, ReleasePrepare, ReleaseService
    from app.modules.ontology.resolver import ConceptResolver
    from app.modules.ontology.service import DomainCreate, OntologyService
    from app.modules.ontology.stewardship import ProposalCreate, ReviewRequest, StewardshipService
    from tests.unit._ontology import ADMIN, ANALYST, FINANCE_STEWARD, FakeCatalog

    async def no_audit(**_):
        return "audit"

    monkeypatch.setattr(access, "write_audit_log", no_audit)
    started = (await db.execute_system("SELECT NOW()"))["rows"][0][0]
    repository = OntologyRepository()
    catalog = FakeCatalog()
    reads = OntologyService(repository, catalog)
    stewardship = StewardshipService(repository, catalog)
    releases = ReleaseService(repository, catalog)
    try:
        finance = await reads.create_domain(
            DomainCreate(name="Finance", steward_roles=["finance_steward"],
                         visibility_roles=["finance_analyst"]),
            ADMIN,
        )
        from app.modules.ontology.contracts import ConceptBody, ConceptChange

        proposal = await stewardship.create(
            ProposalCreate(
                title="Revenue", rationale="Engine acceptance", operation_id=ontology_db + "-p1",
                changes=[
                    ConceptChange(op="create", body=ConceptBody(
                        domain_id=finance["id"], kind="METRIC", slug="NetRevenue",
                        label="Net revenue", definition="Revenue after discounts.",
                        aliases=["omzet"], unit="IDR",
                    )),
                    RepresentationChange(
                        op="create", concept_id="@0", view_id="sales_sv", metric="revenue"
                    ),
                ],
            ),
            ANALYST,
        )
        applied = await stewardship.review(
            proposal.id,
            ReviewRequest(decision="APPROVE", rationale="ok", operation_id=ontology_db + "-r1"),
            FINANCE_STEWARD,
        )
        assert applied.status == "APPLIED"
        first = await releases.prepare(ReleasePrepare(), ADMIN)
        assert first.status == "PREPARED", first.issues
        await releases.publish(first.id, ADMIN)

        resolver = ConceptResolver(reads)
        resolved = await resolver.resolve("berapa omzet bulan ini", ANALYST)
        assert [item["qualified_name"] for item in resolved.concepts] == ["Finance.NetRevenue"]
        context = SimpleNamespace(
            user=ANALYST, release_manifest=None, ontology_release_id=None,
            requested_semantic_view=None,
        )

        async def models():
            view = catalog.views["sales_sv"]
            return [{"semantic_model_id": "sales_sv", "name": "sales_sv",
                     "definition": view.definition}]

        grounding = await ground("omzet per kota", context, models, resolver=resolver)
        assert grounding.status == "grounded" and list(grounding.required) == ["revenue"]

        second = await releases.prepare(ReleasePrepare(note="again"), ADMIN)
        await releases.publish(second.id, ADMIN)
        moved = await releases.rollback(first.id, AliasMove(reason="engine check"), ADMIN)
        assert moved.release_id == first.id
        pinned = await reads.list_concepts(ANALYST)
        assert pinned["release_id"] == first.id and len(pinned["concepts"]) == 1
    finally:
        for table in ONTOLOGY_TABLES.values():
            await db.execute_system(
                f"DELETE FROM NOVA_SYSTEM.{table} WHERE created_at >= %s", [started]
            )


async def test_the_alias_moves_by_compare_and_swap(ontology_db):
    repository = OntologyRepository()
    alias_id = ontology_db + "_alias"
    first = await repository.save(
        "aliases", Alias(id=alias_id, actor=ACTOR, release_id="ont_rel_0001", reason="publish")
    )
    moved = await repository.save(
        "aliases",
        Alias(
            id=alias_id, actor=ACTOR, release_id="ont_rel_0002",
            previous_release_id="ont_rel_0001", reason="publish",
        ),
        expected_revision=first.revision,
    )
    with pytest.raises(HTTPException) as raced:
        await repository.save(
            "aliases",
            Alias(id=alias_id, actor=ACTOR, release_id="ont_rel_0003", reason="late publish"),
            expected_revision=first.revision,
        )
    assert raced.value.status_code == 409
    assert (await repository.latest("aliases", alias_id, Alias)).release_id == moved.release_id
