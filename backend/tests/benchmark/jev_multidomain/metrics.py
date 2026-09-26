"""Report all attempted cases, including provider and execution failures."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

from tests.benchmark.jev_multidomain.execution_score import execution_score

LABELS = ["FINANCE", "MARKETING", "FINANCE + MARKETING", "CLARIFY", "GENERAL", "ERROR", "OTHER"]


def predicted_route(record: dict, judgment: dict) -> str:
    selected = set(record.get("selected_agents", []))
    if selected:
        return " + ".join(sorted(selected)) if selected <= {"FINANCE", "MARKETING"} else "OTHER"
    if judgment.get("response_kind") == "clarification":
        return "CLARIFY"
    if judgment.get("response_kind") == "answer":
        return "GENERAL"
    return "ERROR"


def percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def agent_latencies(records: list[dict]) -> dict:
    startup, execution = [], []
    for record in records:
        children = {r["run_id"] for r in record.get("runs", []) if r.get("depth")}
        times = defaultdict(dict)
        for event in record.get("events", []):
            if event.get("run_id") not in children or not event.get("created_at"):
                continue
            times[event["run_id"]].setdefault(
                event["type"], datetime.fromisoformat(event["created_at"])
            )
        for run in times.values():
            if "agent_started" not in run:
                continue
            if "agent_queued" in run:
                startup.append((run["agent_started"] - run["agent_queued"]).total_seconds())
            finished = [
                run[k] for k in ["agent_completed", "agent_failed", "agent_cancelled"] if k in run
            ]
            if finished:
                execution.append((min(finished) - run["agent_started"]).total_seconds())
    return {
        name: {
            "observations": len(values),
            **{key: percentile(values, q) for key, q in [("p50", 0.5), ("p95", 0.95), ("max", 1)]},
        }
        for name, values in [("startup_seconds", startup), ("execution_seconds", execution)]
    }


def summarize(
    cases: list[dict],
    records: list[dict],
    judgments: list[dict],
    oracle: dict[str, list[dict]] | None = None,
) -> dict:
    by_case = {c["id"]: c for c in cases}
    judges = {j["case_id"]: j for j in judgments}
    matrix = {label: {other: 0 for other in LABELS} for label in LABELS}
    rows, categories, error_types = [], defaultdict(list), Counter()
    for record in records:
        case = by_case[record["case_id"]]
        judgment = judges.get(case["id"], {})
        predicted = predicted_route(record, judgment)
        correct = predicted in [case["expected"], *case.get("allowed_alternatives", [])]
        matrix[case["expected"]][predicted] += 1
        score = judgment.get("score")
        execution = execution_score(record, (oracle or {}).get(case["id"], []))
        errors = []
        expected_agents = set(case["expected"].split(" + ")) & {"FINANCE", "MARKETING"}
        selected = set(record.get("selected_agents", []))
        if expected_agents - selected:
            errors.append("missing_agent")
        if selected - expected_agents:
            errors.append("unnecessary_agent" if selected & expected_agents else "wrong_agent")
        if case["expected"] == "CLARIFY" and predicted != "CLARIFY":
            errors.append("ambiguity_failure")
        if record.get("status") != "completed":
            errors.append("execution_failure")
        if any(
            call.get("error") or not call.get("response") for call in record.get("jev_calls", [])
        ):
            errors.append("JEV_decision_failure")
        if judgment.get("hallucinated_business_claim"):
            errors.append("hallucination")
        event_payloads = [event.get("payload", {}) for event in record.get("events", [])]
        if any(
            p.get("code") == "tool_failed"
            or p.get("event_type") == "tool_status"
            and p.get("status") == "failed"
            for p in event_payloads
        ):
            errors.append("tool_failure")
        tool_errors = []
        for run in record.get("runs", []):
            for message in run.get("checkpoint", {}).get("loop", {}).get("messages", []):
                if message.get("role") != "tool":
                    continue
                try:
                    payload = json.loads(message.get("content") or "{}")
                except (ValueError, TypeError):
                    continue
                if isinstance(payload, dict) and payload.get("error_class"):
                    tool_errors.append(payload["error_class"])
        if "INVALID_SEMANTIC_PLAN" in tool_errors:
            errors.append("semantic_misunderstanding")
        if any("QUERY" in error and "EXECUTION" in error for error in tool_errors):
            errors.append("query_failure")
        if any(
            p.get("code") in {"semantic_model_unavailable", "semantic_view_unavailable"}
            for p in event_payloads
        ):
            errors.append("semantic_view_failure")
        if (
            record.get("harness_error")
            or "COLLABORATION_REJECTED" in tool_errors
            or any(
                r.get("error_class") in {"BudgetExceeded", "ValueError", "session_or_role_changed"}
                for r in record.get("runs", [])
            )
        ):
            errors.append("orchestration_failure")
        if score is not None and score < 3:
            errors.append("answer_incomplete_or_incorrect")
        if execution["correct"] and score is not None and score < 2:
            errors.append("answer_synthesis_failure")
        if score is None:
            errors.append("judge_unavailable")
        error_types.update(errors)
        row = {
            "case_id": case["id"],
            "category": case["category"],
            "split": case["split"],
            "expected": case["expected"],
            "predicted": predicted,
            "routing_correct": correct,
            "score": score,
            "execution": execution,
            "status": record.get("status"),
            "errors": errors,
            "judge_reason": judgment.get("reason", ""),
            "unresolved": (
                not correct
                or record.get("status") != "completed"
                or score != 3
                or (execution["applicable"] and not execution["correct"])
            ),
        }
        rows.append(row)
        categories[case["category"]].append(row)
    classes = {}
    for label in LABELS[:5]:
        tp = matrix[label][label]
        fp = sum(matrix[actual][label] for actual in LABELS if actual != label)
        fn = sum(matrix[label][other] for other in LABELS if other != label)
        precision, recall = tp / (tp + fp) if tp + fp else 0, tp / (tp + fn) if tp + fn else 0
        classes[label] = {
            "support": sum(matrix[label].values()),
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
        }
    agents = {}
    for domain in ["FINANCE", "MARKETING"]:
        tp = sum(
            domain in row["expected"].split(" + ") and domain in row["predicted"].split(" + ")
            for row in rows
        )
        fp = sum(
            domain not in row["expected"].split(" + ") and domain in row["predicted"].split(" + ")
            for row in rows
        )
        fn = sum(
            domain in row["expected"].split(" + ") and domain not in row["predicted"].split(" + ")
            for row in rows
        )
        agents[domain] = {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": tp / (tp + fp) if tp + fp else 0,
            "recall": tp / (tp + fn) if tp + fn else 0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0,
        }
    paraphrases = defaultdict(list)
    for row in rows:
        case = by_case[row["case_id"]]
        if case["category"] == "paraphrase":
            paraphrases[case["group"]].append(row["predicted"])
    numeric = [
        row
        for row in rows
        if row["execution"]["applicable"] or by_case[row["case_id"]].get("metrics")
    ]
    latency = [r["latency_seconds"] for r in records]
    decision_latency = [c["latency_seconds"] for r in records for c in r.get("jev_calls", [])]
    return {
        "attempted": len(records),
        "planned": len(cases),
        "missing_case_ids": sorted(set(by_case) - {r["case_id"] for r in records}),
        "routing_accuracy": mean(r["routing_correct"] for r in rows) if rows else None,
        "completion_rate": mean(r.get("status") == "completed" for r in records) if rows else None,
        "execution_oracle_accuracy": mean(
            r["execution"]["correct"] for r in rows if r["execution"]["applicable"]
        )
        if any(r["execution"]["applicable"] for r in rows)
        else None,
        "execution_oracle_count": sum(r["execution"]["applicable"] for r in rows),
        "critical_failure_candidates": [
            r["case_id"]
            for r in rows
            if "wrong_agent" in r["errors"] and r["status"] == "completed" and r["score"] == 0
        ],
        "answer_normalized_accuracy": sum(r["score"] or 0 for r in rows) / (3 * len(rows))
        if rows
        else None,
        "fully_correct_answers": sum(r["score"] == 3 for r in rows),
        "unresolved_cases": sum(r["unresolved"] for r in rows),
        "judge_missing": sum(r["score"] is None for r in rows),
        "confusion_matrix": matrix,
        "classes": classes,
        "per_agent": agents,
        "deterministic_answer_accuracy": sum(row["score"] or 0 for row in numeric)
        / (3 * len(numeric))
        if numeric
        else None,
        "deterministic_answer_count": len(numeric),
        "paraphrase_modal_agreement": sum(
            max(Counter(v for v in values if v not in {"ERROR", "OTHER"}).values(), default=0)
            for values in paraphrases.values()
        )
        / sum(map(len, paraphrases.values()))
        if paraphrases
        else None,
        "paraphrase_unanimous_groups": sum(
            len(set(values)) == 1 and values[0] not in {"ERROR", "OTHER"}
            for values in paraphrases.values()
        ),
        "paraphrase_group_count": len(paraphrases),
        "macro_f1": mean(v["f1"] for v in classes.values()),
        "categories": {
            key: {
                "n": len(value),
                "routing_accuracy": mean(r["routing_correct"] for r in value),
                "answer_normalized_accuracy": sum(r["score"] or 0 for r in value)
                / (3 * len(value)),
            }
            for key, value in categories.items()
        },
        "splits": {
            split: {
                "n": len(subset),
                "routing_accuracy": mean(r["routing_correct"] for r in subset),
                "answer_normalized_accuracy": sum(r["score"] or 0 for r in subset)
                / (3 * len(subset)),
            }
            for split in {r["split"] for r in rows}
            if (subset := [r for r in rows if r["split"] == split])
        },
        "latency_seconds": {
            "p50": percentile(latency, 0.5),
            "p95": percentile(latency, 0.95),
            "total": sum(latency),
            "max": max(latency) if latency else None,
        },
        "jev_latency_seconds": {
            "p50": percentile(decision_latency, 0.5),
            "p95": percentile(decision_latency, 0.95),
            "calls": len(decision_latency),
            "max": max(decision_latency) if decision_latency else None,
        },
        "tokens": {
            key: sum(int(run.get(key) or 0) for r in records for run in r.get("runs", []))
            for key in ["prompt_tokens", "completion_tokens"]
        },
        "jev_tokens": {
            key: sum(
                int(call.get("response", {}).get("usage", {}).get(key) or 0)
                for record in records
                for call in record.get("jev_calls", [])
            )
            for key in ["input_tokens", "output_tokens"]
        },
        "query_latency_seconds": {
            key: percentile(
                [
                    q["latency_seconds"]
                    for r in records
                    for q in r.get("queries", [])
                    if q.get("rows")
                ],
                quantile,
            )
            for key, quantile in [("p50", 0.5), ("p95", 0.95), ("max", 1)]
        },
        "agent_latency": agent_latencies(records),
        "error_types": {
            key: error_types[key]
            for key in sorted(
                set(error_types)
                | {
                    "wrong_agent",
                    "missing_agent",
                    "unnecessary_agent",
                    "ambiguity_failure",
                    "semantic_misunderstanding",
                    "tool_failure",
                    "query_failure",
                    "semantic_view_failure",
                    "orchestration_failure",
                    "answer_synthesis_failure",
                    "hallucination",
                    "JEV_decision_failure",
                }
            )
        },
        "cases": rows,
    }


def write_summary(ground: Path, results: Path, judgments: Path, output: Path) -> dict:
    cases = json.loads(ground.read_text())["cases"]
    records = [json.loads(line) for line in results.read_text().splitlines()]
    judges = [json.loads(line) for line in judgments.read_text().splitlines()]
    result = summarize(cases, records, judges)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result
