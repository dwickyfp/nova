"""Build the gold-set labelling package (DESIGN §6.3): 40 recorded turns, one page.

    cd backend && uv run python -m tests.benchmark.studio_capability.v2.gold \\
        --run rebench=/path/bench/rebench --run baseline=/path/bench/baseline \\
        --out /path/gold [--size 40]

Composition: 15 turns the deterministic grader sends to the judge, 10 refusals or
limitations, 10 data answers (half passing, half failing) and 5 pages or what-if
turns, chosen with a fixed seed and without repeating a unit. The page is one
self-contained HTML file: one turn per screen with the question, the session's
earlier questions, what the user saw (answer text, tables, KPI tiles, the page
tree, cards, a screenshot when the run recorded one), the expectation and the
reference result. The labeller picks the outcome and scores five dimensions
1-5 (or "can't tell"), and exports the labels as JSON for ``judge calibrate``.
Harness verdicts are not shown, so the labels stay blind.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import random
from pathlib import Path
from typing import Any

from tests.benchmark.studio_capability.v2 import corpus_v3
from tests.benchmark.studio_capability.v2.fold import Signals, fold
from tests.benchmark.studio_capability.v2.grade import grade
from tests.benchmark.studio_capability.v2.ground_truth import resolve
from tests.benchmark.studio_capability.v2.judge import DIMENSIONS, OUTCOME_LABELS, answer_text
from tests.benchmark.studio_capability.v2.rescore import load_turns, session_prior

SEED = 20261124
PACKAGE_VERSION = "gold-2026-11-25.2"
QUOTAS = (("judge", 15), ("refusal_limitation", 10), ("data_answer", 10), ("page", 5))


def _group_candidates(scored: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in QUOTAS}
    for item in scored:
        unit, g, sig = item["unit"], item["grade"], item["signals"]
        if g.verdict == "judge":
            groups["judge"].append(item)
        elif unit.outcome in {"refuse", "limitation"}:
            groups["refusal_limitation"].append(item)
        elif unit.outcome == "answer" and unit.numeric and not sig.pages:
            groups["data_answer"].append(item)
        elif sig.pages or "interactive" in sig.elements:
            groups["page"].append(item)
    return groups


def choose(scored: list[dict[str, Any]], size: int = 40) -> list[dict[str, Any]]:
    """Deterministic selection; the most recent run wins when a unit appears twice."""
    rng = random.Random(SEED)
    groups = _group_candidates(scored)
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    scale = size / sum(q for _, q in QUOTAS)
    for name, quota in QUOTAS:
        want = round(quota * scale)
        pool = sorted(groups[name], key=lambda i: (i["order"], i["unit"].id))
        rng.shuffle(pool)
        pool.sort(key=lambda i: i["order"])  # stable: later runs first, shuffled within
        if name == "data_answer":  # half passing, half failing
            passing = [i for i in pool if i["grade"].verdict == "pass"]
            failing = [i for i in pool if i["grade"].verdict == "fail"]
            pool = [x for pair in zip(failing, passing, strict=False) for x in pair] + \
                failing[len(passing):] + passing[len(failing):]
        taken = 0
        for item in pool:
            if taken >= want:
                break
            if item["unit"].id in seen:
                continue
            seen.add(item["unit"].id)
            item["group"] = name
            chosen.append(item)
            taken += 1
    return chosen


def _screenshot(path: Path, max_width: int = 1100) -> str | None:
    if not path.is_file():
        return None
    try:
        from PIL import Image

        image = Image.open(path)
        image.thumbnail((max_width, 4000))
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=72, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
    except Exception:  # noqa: BLE001 - a missing screenshot only omits the picture
        return None


def _node(node: dict[str, Any]) -> dict[str, Any]:
    props = node.get("props") or {}
    comp = node.get("component")
    keep: dict[str, Any] = {"component": comp}
    for key in ("title", "subtitle", "label", "value", "format", "delta", "badge", "currency",
                "columns", "rows", "text", "markdown", "min", "max", "step", "options", "note",
                "alt", "source"):
        value = props.get(key)
        if value is None or (isinstance(value, dict) and "component" in value):
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict) \
                and "component" in value[0]:
            continue
        keep[key] = value
    if comp == "KpiTiles":
        keep["items"] = [{k: i.get(k) for k in ("label", "value", "format", "currency")}
                         for i in props.get("items") or [] if isinstance(i, dict)
                         and "component" not in i]
    if comp == "DatavizChart":
        chart = _v if isinstance(_v := props.get("chart"), dict) else {}
        keep["visual"] = chart.get("visual")
        keep["title"] = props.get("title") or chart.get("title")
    if comp == "PlaceMap":
        keep["places"] = [p.get("label") for p in props.get("places") or []
                          if isinstance(p, dict)]
        keep["routes"] = len(props.get("routes") or [])
        keep["unavailable"] = bool(props.get("unavailable"))
    if comp in {"Picture", "Image"}:
        keep["url"] = props.get("url") or props.get("src")
        keep["caption"] = props.get("alt") or props.get("source")
    return keep


def unit_payload(item: dict[str, Any], screens: Path | None) -> dict[str, Any]:
    record, unit, sig = item["record"], item["unit"], item["signals"]
    sig_view: Signals = sig
    refs = [r for r in item["references"] if r]
    shot = _screenshot(screens / f"{unit.id}.png") if screens else None
    return {
        "unit_key": f"{item['run']}/{unit.id}", "id": unit.id, "run": item["run"],
        "group": item["group"], "category": unit.category, "lang": unit.lang,
        "question": unit.question, "history": item["history"],
        "expected": {"outcome": unit.outcome, "clarify": unit.clarify,
                     "ui": unit.ui.kinds, "absent": unit.ui.absent,
                     "owner_rules": unit.owner_rules, "focus": unit.judge_focus,
                     "insight": unit.insight},
        "reference": [{"columns": r.get("columns"), "rows": (r.get("rows") or [])[:20]}
                      for r in refs],
        "answer": answer_text(record),
        "tables": [{"columns": t["columns"], "rows": t["rows"][:30]} for t in sig_view.tables],
        "nodes": [_node(n) for n in sig_view.components][:60],
        "cards": [{"kind": h.get("kind"), "status": h.get("status")} for h in sig_view.hitl],
        "card_answers": record.get("hitl_answers") or [],
        "tools": [{"name": t.get("name"), "status": t.get("status"), "reason": t.get("reason")}
                  for t in sig_view.tools],
        "drafts": len(sig_view.drafts), "save_suggested": bool(sig_view.save_suggestions),
        "screenshot": shot,
    }


def build(runs: list[tuple[str, Path]], size: int = 40) -> list[dict[str, Any]]:
    units = {u.id: u for u in corpus_v3.load_units()}
    gt = corpus_v3.load_ground_truth()
    scored: list[dict[str, Any]] = []
    for order, (name, directory) in enumerate(runs):
        records = load_turns(directory)
        folded = {uid: fold(r) for uid, r in records.items()}
        for uid, record in records.items():
            unit = units.get(uid)
            if unit is None or unit.deferred:
                continue
            refs = resolve(unit, gt, units)
            history = []
            if unit.unit == "session_turn" and unit.session_id and unit.turn_index:
                history = [units[f"{unit.session_id}.t{i}"].question
                           for i in range(1, unit.turn_index)
                           if f"{unit.session_id}.t{i}" in units]
            scored.append({"run": name, "order": order, "record": record, "unit": unit,
                           "signals": folded[uid], "references": refs.numeric,
                           "history": history,
                           "grade": grade(unit, folded[uid], refs,
                                          bound_rows=session_prior(folded, uid))})
    chosen = choose(scored, size)
    return [unit_payload(item, dict(runs)[item["run"]] / "screens") for item in chosen]


def write_html(payload: list[dict[str, Any]], path: Path) -> None:
    data = json.dumps({"version": PACKAGE_VERSION, "dimensions": list(DIMENSIONS),
                       "outcomes": list(OUTCOME_LABELS), "units": payload},
                      ensure_ascii=False).replace("</", "<\\/")
    page = (Path(__file__).with_name("gold_template.html").read_text()
            .replace("__DATA__", data).replace("__VERSION__", html.escape(PACKAGE_VERSION)))
    path.write_text(page)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="append", required=True,
                        help="name=directory; list the most recent run first")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--size", type=int, default=40)
    args = parser.parse_args()
    runs = [(s.split("=", 1)[0], Path(s.split("=", 1)[1])) for s in args.run]
    payload = build(runs, args.size)
    args.out.mkdir(parents=True, exist_ok=True)
    write_html(payload, args.out / "gold-labelling.html")
    manifest = [{k: u[k] for k in ("unit_key", "group", "category", "question")}
                for u in payload]
    (args.out / "gold-units.json").write_text(json.dumps(
        {"version": PACKAGE_VERSION, "seed": SEED, "units": manifest}, indent=1,
        ensure_ascii=False))
    groups: dict[str, int] = {}
    for unit in payload:
        groups[unit["group"]] = groups.get(unit["group"], 0) + 1
    print(f"{len(payload)} units {groups} -> {args.out / 'gold-labelling.html'}")


if __name__ == "__main__":
    main()
