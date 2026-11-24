"""Remove Workspace objects that no live file or version references (NDV-STORE-009).

Every file save writes a new object under ``<base>/<namespace>/.objects/`` and
then publishes it in one metadata transaction; a save that fails removes its
object at once. A crash between the write and the commit, a failed removal or an
unknown commit outcome can still leave an object behind. This pass runs in the
maintenance worker under a Redis leader lock and:

1. lists ``.objects`` keys last written before the retention cut-off, so no write
   that may still commit is considered (a write's transaction is bounded far
   below the retention);
2. asks PostgreSQL, as the SERVICE principal ``nova-workspace-objects`` on the
   maintenance login (0060), which of them a live entry or a reserved or
   published version references, reading keys only;
3. removes the others, a bounded number per pass.

An unknown commit outcome resolves itself: a committed save references its
object and keeps it; one that never committed leaves an unreferenced object that
is removed after the retention.

Dashboard images (0064) follow the same rule under ``.viz-assets/``: an image
is written before its registry row commits, and an object no row holds is
removed after the retention. Other keys outside ``.objects`` (imported files,
notebook outputs) are never touched.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.core.exceptions import StorageError
from app.modules.access_control.security_context import SecurityContext

logger = logging.getLogger(__name__)

RECONCILER = SecurityContext(
    "nova-workspace-objects", "WORKSPACE_RECONCILER", principal_type="SERVICE",
)
RETENTION = timedelta(hours=24)
INTERVAL_SECONDS = 6 * 3600
MAX_REMOVALS = 1000
BATCH = 500
_MARKERS = ("/.objects/", "/.viz-assets/")
_LOCK = "nova:workspace-objects:reconciler"


@dataclass(frozen=True)
class StoredObject:
    key: str
    last_modified: datetime


class ObjectListing(Protocol):
    def list_objects(self) -> Iterable[StoredObject]: ...
    async def delete(self, key: str) -> None: ...


#: Returns the subset of ``keys`` that live metadata still references.
Referenced = Callable[[Sequence[str]], Awaitable[set[str]]]


@dataclass
class Pass:
    examined: int = 0
    referenced: int = 0
    removed: int = 0
    failed: int = 0
    deferred: int = 0
    removed_keys: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {"examined": self.examined, "referenced": self.referenced,
                "removed": self.removed, "failed": self.failed, "deferred": self.deferred}


def candidates(objects: Iterable[StoredObject], *, now: datetime,
               retention: timedelta = RETENTION) -> list[str]:
    """Saved-object keys old enough that no write of them can still commit."""
    cutoff = now - retention
    return [item.key for item in objects
            if any(marker in item.key for marker in _MARKERS) and item.last_modified < cutoff]


async def reconcile(store: ObjectListing, referenced: Referenced, *,
                    now: datetime | None = None, retention: timedelta = RETENTION,
                    limit: int = MAX_REMOVALS) -> Pass:
    """One pass: remove old ``.objects`` keys that no live metadata references."""
    keys = candidates(await asyncio.to_thread(lambda: list(store.list_objects())),
                      now=now or datetime.now(UTC), retention=retention)
    result = Pass(examined=len(keys))
    for start in range(0, len(keys), BATCH):
        batch = keys[start:start + BATCH]
        kept = await referenced(batch)
        result.referenced += len(kept)
        for key in batch:
            if key in kept:
                continue
            if result.removed + result.failed >= limit:
                result.deferred += 1
                continue
            try:
                await store.delete(key)
            except StorageError:
                result.failed += 1
                continue
            result.removed += 1
            result.removed_keys.append(key)
    return result


async def referenced_in(connection: Any, keys: Sequence[str]) -> set[str]:
    """Keys a live entry, a reserved/published version (0060) or a dashboard image (0064) holds."""
    if not keys:
        return set()
    cursor = await connection.execute(
        "SELECT object_key FROM nova_meta.config_workspace_entries "
        "WHERE object_key = ANY(%s) AND is_deleted IS NOT TRUE "
        "UNION SELECT object_key FROM nova_meta.config_workspace_file_versions "
        "WHERE object_key = ANY(%s) AND NOT is_deleted AND state <> 'abandoned' "
        "UNION SELECT object_key FROM nova_meta.viz_assets WHERE object_key = ANY(%s)",
        (list(keys), list(keys), list(keys)),
    )
    return {str(row["object_key"]) for row in await cursor.fetchall()}


async def referenced_keys(keys: Sequence[str]) -> set[str]:
    from app.core.metadata_scope import metadata_transaction

    async with metadata_transaction(RECONCILER, read_only=True) as scope:
        return await referenced_in(scope.connection, keys)


class WorkspaceObjects:
    """The Workspace storage owner's objects, listed under its base prefix."""

    def __init__(self, service: Any) -> None:
        self.service = service

    def list_objects(self) -> Iterable[StoredObject]:
        from app.core.config import load_nova_app_config

        prefix = load_nova_app_config().workspace.base_prefix.strip("/")
        for item in self.service.list_internal_objects(f"{prefix}/" if prefix else ""):
            yield StoredObject(str(item["Key"]), item["LastModified"])

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.service._delete_object, key)


async def reconcile_once() -> Pass:
    from app.modules.workspaces.service import workspace_service

    result = await reconcile(WorkspaceObjects(workspace_service), referenced_keys)
    if result.removed or result.failed or result.deferred:
        logger.info("Workspace object reconciliation: %s", result.summary())
    return result


async def run_once_if_leader() -> bool:
    from app.core.redis import session_store
    from app.modules.task_orchestration.transport import LeaderLock

    client = session_store._redis
    if client is None:
        return False
    lock = LeaderLock(client, key=_LOCK, ttl_seconds=3600)
    if not await lock.acquire():
        return False
    try:
        await reconcile_once()
    finally:
        await lock.release()
    return True


async def run(stop: asyncio.Event) -> None:
    """Reconcile on an interval until ``stop`` is set; the first pass runs at once."""
    while not stop.is_set():
        try:
            await run_once_if_leader()
        except Exception:
            logger.warning("Workspace object reconciliation pass failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL_SECONDS)
        except TimeoutError:
            continue
