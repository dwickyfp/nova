"""Run the Studio capability corpus against a live Nova, as the user under test.

    cd backend && NOVA_BENCH_PASSWORD=... NOVA_BENCH_OUT=/path/outside/repo \\
        uv run python -m tests.benchmark.studio_capability.run [--ids a,b] [--limit N]

Each unit is one real Studio turn over HTTP: sign in, the named agent, a new
thread (or the session's thread), the protocol-v2 SSE stream. Per turn the
runner stores the full event journal with arrival times, the folded view of
what the user saw, the deterministic checks, the human-input answers it gave,
and whether the stored journal replays to the same parts (reload).

The run is resumable: a finished unit is skipped, and an interrupted session
continues in its own thread from its next turn. Turns are paced (``--pause``)
and at most two run at once; sessions run their turns in order. The run stops
after ``--max-failures`` consecutive transport failures so a dying stack is
not hammered. No credential is printed or stored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from tests.benchmark.studio_capability import corpus, fixtures, settings
from tests.benchmark.studio_capability.checks import check
from tests.benchmark.studio_capability.client import StudioClient, TurnResult, with_retry
from tests.benchmark.studio_capability.journal import Part, TurnView, fold
from tests.benchmark.studio_capability.v2 import adapters, corpus_v3
from tests.benchmark.studio_capability.v2.budget import Budget, Estimates

DATA_START, DATA_END = "2025-06-01", "2025-07-05"


# ── human-in-the-loop answers ──────────────────────────────────────────

def hitl_policy(unit: dict[str, Any]):  # noqa: ANN201 - returns a client.HitlPolicy
    """Answer like a cooperative person, and deny every action by default.

    ``first`` answers a question with its first choice, picks the suggested (or
    first) option and fills a form with the first value or the data's range.
    ``deny`` declines questions and denies approvals. An approval is allowed
    only when the unit says ``allow``.
    """
    policy = str((unit.get("hitl") or {}).get("policy") or "first")
    text = (unit.get("hitl") or {}).get("text")

    def decide(kind: str, request: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        if kind == "approval":
            return ("allow_once", None) if policy == "allow" else ("deny", None)
        if policy == "deny":
            return "decline", None
        if kind == "question":
            choices = request.get("choices") or []
            answer = text or (choices[0] if choices else "Use revenue, all dates, all cities.")
            return "answer", {"text": str(answer)}
        if kind == "choice":
            options = request.get("options") or []
            chosen = next((o for o in options if o.get("recommended")),
                          options[0] if options else None)
            return ("answer", {"option_id": chosen["id"]}) if chosen else ("decline", None)
        if kind == "form":
            values: dict[str, Any] = {}
            for field in request.get("fields") or []:
                widget = field.get("widget")
                if widget == "select" and field.get("values"):
                    values[field["name"]] = field["values"][0]
                elif widget == "date":
                    values[field["name"]] = DATA_START
                elif widget == "date_range":
                    values[field["name"]] = {"start": DATA_START, "end": DATA_END}
                elif widget in {"number", "integer"}:
                    values[field["name"]] = field.get("minimum", 1)
                elif widget == "toggle":
                    values[field["name"]] = True
                elif widget == "text":
                    values[field["name"]] = str(field.get("default") or "All")
            return "answer", {"values": values}
        return "decline", None

    return decide


# ── one unit ───────────────────────────────────────────────────────────

#: Back-off after the model gateway refuses a turn (usage limits reset in minutes).
OUTAGE_WAIT_S = int(os.environ.get("NOVA_BENCH_OUTAGE_WAIT_S", "300"))


def _resolved(unit: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    """The unit with ``mcp:<tool>`` expectations named as the turn's tool parts name them."""
    if not aliases:
        return unit
    out = dict(unit)
    for key in ("expect_tools", "expect_tool_failure"):
        if unit.get(key):
            out[key] = [aliases.get(name, name) for name in unit[key]]
    return out


class Runner:
    def __init__(self, client: StudioClient, agent: dict[str, Any], out: Path, *,
                 pause: float, max_failures: int, replay: bool,
                 fixture: dict[str, Any] | None = None, budget: Budget | None = None):
        self.client = client
        self.agent = agent
        self.fixture = fixture
        self.out = out
        self.pause = pause
        self.max_failures = max_failures
        self.replay = replay
        self.budget = budget
        self.gt = corpus.ground_truth().get("items", {})
        self._last_start = 0.0
        self._pace = asyncio.Lock()
        self.failures = 0
        self.stopped = False
        (out / "turns").mkdir(parents=True, exist_ok=True)
        (out / "sessions").mkdir(parents=True, exist_ok=True)

    def done(self, unit_id: str) -> bool:
        return (self.out / "turns" / f"{unit_id}.json").is_file()

    async def _paced(self) -> None:
        # A ``PAUSE`` file in the results directory holds new turns (for example while
        # a resource monitor sees the engine near its memory limit); turns in flight end.
        while (self.out / "PAUSE").exists():
            await asyncio.sleep(10)
        async with self._pace:
            wait = self._last_start + self.pause - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start = time.monotonic()

    async def _healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                return (await http.get(f"{settings.api()}/health")).status_code == 200
        except httpx.HTTPError:
            return False

    def agent_for(self, unit: dict[str, Any]) -> dict[str, Any] | None:
        """The benchmark agent, or the disposable fixture agent (None when not set up)."""
        if unit.get("agent") != "fixture":
            return self.agent
        if not self.fixture:
            return None
        return {"agent_id": self.fixture["agent_id"], "name": self.fixture.get("agent_name"),
                "model_name": self.fixture.get("model_name"),
                "aliases": self.fixture.get("aliases") or {}}

    async def turn(self, unit: dict[str, Any], thread_id: str | None) -> dict[str, Any]:
        await self._paced()
        agent = self.agent_for(unit) or self.agent
        agent_id = agent["agent_id"]
        if unit.get("mode") == "deep_research":
            return await self._deep_research(unit)
        if thread_id is None:
            thread_id = await with_retry(
                lambda: self.client.new_thread(agent_id, f"bench {unit['id']}"))
        result: TurnResult = await self.client.turn(
            agent_id, thread_id, unit["question"], hitl=hitl_policy(unit),
            stop_after_s=unit.get("stop_after_s"),
        )
        view = fold(result.events)
        record = self._record(_resolved(unit, agent.get("aliases") or {}), result, view, agent)
        if self.replay and result.run_id and not result.transport_error:
            record["replay"] = await self._replay_check(agent_id, thread_id, result.run_id, view)
        if result.run_id and not result.transport_error:
            # Harness v2: bound dataset rows (previewed as this user) and Nova's trace steps.
            await adapters.attach(self.client, record, agent_id)
        return record

    async def _replay_check(self, agent_id: str, thread_id: str, run_id: str,
                            live: TurnView) -> dict[str, Any]:
        try:
            stored = fold(await self.client.replay(agent_id, thread_id, run_id))
        except Exception as exc:  # noqa: BLE001 - recorded, never fatal
            return {"ok": False, "error": type(exc).__name__}
        shape = [(p.type, p.tool_name) for p in live.parts]
        replayed = [(p.type, p.tool_name) for p in stored.parts]
        return {"ok": shape == replayed and stored.answer_text == live.answer_text,
                "live_parts": len(shape), "replayed_parts": len(replayed),
                "same_text": stored.answer_text == live.answer_text}

    def _record(self, unit: dict[str, Any], result: TurnResult, view: TurnView,
                agent: dict[str, Any] | None = None) -> dict[str, Any]:
        agent = agent or self.agent
        checks = check(unit, self.gt.get(unit["id"]), view,
                       transport_error=result.transport_error, total_s=result.total_s)
        return {
            "id": unit["id"],
            "unit": unit,
            "agent": {"agent_id": agent["agent_id"], "name": agent.get("name"),
                      "model": agent.get("model_name")},
            "identity": {"user": self.client.username, "role": self.client.role},
            "thread_id": result.thread_id,
            "run_id": result.run_id,
            "started_at": result.started_at,
            "total_s": result.total_s,
            "http_status": result.http_status,
            "transport_error": result.transport_error,
            "cancelled_by_runner": result.cancelled_by_runner,
            "hitl_answers": result.hitl_answers,
            "view": view.summary(),
            "checks": checks,
            "events": result.events,
        }

    async def _deep_research(self, unit: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            state = await self.client.deep_research(self.agent["agent_id"], unit["question"])
            error = None
        except Exception as exc:  # noqa: BLE001 - recorded as the unit's failure
            state, error = {}, type(exc).__name__
        final = state.get("final") or {}
        report = str(final.get("report") or "")
        view = TurnView(parts=[Part(part_id="report", type="text", text=report)] if report else [],
                        finish_reason="stop" if report else "error")
        result = TurnResult(thread_id=str((state.get("start") or {}).get("thread_id") or ""),
                            question=unit["question"], transport_error=error,
                            total_s=round(time.monotonic() - started, 3))
        record = self._record(unit, result, view)
        # Harness v2 reads findings tables and usage when the API returns them.
        record["deep_research"] = {"status": final.get("status"),
                                   "plan": final.get("plan"), "progress": final.get("progress"),
                                   "findings": final.get("findings"), "usage": final.get("usage")}
        return record

    def save(self, record: dict[str, Any]) -> None:
        path = self.out / "turns" / f"{record['id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, default=str))
        tmp.replace(path)
        checks = record["checks"]
        print(f"{record['id']:<28} {checks['outcome']:<10} "
              f"num={checks['numeric']['recall']} ui={checks['ui_recall']} "
              f"t={record.get('total_s')}s tools={','.join(checks['tools'])[:60]}", flush=True)

    @staticmethod
    def provider_outage(record: dict[str, Any]) -> bool:
        """A turn the model gateway refused at once (quota, 5xx): not a Studio result."""
        checks = record.get("checks") or {}
        return checks.get("outcome") == "error" and (record.get("total_s") or 0) < 10 and any(
            (e or {}).get("code") == "provider_error" for e in checks.get("errors") or [])

    async def guard(self, record: dict[str, Any]) -> None:
        if self.provider_outage(record):
            self.failures += 1
            print(f"Provider unavailable ({record['id']}); waiting {OUTAGE_WAIT_S}s.", flush=True)
            if self.failures >= self.max_failures:
                print("Stopping: the model provider stays unavailable.", flush=True)
                self.stopped = True
            else:
                await asyncio.sleep(OUTAGE_WAIT_S)
        elif record.get("transport_error") or (record.get("http_status") or 200) >= 500:
            self.failures += 1
            if not await self._healthy():
                await asyncio.sleep(30)
            if self.failures >= self.max_failures:
                print("Stopping: too many consecutive transport failures.", flush=True)
                self.stopped = True
        else:
            self.failures = 0

    def affordable(self, unit: dict[str, Any]) -> bool:
        """False (and the run stops cleanly) once the next unit would exceed the budget."""
        if self.budget is None or self.budget.allows(unit["id"], unit.get("category")):
            return True
        print(f"Stopping before {unit['id']}: token budget left {self.budget.remaining}.",
              flush=True)
        self.stopped = True
        return False

    def charge(self, record: dict[str, Any]) -> None:
        if self.budget is not None:
            self.budget.spend(record)

    async def single(self, unit: dict[str, Any]) -> None:
        if self.stopped or self.done(unit["id"]) or not self.affordable(unit):
            return
        record = await self.turn(unit, None)
        self.charge(record)
        await self.guard(record)
        if not record.get("transport_error") and not self.provider_outage(record):
            self.save(record)

    async def session(self, session: dict[str, Any], turns: list[dict[str, Any]]) -> None:
        state_path = self.out / "sessions" / f"{session['id']}.json"
        state = json.loads(state_path.read_text()) if state_path.is_file() else {}
        thread_id = state.get("thread_id")
        for unit in turns:
            if self.stopped:
                return
            if self.done(unit["id"]):
                continue
            if not self.affordable(unit):
                return  # the session resumes from this turn in a later run
            if thread_id is None:
                thread_id = await with_retry(lambda: self.client.new_thread(
                    self.agent["agent_id"], f"bench {session['id']}"))
                state = {"thread_id": thread_id, "session_id": session["id"]}
                state_path.write_text(json.dumps(state))
            record = await self.turn(unit, thread_id)
            self.charge(record)
            await self.guard(record)
            if self.provider_outage(record):
                # The failed message stays in that thread's history: start the session
                # again in a new thread next time.
                for done in turns:
                    (self.out / "turns" / f"{done['id']}.json").unlink(missing_ok=True)
                state_path.unlink(missing_ok=True)
                return
            if record.get("transport_error"):
                return  # resume this session from this turn next time
            self.save(record)


def select(units: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    wanted = {"single": {"single"}, "session": {"session_turn"}, "fresh": {"fresh_twin"},
              "all": {"single", "session_turn", "fresh_twin"}}[args.units]
    chosen = [u for u in units if u["unit"] in wanted]
    if args.ids:
        ids = set(args.ids.split(","))
        chosen = [u for u in units if u["id"] in ids or u.get("session_id") in ids]
    if args.category:
        chosen = [u for u in chosen if u["category"] in args.category]
    if args.limit:
        chosen = chosen[: args.limit]
    return chosen


def corpus_gate(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Corpus v3 linter (blocking) and the units it defers until a product change."""
    report = corpus_v3.lint(for_run=True)
    if not report.ok:
        for line in report.errors[:40]:
            print(f"corpus: {line}")
        raise SystemExit(f"The corpus linter found {len(report.errors)} problems; the run is "
                         "blocked (python -m tests.benchmark.studio_capability.v2.corpus_v3).")
    deferred = {u.id: u.deferred for u in corpus_v3.load_units() if u.deferred}
    skipped = [u["id"] for u in units if u["id"] in deferred]
    if skipped:
        print(f"{len(skipped)} deferred units skipped: {', '.join(skipped)}")
    return [u for u in units if u["id"] not in deferred]


async def main_async(args: argparse.Namespace) -> None:
    out = settings.out_dir(args.out)
    units = select(corpus.units(), args)
    if not args.dry_run:
        units = corpus_gate(units)
    if args.dry_run:
        for unit in units:
            print(unit["id"], "|", unit["question"][:90])
        print(f"{len(units)} units")
        return
    identity = settings.user()
    client = StudioClient(settings.api())
    try:
        await client.login(identity.username, identity.password, identity.role)
        agent = await client.agent_by_name(settings.agent_name())
        detail = await client.request("GET", f"agents/{agent['agent_id']}")
        agent = {**agent, "model_name": detail.get("model_name")}
        budget = None
        if args.token_budget:
            budget = Budget(args.token_budget, Estimates.from_runs([*args.history, out]))
        manifest = {"corpus_version": units[0]["corpus_version"] if units else None,
                    "corpus_v3_version": corpus_v3.load_units()[0].corpus_version,
                    "ground_truth_fingerprint": corpus_v3.load_ground_truth().get(
                        "data_fingerprint"),
                    "token_budget": args.token_budget,
                    "api": settings.api(), "agent": agent.get("name"),
                    "agent_id": agent["agent_id"], "model": agent.get("model_name"),
                    "user": identity.username, "role": client.role,
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
        fixture = fixtures.load(out)
        skipped = [u["id"] for u in units if u.get("agent") == "fixture" and not fixture]
        if skipped:
            print(f"{len(skipped)} fixture units skipped: run fixtures setup first")
            units = [u for u in units if u["id"] not in set(skipped)]
        manifest["fixture_agent"] = (fixture or {}).get("agent_name")
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
        runner = Runner(client, agent, out, pause=args.pause, max_failures=args.max_failures,
                        replay=not args.no_replay, fixture=fixture, budget=budget)
        sessions = {s["id"]: s for s in corpus.sessions()}
        jobs: list[Any] = []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for unit in units:
            if unit["unit"] == "session_turn":
                grouped.setdefault(unit["session_id"], []).append(unit)
            else:
                jobs.append(("single", unit))
        jobs.extend(("session", (sessions[sid], turns)) for sid, turns in grouped.items())
        gate = asyncio.Semaphore(max(1, min(2, args.concurrency)))

        async def run(job: tuple[str, Any]) -> None:
            async with gate:
                if job[0] == "single":
                    await runner.single(job[1])
                else:
                    await runner.session(*job[1])

        await asyncio.gather(*(run(job) for job in jobs))
        if budget is not None:
            manifest["budget"] = budget.summary()
            (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", help="results directory outside the repository")
    parser.add_argument("--units", choices=["single", "session", "fresh", "all"], default="all")
    parser.add_argument("--ids", help="comma-separated unit or session ids")
    parser.add_argument("--category", action="append", help="limit to a category (repeatable)")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=1, help="1 or 2")
    parser.add_argument("--pause", type=float, default=3.0, help="seconds between turn starts")
    parser.add_argument("--max-failures", type=int, default=4)
    parser.add_argument("--no-replay", action="store_true", help="skip the reload check")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--token-budget", type=int,
                        help="stop before a unit whose estimated tokens exceed what is left")
    parser.add_argument("--history", type=Path, action="append", default=[],
                        help="recorded runs for per-unit token estimates")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
