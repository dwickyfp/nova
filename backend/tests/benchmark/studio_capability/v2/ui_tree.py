"""The UI a turn showed, read as a component tree.

Pages (OpenUI Lang programs) are parsed with Nova's own parser
(``ui_layout.parse_statement``) and evaluated with the state defaults the page
declares, so ``Metric(..., base * (1 + $uplift / 100))`` yields the number the
user saw first. Positional arguments map to prop names in the order of
``ui_components.json``, the schema the server validator and the frontend share.
Every evaluated node is validated against that schema with the same
JSON Schema draft the server uses. Nothing here reads answer text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.modules.assistant import ui_layout
from app.modules.assistant.ui_layout import (
    BinOp,
    Call,
    LayoutError,
    ListE,
    Lit,
    ObjE,
    Ref,
    Ternary,
    Unary,
    parse_statement,
)

CONTROLS = frozenset({"Controls", "Slider", "Segmented", "Toggle"})
#: Controls bound to a $variable (Controls itself only groups them).
BOUND_CONTROLS = frozenset({"Slider", "Segmented", "Toggle"})
MAX_DEPTH = 40


@cache
def layout_contract() -> dict[str, Any]:
    path = Path(ui_layout.__file__).with_name("ui_components.json")
    return dict(json.loads(path.read_text())["layout"])


@cache
def prop_order(component: str) -> tuple[str, ...]:
    spec = layout_contract()["components"].get(component) or {}
    return tuple(((spec.get("props") or {}).get("properties") or {}).keys())


@cache
def _validator(component: str) -> Draft202012Validator | None:
    spec = layout_contract()["components"].get(component)
    if not spec:
        return None
    return Draft202012Validator(spec.get("props") or {"type": "object"})


def schema_errors(node: dict[str, Any]) -> list[str]:
    """Schema problems of one evaluated node (component name and its props)."""
    validator = _validator(str(node.get("component")))
    if validator is None:
        return [f"unknown component {node.get('component')}"]
    # The server drops null arguments before validating (``ui_layout._Checker._args``).
    props = {k: v for k, v in (node.get("props") or {}).items()
             if not k.startswith("_") and v is not None}
    # Child nodes are validated on their own; here they stand for "some value".
    props = {k: _shallow(v) for k, v in props.items()}
    return [f"{node.get('component')}.{'/'.join(map(str, e.path)) or '-'}: {e.validator}"
            for e in validator.iter_errors(props)]


def _shallow(value: Any) -> Any:
    if isinstance(value, dict) and "component" in value and "props" in value:
        return {}
    if isinstance(value, list):
        return [_shallow(item) for item in value]
    return value


# ── evaluation ─────────────────────────────────────────────────────────

class _Evaluator:
    def __init__(self, statements: dict[str, Any]):
        self.statements = statements
        self.memo: dict[str, Any] = {}

    def ref(self, name: str, depth: int) -> Any:
        if name in self.memo:
            return self.memo[name]
        if name not in self.statements or depth > MAX_DEPTH:
            return None
        self.memo[name] = None  # a cycle evaluates to nothing
        value = self.value(self.statements[name], depth + 1)
        self.memo[name] = value
        return value

    def value(self, node: Any, depth: int = 0) -> Any:
        if depth > MAX_DEPTH:
            return None
        if isinstance(node, Lit):
            return node.value
        if isinstance(node, Ref):
            return self.ref(node.name, depth)
        if isinstance(node, ListE):
            return [self.value(item, depth + 1) for item in node.items]
        if isinstance(node, ObjE):
            return {key: self.value(item, depth + 1) for key, item in node.pairs}
        if isinstance(node, Unary):
            operand = self.value(node.operand, depth + 1)
            if node.op == "-" and _is_number(operand):
                return -operand
            if node.op == "!":
                return not operand
            return None
        if isinstance(node, Ternary):
            chosen = node.then if self.value(node.cond, depth + 1) else node.other
            return self.value(chosen, depth + 1)
        if isinstance(node, BinOp):
            return _binary(node.op, self.value(node.left, depth + 1),
                           self.value(node.right, depth + 1))
        if isinstance(node, Call):
            names = prop_order(node.name)
            args = [self.value(arg, depth + 1) for arg in node.args]
            props = {names[i] if i < len(names) else f"_{i}": arg for i, arg in enumerate(args)}
            bound = [arg.name for arg in node.args if isinstance(arg, Ref)]
            return {"component": node.name, "props": props, "_refs": bound}
        return None


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _binary(op: str, left: Any, right: Any) -> Any:
    if _is_number(left) and _is_number(right):
        try:
            if op == "+":
                return left + right
            if op == "-":
                return left - right
            if op == "*":
                return left * right
            if op == "/":
                return left / right if right else None
            if op == "%":
                return left % right if right else None
        except ArithmeticError:
            return None
    if op in {"+", "-", "*", "/", "%"}:
        return None
    try:
        return {"==": left == right, "!=": left != right, "<": left < right,
                "<=": left <= right, ">": left > right, ">=": left >= right,
                "&&": left and right, "||": left or right}.get(op)
    except TypeError:
        return None


def _refs(node: Any, out: set[str]) -> None:
    if isinstance(node, Ref):
        out.add(node.name)
    elif isinstance(node, Call):
        for arg in node.args:
            _refs(arg, out)
    elif isinstance(node, ListE):
        for item in node.items:
            _refs(item, out)
    elif isinstance(node, ObjE):
        for _, item in node.pairs:
            _refs(item, out)
    elif isinstance(node, BinOp):
        _refs(node.left, out)
        _refs(node.right, out)
    elif isinstance(node, Unary):
        _refs(node.operand, out)
    elif isinstance(node, Ternary):
        for item in (node.cond, node.then, node.other):
            _refs(item, out)


def _calls(node: Any, out: list[Call]) -> None:
    if isinstance(node, Call):
        out.append(node)
        for arg in node.args:
            _calls(arg, out)
    elif isinstance(node, ListE):
        for item in node.items:
            _calls(item, out)
    elif isinstance(node, ObjE):
        for _, item in node.pairs:
            _calls(item, out)
    elif isinstance(node, BinOp):
        _calls(node.left, out)
        _calls(node.right, out)
    elif isinstance(node, Unary):
        _calls(node.operand, out)
    elif isinstance(node, Ternary):
        for item in (node.cond, node.then, node.other):
            _calls(item, out)


def walk(node: Any, out: list[dict[str, Any]]) -> None:
    """Flatten evaluated component nodes in reading order."""
    if isinstance(node, dict) and "component" in node and "props" in node:
        out.append(node)
        for value in node["props"].values():
            walk(value, out)
    elif isinstance(node, list):
        for item in node:
            walk(item, out)


@dataclass
class Page:
    """One page program, parsed and evaluated at its declared defaults."""

    statements: dict[str, Any] = field(default_factory=dict)
    parse_errors: int = 0
    nodes: list[dict[str, Any]] = field(default_factory=list)
    dangling: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    inert_controls: list[str] = field(default_factory=list)


def parse_page(program: str) -> Page:
    page = Page()
    for line in program.split("\n"):
        if not line.strip():
            continue
        try:
            name, expression = parse_statement(line)
        except LayoutError:
            page.parse_errors += 1
            continue
        page.statements[name] = expression
    if not page.statements:
        return page
    evaluator = _Evaluator(page.statements)
    page.state = {name: evaluator.ref(name, 0) for name in page.statements
                  if name.startswith("$")}
    roots = ["root"] if "root" in page.statements else list(page.statements)
    for name in roots:
        walk(evaluator.ref(name, 0), page.nodes)
    used: set[str] = set()
    for expression in page.statements.values():
        _refs(expression, used)
    page.dangling = sorted(name for name in used if name not in page.statements)
    page.inert_controls = _inert_controls(page.statements)
    return page


def _inert_controls(statements: dict[str, Any]) -> list[str]:
    """$variables bound to a control that no displayed figure or chart depends on."""
    calls: list[Call] = []
    for expression in statements.values():
        _calls(expression, calls)
    bound: set[str] = set()
    for call in calls:
        if call.name in BOUND_CONTROLS and "value" in prop_order(call.name):
            index = prop_order(call.name).index("value")
            arg = call.args[index] if index < len(call.args) else None
            if isinstance(arg, Ref):
                bound.add(arg.name)
    if not bound:
        return []
    affected: set[str] = set()
    for call in calls:
        if call.name in CONTROLS:
            continue
        for arg in call.args:
            if isinstance(arg, Call):
                continue  # nested nodes are visited as calls of their own
            _closure(arg, statements, affected, set())
    return sorted(bound - affected)


def _closure(node: Any, statements: dict[str, Any], out: set[str], seen: set[str]) -> None:
    names: set[str] = set()
    _refs(node, names)
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        out.add(name)
        expression = statements.get(name)
        if expression is None or (isinstance(expression, Call)
                                  and expression.name in CONTROLS | {"Page", "Section", "Row"}):
            continue
        _closure(expression, statements, out, seen)


# ── vocabulary ─────────────────────────────────────────────────────────

#: The UI kinds expectations may name; the corpus linter checks against this set.
UI_KINDS = frozenset({
    "text", "table", "chart", "chart:bar", "chart:line", "chart:pie", "chart:area",
    "chart:scatter", "chart:combo", "chart:other", "kpi", "map", "place_map", "route",
    "route:alternatives", "route:unavailable", "route:optimized", "places:3+", "picture",
    "picture:shown", "interactive", "slider", "page", "section", "artifact_preview",
    "save_suggestion", "artifact_saved", "suggestions", "hitl:question", "hitl:choice",
    "hitl:form", "number",
})


def kinds(nodes: list[dict[str, Any]], *, tables: int = 0, text: bool = False,
          drafts: int = 0, save_suggestions: int = 0, saved: int = 0, suggestions: int = 0,
          hitl_kinds: list[str] | None = None) -> set[str]:
    """The corpus UI vocabulary, read from the tree and the typed events."""
    out: set[str] = set()
    names = {str(node.get("component")) for node in nodes}
    if text:
        out.add("text")
    if tables or "ResultTable" in names or "Table" in names:
        out.add("table")
    for node in nodes:
        props = node.get("props") or {}
        component = node.get("component")
        if component in {"DatavizChart", "Chart"}:
            chart = _v if isinstance(_v := props.get("chart"), dict) else {}
            visual = str(chart.get("visual") or props.get("type") or "other")
            encoding = _v if isinstance(_v := chart.get("encoding"), dict) else {}
            if visual == "donut" or encoding.get("donut"):
                visual = "pie"
            out.add("chart")
            out.add(f"chart:{visual}" if f"chart:{visual}" in UI_KINDS else "chart:other")
        elif component == "PlaceMap":
            out |= {"map", "place_map"}
            routes = _v if isinstance(_v := props.get("routes"), list) else []
            places = _v if isinstance(_v := props.get("places"), list) else []
            if routes:
                out.add("route")
            if len(routes) >= 2:
                out.add("route:alternatives")
            if props.get("unavailable"):
                out.add("route:unavailable")
            if props.get("optimized") is True:
                out.add("route:optimized")
            if len(places) >= 3:
                out.add("places:3+")
        elif component in {"Picture", "Image", "SearchImage"}:
            out.add("picture")
            if props.get("url") or props.get("src"):
                out.add("picture:shown")
        elif component in {"GeoMap", "DataMap", "RouteMap"}:
            out.add("map")
    if names & {"KpiTiles", "Metric"}:
        out.add("kpi")
    if names & CONTROLS:
        out.add("interactive")
    if "Slider" in names:
        out.add("slider")
    if "Page" in names:
        out.add("page")
    if "Section" in names:
        out.add("section")
    if drafts:
        out.add("artifact_preview")
    if save_suggestions:
        out.add("save_suggestion")
    if saved:
        out.add("artifact_saved")
    if suggestions:
        out.add("suggestions")
    for kind in hitl_kinds or []:
        out.add(f"hitl:{kind}")
    return out
