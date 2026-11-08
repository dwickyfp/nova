"""Immutable ontology releases and the ``current`` alias.

A release is a manifest of exact revisions. Publishing moves the ``current``
alias with a compare-and-swap; rolling back moves it to an earlier published
release. Neither deletes a revision, so an old answer can still be traced to
the meaning it used.
"""

from __future__ import annotations

import builtins

from fastapi import HTTPException
from pydantic import Field

from app.modules.intelligence.contracts import Contract, utc_now
from app.modules.ontology import access
from app.modules.ontology.contracts import (
    CURRENT_ALIAS,
    Actor,
    Alias,
    Concept,
    Domain,
    Relation,
    Release,
    ReleaseMember,
    Representation,
    manifest_hash,
)
from app.modules.ontology.relations import graph_issues
from app.modules.ontology.repository import OntologyRepository, ontology_repository
from app.modules.ontology.representations import (
    SemanticCatalog,
    SemanticViewCatalog,
    freshness,
)
from app.modules.ontology.stewardship import naming_issues


class ReleasePrepare(Contract):
    note: str = Field(default="", max_length=2000)


class AliasMove(Contract):
    reason: str = Field(min_length=1, max_length=2000)


def _member(kind, record) -> ReleaseMember:
    return ReleaseMember(
        kind=kind, id=record.id, revision=record.revision,
        fingerprint=record.content_fingerprint(),
    )


class ReleaseService:
    def __init__(
        self,
        repository: OntologyRepository = ontology_repository,
        catalog: SemanticCatalog | None = None,
    ) -> None:
        self.repository = repository
        self.catalog: SemanticCatalog = catalog or SemanticViewCatalog()

    async def alias(self) -> Alias | None:
        return await self.repository.latest("aliases", CURRENT_ALIAS, Alias)

    async def get(self, release_id: str) -> Release:
        release = await self.repository.latest("releases", release_id, Release)
        if release is None:
            raise HTTPException(status_code=404, detail=access.NOT_FOUND)
        return release

    async def list(self) -> builtins.list[Release]:
        releases = await self.repository.current("releases", Release)
        return sorted(releases, key=lambda item: item.sequence, reverse=True)

    async def prepare(self, body: ReleasePrepare, user: dict) -> Release:
        await access.require_admin("PREPARE", "ONTOLOGY_RELEASE", "new", user)
        actor = Actor.from_user(user)
        alias = await self.alias()
        domains = [item for item in await self.repository.current("domains", Domain)]
        concepts = await self.repository.current("concepts", Concept)
        relations = await self.repository.current("relations", Relation)
        representations = await self.repository.current("representations", Representation)

        issues: list[str] = []
        warnings: list[str] = []
        by_id = {item.id: item for item in domains}
        active_domains = [item for item in domains if item.status == "ACTIVE"]
        for domain in active_domains:
            if access.chain(domain, by_id) is None:
                issues.append(f"Domain {domain.name} has a missing or circular parent")
            elif any(item.status != "ACTIVE" for item in access.chain(domain, by_id) or []):
                issues.append(f"Domain {domain.name} sits under a retired domain")
        approved = {
            item.id: item for item in concepts
            if item.status == "APPROVED" and item.domain_id in by_id
            and by_id[item.domain_id].status == "ACTIVE"
        }
        for concept in concepts:
            if concept.status == "APPROVED" and concept.id not in approved:
                issues.append(f"Concept {concept.slug} belongs to a missing or retired domain")
        active_relations = [item for item in relations if item.status == "ACTIVE"]
        issues.extend(graph_issues(approved, active_relations))
        issues.extend(naming_issues(approved, by_id))
        bound: list[Representation] = []
        for item in representations:
            if item.status != "VALIDATED" or item.concept_id not in approved:
                continue
            state, _view, reason = await freshness(item, self.catalog)
            if state == "current":
                bound.append(item)
            else:
                warnings.append(
                    f"Binding of {approved[item.concept_id].slug} to {item.metric} left out: "
                    + (reason or "the view was republished; run the drift check")
                )

        from app.modules.ontology.lint import lint

        # Advice, never a block: a steward decides whether the wording or the formula is wrong.
        warnings.extend(
            f"Meaning check ({finding.rule}) {finding.concept}: {finding.message}"
            for finding in await lint(user, self.catalog, self.repository)
        )

        members = [
            *(_member("domain", item) for item in active_domains),
            *(_member("concept", item) for item in approved.values()),
            *(_member("relation", item) for item in active_relations),
            *(_member("representation", item) for item in bound),
        ]
        digest = manifest_hash(members)
        current = (
            await self.repository.latest("releases", alias.release_id, Release) if alias else None
        )
        if current is not None and current.manifest_hash == digest:
            warnings.append("Nothing changed since the current release")
        existing = await self.list()
        sequence = (existing[0].sequence + 1) if existing else 1
        release = Release(
            id=f"ont_rel_{sequence:04d}", actor=actor, sequence=sequence,
            base_release_id=alias.release_id if alias else None, members=members,
            manifest_hash=digest, status="BLOCKED" if issues else "PREPARED",
            issues=issues[:200], warnings=warnings[:200], note=body.note,
        )
        saved = await self.repository.save("releases", release)
        await access.audit(
            "PREPARE", "ONTOLOGY_RELEASE", saved.id, user,
            status="SUCCESS" if not issues else "BLOCKED",
        )
        return saved

    async def _still_valid(self, release: Release) -> builtins.list[str]:
        problems: builtins.list[str] = []
        for member in release.members:
            if member.kind != "representation":
                continue
            record = await self.repository.revision(
                "representations", member.id, member.revision, Representation
            )
            if record is None:
                problems.append(f"Binding {member.id} is missing")
                continue
            state, _view, reason = await freshness(record, self.catalog)
            if state != "current":
                problems.append(reason or f"Binding {member.id} needs re-validation")
        return problems

    async def publish(self, release_id: str, user: dict) -> Release:
        await access.require_admin("PUBLISH", "ONTOLOGY_RELEASE", release_id, user)
        actor = Actor.from_user(user)
        release = await self.get(release_id)
        alias = await self.alias()
        if alias is not None and alias.release_id == release.id:
            if release.status != "PUBLISHED":  # finish an interrupted publication
                release = await self._mark_published(release, actor)
            return release
        if release.status == "BLOCKED":
            raise HTTPException(status_code=422, detail={"issues": release.issues})
        if release.status == "PUBLISHED":
            raise HTTPException(
                status_code=409, detail="Release was published before; roll back to it instead"
            )
        if release.base_release_id != (alias.release_id if alias else None):
            raise HTTPException(
                status_code=409, detail="Another release was published; prepare again"
            )
        problems = await self._still_valid(release)
        if problems:
            raise HTTPException(status_code=409, detail={"issues": problems[:50]})
        await self._move(alias, release.id, f"Publish {release.id}", actor)
        release = await self._mark_published(release, actor)
        await access.audit("PUBLISH", "ONTOLOGY_RELEASE", release.id, user)
        return release

    async def rollback(self, release_id: str, body: AliasMove, user: dict) -> Alias:
        await access.require_admin("ROLLBACK", "ONTOLOGY_RELEASE", release_id, user)
        actor = Actor.from_user(user)
        release = await self.get(release_id)
        alias = await self.alias()
        if release.status != "PUBLISHED":
            raise HTTPException(status_code=409, detail="Only a published release can be restored")
        if alias is not None and alias.release_id == release.id:
            return alias
        moved = await self._move(alias, release.id, body.reason, actor)
        await access.audit("ROLLBACK", "ONTOLOGY_RELEASE", release.id, user)
        return moved

    async def _move(self, alias: Alias | None, release_id: str, reason: str, actor: Actor) -> Alias:
        return await self.repository.save(
            "aliases",
            Alias(
                id=CURRENT_ALIAS, actor=actor, release_id=release_id,
                previous_release_id=alias.release_id if alias else None, reason=reason,
            ),
            expected_revision=alias.revision if alias else 0,
        )

    async def _mark_published(self, release: Release, actor: Actor) -> Release:
        return await self.repository.save(
            "releases",
            release.model_copy(
                update={"actor": actor, "status": "PUBLISHED", "published_at": utc_now()}
            ),
            expected_revision=release.revision,
        )


release_service = ReleaseService()
