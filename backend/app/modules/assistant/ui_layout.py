"""Page layouts: OpenUI Lang programs checked and published one statement at a time.

A Studio answer that asks for a dashboard, an overview or a what-if is a page:
an OpenUI Lang program (``name = Component(args)`` per line) in the
``nova-layout-1`` catalog. Its schema has one source, the Zod module
``frontend/src/features/studio/runtime/components/layout.ts``, exported to
``ui_components.json`` (key ``layout``); there is no second validator.

The model writes the program while its answer streams. :class:`LayoutStream`
reads the deltas, cuts complete statements, and checks each one here, in
Python, before anything reaches the browser:

* every component and argument must match the schema;
* ``@Value(evidence, column, ...)`` is replaced by the number read from the
  verified result, so no figure on the page is typed by the model;
* ``Chart(evidence, ...)`` becomes a native ``DatavizChart`` over the result's
  governed query (signed ``draft_ref``), and ``Table(evidence, ...)`` a
  ``ResultTable`` of redacted rows;
* figures written in text (titles, narrative, badges) are checked against the
  verified results like any answer; unsupported clauses are removed.

A statement that fails is left out and its error recorded; the rest of the
page still renders (the browser shows an unresolved reference as nothing).
The browser receives only rewritten, checked statements, in the order the
model wrote them, so a replay of the stored program is the same page.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from app.modules.assistant.tools.redaction import is_credential_column, is_credential_value

CATALOG = "nova-layout-1"
MAX_PROGRAM_BYTES = 64_000
MAX_STATEMENTS = 80
MAX_STATEMENT_CHARS = 8_000
TABLE_ROWS = 50
#: Lang-core builtins a figure may pass through.
_NUMBER_FUNCTIONS = frozenset({"Round", "Abs", "Min", "Max", "Floor", "Ceil"})
_FENCE_OPEN = re.compile(r"```[ \t]*(?:openui|layout)[^\n]*\n", re.IGNORECASE)
_BARE_ROOT = re.compile(r"(?m)^[ \t]*root[ \t]*=[ \t]*Page[ \t]*\(")


class LayoutError(ValueError):
    """A statement does not satisfy the layout contract."""


# ── Syntax ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Lit:
    value: Any


@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple[Any, ...]
    function: bool = False  # ``@Name(...)``


@dataclass(frozen=True)
class ListE:
    items: tuple[Any, ...]


@dataclass(frozen=True)
class ObjE:
    pairs: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class BinOp:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class Unary:
    op: str
    operand: Any


@dataclass(frozen=True)
class Ternary:
    cond: Any
    then: Any
    other: Any


_TOKEN = re.compile(
    r"""
    (?P<ws>[ \t\r\n]+|//[^\n]*)
  | (?P<string>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
  | (?P<number>\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|\.\d+)
  | (?P<func>@[A-Za-z_]\w*)
  | (?P<name>\$?[A-Za-z_]\w*)
  | (?P<op>==|!=|>=|<=|&&|\|\||[()\[\]{},:=?<>+\-*/!%])
    """,
    re.VERBOSE,
)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\", "/": "/"}
#: Binary operators by binding strength (higher binds tighter).
_BINARY = {"||": 1, "&&": 2, "==": 3, "!=": 3, "<": 4, ">": 4, "<=": 4, ">=": 4,
           "+": 5, "-": 5, "*": 6, "/": 6, "%": 6}


def _unquote(text: str) -> str:
    body = text[1:-1]
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            nxt = body[index + 1]
            if nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", body[index + 2:index + 6]):
                out.append(chr(int(body[index + 2:index + 6], 16)))
                index += 6
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match:
            raise LayoutError(f"Unexpected character {text[position]!r}")
        position = match.end()
        kind = match.lastgroup or ""
        if kind != "ws":
            out.append((kind, match.group()))
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.index = 0
        self.depth = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def text(self) -> str | None:
        token = self.peek()
        return token[1] if token else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise LayoutError("The statement ends too early")
        if value is not None and token[1] != value:
            raise LayoutError(f"Expected {value!r}, found {token[1]!r}")
        self.index += 1
        return token

    def expression(self, floor: int = 0) -> Any:
        self.depth += 1
        if self.depth > 40:
            raise LayoutError("The statement nests too deeply")
        try:
            left = self.unary()
            while True:
                op = self.text()
                if op == "?" and floor == 0:
                    self.take("?")
                    then = self.expression()
                    self.take(":")
                    left = Ternary(left, then, self.expression())
                    continue
                strength = _BINARY.get(op or "")
                if strength is None or strength <= floor:
                    return left
                self.take()
                left = BinOp(str(op), left, self.expression(strength))
        finally:
            self.depth -= 1

    def unary(self) -> Any:
        if self.text() in ("-", "!"):
            op = self.take()[1]
            operand = self.unary()
            if op == "-" and isinstance(operand, Lit) and isinstance(operand.value, int | float):
                return Lit(-operand.value)
            return Unary(op, operand)
        return self.primary()

    def primary(self) -> Any:
        kind, text = self.take()
        if kind == "string":
            return Lit(_unquote(text))
        if kind == "number":
            value = float(text)
            whole = value.is_integer() and not re.search(r"[.eE]", text)
            return Lit(int(value) if whole else value)
        if kind == "func":
            return Call(text[1:], self.arguments(), True)
        if kind == "name":
            if text in ("true", "false"):
                return Lit(text == "true")
            if text == "null":
                return Lit(None)
            if self.text() == "(" and not text.startswith("$"):
                return Call(text, self.arguments())
            return Ref(text)
        if text == "[":
            items = []
            while self.text() not in (None, "]"):
                items.append(self.expression())
                if self.text() != "]":
                    self.take(",")
            self.take("]")
            return ListE(tuple(items))
        if text == "{":
            pairs = []
            while self.text() not in (None, "}"):
                key_kind, key = self.take()
                if key_kind not in ("name", "string"):
                    raise LayoutError("An object key must be a name or a string")
                self.take(":")
                pairs.append((_unquote(key) if key_kind == "string" else key, self.expression()))
                if self.text() != "}":
                    self.take(",")
            self.take("}")
            return ObjE(tuple(pairs))
        if text == "(":
            inner = self.expression()
            self.take(")")
            return inner
        raise LayoutError(f"Unexpected {text!r}")

    def arguments(self) -> tuple[Any, ...]:
        self.take("(")
        args = []
        while self.text() not in (None, ")"):
            args.append(self.expression())
            if self.text() != ")":
                self.take(",")
        self.take(")")
        return tuple(args)


def parse_statement(text: str) -> tuple[str, Any]:
    """``(name, expression)`` of one ``name = expression`` statement."""
    tokens = _tokens(text)
    if len(tokens) < 3 or tokens[0][0] != "name" or tokens[1][1] != "=":
        raise LayoutError("A statement is `name = expression`")
    parser = _Parser(tokens[2:])
    value = parser.expression()
    if parser.peek() is not None:
        raise LayoutError(f"Unexpected {parser.text()!r} after the expression")
    return tokens[0][1], value


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _number(value: float | int) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) or float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(float(value))


def _literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        if not math.isfinite(value):
            return "null"
        return _number(value)
    if isinstance(value, Decimal):
        return _number(float(value))
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_quote(str(key))}: {_literal(item)}"
                               for key, item in value.items()) + "}"
    return _quote(str(value))


def serialize(node: Any) -> str:
    """The expression as OpenUI Lang text the browser parses back to ``node``."""
    if isinstance(node, Lit):
        return _literal(node.value)
    if isinstance(node, Ref):
        return node.name
    if isinstance(node, Call):
        return ("@" if node.function else "") + node.name + "(" + ", ".join(
            serialize(arg) for arg in node.args) + ")"
    if isinstance(node, ListE):
        return "[" + ", ".join(serialize(item) for item in node.items) + "]"
    if isinstance(node, ObjE):
        pairs = ", ".join(f"{_quote(key)}: {serialize(item)}" for key, item in node.pairs)
        return "{" + pairs + "}"
    if isinstance(node, BinOp):
        return f"({serialize(node.left)} {node.op} {serialize(node.right)})"
    if isinstance(node, Unary):
        return f"{node.op}({serialize(node.operand)})"
    if isinstance(node, Ternary):
        return f"({serialize(node.cond)} ? {serialize(node.then)} : {serialize(node.other)})"
    raise LayoutError("Unknown expression")


def _plain(node: Any) -> Any:
    """The Python value of a literal expression; raises for anything dynamic."""
    if isinstance(node, Lit):
        return node.value
    if isinstance(node, ListE):
        return [_plain(item) for item in node.items]
    if isinstance(node, ObjE):
        return {key: _plain(item) for key, item in node.pairs}
    raise LayoutError("Expected a literal value")


def _is_literal(node: Any) -> bool:
    try:
        _plain(node)
    except LayoutError:
        return False
    return True


# ── Contract ───────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    from app.modules.assistant.ui_components import contract as components

    layout = components().get("layout")
    if not isinstance(layout, dict) or layout.get("catalog") != CATALOG:
        raise LayoutError("The Studio layout contract is unreadable")
    return layout


def prompt() -> str:
    """The model's layout instructions, generated from the same schemas."""
    return str(contract()["prompt"])


def composer_prompt(*, enabled: bool, presentation: str) -> str:
    """The layout instructions for a turn: empty when pages are off for it.

    The planner marks a turn ``interactive`` when the person asked for a
    dashboard, an overview or a what-if; that turn should answer with a page.
    Every other turn may still choose one, under the same rules.
    """
    if not enabled:
        return ""
    if presentation == "interactive":
        return prompt() + "\nThis request asks for a page: answer with a layout."
    return prompt()


@lru_cache(maxsize=64)
def _prop_validator(component: str, prop: str) -> Draft202012Validator:
    schema = contract()["components"][component]["props"]["properties"][prop]
    return Draft202012Validator(schema)


def _spec(component: str) -> dict[str, Any]:
    entry = contract()["components"].get(component)
    if entry is None:
        raise LayoutError(f"Unknown component {component}")
    return entry


def _kind(schema: dict[str, Any]) -> str:
    """How an argument is checked: children, binding, number, text or literal."""
    types = schema.get("type")
    types = set(types) if isinstance(types, list) else {types} if types else set()
    if "array" in types and schema.get("items") in ({}, None):
        return "children"
    if not types and "anyOf" not in schema and "enum" not in schema:
        return "binding"
    if types and types <= {"number", "integer", "null"} and "integer" not in types:
        return "number"
    if "string" in types and types <= {"string", "null"} and "enum" not in schema \
            and "pattern" not in schema:
        return "text"
    return "literal"


# ── Checking one statement ─────────────────────────────────────────────

#: Builds a native chart for a result from a plan; returns the component payload or None.
ChartBuilder = Callable[
    [str, dict[str, Any], dict[str, Any], str], Awaitable[dict[str, Any] | None]
]
#: Signs the chart at a position of this layout, returns its ``draft_ref`` or None.
DraftSigner = Callable[[int], str | None]


@dataclass
class LayoutContext:
    """What checking a statement needs from the turn."""

    tables: dict[str, dict[str, Any]]
    question: str = ""
    language: str = "en"
    chart: ChartBuilder | None = None
    sign: DraftSigner | None = None
    #: Charts placed so far, in position order, with their governed source.
    charts: list[dict[str, Any]] = field(default_factory=list)
    evidence_ids: set[str] = field(default_factory=set)
    adjusted: bool = False


def _names(table: dict[str, Any]) -> list[str]:
    return [str(c.get("name", "")) if isinstance(c, dict) else str(c)
            for c in table.get("columns") or []]


def _rows(table: dict[str, Any]) -> list[dict[str, Any]]:
    names = _names(table)
    out = []
    for row in table.get("rows") or []:
        values = [row.get(name) for name in names] if isinstance(row, dict) else row
        if isinstance(values, list | tuple):
            out.append(dict(zip(names, values, strict=False)))
    return out


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float | Decimal):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _same(cell: Any, wanted: Any) -> bool:
    if isinstance(wanted, bool) or isinstance(cell, bool):
        return cell is wanted
    if isinstance(wanted, int | float):
        number = _to_number(cell)
        return number is not None and number == float(wanted)
    text = "" if cell is None else str(cell)
    # A date column reads as its ISO day or month: "2025-07" matches 2025-07-01.
    return text == wanted or (len(wanted) >= 7 and text.startswith(wanted)
                              and re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", wanted) is not None)


def resolve_value(args: tuple[Any, ...], ctx: LayoutContext) -> float:
    """The verified figure ``@Value(evidence, column, aggregation?, where?)`` names."""
    if not 2 <= len(args) <= 4:
        raise LayoutError("@Value takes (evidence, column, aggregation?, where?)")
    params = dict(zip(("evidence", "column", "aggregation", "where"),
                      (_plain(arg) for arg in args), strict=False))
    if params.get("aggregation") is None:
        params.pop("aggregation", None)
    if params.get("where") is None:
        params.pop("where", None)
    schema = contract()["functions"]["Value"]["params"]
    errors = list(Draft202012Validator(schema).iter_errors(params))
    if errors:
        raise LayoutError(f"@Value: {errors[0].message}"[:240])
    evidence = str(params["evidence"])
    table = ctx.tables.get(evidence)
    if table is None:
        raise LayoutError(f"@Value names no verified result {evidence!r}")
    column = str(params["column"])
    if column not in _names(table) or is_credential_column(column):
        raise LayoutError(f"The result {evidence!r} has no column {column!r}")
    rows = _rows(table)
    where = params.get("where") or {}
    for key in where:
        if key not in _names(table):
            raise LayoutError(f"The result {evidence!r} has no column {key!r}")
    rows = [row for row in rows if all(_same(row.get(k), v) for k, v in where.items())]
    how = params.get("aggregation", "value")
    if how == "count":
        value: float | None = float(sum(1 for row in rows if row.get(column) is not None))
    else:
        numbers = [_to_number(row.get(column)) for row in rows]
        present = [n for n in numbers if n is not None]
        if not present:
            raise LayoutError(f"{column!r} has no number in the matching rows of {evidence!r}")
        if how == "value":
            if len(rows) != 1 and where:
                raise LayoutError(f"{len(rows)} rows of {evidence!r} match; narrow `where`")
            value = present[0]
        elif how == "sum":
            value = sum(present)
        elif how == "avg":
            value = sum(present) / len(present)
        elif how == "min":
            value = min(present)
        else:
            value = max(present)
    ctx.evidence_ids.add(evidence)
    return float(value)


class _Checker:
    """Checks and rewrites the expression of one statement."""

    def __init__(self, ctx: LayoutContext, state_names: set[str]) -> None:
        self.ctx = ctx
        self.state_names = state_names
        self.texts: list[tuple[Callable[[str], Any], str]] = []

    # numbers ─────────────────────────────────────────────────────────

    def number(self, node: Any, *, sourced: list[bool]) -> Any:
        """A figure: literals, $state, statement references, @Value and arithmetic."""
        if isinstance(node, Lit):
            if node.value is not None and not isinstance(node.value, int | float):
                raise LayoutError("Expected a number")
            return node
        if isinstance(node, Ref):
            sourced[0] = True
            return node
        if isinstance(node, Call) and node.function and node.name == "Value":
            sourced[0] = True
            return Lit(resolve_value(node.args, self.ctx))
        if isinstance(node, Call) and node.function and node.name in _NUMBER_FUNCTIONS:
            return Call(node.name, tuple(self.number(arg, sourced=sourced) for arg in node.args),
                        True)
        if isinstance(node, BinOp) and node.op in {"+", "-", "*", "/", "%"}:
            return BinOp(node.op, self.number(node.left, sourced=sourced),
                         self.number(node.right, sourced=sourced))
        if isinstance(node, Unary) and node.op == "-":
            return Unary("-", self.number(node.operand, sourced=sourced))
        if isinstance(node, Ternary):
            return Ternary(self.condition(node.cond), self.number(node.then, sourced=sourced),
                           self.number(node.other, sourced=sourced))
        raise LayoutError("A figure must be @Value(...), a $variable or arithmetic over them")

    def condition(self, node: Any) -> Any:
        if isinstance(node, Lit | Ref):
            return node
        if isinstance(node, Unary) and node.op == "!":
            return Unary("!", self.condition(node.operand))
        if isinstance(node, BinOp):
            if node.op in {"&&", "||"}:
                return BinOp(node.op, self.condition(node.left), self.condition(node.right))
            if node.op in {"==", "!=", "<", ">", "<=", ">="}:
                return BinOp(node.op, self.scalar(node.left), self.scalar(node.right))
        return self.number(node, sourced=[False])

    def scalar(self, node: Any) -> Any:
        if isinstance(node, Lit) and isinstance(node.value, str):
            return node
        return self.number(node, sourced=[False])

    # text ────────────────────────────────────────────────────────────

    def text(self, node: Any) -> Any:
        """Display text: a literal (checked for figures) or a join with figures."""
        if isinstance(node, Lit):
            if node.value is None:
                return node
            if not isinstance(node.value, str):
                raise LayoutError("Expected text")
            return Lit(self.checked(node.value))
        if isinstance(node, BinOp) and node.op == "+":
            return BinOp("+", self.text_part(node.left), self.text_part(node.right))
        if isinstance(node, Ternary):
            return Ternary(self.condition(node.cond), self.text(node.then), self.text(node.other))
        raise LayoutError("Text must be a string, or strings joined with figures by +")

    def text_part(self, node: Any) -> Any:
        if isinstance(node, Lit) and isinstance(node.value, str) or (
            isinstance(node, BinOp) and node.op == "+"
        ):
            return self.text(node)
        if isinstance(node, Lit) and isinstance(node.value, int | float):
            # A figure typed into a sentence is text, checked like any.
            return Lit(self.checked(_number(node.value)))
        return self.number(node, sourced=[False])

    def checked(self, value: str) -> str:
        """The text with every unsupported figure's clause removed."""
        if not re.search(r"\d", value):
            return value
        from app.modules.assistant.answer_contract import check_numeric_answer, remove_unverified

        check = check_numeric_answer(value, question=self.ctx.question,
                                     tables=self.ctx.tables, language=self.ctx.language)
        if not check.unsupported:
            return value
        self.ctx.adjusted = True
        kept = remove_unverified(value, check.unsupported_spans)
        if not kept or not kept.strip():
            raise LayoutError("The text states figures the verified results do not support")
        return kept.strip()

    # components ──────────────────────────────────────────────────────

    def child(self, node: Any) -> Any:
        if isinstance(node, Ref) and not node.name.startswith("$"):
            return node
        if isinstance(node, Lit) and node.value is None:
            return node
        if isinstance(node, Call) and not node.function:
            return node  # checked by the caller through ``component``
        raise LayoutError("A child must be a component or a statement name")

    async def component(self, node: Call) -> Call:
        if node.name == "Chart":
            return await self.chart(node)
        if node.name == "Table":
            return self.table(node)
        spec = _spec(node.name)
        if spec["author"] == "server":
            raise LayoutError(f"{node.name} is written by Nova, not by the answer")
        schema = spec["props"]
        names = list(schema["properties"])
        if len(node.args) > len(names):
            raise LayoutError(f"{node.name} takes at most {len(names)} arguments")
        props = dict(zip(names, node.args, strict=False))
        for required in schema.get("required", []):
            arg = props.get(required)
            if arg is None or (isinstance(arg, Lit) and arg.value is None
                               and "null" not in str(schema["properties"][required].get("type"))):
                raise LayoutError(f"{node.name} needs {required}")
        args: list[Any] = []
        for name, arg in props.items():
            prop = schema["properties"][name]
            kind = _kind(prop)
            optional = name not in schema.get("required", [])
            if isinstance(arg, Lit) and arg.value is None and optional:
                args.append(arg)
                continue
            if kind == "children":
                if not isinstance(arg, ListE):
                    raise LayoutError(f"{node.name} {name} must be a list")
                if len(arg.items) > int(prop.get("maxItems", 24)):
                    raise LayoutError(f"{node.name} {name} has too many items")
                items = []
                for item in arg.items:
                    item = self.child(item)
                    items.append(await self.component(item) if isinstance(item, Call) else item)
                args.append(ListE(tuple(items)))
            elif kind == "binding":
                if not (isinstance(arg, Ref) and arg.name.startswith("$")):
                    raise LayoutError(f"{node.name} {name} must be a $variable")
                args.append(arg)
            elif kind == "number":
                sourced = [False]
                checked = self.number(arg, sourced=sourced)
                if node.name == "Metric" and name in ("value", "delta") and not sourced[0]:
                    raise LayoutError(f"A Metric {name} must come from @Value(...)")
                args.append(checked)
            elif kind == "text":
                checked_text = self.text(arg)
                if isinstance(checked_text, Lit):
                    self._validate(node.name, name, checked_text.value)
                args.append(checked_text)
            else:
                value = _plain(arg)
                self._validate(node.name, name, value)
                args.append(Lit(value) if not isinstance(arg, ListE | ObjE) else arg)
        if node.name == "Metric":
            self._metric_currency(props)
        return Call(node.name, tuple(args))

    def _metric_currency(self, props: dict[str, Any]) -> None:
        fmt = props.get("format")
        if isinstance(fmt, Lit) and fmt.value == "currency" and not isinstance(
            props.get("currency"), Lit
        ):
            raise LayoutError("A currency Metric needs the currency the data states")

    @staticmethod
    def _validate(component: str, prop: str, value: Any) -> None:
        errors = list(_prop_validator(component, prop).iter_errors(value))
        if errors:
            raise LayoutError(f"{component} {prop}: {errors[0].message}"[:240])

    def _args(self, name: str, node: Call) -> dict[str, Any]:
        schema = _spec(name)["props"]
        names = list(schema["properties"])
        if len(node.args) > len(names):
            raise LayoutError(f"{name} takes at most {len(names)} arguments")
        values = {key: _plain(arg) for key, arg in zip(names, node.args, strict=False)}
        values = {key: value for key, value in values.items() if value is not None}
        errors = list(Draft202012Validator(schema).iter_errors(values))
        if errors:
            where = ".".join(str(part) for part in errors[0].absolute_path) or "arguments"
            raise LayoutError(f"{name} {where}: {errors[0].message}"[:240])
        return values

    def _table(self, evidence: str) -> dict[str, Any]:
        table = self.ctx.tables.get(evidence)
        if table is None:
            raise LayoutError(f"No verified result {evidence!r}")
        self.ctx.evidence_ids.add(evidence)
        return table

    async def chart(self, node: Call) -> Call:
        from app.modules.agents.tools.data_to_chart import _explicit_plan

        args = self._args("Chart", node)
        table = self._table(args["evidence"])
        columns = _names(table)
        y = [args["y"]] if isinstance(args["y"], str) else list(args["y"])
        for name in [args["x"], *y, args.get("color")]:
            if name and name not in columns:
                raise LayoutError(f"The result {args['evidence']!r} has no column {name!r}")
        title = self.checked(str(args["title"]))
        plan = _explicit_plan({**args, "y": y, "title": title})
        if plan is None or self.ctx.chart is None:
            raise LayoutError("The chart cannot be drawn")
        built = await self.ctx.chart(args["evidence"], table, plan, title[:120])
        if built is None:
            raise LayoutError("The chart's result has no governed query to draw from")
        position = len(self.ctx.charts)
        ref = self.ctx.sign(position) if self.ctx.sign else None
        if not ref:
            raise LayoutError("A draft chart needs a saved conversation")
        props = built["props"]
        self.ctx.charts.append({"title": props["title"], "chart": props["chart"],
                                "source": built.get("source"), "draft_ref": ref,
                                **({"plan": built["plan"]} if built.get("plan") else {})})
        return Call("DatavizChart", (Lit(props["title"]), Lit(props["chart"]), Lit(ref)))

    def table(self, node: Call) -> Call:
        args = self._args("Table", node)
        table = self._table(args["evidence"])
        names = _names(table)
        wanted = list(args.get("columns") or names)[:12]
        for name in wanted:
            if name not in names:
                raise LayoutError(f"The result {args['evidence']!r} has no column {name!r}")
        shown = [name for name in wanted if not is_credential_column(name)]
        rows = []
        for row in _rows(table)[:TABLE_ROWS]:
            values = [row.get(name) for name in shown]
            if any(is_credential_value(value) for value in values):
                continue
            rows.append([_json_value(value) for value in values])
        title = args.get("title")
        return Call("ResultTable", (Lit(self.checked(title) if title else None), Lit(shown),
                                    Lit(rows)))


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        number = float(value)
        return int(number) if number.is_integer() else number
    return str(value)


@dataclass
class Statement:
    name: str
    text: str


async def check_statement(
    text: str, ctx: LayoutContext, *, state_names: set[str], defined: set[str],
) -> Statement:
    """The checked, rewritten statement, or :class:`LayoutError`."""
    if len(text) > MAX_STATEMENT_CHARS:
        raise LayoutError("The statement is too long")
    name, expression = parse_statement(text)
    if name in defined:
        raise LayoutError(f"{name} is defined twice")
    checker = _Checker(ctx, state_names)
    if name.startswith("$"):
        if not isinstance(expression, Lit) or isinstance(expression.value, dict | list):
            raise LayoutError("A $variable starts with a literal number, text or true/false")
        if isinstance(expression.value, str) and len(expression.value) > 200:
            raise LayoutError("A $variable default is too long")
        state_names.add(name)
        return Statement(name, f"{name} = {serialize(expression)}")
    if name == "root" and not (isinstance(expression, Call) and expression.name == "Page"):
        raise LayoutError("The root is root = Page([...])")
    if isinstance(expression, Call) and not expression.function:
        rewritten = await checker.component(expression)
    else:
        sourced = [False]
        rewritten = checker.number(expression, sourced=sourced)
        if not sourced[0]:
            raise LayoutError("A figure statement must come from @Value(...)")
    return Statement(name, f"{name} = {serialize(rewritten)}")


# ── Streaming ──────────────────────────────────────────────────────────


class _Splitter:
    """Cuts streamed program text into complete statements.

    A statement ends at a newline outside strings and brackets. The closing
    fence ends the program.
    """

    def __init__(self) -> None:
        self.buffer = ""
        self.depth = 0
        self.quote: str | None = None
        self.escape = False
        self.closed = False

    def feed(self, text: str) -> tuple[list[str], int | None]:
        """Complete statements in ``text``, and where in it the program closed."""
        out: list[str] = []
        for index, char in enumerate(text):
            self.buffer += char
            if self.quote:
                if self.escape:
                    self.escape = False
                elif char == "\\":
                    self.escape = True
                elif char == self.quote or char == "\n":
                    self.quote = None
                continue
            if char in "\"'":
                self.quote = char
            elif char in "([{":
                self.depth += 1
            elif char in ")]}":
                self.depth = max(0, self.depth - 1)
            elif char == "\n" and self.depth == 0:
                line = self.buffer.strip()
                self.buffer = ""
                if line and not line.startswith("//"):
                    out.append(line)
            if self.depth == 0 and self.buffer.strip() == "```":
                self.closed = True
                self.buffer = ""
                return out, index + 1
        return out, None

    def flush(self) -> list[str]:
        line = self.buffer.strip()
        self.buffer = ""
        if line and not line.startswith("```") and not line.startswith("//"):
            return [line]
        return []


@dataclass
class LayoutResult:
    """A layout read to its end."""

    program: str
    statements: list[str]
    errors: list[dict[str, str]]
    charts: list[dict[str, Any]]
    evidence_ids: list[str]
    state_defaults: dict[str, Any]
    title: str
    texts: list[str]
    adjusted: bool


class LayoutStream:
    """Reads one answer's text as it streams and publishes its checked statements.

    ``feed`` returns the statements that just became complete and valid, in
    order. The prose around the program is kept for the normal answer path.
    """

    def __init__(self, ctx: LayoutContext) -> None:
        self.ctx = ctx
        self.text = ""
        self._start: int | None = None
        self._end: int | None = None
        self._fenced = False
        self._splitter = _Splitter()
        self._consumed = 0
        self._published: list[str] = []
        self._names: set[str] = set()
        self._state: dict[str, Any] = {}
        self._errors: list[dict[str, str]] = []
        self._title = ""
        self._texts: list[str] = []
        self._seen = 0
        self._has_root = False
        #: Statements that hold a component (not a figure or state).
        self._components: set[str] = set()

    @property
    def found(self) -> bool:
        return self._start is not None

    @property
    def published(self) -> list[str]:
        return list(self._published)

    def _locate(self) -> None:
        if self._start is not None:
            return
        fence = _FENCE_OPEN.search(self.text)
        bare = _BARE_ROOT.search(self.text)
        if fence and (not bare or fence.start() <= bare.start()):
            self._start, self._fenced = fence.end(), True
        elif bare:
            self._start = bare.start()
        if self._start is not None:
            self._consumed = self._start

    async def feed(self, delta: str) -> list[str]:
        self.text += delta
        self._locate()
        if self._start is None or self._splitter.closed:
            return []
        offset = self._consumed
        fresh = self.text[offset:]
        self._consumed = len(self.text)
        lines, closed_at = self._splitter.feed(fresh)
        if closed_at is not None:
            self._end = offset + closed_at
        return await self._check(lines)

    async def finish(self) -> list[str]:
        """The statements left when the answer ends, plus a root when none came."""
        if self._start is None:
            return []
        out = await self._check(self._splitter.flush())
        if not self._has_root and self._published:
            referenced = self._referenced()
            roots = [name for name in self._components if name not in referenced]
            if roots:
                line = f"root = Page([{', '.join(sorted(roots, key=self._order))}])"
                self._published.append(line)
                out.append(line)
                self._has_root = True
        return out

    def _order(self, name: str) -> int:
        for index, line in enumerate(self._published):
            if line.startswith(f"{name} ="):
                return index
        return len(self._published)

    def _referenced(self) -> set[str]:
        names: set[str] = set()

        def visit(node: Any) -> None:
            if isinstance(node, Ref):
                names.add(node.name)
            elif isinstance(node, Call):
                for arg in node.args:
                    visit(arg)
            elif isinstance(node, ListE):
                for item in node.items:
                    visit(item)
            elif isinstance(node, ObjE):
                for _, item in node.pairs:
                    visit(item)
            elif isinstance(node, BinOp):
                visit(node.left)
                visit(node.right)
            elif isinstance(node, Unary):
                visit(node.operand)
            elif isinstance(node, Ternary):
                visit(node.cond)
                visit(node.then)
                visit(node.other)

        for line in self._published:
            visit(parse_statement(line)[1])
        return names

    async def _check(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        for line in lines:
            self._seen += 1
            if self._seen > MAX_STATEMENTS:
                self._errors.append({"statement": line[:80], "error": "Too many statements"})
                continue
            if len("\n".join(self._published).encode()) + len(line.encode()) > MAX_PROGRAM_BYTES:
                self._errors.append({"statement": line[:80], "error": "The page is too large"})
                continue
            try:
                statement = await check_statement(
                    line, self.ctx, state_names=set(self._state), defined=self._names,
                )
            except LayoutError as exc:
                name = line.split("=", 1)[0].strip()[:80]
                self._errors.append({"statement": name, "error": str(exc)[:300]})
                continue
            except Exception as exc:  # noqa: BLE001 - a statement never breaks the answer
                name = line.split("=", 1)[0].strip()[:80]
                self._errors.append({"statement": name, "error": type(exc).__name__})
                continue
            self._names.add(statement.name)
            if statement.name.startswith("$"):
                self._state[statement.name] = _plain(parse_statement(statement.text)[1])
            if statement.name == "root":
                self._has_root = True
            elif re.match(r"^[A-Za-z_]\w*\s*=\s*[A-Z]\w*\(", statement.text):
                self._components.add(statement.name)
            self._note_text(statement.text)
            self._published.append(statement.text)
            out.append(statement.text)
        return out

    def _note_text(self, line: str) -> None:
        _, expression = parse_statement(line)

        def visit(node: Any) -> None:
            if isinstance(node, Call):
                if node.name == "Header" and node.args and isinstance(node.args[0], Lit):
                    self._title = self._title or str(node.args[0].value or "")
                    if len(node.args) > 1 and isinstance(node.args[1], Lit) and node.args[1].value:
                        self._texts.append(str(node.args[1].value))
                if node.name == "Text" and node.args and isinstance(node.args[0], Lit):
                    self._texts.append(str(node.args[0].value or ""))
                for arg in node.args:
                    visit(arg)
            elif isinstance(node, ListE):
                for item in node.items:
                    visit(item)

        visit(expression)

    def prose(self) -> str:
        """The answer text outside the program."""
        if self._start is None:
            return self.text
        head = self.text[: self._start]
        if self._fenced:
            fence = _FENCE_OPEN.search(self.text)
            head = self.text[: fence.start()] if fence else head
            tail = self.text[self._end:] if self._end is not None else ""
        else:
            # A bare program runs to the end of its statements; the rest is prose.
            tail = ""
        return (head.rstrip() + ("\n\n" + tail.strip() if tail.strip() else "")).strip()

    def result(self) -> LayoutResult:
        return LayoutResult(
            program="\n".join(self._published),
            statements=list(self._published),
            errors=list(self._errors),
            charts=list(self.ctx.charts),
            evidence_ids=sorted(self.ctx.evidence_ids),
            state_defaults=dict(self._state),
            title=self._title,
            texts=list(self._texts),
            adjusted=self.ctx.adjusted,
        )


def fallback_text(result: LayoutResult) -> str:
    """Plain text of the page, for copying and for a client that cannot draw it."""
    parts = [f"**{result.title}**"] if result.title else []
    parts.extend(text for text in result.texts if text)
    return "\n\n".join(parts)


# ── Publishing while the answer streams ───────────────────────────────


class LiveLayout:
    """One answer's page, published statement by statement as the model writes it.

    The block opens (``ui``) with its first valid statement and grows by one
    ``ui_delta`` per checked statement, so the browser draws each section as
    soon as it exists. When the model's text turns out not to be the answer
    (a tool call follows, or the loop composes again), :meth:`retract` empties
    the block. :meth:`finish` seals it and records the stored step the replay
    and the draft-chart endpoint read.
    """

    def __init__(
        self, *, ctx: LayoutContext, content_index: int, content_id: str,
        record: Callable[[dict[str, Any]], Any],
    ) -> None:
        self.stream = LayoutStream(ctx)
        self.content_index = content_index
        self.content_id = content_id
        self._record = record
        self.opened = False
        self.closed = False

    @property
    def shown(self) -> bool:
        """At least one statement reached the browser."""
        return self.opened and not self.closed and bool(self.stream.published)

    def _frames(self, statements: list[str]) -> list[str]:
        from app.modules.assistant import events

        frames: list[str] = []
        if statements and not self.opened:
            self.opened = True
            frames.append(events.format_sse("ui", {
                "content_index": self.content_index, "content_id": self.content_id,
                "payload": {"catalog_version": CATALOG, "fallback": "",
                            "provenance": {"composer": "model"}},
            }))
        for statement in statements:
            frames.append(events.format_sse("ui_delta", {
                "content_index": self.content_index, "content_id": self.content_id,
                "text": statement + "\n",
            }))
        return frames

    async def feed(self, delta: str) -> list[str]:
        if self.closed:
            return []
        return self._frames(await self.stream.feed(delta))

    def retract(self) -> list[str]:
        """Empty a block whose text was not the answer after all."""
        from app.modules.assistant import events

        if self.closed:
            return []
        self.closed = True
        if not self.opened:
            return []
        return [
            events.format_sse("ui_delta", {
                "content_index": self.content_index, "content_id": self.content_id,
                "text": "", "replace": True,
            }),
            events.content_block_done(self.content_index, self.content_id),
        ]

    async def finish(self, *, prose: str) -> list[str]:
        """Seal the page and record it; empty when no statement was valid."""
        from app.modules.assistant import events

        if self.closed:
            return []
        frames = self._frames(await self.stream.finish())
        self.closed = True
        if not self.opened:
            return frames
        result = self.stream.result()
        fallback = fallback_text(result) or prose
        frames.append(events.content_block_done(self.content_index, self.content_id))
        self._record({
            "kind": "ui", "content_index": self.content_index, "content_id": self.content_id,
            "catalog_version": CATALOG, "program": result.program, "fallback": fallback,
            "charts": result.charts, "state_defaults": result.state_defaults,
            "provenance": {
                "composer": "model", "evidence_ids": result.evidence_ids,
                "scope": "verified_result_rows" if result.evidence_ids else "layout",
                "errors": result.errors, "adjusted": result.adjusted,
            },
        })
        return frames
