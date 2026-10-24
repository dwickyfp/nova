"""Calculation formulas: tokens, syntax tree and parser.

A formula is an expression over fields (``[Order Date]`` or a bare
``profit``), literals (``12.5``, ``'East'``, ``#2024-01-31#``, ``TRUE``,
``NULL``), operators, function calls, ``IF``/``CASE`` blocks and
level-of-detail braces (``{FIXED [Region] : SUM([Sales])}``). ``//`` and
``/* */`` comments are ignored.

The parser is a small recursive-descent parser that records source spans on
every node, so the analyzer can point the editor at the exact text in error.
Nodes compare structurally (spans excluded): two identical sub-expressions
are one node for planning, which is how repeated aggregates and LODs are
computed once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

MAX_FORMULA_LENGTH = 8000
MAX_DEPTH = 64

TokenKind = Literal["number", "string", "date", "field", "ident", "op", "eof"]

KEYWORDS = frozenset(
    {
        "IF",
        "THEN",
        "ELSEIF",
        "ELSE",
        "END",
        "CASE",
        "WHEN",
        "AND",
        "OR",
        "NOT",
        "IN",
        "TRUE",
        "FALSE",
        "NULL",
        "FIXED",
        "INCLUDE",
        "EXCLUDE",
    }
)

_OPERATORS = (
    "==",
    "!=",
    "<>",
    "<=",
    ">=",
    "+",
    "-",
    "*",
    "/",
    "%",
    "^",
    "=",
    "<",
    ">",
    "(",
    ")",
    ",",
    "{",
    "}",
    ":",
)

_NUMBER = re.compile(r"(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")


class CalcError(ValueError):
    """A formula is invalid; ``start``/``end`` locate the offending text."""

    def __init__(self, message: str, start: int = 0, end: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.start = start
        self.end = max(end, start)


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    value: str
    start: int
    end: int


def tokenize(formula: str) -> list[Token]:
    """Split a formula into tokens; comments and whitespace are dropped."""
    if len(formula) > MAX_FORMULA_LENGTH:
        raise CalcError(f"A formula can be at most {MAX_FORMULA_LENGTH} characters.")
    tokens: list[Token] = []
    index = 0
    length = len(formula)
    while index < length:
        char = formula[index]
        if char.isspace():
            index += 1
            continue
        if formula.startswith("//", index):
            newline = formula.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if formula.startswith("/*", index):
            close = formula.find("*/", index + 2)
            if close < 0:
                raise CalcError("This comment is never closed.", index, length)
            index = close + 2
            continue
        if char == "[":
            name, index_after = _read_delimited(formula, index, "]", "field name")
            if not name.strip():
                raise CalcError("A field name cannot be empty.", index, index_after)
            tokens.append(Token("field", name, index, index_after))
            index = index_after
            continue
        if char in {"'", '"'}:
            text, index_after = _read_delimited(formula, index, char, "text")
            tokens.append(Token("string", text, index, index_after))
            index = index_after
            continue
        if char == "#":
            close = formula.find("#", index + 1)
            if close < 0:
                raise CalcError("This date literal is never closed with #.", index, length)
            text = formula[index + 1 : close].strip()
            if not _DATE.match(text):
                raise CalcError(
                    "Write dates as #YYYY-MM-DD# or #YYYY-MM-DD HH:MM:SS#.", index, close + 1
                )
            tokens.append(Token("date", text, index, close + 1))
            index = close + 1
            continue
        number = _NUMBER.match(formula, index)
        if number and (char.isdigit() or char == "."):
            tokens.append(Token("number", number.group(0), index, number.end()))
            index = number.end()
            continue
        ident = _IDENT.match(formula, index)
        if ident:
            tokens.append(Token("ident", ident.group(0), index, ident.end()))
            index = ident.end()
            continue
        for operator in _OPERATORS:
            if formula.startswith(operator, index):
                tokens.append(Token("op", operator, index, index + len(operator)))
                index += len(operator)
                break
        else:
            raise CalcError(f"Unexpected character {char!r}.", index, index + 1)
    tokens.append(Token("eof", "", length, length))
    return tokens


def _read_delimited(formula: str, start: int, close: str, what: str) -> tuple[str, int]:
    """Read ``[name]`` / ``'text'``; a doubled closing character escapes it."""
    parts: list[str] = []
    index = start + 1
    while index < len(formula):
        char = formula[index]
        if char == close:
            if formula.startswith(close * 2, index):
                parts.append(close)
                index += 2
                continue
            return "".join(parts), index + 1
        parts.append(char)
        index += 1
    raise CalcError(f"This {what} is never closed with {close}.", start, len(formula))


# ── Syntax tree ────────────────────────────────────────────────


@dataclass(frozen=True)
class Node:
    start: int = field(default=0, compare=False, kw_only=True)
    end: int = field(default=0, compare=False, kw_only=True)


@dataclass(frozen=True)
class Literal_(Node):
    """A constant. ``kind`` is number, string, date, datetime, boolean or null."""

    kind: str
    value: str


@dataclass(frozen=True)
class FieldRef(Node):
    name: str


@dataclass(frozen=True)
class Unary(Node):
    op: str
    operand: Node


@dataclass(frozen=True)
class Binary(Node):
    op: str
    left: Node
    right: Node


@dataclass(frozen=True)
class InList(Node):
    subject: Node
    options: tuple[Node, ...]


@dataclass(frozen=True)
class Call(Node):
    name: str
    args: tuple[Node, ...]


@dataclass(frozen=True)
class If(Node):
    branches: tuple[tuple[Node, Node], ...]
    otherwise: Node | None


@dataclass(frozen=True)
class Case(Node):
    subject: Node
    whens: tuple[tuple[Node, Node], ...]
    otherwise: Node | None


@dataclass(frozen=True)
class Lod(Node):
    """``{FIXED|INCLUDE|EXCLUDE dims : body}``; ``table`` is ``{body}``."""

    kind: Literal["fixed", "include", "exclude", "table"]
    dims: tuple[Node, ...]
    body: Node


def children(node: Node) -> tuple[Node, ...]:
    """Direct sub-expressions, in source order."""
    if isinstance(node, Unary):
        return (node.operand,)
    if isinstance(node, Binary):
        return (node.left, node.right)
    if isinstance(node, InList):
        return (node.subject, *node.options)
    if isinstance(node, Call):
        return node.args
    if isinstance(node, If):
        out: list[Node] = []
        for condition, value in node.branches:
            out.extend((condition, value))
        if node.otherwise is not None:
            out.append(node.otherwise)
        return tuple(out)
    if isinstance(node, Case):
        out = [node.subject]
        for match, value in node.whens:
            out.extend((match, value))
        if node.otherwise is not None:
            out.append(node.otherwise)
        return tuple(out)
    if isinstance(node, Lod):
        return (*node.dims, node.body)
    return ()


# ── Parser ─────────────────────────────────────────────────────


_COMPARISONS = frozenset({"=", "==", "!=", "<>", "<", "<=", ">", ">="})


class _Parser:
    def __init__(self, formula: str) -> None:
        self.tokens = tokenize(formula)
        self.position = 0
        self.depth = 0

    # Token helpers

    @property
    def current(self) -> Token:
        return self.tokens[self.position]

    def advance(self) -> Token:
        token = self.tokens[self.position]
        if token.kind != "eof":
            self.position += 1
        return token

    def is_op(self, *values: str) -> bool:
        return self.current.kind == "op" and self.current.value in values

    def is_keyword(self, *values: str) -> bool:
        return self.current.kind == "ident" and self.current.value.upper() in values

    def expect_op(self, value: str, context: str) -> Token:
        if not self.is_op(value):
            raise self.error(f"Expected {value!r} {context}.")
        return self.advance()

    def expect_keyword(self, value: str, context: str) -> Token:
        if not self.is_keyword(value):
            raise self.error(f"Expected {value} {context}.")
        return self.advance()

    def error(self, message: str) -> CalcError:
        token = self.current
        if token.kind == "eof":
            return CalcError(
                message.rstrip(".") + ", but the formula ended.", token.start, token.end
            )
        return CalcError(message, token.start, token.end)

    # Grammar

    def parse(self) -> Node:
        if self.current.kind == "eof":
            raise CalcError("The formula is empty.")
        node = self.expression()
        if self.current.kind != "eof":
            raise self.error(f"Unexpected {self.current.value!r}; is an operator missing?")
        return node

    def expression(self) -> Node:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise self.error("This formula is nested too deeply.")
        try:
            return self.disjunction()
        finally:
            self.depth -= 1

    def disjunction(self) -> Node:
        node = self.conjunction()
        while self.is_keyword("OR"):
            self.advance()
            right = self.conjunction()
            node = Binary("OR", node, right, start=node.start, end=right.end)
        return node

    def conjunction(self) -> Node:
        node = self.negation()
        while self.is_keyword("AND"):
            self.advance()
            right = self.negation()
            node = Binary("AND", node, right, start=node.start, end=right.end)
        return node

    def negation(self) -> Node:
        if self.is_keyword("NOT"):
            token = self.advance()
            operand = self.negation()
            return Unary("NOT", operand, start=token.start, end=operand.end)
        return self.comparison()

    def comparison(self) -> Node:
        node = self.additive()
        if self.current.kind == "op" and self.current.value in _COMPARISONS:
            op = self.advance().value
            op = {"==": "=", "!=": "<>"}.get(op, op)
            right = self.additive()
            node = Binary(op, node, right, start=node.start, end=right.end)
        elif self.is_keyword("IN"):
            self.advance()
            self.expect_op("(", "after IN")
            options = [self.expression()]
            while self.is_op(","):
                self.advance()
                options.append(self.expression())
            close = self.expect_op(")", "to close the IN list")
            node = InList(node, tuple(options), start=node.start, end=close.end)
        if self.current.kind == "op" and self.current.value in _COMPARISONS:
            raise self.error("Comparisons cannot be chained; combine them with AND.")
        return node

    def additive(self) -> Node:
        node = self.multiplicative()
        while self.is_op("+", "-"):
            op = self.advance().value
            right = self.multiplicative()
            node = Binary(op, node, right, start=node.start, end=right.end)
        return node

    def multiplicative(self) -> Node:
        node = self.unary()
        while self.is_op("*", "/", "%"):
            op = self.advance().value
            right = self.unary()
            node = Binary(op, node, right, start=node.start, end=right.end)
        return node

    def unary(self) -> Node:
        if self.is_op("-", "+"):
            token = self.advance()
            operand = self.unary()
            if token.value == "+":
                return operand
            return Unary("-", operand, start=token.start, end=operand.end)
        return self.power()

    def power(self) -> Node:
        node = self.primary()
        if self.is_op("^"):
            self.advance()
            exponent = self.unary()
            node = Binary("^", node, exponent, start=node.start, end=exponent.end)
        return node

    def primary(self) -> Node:
        token = self.current
        if token.kind == "number":
            self.advance()
            return Literal_("number", token.value, start=token.start, end=token.end)
        if token.kind == "string":
            self.advance()
            return Literal_("string", token.value, start=token.start, end=token.end)
        if token.kind == "date":
            self.advance()
            kind = "datetime" if len(token.value) > 10 else "date"
            return Literal_(kind, token.value.replace("T", " "), start=token.start, end=token.end)
        if token.kind == "field":
            self.advance()
            return FieldRef(token.value, start=token.start, end=token.end)
        if self.is_op("("):
            self.advance()
            node = self.expression()
            self.expect_op(")", "to close the parenthesis")
            return node
        if self.is_op("{"):
            return self.lod()
        if token.kind == "ident":
            word = token.value.upper()
            if word in {"TRUE", "FALSE"}:
                self.advance()
                return Literal_("boolean", word, start=token.start, end=token.end)
            if word == "NULL":
                self.advance()
                return Literal_("null", "NULL", start=token.start, end=token.end)
            if word == "IF":
                return self.if_block()
            if word == "CASE":
                return self.case_block()
            if word in KEYWORDS:
                raise self.error(f"{word} cannot start an expression here.")
            if token.value.startswith("__"):
                raise self.error("Names starting with two underscores are reserved.")
            self.advance()
            if self.is_op("("):
                return self.call(token)
            return FieldRef(token.value, start=token.start, end=token.end)
        if token.kind == "eof":
            raise self.error("Expected a value")
        raise self.error(f"Unexpected {token.value!r}.")

    def call(self, name: Token) -> Node:
        self.expect_op("(", "after the function name")
        args: list[Node] = []
        if not self.is_op(")"):
            args.append(self.expression())
            while self.is_op(","):
                self.advance()
                args.append(self.expression())
        close = self.expect_op(")", f"to close {name.value.upper()}(")
        return Call(name.value.upper(), tuple(args), start=name.start, end=close.end)

    def if_block(self) -> Node:
        start = self.advance().start
        branches: list[tuple[Node, Node]] = []
        condition = self.expression()
        self.expect_keyword("THEN", "after the IF condition")
        branches.append((condition, self.expression()))
        otherwise: Node | None = None
        while self.is_keyword("ELSEIF"):
            self.advance()
            condition = self.expression()
            self.expect_keyword("THEN", "after the ELSEIF condition")
            branches.append((condition, self.expression()))
        if self.is_keyword("ELSE"):
            self.advance()
            otherwise = self.expression()
        end = self.expect_keyword("END", "to close IF")
        return If(tuple(branches), otherwise, start=start, end=end.end)

    def case_block(self) -> Node:
        start = self.advance().start
        subject = self.expression()
        whens: list[tuple[Node, Node]] = []
        while self.is_keyword("WHEN"):
            self.advance()
            match = self.expression()
            self.expect_keyword("THEN", "after the WHEN value")
            whens.append((match, self.expression()))
        if not whens:
            raise self.error("CASE needs at least one WHEN ... THEN.")
        otherwise: Node | None = None
        if self.is_keyword("ELSE"):
            self.advance()
            otherwise = self.expression()
        end = self.expect_keyword("END", "to close CASE")
        return Case(subject, tuple(whens), otherwise, start=start, end=end.end)

    def lod(self) -> Node:
        start = self.advance().start
        kind: Literal["fixed", "include", "exclude", "table"] = "table"
        dims: list[Node] = []
        if self.is_keyword("FIXED", "INCLUDE", "EXCLUDE"):
            kind = self.advance().value.lower()  # type: ignore[assignment]
            if not self.is_op(":"):
                dims.append(self.expression())
                while self.is_op(","):
                    self.advance()
                    dims.append(self.expression())
            self.expect_op(":", "between the dimensions and the aggregate")
        body = self.expression()
        close = self.expect_op("}", "to close the level of detail expression")
        return Lod(kind, tuple(dims), body, start=start, end=close.end)


def parse(formula: str) -> Node:
    """Parse a formula into a syntax tree, or raise :class:`CalcError`."""
    return _Parser(formula).parse()


def field_token(name: str) -> str:
    """Write a field reference the parser reads back as ``name``."""
    return "[" + name.replace("]", "]]") + "]"
