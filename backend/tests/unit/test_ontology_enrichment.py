"""Everyday names from relation roles and a validated model, approved by stewards."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.ontology.contracts import ConceptBody, ConceptChange
from app.modules.ontology.enrichment import EnrichRequest, enrich, validate
from app.modules.ontology.releases import ReleasePrepare
from app.modules.ontology.resolver import ConceptResolver
from app.modules.ontology.stewardship import ProposalCreate, ReviewRequest
from tests.unit._ontology import ADMIN
from tests.unit.test_ontology_lint import STEWARD, Lab

pytestmark = pytest.mark.usefixtures("fake_metadata_db")


class FakeModel:
    """Stands in for the provider: returns a scripted answer and records what it saw."""

    def __init__(self, answer: dict | str, *, fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail
        self.sent: list[dict] = []

    async def resolve(self):
        if self.fail:
            from app.modules.assistant.provider import AssistantProviderError

            raise AssistantProviderError("no provider")
        return SimpleNamespace(
            model="fake", capabilities=SimpleNamespace(supports_json_schema=True)
        )

    async def complete(self, **kwargs):
        self.sent.append(kwargs)
        content = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return {"content": content}


ANSWER = {
    "concepts": [
        {
            "name": "Penjualan.NetRevenue",
            "definition": "",
            "aliases": ["penjualan bersih", "nilai order", "total", "net sales"],
            "ambiguous": [{"name": "omzet", "reason": "kadang dipakai untuk penjualan kotor"}],
        },
        {
            "name": "Penjualan.Employee",
            "definition": "Pegawai lapangan yang menjual atau mengantar.",
            "aliases": ["sopir", "pegawai", "karyawan"],
            "ambiguous": [],
        },
        {"name": "Penjualan.UnitsSold", "definition": "", "aliases": ["pegawai"], "ambiguous": []},
        {
            "name": "Penjualan.Churn",
            "definition": "",
            "aliases": ["outlet berhenti karena harga naik", "123"],
            "ambiguous": [],
        },
        {"name": "Gudang.Hidden", "definition": "", "aliases": ["rahasia"], "ambiguous": []},
    ]
}


@pytest.fixture
async def lab(monkeypatch):
    lab = Lab(monkeypatch)
    await lab.setup()
    proposal = await lab.stewardship.create(
        ProposalCreate(
            title="Entity",
            rationale="Pilot",
            operation_id=lab.op(),
            changes=[
                ConceptChange(
                    op="create",
                    body=ConceptBody(
                        domain_id=lab.sales,
                        kind="BUSINESS_ENTITY",
                        slug="Employee",
                        label="Employee",
                        definition=(
                            "Derived by Nova from retail_sales; a steward should confirm it."
                        ),
                    ),
                ),
            ],
        ),
        ADMIN,
    )
    await lab.stewardship.review(
        proposal.id,
        ReviewRequest(
            decision="APPROVE",
            rationale="Checked",
            operation_id=lab.op(),
        ),
        STEWARD,
    )
    return lab


async def test_roles_and_validated_words_become_one_proposal_per_domain(lab):
    model = FakeModel(ANSWER)
    result = await enrich(
        EnrichRequest(operation_id="op-enrich-1", domain_id=lab.sales),
        ADMIN,
        lab.stewardship,
        client=model,
    )
    (proposal,) = result["proposals"]
    assert proposal["source"] == "ENGINE"
    assert proposal["title"] == "Names and definitions for Penjualan"
    bodies = {change["body"]["slug"]: change["body"] for change in proposal["changes"]}
    assert all(change["op"] == "update" for change in proposal["changes"])
    assert {"penjualan bersih", "net sales"} <= set(bodies["NetRevenue"]["aliases"])
    assert "nilai order" not in bodies["NetRevenue"]["aliases"]  # SalesBeforeReturns has it
    assert {"driver", "salesman", "sopir", "karyawan"} <= set(bodies["Employee"]["aliases"])
    assert bodies["Employee"]["definition"].startswith("Pegawai lapangan")
    assert "pegawai" not in bodies["Employee"]["aliases"]  # given to two concepts
    assert "relation role" in proposal["rationale"]
    assert "'omzet' may also mean" in proposal["rationale"]

    reasons = {(item["value"], item["reason"]) for item in result["rejected"]}
    assert ("total", "Too generic to mean one concept") in reasons
    assert ("outlet berhenti karena harga naik", "Longer than an everyday name") in reasons
    assert ("123", "Not a name") in reasons
    assert ("Gudang.Hidden", "Not a concept that was sent") in {
        (item["concept"], item["reason"]) for item in result["rejected"]
    }
    assert any(r[0] == "nilai order" and r[1].startswith("Already names") for r in reasons)

    # Only metadata goes to the model: names, definitions and formulas, never rows.
    payload = json.loads(model.sent[0]["messages"][1]["content"])
    revenue = next(row for row in payload["concepts"] if row["name"] == "Penjualan.NetRevenue")
    assert "returned_qty" in revenue["formula"]
    assert model.sent[0]["response_format"]["json_schema"]["strict"] is True


async def test_after_approval_and_release_everyday_words_resolve(lab):
    result = await enrich(
        EnrichRequest(operation_id="op-enrich-2", domain_id=lab.sales),
        ADMIN,
        lab.stewardship,
        client=FakeModel(ANSWER),
    )
    await lab.stewardship.review(
        result["proposals"][0]["id"],
        ReviewRequest(
            decision="APPROVE",
            rationale="Words checked",
            operation_id=lab.op(),
        ),
        STEWARD,
    )
    prepared = await lab.reads.releases.prepare(ReleasePrepare(note="names"), ADMIN)
    await lab.reads.releases.publish(prepared.id, ADMIN)
    resolution = await ConceptResolver(lab.reads).resolve(
        "berapa penjualan bersih per sopir bulan ini", ADMIN
    )
    assert {c["qualified_name"] for c in resolution.concepts} == {
        "Penjualan.NetRevenue",
        "Penjualan.Employee",
    }


async def test_concepts_with_open_proposals_are_left_alone(lab):
    await enrich(
        EnrichRequest(operation_id="op-enrich-3", domain_id=lab.sales),
        ADMIN,
        lab.stewardship,
        client=FakeModel(ANSWER),
    )
    model = FakeModel(ANSWER)
    again = await enrich(
        EnrichRequest(operation_id="op-enrich-4", domain_id=lab.sales),
        ADMIN,
        lab.stewardship,
        client=model,
    )
    sent = {row["name"] for row in json.loads(model.sent[0]["messages"][1]["content"])["concepts"]}
    assert not {"Penjualan.NetRevenue", "Penjualan.Employee"} & sent
    # 'penjualan bersih' is promised to NetRevenue by the open proposal.
    for proposal in again["proposals"]:
        for change in proposal["changes"]:
            assert "penjualan bersih" not in change["body"]["aliases"]


async def test_no_provider_and_unknown_domains_are_refused(lab):
    with pytest.raises(HTTPException) as missing:
        await enrich(
            EnrichRequest(operation_id="op-enrich-5", domain_id=lab.sales),
            ADMIN,
            lab.stewardship,
            client=FakeModel(ANSWER, fail=True),
        )
    assert missing.value.status_code == 503
    with pytest.raises(HTTPException) as hidden:
        await enrich(
            EnrichRequest(operation_id="op-enrich-6", domain_id="dom_nope"),
            ADMIN,
            lab.stewardship,
            client=FakeModel(ANSWER),
        )
    assert hidden.value.status_code == 404


async def test_unreadable_model_output_proposes_only_what_is_proven(lab):
    result = await enrich(
        EnrichRequest(operation_id="op-enrich-7", domain_id=lab.sales),
        ADMIN,
        lab.stewardship,
        client=FakeModel("not json"),
    )
    (proposal,) = result["proposals"]
    (change,) = proposal["changes"]
    assert set(change["body"]["aliases"]) == {"driver", "salesman"}


def test_validator_keeps_existing_definitions_and_rejects_causes():
    concept = SimpleNamespace(
        id="c1",
        slug="Margin",
        label="Margin",
        aliases=[],
        definition="Omzet dikurangi harga pokok.",
    )
    accepted, rejected = validate(
        {
            "concepts": [
                {
                    "name": "D.Margin",
                    "aliases": ["laba"],
                    "ambiguous": [],
                    "definition": "Margin naik karena harga pokok turun.",
                }
            ]
        },
        {"D.Margin": concept},
        {"c1": "D.Margin"},
        {},
    )
    assert [alias for alias, _ in accepted["c1"].aliases] == ["laba"]
    assert accepted["c1"].definition is None
    assert rejected[0].reason == "The concept already has a definition"
    assert validate("nonsense", {}, {}, {})[1][0].reason == "The model returned no concept list"


class FlakyModel(FakeModel):
    """Answers prose first, then the JSON it was asked for."""

    async def complete(self, **kwargs):
        self.sent.append(kwargs)
        if len(self.sent) == 1:
            return {"content": "Here are some names you could use."}
        return {"content": json.dumps(self.answer)}


async def test_unreadable_output_is_repaired_once(lab):
    model = FlakyModel(ANSWER)
    result = await enrich(
        EnrichRequest(operation_id="op-enrich-8", domain_id=lab.sales),
        ADMIN,
        lab.stewardship,
        client=model,
    )
    assert len(model.sent) == 2
    assert model.sent[1]["messages"][-1]["role"] == "user"
    bodies = {c["body"]["slug"]: c["body"] for c in result["proposals"][0]["changes"]}
    assert "penjualan bersih" in bodies["NetRevenue"]["aliases"]


def test_hyphenated_words_count_as_one_word():
    concept = SimpleNamespace(id="c1", slug="Gap", label="Gap", aliases=[], definition="x" * 30)
    accepted, rejected = validate(
        {
            "concepts": [
                {
                    "name": "D.Gap",
                    "aliases": ["rata-rata jarak check-in"],
                    "definition": "",
                    "ambiguous": [],
                }
            ]
        },
        {"D.Gap": concept},
        {"c1": "D.Gap"},
        {},
    )
    assert not rejected
    assert accepted["c1"].aliases == [("rata-rata jarak check-in", "model")]


async def test_a_view_synonym_of_another_official_metric_is_taken(lab):
    answer = {
        "concepts": [
            {
                "name": "Penjualan.SalesBeforeReturns",
                "definition": "",
                "aliases": ["nilai penjualan"],
                "ambiguous": [],
            }
        ]
    }
    result = await enrich(
        EnrichRequest(operation_id="op-enrich-9", domain_id=lab.sales),
        ADMIN,
        lab.stewardship,
        client=FakeModel(answer),
    )
    assert {
        "value": "nilai penjualan",
        "concept": "Penjualan.SalesBeforeReturns",
        "reason": "Already names Penjualan.NetRevenue",
    } in result["rejected"]


class SlowModel(FakeModel):
    """Times out on every batch but the first."""

    async def complete(self, **kwargs):
        from app.modules.assistant.provider import AssistantProviderError

        self.sent.append(kwargs)
        if len(self.sent) > 1:
            raise AssistantProviderError("AI provider request failed: ReadTimeout")
        return {"content": json.dumps(self.answer)}


async def test_a_batch_that_times_out_is_reported_not_fatal(lab, monkeypatch):
    from app.modules.ontology import enrichment

    monkeypatch.setattr(enrichment, "MAX_CONCEPTS", 2)
    monkeypatch.setattr(enrichment, "MAX_PARALLEL_CALLS", 1)
    result = await enrich(
        EnrichRequest(operation_id="op-enrich-10", domain_id=lab.sales),
        ADMIN,
        lab.stewardship,
        client=SlowModel(ANSWER),
    )
    late = [item for item in result["rejected"] if item["reason"].startswith("The model did not")]
    assert late and result["proposals"]
