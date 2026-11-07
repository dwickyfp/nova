"""The Supernova ontology benchmark: one reproducible report from measured evaluation runs.

The report reads the completed evaluation runs the caller may see and states, with
95% intervals and paired tests:

* answer quality per gold set and mode (accuracy, reliability, clarification,
  abstention), and the ontology's paired effect (McNemar);
* claim accuracy by consensus and the judges' agreement on a held-out half;
* join discovery precision, recall and F1, calibration (ECE, AUC) and the share
  decided without a person.

It runs no model and changes nothing. Credentials come from the environment and are
never printed:

    NOVA_BENCH_PASSWORD=... python -m tests.benchmark.supernova_ontology.report > report.md

``NOVA_BENCH_URL`` (default ``http://localhost:8000``) and ``NOVA_BENCH_USER``
(default ``nova_admin``) select the deployment and the evaluating ACCOUNTADMIN.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Callable, Iterable
from typing import Any

from app.common.statistics import cohen_kappa, mcnemar, proportion

#: The external reviewer whose blind verdicts the judges are compared with.
REVIEWER = "claude-opus-5.5"

OK: dict[str, Callable[[str], bool]] = {
    "answerable": lambda outcome: outcome == "correct",
    "ambiguous": lambda outcome: outcome == "clarified",
    "unanswerable": lambda outcome: outcome in {"abstained", "clarified"},
}


def half(key: str) -> str:
    """The deterministic dev/test half a judged item belongs to."""
    return "dev" if int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2 == 0 else "test"


def pct(measured: dict | None) -> str:
    if not measured or measured.get("value") is None:
        return "–"
    low, high = measured.get("interval_95") or (None, None)
    interval = f" ({low * 100:.1f}–{high * 100:.1f}%)" if low is not None else ""
    return f"{measured['successes']}/{measured['n']} = {measured['value'] * 100:.1f}%{interval}"


def kind_rates(cases: dict[str, dict], results: dict[str, dict]) -> dict[str, dict]:
    """Per case kind, how often the run did the right thing."""
    rates = {}
    for kind, ok in OK.items():
        ids = [key for key, case in cases.items() if case["kind"] == kind and key in results]
        rates[kind] = proportion(sum(ok(results[key]["outcome"]) for key in ids), len(ids))
    return rates


def paired(cases: dict[str, dict], with_results: dict[str, dict],
           without: dict[str, dict]) -> dict[str, Any]:
    """The ontology's effect on the same answerable cases."""
    ids = [key for key, case in cases.items()
           if case["kind"] == "answerable" and key in with_results and key in without]
    before = [OK["answerable"](without[key]["outcome"]) for key in ids]
    after = [OK["answerable"](with_results[key]["outcome"]) for key in ids]
    return {"n": len(ids), "without": proportion(sum(before), len(ids)),
            "with": proportion(sum(after), len(ids)), "mcnemar": mcnemar(before, after)}


def held_out_kappa(items: Iterable[tuple[str, list[dict]]]) -> dict[str, Any]:
    """κ between the newest model verdict and the external reviewer, per half."""
    pairs: dict[str, tuple[list[str], list[str]]] = {"dev": ([], []), "test": ([], [])}
    for key, reviews in items:
        model = next((r for r in reversed(reviews) if r["kind"] == "model"
                      and not r["reviewer"].startswith("nova:")), None)
        mine = next((r for r in reviews if r["reviewer"] == REVIEWER), None)
        if model and mine:
            left, right = pairs[half(key)]
            left.append(model["verdict"])
            right.append(mine["verdict"])
    return {name: cohen_kappa(left, right) for name, (left, right) in pairs.items()}


def render(sections: dict[str, Any]) -> str:
    lines = ["# Supernova ontology benchmark", ""]
    lines += ["## Answers (no view hint)", "",
              "| Gold set | Mode | Run | Answerable correct | Ambiguous asked back | "
              "Unanswerable refused | Reliability |", "|---|---|---|---|---|---|---|"]
    for row in sections["answers"]:
        lines.append(
            f"| {row['set']} | {row['mode']} | `{row['run'][:12]}…` | "
            f"{pct(row['rates']['answerable'])} | {pct(row['rates']['ambiguous'])} | "
            f"{pct(row['rates']['unanswerable'])} | {row['reliability']:.3f} |")
    lines += ["", "**The ontology's effect (paired, answerable):**", ""]
    for row in sections["effects"]:
        test = row["mcnemar"]
        lines.append(
            f"- {row['set']}: without {pct(row['without'])}, with {pct(row['with'])}; "
            f"{test['gained']} fixed, {test['lost']} broken, McNemar p = {test['p_value']:.3g}")
    claims = sections.get("claims")
    if claims:
        lines += ["", "## Claims", "",
                  f"- Accuracy by consensus: {pct(claims['accuracy'])}",
                  f"- Judge κ on the held-out half: {claims['kappa']['test']['kappa']} "
                  f"(n={claims['kappa']['test']['n']}); dev half "
                  f"{claims['kappa']['dev']['kappa']}"]
    gold = sections.get("gold_judge")
    if gold:
        lines += ["", "## Gold-set judge", "",
                  f"- Rubric {gold['rubric']}: κ on the held-out half "
                  f"{gold['kappa']['test']['kappa']} (n={gold['kappa']['test']['n']}); "
                  f"dev half {gold['kappa']['dev']['kappa']}"]
    joins = sections.get("joins")
    if joins:
        score = joins.get("joins_reviewed") or joins["joins"]
        seconds = round((joins.get("profile_seconds") or 0) + (joins.get("discover_seconds") or 0))
        calibration = joins.get("calibration") or {}
        decisions = joins.get("decisions") or {}
        lines += ["", "## Join discovery", "",
                  f"- Databases: {', '.join(joins.get('databases') or [])}; "
                  f"{joins.get('profiled_columns')} columns in "
                  f"{seconds} s",
                  f"- Precision {pct(score['precision'])}, recall {pct(score['recall'])}, "
                  f"F1 {score['f1']}",
                  f"- Calibration: ECE {calibration.get('ece')}, AUC {calibration.get('auc')}, "
                  f"n={calibration.get('n')}",
                  f"- Decided without a person {pct(decisions.get('automatic'))}; accepted and "
                  f"right {pct(decisions.get('accepted_precision'))}; rejected but real "
                  f"{pct(decisions.get('rejected_misses'))}"]
    return "\n".join(lines) + "\n"


def collect(get: Callable[[str], Any]) -> dict[str, Any]:
    """Read the latest completed runs through ``get(path)`` and compute every section."""
    runs = [run for run in get("/ontology/evaluation/runs") if run["status"] == "COMPLETED"]
    sections: dict[str, Any] = {"answers": [], "effects": []}
    latest: dict[tuple[str, str], dict] = {}
    for run in runs:  # newest first
        if run["kind"] == "answers" and run["summary"].get("view_hint") is False:
            latest.setdefault((run["set_name"], run["mode"]), run)
    for (set_name, mode), run in sorted(latest.items()):
        cases = {case["id"]: case
                 for case in get(f"/ontology/evaluation/cases?set_name={set_name}")
                 if case["status"] == "ACTIVE"}
        results = {item["item"]: item
                   for item in get(f"/ontology/evaluation/runs/{run['id']}/results")}
        sections["answers"].append({
            "set": set_name, "mode": mode, "run": run["id"], "rates": kind_rates(cases, results),
            "reliability": float((run["summary"].get("reliability") or {}).get("value") or 0),
            "results": results, "cases": cases})
    by_set: dict[str, dict[str, dict]] = {}
    for row in sections["answers"]:
        by_set.setdefault(row["set"], {})[row["mode"]] = row
    for set_name, modes in sorted(by_set.items()):
        if {"with_ontology", "without_ontology"} <= set(modes):
            sections["effects"].append({"set": set_name, **paired(
                modes["with_ontology"]["cases"], modes["with_ontology"]["results"],
                modes["without_ontology"]["results"])})
    for row in sections["answers"]:
        row.pop("results")
        row.pop("cases")
    claims = next((run for run in runs if run["kind"] == "claims"), None)
    if claims:
        results = get(f"/ontology/evaluation/runs/{claims['id']}/results")
        sections["claims"] = {
            "run": claims["id"], "accuracy": claims["summary"].get("claim_accuracy"),
            "kappa": held_out_kappa((item["item"], item["reviews"]) for item in results)}
    judge = next((run for run in runs if run["kind"] == "judge"), None)
    if judge:
        cases = get(f"/ontology/evaluation/cases?set_name={judge['set_name']}"
                    f"&version={judge['version']}")
        sections["gold_judge"] = {
            "run": judge["id"], "rubric": judge["summary"].get("rubric"),
            "kappa": held_out_kappa((case["id"], case["reviews"]) for case in cases
                                    if case["status"] != "DRAFT")}
    joins = next((run for run in runs if run["kind"] == "joins"), None)
    if joins:
        sections["joins"] = {"run": joins["id"], **joins["summary"]}
    return sections


def main() -> None:  # pragma: no cover - needs a running deployment
    import httpx

    base = os.environ.get("NOVA_BENCH_URL", "http://localhost:8000").rstrip("/")
    with httpx.Client(base_url=f"{base}/api/v1", timeout=300) as http:
        login = http.post("/auth/login", json={
            "username": os.environ.get("NOVA_BENCH_USER", "nova_admin"),
            "password": os.environ["NOVA_BENCH_PASSWORD"]})
        login.raise_for_status()
        http.headers["Authorization"] = "Bearer " + login.json()["access_token"]

        def get(path: str) -> Any:
            response = http.get(path)
            response.raise_for_status()
            return response.json()

        sys.stdout.write(render(collect(get)))


if __name__ == "__main__":  # pragma: no cover
    main()
