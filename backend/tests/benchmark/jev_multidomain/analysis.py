"""Aggregate artifacts without promoting relevance probes to end-to-end decisions."""

from __future__ import annotations

import json
from collections import Counter
from statistics import mean

from tests.benchmark.jev_multidomain.environment import ARTIFACTS, read_records
from tests.benchmark.jev_multidomain.metrics import percentile, summarize


def probe_summary(cases: list[dict], records: list[dict]) -> dict:
    ground = {c["id"]: c for c in cases}
    rows = []
    for record in records:
        case = ground[record["case_id"]]
        primary = sorted(record["jev_primary_agents"])
        expected = case["expected"].split(" + ")
        trace = record.get("trace", [{}])[-1]
        accepted = trace.get("status") == "accepted"
        scores = trace.get("scores", {}).values()
        confidence = min(
            (s["confidence"] for s in scores if s.get("label") == "primary" and s.get("accepted")),
            default=None,
        )
        eligible = set(expected) <= {"FINANCE", "MARKETING"}
        rows.append(
            {
                "case_id": case["id"],
                "category": case["category"],
                "expected": case["expected"],
                "primary": primary,
                "accepted": accepted,
                "eligible": eligible,
                "primary_set_correct": accepted and primary == sorted(expected)
                if eligible
                else None,
                "lexical_top_one_correct": record["lexical_ranked"][:1] == expected
                if len(expected) == 1 and eligible
                else None,
                "jev_ranked_top_one_correct": record["ranked"][:1] == expected
                if len(expected) == 1 and eligible
                else None,
                "confidence": confidence,
            }
        )
    numeric = [r for r in rows if r["eligible"]]
    single = [r for r in rows if r["lexical_top_one_correct"] is not None]
    ambiguous = [r for r in rows if r["expected"] == "CLARIFY"]
    general = [r for r in rows if r["expected"] == "GENERAL"]
    bins = {}
    for low, high in [(0, 0.6), (0.6, 0.8), (0.8, 0.9), (0.9, 1.01)]:
        sample = [
            r for r in numeric if r["confidence"] is not None and low <= r["confidence"] < high
        ]
        bins[f"{low:.1f}-{min(high, 1):.1f}"] = {
            "n": len(sample),
            "primary_set_accuracy": mean(r["primary_set_correct"] for r in sample)
            if sample
            else None,
        }
    return {
        "attempted": len(rows),
        "agent_set_cases": len(numeric),
        "accepted": sum(r["accepted"] for r in rows),
        "primary_set_accuracy": mean(r["primary_set_correct"] for r in numeric)
        if numeric
        else None,
        "lexical_single_domain_top_one_accuracy": mean(r["lexical_top_one_correct"] for r in single)
        if single
        else None,
        "jev_single_domain_top_one_accuracy": mean(r["jev_ranked_top_one_correct"] for r in single)
        if single
        else None,
        "single_domain_cases": len(single),
        "clarify_cases": len(ambiguous),
        "clarify_with_primary": sum(bool(r["primary"]) for r in ambiguous),
        "clarify_with_confident_primary": sum(
            bool(r["primary"]) and r["confidence"] is not None and r["confidence"] >= 0.9
            for r in ambiguous
        ),
        "general_cases": len(general),
        "general_with_primary": sum(bool(r["primary"]) for r in general),
        "confidence_failure": sum(
            bool(
                r["confidence"] is not None
                and r["confidence"] >= 0.9
                and not r["primary_set_correct"]
            )
            for r in numeric
        ),
        "calibration": bins,
        "categories": {
            category: {
                "n": len(group),
                "primary_set_accuracy": mean(r["primary_set_correct"] for r in group),
            }
            for category in {r["category"] for r in numeric}
            if (group := [r for r in numeric if r["category"] == category])
        },
        "latency_seconds": {
            key: percentile([r["latency_seconds"] for r in records], value)
            for key, value in [("p50", 0.5), ("p95", 0.95), ("max", 1)]
        },
        "tokens": {
            key: sum(
                int(t.get("usage", {}).get(key) or 0) for r in records for t in r.get("trace", [])
            )
            for key in ["input_tokens", "output_tokens"]
        },
        "cases": rows,
        "limitation": "The production hook scores relevance per agent; it has no CLARIFY or "
        "GENERAL decision action. Primary-set metrics are not Smart exact-routing metrics.",
    }


def stability(runs: list[list[dict]], key: str) -> dict:
    mappings = [{r["case_id"]: r for r in rows} for rows in runs]
    common = set.intersection(*(set(m) for m in mappings)) if mappings else set()
    all_values = [
        [json.dumps(m[cid][key], sort_keys=True) for m in mappings] for cid in sorted(common)
    ]
    validity = [
        [
            m[cid][key] not in {"ERROR", "OTHER"}
            if key == "predicted"
            else bool(m[cid].get("accepted", True))
            for m in mappings
        ]
        for cid in sorted(common)
    ]
    valid_values = [
        [v for v, valid in zip(values, valid_flags, strict=True) if valid]
        for values, valid_flags in zip(all_values, validity, strict=True)
    ]
    return {
        "paired_cases": len(common),
        "runs": len(runs),
        "modal_agreement": mean(
            (Counter(v).most_common(1)[0][1] if v else 0) / len(runs) for v in valid_values
        )
        if all_values
        else None,
        "raw_modal_agreement_including_failures": mean(
            Counter(v).most_common(1)[0][1] / len(v) for v in all_values
        )
        if all_values
        else None,
        "valid_paired_cases": sum(all(flags) for flags in validity),
        "unanimous_cases": sum(
            len(set(v)) == 1 and all(flags) for v, flags in zip(all_values, validity, strict=True)
        ),
        "changed_case_ids": [
            cid
            for cid, values in zip(sorted(common), all_values, strict=True)
            if len(set(values)) > 1
        ],
        "change_rate": mean(len(set(v)) > 1 for v in all_values) if all_values else None,
        "valid_change_rate": mean(
            len(set(values)) > 1
            for values, flags in zip(all_values, validity, strict=True)
            if all(flags)
        )
        if any(all(flags) for flags in validity)
        else None,
    }


def aggregate() -> dict:
    cases = json.loads((ARTIFACTS / "ground_truth.json").read_text())["cases"]
    cohorts = json.loads((ARTIFACTS / "cohort-ground-truth.json").read_text())["cases"]
    oracle = json.loads((ARTIFACTS / "oracle.json").read_text())["cases"]
    oracle.update(json.loads((ARTIFACTS / "cohort-oracle.json").read_text())["cases"])
    all_cases = {c["id"]: c for c in cases + cohorts}
    plan = json.loads((ARTIFACTS / "experiments.json").read_text())
    planned_ids = {item["name"]: item["ids"] for item in plan["experiments"]}
    summaries, raw = {}, {}
    names = [
        "final-on",
        "cohort-on",
        "baseline-pilot-recovered",
        "metadata-v2-pilot",
        "corrected-controlled-pilot",
        "pre-evidence-fix",
        "baseline-serial-pilot",
        "relative-target-pilot",
        "before-relative-target-fix/final-on",
        "before-relative-target-fix/baseline-off",
        *[e["name"] for e in plan["experiments"]],
    ]
    for name in names:
        records = read_records(ARTIFACTS / (name + ".jsonl"))
        if not records:
            continue
        raw[name] = records
        ground = (
            cases
            if name == "final-on"
            else cohorts
            if name == "cohort-on"
            else [all_cases[cid] for cid in planned_ids.get(name, [r["case_id"] for r in records])]
        )
        result_path = ARTIFACTS / (name + ".jsonl")
        judgments = read_records(result_path.with_name("judge-" + result_path.name))
        if not judgments and name in {
            "baseline-pilot-recovered",
            "metadata-v2-pilot",
            "corrected-controlled-pilot",
            "pre-evidence-fix",
            "baseline-serial-pilot",
        }:
            judgments = read_records(ARTIFACTS / "judge-rubric-v1" / ("judge-" + result_path.name))
        summaries[name] = summarize(ground, records, judgments, oracle)
        summaries[name]["judge_rubric_versions"] = sorted(
            {row.get("rubric_version", 1) for row in judgments}
        )
    probes = {}
    for path in ARTIFACTS.glob("probe-*.jsonl"):
        if path.stem == "probe-load-pilot" or ".infrastructure" in path.stem:
            continue
        probes[path.stem] = probe_summary(cases, read_records(path))
    comparisons = {}
    if "final-on" in summaries:
        for name, result in summaries.items():
            if name.startswith("variant-"):
                comparisons[name] = stability(
                    [summaries["final-on"]["cases"], result["cases"]], "predicted"
                )
        if all(name in summaries for name in ["repeat-2", "repeat-3"]):
            comparisons["three_run_smart"] = stability(
                [summaries[n]["cases"] for n in ["final-on", "repeat-2", "repeat-3"]], "predicted"
            )
            comparisons["three_run_smart_clear"] = stability(
                [
                    [r for r in summaries[n]["cases"] if r["expected"] != "CLARIFY"]
                    for n in ["final-on", "repeat-2", "repeat-3"]
                ],
                "predicted",
            )
    if "probe-full" in probes:
        for name, result in probes.items():
            if name != "probe-full":
                comparisons[name] = stability(
                    [probes["probe-full"]["cases"], result["cases"]], "primary"
                )
    report = {"smart": summaries, "probes": probes, "comparisons": comparisons}
    (ARTIFACTS / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    result = aggregate()
    print(
        json.dumps(
            {
                name: {
                    k: value.get(k)
                    for k in [
                        "attempted",
                        "routing_accuracy",
                        "execution_oracle_accuracy",
                        "answer_normalized_accuracy",
                    ]
                }
                for name, value in result["smart"].items()
            },
            indent=2,
        )
    )
