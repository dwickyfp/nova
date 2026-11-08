"""Proposals Nova drafts from the derived layer, decided by stewards."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.modules.ontology import access
from app.modules.ontology.contracts import (
    Actor,
    ConceptBody,
    ConceptChange,
    RelationChange,
    RepresentationChange,
)
from app.modules.ontology.derived import compile_view
from app.modules.ontology.promotion import PromotionRequest, propose_from_derived
from app.modules.ontology.service import DomainCreate, OntologyService
from app.modules.ontology.stewardship import ProposalCreate, ReviewRequest, StewardshipService
from tests.unit._ontology import ADMIN, FakeRepository, user
from tests.unit.test_ontology_derived import lab_catalog

SALES = user("sam", "sales_steward")
STOCK = user("sita", "stock_steward")
OPS = user("oki", "ops_steward")
ANALYST = user("ana", "retail_analyst")


class Lab:
    def __init__(self, monkeypatch) -> None:
        self.repository = FakeRepository()
        self.catalog = lab_catalog()
        self.reads = OntologyService(self.repository, self.catalog)
        self.stewardship = StewardshipService(self.repository, self.catalog)
        self.ops = 0

        async def record(**_):
            return "audit"

        monkeypatch.setattr(access, "write_audit_log", record)

    def op(self) -> str:
        self.ops += 1
        return f"operation-{self.ops:04d}"

    async def setup(self) -> None:
        self.domains = {}
        for name, role in (("Penjualan", "sales_steward"), ("Persediaan", "stock_steward"),
                           ("Operasional", "ops_steward")):
            created = await self.reads.create_domain(
                DomainCreate(name=name, steward_roles=[role]), ADMIN
            )
            self.domains[name] = created["id"]
        # One approved, bound concept per view: that is what ties a view to a domain.
        for domain, steward, view, metric, slug in (
            ("Penjualan", SALES, "retail_sales", "net_revenue", "NetRevenue"),
            ("Persediaan", STOCK, "outlet_stock", "stockout_days", "StockoutDays"),
            ("Operasional", OPS, "delivery_routes", "actual_distance_km", "DistanceTravelled"),
        ):
            await self.approve(await self.stewardship.create(ProposalCreate(
                title="Seed", rationale="Pilot", operation_id=self.op(), changes=[
                    ConceptChange(op="create", body=ConceptBody(
                        domain_id=self.domains[domain], kind="METRIC", slug=slug,
                        label=slug, definition=f"The official {slug}.",
                    )),
                    RepresentationChange(op="create", concept_id="@0", view_id=view,
                                         metric=metric),
                ],
            ), ANALYST), steward)
        actor = Actor.from_user(ADMIN)
        for view_id in sorted(self.catalog.views):
            await compile_view(await self.catalog.published(view_id), actor, self.repository)

    async def approve(self, proposal, steward):
        return await self.stewardship.review(
            proposal.id,
            ReviewRequest(decision="APPROVE", rationale="Checked", operation_id=self.op()),
            steward,
        )

    async def propose(self, shared: str | None = None, caller=ANALYST) -> dict:
        return await propose_from_derived(
            PromotionRequest(operation_id=self.op(), shared_domain_id=shared),
            caller, self.stewardship,
        )


@pytest.fixture
async def lab(monkeypatch):
    lab = Lab(monkeypatch)
    await lab.setup()
    return lab


def labels(proposal: dict) -> set[str]:
    return {
        change["body"]["label"] for change in proposal["changes"]
        if change["target"] == "concept"
    }


async def test_one_engine_proposal_per_domain_with_evidence(lab):
    result = await lab.propose()
    proposals = {p["title"]: p for p in result["proposals"]}
    assert set(proposals) == {
        "Derived meaning for Penjualan", "Derived meaning for Persediaan",
        "Derived meaning for Operasional",
    }
    sales = proposals["Derived meaning for Penjualan"]
    assert sales["source"] == "ENGINE" and sales["submitter"] == "ana"
    assert {"Units sold", "Return rate", "Sales order"} <= labels(sales)
    assert "Net revenue" not in labels(sales)  # already official
    relations = [c for c in sales["changes"] if c["target"] == "relation"]
    assert all(c["justification"].startswith("Derived by Nova (") for c in relations)
    official_revenue = next(
        binding.concept_id for binding in lab.repository.rows["representations"].values()
        for binding in binding[-1:] if binding.metric == "net_revenue"
    )
    # sales_before_returns, created here, derives the already official net revenue.
    assert any(
        c["type"] == "DERIVES" and c["target_id"] == official_revenue for c in relations
    )
    assert any(c["type"] == "MEASURES" for c in relations)

    shared = {item["label"] for item in result["skipped"] if "Several domains" in item["reason"]}
    assert {"Outlet", "Product", "Employee"} <= shared


async def test_rerunning_never_duplicates_open_work(lab):
    first = await lab.propose()
    assert first["proposals"]
    again = await lab.propose()
    assert again["proposals"] == []


async def test_the_caller_cannot_approve_and_stewards_apply_by_domain(lab):
    result = await lab.propose()
    sales = next(p for p in result["proposals"] if p["title"].endswith("Penjualan"))
    with pytest.raises(HTTPException) as refused:
        await lab.approve(type("P", (), {"id": sales["id"]}), ANALYST)
    assert refused.value.status_code in {403, 409}
    applied = await lab.approve(type("P", (), {"id": sales["id"]}), SALES)
    assert applied.status == "APPLIED"


async def test_shared_entities_go_home_and_link_once_their_ends_exist(lab):
    first = await lab.propose()
    sales = next(p for p in first["proposals"] if p["title"].endswith("Penjualan"))
    await lab.approve(type("P", (), {"id": sales["id"]}), SALES)

    shared = await lab.propose(shared=lab.domains["Penjualan"])
    home = next(p for p in shared["proposals"] if p["title"].endswith("Penjualan"))
    assert {"Outlet", "Product", "Employee"} <= labels(home)
    links = [c for c in home["changes"] if c["target"] == "relation"]
    # Sales order (approved above) now refers to the new Employee as its salesman.
    assert any("role salesman" in c["justification"] for c in links)


async def test_an_unknown_shared_domain_is_refused(lab):
    with pytest.raises(HTTPException) as refused:
        await lab.propose(shared="dom_missing")
    assert refused.value.status_code == 404


def test_promotion_changes_are_valid_contracts():
    RelationChange(op="create", source_id="a", type="DERIVES", target_id="b",
                   justification="Derived by Nova (95%) from retail_sales v1 metric:x")
