"""Optional independent review; the judge has no execution or approval capability."""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.ai_ml.decision_settings import registered_model
from app.modules.ai_ml.service import ai_service
from app.modules.assistant.provider import AssistantProviderClient
from app.modules.query_autopilot.guidance import EXPLANATIONS, explain, next_steps

MODEL_NAME = "deepseek-v4-1-flash"
RUBRIC_VERSION = 2
JUDGE_TIMEOUT_SECONDS = 90


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detection_validity: int = Field(ge=1, le=5)
    evidence_quality: int = Field(ge=1, le=5)
    diagnosis_quality: int = Field(ge=1, le=5)
    recommendation_quality: int = Field(ge=1, le=5)
    safety: int = Field(
        ge=1, le=5, description="Appropriateness of risk and approval classification",
    )
    experiment_validity: int = Field(ge=1, le=5)
    outcome_interpretation: int = Field(ge=1, le=5)
    explanation_quality: int = Field(ge=1, le=5)
    critical_hallucinations: int = Field(ge=0, le=100)
    independent_root_cause: Literal[
        "STATISTICS",
        "RESOURCE_CONTENTION",
        "OPERATOR_SKEW",
        "PLAN_CHANGE",
        "EXCESSIVE_SCAN",
        "CARDINALITY_ESTIMATE",
        "ENGINE_MEMORY_LIMIT",
        "ENGINE_TIMEOUT",
        "UNKNOWN",
    ]
    explanation: str = Field(max_length=2000)

    @property
    def mean_score(self) -> float:
        return (
            sum(
                (
                    self.detection_validity,
                    self.evidence_quality,
                    self.diagnosis_quality,
                    self.recommendation_quality,
                    self.safety,
                    self.experiment_validity,
                    self.outcome_interpretation,
                    self.explanation_quality,
                )
            )
            / 8
        )


def reduced_evidence(value: dict) -> dict:
    """Allow numeric measurements and a closed vocabulary, never arbitrary source text."""
    metrics = {}
    allowed_metrics = {
        "count",
        "p95_ms",
        "baseline_p95_ms",
        "current_p95_ms",
        "cpu_ms",
        "q_error",
        "growth_ratio",
        "scan_ratio",
        "operator_skew",
        "queue_ms",
        "execution_ms",
        "compatible_aggregates",
        "max_ms",
        "total_ms",
        "p50_ms",
        "p99_ms",
        "scanned_rows",
        "output_rows",
        "estimated_rows",
        "actual_rows",
        "statistics_stale",
        "statistics_missing",
        "table_growth_ratio",
        "max_operator_ms",
        "median_operator_ms",
        "operator_instance_count",
        "saturated",
        "plan_changed",
        "previous_regression",
        "mv_eligible",
        "grouping_key_count",
        "aggregate_expression_count",
        "replay_eligible",
        "memory_limit_exceeded",
        "query_timed_out",
        "rewrite_observed",
        "freshness_proven",
        "ranger_acceptance_proven",
        "bound_pending_samples",
        "counter_pair_sample_count",
    }
    for finding in [{"measured": value.get("facts", {})}, *value.get("findings", [])]:
        for name, number in finding.get("measured", {}).items():
            if (
                name in allowed_metrics
                and type(number) in {int, float, bool}
                and math.isfinite(number)
            ):
                metrics[name] = number
    from app.modules.query_autopilot.detection import Detector

    detected_findings = []
    for finding in value.get("findings", []):
        detector = finding.get("detector")
        if not isinstance(detector, str) or detector not in set(Detector):
            continue
        retained: dict[str, Any] = {"detector": detector}
        severity = finding.get("severity")
        if isinstance(severity, str) and severity in {"info", "warning", "critical"}:
            retained["severity"] = severity
        measured = {
            key: number for key, number in finding.get("measured", {}).items()
            if key in allowed_metrics and type(number) in {int, float, bool}
            and math.isfinite(number)
        }
        if measured:
            retained["measured"] = measured
        detected_findings.append(retained)
    categories = {
        "STATISTICS",
        "RESOURCE_CONTENTION",
        "OPERATOR_SKEW",
        "PLAN_CHANGE",
        "EXCESSIVE_SCAN",
        "CARDINALITY_ESTIMATE",
        "ENGINE_MEMORY_LIMIT",
        "ENGINE_TIMEOUT",
        "UNKNOWN",
    }
    hypotheses = [
        d["category"] for d in value.get("diagnosis", []) if d.get("category") in categories
    ]
    evidence_ids = {
        identifier
        for item in [value, *value.get("findings", []), *value.get("diagnosis", [])]
        for identifier in item.get("evidence_ids", [])
        if isinstance(identifier, str)
    }
    baseline = {
        key: number
        for key, number in value.get("baseline", {}).items()
        if key
        in {
            "eligible",
            "historical_count",
            "current_count",
            "windows",
            "historical_p95",
            "current_p95",
            "upper_envelope",
            "seasonal",
        }
        and type(number) in {int, float, bool}
        and math.isfinite(number)
    }
    decisions = []
    for item in value.get("diagnosis", []):
        category = item.get("category")
        confidence = item.get("confidence")
        if category in EXPLANATIONS and type(confidence) in {int, float} and 0 <= confidence <= 1:
            decisions.append(
                {
                    "category": category,
                    "confidence": confidence,
                    "explanation": explain(category, metrics, baseline),
                    "counterevidence_count": len(item.get("counterevidence", [])),
                }
            )
    steps = next_steps({item["detector"] for item in detected_findings})
    from app.modules.query_autopilot.models import ActionKind, Mode, Risk, State

    def numeric(record, keys):
        return {
            key: number for key, number in record.items()
            if key in keys and type(number) in {int, float, bool} and math.isfinite(number)
        }

    def labeled(record, key, allowed):
        item = record.get(key)
        return item if isinstance(item, str) and item in allowed else None

    action = value.get("action")
    action = action if isinstance(action, dict) else {}
    trial = value.get("experiment")
    trial = trial if isinstance(trial, dict) else {}
    outcome = value.get("outcome")
    outcome = outcome if isinstance(outcome, dict) else {}
    experiment = {
        "status": labeled(trial, "status", set(State)),
        "correctness": labeled(trial, "correctness", {"EQUIVALENT", "DIFFERENT", "INCONCLUSIVE"}),
        **numeric(trial, {"repetitions", "improvement", "snapshot_verified"}),
    }
    reason = labeled(trial, "reason", {
        "authorization_unavailable", "authorization_changed", "snapshot_state_unavailable",
        "snapshot_changed", "invalid_measured_latency", "non_deterministic_before_results",
        "control_result_changed", "result_changed", "experiment_time_budget",
        "materialized_view_rewrite_or_freshness_unproven", "control_workload_regressed",
        "target_regressed", "benefit_not_repeatable_or_below_threshold",
        "ranger_acceptance_unavailable", "ranger_acceptance_expired",
        "ranger_acceptance_unauthorized", "ranger_acceptance_unsupported",
        "ranger_acceptance_provenance_unverified", "ranger_acceptance_binding_changed",
        "ranger_filter_or_mask_acceptance_unproven",
        "ranger_snapshot_security_effects_differ", "ranger_governed_rewrite_unproven",
        "ranger_security_effects_comparison_unsupported",
        "ranger_complete_result_or_correlation_unavailable", "ranger_governed_result_changed",
    })
    if reason is not None:
        experiment["reason"] = reason
    for phase in ("before", "after"):
        measurements = trial.get(phase)
        if isinstance(measurements, dict):
            experiment[phase] = numeric(measurements, {"count", "mean_ms", "stddev_ms", "p95_ms"})
            resource = measurements.get("resource", measurements.get("resource_samples"))
            if isinstance(resource, list):
                experiment[phase]["resource_samples"] = [
                    numeric(item, {
                        "cpu_ms", "queue_ms", "execution_ms", "peak_memory_bytes",
                        "spill_bytes", "scanned_rows", "output_rows",
                    }) for item in resource[:30] if isinstance(item, dict)
                ]
                experiment[phase]["resource_sampling"] = {
                    "sample_count": len(experiment[phase]["resource_samples"]),
                    "population_count": experiment[phase].get("count"),
                    "latency_basis": "selective_engine_profiles",
                    "summary_latency_basis": "nova_elapsed_wall_clock",
                }
    before, after = experiment.get("before", {}), experiment.get("after", {})
    uncertainty_fields = ("count", "mean_ms", "stddev_ms")
    if all(
        type(phase.get(key)) in {int, float} and phase[key] >= 0
        for phase in (before, after) for key in uncertainty_fields
    ) and before["count"] > 0 and after["count"] > 0:
        margin = 1.96 * math.sqrt(
            before["stddev_ms"] ** 2 / before["count"]
            + after["stddev_ms"] ** 2 / after["count"]
        )
        difference = before["mean_ms"] - after["mean_ms"]
        experiment["gain_evidence"] = {
            "mean_difference_ms": difference,
            "mean_difference_margin_ms": margin,
            "repeatable_gain": difference > margin,
        }
    controls = trial.get("controls")
    if isinstance(controls, dict):
        experiment["controls"] = {
            phase: numeric(measurements, {"count", "mean_ms", "stddev_ms", "p95_ms"})
            for phase, measurements in controls.items()
            if phase in {"before", "after"} and isinstance(measurements, dict)
        }
    comparison = trial.get("result_comparison")
    if isinstance(comparison, dict):
        experiment["result_comparison"] = numeric(comparison, {
            "checked_target_samples", "checked_control_samples", "target_equivalent",
            "control_equivalent", "snapshot_verified", "digest_match",
        })
        before_proof, after_proof = comparison.get("before_proof"), comparison.get("after_proof")
        before_digest = before_proof.get("digest") if isinstance(before_proof, dict) else None
        after_digest = after_proof.get("digest") if isinstance(after_proof, dict) else None
        if all(
            isinstance(item, str) and len(item) == 64 and all(c in "0123456789abcdef" for c in item)
            for item in (before_digest, after_digest)
        ):
            experiment["result_comparison"]["digest_match"] = before_digest == after_digest
        for phase in ("before_proof", "after_proof"):
            proof = comparison.get(phase)
            if isinstance(proof, dict):
                experiment["result_comparison"][phase] = numeric(
                    proof, {"row_count", "bytes_compared", "ordered", "column_count"},
                )
                if isinstance(proof.get("types"), (list, tuple)):
                    experiment["result_comparison"][phase]["column_count"] = len(proof["types"])
    operators = value.get("operators", {})
    operator_evidence = []
    if isinstance(operators, dict) and operators.get("query_id_verified") is True:
        for operator in operators.get("operators", [])[:100]:
            if not isinstance(operator, dict):
                continue
            kind = labeled(operator, "operator", {
                "AGGREGATION", "PROJECT", "OLAP_SCAN", "HASH_JOIN", "NEST_LOOP_JOIN",
                "EXCHANGE", "SORT", "TOP_N", "ANALYTIC", "SELECT", "UNION",
            })
            if kind is not None:
                operator_evidence.append({
                    "operator": kind,
                    **numeric(operator, {"node_id", "estimated_rows", "actual_rows"}),
                })
    plans = value.get("plans", {})
    plan_evidence = {}
    plan_kinds = {
        "OLAPSCANNODE": "OLAP_SCAN", "OLAP_SCAN": "OLAP_SCAN",
        "PROJECT": "PROJECT", "EXCHANGE": "EXCHANGE",
        "AGGREGATE": "AGGREGATION", "AGGREGATION": "AGGREGATION",
        "HASH_JOIN": "HASH_JOIN", "NEST_LOOP_JOIN": "NEST_LOOP_JOIN",
        "SORT": "SORT", "TOP_N": "TOP_N", "ANALYTIC": "ANALYTIC",
        "SELECT": "SELECT", "UNION": "UNION",
    }
    if isinstance(plans, dict):
        for phase in ("before", "after"):
            plan = plans.get(phase)
            if not isinstance(plan, dict) or not isinstance(plan.get("operators"), list):
                continue
            retained_operators: list[dict[str, Any]] = []
            for operator in plan["operators"][:100]:
                if not isinstance(operator, dict) or not isinstance(operator.get("operator"), str):
                    continue
                kind = plan_kinds.get(operator["operator"].upper().replace(" ", "_"))
                if kind is None:
                    continue
                estimates = operator.get("estimates")
                retained_operators.append({
                    "operator": kind,
                    "estimates": numeric(
                        estimates if isinstance(estimates, dict) else {},
                        {"cardinality", "avgrowsize", "cpu", "memory", "cost"},
                    ),
                })
            plan_evidence[phase] = {"operators": retained_operators}
    outcome_projection = {
        "state": labeled(outcome, "state", set(State)),
        **numeric(outcome, {"measured_gain"}),
        "correctness": labeled(
            outcome, "correctness", {"sandbox_equivalence_only", "EQUIVALENT", "DIFFERENT"},
        ),
        "reason": labeled(outcome, "reason", {
            "insufficient_control_workload_samples", "untargeted_workload_regressed",
            "insufficient_post_application_samples", "measured_post_application_regression",
            "measured_gain_below_threshold", "production_mv_metadata_identity_unavailable",
            "production_mv_rewrite_freshness_or_binding_unproven",
            "production_plan_or_resource_evidence_unavailable",
            "post_application_gain_not_repeatable",
        }),
        "rollback": labeled(outcome, "rollback", {
            "not_needed", "pending", "not_reversible", "verified_object_removed",
            "authorization_unavailable", "owned_object_binding_changed",
        }),
        "causality": labeled(outcome, "causality", {"observational_post_application_window"}),
    }
    gain = outcome.get("gain_evidence")
    if isinstance(gain, dict):
        outcome_projection["gain_evidence"] = numeric(gain, {
            "before_mean_ms", "after_mean_ms", "mean_difference_margin_ms", "repeatable_gain",
        })
    for phase in ("before", "after"):
        values = outcome.get(phase)
        if isinstance(values, dict):
            outcome_projection[phase] = numeric(values, {"count", "total", "maximum"})
    outcome_controls = outcome.get("controls")
    if isinstance(outcome_controls, dict):
        outcome_controls = list(outcome_controls.values())
    if isinstance(outcome_controls, list):
        outcome_projection["controls"] = [
            {phase: numeric(measurements, {"count", "total", "maximum"})
             for phase, measurements in control.items()
             if phase in {"before", "after"} and isinstance(measurements, dict)}
            for control in outcome_controls[:20] if isinstance(control, dict)
        ]
    action_kind = labeled(action, "kind", set(ActionKind))
    approval = value.get("approval", {})
    approval = approval if isinstance(approval, dict) else {}
    approval_projection = numeric(approval, {
        "candidate_binding_verified", "administrative_role_verified",
        "durable_application_verified", "statement_count",
    })
    evaluation_scope = labeled(value, "evaluation_scope", {
        "isolated_fixture", "enrolled_snapshot", "production",
    })
    verification = outcome.get("verification", {})
    verification = verification if isinstance(verification, dict) else {}
    outcome_projection["verification"] = numeric(verification, {
        "object_binding_verified", "rewrite_observed", "freshness_proven",
        "complete_window_verified", "profile_evidence_available", "plan_evidence_available",
    })
    raw_profiles = value.get("profile_samples", [])
    raw_profiles = raw_profiles if isinstance(raw_profiles, list) else []
    profile_samples = [
        numeric(sample, {
            "cpu_ms", "queue_ms", "execution_ms", "scanned_rows", "output_rows",
            "peak_memory_bytes", "spill_bytes", "pending_observed", "limit_occupied",
            "query_id_verified",
        })
        for sample in raw_profiles[:100]
        if isinstance(sample, dict) and sample.get("query_id_verified") is True
        and all(type(sample.get(key)) in {int, float} and math.isfinite(sample[key])
                and sample[key] >= 0 for key in ("queue_ms", "execution_ms"))
    ]
    stage_availability = {
        "action": "AVAILABLE" if action_kind is not None else "NOT_ASSESSED",
        "experiment": "AVAILABLE" if experiment["status"] is not None else "NOT_ASSESSED",
        "outcome": "AVAILABLE" if outcome_projection["state"] is not None else "NOT_ASSESSED",
    }
    interpretation = []
    if experiment.get("gain_evidence"):
        gain = experiment["gain_evidence"]
        interpretation.append(
            f"The observed trial mean difference is {gain['mean_difference_ms']:g} ms; "
            f"its conservative 95% margin is {gain['mean_difference_margin_ms']:g} ms. "
            "A positive raw percentage alone does not establish repeatable benefit."
        )
    if outcome_projection["state"] == "REGRESSED":
        steps = [
            "Stop rollout; retain the target and control measurements and inspect their "
            "matched profiles. Check the recorded compensation before proposing another trial."
        ]
        interpretation.append(
            "The post-application outcome overrides any sandbox improvement. "
            "The observation establishes a verification failure, not proof of its cause."
        )
    elif outcome_projection["state"] == "SUCCESS":
        steps = [
            "Continue scoped target/control monitoring. The measured verification gain "
            "does not prove that the applied change caused the entire improvement."
        ]
        interpretation.append(
            "SUCCESS describes the measured verification window in the stated evaluation scope. "
            "Result correctness remains limited to the recorded equivalence proof."
        )
    elif experiment["status"] in {"NO_IMPROVEMENT", "REGRESSED", "INCONCLUSIVE", "FAILED"}:
        steps = [
            "Withhold application. Address the recorded trial failure or missing evidence; "
            "any revised candidate requires a new bounded parameter-matched trial with controls."
        ]
    if metrics.get("ranger_acceptance_proven") is True:
        interpretation.append(
            "The measured review verified complete correlated patched-FE row-filter and "
            "masking acceptance against the candidate's exact cohort and evidence binding."
        )
    return {
        "measurements": metrics,
        "detected_findings": detected_findings,
        "operator_evidence": operator_evidence,
        "execution_profile_samples": profile_samples,
        "plan_evidence": plan_evidence,
        "proposed_hypotheses": hypotheses,
        "evidence_count": len(evidence_ids),
        "baseline": baseline,
        "diagnostic_assessment": decisions,
        "proposed_next_steps": steps,
        "measurement_provenance": "deterministic_fixture"
        if value.get("evidence_kind") == "deterministic_fixture"
        else "provided_scoped_measurements",
        "execution_authority": "none",
        "execution_authority_subject": "reviewer",
        "evaluation_scope": evaluation_scope,
        "material_changes_require_experiment_and_policy": True,
        "stage_availability": stage_availability,
        "evidence_availability": {
            "historical_baseline": "AVAILABLE" if baseline.get("eligible") is True
            else "INSUFFICIENT_HISTORY" if baseline else "UNAVAILABLE",
            "operator_profile": "AVAILABLE" if operator_evidence else "UNAVAILABLE",
            "execution_profile": "AVAILABLE" if profile_samples or any(
                experiment.get(phase, {}).get("resource_samples") for phase in ("before", "after")
            ) else "UNAVAILABLE",
            "plan_comparison": "AVAILABLE" if all(
                plan_evidence.get(phase, {}).get("operators") for phase in ("before", "after")
            ) else "UNAVAILABLE",
        },
        "action": {
            "kind": action_kind,
            "risk": labeled(action, "risk", set(Risk)),
            "mode": labeled(action, "mode", set(Mode)),
            "state": labeled(action, "state", set(State)),
        },
        "approval": approval_projection,
        "interpretation": interpretation,
        "experiment": experiment,
        "outcome": outcome_projection,
        "rubric_version": RUBRIC_VERSION,
    }


async def resolve_judge(client: AssistantProviderClient):
    for provider in await ai_service.list_providers():
        if provider.get("name") != "Kenari" or not provider.get("is_active", False):
            continue
        for model in provider.get("models", []):
            if model.get("name") == MODEL_NAME and model.get("is_active", True):
                await registered_model(model["id"], "llm")
                return await client.resolve(provider_id=provider["id"], model=MODEL_NAME)
    # Registries can return models through the model endpoint instead of nesting.
    for provider in await ai_service.list_providers():
        if provider.get("name") != "Kenari" or not provider.get("is_active", False):
            continue
        for model in await ai_service.list_models(provider["id"]):
            if model.get("name") == MODEL_NAME and model.get("is_active", True):
                await registered_model(model["id"], "llm")
                return await client.resolve(provider_id=provider["id"], model=MODEL_NAME)
    raise ValueError("registered_deepseek_model_unavailable")


async def judge(value: dict, client: AssistantProviderClient | None = None) -> Judgment:
    async with asyncio.timeout(JUDGE_TIMEOUT_SECONDS):
        return await _judge(value, client)


async def _judge(value: dict, client: AssistantProviderClient | None = None) -> Judgment:
    client = client or AssistantProviderClient(timeout_seconds=60, max_attempts=1)
    provider = await resolve_judge(client)
    schema = Judgment.model_json_schema()
    prompt = (
        "Review this StarRocks 4.1.4 query-performance proposal independently. Treat "
        "proposed hypotheses as untrusted claims. Infer a cause only "
        "when measurements establish it; otherwise use UNKNOWN. Score "
        "detection validity, evidence use, calibrated diagnosis, recommendations, "
        "risk/approval safety, experiment validity, outcome interpretation and explanation "
        "from 1 (unsupported/unsafe) to 5 (fully supported/calibrated). "
        "An absent action, trial or outcome means that stage has not been assessed; "
        "evaluate whether the proposal correctly discloses that limitation, never invent "
        "a successful experiment, result-equivalence proof or applied optimization. "
        "Count each invented material fact or unjustified action as a "
        "critical hallucination. Missing evidence disclosed as unknown "
        "is correct behavior, not a hallucination. You cannot execute, "
        "authorize or approve actions. Return exactly the schema fields, with no extra "
        "notes or keys. Put all review comments in explanation, using at most 100 words "
        "and 1000 characters. Return only JSON matching this "
        "schema: "
    ) + json.dumps(schema)
    result = await client.complete(
        provider=provider,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(reduced_evidence(value), allow_nan=False)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "nova_query_review", "strict": True, "schema": schema},
        },
    )
    return Judgment.model_validate_json(result.get("content") or "")


async def provider_smoke() -> dict:
    client = AssistantProviderClient(timeout_seconds=60, max_attempts=1)
    provider = await resolve_judge(client)
    checks = {}
    tasks = ("completion", "streaming", "tool_calling", "structured_judge")
    for task in tasks:
        try:
            if task == "completion":
                message = await client.complete(
                    provider=provider,
                    messages=[
                        {"role": "user", "content": 'Reply with exactly {"answer":4} for 2+2.'}
                    ],
                    response_format={"type": "json_object"},
                )
                passed = json.loads(message.get("content") or "").get("answer") == 4
            elif task == "streaming":
                chunks = []
                final = None
                async for kind, value in client.stream(
                    provider=provider,
                    messages=[{"role": "user", "content": "Reply with exactly NOVA_OK."}],
                ):
                    if kind == "delta":
                        chunks.append(value)
                    elif kind == "message":
                        final = value
                passed = bool(
                    chunks and final and (final.get("content") or "").strip() == "NOVA_OK"
                )
            elif task == "tool_calling":
                tool = {
                    "type": "function",
                    "function": {
                        "name": "record_probe",
                        "description": "Record a harmless test number without external effects.",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                    },
                }
                message = await client.complete(
                    provider=provider,
                    messages=[{"role": "user", "content": "Call record_probe with value 7."}],
                    tools=[tool],
                    tool_choice={"type": "function", "function": {"name": "record_probe"}},
                )
                calls = message.get("tool_calls") or []
                passed = (
                    len(calls) == 1
                    and calls[0]["function"]["name"] == "record_probe"
                    and json.loads(calls[0]["function"]["arguments"]) == {"value": 7}
                )
            else:
                result = await judge(
                    {"diagnosis": [{"category": "UNKNOWN"}], "findings": [], "evidence_ids": []},
                    client,
                )
                passed = (
                    result.independent_root_cause == "UNKNOWN"
                    and result.critical_hallucinations == 0
                )
            checks[task] = {"status": "PASS" if passed else "FAIL"}
        except Exception as exc:
            checks[task] = {"status": "FAIL", "error_type": type(exc).__name__}
    return {
        "model": provider.model,
        "provider_id": provider.provider_id,
        "checks": checks,
        "passed": all(c["status"] == "PASS" for c in checks.values()),
    }
