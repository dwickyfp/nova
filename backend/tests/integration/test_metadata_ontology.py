"""Business ontology revisions on the real PG fixture: append-only revisions,
one winner per (id, revision), branch reporting, stewardship and releases."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb

from app.core.metadata_bootstrap import WORKLOAD_ROLES
from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
    MetadataReadOnlyMaintenance,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.ontology.contracts import (
    Actor,
    Alias,
    Concept,
    ConceptBody,
    ConceptChange,
    ConceptKind,
    Domain,
    Proposal,
    Release,
    Review,
)
from app.modules.ontology.derived import Compilation
from app.modules.ontology.evaluation.contracts import EvalResult
from app.modules.ontology.metadata_repository import (
    MetadataBranches,
    OntologyMetadataRepository,
)
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg
READERS = {"web", "agent", "automation"}
ADMIN = ("admin-1", "ACCOUNTADMIN")


def actor(name, role):
    return Actor(principal=name, active_role=role, security_context_version=5)


class Ontology:
    def __init__(self, env, epoch):
        self.environment = env
        self.epoch = epoch

    @asynccontextmanager
    async def transaction(self, workload="web", *, name="alice", role="ANALYST",
                          read_only=False):
        user = session(name, active=role)
        db = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        try:
            await db.open(schema_version=len(load_migrations()), metadata_epoch=self.epoch)
            context = SecurityContext.from_session(user)
            async with db.transaction(context, read_only=read_only) as conn:
                yield OntologyMetadataRepository(conn, context), conn
        finally:
            await db.close()

    async def admin(self, query, params=()):
        cursor = await self.environment.admin.execute(query, params)
        return await cursor.fetchall() if cursor.description else None

    @asynccontextmanager
    async def importer(self):
        """The migrator acting as owner, as the cutover import does."""
        async with await psycopg.AsyncConnection.connect(
            self.environment.role_config("nova_meta_migrator").dsn, autocommit=True,
        ) as conn, conn.transaction():
            await conn.execute("SET LOCAL ROLE nova_meta_owner")
            yield conn


@pytest_asyncio.fixture
async def ontology(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="e" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    return Ontology(environment, epoch)


def compilation(record_id="cmp_1", version=1, who=("alice", "ANALYST")):
    return Compilation(id=record_id, actor=actor(*who), view_id="sv_orders",
                       view_name="Orders", version=version, view_fingerprint="f" * 64)


def domain(**changes):
    return Domain(**{"id": "dom_sales", "actor": actor(*ADMIN), "name": "sales",
                     "steward_roles": ["STEWARD"], **changes})


def proposal(submitter="alice", role="ANALYST", **changes):
    body = ConceptBody(domain_id="dom_sales", kind=ConceptKind.METRIC, slug="revenue",
                       label="Revenue", definition="Sum of order amounts")
    return Proposal(**{
        "id": "prp_1", "actor": actor(submitter, role), "title": "Add revenue",
        "rationale": "Reports need it", "changes": [ConceptChange(op="create", body=body)],
        "domain_ids": ["dom_sales"], "submitter": submitter, "operation_id": "op-00000001",
        "request_digest": "d" * 64, **changes})


def review(principal, role="STEWARD", decision="APPROVE", **changes):
    return Review(**{"operation_id": "rev-" + principal.ljust(8, "x"), "principal": principal,
                     "active_role": role, "domain_ids": ["dom_sales"], "decision": decision,
                     "rationale": "Checked", **changes})


def concept(proposal_id="prp_1", who=("carol", "STEWARD")):
    return Concept(id="cpt_revenue", actor=actor(*who), domain_id="dom_sales",
                   kind=ConceptKind.METRIC, slug="revenue", label="Revenue",
                   definition="Sum of order amounts", proposal_id=proposal_id)


async def create_domain(ontology):
    async with ontology.transaction(name=ADMIN[0], role=ADMIN[1]) as (repo, _):
        return await repo.save("domains", domain())


async def approved_proposal(ontology):
    await create_domain(ontology)
    async with ontology.transaction() as (repo, _):
        filed = await repo.save("proposals", proposal())
    async with ontology.transaction(name="carol", role="STEWARD") as (repo, _):
        approved = filed.model_copy(update={"reviews": [review("carol")], "status": "APPROVED"})
        return await repo.save("proposals", approved, expected_revision=1)


async def test_concurrent_revisions_of_one_record_have_one_winner(ontology):
    async with ontology.transaction() as (repo, _):
        first = await repo.save("compilations", compilation())
    assert first.revision == 1
    ready = asyncio.Barrier(2)

    async def edit(version):
        async with ontology.transaction() as (repo, _):
            await ready.wait()
            return await repo.save("compilations", compilation(version=version),
                                   expected_revision=1)

    results = await asyncio.gather(edit(2), edit(3), return_exceptions=True)
    assert sum(isinstance(r, Compilation) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1
    rows = await ontology.admin(
        "SELECT revision,provenance,actor FROM nova_meta.config_ontology_compilations "
        "ORDER BY revision")
    assert [(r["revision"], r["provenance"], r["actor"]) for r in rows] == [
        (1, "runtime", "alice"), (2, "runtime", "alice")]
    # Two concurrent first revisions of a new id: also one winner.
    ready2 = asyncio.Barrier(2)

    async def create(version):
        async with ontology.transaction() as (repo, _):
            await ready2.wait()
            return await repo.save("compilations", compilation("cmp_2", version))

    results = await asyncio.gather(create(1), create(2), return_exceptions=True)
    assert sum(isinstance(r, Compilation) for r in results) == 1
    assert sum(isinstance(r, MetadataConflict) for r in results) == 1


async def test_saves_follow_the_source_revision_protocol(ontology):
    async with ontology.transaction() as (repo, _):
        first = await repo.save("compilations", compilation())
        assert await repo.save("compilations", compilation()) == first  # unchanged: no-op
        second = await repo.save("compilations", compilation(version=2), expected_revision=1)
        assert second.created_at == first.created_at and second.revision == 2
        assert (await repo.latest("compilations", "cmp_1", Compilation)).version == 2
        assert [r.revision for r in await repo.history("compilations", "cmp_1", Compilation)] == [
            2, 1]
        assert (await repo.revision("compilations", "cmp_1", 1, Compilation)).version == 1
    for expected in (1, 0):
        with pytest.raises(MetadataConflict):
            async with ontology.transaction() as (repo, _):
                await repo.save("compilations", compilation(version=9),
                                expected_revision=expected)
    with pytest.raises(MetadataConflict):
        async with ontology.transaction() as (repo, _):
            await repo.save("compilations", compilation("cmp_new"), expected_revision=4)


async def test_revisions_are_immutable(ontology):
    async with ontology.transaction() as (repo, _):
        await repo.save("compilations", compilation())
    for statement in (
        "UPDATE nova_meta.config_ontology_compilations SET payload='{}'::jsonb",
        "DELETE FROM nova_meta.config_ontology_compilations",
        "TRUNCATE nova_meta.config_ontology_compilations",
        "UPDATE nova_meta.config_ontology_heads SET revision=9",
        "INSERT INTO nova_meta.config_ontology_heads (kind,id,revision,branches) "
        "VALUES ('config_ontology_compilations','x',1,1)",
    ):
        with pytest.raises(MetadataPermissionDenied):
            async with ontology.transaction() as (_, conn):
                await conn.execute(statement)
    # A raw insert must be the next revision and describe itself.
    payload = compilation(version=5).model_dump(mode="json")
    for revision, body in ((1, {**payload, "revision": 1}), (3, {**payload, "revision": 3})):
        with pytest.raises(MetadataConflict):
            async with ontology.transaction() as (_, conn):
                await conn.execute(
                    "INSERT INTO nova_meta.config_ontology_compilations "
                    "(id,revision,operation_id,payload) VALUES ('cmp_1',%s,'op',%s)",
                    (revision, Jsonb(body)))
    with pytest.raises(MetadataIntegrityViolation):
        async with ontology.transaction() as (_, conn):
            await conn.execute(
                "INSERT INTO nova_meta.config_ontology_compilations "
                "(id,revision,operation_id,payload) VALUES ('cmp_1',2,'op',%s)",
                (Jsonb({**payload, "revision": 7}),))


async def test_current_reads_heads_and_reports_imported_branches(ontology):
    await ontology.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    async with ontology.importer() as conn:
        rows = [("cmp_a", 1, "op-a1", 1), ("cmp_a", 2, "op-a2", 2),
                ("cmp_b", 1, "op-b1", 1), ("cmp_b", 1, "op-b2", 3)]
        for record_id, revision, operation, version in rows:
            body = compilation(record_id, version).model_dump(mode="json")
            await conn.execute(
                "INSERT INTO nova_meta.config_ontology_compilations (id,revision,operation_id,"
                "actor,active_role,security_context_version,payload,created_at) "
                "VALUES (%s,%s,%s,'legacy','ANALYST',3,%s,'2026-11-01T00:00:00Z')",
                (record_id, revision, operation, Jsonb({**body, "revision": revision})))
    await ontology.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    assert await ontology.admin(
        "SELECT DISTINCT provenance FROM nova_meta.config_ontology_compilations") == [
        {"provenance": "imported"}]
    async with ontology.transaction(read_only=True) as (repo, _):
        assert (await repo.latest("compilations", "cmp_a", Compilation)).version == 2
        assert await repo.branches("compilations") == [
            {"id": "cmp_b", "revision": 1, "branches": 2}]
        with pytest.raises(MetadataBranches):
            await repo.current("compilations", Compilation)
        with pytest.raises(MetadataBranches):
            await repo.latest("compilations", "cmp_b", Compilation)
        with pytest.raises(MetadataBranches):
            await repo.revision("compilations", "cmp_b", 1, Compilation)
    # A runtime write cannot build on a fork, even naming its revision.
    with pytest.raises(MetadataConflict):
        async with ontology.transaction() as (_, conn):
            body = compilation("cmp_b", 9).model_dump(mode="json")
            await conn.execute(
                "INSERT INTO nova_meta.config_ontology_compilations "
                "(id,revision,operation_id,payload) VALUES ('cmp_b',2,'op',%s)",
                (Jsonb({**body, "revision": 2}),))
    # Reconciliation appends a resolving revision under maintenance.
    await ontology.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    async with ontology.importer() as conn:
        body = compilation("cmp_b", 4).model_dump(mode="json")
        await conn.execute(
            "INSERT INTO nova_meta.config_ontology_compilations (id,revision,operation_id,"
            "actor,active_role,security_context_version,payload,created_at) "
            "VALUES ('cmp_b',2,'op-b3','steward','ACCOUNTADMIN',3,%s,clock_timestamp())",
            (Jsonb({**body, "revision": 2}),))
    await ontology.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    async with ontology.transaction() as (repo, _):
        assert [c.version for c in await repo.current("compilations", Compilation)] == [2, 4]
        saved = await repo.save("compilations", compilation("cmp_b", 5), expected_revision=2)
        assert saved.revision == 3


async def test_a_run_reads_only_its_results_through_the_index(ontology):
    results = [
        (EvalResult(id=f"evr_{run}_{n}", actor=actor("alice", "ANALYST"), run_id=run,
                    item=f"case {n}", outcome="PASS"), 0)
        for run in ("run_a", "run_b") for n in range(3)
    ]
    async with ontology.transaction() as (repo, _):
        assert await repo.save_many("eval_results", results) == 6
        assert await repo.save_many("eval_results", results) == 0
        changed = results[0][0].model_copy(update={"outcome": "FAIL"})
        assert await repo.save_many("eval_results", [(changed, 1)]) == 1
    async with ontology.transaction(read_only=True) as (repo, conn):
        found = await repo.eval_results_for_run("run_a", EvalResult, limit=10)
        assert [(r.id, r.outcome, r.revision) for r in found] == [
            ("evr_run_a_0", "FAIL", 2), ("evr_run_a_1", "PASS", 1), ("evr_run_a_2", "PASS", 1)]
        await conn.execute("SET LOCAL enable_seqscan=off")
        cursor = await conn.execute(
            "EXPLAIN SELECT r.payload FROM nova_meta.config_ontology_eval_results r "
            "WHERE r.run_id=%s", ("run_a",))
        plan = " ".join(row["QUERY PLAN"] for row in await cursor.fetchall())
        assert "config_ontology_eval_results_run" in plan
    # A conflict anywhere in a batch writes nothing.
    stale = results[0][0].model_copy(update={"outcome": "SKIP"})
    fresh = results[1][0].model_copy(update={"outcome": "SKIP"})
    with pytest.raises(MetadataConflict):
        async with ontology.transaction() as (repo, _):
            await repo.save_many("eval_results", [(fresh, 1), (stale, 1)])
    async with ontology.transaction(read_only=True) as (repo, _):
        assert (await repo.latest("eval_results", fresh.id, EvalResult)).outcome == "PASS"


async def test_stewardship_is_enforced_by_storage(ontology):
    with pytest.raises(MetadataPermissionDenied):  # domains need ACCOUNTADMIN
        async with ontology.transaction(name="carol", role="STEWARD") as (repo, _):
            await repo.save("domains", domain())
    await create_domain(ontology)
    with pytest.raises(MetadataPermissionDenied):  # the submitter is the caller
        async with ontology.transaction(name="bob") as (repo, _):
            await repo.save("proposals", proposal("alice"))
    with pytest.raises(MetadataIntegrityViolation):  # a proposal starts pending
        async with ontology.transaction() as (repo, _):
            await repo.save("proposals", proposal(status="APPROVED"))
    async with ontology.transaction("agent") as (repo, _):
        filed = await repo.save("proposals", proposal())
    with pytest.raises(MetadataConflict):  # concepts apply approved proposals only
        async with ontology.transaction(name="carol", role="STEWARD") as (repo, _):
            await repo.save("concepts", concept())
    refused = [
        ("alice", "STEWARD", review("alice"), "APPROVED"),   # own proposal
        ("bob", "ANALYST", review("bob", "ANALYST"), "APPROVED"),  # not a steward
        ("carol", "STEWARD", review("dave"), "APPROVED"),     # review in another's name
    ]
    for name, role, item, status in refused:
        with pytest.raises(MetadataPermissionDenied):
            async with ontology.transaction(name=name, role=role) as (repo, _):
                await repo.save("proposals", filed.model_copy(
                    update={"reviews": [item], "status": status}), expected_revision=1)
    with pytest.raises(MetadataIntegrityViolation):  # the status follows the reviews
        async with ontology.transaction(name="carol", role="STEWARD") as (repo, _):
            await repo.save("proposals", filed.model_copy(
                update={"reviews": [review("carol", decision="REJECT")], "status": "APPROVED"}),
                expected_revision=1)
    with pytest.raises(MetadataIntegrityViolation):  # content is fixed once filed
        async with ontology.transaction(name="carol", role="STEWARD") as (repo, _):
            await repo.save("proposals", filed.model_copy(
                update={"title": "Changed", "reviews": [review("carol")], "status": "APPROVED"}),
                expected_revision=1)
    async with ontology.transaction(name="carol", role="STEWARD") as (repo, _):
        approved = await repo.save("proposals", filed.model_copy(
            update={"reviews": [review("carol")], "status": "APPROVED"}), expected_revision=1)
    with pytest.raises(MetadataPermissionDenied):  # only a steward or ACCOUNTADMIN applies
        async with ontology.transaction(name="bob") as (repo, _):
            await repo.save("concepts", concept(who=("bob", "ANALYST")))
    with pytest.raises(MetadataPermissionDenied):  # only the submitter withdraws
        async with ontology.transaction(name="carol", role="STEWARD") as (repo, _):
            await repo.save("proposals", approved.model_copy(update={"status": "WITHDRAWN"}),
                            expected_revision=2)
    async with ontology.transaction(name="carol", role="STEWARD") as (repo, _):
        await repo.save("concepts", concept())
        applied = await repo.save("proposals", approved.model_copy(update={"status": "APPLIED"}),
                                  expected_revision=2)
    assert applied.revision == 3
    with pytest.raises(MetadataConflict):  # applied is final
        async with ontology.transaction() as (repo, _):
            await repo.save("proposals", applied.model_copy(update={"status": "WITHDRAWN"}),
                            expected_revision=3)
    with pytest.raises(MetadataConflict):  # a later concept change needs a new proposal
        async with ontology.transaction(name="carol", role="STEWARD") as (repo, _):
            await repo.save("concepts", concept().model_copy(update={"label": "Sales"}),
                            expected_revision=1)
    audit = await ontology.admin(
        "SELECT metadata_table,action,user_name,active_role FROM nova_meta.audit_log "
        "WHERE event_type='metadata' AND starts_with(metadata_table,'config_ontology_') "
        "ORDER BY log_id")
    assert [(a["metadata_table"], a["action"], a["user_name"]) for a in audit] == [
        ("config_ontology_domains", "CREATE", "admin-1"),
        ("config_ontology_proposals", "CREATE", "alice"),
        ("config_ontology_proposals", "UPDATE", "carol"),
        ("config_ontology_concepts", "CREATE", "carol"),
        ("config_ontology_proposals", "UPDATE", "carol"),
    ]


async def test_the_submitter_withdraws_and_withdrawn_is_final(ontology):
    approved = await approved_proposal(ontology)
    async with ontology.transaction() as (repo, _):
        withdrawn = await repo.save("proposals",
                                    approved.model_copy(update={"status": "WITHDRAWN"}),
                                    expected_revision=2)
    with pytest.raises(MetadataConflict):
        async with ontology.transaction(name="carol", role="STEWARD") as (repo, _):
            await repo.save("concepts", concept())
    with pytest.raises(MetadataConflict):
        async with ontology.transaction() as (repo, _):
            await repo.save("proposals", withdrawn.model_copy(update={"status": "PENDING"}),
                            expected_revision=3)


async def test_releases_publish_through_the_current_alias(ontology):
    release = Release(id="ont_rel_0001", actor=actor(*ADMIN), sequence=1, members=[],
                      manifest_hash="h" * 64)
    with pytest.raises(MetadataPermissionDenied):
        async with ontology.transaction(name="carol", role="STEWARD") as (repo, _):
            await repo.save("releases", release)
    with pytest.raises(MetadataIntegrityViolation):
        async with ontology.transaction(name=ADMIN[0], role=ADMIN[1]) as (repo, _):
            await repo.save("releases", release.model_copy(update={"status": "PUBLISHED"}))
    async with ontology.transaction(name=ADMIN[0], role=ADMIN[1]) as (repo, _):
        prepared = await repo.save("releases", release)
    published = prepared.model_copy(update={"status": "PUBLISHED"})
    with pytest.raises(MetadataConflict):  # the alias moves first
        async with ontology.transaction(name=ADMIN[0], role=ADMIN[1]) as (repo, _):
            await repo.save("releases", published, expected_revision=1)
    alias = Alias(id="current", actor=actor(*ADMIN), release_id=prepared.id, reason="Publish")
    with pytest.raises(MetadataConflict):  # previous release must match the alias
        async with ontology.transaction(name=ADMIN[0], role=ADMIN[1]) as (repo, _):
            await repo.save("aliases", alias.model_copy(update={"previous_release_id": "x"}))
    with pytest.raises(MetadataPermissionDenied):
        async with ontology.transaction(name="carol", role="STEWARD") as (repo, _):
            await repo.save("aliases", alias)
    async with ontology.transaction(name=ADMIN[0], role=ADMIN[1]) as (repo, _):
        await repo.save("aliases", alias)
        done = await repo.save("releases", published, expected_revision=1)
    assert done.revision == 2
    with pytest.raises(MetadataConflict):  # a published release never changes
        async with ontology.transaction(name=ADMIN[0], role=ADMIN[1]) as (repo, _):
            await repo.save("releases", done.model_copy(update={"note": "edited"}),
                            expected_revision=2)
    second = Release(id="ont_rel_0002", actor=actor(*ADMIN), sequence=2, members=[],
                     manifest_hash="i" * 64, base_release_id=None)
    async with ontology.transaction(name=ADMIN[0], role=ADMIN[1]) as (repo, _):
        await repo.save("releases", second)
    with pytest.raises(MetadataConflict):  # prepared on an older base: prepare again
        async with ontology.transaction(name=ADMIN[0], role=ADMIN[1]) as (repo, _):
            await repo.save("aliases", alias.model_copy(update={
                "release_id": second.id, "previous_release_id": prepared.id}),
                expected_revision=1)


@pytest.mark.parametrize("workload", [role.removeprefix("nova_meta_") for role in WORKLOAD_ROLES])
async def test_workload_privileges(ontology, workload):
    async with ontology.transaction() as (repo, _):
        await repo.save("compilations", compilation())
    if workload not in READERS:
        with pytest.raises(MetadataPermissionDenied):
            async with ontology.transaction(workload, read_only=True) as (repo, _):
                await repo.latest("compilations", "cmp_1", Compilation)
        with pytest.raises(MetadataPermissionDenied):
            async with ontology.transaction(workload) as (repo, _):
                await repo.save("compilations", compilation("cmp_x"))
        return
    async with ontology.transaction(workload, read_only=True) as (repo, _):
        assert (await repo.latest("compilations", "cmp_1", Compilation)).view_id == "sv_orders"
    async with ontology.transaction(workload) as (repo, _):
        await repo.save("compilations", compilation("cmp_" + workload))
    if workload != "web":
        with pytest.raises(MetadataPermissionDenied):
            async with ontology.transaction(workload) as (repo, _):
                await repo.save("eval_results", EvalResult(
                    id="evr_x", actor=actor("alice", "ANALYST"), run_id="r", item="i",
                    outcome="PASS"))


async def test_an_unbound_or_mismatched_actor_is_refused(ontology):
    async with ontology.transaction() as (repo, _):
        repo.actor = SecurityContext.from_session(session("mallory"))
        with pytest.raises(MetadataPermissionDenied):
            await repo.current("compilations", Compilation)


async def test_maintenance_blocks_writes(ontology):
    async with ontology.transaction() as (repo, _):
        await repo.save("compilations", compilation())
    await ontology.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(MetadataReadOnlyMaintenance):
        async with ontology.transaction() as (repo, _):
            await repo.save("compilations", compilation(version=2), expected_revision=1)
    async with ontology.transaction(read_only=True) as (repo, _):
        assert (await repo.latest("compilations", "cmp_1", Compilation)).version == 1


async def test_legacy_import_runs_only_under_maintenance(ontology):
    body = compilation("cmp_legacy").model_dump(mode="json")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        async with ontology.importer() as conn:
            await conn.execute(
                "INSERT INTO nova_meta.config_ontology_compilations (id,revision,operation_id,"
                "actor,active_role,security_context_version,payload,created_at) "
                "VALUES ('cmp_legacy',1,'op',%s,'ANALYST',3,%s,clock_timestamp())",
                ("legacy", Jsonb(body)))
    await ontology.admin("UPDATE nova_meta.deployment_state SET maintenance=true")
    async with ontology.importer() as conn:
        # Imported rows keep their actor and time, and skip runtime lifecycle
        # rules: an imported proposal may already be applied.
        imported = proposal(status="APPLIED", reviews=[review("carol")]).model_dump(mode="json")
        await conn.execute(
            "INSERT INTO nova_meta.config_ontology_proposals (id,revision,operation_id,actor,"
            "active_role,security_context_version,payload,created_at) "
            "VALUES ('prp_1',1,'op-1','alice','ANALYST',3,%s,'2026-10-01T00:00:00Z')",
            (Jsonb(imported),))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # even an import never deletes
        async with ontology.importer() as conn:
            await conn.execute("DELETE FROM nova_meta.config_ontology_proposals")
    await ontology.admin("UPDATE nova_meta.deployment_state SET maintenance=false")
    row = (await ontology.admin(
        "SELECT actor,provenance,created_at FROM nova_meta.config_ontology_proposals"))[0]
    assert row["actor"] == "alice" and row["provenance"] == "imported"
    assert row["created_at"].isoformat().startswith("2026-10-01")
