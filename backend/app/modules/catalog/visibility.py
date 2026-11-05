"""What one caller can see, asked of the engine as that caller, briefly cached.

The engine decides visibility on the caller's own session under their active
role (:func:`app.modules.explorer.repository.caller_metadata`), so Ranger
filters every listing. This module only reuses an answer for a short time.

The cache is partitioned by :meth:`SecurityContext.cache_partition`, which
covers the principal, active role, and security-context version, plus a global
epoch. Switching role, a new security context, any managed access change, or a
catalog create/alter/drop/refresh therefore never reads an older answer. Only
names are cached, never data. Without Redis, every call asks the engine.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from app.core.config import settings
from app.core.redis import session_store
from app.modules.access_control.security_context import SecurityContext

log = logging.getLogger(__name__)

#: Opens the caller's engine session (``explorer.repository.caller_metadata``).
Opener = Callable[[dict], AbstractAsyncContextManager[Any]]

_PREFIX = "nova:catalog:visible:"
_EPOCH = "nova:catalog:visible:epoch"


def _client() -> Any:
    return session_store._redis if settings.CATALOG_VISIBILITY_CACHE_SECONDS > 0 else None


async def _key(user: dict, scope: str) -> str | None:
    client = _client()
    if client is None:
        return None
    epoch = str(await client.get(_EPOCH) or "0")
    partition = SecurityContext.from_session(user).cache_partition(policy_fingerprint=epoch)
    return f"{_PREFIX}{partition}:{scope}"


async def bump() -> None:
    """Retire every cached answer, for every caller."""
    client = session_store._redis
    if client is None:
        return
    try:
        await client.incr(_EPOCH)
    except Exception:  # noqa: BLE001 - the short TTL still bounds staleness
        log.warning("Could not retire cached catalog visibility", exc_info=True)


async def _cached(user: dict, scope: str, load):  # type: ignore[no-untyped-def]
    try:
        key = await _key(user, scope)
    except Exception:  # noqa: BLE001 - a cache fault never blocks the answer
        log.debug("Catalog visibility cache unavailable", exc_info=True)
        key = None
    if key is not None:
        try:
            raw = await session_store._redis.get(key)  # type: ignore[union-attr]
            if raw:
                return json.loads(raw)
        except Exception:  # noqa: BLE001
            log.debug("Catalog visibility cache read failed", exc_info=True)
    value = await load()
    if key is not None:
        try:
            await session_store._redis.set(  # type: ignore[union-attr]
                key, json.dumps(value), ex=settings.CATALOG_VISIBILITY_CACHE_SECONDS
            )
        except Exception:  # noqa: BLE001
            log.debug("Catalog visibility cache write failed", exc_info=True)
    return value


def _opener(opener: Opener | None) -> Opener:
    if opener is not None:
        return opener
    from app.modules.explorer.repository import caller_metadata

    return caller_metadata


async def visible_catalogs(user: dict, opener: Opener | None = None) -> list[dict]:
    """Catalogs the caller can list, each with the databases they can list."""
    open_caller = _opener(opener)

    async def load() -> list[dict]:
        async with open_caller(user) as caller:
            return [
                {**catalog, "databases": await caller.databases(catalog["name"])}
                for catalog in await caller.catalogs()
            ]

    return await _cached(user, "catalogs", load)


async def visible_databases(
    user: dict, catalog: str, opener: Opener | None = None
) -> list[str]:
    """Databases the caller can list in one catalog."""
    open_caller = _opener(opener)

    async def load() -> list[str]:
        async with open_caller(user) as caller:
            return await caller.databases(catalog)

    return await _cached(user, f"databases:{catalog}", load)


async def visible_relations(
    user: dict, catalog: str, database: str, opener: Opener | None = None
) -> dict[str, str]:
    """Relations the caller can list in one database: name → ``BASE TABLE``/``VIEW``."""
    open_caller = _opener(opener)

    async def load() -> dict[str, str]:
        async with open_caller(user) as caller:
            if database not in await caller.databases(catalog):
                return {}
            return await caller.relations(catalog, database)

    return await _cached(user, f"relations:{catalog}\x1f{database}", load)
