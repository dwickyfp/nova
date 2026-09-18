"""Thin adapter over the operator-provided ``starrocks-cluster-sync`` engine.

NOVA-84 Ruling A: Nova **never** bundles, vendors, mirrors or redistributes the
tool or its fat JAR. The wizard invokes an operator-provided binary at a
configured path. v1 deliberately does **not** execute it — ``execute`` is not
implemented and there is no code path that shells out. What lives here is the
availability probe the wizard needs to fail loudly, and the seam that a future
``Execute`` issue (gated on #7 backup/restore) will implement.

The binary is located through ``NOVA_MIGRATION_ENGINE_PATH``. No default path is
assumed and no download is attempted: an absent path is a typed, user-visible
error, not a silent fallback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.modules.migration.schemas import MigrationEngineStatus


class EngineUnavailableError(ValueError):
    """The configured engine binary is absent or unreadable.

    Carries the configured path in the message so the wizard can name it. The
    path is configuration, never a credential, so it is safe to surface.
    """


@dataclass(frozen=True)
class EngineConfig:
    """Where the operator-provided engine binary lives."""

    path: str

    @property
    def configured(self) -> bool:
        return bool(self.path)

    @property
    def available(self) -> bool:
        if not self.path:
            return False
        candidate = Path(self.path)
        return candidate.is_file() and os.access(candidate, os.X_OK)


class ClusterSyncEngine:
    """Adapter for the upstream cross-cluster migration tool.

    ``execute`` is intentionally absent. v1 implements Assessment + Dry-run
    only; the cutover path is a separate, backup-gated issue. Keeping the
    method off the class is stronger than a flag — a caller cannot reach an
    execution path by flipping a boolean.
    """

    def __init__(self, config: EngineConfig | None = None) -> None:
        self._config = config or EngineConfig(
            path=settings.NOVA_MIGRATION_ENGINE_PATH
        )

    @property
    def config(self) -> EngineConfig:
        return self._config

    def status(self) -> MigrationEngineStatus:
        """Report whether the wizard can locate the engine.

        An unconfigured or missing binary is reported, not raised: the
        assessment and dry-run are pure metadata work and remain useful without
        the engine. It is only an eventual execute that must refuse.
        """
        if not self._config.configured:
            return MigrationEngineStatus(
                configured=False,
                available=False,
                path="",
                execute_supported=False,
                message=(
                    "No migration engine configured. Set "
                    "NOVA_MIGRATION_ENGINE_PATH to an operator-provided "
                    "starrocks-cluster-sync binary. Nova does not bundle it."
                ),
            )
        if not self._config.available:
            return MigrationEngineStatus(
                configured=True,
                available=False,
                path=self._config.path,
                execute_supported=False,
                message=(
                    f"Migration engine binary is absent or not executable at "
                    f"'{self._config.path}'. Nova does not download or bundle "
                    "the engine; install it from the upstream release."
                ),
            )
        return MigrationEngineStatus(
            configured=True,
            available=True,
            path=self._config.path,
            execute_supported=False,
            message=(
                "Engine binary found. Assessment and dry-run do not invoke it; "
                "cutover is gated on backup/restore (roadmap #7)."
            ),
        )

    def require_available(self) -> None:
        """Raise naming the configured path when the engine is not usable.

        Reserved for the future execute path so it cannot silently proceed
        without a binary.
        """
        status = self.status()
        if not status.configured:
            raise EngineUnavailableError(
                "No migration engine configured. Set NOVA_MIGRATION_ENGINE_PATH."
            )
        if not status.available:
            raise EngineUnavailableError(
                f"Migration engine binary is absent or not executable at "
                f"'{status.path}'."
            )


cluster_sync_engine = ClusterSyncEngine()
