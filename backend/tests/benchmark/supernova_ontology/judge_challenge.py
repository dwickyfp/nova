"""A balanced challenge set for the gold-set judge, wrong items known by construction.

Natural gold sets are about 94% valid, so Cohen's κ against a reviewer rests on a
handful of "no" items and its interval spans most of [0, 1]. This set pairs every
valid test-half case with one mutant whose meaning no longer matches its unchanged
question (another period, top-N, breakdown or measure), so the right verdict of a
mutant is "no" without anyone's judgement. The judge sees both through the same
rubric, catalog and batch size as a judge run. Protocol:
docs/testing/supernova/judge-agreement-protocol.md.

It calls the configured model and stores nothing. It signs in like the benchmark
report (``NOVA_BENCH_URL``, ``NOVA_BENCH_USER``, ``NOVA_BENCH_PASSWORD``; nothing is
printed) and reads the session with the engine settings the backend uses:

    python -m tests.benchmark.supernova_ontology.judge_challenge out.json
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from collections import Counter
from typing import Any

from app.common.statistics import cohen_kappa
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.ontology.derived import humanize
from app.modules.ontology.evaluation import gold as gold_cases
from app.modules.ontology.evaluation.judging import TEXT, VERDICT, ask_items, items_schema
from app.modules.ontology.evaluation.service import GOLD_RUBRIC, JUDGE_BATCH, RUBRIC
from tests.benchmark.supernova_ontology.report import half

SETS = ("serbadata", "serbadata_business")
KINDS = ("period", "top_n", "breakdown", "measure")
SEED = 7
BOOTSTRAP = 2000


def _words(name: str) -> set[str]:
    return set(humanize(name).lower().split())


def mutate(case: Any, ir: SemanticModelIR, window: list[str] | None,
           kind: str) -> str | None:
    """The case's meaning with one part of its plan changed, or None when it does not apply."""
    plan, meaning = case.plan or {}, case.meaning
    metric = (plan.get("metrics") or [None])[0]
    if kind == "period":
        time = plan.get("time") or {}
        if not window or not time.get("range") or time.get("grain"):
            return None
        whole, _trend = gold_cases.periods(*window)
        current = next((p for p in whole if p.range == time["range"]), None)
        other = next((p for p in whole if current and p.range != current.range
                      and p.words_en not in meaning), None)
        if current is None or other is None or f"in {current.words_en}" not in meaning:
            return None
        return meaning.replace(f"in {current.words_en}", f"in {other.words_en}")
    if kind == "top_n":
        if plan.get("limit") != 5 or "the 5 " not in meaning:
            return None
        return meaning.replace("the 5 ", "the 3 ")
    if kind == "breakdown":
        dims = plan.get("dimensions") or []
        if len(dims) != 1 or not metric:
            return None
        labels = dict(gold_cases.breakdowns(ir, metric))
        label = labels.get(dims[0])
        if not label or label not in meaning:
            # The business vocabulary set writes the field without its qualified name.
            plain = humanize(dims[0].split(".")[1])
            if f"for each {plain}" not in meaning:
                return None
            label = plain
        other = next((text for name, text in sorted(labels.items())
                      if name != dims[0] and not (_words(name.split(".")[1])
                                                  & _words(dims[0].split(".")[1]))), None)
        if other is None:
            return None
        if label == humanize(dims[0].split(".")[1]):
            other = other.split(" [")[0]
        return meaning.replace(label, other)
    if kind == "measure":
        if not metric or ir.metric(metric) is None:
            return None
        words = gold_cases._metric_words(ir, metric)
        if not meaning.startswith(words):
            return None
        others = [item.name for item in ir.metrics
                  if item.name != metric and not (_words(item.name) & _words(metric))]
        if not others:
            return None
        # A different measure per case, fixed by the case id, so no one measure dominates.
        other = random.Random(case.id).choice(sorted(others))
        return gold_cases._metric_words(ir, other) + meaning[len(words):]
    raise ValueError(kind)


def build(cases: list[Any], models: dict[str, dict], windows: dict) -> list[dict]:
    """Valid test-half cases and one mutant of each answerable one."""
    items = []
    position = 0
    for case in cases:
        reviews = [review.verdict for review in case.reviews]
        if case.status != "ACTIVE" or half(case.id) != "test" or not reviews or any(
                verdict != "yes" for verdict in reviews):
            continue
        items.append({"id": f"pos:{case.id}", "label": "yes", "mutation": None, "case": case,
                      "meaning": case.meaning})
        model = models.get(case.view_id)
        if case.kind != "answerable" or model is None:
            continue
        ir = SemanticModelIR.from_ossie(model["definition"])
        for step in range(len(KINDS)):
            kind = KINDS[(position + step) % len(KINDS)]
            changed = mutate(case, ir, windows.get(case.view_id), kind)
            if changed and changed != case.meaning:
                items.append({"id": f"neg:{case.id}:{kind}", "label": "no", "mutation": kind,
                              "case": case, "meaning": changed})
                position += 1
                break
    return items


async def judge(service: Any, items: list[dict], models: dict, windows: dict) -> dict[str, dict]:
    order = list(items)
    random.Random(SEED).shuffle(order)
    verdicts: dict[str, dict] = {}
    for start in range(0, len(order), JUDGE_BATCH):
        batch = order[start:start + JUDGE_BATCH]
        catalog = service._catalog_text(models, sorted({i["case"].view_id for i in batch}), windows)
        found, model_name = await ask_items(
            service.client(), GOLD_RUBRIC, {"catalog": catalog},
            [{"id": item["id"], "kind": item["case"].kind, "question": item["case"].question,
              "meaning": item["meaning"]} for item in batch],
            items_schema({"id": TEXT, "reason": TEXT, "verdict": VERDICT}), "evaluation_review",
        )
        for item in batch:
            reply = found.get(item["id"]) or {}
            verdicts[item["id"]] = {"verdict": str(reply.get("verdict") or "unsure"),
                                    "reason": str(reply.get("reason") or ""),
                                    "model": f"{model_name}@{RUBRIC}"}
        print(f"judged {min(start + JUDGE_BATCH, len(order))}/{len(order)}", file=sys.stderr)
    return verdicts


def score(rows: list[tuple[str, str]], seed: int = SEED) -> dict[str, Any]:
    """κ with a bootstrap interval, sensitivity for "no" and specificity, from (label, verdict)."""
    def kappa(sample: list[tuple[str, str]]) -> float:
        return float(cohen_kappa([a for a, _ in sample], [b for _, b in sample])["kappa"] or 0)

    rng = random.Random(seed)
    boots = sorted(kappa([rng.choice(rows) for _ in rows]) for _ in range(BOOTSTRAP))
    negatives = [verdict for label, verdict in rows if label == "no"]
    positives = [verdict for label, verdict in rows if label == "yes"]
    return {
        "n": len(rows), "kappa": round(kappa(rows), 4),
        "interval_95": [round(boots[int(0.025 * BOOTSTRAP)], 4),
                        round(boots[int(0.975 * BOOTSTRAP) - 1], 4)],
        "sensitivity_no": [sum(v == "no" for v in negatives), len(negatives)],
        "specificity": [sum(v == "yes" for v in positives), len(positives)],
    }


async def session_user() -> dict:  # pragma: no cover - needs a deployment
    """The evaluating administrator's session, signed in through the API."""
    import os

    import httpx

    from app.common.nova_system import init_nova_system
    from app.core.database import db
    from app.core.redis import session_store
    from app.core.security import decode_token

    await db.init_system_pool()
    await session_store.init()
    await init_nova_system()
    base = os.environ.get("NOVA_BENCH_URL", "http://localhost:8000").rstrip("/")
    async with httpx.AsyncClient(base_url=f"{base}/api/v1", timeout=60) as http:
        login = await http.post("/auth/login", json={
            "username": os.environ.get("NOVA_BENCH_USER", "nova_admin"),
            "password": os.environ["NOVA_BENCH_PASSWORD"]})
        login.raise_for_status()
    sid = decode_token(login.json()["access_token"])["sid"]
    return {**(await session_store.get(sid) or {}), "session_id": sid}


async def main(path: str) -> None:  # pragma: no cover - needs a deployment and a model
    from app.modules.ontology.evaluation.service import evaluation_service as service

    user = await session_user()
    models = await service._models(user)
    runs = await service.runs()
    report: dict[str, Any] = {"rubric": RUBRIC, "sets": {}}
    rows_all: list[tuple[str, str]] = []
    for set_name in SETS:
        gold = next(run for run in runs if run.kind == "gold" and run.set_name == set_name)
        windows = gold.summary.get("windows") or {}
        items = build(await service.cases(set_name), models, windows)
        verdicts = await judge(service, items, models, windows)
        rows = [(item["label"], verdicts[item["id"]]["verdict"]) for item in items]
        rows_all += rows
        by_kind = Counter()
        caught = Counter()
        for item in items:
            if item["mutation"]:
                by_kind[item["mutation"]] += 1
                caught[item["mutation"]] += verdicts[item["id"]]["verdict"] == "no"
        report["sets"][set_name] = {
            **score(rows),
            "mutations": {kind: [caught[kind], by_kind[kind]] for kind in KINDS},
            "items": [{"id": item["id"], "label": item["label"], "mutation": item["mutation"],
                       "question": item["case"].question, "meaning": item["meaning"],
                       **verdicts[item["id"]]} for item in items],
        }
    report["all"] = score(rows_all)
    with open(path, "w") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    print(json.dumps({name: {k: v for k, v in body.items() if k != "items"}
                      for name, body in report["sets"].items()}, indent=1))
    print(json.dumps(report["all"]))


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main(sys.argv[1]))
