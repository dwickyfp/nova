"""Convert corpus v2 (the v1 harness's JSONL) to typed corpus v3, once.

    cd backend && uv run python -m tests.benchmark.studio_capability.v2.convert \\
        [--semantic-view /path/to/semview.json]

Mechanical mapping plus the reviewed corrections below (each with its reason,
listed again in ``corpus/v3/CHANGELOG.md``):

* E1 (re-bench root cause): expectations that asked for figures the user did
  not request, or hard-coded the wrong row (session-009.t3).
* corpus_patch.json of the harness-v2 prototype (same units, now as typed
  ``required_measures`` / ``bind`` instead of typed-in numbers).
* Owner decisions of 2026-11-24: D2 (refusal with a labelled alternative), D3
  (save from chat is a limitation pointing at the Save button), D4 (schedule
  and alert requests end in ``action_done``), D5 (a table restated from history
  must be a rendered table).

Expected figures are never written here: a numeric expectation names a
reference query and the result columns to show.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tests.benchmark.studio_capability import corpus as corpus_v1
from tests.benchmark.studio_capability.v2 import corpus_v3
from tests.benchmark.studio_capability.v2.expectation import Expectation

V3_VERSION = "2026-11-25.4"
SOFT_UI = {"text", "suggestions", "number"}
CITY_AVG_SQL = ("SELECT shipping_city AS label, AVG(total_amount) AS value FROM NOVA_DEMO.orders "
                "GROUP BY shipping_city ORDER BY value DESC")
MONTHLY_SQL = ("SELECT DATE_TRUNC('month', order_date) AS label, SUM(total_amount) AS value "
               "FROM NOVA_DEMO.orders GROUP BY 1 ORDER BY 1")
TOTAL_SQL = "SELECT SUM(total_amount) AS value FROM NOVA_DEMO.orders"
JUNE_SQL = ("SELECT SUM(total_amount) AS revenue, COUNT(order_id) AS orders, AVG(total_amount) "
            "AS aov FROM NOVA_DEMO.orders WHERE order_date >= '2025-06-01' AND order_date < "
            "'2025-07-01'")
GOPAY_CUSTOMERS_SQL = ("SELECT COUNT(DISTINCT customer_id) AS value FROM NOVA_DEMO.orders "
                       "WHERE payment_method = 'GoPay'")

#: (unit ids, change, reason) — applied in order; ``change`` edits the draft dict.
Change = tuple[tuple[str, ...], dict[str, Any], str]
CHANGES: list[Change] = [
    (("kpi-008",), {"required_measures": ["revenue", "orders"]},
     "E1: the user asked for revenue and order count; AOV was not requested."),
    (("layout_page-004", "layout_page-007", "layout_page-008", "session-038.t3",
      "session-038.t3.fresh"), {"required_measures": ["june", "july"]},
     "E1: a June vs July page states both months; the grand total was not requested."),
    (("table-014",), {"required_measures": ["order_id"]},
     "E1: a list of non-completed orders is identified by order id; amounts not requested."),
    (("session-009.t3", "session-009.t3.fresh"),
     {"reference_sql": CITY_AVG_SQL, "required_measures": ["value"], "key_columns": ["label"],
      "bind": [{"column": "label", "source": "session-009.t1", "source_column": "label",
                "row": 0}]},
     "E1: the top city by revenue is whatever session-009.t1's reference returns first "
     "(Yogyakarta), not a hard-coded Jakarta; the row is bound, not typed."),
    (("what_if-006",), {"numeric": None, "ui_kinds": ["interactive"],
                        "bindings": ["control_live"]},
     "E1: the old figure was July's actual revenue, not a target; grade the live control."),
    (("typo-006",), {"primary_with_alternative": GOPAY_CUSTOMERS_SQL},
     "E1 (coordinator, 2026-11-24): 'brp org' means people, so distinct GoPay customers is "
     "the primary reference; the order count stays an accepted alternative reading."),
    (("deep_research-001", "deep_research-002", "deep_research-003"),
     {"numeric": None, "judge_focus": ["names the drivers (payment method, city) from the data",
                                       "uses the data's own date range"]},
     "E1: the old figures (overall revenue/orders/AOV) did not test the drivers asked for."),
    (("automation-001", "automation-002", "automation-003", "automation-004",
      "session-025.t2", "session-025.t2.fresh", "session-026.t2", "session-026.t2.fresh"),
     {"outcome": "action_done", "owner_rules": ["autonomous_actions"]},
     "D4 (owner, 2026-11-24): Studio is autonomous; a schedule or alert request runs and the "
     "turn ends in action_done (corpus_patch session-025.t2)."),
    (("session-005.t5", "session-005.t5.fresh", "session-006.t4", "session-006.t4.fresh",
      "session-037.t3", "session-037.t3.fresh"),
     {"outcome": "limitation", "owner_rules": ["save_from_chat"],
      "judge_focus": ["points the user to the Save button instead of claiming to save"]},
     "D3 (owner): there is no save-from-chat tool; the expectation is a limitation that "
     "points to the Save button (save.suggested)."),
    (("session-006.t3",), {"owner_rules": ["history_table"]},
     "D5 (owner): an answer restated from history as a markdown table fails UI."),
]

#: Units whose figures came from the MCP fixture (typed-in v1 ``expect_numbers``):
#: v3 keeps only the governed part as a reference; the external figures are a
#: judge focus because no reference query can produce them.
MCP_REFERENCES = {
    "mcp-001": (TOTAL_SQL, ["value"]), "mcp-002": (JUNE_SQL, ["revenue"]),
    "mcp-003": (MONTHLY_SQL, ["value"]), "mcp-004": (JUNE_SQL, ["revenue"]),
    "skill-001": (MONTHLY_SQL, ["value"]), "skill-002": (JUNE_SQL, ["revenue"]),
}


def semantic_measures(sql: str) -> list[str]:
    """Semantic-model measures a reference needs, read from its aggregate calls."""
    text = "".join(sql.upper().split())
    out = []
    if "COUNT(DISTINCTCUSTOMER_ID)" in text:
        out.append("customer_count")
    if "SUM(TOTAL_AMOUNT)" in text:
        out.append("revenue")
    if "AVG(TOTAL_AMOUNT)" in text:
        out.append("average_order_value")
    if "COUNT(ORDER_ID)" in text or "COUNT(*)" in text:
        out.append("order_count")
    return out


def _numeric_columns(result: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(measure columns, key columns) of a v1 user result, by cell type."""
    from tests.benchmark.studio_capability.v2.fold import num

    columns = [str(c) for c in result.get("columns") or []]
    rows = [r for r in result.get("rows") or [] if isinstance(r, list)]
    measures, keys = [], []
    for index, column in enumerate(columns):
        cells = [r[index] for r in rows if index < len(r) and r[index] is not None]
        if cells and all(num(c) is not None for c in cells):
            measures.append(column)
        else:
            keys.append(column)
    return measures, keys


def draft(unit: dict[str, Any], gt_items: dict[str, Any]) -> dict[str, Any]:
    hitl = unit.get("hitl") or {}
    expected_ui = [k for k in unit.get("expect_ui") or [] if k not in SOFT_UI]
    bindings = []
    if "interactive" in expected_ui or "slider" in expected_ui:
        bindings.append("control_live")
    if any(k.startswith("chart") for k in expected_ui):
        bindings.append("chart_dataset")
    judge_focus = [str(unit["note"])] if unit.get("note") else []
    out: dict[str, Any] = {
        "id": unit["id"], "corpus_version": V3_VERSION, "lang": unit["lang"],
        "question": unit["question"], "category": unit["category"],
        "difficulty": unit.get("difficulty", "medium"), "unit": unit["unit"],
        "session_id": unit.get("session_id"), "turn_index": unit.get("turn_index"),
        "twin_of": unit.get("twin_of"), "outcome": unit["expect_outcome"],
        "clarify": unit.get("clarify") or "never",
        "ui_kinds": expected_ui, "absent": list(unit.get("expect_absent") or []),
        "bindings": bindings, "leak_sql": unit.get("leak_sql"),
        "write_must_not_run": unit["category"] == "write_refusal",
        "tools_required": list(unit.get("expect_tools") or []),
        "judge_focus": judge_focus, "insight": unit.get("expect_insight"),
        "tags": list(unit.get("tags") or []), "owner_rules": [],
        "run": {"mode": unit.get("mode", "chat"),
                "agent": "fixture" if unit.get("agent") == "fixture" else "default",
                "hitl_policy": hitl.get("policy", "first"), "hitl_text": hitl.get("text"),
                "stop_after_s": unit.get("stop_after_s"),
                "expect_tool_failure": list(unit.get("expect_tool_failure") or [])},
        "numeric": None,
    }
    if unit["expect_outcome"] == "refuse":
        out["owner_rules"].append("refusal_alternative")  # D2
    gt = unit.get("gt")
    if gt and gt.get("numeric") != "none" and unit["expect_outcome"] == "answer":
        result = (gt_items.get(unit["id"]) or {}).get("user") or {}
        measures, keys = _numeric_columns(result)
        mode = str(gt.get("numeric", "all"))
        if mode.startswith("col:"):
            measures = [mode[4:]]
            keys = [str(c) for c in result.get("columns") or [] if c != mode[4:]]
        out["numeric"] = {"reference_sql": gt["sql"], "required_measures": measures,
                          "key_columns": keys, "rows": "first" if mode == "first" else "all",
                          "semantic_measures": semantic_measures(gt["sql"])}
    if unit["id"] in MCP_REFERENCES:
        sql, measures = MCP_REFERENCES[unit["id"]]
        out["numeric"] = {"reference_sql": sql, "required_measures": measures,
                          "key_columns": [], "rows": "all",
                          "semantic_measures": semantic_measures(sql)}
        out["judge_focus"].append("figures from the external service (FX rate, store "
                                  "targets) are stated as the tool returned them")
    return out


def apply(drafted: dict[str, Any], change: dict[str, Any]) -> None:
    for key, value in change.items():
        if key in {"required_measures", "reference_sql", "key_columns", "bind",
                   "alternatives", "alternative_measures"}:
            if drafted["numeric"] is not None:
                drafted["numeric"][key] = value
                if key == "reference_sql":
                    drafted["numeric"]["semantic_measures"] = semantic_measures(value)
        elif key == "primary_with_alternative":
            numeric = drafted["numeric"]
            if numeric is not None:  # the old primary becomes the accepted alternative
                numeric["alternatives"] = [numeric["reference_sql"]]
                numeric["alternative_measures"] = [numeric["required_measures"]]
                numeric["reference_sql"] = value
                numeric["required_measures"] = ["value"]
                numeric["semantic_measures"] = semantic_measures(value)
        elif key == "numeric":
            drafted["numeric"] = value
        elif key == "bindings":
            drafted["bindings"] = list(dict.fromkeys(drafted["bindings"] + value))
        elif key in {"owner_rules", "judge_focus"}:
            drafted[key] = list(dict.fromkeys(drafted[key] + value))
        else:
            drafted[key] = value


def typed(drafted: dict[str, Any]) -> Expectation:
    payload = {k: v for k, v in drafted.items() if k not in {
        "ui_kinds", "absent", "bindings", "leak_sql", "write_must_not_run"}}
    payload["ui"] = {"kinds": drafted["ui_kinds"], "absent": drafted["absent"],
                     "bindings": drafted["bindings"]}
    payload["governance"] = {"leak_sql": drafted["leak_sql"],
                             "write_must_not_run": drafted["write_must_not_run"]}
    if drafted["outcome"] in {"action_done"} and drafted.get("numeric"):
        payload["numeric"] = None
    return Expectation.model_validate(payload)


def convert(metrics: set[str] | None = None) -> tuple[list[Expectation], list[str]]:
    """``metrics``: the semantic model's measures; units needing a missing one are deferred."""
    metrics = corpus_v3.semantic_names() if metrics is None else metrics
    gt_items = corpus_v1.ground_truth().get("items") or {}
    drafts = {u["id"]: draft(u, gt_items) for u in corpus_v1.units()}
    log: list[str] = []
    for ids, change, reason in CHANGES:
        touched = [uid for uid in ids if uid in drafts]
        for uid in touched:
            apply(drafts[uid], change)
        log.append(f"- {', '.join(touched)}: {reason}")
    deferred = []
    for drafted in drafts.values():
        numeric = drafted.get("numeric")
        missing = [m for m in (numeric or {}).get("semantic_measures", []) if m not in metrics]
        if missing:
            drafted["deferred"] = f"the semantic model has no {', '.join(missing)}"
            deferred.append(drafted["id"])
    if deferred:
        log.append(f"- {', '.join(deferred)}: deferred until the semantic model has the "
                   "measures they need; linter rule 3.")
    return [typed(d) for d in drafts.values()], log


def trim_semantic_view(path: Path) -> dict[str, Any]:
    """Names and version of the semantic view, without owner or timestamps."""
    view = json.loads(path.read_text())
    active = next((v for v in view.get("versions") or []
                   if v.get("version") == view.get("active_version")), None)
    definition = (active or {}).get("definition") or {}
    fields = [f["name"] for d in definition.get("datasets") or [] for f in d.get("fields") or []]
    return {"name": view.get("name"),
            "version": f"v{view.get('active_version')}/{definition.get('version')}",
            "metrics": [m["name"] for m in definition.get("metrics") or []],
            "dimensions": fields,
            "units": {m["name"]: m.get("unit") for m in definition.get("metrics") or []}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--semantic-view", type=Path,
                        help="the demo_sales semantic view as the API returns it")
    args = parser.parse_args()
    semantic = trim_semantic_view(args.semantic_view) if args.semantic_view else None
    units, log = convert(set(semantic["metrics"]) if semantic else None)
    directory = corpus_v3.V3_DIR
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / corpus_v3.UNITS).open("w") as handle:
        for unit in units:
            handle.write(unit.model_dump_json(exclude_defaults=True) + "\n")
    if args.semantic_view:
        (directory / corpus_v3.SEMANTIC_MODEL).write_text(json.dumps(semantic, indent=1) + "\n")
    changelog = directory / "CHANGELOG.md"
    header = (f"# Corpus v3 changelog\n\n## {V3_VERSION}: converted from corpus v2 "
              "(2026-11-24.2)\n\nMechanical: every v2 unit becomes a typed expectation "
              "(`v2/expectation.py`). `gt.numeric` modes become `required_measures` (all "
              "numeric result columns, one `col:` column, or `rows: first`); `expect_ui` "
              "becomes enforced `ui.kinds` (prose, follow-ups and 'number' stay with the "
              "judge); typed-in `expect_numbers` are removed (MCP units keep the governed "
              "part as a reference).\n\nReviewed corrections:\n\n")
    changelog.write_text(header + "\n".join(log) + "\n")
    print(f"{len(units)} units written; {len(log)} change groups")


if __name__ == "__main__":
    main()
