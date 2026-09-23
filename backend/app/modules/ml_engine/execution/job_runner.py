"""CPU execution boundary for ML workloads."""

from __future__ import annotations

import asyncio
import multiprocessing
import queue
import time
from collections.abc import Callable
from multiprocessing.process import BaseProcess
from typing import Any

from app.core.config import settings
from app.modules.ml_engine.spec import MLExecutionTimeout


class MLJobRunner:
    """Run each job in a bounded process that can be killed at its deadline."""

    worker_direct = True

    def __init__(self, *, max_workers: int | None = None) -> None:
        workers = max_workers or settings.ML_WORKER_PROCESSES
        self._slots = asyncio.Semaphore(min(workers, settings.ML_MAX_CONCURRENCY))
        self._context = multiprocessing.get_context("spawn")
        self._active: set[BaseProcess] = set()

    async def run(
        self,
        func: Callable[..., Any],
        *args: Any,
        timeout_seconds: float,
        **kwargs: Any,
    ) -> Any:
        deadline_at = time.monotonic() + timeout_seconds
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=timeout_seconds)
        except TimeoutError as exc:
            raise MLExecutionTimeout("startup") from exc
        try:
            results = self._context.Queue(maxsize=1)
            phase = self._context.Value("i", 0)
            process = self._context.Process(
                target=_worker_entry,
                args=(results, func, args, kwargs, phase),
                daemon=True,
            )
            process.start()
            self._active.add(process)
            try:
                try:
                    status, payload = await _wait_for_result(
                        results, process, max(0, deadline_at - time.monotonic())
                    )
                except queue.Empty as exc:
                    if process.is_alive():
                        await asyncio.to_thread(_terminate, process)
                        from app.modules.ml_engine.execution.deadline import STAGES

                        raise MLExecutionTimeout(STAGES[phase.value]) from exc
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
                cutoff = getattr(args[0], "deadline_at", deadline_at) if args else deadline_at
                if cutoff and time.monotonic() >= cutoff:
                    from app.modules.ml_engine.execution.deadline import STAGES

                    raise MLExecutionTimeout(STAGES[phase.value]) from None
                raise
            finally:
                self._active.discard(process)
                results.close()
                results.join_thread()
        finally:
            self._slots.release()

    async def run_job(self, job: Any, *, timeout_seconds: float) -> Any:
        """Run the production worker-direct job contract."""
        from app.modules.ml_engine.execution.worker import execute_worker_job

        return await self.run(execute_worker_job, job, timeout_seconds=timeout_seconds)

    def close(self) -> None:
        for process in tuple(self._active):
            _terminate(process)


def _worker_entry(results, func, args, kwargs, phase) -> None:
    from app.modules.ml_engine.execution import deadline

    deadline.phase_state = phase
    try:
        results.put(("ok", func(*args, **kwargs)))
    except BaseException as exc:
        from app.modules.ml_engine.spec import MLError
        from app.modules.query.sql_pipeline import redact_for_output

        error: BaseException
        if isinstance(exc, MLExecutionTimeout):
            error = exc
        elif isinstance(exc, MLError):
            error = type(exc)(redact_for_output(str(exc)))
        else:
            error = RuntimeError(
                f"ML worker failed during {deadline.STAGES[phase.value]} ({type(exc).__name__})"
            )
        results.put(("error", error))


async def _wait_for_result(results, process: BaseProcess, timeout_seconds: float):
    """Poll a multiprocessing queue without parking an executor thread.

    A blocking ``Queue.get(timeout=...)`` inside ``asyncio.to_thread`` cannot
    be cancelled; its thread survives until the entire training timeout. Short
    async polling preserves the hard deadline and makes cancellation prompt.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        try:
            return results.get_nowait()
        except queue.Empty:
            if loop.time() >= deadline:
                raise
            if not process.is_alive():
                # The multiprocessing queue feeder can trail process exit by
                # one scheduler tick.
                await asyncio.sleep(0.01)
                try:
                    return results.get_nowait()
                except queue.Empty as exc:
                    raise RuntimeError(
                        f"ML worker exited without a result (exit code {process.exitcode})"
                    ) from exc
            await asyncio.sleep(min(0.05, deadline - loop.time()))


def _terminate(process: BaseProcess) -> None:
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
