"""Quality cases, promotion, persisted monitoring, and concurrent configuration boundaries."""

import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import settings
from app.modules.agents import quality, quality_monitoring, quality_router, router, service
from app.modules.agents.quality_scoring import (
    Assertion,
    PromotionGates,
    gate_results,
    promotion_eligible,
    score_assertion,
    score_case,
)
from app.modules.assistant import service as assistant_service
from app.modules.assistant.tools import ToolInvocation, ToolOutcome, ToolRegistry
from app.modules.intelligence import schedules
from app.modules.intelligence.contracts import fingerprint
from tests.unit.test_agent_release_quality import EXPECTED, USER


@pytest.fixture
def metadata(monkeypatch):
    locks, rows, trace_rows = {}, {}, []

    @asynccontextmanager
    async def lock(key):
        async with locks.setdefault(key, asyncio.Lock()):
            yield SimpleNamespace(renew=AsyncMock(return_value=True))

    async def execute(sql, params):
        kind = next((kind for kind, table in quality.QUALITY_TABLES.items() if table in sql), None)
        if "CONFIG_ASSISTANT_MESSAGES" in sql:
            return {"rows": deepcopy(trace_rows)}
        if sql.startswith("INSERT"):
            record = json.loads(params[-1])
            rows.setdefault((kind, record["id"]), []).append(record)
            return {"rows": []}
        if sql.startswith("SELECT payload,revision"):
            candidates = rows.get((kind, params[0]), [])
            if len(params) == 4:
                candidates = [
                    record
                    for record in candidates
                    if record["scope"]
                    == {
                        "principal": params[1],
                        "active_role": params[2],
                        "security_context_version": params[3],
                    }
                ]
            return {
                "rows": [
                    [deepcopy(record), record["revision"]] for record in reversed(candidates[-2:])
                ]
            }
        candidates = [
            versions[-1]
            for (stored_kind, _), versions in rows.items()
            if stored_kind == kind
            and versions[-1]["agent_id"] == params[0]
            and versions[-1]["scope"]
            == {
                "principal": params[1],
                "active_role": params[2],
                "security_context_version": params[3],
            }
            and (len(params) == 4 or versions[-1]["id"] == params[4])
        ]
        return {"rows": [[deepcopy(record), 1] for record in candidates]}

    monkeypatch.setattr(quality, "metadata_lock", lock)
    monkeypatch.setattr(quality_monitoring, "metadata_lock", lock)
    monkeypatch.setattr(quality.db, "execute_system", AsyncMock(side_effect=execute))
    monkeypatch.setattr(schedules, "require_execution_binding", AsyncMock())
    monkeypatch.setattr(
        router,
        "_require_agent",
        AsyncMock(
            return_value={
                "agent_id": "agent",
                "owner_name": "alice",
                "release_manifest_id": "manifest",
            }
        ),
    )
    return SimpleNamespace(rows=rows, traces=trace_rows, execute=quality.db.execute_system)


def case(identifier="case", revision=1, **overrides):
    return {
        "id": identifier,
        "revision": revision,
        "agent_id": "agent",
        "name": "Complete task",
        "prompt": "What is revenue?",
        "mandatory": True,
        "critical": True,
        "production_match": "all_traces",
        "assertions": [
            {"scorer": "task_completeness", "required": True, "expected": {"completed": True}}
        ],
        **overrides,
    }


def result(item, status="passed", **overrides):
    return {"case_id": item["id"], "case_revision": item["revision"], "status": status, **overrides}


@pytest.mark.parametrize(
    "scorer",
    [
        name
        for name in EXPECTED
        if name
        not in {
            "tool_selection",
            "evidence_coverage",
            "latency",
            "efficiency",
        }
    ],
)
def test_missing_expected_fact_is_unavailable(scorer):
    assert (
        score_assertion(
            Assertion(scorer=scorer, expected=EXPECTED[scorer]),
            {
                "facts": {scorer: {"unrelated": True}},
            },
        ).status
        == "unavailable"
    )


@pytest.mark.parametrize("elapsed", [True, -1, float("inf"), float("nan"), "100", None])
def test_latency_requires_valid_measured_duration(elapsed):
    assert (
        score_assertion(
            Assertion(scorer="latency", expected={"max_ms": 100}),
            {
                "duration_ms": elapsed,
            },
        ).status
        == "unavailable"
    )


@pytest.mark.parametrize(
    "counts", [{}, {"tool_calls": True}, {"tool_calls": -1}, {"tool_calls": "1"}, None]
)
def test_counts_cannot_be_guessed_or_coerced(counts):
    assert (
        score_assertion(
            Assertion(scorer="efficiency", expected={"tool_calls": 2}),
            {
                "counts": counts,
            },
        ).status
        == "unavailable"
    )


@pytest.mark.parametrize(
    "scorer,expected",
    [
        ("latency", {"max_ms": -1}),
        ("latency", {"max_ms": True}),
        ("efficiency", {"tool_calls": True}),
        ("efficiency", {"unknown": 1}),
        ("tool_selection", {"required": ["x"], "forbidden": ["x"]}),
        ("evidence_coverage", {"minimum": True}),
    ],
)
def test_invalid_assertions_are_rejected(scorer, expected):
    with pytest.raises(ValidationError):
        Assertion(scorer=scorer, expected=expected)


@pytest.mark.parametrize(
    "evidence,expected,status",
    [
        ([{"id": "e", "complete": True}] * 2, {"minimum": 2}, "fail"),
        ([{"id": "e"}], {"complete": True}, "unavailable"),
        ([{"id": "e", "health": {}}], {"minimum_health": "strong"}, "unavailable"),
        ([{"id": "e", "health": {"label": "limited"}}], {"minimum_health": "strong"}, "fail"),
        ([{"id": "e", "complete": False}], {"complete": True}, "fail"),
        ([{"id": "e", "health": {"label": "moderate"}}], {"minimum_health": "moderate"}, "pass"),
        (["provider narrative"], {"minimum": 1}, "unavailable"),
    ],
)
def test_evidence_coverage_checks_distinct_identity_and_recorded_health(evidence, expected, status):
    assert (
        score_assertion(
            Assertion(scorer="evidence_coverage", expected=expected),
            {
                "evidence": evidence,
            },
        ).status
        == status
    )


def test_numeric_claims_require_recorded_support_and_finite_values():
    assertion = Assertion(scorer="numeric_consistency", expected={"claims": [{"value": 42}]})
    trace = {
        "facts": {
            "numeric_consistency": {
                "claims": [
                    {"value": 42, "supported": True, "evidence_id": "query-1"},
                ]
            }
        }
    }
    assert score_assertion(assertion, trace).status == "pass"
    trace["facts"]["numeric_consistency"]["claims"][0]["supported"] = False
    assert score_assertion(assertion, trace).status == "fail"
    del trace["facts"]["numeric_consistency"]["claims"][0]["evidence_id"]
    assert score_assertion(assertion, trace).status == "unavailable"


def test_performance_is_separate_and_only_gates_when_configured():
    assertions = [
        Assertion(scorer="task_completeness", expected={"completed": True}),
        Assertion(scorer="latency", expected={"max_ms": 1}),
    ]
    status, scores = score_case(
        assertions,
        {
            "facts": {"task_completeness": {"completed": True}},
            "duration_ms": 2,
        },
    )
    item = case()
    results = [result(item, status, scores=[score.model_dump() for score in scores])]
    assert status == "passed"
    assessment = gate_results([item], results, PromotionGates())
    assert assessment["mandatory_critical"]["status"] == "passed"
    assert assessment["performance"]["status"] == "failed"
    assert not assessment["performance"]["required"]
    assert promotion_eligible([item], results)
    assert not promotion_eligible([item], results, PromotionGates(performance="required"))


def test_other_cases_and_scorer_gates_cannot_weaken_critical_gate():
    critical, other = case(), case("optional", mandatory=False, critical=False)
    results = [result(critical), result(other, "failed")]
    assert promotion_eligible([critical, other], results)
    assert not promotion_eligible([critical, other], results, PromotionGates(other_cases="all"))
    assert not promotion_eligible([critical], [result(critical, "failed")], PromotionGates())
    assert not promotion_eligible(
        [critical],
        [result(critical)],
        PromotionGates(
            required_scorers=["policy_compliance"],
        ),
    )
    assert not promotion_eligible([critical], [result(critical)] * 2)


@pytest.mark.asyncio
async def test_records_deep_freeze_run_inputs_and_reject_completed_rewrites(metadata):
    record = {
        "id": "run",
        "agent_id": "agent",
        "cases": [case()],
        "status": "running",
        "manifest_id": "manifest",
        "version_id": "version",
        "manifest_fingerprint": "digest",
        "gates": {},
        "results": [],
    }
    run = await quality.save_record("runs", record, USER)
    record["cases"][0]["assertions"][0]["expected"]["completed"] = False
    assert run["cases"][0]["assertions"][0]["expected"]["completed"] is True
    with pytest.raises(HTTPException, match="frozen"):
        await quality.save_record("runs", {**run, "cases": record["cases"]}, USER, run["revision"])
    completed = await quality.save_record(
        "runs", {**run, "status": "passed"}, USER, run["revision"]
    )
    with pytest.raises(HTTPException, match="immutable"):
        await quality.save_record(
            "runs", {**completed, "status": "failed"}, USER, completed["revision"]
        )


@pytest.mark.asyncio
async def test_record_cas_prevents_scope_and_revision_overwrite(metadata):
    original = await quality.save_record("cases", case(), USER)
    with pytest.raises(HTTPException, match="changed"):
        await quality.save_record("cases", original, USER)
    with pytest.raises(HTTPException) as exc:
        await quality.save_record("cases", original, {**USER, "active_role": "ADMIN"}, 1)
    assert exc.value.status_code == 404
    assert await quality.records("cases", "agent", {**USER, "security_context_version": 3}) == []


@pytest.mark.asyncio
async def test_monitoring_concurrent_cas_loser_cannot_modify_schedule(metadata, monkeypatch):
    schedule = AsyncMock(return_value={"task_id": "task", "enabled": True})
    monkeypatch.setattr(schedules, "configure_schedule", schedule)
    body = quality.MonitoringRequest(enabled=True)
    outcomes = await asyncio.gather(
        quality.configure_monitoring("agent", body, USER),
        quality.configure_monitoring("agent", body, USER),
        return_exceptions=True,
    )
    assert (
        sum(isinstance(item, HTTPException) and item.status_code == 409 for item in outcomes) == 1
    )
    assert sum(isinstance(item, dict) and item["enabled"] for item in outcomes) == 1
    schedule.assert_awaited_once()
    versions = next(iter(metadata.rows.values()))
    assert versions[0]["enabled"] is False and versions[0]["schedule_status"] == "pending"
    assert versions[1]["enabled"] is True and versions[1]["schedule_status"] == "ready"


@pytest.mark.asyncio
async def test_schedule_failure_leaves_durable_pause(metadata, monkeypatch):
    monkeypatch.setattr(
        schedules, "configure_schedule", AsyncMock(side_effect=RuntimeError("offline"))
    )
    with pytest.raises(RuntimeError):
        await quality.configure_monitoring("agent", quality.MonitoringRequest(enabled=True), USER)
    stored = (await quality.records("monitoring", "agent", USER))[0]
    assert stored["enabled"] is False and stored["desired_enabled"] is True
    monkeypatch.setattr(settings, "STUDIO_QUALITY_ENABLED", True)
    assert await quality_monitoring.score_production(stored["id"], USER) == {"status": "disabled"}


@pytest.mark.asyncio
async def test_monitoring_binding_is_required_before_schedule_or_trace_read(metadata, monkeypatch):
    denied = AsyncMock(side_effect=HTTPException(403, "binding required"))
    monkeypatch.setattr(schedules, "require_execution_binding", denied)
    schedule = AsyncMock()
    monkeypatch.setattr(schedules, "configure_schedule", schedule)
    with pytest.raises(HTTPException) as exc:
        await quality.configure_monitoring("agent", quality.MonitoringRequest(enabled=True), USER)
    assert exc.value.status_code == 403
    schedule.assert_not_awaited()
    metadata.execute.assert_not_awaited()
    monkeypatch.setattr(settings, "STUDIO_QUALITY_ENABLED", True)
    with pytest.raises(HTTPException):
        await quality_monitoring.score_production("config", USER)
    metadata.execute.assert_not_awaited()


async def production_setup(metadata, monkeypatch, **config_overrides):
    monkeypatch.setattr(settings, "STUDIO_QUALITY_ENABLED", True)
    config = await quality.save_record(
        "monitoring",
        {
            "id": "config",
            "agent_id": "agent",
            "enabled": True,
            "sample_rate": 1.0,
            "max_traces": 1,
            "cadence_minutes": 60,
            "schedule_status": "ready",
            **config_overrides,
        },
        USER,
    )
    await quality.save_record("cases", case(), USER)
    return config


@pytest.mark.asyncio
async def test_production_scoring_never_runs_tools_and_is_scoped_bounded_and_idempotent(
    metadata, monkeypatch
):
    await production_setup(metadata, monkeypatch)
    observation = {
        "kind": "quality_observation",
        "version_id": "historical-version",
        "manifest_id": "historical-manifest",
        "prompt_digest": fingerprint(case()["prompt"]),
        "duration_ms": 12,
        "counts": {"tool_calls": 1},
        "facts": {
            "task_completeness": {"completed": False},
            "execution": {"finish_reason": "denied"},
        },
    }
    metadata.traces.extend(
        [
            ["message", [{"kind": "tool", "name": "mutate"}, observation]],
            ["outside-bound", [observation]],
        ]
    )
    execute_tool = AsyncMock(side_effect=AssertionError("must never execute"))
    monkeypatch.setattr(quality.ReadOnlyEvaluationTool, "run", execute_tool)
    assert await quality_monitoring.score_production("config", USER) == {
        "status": "complete",
        "scored": 1,
    }
    assert await quality_monitoring.score_production("config", USER) == {
        "status": "complete",
        "scored": 0,
    }
    execute_tool.assert_not_awaited()
    run = (await quality.records("runs", "agent", USER))[0]
    assert run["status"] == "failed" and run["promotion_eligible"] is False
    assert run["manifest_id"] == "historical-manifest"
    assert run["version_id"] == "historical-version"
    assert run["results"][0]["trace"]["facts"]["execution"]["finish_reason"] == "denied"
    trace_read = next(
        call for call in metadata.execute.await_args_list if "SELECT message_id" in call.args[0]
    )
    assert trace_read.args[1] == ["agent", "alice", "ANALYST", 2, 1]
    proposal = (await quality.records("proposals", "agent", USER))[0]
    assert proposal["hypothesis"] is True and proposal["observations"][0]["status"] == "fail"


@pytest.mark.asyncio
async def test_missing_production_observation_does_not_invent_success(metadata, monkeypatch):
    await production_setup(metadata, monkeypatch)
    metadata.traces.append(["missing", [{"kind": "tool", "name": "anything"}]])
    await quality_monitoring.score_production("config", USER)
    run = (await quality.records("runs", "agent", USER))[0]
    assert run["status"] == "unavailable"
    assert run["results"][0]["scores"][0]["status"] == "unavailable"
    assert quality.observed_trace(metadata.traces[0][1]) == {}


@pytest.mark.parametrize("tool_calls,status", [(1, "fail"), (None, "unavailable"), (0, "pass")])
async def test_report_only_production_performance_findings_are_reviewable(
    metadata, monkeypatch, tool_calls, status
):
    await production_setup(metadata, monkeypatch)
    original = (await quality.records("cases", "agent", USER))[0]
    await quality.save_record(
        "cases",
        {
            **original,
            "assertions": [
                *original["assertions"],
                {"scorer": "efficiency", "expected": {"tool_calls": 0}},
            ],
        },
        USER,
        original["revision"],
    )
    metadata.traces.append([
        "performance-message",
        [{"kind": "quality_observation", "prompt_digest": fingerprint(case()["prompt"]),
          "facts": {"task_completeness": {"completed": True}},
          "counts": {"tool_calls": tool_calls} if tool_calls is not None else {}}],
    ])
    execute_tool = AsyncMock(side_effect=AssertionError("persisted scoring cannot dispatch"))
    monkeypatch.setattr(quality.ReadOnlyEvaluationTool, "run", execute_tool)
    assert (await quality_monitoring.score_production("config", USER))["scored"] == 1
    execute_tool.assert_not_awaited()
    run = (await quality.records("runs", "agent", USER))[0]
    assert run["status"] == "passed" and run["promotion_eligible"] is False
    assert run["results"][0]["scores"][-1]["status"] == status
    assert run["gate_results"]["performance"]["required"] is False
    proposals = await quality.records("proposals", "agent", USER)
    if status == "pass":
        assert proposals == []
    else:
        assert len(proposals) == 1 and proposals[0]["status"] == "proposed"
        assert proposals[0]["hypothesis"] is True
        assert proposals[0]["observations"][0]["scorer"] == "efficiency"
        assert proposals[0]["observations"][0]["status"] == status
        assert (await quality_monitoring.score_production("config", USER))["scored"] == 0
        assert len(await quality.records("proposals", "agent", USER)) == 1


@pytest.mark.asyncio
async def test_global_monitoring_opt_out_reads_nothing(metadata, monkeypatch):
    monkeypatch.setattr(settings, "STUDIO_QUALITY_ENABLED", False)
    assert await quality_monitoring.score_production("config", USER) == {"status": "disabled"}
    metadata.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_only_wrapper_denies_dynamic_mutations_even_without_consent():
    tool = SimpleNamespace(
        name="mixed",
        classification="read_only",
        requires_consent=False,
        classification_for=lambda call: "destructive" if call.arguments["write"] else "read_only",
        run=AsyncMock(return_value=ToolOutcome(ok=True, summary="read")),
    )
    registry = ToolRegistry()
    registry.register(tool)
    registry.register(SimpleNamespace(name="mutate", classification="destructive"))
    registry.register(SimpleNamespace(name="unknown"))
    bounded = quality.evaluation_registry(registry)
    assert bounded.names() == ["mixed"]
    mutation = await bounded.get("mixed").run(ToolInvocation("m", "mixed", {"write": True}), USER)
    assert not mutation.ok and mutation.error_class == "EVALUATION_READ_ONLY"
    tool.run.assert_not_awaited()
    read = await bounded.get("mixed").run(ToolInvocation("r", "mixed", {"write": False}), USER)
    assert read.ok
    tool.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_evaluation_freezes_cases_and_measures_only_actual_usage(metadata, monkeypatch):
    original = case()
    manifest = {
        "id": "manifest",
        "version_id": "v",
        "fingerprint": "digest",
        "dependencies": {"configuration": {"model_name": "pinned-model"}},
    }
    registry = ToolRegistry()
    monkeypatch.setattr(quality, "load_runtime_manifest", AsyncMock(return_value=manifest))
    monkeypatch.setattr(
        service.agent_service,
        "build_loop_inputs",
        AsyncMock(return_value=(registry, "prompt", 60, 4096)),
    )
    contexts = []

    class FakeLoop:
        def __init__(self, **kwargs):
            assert kwargs["context_manager"].token_budget == 4096

        async def run(self, *, context, **_kwargs):
            contexts.append(context)
            assert context.model_name == "pinned-model" and context.user is USER
            assert context.quality_evaluation is True
            original["assertions"][0]["expected"]["completed"] = False
            context.quality_facts = {"task_completeness": {"completed": True}}
            context.steps.append({"kind": "provider"})
            context.usage = {"total_tokens": 10}
            yield 'event: done\ndata: {"finish_reason": "stop"}\n\n'

    monkeypatch.setattr(assistant_service, "AssistantLoop", FakeLoop)
    run = await quality.evaluate(
        {"agent_id": "agent", "owner_name": "alice"},
        manifest,
        [original],
        USER,
        PromotionGates(max_latency_ms=100000, count_budgets={"total_tokens": 2000}),
    )
    assert run["cases"][0]["assertions"][0]["expected"]["completed"] is True
    assert run["results"][0]["trace"]["counts"] == {}
    assert run["results"][0]["budget_scores"][1]["status"] == "unavailable"
    assert run["gate_results"]["performance"]["status"] == "unavailable"
    assert len(contexts) == 1 and contexts[0].session_id == "session"


@pytest.mark.parametrize("measured_total,expected", [(300, "fail"), (None, "unavailable")])
async def test_partial_context_usage_cannot_replace_a_measured_or_missing_total(
    metadata, monkeypatch, measured_total, expected,
):
    manifest = {
        "id": "manifest", "version_id": "v", "fingerprint": "digest",
        "dependencies": {"configuration": {}},
    }
    monkeypatch.setattr(quality, "load_runtime_manifest", AsyncMock(return_value=manifest))
    monkeypatch.setattr(
        service.agent_service, "build_loop_inputs",
        AsyncMock(return_value=(ToolRegistry(), "prompt", 60, 4096)),
    )

    class FakeLoop:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *, context, **_kwargs):
            context.quality_facts = {"task_completeness": {"completed": True}}
            context.usage = {"total_tokens": 10}
            counts = {"total_tokens": measured_total} if measured_total is not None else {}
            context.steps.append({
                "kind": "quality_observation", "counts": counts,
                "facts": context.quality_facts,
            })
            yield 'event: done\ndata: {"finish_reason": "stop"}\n\n'

    monkeypatch.setattr(assistant_service, "AssistantLoop", FakeLoop)
    run = await quality.evaluate(
        {"agent_id": "agent", "owner_name": "alice"}, manifest, [case()], USER,
        PromotionGates(performance="required", count_budgets={"total_tokens": 100}),
    )
    expected_counts = {"total_tokens": measured_total} if measured_total is not None else {}
    assert run["results"][0]["trace"]["counts"] == expected_counts
    assert run["results"][0]["budget_scores"][0]["status"] == expected
    assert not run["promotion_eligible"]


@pytest.mark.asyncio
async def test_doctor_records_facts_while_proposals_label_hypotheses(metadata, monkeypatch):
    await production_setup(metadata, monkeypatch)
    metadata.traces.append(
        [
            "failure",
            [
                {
                    "kind": "quality_observation",
                    "facts": {
                        "task_completeness": {"completed": False},
                    },
                }
            ],
        ]
    )
    await quality_monitoring.score_production("config", USER)
    monkeypatch.setattr(
        quality_router, "owned_agent", AsyncMock(return_value={"agent_id": "agent"})
    )
    from app.modules.agents import releases

    monkeypatch.setattr(releases, "load_runtime_manifest", AsyncMock(return_value=None))
    doctor = await quality_router.doctor("agent", USER)
    observed = next(item for item in doctor["diagnoses"] if item.get("scorer"))
    assert observed["observation"] == "fail" and observed["hypothesis"] is False
    proposal = (await quality.records("proposals", "agent", USER))[0]
    assert proposal["hypothesis"] is True


@pytest.mark.asyncio
async def test_report_only_unavailable_optional_case_does_not_block_critical_promotion(metadata):
    critical = await quality.save_record("cases", case(), USER)
    optional = await quality.save_record(
        "cases", case("optional", mandatory=False, critical=False), USER
    )
    run = await quality.save_record(
        "runs",
        {
            "id": "promotion",
            "agent_id": "agent",
            "manifest_id": "manifest",
            "manifest_fingerprint": "digest",
            "scorer_set_version": quality.SCORER_SET_VERSION,
            "cases": [critical, optional],
            "status": "unavailable",
            "source": "evaluation",
            "promotion_eligible": True,
            "results": [result(critical), result(optional, "unavailable")],
            "gates": {},
        },
        USER,
    )
    manifest = {"id": "manifest", "fingerprint": "digest"}
    assert await quality.require_promotion("agent", manifest, run["id"], USER) == run


@pytest.mark.asyncio
async def test_new_mandatory_critical_case_invalidates_promotion(metadata):
    critical = await quality.save_record("cases", case(), USER)
    await quality.save_record(
        "runs",
        {
            "id": "promotion",
            "agent_id": "agent",
            "manifest_id": "manifest",
            "manifest_fingerprint": "digest",
            "scorer_set_version": quality.SCORER_SET_VERSION,
            "cases": [critical],
            "status": "passed",
            "source": "evaluation",
            "promotion_eligible": True,
            "results": [result(critical)],
            "gates": {},
        },
        USER,
    )
    await quality.save_record("cases", case("new-critical"), USER)
    with pytest.raises(HTTPException, match="changed"):
        await quality.require_promotion(
            "agent", {"id": "manifest", "fingerprint": "digest"}, "promotion", USER
        )


@pytest.mark.asyncio
async def test_production_run_cannot_be_used_for_promotion(metadata):
    critical = await quality.save_record("cases", case(), USER)
    await quality.save_record(
        "runs",
        {
            "id": "production",
            "agent_id": "agent",
            "manifest_id": "manifest",
            "manifest_fingerprint": "digest",
            "scorer_set_version": quality.SCORER_SET_VERSION,
            "cases": [critical],
            "status": "passed",
            "source": "production",
            "promotion_eligible": True,
            "results": [result(critical)],
            "gates": {},
        },
        USER,
    )
    with pytest.raises(HTTPException, match="gates must pass"):
        await quality.require_promotion(
            "agent", {"id": "manifest", "fingerprint": "digest"}, "production", USER
        )


@pytest.mark.asyncio
async def test_comparison_does_not_claim_regression_across_case_revisions(monkeypatch):
    left = {
        "id": "left",
        "cases": [case()],
        "scorer_set_version": "1",
        "results": [
            result(
                case(),
                scores=[{"scorer": "task_completeness", "scorer_version": "1", "status": "pass"}],
            ),
        ],
    }
    right = {
        "id": "right",
        "cases": [case(revision=2)],
        "scorer_set_version": "1",
        "results": [
            result(
                case(revision=2),
                "failed",
                scores=[{"scorer": "task_completeness", "scorer_version": "1", "status": "fail"}],
            ),
        ],
    }
    monkeypatch.setattr(quality_router, "get_run", AsyncMock(side_effect=[left, right]))
    comparison = await quality_router.compare("agent", "left", "right", USER)
    assert not comparison["comparable"]
    assert all(not change["regression"] for change in comparison["changes"])


@pytest.mark.asyncio
async def test_doctor_dependency_outage_is_observed_without_invented_drift(monkeypatch):
    from app.modules.agents import releases

    monkeypatch.setattr(
        quality_router, "owned_agent", AsyncMock(return_value={"agent_id": "agent"})
    )
    monkeypatch.setattr(
        releases,
        "load_runtime_manifest",
        AsyncMock(side_effect=HTTPException(503, "credential-bearing private detail")),
    )
    monkeypatch.setattr(quality, "records", AsyncMock(return_value=[]))
    doctor = await quality_router.doctor("agent", USER)
    assert doctor["diagnoses"][0]["category"] == "RUNTIME_UNAVAILABLE"
    assert doctor["diagnoses"][0]["hypothesis"] is False
    assert "credential-bearing" not in str(doctor)


@pytest.mark.asyncio
async def test_feedback_proposals_are_idempotent_under_concurrent_analysis(metadata):
    trace = {"id": "run", "results": []}
    first, second = await asyncio.gather(
        quality.feedback_proposal("agent", "message", USER, trace),
        quality.feedback_proposal("agent", "message", USER, trace),
    )
    assert first == second
    versions = next(iter(metadata.rows.values()))
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_cancelled_evaluation_persists_unavailable_run_before_propagating(
    metadata, monkeypatch
):
    manifest = {
        "id": "manifest",
        "version_id": "v",
        "fingerprint": "digest",
        "dependencies": {"configuration": {}},
    }
    monkeypatch.setattr(
        quality, "load_runtime_manifest", AsyncMock(side_effect=asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        await quality.evaluate(
            {"agent_id": "agent", "owner_name": "alice"}, manifest, [case()], USER
        )
    run = (await quality.records("runs", "agent", USER))[0]
    assert run["status"] == "unavailable" and run["promotion_eligible"] is False
    assert run["error_class"] == "CancelledError"


@pytest.mark.parametrize("enabled", ["true", 1])
def test_monitoring_cannot_coerce_an_opt_in(enabled):
    with pytest.raises(ValidationError):
        quality.MonitoringRequest(enabled=enabled)


@pytest.mark.parametrize("measurement", [None, True, -1, "0"])
def test_frozen_global_budgets_require_actual_token_counts(measurement):
    scored = quality.case_result(
        case(),
        {
            "facts": {"task_completeness": {"completed": True}},
            "counts": {"total_tokens": measurement},
        },
        "trace",
        PromotionGates(performance="required", count_budgets={"total_tokens": 1}),
    )
    assert scored["budget_scores"][0]["status"] == "unavailable"
    assert not promotion_eligible([case()], [scored], PromotionGates(performance="required"))
