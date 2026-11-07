"""Records of the ontology evaluation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.modules.intelligence.contracts import Contract, fingerprint
from app.modules.ontology.contracts import OntologyRecord

CaseKind = Literal["answerable", "unanswerable", "ambiguous"]
Behaviour = Literal["answer", "abstain", "clarify"]
RunKind = Literal["gold", "answers", "claims", "joins"]
Mode = Literal["with_ontology", "without_ontology"]
Outcome = Literal["correct", "wrong", "answered", "abstained", "clarified", "failed"]
Verdict = Literal["yes", "no", "unsure"]


class Review(Contract):
    """One judge's or person's verdict on a case or a claim."""

    reviewer: str = Field(min_length=1, max_length=128)
    kind: Literal["model", "external", "person"]
    verdict: Verdict
    reason: str = Field(default="", max_length=1000)


class EvalCase(OntologyRecord):
    set_name: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    kind: CaseKind
    intent: str = Field(min_length=1, max_length=64)
    language: Literal["id", "en"]
    question: str = Field(default="", max_length=1000)  # empty only while a draft
    view_id: str = Field(min_length=1, max_length=128)
    view_name: str = Field(default="", max_length=256)
    plan: dict[str, Any] | None = None
    #: What the plan asks, in business words; what judges compare the question with.
    meaning: str = Field(default="", max_length=2000)
    expected: Behaviour
    #: Business words the question must use verbatim (vocabulary gold sets).
    terms: list[str] = Field(default_factory=list, max_length=8)
    reviews: list[Review] = Field(default_factory=list)
    status: Literal["DRAFT", "ACTIVE", "REJECTED"] = "DRAFT"


class EvalRun(OntologyRecord):
    kind: RunKind
    set_name: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    mode: Mode | None = None
    release_id: str | None = Field(default=None, max_length=64)
    manifest_hash: str | None = Field(default=None, max_length=64)
    status: Literal["RUNNING", "COMPLETED", "FAILED"] = "RUNNING"
    #: Items in the order they are processed; ``done`` of them are finished.
    items: list[str] = Field(default_factory=list)
    done: int = Field(default=0, ge=0)
    stage: str = Field(default="", max_length=64)
    model: str | None = Field(default=None, max_length=256)
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=1000)


class EvalResult(OntologyRecord):
    run_id: str = Field(min_length=1, max_length=64)
    item: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=32)
    detail: dict[str, Any] = Field(default_factory=dict)
    reviews: list[Review] = Field(default_factory=list)


def result_id(run_id: str, item: str) -> str:
    return "evr_" + fingerprint([run_id, item])[:32]


def case_id(set_name: str, version: int, key: str) -> str:
    return "evc_" + fingerprint([set_name, version, key])[:32]
