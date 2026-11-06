"""Versioned, credential-free contracts for the Supernova Business Ontology.

The ontology is shared business meaning for the whole deployment. Unlike the
scoped Context Graph, its identifiers never contain a principal or a role:
who may *see* a concept is decided separately, by its domain.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.modules.intelligence.contracts import Contract, SemanticRef, fingerprint, utc_now

SLUG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
ROLE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}$")
CURRENT_ALIAS = "current"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class ConceptKind(StrEnum):
    TERM = "TERM"
    BUSINESS_ENTITY = "BUSINESS_ENTITY"
    METRIC = "METRIC"
    DIMENSION = "DIMENSION"
    MEASURE = "MEASURE"
    POLICY = "POLICY"
    GOAL = "GOAL"
    BUSINESS_EVENT_TYPE = "BUSINESS_EVENT_TYPE"
    ACTION_TYPE = "ACTION_TYPE"
    PROCESS = "PROCESS"


class Classification(StrEnum):
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"
    HIGHLY_SENSITIVE = "HIGHLY_SENSITIVE"


class Actor(Contract):
    """Who wrote a revision, taken from the authenticated session."""

    principal: str = Field(min_length=1, max_length=128)
    active_role: str = Field(min_length=1, max_length=128)
    security_context_version: int = Field(ge=1)

    @classmethod
    def from_user(cls, user: dict) -> Actor:
        from app.modules.assistant.security import session_security

        context = session_security(user)
        return cls(
            principal=context.principal,
            active_role=context.active_role,
            security_context_version=context.security_context_version,
        )


class OntologyRecord(Contract):
    id: str = Field(min_length=1, max_length=64)
    revision: int = Field(default=1, ge=1)
    actor: Actor
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def content_fingerprint(self) -> str:
        return fingerprint(
            self.model_dump(mode="json", exclude={"revision", "actor", "created_at", "updated_at"})
        )


def _roles(values: list[str]) -> list[str]:
    cleaned = sorted({value.strip() for value in values if value.strip()})
    for value in cleaned:
        if not ROLE.match(value):
            raise ValueError(f"Invalid role name: {value}")
    return cleaned


class Domain(OntologyRecord):
    name: str = Field(min_length=1, max_length=64)
    parent_id: str | None = Field(default=None, max_length=64)
    description: str = Field(default="", max_length=2000)
    steward_roles: list[str] = Field(min_length=1, max_length=16)
    visibility_roles: list[str] = Field(default_factory=list, max_length=64)
    status: Literal["ACTIVE", "RETIRED"] = "ACTIVE"

    @field_validator("steward_roles", "visibility_roles")
    @classmethod
    def clean_roles(cls, values: list[str]) -> list[str]:
        return _roles(values)

    @model_validator(mode="after")
    def has_steward(self):
        if not self.steward_roles:
            raise ValueError("A domain needs at least one steward role")
        return self

    @field_validator("name")
    @classmethod
    def slug_name(cls, value: str) -> str:
        if not SLUG.match(value):
            raise ValueError("Domain names are letters, digits and underscores")
        return value


class ConceptBody(Contract):
    """The business meaning a steward approves."""

    domain_id: str = Field(min_length=1, max_length=64)
    kind: ConceptKind
    slug: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    definition: str = Field(min_length=1, max_length=8000)
    aliases: list[str] = Field(default_factory=list, max_length=32)
    unit: str | None = Field(default=None, max_length=64)
    grain: str | None = Field(default=None, max_length=200)
    time_semantics: str | None = Field(default=None, max_length=500)
    exclusions: list[str] = Field(default_factory=list, max_length=32)
    classification: Classification = Classification.INTERNAL

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        if not SLUG.match(value):
            raise ValueError("Concept slugs are letters, digits and underscores")
        return value

    @field_validator("aliases")
    @classmethod
    def clean_aliases(cls, values: list[str]) -> list[str]:
        seen: dict[str, str] = {}
        for value in values:
            text = " ".join(value.split())
            if not text or len(text) > 200:
                raise ValueError("Aliases are 1-200 characters")
            seen.setdefault(text.casefold(), text)
        return sorted(seen.values(), key=str.casefold)


class Concept(OntologyRecord, ConceptBody):
    """One approved version of a concept; every revision is immutable."""

    status: Literal["APPROVED", "DEPRECATED"] = "APPROVED"
    proposal_id: str = Field(min_length=1, max_length=64)


class RelationType(StrEnum):
    IS_A = "IS_A"
    HAS_PART = "HAS_PART"
    DERIVES = "DERIVES"
    MEASURES = "MEASURES"
    APPLIES_TO = "APPLIES_TO"
    EQUIVALENT_TO = "EQUIVALENT_TO"
    RELATED_TO = "RELATED_TO"
    AFFECTS_GOAL = "AFFECTS_GOAL"


def relation_id(source_id: str, relation: RelationType, target_id: str) -> str:
    if relation in {RelationType.EQUIVALENT_TO, RelationType.RELATED_TO}:
        source_id, target_id = sorted((source_id, target_id))
    return "rel_" + fingerprint([source_id, relation.value, target_id])[:32]


class Relation(OntologyRecord):
    source_id: str = Field(min_length=1, max_length=64)
    type: RelationType
    target_id: str = Field(min_length=1, max_length=64)
    justification: str = Field(min_length=1, max_length=2000)
    status: Literal["ACTIVE", "RETIRED"] = "ACTIVE"
    proposal_id: str = Field(min_length=1, max_length=64)


class Representation(OntologyRecord):
    """A validated binding of a concept to one published semantic metric."""

    concept_id: str = Field(min_length=1, max_length=64)
    semantic: SemanticRef
    metric: str = Field(min_length=1, max_length=128)
    claim_fingerprint: str = Field(min_length=64, max_length=64)
    purpose: str = Field(default="general", min_length=1, max_length=64)
    unit: str | None = Field(default=None, max_length=64)
    status: Literal["VALIDATED", "STALE", "REVOKED"] = "VALIDATED"
    status_reason: str | None = Field(default=None, max_length=500)
    validated_at: datetime = Field(default_factory=utc_now)
    proposal_id: str = Field(min_length=1, max_length=64)


# --- proposals -------------------------------------------------------------


class ConceptChange(Contract):
    target: Literal["concept"] = "concept"
    op: Literal["create", "update", "deprecate"]
    concept_id: str = Field(default="", max_length=64)
    base_revision: int = Field(default=0, ge=0)
    body: ConceptBody | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.op == "create":
            if self.base_revision or self.body is None:
                raise ValueError("Creating a concept needs a body and no base revision")
        elif not self.concept_id or not self.base_revision:
            raise ValueError("Changing a concept needs its id and base revision")
        if self.op == "update" and self.body is None:
            raise ValueError("Updating a concept needs the new body")
        if self.op == "deprecate" and self.body is not None:
            raise ValueError("Deprecating a concept takes no body")
        return self


class RelationChange(Contract):
    target: Literal["relation"] = "relation"
    op: Literal["create", "retire"]
    source_id: str = Field(min_length=1, max_length=64)
    type: RelationType
    target_id: str = Field(min_length=1, max_length=64)
    justification: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def shape(self):
        if self.source_id == self.target_id:
            raise ValueError("A relation needs two different concepts")
        if self.op == "create" and not self.justification.strip():
            raise ValueError("A new relation needs a justification")
        return self


class RepresentationChange(Contract):
    target: Literal["representation"] = "representation"
    op: Literal["create", "revoke"]
    representation_id: str = Field(default="", max_length=64)
    base_revision: int = Field(default=0, ge=0)
    concept_id: str = Field(default="", max_length=64)
    view_id: str = Field(default="", max_length=128)
    metric: str = Field(default="", max_length=128)
    purpose: str = Field(default="general", min_length=1, max_length=64)

    @model_validator(mode="after")
    def shape(self):
        if self.op == "create" and not (self.concept_id and self.view_id and self.metric):
            raise ValueError("A representation needs a concept, a semantic view and a metric")
        if self.op == "revoke" and not (self.representation_id and self.base_revision):
            raise ValueError("Revoking a representation needs its id and base revision")
        return self


Change = Annotated[
    ConceptChange | RelationChange | RepresentationChange, Field(discriminator="target")
]


class Review(Contract):
    operation_id: str = Field(min_length=8, max_length=64)
    principal: str = Field(min_length=1, max_length=128)
    active_role: str = Field(min_length=1, max_length=128)
    domain_ids: list[str] = Field(max_length=32)
    decision: Literal["APPROVE", "REJECT"]
    rationale: str = Field(min_length=1, max_length=2000)
    at: datetime = Field(default_factory=utc_now)


class Proposal(OntologyRecord):
    title: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=4000)
    source: Literal["USER", "BOOTSTRAP", "AGENT", "AUTOPILOT", "NEWS"] = "USER"
    base_release_id: str | None = Field(default=None, max_length=64)
    changes: list[Change] = Field(min_length=1, max_length=200)
    domain_ids: list[str] = Field(min_length=1, max_length=32)
    submitter: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(min_length=8, max_length=64)
    request_digest: str = Field(min_length=64, max_length=64)
    status: Literal["PENDING", "APPROVED", "APPLIED", "REJECTED", "WITHDRAWN"] = "PENDING"
    reviews: list[Review] = Field(default_factory=list, max_length=64)


# --- releases ----------------------------------------------------------------


class ReleaseMember(Contract):
    kind: Literal["domain", "concept", "relation", "representation"]
    id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    fingerprint: str = Field(min_length=64, max_length=64)


class Release(OntologyRecord):
    sequence: int = Field(ge=1)
    base_release_id: str | None = Field(default=None, max_length=64)
    members: list[ReleaseMember] = Field(max_length=20000)
    manifest_hash: str = Field(min_length=64, max_length=64)
    status: Literal["PREPARED", "BLOCKED", "PUBLISHED"] = "PREPARED"
    issues: list[str] = Field(default_factory=list, max_length=200)
    warnings: list[str] = Field(default_factory=list, max_length=200)
    note: str = Field(default="", max_length=2000)
    published_at: datetime | None = None


class Alias(OntologyRecord):
    """Which release is current. Moving it is an optimistic compare-and-swap."""

    release_id: str = Field(min_length=1, max_length=64)
    previous_release_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)


def manifest_hash(members: list[ReleaseMember]) -> str:
    return fingerprint(
        sorted((member.kind, member.id, member.revision, member.fingerprint) for member in members)
    )
