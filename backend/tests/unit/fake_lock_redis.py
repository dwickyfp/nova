"""An in-memory stand-in for the Redis locks run coordination takes."""

import asyncio


class FakeLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        await self._lock.acquire()
        return self

    async def __aexit__(self, *_args):
        self._lock.release()

    async def extend(self, timeout: int, *, replace_ttl: bool) -> bool:
        return self._lock.locked() and timeout == 600 and replace_ttl

    async def owned(self) -> bool:
        return self._lock.locked()


class FakeLockRedis:
    def __init__(self) -> None:
        self.locks: dict[str, FakeLock] = {}

    def lock(self, name: str, **kwargs):
        assert kwargs["timeout"] == 600
        return self.locks.setdefault(name, FakeLock())
