"""Replay of interactive answers stored before the page layout language.

Answers stored under the retired OpenUI catalogs (``nova-openui-2`` and
``nova-openui-3``) are shown through the one layout renderer, read-only. Each
stored block is translated when the message is read, never rewritten in place:

* a ``Chart`` the server had drawn natively keeps its recorded chart and its
  ``draft_ref``, so its data still comes from the governed draft query;
* a ``Kpi`` becomes a ``Metric`` holding the first row's value, as before;
* any other data view (a ``Table``, an un-drawn chart, a map) becomes a
  ``ResultTable`` of the rows the block stored.

The block keeps its fallback text, shown when it holds no data view.

Controls, forms and media of the old catalogs are not carried over: they were
local to the old runtime, and an answer must not act from a replayed page.
"""

from __future__ import annotations

from typing import Any

from app.modules.assistant.events_v2 import PART_DELTA, PART_START
from app.modules.assistant.ui_layout import CATALOG, _literal

LEGACY_CATALOGS = frozenset({"nova-openui-2", "nova-openui-3"})
_MAX_ROWS = 50
_MAX_COLUMNS = 12
_MAX_BLOCKS = 12


def _call(name: str, *args: Any) -> str:
    return f"{name}({', '.join(_literal(arg) for arg in args)})"


def _table(title: Any, records: list[Any], columns: Any = None) -> str | None:
    rows = [record for record in records if isinstance(record, dict)][:_MAX_ROWS]
    if not rows:
        return None
    names = [str(name) for name in columns] if isinstance(columns, list) and columns else list(
        rows[0])
    names = [name for name in names if any(name in row for row in rows)][:_MAX_COLUMNS]
    if not names:
        return None
    return _call("ResultTable", str(title)[:160] if isinstance(title, str) and title else None,
                 names, [[row.get(name) for name in names] for row in rows])


def _metric(node: Any, records: list[Any]) -> str | None:
    column = node.prop("column")
    first = records[0] if records and isinstance(records[0], dict) else {}
    value = first.get(column) if isinstance(column, str) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    label = node.prop("label") if isinstance(node.prop("label"), str) else column
    digits = node.prop("digits")
    args: list[Any] = [str(label)[:120] or "Value", value, "number"]
    if isinstance(digits, int) and not isinstance(digits, bool) and 0 <= digits <= 6:
        args += [None, None, None, digits]
    return _call("Metric", *args)


def legacy_program(block: dict[str, Any]) -> str:
    """The layout program that replays a legacy block."""
    from app.modules.viz.openui_nodes import OpenUiParseError, data_nodes

    evidence = block.get("evidence") if isinstance(block.get("evidence"), dict) else {}
    charts = [item for item in block.get("charts") or [] if isinstance(item, dict)]
    try:
        nodes = data_nodes(str(block.get("program") or ""))
    except OpenUiParseError:
        nodes = []
    blocks: list[str] = []
    metrics: list[str] = []
    drawn = 0
    for node in nodes:
        records = evidence.get(node.evidence_id or "") or []
        if node.component == "Kpi":
            if (metric := _metric(node, records)):
                metrics.append(metric)
            continue
        if node.component == "Chart" and node.evidence_id is not None:
            native = charts[drawn] if drawn < len(charts) else None
            drawn += 1
            if native and isinstance(native.get("chart"), dict) and native.get("draft_ref"):
                title = native.get("title") or node.prop("title") or "Chart"
                blocks.append(_call("DatavizChart", str(title)[:160], native["chart"],
                                    str(native["draft_ref"])))
                continue
        columns = node.prop("columns")
        if node.component == "Chart":
            y = node.prop("y")
            columns = [node.prop("x"), *(y if isinstance(y, list) else [y])]
            columns = [column for column in columns if isinstance(column, str)]
        if (table := _table(node.prop("title"), records, columns)):
            blocks.append(table)
    if metrics:
        blocks.insert(0, f"KpiTiles([{', '.join(metrics[:6])}])")
    blocks = blocks[:_MAX_BLOCKS]
    names = [f"b{index}" for index in range(len(blocks))]
    lines = [f"root = Page([{', '.join(names)}])"]
    lines += [f"{name} = {text}" for name, text in zip(names, blocks, strict=True)]
    return "\n".join(lines)


def _visible_charts(charts: Any) -> list[dict[str, Any]]:
    return [{key: value for key, value in item.items() if key != "source"}
            for item in charts or [] if isinstance(item, dict)]


def migrate_steps(steps: list[Any]) -> list[Any]:
    """Steps with every legacy ui block, and its journal part, replayed as a layout."""
    programs: dict[str, str] = {}
    out: list[Any] = []
    for step in steps:
        if (isinstance(step, dict) and step.get("kind") == "ui"
                and step.get("catalog_version") in LEGACY_CATALOGS):
            program = legacy_program(step)
            programs[str(step.get("content_id") or "")] = program
            step = {
                key: value for key, value in step.items()
                if key not in {"evidence", "actions", "program_sha256", "state_defaults"}
            } | {"catalog_version": CATALOG, "program": program, "state_defaults": {},
                 "provenance": {**(step.get("provenance") or {}), "legacy": True}}
        out.append(step)
    if not programs:
        return out
    return [_migrate_journal(step, programs) for step in out]


def _migrate_journal(step: Any, programs: dict[str, str]) -> Any:
    if not (isinstance(step, dict) and step.get("kind") == "event_journal"):
        return step
    legacy: set[str] = set()
    events: list[Any] = []
    for entry in step.get("events") or []:
        data = entry.get("data") if isinstance(entry, dict) else None
        if not isinstance(data, dict):
            continue
        part_id = str(data.get("part_id"))
        if entry.get("event") == PART_START and data.get("type") == "ui":
            block = data.get("block") if isinstance(data.get("block"), dict) else {}
            program = programs.get(str(data.get("content_id") or ""))
            if block.get("catalog_version") in LEGACY_CATALOGS and program is not None:
                legacy.add(part_id)
                visible = {key: value for key, value in block.items()
                           if key not in {"evidence", "actions", "program_sha256"}}
                visible |= {"catalog_version": CATALOG, "state_defaults": {},
                            "charts": _visible_charts(block.get("charts"))}
                events.append({**entry, "data": {**data, "block": visible}})
                events.append({"event": PART_DELTA, "data": {"part_id": data.get("part_id"),
                                                             "text": program}})
                continue
        if entry.get("event") == PART_DELTA and part_id in legacy:
            continue
        events.append(entry)
    return {**step, "events": events}
