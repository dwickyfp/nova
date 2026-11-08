"""Find the data views inside a stored OpenUI program, without running it.

An Intelligent UI answer is an openui-lang program such as::

    root = Col([Chart(@Sort(@Evidence("e1"), "units", "desc"), "brand", "units", "bar")])

Publishing one of its charts to Dataviz must know exactly which verified result
the chart draws and what the program did to those rows on the way. This module
parses the program into a small expression tree (calls, ``@`` functions,
``$``/name references, lists, objects and literals), substitutes statement
references, and lists every ``Kpi``, ``Table``, ``Chart`` and ``GeoMap`` with
its arguments and the chain of functions between it and ``@Evidence``.

It is analysis only: nothing is evaluated, and anything the parser does not
understand makes the node *opaque* rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MAX_PROGRAM = 64_000
DATA_COMPONENTS = ("Kpi", "Table", "Chart", "GeoMap")
#: Positional props of each data component, in the catalog's order.
POSITIONAL: dict[str, tuple[str, ...]] = {
    "Kpi": ("data", "column", "label", "digits"),
    "Table": ("data", "columns", "title"),
    "Chart": ("data", "x", "y", "type", "title", "series", "selected"),
    "GeoMap": (
        "data",
        "longitude",
        "latitude",
        "geometry",
        "label",
        "title",
        "selected",
        "options",
        "viewport",
    ),
}

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+|//[^\n]*)
  | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<number>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)
  | (?P<func>@[A-Za-z_]\w*)
  | (?P<name>\$?[A-Za-z_]\w*)
  | (?P<op>==|!=|>=|<=|&&|\|\||[()\[\]{},:=?<>+\-*/!.%])
    """,
    re.VERBOSE,
)


class OpenUiParseError(ValueError):
    pass


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple[Any, ...]
    function: bool  # ``@Name(...)``


@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class Opaque:
    """An expression the analysis does not model (operators, member access)."""

    text: str


@dataclass
class DataNode:
    component: str
    props: dict[str, Any]
    evidence_id: str | None
    #: Functions applied to the evidence rows, outermost first, e.g. ``["Sort"]``.
    transforms: list[Call] = field(default_factory=list)
    opaque: bool = False

    def prop(self, name: str) -> Any:
        return self.props.get(name)


def _tokens(program: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    position = 0
    while position < len(program):
        match = _TOKEN.match(program, position)
        if not match:
            raise OpenUiParseError(f"Unexpected character at {position}")
        position = match.end()
        kind = match.lastgroup or ""
        if kind != "ws":
            out.append((kind, match.group()))
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self, offset: int = 0) -> tuple[str, str] | None:
        at = self.index + offset
        return self.tokens[at] if at < len(self.tokens) else None

    def text(self, offset: int = 0) -> str | None:
        token = self.peek(offset)
        return token[1] if token else None

    def kind(self, offset: int = 0) -> str | None:
        token = self.peek(offset)
        return token[0] if token else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if token is None or (value is not None and token[1] != value):
            raise OpenUiParseError(f"Expected {value or 'a token'}")
        self.index += 1
        return token

    def statements(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        while self.peek() is not None:
            kind, name = self.take()
            if kind != "name" or self.take()[1] != "=":
                raise OpenUiParseError("A statement is `name = expression`")
            out[name] = self.expression()
        return out

    def expression(self) -> Any:
        start = self.index
        value = self.primary()
        # Anything after a primary (operators, member access, ternaries) is
        # not modelled: keep the text so the node becomes opaque, not guessed.
        if (
            self.text() is not None
            and self.text() not in (",", ")", "]", "}")
            and not (self.kind() == "name" and self.text(1) == "=")
        ):
            depth = 0
            while (text := self.text()) is not None:
                if depth == 0 and text in (",", ")", "]", "}"):
                    break
                if (
                    depth == 0
                    and self.kind() == "name"
                    and self.text(1) == "="
                    and self.index > start + 1
                ):
                    break
                if text in "([{":
                    depth += 1
                elif text in ")]}":
                    depth -= 1
                self.index += 1
            return Opaque(" ".join(token[1] for token in self.tokens[start : self.index]))
        return value

    def primary(self) -> Any:
        kind, text = self.take()
        if kind == "string":
            body = text[1:-1]
            return re.sub(r"\\(.)", r"\1", body)
        if kind == "number":
            return float(text) if any(c in text for c in ".eE") else int(text)
        if kind == "func":
            return Call(text[1:], self.arguments(), True)
        if kind == "name":
            if text in ("true", "false"):
                return text == "true"
            if text == "null":
                return None
            if self.text() == "(":
                return Call(text, self.arguments(), False)
            return Ref(text)
        if text == "[":
            items = []
            while self.text() not in (None, "]"):
                items.append(self.expression())
                if self.text() == ",":
                    self.take(",")
            self.take("]")
            return items
        if text == "{":
            obj: dict[str, Any] = {}
            while self.text() not in (None, "}"):
                key_kind, key = self.take()
                key = key[1:-1] if key_kind == "string" else key
                self.take(":")
                obj[key] = self.expression()
                if self.text() == ",":
                    self.take(",")
            self.take("}")
            return obj
        if text == "(":
            inner = self.expression()
            self.take(")")
            return inner
        raise OpenUiParseError(f"Unexpected {text!r}")

    def arguments(self) -> tuple[Any, ...]:
        self.take("(")
        args = []
        while self.text() not in (None, ")"):
            args.append(self.expression())
            if self.text() == ",":
                self.take(",")
        self.take(")")
        return tuple(args)


def _resolve(value: Any, statements: dict[str, Any], seen: frozenset[str] = frozenset()) -> Any:
    if isinstance(value, Ref):
        if value.name in seen or value.name not in statements or value.name.startswith("$"):
            return value
        return _resolve(statements[value.name], statements, seen | {value.name})
    if isinstance(value, Call):
        return Call(
            value.name, tuple(_resolve(a, statements, seen) for a in value.args), value.function
        )
    if isinstance(value, list):
        return [_resolve(item, statements, seen) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, statements, seen) for key, item in value.items()}
    return value


def _source(data: Any) -> tuple[str | None, list[Call], bool]:
    """``(evidence_id, transforms outermost first, opaque)`` of a data argument."""
    transforms: list[Call] = []
    current = data
    while isinstance(current, Call) and current.function:
        if current.name == "Evidence":
            evidence = current.args[0] if current.args else None
            return (evidence if isinstance(evidence, str) else None), transforms, False
        transforms.append(current)
        current = current.args[0] if current.args else None
    return None, transforms, True


def _props(component: str, args: tuple[Any, ...]) -> dict[str, Any]:
    names = POSITIONAL[component]
    props: dict[str, Any] = {}
    for index, value in enumerate(args):
        if index == len(args) - 1 and isinstance(value, dict) and index >= len(names):
            props.update(value)
        elif index < len(names):
            props[names[index]] = value
    return props


def data_nodes(program: str) -> list[DataNode]:
    """Every data component in ``program``, in source order."""
    if len(program) > MAX_PROGRAM:
        raise OpenUiParseError("The program is too large to analyse")
    statements = _Parser(_tokens(program)).statements()
    nodes: list[DataNode] = []
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Call):
            if not value.function and value.name in DATA_COMPONENTS and id(value) not in seen:
                seen.add(id(value))
                props = _props(value.name, value.args)
                evidence, transforms, opaque = _source(props.get("data"))
                nodes.append(DataNode(value.name, props, evidence, transforms, opaque))
            for arg in value.args:
                visit(arg)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)

    root = statements.get("root")
    visit(_resolve(root if root is not None else list(statements.values()), statements))
    return nodes
