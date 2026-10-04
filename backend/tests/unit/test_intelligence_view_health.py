"""Semantic View tools honor release pins and expose bounded execution health."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.tools.intelligence_views import FeatureLookupTool, SemanticViewQueryTool
from app.modules.assistant.evidence_health import replay_evidence_health
from app.modules.assistant.tools import ToolInvocation
from app.modules.intelligence.feature_store import feature_store
from app.modules.intelligence.semantic_views import semantic_view_service
from tests.unit.test_semantic_view_canonical import _version, _view


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)


def _context(*, fingerprint="sales-v2", version=2):
    return SimpleNamespace(
        user={"username": "alice", "encrypted_password": "private-encrypted-state",
              "active_role": "OTHER", "security_context_version": 7},
        role="ANALYST", audit_session_id="session-1", agent_id="agent-1",
        semantic_view_ids=["view-1"],
        release_manifest={"dependencies": {"semantic_views": [
            {"view_id": "view-1", "version": version, "fingerprint": fingerprint},
        ]}},
    )


def _invocation(**extra):
    return ToolInvocation("query-1", "semantic_view_query", {
        "view_id": "view-1", "metrics": ["total_revenue"], **extra,
    })


def _result(**extra):
    return {"version": 2, "model_fingerprint": "sales-v2",
            "columns": ["total_revenue", "api_key"], "rows": [[42, "private-value"]], **extra}


@pytest.mark.parametrize("arguments", [{}, {"version": 2}])
async def test_release_version_and_fingerprint_are_passed_to_live_authorized_query(
    monkeypatch, arguments,
):
    query = AsyncMock(return_value=_result(truncated=False))
    monkeypatch.setattr(semantic_view_service, "query", query)
    outcome = await SemanticViewQueryTool().run(_invocation(**arguments), _context())
    assert outcome.ok
    query.assert_awaited_once()
    view, body, user = query.await_args.args
    assert view == "view-1" and body.version == 2
    assert query.await_args.kwargs == {"agent_id": "agent-1", "expected_fingerprint": "sales-v2"}
    assert user["username"] == "alice" and user["active_role"] == "ANALYST"
    assert user["session_id"] == "session-1" and user["security_context_version"] == 7
    assert outcome.table["rows"] == [[42, "***"]]
    health = replay_evidence_health(outcome.metadata["evidence_health"])
    assert health.label == "moderate"
    assert health.facts.semantic_version == 2 and health.facts.semantic_fingerprint == "sales-v2"
    assert health.facts.semantic_grounding == "published" and health.facts.coverage == "complete"
    assert health.facts.verified_query_hit is False
    assert health.data_freshness.status == "unknown" and health.facts.causal_strength == "unknown"
    assert "private-encrypted-state" not in str(health.model_dump())


@pytest.mark.parametrize("case", [
    "changed_version", "boolean_version", "string_version", "missing_pin", "duplicate_pin",
    "missing_fingerprint", "invalid_fingerprint", "invalid_pin_version", "empty_manifest",
    "unbound_view", "missing_agent",
])
async def test_invalid_or_changed_release_requests_fail_before_query(monkeypatch, case):
    context = _context()
    arguments = {}
    pins = context.release_manifest["dependencies"]["semantic_views"]
    if case in {"changed_version", "boolean_version", "string_version"}:
        arguments["version"] = {"changed_version": 3, "boolean_version": True,
                                "string_version": "2"}[case]
    elif case == "missing_pin":
        pins.clear()
    elif case == "duplicate_pin":
        pins.append(deepcopy(pins[0]))
    elif case == "missing_fingerprint":
        pins[0].pop("fingerprint")
    elif case == "invalid_fingerprint":
        pins[0]["fingerprint"] = "token=private-secret"
    elif case == "invalid_pin_version":
        pins[0]["version"] = True
    elif case == "empty_manifest":
        context.release_manifest = {}
    elif case == "unbound_view":
        context.semantic_view_ids = []
    elif case == "missing_agent":
        context.agent_id = None
    query = AsyncMock(return_value=_result())
    monkeypatch.setattr(semantic_view_service, "query", query)
    outcome = await SemanticViewQueryTool().run(_invocation(**arguments), context)
    assert not outcome.ok and outcome.table is None
    query.assert_not_awaited()
    assert outcome.metadata["evidence_health"]["label"] == "insufficient"
    assert "private-secret" not in str(outcome)


@pytest.mark.parametrize("patch", [
    {"version": 3}, {"model_fingerprint": "changed"}, {"model_fingerprint": None},
])
async def test_returned_pin_drift_never_releases_rows_as_evidence(monkeypatch, patch):
    monkeypatch.setattr(semantic_view_service, "query", AsyncMock(return_value=_result(**patch)))
    outcome = await SemanticViewQueryTool().run(_invocation(), _context())
    assert not outcome.ok and outcome.error_class == "POLICY_VIOLATION"
    assert outcome.table is None and "private-value" not in str(outcome)
    assert outcome.metadata["evidence_health"]["label"] == "insufficient"


@pytest.mark.parametrize("bounded", ["rows", "columns", "fetch", "unknown"])
async def test_semantic_view_fetch_and_preview_bounds_do_not_overstate_coverage(
    monkeypatch, bounded,
):
    result = _result()
    if bounded == "rows":
        result["rows"] = [[n, "private-value"] for n in range(101)]
    elif bounded == "columns":
        result["columns"] = ["total_revenue"] * 101
        result["rows"] = [[42] * 101]
    elif bounded == "fetch":
        result["truncated"] = True
    monkeypatch.setattr(semantic_view_service, "query", AsyncMock(return_value=result))
    outcome = await SemanticViewQueryTool().run(_invocation(), _context())
    assert outcome.ok
    health = outcome.metadata["evidence_health"]
    assert health["facts"]["coverage"] == ("unknown" if bounded == "unknown" else "truncated")
    assert health["label"] == "limited"
    assert len(outcome.table["rows"]) <= 100 and len(outcome.table["columns"]) <= 100
    assert all(len(row) <= 100 for row in outcome.table["rows"])


@pytest.mark.parametrize("field_count", [1, 101])
async def test_feature_health_keeps_freshness_and_grounding_unknown(monkeypatch, field_count):
    lookup = AsyncMock(return_value={
        "version": 3, "as_of": "2026-10-03T10:00:00Z",
        "values": {f"value_{index}": index for index in range(field_count)},
    })
    monkeypatch.setattr(feature_store, "lookup", lookup)
    tool = FeatureLookupTool({"feature_groups": ["customer"]})
    context = _context()
    context.release_manifest["dependencies"]["resources"] = [
        {"kind": "feature_group", "id": "customer", "version": 3,
         "definition_digest": "customer-v3"},
    ]
    outcome = await tool.run(ToolInvocation("features", tool.name, {
        "group": "customer", "entity_key": {"customer_id": 1},
    }), context)
    assert outcome.ok and len(outcome.data["values"]) == min(field_count, 100)
    lookup.assert_awaited_once()
    assert lookup.await_args.args[1].version == 3
    health = replay_evidence_health(outcome.metadata["evidence_health"])
    assert health.label == "limited" and health.facts.execution_status == "success"
    assert health.facts.coverage == ("truncated" if field_count > 100 else "unknown")
    assert health.facts.data_as_of is None and health.data_freshness.status == "unknown"
    assert health.facts.semantic_grounding == "unknown"
    assert "causal_strength" in health.unknown_signals
    assert "source_agreement" in health.unknown_signals


async def test_old_published_pin_executes_under_live_caller_authorization(monkeypatch):
    row = _version(status="DEPRECATED")
    context = _context(fingerprint=row["fingerprint"], version=1)
    monkeypatch.setattr(semantic_view_service, "_get", AsyncMock(return_value={
        **_view(), "active_version": 99,
    }))
    monkeypatch.setattr(semantic_view_service, "_version", AsyncMock(return_value=row))
    source = AsyncMock(return_value=True)
    monkeypatch.setattr(semantic_view_service, "_source_access", source)
    monkeypatch.setattr(semantic_view_service, "_entity_access", AsyncMock(return_value=True))
    execute = AsyncMock(return_value=_result(version=1, model_fingerprint=row["fingerprint"]))
    monkeypatch.setattr(semantic_view_service, "_execute_plan", execute)
    authorize = AsyncMock(wraps=semantic_view_service.get_version_for_agent)
    monkeypatch.setattr(semantic_view_service, "get_version_for_agent", authorize)
    outcome = await SemanticViewQueryTool().run(_invocation(), context)
    assert outcome.ok
    authorize.assert_awaited_once()
    assert authorize.await_args.args[:3] == ("view-1", 1, row["fingerprint"])
    assert authorize.await_args.args[3]["security_context_version"] == 7
    assert authorize.await_args.kwargs["agent_id"] == "agent-1"
    execute.assert_awaited_once()
    assert execute.await_args.args[1]["version"] == 1
    assert execute.await_args.args[4]["active_role"] == "ANALYST"
    assert all(call.args[1]["username"] == "alice" for call in source.await_args_list)


@pytest.mark.parametrize("change", ["source_revoked", "entity_revoked", "fingerprint_drift"])
async def test_live_revocation_and_definition_drift_stop_pinned_execution(monkeypatch, change):
    row = _version()
    context = _context(fingerprint=row["fingerprint"], version=1)
    if change == "fingerprint_drift":
        row["fingerprint"] = "changed"
    monkeypatch.setattr(semantic_view_service, "_get", AsyncMock(return_value=_view()))
    monkeypatch.setattr(semantic_view_service, "_version", AsyncMock(return_value=row))
    monkeypatch.setattr(semantic_view_service, "_source_access", AsyncMock(
        return_value=change != "source_revoked",
    ))
    monkeypatch.setattr(semantic_view_service, "_entity_access", AsyncMock(
        return_value=change != "entity_revoked",
    ))
    execute = AsyncMock(return_value=_result())
    monkeypatch.setattr(semantic_view_service, "_execute_plan", execute)
    outcome = await SemanticViewQueryTool().run(_invocation(), context)
    assert not outcome.ok
    execute.assert_not_awaited()
    assert outcome.metadata["evidence_health"]["label"] == "insufficient"
