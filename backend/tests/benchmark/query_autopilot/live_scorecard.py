"""Score retained measured scenarios without treating unavailable evidence as success."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.modules.query_autopilot.detection import Facts, detect, serialize_findings
from app.modules.query_autopilot.models import Policy, utcnow
from app.modules.query_autopilot.statistics import Distribution, Window, compare_baseline


def measured_scorecard(scenarios: dict, contention: dict | None, regression: dict | None) -> dict:
    results = []

    def assessed(name, found, expected, *, diagnosis=(), root=None, source="real_native_engine"):
        actual = {item["detector"] for item in found}
        results.append(
            {
                "case": name,
                "status": "PASS" if actual == expected else "FAIL",
                "source": source,
                "expected": sorted(expected),
                "actual": sorted(actual),
                "expected_root_cause": root,
                "diagnosis": [item["category"] for item in diagnosis],
            }
        )

    by_name = {case["case"]: case for case in scenarios.get("cases", [])}
    for name, expected in (("A", {"high_frequency"}), ("B", {"rare_slow"})):
        case = by_name.get(name)
        if not case or not case.get("query_ids") or not case.get("latency"):
            results.append({"case": name, "status": "UNAVAILABLE"})
            continue
        latency = Distribution.from_dict(case["latency"])
        facts = Facts(latency, compare_baseline(Window(utcnow(), latency), []))
        assessed(name, serialize_findings(detect(facts, Policy())), expected)
    case = by_name.get("F-detection")
    if case and "findings" in case:
        assessed("F-detection", case["findings"], {"high_frequency", "materialized_view"})
    else:
        results.append({"case": "F-detection", "status": "UNAVAILABLE"})
    case = by_name.get("E")
    if case and all(phase in case.get("phases", {}) for phase in ("before", "after")):
        for phase, expected, root in (
            ("before", {"statistics", "cardinality_error"}, "CARDINALITY_ESTIMATE"),
            ("after", set(), None),
        ):
            evidence = case["phases"][phase]
            assessed(
                "E-" + phase, evidence.get("findings", []), expected,
                diagnosis=evidence.get("diagnosis", []), root=root,
            )
    else:
        results.append({"case": "E", "status": "UNAVAILABLE"})
    if contention and all(p in contention.get("phases", {}) for p in ("uncontended", "contended")):
        for phase, expected, root in (
            ("uncontended", set(), None),
            ("contended", {"contention"}, "RESOURCE_CONTENTION"),
        ):
            evidence = contention["phases"][phase]
            if evidence.get("count", 0) >= 100:
                expected = expected | {"high_frequency"}
            latency = evidence.get("latency")
            if isinstance(latency, dict):
                measured_latency = Distribution.from_dict(latency)
                if measured_latency.count >= 20 and (
                    measured_latency.quantile(0.95) or 0
                ) >= Policy().absolute_slow_ms:
                    expected = expected | {"absolute_slow"}
            assessed(
                "G-" + phase, evidence.get("findings", []), expected,
                diagnosis=evidence.get("diagnosis", []), root=root,
            )
    else:
        results.append({"case": "G", "status": "UNAVAILABLE"})
    if regression and regression.get("wall_clock_historical_acceptance") == "PASS":
        facts = regression.get("facts", {})
        bound_contention = (
            regression.get("ground_truth") == "same_query_slowed_by_controlled_admission_queue"
            and facts.get("saturated") is True and facts.get("bound_pending_samples", 0) > 0
            and facts.get("counter_pair_sample_count", 0) == 1
            and facts.get("queue_ms", 0) >= 100
            and type(facts.get("execution_ms")) in {int, float}
            and bool(regression.get("after_query_ids"))
        )
        assessed(
            "D", regression["findings"],
            ({"latency_regression", "contention"} if bound_contention else {"latency_regression"})
            | ({"absolute_slow"} if facts.get("count", 0) >= 20
               and facts.get("p95_ms", 0) >= Policy().absolute_slow_ms else set()),
            diagnosis=regression.get("diagnosis", []),
            root="RESOURCE_CONTENTION" if bound_contention else None,
        )
        for variant, rejected in regression["negative_variants"].items():
            assessed(
                "D-" + variant,
                [] if rejected else [{"detector": "latency_regression"}], set(),
            )
    else:
        results.append({"case": "D", "status": "UNAVAILABLE"})
    measured = [case for case in results if "actual" in case]
    tp = sum(len(set(c["expected"]) & set(c["actual"])) for c in measured)
    fp = sum(len(set(c["actual"]) - set(c["expected"])) for c in measured)
    fn = sum(len(set(c["expected"]) - set(c["actual"])) for c in measured)
    supported = [case for case in measured if case["expected_root_cause"]]
    first = sum(
        bool(c["diagnosis"]) and c["diagnosis"][0] == c["expected_root_cause"]
        for c in supported
    )
    topk = sum(c["expected_root_cause"] in c["diagnosis"][:3] for c in supported)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "evidence_kind": "retained_real_engine_measurements",
        "cases": results,
        "measured_cases": len(measured),
        "unavailable_cases": len(results) - len(measured),
        "detector_counts": {"true_positive": tp, "false_positive": fp, "false_negative": fn},
        "precision": precision,
        "recall": recall,
        "rca": {
            "supported_cases": len(supported), "top1_correct": first, "topk_correct": topk,
            "top1": first / len(supported) if supported else None,
            "topk": topk / len(supported) if supported else None,
        },
        "coverage": {
            "parameter_family": by_name.get("C", {}).get("status", "UNAVAILABLE"),
            "result_change_rejection": by_name.get("H", {}).get("status", "UNAVAILABLE"),
            "controlled_log_fixture": by_name.get("I", {}).get("status", "UNAVAILABLE"),
        },
        "complete_acceptance": False,
        "limitations": [
            "Controlled log evidence is excluded from live detector and RCA denominators.",
            "Production application, risk classification and verification remain separate gates.",
            "The measured sample covers the declared scenarios and detector labels only.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--contention", type=Path)
    parser.add_argument("--regression", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    def read(path):
        return json.loads(path.read_text()) if path else None

    value = measured_scorecard(read(args.scenarios), read(args.contention), read(args.regression))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "live-scorecard.json").write_text(json.dumps(value, indent=2) + "\n")
    counts = value["detector_counts"]
    rca = value["rca"]
    text = (
        "# Retained live scenario scorecard\n\n"
        f"Measured cases: {value['measured_cases']}; unavailable: {value['unavailable_cases']}.\n\n"
        f"Detector labels: {counts['true_positive']} TP, {counts['false_positive']} FP, "
        f"{counts['false_negative']} FN.\n\n"
        f"Precision: {value['precision']}; recall: {value['recall']}.\n\n"
        f"RCA top-1: {rca['top1_correct']}/{rca['supported_cases']}; "
        f"top-3: {rca['topk_correct']}/{rca['supported_cases']}.\n\n"
        + "\n".join(f"- {line}" for line in value["limitations"]) + "\n"
    )
    (args.output / "live-scorecard.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
