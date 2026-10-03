"""Explicit live judge review of retained engine measurements, without SQL or secrets."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from app.core.database import db
from app.modules.query_autopilot.detection import (
    Facts,
    detect,
    diagnose,
    serialize_findings,
)
from app.modules.query_autopilot.judge import RUBRIC_VERSION, judge, reduced_evidence
from app.modules.query_autopilot.models import Candidate, Policy, Scope, digest, utcnow
from app.modules.query_autopilot.statistics import Distribution, Window, compare_baseline


def review_cases(
    scenarios: dict, contention: dict | None, regression: dict | None,
    experiment: dict | None = None,
    governed_cycles: tuple[dict, ...] = (),
    pipeline: dict | None = None,
) -> list[dict]:
    cases = []

    def retain(name: str, value: dict):
        projection = reduced_evidence(value)
        review = {
            "facts": projection["measurements"], "baseline": projection["baseline"],
            "diagnosis": [
                {**d, "counterevidence": [None] * d["counterevidence_count"]}
                for d in projection["diagnostic_assessment"]
            ],
            "findings": projection["detected_findings"],
            "operators": {"query_id_verified": bool(projection["operator_evidence"]),
                          "operators": projection["operator_evidence"]},
            "profile_samples": projection["execution_profile_samples"],
            "plans": projection["plan_evidence"],
            "evidence_ids": [str(i) for i in range(projection["evidence_count"])],
            "action": projection["action"], "experiment": projection["experiment"],
            "outcome": projection["outcome"],
            "approval": projection["approval"],
            "evaluation_scope": projection["evaluation_scope"],
        }
        cases.append({"case": name, "review": review, "evidence": reduced_evidence(review)})

    for value in scenarios.get("cases", []):
        name = value.get("case")
        if name in {"A", "B"} and value.get("query_ids") and value.get("latency"):
            latency = Distribution.from_dict(value["latency"])
            facts = Facts(
                latency, compare_baseline(Window(utcnow(), latency), []),
                evidence_ids=tuple(value["query_ids"]),
            )
            retain(name, {
                "findings": serialize_findings(detect(facts, Policy())),
                "diagnosis": [asdict(d) for d in diagnose(detect(facts, Policy()), facts)],
                "baseline": asdict(facts.baseline),
                "facts": {
                    "count": latency.count, "total_ms": latency.total,
                    "max_ms": latency.maximum, "p95_ms": latency.quantile(0.95),
                },
                "evidence_ids": value["query_ids"],
            })
        elif name == "F-detection" and value.get("findings"):
            facts = dict(value.get("facts", {}))
            if value.get("latency"):
                latency = Distribution.from_dict(value["latency"])
                facts.update({
                    "count": latency.count, "total_ms": latency.total,
                    "max_ms": latency.maximum, "p95_ms": latency.quantile(0.95),
                })
            retain(name, {
                **value, "facts": facts, "evidence_ids": value.get("query_ids", []),
                "action": {"kind": "MATERIALIZED_VIEW", "risk": "APPROVAL"},
            })
        elif name == "E":
            for phase, measured in value.get("phases", {}).items():
                if phase not in {"before", "after"}:
                    continue
                if measured.get("query_id") and "findings" in measured:
                    operators = measured.get("operators", {})
                    facts = operators.get("facts", {}) if operators.get("query_id_verified") else {}
                    statistics = measured.get("statistics", {}).get("facts", {})
                    retain("E-" + phase, {
                        **measured, "evidence_ids": [measured["query_id"]],
                        "facts": {**statistics, **facts},
                        "action": {"kind": "STATISTICS"},
                        "experiment": value.get("experiment", {}),
                    })
    if contention:
        for phase, measured in contention.get("phases", {}).items():
            if phase not in {"uncontended", "contended"}:
                continue
            observations = measured.get("observations", [])
            if not observations or "findings" not in measured:
                continue
            # Keep a counter pair from one execution. No maxima assembled from
            # unrelated executions can imply admission contention.
            bound = [
                o for o in observations if o.get("pending_observed") and o.get("limit_occupied")
            ]
            selected = max(
                bound or observations, key=lambda o: o.get("facts", {}).get("queue_ms", 0),
            )
            profiles = scoped_profile_samples(observations)
            retain("G-" + phase, {
                **measured, "facts": {
                    **selected.get("facts", {}), "count": measured["count"],
                    "saturated": bool(bound), "bound_pending_samples": len(bound),
                    "counter_pair_sample_count": len(profiles),
                },
                "profile_samples": profiles,
                "evidence_ids": [o["query_id"] for o in observations],
            })
    if regression and regression.get("wall_clock_historical_acceptance") == "PASS":
        observations = (contention or {}).get("phases", {}).get("contended", {}).get(
            "observations", [],
        )
        query_ids = set(regression.get("after_query_ids", []))
        retain("D", {
            **regression, "evidence_ids": regression.get("after_query_ids", []),
            "profile_samples": scoped_profile_samples([
                item for item in observations if item.get("query_id") in query_ids
            ]),
        })
    if experiment and experiment.get("evidence_kind") == "real_native_engine_sandbox":
        trial = experiment.get("experiment", {}).get("result", {})
        if trial.get("before", {}).get("query_ids") and trial.get("after", {}).get("query_ids"):
            retain("F-experiment", {
                "findings": [{"detector": "materialized_view"}],
                "action": {
                    "kind": "MATERIALIZED_VIEW", "risk": "APPROVAL",
                    "state": experiment.get("candidate_state"),
                },
                "experiment": trial,
                "plans": experiment.get("experiment", {}).get("plans", {}),
                "facts": {
                    "rewrite_observed": experiment.get("native_rewrite_observed") is True,
                    "freshness_proven": experiment.get("sandbox_object_ready") is True,
                    "ranger_acceptance_proven": experiment.get("ranger_acceptance") == "PASS",
                },
                "evidence_ids": trial["before"]["query_ids"] + trial["after"]["query_ids"],
            })
    for index, cycle in enumerate(governed_cycles):
        if cycle.get("evidence_kind") != "isolated_governed_complete_cycle":
            continue
        candidate = cycle.get("candidate", {})
        action = cycle.get("action") or {}
        try:
            binding = Candidate.model_validate(candidate).binding
        except ValueError:
            binding = None
        approval_verified = bool(
            binding and candidate.get("approval_digest") == binding
            and action.get("binding") == binding
            and action.get("candidate_id") == candidate.get("id")
            and action.get("actor") == candidate.get("approved_by")
            and action.get("actor_role") == "ACCOUNTADMIN"
            and action.get("state") == "APPLIED"
        )
        for measured in cycle.get("experiments", []):
            trial = measured.get("result", {})
            if (measured.get("candidate_id") != candidate.get("id")
                    or any(trial.get(phase, {}).get("count", 0) < 30
                           or len(trial.get(phase, {}).get("query_ids", [])) < 30
                           for phase in ("before", "after"))):
                continue
            proof = any(
                value.get("kind") == "ranger_acceptance"
                and value.get("source") == "patched_fe_acceptance"
                and value.get("availability") == "available"
                and value.get("summary", {}).get("row_filter") == "PASS"
                and value.get("summary", {}).get("masking") == "PASS"
                and value.get("summary", {}).get("complete_result_comparisons", 0) >= 60
                and value.get("id") in candidate.get("evidence_ids", [])
                and value.get("family_id") == candidate.get("family_id")
                and value.get("cohort_id") == digest(candidate.get("scope", {}))
                and value.get("summary", {}).get("scope") == candidate.get("scope")
                and len(value.get("query_ids", [])) >= 60
                for value in cycle.get("ranger_acceptance", [])
            )
            outcome = dict(cycle.get("outcome") or {})
            object_evidence = outcome.get("object_evidence") or {}
            verification = outcome.get("verification_evidence") or {}
            windows = outcome.get("windows") or {}
            try:
                complete_window = (
                    datetime.fromisoformat(windows["after_end"])
                    - datetime.fromisoformat(windows["after_start"])
                    >= timedelta(minutes=30)
                    and windows.get("application_interval_excluded") is True
                )
            except (KeyError, TypeError, ValueError):
                complete_window = False
            outcome["verification"] = {
                "object_binding_verified": bool(
                    object_evidence.get("binding")
                    and object_evidence.get("binding") == action.get("owned_object_binding")
                ),
                "rewrite_observed": object_evidence.get("rewrite_observed") is True,
                "freshness_proven": object_evidence.get("state", {}).get("ready") is True,
                "complete_window_verified": complete_window,
                "profile_evidence_available": bool(verification.get("resources")),
                "plan_evidence_available": bool(verification.get("plans")),
            }
            retain(f"governed-cycle-{index + 1}", {
                "findings": [{"detector": "materialized_view", "severity": "info"}],
                "action": {
                    "kind": candidate.get("kind"), "risk": "APPROVAL", "mode": "GOVERNED",
                    "state": candidate.get("state"),
                },
                "experiment": trial,
                "plans": measured.get("plans", {}),
                "outcome": outcome,
                "approval": {
                    "candidate_binding_verified": approval_verified,
                    "administrative_role_verified": approval_verified,
                    "durable_application_verified": approval_verified,
                    "statement_count": len(action.get("statement_digests", [])),
                },
                "evaluation_scope": "isolated_fixture",
                "diagnosis": [{"category": "UNKNOWN", "confidence": 0}],
                "facts": {
                    "ranger_acceptance_proven": proof,
                    "rewrite_observed": object_evidence.get("rewrite_observed") is True,
                    "freshness_proven": object_evidence.get("state", {}).get("ready") is True,
                },
                "evidence_ids": trial["before"]["query_ids"] + trial["after"]["query_ids"],
            })
    if pipeline and pipeline.get("evidence_kind") == "isolated_collected_wall_clock_workload":
        durable = pipeline.get("durable_pipeline", {})
        family = durable.get("family") or {}
        try:
            scope = Scope.model_validate(durable["scope"])
            start = datetime.fromisoformat(family["window"])
            valid_binding = (
                family.get("family_id") == durable.get("family_id")
                and family.get("cohort_id") == scope.cohort_id
                and family.get("scope") == scope.model_dump(mode="json")
            )
        except (KeyError, TypeError, ValueError):
            valid_binding = False
        if valid_binding:
            samples = []
            observations = pipeline.get("contention", {}).get("phases", {}).get(
                "contended", {},
            ).get("observations", [])
            for item in observations:
                try:
                    observed = datetime.fromisoformat(item["observed_at"])
                    if start <= observed < start + timedelta(minutes=30):
                        samples.append(item)
                except (KeyError, TypeError, ValueError):
                    continue
            baseline = family.get("baseline", {})
            retain("D-durable", {
                **family,
                "facts": {
                    "count": baseline.get("current_count"),
                    "p95_ms": baseline.get("current_p95"),
                },
                "profile_samples": scoped_profile_samples(samples),
                "evaluation_scope": "isolated_fixture",
                "evidence_ids": [item["query_id"] for item in samples if item.get("query_id")],
            })
    return cases


def scoped_profile_samples(observations: list[dict]) -> list[dict]:
    return [
        {**item["facts"], "query_id_verified": True,
         "pending_observed": item.get("pending_observed") is True,
         "limit_occupied": item.get("limit_occupied") is True}
        for item in observations if isinstance(item.get("facts"), dict)
        and isinstance(item.get("query_id"), str) and item["query_id"]
        and type(item["facts"].get("queue_ms")) in {int, float}
        and type(item["facts"].get("execution_ms")) in {int, float}
    ]


async def review(cases: list[dict], output: Path) -> dict:
    if not cases:
        raise ValueError("no_retained_measured_cases")
    results = []
    output.mkdir(parents=True, exist_ok=True)
    initialization_error = None
    try:
        try:
            await db.init_system_pool()
        except Exception as exc:
            initialization_error = type(exc).__name__
        for case in cases:
            try:
                if initialization_error:
                    raise RuntimeError("control_plane_unavailable")
                value = await judge(case["review"])
                results.append({
                    "case": case["case"], "status": "VALID",
                    "mean": value.mean_score, "scores": value.model_dump(),
                    "input": case["evidence"],
                })
            except Exception as exc:
                results.append({
                    "case": case["case"], "status": "FAIL", "error_type": type(exc).__name__,
                    **({"reason": "control_plane_unavailable",
                        "initialization_error_type": initialization_error}
                       if initialization_error else {}),
                })
            progress = {
                "status": "RUNNING", "rubric_version": RUBRIC_VERSION,
                "requested_cases": len(cases), "cases": results,
            }
            temporary = output / "measured-judge-progress.json.tmp"
            temporary.write_text(json.dumps(progress, indent=2) + "\n")
            temporary.replace(output / "measured-judge-progress.json")
            print(f"{case['case']}: {results[-1]['status']}", flush=True)
    finally:
        await db.close_system_pool()
    valid = [r for r in results if r["status"] == "VALID"]
    mean = sum(r["mean"] for r in valid) / len(valid) if valid else None
    hallucinations = sum(r["scores"]["critical_hallucinations"] for r in valid)
    report = {
        "evidence_kind": "retained_real_engine_judge_review",
        "model": "deepseek-v4-1-flash", "cases": results,
        "valid_cases": len(valid), "requested_cases": len(cases),
        "mean": mean, "critical_hallucinations": hallucinations,
        "status": "PASS" if len(valid) == len(cases) and mean >= 4.2 and not hallucinations
        else "FAIL",
        "execution_authority": "none",
        "rubric_version": RUBRIC_VERSION,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "measured-judge.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "measured-judge.md").write_text(
        "# Measured engine evidence review\n\n"
        f"Status: {report['status']}; valid: {len(valid)}/{len(cases)}; "
        f"mean: {mean}; critical hallucinations: {hallucinations}.\n\n"
        "Provider errors count as failures. This review has no action authority.\n"
    )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--contention", type=Path)
    parser.add_argument("--regression", type=Path)
    parser.add_argument("--experiment", type=Path)
    parser.add_argument("--governed-cycle", type=Path, action="append", default=[])
    parser.add_argument("--pipeline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-llm", action="store_true", required=True)
    args = parser.parse_args()

    def read(path):
        return json.loads(path.read_text()) if path else None

    cases = review_cases(
        read(args.scenarios), read(args.contention), read(args.regression), read(args.experiment),
        tuple(read(path) for path in args.governed_cycle),
        read(args.pipeline),
    )
    result = asyncio.run(review(cases, args.output))
    print(json.dumps({key: result[key] for key in ("status", "mean", "valid_cases")}))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
