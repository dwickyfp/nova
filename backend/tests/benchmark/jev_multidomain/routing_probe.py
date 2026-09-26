"""Isolate the existing Jev ranking hook; this is not end-to-end Smart routing."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

from tests.benchmark.jev_multidomain.environment import (
    ARTIFACTS,
    authenticated_user,
    isolated_agent_registry,
)

WORDING = {
    "Finance": "Corporate accounting analyst covering earned revenue, invoices, receivables, "
    "direct costs, operating income, expense plans, and cash movements. Explains timing, "
    "currencies, and denominator choices. Requests campaign attribution and acquisition "
    "evidence from Marketing when needed. Accounting totals alone cannot establish "
    "advertising effectiveness, incremental lift, or the quality of acquired customer cohorts.",
    "Marketing": "Customer acquisition analyst covering campaign reach, engagement, media "
    "efficiency, qualified leads, first purchases, and attributed revenue. Explains funnel "
    "stages, attribution windows, and denominator choices. Requests earned revenue, company "
    "costs, receivables, and customer profit evidence from Finance when needed. Advertising "
    "return alone cannot establish accounting profitability, incremental lift, or financial value.",
}


def variant_candidates(candidates: list, variant: str) -> list:
    result = candidates if variant == "sales" else [c for c in candidates if "Sales" not in c.name]
    if variant == "reverse":
        return list(reversed(result))
    if variant == "none":
        return [replace(c, metrics=(), dimensions=()) for c in result]
    if variant == "minimal":
        return [
            replace(
                c,
                metrics=(),
                dimensions=(),
                manifest=c.manifest.model_copy(
                    update={
                        "delegation_description": c.manifest.delegation_description.split(".")[0]
                        + ".",
                        "good_for": [],
                        "consult_when": [],
                        "not_primary_for": [],
                    }
                ),
            )
            for c in result
        ]
    if variant == "description":
        return [
            replace(
                c,
                manifest=c.manifest.model_copy(
                    update={
                        "delegation_description": ". ".join(
                            reversed(c.manifest.delegation_description.split(". "))
                        ),
                        "good_for": list(reversed(c.manifest.good_for)),
                    }
                ),
            )
            for c in result
        ]
    if variant == "wording":
        return [
            replace(
                c,
                manifest=c.manifest.model_copy(
                    update={
                        "delegation_description": next(
                            (text for domain, text in WORDING.items() if domain in c.name),
                            c.manifest.delegation_description,
                        )
                    }
                ),
            )
            for c in result
        ]
    return result


async def probe(
    cases: list[dict], output: Path, variant: str, concurrency: int = 1, interval: float = 8
) -> None:
    from app.modules.agents.auto_planner import (
        authorized_candidates,
        rank_candidates,
        semantic_matches,
    )
    from app.modules.ai_ml.decision_settings import DecisionSettings
    from app.modules.assistant.decision import DecisionSession, rank_agents

    env = json.loads((ARTIFACTS / "environment.json").read_text())
    domains = {a["id"]: name for name, a in env["agents"].items()}
    allowed = {aid for aid, domain in domains.items() if variant == "sales" or domain != "SALES"}
    with isolated_agent_registry(allowed):
        available = await authorized_candidates(await authenticated_user())
    if {c.agent_id for c in available} != allowed:
        raise RuntimeError("Probe requires verified access to its benchmark agents")
    done = (
        {json.loads(line)["case_id"] for line in output.read_text().splitlines()}
        if output.exists()
        else set()
    )
    sem = asyncio.Semaphore(concurrency)

    async def one(case: dict) -> None:
        if case["id"] in done:
            return
        async with sem:
            await asyncio.sleep(interval)
            candidates = variant_candidates(available, variant)
            question = case["question"]
            if case["history"]:
                question = json.dumps(
                    {"recent_conversation": case["history"], "current_request": question},
                    ensure_ascii=False,
                )
            matches = semantic_matches(question, candidates)
            baseline = rank_candidates(question, candidates, matches)
            # Reverse the actual Jev candidate input, after deterministic pre-ranking.
            if variant == "reverse":
                baseline.reverse()
            session = DecisionSession(DecisionSettings.model_validate(env["decision_settings"]))
            raw = []
            original = session._post

            async def post(payload: dict, timeout: float) -> dict:
                item = {"request": payload}
                try:
                    response = await original(payload, timeout)
                    item["response"] = response
                    return response
                except (Exception, asyncio.CancelledError) as exc:
                    item["error"] = type(exc).__name__
                    item["http_status"] = getattr(
                        getattr(exc, "response", None), "status_code", None
                    )
                    raise
                finally:
                    raw.append(item)

            session._post = post
            start = time.monotonic()
            ranked = await rank_agents(session, question, baseline, semantic_matches=matches)
            trace = session.trace[-1] if session.trace else {}
            candidate_ids = trace.get("candidates", {})
            primary = sorted(
                domains[candidate_ids[key]]
                for key, value in trace.get("choices", {}).items()
                if value == "primary" and key in candidate_ids
            )
            row = {
                "case_id": case["id"],
                "variant": variant,
                "jev_primary_agents": primary,
                "ranked": [domains[c.agent_id] for c in ranked],
                "lexical_ranked": [domains[c.agent_id] for c in baseline],
                "semantic_matches": matches,
                "trace": session.trace,
                "raw": raw,
                "latency_seconds": time.monotonic() - start,
            }
            if any(
                item.get("error") in {"OperationalError", "InterfaceError", "ConnectionError"}
                for item in raw
            ):
                with output.with_suffix(".infrastructure.jsonl").open("a") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                raise RuntimeError("Database unavailable; stop the probe batch")
            with output.open("a") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            print(
                json.dumps(
                    {"probe": case["id"], "status": trace.get("status"), "primary": primary}
                ),
                flush=True,
            )
            if any(item.get("http_status") == 429 for item in raw):
                await asyncio.sleep(60)

    async with asyncio.TaskGroup() as tasks:
        for case in cases:
            tasks.create_task(one(case))
