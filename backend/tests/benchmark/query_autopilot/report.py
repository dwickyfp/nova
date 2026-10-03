"""Offline scorecard by default; --live-llm explicitly calls the configured registry."""

import argparse
import asyncio
import json
from pathlib import Path

from app.core.database import db
from app.modules.query_autopilot.detection import detect, diagnose, serialize_findings
from app.modules.query_autopilot.judge import RUBRIC_VERSION, judge, provider_smoke
from app.modules.query_autopilot.models import Policy
from tests.benchmark.query_autopilot.accuracy import cases, scorecard


async def report(output: Path, *, live_llm=False):
    result = {
        "deterministic": scorecard(),
        "live_llm": {"status": "SKIPPED", "reason": "explicit_live_flag_required"},
        "real_engine": {"status": "NOT_RUN"},
        "rubric_version": RUBRIC_VERSION,
    }
    if live_llm:
        await db.init_system_pool()
        try:
            smoke = await provider_smoke()
            judgments = []
            from dataclasses import asdict

            for name, facts, _, _ in cases():
                findings = detect(facts, Policy())
                try:
                    value = await judge(
                        {
                            "findings": serialize_findings(findings),
                            "diagnosis": [asdict(d) for d in diagnose(findings, facts)],
                            "evidence_ids": facts.evidence_ids,
                            "evidence_kind": "deterministic_fixture",
                            "baseline": asdict(facts.baseline),
                            "facts": {
                                **asdict(facts),
                                "count": facts.latency.count,
                                "total_ms": facts.latency.total,
                                "max_ms": facts.latency.maximum,
                                "p50_ms": facts.latency.quantile(0.5),
                                "p95_ms": facts.latency.quantile(0.95),
                                "p99_ms": facts.latency.quantile(0.99),
                            },
                        }
                    )
                    judgments.append(
                        {
                            "case": name,
                            "status": "PASS"
                            if value.mean_score >= 4.2 and not value.critical_hallucinations
                            else "FAIL",
                            "scores": value.model_dump(),
                            "mean": value.mean_score,
                        }
                    )
                except Exception as exc:
                    failure = {"case": name, "status": "FAIL", "error_type": type(exc).__name__}
                    from pydantic import ValidationError

                    if isinstance(exc, ValidationError):
                        failure["validation"] = [
                            {"location": list(error["loc"]), "type": error["type"]}
                            for error in exc.errors(include_input=False, include_url=False)
                        ]
                    judgments.append(failure)
            valid = [j for j in judgments if "mean" in j]
            mean = sum(j["mean"] for j in valid) / len(valid) if valid else None
            result["live_llm"] = {
                "status": "PASS"
                if smoke["passed"]
                and len(valid) == len(judgments)
                and mean is not None
                and mean >= 4.2
                and all(j["scores"]["critical_hallucinations"] == 0 for j in valid)
                else "FAIL",
                "smoke": smoke,
                "judgments": judgments,
                "mean": mean,
                "evidence_kind": "deterministic_fixture_review",
            }
        except Exception as exc:
            result["live_llm"] = {"status": "FAIL", "error_type": type(exc).__name__}
        finally:
            await db.close_system_pool()
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    card = result["deterministic"]
    counts = card["detector_counts"]
    text = (
        f"# Query Autopilot evaluation\n\nEvidence: deterministic fixtures; "
        f"real-engine acceptance is reported separately.\n\n- Cases: {len(card['cases'])}\n- "
        f"Precision: {card['precision']:.1%} ({counts['true_positive']} true "
        f"positives, {counts['false_positive']} false positives)\n- Recall: "
        f"{card['recall']:.1%} ({counts['false_negative']} missed labels)\n- "
        f"RCA top-1: {card['rca']['top1_correct']}/{card['rca']['supported_cases']}\n- "
        f"RCA top-3: {card['rca']['topk_correct']}/{card['rca']['supported_cases']}\n- "
        f"Live LLM: {result['live_llm']['status']}\n- Real engine: NOT_RUN "
        f"by this command\n"
    )
    (output / "report.md").write_text(text)
    print(text)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/query-autopilot"))
    parser.add_argument("--live-llm", action="store_true")
    args = parser.parse_args()
    value = asyncio.run(report(args.output, live_llm=args.live_llm))
    if value["live_llm"]["status"] == "FAIL":
        raise SystemExit(1)
