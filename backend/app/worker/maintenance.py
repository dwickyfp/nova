"""Background catalog, Search, News and ML retention work outside the HTTP API."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable


async def _watch(name: str, work: Awaitable[None], stop: asyncio.Event) -> None:
    await work
    if not stop.is_set():
        raise RuntimeError(f"Maintenance loop {name} stopped unexpectedly")


class MaintenanceWorker:
    async def run_forever(self, stop: asyncio.Event) -> None:
        from app.modules.catalog import refresher as catalog_refresher
        from app.modules.intelligence import newsroom_refresher
        from app.modules.intelligence.search import search_service
        from app.modules.llm_functions.registration_worker import AIFunctionRegistrationWorker
        from app.modules.ml_engine.service import ml_engine_service

        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(_watch(name, work, stop), name=name)
                for name, work in (
                    ("catalog", catalog_refresher.run(stop)),
                    ("news", newsroom_refresher.run(stop)),
                    ("search", search_service.run()),
                    ("ml-retention", ml_engine_service.sweep_ephemeral()),
                    ("ai-functions", AIFunctionRegistrationWorker().run_forever(stop)),
                )
            ]
            await stop.wait()
            for task in tasks:
                task.cancel()
