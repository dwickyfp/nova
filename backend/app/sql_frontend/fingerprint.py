"""Credential-free workload shapes derived from the central parser's typed tree."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.common.sql_guard import redact_sql_credentials
from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.ast.statements import NativeStatement
from app.sql_frontend.parser import parse_statement

VERSION = 2
_VALUES = {
    "NumericLiteralContext",
    "StringLiteralContext",
    "DateLiteralContext",
    "BinaryLiteralContext",
    "BooleanLiteralContext",
    "ParameterContext",
}
_UNSAFE = {
    "OutfileContext",
    "StageReferenceContext",
    "FileTableFunctionContext",
    "TableFunctionContext",
    "NormalizedTableFunctionContext",
    "UserVariableContext",
    "SystemVariableContext",
    "InformationFunctionContext",
    "SpecialDateTimeContext",
    "OptimizerTraceContext",
    "ExplainDescContext",
    "QueryPeriodContext",
    "SampleClauseContext",
    "LimitElementContext",
    "WindowFunctionCallContext",
}
_PURE_FUNCTIONS = frozenset(
    {
        "abs",
        "ceil",
        "floor",
        "round",
        "coalesce",
        "ifnull",
        "nullif",
        "date_trunc",
        "date_format",
        "year",
        "month",
        "day",
        "hour",
        "lower",
        "upper",
        "length",
        "substring",
        "concat",
        "cast",
        "sum",
        "count",
        "avg",
        "min",
        "max",
    }
)


@dataclass(frozen=True)
class QueryShape:
    family_id: str
    canonical: str | None
    classified: bool
    replay_eligible: bool
    reason: str | None
    tables: tuple[str, ...] = ()
    settings_hash: str = ""
    version: int = VERSION


def _name(node: Any) -> str:
    return type(node).__name__


def _source(sql: str, node: Any) -> str:
    return sql[node.start.start : node.stop.stop + 1]


def _scope(node: Any) -> Any:
    while node is not None and _name(node) != "QueryRelationContext":
        node = getattr(node, "parentCtx", None)
    return node


def _identifier(sql: str, node: Any) -> str:
    return _source(sql, node).strip("`").replace("``", "`")


def _output_order(node: Any) -> bool:
    sort = False
    while node is not None and _name(node) != "QueryRelationContext":
        if _name(node) == "QuerySpecificationContext":
            return False
        sort |= _name(node) == "SortItemContext"
        node = node.parentCtx
    return sort


def fingerprint(sql: str) -> QueryShape:
    """No executable SQL is constructed here; canonical strings are display shapes."""
    try:
        if redact_sql_credentials(sql) != sql:
            raise ValueError("credential-bearing statement")
        parsed = parse_statement(sql)
        statement = ast_builders.build(parsed)
        nodes = list(walk_nodes(parsed.statement_context))
        # The property filters the whole token stream on every access; the
        # loops below would otherwise do that once per alias and per list.
        visible_tokens = parsed.visible_tokens
        if (
            not isinstance(statement, NativeStatement)
            or _name(parsed.statement_context) != "QueryStatementContext"
        ):
            raise ValueError("unsupported statement")
        scope_ids = {
            id(node): index
            for index, node in enumerate(n for n in nodes if _name(n) == "QueryRelationContext")
        }
        aliases: dict[int, dict[str, str]] = {}
        outputs: dict[int, dict[str, str]] = {}
        replacements: dict[int, tuple[int, str]] = {}
        skipped: set[int] = set()
        identifiers: set[int] = set()
        tables: list[str] = []
        safe = not any(_name(n) in _UNSAFE for n in nodes)
        for node in nodes:
            kind = _name(node)
            if kind in {
                "IdentifierContext",
                "UnquotedIdentifierContext",
                "QuotedIdentifierContext",
            }:
                identifiers.update(range(node.start.tokenIndex, node.stop.tokenIndex + 1))
            if kind == "TableAtomContext":
                tables.append(
                    ".".join(_identifier(sql, part) for part in node.qualifiedName().identifier())
                )
            alias = getattr(node, "alias", None)
            if kind == "SelectSingleContext":
                alias = node.identifier()
            if alias is not None and kind in {
                "TableAtomContext",
                "SubqueryWithAliasContext",
                "SelectSingleContext",
            }:
                scope_id = id(_scope(node))
                names = (outputs if kind == "SelectSingleContext" else aliases).setdefault(
                    scope_id, {},
                )
                name = _identifier(sql, alias)
                if name in names:
                    raise ValueError("ambiguous_alias_scope")
                ordinal = len(aliases.get(scope_id, {})) + len(outputs.get(scope_id, {}))
                canonical = f"$s{scope_ids[scope_id]}a{ordinal}"
                names[name] = canonical
                replacements[alias.start.tokenIndex] = (alias.stop.tokenIndex, canonical)
                previous = next(
                    (
                        t
                        for t in reversed(visible_tokens)
                        if t.tokenIndex < alias.start.tokenIndex
                    ),
                    alias.start,
                )
                if previous.text.upper() == "AS":
                    skipped.add(previous.tokenIndex)
            if kind == "SimpleFunctionCallContext":
                safe &= _source(sql, node.qualifiedName()).lower() in _PURE_FUNCTIONS
        for node in nodes:
            kind = _name(node)
            if kind == "ColumnReferenceContext":
                name = _identifier(sql, node.identifier())
                scope = _scope(node)
                qualified = (
                    _name(getattr(node.parentCtx, "parentCtx", None)) == "DereferenceContext"
                )
                replacement = None
                if qualified:
                    while scope is not None:
                        replacement = aliases.get(id(scope), {}).get(name)
                        if replacement is not None:
                            break
                        scope = _scope(scope.parentCtx)
                # Unqualified input names stay intact outside the output ORDER BY.
                elif name in outputs.get(id(scope), {}) and _output_order(node):
                    replacement = outputs[id(scope)][name]
                if replacement is not None:
                    replacements[node.start.tokenIndex] = (node.stop.tokenIndex, replacement)
            if kind in {"IntegerListContext", "StringListContext"}:
                for token in visible_tokens:
                    if (
                        node.start.tokenIndex < token.tokenIndex < node.stop.tokenIndex
                        and token.text not in {",", "-", "+"}
                    ):
                        replacements[token.tokenIndex] = (
                            token.tokenIndex,
                            "?numeric" if kind == "IntegerListContext" else "?string",
                        )
            if kind in _VALUES:
                # ORDER/GROUP BY 1 is a position, whereas x + 1 contains a value.
                parent = node.parentCtx
                positional = False
                while parent is not None and _name(parent) not in {
                    "QueryRelationContext",
                    "QuerySpecificationContext",
                }:
                    if _name(parent) in {"SortItemContext", "ExpressionListContext"}:
                        exprs = parent.expression()
                        exprs = exprs if isinstance(exprs, list) else [exprs]
                        positional = any(
                            e.start.tokenIndex == node.start.tokenIndex
                            and e.stop.tokenIndex == node.stop.tokenIndex
                            for e in exprs
                            if e is not None
                        )
                        if _name(parent) == "ExpressionListContext":
                            positional &= _name(parent.parentCtx).endswith("GroupingSetContext")
                        if positional:
                            break
                    parent = parent.parentCtx
                if not positional:
                    replacements[node.start.tokenIndex] = (
                        node.stop.tokenIndex,
                        f"?{kind.removesuffix('LiteralContext').lower()}",
                    )
        parts: list[str] = []
        until = -1
        for token in visible_tokens:
            if token.tokenIndex <= until or token.text == ";" or token.tokenIndex in skipped:
                continue
            if token.tokenIndex in replacements:
                until, value = replacements[token.tokenIndex]
                parts.append(value)
            else:
                # Identifier case can change resolution; never fold quoted names.
                parts.append(
                    token.text
                    if token.tokenIndex in identifiers or token.text.startswith("`")
                    else token.text.lower()
                )
        canonical = " ".join(parts)
        hints = [t.text for t in parsed.tokens.tokens if t.text and t.text.startswith("/*+")]
        settings_hash = hashlib.sha256(json.dumps(hints).encode()).hexdigest() if hints else ""
        digest = hashlib.sha256(f"{VERSION}:{canonical}".encode()).hexdigest()
        return QueryShape(
            digest,
            canonical,
            True,
            safe,
            None if safe else "non_deterministic_or_unsupported_replay",
            tuple(tables),
            settings_hash,
        )
    except Exception:
        # An unclassified statement retains no literals, identifiers, or parse diagnostics.
        return QueryShape(
            hashlib.sha256(sql.encode()).hexdigest(), None, False, False, "unclassified"
        )
