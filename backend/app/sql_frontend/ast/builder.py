from __future__ import annotations

from collections.abc import Callable

from app.sql_dialect.grammar import StarRocksParser
from app.sql_frontend.antlr_utils import walk_nodes as _walk_nodes
from app.sql_frontend.ast.statements import (
    CreateMLModelStatement,
    CreateTaskStatement,
    DropTaskStatement,
    ExecuteTaskStatement,
    ForcePasswordChangeStatement,
    MLForecastStatement,
    MLMaterializeStatement,
    MLPredictStatement,
    NativeStatement,
    ParameterizedQueryStatement,
    PipeStatement,
    QuerySchemaStatement,
    SecurityStatement,
    StageAwareStatement,
    Statement,
    TaskLifecycleStatement,
)
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import ParsedStatement

_NATIVE_CONTEXTS = frozenset(
    name[0].upper() + name[1:] + "Context"
    for name in vars(StarRocksParser.StatementContext)
    if name in StarRocksParser.ruleNames
    and not name.startswith("nova")
    and name != "createMlModelStatement"
)


class AstBuilderRegistry:
    def __init__(self) -> None:
        self._builders: dict[str, Callable[[ParsedStatement], Statement]] = {}

    def register(self, context_name: str, builder: Callable[[ParsedStatement], Statement]) -> None:
        if context_name in self._builders:
            raise ValueError(f"AST builder already registered for {context_name}")
        self._builders[context_name] = builder

    def build(self, parsed: ParsedStatement) -> Statement:
        statement = self._build(parsed)
        if not isinstance(statement, PipeStatement):
            # A pipe's FILES() source is a validated stage path; anywhere else
            # FILES() would make the engine read an arbitrary location.
            from app.sql_frontend.file_reads import refuse_native_file_reads

            refuse_native_file_reads(parsed)
        return statement

    def _build(self, parsed: ParsedStatement) -> Statement:
        schema_explains = [
            node
            for node in _walk_nodes(parsed.statement_context)
            if type(node).__name__ == "ExplainDescContext" and node.SCHEMA() is not None
        ]
        if schema_explains:
            root = parsed.statement_context
            if (
                len(schema_explains) != 1
                or type(root).__name__ != "QueryStatementContext"
                or schema_explains[0] is not root.explainDesc()
                or root.outfile() is not None
            ):
                raise SemanticError("EXPLAIN SCHEMA requires a top-level query without OUTFILE")
            return QuerySchemaStatement(parsed)
        if any(
            type(node).__name__ == "NamedParameterContext"
            for node in _walk_nodes(parsed.statement_context)
        ):
            if type(parsed.statement_context).__name__ != "QueryStatementContext":
                raise SemanticError("Named parameters are supported only in read queries")
            return ParameterizedQueryStatement(parsed)
        builder = self._builders.get(type(parsed.statement_context).__name__)
        if builder:
            return builder(parsed)
        if type(parsed.statement_context).__name__ not in _NATIVE_CONTEXTS:
            raise SemanticError("No AST builder registered for SQL extension")
        return _native_or_stage(parsed)


def _native_or_stage(parsed: ParsedStatement) -> Statement:
    from app.sql_frontend.backup_restore import repository_targets
    from app.sql_frontend.broker_load import broker_load_targets
    from app.sql_frontend.unload import unload_targets

    # An unload (INSERT INTO FILES, INTO OUTFILE, EXPORT) writes only to a
    # stage, a Broker Load reads only from stages and a backup repository
    # lives only in a stage; a storage URL, credential or unknown option is
    # refused here.
    if (
        unload_targets(parsed)
        or broker_load_targets(parsed)
        or repository_targets(parsed)
        or any(
            type(node).__name__ == "StageReferenceContext"
            for node in _walk_nodes(parsed.statement_context)
        )
    ):
        return StageAwareStatement(parsed)
    return NativeStatement(parsed)


#: Task clauses and bodies only Nova runs; the engine's SUBMIT TASK has none.
_NOVA_TASK_PARTS = frozenset(
    {
        "NovaTaskScriptBlockContext",
        "TaskLanguageClauseContext",
        "TaskRuntimeVersionClauseContext",
        "TaskPackagesClauseContext",
    }
)


def _task(parsed: ParsedStatement) -> Statement:
    if parsed.visible_tokens[0].text.upper() == "CREATE":
        return CreateTaskStatement(parsed)
    context = parsed.statement_context
    if any(type(node).__name__ in _NOVA_TASK_PARTS for node in _walk_nodes(context)) or any(
        context.getChild(i).getText().startswith("$$") for i in range(context.getChildCount())
    ):
        raise SemanticError(
            "SUBMIT TASK takes one engine statement; use CREATE TASK for a script or Python task"
        )
    # SUBMIT TASK becomes a Nova task owned by the caller's active role: run
    # once now when it has no schedule, like CREATE TASK + EXECUTE TASK. The
    # engine never stores it, so it never runs without the caller's role.
    return CreateTaskStatement(parsed)


def _query(parsed: ParsedStatement) -> Statement:
    from app.common.ml_intercept import detect_ml_predict, detect_ml_predict_table

    if parsed.visible_tokens[0].text.upper() in {"EXPLAIN", "DESC", "DESCRIBE"}:
        return _native_or_stage(parsed)
    if detect_ml_predict_table(parsed.normalized_sql, parsed=parsed):
        return MLMaterializeStatement(parsed)
    call = detect_ml_predict(parsed.normalized_sql, tree=parsed.parse_tree)
    if call:
        return MLPredictStatement(parsed, call)
    return _native_or_stage(parsed)


def default_builders() -> AstBuilderRegistry:
    from app.sql_frontend.catalog_ddl import build_catalog_statement
    from app.sql_frontend.merge import build_merge_statement
    from app.sql_frontend.pipes import build_pipe_statement
    from app.sql_frontend.routine_load import CONTEXTS as ROUTINE_LOAD_CONTEXTS
    from app.sql_frontend.routine_load import build_routine_load_statement
    from app.sql_frontend.streams import build_stream_statement

    registry = AstBuilderRegistry()
    for name, builder in {
        "CreateMlModelStatementContext": CreateMLModelStatement,
        "SubmitTaskStatementContext": _task,
        "NovaExecuteTaskStatementContext": ExecuteTaskStatement,
        "DropTaskStatementContext": DropTaskStatement,
        "AlterTaskStatementContext": TaskLifecycleStatement,
        "NovaShowTasksStatementContext": TaskLifecycleStatement,
        "NovaDescribeTaskStatementContext": TaskLifecycleStatement,
        "QueryStatementContext": _query,
        "CreateInternalFunctionStmtContext": NativeStatement,
        "CreateUdfFunctionStmtContext": NativeStatement,
        "NovaForecastStatementContext": MLForecastStatement,
        "NovaForcePasswordStatementContext": ForcePasswordChangeStatement,
        "NovaListStatementContext": StageAwareStatement,
        "NovaCopyStatementContext": StageAwareStatement,
        "NovaStageInsertStatementContext": StageAwareStatement,
        "NovaSecurityShowStatementContext": SecurityStatement,
        "NovaStreamStatementContext": build_stream_statement,
        "NovaCreateConnectionContext": build_catalog_statement,
        "NovaDropConnectionContext": build_catalog_statement,
        "NovaShowConnectionsContext": build_catalog_statement,
        "NovaDescribeConnectionContext": build_catalog_statement,
        "NovaCreateForeignCatalogContext": build_catalog_statement,
        "NovaRefreshCatalogContext": build_catalog_statement,
        "NovaMergeStatementContext": build_merge_statement,
        # Engine pipe grammar, run as Nova pipes (never forwarded to the engine).
        "CreatePipeStatementContext": build_pipe_statement,
        "DropPipeStatementContext": build_pipe_statement,
        "AlterPipeStatementContext": build_pipe_statement,
        "ShowPipeStatementContext": build_pipe_statement,
        "DescPipeStatementContext": build_pipe_statement,
        "CreateRoleStatementContext": SecurityStatement,
        "CreateUserStatementContext": SecurityStatement,
        "AlterUserStatementContext": SecurityStatement,
        "DropUserStatementContext": SecurityStatement,
        "SetDefaultRoleStatementContext": SecurityStatement,
        "DropRoleStatementContext": SecurityStatement,
        "ShowRolesStatementContext": SecurityStatement,
        "ShowGrantsStatementContext": SecurityStatement,
        # Labelled alternatives of showAuthenticationStatement: account listings
        # the engine authorizes (another user's entry needs user admin rights).
        "ShowAllAuthenticationContext": NativeStatement,
        "ShowAuthenticationForUserContext": NativeStatement,
        "GrantRoleToUserContext": SecurityStatement,
        "GrantRoleToRoleContext": SecurityStatement,
        "GrantRoleToGroupContext": SecurityStatement,
        "RevokeRoleFromUserContext": SecurityStatement,
        "RevokeRoleFromRoleContext": SecurityStatement,
        "RevokeRoleFromGroupContext": SecurityStatement,
        "GrantOnUserContext": SecurityStatement,
        "GrantOnSystemContext": SecurityStatement,
        "GrantOnTableBriefContext": SecurityStatement,
        "GrantOnFuncContext": SecurityStatement,
        "GrantOnPrimaryObjContext": SecurityStatement,
        "GrantOnAllContext": SecurityStatement,
        "RevokeOnUserContext": SecurityStatement,
        "RevokeOnSystemContext": SecurityStatement,
        "RevokeOnTableBriefContext": SecurityStatement,
        "RevokeOnFuncContext": SecurityStatement,
        "RevokeOnPrimaryObjContext": SecurityStatement,
        "RevokeOnAllContext": SecurityStatement,
        # Routine loads run through Nova's Kafka connections and owner checks.
        **dict.fromkeys(ROUTINE_LOAD_CONTEXTS, build_routine_load_statement),
    }.items():
        registry.register(name, builder)
    return registry


ast_builders = default_builders()
