from typing import Any

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
            alias = getattr(node.parentCtx, "alias", None)

            def stageReference(self, reference=node):  # noqa: N802
                return reference

        refs.append(_stage_reference_from_atom(Atom(), parsed.normalized_sql))
    from app.sql_frontend.unload import unload_targets

    targets = unload_targets(parsed)
    for target in targets:
        refs.append(
            StageReference(
                full_match=parsed.normalized_sql[target.start : target.end],
                stage_name=target.qualifier[0],
                path_parts=[*target.qualifier[1:], *target.segments],
                file_name=None,
                is_directory=target.is_directory,
                original_text=parsed.normalized_sql,
                start=target.start,
                end=target.end,
                access="write",
                unload=target.clause,
            )
        )
    from app.sql_frontend.backup_restore import repository_targets
    from app.sql_frontend.broker_load import broker_load_targets

    loads = broker_load_targets(parsed)
    repositories = repository_targets(parsed)
    items: list[Any] = [*loads, *repositories]
    for item in items:
        refs.append(
            StageReference(
                full_match=parsed.normalized_sql[item.start : item.end],
                stage_name=item.qualifier[0],
                path_parts=[*item.qualifier[1:], *item.segments],
                file_name=None,
                is_directory=getattr(item, "is_directory", True),
                original_text=parsed.normalized_sql,
                start=item.start,
                end=item.end,
                # Load files are read; a repository is written unless READ ONLY.
                access=getattr(item, "access", "read"),
                unload=item.clause,
            )
        )
    refs.sort(key=lambda ref: ref.start)
    command = CommandType.STAGE_QUERY if refs else CommandType.REGULAR
    if targets:
        command = CommandType.STAGE_UNLOAD
    if loads:
        command = CommandType.STAGE_BROKER_LOAD
    if repositories:
        command = CommandType.STAGE_REPOSITORY
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
                path_parts=list(
                    binding.path[:-1] if binding.file_name and binding.unload is None
                    else binding.path
                ),
                file_name=binding.file_name,
                is_directory=binding.is_directory,
                original_text=sql,
                start=start,
                end=start + len(binding.slot),
                access=binding.access,
                has_alias=binding.has_alias,
                unload=binding.unload,
            )
        )
    return ParsedSQL(CommandType(plan.stage_command), refs, sql, sql, [])
