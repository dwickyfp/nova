"""Notebook packages and files.

Packages: a notebook keeps a manifest of requested packages (in
``NOVA_SYSTEM``), not an installed filesystem. Only packages on the deployment's
allowlist (``NOTEBOOK_PACKAGE_ALLOWLIST``) can be requested; they install into
the kernel's own interpreter, with the package index reachable only while the
install runs, and the manifest changes only after pip succeeded and the
package imports. Every new runtime restores the manifest before it is Ready.

Files: the notebook's Workspace folder is copied into ``/workspace`` when a
runtime starts (bounded), and files the code writes to ``/workspace/outputs``
are copied back to the Workspace when the runtime stops or on request. The
Workspace stays the single source of truth.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import posixpath
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.common.audit import write_audit_log
from app.core.config import settings
from app.modules.notebooks.repository import notebook_repository
from app.modules.notebooks.runtime import events, kernel_calls, state
from app.modules.notebooks.runtime.provider import KernelEvent, ProviderError
from app.modules.workspaces.actor import acting_as
from app.modules.workspaces.service import workspace_service

if TYPE_CHECKING:
    from app.modules.notebooks.runtime.worker import NotebookRuntimeWorker

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$")
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.!+_-]{0,40}$")
_SPECIFIER = re.compile(r"^(?:(?:==|>=|<=|~=|!=|<|>)[0-9][0-9A-Za-z.*+!_-]*,?){1,4}$")
#: Import names that differ from the distribution name.
_IMPORT_NAMES = {
    "scikit-learn": "sklearn",
    "pyyaml": "yaml",
    "beautifulsoup4": "bs4",
    "pillow": "PIL",
    "python-dateutil": "dateutil",
}
#: What a runtime copies in from the Workspace at start.
_HYDRATE_MAX_FILES = 500
_HYDRATE_MAX_BYTES = 200 * 1024 * 1024
_HYDRATE_FILE_MAX_BYTES = 100 * 1024 * 1024


class PackageNotAllowed(ValueError):
    pass


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True)
class AllowedPackage:
    name: str
    specifier: str


def allowlist() -> dict[str, AllowedPackage]:
    allowed: dict[str, AllowedPackage] = {}
    for raw in settings.NOTEBOOK_PACKAGE_ALLOWLIST.split(","):
        raw = raw.strip()
        if not raw:
            continue
        match = re.match(r"^([A-Za-z0-9._-]+)(.*)$", raw)
        if not match:
            continue
        name, specifier = match.group(1), match.group(2).replace(" ", "")
        if specifier and not _SPECIFIER.match(specifier):
            logger.error("Ignoring malformed allowlist entry %r", raw)
            continue
        allowed[canonical(name)] = AllowedPackage(name=canonical(name), specifier=specifier)
    return allowed


def validate_request(name: str, version: str | None) -> dict[str, Any]:
    """A package request the runtime may install, or :class:`PackageNotAllowed`."""
    name = (name or "").strip()
    if not _NAME.match(name):
        raise PackageNotAllowed("A package name holds letters, digits, '.', '-' and '_'")
    entry = allowlist().get(canonical(name))
    if entry is None:
        raise PackageNotAllowed(
            f"'{name}' is not on this deployment's approved package list. "
            "Ask an administrator to approve it."
        )
    specifier = entry.specifier
    if version:
        version = version.strip()
        if not _VERSION.match(version):
            raise PackageNotAllowed("A version looks like 1.2.3")
        specifier = f"=={version}" + (f",{entry.specifier}" if entry.specifier else "")
    return {
        "name": entry.name,
        "specifier": specifier,
        "import_name": _IMPORT_NAMES.get(entry.name, entry.name.replace("-", "_")),
    }


async def _install(worker: NotebookRuntimeWorker, runtime: state.RuntimeState,
                   packages: list[dict[str, Any]], *, job: dict | None) -> dict[str, Any]:
    hosts = [h.strip() for h in settings.NOTEBOOK_PACKAGE_INDEX_HOSTS.split(",") if h.strip()]
    handle = worker.handle_of(runtime)
    result: dict[str, Any] = {}
    error: dict[str, Any] = {}

    async def sink(event: KernelEvent) -> None:
        from app.modules.notebooks.runtime.outputs import payload_of, strip_ansi

        if event.type in {"stdout", "stderr"} and job is not None:
            await events.publish(
                runtime.channel,
                "package.log",
                {"operation_id": job["operation_id"], "text": strip_ansi(event.text)[-4000:]},
            )
        elif event.type == "result":
            found = payload_of(event.bundle)
            if found is not None:
                result.update(found)
        elif event.type == "error":
            error.update(event.error)

    await worker._provider.allow_hosts(handle, hosts)
    try:
        await worker._provider.run(handle, kernel_calls.install(packages), sink)
    finally:
        with contextlib.suppress(ProviderError):
            await worker._provider.deny_hosts(handle, hosts)
    if error and not result:
        result = {"ok": False, "error": error.get("value") or "The install failed"}
    return result


async def run_install(worker: NotebookRuntimeWorker, job: dict[str, Any]) -> None:
    runtime = await state.load(job["runtime_id"])
    operation_id = job["operation_id"]
    if runtime is None or runtime.status not in {"READY", "BUSY"}:
        await events.publish(
            state.job_channel(job),
            "package.finished",
            {"operation_id": operation_id, "ok": False, "error": "The runtime is not running"},
        )
        return
    package = job["package"]
    await state.update(runtime.id, status="BUSY", stage=f"Installing {package['name']}")
    await events.publish(runtime.channel, "runtime.status",
                         {**runtime.public(), "status": "BUSY",
                          "stage": f"Installing {package['name']}"})
    try:
        result = await _install(worker, runtime, [package], job=job)
    except ProviderError as exc:
        result = {"ok": False, "error": str(exc)}
    ok = bool(result.get("ok"))
    if ok:
        manifest = [
            item for item in await notebook_repository.get_packages(runtime.user,
                                                                     runtime.notebook_id)
            if canonical(item.get("name", "")) != package["name"]
        ]
        version = (result.get("versions") or {}).get(package["name"])
        manifest.append({**package, "installed_version": version})
        await notebook_repository.set_packages(runtime.user, runtime.notebook_id, manifest)
    with contextlib.suppress(Exception):
        await write_audit_log(
            event_type="notebook",
            user_name=runtime.user,
            action="INSTALL_PACKAGE",
            object_type="notebook",
            object_name=runtime.notebook_id,
            status="SUCCESS" if ok else "FAILED",
            error_message=f"{package['name']}{package.get('specifier') or ''}",
        )
    await state.update(runtime.id, status="READY", stage="")
    latest = await state.load(runtime.id)
    if latest is not None:
        await events.publish(runtime.channel, "runtime.status", latest.public())
    await events.publish(
        runtime.channel,
        "package.finished",
        {
            "operation_id": operation_id,
            "ok": ok,
            "name": package["name"],
            "version": (result.get("versions") or {}).get(package["name"]),
            "restart_recommended": bool(result.get("restart_recommended")),
            "error": None if ok else str(result.get("error") or "The install failed"),
            "failed_imports": result.get("failed_imports") or [],
        },
    )


async def restore_packages(
    worker: NotebookRuntimeWorker, runtime: state.RuntimeState, *, reinstall: bool = True
) -> None:
    """Install the notebook's manifest into a new runtime before it is Ready.

    After a kernel restart the packages are still installed in the sandbox, so
    nothing is reinstalled.
    """
    if not reinstall:
        return
    manifest = await notebook_repository.get_packages(runtime.user, runtime.notebook_id)
    if not manifest:
        return
    allowed = allowlist()
    packages = []
    for item in manifest:
        name = canonical(str(item.get("name") or ""))
        if name not in allowed:
            continue  # removed from the allowlist since it was installed
        version = item.get("installed_version")
        specifier = f"=={version}" if version and _VERSION.match(str(version)) else (
            item.get("specifier") or ""
        )
        packages.append({"name": name, "specifier": specifier,
                         "import_name": item.get("import_name") or name.replace("-", "_")})
    if not packages:
        return
    await worker._status(runtime, "BOOTSTRAPPING",
                         f"Restoring {len(packages)} package{'s' if len(packages) != 1 else ''}")
    result = await _install(worker, runtime, packages, job=None)
    if not result.get("ok"):
        await events.publish(
            runtime.channel,
            "package.finished",
            {"operation_id": "restore", "ok": False,
             "error": "Some notebook packages could not be restored"},
        )


def _relative(path: str, folder: str) -> str | None:
    if folder and not path.startswith(folder + "/"):
        return None
    rel = path[len(folder) + 1 :] if folder else path
    rel = posixpath.normpath(rel)
    if rel.startswith("../") or rel == ".." or rel.startswith("/"):
        return None
    return rel


async def hydrate_files(worker: NotebookRuntimeWorker, runtime: state.RuntimeState) -> None:
    """Copy the notebook's Workspace folder into ``/workspace`` (bounded)."""
    entries = await workspace_service.list_entries(runtime.files_namespace)
    notebook = next((e for e in entries if e["id"] == runtime.notebook_id), None)
    if notebook is None:
        return
    folder = notebook.get("parent_path") or ""
    selected: list[tuple[dict, str]] = []
    total = 0
    for entry in entries:
        if entry["entry_type"] != "file" or entry["id"] == runtime.notebook_id:
            continue
        rel = _relative(entry["path"], folder)
        if rel is None or rel.startswith("."):
            continue
        size = int(entry.get("size_bytes") or 0)
        if size > _HYDRATE_FILE_MAX_BYTES or total + size > _HYDRATE_MAX_BYTES:
            continue
        if len(selected) >= _HYDRATE_MAX_FILES:
            break
        selected.append((entry, rel))
        total += size
    gate = asyncio.Semaphore(8)

    async def copy(entry: dict, rel: str) -> None:
        async with gate:
            try:
                _entry, data = await workspace_service.read_entry_bytes(
                    runtime.files_namespace, entry["id"]
                )
                await worker._provider.write_file(
                    worker.handle_of(runtime), f"/workspace/{rel}", data
                )
            except Exception:  # noqa: BLE001 - one unreadable file does not stop the start
                logger.info("Workspace file could not be copied into runtime %s", runtime.id)

    await asyncio.gather(*(copy(entry, rel) for entry, rel in selected))


async def sync_file(user: str, notebook_id: str, entry_id: str) -> None:
    """Copy one newly uploaded Workspace file into the uploader's live runtime."""
    from app.modules.notebooks.runtime.opensandbox_provider import OpenSandboxProvider

    runtime = await state.active_runtime(state.channel(notebook_id, user))
    if runtime is None or runtime.status not in {"READY", "BUSY"}:
        return
    entries = await workspace_service.list_entries(runtime.files_namespace)
    notebook = next((e for e in entries if e["id"] == notebook_id), None)
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if notebook is None or entry is None:
        return
    rel = _relative(entry["path"], notebook.get("parent_path") or "")
    if rel is None:
        return
    _entry, data = await workspace_service.read_entry_bytes(runtime.files_namespace, entry_id)
    from app.modules.notebooks.runtime.provider import SandboxHandle

    provider = OpenSandboxProvider()
    try:
        await provider.write_file(
            SandboxHandle(runtime.sandbox_id, runtime.context_id), f"/workspace/{rel}", data
        )
    finally:
        await provider.close()


async def flush_outputs(worker: NotebookRuntimeWorker, runtime: state.RuntimeState) -> int:
    """Copy ``/workspace/outputs`` back into the notebook's Workspace folder."""
    if runtime.status not in {"READY", "BUSY", "STOPPING"} or not runtime.context_id:
        return 0
    listing = await worker.call(runtime, kernel_calls.list_outputs())
    files = listing.get("files") or []
    if not files or not runtime.files_writable:
        # A view-only member's runtime never writes into a shared workspace.
        return 0
    entries = await workspace_service.list_entries(runtime.files_namespace)
    notebook = next((e for e in entries if e["id"] == runtime.notebook_id), None)
    if notebook is None:
        return 0
    base = notebook.get("parent_path") or ""
    copied = 0
    for item in files:
        rel = posixpath.normpath(str(item.get("path") or ""))
        if rel.startswith("..") or rel.startswith("/"):
            continue
        data = await worker._provider.read_file(
            worker.handle_of(runtime), f"/workspace/outputs/{rel}"
        )
        parent = "/".join(p for p in [base, "outputs", posixpath.dirname(rel)] if p)
        await _ensure_folders(runtime.files_namespace, parent)
        with acting_as(runtime.user):
            await workspace_service.create_file_bytes(
                runtime.files_namespace, parent, posixpath.basename(rel), data, replace=True
            )
        copied += 1
    return copied


async def _ensure_folders(user: str, path: str) -> None:
    existing = {e["path"] for e in await workspace_service.list_entries(user)}
    parts = [p for p in path.split("/") if p]
    for index in range(len(parts)):
        current = "/".join(parts[: index + 1])
        if current in existing:
            continue
        parent = "/".join(parts[:index])
        with contextlib.suppress(Exception):
            await workspace_service.create_folder(user, parent, parts[index])
        existing.add(current)
