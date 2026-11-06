"""Domain administration and role-filtered reads of the ontology.

Readers see the ``current`` published release by default; stewards can also
read the ``working`` state, which is approved meaning not yet released.
Concepts in a domain the caller cannot see are filtered out before any search,
count or traversal, and asking for one by id answers the same 404 as a
concept that does not exist.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal, TypeVar

from fastapi import HTTPException
from pydantic import Field

from app.modules.intelligence.contracts import Contract, fingerprint
from app.modules.ontology import access
from app.modules.ontology.contracts import (
    Actor,
    Concept,
    Domain,
    OntologyRecord,
    Relation,
    Release,
    Representation,
)
from app.modules.ontology.relations import RULES, neighbourhood
from app.modules.ontology.releases import ReleaseService
from app.modules.ontology.repository import OntologyRepository, ontology_repository
from app.modules.ontology.representations import SemanticCatalog, SemanticViewCatalog, freshness

Scope = Literal["current", "working"]
RELEASE_CACHE_SIZE = 8
R = TypeVar("R", bound=OntologyRecord)


class DomainCreate(Contract):
    name: str = Field(min_length=1, max_length=64)
    parent_id: str | None = Field(default=None, max_length=64)
    description: str = Field(default="", max_length=2000)
    steward_roles: list[str] = Field(min_length=1, max_length=16)
    visibility_roles: list[str] = Field(default_factory=list, max_length=64)


class DomainUpdate(Contract):
    description: str = Field(default="", max_length=2000)
    steward_roles: list[str] = Field(min_length=1, max_length=16)
    visibility_roles: list[str] = Field(default_factory=list, max_length=64)
    status: Literal["ACTIVE", "RETIRED"] = "ACTIVE"
    expected_revision: int = Field(ge=1)


@dataclass
class Snapshot:
    release: Release | None
    domains: dict[str, Domain]
    concepts: dict[str, Concept]
    relations: list[Relation]
    representations: list[Representation]


class OntologyService:
    def __init__(
        self,
        repository: OntologyRepository = ontology_repository,
        catalog: SemanticCatalog | None = None,
    ) -> None:
        self.repository = repository
        self.catalog: SemanticCatalog = catalog or SemanticViewCatalog()
        self.releases = ReleaseService(repository, self.catalog)
        #: Published releases are immutable, so their pinned records are cached
        #: by release id. Visibility is applied per caller after the cache.
        self._released: OrderedDict[str, Snapshot] = OrderedDict()

    # --- domains -------------------------------------------------------------

    async def _domains(self) -> dict[str, Domain]:
        return {item.id: item for item in await self.repository.current("domains", Domain)}

    def _domain_view(self, domain: Domain, domains: dict[str, Domain], user: dict) -> dict:
        return {
            "id": domain.id,
            "revision": domain.revision,
            "name": domain.name,
            "qualified_path": access.qualified_path(domain, domains),
            "parent_id": domain.parent_id,
            "description": domain.description,
            "steward_roles": domain.steward_roles,
            "visibility_roles": domain.visibility_roles,
            "status": domain.status,
            "can_review": access.stewards(domain, user),
            "updated_at": domain.updated_at,
        }

    async def list_domains(self, user: dict) -> list[dict]:
        domains = await self._domains()
        visible = [item for item in domains.values() if access.can_see(item, domains, user)]
        return sorted(
            (self._domain_view(item, domains, user) for item in visible),
            key=lambda item: item["qualified_path"].casefold(),
        )

    async def create_domain(self, body: DomainCreate, user: dict) -> dict:
        await access.require_admin("CREATE_DOMAIN", "ONTOLOGY_DOMAIN", body.name, user)
        domains = await self._domains()
        if body.parent_id is not None and body.parent_id not in domains:
            raise HTTPException(status_code=422, detail="The parent domain does not exist")
        domain = Domain(
            id="dom_" + fingerprint([body.parent_id, body.name.casefold()])[:32],
            actor=Actor.from_user(user),
            **body.model_dump(),
        )
        existing = domains.get(domain.id)
        if existing is not None:
            if existing.content_fingerprint() != domain.content_fingerprint():
                raise HTTPException(status_code=409, detail="A domain with this name exists")
            return self._domain_view(existing, domains, user)
        saved = await self.repository.save("domains", domain)
        domains[saved.id] = saved
        await access.audit("CREATE_DOMAIN", "ONTOLOGY_DOMAIN", saved.name, user)
        return self._domain_view(saved, domains, user)

    async def update_domain(self, domain_id: str, body: DomainUpdate, user: dict) -> dict:
        await access.require_admin("UPDATE_DOMAIN", "ONTOLOGY_DOMAIN", domain_id, user)
        domains = await self._domains()
        current = domains.get(domain_id)
        if current is None:
            raise HTTPException(status_code=404, detail=access.NOT_FOUND)
        updated = Domain.model_validate(
            {
                **current.model_dump(),
                **body.model_dump(exclude={"expected_revision"}),
                "actor": Actor.from_user(user),
            }
        )
        saved = await self.repository.save(
            "domains", updated, expected_revision=body.expected_revision
        )
        domains[saved.id] = saved
        await access.audit("UPDATE_DOMAIN", "ONTOLOGY_DOMAIN", saved.name, user)
        return self._domain_view(saved, domains, user)

    # --- snapshots -------------------------------------------------------------

    async def _pinned(self, kind: str, model: type[R], pins: dict[str, int]) -> list[R]:
        latest = {item.id: item for item in await self.repository.current(kind, model)}
        records = []
        for record_id, revision in pins.items():
            record = latest.get(record_id)
            if record is None or record.revision != revision:
                record = await self.repository.revision(kind, record_id, revision, model)
            if record is not None:
                records.append(record)
        return records

    async def snapshot(self, scope: Scope, *, release_id: str | None = None) -> Snapshot:
        """The ``working`` state, the ``current`` release, or one named release."""
        if scope == "working":
            return Snapshot(
                release=None,
                domains=await self._domains(),
                concepts={
                    item.id: item for item in await self.repository.current("concepts", Concept)
                },
                relations=await self.repository.current("relations", Relation),
                representations=await self.repository.current("representations", Representation),
            )
        if release_id is None:
            alias = await self.releases.alias()
            if alias is None:
                return Snapshot(None, {}, {}, [], [])
            release_id = alias.release_id
        cached = self._released.get(release_id)
        if cached is not None:
            self._released.move_to_end(release_id)
            return cached
        release = await self.releases.get(release_id)
        if release.status != "PUBLISHED":
            raise HTTPException(status_code=404, detail=access.NOT_FOUND)
        snapshot = await self._release_snapshot(release)
        self._released[release_id] = snapshot
        while len(self._released) > RELEASE_CACHE_SIZE:
            self._released.popitem(last=False)
        return snapshot

    async def _release_snapshot(self, release: Release) -> Snapshot:
        pins: dict[str, dict[str, int]] = {
            "domain": {}, "concept": {}, "relation": {}, "representation": {},
        }
        for member in release.members:
            pins[member.kind][member.id] = member.revision
        return Snapshot(
            release=release,
            domains={
                item.id: item for item in await self._pinned("domains", Domain, pins["domain"])
            },
            concepts={
                item.id: item
                for item in await self._pinned("concepts", Concept, pins["concept"])
            },
            relations=await self._pinned("relations", Relation, pins["relation"]),
            representations=await self._pinned(
                "representations", Representation, pins["representation"]
            ),
        )

    @staticmethod
    def visible_concepts(snapshot: Snapshot, user: dict) -> dict[str, Concept]:
        return {
            concept_id: concept
            for concept_id, concept in snapshot.concepts.items()
            if access.can_see(snapshot.domains.get(concept.domain_id), snapshot.domains, user)
        }

    @staticmethod
    def concept_view(concept: Concept, domains: dict[str, Domain]) -> dict:
        domain = domains.get(concept.domain_id)
        path = access.qualified_path(domain, domains) if domain else concept.domain_id
        return {
            "id": concept.id,
            "revision": concept.revision,
            "qualified_name": f"{path}.{concept.slug}",
            "domain_id": concept.domain_id,
            "kind": concept.kind.value,
            "slug": concept.slug,
            "label": concept.label,
            "definition": concept.definition,
            "aliases": concept.aliases,
            "unit": concept.unit,
            "grain": concept.grain,
            "time_semantics": concept.time_semantics,
            "exclusions": concept.exclusions,
            "classification": concept.classification.value,
            "status": concept.status,
            "proposal_id": concept.proposal_id,
            "updated_at": concept.updated_at,
        }

    def _scope_info(self, snapshot: Snapshot, scope: Scope) -> dict:
        return {
            "scope": scope,
            "release_id": snapshot.release.id if snapshot.release else None,
            "manifest_hash": snapshot.release.manifest_hash if snapshot.release else None,
        }

    # --- concepts ----------------------------------------------------------------

    async def list_concepts(
        self,
        user: dict,
        *,
        scope: Scope = "current",
        domain_id: str | None = None,
        query: str | None = None,
        include_deprecated: bool = False,
        limit: int = 200,
    ) -> dict:
        snapshot = await self.snapshot(scope)
        needle = " ".join((query or "").split()).casefold()
        concepts = []
        for concept in self.visible_concepts(snapshot, user).values():
            if domain_id is not None and concept.domain_id != domain_id:
                continue
            if concept.status != "APPROVED" and not include_deprecated:
                continue
            names = (concept.slug, concept.label, *concept.aliases)
            if needle and not any(needle in name.casefold() for name in names):
                continue
            concepts.append(self.concept_view(concept, snapshot.domains))
        concepts.sort(key=lambda item: item["qualified_name"].casefold())
        bound = min(max(limit, 1), 500)
        return {
            **self._scope_info(snapshot, scope),
            "concepts": concepts[:bound],
            "truncated": len(concepts) > bound,
        }

    async def find(
        self, reference: str, user: dict, *, release_id: str | None = None
    ) -> dict:
        """A visible concept by id or qualified name (case-insensitive)."""
        snapshot = await self.snapshot("current", release_id=release_id)
        visible = self.visible_concepts(snapshot, user)
        if reference in visible:
            return self.concept_view(visible[reference], snapshot.domains)
        wanted = reference.strip().casefold()
        for concept in visible.values():
            view = self.concept_view(concept, snapshot.domains)
            if view["qualified_name"].casefold() == wanted:
                return view
        raise HTTPException(status_code=404, detail=access.NOT_FOUND)

    async def _visible(self, concept_id: str, snapshot: Snapshot, user: dict) -> Concept:
        concept = self.visible_concepts(snapshot, user).get(concept_id)
        if concept is None:
            raise HTTPException(status_code=404, detail=access.NOT_FOUND)
        return concept

    async def get_concept(
        self, concept_id: str, user: dict, *, scope: Scope = "current",
        release_id: str | None = None,
    ) -> dict:
        snapshot = await self.snapshot(scope, release_id=release_id)
        concept = await self._visible(concept_id, snapshot, user)
        history = await self.repository.history("concepts", concept_id, Concept)
        return {
            **self._scope_info(snapshot, scope),
            "concept": self.concept_view(concept, snapshot.domains),
            "history": [
                {
                    "revision": item.revision,
                    "status": item.status,
                    "label": item.label,
                    "proposal_id": item.proposal_id,
                    "changed_by": item.actor.principal,
                    "updated_at": item.updated_at,
                }
                for item in history
            ],
        }

    async def related(
        self, concept_id: str, user: dict, *, scope: Scope = "current", depth: int = 1,
        release_id: str | None = None,
    ) -> dict:
        snapshot = await self.snapshot(scope, release_id=release_id)
        await self._visible(concept_id, snapshot, user)
        visible = self.visible_concepts(snapshot, user)
        reached, edges = neighbourhood(
            concept_id, snapshot.relations,
            {item.id for item in visible.values() if item.status == "APPROVED"},
            depth=depth,
        )
        return {
            **self._scope_info(snapshot, scope),
            "concepts": [self.concept_view(visible[item], snapshot.domains) for item in reached],
            "relations": [
                {
                    "id": edge.id,
                    "source_id": edge.source_id,
                    "type": edge.type.value,
                    "target_id": edge.target_id,
                    "justification": edge.justification,
                    "meaning": RULES[edge.type].description,
                }
                for edge in edges
            ],
        }

    async def representations(
        self, concept_id: str, user: dict, *, scope: Scope = "current",
        release_id: str | None = None,
    ) -> dict:
        snapshot = await self.snapshot(scope, release_id=release_id)
        await self._visible(concept_id, snapshot, user)
        bindings = []
        for item in snapshot.representations:
            if item.concept_id != concept_id:
                continue
            # Ontology access never implies data access: only show bindings to
            # Semantic Views the caller can already see.
            if not await self.catalog.visible(item.semantic.view_id, user):
                continue
            state, _view, reason = await freshness(item, self.catalog)
            bindings.append(
                {
                    "id": item.id,
                    "revision": item.revision,
                    "semantic": item.semantic.model_dump(),
                    "metric": item.metric,
                    "purpose": item.purpose,
                    "unit": item.unit,
                    "status": item.status,
                    "status_reason": item.status_reason or reason,
                    "usable": item.status == "VALIDATED" and state == "current",
                    "validated_at": item.validated_at,
                }
            )
        return {**self._scope_info(snapshot, scope), "representations": bindings}


ontology_service = OntologyService()
