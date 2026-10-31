"""Summarise recorded model calls per Smart run, or compare two measurements.

    cd backend && uv run python -m tests.benchmark.smart_tokens.report \\
        RUNS.json CALLS.jsonl [--json SUMMARY.json]
    cd backend && uv run python -m tests.benchmark.smart_tokens.report \\
        --compare BEFORE.json AFTER.json

A call belongs to a run when it started inside the run's time window, so run
one request at a time. "Reported" numbers are the provider's usage; "parts"
are Nova's own estimate (characters / 4) of what each prompt was made of.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

#: Grace after a run's end for calls whose records land just after it.
_GRACE_SECONDS = 5.0


def _calls_for(run: dict[str, Any], calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start, end = run["started"], run["finished"] + _GRACE_SECONDS
    return [call for call in calls if start <= call["at"] - call["seconds"] <= end]


def _reported(call: dict[str, Any], key: str) -> int:
    return int((call.get("usage") or {}).get(key) or 0)


def summarise_run(run: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    mine = _calls_for(run, calls)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in mine:
        group = "root" if call["participant"] == "/root" else (
            "specialists" if call["participant"].startswith("/root/") else "internal"
        )
        groups[group].append(call)
    by_group = {
        name: {
            "calls": len(items),
            "prompt_tokens": sum(_reported(c, "prompt_tokens") for c in items),
            "completion_tokens": sum(_reported(c, "completion_tokens") for c in items),
            "seconds": round(sum(c["seconds"] for c in items), 1),
        }
        for name, items in sorted(groups.items())
    }
    root_parts: dict[str, int] = defaultdict(int)
    for call in groups.get("root", []):
        for part, value in call["parts"].items():
            root_parts[part] += value
    root_calls = [
        {
            "purpose": call["purpose"],
            "offered_tools": len(call["tools"]),
            "called": call["tool_calls"],
            "prompt_tokens": _reported(call, "prompt_tokens"),
            "completion_tokens": _reported(call, "completion_tokens"),
            "estimated_prompt": sum(call["parts"].values()),
            "largest_parts": sorted(call["parts"].items(), key=lambda kv: -kv[1])[:4],
        }
        for call in groups.get("root", [])
    ]
    total_prompt = sum(_reported(c, "prompt_tokens") for c in mine)
    total_completion = sum(_reported(c, "completion_tokens") for c in mine)
    return {
        "request": run["request"],
        "run_id": run["run_id"],
        "status": run["status"],
        "seconds": run["seconds"],
        "calls": len(mine),
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "total_tokens": total_prompt + total_completion,
        "groups": by_group,
        "root_parts": dict(sorted(root_parts.items(), key=lambda kv: -kv[1])),
        "root_calls": root_calls,
    }


def summarise(runs_file: Path, calls_file: Path) -> dict[str, Any]:
    runs = json.loads(runs_file.read_text())["runs"]
    calls = [json.loads(line) for line in calls_file.read_text().splitlines() if line.strip()]
    per_run = [summarise_run(run, calls) for run in runs]
    finished = [run for run in per_run if run["status"] == "completed"]

    # Failed runs count too: a change that only makes failures cheaper is no saving.
    def avg(key: str, group: str | None = None) -> float:
        values = [
            (run["groups"].get(group, {}).get(key, 0) if group else run[key]) for run in per_run
        ]
        return round(mean(values), 1) if values else 0.0

    return {
        "runs": per_run,
        "completed": len(finished),
        "attempted": len(per_run),
        "mean": {
            "seconds": avg("seconds"),
            "calls": avg("calls"),
            "total_tokens": avg("total_tokens"),
            "root_prompt_tokens": avg("prompt_tokens", "root"),
            "root_calls": avg("calls", "root"),
            "specialist_prompt_tokens": avg("prompt_tokens", "specialists"),
            "internal_prompt_tokens": avg("prompt_tokens", "internal"),
        },
    }


def render(summary: dict[str, Any]) -> str:
    lines = [f"Completed {summary['completed']} of {summary['attempted']} runs.", ""]
    lines += ["| Mean over all runs | Value |", "|---|---:|"]
    lines += [f"| {key} | {value:,} |" for key, value in summary["mean"].items()]
    for run in summary["runs"]:
        lines += ["", f"### {run['request']} · {run['status']} · {run['seconds']} s",
                  f"{run['calls']} calls, {run['total_tokens']:,} tokens "
                  f"({run['prompt_tokens']:,} prompt, {run['completion_tokens']:,} completion)", "",
                  "| Group | Calls | Prompt | Completion | Seconds |", "|---|---:|---:|---:|---:|"]
        lines += [f"| {name} | {g['calls']} | {g['prompt_tokens']:,} | "
                  f"{g['completion_tokens']:,} | {g['seconds']} |"
                  for name, g in run["groups"].items()]
        lines += ["", "Root prompt, estimated by part (all root calls):", "",
                  "| Part | Tokens |", "|---|---:|"]
        lines += [f"| {part} | {value:,} |" for part, value in run["root_parts"].items()]
        lines += ["", "| # | Purpose | Tools offered | Called | Prompt | Completion |",
                  "|---:|---|---:|---|---:|---:|"]
        lines += [f"| {index} | {call['purpose']} | {call['offered_tools']} | "
                  f"{', '.join(call['called']) or 'answer'} | {call['prompt_tokens']:,} | "
                  f"{call['completion_tokens']:,} |"
                  for index, call in enumerate(run["root_calls"], 1)]
    return "\n".join(lines)


def compare(before: dict[str, Any], after: dict[str, Any]) -> str:
    lines = ["| Mean over all runs | Before | After | Change |", "|---|---:|---:|---:|"]
    for key, old in before["mean"].items():
        new = after["mean"].get(key, 0.0)
        change = f"{(new - old) / old * 100:+.0f}%" if old else "n/a"
        lines.append(f"| {key} | {old:,} | {new:,} | {change} |")
    lines.append(f"| completed runs | {before['completed']}/{before['attempted']} | "
                 f"{after['completed']}/{after['attempted']} | |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="?", type=Path)
    parser.add_argument("calls", nargs="?", type=Path)
    parser.add_argument("--json", type=Path, help="write the summary here")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()
    if args.compare:
        before, after = (json.loads(path.read_text()) for path in args.compare)
        print(compare(before, after))
        return
    if not (args.runs and args.calls):
        parser.error("give RUNS.json and CALLS.jsonl, or --compare BEFORE AFTER")
    summary = summarise(args.runs, args.calls)
    if args.json:
        args.json.write_text(json.dumps(summary, indent=1))
    print(render(summary))


if __name__ == "__main__":
    main()
