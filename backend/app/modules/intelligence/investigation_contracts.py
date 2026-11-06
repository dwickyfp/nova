"""Contracts of an investigation finding, importable without the event hub."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.modules.intelligence.contracts import Contract


class Claim(Contract):
    """One material claim of an investigation, with the evidence it rests on."""

    statement: str = Field(min_length=1, max_length=1000)
    #: ``supported_effect`` needs a reviewed experiment, so an agent cannot assert it.
    causal_status: Literal["arithmetic", "association", "unknown"]
    supporting_evidence_ids: list[str] = Field(min_length=1, max_length=20)
    contradicting_evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class UpdateSubmission(Contract):
    summary: str = Field(min_length=1, max_length=2000)
    claims: list[Claim] = Field(min_length=1, max_length=10)
