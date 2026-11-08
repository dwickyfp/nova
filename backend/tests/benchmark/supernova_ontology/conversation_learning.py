"""Does the ontology learn the words people use in Studio? A paired before/after experiment.

Protocol: docs/testing/supernova/conversation-learning-protocol.md. Each new phrase
(docs/testing/supernova/conversation-phrases.json) replaces the recorded measure
term of business gold questions, so the right answer stays known. The first two
questions per metric, by SHA-256 of the case id, are asked in Studio by two people
on threads that allow learning; an answer whose plan used exactly the gold metric is
liked. The remaining questions form the held-out set ``conversation_terms``.

    python -m tests.benchmark.supernova_ontology.conversation_learning build
    python -m tests.benchmark.supernova_ontology.conversation_learning converse out.json

``build`` stores the held-out cases (as the signed-in administrator, through the
evaluation repository). ``converse`` drives Studio over HTTP. Sign-in uses
``NOVA_BENCH_URL``, ``NOVA_BENCH_PASSWORD`` and ``NOVA_BENCH_STEWARD_PASSWORD``;
nothing secret is printed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PHRASES = Path(__file__).resolve().parents[4] / "docs/testing/supernova/conversation-phrases.json"
SOURCE_SET = "serbadata_business"
TEST_SET = "conversation_terms"
AGENT = "b3cf95d8-eb3e-4aa7-bb4c-3d2a520509f8"
PEOPLE = (("nova_admin", "ACCOUNTADMIN", "NOVA_BENCH_PASSWORD"),
          ("retail_steward_test", "retail_steward", "NOVA_BENCH_STEWARD_PASSWORD"))
_TERM = re.compile(r"measure term: '([^']+)'")


def rewrite(question: str, term: str, phrase: str) -> str | None:
    """The question with its measure term replaced by ``phrase``, or None when absent."""
    found = re.search(re.escape(term), question, flags=re.IGNORECASE)
    if found is None:
        return None
    return question[:found.start()] + phrase + question[found.end():]


def split(cases: list[Any], phrases: dict[str, str]) -> tuple[dict, list]:
    """Per metric: two conversation questions and the held-out rest, as rewritten copies."""
    by_metric: dict[str, list[tuple[Any, str]]] = defaultdict(list)
    for case in cases:
        if case.kind != "answerable" or case.status != "ACTIVE" or not case.plan:
            continue
        metric = (case.plan.get("metrics") or [None])[0]
        term = next(iter(_TERM.findall(case.meaning)), None)
        if metric not in phrases or not term:
            continue
        question = rewrite(case.question, term, phrases[metric])
        if question:
            by_metric[metric].append((case, question))
    conversation, held_out = {}, []
    for metric, rows in sorted(by_metric.items()):
        if len(rows) < 3:
            continue
        rows.sort(key=lambda row: hashlib.sha256(row[0].id.encode()).hexdigest())
        conversation[metric] = rows[:2]
        held_out += rows[2:]
    return conversation, held_out


async def build() -> None:  # pragma: no cover - needs a deployment
    from app.modules.ontology.evaluation.contracts import EvalCase
    from app.modules.ontology.evaluation.service import evaluation_service as service
    from tests.benchmark.supernova_ontology.judge_challenge import session_user

    user = await session_user()
    phrases = json.loads(PHRASES.read_text())
    _conversation, held_out = split(await service.cases(SOURCE_SET), phrases)
    existing = await service.cases(TEST_SET)
    if existing:
        raise SystemExit(f"{TEST_SET} already has {len(existing)} cases")
    from app.modules.ontology.contracts import Actor

    records = []
    for case, question in held_out:
        term = _TERM.findall(case.meaning)[0]
        metric = case.plan["metrics"][0]
        records.append((EvalCase(
            id="evc_" + hashlib.sha256(f"{TEST_SET}:{case.id}".encode()).hexdigest()[:32],
            actor=Actor.from_user(user), set_name=TEST_SET, version=1, kind="answerable",
            intent=case.intent, language=case.language, question=question,
            view_id=case.view_id, plan=case.plan,
            meaning=case.meaning.replace(f"'{term}'", f"'{phrases[metric]}'"),
            expected=case.expected, terms=[phrases[metric]], status="ACTIVE"), 0))
    await service.repository.save_many("eval_cases", records)
    print(f"{TEST_SET}: {len(records)} held-out cases")


def used_metric(message: dict) -> list[str]:
    """The metrics of the answer's successful semantic queries."""
    from app.modules.agents.verified_candidates import candidates_from_steps

    return sorted({metric for candidate in candidates_from_steps(message.get("steps"))
                   for metric in candidate["semantic_plan"].get("metrics") or []})


async def converse(path: str) -> None:  # pragma: no cover - needs a deployment
    import httpx

    from app.modules.ontology.evaluation.service import evaluation_service as service
    from tests.benchmark.business_intelligence.client import StudioClient
    from tests.benchmark.supernova_ontology.judge_challenge import session_user

    await session_user()  # opens the system pool for reading the source cases
    phrases = json.loads(PHRASES.read_text())
    conversation, _held_out = split(await service.cases(SOURCE_SET), phrases)
    base = os.environ.get("NOVA_BENCH_URL", "http://localhost:8000").rstrip("/")
    clients = []
    for username, role, secret in PEOPLE:
        client = StudioClient(httpx.AsyncClient(base_url=base, timeout=600))
        await client.login(username, os.environ[secret], role)
        clients.append((username, client))
    rows = []
    for metric, pair in conversation.items():
        for (username, client), (case, question) in zip(clients, pair, strict=True):
            try:
                turn = await client.turn(AGENT, question, learning=True)
            except Exception as exc:  # noqa: BLE001 - a failed turn is an outcome
                rows.append({"metric": metric, "person": username, "question": question,
                             "error": type(exc).__name__})
                continue
            metrics = used_metric(turn.message)
            liked = metrics == [metric]
            if liked:
                await client.like(AGENT, turn)
            rows.append({"metric": metric, "person": username, "question": question,
                         "case": case.id, "used": metrics, "liked": liked,
                         "finish": turn.finish_reason})
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    for _username, client in clients:
        await client.http.aclose()
    Path(path).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    liked = sum(row.get("liked", False) for row in rows)
    print(f"{liked}/{len(rows)} answers liked over {len(conversation)} metrics")


if __name__ == "__main__":  # pragma: no cover
    command = sys.argv[1]
    if command == "build":
        asyncio.run(build())
    elif command == "converse":
        asyncio.run(converse(sys.argv[2]))
    else:
        raise SystemExit("build | converse <out.json>")
