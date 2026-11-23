"""Load the committed corpus and its ground truth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
#: Expected outcomes. ``approval`` is detected but never expected: Studio shows
#: no approval cards (owner decision, 2026-11-23).
OUTCOMES = {"answer", "clarify", "refuse", "limitation", "cancelled"}
NUMERIC_MODES = {"all", "first", "none"}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def questions(directory: Path = CORPUS_DIR) -> list[dict[str, Any]]:
    return _jsonl(directory / "questions.jsonl")


def sessions(directory: Path = CORPUS_DIR) -> list[dict[str, Any]]:
    return _jsonl(directory / "sessions.jsonl")


def ground_truth(directory: Path = CORPUS_DIR) -> dict[str, Any]:
    path = directory / "ground_truth.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def units(directory: Path = CORPUS_DIR) -> list[dict[str, Any]]:
    """Every gradable turn: single questions, session turns and their fresh twins.

    A session turn's ``fresh`` text is the same request written to stand alone;
    it runs in a new thread so the session's context carry-over can be compared
    with a fresh start.
    """
    out: list[dict[str, Any]] = []
    for item in questions(directory):
        out.append({**item, "unit": "single"})
    for session in sessions(directory):
        for index, step in enumerate(session["turns"], start=1):
            base = {
                **step,
                "category": f"session:{session['kind']}",
                "session_id": session["id"],
                "turn_index": index,
                "difficulty": step.get("difficulty", "medium"),
                "mode": "chat",
                "corpus_version": session["corpus_version"],
            }
            out.append({**base, "id": f"{session['id']}.t{index}", "unit": "session_turn"})
            if step.get("fresh"):
                fresh = {k: v for k, v in base.items() if k not in {"fresh", "stop_after_s"}}
                out.append({**fresh, "id": f"{session['id']}.t{index}.fresh",
                            "question": step["fresh"], "unit": "fresh_twin",
                            "twin_of": f"{session['id']}.t{index}"})
    return out


def validate(directory: Path = CORPUS_DIR) -> list[str]:
    """Problems with the corpus's shape; empty when it is valid."""
    problems: list[str] = []
    seen: set[str] = set()
    for unit in units(directory):
        uid = unit["id"]
        if uid in seen:
            problems.append(f"duplicate id {uid}")
        seen.add(uid)
        if unit.get("expect_outcome") not in OUTCOMES:
            problems.append(f"{uid}: unknown outcome {unit.get('expect_outcome')}")
        if unit.get("lang") not in {"en", "id"}:
            problems.append(f"{uid}: language must be en or id")
        if not str(unit.get("question") or "").strip():
            problems.append(f"{uid}: empty question")
        gt = unit.get("gt")
        if gt:
            numeric = str(gt.get("numeric", "all"))
            if numeric not in NUMERIC_MODES and not numeric.startswith("col:"):
                problems.append(f"{uid}: unknown numeric mode {numeric}")
            sql = str(gt.get("sql") or "").lstrip().upper()
            if not sql.startswith("SELECT"):
                problems.append(f"{uid}: ground truth must be one SELECT")
    for session in sessions(directory):
        if not 3 <= len(session["turns"]) <= 6:
            problems.append(f"{session['id']}: a session has 3 to 6 turns")
    return problems
