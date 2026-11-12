"""Run one workload, or supervise all workloads in separate child processes.

Production containers select one ``--role`` for independent limits and scaling.
The default launcher keeps all workloads active for existing development commands.
Metadata remains in NOVA_SYSTEM until the separately verified PG cutover.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Protocol

import redis.asyncio as aioredis

from app.common.secret_keys import require_configured_secrets
from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.modules.task_orchestration.process_health import WorkerProcessHeartbeat
from app.observability.metrics import SERVICE_UP, start_metrics_server
from app.worker.supervisor import ROLES, supervise_processes

if TYPE_CHECKING:
    from app.modules.agents.automations import AutomationWorker
    from app.modules.task_orchestration.worker_service import WorkerService

logger = logging.getLogger(__name__)
SHUTDOWN_SECONDS = 30.0
METRICS_SERVICES = {
    "task": "worker",
    "ingestion": "ingestion-worker",
    "ml": "ml-worker",
    "notebook": "notebook-worker",
    "automation": "automation-worker",
    "autopilot": "autopilot-worker",
    "maintenance": "maintenance-worker",
}


class Workload(Protocol):
    async def run_forever(self, stop_event: asyncio.Event, /) -> None: ...


def build_worker_service(client: aioredis.Redis) -> WorkerService:
    """Wire the task consumer using its dedicated impersonation account."""
    from app.modules.task_orchestration.consumer import GraphRunConsumer
    from app.modules.task_orchestration.execution import DelegateExecutor
    from app.modules.task_orchestration.reconciler import Reconciler
    from app.modules.task_orchestration.repository import (
        task_orchestration_repository as repository,
    )
    from app.modules.task_orchestration.worker_service import WorkerService

    executor = DelegateExecutor(
        None,
        impersonation_user=settings.WORKER_IMPERSONATION_USER,
        impersonation_password=settings.WORKER_IMPERSONATION_PASSWORD,
        impersonation_role=settings.WORKER_IMPERSONATION_ROLE,
    )
    return WorkerService(
        repository,
        executor,
        GraphRunConsumer(client),
        Reconciler(repository),
    )


def build_automation_worker(client: aioredis.Redis) -> AutomationWorker:
    """Scheduled Studio agent runs, impersonating owners like scheduled tasks."""
    from app.modules.agents.automations import AutomationRunner, AutomationWorker
    from app.modules.task_orchestration.execution import DelegateExecutor

    executor = DelegateExecutor(
        None,
        impersonation_user=settings.WORKER_IMPERSONATION_USER,
        impersonation_password=settings.WORKER_IMPERSONATION_PASSWORD,
        impersonation_role=settings.WORKER_IMPERSONATION_ROLE,
    )
    return AutomationWorker(AutomationRunner(executor), client)



def validate_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError("Unknown worker role")
    if role not in {"task", "automation"}:
        return
    if not settings.WORKER_IMPERSONATION_USER or not settings.WORKER_IMPERSONATION_PASSWORD:
        raise RuntimeError("worker impersonation credentials must be configured")
    if settings.WORKER_IMPERSONATION_USER.lower() in {"root", "nova_admin"}:
        raise RuntimeError("the worker requires a dedicated unprivileged account")
    if not settings.WORKER_IMPERSONATION_ROLE:
        raise RuntimeError("the worker requires a dedicated impersonation role")


async def build_workload(role: str, client: aioredis.Redis) -> Workload:
    if role == "maintenance":
        from app.worker.maintenance import MaintenanceWorker

        return MaintenanceWorker()
    if role == "task":
        from app.common.nova_system import init_task_orchestration

        await init_task_orchestration()
        return build_worker_service(client)
    if role == "ingestion":
        from app.modules.migration.job_worker import MigrationJobWorker
        from app.modules.migration.repository import migration_repo

        await migration_repo.ensure_schema()
        return MigrationJobWorker(client)
    if role == "ml":
        from app.modules.ml_engine.job_worker import MLJobWorker
        from app.modules.ml_engine.jobs import ml_job_repo

        await ml_job_repo.ensure_schema()
        return MLJobWorker(client)
    if role == "automation":
        return build_automation_worker(client)
    if role == "notebook":
        from app.modules.notebooks.runtime.opensandbox_provider import OpenSandboxProvider
        from app.modules.notebooks.runtime.worker import NotebookRuntimeWorker

        return NotebookRuntimeWorker(OpenSandboxProvider())
    if role == "autopilot":
        from app.modules.query_autopilot.jobs import AutopilotWorker
        from app.modules.query_autopilot.schema import ensure_schema
        from app.modules.query_autopilot.service import AutopilotService

        await ensure_schema()
        return AutopilotWorker(client, AutopilotService(client=client))
    raise ValueError("Unknown worker role")


@contextlib.contextmanager
def stop_signals(stop: asyncio.Event):
    loop = asyncio.get_running_loop()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in previous:
            loop.add_signal_handler(sig, stop.set)
        yield
    finally:
        for sig, handler in previous.items():
            loop.remove_signal_handler(sig)
            signal.signal(sig, handler)


async def run_workload(
    workload: Workload,
    heartbeat: WorkerProcessHeartbeat,
    stop: asyncio.Event,
    *,
    shutdown_seconds: float = SHUTDOWN_SECONDS,
) -> None:
    tasks = {
        asyncio.create_task(workload.run_forever(stop), name="workload"),
        asyncio.create_task(heartbeat.run(stop), name="heartbeat"),
    }
    stopped = asyncio.create_task(stop.wait(), name="shutdown-request")
    try:
        done, _ = await asyncio.wait(tasks | {stopped}, return_when=asyncio.FIRST_COMPLETED)
        for task in done & tasks:
            # A child returning normally before shutdown is also a failed runtime.
            task.result()
            if not stop.is_set():
                raise RuntimeError(f"Worker {task.get_name()} stopped unexpectedly")
    finally:
        stop.set()
        stopped.cancel()
        await asyncio.gather(stopped, return_exceptions=True)
        _, pending = await asyncio.wait(tasks, timeout=shutdown_seconds)
        for task in pending:
            task.cancel()
        if pending:
            _, still_pending = await asyncio.wait(pending, timeout=5)
            if still_pending:
                logger.error("Worker shutdown requires process termination")
        # Retrieve exceptions without allowing one cleanup error to skip another.
        for task in tasks:
            if task.done() and not task.cancelled():
                task.exception()


async def _run(role: str = "task") -> None:
    require_configured_secrets()
    validate_role(role)
    async with AsyncExitStack() as cleanup:
        cleanup.push_async_callback(db.close_system_pool)
        await db.init_system_pool()
        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        cleanup.push_async_callback(client.aclose)
        cleanup.push_async_callback(session_store.close)
        await session_store.init()
        await client.ping()
        workload = await build_workload(role, client)
        # Only task consumers count toward the existing task-worker health check.
        registry = settings.WORKER_REGISTRY_KEY
        if role != "task":
            registry = f"{registry}:{role}"
        service = METRICS_SERVICES[role]
        heartbeat = WorkerProcessHeartbeat(client, registry_key=registry, service=service)
        cleanup.push_async_callback(heartbeat.close)
        await heartbeat.publish()
        start_metrics_server(service)
        stop = asyncio.Event()
        try:
            with stop_signals(stop):
                logger.info("Worker role %s started", role)
                await run_workload(workload, heartbeat, stop)
        finally:
            SERVICE_UP.labels(service=service).set(0)


async def _run_all() -> None:
    require_configured_secrets()
    for role in ROLES:
        validate_role(role)
    stop = asyncio.Event()
    with stop_signals(stop):
        await supervise_processes(stop)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=ROLES, help="Run only this workload in the process")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(args.role) if args.role else _run_all())


if __name__ == "__main__":
    main()
