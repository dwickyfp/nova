"""Bounded off-event-loop execution for inference and artifact codecs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

from app.core.config import settings


class BoundedMLExecutor:
    def __init__(self, *, max_workers: int | None = None, name: str = "nova-ml-runtime") -> None:
        workers = max_workers or settings.ML_MAX_CONCURRENCY
        self._slots = asyncio.Semaphore(workers)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)

    async def run(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        async with self._slots:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._pool, partial(func, *args, **kwargs))

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
