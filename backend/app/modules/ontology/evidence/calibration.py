"""A calibrated probability that a proposed join is right, measured from labelled joins.

Labels are the joins the published Semantic Views declare, in their direction,
and the proposals reviewers confirmed or rejected; every other candidate is a
negative. A candidate's probability is the measured precision of its evidence
class (histogram binning, Zadrozny and Elkan 2001): the Jeffreys posterior mean
of how often labelled candidates with the same evidence were right. The class
is whether the decision rule chose the candidate, whether the column names the
target table, and whether both columns share an id-like name.

This replaced a logistic regression after a measured comparison on two lab
databases, leave-one-database-out: the evidence classes ranked better (AUC
0.994 against 0.946), were better calibrated (ECE 0.020 against 0.035) and
their automatic decisions made no error, while the regression's continuous
features (distinct counts, sizes) did not transfer between databases.

Quality is always measured on data the model did not see: each database is
scored from the other databases' labels. Because the probability is
calibrated, the decision thresholds are the targets themselves: accept alone at
≥ 0.97, reject alone at ≤ 0.01, and send everything between to review.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

#: Below this many labelled positives no probability is reported at all.
MIN_POSITIVES = 10
#: The precision the machine must keep on what it accepts alone (proposal §5.4).
AUTO_PRECISION = 0.97
#: Rejected alone: the probability that a rejected candidate was right stays at most 1%.
AUTO_MISS = 0.01
#: Jeffreys prior: half a right and half a wrong join before any evidence.
PRIOR = 0.5

Evidence = tuple[bool, bool, bool]


def evidence_class(detail: dict[str, Any]) -> Evidence:
    return (bool(detail.get("proposed")), bool(detail.get("names_target_table")),
            bool(detail.get("same_name")))


def describe(evidence: Evidence) -> str:
    chosen, names, same = evidence
    parts = ["chosen by the rule" if chosen else "not chosen by the rule",
             "column names the target table" if names else "column does not name the table",
             "same id-like name" if same else "different names"]
    return ", ".join(parts)


class EvidenceModel:
    """Measured precision per evidence class."""

    def __init__(self, details: Sequence[dict[str, Any]], labels: Sequence[int]) -> None:
        self.counts: dict[Evidence, list[int]] = defaultdict(lambda: [0, 0])
        for detail, value in zip(details, labels, strict=True):
            counts = self.counts[evidence_class(detail)]
            counts[0] += int(value)
            counts[1] += 1

    def probability(self, detail: dict[str, Any]) -> float:
        right, total = self.counts.get(evidence_class(detail), [0, 0])
        return (right + PRIOR) / (total + 2 * PRIOR)

    def explain(self, detail: dict[str, Any]) -> dict[str, Any]:
        evidence = evidence_class(detail)
        right, total = self.counts.get(evidence, [0, 0])
        return {"evidence": describe(evidence), "right": right, "labelled": total}

    def as_dict(self) -> dict[str, Any]:
        return {"classes": [
            {"evidence": describe(key), "right": value[0], "labelled": value[1]}
            for key, value in sorted(self.counts.items(), reverse=True)
        ]}


def auc(pairs: Sequence[tuple[float, int]]) -> float | None:
    """Probability that a random right join outranks a random wrong one (ties count half)."""
    positives = [p for p, y in pairs if y]
    negatives = [p for p, y in pairs if not y]
    if not positives or not negatives:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return round(wins / (len(positives) * len(negatives)), 4)


def cross_validated(
    details: Sequence[dict[str, Any]], labels: Sequence[int], groups: Sequence[str],
) -> dict[str, Any] | None:
    """Leave-one-group-out probabilities and their calibration, or None without enough labels."""
    from app.common.statistics import calibration

    if sum(labels) < MIN_POSITIVES or len(set(groups)) < 2:
        return None
    predictions = [0.0] * len(details)
    for group in sorted(set(groups)):
        train = [i for i, g in enumerate(groups) if g != group]
        model = EvidenceModel([details[i] for i in train], [labels[i] for i in train])
        for i, g in enumerate(groups):
            if g == group:
                predictions[i] = model.probability(details[i])
    pairs = list(zip(predictions, labels, strict=True))
    measured = calibration([(p, bool(y)) for p, y in pairs])
    return {"probabilities": predictions, "ece": round(measured["ece"], 4),
            "brier": round(measured["brier"], 4), "auc": auc(pairs),
            "bins": measured["bins"], "n": len(pairs), "positives": sum(labels),
            "groups": sorted(set(groups))}


def decide(probability: float) -> str:
    if probability >= AUTO_PRECISION:
        return "accept"
    if probability <= AUTO_MISS:
        return "reject"
    return "review"


def label(detail: dict[str, Any], verdict: str | None) -> int | None:
    """A declared join is right in its direction; a reviewed candidate is what reviewers said;
    an unreviewed proposal outside the declared joins is unknown; any other candidate is
    wrong, including the reverse direction of a declared join."""
    if verdict in {"yes", "no"}:
        return int(verdict == "yes")
    if detail.get("declared_direction"):
        return 1
    if detail.get("proposed") and not detail.get("declared"):
        return None
    return 0


def calibrate(
    details: Sequence[dict[str, Any]], verdicts: Sequence[str | None],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Probabilities, decisions and their quality measured on databases that set nothing."""
    from app.common.statistics import proportion

    labels = [label(item, verdict) for item, verdict in zip(details, verdicts, strict=True)]
    groups = [str(item.get("source", "")).split(".", 1)[0] for item in details]
    known = [i for i, value in enumerate(labels) if value is not None]
    known_labels = [int(labels[i] or 0) for i in known]
    measured = cross_validated([details[i] for i in known], known_labels,
                               [groups[i] for i in known])
    if measured is None:
        return [dict(item) for item in details], {"calibration": None, "decisions": None}
    held_out = dict(zip(known, measured.pop("probabilities"), strict=True))
    model = EvidenceModel([details[i] for i in known], known_labels)
    out = [
        {**item, "probability": round(model.probability(item), 4),
         "decision": decide(model.probability(item)), "label": labels[i],
         "explanation": model.explain(item)}
        for i, item in enumerate(details)
    ]
    # Decisions are judged with held-out probabilities: what the machine would have done
    # on a database whose labels it never saw.
    decided = {i: decide(p) for i, p in held_out.items()}
    accepted = [i for i, choice in decided.items() if choice == "accept"]
    rejected = [i for i, choice in decided.items() if choice == "reject"]
    return out, {
        "calibration": measured,
        "model": model.as_dict(),
        "thresholds": {"accept": AUTO_PRECISION, "reject": AUTO_MISS},
        "decisions": {
            "labelled": len(known),
            "automatic": proportion(len(accepted) + len(rejected), len(known)),
            "accepted_precision": proportion(sum(int(labels[i] or 0) for i in accepted),
                                             len(accepted)),
            "rejected_misses": proportion(sum(int(labels[i] or 0) for i in rejected),
                                          len(rejected)),
            "review_queue": sum(1 for item in out if item["decision"] == "review"),
        },
    }
