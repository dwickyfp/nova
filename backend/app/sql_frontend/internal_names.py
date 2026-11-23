"""Nova-managed relations that user SQL never names.

Each Stream source keeps its change journal and lineage in tables beside the
source (``__nova_stream_changes_<id>``, ``__nova_stream_lineage_<id>``,
see ``app.modules.streams.sources``). They hold every historical row image of
the source, so only Nova's stream writer reads or writes them; Ranger denies
everyone else, and the frontend refuses a statement that names one before it
reaches the engine (a name in a table, column, alias or DDL position alike).
"""

from __future__ import annotations

from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import ParsedStatement

#: Prefix of the Stream journal and lineage tables.
INTERNAL_STREAM_PREFIX = "__nova_stream_"

INTERNAL_RELATION_REFUSAL = (
    "Tables named __nova_stream_… are managed by Nova Streams and cannot be used in SQL; "
    "read a stream's changes with SELECT … FROM <stream>"
)

_IDENTIFIERS = frozenset({
    "UnquotedIdentifierContext", "BackQuotedIdentifierContext", "DigitIdentifierContext",
})


def is_internal_name(name: str) -> bool:
    return name.casefold().startswith(INTERNAL_STREAM_PREFIX)


def _identifier_text(node) -> str:
    text = node.getText()
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        return text[1:-1].replace("``", "`")
    return text


def names_internal_relation(parsed: ParsedStatement) -> bool:
    """True when any identifier of the statement names a Nova-managed table."""
    from app.sql_frontend.streams import name_parts

    for node in walk_nodes(parsed.statement_context):
        kind = type(node).__name__
        if kind == "QualifiedNameContext":
            if any(is_internal_name(part) for part in name_parts(node)):
                return True
        elif kind in _IDENTIFIERS and is_internal_name(_identifier_text(node)):
            return True
    return False


def refuse_internal_relations(parsed: ParsedStatement) -> None:
    """Raise :class:`SemanticError` when the statement names a Nova-managed table."""
    if names_internal_relation(parsed):
        raise SemanticError(INTERNAL_RELATION_REFUSAL)


__all__ = [
    "INTERNAL_RELATION_REFUSAL",
    "INTERNAL_STREAM_PREFIX",
    "is_internal_name",
    "names_internal_relation",
    "refuse_internal_relations",
]
