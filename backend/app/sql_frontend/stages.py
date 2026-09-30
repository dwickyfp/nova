from app.modules.query.dialect.parser import (
    CommandType,
    ParsedSQL,
    _stage_reference_from_atom,
    _walk_nodes,
)
from app.sql_frontend.parser import ParsedStatement


def stage_view(parsed: ParsedStatement) -> ParsedSQL:
    root = parsed.statement_context
    kind = type(root).__name__
    nodes = [node for node in _walk_nodes(root) if type(node).__name__ == "StageReferenceContext"]
    refs = []
    for node in nodes:
        # Reuse the existing segment decoder for relation and command references.
        class Atom:
            def stageReference(self, reference=node):  # noqa: N802
                return reference

        refs.append(_stage_reference_from_atom(Atom(), parsed.normalized_sql))
    command = CommandType.STAGE_QUERY if refs else CommandType.REGULAR
    if kind == "NovaListStatementContext":
        command = CommandType.STAGE_BROWSE
    if kind == "NovaStageInsertStatementContext":
        command = CommandType.STAGE_EXPORT
    if kind == "NovaCopyStatementContext":
        command = (
            CommandType.STAGE_EXPORT
            if root.getChild(2).__class__.__name__ == "StageReferenceContext"
            else CommandType.STAGE_LOAD
        )
    return ParsedSQL(command, refs, parsed.normalized_sql, parsed.normalized_sql, [])
