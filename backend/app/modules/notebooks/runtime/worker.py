"""The notebook runtime worker.

Runs in ``nova-worker``. For each live runtime it drains one FIFO queue of jobs
(start, execute, restart, install, stop) under a Redis lease, so a kernel runs
one thing at a time while different runtimes run in parallel, and a second
worker never drives the same kernel. It is the only writer of a runtime's live
state and of execution rows.

It also keeps the fleet honest: idle and over-age runtimes are stopped, and a
reconciler adopts or removes sandboxes that lost their owner (a crash between
"sandbox created" and "state saved", or a backend restart mid-cell).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import logging
import re
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.common.audit import write_audit_log
from app.core.config import settings
from app.modules.access_control import data_tokens
from app.modules.notebooks.repository import notebook_repository
from app.modules.notebooks.runtime import events, kernel_calls, rpc, state
from app.modules.notebooks.runtime.outputs import OutputCollector, payload_of, summary
from app.modules.notebooks.runtime.provider import (
    TASK_RUNTIME_PREFIX,
    KernelEvent,
    ProviderError,
    SandboxGone,
    SandboxHandle,
    SandboxProvider,
    SandboxSpec,
)
from app.modules.workspaces.service import workspace_service

logger = logging.getLogger(__name__)

LEASE_MS = 30_000
_SWEEP_SECONDS = 30
_RECONCILE_SECONDS = 60


def owner_hash(user: str) -> str:
    return hashlib.sha256(f"nova-notebook:{user}".encode()).hexdigest()[:16]


def proxy_port() -> int:
    return settings.NOTEBOOK_PROXY_PORT or settings.PROXY_PORT


#: Where the kernel leaves displays too large for its result (``end()``).
DISPLAY_FILE = re.compile(r"/tmp/nova/displays/[0-9a-f]{32}\.json")
#: Catalogs a runtime attaches at most; the rest stay reachable via con.nova().
MAX_ATTACHED_CATALOGS = 20
#: How long a runtime start waits for one external catalog to answer.
CATALOG_CHECK_SECONDS = 20


async def visible_catalogs(session: data_tokens.BiSession) -> list[dict[str, str]]:
    """The catalogs the user's role sees, as ``SHOW CATALOGS`` answers it for them."""
    from app.modules.query.service import query_service

    try:
        results = await query_service.execute_statements(
            sql="SHOW CATALOGS",
            username=session.username,
            encrypted_password=session.encrypted_password,
            role=session.active_role,
            security_context_version=session.security_context_version,
            session_id=session.session_id,
            source="notebook",
        )
    except Exception:  # noqa: BLE001 - Nova data still works without extra catalogs
        logger.warning("Could not list catalogs for a notebook runtime", exc_info=True)
        return [{"name": "default_catalog", "type": "Internal"}]
    last = results[-1] if results else None
    rows = (last.rows if last is not None and not last.error else None) or []
    catalogs = [{"name": str(row[0]), "type": str(row[1] if len(row) > 1 else "")}
                for row in rows if row and row[0]]
    if not any(item["name"] == "default_catalog" for item in catalogs):
        catalogs.insert(0, {"name": "default_catalog", "type": "Internal"})
    catalogs = catalogs[:MAX_ATTACHED_CATALOGS]

    async def check(item: dict[str, str]) -> None:
        # DuckDB lists every attached catalog when it reads any catalog's
        # schemas, so a catalog the engine cannot reach would break them all:
        # it is listed with its error instead of attached.
        if item["name"] == "default_catalog":
            return
        name = item["name"].replace("`", "``")
        try:
            answer = await asyncio.wait_for(
                query_service.execute_statements(
                    sql=f"SHOW DATABASES FROM `{name}`",
                    username=session.username,
                    encrypted_password=session.encrypted_password,
                    role=session.active_role,
                    security_context_version=session.security_context_version,
                    session_id=session.session_id,
                    source="notebook",
                ),
                timeout=CATALOG_CHECK_SECONDS,
            )
            failed = next((result.error for result in answer if result.error), None)
        except TimeoutError:
            failed = "did not answer in time"
        except Exception as exc:  # noqa: BLE001
            failed = type(exc).__name__
        if failed:
            item["error"] = str(failed).splitlines()[0][:200]

    await asyncio.gather(*(check(item) for item in catalogs))
    return catalogs


class RunFailed(Exception):
    """Stop-on-error: a cell in this run failed, later cells are skipped."""


class NotebookRuntimeWorker:
    def __init__(self, provider: SandboxProvider, *, worker_id: str | None = None) -> None:
        self._provider = provider
        self._worker_id = worker_id or f"{socket.gethostname()}:{secrets.token_hex(4)}"
        self._draining: dict[str, asyncio.Task] = {}
        self._wake_cursor = "$"

    # ── Loop ──────────────────────────────────────────────────────────────

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        await notebook_repository.ensure_schema()
        await self._reconcile(startup=True)
        last_sweep = last_reconcile = time.monotonic()
        while not stop_event.is_set():
            try:
                await self._wait_for_wake()
                await self._dispatch()
                now = time.monotonic()
                if now - last_sweep >= _SWEEP_SECONDS:
                    last_sweep = now
                    await self._sweep()
                if now - last_reconcile >= _RECONCILE_SECONDS:
                    last_reconcile = now
                    await self._reconcile(startup=False)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive one bad cycle
                logger.exception("Notebook worker cycle failed")
                await asyncio.sleep(1)
        for task in list(self._draining.values()):
            task.cancel()
        with contextlib.suppress(Exception):
            await self._provider.close()

    async def _wait_for_wake(self) -> None:
        client = state.redis()
        try:
            batch = await client.xread({state.WAKE: self._wake_cursor}, count=50, block=1000)
        except Exception:  # noqa: BLE001
            await asyncio.sleep(1)
            return
        for _stream, entries in batch or []:
            for entry_id, _fields in entries:
                self._wake_cursor = entry_id

    async def _dispatch(self) -> None:
        client = state.redis()
        for runtime_id in await client.smembers(state.RUNTIMES):
            if runtime_id in self._draining:
                continue
            if not await client.llen(state.queue_key(runtime_id)):
                continue
            acquired = await client.set(
                state.lease_key(runtime_id), self._worker_id, nx=True, px=LEASE_MS
            )
            if not acquired:
                continue
            task = asyncio.create_task(self._drain(runtime_id), name=f"notebook-{runtime_id}")
            self._draining[runtime_id] = task
            task.add_done_callback(self._forget(runtime_id))

    def _forget(self, runtime_id: str):
        def done(_task: asyncio.Task) -> None:
            self._draining.pop(runtime_id, None)

        return done

    async def _renew_lease(self, runtime_id: str) -> bool:
        client = state.redis()
        owner = await client.get(state.lease_key(runtime_id))
        if owner != self._worker_id:
            return False
        await client.pexpire(state.lease_key(runtime_id), LEASE_MS)
        return True

    async def _release_lease(self, runtime_id: str) -> None:
        client = state.redis()
        if await client.get(state.lease_key(runtime_id)) == self._worker_id:
            await client.delete(state.lease_key(runtime_id))

    async def _drain(self, runtime_id: str) -> None:
        client = state.redis()
        renewer = asyncio.create_task(self._keep_lease(runtime_id))
        try:
            while True:
                if not await self._renew_lease(runtime_id):
                    return
                job_id = await client.lpop(state.queue_key(runtime_id))
                if not job_id:
                    return
                job = await state.get_job(job_id)
                if job is None:
                    continue
                try:
                    await self._handle(job)
                except Exception:  # noqa: BLE001 - one job never stops the queue
                    logger.exception("Notebook job %s failed", job.get("kind"))
        finally:
            renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewer
            await self._release_lease(runtime_id)

    async def _keep_lease(self, runtime_id: str) -> None:
        while True:
            await asyncio.sleep(LEASE_MS / 3000)
            if not await self._renew_lease(runtime_id):
                return

    async def _handle(self, job: dict[str, Any]) -> None:
        kind = job.get("kind")
        if kind == "start":
            await self._start(job)
        elif kind == "execute":
            await self._execute(job)
        elif kind == "restart":
            await self._restart(job)
        elif kind == "install":
            from app.modules.notebooks.packages import run_install

            await run_install(self, job)
        elif kind == "stop":
            await self._stop(job["runtime_id"], reason=job.get("reason") or "user")

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _status(self, runtime: state.RuntimeState, status: str, stage: str = "", **extra):
        runtime.status = status
        runtime.stage = stage
        for key, value in extra.items():
            setattr(runtime, key, value)
        await state.update(runtime.id, status=status, stage=stage, **extra)
        await events.publish(runtime.channel, "runtime.status", runtime.public())

    def handle_of(self, runtime: state.RuntimeState) -> SandboxHandle:
        return SandboxHandle(sandbox_id=runtime.sandbox_id, context_id=runtime.context_id)

    async def call(
        self,
        runtime: state.RuntimeState,
        code: str,
        on_stream: Callable[[KernelEvent], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Run a trusted ``nova_runtime`` call and return its payload.

        ``on_stream`` receives stdout/stderr that arrives during the call: the
        kernel can flush a cell's last output after the cell reports done.
        """
        payload: dict[str, Any] = {}
        error: dict[str, Any] = {}

        async def sink(event: KernelEvent) -> None:
            if event.type in {"stdout", "stderr"} and on_stream is not None:
                await on_stream(event)
                return
            if event.type == "result":
                found = payload_of(event.bundle)
                if found is not None:
                    payload.update(found)
            elif event.type == "error":
                error.update(event.error)

        await self._provider.run(self.handle_of(runtime), code, sink)
        if error and not payload:
            raise ProviderError(f"Runtime setup failed: {error.get('name')}")
        return payload

    async def credential(self, runtime: state.RuntimeState) -> str | None:
        """Mint the runtime's data token and hand it to the kernel as a file.

        Returns the file path, or ``None`` when Nova data cannot be reached
        (no proxy address configured, or the user's session has ended).
        """
        if not settings.NOTEBOOK_PROXY_ADDRESS or not runtime.session_id:
            return None
        try:
            session = await data_tokens.resolve_session(runtime.session_id)
        except data_tokens.BiCredentialError as exc:
            runtime.nova_error = "Sign in again to read Nova data in this notebook"
            logger.info("Notebook runtime %s has no usable session: %s", runtime.id, exc)
            return None
        issued = await data_tokens.mint(
            session,
            purpose=data_tokens.NOTEBOOK,
            ttl=settings.NOTEBOOK_DATA_TOKEN_TTL_SECONDS,
            max_per_principal=settings.NOTEBOOK_MAX_ACTIVE_RUNTIMES * 2,
            subject=runtime.id,
        )
        path = f"/tmp/nova/.cred-{secrets.token_hex(12)}.json"
        body = json.dumps(
            {
                "host": settings.NOTEBOOK_PROXY_ADDRESS,
                "port": proxy_port(),
                "user": issued.username,
                "token": issued.token,
                "catalogs": await visible_catalogs(session),
            }
        ).encode()
        await self._provider.write_file(self.handle_of(runtime), path, body)
        runtime.token_minted_at = time.time()
        runtime.token_role = session.active_role
        runtime.token_version = session.security_context_version
        runtime.active_role = session.active_role
        await state.update(
            runtime.id,
            token_minted_at=runtime.token_minted_at,
            token_role=runtime.token_role,
            token_version=runtime.token_version,
            active_role=runtime.active_role,
        )
        return path

    async def _bootstrap(self, runtime: state.RuntimeState) -> dict[str, Any]:
        path = await self.credential(runtime)
        result = await self.call(
            runtime,
            kernel_calls.bootstrap(
                {
                    "workspace": "/workspace",
                    "preview_rows": settings.NOTEBOOK_PREVIEW_ROWS,
                    "memory_limit": settings.NOTEBOOK_DUCKDB_MEMORY_LIMIT,
                    "threads": settings.NOTEBOOK_DUCKDB_THREADS,
                    "request_timeout": settings.NOTEBOOK_CELL_TIMEOUT_SECONDS,
                    "credential_path": path,
                }
            ),
        )
        if not result.get("ready"):
            raise ProviderError("The runtime's DuckDB did not start")
        nova = result.get("nova") or {}
        runtime.nova_attached = bool(nova.get("attached"))
        if not runtime.nova_attached and not runtime.nova_error:
            runtime.nova_error = (
                "Nova data is not set up for notebooks on this deployment"
                if not settings.NOTEBOOK_PROXY_ADDRESS
                else "Nova data is not reachable from this runtime"
            )
        if runtime.nova_attached:
            runtime.nova_error = ""
        await state.update(
            runtime.id, nova_attached=runtime.nova_attached, nova_error=runtime.nova_error
        )
        return result

    async def _rotate_if_needed(self, runtime: state.RuntimeState, job: dict[str, Any]) -> None:
        """Bind Nova reads to the session and role that asked for this run.

        A role switch or a new sign-in changes what the user may read; the
        runtime follows the role the user is working under, and renews its
        token before it is half-way to expiry.
        """
        session_id = job.get("session_id") or runtime.session_id
        role_changed = (
            job.get("active_role") != runtime.token_role
            or int(job.get("security_version") or 0) != runtime.token_version
        )
        aged = time.time() - runtime.token_minted_at > settings.NOTEBOOK_DATA_TOKEN_TTL_SECONDS / 2
        if session_id == runtime.session_id and not role_changed and not aged:
            return
        runtime.session_id = session_id
        await state.update(runtime.id, session_id=session_id)
        path = await self.credential(runtime)
        if path is None:
            return
        result = await self.call(runtime, kernel_calls.rotate(path))
        runtime.nova_attached = bool(result.get("rotated"))
        runtime.nova_error = "" if runtime.nova_attached else str(result.get("error") or "")
        await state.update(
            runtime.id, nova_attached=runtime.nova_attached, nova_error=runtime.nova_error
        )
        if role_changed:
            await events.publish(runtime.channel, "runtime.status", runtime.public())

    async def _cleanup(
        self, runtime: state.RuntimeState, *, status: str, reason: str, error: str = ""
    ) -> None:
        """Forget a runtime that ended: revoke its data token, keep the outcome."""
        client = state.redis()
        await data_tokens.revoke_subject(data_tokens.NOTEBOOK, runtime.id)
        await self._cancel_queue(runtime, reason="The runtime stopped before this cell ran")
        await client.delete(state.rt_key(runtime.id))
        await client.srem(state.RUNTIMES, runtime.id)
        if await client.get(state.active_key(runtime.channel)) == runtime.id:
            await client.delete(state.active_key(runtime.channel))
        await client.hset(
            state.last_key(runtime.channel),
            mapping={
                "runtime_id": runtime.id,
                "status": status,
                "reason": reason,
                "error": error,
                "ended_at": f"{time.time():.3f}",
            },
        )
        await client.expire(state.last_key(runtime.channel), state.DAY)
        runtime.status = status
        runtime.error = error
        with contextlib.suppress(Exception):
            await notebook_repository.record_runtime(
                {
                    "id": runtime.id,
                    "notebook_id": runtime.notebook_id,
                    "user_name": runtime.user,
                    "status": status,
                    "active_role": runtime.active_role,
                    "image": settings.NOTEBOOK_RUNTIME_IMAGE,
                    "cpu": settings.NOTEBOOK_RUNTIME_CPU,
                    "memory": settings.NOTEBOOK_RUNTIME_MEMORY,
                    "generation": runtime.generation,
                    "error_message": error or None,
                    "created_at": int(runtime.created_at),
                    "ready_at": int(runtime.ready_at) if runtime.ready_at else None,
                    "ended_at": int(time.time()),
                    "end_reason": reason,
                }
            )
        await events.publish(
            runtime.channel,
            "runtime.status",
            {"runtime_id": runtime.id, "status": status, "stage": "", "reason": reason,
             "error": error or None},
        )

    async def _cancel_queue(self, runtime: state.RuntimeState, *, reason: str) -> None:
        client = state.redis()
        while True:
            job_id = await client.lpop(state.queue_key(runtime.id))
            if not job_id:
                break
            job = await state.get_job(job_id)
            if job and job.get("kind") == "execute":
                await self._finish_without_running(job, status="cancelled", message=reason)

    # ── Start ─────────────────────────────────────────────────────────────

    async def _start(self, job: dict[str, Any]) -> None:
        runtime = await state.load(job["runtime_id"])
        if runtime is None or runtime.status != "PROVISIONING":
            return
        address = settings.NOTEBOOK_PROXY_ADDRESS
        spec = SandboxSpec(
            runtime_id=runtime.id,
            notebook_id=runtime.notebook_id,
            owner_hash=owner_hash(runtime.user),
            image=settings.NOTEBOOK_RUNTIME_IMAGE,
            cpu=settings.NOTEBOOK_RUNTIME_CPU,
            memory=settings.NOTEBOOK_RUNTIME_MEMORY,
            lifetime_seconds=settings.NOTEBOOK_MAX_LIFETIME_SECONDS + 600,
            env={"JUPYTER_TOKEN": secrets.token_urlsafe(24)},
            egress=[(address, proxy_port())] if address else [],
            ready_timeout_seconds=min(settings.NOTEBOOK_START_TIMEOUT_SECONDS, 300),
        )
        try:
            await self._status(runtime, "PROVISIONING", "Creating the runtime container")
            handle = await asyncio.wait_for(
                self._provider.create(spec), timeout=settings.NOTEBOOK_START_TIMEOUT_SECONDS
            )
            runtime.sandbox_id, runtime.context_id = handle.sandbox_id, handle.context_id
            await state.update(
                runtime.id, sandbox_id=handle.sandbox_id, context_id=handle.context_id
            )
            if await state.load(runtime.id) is None:  # stopped while starting
                await self._provider.kill(handle.sandbox_id)
                return
            await self._status(runtime, "BOOTSTRAPPING", "Copying workspace files")
            from app.modules.notebooks.packages import hydrate_files, restore_packages

            await hydrate_files(self, runtime)
            await self._status(runtime, "BOOTSTRAPPING", "Starting Python and DuckDB")
            await self._bootstrap(runtime)
            await restore_packages(self, runtime)
            runtime.ready_at = time.time()
            runtime.last_activity = runtime.ready_at
            await self._status(
                runtime, "READY", "", ready_at=runtime.ready_at, last_activity=runtime.ready_at
            )
            await notebook_repository.record_runtime(
                {
                    "id": runtime.id,
                    "notebook_id": runtime.notebook_id,
                    "user_name": runtime.user,
                    "status": "READY",
                    "active_role": runtime.active_role,
                    "image": settings.NOTEBOOK_RUNTIME_IMAGE,
                    "cpu": settings.NOTEBOOK_RUNTIME_CPU,
                    "memory": settings.NOTEBOOK_RUNTIME_MEMORY,
                    "generation": runtime.generation,
                    "created_at": int(runtime.created_at),
                    "ready_at": int(runtime.ready_at),
                }
            )
            await _audit(runtime.user, "RUNTIME_START", runtime.notebook_id, "SUCCESS")
        except Exception as exc:  # noqa: BLE001 - every failure ends in FAILED
            message = str(exc) if isinstance(exc, ProviderError) else (
                "The runtime did not start in time" if isinstance(exc, TimeoutError)
                else "The runtime could not start"
            )
            logger.warning("Notebook runtime %s failed to start: %s", runtime.id, exc)
            if runtime.sandbox_id:
                with contextlib.suppress(Exception):
                    await self._provider.kill(runtime.sandbox_id)
            await self._cleanup(runtime, status="FAILED", reason="start_failed", error=message)
            await _audit(runtime.user, "RUNTIME_START", runtime.notebook_id, "FAILED", message)

    # ── Stop / restart ────────────────────────────────────────────────────

    async def _stop(self, runtime_id: str, *, reason: str) -> None:
        runtime = await state.load(runtime_id)
        if runtime is None:
            return
        await self._status(runtime, "STOPPING", "Saving output files")
        if runtime.sandbox_id:
            from app.modules.notebooks.packages import flush_outputs

            with contextlib.suppress(Exception):
                await flush_outputs(self, runtime)
            try:
                await self._provider.kill(runtime.sandbox_id)
            except ProviderError:
                logger.warning("Sandbox of runtime %s could not be removed now", runtime.id)
        await self._cleanup(runtime, status="STOPPED", reason=reason)
        await _audit(runtime.user, "RUNTIME_STOP", runtime.notebook_id, "SUCCESS", reason)

    async def _restart(self, job: dict[str, Any]) -> None:
        runtime = await state.load(job["runtime_id"])
        if runtime is None or runtime.status not in {"READY", "BUSY"}:
            return
        await self._restart_kernel(runtime, reason="user")

    async def _restart_kernel(self, runtime: state.RuntimeState, *, reason: str) -> None:
        await self._status(runtime, "BOOTSTRAPPING", "Restarting the kernel")
        try:
            handle = await self._provider.restart_context(self.handle_of(runtime))
            runtime.context_id = handle.context_id
            runtime.generation += 1
            runtime.execution_counter = 0
            await state.update(
                runtime.id,
                context_id=handle.context_id,
                generation=runtime.generation,
                execution_counter=0,
            )
            await self._bootstrap(runtime)
            from app.modules.notebooks.packages import restore_packages

            await restore_packages(self, runtime, reinstall=False)
            runtime.last_activity = time.time()
            await self._status(runtime, "READY", "", last_activity=runtime.last_activity)
            await events.publish(
                runtime.channel,
                "runtime.restarted",
                {"runtime_id": runtime.id, "generation": runtime.generation, "reason": reason},
            )
        except SandboxGone:
            await self._cleanup(runtime, status="LOST", reason="sandbox_gone",
                                error="The runtime stopped unexpectedly")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kernel restart failed for %s: %s", runtime.id, exc)
            with contextlib.suppress(Exception):
                await self._provider.kill(runtime.sandbox_id)
            await self._cleanup(runtime, status="FAILED", reason="restart_failed",
                                error="The kernel could not restart")

    # ── Execute ───────────────────────────────────────────────────────────

    async def _finish_without_running(self, job: dict[str, Any], *, status: str, message: str):
        execution_id = job["execution_id"]
        now = time.time()
        await state.set_execution(execution_id, status=status, ended_at=now, message=message)
        await state.redis().hdel(state.inflight_key(state.job_channel(job)), job["cell_id"])
        with contextlib.suppress(Exception):
            await notebook_repository.record_execution(
                {
                    **_row_base(job),
                    "status": status,
                    "error_message": message,
                    "queued_at": int(job.get("queued_at") or now),
                    "ended_at": int(now),
                }
            )
        await events.publish(
            state.job_channel(job),
            "cell.finished",
            {
                "execution_id": execution_id,
                "cell_id": job["cell_id"],
                "status": status,
                "message": message,
                "outputs": [],
            },
        )

    async def _execute(self, job: dict[str, Any]) -> None:
        client = state.redis()
        execution_id = job["execution_id"]
        if await client.exists(state.cancel_key(execution_id)):
            await self._finish_without_running(job, status="cancelled", message="Cancelled")
            return
        run_failed = await client.exists(f"{state.PREFIX}runfail:{job['run_id']}")
        if job.get("on_error") == "stop" and run_failed:
            await self._finish_without_running(
                job, status="skipped", message="Skipped because an earlier cell failed"
            )
            return
        runtime = await state.load(job["runtime_id"])
        if runtime is None or runtime.status not in {"READY", "BUSY"}:
            await self._finish_without_running(
                job, status="cancelled", message="The runtime is not running"
            )
            return
        if int(job.get("generation") or runtime.generation) != runtime.generation:
            # Queued before a kernel restart: it would run against a state the
            # user did not see when they asked.
            await self._finish_without_running(
                job, status="cancelled", message="The kernel restarted before this cell ran"
            )
            return

        runtime.execution_counter += 1
        count = runtime.execution_counter
        started = time.time()
        await state.update(
            runtime.id,
            status="BUSY",
            current_execution=execution_id,
            execution_counter=count,
            last_activity=started,
        )
        runtime.status = "BUSY"
        runtime.current_execution = execution_id
        await events.publish(runtime.channel, "runtime.status", runtime.public())
        await state.set_execution(execution_id, status="running", started_at=started,
                                  execution_count=count)
        await events.publish(
            runtime.channel,
            "cell.started",
            {
                "execution_id": execution_id,
                "cell_id": job["cell_id"],
                "execution_count": count,
                "source_revision": job["source_revision"],
                "started_at": started,
            },
        )

        collector = OutputCollector(max_bytes=settings.NOTEBOOK_OUTPUT_MAX_BYTES)
        status = "succeeded"
        message = ""
        variables: list[dict[str, Any]] | None = None
        try:
            await self._rotate_if_needed(runtime, job)
            await self.call(runtime, kernel_calls.begin(execution_id))
            status, message = await self._run_user_code(runtime, job, collector)
            if status != "lost":
                try:
                    async def late_output(event: KernelEvent) -> None:
                        live = collector.accept(event)
                        if live is not None:
                            await events.publish(
                                runtime.channel,
                                "cell.output",
                                {"execution_id": execution_id, "cell_id": job["cell_id"],
                                 "output": {k: v for k, v in live.items() if k != "_t"}},
                            )

                    finished = await self.call(runtime, kernel_calls.end(), late_output)
                    collector.displays(
                        await self._read_display_files(runtime, finished.get("displays") or [])
                    )
                    variables = finished.get("variables")
                    if variables is not None:
                        await client.set(
                            f"{state.PREFIX}vars:{runtime.id}",
                            json.dumps(variables, default=str),
                            ex=state.DAY,
                        )
                except ProviderError:
                    logger.info("Could not read displays of %s", execution_id)
        except SandboxGone:
            status, message = "lost", "The runtime stopped unexpectedly"
        except ProviderError as exc:
            status, message = "failed", str(exc)
            collector.nova_error("RuntimeError", str(exc))

        restart_after = status == "timeout_restart"
        if restart_after:
            status = "timeout"
        if collector.error and status == "succeeded":
            status = "failed"
            message = collector.error.get("message") or ""
        ended = time.time()
        records = collector.final()
        output_key = None
        output_bytes = 0
        if records:
            body = json.dumps(records, separators=(",", ":"), default=str).encode()
            output_key = workspace_service.internal_object_key(
                runtime.user, ".notebook-outputs", runtime.notebook_id, f"{execution_id}.json"
            )
            try:
                await workspace_service.put_internal_object(output_key, body)
                output_bytes = len(body)
            except Exception:  # noqa: BLE001 - outputs are shown live even if not kept
                logger.warning("Outputs of %s could not be stored", execution_id)
                output_key = None
        row = {
            **_row_base(job),
            "generation": runtime.generation,
            "execution_count": count,
            "status": status,
            "active_role": runtime.active_role,
            "error_name": (collector.error or {}).get("name"),
            "error_message": message or None,
            "output_key": output_key,
            "output_bytes": output_bytes,
            "truncated": collector.truncated,
            "summary": summary(records),
            "queued_at": int(job.get("queued_at") or started),
            "started_at": int(started),
            "ended_at": int(ended),
            "duration_ms": int((ended - started) * 1000),
        }
        with contextlib.suppress(Exception):
            await notebook_repository.record_execution(row)
        await state.set_execution(execution_id, status=status, ended_at=ended)
        await client.hdel(state.inflight_key(runtime.channel), job["cell_id"])
        if status in {"failed", "timeout"} and job.get("on_error") == "stop":
            await client.set(f"{state.PREFIX}runfail:{job['run_id']}", "1", ex=state.DAY)
        await events.publish(
            runtime.channel,
            "cell.finished",
            {
                "execution_id": execution_id,
                "cell_id": job["cell_id"],
                "status": status,
                "message": message or None,
                "execution_count": count,
                "duration_ms": row["duration_ms"],
                "outputs": records,
                "truncated": collector.truncated,
                "variables": variables,
                "source_revision": job["source_revision"],
                "generation": runtime.generation,
                "runtime_id": runtime.id,
            },
        )
        await _audit(
            runtime.user,
            "EXECUTE_CELL",
            runtime.notebook_id,
            "SUCCESS" if status == "succeeded" else "FAILED",
            f"{job['cell_kind']} cell, {job['source_revision']}",
        )
        if status == "lost":
            await self._cleanup(runtime, status="LOST", reason="sandbox_gone",
                                error="The runtime stopped unexpectedly")
            return
        if restart_after:
            await self._restart_kernel(runtime, reason="timeout")
            return
        latest = await state.load(runtime.id)
        if latest is not None:
            await state.update(runtime.id, status="READY", current_execution="",
                               last_activity=time.time())
            latest.status, latest.current_execution = "READY", ""
            await events.publish(runtime.channel, "runtime.status", latest.public())

    async def _run_user_code(
        self, runtime: state.RuntimeState, job: dict[str, Any], collector: OutputCollector
    ) -> tuple[str, str]:
        """Run the cell, watching for cancel and timeout. Returns (status, message)."""
        client = state.redis()
        execution_id = job["execution_id"]
        sql_result: dict[str, Any] = {}

        if job["cell_kind"] == "sql":
            code = kernel_calls.run_sql(
                source=job["source"],
                name=job.get("name"),
                engine=job.get("engine") or "duckdb",
                preview_rows=settings.NOTEBOOK_PREVIEW_ROWS,
            )
        else:
            code = job["source"]

        markers = rpc.MarkerReader()

        async def sink(event: KernelEvent) -> None:
            if event.type == "init" and event.text:
                await state.set_execution(execution_id, kernel_execution_id=event.text)
            if event.type == "result":
                found = payload_of(event.bundle)
                if found is not None:
                    sql_result.update(found)
                    return
            if event.type == "stderr":
                text, requests = markers.feed(event.text)
                for request_id in requests:
                    # The kernel waits for this result, so the stream waits too.
                    await rpc.handle(
                        self._provider, self.handle_of(runtime),
                        job.get("session_id") or runtime.session_id, request_id,
                    )
                if not text:
                    return
                event = dataclasses.replace(event, text=text)
            live = collector.accept(event)
            if live is not None:
                await events.publish(
                    runtime.channel,
                    "cell.output",
                    {"execution_id": execution_id, "cell_id": job["cell_id"], "output":
                     {k: v for k, v in live.items() if k != "_t"}},
                )

        run = asyncio.create_task(self._provider.run(self.handle_of(runtime), code, sink))
        deadline = time.monotonic() + settings.NOTEBOOK_CELL_TIMEOUT_SECONDS
        interrupted_at: float | None = None
        outcome = ("succeeded", "")
        while not run.done():
            await asyncio.wait({run}, timeout=0.5)
            if run.done():
                break
            cancelled = await client.exists(state.cancel_key(execution_id))
            timed_out = time.monotonic() > deadline
            if (cancelled or timed_out) and interrupted_at is None:
                interrupted_at = time.monotonic()
                outcome = ("cancelled", "Stopped") if cancelled else (
                    "timeout",
                    f"Stopped after {settings.NOTEBOOK_CELL_TIMEOUT_SECONDS} seconds",
                )
                kernel_id = (await state.get_execution(execution_id) or {}).get(
                    "kernel_execution_id"
                )
                if kernel_id:
                    with contextlib.suppress(ProviderError):
                        await self._provider.interrupt(self.handle_of(runtime), kernel_id)
            elif (
                interrupted_at is not None
                and time.monotonic() - interrupted_at > settings.NOTEBOOK_INTERRUPT_GRACE_SECONDS
            ):
                run.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await run
                collector.nova_error(
                    "KernelRestarted",
                    "The cell did not stop when asked; the kernel was restarted and "
                    "variables were cleared",
                )
                return "timeout_restart", "The kernel was restarted"
        exc = run.exception()
        if exc is not None:
            if isinstance(exc, SandboxGone):
                return "lost", str(exc)
            if isinstance(exc, ProviderError):
                collector.nova_error("RuntimeError", str(exc))
                return "failed", str(exc)
            raise exc
        if job["cell_kind"] == "sql" and outcome[0] == "succeeded":
            error = sql_result.get("error")
            if isinstance(error, dict):
                collector.nova_error(str(error.get("name") or "Error"), str(error.get("message")))
                return "failed", str(error.get("message"))
            kind = sql_result.get("kind")
            if kind == "table" and isinstance(sql_result.get("table"), dict):
                table = dict(sql_result["table"])
                nova_sql = await self._nova_sql(job, sql_result, table)
                if nova_sql:
                    table["nova_sql"] = nova_sql
                record = {"type": "table", "table": table, "name": sql_result.get("name")}
                collector.record(record, at=time.time())
            elif kind == "status":
                affected = sql_result.get("affected_rows")
                statement = sql_result.get("statement") or "Statement"
                text = f"{statement.title()} done" + (
                    f": {affected} rows" if isinstance(affected, int) else ""
                )
                collector.record({"type": "status", "text": text}, at=time.time())
        if outcome[0] != "succeeded":
            collector.nova_error(
                "Cancelled" if outcome[0] == "cancelled" else "Timeout", outcome[1]
            )
        return outcome

    async def _read_display_files(
        self, runtime: state.RuntimeState, displays: list[Any]
    ) -> list[Any]:
        """Displays the kernel handed over as files (large ones), read back in place."""
        out = []
        for display in displays:
            path = display.get("file") if isinstance(display, dict) else None
            if not path:
                out.append(display)
                continue
            if not DISPLAY_FILE.fullmatch(str(path)):
                continue
            if int(display.get("bytes") or 0) > settings.NOTEBOOK_OUTPUT_MAX_BYTES:
                out.append({"t": display.get("t"), "data": {
                    "text/plain": "Output too large to show"}})
                continue
            try:
                body = await self._provider.read_file(self.handle_of(runtime), str(path))
                out.append({"t": display.get("t"), "data": json.loads(body)})
            except (ProviderError, ValueError):
                logger.info("A large display of runtime %s could not be read", runtime.id)
        return out

    async def _nova_sql(
        self, job: dict[str, Any], sql_result: dict[str, Any], table: dict[str, Any]
    ) -> str | None:
        """The Nova statement that reproduces this table, checked in Nova, or ``None``.

        Dataviz keeps a result up to date by running it again in Nova, so only
        a statement Nova accepts as this user, under this role, qualifies.
        """
        if job.get("engine") == "nova" and table.get("origin") == "nova":
            return str(job["source"]).strip().rstrip(";").strip()
        candidate = str(sql_result.get("nova_candidate") or "").strip().rstrip(";").strip()
        if not candidate:
            return None
        from app.modules.query.service import query_service

        try:
            session = await data_tokens.resolve_session(job.get("session_id") or "")
            results = await query_service.execute_statements(
                sql=f"SELECT * FROM ({candidate}) AS nova_check LIMIT 0",
                username=session.username,
                encrypted_password=session.encrypted_password,
                role=session.active_role,
                security_context_version=session.security_context_version,
                session_id=session.session_id,
                source="notebook",
            )
        except Exception:  # noqa: BLE001 - the table stays usable in the notebook
            return None
        return candidate if results and not any(result.error for result in results) else None

    # ── Fleet hygiene ─────────────────────────────────────────────────────

    async def _sweep(self) -> None:
        now = time.time()
        for runtime in await state.live_runtimes():
            if runtime.status not in {"READY", "BUSY"}:
                continue
            too_old = now - runtime.created_at > settings.NOTEBOOK_MAX_LIFETIME_SECONDS
            idle = (
                runtime.status == "READY"
                and now - runtime.last_activity > settings.NOTEBOOK_IDLE_TIMEOUT_SECONDS
            )
            if too_old or idle:
                await state.put_job(
                    runtime.id, {"kind": "stop", "reason": "lifetime" if too_old else "idle"}
                )

    async def _reconcile(self, *, startup: bool) -> None:
        client = state.redis()
        runtimes = await state.live_runtimes()
        by_id = {runtime.id: runtime for runtime in runtimes}
        now = time.time()
        for runtime in runtimes:
            leased = await client.exists(state.lease_key(runtime.id))
            if leased:
                continue
            if runtime.status in state.STARTING_STATUSES and (
                now - runtime.created_at > settings.NOTEBOOK_START_TIMEOUT_SECONDS + 60
            ):
                if runtime.sandbox_id:
                    with contextlib.suppress(Exception):
                        await self._provider.kill(runtime.sandbox_id)
                await self._cleanup(runtime, status="FAILED", reason="start_abandoned",
                                    error="The runtime did not finish starting")
            elif runtime.status == "BUSY" and runtime.current_execution:
                # The worker that ran this cell is gone: its output stream is
                # lost, the kernel itself may still be running it.
                execution = await state.get_execution(runtime.current_execution) or {}
                kernel_id = execution.get("kernel_execution_id")
                if kernel_id and runtime.sandbox_id:
                    with contextlib.suppress(Exception):
                        await self._provider.interrupt(self.handle_of(runtime), kernel_id)
                job_id = execution.get("job_id")
                job = await state.get_job(job_id) if job_id else None
                if job:
                    await self._finish_without_running(
                        job, status="cancelled",
                        message="Nova restarted while this cell ran; run it again",
                    )
                await state.update(runtime.id, status="READY", current_execution="")
        try:
            sandboxes = await self._provider.list_runtime_sandboxes()
        except ProviderError:
            return
        live_sandbox_ids = set(sandboxes)
        for sandbox_id, runtime_id in sandboxes.items():
            if runtime_id.startswith(TASK_RUNTIME_PREFIX):
                continue  # a Python task run's sandbox; the task worker owns it
            if runtime_id not in by_id:
                logger.info("Removing orphaned notebook sandbox %s", sandbox_id)
                with contextlib.suppress(Exception):
                    await self._provider.kill(sandbox_id)
        for runtime in runtimes:
            if (
                runtime.sandbox_id
                and runtime.sandbox_id not in live_sandbox_ids
                and runtime.status not in state.STARTING_STATUSES
                and not await client.exists(state.lease_key(runtime.id))
            ):
                await self._cleanup(runtime, status="LOST", reason="sandbox_gone",
                                    error="The runtime stopped unexpectedly")


def _row_base(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["execution_id"],
        "notebook_id": job["notebook_id"],
        "user_name": job["user"],
        "runtime_id": job["runtime_id"],
        "run_id": job["run_id"],
        "cell_id": job["cell_id"],
        "cell_kind": job["cell_kind"],
        "sql_engine": job.get("engine") if job["cell_kind"] == "sql" else None,
        "source_revision": job["source_revision"],
    }


async def _audit(user: str, action: str, notebook_id: str, status: str, detail: str = "") -> None:
    with contextlib.suppress(Exception):
        await write_audit_log(
            event_type="notebook",
            user_name=user,
            action=action,
            object_type="notebook",
            object_name=notebook_id,
            status=status,
            error_message=detail or None,
        )
