"""Corpus v3: typed units, their ground truth and the corpus linter (DESIGN §7).

    cd backend && uv run python -m tests.benchmark.studio_capability.v2.corpus_v3 [--offline]

The linter runs without a model. A live run is blocked when it reports a
problem (``run.py`` calls :func:`lint` with ``for_run=True``). ``--offline``
lints for offline re-scoring only: references imported from the v1 ground
truth or still pending are allowed there and reported, never silently used for
a live run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.benchmark.studio_capability.v2 import ui_tree
from tests.benchmark.studio_capability.v2.expectation import OWNER_RULES, Expectation

V3_DIR = Path(__file__).resolve().parents[1] / "corpus" / "v3"
UNITS = "units.jsonl"
GROUND_TRUTH = "ground_truth.json"
SEMANTIC_MODEL = "semantic_model.json"
COMPUTED = "nova_sql"     # executed through Nova's SQL route as admin and as the user


def sql_key(sql: str) -> str:
    """A stable reference id: the SHA-256 of the statement with outer whitespace trimmed."""
    return hashlib.sha256(sql.strip().rstrip(";").strip().encode()).hexdigest()[:16]


def load_units(directory: Path = V3_DIR) -> list[Expectation]:
    path = directory / UNITS
    return [Expectation.model_validate_json(line) for line in path.read_text().splitlines()
            if line.strip()]


def load_ground_truth(directory: Path = V3_DIR) -> dict[str, Any]:
    path = directory / GROUND_TRUTH
    return json.loads(path.read_text()) if path.is_file() else {}


def semantic_names(directory: Path = V3_DIR) -> set[str]:
    path = directory / SEMANTIC_MODEL
    if not path.is_file():
        return set()
    model = json.loads(path.read_text())
    return {str(m) for m in model.get("metrics") or []} | {
        str(d) for d in model.get("dimensions") or []}


@dataclass
class LintReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_select(sql: str) -> bool:
    text = sql.strip().rstrip(";").strip()
    return ";" not in text and text[:6].upper() == "SELECT"


def _contains_figure(value: Any) -> bool:
    """A number typed into an expectation (only reference queries may hold figures)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    if isinstance(value, list):
        return any(_contains_figure(v) for v in value)
    if isinstance(value, dict):
        return any(_contains_figure(v) for v in value.values())
    return False


#: Expectation fields that may hold numbers that are not expected figures.
_STRUCTURAL_NUMBERS = {"turn_index", "tolerance_rel", "row", "stop_after_s"}


def _figures(model: Any, path: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(model, dict):
        for key, value in model.items():
            if key in _STRUCTURAL_NUMBERS:
                continue
            out.extend(_figures(value, f"{path}.{key}" if path else key))
    elif isinstance(model, list):
        for index, value in enumerate(model):
            out.extend(_figures(value, f"{path}[{index}]"))
    elif _contains_figure(model):
        out.append(path)
    return out


def lint(units: list[Expectation] | None = None, ground_truth: dict[str, Any] | None = None, *,
         semantic: set[str] | None = None, for_run: bool = True,
         directory: Path = V3_DIR) -> LintReport:
    """Rules 1-8 of DESIGN §7.2."""
    units = units if units is not None else load_units(directory)
    gt = ground_truth if ground_truth is not None else load_ground_truth(directory)
    semantic = semantic if semantic is not None else semantic_names(directory)
    report = LintReport()
    block = report.errors.append
    by_id = {u.id: u for u in units}
    if len(by_id) != len(units):
        block("duplicate unit ids")
    versions = {u.corpus_version for u in units}
    if len(versions) != 1:
        block(f"units carry several corpus versions: {sorted(versions)}")
    version = next(iter(versions)) if versions else None
    references = gt.get("references") or {}
    if gt.get("corpus_version") != version:  # rule 5
        block(f"ground truth is {gt.get('corpus_version')}, units are {version}")
    if not gt.get("data_fingerprint"):
        block("ground truth has no data fingerprint")
    stale = sorted({k for k, r in references.items() if r.get("computed_by") != COMPUTED})
    if stale:
        message = (f"{len(stale)} references not computed through Nova for this version "
                   f"(imported or pending); run v2.ground_truth --compute")
        (block if for_run else report.warnings.append)(message)

    for unit in units:
        uid = unit.id
        for path in _figures(unit.model_dump(mode="json")):  # rule 8
            block(f"{uid}: literal number in the expectation at {path}")
        unknown = [k for k in unit.ui.kinds + unit.ui.absent if k not in ui_tree.UI_KINDS]
        if unknown:  # rule 7
            block(f"{uid}: UI kinds the tree grader does not know: {unknown}")
        for rule in unit.owner_rules:
            if rule not in OWNER_RULES:
                block(f"{uid}: unknown owner rule {rule}")
        if unit.outcome == "clarify" and unit.clarify == "never":
            block(f"{uid}: a clarify unit says whether it must ask or may assume")
        if unit.unit == "session_turn" and not unit.session_id:
            block(f"{uid}: a session turn names its session")
        spec = unit.numeric
        if spec is None:
            continue
        if unit.outcome != "answer":
            block(f"{uid}: numeric expectations belong to answers")
        for sql in [spec.reference_sql, *spec.alternatives]:  # rule 1
            if not _is_select(sql):
                block(f"{uid}: a reference is one SELECT")
                continue
            ref = references.get(sql_key(sql))
            if ref is None:
                (block if for_run else report.warnings.append)(
                    f"{uid}: reference {sql_key(sql)} not computed")
                continue
            result = ref.get("user") or {}
            if "error" in result or not result.get("rows"):
                block(f"{uid}: reference {sql_key(sql)} is empty or fails for the user")
                continue
            if sql == spec.reference_sql:  # rule 2
                missing = [c for c in spec.required_measures + spec.key_columns
                           if c not in result.get("columns", [])]
                if missing:
                    block(f"{uid}: columns not in the reference result: {missing}")
        for bind in spec.bind:  # rule 4
            source = by_id.get(bind.source)
            if source is None or source.numeric is None:
                block(f"{uid}: binds to {bind.source}, which has no reference")
                continue
            ref = references.get(sql_key(source.numeric.reference_sql)) or {}
            rows = (ref.get("user") or {}).get("rows") or []
            if ref and len(rows) <= bind.row:
                block(f"{uid}: {bind.source} has no row {bind.row} to bind")
        if semantic:  # rule 3
            absent = [m for m in spec.semantic_measures if m not in semantic]
            if absent and unit.deferred:
                report.warnings.append(f"{uid}: deferred ({unit.deferred}); needs {absent}")
            elif absent:
                block(f"{uid}: answer needs {absent} that the semantic model lacks; "
                      "expect limitation/catalog_unsupported or defer the unit")
    _owner_rules_consistent(units, report)  # rule 6
    return report


def _owner_rules_consistent(units: list[Expectation], report: LintReport) -> None:
    """One owner rule, one expectation: every unit citing it expects the same outcome."""
    seen: dict[str, set[str]] = {}
    for unit in units:
        for rule in unit.owner_rules:
            seen.setdefault(rule, set()).add(unit.outcome)
    expected = {"autonomous_actions": {"action_done"}, "save_from_chat": {"limitation"}}
    for rule, outcomes in seen.items():
        if rule in expected and outcomes != expected[rule]:
            report.errors.append(f"owner rule {rule}: units expect {sorted(outcomes)}, "
                                 f"the rule says {sorted(expected[rule])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true",
                        help="lint for offline re-scoring (imported references allowed)")
    args = parser.parse_args()
    report = lint(for_run=not args.offline)
    for line in report.warnings:
        print(f"warning: {line}")
    for line in report.errors:
        print(f"error: {line}")
    print(f"{len(load_units())} units; {len(report.errors)} errors, "
          f"{len(report.warnings)} warnings")
    sys.exit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
