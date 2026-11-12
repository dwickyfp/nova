"""Development launcher; deployment containers select individual worker roles."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys

ROLES = ("task", "ingestion", "ml", "notebook", "automation", "autopilot")


async def supervise_processes(
    stop: asyncio.Event, *, shutdown_seconds: float = 40
) -> None:
    processes: list[asyncio.subprocess.Process] = []
    waits: dict[asyncio.Task[int], str] = {}
    stopped = asyncio.create_task(stop.wait())
    try:
        for role in ROLES:
            if stop.is_set():
                break
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "app.worker", "--role", role,
                # The supervisor forwards signals after every child is tracked.
                start_new_session=True,
            )
            processes.append(process)
            waits[asyncio.create_task(process.wait())] = role
        done, _ = await asyncio.wait(
            {*waits, stopped}, return_when=asyncio.FIRST_COMPLETED
        )
        if not stop.is_set():
            task = next(task for task in waits if task in done)
            raise RuntimeError(f"Worker role {waits[task]} exited ({task.result()})")
    finally:
        stop.set()
        stopped.cancel()
        await asyncio.gather(stopped, return_exceptions=True)
        for process in processes:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
        if waits:
            _, pending = await asyncio.wait(waits, timeout=shutdown_seconds)
            for process in processes:
                # Training helpers can outlive their worker. Each worker owns
                # the session/group created above, so reap its descendants too.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            await asyncio.gather(*pending)
