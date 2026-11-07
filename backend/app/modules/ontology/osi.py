"""Exchange a published ontology release as Open Semantic Interchange (Ossie 0.1.1) documents.

Ossie describes semantic models: datasets, metrics, relationships. A business
ontology adds meaning to them, so a release is exported as one Ossie document
per Semantic View its metrics are bound to:

* every bound metric carries its official meaning where any Ossie reader looks:
  the approved definition as ``description``, the concept's aliases among
  ``synonyms``, and the concept in ``ai_context``;
* the document's ``ai_context.nova_ontology`` holds the complete release the
  caller may see (domains, concepts, relations, bindings) with each record's
  revision, so Nova can read it back without losing anything.

Reading back recomputes every record's content fingerprint and the release's
manifest hash from the documents alone. The same hash proves nothing was lost.
Only what the caller may see is exported; a partial view says so.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Literal, cast

from fastapi import HTTPException
from pydantic import Field

from app.modules.intelligence.contracts import Contract
from app.modules.ontology import access
from app.modules.ontology.contracts import (
    Concept,
    Domain,
    Relation,
    ReleaseMember,
    Representation,
    manifest_hash,
)
from app.modules.ontology.service import OntologyService, ontology_service

OSSIE_VERSION = "0.1.1"
EXTENSION = "nova_ontology"
Kind = Literal["domain", "concept", "relation", "representation"]
KINDS: dict[Kind, type[Domain | Concept | Relation | Representation]] = {
    "domain": Domain, "concept": Concept, "relation": Relation, "representation": Representation,
}


class OsiImport(Contract):
    documents: list[str] = Field(min_length=1, max_length=50)


def _member(kind: Kind, record: Any) -> ReleaseMember:
    return ReleaseMember(kind=kind, id=record.id, revision=record.revision,
                         fingerprint=record.content_fingerprint())


async def export_release(
    user: dict, release_id: str | None = None, service: OntologyService = ontology_service,
) -> dict[str, Any]:
    snapshot = await service.snapshot("current", release_id=release_id)
    if snapshot.release is None:
        raise HTTPException(status_code=404, detail=access.NOT_FOUND)
    domains = snapshot.domains
    seen_domains = {key: item for key, item in domains.items()
                    if access.can_see(item, domains, user)}
    concepts = {key: item for key, item in snapshot.concepts.items()
                if item.domain_id in seen_domains}
    relations = [item for item in snapshot.relations
                 if item.source_id in concepts and item.target_id in concepts]
    views: dict[str, Any] = {}
    bindings = []
    for item in snapshot.representations:
        if item.concept_id not in concepts:
            continue
        view_id = item.semantic.view_id
        if view_id not in views:
            views[view_id] = (await service.catalog.published(view_id)
                              if await service.catalog.visible(view_id, user) else None)
        if views[view_id] is not None:
            bindings.append(item)
    released = {(member.kind, member.id) for member in snapshot.release.members}
    records: dict[Kind, list[Any]] = {
        "domain": list(seen_domains.values()), "concept": list(concepts.values()),
        "relation": relations, "representation": bindings,
    }
    members = [_member(kind, record) for kind, items in records.items() for record in items]
    complete = {(m.kind, m.id) for m in members} == released
    bundle = {
        "release_id": snapshot.release.id,
        "manifest_hash": snapshot.release.manifest_hash if complete else manifest_hash(members),
        "complete": complete,
        "records": {kind: [record.model_dump(mode="json") for record in items]
                    for kind, items in records.items()},
    }
    documents = []
    for view_id, view in sorted(views.items()):
        if view is None:
            continue
        documents.append({"view_id": view_id, "name": view.name,
                          "document": _document(view, concepts, bindings, bundle)})
    await access.audit("EXPORT", "ONTOLOGY_RELEASE", snapshot.release.id, user)
    return {"release_id": snapshot.release.id, "manifest_hash": bundle["manifest_hash"],
            "complete": complete, "documents": documents}


def _document(view: Any, concepts: dict[str, Concept], bindings: list[Representation],
              bundle: dict) -> dict[str, Any]:
    from app.modules.agents.semantic.serialize import to_ossie_document

    document = to_ossie_document(copy.deepcopy(view.definition))
    document["version"] = OSSIE_VERSION
    meaning = {item.metric: concepts[item.concept_id] for item in bindings
               if item.semantic.view_id == view.view_id}
    for metric in document.get("metrics") or []:
        concept = meaning.get(metric.get("name"))
        if concept is None:
            continue
        synonyms = list(metric.get("synonyms") or [])
        metric["synonyms"] = synonyms + [alias for alias in concept.aliases
                                         if alias not in synonyms]
        metric["description"] = concept.definition
        context = metric.get("ai_context")
        metric["ai_context"] = {
            **(context if isinstance(context, dict) else
               {"instructions": context} if context else {}),
            "nova_concept": {"id": concept.id, "revision": concept.revision,
                             "label": concept.label, "kind": concept.kind.value},
        }
    context = document.get("ai_context")
    document["ai_context"] = {
        **(context if isinstance(context, dict) else
           {"instructions": context} if context else {}),
        EXTENSION: bundle,
    }
    return document


def read_documents(texts: list[str]) -> dict[str, Any]:
    """Records and manifest hash read back from exported Ossie documents."""
    from app.modules.agents.semantic.ossie import parse_ossie

    bundles = []
    for text in texts:
        parsed = parse_ossie(text, raise_on_error=False)
        if not parsed.valid:
            raise HTTPException(status_code=422, detail="; ".join(parsed.errors)[:500])
        context = parsed.model.get("ai_context")
        bundle = context.get(EXTENSION) if isinstance(context, dict) else None
        if not isinstance(bundle, dict):
            raise HTTPException(status_code=422,
                                detail="The document carries no Nova ontology extension")
        bundles.append(bundle)
    releases = {bundle.get("release_id") for bundle in bundles}
    if len(releases) != 1:
        raise HTTPException(status_code=422, detail="Documents come from different releases")
    members: dict[tuple[str, str], ReleaseMember] = {}
    counts: dict[str, int] = {}
    for bundle in bundles:
        for kind, model in KINDS.items():
            for raw in (bundle.get("records") or {}).get(kind) or []:
                record = cast(Any, model).model_validate(raw)
                members[(kind, record.id)] = _member(kind, record)
    for member_kind, _id in members:
        counts[member_kind] = counts.get(member_kind, 0) + 1
    recomputed = manifest_hash(list(members.values()))
    declared = bundles[0].get("manifest_hash")
    return {"release_id": releases.pop(), "records": counts, "manifest_hash": recomputed,
            "declared_manifest_hash": declared, "lossless": recomputed == declared,
            "complete": all(bundle.get("complete") for bundle in bundles)}


def as_text(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, default=str)
