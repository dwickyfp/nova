"""Resolve business terms in a question to approved, visible concepts.

Resolution is lexical and deterministic: a concept is named in a question
when its qualified name, slug, label or one of its aliases occurs there as
whole words. The longest name wins over a name inside it. Embedding similarity
is never used to decide what an official concept is.

When one name belongs to approved concepts in more than one domain, a domain
named in the same question picks one; otherwise the result is ambiguous and the
caller must ask. Only concepts the caller may see take part, so neither the
names nor the number of hidden candidates can leak through an ambiguity.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any, Literal

from app.modules.ontology import access
from app.modules.ontology.contracts import Concept, ConceptKind, Representation
from app.modules.ontology.representations import freshness
from app.modules.ontology.service import OntologyService, Snapshot, ontology_service

_WORD = re.compile(r"[^\w]+", re.UNICODE)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
MAX_TEXT = 2000

Status = Literal["no_release", "none", "resolved", "ambiguous"]


def normalise(text: str) -> str:
    return " ".join(_WORD.sub(" ", text.casefold()).split())


def names_of(concept: Concept, qualified_name: str) -> set[str]:
    names = {
        qualified_name,
        concept.slug,
        _CAMEL.sub(" ", concept.slug),
        concept.label,
        *concept.aliases,
    }
    return {normalised for name in names if (normalised := normalise(name))}


@dataclass
class Ambiguity:
    term: str
    candidates: list[dict[str, Any]]


@dataclass
class Resolution:
    status: Status
    release_id: str | None = None
    manifest_hash: str | None = None
    concepts: list[dict[str, Any]] = field(default_factory=list)
    ambiguities: list[Ambiguity] = field(default_factory=list)
    bindings: dict[str, list[Representation]] = field(default_factory=dict)
    #: The words in the question that named each resolved concept.
    terms: dict[str, list[str]] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        """A JSON-ready projection for tools, clarifications and the API."""
        from fastapi.encoders import jsonable_encoder

        return jsonable_encoder({
            "status": self.status,
            "release_id": self.release_id,
            "manifest_hash": self.manifest_hash,
            "concepts": [
                {
                    **concept,
                    "representations": [
                        {
                            "id": item.id,
                            "semantic_view": item.semantic.view_id,
                            "version": item.semantic.version,
                            "metric": item.metric,
                            "purpose": item.purpose,
                        }
                        for item in self.bindings.get(concept["id"], [])
                    ],
                }
                for concept in self.concepts
            ],
            "ambiguities": [
                {
                    "term": item.term,
                    "candidates": item.candidates,
                    "question": "Which definition is meant: "
                    + " or ".join(candidate["qualified_name"] for candidate in item.candidates)
                    + "?",
                }
                for item in self.ambiguities
            ],
        })


class ConceptResolver:
    def __init__(self, service: OntologyService = ontology_service) -> None:
        self.service = service

    @access.with_caller
    async def resolve(
        self,
        text: str,
        user: dict,
        *,
        release_id: str | None = None,
        kinds: frozenset[ConceptKind] | None = None,
        purpose: str = "general",
        snapshot: Snapshot | None = None,
        scope_views: Collection[str] | None = None,
        for_model: bool = True,
    ) -> Resolution:
        """Resolve the business terms in ``text`` for ``user``.

        A resolution goes into a model prompt unless ``for_model`` is False
        (the ontology pages, read by a person): then a concept whose
        classification keeps it from models is left out, as if absent.

        ``scope_views`` are the Semantic Views the asking agent (or open
        dashboard) works with. Their bound concepts name its business domains;
        a term then means only what it means in those domains, their parents
        or their children, unless the question names another domain outright.
        Without bindings in scope, every domain the role can see counts.
        """
        snapshot = snapshot or await self.service.snapshot("current", release_id=release_id)
        if snapshot.release is None:
            return Resolution(status="no_release")
        resolution = Resolution(
            status="none",
            release_id=snapshot.release.id,
            manifest_hash=snapshot.release.manifest_hash,
        )
        visible = self.service.visible_concepts(snapshot, user)
        question = f" {normalise(text[:MAX_TEXT])} "
        in_scope = self._scope(snapshot, visible, scope_views, set(question.split()))
        concepts = {
            concept_id: concept
            for concept_id, concept in visible.items()
            if concept.status == "APPROVED"
            and (kinds is None or concept.kind in kinds)
            and in_scope(concept.domain_id)
            and (not for_model or access.model_may_see(
                concept.classification, snapshot.domains.get(concept.domain_id),
                snapshot.domains, user,
            ))
        }
        views = {
            concept_id: self.service.concept_view(concept, snapshot.domains)
            for concept_id, concept in concepts.items()
        }
        index: dict[str, set[str]] = {}
        for concept_id, concept in concepts.items():
            for name in names_of(concept, views[concept_id]["qualified_name"]):
                index.setdefault(name, set()).add(concept_id)

        taken: list[tuple[int, int]] = []
        matches: list[tuple[str, set[str]]] = []
        for name in sorted(index, key=lambda item: (-len(item), item)):
            needle = f" {name} "
            start = question.find(needle)
            while start != -1:
                span = (start, start + len(needle))
                if not any(span[0] < end and begin < span[1] for begin, end in taken):
                    taken.append(span)
                    matches.append((name, index[name]))
                    break
                start = question.find(needle, start + 1)

        domain_words = {
            domain_id: {normalise(part) for part in access.qualified_path(domain, snapshot.domains)
                        .split(".")}
            for domain_id, domain in snapshot.domains.items()
        }
        words = set(question.split())
        selected: dict[str, None] = {}
        for term, candidate_ids in matches:
            candidates = sorted(candidate_ids)
            if len(candidates) > 1:
                hinted = [
                    item for item in candidates
                    if domain_words.get(concepts[item].domain_id, set()) & words
                ]
                candidates = hinted or candidates
            if len(candidates) == 1:
                selected.setdefault(candidates[0], None)
                resolution.terms.setdefault(candidates[0], []).append(term)
            else:
                resolution.ambiguities.append(
                    Ambiguity(
                        term=term,
                        candidates=[
                            {
                                "id": item,
                                "qualified_name": views[item]["qualified_name"],
                                "label": views[item]["label"],
                                "definition": views[item]["definition"],
                            }
                            for item in candidates
                        ],
                    )
                )
        resolution.concepts = [views[item] for item in selected]
        for concept_id in selected:
            resolution.bindings[concept_id] = await self._usable(
                snapshot, concept_id, user, purpose
            )
        if resolution.ambiguities:
            resolution.status = "ambiguous"
        elif resolution.concepts:
            resolution.status = "resolved"
        return resolution

    def _scope(
        self,
        snapshot: Snapshot,
        visible: dict[str, Any],
        scope_views: Collection[str] | None,
        words: set[str],
    ) -> Callable[[str], bool]:
        """Which domains a scoped turn may draw meanings from (see ``resolve``)."""
        if not scope_views:
            return lambda _domain_id: True
        views = {str(view) for view in scope_views}
        bound = {
            visible[item.concept_id].domain_id
            for item in snapshot.representations
            if item.status == "VALIDATED"
            and item.semantic.view_id in views
            and item.concept_id in visible
        }
        if not bound:
            return lambda _domain_id: True
        paths = {
            domain_id: access.qualified_path(domain, snapshot.domains)
            for domain_id, domain in snapshot.domains.items()
        }
        home = {paths[domain_id] for domain_id in bound if domain_id in paths}

        def related(path: str) -> bool:
            return any(
                path == own or path.startswith(own + ".") or own.startswith(path + ".")
                for own in home
            )

        def named(path: str) -> bool:
            # The question names this domain outright ("HR profit").
            return bool(path) and normalise(path.split(".")[-1]) in words

        def allowed(domain_id: str) -> bool:
            path = paths.get(domain_id, "")
            return related(path) or named(path)

        return allowed

    async def _usable(
        self, snapshot: Snapshot, concept_id: str, user: dict, purpose: str
    ) -> list[Representation]:
        usable = []
        for item in snapshot.representations:
            if (
                item.concept_id != concept_id
                or item.status != "VALIDATED"
                or item.purpose not in {purpose, "general"}
            ):
                continue
            if not await self.service.catalog.visible(item.semantic.view_id, user):
                continue
            state, _view, _reason = await freshness(item, self.service.catalog)
            if state != "stale":
                usable.append(item)
        return sorted(usable, key=lambda item: (item.purpose != purpose, item.id))


concept_resolver = ConceptResolver()
