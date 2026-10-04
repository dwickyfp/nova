"""Release resources stay fixed across provider and consent roundtrips."""

from copy import deepcopy
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import releases
from app.modules.agents.tools.ai_search import AISearchTool
from app.modules.agents.tools.intelligence_views import FeatureLookupTool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolInvocation, ToolRegistry
from app.modules.intelligence.feature_store import feature_store
from app.modules.intelligence.search import search_service
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult

USER = {
    "username": "reader",
    "encrypted_password": "sealed",
    "active_role": "analyst",
    "assigned_roles": ["analyst"],
    "session_id": "session",
    "security_context_version": 7,
}


class ChangingProvider(ScriptedProvider):
    def __init__(self, script, change=lambda: None, **kwargs):
        super().__init__(script, **kwargs)
        self.change = change
        self.plans = 0

    async def plan_turn(self, **kwargs):
        self.plans += 1
        return await super().plan_turn(**kwargs)

    async def stream(self, **kwargs):
        async for kind, payload in super().stream(**kwargs):
            if self.calls == 1 and kind == "message":
                self.change()
            yield kind, payload


@dataclass
class ResourceBoundary:
    tool: object
    arguments: dict
    definition: dict
    listing: AsyncMock
    data_read: AsyncMock
    manifest: dict
    available: bool = True

    def change_version(self):
        self.definition["active_version"] = 2

    def revoke(self):
        self.available = False


async def resource_boundary(kind, monkeypatch):
    definition = {
        "id": "resource-id",
        "name": "customers",
        "active_version": 1,
        "status": "ACTIVE",
        "entity_id": "customer-id",
        "source_relation": "sales.customers",
        "content_columns": ["description"],
        "filter_columns": [],
    }
    bindings = {
        "search_indexes": [{"index": "customers", "filters": {}}]
        if kind == "search" else [],
        "feature_groups": ["customers"] if kind == "feature" else [],
    }
    if kind == "search":
        tool = AISearchTool(bindings)
        arguments = {"index": "customers", "query": "customer", "mode": "LEXICAL"}
        owner, list_name, read_name = search_service, "list", "query"

        async def read(*args):
            return {"version": definition["active_version"], "hits": [
                {"source_key": "1", "content": "RESOURCE_DATA", "rank": 1},
            ]}
    else:
        tool = FeatureLookupTool(bindings)
        arguments = {"group": "customers", "entity_key": {"id": 1}}
        owner, list_name, read_name = feature_store, "list_groups", "lookup"

        async def read(*args):
            return {"version": args[1].version or definition["active_version"],
                    "values": {"score": 17, "label": "RESOURCE_DATA"},
                    "as_of": "2026-10-04T00:00:00Z"}

    data_read = AsyncMock(side_effect=read)
    state = ResourceBoundary(tool, arguments, definition, AsyncMock(), data_read, {})

    async def listing(user):
        assert user == USER
        return [deepcopy(definition)] if state.available else []

    state.listing.side_effect = listing
    monkeypatch.setattr(owner, list_name, state.listing)
    monkeypatch.setattr(owner, read_name, data_read)
    tool.run = AsyncMock(wraps=tool.run)
    configuration = {"resource_bindings": bindings, "semantic_view_ids": []}
    state.manifest = {
        "id": "frozen-release", "version_id": "agent-version",
        "dependencies": {
            "configuration": configuration,
            "resources": await releases.resource_contracts(configuration, USER),
            "models": [{"provider_id": "bench", "name": "bench-model"}],
            "routing": {"enabled": False},
        },
    }
    state.listing.reset_mock()
    return state


@pytest.fixture(params=["search", "feature"])
async def boundary(request, monkeypatch):
    return await resource_boundary(request.param, monkeypatch)


@pytest.fixture
async def feature_boundary(monkeypatch):
    return await resource_boundary("feature", monkeypatch)


async def run_turn(boundary, provider, *, consent=None, grant=True, tools=None, context=None):
    registry = ToolRegistry()
    for tool in tools or [boundary.tool]:
        registry.register(tool)
    thread = AssistantThread("thread", "reader", "Pinned resources")
    thread.consent.always_allow_read_only = grant
    context = context or LoopContext(
        "reader", user=deepcopy(USER), agent_id="agent",
        release_manifest=deepcopy(boundary.manifest), steps=[],
    )
    return TurnResult(frames=[frame async for frame in AssistantLoop(
        provider=provider, registry=registry, system_prompt="test", iterative=True,
        max_iterations=3,
    ).run(thread=thread, user_content="Use the selected capability", context=context,
          resolve_consent=consent or AsyncMock(return_value=True))])


def provider_for(boundary, *, change=lambda: None, calls=None):
    return ChangingProvider([
        calls or tool_call_frame("resource", name=boundary.tool.name,
                                 arguments=boundary.arguments),
        text_frame("Finished"),
    ], change=change, turn_plan={
        "intent": "ui_operation", "tools": [boundary.tool.name],
        "required_tools": [boundary.tool.name], "skills": [], "ml_task": None,
    })


def assert_terminal(provider, result):
    assert result.error_codes == ["release_drift"]
    assert result.finish_reason == "error"
    assert provider.calls == 1
    assert provider.plans == 1
    assert "RESOURCE_DATA" not in "".join(result.frames)
    assert not {"table", "citation", "tool_detail"} & set(result.events)


def assert_blocked(boundary, provider, result):
    assert_terminal(provider, result)
    boundary.tool.run.assert_not_awaited()
    boundary.data_read.assert_not_awaited()


@pytest.mark.parametrize("phase", ["provider", "consent"])
async def test_active_version_change_before_dispatch_is_terminal(boundary, phase):
    frozen = deepcopy(boundary.manifest)
    context = LoopContext("reader", user=deepcopy(USER), agent_id="agent",
                          release_manifest=deepcopy(frozen), steps=[])
    provider = provider_for(
        boundary, change=boundary.change_version if phase == "provider" else lambda: None,
    )

    async def consent(*args):
        boundary.change_version()
        return True

    result = await run_turn(boundary, provider, consent=consent, grant=phase == "provider",
                            context=context)
    assert_blocked(boundary, provider, result)
    assert context.release_manifest == frozen


async def test_live_resource_revocation_after_admission_blocks_data(boundary):
    provider = provider_for(boundary, change=boundary.revoke)
    result = await run_turn(boundary, provider)
    assert_blocked(boundary, provider, result)


async def test_prefetched_resource_cannot_bypass_release_recheck(boundary, monkeypatch):
    entries = []
    dispatched = []
    original_prefetch = AssistantLoop._prefetch
    original_dispatch = AssistantLoop._dispatch_tool

    def prefetch(self, *args):
        entry = original_prefetch(self, *args)
        if entry is not None:
            entries.append(entry)
        return entry

    async def dispatch(self, tool, invocation, context):
        dispatched.append(invocation.tool_call_id)
        return await original_dispatch(self, tool, invocation, context)

    monkeypatch.setattr(AssistantLoop, "_prefetch", prefetch)
    monkeypatch.setattr(AssistantLoop, "_dispatch_tool", dispatch)
    calls = tool_call_frame("first", name=boundary.tool.name, arguments=boundary.arguments)
    extra = deepcopy(boundary.arguments)
    if boundary.tool.name == "ai_search":
        extra["query"] = "another customer"
    else:
        extra["entity_key"] = {"id": 2}
    calls["tool_calls"].extend(tool_call_frame(
        "prefetched", name=boundary.tool.name, arguments=extra,
    )["tool_calls"])
    provider = provider_for(boundary, change=boundary.change_version, calls=calls)
    result = await run_turn(boundary, provider)
    assert_blocked(boundary, provider, result)
    assert len(entries) == 1
    call_id, task, _ = entries[0]
    assert call_id == "prefetched"
    assert task.done() and not task.cancelled()
    assert task.result().error_class == "RELEASE_DRIFT"
    assert set(dispatched) == {"first", "prefetched"}


async def test_definition_change_without_version_bump_is_terminal(boundary):
    def change():
        boundary.definition["entity_id"] = "replacement-entity"

    provider = provider_for(boundary, change=change)
    result = await run_turn(boundary, provider)
    assert_blocked(boundary, provider, result)


@pytest.mark.parametrize("restore", [False, True])
async def test_active_version_change_during_data_read_never_publishes(boundary, restore):
    original = boundary.data_read.side_effect

    async def read(*args):
        boundary.change_version()
        result = await original(*args)
        result["version"] = 2
        if restore:
            boundary.definition["active_version"] = 1
        return result

    boundary.data_read.side_effect = read
    provider = provider_for(boundary)
    result = await run_turn(boundary, provider)
    assert_terminal(provider, result)
    boundary.tool.run.assert_awaited_once()
    boundary.data_read.assert_awaited_once()


async def test_definition_drift_during_same_version_read_discards_result(boundary):
    original = boundary.data_read.side_effect

    async def read(*args):
        result = await original(*args)
        boundary.definition["entity_id"] = "replacement-entity"
        return result

    boundary.data_read.side_effect = read
    provider = provider_for(boundary)
    result = await run_turn(boundary, provider)
    assert_terminal(provider, result)
    boundary.data_read.assert_awaited_once()


async def test_resource_revocation_during_read_discards_result(boundary):
    original = boundary.data_read.side_effect

    async def read(*args):
        result = await original(*args)
        boundary.revoke()
        return result

    boundary.data_read.side_effect = read
    provider = provider_for(boundary)
    result = await run_turn(boundary, provider)
    assert_terminal(provider, result)
    boundary.data_read.assert_awaited_once()


async def test_resource_drift_blocks_mutation_even_for_an_unrelated_tool(boundary):
    mutation = EvalTool("release_effect", classification="destructive")
    provider = ChangingProvider([
        tool_call_frame("effect", name=mutation.name), text_frame("Finished"),
    ], change=boundary.change_version)
    result = await run_turn(boundary, provider, tools=[mutation])
    assert_blocked(boundary, provider, result)
    assert mutation.runs == []


async def test_drift_already_present_at_admission_never_calls_provider(boundary):
    boundary.change_version()
    provider = provider_for(boundary)
    with pytest.raises(HTTPException, match="drifted"):
        await run_turn(boundary, provider)
    assert provider.calls == provider.plans == 0
    boundary.tool.run.assert_not_awaited()
    boundary.data_read.assert_not_awaited()


async def test_unchanged_resource_still_runs_with_current_caller(boundary):
    provider = provider_for(boundary)
    result = await run_turn(boundary, provider)
    assert result.finish_reason == "stop"
    assert result.error_codes == []
    boundary.tool.run.assert_awaited_once()
    boundary.data_read.assert_awaited_once()
    assert boundary.data_read.call_args.args[-1]["username"] == USER["username"]
    assert boundary.data_read.call_args.args[-1]["active_role"] == USER["active_role"]
    assert boundary.listing.await_count >= 4  # Admission and dispatch both authorize.


async def test_feature_explicit_other_version_cannot_bypass_release(feature_boundary):
    boundary = feature_boundary
    boundary.arguments["version"] = 2
    provider = provider_for(boundary)
    result = await run_turn(boundary, provider)
    assert_terminal(provider, result)
    boundary.tool.run.assert_awaited_once()
    boundary.data_read.assert_not_awaited()


@pytest.mark.parametrize("version", [None, 1])
async def test_feature_request_uses_release_version(feature_boundary, version):
    boundary = feature_boundary
    if version is not None:
        boundary.arguments["version"] = version
    provider = provider_for(boundary)
    result = await run_turn(boundary, provider)
    assert result.finish_reason == "stop"
    request = boundary.data_read.call_args.args[1]
    assert request.version == 1
    assert boundary.data_read.call_args.args[2] == USER


async def test_legacy_feature_explicit_version_is_preserved(feature_boundary):
    boundary = feature_boundary
    context = LoopContext("reader", user=deepcopy(USER), agent_id="agent")
    outcome = await boundary.tool.run(ToolInvocation(
        "legacy", boundary.tool.name, {**boundary.arguments, "version": 2},
    ), context)
    assert outcome.ok
    assert outcome.data["version"] == 2
    assert boundary.data_read.call_args.args[1].version == 2


async def test_tool_owner_discards_wrong_version_before_return(boundary):
    original = boundary.data_read.side_effect

    async def read(*args):
        result = await original(*args)
        result["version"] = 2
        return result

    boundary.data_read.side_effect = read
    context = LoopContext("reader", user=deepcopy(USER), agent_id="agent",
                          release_manifest=deepcopy(boundary.manifest))
    outcome = await boundary.tool.run(
        ToolInvocation("owner", boundary.tool.name, boundary.arguments), context,
    )
    assert not outcome.ok
    assert outcome.error_class == "RELEASE_DRIFT"
    assert outcome.data is None and outcome.evidence is None
    assert outcome.table is None and not outcome.citations
    assert "RESOURCE_DATA" not in outcome.summary


@pytest.mark.parametrize("manifest", [None, {"dependencies": {"resources": []}}])
async def test_unpinned_dispatch_does_not_read_release_resources(monkeypatch, manifest):
    contracts = AsyncMock(side_effect=AssertionError("No pinned resources"))
    monkeypatch.setattr(releases, "resource_contracts", contracts)
    tool = EvalTool("release_effect", classification="destructive")
    loop = AssistantLoop(provider=ScriptedProvider([]), registry=ToolRegistry(),
                         system_prompt="test")
    context = LoopContext("reader", user=deepcopy(USER), release_manifest=manifest)
    outcome = await loop._dispatch_tool(tool, ToolInvocation("call", tool.name, {}), context)
    assert outcome.ok
    assert len(tool.runs) == 1
    contracts.assert_not_awaited()
