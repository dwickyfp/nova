"""A short-lived cache of what one reader has proven about one edition.

Opening News re-runs an edition's proof queries as the reader. The outcome of
that proof, never the data, is kept here for a few minutes so the page does not
repeat the queries on every open. An entry holds digests and row counts that the
reader's own session produced; story content is always read from the journal
and still matched against those digests.

An entry is addressed by the reader's principal, active role, session and
security-context version, by the edition and its revision, and by an access
epoch that every access change made through Nova advances. A role switch, a new
session, a re-pressed edition or a policy change therefore cannot find an old
entry. A change made directly in the policy store is bounded by the entry's
lifetime.
"""

from __future__ import annotations

import json
import logging
from time import time

from app.core.config import settings
from app.core.redis import session_store
from app.modules.intelligence.contracts import fingerprint

logger = logging.getLogger(__name__)

_PREFIX = "nova:news:proof:"
_EPOCH = "nova:news:access-epoch"
_READERS = "nova:news:readers"
_SETTLING = "nova:news:access-settling"

Digests = dict[tuple[str, str, str | None], tuple[str, int]]


class ProofCache:
    def __init__(self, client=None, clock=time):
        self._client = client
        self._clock = clock

    @property
    def client(self):
        return self._client if self._client is not None else session_store._redis

    @property
    def enabled(self) -> bool:
        return settings.NEWS_PROOF_CACHE_SECONDS > 0 and self.client is not None

    async def settling(self) -> bool:
        """True while a recent access change may not have reached the engine yet."""
        return bool(await self.client.get(_SETTLING))

    async def epoch(self) -> str:
        return str(await self.client.get(_EPOCH) or "0")

    async def bump(self) -> None:
        """Retire every cached proof after an access change made through Nova."""
        try:
            if self.client is not None:
                await self.client.incr(_EPOCH)
                # The engine applies a policy change a little after it is
                # written. Until then a proof may reflect the old access, so
                # none is kept or reused.
                await self.client.set(
                    _SETTLING, "1", ex=max(1, int(2 * settings.RANGER_POLICY_PROPAGATION_SECONDS))
                )
        except Exception:
            # The change itself stands; entries then expire on their own.
            logger.warning("News proof cache could not be invalidated; entries will expire")

    def _key(self, user: dict, view_id: str, edition, epoch: str) -> str:
        return _PREFIX + fingerprint(
            [
                user["username"],
                user.get("active_role"),
                user.get("session_id"),
                int(user.get("security_context_version") or 1),
                epoch,
                view_id,
                edition.id,
                edition.revision,
            ]
        )

    async def get(self, user: dict, view_id: str, edition) -> dict | None:
        """The reader's cached proof: ``readable``, ``digests`` and ``stale``."""
        if not self.enabled or not user.get("session_id"):
            return None
        try:
            if await self.settling():
                return None
            raw = await self.client.get(self._key(user, view_id, edition, await self.epoch()))
        except Exception:
            return None
        if not raw:
            return None
        entry = json.loads(raw)
        age = self._clock() - entry["checked_at"]
        return {
            "readable": entry["readable"],
            "digests": {
                (group, scope, value): (digest, rows)
                for group, scope, value, digest, rows in entry["digests"]
            },
            "stale": age > settings.NEWS_PROOF_REFRESH_SECONDS,
        }

    async def put(
        self, user: dict, view_id: str, edition, *, readable: bool, digests: Digests
    ) -> None:
        if not self.enabled or not user.get("session_id"):
            return
        entry = {
            "readable": readable,
            "digests": [[*key, *value] for key, value in digests.items()],
            "checked_at": self._clock(),
        }
        try:
            if await self.settling():
                return
            await self.client.set(
                self._key(user, view_id, edition, await self.epoch()),
                json.dumps(entry),
                ex=settings.NEWS_PROOF_CACHE_SECONDS,
            )
        except Exception:
            logger.warning("News proof cache is unavailable; proofs run on every read")

    # ── Readers whose proofs are kept warm ──────────────────────────────────

    async def touch(self, session_id: str | None) -> None:
        """Note that this session is reading News now."""
        if not self.enabled or not session_id:
            return
        try:
            await self.client.zadd(_READERS, {session_id: self._clock()})
        except Exception:
            return

    async def readers(self, limit: int) -> list[str]:
        """Sessions that read News recently, most recent first; idle ones are dropped."""
        if not self.enabled:
            return []
        cutoff = self._clock() - settings.NEWS_PROOF_READER_IDLE_SECONDS
        await self.client.zremrangebyscore(_READERS, "-inf", cutoff)
        return list(await self.client.zrevrange(_READERS, 0, limit - 1))

    async def forget(self, session_id: str) -> None:
        if self.client is not None:
            await self.client.zrem(_READERS, session_id)


proof_cache = ProofCache()
