"""API-side notebook runtime operations.

Requests only describe intent: they check ownership, the security context and
quotas, record the intent in Redis and wake the worker that owns the runtime.
The worker does the work, so a browser refresh, a dropped connection or a
backend restart never runs a cell twice and never loses one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from app.core.config import settings
from app.core.exceptions import NovaException, StorageError
from app.modules.access_control.security_context import SecurityContext
from app.modules.notebooks.documents import is_notebook_name, notebook_documents
from app.modules.notebooks.packages import (
    PackageNotAllowed,
    allowlist,
    sync_file,
    validate_request,
)
from app.modules.notebooks.repository import notebook_repository
from app.modules.notebooks.runtime import events, state
from app.modules.workspaces.repository import workspace_repository
from app.modules.workspaces.service import workspace_service
from app.modules.workspaces.spaces import Space, space_service

RUN_MODES = ("cell", "all", "above", "below")
_IDEMPOTENCY_SECONDS = 600
_LATEST_OUTPUT_BUDGET = 8 * 1024 * 1024


class NotebookRuntimeError(NovaException):
    """A runtime request that cannot be served; ``code`` is stable for the UI."""

    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        super().__init__(message, status_code=status_code)
        self.code = code


def _identity(user: dict[str, Any]) -> dict[str, Any]:
    """The caller's security context; refuses a session without one active role."""
    try:
        context = SecurityContext.from_session(user)
    except Exception as exc:  # noqa: BLE001 - any ambiguity fails closed
        raise NotebookRuntimeError(
            "Choose an active role before running a notebook",
            status_code=403,
            code="FORBIDDEN",
        ) from exc
    return {
        "user": user["username"],
        "session_id": user.get("session_id") or "",
        "active_role": context.active_role,
        "security_version": context.security_context_version,
    }


async def _require_notebook(
    user: dict[str, Any], notebook_id: str, *, edit: bool = False
) -> tuple[dict[str, Any], Space]:
    """The notebook and its workspace, if this person may open it (or change it)."""
    try:
        space = await space_service.for_entry(user, notebook_id, edit=edit)
    except StorageError as exc:
        if exc.status_code == 403:
            raise NotebookRuntimeError(
                "You can view this notebook but not change it", status_code=403, code="FORBIDDEN"
            ) from None
        raise NotebookRuntimeError(
            "Notebook not found", status_code=404, code="NOT_FOUND"
        ) from None
    entry = await workspace_repository.get_entry(space.namespace, notebook_id)
    if not entry or entry["entry_type"] != "file" or not is_notebook_name(entry["name"]):
        raise NotebookRuntimeError("Notebook not found", status_code=404, code="NOT_FOUND")
    return entry, space


def _not_ready(runtime: state.RuntimeState | None) -> NotebookRuntimeError:
    if runtime is None:
        return NotebookRuntimeError(
            "Start the runtime first", status_code=409, code="RUNTIME_NOT_READY"
        )
    return NotebookRuntimeError(
        "The runtime is not ready yet", status_code=409, code="RUNTIME_NOT_READY"
    )


class NotebookRuntimeService:
    # ── Status ────────────────────────────────────────────────────────────

    async def status(self, user: dict[str, Any], notebook_id: str) -> dict[str, Any]:
        _entry, space = await _require_notebook(user, notebook_id)
        ch = state.channel(notebook_id, user["username"])
        runtime = await state.active_runtime(ch)
        if runtime is not None:
            return {"runtime": runtime.public(), "limits": _limits()}
        last = await state.last_outcome(ch)
        return {
            "runtime": {
                "status": last.get("status") if last else "ABSENT",
                "reason": last.get("reason") if last else None,
                "error": (last.get("error") or None) if last else None,
            },
            "limits": _limits(),
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self, user: dict[str, Any], notebook_id: str) -> dict[str, Any]:
        identity = _identity(user)
        _entry, space = await _require_notebook(user, notebook_id)
        ch = state.channel(notebook_id, user["username"])
        if not settings.OPENSANDBOX_API_KEY:
            raise NotebookRuntimeError(
                "Notebook runtimes are not set up on this Nova deployment",
                status_code=503,
                code="RUNTIME_UNAVAILABLE",
            )
        client = state.redis()
        lock = client.lock(f"{state.PREFIX}start-lock:{ch}", timeout=30,
                           blocking_timeout=10)
        async with lock:
            existing = await state.active_runtime(ch)
            if existing is not None:
                return {"runtime": existing.public()}
            live = await state.live_runtimes()
            if len(live) >= settings.NOTEBOOK_MAX_ACTIVE_RUNTIMES:
                raise NotebookRuntimeError(
                    "Every notebook runtime is in use. Stop a runtime you no longer need, "
                    "or try again later.",
                    status_code=429,
                    code="QUOTA_EXCEEDED",
                )
            mine = [r for r in live if r.user == identity["user"]]
            if len(mine) >= settings.NOTEBOOK_MAX_RUNTIMES_PER_USER:
                raise NotebookRuntimeError(
                    f"You can run {settings.NOTEBOOK_MAX_RUNTIMES_PER_USER} notebooks at once. "
                    "Stop one of your other notebook runtimes first.",
                    status_code=429,
                    code="QUOTA_EXCEEDED",
                )
            runtime = state.RuntimeState(
                id=state.new_id("rt"),
                notebook_id=notebook_id,
                user=identity["user"],
                status="PROVISIONING",
                stage="Waiting for a runtime",
                session_id=identity["session_id"],
                active_role=identity["active_role"],
                security_version=identity["security_version"],
                namespace=space.namespace,
                files_writable=space.can_edit,
            )
            await state.save(runtime, ttl=settings.NOTEBOOK_MAX_LIFETIME_SECONDS + 3600)
            await client.set(state.active_key(ch), runtime.id,
                             ex=settings.NOTEBOOK_MAX_LIFETIME_SECONDS + 3600)
            await client.sadd(state.RUNTIMES, runtime.id)
            await client.delete(state.last_key(ch))
            await state.put_job(
                runtime.id, {"kind": "start", "notebook_id": notebook_id, "channel": ch}
            )
        await events.publish(ch, "runtime.status", runtime.public())
        return {"runtime": runtime.public()}

    async def stop(self, user: dict[str, Any], notebook_id: str) -> dict[str, Any]:
        _entry, space = await _require_notebook(user, notebook_id)
        ch = state.channel(notebook_id, user["username"])
        runtime = await state.active_runtime(ch)
        if runtime is None:
            return {"runtime": {"status": "STOPPED"}}
        if runtime.current_execution:
            await state.redis().set(state.cancel_key(runtime.current_execution), "1",
                                    ex=state.DAY)
        # A stop jumps the queue: queued cells are cancelled by the worker.
        job_id = state.new_id("job")
        await state.redis().set(
            state.job_key(job_id), json.dumps({"kind": "stop", "reason": "user",
                                               "job_id": job_id, "runtime_id": runtime.id}),
            ex=state.DAY,
        )
        await state.redis().lpush(state.queue_key(runtime.id), job_id)
        await state.redis().xadd(state.WAKE, {"runtime_id": runtime.id}, maxlen=1000,
                                 approximate=True)
        return {"runtime": {**runtime.public(), "status": "STOPPING"}}

    async def restart(self, user: dict[str, Any], notebook_id: str) -> dict[str, Any]:
        _entry, space = await _require_notebook(user, notebook_id)
        ch = state.channel(notebook_id, user["username"])
        runtime = await state.active_runtime(ch)
        if runtime is None or runtime.status not in {"READY", "BUSY"}:
            raise _not_ready(runtime)
        await self.cancel(user, notebook_id, None)
        await state.put_job(runtime.id, {"kind": "restart"})
        return {"runtime": {**runtime.public(), "status": "BOOTSTRAPPING",
                            "stage": "Restarting the kernel"}}

    async def metrics(self, user: dict[str, Any], notebook_id: str) -> dict[str, Any]:
        from app.modules.notebooks.runtime.opensandbox_provider import OpenSandboxProvider
        from app.modules.notebooks.runtime.provider import ProviderError, SandboxHandle

        _entry, space = await _require_notebook(user, notebook_id)

        ch = state.channel(notebook_id, user["username"])
        runtime = await state.active_runtime(ch)
        if runtime is None or not runtime.sandbox_id or runtime.status not in {"READY", "BUSY"}:
            return {"metrics": None}
        provider = OpenSandboxProvider()
        try:
            measured = await provider.metrics(SandboxHandle(runtime.sandbox_id,
                                                            runtime.context_id))
        except ProviderError:
            return {"metrics": None}
        finally:
            await provider.close()
        return {
            "metrics": {
                "cpu_count": measured.cpu_count,
                "cpu_percent": round(measured.cpu_percent, 1),
                "memory_used_mib": round(measured.memory_used_mib, 1),
                "memory_total_mib": round(measured.memory_total_mib, 1),
            }
        }

    # ── Executions ────────────────────────────────────────────────────────

    async def run(
        self,
        user: dict[str, Any],
        notebook_id: str,
        *,
        mode: str,
        cell_id: str | None,
        base_revision: int | None,
        on_error: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        identity = _identity(user)
        client = state.redis()
        idem = f"{state.PREFIX}idem:{notebook_id}:{idempotency_key}" if idempotency_key else None
        if idem:
            cached = await client.get(idem)
            if cached:
                return json.loads(cached)
        _entry, space = await _require_notebook(user, notebook_id)
        ch = state.channel(notebook_id, user["username"])
        _entry, document = await notebook_documents.get(space.namespace, notebook_id)
        if base_revision is not None and base_revision != document.revision:
            raise NotebookRuntimeError(
                "Save the notebook before running it",
                status_code=409,
                code="NOTEBOOK_CONFLICT",
            )
        runtime = await state.active_runtime(ch)
        if runtime is None or runtime.status not in state.LIVE_STATUSES - {"STOPPING"}:
            raise _not_ready(runtime)
        if mode not in RUN_MODES:
            raise NotebookRuntimeError("Unknown run mode", status_code=400, code="BAD_REQUEST")
        cells = document.cells
        if mode != "all":
            if not cell_id:
                raise NotebookRuntimeError("Choose a cell", status_code=400, code="BAD_REQUEST")
            index = document.index_of(cell_id)
            cells = {
                "cell": cells[index : index + 1],
                "above": cells[:index],
                "below": cells[index:],
            }[mode]
        runnable = [c for c in cells if c.kind != "markdown" and c.source.strip()]
        run_id = state.new_id("run")
        queued = []
        now = time.time()
        for position, cell in enumerate(runnable):
            execution_id = state.new_id("exe")
            job = {
                "kind": "execute",
                "job_id": state.new_id("job"),
                "execution_id": execution_id,
                "run_id": run_id,
                "notebook_id": notebook_id,
                "channel": ch,
                "user": identity["user"],
                "session_id": identity["session_id"],
                "active_role": identity["active_role"],
                "security_version": identity["security_version"],
                "cell_id": cell.id,
                "cell_kind": cell.kind,
                "source": cell.source,
                "name": cell.name,
                "engine": cell.engine,
                "source_revision": cell.source_revision,
                "document_revision": document.revision,
                "generation": runtime.generation,
                "on_error": "stop" if on_error != "continue" else "continue",
                "queued_at": now,
            }
            await state.set_execution(
                execution_id,
                status="queued",
                job_id=job["job_id"],
                cell_id=cell.id,
                notebook_id=notebook_id,
                queued_at=now,
            )
            await client.hset(state.inflight_key(ch), cell.id, execution_id)
            await client.expire(state.inflight_key(ch), state.DAY)
            await state.put_job(runtime.id, job)
            queued.append({"execution_id": execution_id, "cell_id": cell.id,
                           "source_revision": cell.source_revision})
            await events.publish(
                ch,
                "cell.queued",
                {"execution_id": execution_id, "cell_id": cell.id, "position": position,
                 "source_revision": cell.source_revision, "run_id": run_id},
            )
        response = {"run_id": run_id, "executions": queued, "status": "queued"}
        if idem:
            await client.set(idem, json.dumps(response), ex=_IDEMPOTENCY_SECONDS)
        return response

    async def cancel(
        self, user: dict[str, Any], notebook_id: str, execution_id: str | None
    ) -> dict[str, Any]:
        _entry, space = await _require_notebook(user, notebook_id)
        ch = state.channel(notebook_id, user["username"])
        client = state.redis()
        inflight = await client.hgetall(state.inflight_key(ch))
        targets = list(inflight.values()) if execution_id is None else [execution_id]
        cancelled = []
        for target in targets:
            record = await state.get_execution(target)
            if not record or record.get("notebook_id") != notebook_id:
                continue
            if record.get("status") in {"queued", "running"}:
                await client.set(state.cancel_key(target), "1", ex=state.DAY)
                cancelled.append(target)
        return {"cancelled": cancelled}

    async def latest(self, user: dict[str, Any], notebook_id: str) -> dict[str, Any]:
        """The newest outcome of every cell, with outputs, plus what is in flight."""
        username = user["username"]
        _entry, space = await _require_notebook(user, notebook_id)
        ch = state.channel(notebook_id, user["username"])
        rows = await notebook_repository.latest_executions(username, notebook_id)
        # Smallest outputs first, so many cells show theirs; the largest (an
        # interactive chart) are left for the page to fetch one by one.
        budget = _LATEST_OUTPUT_BUDGET
        loaded: list[dict[str, Any]] = []
        for row in sorted(rows, key=lambda item: int(item.get("output_bytes") or 0)):
            key = row.get("output_key")
            size = int(row.get("output_bytes") or 0)
            if not key:
                row["outputs"] = []
            elif size > budget:
                row["outputs"] = None
            else:
                budget -= size
                loaded.append(row)

        async def load_outputs(row: dict[str, Any]) -> None:
            row["outputs"] = await _read_outputs(row["output_key"])

        await asyncio.gather(*(load_outputs(row) for row in loaded))
        inflight = []
        for cell_id, execution_id in (
            await state.redis().hgetall(state.inflight_key(ch))
        ).items():
            record = await state.get_execution(execution_id)
            if record and record.get("status") in {"queued", "running"}:
                inflight.append({"cell_id": cell_id, "execution_id": execution_id,
                                 "status": record["status"],
                                 "execution_count": record.get("execution_count")})
        runtime = await state.active_runtime(ch)
        variables = None
        if runtime is not None:
            raw = await state.redis().get(f"{state.PREFIX}vars:{runtime.id}")
            variables = json.loads(raw) if raw else None
        return {
            "executions": [_public_execution(row) for row in rows],
            "inflight": inflight,
            "variables": variables,
            "generation": runtime.generation if runtime else None,
        }

    async def execution(
        self, user: dict[str, Any], notebook_id: str, execution_id: str
    ) -> dict[str, Any]:
        _entry, space = await _require_notebook(user, notebook_id)
        row = await notebook_repository.get_execution(user["username"], notebook_id, execution_id)
        if row is None:
            live = await state.get_execution(execution_id)
            if not live or live.get("notebook_id") != notebook_id:
                raise NotebookRuntimeError("Execution not found", status_code=404,
                                           code="NOT_FOUND")
            return {"execution_id": execution_id, "status": live.get("status"), "outputs": []}
        row["outputs"] = await _read_outputs(row["output_key"]) if row.get("output_key") else []
        return _public_execution(row)

    # ── Packages and files ────────────────────────────────────────────────

    async def packages(self, user: dict[str, Any], notebook_id: str) -> dict[str, Any]:
        _entry, space = await _require_notebook(user, notebook_id)
        manifest = await notebook_repository.get_packages(user["username"], notebook_id)
        return {
            "requested": manifest,
            "allowed": [
                {"name": item.name, "specifier": item.specifier or None}
                for item in sorted(allowlist().values(), key=lambda p: p.name)
            ],
            "base": [
                {"name": name, "version": version}
                for name, version in (
                    ("duckdb", "2.0.0"), ("pandas", "3.1.1"), ("polars", "2.1.0"),
                    ("pyarrow", "26.0.1"), ("numpy", "2.5.4"), ("matplotlib", "3.11.2"),
                )
            ],
        }

    async def install(
        self, user: dict[str, Any], notebook_id: str, *, name: str, version: str | None
    ) -> dict[str, Any]:
        identity = _identity(user)
        _entry, space = await _require_notebook(user, notebook_id)
        ch = state.channel(notebook_id, user["username"])
        try:
            package = validate_request(name, version)
        except PackageNotAllowed as exc:
            raise NotebookRuntimeError(str(exc), status_code=403,
                                       code="PACKAGE_NOT_ALLOWED") from exc
        runtime = await state.active_runtime(ch)
        if runtime is None or runtime.status not in {"READY", "BUSY"}:
            raise _not_ready(runtime)
        operation_id = state.new_id("pkg")
        await state.put_job(
            runtime.id,
            {"kind": "install", "operation_id": operation_id, "package": package,
             "notebook_id": notebook_id, "channel": ch, "user": identity["user"]},
        )
        return {"operation_id": operation_id, "package": package, "status": "queued"}

    async def remove_package(
        self, user: dict[str, Any], notebook_id: str, name: str
    ) -> dict[str, Any]:
        from app.modules.notebooks.packages import canonical

        _entry, space = await _require_notebook(user, notebook_id)

        manifest = await notebook_repository.get_packages(user["username"], notebook_id)
        kept = [item for item in manifest if canonical(item.get("name", "")) != canonical(name)]
        await notebook_repository.set_packages(user["username"], notebook_id, kept)
        return {"requested": kept, "restart_required": len(kept) != len(manifest)}

    async def upload(
        self,
        user: dict[str, Any],
        notebook_id: str,
        *,
        name: str,
        data: bytes,
        subfolder: str = "data",
    ) -> dict[str, Any]:
        """Store an uploaded data file next to the notebook and copy it in."""
        from app.modules.notebooks.uploads import validate_upload

        entry, space = await _require_notebook(user, notebook_id, edit=True)

        clean_name = validate_upload(name, data)
        base = entry.get("parent_path") or ""
        parent = "/".join(p for p in [base, subfolder] if p)
        from app.modules.notebooks.packages import _ensure_folders
        from app.modules.workspaces.actor import acting_as

        with acting_as(user["username"]):
            await _ensure_folders(space.namespace, parent)
            stored = await workspace_service.create_file_bytes(
                space.namespace, parent, clean_name, data
            )
        # The file is saved either way; a runtime that is not live copies it
        # in when it next starts.
        with contextlib.suppress(Exception):
            await sync_file(user["username"], notebook_id, stored["id"])
        rel = "/".join(p for p in [subfolder, clean_name] if p)
        return {"entry": stored, "runtime_path": f"/workspace/{rel}", "relative_path": rel}


def _limits() -> dict[str, Any]:
    return {
        "cpu": settings.NOTEBOOK_RUNTIME_CPU,
        "memory": settings.NOTEBOOK_RUNTIME_MEMORY,
        "idle_timeout_seconds": settings.NOTEBOOK_IDLE_TIMEOUT_SECONDS,
        "cell_timeout_seconds": settings.NOTEBOOK_CELL_TIMEOUT_SECONDS,
        "preview_rows": settings.NOTEBOOK_PREVIEW_ROWS,
    }


async def _read_outputs(key: str) -> list[dict[str, Any]] | None:
    try:
        return json.loads(await workspace_service.read_internal_object(key))
    except Exception:  # noqa: BLE001 - an expired or missing payload shows as unavailable
        return None


def _public_execution(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "execution_id": row["id"],
        "cell_id": row["cell_id"],
        "cell_kind": row["cell_kind"],
        "sql_engine": row.get("sql_engine"),
        "source_revision": row["source_revision"],
        "generation": row.get("generation"),
        "runtime_id": row.get("runtime_id"),
        "execution_count": row.get("execution_count"),
        "status": row["status"],
        "active_role": row.get("active_role"),
        "error_name": row.get("error_name"),
        "error_message": row.get("error_message"),
        "truncated": row.get("truncated"),
        "summary": row.get("summary"),
        "queued_at": row.get("queued_at"),
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "duration_ms": row.get("duration_ms"),
        "outputs": row.get("outputs"),
    }


notebook_runtime_service = NotebookRuntimeService()
