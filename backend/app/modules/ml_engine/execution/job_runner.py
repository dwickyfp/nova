"""CPU execution boundary for ML workloads."""

from __future__ import annotations

import asyncio
import multiprocessing
import queue
from collections.abc import Callable
from typing import Any

from app.core.config import settings
from app.modules.ml_engine.spec import TrainingTimeout


class MLJobRunner:
    """Run each job in a bounded process that can be killed at its deadline."""

    def __init__(self, *, max_workers: int | None = None) -> None:
        workers = max_workers or settings.ML_WORKER_PROCESSES
        self._slots = asyncio.Semaphore(min(workers, settings.ML_MAX_CONCURRENCY))
        self._context = multiprocessing.get_context("spawn")
        self._active: set[multiprocessing.Process] = set()

    async def run(
        self,
        func: Callable[..., Any],
        *args: Any,
        timeout_seconds: float,
        **kwargs: Any,
    ) -> Any:
        async with self._slots:
            results = self._context.Queue(maxsize=1)
            process = self._context.Process(
                target=_worker_entry,
                args=(results, func, args, kwargs),
                daemon=True,
            )
            process.start()
            self._active.add(process)
            try:
                try:
                    status, payload = await asyncio.to_thread(
                        results.get, True, timeout_seconds
                    )
                except queue.Empty as exc:
                    if process.is_alive():
                        await asyncio.to_thread(_terminate, process)
                        raise TrainingTimeout(
                            f"ML execution exceeded its {timeout_seconds:g}s budget"
                        ) from exc
                    raise RuntimeError(
                        f"ML worker exited without a result (exit code {process.exitcode})"
                    ) from exc
                await asyncio.to_thread(process.join, 5)
                if process.is_alive():
                    await asyncio.to_thread(_terminate, process)
                if status == "error":
                    if isinstance(payload, BaseException):
                        raise payload
                    raise RuntimeError(str(payload))
                return payload
            except asyncio.CancelledError:
                await asyncio.to_thread(_terminate, process)
                raise
            finally:
                self._active.discard(process)
                results.close()
                results.join_thread()

    def close(self) -> None:
        for process in tuple(self._active):
            _terminate(process)


def _worker_entry(results, func, args, kwargs) -> None:
    try:
        results.put(("ok", func(*args, **kwargs)))
    except BaseException as exc:
        try:
            results.put(("error", exc))
        except Exception:
            results.put(("error", f"{type(exc).__name__}: {exc}"))


def _terminate(process: multiprocessing.Process) -> None:
    if not process.is_alive():
        process.join(timeout=0)
        return
    process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)


class InlineJobRunner:
    """Deterministic test runner with the same public contract."""

    async def run(
        self,
        func: Callable[..., Any],
        *args: Any,
        timeout_seconds: float,
        **kwargs: Any,
    ) -> Any:
        return func(*args, **kwargs)
