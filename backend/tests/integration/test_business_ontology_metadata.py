"""Real StarRocks/Redis acceptance for ontology revisions and the release alias."""

from pathlib import Path
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
