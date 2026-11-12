"""Meaning checks compare what stewards approved with what the formula computes."""

from __future__ import annotations

import copy

import pytest

from app.modules.ontology import access
from app.modules.ontology.contracts import (
    Actor,
    ConceptBody,
    ConceptChange,
    RepresentationChange,
)
from app.modules.ontology.derived import compile_view
from app.modules.ontology.lint import aggregate_columns, lint, lint_report
from app.modules.ontology.service import DomainCreate, OntologyService
from app.modules.ontology.stewardship import ProposalCreate, ReviewRequest, StewardshipService
from tests.unit._ontology import ADMIN, FakeRepository, user
from tests.unit.test_ontology_derived import lab_catalog

pytestmark = pytest.mark.usefixtures("fake_metadata_db")

STEWARD = user("sam", "retail_steward")
ANALYST = user("ana", "retail_analyst")
OUTSIDER = user("oz", "nobody")

SALES = [
    (
        "NetRevenue",
        "Net revenue",
        "Omzet bersih: nilai order setelah diskon, dikurangi nilai retur.",
        ["omzet", "omzet bersih"],
        "net_revenue",
    ),
    (
        "SalesBeforeReturns",
        "Sales before returns",
        "Nilai order sebelum retur.",
        ["nilai order"],
        "sales_before_returns",
    ),
    (
        "ReturnedValue",
        "Returned value",
        "Nilai unit yang diretur.",
        ["nilai retur"],
        "returned_value",
    ),
    (
        "GrossMargin",
        "Gross margin",
        "Omzet bersih dikurangi harga pokok.",
        ["margin"],
        "gross_margin",
    ),
    ("UnitsSold", "Units sold", "Unit terjual ke outlet.", ["unit terjual"], "units_sold"),
    ("Churn", "Churn", "Outlet yang berhenti memesan.", [], None),
]


OLD_GROSS_MARGIN = "SUM(sales_orders.net_amount) - SUM(sales_orders.cost_amount)"


def with_gross_margin(definition: dict, expression: str) -> dict:
    """The retail view with another gross margin formula, as it may have been published."""
    changed = copy.deepcopy(definition)
    for metric in changed["metrics"]:
        if metric["name"] == "gross_margin":
            metric["expression"] = expression
            metric["description"] = "Omzet bersih dikurangi harga pokok."
    return changed


class Lab:
    def __init__(self, monkeypatch, *, gross_margin: str | None = OLD_GROSS_MARGIN) -> None:
        self.repository = FakeRepository()
        self.catalog = lab_catalog()
        if gross_margin:
            # The margin as it was first published: before returns, so the checks have a
            # real mistake to find.
            self.catalog.publish(
                "retail_sales",
                with_gross_margin(
                    self.catalog.views["retail_sales"].definition,
                    gross_margin,
                ),
            )
        self.reads = OntologyService(self.repository, self.catalog)
        self.stewardship = StewardshipService(self.repository, self.catalog)
        self.ops = 0

        async def record(**_):
            return "audit"

        monkeypatch.setattr(access, "write_audit_log", record)

    def op(self) -> str:
        self.ops += 1
        return f"operation-{self.ops:04d}"

    async def domain(self, name: str, readers: list[str] | None = None) -> str:
        created = await self.reads.create_domain(
            DomainCreate(
                name=name, steward_roles=["retail_steward"], visibility_roles=readers or []
            ),
            ADMIN,
        )
        return created["id"]

    async def approve(self, domain_id: str, rows) -> None:
        changes: list = []
        for slug, label, definition, aliases, metric in rows:
            changes.append(
                ConceptChange(
                    op="create",
                    body=ConceptBody(
                        domain_id=domain_id,
                        kind="METRIC",
                        slug=slug,
                        label=label,
                        definition=definition,
                        aliases=aliases,
                    ),
                )
            )
            if metric:
                view, name = metric
                changes.append(
                    RepresentationChange(
                        op="create",
                        concept_id=f"@{len(changes) - 1}",
                        view_id=view,
                        metric=name,
                    )
                )
        proposal = await self.stewardship.create(
            ProposalCreate(
                title="Seed",
                rationale="Pilot",
                operation_id=self.op(),
                changes=changes,
            ),
            ADMIN,
        )
        await self.stewardship.review(
            proposal.id,
            ReviewRequest(
                decision="APPROVE",
                rationale="Checked",
                operation_id=self.op(),
            ),
            STEWARD,
        )

    async def setup(self) -> None:
        self.sales = await self.domain("Penjualan")
        self.stock = await self.domain("Persediaan")
        self.ops_domain = await self.domain("Operasional", readers=["retail_steward"])
        await self.approve(
            self.sales,
            [
                (slug, label, text, aliases, ("retail_sales", metric) if metric else None)
                for slug, label, text, aliases, metric in SALES
            ],
        )
        await self.approve(
            self.stock,
            [
                (
                    "ReceivedUnits",
                    "Received units",
                    "Unit yang diterima outlet.",
                    [],
                    ("outlet_stock", "received_units"),
                )
            ],
        )
        await self.approve(
            self.ops_domain,
            [
                (
                    "ReceivedUnits",
                    "Received units",
                    "Unit yang diterima outlet dari driver.",
                    [],
                    ("delivery_routes", "received_units"),
                )
            ],
        )
        actor = Actor.from_user(ADMIN)
        for view_id in sorted(self.catalog.views):
            await compile_view(await self.catalog.published(view_id), actor, self.repository)


@pytest.fixture
async def lab(monkeypatch):
    lab = Lab(monkeypatch)
    await lab.setup()
    return lab


def by_rule(findings, rule: str) -> dict[str, list]:
    found: dict[str, list] = {}
    for item in findings:
        if item.rule == rule:
            found.setdefault(item.concept, []).append(item)
    return found


async def test_a_definition_that_names_net_revenue_over_sales_before_returns(lab):
    findings = await lint(ADMIN, lab.catalog, lab.repository)
    mismatch = by_rule(findings, "definition_formula")
    assert set(mismatch) == {"Penjualan.GrossMargin"}
    message = mismatch["Penjualan.GrossMargin"][0].message
    assert "names Net revenue" in message
    assert "leaves out Returned value" in message
    assert "built from Sales before returns" in message
    assert findings[0].severity == "error"


async def test_a_sum_that_ignores_returns_another_metric_takes_out(lab):
    ignored = by_rule(await lint(ADMIN, lab.catalog, lab.repository), "adjustment_ignored")
    assert set(ignored) == {"Penjualan.GrossMargin", "Penjualan.UnitsSold"}
    assert "returned_qty" in ignored["Penjualan.UnitsSold"][0].message
    # Sales before returns says so in its words; net revenue subtracts them.


async def test_shared_names_and_unbound_metrics(lab):
    findings = await lint(ADMIN, lab.catalog, lab.repository)
    ambiguous = by_rule(findings, "ambiguous_name")
    assert len(ambiguous) == 1
    (only,) = next(iter(ambiguous.values()))
    assert "Operasional.ReceivedUnits and Persediaan.ReceivedUnits" in only.message
    assert set(by_rule(findings, "unbound_metric")) == {"Penjualan.Churn"}


async def test_findings_only_cover_what_the_caller_sees(lab):
    lab.catalog.hidden["retail_sales"] = {ANALYST["username"], OUTSIDER["username"]}
    findings = await lint(ANALYST, lab.catalog, lab.repository)
    assert not by_rule(findings, "definition_formula")
    assert not by_rule(findings, "adjustment_ignored")
    # Operasional is only visible to its stewards, so its name is not shared here.
    assert not by_rule(findings, "ambiguous_name")
    report = await lint_report(OUTSIDER, lab.catalog, lab.repository)
    assert {item["concept"] for item in report["findings"]} <= {"Penjualan.Churn"}
    assert report["counts"]["error"] == 0


def test_aggregate_columns_are_grouped_per_aggregate():
    import json

    from app.modules.agents.semantic.ir import SemanticModelIR
    from app.modules.agents.semantic.ossie import parse_ossie
    from tests.benchmark.retail_field.views import VIEWS

    model = SemanticModelIR.from_ossie(parse_ossie(json.dumps(VIEWS["retail_sales"])).model)
    metric = model.metric("net_revenue")
    (group,) = aggregate_columns(metric.expression, metric.base_dataset, model)
    assert ("sales_orders", "returned_qty") in group and ("sales_orders", "net_amount") in group
    assert aggregate_columns("not sql (", "sales_orders", model) == []


async def test_release_preparation_carries_the_checks_as_warnings(lab):
    from app.modules.ontology.releases import ReleasePrepare

    release = await lab.reads.releases.prepare(ReleasePrepare(note="Pilot"), ADMIN)
    assert release.status == "PREPARED"
    assert any(
        warning.startswith("Meaning check (definition_formula) Penjualan.GrossMargin")
        for warning in release.warnings
    )


def test_lint_route(lab):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_user
    from app.modules.ontology import router as router_module

    app = FastAPI()
    app.include_router(router_module.router, prefix="/ontology")
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    original = router_module.ontology_service
    router_module.ontology_service = lab.reads
    try:
        body = TestClient(app).get("/ontology/lint").json()
    finally:
        router_module.ontology_service = original
    assert body["counts"]["error"] == 1
    assert {item["rule"] for item in body["findings"]} == {
        "definition_formula",
        "adjustment_ignored",
        "ambiguous_name",
        "unbound_metric",
    }


async def test_the_corrected_margin_is_built_from_net_revenue(monkeypatch):
    lab = Lab(monkeypatch, gross_margin=None)  # the formula in the lab view today
    await lab.setup()
    findings = await lint(ADMIN, lab.catalog, lab.repository)
    assert "Penjualan.GrossMargin" not in by_rule(findings, "definition_formula")
    assert "Penjualan.GrossMargin" not in by_rule(findings, "adjustment_ignored")
    derives = {
        (item.source_key, item.target_key)
        for records in lab.repository.rows["derived"].values()
        for item in records[-1:]
        if item.status == "ACTIVE" and item.relation_type and item.relation_type.value == "DERIVES"
    }
    assert ("metric:retail_sales.net_revenue", "metric:retail_sales.gross_margin") in derives
