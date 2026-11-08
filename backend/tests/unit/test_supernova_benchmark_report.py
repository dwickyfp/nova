"""The ontology benchmark report computes its numbers from stored runs only."""

from tests.benchmark.supernova_ontology.report import collect, half, held_out_kappa, render


def review(reviewer, kind, verdict):
    return {"reviewer": reviewer, "kind": kind, "verdict": verdict}


def fake_api():
    cases = [{"id": f"c{i}", "status": "ACTIVE", "kind": kind, "reviews": [
        review("judge@rubric-4", "model", "yes"), review("claude-opus-5.5", "external", "yes")]}
        for i, kind in enumerate(["answerable"] * 6 + ["ambiguous", "unanswerable"])]
    outcome = {"with_ontology": ["correct"] * 6 + ["clarified", "abstained"],
               "without_ontology": ["correct"] * 4 + ["wrong", "wrong", "answered", "answered"]}
    runs = [
        {"id": f"evn_{mode}", "kind": "answers", "status": "COMPLETED", "set_name": "s",
         "mode": mode, "summary": {"view_hint": False, "reliability": {"value": 0.5}}}
        for mode in outcome
    ] + [
        {"id": "evn_claims", "kind": "claims", "status": "COMPLETED", "set_name": "claims",
         "summary": {"claim_accuracy": {"value": 0.9, "n": 166,
                                        "interval_95": [0.85, 0.95]}}},
        {"id": "evn_judge", "kind": "judge", "status": "COMPLETED", "set_name": "s",
         "version": 1, "summary": {"rubric": "rubric-4"}},
        {"id": "evn_joins", "kind": "joins", "status": "COMPLETED", "set_name": "joins",
         "summary": {"databases": ["LAB"], "profiled_columns": 5, "profile_seconds": 1,
                     "discover_seconds": 2, "joins": {
                         "precision": {"value": 1.0, "successes": 2, "n": 2,
                                       "interval_95": [0.3, 1.0]},
                         "recall": {"value": 1.0, "successes": 2, "n": 2,
                                    "interval_95": [0.3, 1.0]}, "f1": 1.0}}},
    ]

    def get(path):
        if path == "/ontology/evaluation/runs":
            return runs
        if path.startswith("/ontology/evaluation/cases"):
            return cases
        run_id = path.split("/")[4]
        if run_id == "evn_claims":
            return [{"item": "a", "outcome": "yes", "reviews": cases[0]["reviews"]}]
        mode = run_id.removeprefix("evn_")
        return [{"item": f"c{i}", "outcome": value} for i, value in enumerate(outcome[mode])]

    return get


def test_the_report_states_rates_paired_effects_kappa_and_joins():
    sections = collect(fake_api())
    rows = {row["mode"]: row for row in sections["answers"]}
    assert rows["with_ontology"]["rates"]["answerable"]["successes"] == 6
    assert rows["without_ontology"]["rates"]["ambiguous"]["successes"] == 0
    effect = sections["effects"][0]
    assert effect["mcnemar"]["gained"] == 2 and effect["mcnemar"]["lost"] == 0
    text = render(sections)
    assert "| s | with_ontology |" in text and "2 fixed, 0 broken" in text
    assert "## Join discovery" in text and "F1 1.0" in text
    assert "Rubric rubric-4" in text
    # A stratified claim estimate has no count of successes.
    assert "Accuracy by consensus: 90.0% (85.0–95.0%), n=166" in text


def test_kappa_is_reported_per_deterministic_half():
    assert half("c1") in {"dev", "test"} and half("c1") == half("c1")
    items = [(f"k{i}", [review("m@r", "model", "yes" if i % 3 else "no"),
                        review("claude-opus-5.5", "external", "yes" if i % 3 else "no")])
             for i in range(40)]
    kappa = held_out_kappa(items)
    assert kappa["dev"]["kappa"] == 1.0 and kappa["test"]["kappa"] == 1.0
    assert kappa["dev"]["n"] + kappa["test"]["n"] == 40
