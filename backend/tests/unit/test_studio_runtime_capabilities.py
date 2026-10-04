from __future__ import annotations

import json
import logging
from itertools import product
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core import config
from app.core.studio_capabilities import (
    STUDIO_FLAG_NAMES,
    StudioFeatureCapability,
    effective_studio_capabilities,
    log_studio_capabilities,
)
from app.modules.agents.studio_schemas import StudioCapabilities
from app.modules.agents.studio_service import studio_service


@pytest.mark.parametrize("flags", list(product((False, True), repeat=4)))
def test_effective_flags_remain_independent_and_never_imply_an_executor(flags):
    settings = SimpleNamespace(**dict(zip(STUDIO_FLAG_NAMES, flags, strict=True)))
    result = effective_studio_capabilities(settings)
    for name, enabled in zip(
        ("business_workflow", "actions", "quality"), flags[:3], strict=True
    ):
        feature = getattr(result, name)
        assert feature.enabled == feature.available == enabled
        assert feature.status == ("AVAILABLE" if enabled else "DISABLED")
    analysis = result.analysis_workspace
    assert analysis.enabled == flags[3]
    assert not analysis.available and not analysis.executor_available
    assert analysis.status == ("BLOCKED_BY_INFRASTRUCTURE" if flags[3] else "DISABLED")


def test_disabled_isolated_executor_is_reported_without_becoming_usable():
    result = effective_studio_capabilities(
        SimpleNamespace(), analysis_executor_isolated=True
    ).analysis_workspace
    assert not result.enabled and not result.available
    assert result.executor_available and result.status == "DISABLED"


def test_capability_requires_boolean_configuration_and_isolation():
    result = effective_studio_capabilities(
        SimpleNamespace(STUDIO_ANALYSIS_WORKSPACE_ENABLED="false"),
        analysis_executor_isolated="true",  # type: ignore[arg-type]
    )
    assert not result.analysis_workspace.enabled
    assert not result.analysis_workspace.executor_available
    assert not result.analysis_workspace.available


def test_capability_contract_is_bounded_and_rejects_arbitrary_configuration():
    with pytest.raises(ValidationError):
        StudioFeatureCapability(
            enabled=True,
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
    settings = SimpleNamespace(
        **dict.fromkeys(STUDIO_FLAG_NAMES, True),
        SECRET_KEY="signing-secret-never-log",
        FERNET_KEY="encryption-secret-never-log",
        REDIS_URL="redis://credential-bearing-endpoint",
    )
    monkeypatch.setattr(config, "settings", settings)
    with caplog.at_level(logging.INFO):
        log_studio_capabilities(logging.getLogger(__name__), process)
    snapshot = studio_service.runtime_capabilities()
    record = caplog.records[-1]
    assert record.args[0] == process
    assert json.loads(record.args[1]) == snapshot.model_dump(exclude_none=True)
    assert snapshot.analysis_workspace.status == "BLOCKED_BY_INFRASTRUCTURE"
    assert "never-log" not in caplog.text and "credential-bearing" not in caplog.text
