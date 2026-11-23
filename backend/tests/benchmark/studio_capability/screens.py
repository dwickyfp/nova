"""Choose which recorded turns to screenshot, then take the screenshots.

    cd backend && uv run python -m tests.benchmark.studio_capability.screens plan [--min 40]
    cd backend && uv run python -m tests.benchmark.studio_capability.screens shoot

The sample covers every category, every session's last turn, and at least
``--min`` turns in all, preferring turns whose checks found a problem and
turns that show different components. ``shoot`` runs
``frontend/scripts/studio-capability-screens.mjs`` (Playwright, light theme,
1440 px wide) with the user's credentials passed through the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from tests.benchmark.studio_capability import settings


def load_turns(out: Path) -> list[dict[str, Any]]:
    turns = []
    for path in sorted((out / "turns").glob("*.json")):
        record = json.loads(path.read_text())
        record.pop("events", None)
        turns.append(record)
    return turns


def _interest(record: dict[str, Any]) -> tuple[int, int]:
    checks = record["checks"]
    problems = int(not checks["outcome_ok"]) + int((checks["ui_recall"] or 1) < 1) + int(
        (checks["numeric"]["recall"] if checks["numeric"]["recall"] is not None else 1) < 1)
    return problems, len(checks["elements"])


def choose(turns: list[dict[str, Any]], minimum: int) -> list[dict[str, Any]]:
    eligible = [t for t in turns if t.get("thread_id") and t["unit"].get("mode") != "deep_research"]
    chosen: dict[str, dict[str, Any]] = {}
    last: dict[str, dict[str, Any]] = {}
    for record in eligible:
        sid = record["unit"].get("session_id")
        if record["unit"]["unit"] == "session_turn" and sid and (
                sid not in last or record["unit"]["turn_index"] > last[sid]["unit"]["turn_index"]):
            last[sid] = record
    chosen.update({r["id"]: r for r in last.values()})
    by_category: dict[str, list[dict[str, Any]]] = {}
    for record in eligible:
        if record["unit"]["unit"] == "single":
            by_category.setdefault(record["unit"]["category"], []).append(record)
    for records in by_category.values():
        best = max(records, key=_interest)
        chosen.setdefault(best["id"], best)
    rest = sorted((r for r in eligible if r["id"] not in chosen and r["unit"]["unit"] == "single"),
                  key=_interest, reverse=True)
    singles = sum(1 for r in chosen.values() if r["unit"]["unit"] == "single")
    for record in rest:
        if singles >= minimum:
            break
        chosen[record["id"]] = record
        singles += 1
    return list(chosen.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["plan", "shoot"])
    parser.add_argument("--out")
    parser.add_argument("--min", type=int, default=40, help="minimum single-question turns")
    args = parser.parse_args()
    out = settings.out_dir(args.out)
    plan_path = out / "screens" / "plan.json"
    if args.action == "plan":
        picked = choose(load_turns(out), args.min)
        plan = [{"id": r["id"], "agent_id": r["agent"]["agent_id"], "thread_id": r["thread_id"],
                 "file": str(out / "screens" / f"{r['id']}.png")} for r in picked]
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(plan, indent=1))
        print(f"{len(plan)} screenshots planned in {plan_path}")
        return
    identity = settings.user()
    env = {**os.environ, "NOVA_BENCH_UI": settings.ui(), "NOVA_BENCH_USER": identity.username,
           "NOVA_BENCH_PASSWORD": identity.password}
    subprocess.run(["node", "scripts/studio-capability-screens.mjs", str(plan_path)],
                   cwd=settings.REPO / "frontend", env=env, check=True)


if __name__ == "__main__":
    main()
