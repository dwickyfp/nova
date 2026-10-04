"""Credential-free effective Studio rollout capabilities."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict

STUDIO_FLAG_NAMES = (
    "STUDIO_BUSINESS_WORKFLOW_ENABLED",
    "STUDIO_ACTIONS_ENABLED",
    "STUDIO_QUALITY_ENABLED",
    "STUDIO_ANALYSIS_WORKSPACE_ENABLED",
)

CapabilityStatus = Literal["DISABLED", "AVAILABLE", "BLOCKED_BY_INFRASTRUCTURE"]


class StudioFeatureCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    available: bool
    status: CapabilityStatus
    reason_code: Literal["FEATURE_DISABLED", "READY", "ISOLATED_EXECUTOR_UNAVAILABLE"]
    executor_available: bool | None = None


class StudioRuntimeCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    business_workflow: StudioFeatureCapability
    actions: StudioFeatureCapability
    quality: StudioFeatureCapability
    analysis_workspace: StudioFeatureCapability
    execution_timezone: str


def effective_studio_capabilities(
    config: object | None = None, *, analysis_executor_isolated: bool = False
) -> StudioRuntimeCapabilities:
    from app.core.database import configured_timezone

    if config is None:
        from app.core.config import settings

        config = settings

    def feature(name: str, *, executor: bool | None = None) -> StudioFeatureCapability:
        enabled = getattr(config, name, False) is True
        available = enabled and executor is not False
        status: CapabilityStatus = (
            "AVAILABLE"
            if available
            else "BLOCKED_BY_INFRASTRUCTURE"
            if enabled
            else "DISABLED"
        )
        return StudioFeatureCapability(
            enabled=enabled,
            available=available,
            status=status,
            reason_code="READY"
            if available
            else "ISOLATED_EXECUTOR_UNAVAILABLE"
            if enabled
            else "FEATURE_DISABLED",
            executor_available=executor,
        )

    return StudioRuntimeCapabilities(
        business_workflow=feature(STUDIO_FLAG_NAMES[0]),
        actions=feature(STUDIO_FLAG_NAMES[1]),
        quality=feature(STUDIO_FLAG_NAMES[2]),
        analysis_workspace=feature(
            STUDIO_FLAG_NAMES[3], executor=analysis_executor_isolated is True
        ),
        execution_timezone=configured_timezone(),
    )


def log_studio_capabilities(
    logger: logging.Logger,
    process: Literal["api", "task-worker", "smart-worker"],
) -> None:
    from app.modules.assistant.analysis_workspace import analytical_workspace

    capabilities = effective_studio_capabilities(
        analysis_executor_isolated=analytical_workspace.executor.isolated
    )
    logger.info(
        "Studio capabilities process=%s %s",
        process,
        capabilities.model_dump_json(exclude_none=True),
    )
