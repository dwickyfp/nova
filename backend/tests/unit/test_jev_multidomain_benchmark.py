import hashlib
import json
from collections import Counter

import pytest
import yaml

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticFilter, SemanticPlan
from app.modules.agents.semantic.runtime import validate_semantic_model_ir
from tests.benchmark.jev_multidomain.cases import build_cases, frozen_document
from tests.benchmark.jev_multidomain.catalog import METRICS, definition
from tests.benchmark.jev_multidomain.data import SCHEMAS, generate
from tests.benchmark.jev_multidomain.environment import ARTIFACTS, public_trace
from tests.benchmark.jev_multidomain.execution_score import execution_score, query_matches
from tests.benchmark.jev_multidomain.experiments import experiment_plan
from tests.benchmark.jev_multidomain.metrics import predicted_route, summarize
from tests.benchmark.jev_multidomain.oracle import metric_sql


def test_exception_trace_preserves_code_and_location_without_secret_message():
    from tests.benchmark.jev_multidomain.runner import exception_trace

    try:
        raise RuntimeError(1064, "password=private-benchmark-secret")
    except RuntimeError as error:
        trace = exception_trace(error)

    assert trace["type"] == "RuntimeError"
    assert trace["code"] == 1064
    assert trace["frames"][-1]["function"] == (
        "test_exception_trace_preserves_code_and_location_without_secret_message"
    )
    assert "private-benchmark-secret" not in str(trace)


def test_frozen_questions_are_unchanged_and_paraphrase_groups_do_not_leak():
    frozen = frozen_document()
    assert frozen == json.loads((ARTIFACTS / "ground_truth.json").read_text())
    assert len(frozen["cases"]) == 200
    assert Counter(c["split"] for c in frozen["cases"]) == {"dev": 138, "holdout": 62}
    groups = {}
    for case in frozen["cases"]:
        assert set(case["metrics"]) <= METRICS.keys()
        groups.setdefault(case["group"], set()).add(case["split"])
    assert all(len(splits) == 1 for splits in groups.values())


def test_seeded_dataset_has_unique_keys_and_declared_row_shapes():
    rows = generate()
    assert sum(map(len, rows.values())) == 155604
    digest = hashlib.sha256(json.dumps(rows, default=str, sort_keys=True).encode()).hexdigest()
    assert (
        digest
        == hashlib.sha256(json.dumps(generate(), default=str, sort_keys=True).encode()).hexdigest()
    )
    for table, values in rows.items():
        assert len({r[0] for r in values}) == len(values), table
        assert all(len(r) == len(SCHEMAS[table].split(", ")) for r in values), table
    assert {r[1] for r in rows["revenue"]} <= {r[0] for r in rows["customers"]}


@pytest.mark.parametrize("domain", ["FINANCE", "MARKETING"])
def test_rich_semantic_catalog_compiles_governed_metrics(domain):
    ir = SemanticModelIR.from_ossie(parse_ossie(yaml.safe_dump(definition(domain))).as_dict())
    assert validate_semantic_model_ir(ir).valid
    assert ir.query_generation_instructions
    assert len(ir.metrics) == 18
    for metric in ir.metrics:
        filters = (
            (
                SemanticFilter(metric.default_time_dimension, ">=", "2025-07-01"),
                SemanticFilter(metric.default_time_dimension, "<", "2025-10-01"),
            )
            if metric.default_time_dimension
            else ()
        )
        compiled = SemanticCompiler().compile(
            ir, SemanticPlan(metrics=(metric.name,), filters=filters)
        )
        assert "SELECT" in compiled.sql
        assert metric.base_dataset in compiled.sql
        assert ("2025-07-01" in compiled.sql) == bool(metric.default_time_dimension)


def test_oracle_preserves_snapshot_and_ratio_meanings():
    base = {"period": "2025-Q3", "group_by": None, "region": None}
    assert "2025-07-01" in metric_sql(base, "recognized_revenue")
    assert "WHERE" not in metric_sql(base, "overdue_amount")
    assert "WHERE" not in metric_sql(base, "campaign_budget")
    assert (
        "SUM(ad_performance.attributed_revenue)/NULLIF(SUM(ad_performance.spend),0)"
        in metric_sql(base, "roas")
    )


def test_failed_cases_and_missing_judgments_remain_in_denominator():
    cases = [
        {"id": "a", "expected": "FINANCE", "category": "explicit", "split": "dev"},
        {"id": "b", "expected": "MARKETING", "category": "explicit", "split": "dev"},
    ]
    records = [
        {
            "case_id": "a",
            "selected_agents": ["FINANCE"],
            "status": "completed",
            "latency_seconds": 2,
        },
        {"case_id": "b", "selected_agents": [], "status": "failed", "latency_seconds": 10},
    ]
    report = summarize(cases, records, [{"case_id": "a", "score": 3, "response_kind": "answer"}])
    assert report["routing_accuracy"] == 0.5
    assert report["answer_normalized_accuracy"] == 0.5
    assert report["judge_missing"] == 1
    assert report["confusion_matrix"]["MARKETING"]["ERROR"] == 1


def test_clarification_is_not_inferred_from_ground_truth():
    assert predicted_route({"selected_agents": []}, {"response_kind": "clarification"}) == "CLARIFY"
    assert (
        predicted_route({"selected_agents": ["FINANCE"]}, {"response_kind": "clarification"})
        == "FINANCE"
    )


def test_recovered_decision_timeout_is_visible_without_marking_good_answer_unresolved():
    report = summarize(
        [{"id": "a", "expected": "FINANCE", "category": "explicit", "split": "dev"}],
        [
            {
                "case_id": "a",
                "selected_agents": ["FINANCE"],
                "status": "completed",
                "latency_seconds": 4,
                "jev_calls": [{"error": "TimeoutError", "latency_seconds": 3}],
            }
        ],
        [{"case_id": "a", "score": 3, "response_kind": "answer"}],
    )
    assert report["error_types"]["JEV_decision_failure"] == 1
    assert report["unresolved_cases"] == 0
    assert report["answer_normalized_accuracy"] == 1


def test_ten_multiturn_conversations_have_three_distinct_turns():
    grouped = {}
    for case in build_cases():
        if case.category == "multiturn":
            grouped.setdefault(case.group, []).append(case)
    assert len(grouped) == 10
    assert all([len(c.history) for c in cases] == [0, 1, 2] for cases in grouped.values())


def test_public_trace_removes_auth_values_in_nested_json_without_changing_evidence():
    record = {
        "session_id": "test-session",
        "content": json.dumps({"api_key": "test-key", "rows": [[12]]}),
        "queries": [{"sql": "SELECT 12", "rows": [[12]]}],
    }
    redacted = public_trace(record)
    assert redacted["session_id"] == "[redacted]"
    assert json.loads(redacted["content"])["api_key"] == "[redacted]"
    assert redacted["queries"] == record["queries"]
    assert record["session_id"] == "test-session"


def test_execution_scoring_rejects_wrong_period_totals_and_missing_domain_evidence():
    gold = {
        "metric": "revenue",
        "columns": ["region", "revenue"],
        "rows": [["East", 100], ["West", 200]],
    }
    assert query_matches(
        {"columns": ["revenue", "region"], "rows": [[200, "West"], [100, "East"]]}, gold
    )
    assert not query_matches(
        {"columns": gold["columns"], "rows": [["East", 100], ["West", 300]]}, gold
    )
    assert not query_matches({"columns": gold["columns"], "rows": [["East", 100]]}, gold)
    result = execution_score(
        {"queries": [gold]}, [gold, {"metric": "spend", "columns": ["spend"], "rows": [[50]]}]
    )
    assert not result["correct"]
    assert result["metrics"] == [
        {"metric": "revenue", "correct": True},
        {"metric": "spend", "correct": False},
    ]


def test_paired_experiments_are_frozen_and_exclude_holdout():
    plan = experiment_plan()
    assert plan == json.loads((ARTIFACTS / "experiments.json").read_text())
    cases = {case.id: case for case in build_cases()}
    assert len(plan["paired_case_ids"]) == 12
    assert len(plan["repeat_case_ids"]) == 6
    assert all(cases[cid].split == "dev" for cid in plan["paired_case_ids"])
    assert {cases[cid].category for cid in plan["repeat_case_ids"]} == {
        "ambiguous",
        "cross_domain",
        "adversarial",
    }


@pytest.mark.asyncio
async def test_missing_authentication_is_a_recorded_failure_not_a_dropped_case(monkeypatch):
    from tests.benchmark.jev_multidomain import runner

    async def unavailable():
        raise RuntimeError("No authenticated session")

    monkeypatch.setattr(runner, "authenticated_user", unavailable)
    result = await runner.LiveBenchmark("on").run_case({"id": "auth-error", "question": "Example"})
    assert result["case_id"] == "auth-error"
    assert result["status"] == "harness_error"
    assert result["harness_error"] == "RuntimeError"


def test_probe_stability_is_not_five_way_routing_accuracy():
    from tests.benchmark.jev_multidomain.analysis import probe_summary, stability

    case = {"id": "ambiguous", "expected": "CLARIFY", "category": "ambiguous"}
    record = {
        "case_id": "ambiguous",
        "jev_primary_agents": ["FINANCE"],
        "trace": [{"status": "accepted"}],
        "lexical_ranked": ["FINANCE", "MARKETING"],
        "ranked": ["FINANCE", "MARKETING"],
        "latency_seconds": 1,
    }
    result = probe_summary([case], [record])
    assert result["primary_set_accuracy"] is None
    assert result["clarify_with_primary"] == 1
    assert stability([[record], [record], [record]], "jev_primary_agents")["unanimous_cases"] == 1
    errors = [[{"case_id": "failed", "predicted": "ERROR"}]] * 3
    assert stability(errors, "predicted")["modal_agreement"] == 0
    assert stability(errors, "predicted")["unanimous_cases"] == 0
    assert stability(errors, "predicted")["valid_change_rate"] is None
    first = [
        {"case_id": "stable", "predicted": "FINANCE"},
        {"case_id": "failed", "predicted": "ERROR"},
    ]
    second = [
        {"case_id": "stable", "predicted": "FINANCE"},
        {"case_id": "failed", "predicted": "MARKETING"},
    ]
    measured = stability([first, second], "predicted")
    assert measured["change_rate"] == 0.5
    assert measured["valid_change_rate"] == 0
    assert measured["valid_paired_cases"] == 1


def test_probe_confidence_uses_only_accepted_primary_choices():
    from tests.benchmark.jev_multidomain.analysis import probe_summary

    result = probe_summary(
        [{"id": "ambiguous", "expected": "CLARIFY", "category": "ambiguous"}],
        [
            {
                "case_id": "ambiguous",
                "jev_primary_agents": ["MARKETING"],
                "lexical_ranked": ["FINANCE", "MARKETING"],
                "ranked": ["MARKETING", "FINANCE"],
                "latency_seconds": 1,
                "trace": [
                    {
                        "status": "accepted",
                        "scores": {
                            "c0": {"label": "primary", "confidence": 0.18, "accepted": False},
                            "c1": {"label": "primary", "confidence": 0.99, "accepted": True},
                        },
                    }
                ],
            }
        ],
    )
    assert result["cases"][0]["confidence"] == 0.99
    assert result["clarify_with_confident_primary"] == 1


@pytest.mark.asyncio
async def test_database_outage_stops_batch_without_scoring_unexecuted_cases(monkeypatch, tmp_path):
    from contextlib import nullcontext

    from tests.benchmark.jev_multidomain.runner import LiveBenchmark

    benchmark = LiveBenchmark("on")
    monkeypatch.setattr(benchmark, "instruments", nullcontext)

    async def unavailable(case, thread_id=None):
        return {
            "case_id": case["id"],
            "status": "harness_error",
            "harness_error": "OperationalError",
        }

    monkeypatch.setattr(benchmark, "run_case", unavailable)
    output = tmp_path / "results.jsonl"
    with pytest.raises(ExceptionGroup, match="TaskGroup"):
        await benchmark.run([{"id": "a", "category": "explicit"}], output)
    assert not output.exists()
    assert json.loads(output.with_suffix(".infrastructure.jsonl").read_text())["case_id"] == "a"


def test_live_artifact_reader_waits_for_complete_records_and_rejects_malformed_lines(tmp_path):
    from tests.benchmark.jev_multidomain.environment import read_records

    source = tmp_path / "results.jsonl"
    source.write_text('{"case_id":"a"}\n{"case_id":')
    assert read_records(source) == [{"case_id": "a"}]
    source.write_text('{"case_id":"a"}\ninvalid\n')
    with pytest.raises(ValueError):
        read_records(source)


@pytest.mark.asyncio
async def test_registry_isolation_retains_real_authorization_for_only_fixture_agents(monkeypatch):
    from unittest.mock import AsyncMock

    from app.modules.agents import auto_planner
    from app.modules.agents.capabilities import CapabilityManifest
    from tests.benchmark.jev_multidomain.environment import isolated_agent_registry

    allowed = {"agent_id": "allowed", "owner_name": "alice", "name": "Finance"}
    unrelated = {"agent_id": "unrelated", "owner_name": "alice", "name": "Outside fixture"}
    monkeypatch.setattr(
        auto_planner.agent_repository, "list_agents", AsyncMock(return_value=[allowed, unrelated])
    )
    monkeypatch.setattr(
        auto_planner.agent_repository, "list_shared_agents", AsyncMock(return_value=[unrelated])
    )
    verify = AsyncMock(return_value=True)
    monkeypatch.setattr(auto_planner, "has_verified_access", verify)
    monkeypatch.setattr(
        auto_planner.capability_repository,
        "get",
        AsyncMock(return_value=CapabilityManifest(available_to_auto=True)),
    )
    with isolated_agent_registry({"allowed"}):
        result = await auto_planner.authorized_candidates(
            {"username": "alice", "active_role": "ANALYST", "roles": ["ANALYST"]}
        )
    assert [c.agent_id for c in result] == ["allowed"]
    assert verify.await_count == 1
    assert verify.await_args.args[0] == allowed
