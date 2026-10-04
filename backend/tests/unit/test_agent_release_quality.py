from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import quality, releases, service
from app.modules.agents.quality_scoring import (
    SCORERS,
    Assertion,
    promotion_eligible,
    score_assertion,
)
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tool_gate import resolve_tool_consent
from app.modules.assistant.tools import ToolInvocation, ToolRegistry
from app.modules.intelligence.contracts import fingerprint

USER = {
    "username": "alice",
    "active_role": "ANALYST",
    "assigned_roles": ["ANALYST", "ADMIN"],
    "session_id": "session",
    "security_context_version": 2,
}
EXPECTED = {
    "semantic_selection": {"metrics": ["revenue"]},
    "tool_selection": {"required": ["semantic_query"]},
    "tool_arguments": {"semantic_query": {"question": "Q1"}},
    "numeric_consistency": {"accepted": True},
    "evidence_coverage": {"minimum": 1},
    "clarification_quality": {"requested": True},
    "task_completeness": {"completed": True},
    "policy_compliance": {"consent_resolved": True},
    "action_verification": {"verified": True},
    "latency": {"max_ms": 2000},
    "efficiency": {"tool_calls": 2},
}


@pytest.mark.parametrize("scorer", sorted(SCORERS))
def test_all_scorer_families_refuse_missing_evidence(scorer):
    result = score_assertion(Assertion(scorer=scorer, expected=EXPECTED[scorer]), {})
    assert result.status == "unavailable"
    assert result.failure_taxonomy


@pytest.mark.parametrize(
    ("scorer", "expected", "trace"),
    [
        (
            "semantic_selection",
            {"metrics": ["revenue"]},
            {"facts": {"semantic_selection": {"metrics": ["revenue"]}}},
        ),
        (
            "tool_selection",
            {"required": ["semantic_query"], "forbidden": ["query_mutate"]},
            {"tool_names": ["semantic_query"]},
        ),
        (
            "tool_arguments",
            {"semantic_query": {"question": "Q1"}},
            {"facts": {"tool_arguments": {"semantic_query": {"question": "Q1"}}}},
        ),
        (
            "numeric_consistency",
            {"accepted": True, "unsupported_count": 0},
            {"facts": {"numeric_consistency": {"accepted": True, "unsupported_count": 0}}},
        ),
        (
            "evidence_coverage",
            {"minimum": 1, "complete": True},
            {"evidence": [{"id": "evidence-1", "complete": True}]},
        ),
        (
            "clarification_quality",
            {"requested": True},
            {"facts": {"clarification_quality": {"requested": True}}},
        ),
        (
            "task_completeness",
            {"completed": True},
            {"facts": {"task_completeness": {"completed": True}}},
        ),
        (
            "policy_compliance",
            {"consent_resolved": True},
            {"facts": {"policy_compliance": {"consent_resolved": True}}},
        ),
        (
            "action_verification",
            {"verified": True},
            {"facts": {"action_verification": {"verified": True}}},
        ),
        ("latency", {"max_ms": 2000}, {"duration_ms": 1000}),
        ("efficiency", {"tool_calls": 2}, {"counts": {"tool_calls": 1}}),
    ],
)
def test_scorers_use_recorded_facts(scorer, expected, trace):
    assert score_assertion(Assertion(scorer=scorer, expected=expected), trace).status == "pass"


def test_wrong_period_and_unsupported_number_fail():
    assert (
        score_assertion(
            Assertion(scorer="semantic_selection", expected={"period": "Q1"}),
            {"facts": {"semantic_selection": {"period": "Q2"}}},
        ).status
        == "fail"
    )
    assert (
        score_assertion(
            Assertion(scorer="numeric_consistency", expected={"accepted": True}),
            {"facts": {"numeric_consistency": {"accepted": False}}},
        ).status
        == "fail"
    )


def test_critical_promotion_requires_complete_matching_revision():
    cases = [{"id": "critical", "revision": 2, "mandatory": True, "critical": True}]
    assert not promotion_eligible(cases, [])
    assert not promotion_eligible(
        cases, [{"case_id": "critical", "case_revision": 1, "status": "passed"}]
    )
    assert not promotion_eligible(
        cases, [{"case_id": "critical", "case_revision": 2, "status": "unavailable"}]
    )
    assert promotion_eligible(
        cases, [{"case_id": "critical", "case_revision": 2, "status": "passed"}]
    )
    assert not promotion_eligible([], [])


@pytest.mark.asyncio
async def test_live_consent_never_inherits_read_grant_for_mutation():
    tool = type("Tool", (), {"requires_consent": True})()
    thread = AssistantThread("thread", "alice", "Test")
    thread.consent.always_allow_read_only = True
    resolver = AsyncMock(return_value=False)
    invocation = ToolInvocation("call", "mutate", {})
    assert await resolve_tool_consent(tool, invocation, "read_only", thread, resolver)
    resolver.assert_not_awaited()
    assert not await resolve_tool_consent(tool, invocation, "destructive", thread, resolver)
    resolver.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_manifest_rejects_model_drift(monkeypatch):
    dependency = {
        "id": "model",
        "provider_id": "provider",
        "name": "model",
        "configuration_digest": "original",
        "externally_mutable": True,
    }
    dependencies = {"models": [dependency]}
    manifest = {"dependencies": dependencies, "fingerprint": fingerprint(dependencies)}
    monkeypatch.setattr(releases, "get_manifest", AsyncMock(return_value=manifest))
    monkeypatch.setattr(
        releases,
        "model_contract",
        AsyncMock(return_value={**dependency, "configuration_digest": "changed"}),
    )
    with pytest.raises(HTTPException, match="drifted"):
        await releases.load_runtime_manifest(
            {"agent_id": "agent", "owner_name": "alice", "release_manifest_id": "release"}
        )


@pytest.mark.asyncio
async def test_pinned_skills_ignore_current_catalog(monkeypatch):
    dependencies = {
        "configuration": {
            "name": "Agent",
            "default_tools": [],
            "resource_bindings": {},
            "default_skills": ["skill"],
            "discoverable_skills": [],
        },
        "skills": [
            {
                "name": "skill",
                "summary": "Pinned",
                "triggers": [],
                "body": "Original instruction",
                "trust_level": "user_skill",
            }
        ],
        "tools": [],
        "default_skills": ["skill"],
        "discoverable_skills": [],
        "compiled_prompt": "Pinned prompt",
    }
    monkeypatch.setattr(
        releases, "load_runtime_manifest", AsyncMock(return_value={"dependencies": dependencies})
    )
    monkeypatch.setattr(releases, "assert_tool_contracts", lambda *_: None)
    monkeypatch.setattr(
        service.agent_repository,
        "list_skills",
        AsyncMock(
            return_value=[
                {"name": "skill", "body": "Changed instruction"},
                {"name": "new", "body": "Unexpected"},
            ]
        ),
    )
    registry, prompt, _, _ = await service.agent_service.build_loop_inputs(
        {"agent_id": "agent", "owner_name": "alice", "release_manifest_id": "release"}
    )
    assert prompt == "Pinned prompt"
    assert registry.skill_definitions["skill"].body == "Original instruction"
    assert "new" not in registry.skill_definitions


@pytest.mark.asyncio
async def test_promotion_invalidates_changed_required_cases(monkeypatch):
    run = {
        "manifest_id": "manifest",
        "scorer_set_version": releases.SCORER_SET_VERSION,
        "cases": [{"id": "case", "revision": 1, "mandatory": True, "critical": True}],
        "results": [{"case_id": "case", "case_revision": 1, "status": "passed"}],
        "promotion_eligible": True,
    }

    async def records(kind, *_):
        return [run] if kind == "runs" else [{"id": "case", "revision": 2, "mandatory": True}]

    monkeypatch.setattr(quality, "records", records)
    with pytest.raises(HTTPException, match="changed"):
        await quality.require_promotion("agent", {"id": "manifest"}, "run", USER)


def test_tool_digest_detects_configuration_changes():
    registry = ToolRegistry()
    tool = type(
        "Tool",
        (),
        {
            "name": "t",
            "parameters": {"type": "object"},
            "description": "Run",
            "classification": "read_only",
            "tool": {"definition": {"function": "original"}},
        },
    )()
    registry.register(tool)
    contracts = deepcopy(releases.tool_contracts(registry))
    tool.tool["definition"]["function"] = "changed"
    assert releases.tool_contracts(registry) != contracts
