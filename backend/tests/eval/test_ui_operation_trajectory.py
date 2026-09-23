from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest

from app.modules.assistant.consent import ConsentApproval
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.assistant.tools.ui_actions import CallUIOperationTool
from tests.benchmark.harness import text_frame, tool_call_frame
from tests.eval.harness import EvalTool, Scenario, run_scenario


class ScriptedUIAction(CallUIOperationTool):
    def __init__(self, *, execute: bool = False) -> None:
        self.runs: list[ToolInvocation] = []
        self.execute = execute

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        self.runs.append(invocation)
        if self.execute:
            return await super().run(invocation, context)
        return ToolOutcome(ok=True, summary="Scope policy created", data={"status": "PROPAGATING"})


@pytest.mark.asyncio
async def test_ui_action_discovery_then_mutation_requires_consent() -> None:
    browser = EvalTool(
        "list_ui_operations",
        parameters={
            "type": "object",
            "properties": {"resource": {"type": "string"}},
            "required": ["resource"],
        },
        summary="Found scope operation",
    )
    browser.requires_consent = False
    action = ScriptedUIAction()
    result = await run_scenario(Scenario(
        name="ui_scope_action",
        content="Add a data scope to the analyst role",
        tools=[browser, action],
        read_only_grant=True,
        script=[
            tool_call_frame(
                "browse-1", name="list_ui_operations",
                arguments={"resource": "access-control"}
            ),
            tool_call_frame(
                "act-1",
                name="call_ui_operation",
                arguments={
                    "operation": "POST /api/v1/access-control/data-scopes",
                    "body": {
                        "principal": "alice",
                        "role": "analyst",
                        "database": "sales",
                        "table": "orders",
                        "bindings": [
                            {"dimension": "region", "column": "region", "values": ["west"]}
                        ],
                    },
                },
            ),
            text_frame("Scope policy created; propagation is pending."),
        ],
    ))
    assert result.tool_runs == ["list_ui_operations", "call_ui_operation"]
    assert result.consent_prompts == [("call_ui_operation", "destructive")]
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_ui_action_rejects_secret_before_api_and_redacts_trace() -> None:
    action = ScriptedUIAction(execute=True)
    marker = "private-test-password-123"
    result = await run_scenario(Scenario(
        name="ui_secret_rejection",
        content="Create user alice",
        tools=[action],
        read_only_grant=True,
        script=[tool_call_frame(
            "act-1",
            name="call_ui_operation",
            arguments={
                "operation": "POST /api/v1/users",
                "body": {"username": "alice", "password": marker},
            },
        )],
    ))
    assert result.consent_prompts == [("call_ui_operation", "destructive")]
    assert result.finish_reason == "error"
    assert marker not in "".join(result.frames)


@pytest.mark.asyncio
async def test_ui_workflow_can_make_three_bounded_calls() -> None:
    action = ScriptedUIAction()
    result = await run_scenario(Scenario(
        name="ui_three_step_workflow",
        content="Create a role, add a scope, and verify the roles",
        tools=[action],
        script=[
            tool_call_frame(
                "a1", name="call_ui_operation",
                arguments={
                    "operation": "POST /api/v1/access-control/roles",
                    "body": {"name": "analyst"},
                },
            ),
            tool_call_frame(
                "a2", name="call_ui_operation",
                arguments={
                    "operation": "POST /api/v1/access-control/data-scopes",
                    "body": {
                        "principal": "alice", "role": "analyst", "database": "sales",
                        "table": "orders", "bindings": [
                            {"dimension": "region", "column": "region", "values": ["west"]}
                        ],
                    },
                },
            ),
            tool_call_frame(
                "a3", name="call_ui_operation",
                arguments={"operation": "GET /api/v1/access-control/roles"},
            ),
            text_frame("Role created, scope added, and roles verified."),
        ],
    ))
    assert result.tool_runs == ["call_ui_operation"] * 3
    assert len(result.consent_prompts) == 3
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_secure_approval_never_enters_provider_or_trace() -> None:
    marker = "private-password-marker"
    contexts: list[Any] = []

    class SecureAction(CallUIOperationTool):
        def __init__(self) -> None:
            self.runs: list[ToolInvocation] = []

        async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
            self.runs.append(invocation)
            contexts.append(context)
            assert context.secure_input == {"password": marker}
            return ToolOutcome(ok=True, summary="User created")

    async def approve(_invocation: ToolInvocation, _classification: str) -> ConsentApproval:
        return ConsentApproval({"password": marker})

    result = await run_scenario(Scenario(
        name="secure_user_creation",
        content="Create user Maya",
        tools=[SecureAction()],
        resolve_consent=approve,
        script=[
            tool_call_frame(
                "create-user", name="call_ui_operation",
                arguments={
                    "operation": "POST /api/v1/users",
                    "body": {"username": "maya"},
                },
            ),
            text_frame("User Maya was created."),
        ],
    ))
    assert result.tool_runs == ["call_ui_operation"]
    assert result.finish_reason == "stop"
    assert contexts[0].secure_input is None
    assert marker not in str(result.frames)
    assert marker not in str(result.provider_calls)
    assert marker not in str(result.steps)


@pytest.mark.asyncio
async def test_stage_upload_bytes_stay_out_of_provider_and_trace() -> None:
    marker = b"private-stage-file-marker"
    stream = BytesIO(marker)

    class UploadAction(CallUIOperationTool):
        def __init__(self) -> None:
            self.runs: list[ToolInvocation] = []

        async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
            self.runs.append(invocation)
            assert context.file_upload is not None
            assert context.file_upload[1].read() == marker
            return ToolOutcome(ok=True, summary="Stage file uploaded")

    async def approve(_invocation: ToolInvocation, _classification: str) -> ConsentApproval:
        return ConsentApproval(upload=("data.csv", stream, "text/csv"))

    result = await run_scenario(Scenario(
        name="stage_upload_file_handoff",
        content="Upload my file to stage 1",
        tools=[UploadAction()],
        resolve_consent=approve,
        script=[
            tool_call_frame(
                "stage-upload", name="call_ui_operation",
                arguments={
                    "operation": "POST /api/v1/stages/{stage_id}/files",
                    "path_params": {"stage_id": "stage-1"},
                },
            ),
            text_frame("Stage file uploaded."),
        ],
    ))
    assert result.consent_prompts == [("call_ui_operation", "destructive")]
    assert result.tool_runs == ["call_ui_operation"]
    assert result.finish_reason == "stop"
    assert stream.closed
    assert marker.decode() not in str(result.frames)
    assert marker.decode() not in str(result.provider_calls)
    assert marker.decode() not in str(result.steps)
