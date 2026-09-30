from app.modules.query.dialect.parser import (
    CommandType,
    ParsedSQL,
)
from app.sql_frontend.antlr_utils import walk_nodes as _walk_nodes
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import ParsedStatement
from app.sql_frontend.stage_parser import StageReference
from app.sql_frontend.stage_parser import stage_reference_from_atom as _stage_reference_from_atom


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


def planned_stages(plan) -> ParsedSQL:
    sql = plan.engine_sql
    refs = []
    for binding in plan.stage_bindings:
        if not binding.slot or sql.count(binding.slot) != 1:
            raise SemanticError("Rewrite removed or duplicated a stage binding")
        start = sql.index(binding.slot)
        refs.append(
            StageReference(
                full_match=binding.slot,
                stage_name=binding.name,
                path_parts=list(binding.path[:-1] if binding.file_name else binding.path),
                file_name=binding.file_name,
                is_directory=binding.is_directory,
                original_text=sql,
                start=start,
                end=start + len(binding.slot),
                access=binding.access,
            )
        )
    return ParsedSQL(CommandType(plan.stage_command), refs, sql, sql, [])
