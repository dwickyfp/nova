"""Supernova Business Ontology: stewardship, releases, drift and role-safe reads."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.ontology import access
from app.modules.ontology.bootstrap import BootstrapRequest, bootstrap, slug_for
from app.modules.ontology.contracts import (
    Actor,
    ConceptBody,
    ConceptChange,
    Domain,
    Relation,
    RelationChange,
    RelationType,
    Representation,
    RepresentationChange,
)
from app.modules.ontology.relations import neighbourhood
from app.modules.ontology.releases import AliasMove, ReleasePrepare, ReleaseService
from app.modules.ontology.representations import freshness, meaning_fingerprint, refresh_view
from app.modules.ontology.service import DomainCreate, DomainUpdate, OntologyService
from app.modules.ontology.stewardship import ProposalCreate, ReviewRequest, StewardshipService
from tests.unit._ontology import (
    ADMIN,
    ANALYST,
    FINANCE_STEWARD,
    OUTSIDER,
    SALES_STEWARD,
    FakeCatalog,
    FakeRepository,
    definition,
    user,
)


class World:
    def __init__(self, monkeypatch) -> None:
        from app.core import metadata_runtime
        from tests.unit.metadata_fakes import FakeMetadataDatabase

        self.database = FakeMetadataDatabase()
        monkeypatch.setattr(metadata_runtime, "_database", self.database)
        self.repository = FakeRepository()
        self.catalog = FakeCatalog()
        self.reads = OntologyService(self.repository, self.catalog)
        self.stewardship = StewardshipService(self.repository, self.catalog)
        self.releases = ReleaseService(self.repository, self.catalog)
        self.audits: list[dict] = []
        self._ops = 0

        async def record(**kwargs):
            self.audits.append(kwargs)
            return "audit"

        monkeypatch.setattr(access, "write_audit_log", record)

    def op(self) -> str:
        self._ops += 1
        return f"operation-{self._ops:04d}"

    async def domains(self):
        self.finance = (
            await self.reads.create_domain(
                DomainCreate(
                    name="Finance", steward_roles=["finance_steward"],
                    visibility_roles=["finance_analyst"],
                ),
                ADMIN,
            )
        )["id"]
        self.sales = (
            await self.reads.create_domain(
                DomainCreate(name="Sales", steward_roles=["sales_steward"]), ADMIN
            )
        )["id"]

    async def let_sales_steward_read_finance(self):
        finance = next(
            item for item in await self.reads.list_domains(ADMIN) if item["id"] == self.finance
        )
        await self.reads.update_domain(
            self.finance,
            DomainUpdate(
                steward_roles=["finance_steward"],
                visibility_roles=[*finance["visibility_roles"], "sales_steward"],
                expected_revision=finance["revision"],
            ),
            ADMIN,
        )

    def concept(self, domain_id: str, slug: str, **extra) -> ConceptChange:
        return ConceptChange(
            op="create",
            body=ConceptBody(
                domain_id=domain_id,
                kind=extra.pop("kind", "METRIC"),
                slug=slug,
                label=extra.pop("label", slug),
                definition=extra.pop("definition", f"The official meaning of {slug}."),
                **extra,
            ),
        )

    async def propose(self, changes, submitter=ANALYST, title="Change"):
        return await self.stewardship.create(
            ProposalCreate(
                title=title, rationale="Pilot scope", changes=changes, operation_id=self.op()
            ),
            submitter,
        )

    async def review(self, proposal, reviewer, decision="APPROVE"):
        return await self.stewardship.review(
            proposal.id,
            ReviewRequest(decision=decision, rationale="Checked", operation_id=self.op()),
            reviewer,
        )

    async def approved(self, changes, reviewers=(FINANCE_STEWARD,), submitter=ANALYST):
        proposal = await self.propose(changes, submitter)
        for reviewer in reviewers:
            proposal = await self.review(proposal, reviewer)
        assert proposal.status == "APPLIED", proposal.status
        return proposal

    async def release(self):
        prepared = await self.releases.prepare(ReleasePrepare(note="pilot"), ADMIN)
        assert prepared.status == "PREPARED", prepared.issues
        return await self.releases.publish(prepared.id, ADMIN)

    def concept_id(self, proposal, index=0):
        return proposal.changes[index].concept_id


@pytest.fixture
async def world(monkeypatch):
    world = World(monkeypatch)
    await world.domains()
    return world


# --- domains -----------------------------------------------------------------


async def test_only_accountadmin_creates_domains_and_refusals_are_audited(world):
    with pytest.raises(HTTPException) as denied:
        await world.reads.create_domain(
            DomainCreate(name="Ops", steward_roles=["ops_steward"]), FINANCE_STEWARD
        )
    assert denied.value.status_code == 403
    assert world.audits[-1]["status"] == "DENIED"
    assert world.audits[-1]["decision"] == "DENY"


async def test_creating_the_same_domain_twice_is_idempotent_but_a_clash_is_refused(world):
    again = await world.reads.create_domain(
        DomainCreate(name="Sales", steward_roles=["sales_steward"]), ADMIN
    )
    assert again["id"] == world.sales
    with pytest.raises(HTTPException) as clash:
        await world.reads.create_domain(
            DomainCreate(name="sales", steward_roles=["someone_else"]), ADMIN
        )
    assert clash.value.status_code == 409


async def test_domain_paths_and_parent_visibility(world):
    child = await world.reads.create_domain(
        DomainCreate(name="Treasury", parent_id=world.finance, steward_roles=["treasurer"]),
        ADMIN,
    )
    assert child["qualified_path"] == "Finance.Treasury"
    # The child has no visibility list of its own, but its parent hides it.
    outsider_paths = [item["qualified_path"] for item in await world.reads.list_domains(OUTSIDER)]
    assert outsider_paths == ["Sales"]
    analyst_paths = [item["qualified_path"] for item in await world.reads.list_domains(ANALYST)]
    assert analyst_paths == ["Finance", "Finance.Treasury", "Sales"]
    with pytest.raises(HTTPException):
        await world.reads.create_domain(
            DomainCreate(name="Orphan", parent_id="dom_missing", steward_roles=["x"]), ADMIN
        )


async def test_domain_update_is_revision_checked(world):
    domain = next(
        item for item in await world.reads.list_domains(ADMIN) if item["id"] == world.sales
    )
    updated = await world.reads.update_domain(
        world.sales,
        DomainUpdate(
            description="Pipeline", steward_roles=["sales_steward", "sales_ops"],
            expected_revision=domain["revision"],
        ),
        ADMIN,
    )
    assert updated["steward_roles"] == ["sales_ops", "sales_steward"]
    with pytest.raises(HTTPException) as stale:
        await world.reads.update_domain(
            world.sales,
            DomainUpdate(steward_roles=["sales_steward"], expected_revision=domain["revision"]),
            ADMIN,
        )
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as missing:
        await world.reads.update_domain(
            "dom_missing", DomainUpdate(steward_roles=["x"], expected_revision=1), ADMIN
        )
    assert missing.value.status_code == 404


# --- stewardship -------------------------------------------------------------


async def test_same_name_in_two_domains_is_two_concepts_but_not_within_one(world):
    await world.approved([world.concept(world.finance, "Revenue")])
    await world.approved(
        [world.concept(world.sales, "Revenue")], reviewers=(SALES_STEWARD,), submitter=ANALYST
    )
    working = await world.reads.list_concepts(ADMIN, scope="working")
    assert sorted(item["qualified_name"] for item in working["concepts"]) == [
        "Finance.Revenue", "Sales.Revenue",
    ]
    with pytest.raises(HTTPException) as duplicate:
        await world.propose([world.concept(world.finance, "revenue")])
    assert duplicate.value.status_code == 422
    assert "Revenue" in str(duplicate.value.detail) or "revenue" in str(duplicate.value.detail)


async def test_an_alias_cannot_name_two_concepts_in_one_domain(world):
    await world.approved([world.concept(world.finance, "NetRevenue", aliases=["net sales"])])
    with pytest.raises(HTTPException) as clash:
        await world.propose([world.concept(world.finance, "Turnover", aliases=["Net Sales"])])
    assert "names two concepts" in str(clash.value.detail)


async def test_the_submitter_cannot_approve_and_a_non_steward_cannot_review(world):
    proposal = await world.propose([world.concept(world.finance, "Margin")], FINANCE_STEWARD)
    with pytest.raises(HTTPException) as own:
        await world.review(proposal, FINANCE_STEWARD)
    assert own.value.status_code == 403
    with pytest.raises(HTTPException) as reader:
        await world.review(proposal, ANALYST)
    assert reader.value.status_code == 403
    assert [item["status"] for item in world.audits[-2:]] == ["DENIED", "DENIED"]
    with pytest.raises(HTTPException) as foreign:
        await world.review(proposal, SALES_STEWARD)
    assert foreign.value.status_code == 404  # Finance is invisible to Sales


async def test_a_steward_must_see_every_domain_a_proposal_touches(world):
    finance = world.concept_id(await world.approved([world.concept(world.finance, "Revenue")]))
    sales = world.concept_id(
        await world.approved([world.concept(world.sales, "Revenue")], reviewers=(SALES_STEWARD,))
    )
    proposal = await world.propose(
        [
            RelationChange(
                op="create", source_id=finance, type="EQUIVALENT_TO", target_id=sales,
                justification="Same recognition rule",
            )
        ]
    )
    with pytest.raises(HTTPException) as hidden:
        await world.review(proposal, SALES_STEWARD)
    assert hidden.value.status_code == 404
    assert (await world.stewardship.get(proposal.id, ADMIN)).status == "PENDING"


async def test_approval_is_not_official_until_a_release_is_published(world):
    proposal = await world.approved([world.concept(world.finance, "NetRevenue")])
    concept_id = world.concept_id(proposal)
    assert (await world.reads.list_concepts(ANALYST))["concepts"] == []
    with pytest.raises(HTTPException) as missing:
        await world.reads.get_concept(concept_id, ANALYST)
    assert missing.value.status_code == 404
    release = await world.release()
    current = await world.reads.get_concept(concept_id, ANALYST)
    assert current["release_id"] == release.id
    assert current["concept"]["qualified_name"] == "Finance.NetRevenue"


async def test_rename_keeps_the_id_and_old_releases_keep_the_old_version(world):
    created = await world.approved([world.concept(world.finance, "NetRevenue", label="Revenue")])
    concept_id = world.concept_id(created)
    first = await world.release()
    body = ConceptBody(
        domain_id=world.finance, kind="METRIC", slug="NetRevenue", label="Net revenue",
        definition="Revenue after refunds and discounts.", aliases=["net sales"],
    )
    await world.approved(
        [ConceptChange(op="update", concept_id=concept_id, base_revision=1, body=body)]
    )
    second = await world.release()
    detail = await world.reads.get_concept(concept_id, ANALYST)
    assert detail["concept"]["id"] == concept_id
    assert detail["concept"]["label"] == "Net revenue"
    assert [item["revision"] for item in detail["history"]] == [2, 1]
    await world.releases.rollback(first.id, AliasMove(reason="regression"), ADMIN)
    restored = await world.reads.get_concept(concept_id, ANALYST)
    assert restored["release_id"] == first.id
    assert restored["concept"]["label"] == "Revenue"
    assert second.id != first.id


async def test_a_concept_keeps_its_home_domain_and_stale_bases_are_refused(world):
    created = await world.approved([world.concept(world.finance, "Bookings")])
    concept_id = world.concept_id(created)
    moved = ConceptBody(
        domain_id=world.sales, kind="METRIC", slug="Bookings", label="Bookings",
        definition="Signed contract value.",
    )
    with pytest.raises(HTTPException) as move:
        await world.propose(
            [ConceptChange(op="update", concept_id=concept_id, base_revision=1, body=moved)]
        )
    assert "home domain" in str(move.value.detail)
    with pytest.raises(HTTPException) as stale:
        await world.propose(
            [ConceptChange(op="deprecate", concept_id=concept_id, base_revision=7)]
        )
    assert "changed since" in str(stale.value.detail)


async def test_is_a_cycles_are_blocked(world):
    created = await world.approved(
        [
            world.concept(world.finance, "Customer", kind="BUSINESS_ENTITY"),
            world.concept(world.finance, "Account", kind="BUSINESS_ENTITY"),
            RelationChange(
                op="create", source_id="@1", type="IS_A", target_id="@0",
                justification="Every account is a customer",
            ),
        ]
    )
    customer, account = world.concept_id(created, 0), world.concept_id(created, 1)
    with pytest.raises(HTTPException) as cycle:
        await world.propose(
            [
                RelationChange(
                    op="create", source_id=customer, type="IS_A", target_id=account,
                    justification="loop",
                )
            ]
        )
    assert "cycle" in str(cycle.value.detail)


async def test_relation_kinds_are_checked(world):
    with pytest.raises(HTTPException) as wrong:
        await world.propose(
            [
                world.concept(world.finance, "Revenue"),
                world.concept(world.finance, "Customer", kind="BUSINESS_ENTITY"),
                RelationChange(
                    op="create", source_id="@1", type="MEASURES", target_id="@0",
                    justification="backwards",
                ),
            ]
        )
    assert "cannot link" in str(wrong.value.detail)
    with pytest.raises(HTTPException) as unknown_ref:
        await world.propose(
            [
                RelationChange(
                    op="create", source_id="@3", type="RELATED_TO", target_id="con_x",
                    justification="dangling",
                )
            ]
        )
    assert unknown_ref.value.status_code == 422


async def test_cross_domain_equivalence_needs_both_stewards(world):
    await world.let_sales_steward_read_finance()
    finance = world.concept_id(await world.approved([world.concept(world.finance, "Revenue")]))
    sales = world.concept_id(
        await world.approved([world.concept(world.sales, "Revenue")], reviewers=(SALES_STEWARD,))
    )
    proposal = await world.propose(
        [
            RelationChange(
                op="create", source_id=finance, type="EQUIVALENT_TO", target_id=sales,
                justification="Both recognise revenue at invoice date",
            )
        ]
    )
    assert proposal.domain_ids == sorted([world.finance, world.sales])
    proposal = await world.review(proposal, FINANCE_STEWARD)
    assert proposal.status == "PENDING"
    proposal = await world.review(proposal, SALES_STEWARD)
    assert proposal.status == "APPLIED"
    relations = await world.repository.current("relations", Relation)
    assert [item.type for item in relations] == [RelationType.EQUIVALENT_TO]


async def test_any_steward_rejection_closes_the_proposal(world):
    proposal = await world.propose([world.concept(world.finance, "Churn")])
    proposal = await world.review(proposal, FINANCE_STEWARD, decision="REJECT")
    assert proposal.status == "REJECTED"
    assert await world.repository.current("concepts", Domain) == []
    with pytest.raises(HTTPException) as closed:
        await world.review(proposal, FINANCE_STEWARD)
    assert closed.value.status_code == 409


async def test_proposals_are_idempotent_by_operation_id(world):
    body = ProposalCreate(
        title="Revenue", rationale="Pilot", changes=[world.concept(world.finance, "Revenue")],
        operation_id="same-operation",
    )
    first = await world.stewardship.create(body, ANALYST)
    again = await world.stewardship.create(body, ANALYST)
    assert again.id == first.id and again.revision == first.revision
    with pytest.raises(HTTPException) as reused:
        await world.stewardship.create(body.model_copy(update={"title": "Other"}), ANALYST)
    assert reused.value.status_code == 409
    review = ReviewRequest(decision="APPROVE", rationale="ok", operation_id="review-once")
    applied = await world.stewardship.review(first.id, review, FINANCE_STEWARD)
    replay = await world.stewardship.review(first.id, review, FINANCE_STEWARD)
    assert replay.revision == applied.revision and replay.status == "APPLIED"


async def test_an_interrupted_application_resumes_without_duplicates(world):
    proposal = await world.propose(
        [
            world.concept(world.finance, "Revenue", unit="IDR"),
            world.concept(world.finance, "Refunds", unit="IDR"),
            RepresentationChange(
                op="create", concept_id="@0", view_id="sales_sv", metric="revenue"
            ),
        ]
    )
    world.repository.fail_after = world.repository.saves + 2  # proposal status + one concept
    with pytest.raises(RuntimeError):
        await world.review(proposal, FINANCE_STEWARD)
    assert (await world.stewardship.get(proposal.id, ADMIN)).status == "APPROVED"
    world.repository.fail_after = None
    with pytest.raises(HTTPException) as denied:
        await world.stewardship.apply(proposal.id, OUTSIDER)
    assert denied.value.status_code in {403, 404}
    applied = await world.stewardship.apply(proposal.id, FINANCE_STEWARD)
    assert applied.status == "APPLIED"
    assert (await world.stewardship.apply(proposal.id, FINANCE_STEWARD)).status == "APPLIED"
    concepts = world.repository.rows["concepts"]
    assert sorted(len(revisions) for revisions in concepts.values()) == [1, 1]
    assert len(world.repository.rows["representations"]) == 1


async def test_only_the_submitter_withdraws(world):
    proposal = await world.propose([world.concept(world.finance, "Churn")])
    with pytest.raises(HTTPException) as denied:
        await world.stewardship.withdraw(proposal.id, FINANCE_STEWARD)
    assert denied.value.status_code == 403
    withdrawn = await world.stewardship.withdraw(proposal.id, ANALYST)
    assert withdrawn.status == "WITHDRAWN"
    assert (await world.stewardship.withdraw(proposal.id, ANALYST)).status == "WITHDRAWN"
    with pytest.raises(HTTPException):
        await world.stewardship.apply(proposal.id, FINANCE_STEWARD)


async def test_proposals_are_listed_only_to_people_who_can_see_their_domains(world):
    proposal = await world.propose([world.concept(world.finance, "Churn")])
    assert [item.id for item in await world.stewardship.list(FINANCE_STEWARD)] == [proposal.id]
    assert await world.stewardship.list(OUTSIDER) == []
    assert await world.stewardship.list(FINANCE_STEWARD, "APPLIED") == []
    with pytest.raises(HTTPException) as hidden:
        await world.stewardship.get(proposal.id, OUTSIDER)
    assert hidden.value.status_code == 404


async def test_deprecating_a_concept_requires_its_links_to_go_first(world):
    created = await world.approved(
        [
            world.concept(world.finance, "Revenue", unit="IDR"),
            RepresentationChange(
                op="create", concept_id="@0", view_id="sales_sv", metric="revenue"
            ),
        ]
    )
    concept_id = world.concept_id(created)
    binding = (await world.repository.current("representations", Representation))[0]
    with pytest.raises(HTTPException) as blocked:
        await world.propose([ConceptChange(op="deprecate", concept_id=concept_id, base_revision=1)])
    assert "Revoke representation" in str(blocked.value.detail)
    await world.approved(
        [
            ConceptChange(op="deprecate", concept_id=concept_id, base_revision=1),
            RepresentationChange(
                op="revoke", representation_id=binding.id, base_revision=binding.revision
            ),
        ]
    )
    listing = await world.reads.list_concepts(ADMIN, scope="working", include_deprecated=True)
    assert [item["status"] for item in listing["concepts"]] == ["DEPRECATED"]


# --- representations and drift -------------------------------------------------


async def test_bindings_are_validated_against_the_published_metric(world):
    with pytest.raises(HTTPException) as unit:
        await world.propose(
            [
                world.concept(world.finance, "Revenue", unit="USD"),
                RepresentationChange(
                    op="create", concept_id="@0", view_id="sales_sv", metric="revenue"
                ),
            ]
        )
    assert "IDR" in str(unit.value.detail)
    for metric, view, expected in (
        ("missing_metric", "sales_sv", "no public metric"),
        ("revenue", "unknown_sv", "no published version"),
    ):
        with pytest.raises(HTTPException) as bad:
            await world.propose(
                [
                    world.concept(world.finance, f"Thing{len(expected)}"),
                    RepresentationChange(
                        op="create", concept_id="@0", view_id=view, metric=metric
                    ),
                ]
            )
        assert expected in str(bad.value.detail)
    with pytest.raises(HTTPException) as kind:
        await world.propose(
            [
                world.concept(world.finance, "Customer", kind="BUSINESS_ENTITY"),
                RepresentationChange(
                    op="create", concept_id="@0", view_id="sales_sv", metric="order_count"
                ),
            ]
        )
    assert "cannot bind" in str(kind.value.detail)


async def _bound_revenue(world):
    created = await world.approved(
        [
            world.concept(world.finance, "NetRevenue", unit="IDR"),
            RepresentationChange(
                op="create", concept_id="@0", view_id="sales_sv", metric="revenue"
            ),
        ]
    )
    return world.concept_id(created)


def _renamed_time_dimension(*, column: str = "sale_date") -> dict:
    """The sale date field renamed to ``sold_on``, reading ``column``."""
    value = definition()
    for field in value["datasets"][0]["fields"]:
        if field["name"] == "sale_date":
            field["name"], field["expression"] = "sold_on", column
    for metric in value["metrics"]:
        if metric.get("default_time_dimension") == "sale_date":
            metric["default_time_dimension"] = "sold_on"
    return value


async def test_a_renamed_dimension_keeps_the_binding_with_a_reason(world, monkeypatch):
    from app.modules.ontology import access

    audits = []

    async def audit(*args, **kwargs):
        audits.append((args, kwargs))

    monkeypatch.setattr(access, "audit", audit)
    await _bound_revenue(world)
    actor = Actor.from_user(ADMIN)
    world.catalog.publish("sales_sv", _renamed_time_dimension())
    assert await refresh_view("sales_sv", actor, world.catalog, world.repository) == {
        "repinned": 1, "stale": 0, "cosmetic": 1,
    }
    binding = (await world.repository.current("representations", Representation))[0]
    assert binding.status == "VALIDATED" and binding.semantic.version == 2
    assert "computes the same thing" in (binding.status_reason or "")
    # The new claim is pinned, so the next check sees the binding as current.
    assert (await freshness(binding, world.catalog))[0] == "current"

    # The same rename pointing at another column is a real change.
    world.catalog.publish("sales_sv", _renamed_time_dimension(column="city"))
    assert (await refresh_view("sales_sv", actor, world.catalog, world.repository))["stale"] == 1


def test_meaning_resolves_names_but_not_computation():
    base = meaning_fingerprint(definition(), "revenue")
    assert base is not None
    assert meaning_fingerprint(_renamed_time_dimension(), "revenue") == base
    changed = definition(revenue_expression="SUM(sales.orders)")
    assert meaning_fingerprint(changed, "revenue") != base
    assert meaning_fingerprint(definition(), "missing") is None


async def test_republishing_an_unchanged_metric_repins_and_a_changed_one_goes_stale(world):
    concept_id = await _bound_revenue(world)
    actor = Actor.from_user(ADMIN)
    world.catalog.publish("sales_sv", definition() | {"description": "Reworded view"})
    assert await refresh_view("sales_sv", actor, world.catalog, world.repository) == {
        "repinned": 1, "stale": 0, "cosmetic": 0,
    }
    binding = (await world.repository.current("representations", Representation))[0]
    assert binding.semantic.version == 2 and binding.status == "VALIDATED"

    world.catalog.publish("sales_sv", definition(revenue_expression="SUM(sales.gross)"))
    assert await refresh_view("sales_sv", actor, world.catalog, world.repository) == {
        "repinned": 0, "stale": 1, "cosmetic": 0,
    }
    working = await world.reads.representations(concept_id, ADMIN, scope="working")
    assert working["representations"][0]["status"] == "STALE"
    assert working["representations"][0]["usable"] is False
    assert "changed the definition" in working["representations"][0]["status_reason"]


async def test_a_release_leaves_out_bindings_whose_view_moved(world):
    await _bound_revenue(world)
    world.catalog.publish("sales_sv", definition(drop_revenue=True))
    prepared = await world.releases.prepare(ReleasePrepare(), ADMIN)
    assert prepared.status == "PREPARED"
    assert not [item for item in prepared.members if item.kind == "representation"]
    assert any("left out" in item for item in prepared.warnings)


async def test_publishing_fails_closed_when_a_pinned_binding_went_stale(world):
    await _bound_revenue(world)
    prepared = await world.releases.prepare(ReleasePrepare(), ADMIN)
    world.catalog.publish("sales_sv", definition(revenue_expression="SUM(sales.gross)"))
    with pytest.raises(HTTPException) as stale:
        await world.releases.publish(prepared.id, ADMIN)
    assert stale.value.status_code == 409
    assert await world.releases.alias() is None


async def test_bindings_are_shown_only_for_views_the_reader_can_see(world):
    concept_id = await _bound_revenue(world)
    await world.release()
    assert len((await world.reads.representations(concept_id, ANALYST))["representations"]) == 1
    world.catalog.hidden["sales_sv"] = {ANALYST["username"]}
    assert (await world.reads.representations(concept_id, ANALYST))["representations"] == []


# --- releases ------------------------------------------------------------------


async def test_release_publication_rollback_and_concurrency(world):
    await world.approved([world.concept(world.finance, "Revenue")])
    with pytest.raises(HTTPException) as denied:
        await world.releases.prepare(ReleasePrepare(), FINANCE_STEWARD)
    assert denied.value.status_code == 403
    first = await world.release()
    assert first.status == "PUBLISHED" and first.sequence == 1
    assert (await world.releases.publish(first.id, ADMIN)).id == first.id  # idempotent

    a = await world.releases.prepare(ReleasePrepare(note="a"), ADMIN)
    b = await world.releases.prepare(ReleasePrepare(note="b"), ADMIN)
    assert "Nothing changed since the current release" in a.warnings
    await world.releases.publish(b.id, ADMIN)
    with pytest.raises(HTTPException) as raced:
        await world.releases.publish(a.id, ADMIN)
    assert raced.value.status_code == 409

    alias = await world.releases.rollback(first.id, AliasMove(reason="bad release"), ADMIN)
    assert alias.release_id == first.id and alias.previous_release_id == b.id
    with pytest.raises(HTTPException) as again:
        await world.releases.publish(b.id, ADMIN)
    assert "roll back" in str(again.value.detail)
    with pytest.raises(HTTPException) as unpublished:
        await world.releases.rollback(a.id, AliasMove(reason="never published"), ADMIN)
    assert unpublished.value.status_code == 409
    assert [item.sequence for item in await world.releases.list()] == [3, 2, 1]


async def test_a_blocked_release_cannot_be_published(world):
    created = await world.approved([world.concept(world.finance, "Revenue")])
    concept_id = world.concept_id(created)
    # Corrupt the working state behind the stewardship checks.
    world.repository.rows["relations"] = {
        "rel_x": [
            Relation(
                id="rel_x", actor=Actor.from_user(ADMIN), source_id=concept_id,
                type="RELATED_TO", target_id="con_missing", justification="dangling",
                proposal_id="manual",
            )
        ]
    }
    blocked = await world.releases.prepare(ReleasePrepare(), ADMIN)
    assert blocked.status == "BLOCKED"
    assert "not an approved concept" in blocked.issues[0]
    with pytest.raises(HTTPException) as refused:
        await world.releases.publish(blocked.id, ADMIN)
    assert refused.value.status_code == 422


# --- role-safe reads ---------------------------------------------------------------


async def test_hidden_concepts_leak_neither_names_counts_nor_neighbours(world):
    await world.let_sales_steward_read_finance()
    created = await world.approved(
        [
            world.concept(world.finance, "CostOfCapital", aliases=["revenue hurdle"]),
        ]
    )
    hidden = world.concept_id(created)
    visible = world.concept_id(
        await world.approved(
            [
                world.concept(world.sales, "Revenue"),
                RelationChange(
                    op="create", source_id="@0", type="RELATED_TO", target_id=hidden,
                    justification="Both feed planning",
                ),
            ],
            reviewers=(SALES_STEWARD, FINANCE_STEWARD),
        )
    )
    await world.release()
    listing = await world.reads.list_concepts(OUTSIDER, query="revenue")
    assert [item["qualified_name"] for item in listing["concepts"]] == ["Sales.Revenue"]
    assert listing["truncated"] is False
    with pytest.raises(HTTPException) as missing:
        await world.reads.get_concept(hidden, OUTSIDER)
    assert missing.value.status_code == 404
    assert missing.value.detail == access.NOT_FOUND
    neighbours = await world.reads.related(visible, OUTSIDER, depth=2)
    assert neighbours["concepts"] == [] and neighbours["relations"] == []
    analyst_view = await world.reads.related(visible, ANALYST)
    assert [item["qualified_name"] for item in analyst_view["concepts"]] == [
        "Finance.CostOfCapital"
    ]
    assert analyst_view["relations"][0]["meaning"].endswith("not causation")


async def test_retired_domains_disappear_from_readers(world):
    await world.approved([world.concept(world.sales, "Pipeline")], reviewers=(SALES_STEWARD,))
    domain = next(
        item for item in await world.reads.list_domains(ADMIN) if item["id"] == world.sales
    )
    await world.reads.update_domain(
        world.sales,
        DomainUpdate(
            steward_roles=["sales_steward"], status="RETIRED", expected_revision=domain["revision"]
        ),
        ADMIN,
    )
    assert (await world.reads.list_concepts(OUTSIDER, scope="working"))["concepts"] == []
    with pytest.raises(HTTPException):
        await world.propose([world.concept(world.sales, "Quota")])


# --- bootstrap -------------------------------------------------------------------


async def test_bootstrap_drafts_concepts_and_bindings_from_a_published_view(world):
    proposal = await bootstrap(
        BootstrapRequest(view_id="sales_sv", domain_id=world.sales, operation_id=world.op()),
        SALES_STEWARD,
        world.stewardship,
    )
    assert proposal.source == "BOOTSTRAP" and proposal.status == "PENDING"
    kinds = [change.target for change in proposal.changes]
    assert kinds == ["concept", "representation", "concept", "representation"]
    revenue = proposal.changes[2]
    assert revenue.body.slug == "Revenue"
    assert revenue.body.aliases == ["net sales"]
    assert revenue.body.unit == "IDR"
    assert proposal.changes[3].concept_id == revenue.concept_id
    with pytest.raises(HTTPException) as own:
        await world.review(proposal, SALES_STEWARD)
    assert own.value.status_code == 403
    sales_lead = user("lee", "sales_steward")
    assert (await world.review(proposal, sales_lead)).status == "APPLIED"
    with pytest.raises(HTTPException) as nothing:
        await bootstrap(
            BootstrapRequest(view_id="sales_sv", domain_id=world.sales, operation_id=world.op()),
            SALES_STEWARD,
            world.stewardship,
        )
    assert nothing.value.status_code == 409


async def test_bootstrap_needs_a_visible_published_view_and_domain(world):
    for request, reader, status in (
        (BootstrapRequest(view_id="sales_sv", domain_id=world.finance, operation_id="op-hidden-1"),
         OUTSIDER, 404),
        (BootstrapRequest(view_id="nope_sv", domain_id=world.sales, operation_id="op-missing-1"),
         SALES_STEWARD, 404),
    ):
        with pytest.raises(HTTPException) as refused:
            await bootstrap(request, reader, world.stewardship)
        assert refused.value.status_code == status


def test_metric_names_become_concept_slugs():
    assert slug_for("net_revenue") == "NetRevenue"
    assert slug_for("90d churn") == "M90dChurn"
    assert slug_for("___") == "Metric"


# --- contracts and graph helpers -----------------------------------------------------


def test_change_contracts_reject_malformed_requests():
    with pytest.raises(ValidationError):
        ConceptChange(op="create")
    with pytest.raises(ValidationError):
        ConceptChange(op="update", concept_id="con_1", base_revision=1)
    with pytest.raises(ValidationError):
        RelationChange(op="create", source_id="a", type="IS_A", target_id="a", justification="x")
    with pytest.raises(ValidationError):
        RelationChange(op="create", source_id="a", type="IS_A", target_id="b")
    with pytest.raises(ValidationError):
        RepresentationChange(op="revoke", representation_id="rep_1")
    with pytest.raises(ValidationError):
        Domain(id="d", actor=Actor.from_user(ADMIN), name="Ok", steward_roles=["  "])
    with pytest.raises(ValidationError):
        Domain(id="d", actor=Actor.from_user(ADMIN), name="bad name", steward_roles=["x"])
    with pytest.raises(ValidationError):
        ConceptBody(domain_id="d", kind="METRIC", slug="1bad", label="x", definition="x")


def test_traversal_is_bounded():
    actor = Actor.from_user(ADMIN)
    chain = [
        Relation(
            id=f"rel_{index}", actor=actor, source_id=f"c{index}", type="RELATED_TO",
            target_id=f"c{index + 1}", justification="chain", proposal_id="p",
        )
        for index in range(5)
    ]
    visible = {f"c{index}" for index in range(6)}
    assert neighbourhood("c0", chain, visible, depth=2)[0] == ["c1", "c2"]
    with pytest.raises(ValueError):
        neighbourhood("c0", chain, visible, depth=3)


async def test_published_release_snapshots_are_cached_by_release(world, monkeypatch):
    from app.modules.ontology import service as service_module

    await world.approved([world.concept(world.finance, "Revenue")])
    first = await world.release()
    reads = []
    original = world.repository.current

    async def counting(kind, model):
        reads.append(kind)
        return await original(kind, model)

    monkeypatch.setattr(world.repository, "current", counting)
    await world.reads.snapshot("current")
    await world.reads.snapshot("current", release_id=first.id)
    assert reads.count("concepts") == 1
    monkeypatch.setattr(service_module, "RELEASE_CACHE_SIZE", 1)
    await world.approved([world.concept(world.finance, "Margin")])
    second = await world.release()
    assert len((await world.reads.snapshot("current")).concepts) == 2
    assert list(world.reads._released) == [second.id]
    with pytest.raises(HTTPException):
        await world.reads.snapshot("current", release_id="ont_rel_9999")
