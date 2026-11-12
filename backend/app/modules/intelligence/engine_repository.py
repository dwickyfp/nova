"""Scoped, revision-checked persistence for the Intelligence domain.

Records live in the PostgreSQL control metadata (``nova_meta``). Every call
runs in one actor-bound transaction: by default the actor is the record's
scope, which services build from a validated session (or, for scheduled
handlers, from the account the schedule runs for). Reads of another
principal's record that a service has already authorized (a reviewer's
action, a granted decision, a proven story) bind the reader instead and go
through the narrow shared lookups of the store. The store enforces the
revision chain, so a stale or forked write fails with 409 without a Redis lock.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, TypeVar

from fastapi import HTTPException

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.core.redis import session_store
from app.modules.access_control.security_context import SecurityContext
from app.modules.intelligence.contracts import NewsItem, Record, Scope, fingerprint, utc_now
from app.modules.intelligence.metadata_repository import (
    IntelligenceMetadataRepository,
    intelligence_metadata,
    scope_actor,
)
from app.modules.task_orchestration.transport import LeaderLock

R = TypeVar("R", bound=Record)
_RECONCILE = "Concurrent revisions require reconciliation"


@asynccontextmanager
async def metadata_lock(key: str, *, timeout_seconds: int = 20):
    """A short Redis lease that serializes a multi-step workflow (not a record write)."""
    client = session_store._redis
    if client is None:
        raise HTTPException(status_code=503, detail="Metadata coordination is unavailable")
    if not 1 <= timeout_seconds <= 120:
        raise ValueError("Metadata coordination deadline must be within 1..120 seconds")
    lock = LeaderLock(
        client, key=f"nova:intelligence:write:{fingerprint(key)}",
        ttl_seconds=timeout_seconds + 10,
    )
    if not await lock.acquire():
        raise HTTPException(status_code=409, detail="This record is being updated; retry")
    try:
        async with asyncio.timeout(timeout_seconds):
            yield lock
    finally:
        await lock.release()


def _owned_by(row: dict[str, Any], scope: Scope) -> bool:
    return (
        row.get("principal") == scope.principal
        and row.get("active_role") == scope.active_role
        and int(row.get("security_context_version") or 0) == scope.security_context_version
    )


def _iso(value) -> str:
    return value.isoformat().replace("+00:00", "Z")


@asynccontextmanager
async def _reconciled():
    """Keep the source's 409 detail for a branched revision chain."""
    try:
        yield
    except MetadataConflict:
        raise HTTPException(status_code=409, detail=_RECONCILE) from None


class IntelligenceRepository:
    @staticmethod
    def _session(actor: SecurityContext, *, read_only: bool = False):
        return intelligence_metadata(actor, read_only=read_only)

    async def _read(
        self, actor: SecurityContext, call,
    ):
        async with _reconciled(), self._session(actor, read_only=True) as repo:
            return await call(repo)

    async def recent_incident(self, candidate: NewsItem, cooldown_hours: int) -> NewsItem | None:
        scope = candidate.scope
        span = timedelta(hours=cooldown_hours)

        async def read(repo: IntelligenceMetadataRepository):
            return await repo.current_records(
                "news", scope=scope_actor(scope), match={"monitor_id": candidate.monitor_id},
                between=("window.end", _iso(candidate.window.end - span),
                         _iso(candidate.window.end + span)),
                limit=101,
            )

        rows = await self._read(scope_actor(scope), read)
        if len(rows) > 100:
            raise HTTPException(status_code=409, detail="Incident history requires reconciliation")
        for row in rows:
            prior = NewsItem.model_validate(row["payload"])
            if (
                prior.semantic == candidate.semantic
                and (prior.change > 0) == (candidate.change > 0)
                and abs(prior.window.end - candidate.window.end) < span
            ):
                return prior
        return None

    async def get(
        self,
        kind: str,
        record_id: str,
        scope: Scope,
        model: type[R],
        *,
        revision: int | None = None,
        actor: SecurityContext | None = None,
    ) -> R | None:
        payload = await self.get_payload(
            kind, record_id, scope, revision=revision, actor=actor,
        )
        return model.model_validate(payload) if payload is not None else None

    async def get_payload(
        self, kind: str, record_id: str, scope: Scope, *, revision: int | None = None,
        actor: SecurityContext | None = None,
    ) -> dict[str, Any] | None:
        """The stored JSON of one record of ``scope`` (newest or the given revision).

        ``actor`` reads a deployment-readable record (an edition or story) as
        the reader; scoped records can only be read as their own scope.
        """
        row = await self._read(
            actor or scope_actor(scope),
            lambda repo: repo.get_record(kind, record_id, revision=revision),
        )
        return row["payload"] if row is not None and _owned_by(row, scope) else None

    async def shared_decision(
        self, record_id: str, owner_name: str, model: type[R], *, actor: SecurityContext,
    ) -> R | None:
        """A decision named with its owner, read as ``actor`` after the grant check."""
        payload = await self._read(
            actor, lambda repo: repo.shared_decision(record_id, owner_name),
        )
        return model.model_validate(payload) if payload is not None else None

    async def related(self, kind: str, decision_id: str, scope: Scope, model: type[R]) -> list[R]:
        if kind not in {"events", "outcomes", "actions", "action_events"}:
            raise ValueError("Unsupported lineage relation")
        try:
            rows = await self._read(
                scope_actor(scope),
                lambda repo: repo.current_records(
                    kind, scope=scope_actor(scope), match={"decision_id": decision_id},
                    limit=101,
                ),
            )
        except HTTPException as exc:
            raise HTTPException(
                status_code=409, detail="Concurrent lineage revisions require reconciliation"
            ) from exc
        if len(rows) > 100:
            raise HTTPException(
                status_code=422, detail="Lineage exceeds the per-request record bound"
            )
        return [model.model_validate(row["payload"]) for row in rows]

    async def action_for_review(
        self, record_id: str, model: type[R], *, actor: SecurityContext,
    ) -> R | None:
        # Only the review service uses this lookup, then authorizes the linked
        # decision and the reviewer's role before returning any record data.
        payload = await self._read(actor, lambda repo: repo.action_for_review(record_id))
        return model.model_validate(payload) if payload is not None else None

    async def latest_edition(
        self, view_id: str, scope: Scope, model: type[R], *, actor: SecurityContext | None = None,
    ) -> R | None:
        """The newest edition the view's execution account pressed."""
        payload = await self._read(
            actor or scope_actor(scope),
            lambda repo: repo.latest_edition(
                view_id, scope.principal, scope.active_role, scope.security_context_version,
            ),
        )
        return model.model_validate(payload) if payload is not None else None

    async def edition_stories(
        self, story_ids: list[str], scope: Scope, model: type[R], *,
        actor: SecurityContext | None = None,
    ) -> list[R]:
        """The current revision of each story of one edition, in one read."""
        if not story_ids:
            return []
        rows = await self._read(
            actor or scope_actor(scope),
            lambda repo: repo.current_records(
                "stories", scope=scope_actor(scope), ids=story_ids, limit=len(story_ids),
            ),
        )
        return [model.model_validate(row["payload"]) for row in rows]

    async def shared_story(
        self, record_id: str, model: type[R], *, actor: SecurityContext,
    ) -> R | None:
        # Only the newsroom reader uses this lookup. It returns nothing to a
        # caller until that caller reproduces every access proof of the story.
        row = await self._read(actor, lambda repo: repo.get_record("stories", record_id))
        return model.model_validate(row["payload"]) if row is not None else None

    async def page(
        self,
        kind: str,
        scope: Scope,
        model: type[R],
        *,
        after: str = "",
        limit: int = 50,
        endpoint: str | None = None,
        search: str | None = None,
    ) -> list[R]:
        if search is not None and kind != "nodes":
            raise ValueError("Text search is only available for context nodes")
        if endpoint is not None and kind != "edges":
            raise ValueError("Endpoint filtering is only available for graph edges")
        rows = await self._read(
            scope_actor(scope),
            lambda repo: repo.current_records(
                kind, scope=scope_actor(scope), after=after, endpoint=endpoint, search=search,
                limit=min(max(limit, 1), 101),
            ),
        )
        return [model.model_validate(row["payload"]) for row in rows]

    async def save(self, kind: str, record: R, *, expected_revision: int = 0) -> R:
        """Append ``record`` as the next revision under its own scope.

        Saving content equal to the newest revision returns that revision. A
        stale ``expected_revision`` or a branched chain is a 409; an id that
        belongs to another scope is not disclosed (404).
        """
        actor = scope_actor(record.scope)
        try:
            async with self._session(actor) as repo:
                current = await repo.get_record(kind, record.id)
                if current is not None:
                    if not _owned_by(current, record.scope):
                        raise HTTPException(status_code=404, detail="Record unavailable")
                    old = type(record).model_validate(current["payload"])
                    ignored = {"revision", "created_at", "updated_at"}
                    if old.model_dump(exclude=ignored) == record.model_dump(exclude=ignored):
                        return old
                    if old.revision != expected_revision:
                        raise HTTPException(
                            status_code=409, detail="Record changed; reload before editing"
                        )
                    record = record.model_copy(update={"created_at": old.created_at})
                elif expected_revision:
                    raise HTTPException(
                        status_code=409, detail="Record no longer matches this revision"
                    )
                record = record.model_copy(
                    update={"revision": expected_revision + 1, "updated_at": utc_now()}
                )
                stored = await repo.save_record(
                    kind, record.model_dump(mode="json"), expected_revision=expected_revision,
                )
                return type(record).model_validate(stored)
        except MetadataPermissionDenied:
            # The id exists under another scope; the store refused the append.
            raise HTTPException(status_code=404, detail="Record unavailable") from None
        except MetadataConflict:
            raise HTTPException(
                status_code=409, detail="Record changed; reload before editing"
            ) from None


intelligence_repository = IntelligenceRepository()
