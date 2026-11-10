"""The sandbox provider seam.

One protocol in front of the sandbox technology; only
:mod:`app.modules.notebooks.runtime.opensandbox_provider` imports the
OpenSandbox SDK. Tests use a fake with the same shape.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """The sandbox service failed; the message is safe to show."""


class SandboxGone(ProviderError):
    """The sandbox no longer exists (stopped, expired or removed)."""


@dataclass(frozen=True)
class SandboxSpec:
    runtime_id: str
    notebook_id: str
    owner_hash: str
    image: str
    cpu: str
    memory: str
    lifetime_seconds: int
    #: Environment for the sandbox (the Jupyter token only).
    env: dict[str, str]
    #: ``(ip_or_cidr, port)`` pairs the sandbox may connect to; all else is denied.
    egress: list[tuple[str, int]]
    ready_timeout_seconds: int


@dataclass(frozen=True)
class SandboxHandle:
    sandbox_id: str
    context_id: str


@dataclass
class KernelEvent:
    """One raw event from a code run, normalised to plain types."""

    type: str  # init | stdout | stderr | result | error | complete | other
    timestamp: float = 0.0
    text: str = ""
    #: ``result``: the MIME bundle (``text`` is ``text/plain``).
    bundle: dict[str, Any] = field(default_factory=dict)
    #: ``error``: name, value, traceback lines.
    error: dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[KernelEvent], Awaitable[None]]


@dataclass(frozen=True)
class SandboxMetrics:
    cpu_count: float
    cpu_percent: float
    memory_total_mib: float
    memory_used_mib: float


class SandboxProvider(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...

    async def run(self, handle: SandboxHandle, code: str, sink: EventSink) -> None:
        """Run code in the runtime's context, sending every event to ``sink``.

        Returns when the run's event stream ends (not at ``complete``: trailing
        output can follow it).
        """
        ...

    async def interrupt(self, handle: SandboxHandle, kernel_execution_id: str) -> None: ...

    async def restart_context(self, handle: SandboxHandle) -> SandboxHandle: ...

    async def write_file(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def read_file(self, handle: SandboxHandle, path: str) -> bytes: ...

    async def metrics(self, handle: SandboxHandle) -> SandboxMetrics: ...

    async def allow_hosts(self, handle: SandboxHandle, hosts: list[str]) -> None: ...

    async def deny_hosts(self, handle: SandboxHandle, hosts: list[str]) -> None: ...

    async def kill(self, sandbox_id: str) -> None: ...

    async def list_runtime_sandboxes(self) -> dict[str, str]:
        """Live Nova runtime sandboxes: sandbox id → runtime id (from metadata)."""
        ...

    async def close(self) -> None: ...
