"""Frozen, stratified development subsets for paired live experiments."""

from __future__ import annotations

import hashlib
import json

from tests.benchmark.jev_multidomain.environment import ARTIFACTS


def experiment_plan() -> dict:
    ground = json.loads((ARTIFACTS / "ground_truth.json").read_text())
    allocations = {
        "explicit_finance": 1,
        "explicit_marketing": 1,
        "semantic_finance": 1,
        "semantic_marketing": 1,
        "ambiguous": 2,
        "cross_domain": 2,
        "adversarial": 2,
        "natural": 1,
        "general": 1,
    }
    selected = []
    for category, count in allocations.items():
        candidates = [
            c for c in ground["cases"] if c["split"] == "dev" and c["category"] == category
        ]
        candidates.sort(
            key=lambda c: hashlib.sha256(("robustness-v1:" + c["id"]).encode()).digest()
        )
        selected.extend(candidates[:count])
    hard = [
        c["id"] for c in selected if c["category"] in {"ambiguous", "cross_domain", "adversarial"}
    ]
    ids = [c["id"] for c in selected]
    return {
        "version": 1,
        "ground_truth_sha256": ground["sha256"],
        "selection": "First SHA256(robustness-v1:case_id) within each declared DEV category; "
        "no outputs used",
        "paired_case_ids": ids,
        "repeat_case_ids": hard,
        "experiments": [
            {"name": "baseline-off", "arm": "off", "variant": "full", "ids": ids},
            {"name": "controlled-on", "arm": "controlled", "variant": "full", "ids": ids},
            {"name": "repeat-2", "arm": "on", "variant": "full", "ids": hard},
            {"name": "repeat-3", "arm": "on", "variant": "full", "ids": hard},
            *[
                {"name": f"variant-{variant}", "arm": "on", "variant": variant, "ids": ids}
                for variant in ["reverse", "description", "none", "minimal", "sales"]
            ],
        ],
        "caveats": [
            "Twelve cases cannot establish a population order-sensitivity bound below 2%.",
            "Only controlled-on vs baseline-off holds the answer model constant.",
            "Registration reversal may be canonicalized before Jev; "
            "separate probes reverse Jev input directly.",
            "Sales is enabled only after the complete primary run.",
        ],
    }


def freeze_plan() -> dict:
    plan = experiment_plan()
    target = ARTIFACTS / "experiments.json"
    if target.exists() and json.loads(target.read_text()) != plan:
        raise ValueError("The experiment selection is already frozen")
    target.write_text(json.dumps(plan, indent=2))
    return plan
