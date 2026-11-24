"""Fold a recorded Studio turn into structured signals.

The input is the runner's turn record (``turns/<id>.json``): the protocol-v2
event journal plus what the runner attached (dataset previews, thread trace
steps, a deep-research result). The answer text is kept only as a length:
nothing here reads prose. Figures come from typed places: table parts (rows
bound to a ``tool_call_id``), KPI items (``evidence_id`` + ``column``), page
programs evaluated by :mod:`ui_tree`, bound dataset rows, and Nova's own
numeric claims when the product sends them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from tests.benchmark.studio_capability.v2 import ui_tree
from tests.benchmark.studio_capability.v2.outcome import Claim, TurnOutcome, product_outcome

PUBLIC_COMPONENTS = frozenset({"PlaceMap", "Picture", "Image", "SearchImage", "RouteMap"})
DATA_COMPONENTS = frozenset({"DatavizChart", "GeoMap", "DataMap"})
SHOWN_SOURCES = frozenset({"table", "kpi", "page_metric", "result_table", "dataset_preview"})


def num(value: Any) -> float | None:
    """A typed cell or prop as a number, by type conversion only."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return float(Decimal(value.strip()))
        except (InvalidOperation, ValueError):
            return None
    return None


@dataclass(frozen=True)
class Evidence:
    """One number the user could see, with where it came from."""

    value: float
    source: str
    tool: str | None = None
    ref: str | None = None
    column: str | None = None
    decimals: int | None = None


@dataclass
class Signals:
    finish_reason: str | None = None
    run_errors: list[dict[str, Any]] = field(default_factory=list)
    transport_error: str | None = None
    cancelled: bool = False
    tools: list[dict[str, Any]] = field(default_factory=list)
    hitl: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    components: list[dict[str, Any]] = field(default_factory=list)
    pages: list[ui_tree.Page] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    drafts: list[dict[str, Any]] = field(default_factory=list)
    save_suggestions: list[dict[str, Any]] = field(default_factory=list)
    artifacts_saved: int = 0
    suggestions: int = 0
    answer_chars: int = 0
    business_results: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    elements: set[str] = field(default_factory=set)
    product_outcome: TurnOutcome | None = None
    claims: list[Claim] = field(default_factory=list)
    trace_facts: dict[str, Any] = field(default_factory=dict)
    datasets_bound: list[str] = field(default_factory=list)
    deep_research: dict[str, Any] | None = None

    def tool_ok(self, names: set[str] | frozenset[str] | None = None) -> bool:
        return any(t.get("status") == "done" and t.get("exec") != "not_run"
                   and (names is None or t.get("name") in names) for t in self.tools)

    @property
    def data_shown(self) -> bool:
        return any(e.source in SHOWN_SOURCES for e in self.evidence) or any(
            c.get("component") in DATA_COMPONENTS for c in self.components)

    @property
    def public_content_shown(self) -> bool:
        return any(c.get("component") in PUBLIC_COMPONENTS for c in self.components)

    @property
    def page_text_only(self) -> bool:
        return bool(self.pages) and not self.data_shown and any(
            c.get("component") in {"Text", "Header"} for c in self.components)

    @property
    def hitl_pending(self) -> bool:
        return any(h["status"] not in {"answered", "declined"} for h in self.hitl)

    @property
    def page_dangling_refs(self) -> int:
        return sum(len(page.dangling) for page in self.pages)

    @property
    def inert_controls(self) -> list[str]:
        return [name for page in self.pages for name in page.inert_controls]

    @property
    def tool_names(self) -> list[str]:
        return [str(t.get("name")) for t in self.tools if t.get("name")]


def fold(record: dict[str, Any]) -> Signals:
    sig = Signals(transport_error=record.get("transport_error"),
                  cancelled=bool(record.get("cancelled_by_runner")))
    if record.get("deep_research") is not None and not record.get("events"):
        from tests.benchmark.studio_capability.v2.adapters import fold_deep_research

        return fold_deep_research(record, sig)
    parts: dict[str, dict[str, Any]] = {}
    order: list[dict[str, Any]] = []
    call_tool: dict[str, str] = {}
    for entry in record.get("events") or []:
        name = entry.get("event")
        data = _v if isinstance(_v := entry.get("data"), dict) else {}
        pid = str(data.get("part_id"))
        if name == "part.start":
            part = {"type": data.get("type"), "tool": data.get("tool_name"),
                    "call": data.get("tool_call_id"), "block": data.get("block") or {},
                    "text": str(data.get("text") or ""), "status": "", "reason": None,
                    "classification": data.get("classification"), "health": None}
            parts[pid] = part
            order.append(part)
            if part["type"] == "tool" and part["call"]:
                call_tool[str(part["call"])] = str(part["tool"])
        elif name == "part.delta" and pid in parts:
            text = str(data.get("text") or "")
            parts[pid]["text"] = text if data.get("replace") else parts[pid]["text"] + text
        elif name == "part.end" and pid in parts:
            if isinstance(data.get("final"), str) and parts[pid]["type"] in {"text", "ui"}:
                parts[pid]["text"] = data["final"]
        elif name == "tool.status" and pid in parts:
            parts[pid]["status"] = data.get("status") or parts[pid]["status"]
            if data.get("reason"):
                parts[pid]["reason"] = data["reason"]
        elif name == "tool.evidence" and pid in parts and data.get("kind") == "health":
            parts[pid]["health"] = data.get("payload") or {}
        elif name == "input.required":
            sig.hitl.append({"part": pid, "kind": data.get("kind"), "status": "pending"})
        elif name == "input.resolved":
            for item in sig.hitl:
                if item["part"] == pid:
                    item["status"] = data.get("status") or "resolved"
        elif name == "artifact.draft":
            sig.drafts.append(dict(data))
        elif name == "save.suggested":
            sig.save_suggestions.append(dict(data))
        elif name == "artifact.saved":
            sig.artifacts_saved += 1
        elif name == "suggestions":
            sig.suggestions += len(data.get("suggestions") or [])
        elif name == "business_result":
            sig.business_results.append({k: data.get(k) for k in ("status", "reason",
                                                                  "error_code")})
        elif name == "run.error":
            sig.run_errors.append({"code": data.get("code")})
        elif name == "run.finished":
            sig.finish_reason = data.get("finish_reason")
            sig.usage = data.get("usage") or {}
            sig.product_outcome = product_outcome(data.get("outcome"))

    for part in order:
        _fold_part(sig, part, call_tool)
    _table_aggregates(sig)
    _component_evidence(sig)
    _attachments(sig, record)
    sig.elements = ui_tree.kinds(
        sig.components, tables=len(sig.tables), text=bool(sig.answer_chars),
        drafts=len(sig.drafts), save_suggestions=len(sig.save_suggestions),
        saved=sig.artifacts_saved, suggestions=sig.suggestions,
        hitl_kinds=[str(h.get("kind")) for h in sig.hitl])
    return sig


def _fold_part(sig: Signals, part: dict[str, Any], call_tool: dict[str, str]) -> None:
    kind = part["type"]
    if kind == "tool":
        health = part["health"] or {}
        facts = health.get("facts") or {}
        sig.tools.append({"name": part["tool"], "call": part["call"], "status": part["status"],
                          "reason": part["reason"], "classification": part["classification"],
                          "exec": facts.get("execution_status"),
                          "reasons": health.get("reasons") or []})
    elif kind == "text":
        sig.answer_chars += len(part["text"].strip())
    elif kind == "table":
        block = part["block"]
        call = block.get("tool_call_id")
        tool = call_tool.get(str(call)) if call else None
        columns = [str(c) for c in block.get("columns") or []]
        rows = [r for r in block.get("rows") or [] if isinstance(r, list | dict)]
        sig.tables.append({"columns": columns, "rows": rows, "tool": tool, "call": call})
        for row in rows:
            cells = row if isinstance(row, list) else [row.get(c) for c in columns]
            for index, cell in enumerate(cells):
                value = num(cell)
                if value is not None:
                    sig.evidence.append(Evidence(value, "table", tool, call,
                                                 columns[index] if index < len(columns) else None))
    elif kind == "component":
        block = part["block"]
        node = {"component": block.get("component"), "props": block.get("props") or {}}
        sig.components.append(node)
        sig.schema_errors.extend(ui_tree.schema_errors(node))
    elif kind == "ui":
        page = ui_tree.parse_page(part["text"])
        if page.statements:
            sig.pages.append(page)
            sig.components.extend(page.nodes)
            for node in page.nodes:
                sig.schema_errors.extend(ui_tree.schema_errors(node))


def _table_aggregates(sig: Signals) -> None:
    """Count, sum, avg, min and max per table column: the aggregates @Value allows."""
    for table in sig.tables:
        columns = table["columns"]
        rows = [r if isinstance(r, list) else [r.get(c) for c in columns] for r in table["rows"]]
        if not rows:
            continue
        sig.evidence.append(Evidence(float(len(rows)), "table_agg:count", table["tool"],
                                     table["call"], None))
        for index, column in enumerate(columns):
            values = [v for v in (num(r[index]) for r in rows if index < len(r)) if v is not None]
            if len(values) < 2:
                continue
            for how, value in (("sum", sum(values)), ("avg", sum(values) / len(values)),
                               ("min", min(values)), ("max", max(values))):
                sig.evidence.append(Evidence(value, f"table_agg:{how}", table["tool"],
                                             table["call"], column))


def _component_evidence(sig: Signals) -> None:
    for node in sig.components:
        props = node.get("props") or {}
        component = node.get("component")
        if component == "KpiTiles":
            for item in props.get("items") or []:
                if not isinstance(item, dict) or "component" in item:
                    continue  # page form: Metric(...) nodes are visited on their own
                value = num(item.get("value"))
                if value is not None:
                    decimals = item.get("decimals")
                    sig.evidence.append(Evidence(
                        value, "kpi", None, item.get("evidence_id"), item.get("column"),
                        decimals if isinstance(decimals, int) else None))
        elif component == "Metric":
            value = num(props.get("value"))
            if value is not None:
                sig.evidence.append(Evidence(value, "page_metric", None, None,
                                             str(props.get("label") or "") or None))
        elif component == "ResultTable":
            columns = [str(c) for c in props.get("columns") or []]
            for row in props.get("rows") or []:
                for index, cell in enumerate(row if isinstance(row, list) else []):
                    value = num(cell)
                    if value is not None:
                        sig.evidence.append(Evidence(
                            value, "result_table", None, None,
                            columns[index] if index < len(columns) else None))
        elif component == "DatavizChart":
            chart = _v if isinstance(_v := props.get("chart"), dict) else {}
            if chart.get("dataset_id"):
                sig.datasets_bound.append(str(chart["dataset_id"]))


def _attachments(sig: Signals, record: dict[str, Any]) -> None:
    """What the runner attached after the turn (H2 adapters)."""
    from tests.benchmark.studio_capability.v2 import adapters

    sig.evidence.extend(adapters.dataset_evidence(record, sig.datasets_bound))
    facts = adapters.trace_facts(record)
    if facts:
        sig.trace_facts = facts
    if sig.product_outcome is not None and sig.product_outcome.claims:
        sig.claims = list(sig.product_outcome.claims)
    elif facts.get("claims"):
        sig.claims = list(facts["claims"])
    for claim in sig.claims:
        if claim.supported and claim.value is not None:
            sig.evidence.append(Evidence(float(claim.value), "claim", None, claim.evidence_id,
                                         claim.column))
