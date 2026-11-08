"""Proposals from the derived layer: Nova drafts, stewards decide.

The compiler already proves entities, metrics and their relations from
published Semantic Views. This turns what is not yet official into one
proposal per domain, each change carrying the evidence it came from, so a
steward reviews and approves a domain at once instead of typing it.

Where things go:

- A metric goes to the domain that already owns concepts bound to its view.
- An entity goes to the domain whose metrics measure it, or whose entities
  refer to it. An entity several domains use (Outlet, Product) goes to
  ``shared_domain_id`` when one is given, and is reported otherwise.
- A relation is proposed when both ends are approved concepts or are created
  in the same proposal. A relation to a concept another domain has not
  approved yet waits for a later run.

Nothing is skipped silently: every derived item left out is reported with the
reason. Submission goes through the normal stewardship path, so the caller can
never approve these proposals, and nothing is official before a release.
"""

from __future__ import annotations

import re
from collections import Counter

from fastapi import HTTPException
from pydantic import Field

from app.modules.intelligence.contracts import Contract, fingerprint
from app.modules.ontology import access
from app.modules.ontology.bootstrap import slug_for
from app.modules.ontology.contracts import (
    Change,
    ConceptBody,
    ConceptChange,
    ConceptKind,
    Proposal,
    RelationChange,
    RelationType,
    RepresentationChange,
    relation_id,
)
from app.modules.ontology.derived import visible_layer
from app.modules.ontology.stewardship import ProposalCreate, State, StewardshipService, load_state

MAX_CHANGES = 200


class PromotionRequest(Contract):
    operation_id: str = Field(min_length=8, max_length=64)
    #: Home domain for entities that several domains use.
    shared_domain_id: str | None = Field(default=None, max_length=64)


def entity_slug(label: str) -> str:
    return slug_for(re.sub(r"\s+", "_", label))


def _evidence_line(item: dict) -> str:
    sources = "; ".join(
        f"{e['view_name']} v{e['semantic']['version']} {e['source']}" for e in item["evidence"][:4]
    )
    return f"Derived by Nova ({round(item['confidence'] * 100)}%) from {sources}"


class _Draft:
    """One domain's proposal while it is being assembled."""

    def __init__(self, domain_id: str, taken: set[str]) -> None:
        self.domain_id = domain_id
        self.taken = set(taken)
        self.changes: list[Change] = []
        self.created: dict[str, str] = {}  # derived key -> "@index"
        self.lines: list[str] = []

    def full(self) -> bool:
        return len(self.changes) >= MAX_CHANGES

    def free(self, *names: str) -> bool:
        return all(name.casefold() not in self.taken for name in names if name)

    def add_concept(self, item: dict, kind: ConceptKind, slug: str, label: str) -> str:
        aliases = [alias for alias in item["aliases"] if self.free(alias)
                   and alias.casefold() not in {slug.casefold(), label.casefold()}][:32]
        self.taken.update(name.casefold() for name in (slug, label, *aliases))
        reference = f"@{len(self.changes)}"
        definition = item["definition"] or (
            f"Derived by Nova from {item['evidence'][0]['view_name']}; "
            "a steward should confirm what it means."
        )
        self.changes.append(ConceptChange(op="create", body=ConceptBody(
            domain_id=self.domain_id, kind=kind, slug=slug, label=label[:200],
            definition=definition[:8000], aliases=aliases, unit=item.get("unit"),
        )))
        self.created[item["key"]] = reference
        self.lines.append(f"- {label}: {_evidence_line(item)}")
        return reference


def _view_domains(state: State) -> dict[str, str]:
    """Each view's domain: the one owning most approved concepts bound to it."""
    counts: dict[str, Counter] = {}
    for binding in state.representations.values():
        concept = state.concepts.get(binding.concept_id)
        if concept is None or concept.status != "APPROVED" or binding.status == "REVOKED":
            continue
        counts.setdefault(binding.semantic.view_id, Counter())[concept.domain_id] += 1
    owners = {}
    for view_id, counter in counts.items():
        ranked = sorted(counter.items(), key=lambda pair: (-pair[1], pair[0]))
        owners[view_id] = ranked[0][0]
    return owners


async def propose_from_derived(
    body: PromotionRequest, user: dict, stewardship: StewardshipService
) -> dict:
    repository, catalog = stewardship.repository, stewardship.catalog
    state = await load_state(repository)
    layer = await visible_layer(user, catalog, repository)
    items = {item["key"]: item for item in layer["items"]}
    domains = state.domains
    visible = {
        domain_id for domain_id, domain in domains.items()
        if domain.status == "ACTIVE" and access.can_see(domain, domains, user)
    }
    if body.shared_domain_id is not None and body.shared_domain_id not in visible:
        raise HTTPException(status_code=404, detail=access.NOT_FOUND)
    view_domain = {k: v for k, v in _view_domains(state).items() if v in visible}

    approved = {cid: c for cid, c in state.concepts.items() if c.status == "APPROVED"}
    official: dict[str, str] = {}  # derived key -> approved concept id
    for binding in state.representations.values():
        if binding.status != "VALIDATED" or binding.concept_id not in approved:
            continue
        official[f"metric:{binding.semantic.view_id}.{binding.metric}"] = binding.concept_id
    pending: set[str] = set()  # derived keys some open proposal already covers
    for proposal in await repository.current("proposals", Proposal):
        if proposal.status not in {"PENDING", "APPROVED"}:
            continue
        for change in proposal.changes:
            if isinstance(change, RepresentationChange) and change.op == "create":
                pending.add(f"metric:{change.view_id}.{change.metric}")
            if isinstance(change, ConceptChange) and change.body is not None:
                pending.add(f"name:{change.body.domain_id}:{change.body.label.casefold()}")
    names: dict[str, set[str]] = {}
    for concept in approved.values():
        names.setdefault(concept.domain_id, set()).update(
            name.casefold() for name in (concept.slug, concept.label, *concept.aliases)
        )
    by_label: dict[tuple[str, str], str] = {
        (concept.domain_id, concept.label.casefold()): cid for cid, concept in approved.items()
    }
    for concept_id, concept in approved.items():
        if concept.kind == ConceptKind.BUSINESS_ENTITY:
            for key, item in items.items():
                if item["concept_kind"] == "BUSINESS_ENTITY" and (
                    item["label"].casefold() == concept.label.casefold()
                ):
                    official.setdefault(key, concept_id)

    drafts: dict[str, _Draft] = {}
    skipped: list[dict] = []

    def draft(domain_id: str) -> _Draft:
        return drafts.setdefault(domain_id, _Draft(domain_id, names.get(domain_id, set())))

    def skip(item: dict, reason: str) -> None:
        skipped.append({"key": item["key"], "label": item["label"], "reason": reason})

    relations = [item for item in items.values() if item["item_type"] == "relation"]
    metric_domain: dict[str, str] = {}
    for item in sorted(items.values(), key=lambda value: value["key"]):
        if item["concept_kind"] != "METRIC":
            continue
        view_id = (item["metric"] or "").rpartition(".")[0]
        domain_id = view_domain.get(view_id)
        if domain_id is None:
            skip(item, "No domain owns concepts from this view yet; bootstrap it once")
            continue
        metric_domain[item["key"]] = domain_id
        if item["key"] in official or item["key"] in pending:
            continue
        target = draft(domain_id)
        metric_name = (item["metric"] or "").rpartition(".")[2]
        slug, label = slug_for(metric_name), item["label"]
        if target.full():
            skip(item, "This run reached the proposal size limit; run it again")
        elif not target.free(slug, label) or f"name:{domain_id}:{label.casefold()}" in pending:
            skip(item, f"The name {label} is already used in this domain")
        else:
            reference = target.add_concept(item, ConceptKind.METRIC, slug, label)
            target.changes.append(RepresentationChange(
                op="create", concept_id=reference, view_id=view_id, metric=metric_name,
            ))

    # Entities: home by the metrics that measure them, then by who refers to them.
    homes: dict[str, Counter] = {}
    for relation in relations:
        if relation["relation_type"] == "MEASURES" and relation["source_key"] in metric_domain:
            homes.setdefault(relation["target_key"], Counter())[
                metric_domain[relation["source_key"]]
            ] += 1
    measured = {key for key, counter in homes.items() if counter}
    for relation in relations:
        source, target = relation["source_key"], relation["target_key"]
        if relation["relation_type"] != "RELATED_TO" or target in measured:
            continue
        if items.get(source, {}).get("concept_kind") != "BUSINESS_ENTITY":
            continue
        for domain_id in homes.get(source, Counter()):
            homes.setdefault(target, Counter())[domain_id] += 1
    entity_domain: dict[str, str] = {}
    for item in sorted(items.values(), key=lambda value: value["key"]):
        if item["concept_kind"] != "BUSINESS_ENTITY":
            continue
        counter = homes.get(item["key"], Counter())
        if len(counter) == 1:
            domain_id = next(iter(counter))
        elif len(counter) > 1 and body.shared_domain_id:
            domain_id = body.shared_domain_id
        elif len(counter) > 1:
            skip(item, "Several domains use this entity; choose a domain for shared entities")
            continue
        else:
            skip(item, "No domain's metrics or entities use it yet")
            continue
        entity_domain[item["key"]] = domain_id
        existing = by_label.get((domain_id, item["label"].casefold()))
        if existing is not None:
            official.setdefault(item["key"], existing)
            continue
        if item["key"] in official or f"name:{domain_id}:{item['label'].casefold()}" in pending:
            continue
        target = draft(domain_id)
        slug = entity_slug(item["label"])
        if target.full():
            skip(item, "This run reached the proposal size limit; run it again")
        elif not target.free(slug, item["label"]):
            skip(item, f"The name {item['label']} is already used in this domain")
        else:
            target.add_concept(item, ConceptKind.BUSINESS_ENTITY, slug, item["label"])

    # Relations between concepts that are approved or created in the same proposal.
    seen: set[str] = set()
    for relation in sorted(relations, key=lambda value: value["key"]):
        kind = relation["relation_type"]
        source, target = relation["source_key"], relation["target_key"]
        if kind not in {"MEASURES", "DERIVES", "RELATED_TO"}:
            continue
        if kind == "RELATED_TO" and not all(
            items.get(key, {}).get("concept_kind") == "BUSINESS_ENTITY" for key in (source, target)
        ):
            continue
        home = metric_domain.get(source) or entity_domain.get(source)
        if home is None:
            continue
        holder = drafts.get(home)
        ends: list[str | None] = []
        for key in (source, target):
            if key in official:
                ends.append(official[key])
            elif holder is not None and key in holder.created:
                ends.append(holder.created[key])
            else:
                ends.append(None)
        if ends[0] is None or ends[1] is None:
            continue
        resolved: list[str] = [ends[0], ends[1]]
        if not any(end.startswith("@") for end in resolved):
            existing = relation_id(resolved[0], RelationType(kind), resolved[1])
            if existing in state.relations and state.relations[existing].status == "ACTIVE":
                continue
        identity = f"{kind}:{':'.join(sorted(resolved) if kind == 'RELATED_TO' else resolved)}"
        if identity in seen or resolved[0] == resolved[1]:
            continue
        seen.add(identity)
        target_draft = draft(home)
        if target_draft.full():
            continue
        role = f"; role {relation['role']}" if relation.get("role") else ""
        target_draft.changes.append(RelationChange(
            op="create", source_id=resolved[0], type=RelationType(kind), target_id=resolved[1],
            justification=(_evidence_line(relation) + role)[:2000],
        ))

    created = []
    for domain_id, target in sorted(drafts.items()):
        if not target.changes:
            continue
        concepts = sum(isinstance(change, ConceptChange) for change in target.changes)
        links = sum(isinstance(change, RelationChange) for change in target.changes)
        domain = domains[domain_id]
        rationale = (
            f"Nova derived {concepts} concept(s) and {links} relation(s) for {domain.name} from "
            "published Semantic Views. Each change carries the evidence it came from; review "
            "the meaning, not the typing.\n" + "\n".join(target.lines)
        )[:4000]
        digest = fingerprint([change.model_dump(mode="json") for change in target.changes])
        proposal = await stewardship.create(
            ProposalCreate(
                title=f"Derived meaning for {domain.name}",
                rationale=rationale,
                changes=target.changes,
                operation_id="eng-" + fingerprint([body.operation_id, domain_id, digest])[:40],
            ),
            user,
            source="ENGINE",
        )
        created.append(proposal)
    await access.audit(
        "PROPOSE_DERIVED", "ONTOLOGY_PROPOSAL", ",".join(p.id for p in created)[:128] or "none",
        user,
    )
    return {
        "proposals": [
            proposal.model_dump(mode="json", exclude={"request_digest"}) for proposal in created
        ],
        "skipped": skipped,
    }
