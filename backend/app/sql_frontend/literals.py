"""Typed literals Nova accepts but the engine spells differently.

``TIMESTAMP '2026-01-01 10:00:00'`` is standard SQL and what DuckDB's MySQL
scanner sends when it pushes a timestamp down; StarRocks only has
``DATETIME '...'``. The grammar accepts both (a NOVA region of ``dateLiteral``)
and the native planner rewrites the keyword's source span, nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.sql_dialect.grammar.StarRocksParser import StarRocksParser
from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.parser import ParsedStatement


@dataclass(frozen=True, slots=True)
class LiteralRewrite:
    """Replace ``[start, end)`` of the statement with ``slot``."""

    slot: str
    start: int
    end: int


def typed_literal_rewrites(parsed: ParsedStatement) -> tuple[LiteralRewrite, ...]:
    rewrites: list[LiteralRewrite] = []
    for node in walk_nodes(parsed.statement_context):
        if not isinstance(node, StarRocksParser.DateLiteralContext):
            continue
        keyword = node.TIMESTAMP()
        if keyword is not None:
            token = keyword.getSymbol()
            rewrites.append(LiteralRewrite("DATETIME", token.start, token.stop + 1))
    return tuple(rewrites)
