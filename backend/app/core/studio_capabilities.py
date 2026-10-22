"""Credential-free effective Studio capabilities."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict

CapabilityStatus = Literal["AVAILABLE", "BLOCKED_BY_INFRASTRUCTURE"]


class StudioFeatureCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    available: bool
    status: CapabilityStatus
    reason_code: Literal["READY", "ISOLATED_EXECUTOR_UNAVAILABLE"]
    executor_available: bool | None = None


class StudioRuntimeCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    business_workflow: StudioFeatureCapability
    actions: StudioFeatureCapability
    quality: StudioFeatureCapability
    analysis_workspace: StudioFeatureCapability
    execution_timezone: str


def effective_studio_capabilities(
    *, analysis_executor_isolated: bool = False
) -> StudioRuntimeCapabilities:
    from app.core.database import configured_timezone

    def feature(*, executor: bool | None = None) -> StudioFeatureCapability:
        available = executor is not False
        return StudioFeatureCapability(
            available=available,
            status="AVAILABLE" if available else "BLOCKED_BY_INFRASTRUCTURE",
            reason_code="READY" if available else "ISOLATED_EXECUTOR_UNAVAILABLE",
            executor_available=executor,
        )

    return StudioRuntimeCapabilities(
        business_workflow=feature(),
        actions=feature(),
        quality=feature(),
        analysis_workspace=feature(executor=analysis_executor_isolated is True),
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
