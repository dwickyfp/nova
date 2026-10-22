from __future__ import annotations

import json
import logging

import pytest
from pydantic import ValidationError

from app.core import config
from app.core.studio_capabilities import (
    StudioFeatureCapability,
    effective_studio_capabilities,
    log_studio_capabilities,
)
from app.modules.agents.studio_schemas import StudioCapabilities
from app.modules.agents.studio_service import studio_service


def test_business_capabilities_are_always_available():
    result = effective_studio_capabilities()
    for name in ("business_workflow", "actions", "quality"):
        feature = getattr(result, name)
        assert feature.available and feature.status == "AVAILABLE"
        assert feature.reason_code == "READY" and feature.executor_available is None


def test_analysis_needs_an_isolated_executor():
    blocked = effective_studio_capabilities().analysis_workspace
    assert not blocked.available and not blocked.executor_available
    assert blocked.status == "BLOCKED_BY_INFRASTRUCTURE"
    assert blocked.reason_code == "ISOLATED_EXECUTOR_UNAVAILABLE"
    ready = effective_studio_capabilities(analysis_executor_isolated=True).analysis_workspace
    assert ready.available and ready.executor_available and ready.status == "AVAILABLE"


def test_capability_requires_boolean_isolation():
    result = effective_studio_capabilities(
        analysis_executor_isolated="true",  # type: ignore[arg-type]
    )
    assert not result.analysis_workspace.executor_available
    assert not result.analysis_workspace.available


def test_capability_contract_is_bounded_and_rejects_arbitrary_configuration():
    with pytest.raises(ValidationError):
        StudioFeatureCapability(
            available=True,
            status="AVAILABLE",
            reason_code="READY",
            credentials="private",
        )
    assert StudioCapabilities().runtime is None


@pytest.mark.parametrize("process", ["api", "task-worker", "smart-worker"])
def test_startup_diagnostics_and_studio_use_the_same_safe_snapshot(
    monkeypatch, caplog, process
):
    monkeypatch.setattr(config.settings, "SECRET_KEY", "signing-secret-never-log")
    monkeypatch.setattr(config.settings, "FERNET_KEY", "encryption-secret-never-log")
    with caplog.at_level(logging.INFO):
        log_studio_capabilities(logging.getLogger(__name__), process)
    snapshot = studio_service.runtime_capabilities()
    record = caplog.records[-1]
    assert record.args[0] == process
    assert json.loads(record.args[1]) == snapshot.model_dump(exclude_none=True)
    assert snapshot.analysis_workspace.status == "BLOCKED_BY_INFRASTRUCTURE"
    assert "never-log" not in caplog.text
