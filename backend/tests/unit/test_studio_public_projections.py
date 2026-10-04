"""Public boundaries preserve governed facts while withholding durable execution state."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.deps import get_current_user
from app.modules.agents import mission_router
from app.modules.agents.mission_schema import Mission, ResumableMission
from app.modules.agents.public_projections import (
    PublicMission,
    PublicMissionDeliverable,
    public_business_result,
    public_mission,
    sanitize_workflow_payload,
    sanitize_workflow_sse,
)
from app.modules.assistant.evidence_health import EvidenceFacts, assess_evidence
from app.modules.intelligence import action_router
from app.modules.intelligence.action_contracts import Action, ActionRead
from app.modules.intelligence.contracts import (
    Confidence,
    EvidenceRef,
    Hypothesis,
    Investigation,
    Monitor,
    MonitorConfiguration,
    NewsItem,
    Scope,
    SemanticRef,
    Window,
)
from app.modules.intelligence.public_projections import (
    PUBLIC_RECORD_MODELS,
    PublicAction,
    PublicEvidenceRef,
    public_action,
    public_canonical_record,
    public_investigation,
)

NOW = datetime(2026, 10, 4, 9, tzinfo=UTC)
USER = {
    "username": "alice",
    "active_role": "ANALYST",
    "assigned_roles": ["ANALYST"],
    "session_id": "private-session",
    "security_context_version": 7,
}
SCOPE = Scope.from_user(USER)
SEMANTIC = SemanticRef(view_id="sales", version=3, fingerprint="published-sales")
WINDOW = Window(start=NOW - timedelta(days=1), end=NOW)
HEALTH = assess_evidence(
    EvidenceFacts(
        semantic_grounding="published",
        semantic_view_id="sales",
        semantic_version=3,
        semantic_fingerprint="published-sales",
        execution_status="success",
        coverage="complete",
        semantic_ambiguity="none",
        causal_strength="arithmetic",
    ),
    assessed_at=NOW,
)


@pytest.fixture
def evidence():
    return EvidenceRef(
        id="evidence",
        source_type="query",
        source_id="governed-query",
        scope=SCOPE,
        semantic=SEMANTIC,
        method="semantic-query-v1",
        digest="d" * 64,
        observed_at=NOW,
        window_start=WINDOW.start,
        window_end=WINDOW.end,
        relations=["SALES.ORDERS"],
        semantic_plan={"metrics": ["revenue"], "filters": [{"value": "private-population"}]},
        evidence_health=HEALTH,
    )


@pytest.fixture
def investigation(evidence):
    return Investigation(
        id="investigation",
        revision=3,
        created_at=NOW,
        updated_at=NOW,
        scope=SCOPE,
        news_id="news",
        semantic=SEMANTIC,
        residual=-12,
        method="dimension-difference-v2",
        status="complete",
        evidence=[evidence],
        hypotheses=[
            Hypothesis(
                id="enterprise",
                label="Enterprise decline",
                contribution=-48,
                causal_status="arithmetic",
                next_test="Review region breakdown",
                confidence=Confidence(dimension="causal", method="arithmetic", value=0.8),
                evidence_ids=[evidence.id],
                contradicting_ids=["other-evidence"],
            ),
            Hypothesis(
                id="campaign",
                label="Campaign activity",
                causal_status="association",
                confidence=Confidence(dimension="causal", method="aligned-window"),
            ),
        ],
        timeline=[{"at": NOW.isoformat(), "kind": "detection", "reference": "news"}],
        decompositions=[
            {
                "dimension": "region",
                "change": "-60",
                "residual": "-12",
                "reconciled": True,
                "components": [{"segment": "Enterprise", "contribution": "-48"}],
            }
        ],
    )


@pytest.fixture
def mission():
    established = {
        "schema_version": 1,
        "health": HEALTH.model_dump(mode="json"),
        "semantic": SEMANTIC.model_dump(),
        "metrics": ["revenue"],
        "validated_plan_fingerprint": "p" * 64,
        "model_fingerprint": "published-sales",
    }
    return Mission(
        mission_id="mission",
        thread_id="thread",
        agent_id="finance",
        scope=SCOPE,
        objective="Investigate revenue decline",
        work_intent="INVESTIGATE",
        status="running",
        revision=4,
        operation_id="private-operation",
        request_digest="q" * 64,
        created_at=NOW,
        updated_at=NOW,
        run_ids=["run"],
        evidence_refs=["evidence"],
        stages=[{"kind": "investigate", "label": "Investigate", "status": "running"}],
        object_refs=[{"kind": "investigation", "id": "investigation", "revision": 3}],
        projection_cursor={"run": 10},
        semantic_anchors=[
            {
                "semantic": SEMANTIC,
                "metrics": ["revenue"],
                "filter_population_fingerprint": "f" * 64,
            }
        ],
        continuation={"mode": "continue", "reason": "semantic_anchor", "mission_id": "mission"},
        investigation_requirements={
            "required_inputs": ["driver_dimension"],
            "established": established,
        },
    )


@pytest.fixture(params=["monitor-v1", "automation-v1"])
def action(request):
    monitor = MonitorConfiguration(
        name="Revenue monitor",
        agent_id="finance",
        semantic=SEMANTIC,
        plan={
            "metrics": ["revenue"],
            "filters": [{"field": "region", "operator": "=", "value": "Enterprise"}],
        },
        value_column="revenue",
        time_dimension="ordered_at",
        timezone="UTC",
    )
    configuration = (
        monitor
        if request.param == "monitor-v1"
        else {
            "agent_id": "finance",
            "semantic": SEMANTIC,
            "title": "Revenue review",
            "prompt": "Review governed revenue",
            "schedule_kind": "interval",
            "schedule_expr": "60 minutes",
            "timezone": "UTC",
            "delivery": "studio",
        }
    )
    receipt = (
        {
            "monitor_id": "monitor",
            "monitor_revision": 2,
            "task_id": "task",
            "schedule_enabled": True,
        }
        if request.param == "monitor-v1"
        else {
            "automation_id": "automation",
            "configuration_digest": "c" * 64,
            "schedule_enabled": True,
            "delivery": "studio",
        }
    )
    return Action(
        id="action",
        created_at=NOW,
        updated_at=NOW,
        scope=SCOPE,
        revision=2,
        decision_id="decision",
        decision_revision=5,
        decision_digest="d" * 64,
        option_id="selected",
        semantic=SEMANTIC,
        adapter_id=request.param,
        action_type="monitor" if request.param == "monitor-v1" else "automation",
        configuration=configuration,
        idempotency_key="private-idempotency",
        request_digest="r" * 64,
        mission_id="mission",
        status="awaiting_consent",
        expected_effect="Create governed monitoring",
        consent_call_id="current-consent",
        dispatch_fence="private-fence",
        last_operation_digest="o" * 64,
        policy={
            "decision": "REQUIRE_APPROVAL",
            "reason": "Review required",
            "policy_id": "policy",
            "policy_revision": 2,
            "context_digest": "p" * 64,
        },
        approval={
            "actor": "reviewer",
            "active_role": "REVIEWER",
            "security_context_version": 8,
            "session_id": "reviewer-session",
            "policy_digest": "p" * 64,
            "operation_id": "approval-operation",
            "approved_at": NOW,
        },
        receipt=receipt,
        compensation_receipt=receipt,
        verification={"checked_at": NOW, "complete": False, "reason": "readback_failed"},
    )


def assert_private_fields_absent(value):
    prohibited = {
        "scope",
        "session_id",
        "security_context_version",
        "owner_scope",
        "current_binding",
        "run_bindings",
        "object_bindings",
        "historical_bindings",
        "projection_cursor",
        "operation_id",
        "request_digest",
        "digest",
        "semantic_plan",
        "dispatch_fence",
        "policy_digest",
        "context_digest",
        "configuration_digest",
        "idempotency_key",
        "resume_operations",
        "turn_operations",
        "execution_contexts",
        "release_pins",
        "private_future_field",
        "worker_id",
        "lease_id",
        "last_operation_digest",
    }
    if isinstance(value, dict):
        assert not prohibited & value.keys()
        for item in value.values():
            assert_private_fields_absent(item)
    elif isinstance(value, list):
        for item in value:
            assert_private_fields_absent(item)


def test_mission_allowlist_preserves_ui_contract_and_never_mutates_source(mission):
    raw = mission.model_dump(mode="json")
    raw["private_future_field"] = "not-for-browser"
    raw["stages"][0]["worker_id"] = "worker"
    raw["object_refs"][0]["lease_id"] = "lease"
    raw["investigation_requirements"]["established"]["scope"] = SCOPE.model_dump()
    before = deepcopy(raw)
    projected = public_mission(raw)
    assert isinstance(projected, PublicMission)
    data = projected.model_dump(mode="json")
    assert_private_fields_absent(data)
    assert data["mission_id"] == "mission" and data["revision"] == 4
    assert data["run_ids"] == ["run"] and data["evidence_refs"] == ["evidence"]
    assert data["object_refs"] == [{"kind": "investigation", "id": "investigation", "revision": 3}]
    assert data["continuation"]["reason"] == "semantic_anchor"
    assert data["semantic_anchors"][0] == {
        "semantic": SEMANTIC.model_dump(),
        "metrics": ["revenue"],
    }
    assert data["investigation_requirements"]["established"]["health"] == HEALTH.model_dump(
        mode="json"
    )
    assert raw == before


def test_investigation_has_explicit_nested_evidence_and_causal_allowlists(investigation):
    raw = investigation.model_dump(mode="json")
    raw["evidence"][0]["private_future_field"] = "new-persistence-detail"
    raw["hypotheses"][0]["worker_id"] = "worker"
    raw["timeline"][0]["scope"] = SCOPE.model_dump()
    raw["decompositions"][0]["components"][0]["request_digest"] = "hidden"
    data = public_investigation(raw).model_dump(mode="json")
    assert_private_fields_absent(data)
    assert data["hypotheses"][0]["contribution"] == -48
    assert [item["causal_status"] for item in data["hypotheses"]] == ["arithmetic", "association"]
    assert "value" not in data["hypotheses"][0]["confidence"]
    assert data["evidence"][0]["source_id"] == "governed-query"
    assert data["evidence"][0]["evidence_health"] == HEALTH.model_dump(mode="json")
    assert data["semantic"] == SEMANTIC.model_dump()
    assert data["decompositions"][0]["components"] == [
        {"segment": "Enterprise", "contribution": "-48"}
    ]


def test_action_preserves_live_consent_and_safe_business_receipts(action):
    raw = action.model_dump(mode="json")
    raw["configuration"]["private_future_field"] = "dispatch-internals"
    raw["policy"]["worker_id"] = "worker"
    raw["approval"]["lease_id"] = "lease"
    if action.adapter_id == "monitor-v1":
        raw["configuration"]["plan"]["scope"] = SCOPE.model_dump()
        raw["configuration"]["plan"]["filters"][0]["session_id"] = "hidden"
    data = public_action(raw, execution_current=True).model_dump(mode="json")
    assert_private_fields_absent(data)
    assert data["execution_current"] and data["consent_call_id"] == "current-consent"
    assert data["approval"] == {
        "actor": "reviewer",
        "active_role": "REVIEWER",
        "approved_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    assert data["receipt"]["schedule_enabled"]
    assert data["compensation_receipt"]["schedule_enabled"]
    assert data["configuration"]["timezone"] == "UTC"
    assert data["verification"]["reason"] == "readback_failed"


def test_historical_action_cannot_advertise_current_consent(action):
    historical = ActionRead.model_validate(action.model_dump() | {"execution_current": False})
    public = public_action(historical)
    assert not public.execution_current and public.consent_call_id is None
    assert public_action(action).consent_call_id is None
    current = ActionRead.model_validate(action.model_dump() | {"execution_current": True})
    assert public_action(current).consent_call_id == "current-consent"
    settled = current.model_copy(update={"status": "verified"})
    assert public_action(settled).consent_call_id is None


def test_deliverable_public_sources_exclude_frozen_internal_fact_payloads():
    raw = {
        "deliverable_id": "deliverable",
        "mission_id": "mission",
        "mission_revision": 3,
        "kind": "investigation_report",
        "title": "Revenue report",
        "markdown": "Evidence summary",
        "evidence_refs": ["evidence"],
        "object_refs": [],
        "created_at": NOW,
        "sources": [
            {
                "kind": "mission",
                "id": "mission",
                "revision": 3,
                "fingerprint": "f" * 64,
                "facts": {"scope": SCOPE.model_dump(), "release_pins": ["internal"]},
            }
        ],
    }
    data = PublicMissionDeliverable.model_validate(raw).model_dump(mode="json")
    assert_private_fields_absent(data)
    assert "facts" not in data["sources"][0]
    assert data["sources"][0]["fingerprint"] == "f" * 64


def test_public_hook_event_is_bounded_and_does_not_publish_trace_metadata(mission, investigation):
    data = public_business_result(
        {
            "status": "complete",
            "mission_id": mission.mission_id,
            "mission": mission.model_dump(),
            "investigation": investigation.model_dump(),
            "comparison_id": "comparison",
            "trace_metadata": {"lease_id": "private"},
            "provider_observation": {"private": "value"},
        }
    )
    assert_private_fields_absent(data)
    assert "trace_metadata" not in data and "provider_observation" not in data
    assert data["investigation"]["hypotheses"][0]["label"] == "Enterprise decline"
    assert data["comparison_id"] == "comparison"


def test_legacy_thread_fragments_use_same_projection_as_live_records(mission, investigation):
    live = {
        "status": "complete",
        "mission": mission.model_dump(mode="json"),
        "investigation": investigation.model_dump(mode="json"),
    }
    transcript = [
        {
            "message_id": "message",
            "steps": [
                {
                    "kind": "tool",
                    "name": "semantic_query",
                    "trace_detail": {"business_result": live, "duration_ms": 12},
                }
            ],
        }
    ]
    before = deepcopy(transcript)
    replay = sanitize_workflow_payload(transcript)
    result = replay[0]["steps"][0]["trace_detail"]["business_result"]
    assert result == public_business_result(live)
    assert replay[0]["steps"][0]["trace_detail"]["duration_ms"] == 12
    assert sanitize_workflow_payload(replay) == replay
    assert_private_fields_absent(replay)
    assert transcript == before


@pytest.mark.parametrize("event", ["mission_updated", "business_result"])
def test_legacy_sse_keeps_run_id_sequence_and_event_id(mission, investigation, event):
    import json

    payload = {"mission": mission.model_dump(mode="json"), "run_id": "run", "sequence": 41}
    if event == "business_result":
        payload.update(status="complete", investigation=investigation.model_dump(mode="json"))
    frame = f"id: event-41\nevent: {event}\ndata: {json.dumps(payload)}\n\n"
    safe = sanitize_workflow_sse(frame)
    result = json.loads(safe.split("data: ", 1)[1])
    assert safe.startswith(f"id: event-41\nevent: {event}\n")
    assert result["run_id"] == "run" and result["sequence"] == 41
    assert result["mission"] == public_mission(mission).model_dump(mode="json")
    assert_private_fields_absent(result)
    assert sanitize_workflow_sse(safe) == safe


@pytest.mark.parametrize("raw", [None, "not a mission", {"scope": {"session_id": "private"}}])
def test_malformed_legacy_fragments_are_withheld(raw):
    result = sanitize_workflow_payload({"mission": raw, "business_result": raw})
    assert result == {"mission": None, "business_result": None}


def test_invalid_workflow_json_is_never_replayed_verbatim():
    frame = "event: mission_updated\ndata: private-invalid-payload\n\n"
    safe = sanitize_workflow_sse(frame)
    assert "private-invalid-payload" not in safe
    assert "invalid_workflow_projection" in safe
    assert sanitize_workflow_sse("event: ping\ndata: {}\n\n") == "event: ping\ndata: {}\n\n"


def test_retained_credential_shapes_are_rejected_and_legacy_is_withheld(investigation):
    raw = investigation.model_dump(mode="json")
    raw["hypotheses"][0]["label"] = "https://user:password@example.com/data"
    with pytest.raises(ValidationError):
        public_investigation(raw)
    assert sanitize_workflow_payload({"investigation": raw}) == {"investigation": None}


def test_unknown_canonical_kinds_fail_closed(investigation):
    with pytest.raises(KeyError):
        public_canonical_record("private_runtime", investigation)


def test_all_registered_intelligence_records_have_public_contracts():
    from app.modules.intelligence.engine import MODELS

    assert MODELS.keys() == PUBLIC_RECORD_MODELS.keys()
    for model in PUBLIC_RECORD_MODELS.values():
        assert "scope" not in model.model_fields


def test_quality_and_canonicalization_measurement_scope_labels_remain_public():
    from app.modules.intelligence.engine import CanonicalizationMetrics

    value = {
        "quality_observation": {"scope": "current_loop_attempt", "tool_count": 2},
        "business_canonicalization": CanonicalizationMetrics(
            business_canonicalization_status="created",
            automatic_investigation_query_count=3,
        ).model_dump(mode="json"),
        "security": {"scope": SCOPE.model_dump(mode="json")},
        "unsafe_label": {"scope": "ANALYST"},
    }
    safe = sanitize_workflow_payload(value)
    assert safe["quality_observation"] == value["quality_observation"]
    assert safe["business_canonicalization"] == value["business_canonicalization"]
    assert safe["security"] == {} and safe["unsafe_label"] == {}
    assert sanitize_workflow_payload(safe) == safe
    replay = {
        "kind": "quality_observation",
        "measurement": value["quality_observation"],
        "facts": {"business_canonicalization": [value["business_canonicalization"]]},
    }
    assert sanitize_workflow_payload(replay) == replay
    from app.modules.intelligence.responses import public_evidence

    assert public_evidence(replay) == replay


def test_already_public_mission_investigation_and_action_are_idempotent(
    mission, investigation, action
):
    value = {
        "mission": public_mission(mission).model_dump(mode="json"),
        "investigation": public_investigation(investigation).model_dump(mode="json"),
        "action": public_action(action, execution_current=True).model_dump(mode="json"),
        "workflow": {"mission_id": "mission", "run_id": "run", "root_run_id": "root"},
    }
    assert sanitize_workflow_payload(value) == value
    assert sanitize_workflow_payload(sanitize_workflow_payload(value)) == value


def test_provider_canonical_observation_keeps_numbers_citations_and_verifier_references():
    from tests.unit.test_business_observation_boundary import observation

    canonical = observation() | {
        "numeric_evidence_refs": {"comparison": "evidence_2", "hypotheses": "evidence_3"},
        "evidence_refs": [
            {
                "id": "citation",
                "source_type": "query",
                "source_id": "query",
                "method": "semantic-query",
                "semantic": SEMANTIC.model_dump(),
                "window_start": WINDOW.start.isoformat(),
                "window_end": WINDOW.end.isoformat(),
            }
        ],
    }
    value = {
        "canonical_business_result": canonical,
        "workflow": {"mission_id": "mission", "run_id": "child", "root_run_id": "root"},
    }
    assert sanitize_workflow_payload(value) == value
    legacy = deepcopy(value)
    legacy["workflow"]["session_id"] = "private"
    legacy["canonical_business_result"]["evidence_refs"][0]["scope"] = SCOPE.model_dump()
    legacy["canonical_business_result"]["private_future_field"] = "private"
    legacy["canonical_business_result"]["numeric_evidence_refs"]["lease_id"] = "private"
    assert sanitize_workflow_payload(legacy) == value


def test_parent_canonical_truncation_and_retained_hypothesis_references_survive_replay(
    investigation,
    evidence,
):
    from app.modules.agents.business_results import canonical_observation

    references = [evidence.model_copy(update={"id": f"citation-{i}"}) for i in range(21)]
    hypothesis = investigation.hypotheses[0].model_copy(
        update={
            "id": "h" * 160,
            "dimension": "d" * 160,
            "evidence_ids": [item.id for item in references],
        }
    )
    investigation = investigation.model_copy(
        update={"evidence": references, "hypotheses": [hypothesis]}
    )
    news = SimpleNamespace(
        id=investigation.news_id,
        revision=investigation.news_revision,
        semantic=investigation.semantic,
        before=100,
        after=40,
        change=-60,
        relative_change=-0.6,
    )
    comparison = SimpleNamespace(
        id="comparison",
        revision=1,
        semantic=investigation.semantic,
        investigation_id=investigation.id,
        investigation_revision=investigation.revision,
        news_id=news.id,
        configuration=None,
        current_window=WINDOW,
        baseline_window=WINDOW,
        calendar_timezone="UTC",
    )
    canonical = canonical_observation(investigation, news, comparison, target_metric="revenue")
    retained_ids = {item["id"] for item in canonical["evidence_refs"]}
    assert len(retained_ids) <= 20
    assert canonical["hypotheses"][0]["evidence_refs"] == [
        item.id for item in references if item.id in retained_ids
    ]
    assert len(canonical["hypotheses"][0]["id"]) <= 128
    assert len(canonical["hypotheses"][0]["dimension"]) <= 128
    assert canonical["truncation"]["omitted_hypothesis_refs"] == 21 - len(retained_ids)
    assert canonical["truncation"]["truncated"] is True
    replay = {"canonical_business_result": canonical}
    assert sanitize_workflow_payload(replay) == replay
    assert sanitize_workflow_payload(sanitize_workflow_payload(replay)) == replay


def test_public_monitor_plan_preserves_reviewed_request_shape():
    monitor = Monitor(
        id="monitor",
        scope=SCOPE,
        name="Revenue",
        agent_id="finance",
        semantic=SEMANTIC,
        plan={"metrics": ["revenue"], "time": {"dimension": "ordered_at", "range": "this month"}},
        value_column="revenue",
        time_dimension="ordered_at",
    )
    public = public_canonical_record("monitors", monitor)
    assert public["plan"] == monitor.plan
    assert sanitize_workflow_payload(public) == public


@pytest.mark.parametrize("nullable", [
    "dimensions", "filters", "named_filters", "order_by", "unresolved_concepts",
    "having", "transforms", "partition_by", "missing_partition", "filter_operator",
    "missing_filter_operator", "order_direction", "having_operator", "unresolved_metadata",
])
async def test_monitor_http_reads_preserve_parser_supported_null_collections(monkeypatch, nullable):
    from app.modules.agents.semantic.planning import SemanticPlan
    from app.modules.intelligence import engine_router
    from app.modules.intelligence.contracts import fingerprint

    plan = {"metrics": ["revenue"]}
    if nullable == "partition_by":
        plan["top_n_per_group"] = {"n": 3, "metric": "revenue", "partition_by": None}
    elif nullable == "missing_partition":
        plan["top_n_per_group"] = {"n": 3, "metric": "revenue"}
    elif nullable in {"filter_operator", "missing_filter_operator"}:
        plan["filters"] = [{"field": "region", "value": "Enterprise"}]
        if nullable == "filter_operator":
            plan["filters"][0]["operator"] = None
    elif nullable == "order_direction":
        plan["order_by"] = [{"field": "revenue", "direction": None}]
    elif nullable == "having_operator":
        plan["having"] = [{"metric": "revenue", "operator": None, "value": 0}]
    elif nullable == "unresolved_metadata":
        plan["unresolved_concepts"] = [{"text": "optional", "type_hint": None, "material": None}]
    else:
        plan[nullable] = None
    SemanticPlan.from_dict(plan)
    monitor = Monitor(
        id="monitor", scope=SCOPE, name="Revenue", agent_id="finance", semantic=SEMANTIC,
        plan=plan, value_column="revenue", time_dimension="ordered_at",
    )
    read = AsyncMock(return_value=monitor)
    page = AsyncMock(return_value={"items": [monitor], "next_after": None})
    monkeypatch.setattr(engine_router.intelligence_service, "get", read)
    monkeypatch.setattr(engine_router.intelligence_service, "page", page)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: USER
    app.include_router(engine_router.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        detail = await client.get("/monitors/monitor")
        listing = await client.get("/monitors")
    assert detail.status_code == listing.status_code == 200
    for public in (detail.json(), listing.json()["items"][0]):
        assert public["plan"] == plan
        assert fingerprint(public["plan"]) == fingerprint(monitor.plan)
        assert_private_fields_absent(public)
        assert sanitize_workflow_payload(public) == public
    assert read.await_args.args[-1] == USER
    assert page.await_args.args[-1] == USER


@pytest.mark.parametrize(
    "kind",
    [
        "decisions",
        "outcomes",
        "events",
        "action_events",
        "observations",
        "comparisons",
        "nodes",
        "edges",
    ],
)
def test_related_canonical_records_have_safe_nested_public_projections(kind, evidence):
    from app.modules.intelligence.engine import MODELS

    values = {
        "decisions": {
            "title": "Revenue recommendation",
            "agent_id": "finance",
            "investigation_id": "investigation",
            "semantic": SEMANTIC,
            "target_metric": "revenue",
            "baseline": 100,
            "currency": "USD",
            "outcome_window": WINDOW,
            "request_digest": "r" * 64,
            "last_operation_digest": "o" * 64,
            "options": [
                {
                    "id": "monitor",
                    "description": "Review revenue",
                    "action_type": "monitor",
                    "assumptions": {"cost": 10, "request_digest": "hidden"},
                    "prediction": 100,
                    "cost": 10,
                    "risk": "low",
                    "feasible": True,
                    "method": "scenario",
                    "evidence_ids": ["evidence"],
                    "effects": {"gross_profit": 50, "worker_id": "hidden"},
                }
            ],
            "policy": {
                "decision": "ALLOW",
                "reason": "Reviewed",
                "policy_id": "policy",
                "policy_revision": 3,
                "context_digest": "c" * 64,
            },
            "evidence": [evidence],
        },
        "outcomes": {
            "decision_id": "decision",
            "decision_revision": 3,
            "semantic": SEMANTIC,
            "window": WINDOW,
            "predicted": 100,
            "actual": 90,
            "completeness": 1,
            "attribution": "observed_after",
            "status": "complete",
            "dimensions": {"error": -10, "request_digest": "hidden"},
            "evidence": [evidence],
        },
        "events": {
            "decision_id": "decision",
            "decision_revision": 3,
            "event": "approved",
            "actor": "reviewer",
            "context_digest": "c" * 64,
        },
        "action_events": {
            "decision_id": "decision",
            "action_id": "action",
            "action_revision": 3,
            "event": "verified",
            "actor": "reviewer",
            "context_digest": "c" * 64,
            "dispatch_fence": "hidden",
        },
        "observations": {
            "monitor_id": "monitor",
            "semantic": SEMANTIC,
            "window": WINDOW,
            "value": 100,
            "evidence": [evidence],
        },
        "comparisons": {
            "request_digest": "c" * 64,
            "semantic": SEMANTIC,
            "monitor_id": "monitor",
            "current_window": WINDOW,
            "baseline_window": WINDOW,
            "automatic_mission_id": "mission",
            "automatic_operation_id": "hidden",
        },
        "nodes": {
            "kind": "metric",
            "name": "Revenue",
            "reference_id": "revenue",
            "semantic": SEMANTIC,
            "authority_basis": {"published": True, "worker_id": "hidden"},
            "evidence": [evidence],
        },
        "edges": {
            "source": "revenue",
            "target": "sales",
            "relationship": "defined_by",
            "evidence": [evidence],
        },
    }
    source = MODELS[kind](
        id="record", revision=3, scope=SCOPE, created_at=NOW, updated_at=NOW, **values[kind]
    )
    public = public_canonical_record(kind, source)
    assert_private_fields_absent(public)
    assert public["id"] == "record" and public["revision"] == 3
    if kind == "decisions":
        assert public["options"][0]["assumptions"] == {"cost": 10}
        assert public["options"][0]["effects"] == {"gross_profit": 50}
        assert public["currency"] == "USD" and public["policy"]["decision"] == "ALLOW"
    elif kind == "outcomes":
        assert public["dimensions"] == {"error": -10} and public["actual"] == 90
    elif kind == "nodes":
        assert public["authority_basis"] == {"published": True}
    elif kind == "comparisons":
        assert "automatic_operation_id" not in public and "automatic_mission_id" not in public
    assert sanitize_workflow_payload(public) == public


@pytest.fixture
def mission_api(monkeypatch, mission, investigation, evidence):
    monitor = Monitor(
        id="monitor",
        scope=SCOPE,
        name="Revenue",
        agent_id="finance",
        semantic=SEMANTIC,
        plan={"metrics": ["revenue"]},
        value_column="revenue",
        time_dimension="ordered_at",
    )
    news = NewsItem(
        id="news",
        scope=SCOPE,
        monitor_id=monitor.id,
        title="Revenue decline",
        summary="Down 60",
        semantic=SEMANTIC,
        window=WINDOW,
        before=100,
        after=40,
        change=-60,
        severity="warning",
        dedup_key="private-dedup",
        confidence=Confidence(dimension="detection", method="delta"),
        evidence=[evidence],
    )
    methods = {
        name: AsyncMock(return_value=mission)
        for name in (
            "get",
            "create",
            "resume",
            "attach_run",
            "link",
            "cancel",
        )
    }
    methods["list"] = AsyncMock(return_value=[mission])
    methods["resumable"] = AsyncMock(
        return_value=[
            ResumableMission(
                mission_id="mission",
                thread_id="thread",
                objective=mission.objective,
                status=mission.status,
                revision=4,
                resume_required=True,
            )
        ]
    )
    methods["canonical_read"] = AsyncMock(return_value=investigation.model_dump(mode="json"))
    methods["investigation_context"] = AsyncMock(
        return_value={
            "investigation": investigation.model_dump(mode="json"),
            "news": news.model_dump(mode="json"),
            "monitor": monitor.model_dump(mode="json"),
        }
    )
    service = SimpleNamespace(**methods)
    monkeypatch.setattr(mission_router, "mission_service", service)
    app = FastAPI()
    app.include_router(mission_router.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return app, service


@pytest.mark.parametrize(
    ("method", "path", "body", "operation"),
    [
        ("GET", "/studio/missions/mission", None, "get"),
        (
            "POST",
            "/studio/threads/thread/missions",
            {"objective": "Inspect revenue", "operation_id": "create"},
            "create",
        ),
        (
            "POST",
            "/studio/missions/mission/resume",
            {"operation_id": "resume", "expected_revision": 4},
            "resume",
        ),
        ("POST", "/studio/missions/mission/runs", {"run_id": "run"}, "attach_run"),
        (
            "POST",
            "/studio/missions/mission/objects",
            {
                "expected_revision": 4,
                "object_ref": {"kind": "investigation", "id": "investigation", "revision": 3},
            },
            "link",
        ),
        ("POST", "/studio/missions/mission/cancel", {"expected_revision": 4}, "cancel"),
    ],
)
async def test_every_mission_response_uses_the_same_public_contract(
    mission_api, method, path, body, operation
):
    app, service = mission_api
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, json=body)
    assert response.status_code == 200, response.text
    assert_private_fields_absent(response.json())
    assert response.json()["mission_id"] == "mission"
    assert getattr(service, operation).await_args.args[-1] == USER


async def test_lists_resumable_summaries_and_exact_canonical_context_are_public(mission_api):
    app, service = mission_api
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/studio/threads/thread/missions")
        resumable = await client.get("/studio/threads/thread/missions/resumable")
        canonical = await client.get(
            "/studio/missions/mission/objects/investigation/investigation?revision=3"
        )
        context = await client.get(
            "/studio/missions/mission/objects/investigation/investigation/context?revision=3"
        )
    for response in (listed, resumable, canonical, context):
        assert response.status_code == 200, response.text
        assert_private_fields_absent(response.json())
    summary = resumable.json()["missions"][0]
    assert set(summary) == {
        "mission_id",
        "thread_id",
        "objective",
        "status",
        "revision",
        "resume_required",
    }
    assert summary["resume_required"] is True
    assert canonical.json()["hypotheses"][0]["causal_status"] == "arithmetic"
    assert context.json()["monitor"]["plan"]["metrics"] == ["revenue"]
    assert service.canonical_read.await_args.args[1].revision == 3
    assert service.investigation_context.await_args.args == ("mission", "investigation", 3, USER)


async def test_deliverable_routes_publish_reference_proofs_without_internal_source_facts(
    mission_api,
):
    app, service = mission_api
    document = {
        "deliverable_id": "document",
        "mission_id": "mission",
        "mission_revision": 4,
        "kind": "investigation_report",
        "title": "Revenue report",
        "markdown": "Recorded summary",
        "created_at": NOW,
        "evidence_refs": ["evidence"],
        "object_refs": [],
        "sources": [
            {
                "kind": "mission",
                "id": "mission",
                "revision": 4,
                "fingerprint": "f" * 64,
                "facts": {"release_pins": ["internal"]},
            }
        ],
    }
    service.deliver = AsyncMock(return_value=document)
    service.deliverables = AsyncMock(return_value=[document])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/studio/missions/mission/deliverables",
            json={
                "kind": "investigation_report",
                "operation_id": "deliver",
                "expected_revision": 4,
            },
        )
        listed = await client.get("/studio/missions/mission/deliverables")
    assert created.status_code == 200, created.text
    assert listed.status_code == 200, listed.text
    assert listed.json()["deliverables"] == [created.json()]
    assert created.json()["markdown"] == "Recorded summary"
    assert "facts" not in created.json()["sources"][0]
    assert service.deliver.await_args.args[-1] == USER
    assert service.deliver.await_args.args[1].expected_revision == 4


@pytest.mark.parametrize("status", [403, 404, 409, 503])
async def test_projection_does_not_turn_authorization_refusals_into_public_success(
    mission_api, status
):
    app, service = mission_api
    service.get.side_effect = HTTPException(status_code=status, detail="unavailable")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/studio/missions/mission")
    assert response.status_code == status
    assert response.json() == {"detail": "unavailable"}


@pytest.fixture
def action_api(monkeypatch, action):
    service = SimpleNamespace(
        **{
            name: AsyncMock(return_value=action)
            for name in (
                "preview",
                "get",
                "verify",
                "cancel",
            )
        }
    )
    service.read = AsyncMock(
        return_value=ActionRead.model_validate(action.model_dump() | {"execution_current": False})
    )
    service.review = AsyncMock(
        return_value={
            "id": action.id,
            "revision": action.revision,
            "status": action.status,
            "dispatch_fence": "hidden",
        }
    )
    monkeypatch.setattr(action_router, "action_service", service)
    supervised = AsyncMock(return_value=action)
    monkeypatch.setattr(action_router, "supervised_action", supervised)
    thread = AsyncMock()
    monkeypatch.setattr("app.modules.agents.router._require_agent_thread", thread)
    app = FastAPI()
    app.include_router(action_router.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return app, service, supervised, thread


async def test_action_read_preserves_authoritative_historical_state(action_api):
    app, service, *_ = action_api
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/actions/action?mission_id=mission&revision=2")
    assert response.status_code == 200, response.text
    data = response.json()
    assert_private_fields_absent(data)
    assert data["execution_current"] is False and data["consent_call_id"] is None
    assert service.read.await_args.kwargs == {"mission_id": "mission", "revision": 2}


@pytest.mark.parametrize("operation", ["execute", "verify", "compensate", "cancel"])
async def test_action_mutation_responses_keep_consent_and_verification_ui(action_api, operation):
    app, service, supervised, thread = action_api
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/actions/action/{operation}",
            json={
                "operation_id": "user-operation",
                "expected_revision": 2,
                "thread_id": "thread",
            },
        )
    assert response.status_code == 200, response.text
    data = response.json()
    assert_private_fields_absent(data)
    assert data["execution_current"] and data["consent_call_id"] == "current-consent"
    if operation == "verify":
        assert service.verify.await_args.kwargs == {"expected_revision": 2}
        assert thread.await_args.args == ("thread", "finance", "alice")
    elif operation in {"execute", "compensate"}:
        assert supervised.await_args.args[-1] == USER
        assert supervised.await_args.kwargs == (
            {"compensate": True} if operation == "compensate" else {}
        )


async def test_action_preview_and_review_project_their_results(action_api, action):
    app, service, *_ = action_api
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        preview = await client.post(
            "/actions/preview",
            json={
                "idempotency_key": "preview-operation",
                "decision_id": "decision",
                "expected_decision_revision": 5,
                "option_id": "selected",
                "adapter_id": action.adapter_id,
                "configuration": action.configuration.model_dump(mode="json"),
            },
        )
        review = await client.post(
            "/actions/action/review",
            json={
                "operation_id": "review-operation",
                "expected_revision": 2,
                "operation": "approve",
            },
        )
    assert preview.status_code == 201, preview.text
    assert review.status_code == 200, review.text
    assert_private_fields_absent(preview.json())
    assert review.json() == {"id": "action", "revision": 2, "status": "awaiting_consent"}
    assert service.preview.await_args.args[-1] == USER


def test_openapi_uses_public_schema_names(mission_api, action_api):
    mission_app, _ = mission_api
    action_app, *_ = action_api
    for app, model in ((mission_app, PublicMission), (action_app, PublicAction)):
        schemas = app.openapi()["components"]["schemas"]
        assert model.__name__ in schemas
        assert "Scope" not in schemas and "Record" not in schemas
        assert "ActionApproval" not in schemas and "EvidenceRef" not in schemas
    assert "scope" not in PublicEvidenceRef.model_fields
