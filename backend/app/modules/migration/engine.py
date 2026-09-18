"""Thin adapter over the optional ``starrocks-cluster-sync`` engine.

This is an **interface plus a stub** for v1. The dry-run path must never shell
out to a real cutover, so nothing here executes anything — the adapter only
answers whether the operator-provided binary exists and is runnable.

Why no bundling (NOVA-84 follow-up, Ruling 1): the binary's license is not
declared anywhere — no ``LICENSE`` file, no pom ``<licenses>`` block, no public
source repo — so it is all-rights-reserved by default. v1 therefore **adopts and
invokes** it, never redistributes it. The operator installs the official binary
and points ``MIGRATION_CLUSTER_SYNC_BINARY`` at it; a missing binary is a typed,
non-fatal condition the connector reports, not an error that blocks assessment.

Execute (the only path that would run the binary) is gated on #7 and is
intentionally absent from this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings


class MigrationEngineUnavailableError(RuntimeError):
    """The operator-provided migration binary is not available.

    Typed so callers can surface the reason without treating it as a crash. The
    message never contains a path the operator did not configure, and never a
    credential.
    """


@dataclass(frozen=True)
class EngineStatus:
    available: bool
    configured_path: str | None
    resolved_path: str | None
    reason: str | None


class StarRocksClusterSyncAdapter:
    """Detect the operator's ``starrocks-cluster-sync`` binary.

    No method on this class executes the engine. ``status()`` is safe to call on
    every request; ``require()`` raises the typed error for a caller that wants
    to fail loudly. Both are side-effect-free.
    """

    def __init__(self, binary_path: str | None = None) -> None:
        # An explicit path (tests) wins over settings; empty/None means the
        # connector was not configured with one.
        raw = binary_path if binary_path is not None else settings.MIGRATION_CLUSTER_SYNC_BINARY
        self._configured = (raw or "").strip()

    @property
    def configured_path(self) -> str | None:
        return self._configured or None

    def status(self) -> EngineStatus:
        """Report availability without raising."""
        if not self._configured:
            return EngineStatus(
                available=False,
                configured_path=None,
                resolved_path=None,
                reason=(
                    "No starrocks-cluster-sync binary configured. Nova does not "
                    "bundle it; set MIGRATION_CLUSTER_SYNC_BINARY to the official "
                    "binary installed by the operator."
                ),
            )
        path = Path(self._configured)
        if not path.is_file():
            return EngineStatus(
                available=False,
                configured_path=self._configured,
                resolved_path=None,
                reason="Configured migration binary does not exist at the given path.",
            )
        if not os.access(path, os.X_OK):
            return EngineStatus(
                available=False,
                configured_path=self._configured,
                resolved_path=str(path),
                reason="Configured migration binary exists but is not executable.",
            )
        return EngineStatus(
            available=True,
            configured_path=self._configured,
            resolved_path=str(path),
            reason=None,
        )

    def require(self) -> str:
        """Return the resolved binary path or raise the typed error."""
        status = self.status()
        if not status.available or status.resolved_path is None:
            raise MigrationEngineUnavailableError(status.reason or "Migration engine unavailable")
        return status.resolved_path


migration_engine = StarRocksClusterSyncAdapter()
