"""Ask the running backend every case as the lab user and keep what a user would see.

    cd backend && uv run python -m tests.benchmark.smart_native.run OUT.json [case ...]

``--direct`` also asks the single specialists the cases name, as a baseline, and
``--workers N`` asks N cases at once. Needs the backend and ``app.agent_worker``.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from tests.benchmark.smart_native.cases import CASES, Case
from tests.benchmark.smart_native.lab import SMART, ask, login, read_state

ARTIFACTS = ("table", "chart", "automation_proposal")


def seen(result: dict) -> dict:
    """The answer, the artifacts shown with it, and any error the user met."""
    frames = [frame for frame in result["frames"] if isinstance(frame["data"], dict)]
    answer = next((frame["data"].get("answer") for frame in reversed(frames)
                   if frame["event"] == "agent_completed"), None)
    if answer is None:
        answer = "".join(frame["data"].get("delta") or frame["data"].get("text") or ""
                         for frame in frames if frame["event"] == "text_delta")
    shown = ""
    for frame in frames:
        table = frame["data"]
        if frame["event"] == "table" and table.get("rows"):
            shown += "\n" + " | ".join(map(str, table.get("columns", [])))
            shown += "".join("\n" + " | ".join(map(str, row)) for row in table["rows"][:30])
        if frame["event"] == "chart":
            # A chart draws its figures: they are stated, though not in the prose.
            shown += "\n[chart] " + json.dumps(table, ensure_ascii=False, default=str)[:6000]
    tools, calls = [], set()
    for frame in frames:
        call = frame["data"].get("tool_call_id")
        if frame["data"].get("tool_name") and call and call not in calls:
            calls.add(call)
            tools.append(frame["data"]["tool_name"])
    done = next((frame["data"] for frame in reversed(frames) if frame["event"] == "done"), {})
    return {
        "answer": answer or "", "shown": shown.strip(), "tools": tools,
        "seconds": result["seconds"],
        "artifacts": sorted({frame["event"] for frame in frames
                             if frame["event"] in ARTIFACTS}),
        # A specialist error the root recovered from never reaches this stream.
        "errors": [json.dumps(frame["data"], ensure_ascii=False)[:300]
                   for frame in frames if frame["event"] == "error"],
        "finish_reason": done.get("finish_reason"), "run_id": result.get("run_id"),
        "thread_id": result["thread_id"],
    }


def converse(case: Case, agent_id: str) -> list[dict]:
    thread, turns = None, []
    for question in case.turns:
        try:
            raw = ask(agent_id, question, thread_id=thread)
        except Exception as exc:  # noqa: BLE001 - a failed turn is a result, not a crash
            turns.append({"question": question, "answer": "",
                          "errors": [f"{type(exc).__name__}: {exc}"[:300]]})
            break
        thread = raw["thread_id"]
        turns.append({"question": question, **seen(raw)})
    return turns


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("cases", nargs="*")
    parser.add_argument("--direct", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    options = parser.parse_args()
    ids = read_state("ids.json")
    agents = {"smart": SMART, "finance": ids["finance_agent"], "hr": ids["hr_agent"]}
    login()
    results, lock = {}, Lock()
    jobs = [(case, target) for case in CASES if not options.cases or case.id in options.cases
            for target in ("smart", *(case.direct if options.direct else ()))]

    def one(job: tuple[Case, str]) -> None:
        case, target = job
        turns = converse(case, agents[target])
        with lock:
            results[f"{case.id}:{target}"] = {
                "case": case.id, "target": target, "gold": list(case.gold),
                "artifacts": list(case.artifacts), "expect": case.expect,
                "reference": list(case.reference),
                "questions": list(case.turns), "turns": turns,
            }
            options.out.write_text(json.dumps(results, indent=1, ensure_ascii=False))
        last = turns[-1]
        print(f"{case.id}:{target} {last.get('seconds')}s tools={last.get('tools')} "
              f"errors={len(last.get('errors') or [])}", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, options.workers)) as pool:
        list(pool.map(one, jobs))


if __name__ == "__main__":
    main()
