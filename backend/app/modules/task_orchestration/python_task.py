"""Run a ``LANGUAGE PYTHON`` task in a one-run sandbox with novaduck.

A Python task is a script, not a procedure: ``CREATE TASK ... LANGUAGE PYTHON
PACKAGES = ('name==1.2.3') AS $$ ... $$``. Each run gets its own sandbox from
the notebook runtime image (OpenSandbox), so it has what a notebook kernel
has: ``novaduck.connect()`` attached to every catalog the run's role sees,
``con.write`` / ``con.merge_into`` / ``con.put`` handled by Nova as the run's
user, and pandas, pyarrow, polars and DuckDB preinstalled.

Identity follows the SQL task rules:

* a **manual** run (Run button, ``EXECUTE TASK``) reads and writes as the
  person who started it, under the task's owner role. Their Nova session must
  still have that role active, or the run fails rather than switch roles;
* a **scheduled** run acts as the service account bound to the owner role.
  The worker first proves it may impersonate that account with that role
  (the same check a scheduled SQL task makes), then hands the sandbox a token
  for a delegated session the proxy opens through the same impersonation.

The sandbox gets a task data token (purpose ``task``) bound to that session
and to this run's sandbox: it stops working when the run ends. Egress is the
proxy only; the package index is reachable only while the task's packages
install. Packages are checked against the deployment's allowlist again at run
time, so removing a package from the allowlist stops tasks that need it.

Sandboxes are removed when the run ends. A worker that dies mid-run leaves the
sandbox to expire at its lifetime (timeout plus a margin); the task reconciler
fails the node when its heartbeat stops.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.modules.access_control import data_tokens
from app.modules.notebooks.runtime import kernel_calls, rpc
from app.modules.notebooks.runtime.outputs import payload_of, strip_ansi
from app.modules.notebooks.runtime.provider import (
    TASK_RUNTIME_PREFIX,
    KernelEvent,
    ProviderError,
    SandboxHandle,
    SandboxProvider,
    SandboxSpec,
)
from app.modules.task_orchestration.execution import (
    DelegateExecutor,
    NodeExecutionError,
    TaskSpec,
    _dict_cursor,
    _redact,
)

logger = logging.getLogger(__name__)

#: Printed output kept on the node run (its tail), in characters.
OUTPUT_TAIL_CHARS = 16_000
#: Traceback lines kept in the node's error.
TRACEBACK_LINES = 12
HEARTBEAT_SECONDS = 10.0

_provider: SandboxProvider | None = None


def default_provider() -> SandboxProvider:
    """The process's OpenSandbox client, created on first use."""
    global _provider
    if _provider is None:
        from app.modules.notebooks.runtime.opensandbox_provider import OpenSandboxProvider

        _provider = OpenSandboxProvider()
    return _provider


@dataclass(frozen=True)
class PythonTaskConfig:
    runtime_version: str
    packages: tuple[str, ...]

    @classmethod
    def of(cls, task: dict[str, Any]) -> PythonTaskConfig:
        raw = task.get("handler_config")
        config = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return cls(
            runtime_version=str(config.get("runtime_version") or ""),
            packages=tuple(str(item) for item in config.get("packages") or []),
        )


def install_requests(packages: tuple[str, ...]) -> list[dict[str, Any]]:
    """Each ``name[==version]`` pin as an install request, allowlist checked now."""
    from app.modules.notebooks.packages import PackageNotAllowed, validate_request

    requests: list[dict[str, Any]] = []
    for pin in packages:
        name, _, version = pin.partition("==")
        try:
            requests.append(validate_request(name, version or None))
        except PackageNotAllowed as exc:
            raise NodeExecutionError(f"Package {pin!r} cannot be installed: {exc}") from None
    return requests


class _Output:
    """The run's printed output, keeping only its tail."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._size = 0

    def add(self, text: str) -> None:
        if not text:
            return
        self._parts.append(text)
        self._size += len(text)
        # Drop whole chunks from the front while the rest still covers the tail
        # (ANSI codes are stripped later, so keep a margin for them).
        while len(self._parts) > 1 and self._size - len(self._parts[0]) >= OUTPUT_TAIL_CHARS * 2:
            self._size -= len(self._parts.pop(0))

    def text(self) -> str:
        joined = strip_ansi("".join(self._parts))
        if len(joined) > OUTPUT_TAIL_CHARS:
            joined = "…" + joined[-OUTPUT_TAIL_CHARS:]
        return _redact(joined)


async def run_python_task(
    task: dict[str, Any],
    spec: TaskSpec,
    owner: str,
    executor: DelegateExecutor,
    *,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    provider: SandboxProvider | None = None,
) -> str:
    """Run the task's Python once; return its printed output or raise."""
    source = str(task.get("definition") or "")
    if not source.strip():
        raise NodeExecutionError(f"task {spec.name!r} has no Python code")
    config = PythonTaskConfig.of(task)
    from app.modules.task_orchestration.ddl import PYTHON_RUNTIME_VERSIONS

    if config.runtime_version and config.runtime_version not in PYTHON_RUNTIME_VERSIONS:
        raise NodeExecutionError(
            f"Python {config.runtime_version} is no longer available for tasks"
        )
    packages = install_requests(config.packages)
    if not settings.OPENSANDBOX_API_KEY:
        raise NodeExecutionError(
            "Python tasks run in a notebook runtime, which is not set up on this deployment"
        )
    if not settings.NOTEBOOK_PROXY_ADDRESS:
        raise NodeExecutionError(
            "Python tasks read Nova data through the MySQL proxy, which runtimes "
            "cannot reach on this deployment (NOTEBOOK_PROXY_ADDRESS)"
        )
    if not spec.active_role:
        raise NodeExecutionError(f"task {spec.name!r} has no execution role")

    sandboxes = provider or default_provider()
    runtime_id = f"{TASK_RUNTIME_PREFIX}{uuid.uuid4().hex}"
    output = _Output()
    async with _run_identity(owner, spec, executor) as identity:
        return await _run_in_sandbox(
            sandboxes,
            runtime_id,
            task,
            spec,
            owner,
            identity,
            packages,
            source,
            output,
            heartbeat,
        )


@dataclass
class _Identity:
    session: data_tokens.BiSession
    #: Scheduled runs: the verified impersonated connection Nova uses for the
    #: kernel's write requests (con.write, con.merge_into, con.put).
    connection: Any = None


@asynccontextmanager
async def _run_identity(
    owner: str, spec: TaskSpec, executor: DelegateExecutor
) -> AsyncIterator[_Identity]:
    ttl = settings.TASK_PYTHON_TIMEOUT_SECONDS + 600
    role = spec.active_role
    if not role:
        raise NodeExecutionError(f"task {spec.name!r} has no execution role")
    if spec.execution_session_id:
        try:
            session = await data_tokens.resolve_session(spec.execution_session_id)
        except data_tokens.BiCredentialError:
            raise NodeExecutionError(
                "The session that started this run has ended; sign in and run again"
            ) from None
        if session.username != owner:
            raise NodeExecutionError("The run's session belongs to another user")
        if session.active_role != spec.active_role:
            raise NodeExecutionError(
                f"Your active role changed to {session.active_role!r}; activate "
                f"{spec.active_role!r} and run the task again"
            )
        yield _Identity(session=session)
        return
    # Scheduled: prove the worker may act as the bound account with the owner
    # role before any token exists, and keep that connection for write requests.
    async with executor._owner_conn(owner) as connection:
        async with _dict_cursor(connection) as cur:
            await executor._prepare_task_session(cur, spec)
        session_id = await data_tokens.create_delegated_session(owner, role, ttl)
        try:
            session = await data_tokens.resolve_session(session_id)
            yield _Identity(session=session, connection=connection)
        finally:
            with contextlib.suppress(Exception):
                await data_tokens.end_delegated_session(session_id)


async def _run_in_sandbox(
    sandboxes: SandboxProvider,
    runtime_id: str,
    task: dict[str, Any],
    spec: TaskSpec,
    owner: str,
    identity: _Identity,
    packages: list[dict[str, Any]],
    source: str,
    output: _Output,
    heartbeat: Callable[[], Awaitable[None]] | None,
) -> str:
    from app.modules.notebooks.runtime.worker import owner_hash, proxy_port

    redis = data_tokens._redis()
    subject_key = f"{data_tokens.TASK.subject_key_prefix}{runtime_id}"
    lifetime = settings.TASK_PYTHON_TIMEOUT_SECONDS + 600
    await redis.set(subject_key, str(task.get("id") or ""), ex=lifetime)
    deadline = time.monotonic() + settings.TASK_PYTHON_TIMEOUT_SECONDS
    handle: SandboxHandle | None = None
    try:
        sandbox_spec = SandboxSpec(
            runtime_id=runtime_id,
            # Sandbox metadata values are label-shaped: letters, digits, '-', '_', '.'.
            notebook_id=f"task-{task.get('id')}"[:63],
            owner_hash=owner_hash(owner),
            image=settings.NOTEBOOK_RUNTIME_IMAGE,
            cpu=settings.NOTEBOOK_RUNTIME_CPU,
            memory=settings.NOTEBOOK_RUNTIME_MEMORY,
            lifetime_seconds=lifetime,
            env={"JUPYTER_TOKEN": secrets.token_urlsafe(24)},
            egress=[(settings.NOTEBOOK_PROXY_ADDRESS, proxy_port())],
            ready_timeout_seconds=min(settings.NOTEBOOK_START_TIMEOUT_SECONDS, 300),
        )
        try:
            handle = await _with_heartbeat(
                asyncio.wait_for(
                    sandboxes.create(sandbox_spec),
                    timeout=settings.NOTEBOOK_START_TIMEOUT_SECONDS,
                ),
                heartbeat,
            )
        except TimeoutError:
            raise NodeExecutionError("The Python runtime did not start in time") from None
        except ProviderError as exc:
            raise NodeExecutionError(f"The Python runtime could not start: {exc}") from None
        await _bootstrap(sandboxes, handle, runtime_id, identity, heartbeat)
        if packages:
            await _install(sandboxes, handle, packages, output, heartbeat)
        await _run_code(sandboxes, handle, identity, source, output, deadline, heartbeat)
        return output.text()
    finally:
        if handle is not None:
            try:
                await sandboxes.kill(handle.sandbox_id)
            except ProviderError:
                logger.warning("Sandbox of Python task run %s could not be removed now", runtime_id)
        with contextlib.suppress(Exception):
            await data_tokens.revoke_subject(data_tokens.TASK, runtime_id)
        with contextlib.suppress(Exception):
            await redis.delete(subject_key)


async def _with_heartbeat(awaitable: Awaitable[Any], heartbeat) -> Any:
    """Await while stamping the node alive, so a slow start is not abandoned."""
    work = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _ = await asyncio.wait({work}, timeout=HEARTBEAT_SECONDS)
            if done:
                return work.result()
            if heartbeat is not None:
                await heartbeat()
    finally:
        if not work.done():
            work.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await work


async def _call(
    sandboxes: SandboxProvider,
    handle: SandboxHandle,
    code: str,
    heartbeat,
    on_stream: Callable[[KernelEvent], None] | None = None,
) -> dict[str, Any]:
    """A trusted ``nova_runtime`` call; its payload, or ProviderError."""
    payload: dict[str, Any] = {}
    error: dict[str, Any] = {}

    async def sink(event: KernelEvent) -> None:
        if event.type in {"stdout", "stderr"} and on_stream is not None:
            on_stream(event)
        elif event.type == "result":
            found = payload_of(event.bundle)
            if found is not None:
                payload.update(found)
        elif event.type == "error":
            error.update(event.error)

    await _with_heartbeat(sandboxes.run(handle, code, sink), heartbeat)
    if error and not payload:
        raise ProviderError(f"Runtime setup failed: {error.get('name')}")
    return payload


async def _bootstrap(
    sandboxes: SandboxProvider,
    handle: SandboxHandle,
    runtime_id: str,
    identity: _Identity,
    heartbeat,
) -> None:
    from app.modules.notebooks.runtime.worker import proxy_port

    issued = await data_tokens.mint(
        identity.session,
        purpose=data_tokens.TASK,
        ttl=settings.TASK_PYTHON_TIMEOUT_SECONDS + 600,
        max_per_principal=max(settings.WORKER_MAX_PARALLEL_NODES * 2, 4),
        subject=runtime_id,
    )
    path = f"/tmp/nova/.cred-{secrets.token_hex(12)}.json"
    body = json.dumps(
        {
            "host": settings.NOTEBOOK_PROXY_ADDRESS,
            "port": proxy_port(),
            "user": issued.username,
            "token": issued.token,
            "catalogs": await _catalogs(identity),
        }
    ).encode()
    try:
        await sandboxes.write_file(handle, path, body)
        result = await _call(
            sandboxes,
            handle,
            kernel_calls.bootstrap(
                {
                    "workspace": "/workspace",
                    "preview_rows": settings.NOTEBOOK_PREVIEW_ROWS,
                    "memory_limit": settings.NOTEBOOK_DUCKDB_MEMORY_LIMIT,
                    "threads": settings.NOTEBOOK_DUCKDB_THREADS,
                    "request_timeout": settings.TASK_PYTHON_TIMEOUT_SECONDS,
                    "credential_path": path,
                }
            ),
            heartbeat,
        )
    except ProviderError as exc:
        raise NodeExecutionError(f"The Python runtime did not start: {exc}") from None
    if not result.get("ready"):
        raise NodeExecutionError("The Python runtime's DuckDB did not start")
    if not (result.get("nova") or {}).get("attached"):
        raise NodeExecutionError("The Python runtime could not reach Nova data")


async def _catalogs(identity: _Identity) -> list[dict[str, str]]:
    """Catalogs novaduck attaches: the ones the run's role sees and that answer."""
    if identity.connection is None:
        from app.modules.notebooks.runtime.worker import visible_catalogs

        return await visible_catalogs(identity.session)
    from app.modules.notebooks.runtime.worker import CATALOG_CHECK_SECONDS, MAX_ATTACHED_CATALOGS

    catalogs: list[dict[str, str]] = [{"name": "default_catalog", "type": "Internal"}]
    try:
        async with _dict_cursor(identity.connection) as cur:
            await cur.execute("SHOW CATALOGS")
            rows = await cur.fetchall() or []
            for row in rows:
                values = list(row.values()) if isinstance(row, dict) else list(row)
                name = str(values[0]) if values else ""
                if not name or name == "default_catalog":
                    continue
                item = {"name": name, "type": str(values[1]) if len(values) > 1 else ""}
                try:
                    escaped = name.replace("`", "``")
                    await asyncio.wait_for(
                        cur.execute(f"SHOW DATABASES FROM `{escaped}`"),
                        timeout=CATALOG_CHECK_SECONDS,
                    )
                    await cur.fetchall()
                except Exception as exc:  # noqa: BLE001 - listed with its error
                    item["error"] = type(exc).__name__
                catalogs.append(item)
    except Exception:  # noqa: BLE001 - Nova data still works without extra catalogs
        logger.warning("Could not list catalogs for a Python task run", exc_info=True)
    return catalogs[:MAX_ATTACHED_CATALOGS]


async def _install(
    sandboxes: SandboxProvider,
    handle: SandboxHandle,
    packages: list[dict[str, Any]],
    output: _Output,
    heartbeat,
) -> None:
    hosts = [h.strip() for h in settings.NOTEBOOK_PACKAGE_INDEX_HOSTS.split(",") if h.strip()]
    log = _Output()
    await sandboxes.allow_hosts(handle, hosts)
    try:
        result = await _call(
            sandboxes,
            handle,
            kernel_calls.install(packages),
            heartbeat,
            on_stream=lambda event: log.add(event.text),
        )
    except ProviderError as exc:
        raise NodeExecutionError(f"Packages could not be installed: {exc}") from None
    finally:
        with contextlib.suppress(ProviderError):
            await sandboxes.deny_hosts(handle, hosts)
    if not result.get("ok"):
        failed = ", ".join(str(item.get("module")) for item in result.get("failed_imports") or [])
        detail = f" (could not import {failed})" if failed else ""
        raise NodeExecutionError(
            f"Packages could not be installed{detail}: "
            f"{result.get('error') or 'pip failed'}\n{log.text()[-2000:]}".strip()
        )
    versions = result.get("versions") or {}
    installed = ", ".join(f"{name} {version}" for name, version in sorted(versions.items()))
    if installed:
        output.add(f"[nova] installed {installed}\n")


async def _run_code(
    sandboxes: SandboxProvider,
    handle: SandboxHandle,
    identity: _Identity,
    source: str,
    output: _Output,
    deadline: float,
    heartbeat,
) -> None:
    error: dict[str, Any] = {}
    markers = rpc.MarkerReader()
    kernel_execution: dict[str, str] = {}

    async def sink(event: KernelEvent) -> None:
        if event.type == "init" and event.text:
            kernel_execution["id"] = event.text
        elif event.type == "stderr":
            text, requests = markers.feed(event.text)
            for request_id in requests:
                # The kernel waits for the result file, so the stream waits too.
                await _rpc(sandboxes, handle, identity, request_id)
            output.add(text)
        elif event.type == "stdout":
            output.add(event.text)
        elif event.type == "result":
            text = (event.bundle or {}).get("text/plain") or event.text
            if text and payload_of(event.bundle) is None:
                output.add(f"{text}\n")
        elif event.type == "error":
            error.update(event.error)

    run = asyncio.create_task(sandboxes.run(handle, source, sink))
    try:
        while not run.done():
            await asyncio.wait({run}, timeout=HEARTBEAT_SECONDS)
            if run.done():
                break
            if heartbeat is not None:
                await heartbeat()
            if time.monotonic() > deadline:
                if kernel_execution.get("id"):
                    with contextlib.suppress(ProviderError):
                        await sandboxes.interrupt(handle, kernel_execution["id"])
                raise NodeExecutionError(
                    f"The Python task did not finish within "
                    f"{settings.TASK_PYTHON_TIMEOUT_SECONDS} seconds"
                )
        exc = run.exception()
    finally:
        if not run.done():
            run.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await run
    if exc is not None:
        if isinstance(exc, ProviderError):
            raise NodeExecutionError(f"The Python runtime failed: {exc}") from None
        raise NodeExecutionError(type(exc).__name__) from exc
    if error:
        name = str(error.get("name") or "Error")
        value = str(error.get("value") or "")
        trace = [strip_ansi(str(line)) for line in error.get("traceback") or []]
        tail = "\n".join(trace[-TRACEBACK_LINES:])
        parts = [f"{name}: {value}" if value else name]
        if tail:
            parts.append(tail)
        printed = output.text()
        if printed:
            parts.append(f"--- output ---\n{printed[-4000:]}")
        raise NodeExecutionError(_redact("\n".join(parts)))


async def _rpc(
    sandboxes: SandboxProvider, handle: SandboxHandle, identity: _Identity, request_id: str
) -> None:
    """A kernel write request, done as the run's user (see notebooks/runtime/rpc.py)."""
    if identity.connection is None:
        await rpc.handle(sandboxes, handle, identity.session.session_id, request_id)
        return
    from app.modules.query.service import delegated_connection

    # A delegated session has no password: Nova's own writes for it run on the
    # verified impersonated connection.
    with delegated_connection(identity.session.username, identity.connection):
        await rpc.handle(sandboxes, handle, identity.session.session_id, request_id)


__all__ = ["PythonTaskConfig", "default_provider", "install_requests", "run_python_task"]
