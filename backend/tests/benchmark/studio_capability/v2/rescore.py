"""Re-score recorded runs offline with v2 and compare with the v1 verdicts.

    cd backend && uv run python -m tests.benchmark.studio_capability.v2.rescore \\
        --run baseline=/path/bench/baseline --run rebench=/path/bench/rebench \\
        --out /path/outside/repo [--rca units.csv]

Reads ``<run>/turns/*.json`` (no Nova, no model). Writes ``rescore.csv`` (one
row per run and turn), ``summary.json`` and ``flips.md`` (v1 -> v2 per run,
and the paired comparison of the first two runs).
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from tests.benchmark.studio_capability.v2 import corpus_v3, stats
from tests.benchmark.studio_capability.v2.expectation import Expectation
from tests.benchmark.studio_capability.v2.fold import Evidence, Signals, fold
from tests.benchmark.studio_capability.v2.grade import Grade, grade
from tests.benchmark.studio_capability.v2.ground_truth import resolve


def v1_verdict(checks: dict[str, Any]) -> str:
    """The v1 harness's pass/fail as its reports counted it."""
    recall = (checks.get("numeric") or {}).get("recall")
    bad = (not checks.get("outcome_ok") or (recall is not None and recall < 1.0)
           or checks.get("outcome") == "error" or bool(checks.get("errors")))
    return "fail" if bad else "pass"


def load_turns(directory: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted((directory / "turns").glob("*.json")):
        record = json.loads(path.read_text())
        out[str(record["id"])] = record
    return out


def session_prior(folded: dict[str, Signals], unit_id: str) -> list[Evidence]:
    """Offline proxy for a dataset preview: the latest earlier table of the session.

    A chart edit stays bound to the governed query of an earlier turn; online the
    runner previews the dataset as the user instead (``adapters.attach``).
    """
    if "." not in unit_id or unit_id.endswith(".fresh"):
        return []
    session, turn = unit_id.split(".", 1)
    digits = turn.removeprefix("t")
    if not digits.isdigit():
        return []
    for prior in range(int(digits) - 1, 0, -1):
        sig = folded.get(f"{session}.t{prior}")
        if sig is None:
            continue
        rows = [Evidence(e.value, "bound_dataset", e.tool, e.ref, e.column)
                for e in sig.evidence if e.source == "table"]
        if rows:
            return rows
    return []


def score_run(records: dict[str, dict[str, Any]], units: dict[str, Expectation],
              gt: dict[str, Any]) -> list[tuple[dict[str, Any], Grade | None]]:
    folded = {uid: fold(record) for uid, record in records.items()}
    out: list[tuple[dict[str, Any], Grade | None]] = []
    for uid, record in records.items():
        unit = units.get(uid)
        if unit is None:
            out.append((record, None))
            continue
        refs = resolve(unit, gt, units)
        out.append((record, grade(unit, folded[uid], refs,
                                  bound_rows=session_prior(folded, uid))))
    return out


def rows_for(run: str, scored: list[tuple[dict[str, Any], Grade | None]],
             rca: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rows = []
    for record, g in scored:
        checks = record.get("checks") or {}
        v1 = v1_verdict(checks)
        label = rca.get(record["id"]) if run == "rebench" else None
        summary = g.summary() if g else {"verdict": "unscored", "why": "unit not in corpus v3"}
        rows.append({
            "run": run, "id": record["id"], "category": (record.get("unit") or {}).get("category"),
            "v1_outcome": checks.get("outcome"), "v1_verdict": v1,
            "v2_outcome": summary.get("outcome"), "v2_reason": summary.get("reason"),
            "v2_outcome_source": summary.get("outcome_source"),
            "v2_recall": summary.get("recall"), "v2_ui_ok": summary.get("ui_ok"),
            "v2_ui_missing": ";".join(summary.get("ui_missing") or []),
            "v2_ui_defects": ";".join(summary.get("ui_defects") or []),
            "v2_verdict": summary["verdict"], "v2_core_verdict": summary.get("core_verdict"),
            "v2_why": summary["why"],
            "rca_class": (label or {}).get("root_class", ""),
            "rca_truth": ("pass" if (label or {}).get("root_class") == "1" else "fail")
            if label else "",
            "flip": f"{v1}->{summary['verdict']}" if v1 != summary["verdict"] else "",
            "tokens": ((checks.get("usage") or {}).get("total_tokens")),
        })
    return rows


def summarize(rows: list[dict[str, Any]], runs: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for run in runs:
        subset = [r for r in rows if r["run"] == run]
        summary[run] = {"turns": len(subset), "v1": dict(Counter(r["v1_verdict"] for r in subset)),
                        "v2": dict(Counter(r["v2_verdict"] for r in subset)),
                        "v2_core": dict(Counter(r["v2_core_verdict"] for r in subset)),
                        "flips": dict(Counter(r["flip"] for r in subset if r["flip"])),
                        "outcomes": dict(Counter(f"{r['v2_outcome']}/{r['v2_reason']}"
                                                 for r in subset))}
    labelled = [r for r in rows if r["rca_truth"]]
    if labelled:
        agreement: dict[str, Any] = {
            "n": len(labelled),
            "v1_agree": sum(r["v1_verdict"] == r["rca_truth"] for r in labelled),
            "kappa_v1": stats.cohen_kappa([(r["v1_verdict"], r["rca_truth"]) for r in labelled]),
        }
        for key in ("v2_core_verdict", "v2_verdict"):
            decided = [r for r in labelled if r[key] != "judge"]
            agreement[key] = {
                "agree": sum(r[key] == r["rca_truth"] for r in labelled),
                "judge": len(labelled) - len(decided),
                "disagree": [r["id"] for r in decided if r[key] != r["rca_truth"]],
                "kappa_decided": stats.cohen_kappa([(r[key], r["rca_truth"]) for r in decided]),
            }
        summary["rca_agreement"] = agreement
    if len(runs) >= 2:
        before = {r["id"]: r for r in rows if r["run"] == runs[0]}
        after = {r["id"]: r for r in rows if r["run"] == runs[1]}
        for key in ("v1_verdict", "v2_core_verdict", "v2_verdict"):
            summary[f"paired_{key}"] = stats.paired_binary(
                {i: before[i][key] for i in before}, {i: after[i][key] for i in after})
    return summary


def flips_markdown(rows: list[dict[str, Any]], summary: dict[str, Any], runs: list[str]) -> str:
    lines = ["# v1 -> v2 flip table", ""]
    for run in runs:
        s = summary[run]
        lines += [f"## {run} ({s['turns']} turns)", "",
                  f"v1 {s['v1']} -> v2 core {s['v2_core']} -> v2 full {s['v2']}", "",
                  "| Flip | Units |", "|---|---|"]
        for flip, count in sorted(s["flips"].items(), key=lambda kv: -kv[1]):
            ids = [r["id"] for r in rows if r["run"] == run and r["flip"] == flip]
            lines.append(f"| {flip} ({count}) | {', '.join(ids)} |")
        lines.append("")
    if "rca_agreement" in summary:
        a = summary["rca_agreement"]
        lines += ["## Agreement with the root-cause labels (rebench)", "",
                  f"- v1: {a['v1_agree']}/{a['n']} (kappa {a['kappa_v1']})"]
        for key, label in (("v2_core_verdict", "v2 core (outcome, safety, figures)"),
                           ("v2_verdict", "v2 full (+ UI tree)")):
            b = a[key]
            lines.append(f"- {label}: {b['agree']}/{a['n']}, {b['judge']} to the judge, kappa "
                         f"on decided {b['kappa_decided']}; disagrees on: "
                         f"{', '.join(b['disagree']) or '-'}")
        lines.append("")
    for key in ("paired_v1_verdict", "paired_v2_core_verdict", "paired_v2_verdict"):
        if key in summary:
            p = summary[key]
            lines.append(f"Paired {runs[0]} -> {runs[1]} ({key[7:]}): {p['shared']} shared, "
                         f"{p['decided']} decided, fixed {p['fixed']}, regressed "
                         f"{p['regressed']}, McNemar exact p={p['mcnemar_p']}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="append", required=True, help="name=directory")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rca", type=Path, help="root-cause units.csv (unit, root_class)")
    args = parser.parse_args()
    units = {u.id: u for u in corpus_v3.load_units()}
    gt = corpus_v3.load_ground_truth()
    rca = {r["unit"]: r for r in csv.DictReader(args.rca.open())} if args.rca else {}
    rows: list[dict[str, Any]] = []
    runs = []
    for spec in args.run:
        name, directory = spec.split("=", 1)
        runs.append(name)
        rows += rows_for(name, score_run(load_turns(Path(directory)), units, gt), rca)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "rescore.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = summarize(rows, runs)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    (args.out / "flips.md").write_text(flips_markdown(rows, summary, runs))
    print(json.dumps({k: v for k, v in summary.items()}, indent=1, default=str)[:4000])


if __name__ == "__main__":
    main()
