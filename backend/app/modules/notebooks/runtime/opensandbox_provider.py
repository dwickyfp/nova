"""OpenSandbox (release-1.2.0) behind :class:`SandboxProvider`.

The only module that imports the OpenSandbox SDK. Two places read execd's raw
event stream instead of the SDK's ``Execution`` model, because the pinned SDK
keeps only ``text/plain`` of a result (docs/testing/notebook/README.md, phase 0):
:meth:`OpenSandboxProvider.run` keeps every MIME key of an ``execute_result``.
It reaches the stream through the SDK's own adapter (its SSE client and execd
URL), so authentication, proxy routing and retries stay the SDK's.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

from app.core.config import settings
from app.modules.notebooks.runtime.provider import (
    EventSink,
    KernelEvent,
    ProviderError,
    SandboxGone,
    SandboxHandle,
    SandboxMetrics,
    SandboxSpec,
)

logger = logging.getLogger(__name__)

#: Metadata keys Nova sets on each sandbox; the reconciler finds them by these.
META_RUNTIME = "nova.runtime_id"
META_NOTEBOOK = "nova.notebook_id"
META_OWNER = "nova.owner"
ENTRYPOINT = ["/opt/nova/entrypoint.sh"]


#: Ordinary control calls (list, kill, files) answer quickly.
REQUEST_SECONDS = 60


def _connection_config(request_seconds: int = REQUEST_SECONDS):
    from opensandbox.config import ConnectionConfig

    if not settings.OPENSANDBOX_API_KEY:
        raise ProviderError("Notebook runtimes are not configured on this Nova deployment")
    return ConnectionConfig(
        domain=settings.OPENSANDBOX_DOMAIN,
        api_key=settings.OPENSANDBOX_API_KEY,
        request_timeout=timedelta(seconds=request_seconds),
        use_server_proxy=settings.OPENSANDBOX_USE_SERVER_PROXY,
    )


def _sdk_error(exc: BaseException) -> ProviderError:
    """A safe, user-facing error for an SDK failure; the detail goes to the log."""
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    name = type(exc).__name__
    logger.warning("OpenSandbox call failed: %s: %s", name, str(exc)[:500])
    if status == 404 or "SANDBOX_NOT_FOUND" in str(exc):
        return SandboxGone("The notebook runtime is no longer running")
    if "Timeout" in name or isinstance(exc, TimeoutError):
        return ProviderError("The notebook runtime service did not answer in time")
    if status in {401, 403}:
        return ProviderError("Nova could not authenticate to the notebook runtime service")
    if status == 400 or "INVALID_PARAMETER" in str(exc):
        return ProviderError("The notebook runtime service refused the runtime settings")
    return ProviderError("The notebook runtime service failed")


_STREAM_ENDED = (
    "The connection ended before Nova received the cell result. The cell may have run; "
    "check its effects before running it again"
)


class _SessionBusy(Exception):
    """execd refused the run because the previous one is still finishing."""


class _Live:
    """A connected sandbox plus its code interpreter, cached per sandbox id."""

    def __init__(self, sandbox: Any, interpreter: Any) -> None:
        self.sandbox = sandbox
        self.interpreter = interpreter


class OpenSandboxProvider:
    def __init__(self) -> None:
        self._live: dict[str, _Live] = {}
        self._lock = asyncio.Lock()

    async def _connect(self, sandbox_id: str) -> _Live:
        live = self._live.get(sandbox_id)
        if live is not None:
            return live
        from code_interpreter import CodeInterpreter
        from opensandbox import Sandbox

        async with self._lock:
            live = self._live.get(sandbox_id)
            if live is not None:
                return live
            try:
                sandbox = await Sandbox.connect(
                    sandbox_id,
                    connection_config=_connection_config(),
                    connect_timeout=timedelta(seconds=20),
                )
                interpreter = await CodeInterpreter.create(sandbox=sandbox)
            except ProviderError:
                raise
            except Exception as exc:  # noqa: BLE001 - SDK errors vary by transport
                raise _sdk_error(exc) from exc
            live = _Live(sandbox, interpreter)
            self._live[sandbox_id] = live
            return live

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        from code_interpreter import CodeInterpreter, SupportedLanguage
        from opensandbox import Sandbox
        from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule

        policy = NetworkPolicy(
            default_action="deny",
            egress=[
                NetworkRule(action="allow", target=_cidr(address), ports=[port])
                for address, port in spec.egress
            ],
        )
        try:
            # Creation can include the server pulling a missing image, so it
            # gets the runtime's whole start budget rather than the control
            # call timeout. A request abandoned anyway is reaped by the sweep.
            sandbox = await Sandbox.create(
                spec.image,
                connection_config=_connection_config(
                    max(REQUEST_SECONDS, spec.ready_timeout_seconds)
                ),
                entrypoint=ENTRYPOINT,
                env=spec.env,
                metadata={
                    META_RUNTIME: spec.runtime_id,
                    META_NOTEBOOK: spec.notebook_id,
                    META_OWNER: spec.owner_hash,
                },
                resource={"cpu": spec.cpu, "memory": spec.memory},
                timeout=timedelta(seconds=spec.lifetime_seconds),
                ready_timeout=timedelta(seconds=spec.ready_timeout_seconds),
                network_policy=policy,
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _sdk_error(exc) from exc
        try:
            interpreter = await CodeInterpreter.create(sandbox=sandbox)
            context = await interpreter.codes.create_context(SupportedLanguage.PYTHON)
        except Exception as exc:  # noqa: BLE001
            await self._kill_quietly(sandbox)
            raise _sdk_error(exc) from exc
        self._live[sandbox.id] = _Live(sandbox, interpreter)
        return SandboxHandle(sandbox_id=sandbox.id, context_id=str(context.id))

    async def run(self, handle: SandboxHandle, code: str, sink: EventSink) -> None:
        """Run code; retry briefly while execd still reports the session busy.

        execd can answer "session is busy" for a moment after the previous run's
        stream ended (the kernel is not idle yet). Nothing ran in that case, so
        retrying is safe; anything else is passed through once.
        """
        for attempt in range(40):
            try:
                await self._run_once(handle, code, sink)
                return
            except _SessionBusy:
                await asyncio.sleep(min(0.1 * (attempt + 1), 0.5))
        raise ProviderError("The runtime is still running an earlier cell")

    async def _run_once(self, handle: SandboxHandle, code: str, sink: EventSink) -> None:
        from code_interpreter.adapters.converter.code_execution_converter import (
            CodeExecutionConverter,
        )
        from code_interpreter.models.code import CodeContext

        live = await self._connect(handle.sandbox_id)
        adapter = live.interpreter.codes
        context = CodeContext(id=handle.context_id, language="python")
        request = CodeExecutionConverter.to_api_run_code_request(code, context)
        try:
            client = await adapter._get_sse_client()  # noqa: SLF001 - see module docstring
            url = adapter._get_execd_url(adapter.RUN_CODE_PATH)  # noqa: SLF001
            async with client.stream("POST", url, json=request) as response:
                if response.status_code == 404:
                    raise SandboxGone("The notebook runtime is no longer running")
                if response.status_code != 200:
                    await response.aread()
                    if "busy" in response.text.lower():
                        raise _SessionBusy()
                    raise ProviderError(_execd_error(response.text))
                started = False
                finished = False
                event_types: set[str] = set()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event = _kernel_event(raw)
                    event_types.add(str(raw.get("type", "unknown"))[:64])
                    if (
                        not started
                        and event.type == "error"
                        and "session is busy" in str(event.error.get("value", "")).lower()
                    ):
                        raise _SessionBusy()
                    if event.type in {"stdout", "stderr", "result", "complete"}:
                        started = True
                    if event.type in {"complete", "error"}:
                        finished = True
                    await sink(event)
                if not finished:
                    logger.warning(
                        "Notebook execution stream ended without completion; events=%s",
                        sorted(event_types),
                    )
                    # EOF does not establish whether the kernel ran the code.
                    # Never replay a cell whose side effects may have committed.
                    raise ProviderError(_STREAM_ENDED)
        except (ProviderError, _SessionBusy, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001
            self._live.pop(handle.sandbox_id, None)
            raise _sdk_error(exc) from exc

    async def interrupt(self, handle: SandboxHandle, kernel_execution_id: str) -> None:
        live = await self._connect(handle.sandbox_id)
        try:
            await live.interpreter.codes.interrupt(kernel_execution_id)
        except Exception as exc:  # noqa: BLE001
            raise _sdk_error(exc) from exc

    async def restart_context(self, handle: SandboxHandle) -> SandboxHandle:
        from code_interpreter import SupportedLanguage

        live = await self._connect(handle.sandbox_id)
        try:
            try:
                await live.interpreter.codes.delete_context(handle.context_id)
            except Exception:  # noqa: BLE001 - a dead context is already gone
                logger.info("Old kernel context could not be deleted; creating a new one")
            context = await live.interpreter.codes.create_context(SupportedLanguage.PYTHON)
        except Exception as exc:  # noqa: BLE001
            raise _sdk_error(exc) from exc
        return SandboxHandle(sandbox_id=handle.sandbox_id, context_id=str(context.id))

    async def write_file(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        live = await self._connect(handle.sandbox_id)
        try:
            await live.sandbox.files.write_file(path, content, mode=600)
        except Exception as exc:  # noqa: BLE001
            raise _sdk_error(exc) from exc

    async def read_file(self, handle: SandboxHandle, path: str) -> bytes:
        live = await self._connect(handle.sandbox_id)
        try:
            return await live.sandbox.files.read_bytes(path)
        except Exception as exc:  # noqa: BLE001
            raise _sdk_error(exc) from exc

    async def metrics(self, handle: SandboxHandle) -> SandboxMetrics:
        live = await self._connect(handle.sandbox_id)
        try:
            raw = await live.sandbox.get_metrics()
        except Exception as exc:  # noqa: BLE001
            raise _sdk_error(exc) from exc
        return SandboxMetrics(
            cpu_count=float(raw.cpu_count or 0),
            cpu_percent=float(raw.cpu_used_percentage or 0),
            memory_total_mib=float(raw.memory_total_in_mib or 0),
            memory_used_mib=float(raw.memory_used_in_mib or 0),
        )

    async def allow_hosts(self, handle: SandboxHandle, hosts: list[str]) -> None:
        from opensandbox.models.sandboxes import NetworkRule

        live = await self._connect(handle.sandbox_id)
        try:
            await live.sandbox.patch_egress_rules(
                [NetworkRule(action="allow", target=host) for host in hosts]
            )
        except Exception as exc:  # noqa: BLE001
            raise _sdk_error(exc) from exc

    async def deny_hosts(self, handle: SandboxHandle, hosts: list[str]) -> None:
        live = await self._connect(handle.sandbox_id)
        try:
            await live.sandbox.delete_egress_rules(hosts)
        except Exception as exc:  # noqa: BLE001
            raise _sdk_error(exc) from exc

    async def kill(self, sandbox_id: str) -> None:
        live = self._live.pop(sandbox_id, None)
        if live is not None:
            await self._kill_quietly(live.sandbox)
            return
        from opensandbox.manager import SandboxManager

        manager = await SandboxManager.create(_connection_config())
        try:
            await manager.kill_sandbox(sandbox_id)
        except Exception as exc:  # noqa: BLE001
            error = _sdk_error(exc)
            if not isinstance(error, SandboxGone):
                raise error from exc
        finally:
            await manager.close()

    async def list_runtime_sandboxes(self) -> dict[str, str]:
        from opensandbox.manager import SandboxManager
        from opensandbox.models.sandboxes import SandboxFilter

        manager = await SandboxManager.create(_connection_config())
        found: dict[str, str] = {}
        try:
            page = 1
            while page <= 50:
                result = await manager.list_sandbox_infos(SandboxFilter(page=page, page_size=100))
                for info in result.sandbox_infos:
                    runtime_id = (info.metadata or {}).get(META_RUNTIME)
                    state = (info.status.state or "").lower() if info.status else ""
                    if runtime_id and state not in {"terminated", "failed", "stopped"}:
                        found[info.id] = runtime_id
                if len(result.sandbox_infos) < 100:
                    break
                page += 1
        except Exception as exc:  # noqa: BLE001
            raise _sdk_error(exc) from exc
        finally:
            await manager.close()
        return found

    async def close(self) -> None:
        for live in list(self._live.values()):
            try:
                await live.sandbox.close()
            except Exception:  # noqa: BLE001
                logger.debug("Sandbox client close failed", exc_info=True)
        self._live.clear()

    async def _kill_quietly(self, sandbox: Any) -> None:
        try:
            await sandbox.kill()
        except Exception:  # noqa: BLE001
            logger.warning("Sandbox kill failed; the reconciler will retry")
        try:
            await sandbox.close()
        except Exception:  # noqa: BLE001
            logger.debug("Sandbox client close failed", exc_info=True)


def _cidr(address: str) -> str:
    return address if "/" in address else f"{address}/32"


def _execd_error(body: str) -> str:
    try:
        message = json.loads(body).get("message") or ""
    except (ValueError, AttributeError):
        message = ""
    if "busy" in message.lower():
        return "The runtime is still running an earlier cell"
    return "The notebook runtime could not run this cell"


def _kernel_event(raw: dict[str, Any]) -> KernelEvent:
    kind = str(raw.get("type") or "other")
    timestamp = float(raw.get("timestamp") or 0) / 1000.0
    if "code" in raw and "message" in raw and "type" not in raw:
        return KernelEvent(
            type="error",
            timestamp=timestamp,
            error={"name": "RuntimeError", "value": str(raw.get("message")), "traceback": []},
        )
    if kind in {"stdout", "stderr"}:
        return KernelEvent(type=kind, timestamp=timestamp, text=str(raw.get("text") or ""))
    if kind == "init":
        return KernelEvent(type="init", timestamp=timestamp, text=str(raw.get("text") or ""))
    if kind == "result":
        bundle = dict(raw.get("results") or {})
        if "text" in bundle and "text/plain" not in bundle:
            bundle["text/plain"] = bundle.pop("text")
        return KernelEvent(type="result", timestamp=timestamp, bundle=bundle)
    if kind == "error":
        error = raw.get("error") or {}
        return KernelEvent(
            type="error",
            timestamp=timestamp,
            error={
                "name": str(error.get("ename") or "Error"),
                "value": str(error.get("evalue") or ""),
                "traceback": [str(line) for line in (error.get("traceback") or [])][:200],
            },
        )
    if kind == "execution_complete":
        return KernelEvent(type="complete", timestamp=timestamp)
    return KernelEvent(type="other", timestamp=timestamp, text=kind)
