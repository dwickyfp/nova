"""Score one pass: gold values, wording, artifacts, and an LLM judge as the reader.

    cd backend && uv run python -m tests.benchmark.smart_native.judge RESULTS.json
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from tests.benchmark.smart_native.lab import read_state

DIMENSIONS = ("accuracy", "completeness", "nativeness", "grounded")
RUBRIC = """You are a strict evaluator of a business data assistant. Score ONE final answer.
You receive the conversation, the expectation, GOLD rows computed independently by SQL, the
answer, and the result table or chart the product displays beside it. That table is raw data
shown by the product, not the assistant's prose: do not penalize its formatting, and count its
figures as stated. `gold_figures_all_stated` is an exact arithmetic check already done for you;
trust it over your own digit counting. Indonesian writes the decimal mark as a comma and
groups thousands with dots: "12,404 miliar" is 12.404 billion, the same as 12.404.000.000.
GOLD may hold more rows than the answer needs; a
figure GOLD does not cover is not invented when the displayed result holds it. Score each
dimension 0-5 (5 = flawless):
- accuracy: every number stated matches GOLD (formatting, currency symbols, thousand separators
  and sensible rounding are fine). A wrong, missing, or redacted required number is at most 2.
  With no GOLD, judge against the expectation.
- completeness: everything the user asked is answered (all groups, both parts of a question).
- nativeness: the reader is a business person who knows the data but nothing about the product.
  The assistant must answer as ONE analyst in plain business language. Penalize: narrating
  delegation or forwarding, naming agents or specialists, product jargon ("Semantic View", tool
  names), identifiers with underscores or backticks, evidence or run ids, redaction placeholders
  or notes about removed numbers, a stiff machine-like listing instead of a natural answer, or
  telling the user to ask elsewhere. Naming a source by its subject ("finance data") is good.
- grounded: no invented numbers, sources, or capabilities; limits stated honestly.
Return ONLY JSON: {"accuracy":n,"completeness":n,"nativeness":n,"grounded":n,
"issues":["short concrete issue", ...]}."""

JARGON = re.compile(
    r"`|\b[a-z]+(?:_[a-z0-9]+)+\b|semantic view|evidence[_ ]?\d|\bspesialis\b|\bspecialist|"
    r"finance analyst|hr analyst|\bagent\b|\bagen\b|delegas|teruskan ke|tidak terverifikasi|"
    r"unverified|dikoreksi|numbers were removed|angka dihapus|hasil query terotorisasi|"
    r"\[\s*(?:redacted|dihapus|angka)\s*\]|<[｜|]", re.I)
SCALES = {"ribu": 1e3, "thousand": 1e3, "juta": 1e6, "million": 1e6, "miliar": 1e9,
          "milyar": 1e9, "billion": 1e9, "triliun": 1e12, "trillion": 1e12}
NUMBER = re.compile(r"\d[\d.,]*\d|\d")
SCALE = re.compile(r"\s*(" + "|".join(SCALES) + r")\b", re.I)


def jargon(answer: str) -> list[str]:
    return sorted({match.group(0).lower() for match in JARGON.finditer(answer)})


def readable(rows: list[list[str]]) -> list[list[str]]:
    """Gold with thousands separators, so the judge does not miscount digits."""
    def cell(value: str) -> str:
        try:
            return f"{float(value):,.2f}"
        except ValueError:
            return value
    return [[cell(value) for value in row] for row in rows]


def _readings(token: str) -> list[tuple[float, float]]:
    """Each way the token reads, with half a unit of its last shown digit."""
    plain = re.sub(r"[.,]", "", token)
    readings = [(float(plain), 0.5)]
    split = re.match(r"^(.*)[.,](\d+)$", token)
    if split:
        whole = re.sub(r"[.,]", "", split.group(1)) or "0"
        decimals = split.group(2)
        readings.append((float(f"{whole}.{decimals}"), 0.5 * 10 ** -len(decimals)))
    return readings


def stated(answer: str) -> list[tuple[float, float]]:
    values = []
    for match in NUMBER.finditer(answer):
        scale = SCALE.match(answer, match.end())
        factor = SCALES[scale.group(1).lower()] if scale else 1.0
        values += [(value * factor, slack * factor) for value, slack in _readings(match.group(0))]
    return values


def gold_missing(answer: str, keys: tuple[str, ...] | list[str], gold: dict) -> list[str]:
    """Gold figures the answer does not state, allowing only the rounding it shows."""
    values, missing = stated(answer), []
    for key in keys:
        for row in gold[key]:
            want = abs(float(row[-1]))
            if not any(abs(value - want) <= max(slack, want * 0.0005) for value, slack in values):
                missing.append(f"{key}:{'/'.join(row)}")
    return missing


async def _verdict(provider, payload: dict) -> dict:
    from app.modules.assistant.provider import assistant_provider

    for _ in range(2):
        try:
            message = await assistant_provider.complete(
                provider=provider, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": RUBRIC},
                          {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
            found = re.search(r"\{.*\}", message.get("content") or "", re.S)
            verdict = json.loads(found.group(0)) if found else {}
            if all(isinstance(verdict.get(name), (int, float)) for name in DIMENSIONS):
                return verdict
        except Exception as exc:  # noqa: BLE001 - a judge failure is a failed case, reported
            last = f"{type(exc).__name__}: {exc}"[:200]
            if "402" in last:
                raise SystemExit("The provider returned HTTP 402; stop and refill credit.") from exc
    return {"judge_error": "no usable verdict"}


READINGS = 3


async def _median(provider, payload: dict) -> dict:
    """Three readings, the middle score per dimension: one misread does not decide a case."""
    readings = [await _verdict(provider, payload) for _ in range(READINGS)]
    scored = [item for item in readings if "judge_error" not in item]
    if len(scored) < 2:
        return {"judge_error": "fewer than two usable verdicts"}
    verdict = {name: sorted(item[name] for item in scored)[len(scored) // 2]
               for name in DIMENSIONS}
    verdict["readings"] = [[item[name] for name in DIMENSIONS] for item in scored]
    verdict["issues"] = [issue for item in scored for issue in item.get("issues") or []][:8]
    return verdict


async def judge(path: Path) -> dict:
    from app.core.database import db
    from app.modules.assistant.provider import assistant_provider

    gold = read_state("gold.json")
    if not gold:
        raise SystemExit("Compute gold first: python -m tests.benchmark.smart_native.gold")
    results = json.loads(path.read_text())
    await db.init_system_pool()
    provider = await assistant_provider.resolve(provider_id=None, model=None)
    report = {}
    for key, item in results.items():
        turns = item["turns"]
        last = turns[-1]
        answer = last.get("answer") or ""
        missing = gold_missing(f"{answer}\n{last.get('shown', '')}", item["gold"], gold)
        verdict = await _median(provider, {
            "conversation": [{"user": turn["question"], "assistant": turn.get("answer", "")}
                             for turn in turns[:-1]] + [{"user": last["question"]}],
            "expectation": item["expect"],
            "gold": {name: readable(gold[name])
                     for name in [*item["gold"], *item.get("reference", [])]},
            "gold_figures_all_stated": not missing if item["gold"] else None,
            "answer_to_score": answer or "(empty answer)",
            "displayed_beside_the_answer": {
                "result_table": last.get("shown") or None,
                "artifacts": last.get("artifacts") or [],
            },
        })
        failures = [f"{name}<4" for name in DIMENSIONS if verdict.get(name, 0) < 4]
        terms = jargon(answer) if item["target"] == "smart" else []
        absent = [kind for kind in item.get("artifacts", [])
                  if kind not in last.get("artifacts", [])]
        failures += [f"gold:{len(missing)}"] * bool(missing) + [f"jargon:{','.join(terms)}"] * bool(
            terms) + [f"no {kind}" for kind in absent] + ["error"] * bool(last.get("errors"))
        if len(turns) < len(item.get("questions", turns)):
            failures.append("turn failed")
        report[key] = {
            **verdict, "pass": not failures, "failures": failures, "gold_missing": missing,
            "seconds": last.get("seconds"), "tools": last.get("tools"),
            "errors": last.get("errors"),
        }
        print(("PASS " if not failures else "FAIL ") + key, last.get("seconds"),
              json.dumps(failures, ensure_ascii=False), verdict.get("issues") or "", flush=True)
    await db.close_system_pool()
    path.with_suffix(".judge.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    passed = sum(item["pass"] for item in report.values())
    print(f"pass {passed}/{len(report)}")
    return report


if __name__ == "__main__":
    outcome = asyncio.run(judge(Path(sys.argv[1])))
    sys.exit(0 if outcome and all(item["pass"] for item in outcome.values()) else 1)
