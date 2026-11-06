"""Change proposals, steward review, and atomic-by-replay application.

Approved meaning only changes through a proposal. Each affected domain needs
an approval from one of its steward roles, the submitter can never approve
their own proposal, and any steward's rejection closes it. Applying writes the
new revisions in dependency order; every record carries the proposal id, so an
interrupted application is resumed rather than repeated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from fastapi import HTTPException
from pydantic import Field

from app.modules.intelligence.contracts import Contract, fingerprint
from app.modules.ontology import access
from app.modules.ontology.contracts import (
    CURRENT_ALIAS,
    Actor,
    Alias,
    Change,
    Concept,
    ConceptChange,
    Domain,
    OntologyRecord,
    Proposal,
    Relation,
    RelationChange,
    Representation,
    RepresentationChange,
    Review,
    relation_id,
)
from app.modules.ontology.relations import graph_issues
from app.modules.ontology.repository import OntologyRepository, ontology_repository
from app.modules.ontology.representations import SemanticCatalog, SemanticViewCatalog, build


class ProposalCreate(Contract):
    title: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=4000)
    changes: list[Change] = Field(min_length=1, max_length=200)
    base_release_id: str | None = Field(default=None, max_length=64)
    operation_id: str = Field(min_length=8, max_length=64)


class ReviewRequest(Contract):
    decision: Literal["APPROVE", "REJECT"]
    rationale: str = Field(min_length=1, max_length=2000)
    operation_id: str = Field(min_length=8, max_length=64)


@dataclass
class State:
    domains: dict[str, Domain]
    concepts: dict[str, Concept]
    relations: dict[str, Relation]
    representations: dict[str, Representation]


@dataclass
class Plan:
    writes: list[tuple[str, OntologyRecord, int]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    domain_ids: set[str] = field(default_factory=set)


async def load_state(repository: OntologyRepository) -> State:
    return State(
        domains={item.id: item for item in await repository.current("domains", Domain)},
        concepts={item.id: item for item in await repository.current("concepts", Concept)},
        relations={item.id: item for item in await repository.current("relations", Relation)},
        representations={
            item.id: item
            for item in await repository.current("representations", Representation)
        },
    )


def assign_ids(proposal_id: str, changes: list[Change]) -> list[Change]:
    """Give every created concept and binding a stable id derived from the proposal.

    A relation or binding can name a concept created by the same proposal as
    ``@<index>``, the position of that concept's change in the list.
    """
    created: dict[str, str] = {}
    for index, change in enumerate(changes):
        if isinstance(change, ConceptChange) and change.op == "create":
            created[f"@{index}"] = "con_" + fingerprint([proposal_id, index])[:32]

    def resolve(value: str) -> str:
        if value.startswith("@"):
            if value not in created:
                raise HTTPException(
                    status_code=422, detail=f"{value} does not name a concept created here"
                )
            return created[value]
        return value

    assigned: list[Change] = []
    for index, change in enumerate(changes):
        if isinstance(change, ConceptChange) and change.op == "create":
            change = change.model_copy(update={"concept_id": created[f"@{index}"]})
        elif isinstance(change, RelationChange):
            change = change.model_copy(
                update={"source_id": resolve(change.source_id),
                        "target_id": resolve(change.target_id)}
            )
        elif isinstance(change, RepresentationChange) and change.op == "create":
            change = change.model_copy(
                update={
                    "representation_id": "rep_" + fingerprint([proposal_id, index])[:32],
                    "concept_id": resolve(change.concept_id),
                }
            )
        assigned.append(change)
    return assigned


def _target_key(change: Change) -> str:
    if isinstance(change, ConceptChange):
        return f"concept:{change.concept_id}"
    if isinstance(change, RelationChange):
        return f"relation:{relation_id(change.source_id, change.type, change.target_id)}"
    return f"representation:{change.representation_id}"


async def plan_changes(
    proposal_id: str,
    changes: list[Change],
    state: State,
    catalog: SemanticCatalog,
    actor: Actor,
) -> Plan:
    """Validate ``changes`` against ``state`` and list the revisions to write."""
    plan = Plan()
    concepts = dict(state.concepts)
    relations = dict(state.relations)
    representations = dict(state.representations)
    keys = [_target_key(change) for change in changes]
    if len(set(keys)) != len(keys):
        plan.issues.append("A proposal changes each record at most once")
        return plan
    concept_writes: list[tuple[str, OntologyRecord, int]] = []
    relation_writes: list[tuple[str, OntologyRecord, int]] = []
    representation_writes: list[tuple[str, OntologyRecord, int]] = []

    for change in changes:
        if isinstance(change, ConceptChange):
            _plan_concept(change, proposal_id, state, concepts, actor, plan, concept_writes)
    for change in changes:
        if isinstance(change, RelationChange):
            _plan_relation(change, proposal_id, concepts, relations, actor, plan, relation_writes)
    for change in changes:
        if isinstance(change, RepresentationChange):
            await _plan_representation(
                change, proposal_id, concepts, representations, catalog, actor, plan,
                representation_writes,
            )

    plan.issues.extend(naming_issues(concepts, state.domains))
    plan.issues.extend(graph_issues(concepts, list(relations.values())))
    for representation in representations.values():
        concept = concepts.get(representation.concept_id)
        if representation.status == "VALIDATED" and (
            concept is None or concept.status != "APPROVED"
        ):
            plan.issues.append(
                f"Revoke representation {representation.id} before deprecating its concept"
            )
    plan.writes = concept_writes + relation_writes + representation_writes
    return plan


def _plan_concept(
    change: ConceptChange,
    proposal_id: str,
    state: State,
    concepts: dict[str, Concept],
    actor: Actor,
    plan: Plan,
    writes: list,
) -> None:
    current = concepts.get(change.concept_id)
    if current is not None and current.proposal_id == proposal_id:
        plan.domain_ids.add(current.domain_id)
        return  # already applied by an earlier attempt
    if change.op == "create":
        assert change.body is not None
        domain = state.domains.get(change.body.domain_id)
        if domain is None or domain.status != "ACTIVE":
            plan.issues.append(f"Domain {change.body.domain_id} is not an active domain")
            return
        if current is not None:
            plan.issues.append(f"Concept {change.concept_id} already exists")
            return
        record = Concept(
            id=change.concept_id, actor=actor, proposal_id=proposal_id,
            **change.body.model_dump(),
        )
        concepts[record.id] = record
        plan.domain_ids.add(record.domain_id)
        writes.append(("concepts", record, 0))
        return
    if current is None:
        plan.issues.append(f"Concept {change.concept_id} does not exist")
        return
    plan.domain_ids.add(current.domain_id)
    if current.revision != change.base_revision:
        plan.issues.append(f"Concept {current.slug} changed since revision {change.base_revision}")
        return
    if current.status != "APPROVED":
        plan.issues.append(f"Concept {current.slug} is deprecated")
        return
    if change.op == "update":
        assert change.body is not None
        if change.body.domain_id != current.domain_id:
            plan.issues.append("A concept keeps its home domain; propose a new concept instead")
            return
        record = Concept(
            id=current.id, actor=actor, proposal_id=proposal_id, **change.body.model_dump()
        )
    else:
        record = current.model_copy(
            update={"actor": actor, "proposal_id": proposal_id, "status": "DEPRECATED"}
        )
    concepts[record.id] = record
    writes.append(("concepts", record, current.revision))


def _plan_relation(
    change: RelationChange,
    proposal_id: str,
    concepts: dict[str, Concept],
    relations: dict[str, Relation],
    actor: Actor,
    plan: Plan,
    writes: list,
) -> None:
    record_id = relation_id(change.source_id, change.type, change.target_id)
    for concept_id in (change.source_id, change.target_id):
        concept = concepts.get(concept_id)
        if concept is not None:
            plan.domain_ids.add(concept.domain_id)
    current = relations.get(record_id)
    if current is not None and current.proposal_id == proposal_id:
        return
    if change.op == "create":
        if current is not None and current.status == "ACTIVE":
            plan.issues.append(
                f"{change.type.value} {change.source_id} -> {change.target_id} exists"
            )
            return
        record = Relation(
            id=record_id, actor=actor, source_id=change.source_id, type=change.type,
            target_id=change.target_id, justification=change.justification.strip(),
            proposal_id=proposal_id,
        )
    else:
        if current is None or current.status != "ACTIVE":
            plan.issues.append(
                f"{change.type.value} {change.source_id} -> {change.target_id} is not active"
            )
            return
        record = current.model_copy(
            update={"actor": actor, "proposal_id": proposal_id, "status": "RETIRED"}
        )
    relations[record_id] = record
    writes.append(("relations", record, current.revision if current else 0))


async def _plan_representation(
    change: RepresentationChange,
    proposal_id: str,
    concepts: dict[str, Concept],
    representations: dict[str, Representation],
    catalog: SemanticCatalog,
    actor: Actor,
    plan: Plan,
    writes: list,
) -> None:
    current = representations.get(change.representation_id)
    concept_id = change.concept_id if change.op == "create" else (
        current.concept_id if current else ""
    )
    concept = concepts.get(concept_id)
    if concept is not None:
        plan.domain_ids.add(concept.domain_id)
    if current is not None and current.proposal_id == proposal_id:
        return
    if change.op == "create":
        if current is not None:
            plan.issues.append(f"Representation {change.representation_id} already exists")
            return
        record, issues = await build(change, concept, catalog, actor, proposal_id)
        if issues or record is None:
            plan.issues.extend(issues)
            return
        duplicate = next(
            (
                item for item in representations.values()
                if item.status == "VALIDATED" and item.concept_id == record.concept_id
                and item.purpose == record.purpose
                and item.semantic.view_id == record.semantic.view_id
                and item.metric == record.metric
            ),
            None,
        )
        if duplicate is not None:
            name = concept.slug if concept else concept_id
            plan.issues.append(f"{name} already binds this metric")
            return
        representations[record.id] = record
        writes.append(("representations", record, 0))
        return
    if current is None:
        plan.issues.append(f"Representation {change.representation_id} does not exist")
        return
    if current.revision != change.base_revision or current.status == "REVOKED":
        plan.issues.append(f"Representation {current.id} changed or is already revoked")
        return
    record = current.model_copy(
        update={"actor": actor, "proposal_id": proposal_id, "status": "REVOKED",
                "status_reason": "Revoked by proposal"}
    )
    representations[record.id] = record
    writes.append(("representations", record, current.revision))


def naming_issues(concepts: dict[str, Concept], domains: dict[str, Domain]) -> list[str]:
    """Names are unique within a domain; the same name in two domains is allowed."""
    issues = []
    slugs: dict[tuple[str, str], str] = {}
    names: dict[tuple[str, str], str] = {}
    for concept in sorted(concepts.values(), key=lambda item: item.id):
        if concept.status != "APPROVED":
            continue
        slug_key = (concept.domain_id, concept.slug.casefold())
        if slug_key in slugs:
            issues.append(f"Two concepts in one domain are named {concept.slug}")
        slugs[slug_key] = concept.id
        for name in {concept.slug, concept.label, *concept.aliases}:
            key = (concept.domain_id, name.casefold())
            owner = names.setdefault(key, concept.id)
            if owner != concept.id:
                domain = domains.get(concept.domain_id)
                where = domain.name if domain else concept.domain_id
                issues.append(f"'{name}' names two concepts in domain {where}")
    return issues


class StewardshipService:
    def __init__(
        self,
        repository: OntologyRepository = ontology_repository,
        catalog: SemanticCatalog | None = None,
    ) -> None:
        self.repository = repository
        self.catalog: SemanticCatalog = catalog or SemanticViewCatalog()

    async def _visible(self, proposal: Proposal, user: dict, domains: dict[str, Domain]) -> bool:
        return (
            access.is_admin(user)
            or proposal.submitter == Actor.from_user(user).principal
            or all(access.can_see(domains.get(item), domains, user) for item in proposal.domain_ids)
        )

    async def get(self, proposal_id: str, user: dict) -> Proposal:
        proposal = await self.repository.latest("proposals", proposal_id, Proposal)
        domains = {item.id: item for item in await self.repository.current("domains", Domain)}
        if proposal is None or not await self._visible(proposal, user, domains):
            raise HTTPException(status_code=404, detail=access.NOT_FOUND)
        return proposal

    async def list(self, user: dict, status: str | None = None) -> list[Proposal]:
        domains = {item.id: item for item in await self.repository.current("domains", Domain)}
        proposals = await self.repository.current("proposals", Proposal)
        visible = [
            item for item in proposals
            if (status is None or item.status == status)
            and await self._visible(item, user, domains)
        ]
        return sorted(visible, key=lambda item: item.created_at, reverse=True)

    async def create(
        self,
        body: ProposalCreate,
        user: dict,
        *,
        source: Literal["USER", "BOOTSTRAP", "AGENT", "AUTOPILOT", "NEWS"] = "USER",
    ) -> Proposal:
        actor = Actor.from_user(user)
        proposal_id = "prp_" + fingerprint([actor.principal, body.operation_id])[:32]
        digest = fingerprint(body.model_dump(mode="json"))
        existing = await self.repository.latest("proposals", proposal_id, Proposal)
        if existing is not None:
            if existing.request_digest != digest:
                raise HTTPException(
                    status_code=409, detail="This operation id was used for a different proposal"
                )
            return existing
        changes = assign_ids(proposal_id, body.changes)
        state = await load_state(self.repository)
        plan = await plan_changes(proposal_id, changes, state, self.catalog, actor)
        if plan.issues:
            raise HTTPException(status_code=422, detail={"issues": plan.issues[:50]})
        for domain_id in plan.domain_ids:
            if not access.can_see(state.domains.get(domain_id), state.domains, user):
                raise HTTPException(status_code=404, detail=access.NOT_FOUND)
        base = body.base_release_id
        if base is None:
            alias = await self.repository.latest("aliases", CURRENT_ALIAS, Alias)
            base = alias.release_id if alias else None
        proposal = Proposal(
            id=proposal_id, actor=actor, title=body.title, rationale=body.rationale,
            source=source, base_release_id=base, changes=changes,
            domain_ids=sorted(plan.domain_ids), submitter=actor.principal,
            operation_id=body.operation_id, request_digest=digest,
        )
        saved = await self.repository.save("proposals", proposal)
        await access.audit("PROPOSE", "ONTOLOGY_PROPOSAL", saved.id, user)
        return saved

    async def review(self, proposal_id: str, body: ReviewRequest, user: dict) -> Proposal:
        proposal = await self.get(proposal_id, user)
        if any(item.operation_id == body.operation_id for item in proposal.reviews):
            return proposal
        actor = Actor.from_user(user)
        if proposal.status != "PENDING":
            raise HTTPException(
                status_code=409, detail=f"The proposal is {proposal.status.lower()}"
            )
        if proposal.submitter == actor.principal:
            raise await access.deny(
                "REVIEW", "ONTOLOGY_PROPOSAL", proposal.id, user,
                "The submitter cannot review their own proposal",
            )
        domains = {item.id: item for item in await self.repository.current("domains", Domain)}
        stewarded = [
            item for item in proposal.domain_ids
            if (domain := domains.get(item)) is not None and access.stewards(domain, user)
        ]
        if not stewarded:
            raise await access.deny(
                "REVIEW", "ONTOLOGY_PROPOSAL", proposal.id, user,
                "The active role does not steward a domain of this proposal",
            )
        review = Review(
            operation_id=body.operation_id, principal=actor.principal,
            active_role=actor.active_role, domain_ids=stewarded,
            decision=body.decision, rationale=body.rationale,
        )
        reviews = [*proposal.reviews, review]
        approved = {
            domain for item in reviews if item.decision == "APPROVE" for domain in item.domain_ids
        }
        status: Literal["PENDING", "APPROVED", "REJECTED"] = (
            "REJECTED" if body.decision == "REJECT"
            else "APPROVED" if approved >= set(proposal.domain_ids)
            else "PENDING"
        )
        proposal = await self.repository.save(
            "proposals",
            proposal.model_copy(update={"actor": actor, "reviews": reviews, "status": status}),
            expected_revision=proposal.revision,
        )
        await access.audit(
            "REVIEW", "ONTOLOGY_PROPOSAL", proposal.id, user, decision=body.decision
        )
        if proposal.status == "APPROVED":
            proposal = await self._apply(proposal, user)
        return proposal

    async def apply(self, proposal_id: str, user: dict) -> Proposal:
        """Finish applying an approved proposal, for example after a crash."""
        proposal = await self.get(proposal_id, user)
        if proposal.status == "APPLIED":
            return proposal
        if proposal.status != "APPROVED":
            raise HTTPException(status_code=409, detail="Only an approved proposal can be applied")
        domains = {item.id: item for item in await self.repository.current("domains", Domain)}
        if not access.is_admin(user) and not any(
            (domain := domains.get(item)) is not None and access.stewards(domain, user)
            for item in proposal.domain_ids
        ):
            raise await access.deny(
                "APPLY", "ONTOLOGY_PROPOSAL", proposal.id, user,
                "Only a steward of this proposal may apply it",
            )
        return await self._apply(proposal, user)

    async def _apply(self, proposal: Proposal, user: dict) -> Proposal:
        actor = Actor.from_user(user)
        state = await load_state(self.repository)
        plan = await plan_changes(proposal.id, proposal.changes, state, self.catalog, actor)
        if plan.issues:
            await access.audit(
                "APPLY", "ONTOLOGY_PROPOSAL", proposal.id, user, status="FAILED",
                error="; ".join(plan.issues[:5]),
            )
            raise HTTPException(status_code=409, detail={"issues": plan.issues[:50]})
        for kind, record, expected in plan.writes:
            await self.repository.save(kind, record, expected_revision=expected)
        applied = await self.repository.save(
            "proposals",
            proposal.model_copy(update={"actor": actor, "status": "APPLIED"}),
            expected_revision=proposal.revision,
        )
        await access.audit("APPLY", "ONTOLOGY_PROPOSAL", proposal.id, user)
        return applied

    async def withdraw(self, proposal_id: str, user: dict) -> Proposal:
        proposal = await self.get(proposal_id, user)
        actor = Actor.from_user(user)
        if proposal.status == "WITHDRAWN":
            return proposal
        if proposal.submitter != actor.principal:
            raise await access.deny(
                "WITHDRAW", "ONTOLOGY_PROPOSAL", proposal.id, user,
                "Only the submitter can withdraw a proposal",
            )
        if proposal.status not in {"PENDING", "APPROVED"}:
            raise HTTPException(
                status_code=409, detail=f"The proposal is {proposal.status.lower()}"
            )
        withdrawn = await self.repository.save(
            "proposals",
            proposal.model_copy(update={"actor": actor, "status": "WITHDRAWN"}),
            expected_revision=proposal.revision,
        )
        await access.audit("WITHDRAW", "ONTOLOGY_PROPOSAL", proposal.id, user)
        return withdrawn


stewardship_service = StewardshipService()
