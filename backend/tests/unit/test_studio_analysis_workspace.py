from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.agents import mission, resource_delegation
from app.modules.agents.harness_repository import harness_repository
from app.modules.assistant import analysis_workspace as module
from app.modules.assistant.analysis_workspace import (
    AnalysisBounds,
    AnalysisRequest,
    AnalysisResult,
    AnalyticalWorkspace,
    AnalyticalWorkspaceTool,
)
from app.modules.assistant.tools import ToolInvocation
from tests.unit.test_studio_missions import USER


class FakeExecutor:
    isolated = True

    def __init__(self):
        self.calls = []
        self.cancelled = []
        self.result = "completed"
        self.block = False
        self.started = asyncio.Event()

    async def execute(self, execution_id, request, scope, inputs, cancelled):
        self.calls.append((execution_id, request, scope, inputs))
        self.started.set()
        if self.block:
            await cancelled.wait()
        if self.result == "oversized":
            return AnalysisResult(
                execution_id=execution_id,
                status="completed",
                summary="Done",
                rows=[{"value": "x" * 300000}],
            )
        if self.result == "secret":
            return AnalysisResult(
                execution_id=execution_id,
                status="completed",
                summary="api_key='sk-do-not-disclose-abcdefghijklmnopqrstuv'",
            )
        return AnalysisResult(
            execution_id=execution_id,
            status="completed",
            summary="Analysis complete",
            rows=[{"value": 3}],
        )

    async def cancel(self, execution_id, scope):
        self.cancelled.append((execution_id, scope))


@pytest.fixture
def analysis_environment(monkeypatch):
    monkeypatch.setattr(module, "settings", SimpleNamespace(STUDIO_ANALYSIS_WORKSPACE_ENABLED=True))
    monkeypatch.setattr(mission, "require_thread", AsyncMock())
    monkeypatch.setattr(module, "write_audit_log", AsyncMock())
    monkeypatch.setattr(
        harness_repository,
        "get",
        AsyncMock(
            return_value={
                "run_id": "run",
                "thread_id": "thread",
                "owner_name": "alice",
                "role_name": "analyst",
                "session_id": "login",
                "security_version": 1,
                "status": "running",
            }
        ),
    )
    monkeypatch.setattr(
        resource_delegation.resource_delegation,
        "load",
        AsyncMock(
            return_value=(
                [{"name": "data.csv", "media_type": "text/plain", "content": "value\n1\n2\n"}],
                ["resource"],
            )
        ),
    )


def request(**kwargs):
    return AnalysisRequest(thread_id="thread", run_id="run", code="print(1 + 2)", **kwargs)


async def test_default_executor_is_unavailable_even_when_feature_enabled(analysis_environment):
    workspace = AnalyticalWorkspace()
    assert workspace.capability().enabled
    assert not workspace.capability().available
    assert workspace.capability().status == "BLOCKED_BY_INFRASTRUCTURE"
    assert not workspace.capability().executor_available
    result = await workspace.execute(request(), USER)
    assert result.status == "unavailable"
    assert result.blocker == "BLOCKED_BY_INFRASTRUCTURE"
    assert result.rows == [] and result.outputs == []
    resource_delegation.resource_delegation.load.assert_not_awaited()


async def test_unavailable_http_execution_is_a_bounded_audited_refusal(
    analysis_environment, monkeypatch
):
    workspace = AnalyticalWorkspace()
    monkeypatch.setattr(module, "analytical_workspace", workspace)
    body = request()
    body.code = "raise RuntimeError('must never execute')"
    response = await module.execute_analysis(body, USER)
    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload["status"] == "unavailable"
    assert payload["blocker"] == "BLOCKED_BY_INFRASTRUCTURE"
    assert payload["rows"] == payload["outputs"] == []
    assert "must never execute" not in response.body.decode()
    assert workspace.active == {}
    module.write_audit_log.assert_awaited_once()
    assert module.write_audit_log.call_args.kwargs["status"] == "UNAVAILABLE"
    harness_repository.get.assert_not_awaited()
    resource_delegation.resource_delegation.load.assert_not_awaited()


async def test_nonisolated_executor_is_never_used_as_a_fallback(analysis_environment):
    fake = FakeExecutor()
    fake.isolated = False
    workspace = AnalyticalWorkspace(fake)
    result = await workspace.execute(request(), USER)
    assert result.status == "unavailable" and result.blocker == "BLOCKED_BY_INFRASTRUCTURE"
    assert fake.calls == []


async def test_tool_preserves_legacy_error_and_reports_infrastructure_blocker(
    analysis_environment, monkeypatch
):
    monkeypatch.setattr(module, "analytical_workspace", AnalyticalWorkspace())
    context = SimpleNamespace(user=USER, thread_id="thread", run_id="run")
    outcome = await AnalyticalWorkspaceTool().run(
        ToolInvocation("call", "analysis_workspace", request().model_dump()), context
    )
    assert not outcome.ok and not outcome.recoverable
    assert outcome.error_class == "ANALYSIS_UNAVAILABLE" and outcome.data is None
    assert outcome.metadata["blocker"] == "BLOCKED_BY_INFRASTRUCTURE"


async def test_fake_executor_receives_only_selected_granted_inputs_and_caller_scope(
    analysis_environment,
):
    fake = FakeExecutor()
    result = await AnalyticalWorkspace(fake).execute(request(resource_refs=["resource"]), USER)
    assert result.status == "completed"
    _, _, scope, inputs = fake.calls[0]
    assert scope.principal == "alice" and scope.active_role == "analyst"
    assert [item.resource_id for item in inputs] == ["resource"]
    assert inputs[0].content == "value\n1\n2\n"


async def test_resource_injection_is_rejected_before_executor(analysis_environment):
    fake = FakeExecutor()
    with pytest.raises(HTTPException) as error:
        await AnalyticalWorkspace(fake).execute(request(resource_refs=["ungranted"]), USER)
    assert error.value.status_code == 403
    assert fake.calls == []


@pytest.mark.parametrize(
    "invalid",
    [
        {"network": True},
        {"timeout_seconds": 61},
        {"memory_mb": 513},
        {"cpu_seconds": 31},
        {"max_output_rows": 1001},
        {"packages": ["requests"]},
    ],
)
def test_network_and_compute_contract_is_bounded(invalid):
    with pytest.raises(ValidationError):
        AnalysisBounds(**invalid)


@pytest.mark.parametrize("failure", ["oversized", "secret"])
async def test_executor_output_failure_cannot_leak_or_claim_completion(
    analysis_environment, failure
):
    fake = FakeExecutor()
    fake.result = failure
    result = await AnalyticalWorkspace(fake).execute(request(), USER)
    assert result.status == "failed"
    assert result.rows == []
    assert "sk-do" not in result.model_dump_json()
    assert len(fake.cancelled) == 1


async def test_cancellation_is_scope_bound_and_signals_executor(analysis_environment):
    fake = FakeExecutor()
    fake.block = True
    workspace = AnalyticalWorkspace(fake)
    pending = asyncio.create_task(workspace.execute(request(), USER))
    await fake.started.wait()
    execution_id = fake.calls[0][0]
    with pytest.raises(HTTPException) as error:
        await workspace.cancel(execution_id, {**USER, "session_id": "other"})
    assert error.value.status_code == 404
    await workspace.cancel(execution_id, USER)
    result = await pending
    assert result.status == "cancelled" and result.rows == []
    assert workspace.active == {}


async def test_timeout_stops_executor_and_discards_output(analysis_environment):
    fake = FakeExecutor()
    fake.block = True
    workspace = AnalyticalWorkspace(fake)
    result = await workspace.execute(request(bounds=AnalysisBounds(timeout_seconds=1)), USER)
    assert result.status == "timeout" and result.rows == []
    assert len(fake.cancelled) == 1
    assert workspace.active == {}


async def test_tool_cannot_select_another_thread_or_run(analysis_environment):
    tool = AnalyticalWorkspaceTool()
    context = SimpleNamespace(user=USER, thread_id="another", run_id="run")
    result = await tool.run(ToolInvocation("call", tool.name, request().model_dump()), context)
    assert not result.ok and result.error_class == "ANALYSIS_REJECTED"


async def test_disabled_fake_executor_is_never_called(analysis_environment, monkeypatch):
    monkeypatch.setattr(
        module, "settings", SimpleNamespace(STUDIO_ANALYSIS_WORKSPACE_ENABLED=False)
    )
    fake = FakeExecutor()
    workspace = AnalyticalWorkspace(fake)
    capability = workspace.capability()
    assert not capability.enabled and not capability.available
    assert capability.status == "DISABLED"
    assert capability.executor_available and capability.isolation == "isolated"
    result = await workspace.execute(request(), USER)
    assert result.status == "unavailable"
    assert result.blocker == "FEATURE_DISABLED"
    assert fake.calls == []


async def test_uncooperative_executor_cancel_is_bounded_and_withholds_result(
    analysis_environment, monkeypatch
):
    class StubbornExecutor(FakeExecutor):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()
            self.shutdown_started = asyncio.Event()

        async def cancel(self, execution_id, scope):
            self.shutdown_started.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue

    monkeypatch.setattr(module, "STOP_GRACE_SECONDS", 0.02)
    fake = StubbornExecutor()
    fake.block = True
    workspace = AnalyticalWorkspace(fake)
    pending = asyncio.create_task(workspace.execute(request(), USER))
    await fake.started.wait()
    execution_id = fake.calls[0][0]
    try:
        async with asyncio.timeout(1):
            outcome = await workspace.cancel(execution_id, USER)
            result = await pending
        assert fake.shutdown_started.is_set()
        assert outcome["status"] == result.status == "failed"
        assert result.rows == [] and result.outputs == []
        assert workspace.active == {}
    finally:
        fake.release.set()
        await asyncio.sleep(0)


async def test_cancel_callback_failure_is_sanitized_and_cleanup_finishes(analysis_environment):
    fake = FakeExecutor()
    fake.block = True
    fake.cancel = AsyncMock(side_effect=RuntimeError("private credentials"))
    workspace = AnalyticalWorkspace(fake)
    pending = asyncio.create_task(workspace.execute(request(), USER))
    await fake.started.wait()
    outcome = await workspace.cancel(fake.calls[0][0], USER)
    result = await pending
    assert outcome["status"] == result.status == "failed"
    assert "private credentials" not in result.model_dump_json()
    fake.cancel.assert_awaited_once()
    assert workspace.active == {}


async def test_request_task_cancellation_stops_executor_and_preserves_cancelled_error(
    analysis_environment,
):
    fake = FakeExecutor()
    fake.block = True
    workspace = AnalyticalWorkspace(fake)
    pending = asyncio.create_task(workspace.execute(request(), USER))
    await fake.started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert len(fake.cancelled) == 1
    assert workspace.active == {}


async def test_analysis_refuses_stopped_participant_before_executor(analysis_environment):
    harness_repository.get.return_value["status"] = "cancelled"
    fake = FakeExecutor()
    with pytest.raises(HTTPException) as error:
        await AnalyticalWorkspace(fake).execute(request(), USER)
    assert error.value.status_code == 409
    assert not fake.calls


async def test_result_is_withheld_after_participant_security_changes(analysis_environment):
    initial = harness_repository.get.return_value
    harness_repository.get.side_effect = [initial, {**initial, "security_version": 2}]
    fake = FakeExecutor()
    result = await AnalyticalWorkspace(fake).execute(request(), USER)
    assert result.status == "failed" and result.rows == []
    assert len(fake.cancelled) == 1
