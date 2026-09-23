"""Versioned Redis cache for serving materialized feature values."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import redis.asyncio as aioredis

from app.core.config import settings

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_PREFIX = "nova:feature:"
_PUBLISH_SCRIPT = """
local existing = redis.call('GET', KEYS[1])
if existing then
  local old = cjson.decode(existing)
  if old['as_of'] > ARGV[1] then return 'stale' end
  if old['as_of'] == ARGV[1] and old['digest'] ~= ARGV[2] then return 'conflict' end
end
redis.call('SET', KEYS[1], ARGV[3], 'EX', ARGV[4])
return 'ok'
"""


class OnlineFeatureError(ValueError):
    """The online cache rejected an invalid or stale feature record."""


class RedisFeatureClient(Protocol):
    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any: ...

    async def get(self, key: str) -> str | None: ...

    async def mget(self, keys: list[str]) -> list[str | None]: ...

    async def delete(self, key: str) -> int: ...

    async def ping(self) -> bool: ...


@dataclass(frozen=True)
class OnlineFeatureRecord:
    group: str
    version: int
    entity_key: dict[str, str | int]
    values: dict[str, Any]
    as_of: datetime
    ttl_seconds: int

    def validate(self) -> None:
        if not _NAME.fullmatch(self.group) or self.version < 1:
            raise OnlineFeatureError("Invalid feature group or version")
        if not 1 <= len(self.entity_key) <= 8 or not 1 <= len(self.values) <= 128:
            raise OnlineFeatureError("Invalid entity key or feature count")
        if any(not _NAME.fullmatch(name) for name in (*self.entity_key, *self.values)):
            raise OnlineFeatureError("Invalid entity or feature name")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (str, int))
            or value == ""
            for value in self.entity_key.values()
        ):
            raise OnlineFeatureError("Entity keys must be non-empty strings or integers")
        if self.as_of.tzinfo is None or not 1 <= self.ttl_seconds <= 2_592_000:
            raise OnlineFeatureError("A timezone-aware timestamp and bounded TTL are required")
        try:
            json.dumps(self.values, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise OnlineFeatureError("Feature values must be finite JSON values") from exc


class RedisOnlineFeatureStore:
    """TTL-backed cache keyed by exact group version and canonical entity identity."""

    def __init__(self, client: RedisFeatureClient | None = None) -> None:
        self._client = client
        self._injected_client = client is not None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    def _redis(self) -> RedisFeatureClient:
        loop = asyncio.get_running_loop()
        if self._client is None or (not self._injected_client and self._client_loop is not loop):
            self._client = cast(
                RedisFeatureClient, aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            )
            self._client_loop = loop
        return self._client

    @staticmethod
    def _key(group: str, version: int, entity_key: dict[str, str | int]) -> str:
        if not _NAME.fullmatch(group) or version < 1 or not 1 <= len(entity_key) <= 8:
            raise OnlineFeatureError("Invalid feature identity")
        if any(not _NAME.fullmatch(name) for name in entity_key):
            raise OnlineFeatureError("Invalid entity key name")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (str, int))
            or value == ""
            for value in entity_key.values()
        ):
            raise OnlineFeatureError("Invalid entity key value")
        canonical = json.dumps(
            entity_key, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        return f"{_PREFIX}{group}:v{version}:{digest}"

    async def publish(self, record: OnlineFeatureRecord) -> None:
        record.validate()
        key = self._key(record.group, record.version, record.entity_key)
        as_of = record.as_of.astimezone(UTC).isoformat(timespec="microseconds")
        values_json = json.dumps(
            record.values, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        digest = hashlib.sha256(values_json.encode()).hexdigest()
        payload = json.dumps(
            {"as_of": as_of, "digest": digest, "values": record.values},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        outcome = await self._redis().eval(
            _PUBLISH_SCRIPT, 1, key, as_of, digest, payload, record.ttl_seconds
        )
        if outcome == "stale":
            raise OnlineFeatureError("Older feature values cannot replace a newer observation")
        if outcome == "conflict":
            raise OnlineFeatureError("Conflicting values at the same feature timestamp")
        if outcome != "ok":
            raise OnlineFeatureError("Online materialization failed")

    async def get(
        self, group: str, version: int, entity_key: dict[str, str | int]
    ) -> dict[str, Any] | None:
        payload = await self._redis().get(self._key(group, version, entity_key))
        return json.loads(payload) if payload else None

    async def batch_get(
        self, group: str, version: int, entity_keys: list[dict[str, str | int]]
    ) -> list[dict[str, Any] | None]:
        if len(entity_keys) > 100:
            raise OnlineFeatureError("At most 100 entities can be read in one batch")
        if not entity_keys:
            return []
        keys = [self._key(group, version, entity_key) for entity_key in entity_keys]
        payloads = await self._redis().mget(keys)
        return [json.loads(payload) if payload else None for payload in payloads]

    async def delete(self, group: str, version: int, entity_key: dict[str, str | int]) -> bool:
        return bool(await self._redis().delete(self._key(group, version, entity_key)))

    async def health(self) -> bool:
        return bool(await self._redis().ping())
